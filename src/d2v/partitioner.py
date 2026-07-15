"""大規模トポロジをゾーン単位で「俯瞰図 + ゾーン詳細図」に分割する。

しきい値（ノード数）を超え、かつ ``zone`` が付与されている場合にのみ分割する。
各詳細図はゾーン内ノードに加え、他ゾーンへ跨る接続を「境界スタブ（外部参照ノード）」
として含めることで自己完結させ、俯瞰図が全体の地図として機能する。
"""

from __future__ import annotations

import ipaddress
from collections import Counter, OrderedDict
from dataclasses import dataclass

from d2v import parser
from d2v.config import settings
from d2v.parser import TopologyModel

# 分割を発動するデフォルトのノード数しきい値
DEFAULT_SPLIT_THRESHOLD = 40

_YamlDict = dict


@dataclass
class SubDiagram:
    """分割後の 1 枚の図を表す。"""

    key: str    # 出力ディレクトリ・ファイル名に使う安全な識別子
    title: str  # 人間可読なタイトル
    text: str   # generator/pipeline に渡す構造化テキスト


def node_count(model: TopologyModel) -> int:
    """ノード（デバイス）総数を返す。"""
    return len(model.devices)


def has_zones(model: TopologyModel) -> bool:
    """いずれかのデバイスに zone が設定されているか。"""
    return any(d.get("zone") for d in model.devices)

def should_split(model: TopologyModel, threshold: int = DEFAULT_SPLIT_THRESHOLD) -> bool:
    """分割すべきか判定する（しきい値超過かつゾーン情報あり）。"""
    return node_count(model) > threshold and has_zones(model)


def _safe_key(name: str) -> str:
    """ゾーン名をファイル名に使える識別子へ変換する。"""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name)


def _group_by_zone(model: TopologyModel) -> "OrderedDict[str, list[_YamlDict]]":
    """デバイスをゾーン別にグルーピングする（初出順を維持）。"""
    zones: OrderedDict[str, list[_YamlDict]] = OrderedDict()
    for dev in model.devices:
        z = dev.get("zone") or "(no-zone)"
        zones.setdefault(z, []).append(dev)
    return zones


def _endpoint_zones(model: TopologyModel, conn: _YamlDict) -> tuple[str, str] | None:
    """接続両端のゾーン名を返す。endpoint が 2 個でなければ None。"""
    eps = conn.get("endpoint", [])
    if len(eps) != 2:
        return None
    z0 = model.zone_of(eps[0].get("device-id", "")) or "(no-zone)"
    z1 = model.zone_of(eps[1].get("device-id", "")) or "(no-zone)"
    return z0, z1


def _subnets_for(devices: list[_YamlDict], subnets: list[_YamlDict]) -> list[_YamlDict]:
    """指定デバイス群のインターフェース IP が属するサブネットのみ抽出する。"""
    nets = set()
    for dev in devices:
        for iface in dev.get("interface", []):
            ip = iface.get("ip-address")
            if not ip:
                continue
            try:
                nets.add(ipaddress.ip_interface(ip).network)
            except ValueError:
                continue
    result: list[_YamlDict] = []
    for sn in subnets:
        prefix = sn.get("prefix", "")
        try:
            net = ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            continue
        if net in nets:
            result.append(sn)
    return result


def _overview_text(
    model: TopologyModel,
    zones: "OrderedDict[str, list[_YamlDict]]",
    inter_pairs: "OrderedDict[tuple[str, str], int]",
) -> str:
    """ゾーン単位の俯瞰図テキストを生成する。"""
    lines: list[str] = []
    lines.append("# ネットワーク全体俯瞰図（ゾーン単位）\n")
    lines.append(
        f"全 {node_count(model)} 台のノードを {len(zones)} 個のゾーンに集約した俯瞰図です。"
        "各ゾーンを**必ず 1 つのノード**として描画してください。"
        "ゾーン内の個別デバイス（ルータ・スイッチ等）は 1 台ずつ描かず、"
        "デバイス名やインターフェース名を創作しないでください。"
        "ゾーン内部の詳細は個別の詳細図に分割されています。\n"
    )
    lines.append(f"## ゾーン一覧（{len(zones)} 個）\n")
    for z, devs in zones.items():
        types = Counter(d.get("device-type", "unknown") for d in devs)
        tdesc = ", ".join(f"{t}×{c}" for t, c in types.items())
        lines.append(f"- {z}: {len(devs)} 台 ({tdesc})")

    lines.append(f"\n## ゾーン間接続一覧（{len(inter_pairs)} 本）\n")
    for (a, b), cnt in inter_pairs.items():
        suffix = f"  # {cnt} 本のリンクを集約" if cnt > 1 else ""
        lines.append(f"- {a}  <-->  {b}{suffix}")

    return "\n".join(lines)


def _detail_text(
    model: TopologyModel,
    zone: str,
    devices: list[_YamlDict],
    intra: list[_YamlDict],
    boundary: list[_YamlDict],
) -> str:
    """1 ゾーンの詳細図テキストを生成する（境界スタブを含む）。"""
    zone_ids = {d.get("device-id", "") for d in devices}

    # 境界接続から外部デバイスと、そのデバイスが使う境界インターフェースを収集
    external_ifaces: OrderedDict[str, set[str]] = OrderedDict()
    for conn in boundary:
        for ep in conn.get("endpoint", []):
            did = ep.get("device-id", "")
            if did and did not in zone_ids:
                external_ifaces.setdefault(did, set()).add(ep.get("interface-id", ""))

    # 外部デバイスをゾーン別にグルーピングし、数が多いゾーンは 1 ノードに集約する。
    # （多数の境界スタブが横一列に並んで詳細図が横長になるのを防ぐ）
    ext_zone_devices: OrderedDict[str, list[str]] = OrderedDict()
    for did in external_ifaces:
        z = model.zone_of(did) or "unknown"
        ext_zone_devices.setdefault(z, []).append(did)
    aggregated_zones = {
        z for z, dids in ext_zone_devices.items()
        if len(dids) > settings.boundary_agg_threshold
    }
    # 出力順を安定させるため、初出順のリストも保持する
    aggregated_zones_ordered = [
        z for z in ext_zone_devices if z in aggregated_zones
    ]
    individual_ext = [
        did for did in external_ifaces
        if (model.zone_of(did) or "unknown") not in aggregated_zones
    ]

    # 境界接続を「個別表示」と「ゾーン集約」に振り分ける
    indiv_boundary: list[_YamlDict] = []
    # (ゾーン内デバイスID, 外部ゾーン名) -> 集約したリンク本数
    agg_boundary: OrderedDict[tuple[str, str], int] = OrderedDict()
    for conn in boundary:
        eps = conn.get("endpoint", [])
        int_ep = next((e for e in eps if e.get("device-id", "") in zone_ids), None)
        ext_ep = next((e for e in eps if e.get("device-id", "") not in zone_ids), None)
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

    lines: list[str] = []
    lines.append(f"# ゾーン詳細図: {zone}\n")
    lines.append(
        f"この図はネットワーク全体のうち「{zone}」ゾーンの詳細です。"
        "末尾の外部ゾーン参照ノードは他ゾーンの図で詳細化されている境界デバイスであり、"
        "破線・別スタイルで区別して描画してください。"
        "多数の外部デバイスを持つゾーンは「ゾーン全体を表す 1 ノード」に集約しています。"
        "集約ノードは他ゾーンの外部参照なので、必ず 1 ノードとして破線で描画し、"
        "内部の個別デバイスに展開しないでください。\n"
    )

    # ノード一覧（ゾーン内ノード + 外部境界スタブ）
    lines.append(f"## ノード一覧（{node_total} 台）\n")
    for dev in devices:
        lines.extend(parser.device_lines(dev))
    # 個別表示する外部スタブ
    for did in individual_ext:
        ext_dev = model.device_map.get(did, {"device-id": did})
        ext_zone = model.zone_of(did) or "unknown"
        lines.extend(
            parser.device_lines(
                ext_dev, only_interfaces=external_ifaces[did], external_zone=ext_zone
            )
        )
    # ゾーン集約する外部スタブ（1 ゾーン = 1 ノード）
    for z in aggregated_zones_ordered:
        n = len(ext_zone_devices[z])
        stub_id = f"ext-{_safe_key(z)}"
        lines.append(
            f"- {stub_id} ({z} ゾーン全体)  "
            f"[外部ゾーン={z} ・ {n} 台を集約 ・ 別図参照]"
        )

    # 接続一覧（ゾーン内接続 + 境界接続）
    lines.append(f"\n## 物理接続一覧（{conn_total} 本）\n")
    for conn in intra:
        line = parser.connection_line(conn, model.device_map)
        if line is not None:
            lines.append(line)
    # 個別の境界接続
    for conn in indiv_boundary:
        zpair = _endpoint_zones(model, conn)
        other = ""
        if zpair is not None:
            other = zpair[1] if zpair[0] == zone else zpair[0]
        note = f"境界: {other} ゾーンへ（{other} 図参照）" if other else "境界リンク"
        line = parser.connection_line(conn, model.device_map, note=note)
        if line is not None:
            lines.append(line)
    # 集約した境界接続（ゾーン内デバイス <--> 外部ゾーン集約ノード）
    for (int_did, ez), cnt in agg_boundary.items():
        stub_id = f"ext-{_safe_key(ez)}"
        suffix = f"{cnt} 本のリンクを集約" if cnt > 1 else "境界リンク"
        lines.append(
            f"- {int_did}  <-->  {stub_id}  # 境界: {ez} ゾーンへ（{suffix}・{ez} 図参照）"
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


def plan(
    model: TopologyModel, threshold: int = DEFAULT_SPLIT_THRESHOLD
) -> list[SubDiagram] | None:
    """分割計画を返す。分割不要なら None。

    先頭要素が俯瞰図（key="overview"）、以降が各ゾーンの詳細図。
    """
    if not should_split(model, threshold):
        return None

    zones = _group_by_zone(model)

    # 接続をゾーン内 / ゾーン間に振り分け
    intra_by_zone: dict[str, list[_YamlDict]] = {z: [] for z in zones}
    boundary_by_zone: dict[str, list[_YamlDict]] = {z: [] for z in zones}
    inter_pairs: OrderedDict[tuple[str, str], int] = OrderedDict()

    for conn in model.connections:
        zpair = _endpoint_zones(model, conn)
        if zpair is None:
            continue
        z0, z1 = zpair
        if z0 == z1:
            intra_by_zone.setdefault(z0, []).append(conn)
        else:
            boundary_by_zone.setdefault(z0, []).append(conn)
            boundary_by_zone.setdefault(z1, []).append(conn)
            key = tuple(sorted((z0, z1)))
            inter_pairs[key] = inter_pairs.get(key, 0) + 1

    diagrams: list[SubDiagram] = [
        SubDiagram(
            key="overview",
            title="全体俯瞰図（ゾーン単位）",
            text=_overview_text(model, zones, inter_pairs),
        )
    ]
    for zone, devs in zones.items():
        diagrams.append(
            SubDiagram(
                key=f"zone-{_safe_key(zone)}",
                title=f"ゾーン詳細図: {zone}",
                text=_detail_text(
                    model,
                    zone,
                    devs,
                    intra_by_zone.get(zone, []),
                    boundary_by_zone.get(zone, []),
                ),
            )
        )
    return diagrams
