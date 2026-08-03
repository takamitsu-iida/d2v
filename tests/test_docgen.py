"""docgen Phase 0-1: 概要・ゾーン・デバイス台帳・詳細セクションのテスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from d2v.docgen import ALL_SECTIONS, extract, to_json, to_markdown
from d2v.parser import TopologyModel, load_model

SMALL = Path("examples/sample_topology_small.yaml")
MEDIUM = Path("examples/sample_topology_medium.yaml")


@pytest.fixture()
def small_model() -> TopologyModel:
    return load_model(SMALL)


def test_extract_overview_counts(small_model: TopologyModel) -> None:
    ov = extract(small_model)["overview"]
    assert ov["device_count"] == len(small_model.devices)
    expected_zones = len({d.get("zone", "") for d in small_model.devices if d.get("zone")})
    assert ov["zone_count"] == expected_zones
    assert ov["connection_count"] == len(small_model.connections)
    assert ov["subnet_count"] == len(small_model.subnets)


def test_extract_zones_no_duplicates(small_model: TopologyModel) -> None:
    zones = extract(small_model)["zones"]
    names = [z["zone"] for z in zones]
    assert len(names) == len(set(names))


def test_extract_zones_device_counts_sum(small_model: TopologyModel) -> None:
    zones = extract(small_model)["zones"]
    assert sum(z["device_count"] for z in zones) == len(small_model.devices)


def test_extract_zones_each_count_matches_devices(small_model: TopologyModel) -> None:
    for z in extract(small_model)["zones"]:
        assert z["device_count"] == len(z["devices"])


def test_extract_devices_all_present(small_model: TopologyModel) -> None:
    ids = {d["device_id"] for d in extract(small_model)["devices"]}
    assert ids == {d["device-id"] for d in small_model.devices}


def test_extract_devices_asn_field(small_model: TopologyModel) -> None:
    by_id = {d["device_id"]: d for d in extract(small_model)["devices"]}
    for dev in small_model.devices:
        asn = dev.get("asn")
        expected = str(asn) if asn is not None else ""
        assert by_id[dev["device-id"]]["asn"] == expected


def test_extract_title(small_model: TopologyModel) -> None:
    assert extract(small_model, title="test-title")["title"] == "test-title"


def test_to_markdown_contains_all_sections(small_model: TopologyModel) -> None:
    md = to_markdown(extract(small_model))
    assert "## ネットワーク概要" in md
    assert "## ゾーン構成" in md
    assert "## デバイス台帳" in md


def test_to_markdown_sections_filter(small_model: TopologyModel) -> None:
    md = to_markdown(extract(small_model), sections=["overview"])
    assert "## ネットワーク概要" in md
    assert "## ゾーン構成" not in md
    assert "## デバイス台帳" not in md


def test_to_markdown_title_in_output(small_model: TopologyModel) -> None:
    md = to_markdown(extract(small_model, title="MyNet"))
    assert "# MyNet" in md


def test_to_json_valid_structure(small_model: TopologyModel) -> None:
    parsed = json.loads(to_json(extract(small_model)))
    assert "overview" in parsed
    assert "zones" in parsed
    assert "devices" in parsed


def test_all_sections_constant() -> None:
    assert set(ALL_SECTIONS) == {"overview", "zones", "devices",
                                  "interfaces", "connections", "lags", "vlans", "subnets"}


# ---------------------------------------------------------------------------
# Phase 1: インターフェース台帳
# ---------------------------------------------------------------------------


@pytest.fixture()
def medium_model() -> TopologyModel:
    return load_model(MEDIUM)


def test_extract_interfaces_count(small_model: TopologyModel) -> None:
    total_ifaces = sum(len(d.get("interface", [])) for d in small_model.devices)
    assert len(extract(small_model)["interfaces"]) == total_ifaces


def test_extract_interfaces_fields(small_model: TopologyModel) -> None:
    ifaces = extract(small_model)["interfaces"]
    assert all({"device_id", "interface_id", "description", "ip_address",
                "port_type", "speed_gbps"} <= set(r) for r in ifaces)


def test_extract_interfaces_speed_gbps(small_model: TopologyModel) -> None:
    ifaces = extract(small_model)["interfaces"]
    by_id = {(r["device_id"], r["interface_id"]): r for r in ifaces}
    # core-sw-01 GigabitEthernet1/0/23 は port-speed-gbps: 10
    rec = by_id.get(("core-sw-01", "GigabitEthernet1/0/23"))
    assert rec is not None and rec["speed_gbps"] == "10"


# ---------------------------------------------------------------------------
# Phase 1: 物理接続一覧
# ---------------------------------------------------------------------------


def test_extract_connections_count(small_model: TopologyModel) -> None:
    assert len(extract(small_model)["connections"]) == len(small_model.connections)


def test_extract_connections_ip_resolved(small_model: TopologyModel) -> None:
    conns = extract(small_model)["connections"]
    # router-01__fw-01 の接続で両端 IP が解決されている
    router_fw = next(
        (c for c in conns if c["connection_id"] == "router-01__fw-01"), None
    )
    assert router_fw is not None
    assert router_fw["ip_a"] != "" or router_fw["ip_b"] != ""


def test_extract_connections_no_dangling(small_model: TopologyModel) -> None:
    conns = extract(small_model)["connections"]
    assert all(c["device_a"] and c["device_b"] for c in conns)


# ---------------------------------------------------------------------------
# Phase 1: LAG 構成表
# ---------------------------------------------------------------------------


def test_extract_lags_count(small_model: TopologyModel) -> None:
    assert len(extract(small_model)["lags"]) == len(small_model.lags)


def test_extract_lags_members_nonempty(small_model: TopologyModel) -> None:
    for lag in extract(small_model)["lags"]:
        assert lag["members"] != ""


def test_extract_lags_mlag_flag(small_model: TopologyModel) -> None:
    for lag in extract(small_model)["lags"]:
        assert lag["mlag"] in ("有効", "無効", "")


# ---------------------------------------------------------------------------
# Phase 1: VLAN 一覧
# ---------------------------------------------------------------------------


def test_extract_vlans_count(small_model: TopologyModel) -> None:
    assert len(extract(small_model)["vlans"]) == len(small_model.vlans)


def test_extract_vlans_fields(small_model: TopologyModel) -> None:
    vlans = extract(small_model)["vlans"]
    assert all({"vlan_id", "name"} <= set(v) for v in vlans)


# ---------------------------------------------------------------------------
# Phase 1: サブネット表
# ---------------------------------------------------------------------------


def test_extract_subnets_count(small_model: TopologyModel) -> None:
    assert len(extract(small_model)["subnets"]) == len(small_model.subnets)


def test_extract_subnets_fields(small_model: TopologyModel) -> None:
    subnets = extract(small_model)["subnets"]
    assert all({"subnet_id", "prefix", "description"} <= set(s) for s in subnets)


# ---------------------------------------------------------------------------
# Phase 1: Markdown 出力（medium で全セクション）
# ---------------------------------------------------------------------------


def test_to_markdown_all_phase1_sections(medium_model: TopologyModel) -> None:
    md = to_markdown(extract(medium_model))
    for heading in [
        "## インターフェース台帳",
        "## 物理接続一覧",
        "## サブネット / IP 管理表",
    ]:
        assert heading in md


def test_to_markdown_lags_only_when_present(medium_model: TopologyModel) -> None:
    md_medium = to_markdown(extract(medium_model))
    # medium には LAG がないため出力されない
    assert "## LAG 構成" not in md_medium
    md_small = to_markdown(extract(load_model(SMALL)))
    assert "## LAG 構成" in md_small


def test_to_markdown_vlans_only_when_present(medium_model: TopologyModel) -> None:
    # medium には VLAN が定義されていない
    md_medium = to_markdown(extract(medium_model))
    assert "## VLAN 一覧" not in md_medium
    md_small = to_markdown(extract(load_model(SMALL)))
    assert "## VLAN 一覧" in md_small


def test_to_json_phase1_keys(small_model: TopologyModel) -> None:
    parsed = json.loads(to_json(extract(small_model)))
    for key in ("interfaces", "connections", "lags", "vlans", "subnets"):
        assert key in parsed


# ---------------------------------------------------------------------------
# Phase 2: ゾーン間接続マトリクス
# ---------------------------------------------------------------------------


def test_zone_matrix_zones_sorted(small_model: TopologyModel) -> None:
    zm = extract(small_model)["zone_matrix"]
    assert zm["zones"] == sorted(zm["zones"])


def test_zone_matrix_symmetric(small_model: TopologyModel) -> None:
    counts = extract(small_model)["zone_matrix"]["counts"]
    zones = list(counts)
    for z0 in zones:
        for z1 in zones:
            assert counts[z0][z1] == counts[z1][z0]


def test_zone_matrix_intra_zone_counts(small_model: TopologyModel) -> None:
    # small: router-01--fw-01 (wan-edge intra), dmz-sw-01--web-server-01 (dmz intra),
    #        office-sw-01--pc-01 (office intra)
    counts = extract(small_model)["zone_matrix"]["counts"]
    assert counts["wan-edge"]["wan-edge"] == 1
    assert counts["dmz"]["dmz"] == 1
    assert counts["office"]["office"] == 1
    assert counts["core"]["core"] == 0


def test_zone_matrix_inter_zone_counts(small_model: TopologyModel) -> None:
    # fw-01(wan-edge)--core-sw-01(core): 1 本
    # core-sw-01(core)--office-sw-01(office): 3 本（通常 + LAG x2）
    counts = extract(small_model)["zone_matrix"]["counts"]
    assert counts["wan-edge"]["core"] == 1
    assert counts["core"]["office"] == 3


def test_zone_matrix_total_equals_connection_count(small_model: TopologyModel) -> None:
    # 上三角（対角含む）の合計 == 接続数（無向グラフ）
    zm = extract(small_model)["zone_matrix"]
    zones = zm["zones"]
    counts = zm["counts"]
    total = sum(counts[z0][z1] for i, z0 in enumerate(zones) for z1 in zones[i:])
    assert total == len(small_model.connections)


def test_zone_matrix_large(medium_model: TopologyModel) -> None:
    zm = extract(medium_model)["zone_matrix"]
    assert len(zm["zones"]) >= 4
    for z in zm["zones"]:
        assert z in zm["counts"]


def test_to_markdown_zone_matrix_section(small_model: TopologyModel) -> None:
    md = to_markdown(extract(small_model))
    assert "## ゾーン間接続マトリクス" in md
    assert "3" in md  # core--office の 3 本


def test_to_markdown_zone_matrix_dash_for_zero(small_model: TopologyModel) -> None:
    md = to_markdown(extract(small_model), sections=["zone_matrix"])
    assert "-" in md  # ゼロは "-" 表示


def test_all_sections_constant() -> None:
    assert set(ALL_SECTIONS) == {
        "overview", "zones", "zone_matrix", "devices",
        "interfaces", "connections", "lags", "vlans", "subnets",
    }
