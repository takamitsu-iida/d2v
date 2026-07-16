"""partitioner の境界スタブ集約ロジック（_aggregate_boundary_stubs）のテスト。"""

from __future__ import annotations

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
