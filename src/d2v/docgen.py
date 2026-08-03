"""iida-network-model YAML から設計書（Markdown / JSON）を生成する。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from d2v.parser import TopologyModel

ALL_SECTIONS: list[str] = [
    "overview", "zones", "zone_matrix", "devices",
    "interfaces", "connections", "lags", "vlans", "subnets",
]

# ---------------------------------------------------------------------------
# 抽出
# ---------------------------------------------------------------------------


def extract(model: TopologyModel, title: str = "") -> dict[str, Any]:
    """TopologyModel から設計書データ辞書を生成する。"""
    return {
        "title": title,
        "overview": _extract_overview(model),
        "zones": _extract_zones(model),
        "zone_matrix": _extract_zone_matrix(model),
        "devices": _extract_devices(model),
        "interfaces": _extract_interfaces(model),
        "connections": _extract_connections(model),
        "lags": _extract_lags(model),
        "vlans": _extract_vlans(model),
        "subnets": _extract_subnets(model),
    }


def _extract_overview(model: TopologyModel) -> dict[str, int]:
    zones = {d.get("zone", "") for d in model.devices if d.get("zone")}
    return {
        "device_count": len(model.devices),
        "zone_count": len(zones),
        "connection_count": len(model.connections),
        "subnet_count": len(model.subnets),
    }


def _extract_zones(model: TopologyModel) -> list[dict[str, Any]]:
    zone_devices: dict[str, list[dict[str, str]]] = defaultdict(list)
    no_zone: list[dict[str, str]] = []
    for dev in model.devices:
        zone = dev.get("zone", "")
        entry = {
            "id": dev.get("device-id", ""),
            "name": dev.get("device-name", ""),
            "type": dev.get("device-type", ""),
        }
        if zone:
            zone_devices[zone].append(entry)
        else:
            no_zone.append(entry)

    result = [
        {
            "zone": zname,
            "device_count": len(devs),
            "devices": devs,
        }
        for zname, devs in sorted(zone_devices.items())
    ]
    if no_zone:
        result.append({"zone": "(未分類)", "device_count": len(no_zone), "devices": no_zone})
    return result


def _extract_devices(model: TopologyModel) -> list[dict[str, str]]:
    return [
        {
            "device_id": d.get("device-id", ""),
            "device_name": d.get("device-name", ""),
            "device_type": d.get("device-type", ""),
            "zone": d.get("zone", ""),
            "loopback": d.get("loopback", ""),
            "asn": str(d["asn"]) if d.get("asn") is not None else "",
        }
        for d in model.devices
    ]


def _extract_interfaces(model: TopologyModel) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for dev in model.devices:
        did = dev.get("device-id", "")
        for iface in dev.get("interface", []):
            rows.append({
                "device_id": did,
                "interface_id": iface.get("interface-id", ""),
                "description": iface.get("description", ""),
                "ip_address": iface.get("ip-address", ""),
                "port_type": iface.get("port-type", ""),
                "speed_gbps": str(iface["port-speed-gbps"]) if iface.get("port-speed-gbps") is not None else "",
            })
    return rows


def _iface_ip(model: TopologyModel, device_id: str, interface_id: str) -> str:
    """device_map からインターフェースの IP アドレスを解決する。"""
    for iface in model.device_map.get(device_id, {}).get("interface", []):
        if iface.get("interface-id") == interface_id:
            return iface.get("ip-address", "")
    return ""


def _extract_connections(model: TopologyModel) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for conn in model.connections:
        endpoints = conn.get("endpoint", [])
        if len(endpoints) != 2:
            continue
        ep0, ep1 = endpoints[0], endpoints[1]
        d0, i0 = ep0.get("device-id", ""), ep0.get("interface-id", "")
        d1, i1 = ep1.get("device-id", ""), ep1.get("interface-id", "")
        rows.append({
            "connection_id": conn.get("connection-id", ""),
            "device_a": d0,
            "interface_a": i0,
            "ip_a": _iface_ip(model, d0, i0),
            "device_b": d1,
            "interface_b": i1,
            "ip_b": _iface_ip(model, d1, i1),
        })
    return rows


def _extract_lags(model: TopologyModel) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for lag in model.lags:
        members = ", ".join(
            m.get("interface-id", "") for m in lag.get("member-interface", [])
        )
        mlag = lag.get("mlag", {})
        mlag_str = "有効" if mlag.get("enabled") else ("無効" if "enabled" in mlag else "")
        rows.append({
            "device_id": lag.get("device-id", ""),
            "lag_id": lag.get("lag-id", ""),
            "mode": lag.get("mode", ""),
            "members": members,
            "mlag": mlag_str,
        })
    return rows


def _extract_vlans(model: TopologyModel) -> list[dict[str, str]]:
    return [
        {
            "vlan_id": str(v.get("vlan-id", "")),
            "name": v.get("name", ""),
        }
        for v in model.vlans
    ]


def _extract_subnets(model: TopologyModel) -> list[dict[str, str]]:
    return [
        {
            "subnet_id": s.get("subnet-id", ""),
            "prefix": s.get("prefix", ""),
            "description": s.get("description", ""),
        }
        for s in model.subnets
    ]


def _extract_zone_matrix(model: TopologyModel) -> dict[str, Any]:
    """ゾーン間の物理接続本数を対称行列として集計する。"""
    zones = sorted({d.get("zone", "") for d in model.devices if d.get("zone")})
    # 全セルを 0 で初期化
    counts: dict[str, dict[str, int]] = {z: {z2: 0 for z2 in zones} for z in zones}
    for conn in model.connections:
        endpoints = conn.get("endpoint", [])
        if len(endpoints) != 2:
            continue
        z0 = model.zone_of(endpoints[0].get("device-id", ""))
        z1 = model.zone_of(endpoints[1].get("device-id", ""))
        if z0 in counts and z1 in counts:
            counts[z0][z1] += 1
            if z0 != z1:
                counts[z1][z0] += 1
    return {"zones": zones, "counts": counts}


# ---------------------------------------------------------------------------
# Markdown 生成
# ---------------------------------------------------------------------------


def to_markdown(data: dict[str, Any], sections: list[str] | None = None) -> str:
    """設計書データを Markdown 文字列に整形する。"""
    active = set(sections) if sections else set(ALL_SECTIONS)
    parts: list[str] = []

    if data.get("title"):
        parts.append(f"# {data['title']}\n")
    if "overview" in active:
        parts.append(_md_overview(data["overview"]))
    if "zones" in active:
        parts.append(_md_zones(data["zones"]))
    if "zone_matrix" in active and data.get("zone_matrix", {}).get("zones"):
        parts.append(_md_zone_matrix(data["zone_matrix"]))
    if "devices" in active:
        parts.append(_md_devices(data["devices"]))
    if "interfaces" in active and data.get("interfaces"):
        parts.append(_md_interfaces(data["interfaces"]))
    if "connections" in active and data.get("connections"):
        parts.append(_md_connections(data["connections"]))
    if "lags" in active and data.get("lags"):
        parts.append(_md_lags(data["lags"]))
    if "vlans" in active and data.get("vlans"):
        parts.append(_md_vlans(data["vlans"]))
    if "subnets" in active and data.get("subnets"):
        parts.append(_md_subnets(data["subnets"]))

    return "\n\n".join(parts)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    sep = " | ".join(["---"] * len(headers))
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + sep + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def _md_overview(ov: dict[str, int]) -> str:
    rows = [
        ["デバイス数", str(ov["device_count"])],
        ["ゾーン数", str(ov["zone_count"])],
        ["物理接続数", str(ov["connection_count"])],
        ["サブネット数", str(ov["subnet_count"])],
    ]
    return "## ネットワーク概要\n\n" + _md_table(["項目", "値"], rows)


def _md_zones(zones: list[dict[str, Any]]) -> str:
    rows = [
        [
            z["zone"],
            str(z["device_count"]),
            ", ".join(f"{d['id']}({d['type']})" for d in z["devices"]),
        ]
        for z in zones
    ]
    return "## ゾーン構成\n\n" + _md_table(["ゾーン名", "デバイス数", "所属デバイス（種別）"], rows)


def _md_devices(devices: list[dict[str, str]]) -> str:
    rows = [
        [d["device_id"], d["device_name"], d["device_type"], d["zone"], d["loopback"], d["asn"]]
        for d in devices
    ]
    return "## デバイス台帳\n\n" + _md_table(
        ["デバイス ID", "デバイス名", "種別", "ゾーン", "ループバック IP", "ASN"],
        rows,
    )


def _md_interfaces(interfaces: list[dict[str, str]]) -> str:
    rows = [
        [i["device_id"], i["interface_id"], i["description"],
         i["ip_address"], i["port_type"], i["speed_gbps"]]
        for i in interfaces
    ]
    return "## インターフェース台帳\n\n" + _md_table(
        ["デバイス ID", "インターフェース ID", "説明", "IP アドレス", "ポート種別", "速度(Gbps)"],
        rows,
    )


def _md_connections(connections: list[dict[str, str]]) -> str:
    rows = [
        [c["connection_id"],
         c["device_a"], c["interface_a"], c["ip_a"],
         c["device_b"], c["interface_b"], c["ip_b"]]
        for c in connections
    ]
    return "## 物理接続一覧\n\n" + _md_table(
        ["接続 ID", "デバイス A", "IF A", "IP A", "デバイス B", "IF B", "IP B"],
        rows,
    )


def _md_lags(lags: list[dict[str, str]]) -> str:
    rows = [
        [l["device_id"], l["lag_id"], l["mode"], l["members"], l["mlag"]]
        for l in lags
    ]
    return "## LAG 構成\n\n" + _md_table(
        ["デバイス ID", "LAG ID", "モード", "メンバー IF", "MLAG"],
        rows,
    )


def _md_vlans(vlans: list[dict[str, str]]) -> str:
    rows = [[v["vlan_id"], v["name"]] for v in vlans]
    return "## VLAN 一覧\n\n" + _md_table(["VLAN ID", "名前"], rows)


def _md_subnets(subnets: list[dict[str, str]]) -> str:
    rows = [[s["subnet_id"], s["prefix"], s["description"]] for s in subnets]
    return "## サブネット / IP 管理表\n\n" + _md_table(
        ["サブネット ID", "プレフィックス", "説明"],
        rows,
    )


def _md_zone_matrix(zm: dict[str, Any]) -> str:
    zones: list[str] = zm["zones"]
    counts: dict[str, dict[str, int]] = zm["counts"]
    headers = ["ゾーン"] + zones
    rows = [
        [z] + [str(counts[z][z2]) if counts[z][z2] else "-" for z2 in zones]
        for z in zones
    ]
    return "## ゾーン間接続マトリクス\n\n" + _md_table(headers, rows)


# ---------------------------------------------------------------------------
# JSON 生成
# ---------------------------------------------------------------------------


def to_json(data: dict[str, Any]) -> str:
    """設計書データを JSON 文字列に整形する。"""
    return json.dumps(data, ensure_ascii=False, indent=2)
