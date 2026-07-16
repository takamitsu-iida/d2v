"""evaluator モジュールのテスト。

ルールベース検証（``_run_rule_checks``）・LLM 応答パース（``_parse_llm_json``）と、
LLM をモックした ``evaluate()`` のペナルティ適用・俯瞰図モード・JSON 保存を検証する。
"""

from __future__ import annotations

import json

import pytest

from d2v import evaluator

# ラベル・cluster を備えた「ペナルティなし」の DOT
_GOOD_DOT = """
digraph G {
  subgraph cluster_z { label="zone-a"; }
  "a" [label="A"];
  "b" [label="B"];
  "a" -> "b" [taillabel="p1", headlabel="p2"];
}
"""

_TOPO = "ノード一覧（2 台）\n...\n物理接続一覧（1 本）\n"


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response

    def chat(self, system: str, user: str) -> str:
        return self._response


@pytest.fixture
def install_llm(monkeypatch):
    def _install(response: str) -> None:
        monkeypatch.setattr(evaluator, "get_llm", lambda: _FakeLLM(response))

    return _install


# ---------------------------------------------------------------------------
# _parse_llm_json
# ---------------------------------------------------------------------------


def test_parse_json_from_code_fence():
    text = '```json\n{"score": 9, "issues": ["x"]}\n```'
    score, issues = evaluator._parse_llm_json(text)
    assert score == 9
    assert issues == ["x"]


def test_parse_json_raw_object():
    score, issues = evaluator._parse_llm_json('前置き {"score": 7, "issues": []} 後置き')
    assert score == 7
    assert issues == []


def test_parse_json_clamps_score():
    score, _ = evaluator._parse_llm_json('{"score": 99, "issues": []}')
    assert score == 10
    score, _ = evaluator._parse_llm_json('{"score": -5, "issues": []}')
    assert score == 1


def test_parse_json_invalid_falls_back_to_5():
    score, issues = evaluator._parse_llm_json("JSON ではないテキスト")
    assert score == 5
    assert issues  # フォールバックメッセージが入る


# ---------------------------------------------------------------------------
# _run_rule_checks
# ---------------------------------------------------------------------------


def test_rule_checks_detects_labels_and_cluster():
    rc = evaluator._run_rule_checks(_GOOD_DOT, _TOPO)
    assert rc.node_count_ok
    assert rc.edge_count_ok
    assert rc.has_taillabel
    assert rc.has_headlabel
    assert rc.has_subgraph_cluster


def test_rule_checks_flags_node_deficiency():
    topo = "ノード一覧（10 台）\n物理接続一覧（1 本）\n"
    rc = evaluator._run_rule_checks(_GOOD_DOT, topo)
    assert rc.node_count_ok is False


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def test_evaluate_passes_without_penalty(tmp_path, install_llm):
    install_llm('{"score": 9, "issues": []}')
    result = evaluator.evaluate(
        dot_code=_GOOD_DOT,
        topology_text=_TOPO,
        output_dir=tmp_path,
        iteration=0,
        threshold=8,
    )
    assert result.score == 9
    assert result.passed is True


def test_evaluate_penalizes_missing_cluster(tmp_path, install_llm):
    install_llm('{"score": 9, "issues": []}')
    dot_no_cluster = 'digraph G { "a" [label="A"]; "b" [label="B"]; "a" -> "b" [taillabel="p1", headlabel="p2"]; }'
    result = evaluator.evaluate(
        dot_code=dot_no_cluster,
        topology_text=_TOPO,
        output_dir=tmp_path,
        iteration=0,
        threshold=8,
    )
    assert result.score == 7  # cluster 欠如で 7 に減点
    assert result.passed is False
    assert any("cluster" in issue for issue in result.issues)


def test_evaluate_penalizes_node_deficiency(tmp_path, install_llm):
    install_llm('{"score": 9, "issues": []}')
    topo = "ノード一覧（10 台）\n物理接続一覧（1 本）\n"
    result = evaluator.evaluate(
        dot_code=_GOOD_DOT,
        topology_text=topo,
        output_dir=tmp_path,
        iteration=0,
        threshold=8,
    )
    assert result.score == 5  # ノード不足で 5 に減点
    assert result.passed is False


def test_evaluate_overview_skips_cluster_rules(tmp_path, install_llm):
    install_llm('{"score": 9, "issues": []}')
    # 俯瞰図では cluster / taillabel / headlabel を減点対象にしない
    overview_dot = 'digraph G { "z1" [label="zone1"]; "z2" [label="zone2"]; "z1" -> "z2"; }'
    result = evaluator.evaluate(
        dot_code=overview_dot,
        topology_text="ノード一覧（2 台）\n物理接続一覧（1 本）\n",
        output_dir=tmp_path,
        iteration=0,
        threshold=8,
        is_overview=True,
    )
    assert result.score == 9
    assert result.passed is True


def test_evaluate_writes_json_file(tmp_path, install_llm):
    install_llm('{"score": 8, "issues": []}')
    evaluator.evaluate(
        dot_code=_GOOD_DOT,
        topology_text=_TOPO,
        output_dir=tmp_path,
        iteration=3,
        threshold=8,
    )
    json_path = tmp_path / "eval_iter03.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["score"] == 8
    assert data["iteration"] == 3
