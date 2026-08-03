"""iida-network-model YAML をパースし、LLM プロンプト用の構造化テキストを生成する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from d2v.errors import InputError

_YamlDict = dict[str, Any]


class TopologyParseError(ValueError):
    """iida-network-model YAML のスキーマ違反エラー。"""


@dataclass
class TopologyModel:
    """パース済みトポロジの構造化モデル。

    テキスト整形（``build_text``）や分割（``partitioner``）から再利用する。
    """

    devices: list[_YamlDict] = field(default_factory=list)
    connections: list[_YamlDict] = field(default_factory=list)
    subnets: list[_YamlDict] = field(default_factory=list)
    device_map: dict[str, _YamlDict] = field(default_factory=dict)
    lags: list[_YamlDict] = field(default_factory=list)

    def zone_of(self, device_id: str) -> str:
        """デバイス ID からゾーン名を返す（未設定なら空文字）。"""
        return self.device_map.get(device_id, {}).get("zone", "") or ""


def _load_yaml(path: Path) -> _YamlDict:
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError as e:
        raise InputError(f"ファイルが見つかりません: {path}") from e
    except yaml.YAMLError as e:
        raise InputError(f"YAML 解析に失敗しました: {e}") from e


def _require(d: _YamlDict, key: str, context: str) -> Any:
    """必須フィールドを取得する。存在しない場合は TopologyParseError を送出する。"""
    if key not in d:
        raise TopologyParseError(f"必須フィールド '{key}' が {context} に存在しません。")
    return d[key]


def _get_interface_ip(device: _YamlDict, interface_id: str) -> str:
    """デバイスの指定インターフェースの IP アドレスを返す（未設定なら空文字）。"""
    for iface in device.get("interface", []):
        if iface.get("interface-id") == interface_id:
            return iface.get("ip-address", "")
    return ""


def load_model(path: Path) -> TopologyModel:
    """iida-network-model YAML を読み込み、構造化モデルを返す。"""
    raw = _load_yaml(path)

    try:
        root = _require(raw, "network-model", "トップレベル")
        phys = _require(root, "physical-layer", "network-model")
        devices: list[_YamlDict] = _require(phys, "device", "physical-layer")
    except TopologyParseError as e:
        raise InputError(f"スキーマエラー: {e}") from e

    connections: list[_YamlDict] = phys.get("physical-connection", [])
    layer3 = root.get("layer3-layer", {})
    subnets: list[_YamlDict] = layer3.get("ip-subnet", [])
    layer2 = root.get("layer2-layer", {})
    lags: list[_YamlDict] = layer2.get("link-aggregation", [])

    device_map: dict[str, _YamlDict] = {d["device-id"]: d for d in devices}

    return TopologyModel(
        devices=devices,
        connections=connections,
        subnets=subnets,
        device_map=device_map,
        lags=lags,
    )


def build_lag_lookup(lags: list[_YamlDict]) -> dict[tuple[str, str], str]:
    """(device-id, interface-id) → 所属 LAG 名（lag-id）の辞書を構築する。"""
    lookup: dict[tuple[str, str], str] = {}
    for lag in lags:
        did = lag.get("device-id", "")
        lag_id = lag.get("lag-id", "")
        for member in lag.get("member-interface", []):
            iid = member.get("interface-id", "")
            if did and iid and lag_id:
                lookup[(did, iid)] = lag_id
    return lookup


def device_lines(dev: _YamlDict, only_interfaces: set[str] | None = None,
                 external_zone: str | None = None) -> list[str]:
    """1 デバイスをノード一覧向けのテキスト行に整形する。

    Args:
        dev: デバイス辞書
        only_interfaces: 出力するインターフェース ID を限定する集合。
            None の場合は全インターフェースを出力する（通常ノード）。
        external_zone: 指定すると「外部ゾーン参照ノード」として注記する
            （分割詳細図の境界スタブ用）。
    """
    did = dev.get("device-id", "")
    dtype = dev.get("device-type", "unknown")
    zone = dev.get("zone", "")
    asn = dev.get("asn")
    loopback = dev.get("loopback", "")
    name = dev.get("device-name", did)

    if external_zone is not None:
        header = f"- {did} ({name})  [外部ゾーン={external_zone} ・ 別図参照]"
    else:
        attrs: list[str] = [f"type={dtype}"]
        if zone:
            attrs.append(f"zone={zone}")
        if asn is not None:
            attrs.append(f"ASN={asn}")
        if loopback:
            attrs.append(f"loopback={loopback}")
        header = f"- {did} ({name})  [{', '.join(attrs)}]"

    lines = [header]
    for iface in dev.get("interface", []):
        iid = iface.get("interface-id", "")
        if only_interfaces is not None and iid not in only_interfaces:
            continue
        ip = iface.get("ip-address", "")
        desc = iface.get("description", "")
        iface_line = f"    {iid}"
        if ip:
            iface_line += f"  {ip}"
        if external_zone is not None:
            iface_line += "  # 境界インターフェース（外部ゾーン参照）"
        elif desc:
            iface_line += f"  # {desc}"
        lines.append(iface_line)
    return lines


def connection_line(conn: _YamlDict, device_map: dict[str, _YamlDict],
                    note: str = "", lag_lookup: dict[tuple[str, str], str] | None = None) -> str | None:
    """1 物理接続を接続一覧向けのテキスト行に整形する。

    Args:
        conn: physical-connection エントリ
        device_map: デバイス ID → デバイス辞書
        note: 行末に付与する注記（境界リンク表示などに使用）
        lag_lookup: (device-id, interface-id) → LAG 名の辞書。両端のインターフェースが
            同一のリンクアグリゲーションに属する場合、LAG 注記を付与する。

    Returns:
        整形済みの行。endpoint が 2 個でない場合は None。
    """
    endpoints: list[_YamlDict] = conn.get("endpoint", [])
    if len(endpoints) != 2:
        return None
    ep0, ep1 = endpoints[0], endpoints[1]
    d0 = ep0.get("device-id", "")
    i0 = ep0.get("interface-id", "")
    d1 = ep1.get("device-id", "")
    i1 = ep1.get("interface-id", "")
    ip0 = _get_interface_ip(device_map.get(d0, {}), i0)
    ip1 = _get_interface_ip(device_map.get(d1, {}), i1)

    seg = f"{d0}[{i0}]"
    if ip0:
        seg += f"({ip0})"
    seg += "  <-->  "
    seg += f"{d1}[{i1}]"
    if ip1:
        seg += f"({ip1})"
    line = f"- {seg}"

    notes: list[str] = []
    if lag_lookup:
        lag0 = lag_lookup.get((d0, i0))
        lag1 = lag_lookup.get((d1, i1))
        if lag0 and lag1:
            notes.append(f"LAG={d0}:{lag0}<->{d1}:{lag1}（リンクアグリゲーション束の一部）")
    if note:
        notes.append(note)
    if notes:
        line += "  # " + " / ".join(notes)
    return line


def _endpoint_lag(ep: _YamlDict, lag_lookup: dict[tuple[str, str], str]) -> str | None:
    """1 endpoint の所属 LAG 名を解決する。

    明示的な ``lag-ref`` があればそれを優先し、なければ
    (device-id, interface-id) → member-interface の照合で推定する。
    """
    ref = ep.get("lag-ref")
    if ref:
        return ref
    return lag_lookup.get((ep.get("device-id", ""), ep.get("interface-id", "")))


def _lag_bundle_line(members: list[_YamlDict], device_map: dict[str, _YamlDict],
                     lag_lookup: dict[tuple[str, str], str]) -> str | None:
    """同一 LAG 束に属する複数の物理接続を、1 本の論理リンク行に集約する。

    図では 1 本のエッジとして描画させ、メンバーポートはインターフェースラベルへ
    列挙する方針のため、束のポートをまとめて表示する。
    """
    first = members[0].get("endpoint", [])
    if len(first) != 2:
        return None
    d_a = first[0].get("device-id", "")
    d_b = first[1].get("device-id", "")
    ports_a: list[str] = []
    ports_b: list[str] = []
    lag_a = ""
    lag_b = ""
    for conn in members:
        eps = conn.get("endpoint", [])
        if len(eps) != 2:
            continue
        for ep in eps:
            d = ep.get("device-id", "")
            i = ep.get("interface-id", "")
            lg = _endpoint_lag(ep, lag_lookup) or ""
            if d == d_a:
                ports_a.append(i)
                lag_a = lag_a or lg
            elif d == d_b:
                ports_b.append(i)
                lag_b = lag_b or lg
    seg_a = f"{d_a}[{lag_a}: {', '.join(ports_a)}]"
    seg_b = f"{d_b}[{lag_b}: {', '.join(ports_b)}]"
    n = len(members)
    return (f"- {seg_a}  <-->  {seg_b}"
            f"  # リンクアグリゲーション（{n} 本のメンバーを束ねた 1 本の論理リンク。"
            f"図では 1 本のエッジで描画し、メンバーポートはインターフェースラベルに列挙する）")


def connection_section(connections: list[_YamlDict], device_map: dict[str, _YamlDict],
                       lag_lookup: dict[tuple[str, str], str]) -> tuple[list[str], int]:
    """物理接続を整形する。LAG メンバー接続は 1 本の論理リンクに集約する。

    LAG 別の判定は、endpoint の明示的な ``lag-ref`` を優先し、なければ
    interface-id と member-interface の照合にフォールバックする。

    Returns:
        (整形済みの行リスト, 表示上の接続本数)。本数は集約後の行数と一致する。
    """
    bundles: dict[frozenset[tuple[str, str]], list[_YamlDict]] = {}
    order: list[tuple[str, Any]] = []
    for conn in connections:
        eps = conn.get("endpoint", [])
        if len(eps) == 2:
            d0 = eps[0].get("device-id", "")
            d1 = eps[1].get("device-id", "")
            lag0 = _endpoint_lag(eps[0], lag_lookup)
            lag1 = _endpoint_lag(eps[1], lag_lookup)
            if lag0 and lag1:
                key = frozenset({(d0, lag0), (d1, lag1)})
                if key not in bundles:
                    bundles[key] = []
                    order.append(("lag", key))
                bundles[key].append(conn)
                continue
        order.append(("normal", conn))

    out: list[str] = []
    for kind, ref in order:
        if kind == "normal":
            line = connection_line(ref, device_map)
        else:
            line = _lag_bundle_line(bundles[ref], device_map, lag_lookup)
        if line is not None:
            out.append(line)
    return out, len(out)


def build_text(
    devices: list[_YamlDict],
    connections: list[_YamlDict],
    subnets: list[_YamlDict],
    device_map: dict[str, _YamlDict],
    lags: list[_YamlDict] | None = None,
) -> str:
    """デバイス・接続・サブネットを LLM 用の構造化テキストに整形する。"""
    lags = lags or []
    lag_lookup = build_lag_lookup(lags)
    lines: list[str] = []

    # ノード一覧
    lines.append(f"## ノード一覧（{len(devices)} 台）\n")
    for dev in devices:
        lines.extend(device_lines(dev))

    # 接続一覧（LAG メンバーは 1 本の論理リンクに集約して表示する）
    conn_lines, conn_count = connection_section(connections, device_map, lag_lookup)
    lines.append(f"\n## 物理接続一覧（{conn_count} 本）\n")
    lines.extend(conn_lines)

    # リンクアグリゲーション（LAG / Port-channel）一覧
    if lags:
        lines.append(f"\n## リンクアグリゲーション一覧（{len(lags)} 件）\n")
        for lag in lags:
            did = lag.get("device-id", "")
            lag_id = lag.get("lag-id", "")
            mode = lag.get("mode", "")
            members = [m.get("interface-id", "") for m in lag.get("member-interface", [])]
            entry = f"- {did} / {lag_id}"
            if mode:
                entry += f"  [{mode}]"
            if members:
                entry += f"  members: {', '.join(members)}"
            mlag = lag.get("mlag", {})
            if mlag.get("enabled"):
                entry += f"  (MLAG peer={mlag.get('peer-device-id', '')})"
            lines.append(entry)

    # サブネット一覧
    if subnets:
        lines.append(f"\n## L3 サブネット一覧（{len(subnets)} 件）\n")
        for sn in subnets:
            prefix = sn.get("prefix", "")
            desc = sn.get("description", "")
            entry = f"- {prefix}"
            if desc:
                entry += f"  ({desc})"
            lines.append(entry)

    return "\n".join(lines)


def parse(path: Path) -> str:
    """iida-network-model YAML をパースし、LLM のユーザーメッセージ用テキストを返す。

    Args:
        path: トポロジ YAML ファイルのパス

    Returns:
        ノード・接続・サブネット情報を含む構造化テキスト
    """
    model = load_model(path)
    return build_text(model.devices, model.connections, model.subnets, model.device_map, model.lags)
