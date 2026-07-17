"""partitioner の境界スタブ集約ロジック（_aggregate_boundary_stubs）のテスト。"""

from __future__ import annotations

import pytest

from d2v import partitioner
from d2v.parser import TopologyModel


def _dev(did: str, zone: str) -> dict:
    return {"device-id": did, "device-type": "switch", "zone": zone}


def _conn(a: str, b: str) -> dict:
    return {
        "endpoint": [
            {"device-id": a, "interface-id": "e0"},
            {"device-id": b, "interface-id": "e1"},
        ]
    }


def _model(devices: list[dict]) -> TopologyModel:
    device_map = {d["device-id"]: d for d in devices}
    return TopologyModel(
        devices=devices,
        connections=[],
        subnets=[],
        device_map=device_map,
    )


def test_aggregates_zone_over_threshold():
    # ゾーン b は 4 台（既定しきい値 3 超）→ 集約。ゾーン c は 1 台 → 個別。
    devices = [
        _dev("a1", "a"), _dev("a2", "a"),
        _dev("b1", "b"), _dev("b2", "b"), _dev("b3", "b"), _dev("b4", "b"),
        _dev("c1", "c"),
    ]
    model = _model(devices)
    zone_ids = {"a1", "a2"}
    boundary = [
        _conn("a1", "b1"), _conn("a1", "b2"), _conn("a1", "b3"),
        _conn("a2", "b4"),
        _conn("a1", "c1"),
    ]

    bp = partitioner._aggregate_boundary_stubs(model, zone_ids, boundary)

    assert bp.aggregated_zones_ordered == ["b"]
    assert bp.individual_ext == ["c1"]
    assert set(bp.ext_zone_devices["b"]) == {"b1", "b2", "b3", "b4"}
    # 個別の境界接続は a1<->c1 の 1 本のみ
    assert len(bp.indiv_boundary) == 1
    # 集約境界: (a1, b)=3 本, (a2, b)=1 本
    assert bp.agg_boundary[("a1", "b")] == 3
    assert bp.agg_boundary[("a2", "b")] == 1


def test_no_aggregation_under_threshold():
    # ゾーン b が 2 台のみ（しきい値以下）→ 集約せず個別表示
    devices = [_dev("a1", "a"), _dev("b1", "b"), _dev("b2", "b")]
    model = _model(devices)
    zone_ids = {"a1"}
    boundary = [_conn("a1", "b1"), _conn("a1", "b2")]

    bp = partitioner._aggregate_boundary_stubs(model, zone_ids, boundary)

    assert bp.aggregated_zones_ordered == []
    assert set(bp.individual_ext) == {"b1", "b2"}
    assert len(bp.indiv_boundary) == 2
    assert bp.agg_boundary == {}


def test_empty_boundary_yields_empty_plan():
    model = _model([_dev("a1", "a")])
    bp = partitioner._aggregate_boundary_stubs(model, {"a1"}, [])
    assert bp.individual_ext == []
    assert bp.aggregated_zones_ordered == []
    assert bp.indiv_boundary == []
    assert bp.agg_boundary == {}


# ---------------------------------------------------------------------------
# 決定論 focus 図（build_focus_dot / _focus_data）
# ---------------------------------------------------------------------------


def _linear_model() -> TopologyModel:
    """a - b - c - d の一直線トポロジ（各ノードは別 device-type/zone）。"""
    devices = [
        {"device-id": "a", "device-name": "Node A", "device-type": "router", "zone": "z1"},
        {"device-id": "b", "device-name": "Node B", "device-type": "firewall", "zone": "z1"},
        {"device-id": "c", "device-name": "Node C", "device-type": "switch", "zone": "z2"},
        {"device-id": "d", "device-name": "Node D", "device-type": "server", "zone": "z2"},
    ]
    connections = [_conn("a", "b"), _conn("b", "c"), _conn("c", "d")]
    connections[0]["connection-id"] = "a__b"
    connections[1]["connection-id"] = "b__c"
    connections[2]["connection-id"] = "c__d"
    return TopologyModel(
        devices=devices,
        connections=connections,
        subnets=[],
        device_map={d["device-id"]: d for d in devices},
    )


def test_build_focus_dot_is_deterministic():
    model = _linear_model()
    dot1 = partitioner.build_focus_dot(model, "b", hops=1)
    dot2 = partitioner.build_focus_dot(model, ["b"], hops=1)
    assert dot1 is not None
    # 同一入力（str/list を正規化）→ 完全一致する DOT（冪等）
    assert dot1 == dot2
    assert dot1.startswith("digraph focus {")
    assert dot1.rstrip().endswith("}")


def test_build_focus_dot_returns_none_for_missing_device():
    model = _linear_model()
    assert partitioner.build_focus_dot(model, "nonexistent", hops=1) is None
    # 一部でも存在しなければ None
    assert partitioner.build_focus_dot(model, ["b", "nope"], hops=1) is None


def test_build_focus_dot_highlights_focus_and_scope():
    model = _linear_model()
    # b を中心に 1 ホップ → a, b, c を含み d は含まない
    dot = partitioner.build_focus_dot(model, "b", hops=1)
    assert dot is not None
    assert '"a"' in dot and '"b"' in dot and '"c"' in dot
    assert '"d"' not in dot
    # 注目ノード b は強調（★注目）され、双方向ジャンプ用 id を持つ
    assert "★注目・0 ホップ" in dot
    assert 'id="device:b"' in dot
    # c は境界ノード（この先に d が省略されている）
    assert "この先 1 台省略" in dot


def test_build_focus_dot_hop_labels_and_edges():
    model = _linear_model()
    dot = partitioner.build_focus_dot(model, "b", hops=1)
    assert dot is not None
    # a と c は 1 ホップ
    assert "1 ホップ" in dot
    # intra 接続のみ描画（a-b, b-c）。範囲外の c-d は出ない
    assert '"a" -> "b"' in dot
    assert '"b" -> "c"' in dot
    assert 'tooltip="c__d"' not in dot
    # zone はゆるく cluster 化される
    assert "subgraph cluster_z" in dot


def test_focus_data_shared_between_paths():
    model = _linear_model()
    data = partitioner._focus_data(model, "b", hops=1)
    assert data is not None
    assert data.focus_ids == ["b"]
    assert data.included == {"a", "b", "c"}
    assert data.dist["b"] == 0 and data.dist["a"] == 1 and data.dist["c"] == 1
    # c は範囲外の d を隣接に持つため境界（省略 1 台）
    assert data.truncated == {"c": 1}
    # focus_plan も同じ構造データから生成され、None にならない
    plan = partitioner.focus_plan(model, "b", hops=1)
    assert plan is not None
    assert "3 台" in plan.title


def test_focus_hops_zero_shows_only_specified_nodes():
    model = _linear_model()
    # hops=0 → 指定した a, b のみ（相互接続 a-b だけ）を抽出し、c/d は含まない
    data = partitioner._focus_data(model, ["a", "b"], hops=0)
    assert data is not None
    assert data.included == {"a", "b"}
    assert [c["connection-id"] for c in data.intra] == ["a__b"]

    plan = partitioner.focus_plan(model, ["a", "b"], hops=0)
    assert plan is not None
    assert plan.key == "focus-a-b-0hop"
    assert "のみ" in plan.title

    dot = partitioner.build_focus_dot(model, ["a", "b"], hops=0)
    assert dot is not None
    assert '"a"' in dot and '"b"' in dot
    assert '"c"' not in dot and '"d"' not in dot


def test_focus_hops_zero_draws_indirect_edge_via_omitted_node():
    # a - c - b（c は共通の中継ノード）。a と b を hops=0 で指定すると、
    # a と b は直接リンクは無いが c を介して繋がるため間接エッジになる。
    devices = [
        {"device-id": "a", "device-name": "A", "device-type": "router", "zone": "z"},
        {"device-id": "b", "device-name": "B", "device-type": "router", "zone": "z"},
        {"device-id": "c", "device-name": "C", "device-type": "switch", "zone": "z"},
    ]
    conns = [_conn("a", "c"), _conn("c", "b")]
    conns[0]["connection-id"] = "a__c"
    conns[1]["connection-id"] = "c__b"
    model = TopologyModel(
        devices=devices,
        connections=conns,
        subnets=[],
        device_map={d["device-id"]: d for d in devices},
    )

    data = partitioner._focus_data(model, ["a", "b"], hops=0)
    assert data is not None
    assert data.included == {"a", "b"}
    assert data.intra == []
    # a と b は c（省略ノード）を介して繋がる → 間接接続 1 組
    assert data.indirect == [("a", "b", "c", 1)]

    dot = partitioner.build_focus_dot(model, ["a", "b"], hops=0)
    assert dot is not None
    # 破線・矢印なしの間接エッジが描かれ、c は描画されない
    assert '"c"' not in dot
    assert '"a" -> "b"' in dot
    assert "style=dashed" in dot
    assert "c 経由" in dot


def test_focus_no_indirect_edge_when_directly_connected():
    # a - b の直接リンクがある場合は間接エッジを作らない
    model = _linear_model()
    data = partitioner._focus_data(model, ["a", "b"], hops=0)
    assert data is not None
    assert [c["connection-id"] for c in data.intra] == ["a__b"]
    assert data.indirect == []


def test_focus_data_rejects_negative_hops():
    model = _linear_model()
    with pytest.raises(ValueError):
        partitioner._focus_data(model, "b", hops=-1)
