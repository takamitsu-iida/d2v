"""iida-network-model YAML をパースし、LLM プロンプト用の構造化テキストを生成する。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

_YamlDict = dict[str, Any]


class TopologyParseError(ValueError):
    """iida-network-model YAML のスキーマ違反エラー。"""


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


def parse(path: Path) -> str:
    """iida-network-model YAML をパースし、LLM のユーザーメッセージ用テキストを返す。

    Args:
        path: トポロジ YAML ファイルのパス

    Returns:
        ノード・接続・サブネット情報を含む構造化テキスト
    """
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

    # デバイス辞書（接続先の IP 解決に使用）
    device_map: dict[str, _YamlDict] = {d["device-id"]: d for d in devices}

    lines: list[str] = []

    # ノード一覧
    lines.append(f"## ノード一覧（{len(devices)} 台）\n")
    for dev in devices:
        did = dev.get("device-id", "")
        dtype = dev.get("device-type", "unknown")
        zone = dev.get("zone", "")
        asn = dev.get("asn")
        loopback = dev.get("loopback", "")
        name = dev.get("device-name", did)

        attrs: list[str] = [f"type={dtype}"]
        if zone:
            attrs.append(f"zone={zone}")
        if asn is not None:
            attrs.append(f"ASN={asn}")
        if loopback:
            attrs.append(f"loopback={loopback}")

        lines.append(f"- {did} ({name})  [{', '.join(attrs)}]")
        for iface in dev.get("interface", []):
            iid = iface.get("interface-id", "")
            ip = iface.get("ip-address", "")
            desc = iface.get("description", "")
            iface_line = f"    {iid}"
            if ip:
                iface_line += f"  {ip}"
            if desc:
                iface_line += f"  # {desc}"
            lines.append(iface_line)

    # 接続一覧
    lines.append(f"\n## 物理接続一覧（{len(connections)} 本）\n")
    for conn in connections:
        endpoints: list[_YamlDict] = conn.get("endpoint", [])
        if len(endpoints) != 2:
            continue
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
        lines.append(f"- {seg}")

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
