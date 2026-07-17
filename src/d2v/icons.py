"""ノードのデバイスアイコン（絵文字の代替）。

単一のジオメトリ定義（``_GEOM``）から、ベクター（SVG）とラスタ（PNG）の
両方を生成する。図中のノードには HTML 風ラベルの ``<IMG>`` としてアイコンを
埋め込む。

なぜ両形式が必要か:
    このリポジトリが対象とする Graphviz（2.4x 系）の PNG 出力（cairo）には
    SVG 画像を読み込むプラグイン（rsvg）が無いため、PNG 出力では SVG アイコンを
    ラスタライズできない。そこで PNG 出力用にはラスタの PNG アイコンを用い、
    SVG 出力では :func:`inline_svg_icons` でベクターの SVG をインライン埋め込みして
    自己完結（外部ファイル参照なし）にする。

DOT 内でのアイコン参照は常に ``<type>.png``（フォーマット非依存）とし、
:func:`imagepath_line` が指すアセットディレクトリから解決する。SVG 出力時は
レンダラが ``<image>`` 要素をベクター SVG に置換する。
"""

from __future__ import annotations

import base64
import re
from html import escape
from pathlib import Path

# アイコン資産の配置先（.svg / .png をコミットしておく）
ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "icons"

# アイコンの論理座標系（正方 96x96・左上原点）
CANVAS = 96

# device-type → アイコン種別（DeviceType の全値をカバーし、未知は "unknown"）
DEVICE_TYPES = (
    "router",
    "switch",
    "firewall",
    "server",
    "host",
    "load-balancer",
    "unknown",
)

# 種別ごとの基調色（ラベルの淡色ボックス上で映える濃色）
_COLOR: dict[str, str] = {
    "router": "#1A73E8",        # 青
    "switch": "#12A150",        # 緑
    "firewall": "#EA4335",      # 赤
    "server": "#5F6368",        # グレー
    "host": "#9334E6",          # 紫
    "load-balancer": "#E37400",  # 橙
    "unknown": "#9AA0A6",       # 淡グレー
}


# ---------------------------------------------------------------------------
# ジオメトリ定義
# 各プリミティブは dict:
#   {"t": "rrect", "xy": (x0, y0, x1, y1), "r": 半径, "fill": 塗り, "stroke": 線, "w": 線幅}
#   {"t": "line",  "xy": (x0, y0, x1, y1), "w": 線幅}
#   {"t": "poly",  "pts": [(x, y), ...], "fill": 塗り}
#   {"t": "circle","xy": (cx, cy), "r": 半径, "fill": 塗り, "stroke": 線, "w": 線幅}
# 色に None を渡すと種別の基調色を用いる。"white" はそのまま白。
# ---------------------------------------------------------------------------
def _geom(t: str) -> list[dict]:
    """種別 t のアイコンを構成するプリミティブ列を返す。"""
    if t == "router":
        # 円形の筐体 + 左右双方向の矢印（ルーティング）
        return [
            {"t": "circle", "xy": (48, 48), "r": 28, "fill": None},
            {"t": "line", "xy": (32, 48, 64, 48), "w": 5},
            {"t": "poly", "pts": [(32, 41), (32, 55), (22, 48)], "fill": "base"},
            {"t": "poly", "pts": [(64, 41), (64, 55), (74, 48)], "fill": "base"},
        ]
    if t == "switch":
        # 端末筐体 + 4 つのポート（スイッチング）
        prims: list[dict] = [
            {"t": "rrect", "xy": (10, 40, 86, 66), "r": 6, "fill": None},
        ]
        for i in range(4):
            x = 20 + i * 15
            prims.append(
                {"t": "rrect", "xy": (x, 49, x + 9, 57), "r": 1, "fill": "base"}
            )
        return prims
    if t == "firewall":
        # レンガ壁（外枠 + 横目地 + 千鳥の縦目地）
        prims = [{"t": "rrect", "xy": (16, 18, 80, 78), "r": 4, "fill": None}]
        for y in (38, 58):
            prims.append({"t": "line", "xy": (16, y, 80, y), "w": 4})
        # 上段・下段の縦目地は左右対称、中段は千鳥
        prims.append({"t": "line", "xy": (48, 18, 48, 38), "w": 4})
        prims.append({"t": "line", "xy": (32, 38, 32, 58), "w": 4})
        prims.append({"t": "line", "xy": (64, 38, 64, 58), "w": 4})
        prims.append({"t": "line", "xy": (48, 58, 48, 78), "w": 4})
        return prims
    if t == "server":
        # ラック筐体 + 3 スロット + 各スロットの LED
        prims = [{"t": "rrect", "xy": (26, 14, 70, 82), "r": 5, "fill": None}]
        for i in range(3):
            y = 28 + i * 20
            prims.append({"t": "line", "xy": (26, y + 8, 70, y + 8), "w": 3})
            prims.append({"t": "circle", "xy": (34, y, ), "r": 3, "fill": None})
        return prims
    if t == "host":
        # モニタ画面 + スタンド + 台座（PC 端末）
        return [
            {"t": "rrect", "xy": (14, 20, 82, 60), "r": 4, "fill": None},
            {"t": "line", "xy": (48, 60, 48, 72), "w": 6},
            {"t": "line", "xy": (32, 74, 64, 74), "w": 6},
        ]
    if t == "load-balancer":
        # 1 → 3 の分散ツリー（ロードバランサ）
        return [
            {"t": "circle", "xy": (48, 22), "r": 9, "fill": None},
            {"t": "line", "xy": (48, 31, 48, 44), "w": 4},
            {"t": "line", "xy": (24, 60, 72, 60), "w": 4},
            {"t": "line", "xy": (24, 52, 24, 60), "w": 4},
            {"t": "line", "xy": (48, 44, 48, 60), "w": 4},
            {"t": "line", "xy": (72, 52, 72, 60), "w": 4},
            {"t": "circle", "xy": (24, 70), "r": 8, "fill": None},
            {"t": "circle", "xy": (48, 70), "r": 8, "fill": None},
            {"t": "circle", "xy": (72, 70), "r": 8, "fill": None},
        ]
    # unknown: 角丸ボックス + 中央ドット
    return [
        {"t": "rrect", "xy": (22, 22, 74, 74), "r": 10, "fill": None},
        {"t": "circle", "xy": (48, 48), "r": 6, "fill": None},
    ]


def _col(value: object, base: str) -> str:
    """None は基調色、それ以外はそのまま返す（線色用）。"""
    return base if value is None else str(value)


def _fill(value: object, base: str) -> str:
    """塗り色を解決する。None→"none"（塗りなし）, "base"→基調色, それ以外はそのまま。"""
    if value is None:
        return "none"
    if value == "base":
        return base
    return str(value)


# ---------------------------------------------------------------------------
# SVG 生成
# ---------------------------------------------------------------------------
def render_svg_inner(device_type: str) -> str:
    """アウター ``<svg>`` を除いた内側要素だけを返す（インライン埋め込み用）。"""
    t = device_type if device_type in _COLOR else "unknown"
    base = _COLOR[t]
    parts: list[str] = []
    for p in _geom(t):
        kind = p["t"]
        stroke = _col(p.get("stroke", base), base)
        if kind == "rrect":
            x0, y0, x1, y1 = p["xy"]
            fill_attr = _fill(p.get("fill"), base)
            parts.append(
                f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" '
                f'rx="{p["r"]}" ry="{p["r"]}" fill="{fill_attr}" '
                f'stroke="{stroke}" stroke-width="{p.get("w", 4)}"/>'
            )
        elif kind == "line":
            x0, y0, x1, y1 = p["xy"]
            parts.append(
                f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" '
                f'stroke="{stroke}" stroke-width="{p.get("w", 4)}" '
                'stroke-linecap="round"/>'
            )
        elif kind == "poly":
            pts = " ".join(f"{x},{y}" for x, y in p["pts"])
            parts.append(f'<polygon points="{pts}" fill="{_fill(p.get("fill"), base)}"/>')
        elif kind == "circle":
            cx, cy = p["xy"]
            fill_attr = _fill(p.get("fill"), base)
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{p["r"]}" fill="{fill_attr}" '
                f'stroke="{stroke}" stroke-width="{p.get("w", 4)}"/>'
            )
    return "".join(parts)


def render_svg(device_type: str) -> str:
    """完全な SVG 文書（96x96）を返す。"""
    inner = render_svg_inner(device_type)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS}" '
        f'height="{CANVAS}" viewBox="0 0 {CANVAS} {CANVAS}">'
        f"{inner}</svg>\n"
    )


# ---------------------------------------------------------------------------
# PNG 生成（Pillow）
# ---------------------------------------------------------------------------
def render_png(device_type: str, size: int = 384) -> "Image.Image":  # noqa: F821
    """透過 PNG の Pillow Image を返す（ラスタ・高解像度）。"""
    from PIL import Image, ImageDraw

    t = device_type if device_type in _COLOR else "unknown"
    base = _COLOR[t]
    scale = size / CANVAS
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def s(v: float) -> float:
        return v * scale

    for p in _geom(t):
        kind = p["t"]
        stroke = _col(p.get("stroke", base), base)
        w = int(round(s(p.get("w", 4))))
        if kind == "rrect":
            x0, y0, x1, y1 = p["xy"]
            f = _fill(p.get("fill"), base)
            draw.rounded_rectangle(
                [s(x0), s(y0), s(x1), s(y1)],
                radius=s(p["r"]),
                fill=(None if f == "none" else f),
                outline=stroke,
                width=w,
            )
        elif kind == "line":
            x0, y0, x1, y1 = p["xy"]
            draw.line([s(x0), s(y0), s(x1), s(y1)], fill=stroke, width=w)
            # 丸キャップを端点の円で近似
            r = w / 2
            for cx, cy in ((s(x0), s(y0)), (s(x1), s(y1))):
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=stroke)
        elif kind == "poly":
            draw.polygon([(s(x), s(y)) for x, y in p["pts"]], fill=_fill(p.get("fill"), base))
        elif kind == "circle":
            cx, cy = p["xy"]
            r = p["r"]
            f = _fill(p.get("fill"), base)
            draw.ellipse(
                [s(cx - r), s(cy - r), s(cx + r), s(cy + r)],
                fill=(None if f == "none" else f),
                outline=stroke,
                width=w,
            )
    return img


def write_assets(directory: Path | None = None) -> Path:
    """全種別の ``.svg`` と ``.png`` を生成して書き出す（開発時の再生成用）。"""
    out = Path(directory) if directory is not None else ASSETS_DIR
    out.mkdir(parents=True, exist_ok=True)
    for t in DEVICE_TYPES:
        (out / f"{t}.svg").write_text(render_svg(t), encoding="utf-8")
        render_png(t).save(out / f"{t}.png")
    return out


# ---------------------------------------------------------------------------
# DOT / ラベル生成ヘルパ
# ---------------------------------------------------------------------------
def icon_type(device_type: object) -> str:
    """任意の device-type を既知のアイコン種別へ正規化する。"""
    t = str(device_type or "").strip().lower()
    return t if t in _COLOR else "unknown"


def icon_filename(device_type: object) -> str:
    """DOT の ``<IMG SRC=...>`` に使うファイル名（常に PNG・フォーマット非依存）。"""
    return f"{icon_type(device_type)}.png"


def imagepath_line() -> str:
    """DOT グラフ属性 ``imagepath`` の 1 行（末尾セミコロン付き）。"""
    return f'    imagepath="{ASSETS_DIR}";'


def html_label(
    device_type: object,
    lines: list[str],
    *,
    icon_px: int = 26,
    small_from: int = 1,
) -> str:
    """アイコン付き HTML 風ラベル（``label=<...>`` に渡す ``<...>`` 全体）を返す。

    Args:
        device_type: デバイス種別（アイコン選択に使用）。
        lines: 表示するテキスト行（先頭行は主ラベル、以降は補助情報）。
        icon_px: アイコンの表示サイズ（px）。
        small_from: このインデックス以降の行を小さめフォントで表示する。

    Returns:
        ``<...>`` を含む HTML 風ラベル文字列。
    """
    src = icon_filename(device_type)
    rows = [
        f'<TR><TD FIXEDSIZE="TRUE" WIDTH="{icon_px}" HEIGHT="{icon_px}">'
        f'<IMG SRC="{src}" SCALE="TRUE"/></TD></TR>'
    ]
    for i, text in enumerate(lines):
        cell = escape(str(text), quote=True)
        if i >= small_from:
            cell = f'<FONT POINT-SIZE="8">{cell}</FONT>'
        rows.append(f"<TR><TD>{cell}</TD></TR>")
    return (
        '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1">'
        + "".join(rows)
        + "</TABLE>>"
    )


# ---------------------------------------------------------------------------
# レンダラ用ポストプロセス
# ---------------------------------------------------------------------------
# Graphviz が出力した SVG 内の <image ... xlink:href="TYPE.png" .../> にマッチ
_IMAGE_RE = re.compile(
    r'<image\b([^>]*?)xlink:href="([^"]+?\.png)"([^>]*?)/>',
    re.IGNORECASE,
)
_ATTR_RE = re.compile(r'(\w[\w:-]*)\s*=\s*"([^"]*)"')


def inline_svg_icons(svg_text: str) -> str:
    """SVG 出力中の ``<image href="TYPE.png">`` をベクター SVG に置換する。

    外部ファイル参照を排し、アイコンをベクターで自己完結させる。既知の種別名に
    一致しない画像参照はそのまま残す。
    """

    def _repl(m: re.Match[str]) -> str:
        attrs = dict(_ATTR_RE.findall(m.group(1) + " " + m.group(3)))
        href = m.group(2)
        stem = Path(href).stem
        if stem not in _COLOR:
            return m.group(0)
        x = attrs.get("x", "0")
        y = attrs.get("y", "0")
        w = attrs.get("width", str(CANVAS)).rstrip("px")
        h = attrs.get("height", str(CANVAS)).rstrip("px")
        inner = render_svg_inner(stem)
        # ネストした <svg> と viewBox で 96x96 → 指定ボックスへ自動スケール
        return (
            f'<svg x="{x}" y="{y}" width="{w}" height="{h}" '
            f'viewBox="0 0 {CANVAS} {CANVAS}" '
            f'preserveAspectRatio="xMidYMid meet">{inner}</svg>'
        )

    return _IMAGE_RE.sub(_repl, svg_text)


# ---------------------------------------------------------------------------
# LLM 生成 DOT へのアイコン注入
# ---------------------------------------------------------------------------
# ノード文中の d2vtype="TYPE"（アイコン用の機械可読属性）
_D2VTYPE_RE = re.compile(r'\s*,?\s*d2vtype\s*=\s*"([^"]*)"')
# label="..."（エスケープされた \" と \n を含みうる）
_LABEL_RE = re.compile(r'label\s*=\s*"((?:\\.|[^"\\])*)"')


def _label_to_lines(raw: str) -> list[str]:
    r"""DOT のラベル文字列（``\n`` 区切り・``\"`` エスケープ）を行リストへ。"""
    # \n（改行指定）で分割してから、その他のエスケープを戻す
    parts = re.split(r"\\n", raw)
    out: list[str] = []
    for part in parts:
        text = part.replace('\\"', '"').replace("\\\\", "\\").strip()
        if text:
            out.append(text)
    return out or [raw]


def inject_icons_into_dot(dot_code: str) -> str:
    """LLM 生成 DOT に含まれる ``d2vtype="..."`` 付きノードへアイコンを注入する。

    LLM には（HTML ラベルを直接書かせるのではなく）各ノードへ機械可読な
    ``d2vtype="router"`` 属性と通常の ``label="..."`` を付けさせる。本関数がそれを
    決定論的にアイコン付き HTML ラベルへ変換するため、DOT の妥当性を損ねにくい。

    - ``d2vtype`` を持たないノードは変更しない。
    - 変換に失敗した場合は元のノード文をそのまま残す（グレースフル）。
    """
    if "d2vtype" not in dot_code:
        return dot_code

    def _convert_stmt(stmt: str) -> str:
        mt = _D2VTYPE_RE.search(stmt)
        if not mt:
            return stmt
        dtype = mt.group(1)
        # d2vtype 属性を除去
        stmt2 = _D2VTYPE_RE.sub("", stmt, count=1)
        ml = _LABEL_RE.search(stmt2)
        if not ml:
            return stmt2
        lines = _label_to_lines(ml.group(1))
        html = html_label(dtype, lines)
        return stmt2[: ml.start()] + f"label={html}" + stmt2[ml.end():]

    # 属性リスト [ ... ] を持つ文を対象に変換する
    out: list[str] = []
    i = 0
    n = len(dot_code)
    while i < n:
        lb = dot_code.find("[", i)
        if lb == -1:
            out.append(dot_code[i:])
            break
        rb = _matching_bracket(dot_code, lb)
        if rb == -1:
            out.append(dot_code[i:])
            break
        # 文の開始（直前の ; か { か改行）からブラケット閉じまでを 1 文とみなす
        start = max(
            dot_code.rfind(";", i, lb),
            dot_code.rfind("{", i, lb),
            dot_code.rfind("}", i, lb),
        ) + 1
        out.append(dot_code[i:start])
        stmt = dot_code[start : rb + 1]
        out.append(_convert_stmt(stmt))
        i = rb + 1
    return "".join(out)


def _matching_bracket(s: str, open_idx: int) -> int:
    """``s[open_idx]`` の ``[`` に対応する ``]`` の位置を返す（文字列内は無視）。"""
    depth = 0
    in_str = False
    esc = False
    for j in range(open_idx, len(s)):
        c = s[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return j
    return -1
