"""d2v 生成オーケストレーション（CLI・Web GUI 共通）。

`main.py` の run_d2v 分岐（single / split / focus / zone）をここへ集約し、
UI 非依存の純関数として提供する。進捗は ``ProgressCallback`` 経由で emit し、
CLI は rich 表示へ、Web GUI は SSE へ橋渡しする。

検証エラーは ``D2VJobError`` を送出する（CLI は捕捉して赤字表示＋終了、
Web はエラーレスポンスに変換する）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from d2v import parser, partitioner, pipeline
from d2v.pipeline import PipelineResult
from d2v.progress import ProgressCallback, ProgressEvent, emit


class D2VJobError(Exception):
    """ユーザー起因の検証エラー（不正なパラメータ・存在しないノード/ゾーン等）。"""


# ---------------------------------------------------------------------------
# パラメータ・結果モデル
# ---------------------------------------------------------------------------


@dataclass
class D2VParams:
    """d2v ジョブのパラメータ（CLI 引数と 1:1 対応）。"""

    input_path: Path
    output_dir: Path = Path("output")
    fmt: str = "png"
    max_iter: int = 3
    threshold: int = 8
    patience: int = 1
    no_split: bool = False
    split_threshold: int = partitioner.DEFAULT_SPLIT_THRESHOLD
    focus: list[str] | None = None
    hops: int = 1
    zone: list[str] | None = None
    zone_opacity: float = 0.4

    @property
    def stem(self) -> str:
        return self.input_path.stem


@dataclass
class DiagramOutput:
    """生成された 1 枚の図の成果物。"""

    key: str            # "single" / "overview" / "zone-..." / "focus-..." など
    title: str
    final_image: Path   # 出力ルートに集約したベスト画像
    result: PipelineResult
    final_legend: Path | None = None  # 集約した凡例画像（別ファイル・任意）

    @property
    def score(self) -> int:
        return self.result.best_result.score

    @property
    def passed(self) -> bool:
        return self.result.best_result.passed


@dataclass
class D2VJobResult:
    """ジョブ全体の結果。"""

    mode: str                       # "single" / "split" / "focus" / "zone"
    outputs: list[DiagramOutput]
    output_dir: Path
    topology_text: str = ""


@dataclass
class V2DJobResult:
    """v2d ジョブ（画像 → YAML）の結果。"""

    yaml_text: str
    yaml_path: Path
    sidecar_path: Path
    original_image: Path
    node_count: int
    edge_count: int
    cluster_count: int
    confidence: float
    notes: list[str] = field(default_factory=list)
    low_confidence_nodes: list[dict] = field(default_factory=list)
    metrics: dict | None = None
    rerender_image: Path | None = None
    rerender_score: int | None = None


# ---------------------------------------------------------------------------
# メイン API
# ---------------------------------------------------------------------------


def run_d2v_job(
    params: D2VParams,
    progress: ProgressCallback | None = None,
) -> D2VJobResult:
    """d2v の生成ジョブを実行する（single / split / focus / zone を自動判別）。

    Args:
        params: ジョブパラメータ。
        progress: 進捗コールバック（任意）。

    Returns:
        D2VJobResult。

    Raises:
        D2VJobError: パラメータ検証に失敗した場合。
    """
    # ── トポロジ解析 ─────────────────────────────────────────────
    model = parser.load_model(params.input_path)
    topology_text = parser.build_text(
        model.devices, model.connections, model.subnets, model.device_map
    )
    emit(progress, ProgressEvent(
        stage="topology", message="トポロジ解析完了",
        extra={"text": topology_text},
    ))

    # ── 検証 ─────────────────────────────────────────────────────
    if params.focus is not None and params.zone is not None:
        raise D2VJobError(
            "--focus と --zone は同時に指定できません。どちらか一方を指定してください。"
        )

    # ── モード判別 → 図のリスト決定 ──────────────────────────────
    if params.focus is not None:
        mode = "focus"
        diagrams = [_build_focus(model, params)]
    elif params.zone is not None:
        mode = "zone"
        diagrams = [_build_zone(model, params)]
    else:
        plan = None if params.no_split else partitioner.plan(model, params.split_threshold)
        if plan is None:
            mode = "single"
            diagrams = None  # 単一図（topology_text をそのまま使う）
        else:
            mode = "split"
            diagrams = plan

    count = 1 if diagrams is None else len(diagrams)
    emit(progress, ProgressEvent(
        stage="plan", message=f"モード: {mode}",
        total=count,
        extra={"mode": mode, "split_threshold": params.split_threshold},
    ))

    # ── 実行 ─────────────────────────────────────────────────────
    if diagrams is None:
        outputs = [_run_single(params, topology_text, progress)]
    else:
        outputs = _run_multi(params, diagrams, mode, progress)

    emit(progress, ProgressEvent(
        stage="job_done", message="ジョブ完了",
        total=len(outputs),
        extra={"mode": mode, "output_dir": str(params.output_dir)},
    ))
    return D2VJobResult(
        mode=mode,
        outputs=outputs,
        output_dir=params.output_dir,
        topology_text=topology_text,
    )


# ---------------------------------------------------------------------------
# 図リストの構築（focus / zone）
# ---------------------------------------------------------------------------


def _build_focus(model: "parser.TopologyModel", params: D2VParams) -> partitioner.SubDiagram:
    if params.hops < 0:
        raise D2VJobError("--hops は 0 以上を指定してください。")
    missing = [fid for fid in params.focus if fid not in model.device_map]
    if missing:
        available = ", ".join(sorted(model.device_map)) or "(なし)"
        raise D2VJobError(
            f"注目ノード {', '.join(missing)} がトポロジに存在しません。"
            f" 利用可能な device-id: {available}"
        )
    diagram = partitioner.focus_plan(model, params.focus, params.hops)
    if diagram is None:
        raise D2VJobError("集中図を生成できませんでした。")
    return diagram


def _build_zone(model: "parser.TopologyModel", params: D2VParams) -> partitioner.SubDiagram:
    known = partitioner.available_zones(model)
    missing = [z for z in params.zone if z not in known]
    if missing:
        available = ", ".join(known) or "(なし)"
        raise D2VJobError(
            f"ゾーン {', '.join(missing)} がトポロジに存在しません。"
            f" 利用可能なゾーン: {available}"
        )
    diagram = partitioner.zone_plan(model, params.zone)
    if diagram is None:
        raise D2VJobError("ゾーン限定図を生成できませんでした。")
    return diagram


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def _run_single(
    params: D2VParams, topology_text: str, progress: ProgressCallback | None
) -> DiagramOutput:
    """従来どおり 1 枚の図を生成する。"""
    result = pipeline.run(
        topology_text=topology_text,
        output_dir=params.output_dir,
        stem=params.stem,
        fmt=params.fmt,
        max_iterations=params.max_iter,
        threshold=params.threshold,
        patience=params.patience,
        zone_opacity=params.zone_opacity,
        progress_callback=progress,
    )
    return DiagramOutput(
        key="single",
        title="構成図",
        final_image=result.best_image,
        final_legend=result.best_legend,
        result=result,
    )


def _run_multi(
    params: D2VParams,
    diagrams: list[partitioner.SubDiagram],
    mode: str,
    progress: ProgressCallback | None,
) -> list[DiagramOutput]:
    """複数枚（split / focus / zone）を生成する。"""
    outputs: list[DiagramOutput] = []
    total = len(diagrams)
    for idx, diag in enumerate(diagrams):
        emit(progress, ProgressEvent(
            stage="diagram_start", iteration=idx, total=total,
            message=diag.title,
            extra={"key": diag.key, "title": diag.title, "text": diag.text, "mode": mode},
        ))
        sub_stem = f"{params.stem}_{diag.key}"
        result = pipeline.run(
            topology_text=diag.text,
            output_dir=params.output_dir / diag.key,
            stem=sub_stem,
            fmt=params.fmt,
            max_iterations=params.max_iter,
            threshold=params.threshold,
            patience=params.patience,
            zone_opacity=params.zone_opacity,
            # 俯瞰図はゾーン単位の全体地図用プロンプトを使う（個別デバイスを展開しない）
            system_prompt_file=(
                "diagram-system-overview.md" if diag.key == "overview"
                else "diagram-system.md"
            ),
            progress_callback=progress,
        )
        # ベスト画像を出力ルートへ集約
        final_path = params.output_dir / f"{sub_stem}.{params.fmt}"
        shutil.copy2(result.best_image, final_path)
        # 凡例（別ファイル）もベスト図と並べて集約する（存在する場合のみ）
        final_legend: Path | None = None
        if result.best_legend is not None and result.best_legend.exists():
            final_legend = params.output_dir / f"{sub_stem}_legend.{params.fmt}"
            shutil.copy2(result.best_legend, final_legend)
        output = DiagramOutput(
            key=diag.key,
            title=diag.title,
            final_image=final_path,
            final_legend=final_legend,
            result=result,
        )
        outputs.append(output)
        emit(progress, ProgressEvent(
            stage="diagram_done", iteration=idx, total=total,
            score=output.score, passed=output.passed,
            message=f"{diag.title} 完了",
            extra={"key": diag.key, "title": diag.title, "image": str(final_path)},
        ))
    return outputs


# ---------------------------------------------------------------------------
# v2d ジョブ（画像 → YAML）
# ---------------------------------------------------------------------------


def run_v2d_job(
    image_path: Path,
    output_dir: Path,
    truth_path: Path | None = None,
    rerender: bool = False,
    fmt: str = "png",
    progress: ProgressCallback | None = None,
) -> V2DJobResult:
    """画像から iida-network-model YAML を抽出し、任意で精度計測・再描画する。

    Args:
        image_path: 入力画像。
        output_dir: 出力ディレクトリ（ジョブ専用）。
        truth_path: 正解 YAML（指定すると精度計測）。
        rerender: True なら d2v で再描画（LLM 使用）。
        fmt: 再描画フォーマット。
        progress: 進捗コールバック。
    """
    # 画像処理系は遅延インポート（v2d 実行時のみ読み込む）
    from d2v.v2d import evaluate as v2d_evaluate
    from d2v.v2d import pipeline as v2d_pipeline

    # ── 抽出 → 補正 → YAML 化 ────────────────────────────────────
    emit(progress, ProgressEvent(stage="v2d_extract", message="画像を解析中（vision LLM）..."))
    result = v2d_pipeline.run(image_path, output_dir)
    emit(progress, ProgressEvent(
        stage="v2d_extracted", message="抽出完了",
        extra={
            "nodes": result.node_count,
            "edges": result.edge_count,
            "clusters": result.cluster_count,
            "confidence": result.confidence,
        },
    ))

    low_conf = [
        {"id": n.id, "hostname": n.hostname, "confidence": n.confidence}
        for n in result.diagram.nodes
        if n.confidence < 0.7
    ]

    # ── 精度計測（正解 YAML があれば） ───────────────────────────
    metrics: dict | None = None
    if truth_path is not None:
        emit(progress, ProgressEvent(stage="v2d_metrics_start", message="精度を計測中..."))
        m = v2d_evaluate.evaluate_files(result.yaml_path, truth_path)
        metrics = {
            "nodes": {"precision": m.nodes.precision, "recall": m.nodes.recall, "f1": m.nodes.f1},
            "edges": {"precision": m.edges.precision, "recall": m.edges.recall, "f1": m.edges.f1},
            "device_type_accuracy": m.device_type_accuracy,
            "zone_accuracy": m.zone_accuracy,
            "loopback_accuracy": m.loopback_accuracy,
            "matched_nodes": m.matched_nodes,
            "truth_nodes": m.truth_nodes,
            "pred_nodes": m.pred_nodes,
            "truth_edges": m.truth_edges,
            "pred_edges": m.pred_edges,
        }
        emit(progress, ProgressEvent(
            stage="v2d_metrics", message="精度計測完了", extra=metrics
        ))

    # ── 再描画（d2v 往復） ────────────────────────────────────────
    rerender_image: Path | None = None
    rerender_score: int | None = None
    if rerender:
        emit(progress, ProgressEvent(stage="v2d_rerender_start", message="d2v で再描画中..."))
        rr = v2d_evaluate.rerender_with_d2v(
            result.yaml_path, output_dir / "rerender", fmt=fmt
        )
        rerender_image = rr.best_image
        rerender_score = rr.best_result.score
        emit(progress, ProgressEvent(
            stage="v2d_rerender", message="再描画完了", score=rerender_score,
            extra={"image": str(rerender_image)},
        ))

    emit(progress, ProgressEvent(stage="job_done", message="ジョブ完了"))
    return V2DJobResult(
        yaml_text=result.yaml_text,
        yaml_path=result.yaml_path,
        sidecar_path=result.sidecar_path,
        original_image=image_path,
        node_count=result.node_count,
        edge_count=result.edge_count,
        cluster_count=result.cluster_count,
        confidence=result.confidence,
        notes=list(result.diagram.notes),
        low_confidence_nodes=low_conf,
        metrics=metrics,
        rerender_image=rerender_image,
        rerender_score=rerender_score,
    )
