"""ノード集中図（focus）の LLM テキスト生成パス: FocusData, focus_plan, _focus_text。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from d2v import parser
from d2v.parser import TopologyModel
from d2v.partitioner._dot_builder import (
    SubDiagram,
    _build_adjacency,
    _safe_key,
    _subnets_for,
    hop_distances,
)

_YamlDict = dict[str, Any]


def _focus_text(
    model: TopologyModel,
    focus_ids: list[str],
    hops: int,
    dist: "OrderedDict[str, int]",
    intra: list[_YamlDict],
    truncated: "dict[str, int]",
    indirect: "list[tuple[str, str, str, int]]",
) -> str:
    """注目ノード集中図のテキストを生成する。

    Args:
        focus_ids: 注目ノードの device-id 群（1 台以上）。
        dist: device-id → ホップ数（focus_ids を含む）。
        intra: 両端が dist に含まれる物理接続。
        truncated: 境界ノード（hops ホップ地点）で、この先に省略された
            隣接ノードを持つものの device-id → 省略数。
        indirect: 直接リンクは無いが省略ノードを介して繋がる
            (u, v, 代表経由ノード, 経由ノード数) の一覧。
    """
    focus_set = set(focus_ids)

    def _label(did: str) -> str:
        dev = model.device_map.get(did, {"device-id": did})
        return f"{did} ({dev.get('device-name', did)})"

    focus_labels = "、".join(_label(f) for f in focus_ids)
    multi = len(focus_ids) > 1

    lines: list[str] = []
    if hops == 0:
        # hops=0: 指定した注目ノードのみ（相互接続だけ）を描く
        if multi:
            lines.append(f"# ノード集中図: {focus_labels} のみ\n")
            lines.append(
                f"この図は指定した {len(focus_ids)} 台のノード「{focus_labels}」"
                "だけを抜き出した部分構成図です。"
                "**指定ノードのみ**を描き、これらの間の物理接続だけを表示します。"
                "指定ノードに接続する他のノードは意図的に省略されています。\n"
            )
        else:
            lines.append(f"# ノード集中図: {focus_labels} のみ\n")
            lines.append(
                f"この図は指定したノード「{focus_labels}」だけを抜き出した"
                "部分構成図です。"
                "**指定ノードのみ**を描き、それに接続する他のノードは"
                "意図的に省略されています。\n"
            )
    elif multi:
        lines.append(
            f"# ノード集中図: {focus_labels} を中心に {hops} ホップ以内\n"
        )
        lines.append(
            f"この図は {len(focus_ids)} 台の注目ノード「{focus_labels}」を中心に、"
            f"そのいずれかから物理接続を {hops} ホップたどって到達できるノードだけを"
            "抜き出した部分構成図です。"
            "**注目ノード群を図の中心付近に近接させて強調配置**し、"
            "周辺ノードをその周りにバランスよく配置してください。"
            "各ノードの見出しに付いた「N ホップ」は最も近い注目ノードからの距離です。"
            "この範囲外のノードは意図的に省略されています。\n"
        )
    else:
        lines.append(f"# ノード集中図: {focus_labels} から {hops} ホップ以内\n")
        lines.append(
            f"この図は注目ノード「{focus_labels}」を中心に、"
            f"そこから物理接続を {hops} ホップたどって到達できるノードだけを抜き出した"
            "部分構成図です。"
            "**注目ノードを図の中心付近に強調配置**し、"
            "周辺ノードをその周りにバランスよく配置してください。"
            "各ノードの見出しに付いた「N ホップ」は注目ノードからの距離です。"
            "この範囲外のノードは意図的に省略されています。\n"
        )
    # 縦横比のバランス指示（集中図は 1 ホップ先が多いと縦長になりやすいため明示）
    lines.append(
        "**縦横比のバランス（重要）**: この図は注目ノードに多数のノードがぶら下がる"
        "スター状になりやすく、そのまま縦一列に並べると極端に縦長で読みづらくなります。"
        "図全体の縦横比は**幅 : 高さ ＝ 4 : 3 程度**のバランスを目指してください。"
        "1 ホップ先のノードが多い場合は、縦一列に積まず "
        "`{rank=same; ...}` で複数を同じ段に並べたり、`rankdir=LR` を用いて"
        "横方向へ展開したりして、極端な縦長を避けてください。\n"
    )

    # ホップ別のノード数サマリ
    by_hop: OrderedDict[int, list[str]] = OrderedDict()
    for did, h in dist.items():
        by_hop.setdefault(h, []).append(did)
    summary = ", ".join(
        (f"注目ノード×{len(ids)}" if h == 0 else f"{h} ホップ×{len(ids)}")
        for h, ids in sorted(by_hop.items())
    )

    # ノード一覧（ホップ数を注記）
    lines.append(f"## ノード一覧（{len(dist)} 台: {summary}）\n")
    for did, h in dist.items():
        dev = model.device_map.get(did, {"device-id": did})
        dl = parser.device_lines(dev)
        if did in focus_set:
            marker = "★注目ノード（中心・0 ホップ）"
        else:
            marker = f"{h} ホップ"
            if did in truncated:
                marker += f" ・ この先に {truncated[did]} 台の接続あり（省略）"
        # 先頭行（ヘッダ）にホップ注記を追記する
        if dl:
            dl[0] = f"{dl[0]}  «{marker}»"
        lines.extend(dl)

    # 接続一覧（LAG メンバーは 1 本の論理リンクに集約する）
    lag_lookup = parser.build_lag_lookup(model.lags)
    intra_lines, intra_count = parser.connection_section(intra, model.device_map, lag_lookup)
    lines.append(f"\n## 物理接続一覧（{intra_count} 本）\n")
    lines.extend(intra_lines)

    # 間接接続一覧（直接リンクは無いが、省略ノードを介して繋がる注目ノード同士）
    if indirect:
        lines.append(
            f"\n## 間接接続一覧（{len(indirect)} 組・省略ノード経由）\n"
        )
        lines.append(
            "以下のノード同士は直接リンクはありませんが、この図には描かれていない"
            "中継ノードを経由して繋がっています。これらの関係は**破線のエッジ**で結び、"
            "矢印は付けず、ラベルに経由ノード（例）を添えてください。"
            "実線の物理接続とは明確に区別できるようにしてください。\n"
        )
        for u, v, via, count in indirect:
            via_txt = via if count == 1 else f"{via} ほか {count - 1} 台"
            lines.append(f"- {_label(u)} ┈（{via_txt} 経由）┈ {_label(v)}")

    # 関連サブネット
    focus_devices = [
        model.device_map[d] for d in dist if d in model.device_map
    ]
    rel_subnets = _subnets_for(focus_devices, model.subnets)
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


@dataclass
class FocusData:
    """注目ノード集中図の構造データ（すべて決定論的に算出される）。

    LLM 経路（``focus_plan`` → ``_focus_text``）と決定論経路
    （``build_focus_dot``）の双方から共有され、二重実装を防ぐ。
    """

    focus_ids: list[str]           # 正規化済み注目ノード（重複除去・順序保持）
    hops: int                      # 抽出した最大ホップ数
    dist: "OrderedDict[str, int]"  # device-id → 最短ホップ数（注目ノードは 0）
    included: set[str]             # dist に含まれる device-id 集合
    intra: list[_YamlDict]         # 両端が included に含まれる物理接続
    truncated: dict[str, int]      # 境界ノード device-id → 省略された隣接数
    # 間接接続: (u, v, 代表経由ノード, 経由ノード数)。u/v は included 内だが
    # 直接リンクは無く、included 外（省略）の共通隣接ノードを介して繋がる組。
    indirect: list[tuple[str, str, str, int]]


def _focus_data(
    model: TopologyModel, focus_ids: "str | list[str]", hops: int
) -> "FocusData | None":
    """focus 指定を検証し、集中図の構造データを算出する。

    指定した注目ノードのいずれかがトポロジに存在しない場合は None を返す
    （呼び出し側でエラー表示）。``hops`` が 1 未満なら ValueError。
    """
    if isinstance(focus_ids, str):
        focus_ids = [focus_ids]
    # 重複を除きつつ指定順を保持する
    seen: dict[str, None] = {}
    for fid in focus_ids:
        seen.setdefault(fid, None)
    focus_ids = list(seen)

    if not focus_ids or any(fid not in model.device_map for fid in focus_ids):
        return None
    if hops < 0:
        raise ValueError("hops は 0 以上を指定してください。")

    dist = hop_distances(model, focus_ids, hops)
    included = set(dist)

    # 両端が included に含まれる物理接続のみ抽出
    intra: list[_YamlDict] = []
    for conn in model.connections:
        eps = conn.get("endpoint", [])
        if len(eps) != 2:
            continue
        d0 = eps[0].get("device-id", "")
        d1 = eps[1].get("device-id", "")
        if d0 in included and d1 in included:
            intra.append(conn)

    # 境界ノード（hops ホップ地点）で、この先に省略された隣接ノードを数える
    adj = _build_adjacency(model)
    truncated: dict[str, int] = {}
    for did, h in dist.items():
        if h != hops:
            continue
        hidden = [nb for nb in adj.get(did, set()) if nb not in included]
        if hidden:
            truncated[did] = len(hidden)

    # 間接接続: included 内の 2 ノードが直接リンクされていないが、included 外
    # （省略された）共通隣接ノードを介して繋がっている場合、その関係を記録する。
    direct_pairs: set[frozenset[str]] = set()
    for conn in intra:
        eps = conn.get("endpoint", [])
        d0 = eps[0].get("device-id", "")
        d1 = eps[1].get("device-id", "")
        if d0 and d1:
            direct_pairs.add(frozenset((d0, d1)))
    ordered = list(dist)
    indirect: list[tuple[str, str, str, int]] = []
    for i, u in enumerate(ordered):
        for v in ordered[i + 1:]:
            if frozenset((u, v)) in direct_pairs:
                continue
            common_hidden = sorted(
                (adj.get(u, set()) & adj.get(v, set())) - included
            )
            if common_hidden:
                indirect.append((u, v, common_hidden[0], len(common_hidden)))

    return FocusData(
        focus_ids=focus_ids,
        hops=hops,
        dist=dist,
        included=included,
        intra=intra,
        truncated=truncated,
        indirect=indirect,
    )


def focus_plan(
    model: TopologyModel, focus_ids: "str | list[str]", hops: int = 1
) -> SubDiagram | None:
    """注目ノード群から hops ホップ以内の集中図を返す。

    複数の注目ノードを指定した場合、いずれかのノードから hops ホップ以内に
    到達できるノードの和集合を 1 枚のサブグラフとして抽出する。
    指定した注目ノードのいずれかがトポロジに存在しない場合は None を返す
    （呼び出し側でエラー表示）。
    """
    data = _focus_data(model, focus_ids, hops)
    if data is None:
        return None
    focus_ids = data.focus_ids
    dist = data.dist

    def _name(did: str) -> str:
        return model.device_map.get(did, {}).get("device-name", did)

    key_ids = "-".join(_safe_key(f) for f in focus_ids)
    if hops == 0:
        names = "、".join(_name(f) for f in focus_ids)
        title = f"ノード集中図: {names} のみ（{len(dist)} 台）"
    elif len(focus_ids) > 1:
        names = "、".join(_name(f) for f in focus_ids)
        title = f"ノード集中図: {names} を中心に {hops} ホップ以内（{len(dist)} 台）"
    else:
        title = (
            f"ノード集中図: {_name(focus_ids[0])} から {hops} ホップ以内"
            f"（{len(dist)} 台）"
        )
    return SubDiagram(
        key=f"focus-{key_ids}-{hops}hop",
        title=title,
        text=_focus_text(
            model, focus_ids, hops, dist, data.intra, data.truncated,
            data.indirect,
        ),
    )
