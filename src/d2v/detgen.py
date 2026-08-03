"""決定論的（LLM 非依存）な YAML → Graphviz DOT ジェネレータ。

このモジュールは実験的な追加機能であり、**単体で完結**している。将来不要になった
場合は、このファイル（``src/d2v/detgen.py``）を削除し、``main.py`` の ``dot``
サブコマンド振り分けブロック（数行）を取り除くだけで、他機能に影響を与えずに
丸ごと撤去できる。

通常の d2v フロー（``parser`` → ``generator``(LLM) → ``evaluator`` → 改善ループ）
とは独立しており、LLM も評価ループも一切使わない。``TopologyModel`` から
Graphviz DOT を直接組み立て、``renderer.render`` で PNG / SVG にする。

決定論的なので、同じ入力からは常に同じ DOT が得られる（API キー不要・オフライン可）。
レイアウト品質は LLM 経路に劣るが、「LLM が使えない環境でも 1 枚描ける」ことを
目的とした最小実装である。
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from pathlib import Path
from typing import Any

from d2v import icons, renderer
from d2v.errors import D2VError
from d2v.parser import TopologyModel, load_model

_YamlDict = dict[str, Any]

# device-type（＝ icons.icon_type 正規化後）ごとのノード配色。
# prompts/diagram-system.md の「ノードカラー定義」に準拠した淡色パステル。
_NODE_STYLE: dict[str, tuple[str, str]] = {
    # icon_type: (fillcolor, color)
    "router": ("#E6F4EA", "#137333"),
    "switch": ("#E8F0FE", "#1A73E8"),
    "firewall": ("#FCE8E6", "#C5221F"),
    "server": ("#FFF8E1", "#E37400"),
    "host": ("#F3E5F5", "#7B1FA2"),
    "load-balancer": ("#FFF3E0", "#E37400"),
    "unknown": ("#F1F3F4", "#5F6368"),
}

# ゾーン cluster 用の淡いパステル配色（bgcolor, 枠線色, ラベル文字色）を巡回割当。
_ZONE_PALETTE: list[tuple[str, str, str]] = [
    ("#F8F9FA", "#5F6368", "#3C4043"),
    ("#E8F5E9", "#137333", "#0B5394"),
    ("#FBE9E7", "#C5221F", "#A50E0E"),
    ("#E3F2FD", "#1A73E8", "#174EA6"),
    ("#F3E5F5", "#7B1FA2", "#6A1B9A"),
    ("#FFFDE7", "#F57F17", "#B26A00"),
    ("#E0F7FA", "#00838F", "#006064"),
    ("#FCE4EC", "#AD1457", "#880E4F"),
]


def _quote(text: str) -> str:
    """DOT 用にダブルクォートで囲む。

    バックスラッシュ・ダブルクォートをエスケープし、実改行は DOT の改行
    エスケープ ``\\n`` へ変換する（ラベル内で改行させる）。
    """
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n")
    return f'"{escaped}"'


def _primary_ip(dev: _YamlDict) -> str:
    """ノードラベルに載せる代表 IP を返す（loopback 優先、無ければ最初の IP）。"""
    loopback = str(dev.get("loopback", "") or "")
    if loopback:
        return loopback.split("/")[0]
    for iface in dev.get("interface", []):
        ip = iface.get("ip-address", "")
        if ip:
            return str(ip).split("/")[0]
    return ""


def _node_label(dev: _YamlDict) -> str:
    """ノードラベル（ホスト名＋代表 IP を改行区切り）を組み立てる。"""
    did = str(dev.get("device-id", ""))
    ip = _primary_ip(dev)
    return f"{did}\n{ip}" if ip else did


def _interface_ip(dev: _YamlDict, interface_id: str) -> str:
    """デバイスの指定インターフェースの IP（プレフィックス付き）を返す。"""
    for iface in dev.get("interface", []):
        if iface.get("interface-id") == interface_id:
            return str(iface.get("ip-address", "") or "")
    return ""


def _segment_label(ip0: str, ip1: str) -> str:
    """接続セグメントのネットワークアドレス（例: 10.1.0.0/30）を推定する。"""
    for ip in (ip0, ip1):
        if not ip or "/" not in ip:
            continue
        try:
            net = ipaddress.ip_interface(ip).network
        except ValueError:
            continue
        return str(net)
    return ""


def _zone_style(index: int) -> tuple[str, str, str]:
    """ゾーン番号に対応するパステル配色を巡回で返す。"""
    return _ZONE_PALETTE[index % len(_ZONE_PALETTE)]


def _sanitize_cluster_id(zone: str, index: int) -> str:
    """cluster 名に使える識別子（英数字とアンダースコアのみ）を作る。"""
    safe = "".join(ch if ch.isalnum() else "_" for ch in zone)
    return f"cluster_{index}_{safe}" if safe else f"cluster_{index}"


def _node_stmt(dev: _YamlDict, indent: str = "    ") -> str:
    """1 ノードの DOT 文を生成する。"""
    did = str(dev.get("device-id", ""))
    dtype = icons.icon_type(dev.get("device-type"))
    fill, color = _NODE_STYLE.get(dtype, _NODE_STYLE["unknown"])
    attrs = [
        f"label={_quote(_node_label(dev))}",
        f'fillcolor="{fill}"',
        f'color="{color}"',
        'style="filled,rounded"',
    ]
    # unknown はアイコンなし（d2vtype を付けない）
    if dtype != "unknown":
        attrs.append(f'd2vtype="{dtype}"')
    return f"{indent}{_quote(did)} [{', '.join(attrs)}];"


def _edge_stmt(conn: _YamlDict, model: TopologyModel, indent: str = "    ") -> str | None:
    """1 物理接続の DOT 文を生成する（endpoint が 2 個でなければ None）。"""
    endpoints = conn.get("endpoint", [])
    if len(endpoints) != 2:
        return None
    ep0, ep1 = endpoints[0], endpoints[1]
    d0 = str(ep0.get("device-id", ""))
    i0 = str(ep0.get("interface-id", ""))
    d1 = str(ep1.get("device-id", ""))
    i1 = str(ep1.get("interface-id", ""))
    if not d0 or not d1:
        return None

    ip0 = _interface_ip(model.device_map.get(d0, {}), i0)
    ip1 = _interface_ip(model.device_map.get(d1, {}), i1)

    attrs = ["dir=none"]
    if i0:
        attrs.append(f"taillabel={_quote(i0)}")
    if i1:
        attrs.append(f"headlabel={_quote(i1)}")
    seg = _segment_label(ip0, ip1)
    if seg:
        attrs.append(f"label={_quote(seg)}")
    return f"{indent}{_quote(d0)} -> {_quote(d1)} [{', '.join(attrs)}];"


def generate_dot(model: TopologyModel, *, title: str = "") -> str:
    """``TopologyModel`` から Graphviz DOT コードを決定論的に生成する。

    ノードはゾーン（``zone``）ごとに ``subgraph cluster`` へまとめ、種別に応じた
    アイコン属性（``d2vtype``）と配色を付与する。物理接続はポート名（taillabel /
    headlabel）とセグメント（label）付きの無向エッジとして描画する。

    Args:
        model: パース済みトポロジ。
        title: 図タイトル（``label`` として上部に表示。空なら付けない）。

    Returns:
        Graphviz DOT 形式のコード文字列。
    """
    lines: list[str] = ["digraph G {"]
    lines.append("    compound=true;")
    lines.append("    newrank=true;")
    lines.append("    nodesep=0.6;")
    lines.append("    ranksep=1.0;")
    lines.append("    splines=true;")
    lines.append("    rankdir=TB;")
    lines.append('    fontname="Helvetica,Arial,sans-serif";')
    if title:
        lines.append(f"    labelloc=t; label={_quote(title)}; fontsize=16;")
    lines.append(
        '    node [fontname="Helvetica,Arial,sans-serif", fontsize=10, '
        'shape=box, style="filled,rounded", width=1.5];'
    )
    lines.append(
        '    edge [fontname="Helvetica,Arial,sans-serif", fontsize=8, '
        'color="#4A5568", penwidth=1.5, dir=none];'
    )
    lines.append("")

    # ── ゾーンごとに cluster を作ってノードを配置 ──────────────
    # ゾーンの出現順を保持（入力順で決定論的にする）。zone 未設定は "" にまとめる。
    zone_order: list[str] = []
    zone_devices: dict[str, list[_YamlDict]] = {}
    for dev in model.devices:
        zone = str(dev.get("zone", "") or "")
        if zone not in zone_devices:
            zone_devices[zone] = []
            zone_order.append(zone)
        zone_devices[zone].append(dev)

    for zi, zone in enumerate(zone_order):
        devs = zone_devices[zone]
        if zone:
            bg, border, fontcolor = _zone_style(zi)
            cid = _sanitize_cluster_id(zone, zi)
            lines.append(f"    subgraph {cid} {{")
            lines.append(f"        label={_quote(zone)};")
            lines.append(f'        bgcolor="{bg}";')
            lines.append(f'        color="{border}";')
            lines.append(f'        fontcolor="{fontcolor}";')
            for dev in devs:
                lines.append(_node_stmt(dev, indent="        "))
            lines.append("    }")
        else:
            # ゾーン未設定ノードは cluster に入れず直接置く
            for dev in devs:
                lines.append(_node_stmt(dev, indent="    "))
    lines.append("")

    # ── 物理接続をエッジとして描画 ──────────────────────────
    for conn in model.connections:
        stmt = _edge_stmt(conn, model)
        if stmt is not None:
            lines.append(stmt)

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI（`python main.py dot -i topology.yaml`）
# ---------------------------------------------------------------------------


def run_cli(argv: list[str]) -> None:
    """`dot` サブコマンドのエントリポイント（LLM 非依存で 1 枚描画）。"""
    ap = argparse.ArgumentParser(
        prog="d2v dot",
        description=(
            "LLM を使わず、iida-network-model YAML から決定論的に Graphviz DOT を "
            "生成して構成図（PNG / SVG）を出力します（評価・改善ループなし）。"
        ),
    )
    ap.add_argument(
        "--input", "-i", required=True, type=Path, metavar="TOPOLOGY_YAML",
        help="入力トポロジ YAML ファイルのパス",
    )
    ap.add_argument(
        "--output-dir", "-o", type=Path, default=Path("output"), metavar="DIR",
        help="出力ディレクトリ（デフォルト: output）",
    )
    ap.add_argument(
        "--format", "-f", choices=["png", "svg"], default="png",
        help="出力フォーマット（デフォルト: png）",
    )
    ap.add_argument(
        "--stem", type=str, default=None, metavar="NAME",
        help="出力ファイル名（拡張子なし。デフォルトは入力ファイル名）",
    )
    ap.add_argument(
        "--zone-opacity", type=float, default=0.4, metavar="0.0-1.0",
        help="ゾーン背景色の不透明度（デフォルト: 0.4）",
    )
    ap.add_argument(
        "--print-dot", action="store_true",
        help="生成した DOT コードを標準出力にも表示する",
    )
    args = ap.parse_args(argv)

    stem = args.stem or args.input.stem
    try:
        model = load_model(args.input)
        dot_code = generate_dot(model, title=stem)
        image_path = renderer.render(
            dot_code,
            output_dir=args.output_dir,
            stem=stem,
            fmt=args.format,
            zone_opacity=args.zone_opacity,
        )
    except D2VError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
    except renderer.RenderError as e:
        print(f"レンダリングに失敗しました: {e}", file=sys.stderr)
        print(f"  DOT を保存しました: {e.dot_path}", file=sys.stderr)
        sys.exit(1)

    if args.print_dot:
        print(dot_code)
    print(
        f"決定論的生成（LLM 非依存）が完了しました:\n"
        f"  ノード数 : {len(model.devices)}\n"
        f"  接続数   : {len(model.connections)}\n"
        f"  出力画像 : {image_path}"
    )
