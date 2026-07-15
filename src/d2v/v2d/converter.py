"""v2d 変換器: 中間表現 ``ExtractedDiagram`` を iida-network-model YAML に変換する。

出力は d2v の入力スキーマと同一のため、v2d → d2v で再描画できる（往復性）。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import yaml

from d2v.v2d.schema import ExtractedDiagram, ExtractedNode

_YamlDict = dict[str, Any]


def _safe_id(text: str) -> str:
    """ホスト名等を device-id に使える識別子へ正規化する。"""
    cleaned = "".join(c if c.isalnum() or c in "-_." else "-" for c in text.strip())
    cleaned = cleaned.strip("-")
    return cleaned or "node"


def _zone_of(diagram: ExtractedDiagram) -> dict[str, str]:
    """ノード id → ゾーン名のマップを構築する（クラスタ所属を優先）。"""
    zone_map: dict[str, str] = {}
    for cluster in diagram.clusters:
        zone_name = cluster.label or cluster.id
        for member_id in cluster.members:
            zone_map[member_id] = zone_name
    # クラスタに含まれないが node.zone を持つ場合は補完する
    for node in diagram.nodes:
        if node.id not in zone_map and node.zone:
            zone_map[node.id] = node.zone
    return zone_map


def _device_id_map(diagram: ExtractedDiagram) -> dict[str, str]:
    """ノード id → device-id（ホスト名優先・衝突回避）のマップを構築する。"""
    id_map: dict[str, str] = {}
    used: set[str] = set()
    for node in diagram.nodes:
        base = _safe_id(node.hostname or node.id)
        did = base
        i = 2
        while did in used:
            did = f"{base}-{i}"
            i += 1
        used.add(did)
        id_map[node.id] = did
    return id_map


def build_model(diagram: ExtractedDiagram) -> _YamlDict:
    """中間表現から iida-network-model 相当の辞書を構築する。"""
    zone_map = _zone_of(diagram)
    did_map = _device_id_map(diagram)

    # ノード id → インターフェース（interface-id → ip-address）を集約
    interfaces: dict[str, "OrderedDict[str, str | None]"] = {
        n.id: OrderedDict() for n in diagram.nodes
    }
    iface_counter: dict[str, int] = {n.id: 0 for n in diagram.nodes}

    def _ensure_interface(node_id: str, port: str | None) -> str:
        """ノードに対しインターフェースを確保し interface-id を返す。"""
        if node_id not in interfaces:
            # エッジが未知ノードを指す場合に備え動的に作成
            interfaces[node_id] = OrderedDict()
            iface_counter[node_id] = 0
        if port:
            interfaces[node_id].setdefault(port, None)
            return port
        # ポート名が読めない場合は連番で合成
        iface_counter[node_id] += 1
        synth = f"if{iface_counter[node_id]}"
        interfaces[node_id].setdefault(synth, None)
        return synth

    # エッジからインターフェースと物理接続を構築
    connections: list[_YamlDict] = []
    subnets: "OrderedDict[str, None]" = OrderedDict()
    for edge in diagram.edges:
        s_iface = _ensure_interface(edge.source, edge.source_port)
        t_iface = _ensure_interface(edge.target, edge.target_port)
        s_did = did_map.get(edge.source, _safe_id(edge.source))
        t_did = did_map.get(edge.target, _safe_id(edge.target))
        connections.append({
            "connection-id": f"{s_did}_{s_iface}__{t_did}_{t_iface}",
            "endpoint": [
                {"device-id": s_did, "interface-id": s_iface},
                {"device-id": t_did, "interface-id": t_iface},
            ],
        })
        if edge.segment:
            subnets.setdefault(edge.segment, None)

    # デバイス一覧を構築
    devices: list[_YamlDict] = []
    for node in diagram.nodes:
        did = did_map[node.id]
        entry: _YamlDict = {
            "device-id": did,
            "device-name": node.hostname or did,
            "device-type": node.device_type,
        }
        zone = zone_map.get(node.id)
        if zone:
            entry["zone"] = zone
        if node.loopback:
            entry["loopback"] = node.loopback
        iface_list: list[_YamlDict] = []
        for iid, ip in interfaces.get(node.id, {}).items():
            iface_entry: _YamlDict = {"interface-id": iid}
            if ip:
                iface_entry["ip-address"] = ip
            iface_list.append(iface_entry)
        if iface_list:
            entry["interface"] = iface_list
        devices.append(entry)

    physical_layer: _YamlDict = {"device": devices}
    if connections:
        physical_layer["physical-connection"] = connections

    model: _YamlDict = {"network-model": {"physical-layer": physical_layer}}
    if subnets:
        model["network-model"]["layer3-layer"] = {
            "ip-subnet": [{"prefix": p} for p in subnets]
        }
    return model


def to_yaml(diagram: ExtractedDiagram) -> str:
    """中間表現を iida-network-model YAML 文字列に変換する。"""
    model = build_model(diagram)
    return yaml.safe_dump(model, allow_unicode=True, sort_keys=False)
