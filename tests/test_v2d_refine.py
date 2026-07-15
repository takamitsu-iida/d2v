"""v2d 整合性補正（refine）のテスト。"""

from __future__ import annotations

from d2v.v2d.refine import refine
from d2v.v2d.schema import (
    ExtractedCluster,
    ExtractedDiagram,
    ExtractedEdge,
    ExtractedNode,
)


def test_merge_duplicate_hostname_case_insensitive():
    d = ExtractedDiagram(
        nodes=[
            ExtractedNode(id="n1", hostname="fw-01"),
            ExtractedNode(id="n2", hostname="FW-01", loopback="10.0.0.2/32"),
        ],
    )
    refined, report = refine(d)
    assert len(refined.nodes) == 1
    # マージで欠損属性が補完される
    assert refined.nodes[0].loopback == "10.0.0.2/32"
    assert report.merged_nodes


def test_drop_self_loop_and_unknown_and_duplicate_edges():
    d = ExtractedDiagram(
        nodes=[
            ExtractedNode(id="a", hostname="a"),
            ExtractedNode(id="b", hostname="b"),
        ],
        edges=[
            ExtractedEdge(source="a", target="b", source_port="p1", target_port="p2"),
            ExtractedEdge(source="a", target="b", source_port="p1", target_port="p2"),  # 重複
            ExtractedEdge(source="a", target="a"),   # 自己ループ
            ExtractedEdge(source="a", target="zzz"),  # 未定義参照
        ],
    )
    refined, report = refine(d)
    assert len(refined.edges) == 1
    assert len(report.dropped_edges) == 3


def test_zone_filled_from_cluster():
    d = ExtractedDiagram(
        nodes=[ExtractedNode(id="n1", hostname="a")],
        clusters=[ExtractedCluster(id="c1", label="dmz", members=["n1"])],
    )
    refined, _ = refine(d)
    assert refined.nodes[0].zone == "dmz"


def test_unknown_cluster_member_removed():
    d = ExtractedDiagram(
        nodes=[ExtractedNode(id="n1", hostname="a")],
        clusters=[ExtractedCluster(id="c1", label="z", members=["n1", "ghost"])],
    )
    refined, report = refine(d)
    assert refined.clusters[0].members == ["n1"]
    assert report.fixed_cluster_members


def test_isolated_node_detected():
    d = ExtractedDiagram(
        nodes=[
            ExtractedNode(id="a", hostname="a"),
            ExtractedNode(id="b", hostname="b"),
            ExtractedNode(id="c", hostname="iso"),
        ],
        edges=[ExtractedEdge(source="a", target="b")],
    )
    refined, report = refine(d)
    assert any("iso" in m for m in report.isolated_nodes)
    # 孤立ノードは除去されない
    assert len(refined.nodes) == 3


def test_edge_endpoints_remapped_after_merge():
    d = ExtractedDiagram(
        nodes=[
            ExtractedNode(id="n1", hostname="a"),
            ExtractedNode(id="n2", hostname="b"),
            ExtractedNode(id="n2b", hostname="B"),  # n2 と重複
        ],
        edges=[ExtractedEdge(source="n1", target="n2b")],
    )
    refined, _ = refine(d)
    # n2b は n2 にマージされ、エッジは n1->n2 になる
    assert (refined.edges[0].source, refined.edges[0].target) == ("n1", "n2")
