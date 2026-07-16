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

app = FastAPI(
    title="d2v Web GUI",
    description="iida-network-model YAML → 構成図 / 画像 → YAML をブラウザで実行する GUI",
    version="0.1.0",
)


@app.get("/api/meta")
def get_meta() -> dict:
    """UI 初期化に必要なメタ情報（プロバイダ・既定値・サンプル一覧）を返す。"""
    # トポロジのサンプルのみを列挙する（ポリシーファイル等は除外）
    examples = (
        sorted(
            p.name
            for p in _EXAMPLES_DIR.glob("*.yaml")
            if "policy" not in p.name
        )
        if _EXAMPLES_DIR.exists()
        else []
    )
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
    return _read_yaml_source(req.source, req.example, req.yaml_text)


def _read_yaml_source(
    source: str, example: str | None, yaml_text: str | None
) -> str:
    """source（example/text）から YAML 本文を取り出す（パストラバーサル・サイズ検証つき）。"""
    if source == "example":
        if not example:
            raise HTTPException(400, "source=example のときは example を指定してください。")
        # パストラバーサル防止: examples ディレクトリ直下の実在ファイルのみ許可
        target = (_EXAMPLES_DIR / example).resolve()
        if target.parent != _EXAMPLES_DIR.resolve() or not target.is_file():
            raise HTTPException(400, f"サンプル '{example}' が見つかりません。")
        return target.read_text(encoding="utf-8")
    if source == "text":
        if not yaml_text or not yaml_text.strip():
            raise HTTPException(400, "source=text のときは yaml_text を指定してください。")
        if len(yaml_text.encode("utf-8")) > settings.webui_max_yaml_bytes:
            raise HTTPException(413, "YAML が大きすぎます。")
        return yaml_text
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


# ---------------------------------------------------------------------------
# セマンティック検証 API（design lint）
# ---------------------------------------------------------------------------


class ValidateRequest(BaseModel):
    """セマンティック検証リクエスト。"""

    source: str = Field("example", description="'example' | 'text'")
    example: str | None = None
    yaml_text: str | None = None
    explain: bool = False
    strict: bool = False


def _load_model_from_text(text: str):
    """YAML 本文を一時ファイル経由でパースし TopologyModel を返す（失敗は 400）。"""
    import tempfile

    from d2v.errors import D2VError
    from d2v.parser import load_model

    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(text)
            tmp = Path(f.name)
        try:
            return load_model(tmp)
        except D2VError as e:
            raise HTTPException(400, str(e))
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


@app.post("/api/validate")
def validate_topology(req: ValidateRequest) -> dict:
    """トポロジ YAML を同期的にセマンティック検証し、結果を返す。

    検証は決定論的で高速なため（LLM 不要）、ジョブ化せず即時に結果を返す。
    ``explain=True`` のときのみ LLM で理由・修正案を付与する（失敗しても検証結果は返す）。
    """
    from d2v import validator

    text = _read_yaml_source(req.source, req.example, req.yaml_text)
    model = _load_model_from_text(text)

    report = validator.validate(model)

    explain_error: str | None = None
    if req.explain and report.issues:
        try:
            report = validator.explain(report, model)
        except (SystemExit, Exception) as e:  # noqa: BLE001 - 検証結果は必ず返す
            explain_error = f"説明の生成に失敗しました: {e}"

    return {
        "ok": report.ok,
        "passed": report.passed(strict=req.strict),
        "counts": report.counts,
        "issues": [i.model_dump() for i in report.issues],
        "explain_error": explain_error,
    }


# ---------------------------------------------------------------------------
# 意味的 diff API
# ---------------------------------------------------------------------------

# 生成した差分図を token で引けるようにする（プロセス内・ローカルツール想定）
_DIFF_IMAGES: dict[str, Path] = {}


class DiffSide(BaseModel):
    """diff の一方（変更前 / 変更後）の入力。"""

    source: str = Field("example", description="'example' | 'text'")
    example: str | None = None
    yaml_text: str | None = None


class DiffRequest(BaseModel):
    """意味的 diff リクエスト。"""

    before: DiffSide
    after: DiffSide
    summarize: bool = False
    image: bool = True
    format: str = "png"


@app.post("/api/diff")
def diff_topologies(req: DiffRequest) -> dict:
    """2 つのトポロジ YAML の構造差分を算出し、差分図（任意）を生成する。"""
    import uuid

    from d2v import diff as diff_mod

    if req.format not in ("png", "svg"):
        raise HTTPException(400, "format は png または svg を指定してください。")

    before_text = _read_yaml_source(
        req.before.source, req.before.example, req.before.yaml_text
    )
    after_text = _read_yaml_source(
        req.after.source, req.after.example, req.after.yaml_text
    )
    before_model = _load_model_from_text(before_text)
    after_model = _load_model_from_text(after_text)

    topo_diff = diff_mod.compare(before_model, after_model)

    summary_error: str | None = None
    if req.summarize and not topo_diff.is_empty():
        try:
            topo_diff = diff_mod.summarize(topo_diff)
        except (SystemExit, Exception) as e:  # noqa: BLE001 - 差分は必ず返す
            summary_error = f"要約の生成に失敗しました: {e}"

    image_token: str | None = None
    if req.image and not topo_diff.is_empty():
        image_token = uuid.uuid4().hex
        out_dir = _ROOT / "output" / "webui" / "diff" / image_token
        try:
            img = diff_mod.render_diff_diagram(
                before_model, after_model, topo_diff, out_dir,
                stem="diff", fmt=req.format,
            )
            _DIFF_IMAGES[image_token] = img
        except Exception:  # noqa: BLE001 - 図が作れなくても差分は返す
            image_token = None

    return {
        "diff": topo_diff.model_dump(),
        "is_empty": topo_diff.is_empty(),
        "image_token": image_token,
        "format": req.format,
        "summary_error": summary_error,
    }


@app.get("/api/diff/image/{token}")
def get_diff_image(token: str):
    """diff で生成した差分図を token で返す。"""
    path = _DIFF_IMAGES.get(token)
    if path is None or not path.exists():
        raise HTTPException(404, "差分図が見つかりません。")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# focus ライブプレビュー API（edit-assist）
#   エディタのカーソル位置に追従して、注目ノード周辺の構成図を即時返す。
#   決定論経路（LLM 不使用）のため同期で完結し、ジョブ化しない。
# ---------------------------------------------------------------------------


class FocusResolveRequest(BaseModel):
    """カーソル行 → 注目ノード解決リクエスト。"""

    source: str = Field("example", description="'example' | 'text'")
    example: str | None = None
    yaml_text: str | None = None
    line: int = Field(..., ge=1, description="カーソル行（1 始まり）")


@app.post("/api/focus/resolve")
def focus_resolve(req: FocusResolveRequest) -> dict:
    """YAML 本文とカーソル行から、注目 device-id を解決して返す。

    device ブロック内なら 1 台、physical-connection ブロック内なら両端 2 台。
    どこにも該当しなければ直前の最も近いブロックへフォールバックする。
    """
    from d2v import edit_assist

    text = _read_yaml_source(req.source, req.example, req.yaml_text)
    res = edit_assist.resolve_focus(text, req.line)
    return {
        "focus": res.focus_ids,
        "context": res.context,
        "device_lines": res.device_lines,
    }


class FocusPreviewRequest(BaseModel):
    """focus ライブプレビュー生成リクエスト。

    ``focus`` を明示した場合はそれを優先する。未指定かつ ``line`` があれば
    カーソル行から注目ノードを解決する。
    """

    source: str = Field("example", description="'example' | 'text'")
    example: str | None = None
    yaml_text: str | None = None
    focus: list[str] | None = None
    line: int | None = Field(None, ge=1, description="カーソル行（1 始まり）")
    hops: int = Field(1, ge=1, le=5)


@app.post("/api/focus/preview")
def focus_preview(req: FocusPreviewRequest) -> dict:
    """注目ノード周辺（hops ホップ以内）の集中図 SVG を同期生成して返す。

    レンダリングは決定論的（LLM 不使用）なので即時に完結する。
    ライブ編集では不正 YAML・未解決が頻繁に起きるため、注目ノードが決まらない
    ／存在しない場合も 200 で構造化情報を返す（呼び出し側が直前の図を保持できる）。
    """
    import tempfile

    from d2v import edit_assist, partitioner

    text = _read_yaml_source(req.source, req.example, req.yaml_text)
    model = _load_model_from_text(text)

    # 注目ノードの決定: focus 明示 > line 解決
    context = "explicit"
    device_lines: dict[str, int] = {}
    focus_ids = list(req.focus) if req.focus else []
    if not focus_ids and req.line is not None:
        res = edit_assist.resolve_focus(text, req.line)
        focus_ids = res.focus_ids
        context = res.context
        device_lines = res.device_lines
    if not device_lines:
        # 明示 focus のときも双方向ジャンプ用に定義行を返す
        _, device_lines = edit_assist._parse_spans(text)

    base = {
        "svg": None,
        "focus": focus_ids,
        "context": context,
        "hops": req.hops,
        "device_lines": device_lines,
        "not_found": [],
        "message": None,
    }

    if not focus_ids:
        base["message"] = "注目ノードを特定できませんでした。"
        return base

    missing = [f for f in focus_ids if f not in model.device_map]
    if missing:
        base["not_found"] = missing
        base["message"] = f"存在しない device-id: {', '.join(missing)}"
        return base

    dot_code = partitioner.build_focus_dot(model, focus_ids, req.hops)
    if dot_code is None:
        base["message"] = "集中図を生成できませんでした。"
        return base

    from d2v import renderer

    with tempfile.TemporaryDirectory() as tmp:
        img = renderer.render(dot_code, Path(tmp), stem="focus", fmt="svg")
        base["svg"] = img.read_text(encoding="utf-8")
    return base


class LintRequest(BaseModel):
    """エディタ diagnostics 用の design lint リクエスト。"""

    source: str = Field("example", description="'example' | 'text'")
    example: str | None = None
    yaml_text: str | None = None
    strict: bool = False


@app.post("/api/lint")
def lint_topology(req: LintRequest) -> dict:
    """design lint（セマンティック検証）を行い、各 issue を行番号付きで返す。

    エディタで波線（diagnostics）を出すため、issue の ``targets``
    （device-id / connection-id / subnet-id）を YAML 上の行へ解決して付与する。
    行を特定できない issue は ``line=null`` を返す（呼び出し側で先頭行に集約）。
    """
    from d2v import edit_assist, validator

    text = _read_yaml_source(req.source, req.example, req.yaml_text)
    model = _load_model_from_text(text)

    report = validator.validate(model)
    sym = edit_assist.symbol_lines(text)

    issues: list[dict] = []
    for iss in report.issues:
        line: int | None = None
        for t in iss.targets:
            if t in sym:
                line = sym[t]
                break
        issues.append(
            {
                "rule": iss.rule,
                "severity": iss.severity,
                "message": iss.message,
                "targets": iss.targets,
                "line": line,
            }
        )

    return {
        "ok": report.ok,
        "passed": report.passed(strict=req.strict),
        "counts": report.counts,
        "issues": issues,
    }


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
