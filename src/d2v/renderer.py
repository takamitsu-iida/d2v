"""Graphviz DOT コードを PNG / SVG にレンダリングする。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import graphviz


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
    dot_code = apply_zone_opacity(dot_code, zone_opacity)
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
