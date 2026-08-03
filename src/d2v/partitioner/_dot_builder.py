"""共通ユーティリティ: SubDiagram, 分割判定, YAMLヘルパ関数。"""

from __future__ import annotations

import ipaddress
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from d2v.parser import TopologyModel

DEFAULT_SPLIT_THRESHOLD = 40

_YamlDict = dict[str, Any]


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


def available_zones(model: TopologyModel) -> list[str]:
    """トポロジに存在するゾーン名の一覧を初出順で返す。"""
    return list(_group_by_zone(model).keys())


def _build_adjacency(model: TopologyModel) -> "dict[str, set[str]]":
    """物理接続から隣接リスト（無向グラフ）を構築する。"""
    adj: dict[str, set[str]] = {}
    for conn in model.connections:
        eps = conn.get("endpoint", [])
        if len(eps) != 2:
            continue
        d0 = eps[0].get("device-id", "")
        d1 = eps[1].get("device-id", "")
        if not d0 or not d1:
            continue
        adj.setdefault(d0, set()).add(d1)
        adj.setdefault(d1, set()).add(d0)
    return adj


def hop_distances(
    model: TopologyModel, focus_ids: "str | list[str]", max_hops: int
) -> "OrderedDict[str, int]":
    """注目ノード群から各デバイスまでの最短ホップ数を多点 BFS で求める。

    複数の注目ノードを指定した場合、各デバイスのホップ数は「いずれかの注目
    ノードからの最短距離」になる（和集合の近傍を抽出する）。

    Args:
        focus_ids: 注目ノードの device-id（単一文字列またはリスト）。
        max_hops: 抽出する最大ホップ数。

    Returns:
        ホップ数の昇順・初出順を保つ ``OrderedDict[device-id, hop]``。
    """
    if isinstance(focus_ids, str):
        focus_ids = [focus_ids]
    adj = _build_adjacency(model)
    dist: "OrderedDict[str, int]" = OrderedDict()
    frontier: list[str] = []
    for fid in focus_ids:
        if fid not in dist:
            dist[fid] = 0
            frontier.append(fid)
    for hop in range(1, max_hops + 1):
        nxt: list[str] = []
        for node in frontier:
            for nb in sorted(adj.get(node, set())):
                if nb not in dist:
                    dist[nb] = hop
                    nxt.append(nb)
        frontier = nxt
    return dist
