"""Graphviz DOT コードを PNG / SVG にレンダリングする。"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import graphviz

from d2v.config import settings


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
    tolerance: float = 2.0,
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
        tolerance: この倍率までの横長は許容し、切り替えない。

    Returns:
        必要に応じて rankdir を調整した DOT コード
    """
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
) -> Path:
    """DOT コードをレンダリングして画像ファイルを保存する。

    Args:
        dot_code: Graphviz DOT 形式のコード
        output_dir: 出力先ディレクトリ（存在しない場合は自動作成）
        stem: 出力ファイル名（拡張子なし）
        fmt: 出力フォーマット ("png" または "svg")
        zone_opacity: ゾーン（cluster）背景色の不透明度 0.0〜1.0。
            1.0 未満のとき `bgcolor` にアルファ値を付与して背景を淡くする。

    Returns:
        生成した画像ファイルの Path
    """
    # cluster の style="filled" を除去して淡い bgcolor を優先させてから透過を付与する
    dot_code = neutralize_cluster_fill(dot_code)
    dot_code = remove_edge_arrows(dot_code)
    dot_code = apply_zone_opacity(dot_code, zone_opacity)
    # 横長すぎる図は縦横比を目標（既定 4:3）に近づける
    dot_code = fit_aspect_ratio(dot_code, settings.diagram_aspect_ratio)
    output_dir.mkdir(parents=True, exist_ok=True)

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
    except graphviz.ExecutableNotFound:
        # Graphviz 未インストールは環境起因の回復不能エラー → 即終了
        print(
            "\n[エラー] Graphviz がインストールされていません。\n"
            "  sudo apt install graphviz\n",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        # DOT の構文エラー等は改善ループで回復しうるため例外を送出する
        raise RenderError(str(e), dot_path) from e

    return Path(rendered)
