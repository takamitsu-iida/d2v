"""v2d 評価: 抽出結果を正解トポロジと突き合わせ、一致率を計測する。

Phase 0-F の成功条件（ノード F1 / エッジ F1 / ラベル一致 / ゾーン一致）を自動計測する。
正解には d2v が生成した図の元 YAML（`examples/*.yaml`）を用いる。
また d2v で再描画して元画像と視覚比較するためのラッパーも提供する。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from d2v import parser
from d2v.parser import TopologyModel

_YamlDict = dict[str, Any]


@dataclass
class PRF:
    """precision / recall / F1。"""

    precision: float
    recall: float
    f1: float


@dataclass
class V2dMetrics:
    """v2d 抽出結果と正解の一致指標。"""

    nodes: PRF
    edges: PRF
    device_type_accuracy: float   # マッチしたノードのうち種別一致の割合
    zone_accuracy: float          # マッチしたノードのうちゾーン一致の割合
    loopback_accuracy: float      # 正解に loopback があるマッチノードでの一致割合
    matched_nodes: int
    truth_nodes: int
    pred_nodes: int
    truth_edges: int
    pred_edges: int

    def summary(self) -> str:
        return (
            "== v2d 抽出精度 ==\n"
            f"ノード : P={self.nodes.precision:.2f} R={self.nodes.recall:.2f} "
            f"F1={self.nodes.f1:.2f}  ({self.pred_nodes} 検出 / 正解 {self.truth_nodes})\n"
            f"エッジ : P={self.edges.precision:.2f} R={self.edges.recall:.2f} "
            f"F1={self.edges.f1:.2f}  ({self.pred_edges} 検出 / 正解 {self.truth_edges})\n"
            f"種別一致 : {self.device_type_accuracy:.2f}\n"
            f"ゾーン一致 : {self.zone_accuracy:.2f}\n"
            f"loopback一致 : {self.loopback_accuracy:.2f}\n"
            f"マッチノード数 : {self.matched_nodes}"
        )


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _zone_norm(s: Any) -> str:
    """ゾーン比較用の正規化（英数字のみ・小文字化）。

    図の表示名（例: "WAN / Edge"）とモデルの zone 名（例: "wan-edge"）の
    表記差を吸収する。両者とも "wanedge" となり比較可能になる。
    """
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def _prf(pred: set, truth: set) -> PRF:
    tp = len(pred & truth)
    precision = tp / len(pred) if pred else (1.0 if not truth else 0.0)
    recall = tp / len(truth) if truth else (1.0 if not pred else 0.0)
    denom = precision + recall
    f1 = (2 * precision * recall / denom) if denom else 0.0
    return PRF(precision, recall, f1)


def _edge_set(model: TopologyModel) -> set[frozenset[str]]:
    edges: set[frozenset[str]] = set()
    for conn in model.connections:
        eps = conn.get("endpoint", [])
        if len(eps) != 2:
            continue
        a, b = _norm(eps[0].get("device-id")), _norm(eps[1].get("device-id"))
        if a and b and a != b:
            edges.add(frozenset((a, b)))
    return edges


def compare_models(pred: TopologyModel, truth: TopologyModel) -> V2dMetrics:
    """抽出モデル（pred）と正解モデル（truth）を突き合わせて指標を計算する。

    device-id で照合する（大小文字・前後空白は無視）。ゾーンは表示名と
    モデルの zone 名が異なることがあるため、参考値として一致率を出す。
    """
    pred_by = {_norm(d.get("device-id")): d for d in pred.devices}
    truth_by = {_norm(d.get("device-id")): d for d in truth.devices}
    pred_ids = set(pred_by)
    truth_ids = set(truth_by)

    node_prf = _prf(pred_ids, truth_ids)
    edge_prf = _prf(_edge_set(pred), _edge_set(truth))

    matched = pred_ids & truth_ids
    dt_ok = zone_ok = lb_ok = lb_total = 0
    for nid in matched:
        p, t = pred_by[nid], truth_by[nid]
        if _norm(p.get("device-type")) == _norm(t.get("device-type")):
            dt_ok += 1
        if _zone_norm(p.get("zone")) == _zone_norm(t.get("zone")):
            zone_ok += 1
        if t.get("loopback"):
            lb_total += 1
            if _norm(p.get("loopback")) == _norm(t.get("loopback")):
                lb_ok += 1

    m = len(matched) or 1
    return V2dMetrics(
        nodes=node_prf,
        edges=edge_prf,
        device_type_accuracy=dt_ok / m,
        zone_accuracy=zone_ok / m,
        loopback_accuracy=(lb_ok / lb_total) if lb_total else 1.0,
        matched_nodes=len(matched),
        truth_nodes=len(truth_ids),
        pred_nodes=len(pred_ids),
        truth_edges=len(_edge_set(truth)),
        pred_edges=len(_edge_set(pred)),
    )


def evaluate_files(pred_yaml: str | Path, truth_yaml: str | Path) -> V2dMetrics:
    """2 つの iida-network-model YAML ファイルを比較する。"""
    pred = parser.load_model(Path(pred_yaml))
    truth = parser.load_model(Path(truth_yaml))
    return compare_models(pred, truth)


def rerender_with_d2v(
    yaml_path: str | Path,
    output_dir: str | Path,
    fmt: str = "png",
    max_iter: int = 1,
    threshold: int = 8,
):
    """v2d 出力 YAML を d2v で再描画し、元画像との視覚比較に用いる。

    d2v の生成パイプラインを呼ぶため LLM を使用する。往復ループ
    （画像 → v2d → YAML → d2v → 画像）を閉じるための補助。

    Returns:
        d2v.pipeline.PipelineResult
    """
    from d2v import pipeline as d2v_pipeline

    yaml_path = Path(yaml_path)
    model = parser.load_model(yaml_path)
    text = parser.build_text(
        model.devices, model.connections, model.subnets, model.device_map
    )
    return d2v_pipeline.run(
        topology_text=text,
        output_dir=Path(output_dir),
        stem=yaml_path.stem,
        fmt=fmt,
        max_iterations=max_iter,
        threshold=threshold,
    )
