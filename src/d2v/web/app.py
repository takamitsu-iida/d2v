"""FastAPI アプリ定義。

Phase 0 では最小構成として次を提供する:
  - 静的 SPA（static/index.html）の配信
  - ``GET /api/meta``: 利用可能な LLM プロバイダ・既定パラメータ・examples 一覧

生成ジョブ・SSE 進捗・v2d は後続フェーズ（Phase 2 以降）で追加する。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from d2v import partitioner
from d2v.config import settings
from d2v.web import jobs
from d2v.web.jobs import JobBusyError

# リポジトリルートと examples ディレクトリ（src/d2v/web/app.py から 3 つ上がルート）
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_EXAMPLES_DIR = _ROOT / "examples"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

# 貼り付け YAML の最大バイト数（OWASP: 過大入力によるリソース枯渇を防ぐ）
_MAX_YAML_BYTES = 1_000_000

app = FastAPI(
    title="d2v Web GUI",
    description="iida-network-model YAML → 構成図 / 画像 → YAML をブラウザで実行する GUI",
    version="0.1.0",
)


@app.get("/api/meta")
def get_meta() -> dict:
    """UI 初期化に必要なメタ情報（プロバイダ・既定値・サンプル一覧）を返す。"""
    examples = sorted(p.name for p in _EXAMPLES_DIR.glob("*.yaml")) if _EXAMPLES_DIR.exists() else []
    return {
        "llm_provider": settings.llm_provider,
        "examples": examples,
        "defaults": {
            "format": "png",
            "max_iter": 3,
            "threshold": 8,
            "patience": 1,
            "split_threshold": partitioner.DEFAULT_SPLIT_THRESHOLD,
            "no_split": False,
            "hops": 1,
            "zone_opacity": 0.4,
        },
        "formats": ["png", "svg"],
        "modes": ["auto", "single", "split", "focus", "zone"],
    }


@app.get("/api/examples/{name}")
def get_example(name: str) -> dict:
    """サンプル YAML の内容を返す（プレビュー・貼り付け初期値用）。"""
    # パストラバーサル防止: examples ディレクトリ直下の実在ファイルのみ許可
    target = (_EXAMPLES_DIR / name).resolve()
    if target.parent != _EXAMPLES_DIR.resolve() or not target.is_file():
        raise HTTPException(404, f"サンプル '{name}' が見つかりません。")
    return {"name": name, "content": target.read_text(encoding="utf-8")}


# ---------------------------------------------------------------------------
# d2v ジョブ API
# ---------------------------------------------------------------------------


class D2VJobRequest(BaseModel):
    """d2v ジョブ作成リクエスト（CLI 引数と 1:1 対応）。"""

    source: str = Field("example", description="'example' | 'text'")
    example: str | None = Field(None, description="source=example のときのサンプル名")
    yaml_text: str | None = Field(None, description="source=text のときの YAML 本文")

    format: str = "png"
    max_iter: int = Field(3, ge=1, le=20)
    threshold: int = Field(8, ge=1, le=10)
    patience: int = Field(1, ge=1, le=20)
    no_split: bool = False
    split_threshold: int = Field(partitioner.DEFAULT_SPLIT_THRESHOLD, ge=1)
    focus: list[str] | None = None
    hops: int = Field(1, ge=1, le=5)
    zone: list[str] | None = None
    zone_opacity: float = Field(0.4, ge=0.0, le=1.0)


def _resolve_input_text(req: D2VJobRequest) -> str:
    """リクエストから入力 YAML 本文を取り出す（検証つき）。"""
    if req.source == "example":
        if not req.example:
            raise HTTPException(400, "source=example のときは example を指定してください。")
        # パストラバーサル防止: examples ディレクトリ直下の実在ファイルのみ許可
        target = (_EXAMPLES_DIR / req.example).resolve()
        if target.parent != _EXAMPLES_DIR.resolve() or not target.is_file():
            raise HTTPException(400, f"サンプル '{req.example}' が見つかりません。")
        return target.read_text(encoding="utf-8")
    if req.source == "text":
        if not req.yaml_text or not req.yaml_text.strip():
            raise HTTPException(400, "source=text のときは yaml_text を指定してください。")
        if len(req.yaml_text.encode("utf-8")) > _MAX_YAML_BYTES:
            raise HTTPException(413, "YAML が大きすぎます。")
        return req.yaml_text
    raise HTTPException(400, "source は 'example' または 'text' を指定してください。")


@app.post("/api/d2v/jobs")
def create_d2v_job(req: D2VJobRequest) -> dict:
    """d2v ジョブを作成し、job_id を返す。"""
    if req.format not in ("png", "svg"):
        raise HTTPException(400, "format は png または svg を指定してください。")
    input_text = _resolve_input_text(req)
    options = {
        "fmt": req.format,
        "max_iter": req.max_iter,
        "threshold": req.threshold,
        "patience": req.patience,
        "no_split": req.no_split,
        "split_threshold": req.split_threshold,
        "focus": req.focus,
        "hops": req.hops,
        "zone": req.zone,
        "zone_opacity": req.zone_opacity,
    }
    try:
        job = jobs.registry.create_d2v_job(
            input_text=input_text,
            options=options,
            request_meta=req.model_dump(exclude={"yaml_text"}),
        )
    except JobBusyError as e:
        raise HTTPException(429, str(e))
    return {"job_id": job.id, "state": job.state.value}

@app.get("/api/jobs")
def list_jobs() -> dict:
    """全ジョブの要約を新しい順で返す（履歴一覧用）。"""
    return {"jobs": jobs.registry.list_jobs()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    """ジョブの状態・結果メタを返す。"""
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(404, "ジョブが見つかりません。")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/events")
def stream_job_events(job_id: str) -> StreamingResponse:
    """ジョブの進捗を SSE でストリーミングする。"""
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(404, "ジョブが見つかりません。")

    def gen():
        for event in job.stream():
            yield jobs.sse_format(event)
        # 終端イベント（最終状態を通知）
        import json
        final = json.dumps(
            {"state": job.state.value, "error": job.error}, ensure_ascii=False
        )
        yield f"event: end\ndata: {final}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 成果物配信（画像・DOT・評価 JSON）
# ---------------------------------------------------------------------------


def _require_output(job_id: str, key: str | None):
    """ジョブと指定 key の DiagramOutput を取得する（未指定なら先頭）。"""
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(404, "ジョブが見つかりません。")
    if job.result is None:
        raise HTTPException(409, "ジョブはまだ完了していません。")
    outputs = job.result.outputs
    if key is None:
        return outputs[0]
    for o in outputs:
        if o.key == key:
            return o
    raise HTTPException(404, f"key '{key}' の成果物が見つかりません。")


@app.get("/api/jobs/{job_id}/image")
def get_job_image(job_id: str, key: str | None = None) -> FileResponse:
    """指定した図のベスト画像を返す。"""
    output = _require_output(job_id, key)
    if not output.final_image.is_file():
        raise HTTPException(404, "画像ファイルが見つかりません。")
    return FileResponse(output.final_image)


@app.get("/api/jobs/{job_id}/dot", response_class=PlainTextResponse)
def get_job_dot(job_id: str, key: str | None = None) -> str:
    """指定した図のベスト DOT ソースを返す。"""
    output = _require_output(job_id, key)
    return output.result.best_dot


@app.get("/api/jobs/{job_id}/eval")
def get_job_eval(job_id: str, key: str | None = None) -> dict:
    """指定した図のベスト評価結果を返す。"""
    output = _require_output(job_id, key)
    return output.result.best_result.model_dump()


# ---------------------------------------------------------------------------
# v2d ジョブ API（画像 → YAML）
# ---------------------------------------------------------------------------

_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg"}
_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
_MAX_IMAGE_BYTES = 12_000_000


@app.post("/api/v2d/jobs")
async def create_v2d_job(
    image: UploadFile = File(...),
    truth: str | None = Form(None),
    rerender: bool = Form(False),
    format: str = Form("png"),
) -> dict:
    """画像アップロードで v2d ジョブを作成し、job_id を返す。"""
    if format not in ("png", "svg"):
        raise HTTPException(400, "format は png または svg を指定してください。")
    # 拡張子・MIME を検査（OWASP: 不正ファイルのアップロード対策）
    ext = Path(image.filename or "").suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, "対応画像は PNG / JPEG のみです。")
    if image.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(400, f"未対応の Content-Type: {image.content_type}")
    data = await image.read()
    if len(data) == 0:
        raise HTTPException(400, "空のファイルです。")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(413, "画像が大きすぎます（上限 12MB）。")

    try:
        job = jobs.registry.create_v2d_job(
            image_bytes=data,
            image_filename=image.filename or "input.png",
            truth_text=truth,
            rerender=rerender,
            fmt=format,
            request_meta={
                "filename": image.filename,
                "has_truth": bool(truth and truth.strip()),
                "rerender": rerender,
                "format": format,
            },
        )
    except JobBusyError as e:
        raise HTTPException(429, str(e))
    return {"job_id": job.id, "state": job.state.value}
def _require_v2d(job_id: str):
    """v2d ジョブと結果を取得する。"""
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(404, "ジョブが見つかりません。")
    if job.kind != "v2d":
        raise HTTPException(400, "v2d ジョブではありません。")
    if job.result is None:
        raise HTTPException(409, "ジョブはまだ完了していません。")
    return job.result


@app.get("/api/jobs/{job_id}/v2d/yaml", response_class=PlainTextResponse)
def get_v2d_yaml(job_id: str) -> str:
    """抽出された iida-network-model YAML を返す。"""
    return _require_v2d(job_id).yaml_text


@app.get("/api/jobs/{job_id}/v2d/sidecar")
def get_v2d_sidecar(job_id: str) -> FileResponse:
    """サイドカー JSON を返す。"""
    result = _require_v2d(job_id)
    if not result.sidecar_path.is_file():
        raise HTTPException(404, "サイドカーが見つかりません。")
    return FileResponse(result.sidecar_path, media_type="application/json")


@app.get("/api/jobs/{job_id}/v2d/original")
def get_v2d_original(job_id: str) -> FileResponse:
    """アップロードされた元画像を返す。"""
    result = _require_v2d(job_id)
    if not result.original_image.is_file():
        raise HTTPException(404, "元画像が見つかりません。")
    return FileResponse(result.original_image)


@app.get("/api/jobs/{job_id}/v2d/rerender")
def get_v2d_rerender(job_id: str) -> FileResponse:
    """d2v による再描画画像を返す。"""
    result = _require_v2d(job_id)
    if result.rerender_image is None or not result.rerender_image.is_file():
        raise HTTPException(404, "再描画画像がありません。")
    return FileResponse(result.rerender_image)


@app.get("/")
def index() -> FileResponse:
    """SPA 本体を返す。"""
    return FileResponse(_STATIC_DIR / "index.html")


# 静的アセット（app.js / style.css など）を /static で配信する
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
