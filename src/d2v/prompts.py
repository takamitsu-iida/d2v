"""プロンプトファイルの共通ローダー。

``prompts/`` ディレクトリはプロジェクトルート直下にあり、
generator / evaluator / pipeline / v2d などの各モジュールから参照される。
以前は各モジュールが同じ ``_PROMPTS_DIR`` 計算と ``_load_prompt`` 実装を
重複して持っていたため、ここへ一本化する。
"""

from __future__ import annotations

from pathlib import Path

from d2v.errors import PromptNotFoundError

# このファイルは src/d2v/prompts.py にあるため、プロジェクトルートは 3 つ上。
# （src/d2v/prompts.py → src/d2v → src → <root>）
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def load_prompt(filename: str) -> str:
    """``prompts/`` ディレクトリからプロンプトファイルを読み込む。

    Args:
        filename: ``prompts/`` からの相対ファイル名（例: ``diagram-system.md``）。

    Returns:
        プロンプト本文（UTF-8）。

    Raises:
        PromptNotFoundError: ファイルが存在しない場合。
    """
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise PromptNotFoundError(f"プロンプトファイルが見つかりません: {path}")
    return path.read_text(encoding="utf-8")
