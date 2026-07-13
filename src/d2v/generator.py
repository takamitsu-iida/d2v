"""LLM にトポロジテキストを渡し、Graphviz DOT コードを生成する。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from d2v.llm import get_llm

# prompts/ ディレクトリはプロジェクトルート直下
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# ```dot ... ``` ブロックを抽出する正規表現
_DOT_BLOCK_RE = re.compile(r"```dot\s*(.*?)```", re.DOTALL | re.IGNORECASE)
# フォールバック: digraph で始まるコードブロック
_DIGRAPH_RE = re.compile(r"```[^\n]*\n\s*(digraph\s.*?)```", re.DOTALL)


def _load_prompt(filename: str) -> str:
    """prompts/ ディレクトリからプロンプトファイルを読み込む。"""
    path = _PROMPTS_DIR / filename
    if not path.exists():
        print(f"\n[エラー] プロンプトファイルが見つかりません: {path}\n", file=sys.stderr)
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _extract_dot(text: str) -> str:
    """LLM 応答テキストから DOT コードを抽出する。

    優先順位:
      1. ```dot ... ``` ブロック
      2. ``` で囲まれた digraph から始まるブロック
      3. テキスト全体（フォールバック）
    """
    m = _DOT_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _DIGRAPH_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def generate(topology_text: str, improvement_hints: list[str] | None = None) -> str:
    """トポロジテキストを LLM に渡し、Graphviz DOT コードを返す。

    Args:
        topology_text: parser.parse() が返す構造化トポロジテキスト
        improvement_hints: 改善ループ時に渡す改善点リスト（初回は None）

    Returns:
        Graphviz DOT 形式のコード文字列
    """
    system_prompt = _load_prompt("diagram-system.md")

    if improvement_hints:
        hints_text = "\n".join(f"- {h}" for h in improvement_hints)
        user_message = (
            f"{topology_text}\n\n"
            "## 前回の評価で指摘された改善点\n"
            "以下の問題点を修正した DOT コードを再生成してください:\n"
            f"{hints_text}"
        )
    else:
        user_message = topology_text

    llm = get_llm()
    response = llm.chat(system=system_prompt, user=user_message)
    return _extract_dot(response)
