"""diff（意味的 diff）Phase 0: 構造差分エンジンのテスト。"""

from __future__ import annotations

import pytest
from rich.console import Console

from d2v.diff import (
    AttrChange,
    NodeChange,
    TopologyDiff,
    build_diff_dot,
    build_impact_dot,
    compare,
    impact,
    render_diff,
    render_diff_diagram,
    render_impact,
    summarize,
)
from d2v.parser import TopologyModel


def _model(devices=None, connections=None, subnets=None) -> TopologyModel:
    devices = devices or []
    device_map = {d["device-id"]: d for d in devices if d.get("device-id")}
    return TopologyModel(
        devices=devices,
        connections=connections or [],
        subnets=subnets or [],
        device_map=device_map,
    )


def _dev(did, **kw):
    d = {"device-id": did}
    d.update({k.replace("_", "-"): v for k, v in kw.items()})
    return d


def _conn(cid, a, b):
    return {
        "connection-id": cid,
        "endpoint": [
            {"device-id": a[0], "interface-id": a[1]},
            {"device-id": b[0], "interface-id": b[1]},
        ],
    }


def test_identical_models_have_empty_diff():
    devices = [_dev("r1", zone="core"), _dev("r2", zone="core")]
    conns = [_conn("c1", ("r1", "g0"), ("r2", "g0"))]
    m1 = _model(devices, conns)
    m2 = _model([_dev("r1", zone="core"), _dev("r2", zone="core")], [_conn("c1", ("r1", "g0"), ("r2", "g0"))])
    diff = compare(m1, m2)
    assert diff.is_empty()


def test_node_added_and_removed():
    before = _model([_dev("r1"), _dev("r2")])
    after = _model([_dev("r1"), _dev("r3")])
    diff = compare(before, after)
    assert diff.nodes_added == ["r3"]
    assert diff.nodes_removed == ["r2"]
    assert not diff.is_empty()


def test_node_attribute_change():
    before = _model([_dev("r1", zone="core", loopback="10.0.0.1/32")])
    after = _model([_dev("r1", zone="edge", loopback="10.0.0.1/32")])
    diff = compare(before, after)
    assert len(diff.nodes_changed) == 1
    nc = diff.nodes_changed[0]
    assert nc.device_id == "r1"
    fields = {c.field: (c.before, c.after) for c in nc.changes}
    assert fields["zone"] == ("core", "edge")
    assert "loopback" not in fields  # 変わっていないので出ない


def test_interface_set_change_detected():
    before = _model([_dev("r1", interface=[{"interface-id": "g0"}])])
    after = _model([_dev("r1", interface=[{"interface-id": "g0"}, {"interface-id": "g1"}])])
    diff = compare(before, after)
    nc = diff.nodes_changed[0]
    fields = {c.field: (c.before, c.after) for c in nc.changes}
    assert fields["interfaces"] == ("g0", "g0, g1")


def test_edge_added_and_removed():
    before = _model(
        [_dev("r1"), _dev("r2"), _dev("r3")],
        [_conn("c1", ("r1", "g0"), ("r2", "g0"))],
    )
    after = _model(
        [_dev("r1"), _dev("r2"), _dev("r3")],
        [_conn("c2", ("r2", "g1"), ("r3", "g0"))],
    )
    diff = compare(before, after)
    assert diff.edges_added == ["c2"]
    assert diff.edges_removed == ["c1"]


def test_edge_rename_only_is_not_a_change():
    # 同一端点・ポートで connection-id だけ変えても差分なし（endpoint キーで識別）
    before = _model([_dev("r1"), _dev("r2")], [_conn("old-name", ("r1", "g0"), ("r2", "g0"))])
    after = _model([_dev("r1"), _dev("r2")], [_conn("new-name", ("r1", "g0"), ("r2", "g0"))])
    diff = compare(before, after)
    assert diff.edges_added == []
    assert diff.edges_removed == []


def test_edge_undirected_matching():
    # 端点の順序が逆でも同一リンクとして扱う
    before = _model([_dev("r1"), _dev("r2")], [_conn("c1", ("r1", "g0"), ("r2", "g0"))])
    after = _model([_dev("r1"), _dev("r2")], [_conn("c1", ("r2", "g0"), ("r1", "g0"))])
    diff = compare(before, after)
    assert diff.is_empty()


def test_zone_added_removed():
    before = _model([_dev("r1", zone="core"), _dev("r2", zone="dmz")])
    after = _model([_dev("r1", zone="core"), _dev("r2", zone="edge")])
    diff = compare(before, after)
    assert diff.zones_added == ["edge"]
    assert diff.zones_removed == ["dmz"]


def test_subnet_added_removed():
    before = _model(subnets=[{"subnet-id": "a", "prefix": "10.0.0.0/24"}])
    after = _model(subnets=[{"subnet-id": "b", "prefix": "10.0.1.0/24"}])
    diff = compare(before, after)
    assert diff.subnets_added == ["b (10.0.1.0/24)"]
    assert diff.subnets_removed == ["a (10.0.0.0/24)"]


def test_render_diff_empty():
    console = Console(record=True, width=80)
    console.print(render_diff(compare(_model([_dev("r1")]), _model([_dev("r1")]))))
    assert "変化はありません" in console.export_text()


def test_render_diff_shows_changes():
    before = _model([_dev("r1", zone="core"), _dev("r2")], [_conn("c1", ("r1", "g0"), ("r2", "g0"))])
    after = _model([_dev("r1", zone="edge"), _dev("r3")])
    diff = compare(before, after)
    console = Console(record=True, width=100)
    console.print(render_diff(diff))
    out = console.export_text()
    assert "ノード追加" in out and "r3" in out
    assert "ノード削除" in out and "r2" in out
    assert "ノード変更" in out and "core → edge" in out


def test_topology_diff_is_empty_default():
    assert TopologyDiff().is_empty()
    assert not TopologyDiff(nodes_added=["x"]).is_empty()


# ---------------------------------------------------------------------------
# Phase 1: 差分図（DOT 生成・レンダリング）
# ---------------------------------------------------------------------------


def test_build_diff_dot_colors_and_structure():
    before = _model(
        [_dev("r1", zone="core", device_type="router"),
         _dev("r2", zone="core", device_type="switch")],
        [_conn("c1", ("r1", "g0"), ("r2", "g0"))],
    )
    after = _model(
        [_dev("r1", zone="edge", device_type="router"),
         _dev("r3", zone="core", device_type="switch")],
        [_conn("c2", ("r1", "g0"), ("r3", "g0"))],
    )
    diff = compare(before, after)
    dot = build_diff_dot(before, after, diff)

    assert dot.startswith("digraph diff {")
    assert dot.rstrip().endswith("}")
    # 追加ノード r3（緑）・削除ノード r2（赤）・変更ノード r1（橙）
    assert '"r3"' in dot and "#137333" in dot   # added color
    assert '"r2"' in dot and "#C5221F" in dot   # removed color
    assert '"r1"' in dot and "#E37400" in dot   # changed color
    # 変更ノードは変更フィールドをラベルに含む
    assert "変更: zone" in dot
    # cluster 化されている
    assert "subgraph cluster_z" in dot
    # 凡例
    assert "cluster_legend" in dot
    # アイコン
    assert "🌐" in dot and "🔀" in dot


def test_build_diff_dot_empty_diff_has_only_unchanged():
    m = _model([_dev("r1", device_type="router")], [])
    dot = build_diff_dot(m, m, compare(m, m))
    # r1 ノードは変更なしの色（淡灰）で描画される
    r1_line = next(ln for ln in dot.splitlines() if ln.strip().startswith('"r1"'))
    assert "#F1F3F4" in r1_line
    assert "#137333" not in r1_line  # 追加色ではない


def test_render_diff_diagram_writes_file(tmp_path):
    pytest.importorskip("graphviz")
    from d2v.errors import GraphvizNotFoundError

    before = _model([_dev("r1", zone="core"), _dev("r2", zone="core")],
                    [_conn("c1", ("r1", "g0"), ("r2", "g0"))])
    after = _model([_dev("r1", zone="core"), _dev("r3", zone="core")],
                   [_conn("c2", ("r1", "g0"), ("r3", "g0"))])
    diff = compare(before, after)
    try:
        out = render_diff_diagram(before, after, diff, tmp_path, stem="d", fmt="png")
    except GraphvizNotFoundError:
        pytest.skip("Graphviz 未インストール")
    assert out.exists()
    assert (tmp_path / "d.dot").exists()  # DOT ソースも保存される


# ---------------------------------------------------------------------------
# Phase 2: LLM 自然言語サマリ（--summarize）
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


def test_summarize_fills_summary():
    before = _model([_dev("r1")])
    after = _model([_dev("r1"), _dev("r2")])
    diff = compare(before, after)
    llm = _FakeLLM("- ノード追加: r2")
    result = summarize(diff, llm=llm)
    assert result.summary == "- ノード追加: r2"
    # 構造差分は不変
    assert result.nodes_added == ["r2"]
    assert len(llm.calls) == 1
    # diff の内容が LLM に渡っている
    assert "r2" in llm.calls[0][1]


def test_summarize_strips_code_fence():
    before = _model([_dev("r1")])
    after = _model([_dev("r1"), _dev("r2")])
    diff = compare(before, after)
    llm = _FakeLLM("```\n- ノード追加: r2\n```")
    result = summarize(diff, llm=llm)
    assert result.summary.strip() == "- ノード追加: r2"


def test_summarize_empty_diff_skips_llm():
    m = _model([_dev("r1")])
    diff = compare(m, m)
    llm = _FakeLLM("何か")
    result = summarize(diff, llm=llm)
    assert result.summary == ""
    assert llm.calls == []  # 変化なしでは LLM を呼ばない


def test_render_diff_shows_summary():
    before = _model([_dev("r1")])
    after = _model([_dev("r1"), _dev("r2")])
    diff = summarize(compare(before, after), llm=_FakeLLM("- ノード追加: r2"))
    console = Console(record=True, width=100)
    console.print(render_diff(diff))
    assert "ノード追加: r2" in console.export_text()


# ---------------------------------------------------------------------------
# Phase 3: 影響分析（blast radius）
# ---------------------------------------------------------------------------


def _line(n):
    """n1-n2-...-nN の直鎖トポロジ。"""
    devices = [_dev(f"n{i}") for i in range(1, n + 1)]
    conns = [
        _conn(f"c{i}", (f"n{i}", f"p{i}a"), (f"n{i+1}", f"p{i}b"))
        for i in range(1, n)
    ]
    return _model(devices, conns)


def _ring(n):
    devices = [_dev(f"n{i}") for i in range(1, n + 1)]
    conns = [
        _conn(f"c{i}", (f"n{i}", f"p{i}a"), (f"n{i % n + 1}", f"p{i}b"))
        for i in range(1, n + 1)
    ]
    return _model(devices, conns)


def test_impact_removing_leaf_isolates_nothing():
    # n1-n2-n3、末端 n3 を落としても他は繋がったまま
    m = _line(3)
    report = impact(m, removed_devices=["n3"])
    assert report.unreachable == []
    assert report.is_isolating() is False
    assert set(report.reachable) == {"n1", "n2"}


def test_impact_removing_articulation_isolates_partition():
    # n1-n2-n3-n4、中間 n2 を落とすと n1 が孤立、n3/n4 が残る
    m = _line(4)
    report = impact(m, removed_devices=["n2"])
    assert report.is_isolating() is True
    # 最大成分 {n3,n4} が到達可能、n1 が到達不能
    assert report.unreachable == ["n1"]
    assert set(report.reachable) == {"n3", "n4"}
    assert report.components == 2


def test_impact_removing_edge_partitions_chain():
    # 直鎖の中央リンクを落とすと二分される
    m = _line(4)
    report = impact(m, removed_edges=[("n2", "n3")])
    assert report.is_isolating() is True
    assert report.removed_edges == ["n2 <-> n3"]
    # 最大成分同数(2/2)なので小さい device-id を含む方 {n1,n2} が core
    assert set(report.reachable) == {"n1", "n2"}
    assert report.unreachable == ["n3", "n4"]


def test_impact_ring_is_resilient():
    # 環状では 1 台落としても分断されない
    m = _ring(4)
    report = impact(m, removed_devices=["n1"])
    assert report.unreachable == []


def test_render_impact_text():
    report = impact(_line(4), removed_devices=["n2"])
    console = Console(record=True, width=80)
    console.print(render_impact(report))
    out = console.export_text()
    assert "blast radius" in out
    assert "n2" in out
    assert "到達不能" in out and "n1" in out


def test_build_impact_dot_highlights():
    m = _line(4)
    report = impact(m, removed_devices=["n2"])
    dot = build_impact_dot(m, report)
    assert dot.startswith("digraph impact {")
    # 除去ノード n2 は ✖ マーク付き
    assert "✖" in dot
    # 到達不能 n1 は赤系、到達可能 n3/n4 は緑系
    assert "#FCE8E6" in dot  # unreachable fill
    assert "#E6F4EA" in dot  # reachable fill


def test_impact_no_removal_all_reachable():
    m = _ring(3)
    report = impact(m)
    assert report.unreachable == []
    assert set(report.reachable) == {"n1", "n2", "n3"}
    assert report.components == 1
