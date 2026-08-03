"""決定論 focus 図（LLM 不使用）の DOT 直接生成: build_focus_dot, render_focus_diagram。"""

from __future__ import annotations

from collections import OrderedDict

from d2v import icons
from d2v.parser import TopologyModel
from d2v.partitioner._dot_builder import hop_distances
from d2v.partitioner._focus import FocusData, _focus_data

# 役割 → (fillcolor, color, penwidth)
_FOCUS_STYLE_FOCUS = ("#D2E3FC", "#1A73E8", "2.4")   # ★注目ノード（強調）
_FOCUS_STYLE_NORMAL = ("#F1F3F4", "#9AA0A6", "1")    # 通常ノード
_FOCUS_STYLE_BOUNDARY = ("#F1F3F4", "#E37400", "1")  # 境界（この先に省略あり）


def _dq(s: object) -> str:
    """DOT 用にダブルクォートで囲む（内部のダブルクォートのみエスケープ）。"""
    return '"' + str(s).replace('"', '\\"') + '"'


def build_focus_dot(
    model: TopologyModel,
    focus_ids: "str | list[str]",
    hops: int = 1,
) -> str | None:
    """注目ノード周辺（hops ホップ以内）の集中図を LLM を使わず決定論的に
    Graphviz DOT へ変換する（エディタのライブプレビュー用）。

    - 注目ノードは青で強調（★注目・0 ホップ）。
    - 境界ノード（この先に省略された接続を持つ）は橙の破線枠で表す。
    - 各ノードには ``id="device:<device-id>"`` を付与し、SVG 上のクリックから
      YAML 定義行への双方向ジャンプを可能にする。
    - zone があればゆるく cluster 化する。

    指定した注目ノードのいずれかがトポロジに存在しない場合は None を返す。
    同一入力に対して常に同一の DOT を返す（冪等）。
    """
    data = _focus_data(model, focus_ids, hops)
    if data is None:
        return None

    focus_set = set(data.focus_ids)

    # zone ごとにグルーピングする（dist の決定論的な初出順を維持）
    by_zone: "OrderedDict[str, list[str]]" = OrderedDict()
    for did in data.dist:
        z = model.device_map.get(did, {}).get("zone") or ""
        by_zone.setdefault(z, []).append(did)

    lines: list[str] = [
        "digraph focus {",
        "    compound=true; newrank=true; rankdir=LR;",
        '    graph [fontname="Helvetica,Arial,sans-serif"];',
        '    node [fontname="Helvetica,Arial,sans-serif", fontsize=10, '
        'shape=box, style="filled,rounded"];',
        '    edge [fontname="Helvetica,Arial,sans-serif", fontsize=8, '
        'color="#5F6368"];',
    ]

    def _emit_node(did: str, indent: str) -> None:
        dev = model.device_map.get(did, {"device-id": did})
        name = dev.get("device-name", "")
        hop = data.dist.get(did, 0)
        is_focus = did in focus_set
        if is_focus:
            fill, color, pw = _FOCUS_STYLE_FOCUS
            marker = "★注目・0 ホップ"
            style = "filled,rounded"
        elif did in data.truncated:
            fill, color, pw = _FOCUS_STYLE_BOUNDARY
            marker = f"{hop} ホップ・この先 {data.truncated[did]} 台省略"
            style = "filled,rounded,dashed"
        else:
            fill, color, pw = _FOCUS_STYLE_NORMAL
            marker = f"{hop} ホップ"
            style = "filled,rounded"

        label_lines = [did]
        if name and name != did:
            label_lines.append(name)
        label_lines.append(marker)
        label = icons.html_label(dev.get("device-type"), label_lines)
        tooltip = did + (f" / {name}" if name and name != did else "") + \
            f" ・ {marker}"
        lines.append(
            f"{indent}{_dq(did)} [label={label}, "
            f'fillcolor="{fill}", color="{color}", penwidth={pw}, '
            f'style="{style}", id={_dq("device:" + did)}, '
            f"tooltip={_dq(tooltip)}];"
        )

    ci = 0
    for zone, members in by_zone.items():
        if zone:
            lines.append(f"    subgraph cluster_z{ci} {{")
            lines.append(
                f"        label={_dq(zone)}; bgcolor=\"#F8F9FA\"; "
                f'color="#5F6368"; fontcolor="#3C4043";'
            )
            for did in members:
                _emit_node(did, "        ")
            lines.append("    }")
            ci += 1
        else:
            for did in members:
                _emit_node(did, "    ")

    # 物理接続（両端が抽出範囲内のものだけ）を決定論的な順序で描く
    for conn in data.intra:
        eps = conn.get("endpoint", [])
        d0 = eps[0].get("device-id", "")
        d1 = eps[1].get("device-id", "")
        cid = conn.get("connection-id", f"{d0}__{d1}")
        lines.append(f"    {_dq(d0)} -> {_dq(d1)} [tooltip={_dq(cid)}];")

    # 間接接続（省略された中継ノードを介して繋がる組）を破線で示す。
    # 矢印は付けず、レイアウトを歪めないよう constraint=false とする。
    for u, v, via, count in data.indirect:
        via_txt = via if count == 1 else f"{via} ほか{count - 1}台"
        lbl = f"{via_txt} 経由"
        lines.append(
            f"    {_dq(u)} -> {_dq(v)} "
            f'[style=dashed, color="#9AA0A6", arrowhead=none, '
            f"constraint=false, label={_dq(lbl)}, fontsize=8, "
            f'fontcolor="#80868B", tooltip={_dq(f"{u} ┈ {v}（{lbl}）")}];'
        )

    lines.append("}")
    return "\n".join(lines)


def render_focus_diagram(
    model: TopologyModel,
    focus_ids: "str | list[str]",
    output_dir,
    hops: int = 1,
    stem: str = "focus",
    fmt: str = "svg",
):
    """決定論 focus 図（LLM 不使用）を DOT 化・レンダリングして画像を保存する。

    focus_ids のいずれかがトポロジに存在しない場合は None を返す。

    Returns:
        生成した画像ファイルの Path（focus 不在時は None）。
    """
    from d2v import renderer

    dot_code = build_focus_dot(model, focus_ids, hops)
    if dot_code is None:
        return None
    return renderer.render(dot_code, output_dir, stem=stem, fmt=fmt)
