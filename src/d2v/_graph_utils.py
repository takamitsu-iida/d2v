"""無向グラフの構築とアルゴリズム（Tarjan DFS）を提供する内部ユーティリティ。

``validator`` と ``diff`` から共有される純粋なグラフ関数のみを置く。
"""

from __future__ import annotations

from typing import Any

from d2v.parser import TopologyModel

_YamlDict = dict[str, Any]

# 無向・ポート込みの接続を一意に識別するキー型（diff と validator で共用）
EdgeKey = frozenset[tuple[str | None, str | None]]


def make_edge_key(conn: _YamlDict) -> "EdgeKey | None":
    """無向・ポート込みの接続キーを返す（端点が 2 個でなければ None）。"""
    eps = conn.get("endpoint", []) or []
    if len(eps) != 2:
        return None
    a = (eps[0].get("device-id"), eps[0].get("interface-id"))
    b = (eps[1].get("device-id"), eps[1].get("interface-id"))
    return frozenset((a, b))


def build_graph(model: TopologyModel) -> dict[str, set[str]]:
    """physical-connection から無向グラフ（隣接リスト）を構築する。

    ノードは全 device-id（孤立ノードも含む）。自己ループ・未定義デバイス参照・
    端点数≠2 の接続は無視する（それぞれ別ルールが検出する）。
    """
    adj: dict[str, set[str]] = {
        d["device-id"]: set() for d in model.devices if d.get("device-id")
    }
    for conn in model.connections:
        eps = conn.get("endpoint", []) or []
        if len(eps) != 2:
            continue
        a, b = eps[0].get("device-id"), eps[1].get("device-id")
        if not a or not b or a == b or a not in adj or b not in adj:
            continue
        adj[a].add(b)
        adj[b].add(a)
    return adj


def pair_multiplicity(model: TopologyModel) -> dict[frozenset[str], int]:
    """デバイス対ごとの物理接続本数を返す（並行リンク＝LAG の判定に使う）。"""
    counts: dict[frozenset[str], int] = {}
    for conn in model.connections:
        eps = conn.get("endpoint", []) or []
        if len(eps) != 2:
            continue
        a, b = eps[0].get("device-id"), eps[1].get("device-id")
        if not a or not b or a == b:
            continue
        key = frozenset((a, b))
        counts[key] = counts.get(key, 0) + 1
    return counts


def articulation_and_bridges(
    adj: dict[str, set[str]],
) -> tuple[set[str], list[frozenset[str]]]:
    """無向グラフの関節点（articulation point）と橋（bridge）を返す（Tarjan/DFS）。"""
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    timer = [0]
    aps: set[str] = set()
    bridges: list[frozenset[str]] = []

    def dfs(u: str, parent: str | None) -> None:
        disc[u] = low[u] = timer[0]
        timer[0] += 1
        children = 0
        for v in adj[u]:
            if v == parent:
                continue
            if v not in disc:
                children += 1
                dfs(v, u)
                low[u] = min(low[u], low[v])
                if parent is not None and low[v] >= disc[u]:
                    aps.add(u)
                if low[v] > disc[u]:
                    bridges.append(frozenset((u, v)))
            else:
                low[u] = min(low[u], disc[v])
        if parent is None and children > 1:
            aps.add(u)

    for node in adj:
        if node not in disc:
            dfs(node, None)
    return aps, bridges
