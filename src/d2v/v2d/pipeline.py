"""v2d パイプライン: 画像 → 抽出 → 整合性補正 → iida-network-model YAML 出力。

一連の流れをまとめ、成果物として次を出力する:
  - ``<stem>.yaml``      : iida-network-model YAML（d2v で再描画可能）
  - ``<stem>.v2d.json``  : 確信度・所見・補正内容・カウントを記録したサイドカー
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from d2v import parser
from d2v.v2d import converter
from d2v.v2d.extractor import extract_from_image
from d2v.v2d.refine import RefineReport, refine
from d2v.v2d.schema import ExtractedDiagram


@dataclass
class V2dResult:
    """v2d 実行結果。"""

    yaml_path: Path
    sidecar_path: Path
    diagram: ExtractedDiagram
    report: RefineReport
    yaml_text: str
    node_count: int
    edge_count: int
    cluster_count: int
    confidence: float


def run(
    image_path: str | Path,
    output_dir: str | Path,
    stem: str | None = None,
    max_dim: int | None = None,
) -> V2dResult:
    """画像から iida-network-model YAML とサイドカーを生成する。

    Args:
        image_path: 入力画像（PNG / JPEG）
        output_dir: 出力ディレクトリ
        stem: 出力ファイル名ベース（省略時は画像ファイル名）
        max_dim: 画像の最大辺ピクセル上限（None なら設定値）

    Returns:
        V2dResult
    """
    image_path = Path(image_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or image_path.stem

    # ── 抽出 → 整合性補正 → YAML 化 ──────────────────────────────
    diagram, pre = extract_from_image(image_path, max_dim=max_dim)
    refined, report = refine(diagram)
    yaml_text = converter.to_yaml(refined)

    yaml_path = output_dir / f"{stem}.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")

    # ── 既存 parser との整合確認（d2v で再パースできるか） ──────────
    model = parser.load_model(yaml_path)
    parsed_counts = {
        "devices": len(model.devices),
        "connections": len(model.connections),
        "subnets": len(model.subnets),
    }

    # ── サイドカー（確信度・所見・補正内容） ────────────────────────
    sidecar = {
        "source_image": str(image_path),
        "original_size": list(pre.original_size),
        "processed_size": [pre.width, pre.height],
        "preprocess_warnings": pre.warnings,
        "overall_confidence": refined.confidence,
        "counts": {
            "nodes": len(refined.nodes),
            "edges": len(refined.edges),
            "clusters": len(refined.clusters),
        },
        "parsed_counts": parsed_counts,
        "refine": {
            "merged_nodes": report.merged_nodes,
            "dropped_edges": report.dropped_edges,
            "fixed_cluster_members": report.fixed_cluster_members,
            "isolated_nodes": report.isolated_nodes,
        },
        "notes": refined.notes,
        "low_confidence_nodes": [
            {"id": n.id, "hostname": n.hostname, "confidence": n.confidence}
            for n in refined.nodes
            if n.confidence < 0.7
        ],
    }
    sidecar_path = output_dir / f"{stem}.v2d.json"
    sidecar_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return V2dResult(
        yaml_path=yaml_path,
        sidecar_path=sidecar_path,
        diagram=refined,
        report=report,
        yaml_text=yaml_text,
        node_count=len(refined.nodes),
        edge_count=len(refined.edges),
        cluster_count=len(refined.clusters),
        confidence=refined.confidence,
    )
