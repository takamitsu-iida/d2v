"""v2d 整合性補正: 抽出した中間表現 ``ExtractedDiagram`` を後処理で整える。

vision LLM 方式では抽出時点でノード/エッジ/クラスタの対応が付いているため、
Phase 4 は「構造の復元」ではなく「抽出結果の整合性チェックと補正」を担う。
OCR/CV 方式を導入する場合は、検出片からの対応付けロジックをこの層に実装する。

補正内容:
  - 同一ホスト名ノードのマージ（id を代表 id に統一）
  - 未定義ノードを参照するエッジ・クラスタメンバーの除去
  - 自己ループ（source==target）の除去
  - 重複エッジ（無向・ポート込み）の除去
  - クラスタ所属からノードの zone を補完
  - 孤立ノード（接続なし）の検出（除去はせず所見に記録）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from d2v.v2d.schema import ExtractedDiagram, ExtractedCluster, ExtractedEdge, ExtractedNode


@dataclass
class RefineReport:
    """整合性補正で行った変更の記録。"""

    merged_nodes: list[str] = field(default_factory=list)
    dropped_edges: list[str] = field(default_factory=list)
    fixed_cluster_members: list[str] = field(default_factory=list)
    isolated_nodes: list[str] = field(default_factory=list)

    def messages(self) -> list[str]:
        msgs: list[str] = []
        for m in self.merged_nodes:
            msgs.append(f"重複ノードをマージ: {m}")
        for e in self.dropped_edges:
            msgs.append(f"不整合なエッジを除去: {e}")
        for c in self.fixed_cluster_members:
            msgs.append(f"未定義のクラスタメンバーを除去: {c}")
        for n in self.isolated_nodes:
            msgs.append(f"孤立ノード（接続なし）: {n}")
        return msgs


def _hostname_key(node: ExtractedNode) -> str | None:
    if not node.hostname:
        return None
    return node.hostname.strip().lower()


def refine(diagram: ExtractedDiagram) -> tuple[ExtractedDiagram, RefineReport]:
    """中間表現を整合性補正し、補正後の表現とレポートを返す。"""
    report = RefineReport()

    # ── 1. 同一ホスト名ノードをマージ（id を代表 id に統一） ──────────
    id_remap: dict[str, str] = {}
    canonical_by_host: dict[str, ExtractedNode] = {}
    kept_nodes: list[ExtractedNode] = []
    for node in diagram.nodes:
        key = _hostname_key(node)
        if key is not None and key in canonical_by_host:
            canonical = canonical_by_host[key]
            id_remap[node.id] = canonical.id
            report.merged_nodes.append(f"{node.id} → {canonical.id} ({node.hostname})")
            # 情報を補完（代表側に欠けている属性を補う）
            if not canonical.loopback and node.loopback:
                canonical.loopback = node.loopback
            if not canonical.zone and node.zone:
                canonical.zone = node.zone
            continue
        id_remap[node.id] = node.id
        if key is not None:
            canonical_by_host[key] = node
        kept_nodes.append(node)

    valid_ids = {n.id for n in kept_nodes}

    # ── 2. クラスタ所属からノードの zone を補完 ─────────────────────
    node_by_id = {n.id: n for n in kept_nodes}
    for cluster in diagram.clusters:
        zone_name = cluster.label or cluster.id
        for member in cluster.members:
            cid = id_remap.get(member, member)
            n = node_by_id.get(cid)
            if n is not None and not n.zone:
                n.zone = zone_name

    # ── 3. エッジの補正（id 付け替え・自己ループ/未定義/重複の除去） ──
    kept_edges: list[ExtractedEdge] = []
    seen_edges: set[tuple] = set()
    for edge in diagram.edges:
        s = id_remap.get(edge.source, edge.source)
        t = id_remap.get(edge.target, edge.target)
        if s not in valid_ids or t not in valid_ids:
            report.dropped_edges.append(
                f"{edge.source}→{edge.target}（未定義ノード参照）"
            )
            continue
        if s == t:
            report.dropped_edges.append(f"{edge.source}→{edge.target}（自己ループ）")
            continue
        # 無向・ポート込みで重複判定
        pair = tuple(sorted([(s, edge.source_port), (t, edge.target_port)]))
        if pair in seen_edges:
            report.dropped_edges.append(f"{edge.source}→{edge.target}（重複）")
            continue
        seen_edges.add(pair)
        kept_edges.append(
            edge.model_copy(update={"source": s, "target": t})
        )

    # ── 4. クラスタメンバーの補正（未定義除去・id 付け替え・重複除去） ─
    kept_clusters: list[ExtractedCluster] = []
    for cluster in diagram.clusters:
        fixed_members: list[str] = []
        for member in cluster.members:
            cid = id_remap.get(member, member)
            if cid not in valid_ids:
                report.fixed_cluster_members.append(
                    f"{cluster.label or cluster.id}: {member}"
                )
                continue
            if cid not in fixed_members:
                fixed_members.append(cid)
        kept_clusters.append(cluster.model_copy(update={"members": fixed_members}))

    # ── 5. 孤立ノードの検出（除去はしない） ─────────────────────────
    connected: set[str] = set()
    for edge in kept_edges:
        connected.add(edge.source)
        connected.add(edge.target)
    for node in kept_nodes:
        if node.id not in connected:
            report.isolated_nodes.append(f"{node.hostname or node.id}")

    refined = ExtractedDiagram(
        nodes=kept_nodes,
        edges=kept_edges,
        clusters=kept_clusters,
        notes=list(diagram.notes) + report.messages(),
        confidence=diagram.confidence,
    )
    return refined, report
