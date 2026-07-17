"""2段階レイアウト（ゾーン配置の決定論化）のコア。

ゾーン（``subgraph cluster``）の相対位置は、本図では実デバイスノード＋実リンクを
Graphviz ``dot`` が階層配置した副産物として決まり、実行ごとに揺れやすい。本モジュール
はゾーンの位置だけを**決定論的に固定**するための 2 段階レイアウトを提供する。

- **Stage 1（ゾーングラフ配置 / :func:`compute_zone_placement`）**
  ゾーンをノード・ゾーン間接続をエッジとする小さなグラフを ``dot`` で配置し、各ゾーンの
  「段（tier）・段内左右順」を確定する。小規模グラフなので配置は安定・決定論的。
- **Stage 2（制約 DOT 生成 / :func:`zone_constraint_dot`）**
  本図に各ゾーンの**不可視アンカーノード**を仕込み、``{rank=same}``＋不可視エッジで段・
  段内順を固定する DOT 行を生成する。``dot`` のクラスタ枠・アイコン・ラベルはそのまま
  活かせる。

このモジュールは ``graphviz`` のみに依存し、副作用を持たない純粋関数の集まりである。
アンカーノード宣言（:func:`anchor_decl`）は各 ``subgraph cluster`` 内に、制約行
（:func:`zone_constraint_dot`）はグラフ最上位に配置する想定。
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import graphviz

if TYPE_CHECKING:
    from d2v.parser import TopologyModel

# アンカーノード ID の接頭辞。実デバイス ID と衝突しないよう記号を含める。
_ANCHOR_PREFIX = "__za_"

# 不可視アンカーノードの属性（微小な固定サイズの点として扱い、描画はしない）。
# 完全な zero-size にすると spline 経路計算が破綻して警告が出るため、極小値を与える。
_ANCHOR_ATTRS = 'style=invis, shape=point, width=0.01, height=0.01, fixedsize=true'


@dataclass(frozen=True)
class ZonePlacement:
    """Stage 1 の結果（ゾーンの段・段内順）。

    Attributes:
        tiers: 上段→下段の順に並べたゾーン名リストのリスト。``tiers[t]`` は
            段 ``t`` に属するゾーン名を左→右順に並べたもの。
        tier_of: ゾーン名 → 段番号（0 が最上段）。
        order_in_tier: ゾーン名 → 段内左右順（0 が最左）。
    """

    tiers: list[list[str]] = field(default_factory=list)
    tier_of: dict[str, int] = field(default_factory=dict)
    order_in_tier: dict[str, int] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """段が 1 つも無い（フォールバック）状態かどうか。"""
        return not self.tiers

    @property
    def zones(self) -> list[str]:
        """配置対象となった全ゾーン名（上段→下段・左→右順）。"""
        return [z for tier in self.tiers for z in tier]


def anchor_name(zone: str) -> str:
    """ゾーン名から不可視アンカーノードの ID を決める。

    DOT へ出力する際は常にダブルクォートで囲む前提なので、ゾーン名に含まれる
    ハイフン等の特殊文字はそのまま利用できる。
    """
    return f"{_ANCHOR_PREFIX}{zone}"


def anchor_decl(zone: str) -> str:
    """ゾーンの不可視アンカーノード宣言行を返す。

    この行は対象ゾーンの ``subgraph cluster`` 内に配置すること。アンカーが
    cluster 内にあることで、アンカーに課した rank 制約がクラスタ位置に波及する。
    """
    return f'"{anchor_name(zone)}" [{_ANCHOR_ATTRS}];'


def _inter_zone_weights(model: TopologyModel) -> dict[tuple[str, str], int]:
    """別ゾーン間の物理接続本数を、ゾーン対（名前昇順タプル）ごとに集約する。

    同一ゾーン内接続・ゾーン未設定端点・端点数が 2 でない接続は無視する。
    """
    weights: dict[tuple[str, str], int] = {}
    for conn in model.connections:
        eps = conn.get("endpoint", [])
        if len(eps) != 2:
            continue
        z0 = model.zone_of(eps[0].get("device-id", ""))
        z1 = model.zone_of(eps[1].get("device-id", ""))
        if not z0 or not z1 or z0 == z1:
            continue
        key = (z0, z1) if z0 < z1 else (z1, z0)
        weights[key] = weights.get(key, 0) + 1
    return weights


def _zone_graph_dot(zones: list[str], weights: dict[tuple[str, str], int]) -> str:
    """ゾーングラフ（ゾーン=ノード・ゾーン間接続=エッジ）の DOT を組み立てる。

    ノード宣言・エッジ宣言ともに決定論的な順序（ゾーン名昇順）で出力し、
    ``dot`` のタイブレークを安定させる。エッジ方向は名前昇順（a→b）に固定する。
    """
    lines = ["digraph zonelayout {", "  rankdir=TB;", "  node [shape=box];"]
    for zone in zones:
        lines.append(f'  "{zone}";')
    for (a, b), weight in sorted(weights.items()):
        lines.append(f'  "{a}" -> "{b}" [weight={weight}];')
    lines.append("}")
    return "\n".join(lines)


def _parse_plain_positions(plain: str) -> dict[str, tuple[float, float]]:
    """``dot -Tplain`` 相当の出力から各ノードの (x, y) 座標を取り出す。

    plain 形式の ``node <name> <x> <y> ...`` 行のみを対象とする。名前に空白が
    含まれてもよいよう :func:`shlex.split` で分解する。
    """
    positions: dict[str, tuple[float, float]] = {}
    for line in plain.splitlines():
        if not line.startswith("node"):
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        if len(tokens) < 4 or tokens[0] != "node":
            continue
        try:
            x, y = float(tokens[2]), float(tokens[3])
        except ValueError:
            continue
        positions[tokens[1]] = (x, y)
    return positions


def _placement_from_weights(weights: dict[tuple[str, str], int]) -> ZonePlacement:
    """ゾーン対→接続本数の重みから :class:`ZonePlacement` を算出する（Stage1 の中核）。

    ゾーングラフを ``dot`` で配置し、y（段）・x（段内順）から段構成を決める。
    ゾーンが 2 個未満・重みが空・``dot`` 実行や座標解析に失敗した場合は空を返す。

    plain 形式では y 軸は上向き（原点が左下）なので、y が大きいゾーンほど上段になる。
    """
    zones = sorted({z for pair in weights for z in pair})
    if not weights or len(zones) < 2:
        return ZonePlacement()

    dot = _zone_graph_dot(zones, weights)
    try:
        out = graphviz.Source(dot).pipe(format="plain")
    except Exception:
        return ZonePlacement()
    text = out.decode("utf-8", "ignore") if isinstance(out, bytes) else str(out)

    positions = _parse_plain_positions(text)
    if not positions:
        return ZonePlacement()

    # y（段）を降順にまとめ、上段（y 最大）を tier 0 とする。
    unique_y = sorted({round(y, 2) for _, y in positions.values()}, reverse=True)
    tier_index = {y: i for i, y in enumerate(unique_y)}

    buckets: list[list[tuple[float, str]]] = [[] for _ in unique_y]
    for name, (x, y) in positions.items():
        buckets[tier_index[round(y, 2)]].append((x, name))

    tiers: list[list[str]] = []
    tier_of: dict[str, int] = {}
    order_in_tier: dict[str, int] = {}
    for ti, bucket in enumerate(buckets):
        # 段内は x 昇順（同 x はゾーン名でタイブレーク）で左→右に並べる。
        bucket.sort(key=lambda item: (item[0], item[1]))
        names = [name for _, name in bucket]
        tiers.append(names)
        for oi, name in enumerate(names):
            tier_of[name] = ti
            order_in_tier[name] = oi

    return ZonePlacement(tiers=tiers, tier_of=tier_of, order_in_tier=order_in_tier)


def compute_zone_placement(model: TopologyModel) -> ZonePlacement:
    """Stage 1: モデルのゾーン間接続からゾーングラフを配置し、段・段内順を確定する。

    以下のいずれかに該当する場合は空の :class:`ZonePlacement` を返し、呼び出し側は
    従来動作にフォールバックする:

    - ゾーン間接続が存在しない
    - 配置対象ゾーンが 2 個未満
    - ``dot`` の実行・座標解析に失敗した
    """
    return _placement_from_weights(_inter_zone_weights(model))


def compute_zone_placement_from_pairs(
    pairs: Iterable[tuple[str, str]],
) -> ZonePlacement:
    """Stage 1（DOT 由来）: ゾーン対の列から段・段内順を確定する。

    LLM 生成 DOT のように ``TopologyModel`` を持たない経路向け。各要素は
    ``(zoneA, zoneB)`` のゾーン対で、空文字・同一ゾーンの対は無視する。方向は
    名前昇順に正規化して集約する。
    """
    weights: dict[tuple[str, str], int] = {}
    for a, b in pairs:
        if not a or not b or a == b:
            continue
        key = (a, b) if a < b else (b, a)
        weights[key] = weights.get(key, 0) + 1
    return _placement_from_weights(weights)



def zone_constraint_dot(
    placement: ZonePlacement,
    zone_anchor: dict[str, str] | None = None,
) -> list[str]:
    """Stage 2: ゾーンの段（縦順）を固定する制約 DOT 行を生成する。

    生成する行は本図のグラフ最上位（各 cluster の外側）に配置する。アンカーノード
    自体の宣言（:func:`anchor_decl`）は各 cluster 内に置いておくこと。

    段内の**左右順**は、クラスタ全幅を横断する不可視エッジが ``dot`` の spline 経路
    計算を破綻させる（"Unable to reclaim box space" 警告）ため、ここでは DOT へは
    出力しない。左右順は呼び出し側が cluster の出力順で決定論的に制御する
    （:attr:`ZonePlacement.order_in_tier` を参照）。

    Args:
        placement: :func:`compute_zone_placement` の結果。
        zone_anchor: ゾーン名 → アンカーノード ID の対応。省略時は
            :func:`anchor_name` で自動生成する。

    Returns:
        DOT 行のリスト（空の placement では空リスト）。以下を含む:

        - 各段の ``{ rank=same; ... }``（同段を同一ランクに揃える）
        - 隣接段どうしを結ぶ不可視エッジ（段の上下順を固定）
    """
    if placement.is_empty():
        return []
    if zone_anchor is None:
        zone_anchor = {z: anchor_name(z) for z in placement.zones}

    lines: list[str] = ["// --- zone tier constraints (2-stage layout) ---"]

    # 各段: rank=same で同段を同一ランクに揃える（左右順は cluster 出力順で担保）。
    for tier in placement.tiers:
        anchors = [zone_anchor[z] for z in tier if z in zone_anchor]
        if not anchors:
            continue
        members = " ".join(f'"{a}";' for a in anchors)
        lines.append(f"{{ rank=same; {members} }}")

    # 隣接段: 各段の先頭アンカーどうしを不可視エッジで結び、上下順を固定する。
    for upper, lower in zip(placement.tiers, placement.tiers[1:]):
        if not upper or not lower:
            continue
        top = zone_anchor.get(upper[0])
        bottom = zone_anchor.get(lower[0])
        if top and bottom:
            lines.append(f'"{top}" -> "{bottom}" [style=invis];')

    return lines
