"""zonelayout（2段階レイアウト）の Stage1/Stage2 コアのテスト。

Stage1（:func:`compute_zone_placement`）は Graphviz ``dot`` を実際に起動して
座標を得るため、``dot`` バイナリが必要（同リポジトリの描画系テストと同前提）。
"""

from __future__ import annotations

from d2v import zonelayout
from d2v.parser import TopologyModel, layout_directive


def _dev(did: str, zone: str) -> dict:
    return {"device-id": did, "device-type": "switch", "zone": zone}


def _conn(a: str, b: str) -> dict:
    return {
        "endpoint": [
            {"device-id": a, "interface-id": "e0"},
            {"device-id": b, "interface-id": "e1"},
        ]
    }


def _model(devices: list[dict], connections: list[dict]) -> TopologyModel:
    device_map = {d["device-id"]: d for d in devices}
    return TopologyModel(
        devices=devices,
        connections=connections,
        subnets=[],
        device_map=device_map,
    )


def _linear_model() -> TopologyModel:
    """3 ゾーンが a-b-c と一列に接続された決定論的モデル。"""
    devices = [
        _dev("d-a", "zone-a"),
        _dev("d-b", "zone-b"),
        _dev("d-c", "zone-c"),
    ]
    connections = [_conn("d-a", "d-b"), _conn("d-b", "d-c")]
    return _model(devices, connections)


# --- Stage1: compute_zone_placement -------------------------------------


def test_placement_groups_zones_into_tiers() -> None:
    placement = zonelayout.compute_zone_placement(_linear_model())

    assert not placement.is_empty()
    # a-b-c の直列なので 3 段に分かれる。
    assert len(placement.tiers) == 3
    assert set(placement.zones) == {"zone-a", "zone-b", "zone-c"}
    # 各ゾーンは 1 段に 1 個ずつ。
    for tier in placement.tiers:
        assert len(tier) == 1
    # tier_of と tiers の整合。
    for ti, tier in enumerate(placement.tiers):
        for zone in tier:
            assert placement.tier_of[zone] == ti


def test_placement_is_deterministic() -> None:
    first = zonelayout.compute_zone_placement(_linear_model())
    second = zonelayout.compute_zone_placement(_linear_model())

    assert first.tiers == second.tiers
    assert first.tier_of == second.tier_of
    assert first.order_in_tier == second.order_in_tier


def test_same_tier_zones_have_distinct_order() -> None:
    # ハブ h に a, b, c がぶら下がる → a/b/c は同段になり左右順が付く。
    devices = [
        _dev("d-hub", "zone-hub"),
        _dev("d-a", "zone-a"),
        _dev("d-b", "zone-b"),
        _dev("d-c", "zone-c"),
    ]
    connections = [_conn("d-hub", "d-a"), _conn("d-hub", "d-b"), _conn("d-hub", "d-c")]
    placement = zonelayout.compute_zone_placement(_model(devices, connections))

    assert not placement.is_empty()
    # 葉ゾーンが同段にまとまる段が存在し、その段内順序が一意である。
    leaf_tier = max(placement.tiers, key=len)
    assert len(leaf_tier) >= 2
    orders = [placement.order_in_tier[z] for z in leaf_tier]
    assert sorted(orders) == list(range(len(leaf_tier)))


# --- フォールバック条件 -------------------------------------------------


def test_single_zone_falls_back_to_empty() -> None:
    devices = [_dev("d-a", "zone-a"), _dev("d-b", "zone-a")]
    connections = [_conn("d-a", "d-b")]
    placement = zonelayout.compute_zone_placement(_model(devices, connections))

    assert placement.is_empty()
    assert placement.zones == []


def test_no_inter_zone_connection_falls_back_to_empty() -> None:
    # ゾーンは 2 つあるが、ゾーンをまたぐ接続が無い。
    devices = [_dev("d-a", "zone-a"), _dev("d-b", "zone-b")]
    connections: list[dict] = []
    placement = zonelayout.compute_zone_placement(_model(devices, connections))

    assert placement.is_empty()


def test_unset_zone_endpoints_are_ignored() -> None:
    devices = [_dev("d-a", "zone-a"), _dev("d-x", "")]
    connections = [_conn("d-a", "d-x")]
    placement = zonelayout.compute_zone_placement(_model(devices, connections))

    # ゾーン未設定端点は集約対象外 → 実質ゾーン間接続ゼロ。
    assert placement.is_empty()


# --- Stage2: zone_constraint_dot / アンカー -----------------------------


def test_anchor_helpers() -> None:
    assert zonelayout.anchor_name("zone-a") == "__za_zone-a"
    decl = zonelayout.anchor_decl("zone-a")
    assert decl.startswith('"__za_zone-a"')
    assert "style=invis" in decl
    assert "shape=point" in decl


def test_constraint_dot_contains_rank_and_invis_edges() -> None:
    placement = zonelayout.compute_zone_placement(_linear_model())
    lines = zonelayout.zone_constraint_dot(placement)
    text = "\n".join(lines)

    # 各段に rank=same 行があり、段は 3 つ。
    assert text.count("rank=same") == len(placement.tiers) == 3
    # 隣接段（2 対）を結ぶ不可視エッジがある。
    assert text.count("[style=invis];") == 2
    # すべてのゾーンのアンカー名が制約に現れる。
    for zone in placement.zones:
        assert zonelayout.anchor_name(zone) in text


def test_constraint_dot_same_tier_grouping() -> None:
    devices = [
        _dev("d-hub", "zone-hub"),
        _dev("d-a", "zone-a"),
        _dev("d-b", "zone-b"),
    ]
    connections = [_conn("d-hub", "d-a"), _conn("d-hub", "d-b")]
    placement = zonelayout.compute_zone_placement(_model(devices, connections))
    lines = zonelayout.zone_constraint_dot(placement)

    # 同段に 2 ゾーン以上ある段では、rank=same に複数アンカーが列挙される。
    multi = [t for t in placement.tiers if len(t) >= 2]
    if multi:
        assert any(
            "rank=same" in ln and ln.count("__za_") >= 2 for ln in lines
        )
    # 横方向の不可視チェイン（rank=same 内の ->）は出力しない。
    assert not any("rank=same" in ln and "->" in ln for ln in lines)


def test_constraint_dot_custom_anchor_mapping() -> None:
    placement = zonelayout.compute_zone_placement(_linear_model())
    mapping = {z: f"anchor_{i}" for i, z in enumerate(placement.zones)}
    text = "\n".join(zonelayout.zone_constraint_dot(placement, mapping))

    for name in mapping.values():
        assert name in text
    # 既定のアンカー名は使われない。
    assert "__za_" not in text


def test_empty_placement_yields_no_constraints() -> None:
    empty = zonelayout.ZonePlacement()
    assert zonelayout.zone_constraint_dot(empty) == []


# --- Stage1（DOT 由来）: compute_zone_placement_from_pairs ----------------


def test_placement_from_pairs_matches_model() -> None:
    # 直列 a-b-c をゾーン対で与えても、モデル経由と同じ段構成になる。
    pairs = [("zone-a", "zone-b"), ("zone-b", "zone-c")]
    placement = zonelayout.compute_zone_placement_from_pairs(pairs)

    assert not placement.is_empty()
    assert len(placement.tiers) == 3
    assert set(placement.zones) == {"zone-a", "zone-b", "zone-c"}


def test_placement_from_pairs_ignores_empty_and_self() -> None:
    pairs = [("zone-a", ""), ("", "zone-b"), ("zone-a", "zone-a")]
    assert zonelayout.compute_zone_placement_from_pairs(pairs).is_empty()


# --- parser.layout_directive: zoned --------------------------------------


def test_layout_directive_zoned_requests_d2vzone() -> None:
    directive = layout_directive({"layout": "zoned"})
    assert "d2vzone" in directive
    assert "図レイアウト指定" in directive


def test_layout_directive_empty_for_no_diagram() -> None:
    assert layout_directive({}) == ""
    assert layout_directive({"layout": "layered"}) == ""
