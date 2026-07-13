"""Graphviz DOT コードを PNG / SVG にレンダリングする。"""

from __future__ import annotations

import sys
from pathlib import Path

import graphviz


def render(
    dot_code: str,
    output_dir: Path,
    stem: str = "diagram",
    fmt: str = "png",
) -> Path:
    """DOT コードをレンダリングして画像ファイルを保存する。

    Args:
        dot_code: Graphviz DOT 形式のコード
        output_dir: 出力先ディレクトリ（存在しない場合は自動作成）
        stem: 出力ファイル名（拡張子なし）
        fmt: 出力フォーマット ("png" または "svg")

    Returns:
        生成した画像ファイルの Path
    """
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
        print(
            "\n[エラー] Graphviz がインストールされていません。\n"
            "  sudo apt install graphviz\n",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(
            f"\n[レンダリングエラー] DOT コードの処理に失敗しました:\n{e}\n"
            f"DOT ファイルを確認してください: {dot_path}\n",
            file=sys.stderr,
        )
        sys.exit(1)

    return Path(rendered)
