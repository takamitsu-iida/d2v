"""iida-network-model YAML をパースし、LLM プロンプト用の構造化テキストを生成する。"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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

    def zone_of(self, device_id: str) -> str:
        """デバイス ID からゾーン名を返す（未設定なら空文字）。"""
        return self.device_map.get(device_id, {}).get("zone", "") or ""


def _load_yaml(path: Path) -> _YamlDict:
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"\n[エラー] ファイルが見つかりません: {path}\n", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"\n[エラー] YAML 解析に失敗しました: {e}\n", file=sys.stderr)
        sys.exit(1)


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
        print(f"\n[スキーマエラー] {e}\n", file=sys.stderr)
        sys.exit(1)

    connections: list[_YamlDict] = phys.get("physical-connection", [])
    layer3 = root.get("layer3-layer", {})
    subnets: list[_YamlDict] = layer3.get("ip-subnet", [])

    device_map: dict[str, _YamlDict] = {d["device-id"]: d for d in devices}

    return TopologyModel(
        devices=devices,
        connections=connections,
        subnets=subnets,
        device_map=device_map,
    )


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
                    note: str = "") -> str | None:
    """1 物理接続を接続一覧向けのテキスト行に整形する。

    Args:
        conn: physical-connection エントリ
        device_map: デバイス ID → デバイス辞書
        note: 行末に付与する注記（境界リンク表示などに使用）

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
    if note:
        line += f"  # {note}"
    return line


def build_text(
    devices: list[_YamlDict],
    connections: list[_YamlDict],
    subnets: list[_YamlDict],
    device_map: dict[str, _YamlDict],
) -> str:
    """デバイス・接続・サブネットを LLM 用の構造化テキストに整形する。"""
    lines: list[str] = []

    # ノード一覧
    lines.append(f"## ノード一覧（{len(devices)} 台）\n")
    for dev in devices:
        lines.extend(device_lines(dev))

    # 接続一覧
    lines.append(f"\n## 物理接続一覧（{len(connections)} 本）\n")
    for conn in connections:
        line = connection_line(conn, device_map)
        if line is not None:
            lines.append(line)

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
    return build_text(model.devices, model.connections, model.subnets, model.device_map)
