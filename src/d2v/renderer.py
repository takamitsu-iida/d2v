"""Graphviz DOT コードを PNG / SVG にレンダリングする。"""

from __future__ import annotations

import math
import re
from pathlib import Path

import graphviz

from d2v import icons, zonelayout
from d2v.config import settings
from d2v.errors import GraphvizNotFoundError


class RenderError(Exception):
    """DOT コードのレンダリングに失敗したことを示す回復可能な例外。

    DOT の構文エラーなど、LLM の再生成で修正しうる失敗を表す。
    保存済みの .dot ファイルパスと Graphviz からのエラーメッセージを保持する。
    """

    def __init__(self, message: str, dot_path: Path):
        super().__init__(message)
        self.dot_path = dot_path


# bgcolor="#RRGGBB" / bgcolor="#RRGGBBAA" にマッチする正規表現
# （引用符の有無・大文字小文字を許容し、既存のアルファ値も上書きできるようにする）
_BGCOLOR_RE = re.compile(
    r'(bgcolor\s*=\s*")#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?(")',
    re.IGNORECASE,
)

# cluster レベルの `style="filled";` 文にマッチする正規表現。
# これがあると Graphviz は cluster を `bgcolor` ではなく枠線色 `color` で塗りつぶし、
# 背景が濃色になってしまう。ノードの `style="filled,rounded"`（`[...]` 内・カンマ区切り・
# セミコロン終端なし）は値が異なりマッチしないため、cluster 背景のみを無効化できる。
_CLUSTER_FILLED_RE = re.compile(r'style\s*=\s*"?filled"?\s*;', re.IGNORECASE)

# グラフ宣言の開き波括弧（例: `digraph G {`）にマッチする正規表現
_GRAPH_OPEN_RE = re.compile(r"(strict\s+)?(di)?graph\b[^{]*\{", re.IGNORECASE)


def inject_imagepath(dot_code: str) -> str:
    """ノードアイコン（`<IMG SRC="TYPE.png">`）の探索先を DOT に注入する。

    グラフ宣言直後に `imagepath="<アセットディレクトリ>";` を挿入し、DOT 内の
    アイコン参照をファイル名だけで解決できるようにする。既に `imagepath` が
    指定済みの場合は二重挿入を避けて元のコードを返す。
    """
    if re.search(r"\bimagepath\s*=", dot_code, re.IGNORECASE):
        return dot_code
    m = _GRAPH_OPEN_RE.search(dot_code)
    if not m:
        return dot_code
    at = m.end()
    return dot_code[:at] + "\n" + icons.imagepath_line() + dot_code[at:]


def remove_edge_arrows(dot_code: str) -> str:
    """全エッジの矢じりを消す（物理リンクは双方向で向きを持たないため）。

    グラフ宣言直後にエッジのデフォルト属性 `edge [dir=none];` を挿入する。
    Graphviz の属性デフォルトは以降に定義されるエッジへ累積適用され、後続の
    `edge [...]`（色・太さ等）は `dir` を上書きしないため、全リンクが矢じりなしの
    線として描画される。既に `dir=none` が指定済みでも二重指定は無害。

    Args:
        dot_code: Graphviz DOT 形式のコード

    Returns:
        エッジのデフォルトを矢じりなしにした DOT コード
    """
    m = _GRAPH_OPEN_RE.search(dot_code)
    if not m:
        return dot_code
    insert_at = m.end()
    return (
        dot_code[:insert_at]
        + "\n    edge [dir=none];  // 物理リンクは無向（矢じりなし）"
        + dot_code[insert_at:]
    )


def inject_render_quality(dot_code: str, *, dpi: int = 0, pad: float = 0.0) -> str:
    """レンダリング品質を上げるグラフ属性（``dpi`` / ``pad``）を注入する。

    グラフ宣言直後に ``graph [dpi=..., pad=...]`` を挿入する。``dpi`` は PNG などの
    ラスタ出力の解像度を上げてアイコン・文字のジャギーを抑える（SVG では無視される）。
    ``pad`` は描画領域の外周に余白（インチ）を足し、図の端が切れて見える窮屈さを
    緩和する。既に同じ属性が指定済みの場合は二重指定を避ける。

    Args:
        dot_code: Graphviz DOT 形式のコード
        dpi: ラスタ出力の解像度。0 以下で無効。
        pad: 外周余白（インチ）。0 以下で無効。

    Returns:
        品質向上属性を注入した DOT コード
    """
    attrs: list[str] = []
    if dpi > 0 and not re.search(r"\bdpi\s*=", dot_code, re.IGNORECASE):
        attrs.append(f"dpi={int(dpi)}")
    if pad > 0 and not re.search(r"\bpad\s*=", dot_code, re.IGNORECASE):
        attrs.append(f'pad="{pad}"')
    if not attrs:
        return dot_code
    m = _GRAPH_OPEN_RE.search(dot_code)
    if not m:
        return dot_code
    at = m.end()
    return dot_code[:at] + f"\n    graph [{', '.join(attrs)}];" + dot_code[at:]


def emphasize_node_borders(dot_code: str, penwidth: float = 1.6) -> str:
    """全ノードの枠線を少し太くして立体感（縁取り）を強める。

    グラフ宣言直後にノードのデフォルト属性 ``node [penwidth=...]`` を挿入する。
    Graphviz のデフォルトは以降に定義されるノードへ累積適用され、DOT 内の後続の
    ``node [...]``（フォント・形状等）は ``penwidth`` を上書きしないため、淡色で
    塗られたノードの縁取りが明確になり、フラットすぎる箱がくっきり締まって見える。
    個別ノードが独自に ``penwidth`` を指定していればそちらが優先される。

    Args:
        dot_code: Graphviz DOT 形式のコード
        penwidth: ノード枠線の太さ。0 以下で無効。

    Returns:
        ノード枠線を強調した DOT コード
    """
    if penwidth <= 0:
        return dot_code
    m = _GRAPH_OPEN_RE.search(dot_code)
    if not m:
        return dot_code
    at = m.end()
    return dot_code[:at] + f"\n    node [penwidth={penwidth}];" + dot_code[at:]


# d2vzone 属性（cluster がどのゾーンかを示す）にマッチする正規表現
_D2VZONE_RE = re.compile(r'd2vzone\s*=\s*"?([^"\s;]+)"?', re.IGNORECASE)

# 2段階レイアウト制約を注入済みかを判定するマーカー
_ZONE_CONSTRAINT_MARKER = "zone tier constraints"


class _ZoneScan:
    """DOT 走査で得たゾーン構造（cluster・ノード所属・ゾーン間接続）。"""

    def __init__(self) -> None:
        # cluster ごとの {"zone": str, "close": int(閉じ } の位置)}
        self.clusters: list[dict] = []
        # ノード名 → 所属ゾーン名
        self.node_zone: dict[str, str] = {}
        # ゾーン間接続のゾーン対（順不同、重複可）
        self.pairs: list[tuple[str, str]] = []
        # ルートグラフの閉じ } の位置（制約行の挿入先）
        self.root_close: int = -1


def _strip_token(token: str) -> str:
    """ノード参照トークンを正規化する（ポート ``:x`` 除去・両端クォート除去）。"""
    token = token.strip().split(":", 1)[0].strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        token = token[1:-1]
    return token


def _scan_zone_dot(dot_code: str) -> _ZoneScan:
    """DOT を走査して cluster の d2vzone・ノード所属・ゾーン間接続を収集する。

    文字列・行コメント（``//`` / ``#``）・ブロックコメント（``/* */``）を無視し、
    ``{`` / ``}`` / ``;`` で区切ったステートメント単位に解析する。
    """
    scan = _ZoneScan()
    stack: list[dict] = []  # フレーム: {"zone": str, "subgraph": bool}
    node_frames: list[tuple[str, list[dict]]] = []  # (ノード名, その時点のstack写し)

    n = len(dot_code)
    i = 0
    buf: list[str] = []

    def cur_zone() -> str:
        for fr in reversed(stack):
            if fr["zone"]:
                return fr["zone"]
        return ""

    def flush() -> None:
        stmt = "".join(buf).strip()
        buf.clear()
        if not stmt:
            return
        core = stmt.split("[", 1)[0]
        # d2vzone 属性 → 現在の（最も内側の）cluster フレームに設定
        mz = _D2VZONE_RE.search(stmt)
        if mz and "=" in core and "->" not in core and "--" not in core:
            if stack:
                stack[-1]["zone"] = mz.group(1)
            return
        # エッジ（-> / --）→ ゾーン間接続を収集
        if "->" in core or "--" in core:
            parts = re.split(r"->|--", core)
            endpoints = [_strip_token(p) for p in parts if _strip_token(p)]
            for a, b in zip(endpoints, endpoints[1:]):
                scan.pairs.append((a, b))
            return
        # ノード宣言（属性リスト前に = を含まない）→ 所属ゾーンを記録
        if "=" in core:
            return
        tokens = core.split()
        if not tokens:
            return
        name = _strip_token(tokens[0])
        if not name or name.lower() in _DOT_KEYWORDS:
            return
        node_frames.append((name, list(stack)))

    while i < n:
        c = dot_code[i]
        # 文字列リテラル
        if c in "\"'":
            buf.append(c)
            i += 1
            while i < n:
                buf.append(dot_code[i])
                if dot_code[i] == c and dot_code[i - 1] != "\\":
                    i += 1
                    break
                i += 1
            continue
        # コメント
        if c == "/" and i + 1 < n and dot_code[i + 1] == "/":
            while i < n and dot_code[i] != "\n":
                i += 1
            continue
        if c == "#":
            while i < n and dot_code[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and dot_code[i + 1] == "*":
            i += 2
            while i + 1 < n and not (dot_code[i] == "*" and dot_code[i + 1] == "/"):
                i += 1
            i += 2
            continue
        # 構造文字
        if c == "{":
            header = "".join(buf)
            buf.clear()
            stack.append({"zone": "", "subgraph": "subgraph" in header.lower()})
            i += 1
            continue
        if c == "}":
            flush()
            frame = stack.pop() if stack else {"zone": "", "subgraph": False}
            if frame.get("zone"):
                scan.clusters.append({"zone": frame["zone"], "close": i})
            if not stack:
                scan.root_close = i
            i += 1
            continue
        if c == ";":
            flush()
            i += 1
            continue
        buf.append(c)
        i += 1

    # ノード → ゾーン（最も内側の非空ゾーンを採用）を解決
    for name, frames in node_frames:
        zone = ""
        for fr in reversed(frames):
            if fr["zone"]:
                zone = fr["zone"]
                break
        if zone:
            scan.node_zone[name] = zone
    return scan


# DOT のキーワード（ノード宣言と誤認しないよう除外する）
_DOT_KEYWORDS = {
    "node", "edge", "graph", "subgraph", "digraph", "strict", "rank",
}


def inject_zone_constraints(dot_code: str) -> str:
    """LLM 生成 DOT に 2 段階レイアウトのゾーン制約を後処理注入する。

    各 cluster の ``d2vzone="<zone>"`` 属性でゾーンを識別し、cluster 内ノードと
    エッジからゾーン間接続を集約して段配置（:mod:`d2v.zonelayout`）を求める。その後、
    各 cluster 内へ不可視アンカーを、グラフ最上位へ段の ``rank``・順序制約を挿入する。

    ``d2vzone`` を持つ cluster が無い / ゾーンが 2 個未満 / ゾーン間接続が無い / 既に
    注入済み等の場合は、元のコードをそのまま返す（後方互換のフォールバック）。
    """
    if _ZONE_CONSTRAINT_MARKER in dot_code or "d2vzone" not in dot_code:
        return dot_code

    scan = _scan_zone_dot(dot_code)
    if len({c["zone"] for c in scan.clusters}) < 2:
        return dot_code

    zone_pairs = [
        (scan.node_zone.get(a, ""), scan.node_zone.get(b, ""))
        for a, b in scan.pairs
    ]
    placement = zonelayout.compute_zone_placement_from_pairs(zone_pairs)
    if placement.is_empty():
        return dot_code

    # 挿入は文字位置がずれないよう、末尾（大きい位置）から順に適用する。
    inserts: list[tuple[int, str]] = []

    # 各 cluster 内へ不可視アンカーを挿入（配置対象ゾーンのみ）。
    for cluster in scan.clusters:
        zone = cluster["zone"]
        if zone in placement.tier_of:
            inserts.append(
                (cluster["close"], f"    {zonelayout.anchor_decl(zone)}\n")
            )

    # グラフ最上位へ段の rank/順序制約を挿入。
    if scan.root_close >= 0:
        constraint = "\n".join(
            f"    {ln}" for ln in zonelayout.zone_constraint_dot(placement)
        )
        if constraint:
            inserts.append((scan.root_close, constraint + "\n"))

    if not inserts:
        return dot_code

    result = dot_code
    for pos, text in sorted(inserts, key=lambda t: t[0], reverse=True):
        result = result[:pos] + text + result[pos:]
    return result


# 凡例に載せるデバイス種別の並び順と日本語ラベル（README のデバイス表に準拠）。
_LEGEND_ORDER: list[str] = [
    "router",
    "switch",
    "firewall",
    "server",
    "host",
    "load-balancer",
]
_LEGEND_LABELS: dict[str, str] = {
    "router": "ルータ",
    "switch": "スイッチ",
    "firewall": "ファイアウォール",
    "server": "サーバ",
    "host": "ホスト",
    "load-balancer": "ロードバランサ",
}
# DOT 内の d2vtype="TYPE"（アイコン用の機械可読属性）を走査する正規表現
_D2VTYPE_SCAN_RE = re.compile(r'd2vtype\s*=\s*"([^"]*)"')


def legend_types(dot_code: str) -> list[str]:
    """DOT に登場するデバイス種別を凡例掲載順（``_LEGEND_ORDER``）で返す。

    DOT 内の ``d2vtype="..."`` を走査し、実際に登場する種別だけを既定順で抽出する。
    既に ``cluster_legend`` を含む DOT（例: diff の意味凡例）は独自の凡例を持つため
    対象外として空リストを返す。

    Args:
        dot_code: Graphviz DOT 形式のコード（アイコン注入前）

    Returns:
        凡例に載せるデバイス種別のリスト（対象がなければ空）
    """
    if "cluster_legend" in dot_code:
        return []
    present = set(_D2VTYPE_SCAN_RE.findall(dot_code))
    return [t for t in _LEGEND_ORDER if t in present]


def build_legend_dot(types: list[str]) -> str:
    """デバイス種別のアイコン凡例だけを描く独立した DOT を組み立てる。

    各種別のアイコンと名称を縦一列に並べた、単体で完結する ``digraph`` を返す。
    ノードには ``d2vtype`` を付けておくことで、後段の
    :func:`icons.inject_icons_into_dot` により本体図と同じアイコンが埋め込まれる。

    Args:
        types: 凡例に載せるデバイス種別（``_LEGEND_ORDER`` 順を想定）。

    Returns:
        凡例のみを描く Graphviz DOT 文字列。
    """
    node_lines: list[str] = []
    ids: list[str] = []
    for t in types:
        nid = f'"d2vlegend_{t}"'
        ids.append(nid)
        color = icons._COLOR.get(t, "#5F6368")
        label = _LEGEND_LABELS.get(t, t)
        node_lines.append(
            f'    {nid} [label="{label}", d2vtype="{t}", shape=box, '
            f'style="filled,rounded", fillcolor="#FFFFFF", color="{color}", '
            "penwidth=1.6];"
        )
    # 凡例ノードを縦一列に整列させる不可視エッジ
    stack = f'    {" -> ".join(ids)} [style=invis];' if len(ids) > 1 else ""
    return (
        "digraph legend {\n"
        '    label="凡例"; labelloc=t; fontsize=14;\n'
        '    fontname="Helvetica,Arial,sans-serif";\n'
        '    bgcolor="#FFFFFF";\n'
        "    nodesep=0.25; ranksep=0.25;\n"
        '    node [fontname="Helvetica,Arial,sans-serif", fontsize=10];\n'
        + "\n".join(node_lines)
        + ("\n" + stack if stack else "")
        + "\n}\n"
    )


def neutralize_cluster_fill(dot_code: str) -> str:
    """cluster の `style="filled";` を除去し、淡い `bgcolor` を優先させる。

    Graphviz では cluster に `style="filled"` があると `fillcolor`（未指定なら枠線色
    `color`）で塗りつぶされ、`bgcolor` に指定した淡いパステル色が無視されて背景が
    濃色（ドギツい色）になる。この文を取り除くことで `bgcolor` が背景として反映され、
    枠線は `color` で描かれるようになる。

    Args:
        dot_code: Graphviz DOT 形式のコード

    Returns:
        cluster 背景の塗りつぶし指定を除去した DOT コード
    """
    return _CLUSTER_FILLED_RE.sub("", dot_code)


def apply_zone_opacity(dot_code: str, opacity: float) -> str:
    """DOT コード中の `bgcolor`（ゾーン/cluster の背景色）に透過度を付与する。

    Graphviz は `#RRGGBBAA` 形式でアルファ値を解釈するため、6 桁の HEX 背景色に
    アルファチャンネルを追記して背景を淡くする。既に 8 桁（アルファ付き）の場合は
    アルファ値を上書きする。

    Args:
        dot_code: Graphviz DOT 形式のコード
        opacity: 不透明度 0.0（完全透明）〜 1.0（不透明）

    Returns:
        `bgcolor` にアルファ値を付与した DOT コード
    """
    # 1.0（不透明）は変換不要。範囲外は 0.0〜1.0 にクランプする。
    opacity = max(0.0, min(1.0, opacity))
    if opacity >= 1.0:
        return dot_code

    alpha = f"{round(opacity * 255):02X}"

    def _repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}#{m.group(2)}{alpha}{m.group(3)}"

    return _BGCOLOR_RE.sub(_repl, dot_code)


# グラフレベルの rankdir 指定にマッチする正規表現
_RANKDIR_RE = re.compile(r'rankdir\s*=\s*"?[A-Za-z]{2}"?', re.IGNORECASE)


def _set_rankdir(dot_code: str, direction: str) -> str:
    """DOT の rankdir を指定方向へ差し替える（無ければグラフ宣言直後に挿入）。"""
    if _RANKDIR_RE.search(dot_code):
        return _RANKDIR_RE.sub(f"rankdir={direction}", dot_code, count=1)
    m = _GRAPH_OPEN_RE.search(dot_code)
    if not m:
        return dot_code
    at = m.end()
    return dot_code[:at] + f"\n    rankdir={direction};" + dot_code[at:]


def _graph_dimensions(dot_code: str) -> tuple[float, float] | None:
    """Graphviz の plain 出力からグラフ全体の幅・高さ（インチ）を取得する。

    画像を生成せずにレイアウトのバウンディングボックスだけを得るため、
    ``dot -Tplain`` 相当の出力先頭行 ``graph <scale> <width> <height>`` を解析する。
    """
    try:
        out = graphviz.Source(dot_code).pipe(format="plain")
    except Exception:
        return None
    text = out.decode("utf-8", "ignore") if isinstance(out, bytes) else str(out)
    first = text.splitlines()[0] if text else ""
    parts = first.split()
    if len(parts) >= 4 and parts[0] == "graph":
        try:
            return float(parts[2]), float(parts[3])
        except ValueError:
            return None
    return None


def fit_aspect_ratio(
    dot_code: str,
    target_wh: float,
    tolerance: float | None = None,
) -> str:
    """図の縦横比を目標に近づける（横長すぎる図は rankdir=LR で縦積みにする）。

    実際にレイアウトの寸法を測り、幅／高さが目標比の ``tolerance`` 倍を超えて
    横長な場合のみ、``rankdir=LR``（左右方向）へ切り替えた方が目標比に近いかを
    比較して採用する。通常の図（目標比に近い縦長・正方形）はそのまま維持する。
    ``ratio`` による引き伸ばしと違い、レイアウトを組み替えるため余白の間延びや
    歪みが生じない。

    Args:
        dot_code: Graphviz DOT 形式のコード
        target_wh: 目標の幅／高さ比（例: 4:3 なら 4/3≈1.33）。0 以下で無効化。
        tolerance: この倍率までの横長は許容し、切り替えない。None のとき
            ``settings.diagram_aspect_tolerance`` を使う。

    Returns:
        必要に応じて rankdir を調整した DOT コード
    """
    if tolerance is None:
        tolerance = settings.diagram_aspect_tolerance
    if target_wh <= 0:
        return dot_code
    dims = _graph_dimensions(dot_code)
    if not dims:
        return dot_code
    w, h = dims
    if w <= 0 or h <= 0:
        return dot_code
    wh = w / h
    # 目標比の tolerance 倍以内に収まっていれば十分見やすいので変更しない
    if wh <= target_wh * tolerance:
        return dot_code

    # 横長すぎる → rankdir=LR で縦積みにした方が目標比に近いか比較する
    lr_code = _set_rankdir(dot_code, "LR")
    lr_dims = _graph_dimensions(lr_code)
    if not lr_dims or lr_dims[1] <= 0:
        return dot_code
    lr_wh = lr_dims[0] / lr_dims[1]
    err_current = abs(math.log(wh / target_wh))
    err_lr = abs(math.log(lr_wh / target_wh)) if lr_wh > 0 else float("inf")
    return lr_code if err_lr < err_current else dot_code


def render(
    dot_code: str,
    output_dir: Path,
    stem: str = "diagram",
    fmt: str = "png",
    zone_opacity: float = 0.4,
    show_legend: bool = True,
) -> Path:
    """DOT コードをレンダリングして画像ファイルを保存する。

    Args:
        dot_code: Graphviz DOT 形式のコード
        output_dir: 出力先ディレクトリ（存在しない場合は自動作成）
        stem: 出力ファイル名（拡張子なし）
        fmt: 出力フォーマット ("png" または "svg")
        zone_opacity: ゾーン（cluster）背景色の不透明度 0.0〜1.0。
            1.0 未満のとき `bgcolor` にアルファ値を付与して背景を淡くする。
        show_legend: デバイス種別のアイコン凡例を **別ファイル**
            （``<stem>_legend.<fmt>``）として出力するか。本体図には埋め込まない。

    Returns:
        生成した本体画像ファイルの Path
    """
    # 凡例掲載対象の種別は、アイコン注入で d2vtype が消える前に把握しておく
    types = legend_types(dot_code) if show_legend else []
    # 2段階レイアウト: d2vzone 付き cluster があればゾーン段制約を注入する（無ければ素通し）
    dot_code = inject_zone_constraints(dot_code)
    # cluster の style="filled" を除去して淡い bgcolor を優先させてから透過を付与する
    dot_code = neutralize_cluster_fill(dot_code)
    dot_code = remove_edge_arrows(dot_code)
    dot_code = apply_zone_opacity(dot_code, zone_opacity)
    # ノードの縁取りを強めてフラットな箱に立体感を与える
    dot_code = emphasize_node_borders(dot_code)
    # LLM 生成 DOT の d2vtype 属性付きノードへアイコンを注入し、探索先を設定する
    dot_code = icons.inject_icons_into_dot(dot_code)
    dot_code = inject_imagepath(dot_code)
    # PNG ラスタ出力の解像度を上げ、外周に余白を足して視認性を高める
    dot_code = inject_render_quality(
        dot_code,
        dpi=settings.diagram_dpi if fmt == "png" else 0,
        pad=0.4,
    )
    # 横長すぎる図は縦横比を目標（既定 4:3）に近づける
    dot_code = fit_aspect_ratio(dot_code, settings.diagram_aspect_ratio)
    output_dir.mkdir(parents=True, exist_ok=True)

    rendered_path = _rasterize(dot_code, output_dir, stem, fmt)

    # 凡例は本体図に埋め込まず、独立した画像ファイルとして出力する
    if types:
        try:
            render_legend(types, output_dir, f"{stem}_legend", fmt)
        except (RenderError, GraphvizNotFoundError):
            pass

    return rendered_path


def render_legend(
    types: list[str],
    output_dir: Path,
    stem: str = "legend",
    fmt: str = "png",
) -> Path:
    """デバイス種別のアイコン凡例だけを独立した画像ファイルとして出力する。

    Args:
        types: 凡例に載せるデバイス種別（``legend_types`` で得た並び順）。
        output_dir: 出力先ディレクトリ（存在しない場合は自動作成）。
        stem: 出力ファイル名（拡張子なし）。
        fmt: 出力フォーマット ("png" または "svg")。

    Returns:
        生成した凡例画像ファイルの Path。
    """
    dot_code = build_legend_dot(types)
    dot_code = icons.inject_icons_into_dot(dot_code)
    dot_code = inject_imagepath(dot_code)
    dot_code = inject_render_quality(
        dot_code,
        dpi=settings.diagram_dpi if fmt == "png" else 0,
        pad=0.2,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    return _rasterize(dot_code, output_dir, stem, fmt)


def _rasterize(dot_code: str, output_dir: Path, stem: str, fmt: str) -> Path:
    """加工済み DOT を Graphviz でレンダリングし、画像と .dot を保存する。

    Args:
        dot_code: すべての後処理を適用済みの DOT コード。
        output_dir: 出力先ディレクトリ（呼び出し側で作成済みを想定）。
        stem: 出力ファイル名（拡張子なし）。
        fmt: 出力フォーマット ("png" または "svg")。

    Returns:
        生成した画像ファイルの Path。
    """
    # DOT ソースも保存しておく（デバッグ・差分確認用）
    dot_path = output_dir / f"{stem}.dot"
    dot_path.write_text(dot_code, encoding="utf-8")

    try:
        src = graphviz.Source(dot_code, format=fmt)
        rendered = src.render(
            filename=stem,
            directory=str(output_dir),
            cleanup=True,  # 中間 .gv ファイルを削除
        )
    except graphviz.ExecutableNotFound as e:
        # Graphviz 未インストールは環境起因の回復不能エラー
        raise GraphvizNotFoundError(
            "Graphviz がインストールされていません。次を実行してください:\n"
            "  sudo apt install graphviz"
        ) from e
    except Exception as e:
        # DOT の構文エラー等は改善ループで回復しうるため例外を送出する
        raise RenderError(str(e), dot_path) from e

    rendered_path = Path(rendered)
    # SVG 出力ではアイコンをベクターでインライン埋め込みし、外部ファイル参照を
    # 排して自己完結（ブラウザ表示・移動に強い）させる。
    if fmt == "svg":
        try:
            svg_text = rendered_path.read_text(encoding="utf-8")
            rendered_path.write_text(
                icons.inline_svg_icons(svg_text), encoding="utf-8"
            )
        except OSError:
            pass

    return rendered_path
