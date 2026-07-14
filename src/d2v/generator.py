"""LLM にトポロジテキストを渡し、Graphviz DOT コードを生成する。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from d2v.llm import get_llm
from d2v.llm.base import LLMClient

# prompts/ ディレクトリはプロジェクトルート直下
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# ```dot ... ``` ブロックを抽出する正規表現
_DOT_BLOCK_RE = re.compile(r"```dot\s*(.*?)```", re.DOTALL | re.IGNORECASE)
# フォールバック: digraph で始まるコードブロック
_DIGRAPH_RE = re.compile(r"```[^\n]*\n\s*(digraph\s.*?)```", re.DOTALL)
# 汎用コードフェンス（```lang\n ... ```）
_GENERIC_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def _load_prompt(filename: str) -> str:
    """prompts/ ディレクトリからプロンプトファイルを読み込む。"""
    path = _PROMPTS_DIR / filename
    if not path.exists():
        print(f"\n[エラー] プロンプトファイルが見つかりません: {path}\n", file=sys.stderr)
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _strip_code_fences(text: str) -> str:
    """先頭・末尾に残ったコードフェンス行（``` で始まる行）を除去する。"""
    lines = text.splitlines()
    while lines and lines[0].lstrip().startswith("```"):
        lines.pop(0)
    while lines and lines[-1].lstrip().startswith("```"):
        lines.pop()
    return "\n".join(lines).strip()


def _extract_dot(text: str) -> str:
    """LLM 応答テキストから DOT コードを抽出する。

    優先順位:
      1. ```dot ... ``` ブロック
      2. ``` で囲まれた digraph から始まるブロック
      3. 任意の言語指定を持つ汎用コードフェンスブロック
      4. テキスト全体（フォールバック。残存するフェンス行は除去する）
    """
    m = _DOT_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _DIGRAPH_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _GENERIC_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    return _strip_code_fences(text)


# 入力テキストからノード数・エッジ数を読み取る正規表現
_NODE_COUNT_RE = re.compile(r"ノード一覧（(\d+)\s*台）")
_EDGE_COUNT_RE = re.compile(r"物理接続一覧（(\d+)\s*本）")


def _completeness_directive(topology_text: str) -> str:
    """入力の台数・本数を明示し、全要素の出力を促すディレクティブを生成する。"""
    m_nodes = _NODE_COUNT_RE.search(topology_text)
    m_edges = _EDGE_COUNT_RE.search(topology_text)
    parts: list[str] = []
    if m_nodes:
        parts.append(f"ノードを**ちょうど {m_nodes.group(1)} 個**")
    if m_edges:
        parts.append(f"エッジ（`->`）を**ちょうど {m_edges.group(1)} 本**")
    if not parts:
        return ""
    return (
        "\n\n## 出力要件（厳守）\n"
        f"生成する DOT には {' と '.join(parts)} 定義してください。"
        "要約・省略・プレースホルダは禁止です。"
        "出力前にノード数・エッジ数を数え、上記と一致することを確認してください。"
    )


def _looks_truncated(dot_code: str) -> bool:
    """DOT コードが途中で切れている（閉じ括弧不足）かを簡易判定する。"""
    if not dot_code:
        return True
    # digraph の開き括弧に対して閉じ括弧が不足していれば途中切れとみなす
    return dot_code.count("{") > dot_code.count("}")



def generate(topology_text: str, improvement_hints: list[str] | None = None) -> str:
    """トポロジテキストを LLM に渡し、Graphviz DOT コードを返す。

    Args:
        topology_text: parser.parse() が返す構造化トポロジテキスト
        improvement_hints: 改善ループ時に渡す改善点リスト（初回は None）

    Returns:
        Graphviz DOT 形式のコード文字列
    """
    system_prompt = _load_prompt("diagram-system.md")

    directive = _completeness_directive(topology_text)

    if improvement_hints:
        hints_text = "\n".join(f"- {h}" for h in improvement_hints)
        user_message = (
            f"{topology_text}\n\n"
            "## 前回の評価で指摘された改善点\n"
            "以下の問題点を修正した DOT コードを再生成してください:\n"
            f"{hints_text}"
            f"{directive}"
        )
    else:
        user_message = f"{topology_text}{directive}"

    llm = get_llm()
    response = llm.chat(system=system_prompt, user=user_message)
    dot_code = _extract_dot(response)

    # 出力トークン上限などで DOT が途中で切れた場合、続きを生成して結合する
    if _looks_truncated(dot_code):
        dot_code = _continue_generation(llm, system_prompt, user_message, dot_code)

    return dot_code


def _continue_generation(
    llm: LLMClient,
    system_prompt: str,
    user_message: str,
    partial_dot: str,
    max_rounds: int = 2,
) -> str:
    """途中で切れた DOT コードの続きを LLM に生成させて結合する。

    出力トークン上限に達して DOT が途切れた場合の救済策。直前までの部分コードを
    アシスタント出力として提示し、「続きのみ」を生成させて連結する。
    """
    accumulated = partial_dot
    for _ in range(max_rounds):
        if not _looks_truncated(accumulated):
            break
        continue_prompt = (
            f"{user_message}\n\n"
            "## これまでに生成した DOT コード（途中まで）\n"
            f"```dot\n{accumulated}\n```\n\n"
            "上記の DOT コードは出力途中で切れています。"
            "**続きの部分のみ**を出力してください（すでに出力済みの行は繰り返さない）。"
            "最終的に `}` で正しく閉じてください。コードブロックは不要で、続きの生テキストのみを返してください。"
        )
        response = llm.chat(system=system_prompt, user=continue_prompt)
        cont = _extract_dot(response)
        if not cont:
            break
        accumulated = f"{accumulated.rstrip()}\n{cont.lstrip()}"
    return accumulated
