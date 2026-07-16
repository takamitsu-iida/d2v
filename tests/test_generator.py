"""generator モジュールのテスト。

DOT 抽出・途中切れ判定・完全性ディレクティブといった純粋関数と、
LLM をモックした ``generate()`` のふるまい（改善ヒントの反映・途中切れ時の
継続生成）を検証する。
"""

from __future__ import annotations

import pytest

from d2v import generator


class _FakeLLM:
    """``chat`` が事前設定した応答を順に返すフェイク LLM クライアント。"""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._responses.pop(0)


@pytest.fixture
def install_llm(monkeypatch):
    """responses を返す _FakeLLM を generator.get_llm に差し込むファクトリ。"""

    def _install(responses: list[str]) -> _FakeLLM:
        llm = _FakeLLM(responses)
        monkeypatch.setattr(generator, "get_llm", lambda: llm)
        return llm

    return _install


# ---------------------------------------------------------------------------
# _extract_dot
# ---------------------------------------------------------------------------


def test_extract_dot_from_dot_fence():
    text = "説明文\n```dot\ndigraph G { a -> b; }\n```\nおわり"
    assert generator._extract_dot(text) == "digraph G { a -> b; }"


def test_extract_dot_digraph_fallback():
    text = "```\ndigraph G {\n  a -> b;\n}\n```"
    assert generator._extract_dot(text).startswith("digraph G")


def test_extract_dot_generic_fence():
    text = "```text\nfoo bar\n```"
    assert generator._extract_dot(text) == "foo bar"


def test_extract_dot_raw_text():
    assert "digraph G" in generator._extract_dot("digraph G { a -> b; }")


# ---------------------------------------------------------------------------
# _looks_truncated
# ---------------------------------------------------------------------------


def test_looks_truncated_balanced():
    assert generator._looks_truncated("digraph G { a -> b; }") is False


def test_looks_truncated_unbalanced():
    assert generator._looks_truncated("digraph G { a -> b;") is True


def test_looks_truncated_empty():
    assert generator._looks_truncated("") is True


# ---------------------------------------------------------------------------
# _completeness_directive
# ---------------------------------------------------------------------------


def test_completeness_directive_includes_counts():
    topo = "ノード一覧（5 台）\n...\n物理接続一覧（7 本）\n"
    directive = generator._completeness_directive(topo)
    assert "5" in directive
    assert "7" in directive


def test_completeness_directive_empty_without_counts():
    assert generator._completeness_directive("カウント情報なし") == ""


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_generate_returns_extracted_dot(install_llm):
    install_llm(["```dot\ndigraph G { a -> b; }\n```"])
    out = generator.generate("ノード一覧（2 台）\n物理接続一覧（1 本）\n")
    assert out == "digraph G { a -> b; }"


def test_generate_passes_improvement_hints(install_llm):
    llm = install_llm(["```dot\ndigraph G { a -> b; }\n```"])
    generator.generate("topo", improvement_hints=["ラベルを追加", "色を変更"])
    _system, user = llm.calls[0]
    assert "ラベルを追加" in user
    assert "色を変更" in user
    assert "改善点" in user


def test_generate_continues_when_truncated(install_llm):
    # 1 回目は閉じ括弧が不足して途中切れ、2 回目で続きを返して完成させる。
    llm = install_llm(
        [
            "```dot\ndigraph G { a -> b;\n```",  # } 不足 → truncated
            "```dot\n  c -> d;\n}\n```",          # 続き
        ]
    )
    out = generator.generate("topo")
    assert out.count("{") == out.count("}")
    assert len(llm.calls) == 2
