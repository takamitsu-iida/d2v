"""エディタ連携（edit-assist）の補助ロジック。

YAML 本文とカーソル行から、いま注目している device（ノード）を解決する。
`web/app.py` の focus プレビュー API から使われ、単体でもテスト可能なように
Web 層から独立させている。

行番号の取得には PyYAML の ``yaml.compose``（ノードの ``start_mark`` /
``end_mark``）を用いる。追加依存はない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml


@dataclass
class FocusResolution:
    """カーソル行 → 注目ノードの解決結果。

    Attributes:
        focus_ids: 注目 device-id 群（device ブロック内なら 1 台、
            physical-connection ブロック内なら両端の 2 台）。空なら未解決。
        context: 解決元。``"device"`` / ``"connection"`` / ``"none"``。
        device_lines: device-id → 定義行（1 始まり）。SVG からの
            双方向ジャンプに使う。
    """

    focus_ids: list[str] = field(default_factory=list)
    context: str = "none"
    device_lines: dict[str, int] = field(default_factory=dict)


def _get(node: object, key: str) -> "yaml.Node | None":
    """MappingNode から指定キーの値ノードを返す（無ければ None）。"""
    if not isinstance(node, yaml.MappingNode):
        return None
    for k, v in node.value:
        if isinstance(k, yaml.ScalarNode) and k.value == key:
            return v
    return None


@dataclass
class _Span:
    """本文中のブロックが占める行範囲（0 始まり・半開区間 [start, end)）。"""

    focus_ids: list[str]
    context: str
    start: int
    end: int


def _parse_spans(text: str) -> "tuple[list[_Span], dict[str, int]]":
    """YAML を compose し、device / connection ブロックの行範囲を抽出する。

    解析に失敗した場合（編集途中の不正 YAML 等）は空を返す。
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return [], {}
    if root is None:
        return [], {}

    nm = _get(root, "network-model")
    phys = _get(nm, "physical-layer") if nm is not None else None
    if phys is None:
        return [], {}

    spans: list[_Span] = []
    device_lines: dict[str, int] = {}

    dev_seq = _get(phys, "device")
    if isinstance(dev_seq, yaml.SequenceNode):
        for item in dev_seq.value:
            did_node = _get(item, "device-id")
            if not isinstance(did_node, yaml.ScalarNode):
                continue
            did = did_node.value
            spans.append(
                _Span([did], "device", item.start_mark.line, item.end_mark.line)
            )
            # device-id 定義行（1 始まり）を記録する
            device_lines.setdefault(did, did_node.start_mark.line + 1)

    conn_seq = _get(phys, "physical-connection")
    if isinstance(conn_seq, yaml.SequenceNode):
        for item in conn_seq.value:
            ep = _get(item, "endpoint")
            dids: list[str] = []
            if isinstance(ep, yaml.SequenceNode):
                for e in ep.value:
                    dn = _get(e, "device-id")
                    if isinstance(dn, yaml.ScalarNode) and dn.value:
                        dids.append(dn.value)
            # 重複を除きつつ順序を保つ
            uniq = list(dict.fromkeys(dids))
            spans.append(
                _Span(uniq, "connection", item.start_mark.line, item.end_mark.line)
            )

    return spans, device_lines


def resolve_focus(text: str, line: int) -> FocusResolution:
    """YAML 本文とカーソル行（1 始まり）から注目ノードを解決する。

    - device ブロック内 → その device 1 台。
    - physical-connection ブロック内 → 両端の device 2 台。
    - どのブロックにも含まれない場合 → 直前（カーソルより上）の最も近い
      ブロックにフォールバックする。該当なしなら空の結果。
    """
    spans, device_lines = _parse_spans(text)
    if not spans:
        return FocusResolution([], "none", device_lines)

    cursor0 = line - 1

    # 1) カーソルを含む最小のブロックを優先する
    containing = [s for s in spans if s.start <= cursor0 < s.end]
    if containing:
        best = min(containing, key=lambda s: s.end - s.start)
        return FocusResolution(list(best.focus_ids), best.context, device_lines)

    # 2) フォールバック: カーソルより上で最も近いブロック（開始行が最大のもの）
    above = [s for s in spans if s.start <= cursor0]
    if above:
        best = max(above, key=lambda s: s.start)
        return FocusResolution(list(best.focus_ids), best.context, device_lines)

    return FocusResolution([], "none", device_lines)


def symbol_lines(text: str) -> dict[str, int]:
    """device-id / connection-id / subnet-id を定義行（1 始まり）へ対応づける。

    design lint（validator）の各 issue が持つ ``targets`` を YAML 上の行へ
    マップし、エディタの diagnostics（波線）を出すために使う。
    解析に失敗した場合（編集途中の不正 YAML 等）は空を返す。
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return {}
    if root is None:
        return {}

    lines: dict[str, int] = {}

    def _record(node: object, key: str) -> None:
        """MappingNode の指定キーの scalar 値を、その定義行に対応づける。"""
        v = _get(node, key)
        if isinstance(v, yaml.ScalarNode) and v.value:
            lines.setdefault(v.value, v.start_mark.line + 1)

    nm = _get(root, "network-model")
    if nm is None:
        return lines

    phys = _get(nm, "physical-layer")
    if phys is not None:
        dev_seq = _get(phys, "device")
        if isinstance(dev_seq, yaml.SequenceNode):
            for item in dev_seq.value:
                _record(item, "device-id")
        conn_seq = _get(phys, "physical-connection")
        if isinstance(conn_seq, yaml.SequenceNode):
            for item in conn_seq.value:
                _record(item, "connection-id")

    l3 = _get(nm, "layer3-layer")
    if l3 is not None:
        sn_seq = _get(l3, "ip-subnet")
        if isinstance(sn_seq, yaml.SequenceNode):
            for item in sn_seq.value:
                _record(item, "subnet-id")
                _record(item, "prefix")

    return lines
