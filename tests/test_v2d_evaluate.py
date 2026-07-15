"""v2d 評価指標（compare_models）のテスト。"""

from __future__ import annotations

from d2v.parser import TopologyModel
from d2v.v2d.evaluate import compare_models


def _model(devices, connections) -> TopologyModel:
    device_map = {d["device-id"]: d for d in devices}
    return TopologyModel(
        devices=devices,
        connections=connections,
        subnets=[],
        device_map=device_map,
    )


def _dev(did, dtype="switch", zone=None, loopback=None):
    d = {"device-id": did, "device-type": dtype}
    if zone:
        d["zone"] = zone
    if loopback:
        d["loopback"] = loopback
    return d


def _conn(a, b):
    return {"endpoint": [{"device-id": a, "interface-id": "x"},
                         {"device-id": b, "interface-id": "y"}]}


def test_perfect_match():
    devices = [_dev("r1", "router"), _dev("s1", "switch")]
    conns = [_conn("r1", "s1")]
    truth = _model(devices, conns)
    pred = _model([_dev("r1", "router"), _dev("s1", "switch")], [_conn("r1", "s1")])
    m = compare_models(pred, truth)
    assert m.nodes.f1 == 1.0
    assert m.edges.f1 == 1.0
    assert m.device_type_accuracy == 1.0


def test_missing_node_lowers_recall():
    truth = _model([_dev("a"), _dev("b"), _dev("c")], [])
    pred = _model([_dev("a"), _dev("b")], [])
    m = compare_models(pred, truth)
    assert m.nodes.recall < 1.0
    assert m.nodes.precision == 1.0


def test_edge_direction_ignored():
    truth = _model([_dev("a"), _dev("b")], [_conn("a", "b")])
    pred = _model([_dev("a"), _dev("b")], [_conn("b", "a")])  # 逆順
    m = compare_models(pred, truth)
    assert m.edges.f1 == 1.0


def test_zone_normalization_absorbs_label_diff():
    truth = _model([_dev("a", zone="wan-edge")], [])
    pred = _model([_dev("a", zone="WAN / Edge")], [])
    m = compare_models(pred, truth)
    assert m.zone_accuracy == 1.0


def test_loopback_accuracy_only_counts_truth_with_loopback():
    truth = _model([_dev("a", loopback="10.0.0.1/32"), _dev("b")], [])
    pred = _model([_dev("a", loopback="10.0.0.1/32"), _dev("b")], [])
    m = compare_models(pred, truth)
    assert m.loopback_accuracy == 1.0
