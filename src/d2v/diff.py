"""トポロジの意味的 diff（構造差分）。

2 つの ``TopologyModel``（変更前 / 変更後）を比較し、行 diff ではなく
**構造的な変化**（ノード・エッジ・ゾーン・サブネットの追加/削除、ノード属性変更）を
``TopologyDiff`` として決定論的に算出する。

方針:
- 差分計算は純 Python・決定論的（LLM 非依存）。
- ノードは ``device-id``、サブネットは ``subnet-id``/prefix を安定キーに追跡する。
- エッジは **無向・ポート込みの正規化キー**（端点の {device-id, interface-id} 集合）で
  同一物理リンクを識別する。connection-id は表示ラベルにのみ使う（リネームで
  差分が発生しないようにするため）。

Phase 0: データモデル・``compare()``・rich テキスト整形。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field
from rich.console import Group, RenderableType
from rich.text import Text

from d2v.parser import TopologyModel

_YamlDict = dict[str, Any]

# ノード属性比較の対象フィールド（表示順）。
_NODE_FIELDS: tuple[str, ...] = ("device-type", "zone", "asn", "loopback", "interfaces")


# ---------------------------------------------------------------------------
# データモデル
# ---------------------------------------------------------------------------


class AttrChange(BaseModel):
    """ノード属性 1 件の変更。"""

    field: str            # 変更フィールド（device-type / zone / asn / loopback / interfaces）
    before: str | None
    after: str | None


class NodeChange(BaseModel):
    """既存ノードの属性変更。"""

    device_id: str
    changes: list[AttrChange]


class TopologyDiff(BaseModel):
    """2 トポロジ間の構造差分。"""

    nodes_added: list[str] = Field(default_factory=list)
    nodes_removed: list[str] = Field(default_factory=list)
    nodes_changed: list[NodeChange] = Field(default_factory=list)
    edges_added: list[str] = Field(default_factory=list)
    edges_removed: list[str] = Field(default_factory=list)
    zones_added: list[str] = Field(default_factory=list)
    zones_removed: list[str] = Field(default_factory=list)
    subnets_added: list[str] = Field(default_factory=list)
    subnets_removed: list[str] = Field(default_factory=list)
    summary: str = ""  # LLM 要約（Phase 2 で充填）

    def is_empty(self) -> bool:
        """構造変化が 1 件も無ければ True。"""
        return not (
            self.nodes_added
            or self.nodes_removed
            or self.nodes_changed
            or self.edges_added
            or self.edges_removed
            or self.zones_added
            or self.zones_removed
            or self.subnets_added
            or self.subnets_removed
        )


# ---------------------------------------------------------------------------
# 比較ヘルパ
# ---------------------------------------------------------------------------


def _iface_ids(dev: _YamlDict) -> set[str]:
    return {
        i.get("interface-id")
        for i in dev.get("interface", []) or []
        if i.get("interface-id")
    }


def _fmt(value: object) -> str | None:
    """属性値を比較・表示用の文字列に正規化する（None はそのまま）。"""
    if value is None:
        return None
    return str(value)


def _node_attrs(dev: _YamlDict) -> dict[str, str | None]:
    """ノードの比較対象属性を取り出す。"""
    ifaces = ", ".join(sorted(_iface_ids(dev)))
    return {
        "device-type": _fmt(dev.get("device-type")),
        "zone": _fmt(dev.get("zone")),
        "asn": _fmt(dev.get("asn")),
        "loopback": _fmt(dev.get("loopback")),
        "interfaces": ifaces or None,
    }


def _edge_identity(conn: _YamlDict) -> frozenset[tuple[str | None, str | None]] | None:
    """無向・ポート込みの接続キーを返す（端点が 2 個でなければ None）。"""
    eps = conn.get("endpoint", []) or []
    if len(eps) != 2:
        return None
    a = (eps[0].get("device-id"), eps[0].get("interface-id"))
    b = (eps[1].get("device-id"), eps[1].get("interface-id"))
    return frozenset((a, b))


def _edge_label(conn: _YamlDict) -> str:
    """接続の表示ラベル（connection-id 優先、無ければ端点から合成）。"""
    cid = conn.get("connection-id")
    if cid:
        return str(cid)
    parts: list[str] = []
    for ep in conn.get("endpoint", []) or []:
        did = ep.get("device-id", "?")
        iid = ep.get("interface-id", "")
        parts.append(f"{did}[{iid}]" if iid else str(did))
    return " <-> ".join(parts)


def _edge_map(model: TopologyModel) -> dict[frozenset, str]:
    """接続キー → 表示ラベルの辞書を返す。"""
    result: dict[frozenset, str] = {}
    for conn in model.connections:
        key = _edge_identity(conn)
        if key is not None:
            result.setdefault(key, _edge_label(conn))
    return result


def _zone_set(model: TopologyModel) -> set[str]:
    return {
        z for dev in model.devices if (z := dev.get("zone"))
    }


def _subnet_map(model: TopologyModel) -> dict[str, str]:
    """サブネットキー（subnet-id 優先、無ければ prefix）→ 表示ラベル。"""
    result: dict[str, str] = {}
    for sn in model.subnets:
        prefix = sn.get("prefix")
        sid = sn.get("subnet-id")
        key = str(sid) if sid else (str(prefix) if prefix else None)
        if key is None:
            continue
        label = str(sid) if sid else str(prefix)
        if prefix and sid:
            label = f"{sid} ({prefix})"
        result.setdefault(key, label)
    return result


# ---------------------------------------------------------------------------
# 比較本体
# ---------------------------------------------------------------------------


def compare(before: TopologyModel, after: TopologyModel) -> TopologyDiff:
    """2 つのトポロジモデルを比較し ``TopologyDiff`` を返す。"""
    before_ids = set(before.device_map)
    after_ids = set(after.device_map)

    nodes_added = sorted(after_ids - before_ids)
    nodes_removed = sorted(before_ids - after_ids)

    nodes_changed: list[NodeChange] = []
    for did in sorted(before_ids & after_ids):
        b_attrs = _node_attrs(before.device_map[did])
        a_attrs = _node_attrs(after.device_map[did])
        changes = [
            AttrChange(field=f, before=b_attrs[f], after=a_attrs[f])
            for f in _NODE_FIELDS
            if b_attrs[f] != a_attrs[f]
        ]
        if changes:
            nodes_changed.append(NodeChange(device_id=did, changes=changes))

    before_edges = _edge_map(before)
    after_edges = _edge_map(after)
    edges_added = sorted(
        label for key, label in after_edges.items() if key not in before_edges
    )
    edges_removed = sorted(
        label for key, label in before_edges.items() if key not in after_edges
    )

    before_zones = _zone_set(before)
    after_zones = _zone_set(after)
    zones_added = sorted(after_zones - before_zones)
    zones_removed = sorted(before_zones - after_zones)

    before_subnets = _subnet_map(before)
    after_subnets = _subnet_map(after)
    subnets_added = sorted(
        label for key, label in after_subnets.items() if key not in before_subnets
    )
    subnets_removed = sorted(
        label for key, label in before_subnets.items() if key not in after_subnets
    )

    return TopologyDiff(
        nodes_added=nodes_added,
        nodes_removed=nodes_removed,
        nodes_changed=nodes_changed,
        edges_added=edges_added,
        edges_removed=edges_removed,
        zones_added=zones_added,
        zones_removed=zones_removed,
        subnets_added=subnets_added,
        subnets_removed=subnets_removed,
    )


# ---------------------------------------------------------------------------
# LLM 自然言語サマリ（Phase 2: --summarize）
# ---------------------------------------------------------------------------


def summarize(diff: TopologyDiff, llm: Any | None = None) -> TopologyDiff:
    """構造差分に LLM で自然言語サマリを付与する（``summary`` を充填して返す）。

    差分の**内容だけ**を言語化する（捏造禁止はプロンプトで担保）。変化なしの
    ときは LLM を呼ばず、差分をそのまま返す。LLM 応答が空でも構造差分は不変。
    """
    if diff.is_empty():
        return diff

    if llm is None:
        from d2v.llm import get_llm

        llm = get_llm()

    from d2v.prompts import load_prompt

    payload = diff.model_dump(exclude={"summary"})
    system = load_prompt("diagram-diff.md")
    user = (
        "## 構造差分（JSON）\n\n```json\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )
    response = llm.chat(system=system, user=user)
    text = _strip_code_fence(response).strip()
    return diff.model_copy(update={"summary": text})


def _strip_code_fence(text: str) -> str:
    """先頭・末尾のコードフェンス（```）を取り除く。"""
    m = re.search(r"```(?:\w+)?\s*(.*?)```", text, re.DOTALL)
    return m.group(1) if m else text


# ---------------------------------------------------------------------------
# rich 整形
# ---------------------------------------------------------------------------

_ADD_STYLE = "green"
_DEL_STYLE = "red"
_CHG_STYLE = "yellow"


def render_diff(diff: TopologyDiff) -> RenderableType:
    """差分を rich renderable（＋/−/~ 記号つき）に整形する。"""
    if diff.is_empty():
        return Text("= 構造上の変化はありません。", style="bold green")

    summary = Text()
    summary.append("差分サマリ  ", style="bold")
    summary.append(
        f"+ノード {len(diff.nodes_added)}  -ノード {len(diff.nodes_removed)}  "
        f"~ノード {len(diff.nodes_changed)}  ",
    )
    summary.append(f"+エッジ {len(diff.edges_added)}  -エッジ {len(diff.edges_removed)}")

    body = Text()

    def _lines(title: str, items: list[str], sign: str, style: str) -> None:
        if not items:
            return
        body.append(f"\n{title}\n", style="bold")
        for item in items:
            body.append(f"  {sign} ", style=style)
            body.append(f"{item}\n")

    _lines("ノード追加", diff.nodes_added, "+", _ADD_STYLE)
    _lines("ノード削除", diff.nodes_removed, "-", _DEL_STYLE)

    if diff.nodes_changed:
        body.append("\nノード変更\n", style="bold")
        for nc in diff.nodes_changed:
            body.append("  ~ ", style=_CHG_STYLE)
            body.append(f"{nc.device_id}\n")
            for ch in nc.changes:
                body.append(
                    f"      {ch.field}: {ch.before} → {ch.after}\n", style="dim"
                )

    _lines("エッジ追加", diff.edges_added, "+", _ADD_STYLE)
    _lines("エッジ削除", diff.edges_removed, "-", _DEL_STYLE)
    _lines("ゾーン追加", diff.zones_added, "+", _ADD_STYLE)
    _lines("ゾーン削除", diff.zones_removed, "-", _DEL_STYLE)
    _lines("サブネット追加", diff.subnets_added, "+", _ADD_STYLE)
    _lines("サブネット削除", diff.subnets_removed, "-", _DEL_STYLE)

    renderables: list[RenderableType] = [summary, body]
    if diff.summary:
        renderables.append(Text(f"\n{diff.summary}", style="italic"))
    return Group(*renderables)


# ---------------------------------------------------------------------------
# 差分図（Graphviz DOT）: 和集合グラフを色分け描画
# ---------------------------------------------------------------------------

# device-type → 絵文字アイコン（d2v の作図規約に準拠）
_ICON: dict[str, str] = {
    "router": "🌐",
    "switch": "🔀",
    "firewall": "🧱",
    "server": "💻",
    "host": "💻",
    "load-balancer": "⚖️",
}

# 差分ステータス → (fillcolor, color, node style)
_NODE_STATUS_STYLE: dict[str, tuple[str, str, str]] = {
    "added": ("#E6F4EA", "#137333", "filled,rounded"),
    "removed": ("#FCE8E6", "#C5221F", "filled,rounded,dashed"),
    "changed": ("#FEF7E0", "#E37400", "filled,rounded"),
    "unchanged": ("#F1F3F4", "#9AA0A6", "filled,rounded"),
}

# 差分ステータス → (color, style, penwidth)
_EDGE_STATUS_STYLE: dict[str, tuple[str, str, str]] = {
    "added": ("#137333", "solid", "2"),
    "removed": ("#C5221F", "dashed", "2"),
    "unchanged": ("#9AA0A6", "solid", "1"),
}


def _q(s: object) -> str:
    """DOT 用にダブルクォートで囲む（内部のダブルクォートのみエスケープ）。"""
    return '"' + str(s).replace('"', '\\"') + '"'


def _collect_edge_status(
    before: TopologyModel, after: TopologyModel
) -> list[tuple[str, str, str]]:
    """和集合エッジを (a_device, b_device, status) の決定的リストで返す。"""
    entries: dict[frozenset, dict[str, Any]] = {}
    for model, flag in ((before, "before"), (after, "after")):
        for conn in model.connections:
            key = _edge_identity(conn)
            if key is None:
                continue
            eps = conn.get("endpoint", [])
            entry = entries.setdefault(
                key,
                {
                    "a": eps[0].get("device-id"),
                    "b": eps[1].get("device-id"),
                    "before": False,
                    "after": False,
                },
            )
            entry[flag] = True

    out: list[tuple[str, str, str]] = []
    for entry in entries.values():
        if entry["before"] and entry["after"]:
            status = "unchanged"
        elif entry["after"]:
            status = "added"
        else:
            status = "removed"
        out.append((entry["a"] or "", entry["b"] or "", status))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return out


def _legend_dot() -> str:
    return (
        "    subgraph cluster_legend {\n"
        '        label="凡例"; bgcolor="#FFFFFF"; color="#5F6368"; fontcolor="#3C4043";\n'
        '        "legend_add"  [label="追加", fillcolor="#E6F4EA", color="#137333", style="filled,rounded"];\n'
        '        "legend_del"  [label="削除", fillcolor="#FCE8E6", color="#C5221F", style="filled,rounded,dashed"];\n'
        '        "legend_chg"  [label="変更", fillcolor="#FEF7E0", color="#E37400", style="filled,rounded"];\n'
        '        "legend_same" [label="変更なし", fillcolor="#F1F3F4", color="#9AA0A6", style="filled,rounded"];\n'
        '        "legend_add" -> "legend_del" -> "legend_chg" -> "legend_same" [style=invis];\n'
        "    }"
    )


def build_diff_dot(
    before: TopologyModel,
    after: TopologyModel,
    diff: TopologyDiff,
) -> str:
    """変更前後を重ねた**和集合グラフ**を色分けした Graphviz DOT を生成する。

    追加=緑・削除=赤(点線)・変更=橙・無変更=淡灰で描画し、zone ごとに cluster 化する。
    """
    added = set(diff.nodes_added)
    removed = set(diff.nodes_removed)
    changed = {nc.device_id: nc for nc in diff.nodes_changed}

    def _status(did: str) -> str:
        if did in added:
            return "added"
        if did in removed:
            return "removed"
        if did in changed:
            return "changed"
        return "unchanged"

    def _dev(did: str) -> _YamlDict:
        return after.device_map.get(did) or before.device_map.get(did) or {}

    node_ids = sorted(set(before.device_map) | set(after.device_map))

    # zone ごとにグループ化（after の zone を優先）
    by_zone: dict[str, list[str]] = {}
    for did in node_ids:
        by_zone.setdefault(_dev(did).get("zone") or "", []).append(did)

    lines: list[str] = [
        "digraph diff {",
        "    compound=true; newrank=true; rankdir=TB;",
        '    graph [fontname="Helvetica,Arial,sans-serif"];',
        '    node [fontname="Helvetica,Arial,sans-serif", fontsize=10, shape=box, style="filled,rounded"];',
        '    edge [fontname="Helvetica,Arial,sans-serif", fontsize=8];',
    ]

    def _emit_node(did: str, indent: str) -> None:
        status = _status(did)
        fill, color, style = _NODE_STATUS_STYLE[status]
        dev = _dev(did)
        icon = _ICON.get(dev.get("device-type"), "📦")
        label = f"{icon} {did}"
        tooltip = {"added": "追加", "removed": "削除", "unchanged": "変更なし"}.get(status, "変更")
        if did in changed:
            fields = ", ".join(c.field for c in changed[did].changes)
            label += f"\\n(変更: {fields})"
            tooltip = "; ".join(
                f"{c.field}: {c.before}→{c.after}" for c in changed[did].changes
            )
        lines.append(
            f'{indent}{_q(did)} [label={_q(label)}, fillcolor="{fill}", '
            f'color="{color}", style="{style}", tooltip={_q(tooltip)}];'
        )

    ci = 0
    for zone in sorted(by_zone):
        members = by_zone[zone]
        if zone:
            lines.append(f"    subgraph cluster_z{ci} {{")
            lines.append(
                f"        label={_q(zone)}; bgcolor=\"#F8F9FA\"; "
                f'color="#5F6368"; fontcolor="#3C4043";'
            )
            for did in members:
                _emit_node(did, "        ")
            lines.append("    }")
            ci += 1
        else:
            for did in members:
                _emit_node(did, "    ")

    for a, b, status in _collect_edge_status(before, after):
        color, style, pw = _EDGE_STATUS_STYLE[status]
        lines.append(
            f'    {_q(a)} -> {_q(b)} [color="{color}", style="{style}", penwidth={pw}];'
        )

    lines.append(_legend_dot())
    lines.append("}")
    return "\n".join(lines)


def render_diff_diagram(
    before: TopologyModel,
    after: TopologyModel,
    diff: TopologyDiff,
    output_dir,
    stem: str = "diff",
    fmt: str = "png",
):
    """差分図をレンダリングして画像を保存する（DOT ソースも保存される）。

    Returns:
        生成した画像ファイルの Path。
    """
    from d2v import renderer

    dot_code = build_diff_dot(before, after, diff)
    return renderer.render(dot_code, output_dir, stem=stem, fmt=fmt)


# ---------------------------------------------------------------------------
# 影響分析（Phase 3: blast radius）
# ---------------------------------------------------------------------------


class ImpactReport(BaseModel):
    """機器/リンクの除去による到達性への影響。"""

    removed_devices: list[str] = Field(default_factory=list)
    removed_edges: list[str] = Field(default_factory=list)  # "a <-> b" ラベル
    reachable: list[str] = Field(default_factory=list)      # 最大連結成分（到達可能に残る）
    unreachable: list[str] = Field(default_factory=list)    # 分断され到達不能になるノード
    components: int = 0                                      # 除去後の残存連結成分数

    def is_isolating(self) -> bool:
        """除去により到達不能ノードが生じるなら True。"""
        return bool(self.unreachable)


def _components(adj: dict[str, set[str]]) -> list[set[str]]:
    """無向グラフの連結成分を列挙する。"""
    seen: set[str] = set()
    comps: list[set[str]] = []
    for start in adj:
        if start in seen:
            continue
        comp: set[str] = set()
        stack = [start]
        seen.add(start)
        while stack:
            u = stack.pop()
            comp.add(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(comp)
    return comps


def impact(
    model: TopologyModel,
    *,
    removed_devices: list[str] | None = None,
    removed_edges: list[tuple[str, str]] | None = None,
) -> ImpactReport:
    """指定した機器/リンクを落としたときの到達不能範囲（blast radius）を算出する。

    ``validator`` と同じ無向グラフ（``_build_graph``）を共有し、除去後の残存グラフの
    連結成分を求める。最大成分を「到達可能に残るコア」とし、そこから切り離された
    ノードを到達不能（分断された影響範囲）として返す。
    """
    from d2v import validator

    removed_dev_set = set(removed_devices or [])
    removed_edge_pairs = [tuple(e) for e in (removed_edges or [])]

    # 元グラフを可変コピーして除去を適用する
    adj = {k: set(v) for k, v in validator._build_graph(model).items()}

    edge_labels: list[str] = []
    for a, b in removed_edge_pairs:
        if a in adj:
            adj[a].discard(b)
        if b in adj:
            adj[b].discard(a)
        edge_labels.append(f"{a} <-> {b}")

    for d in removed_dev_set:
        adj.pop(d, None)
    for neighbors in adj.values():
        neighbors -= removed_dev_set

    comps = _components(adj)
    if comps:
        # 最大成分を到達可能コアとする（同数なら最小 device-id を含む成分を優先＝決定的）
        core = min(comps, key=lambda c: (-len(c), sorted(c)[0]))
    else:
        core = set()

    remaining = set(adj)
    return ImpactReport(
        removed_devices=sorted(removed_dev_set),
        removed_edges=sorted(edge_labels),
        reachable=sorted(core),
        unreachable=sorted(remaining - core),
        components=len(comps),
    )


def render_impact(report: ImpactReport) -> RenderableType:
    """影響分析結果を rich テキストに整形する。"""
    body = Text()
    body.append("影響分析（blast radius）\n", style="bold")
    removed = report.removed_devices + report.removed_edges
    body.append("除去: ", style="bold")
    body.append((", ".join(removed) or "（なし）") + "\n")
    if not report.unreachable:
        body.append("到達不能になるノードはありません（冗長経路あり）。", style="green")
    else:
        body.append(
            f"到達不能 {len(report.unreachable)} 台: ", style="bold red"
        )
        body.append(", ".join(report.unreachable) + "\n", style="red")
        body.append(f"（残存連結成分: {report.components}）", style="dim")
    return body


# 影響ステータス → (fillcolor, color, style, fontcolor)
_IMPACT_NODE_STYLE: dict[str, tuple[str, str, str, str]] = {
    "reachable": ("#E6F4EA", "#137333", "filled,rounded", "#0B3D1E"),
    "unreachable": ("#FCE8E6", "#C5221F", "filled,rounded", "#5F1512"),
    "removed": ("#5F6368", "#3C4043", "filled,rounded,dashed", "#FFFFFF"),
}


def build_impact_dot(model: TopologyModel, report: ImpactReport) -> str:
    """影響範囲をハイライトした Graphviz DOT を生成する。

    除去機器=灰(破線)・到達不能=赤・到達可能=緑で塗り分け、除去/切断されたリンクは
    赤の破線で描画する。
    """
    removed = set(report.removed_devices)
    unreachable = set(report.unreachable)
    removed_pairs = {
        frozenset(lbl.split(" <-> ")) for lbl in report.removed_edges
    }

    def _status(did: str) -> str:
        if did in removed:
            return "removed"
        if did in unreachable:
            return "unreachable"
        return "reachable"

    node_ids = sorted(model.device_map)
    by_zone: dict[str, list[str]] = {}
    for did in node_ids:
        by_zone.setdefault(model.device_map[did].get("zone") or "", []).append(did)

    lines: list[str] = [
        "digraph impact {",
        "    compound=true; newrank=true; rankdir=TB;",
        '    graph [fontname="Helvetica,Arial,sans-serif"];',
        '    node [fontname="Helvetica,Arial,sans-serif", fontsize=10, shape=box, style="filled,rounded"];',
        '    edge [fontname="Helvetica,Arial,sans-serif", fontsize=8];',
    ]

    def _emit_node(did: str, indent: str) -> None:
        fill, color, style, fontcolor = _IMPACT_NODE_STYLE[_status(did)]
        dev = model.device_map.get(did, {})
        icon = _ICON.get(dev.get("device-type"), "📦")
        mark = "✖ " if did in removed else ""
        lines.append(
            f'{indent}{_q(did)} [label={_q(mark + icon + " " + did)}, '
            f'fillcolor="{fill}", color="{color}", style="{style}", fontcolor="{fontcolor}"];'
        )

    ci = 0
    for zone in sorted(by_zone):
        members = by_zone[zone]
        if zone:
            lines.append(f"    subgraph cluster_iz{ci} {{")
            lines.append(
                f"        label={_q(zone)}; bgcolor=\"#F8F9FA\"; "
                f'color="#5F6368"; fontcolor="#3C4043";'
            )
            for did in members:
                _emit_node(did, "        ")
            lines.append("    }")
            ci += 1
        else:
            for did in members:
                _emit_node(did, "    ")

    for conn in model.connections:
        key = _edge_identity(conn)
        if key is None:
            continue
        eps = conn.get("endpoint", [])
        a, b = eps[0].get("device-id"), eps[1].get("device-id")
        affected = (
            a in removed or b in removed or frozenset((a, b)) in removed_pairs
        )
        if affected:
            attrs = 'color="#C5221F", style="dashed", penwidth=2'
        else:
            attrs = 'color="#9AA0A6", style="solid", penwidth=1'
        lines.append(f"    {_q(a)} -> {_q(b)} [{attrs}];")

    lines.append("}")
    return "\n".join(lines)


def render_impact_diagram(
    model: TopologyModel,
    report: ImpactReport,
    output_dir,
    stem: str = "impact",
    fmt: str = "png",
):
    """影響範囲ハイライト図をレンダリングして画像を保存する。"""
    from d2v import renderer

    dot_code = build_impact_dot(model, report)
    return renderer.render(dot_code, output_dir, stem=stem, fmt=fmt)
