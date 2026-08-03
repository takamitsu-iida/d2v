"""ゾーン限定図（zone-only）の生成と zone_plan()。"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from d2v import parser
from d2v.config import settings
from d2v.parser import TopologyModel
from d2v.partitioner._dot_builder import (
    SubDiagram,
    _endpoint_zones,
    _group_by_zone,
    _safe_key,
    _subnets_for,
)

_YamlDict = dict[str, Any]


def _zones_text(
    model: TopologyModel,
    zones: list[str],
    devices: list[_YamlDict],
    intra: list[_YamlDict],
    boundary: list[_YamlDict],
) -> str:
    """指定ゾーン群だけを描画対象にした図のテキストを生成する。

    対象外ゾーンへ跨る接続は「外部ゾーン参照ノード（境界スタブ）」として含める。
    """
    included_ids = {d.get("device-id", "") for d in devices}
    included_zones = set(zones)

    # 境界接続から外部デバイスと、その境界インターフェースを収集
    external_ifaces: OrderedDict[str, set[str]] = OrderedDict()
    for conn in boundary:
        for ep in conn.get("endpoint", []):
            did = ep.get("device-id", "")
            if did and did not in included_ids:
                external_ifaces.setdefault(did, set()).add(ep.get("interface-id", ""))

    # 外部デバイスをゾーン別にグルーピングし、多いゾーンは 1 ノードに集約する
    ext_zone_devices: OrderedDict[str, list[str]] = OrderedDict()
    for did in external_ifaces:
        z = model.zone_of(did) or "unknown"
        ext_zone_devices.setdefault(z, []).append(did)
    aggregated_zones = {
        z for z, dids in ext_zone_devices.items()
        if len(dids) > settings.boundary_agg_threshold
    }
    aggregated_zones_ordered = [z for z in ext_zone_devices if z in aggregated_zones]
    individual_ext = [
        did for did in external_ifaces
        if (model.zone_of(did) or "unknown") not in aggregated_zones
    ]

    # 境界接続を「個別表示」と「ゾーン集約」に振り分ける
    indiv_boundary: list[_YamlDict] = []
    agg_boundary: OrderedDict[tuple[str, str], int] = OrderedDict()
    for conn in boundary:
        eps = conn.get("endpoint", [])
        int_ep = next((e for e in eps if e.get("device-id", "") in included_ids), None)
        ext_ep = next(
            (e for e in eps if e.get("device-id", "") not in included_ids), None
        )
        if int_ep is None or ext_ep is None:
            indiv_boundary.append(conn)
            continue
        ez = model.zone_of(ext_ep.get("device-id", "")) or "unknown"
        if ez in aggregated_zones:
            key = (int_ep.get("device-id", ""), ez)
            agg_boundary[key] = agg_boundary.get(key, 0) + 1
        else:
            indiv_boundary.append(conn)

    node_total = len(devices) + len(individual_ext) + len(aggregated_zones)
    conn_total = len(intra) + len(indiv_boundary) + len(agg_boundary)

    zone_label = "、".join(zones)
    lines: list[str] = []
    lines.append(f"# ゾーン限定図: {zone_label}\n")
    lines.append(
        f"この図はネットワーク全体のうち指定された {len(zones)} 個のゾーン"
        f"「{zone_label}」だけを描画対象にした部分構成図です。"
        "各ゾーンは背景色付きの subgraph cluster としてグルーピングしてください。"
        "末尾の外部ゾーン参照ノードは描画対象外のゾーンにある境界デバイスであり、"
        "破線・別スタイルで区別して描画してください。"
        "多数の外部デバイスを持つゾーンは「ゾーン全体を表す 1 ノード」に集約しています。"
        "集約ノードは対象外ゾーンの参照なので、必ず 1 ノードとして破線で描画し、"
        "内部の個別デバイスに展開しないでください。\n"
    )

    # ノード一覧（対象ゾーン内ノード + 外部境界スタブ）
    lines.append(f"## ノード一覧（{node_total} 台）\n")
    for dev in devices:
        lines.extend(parser.device_lines(dev))
    for did in individual_ext:
        ext_dev = model.device_map.get(did, {"device-id": did})
        ext_zone = model.zone_of(did) or "unknown"
        lines.extend(
            parser.device_lines(
                ext_dev, only_interfaces=external_ifaces[did], external_zone=ext_zone
            )
        )
    for z in aggregated_zones_ordered:
        n = len(ext_zone_devices[z])
        stub_id = f"ext-{_safe_key(z)}"
        lines.append(
            f"- {stub_id} ({z} ゾーン全体)  "
            f"[外部ゾーン={z} ・ {n} 台を集約 ・ 対象外]"
        )

    # 接続一覧（ゾーン内接続 + 境界接続）
    lines.append(f"\n## 物理接続一覧（{conn_total} 本）\n")
    for conn in intra:
        line = parser.connection_line(conn, model.device_map)
        if line is not None:
            lines.append(line)
    for conn in indiv_boundary:
        zpair = _endpoint_zones(model, conn)
        other = ""
        if zpair is not None:
            other = zpair[1] if zpair[0] in included_zones else zpair[0]
        note = f"境界: {other} ゾーンへ（対象外）" if other else "境界リンク"
        line = parser.connection_line(conn, model.device_map, note=note)
        if line is not None:
            lines.append(line)
    for (int_did, ez), cnt in agg_boundary.items():
        stub_id = f"ext-{_safe_key(ez)}"
        suffix = f"{cnt} 本のリンクを集約" if cnt > 1 else "境界リンク"
        lines.append(
            f"- {int_did}  <-->  {stub_id}  # 境界: {ez} ゾーンへ（{suffix}・対象外）"
        )

    # 関連サブネット
    rel_subnets = _subnets_for(devices, model.subnets)
    if rel_subnets:
        lines.append(f"\n## L3 サブネット一覧（{len(rel_subnets)} 件）\n")
        for sn in rel_subnets:
            prefix = sn.get("prefix", "")
            desc = sn.get("description", "")
            entry = f"- {prefix}"
            if desc:
                entry += f"  ({desc})"
            lines.append(entry)

    return "\n".join(lines)


def zone_plan(
    model: TopologyModel, zones: "str | list[str]"
) -> SubDiagram | None:
    """指定したゾーンだけを描画対象にした図を返す。

    複数ゾーンを指定した場合、それらをまとめて 1 枚に描画する。対象外ゾーンへ
    跨る接続は境界スタブ（外部ゾーン参照ノード）として含める。指定ゾーンが
    トポロジに存在しない場合は None を返す（呼び出し側でエラー表示）。
    """
    if isinstance(zones, str):
        zones = [zones]
    # 重複を除きつつ指定順を保持する
    seen: dict[str, None] = {}
    for z in zones:
        seen.setdefault(z, None)
    zones = list(seen)

    grouped = _group_by_zone(model)
    if not zones or any(z not in grouped for z in zones):
        return None

    # 対象ゾーンのデバイスを指定順・初出順に収集
    devices: list[_YamlDict] = []
    for z in zones:
        devices.extend(grouped[z])
    included_ids = {d.get("device-id", "") for d in devices}

    # 接続を「対象内接続」と「境界接続」に振り分け
    intra: list[_YamlDict] = []
    boundary: list[_YamlDict] = []
    for conn in model.connections:
        eps = conn.get("endpoint", [])
        if len(eps) != 2:
            continue
        d0 = eps[0].get("device-id", "")
        d1 = eps[1].get("device-id", "")
        in0 = d0 in included_ids
        in1 = d1 in included_ids
        if in0 and in1:
            intra.append(conn)
        elif in0 or in1:
            boundary.append(conn)

    key_zones = "-".join(_safe_key(z) for z in zones)
    zone_label = "、".join(zones)
    return SubDiagram(
        key=f"zone-only-{key_zones}",
        title=f"ゾーン限定図: {zone_label}（{len(devices)} 台）",
        text=_zones_text(model, zones, devices, intra, boundary),
    )
