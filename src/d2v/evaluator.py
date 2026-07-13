"""生成した DOT コードをルールベース + LLM で評価し EvaluationResult を返す。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel

from d2v.llm import get_llm

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# ---------------------------------------------------------------------------
# Pydantic モデル
# ---------------------------------------------------------------------------


class RuleCheckResult(BaseModel):
    """ルールベース検証の結果。"""

    node_count_ok: bool         # 期待ノード数を充足しているか
    edge_count_ok: bool         # 期待エッジ数を充足しているか
    has_taillabel: bool         # いずれかのエッジに taillabel があるか
    has_headlabel: bool         # いずれかのエッジに headlabel があるか
    has_subgraph_cluster: bool  # subgraph cluster が存在するか
    has_ip_labels: bool         # IP アドレスのようなラベルがあるか


class EvaluationResult(BaseModel):
    """評価結果。"""

    iteration: int
    score: int           # LLM による 1〜10 点（ルールペナルティ適用後）
    passed: bool         # score >= threshold
    issues: list[str]    # 改善点リスト
    rule_checks: RuleCheckResult


# ---------------------------------------------------------------------------
# ルールベース検証
# ---------------------------------------------------------------------------

_NODE_COUNT_RE = re.compile(r"ノード一覧（(\d+)\s*台）")
_EDGE_COUNT_RE = re.compile(r"物理接続一覧（(\d+)\s*本）")

# DOT ノード定義: 行頭 or セミコロン/開き括弧の直後に識別子 + [ が続く
# → インライン cluster 記述にも対応（"{ label=...; nodeId [...];" など）
# エッジ属性（-> nodeId [...]）との区別: -> の直後は除外
_DOT_NODE_DEF_RE = re.compile(
    r"(?:^|[;{])\s*(?!subgraph\b|digraph\b|graph\b|edge\b|node\b)"
    r"([A-Za-z_][A-Za-z0-9_-]*)\s*\[",
    re.MULTILINE,
)
_DOT_EDGE_RE = re.compile(r"->")
_IP_RE = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?")


def _run_rule_checks(
    dot_code: str,
    topology_text: str,
    threshold_ratio: float = 0.8,
) -> RuleCheckResult:
    """DOT コードに対してルールベース検証を実行する。"""
    m_nodes = _NODE_COUNT_RE.search(topology_text)
    expected_nodes = int(m_nodes.group(1)) if m_nodes else 0

    m_edges = _EDGE_COUNT_RE.search(topology_text)
    expected_edges = int(m_edges.group(1)) if m_edges else 0

    dot_nodes = len(set(_DOT_NODE_DEF_RE.findall(dot_code)))
    dot_edges = len(_DOT_EDGE_RE.findall(dot_code))

    return RuleCheckResult(
        node_count_ok=(
            expected_nodes == 0
            or dot_nodes >= int(expected_nodes * threshold_ratio)
        ),
        edge_count_ok=(
            expected_edges == 0
            or dot_edges >= int(expected_edges * threshold_ratio)
        ),
        has_taillabel="taillabel=" in dot_code,
        has_headlabel="headlabel=" in dot_code,
        has_subgraph_cluster=bool(re.search(r"subgraph\s+cluster", dot_code)),
        has_ip_labels=bool(_IP_RE.search(dot_code)),
    )


# ---------------------------------------------------------------------------
# LLM 評価
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJ_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    if not path.exists():
        print(f"\n[エラー] プロンプトファイルが見つかりません: {path}\n", file=sys.stderr)
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _parse_llm_json(text: str) -> tuple[int, list[str]]:
    """LLM 応答テキストから score と issues を抽出する。"""
    # コードブロック内の JSON を優先
    m = _JSON_BLOCK_RE.search(text)
    raw = m.group(1) if m else text

    # JSON オブジェクトを抽出
    m2 = _JSON_OBJ_RE.search(raw)
    if not m2:
        return 5, ["LLM の評価レスポンスから JSON を抽出できませんでした。"]

    try:
        data = json.loads(m2.group())
        score = max(1, min(10, int(data.get("score", 5))))
        issues = [str(i) for i in data.get("issues", [])]
        return score, issues
    except (json.JSONDecodeError, ValueError):
        return 5, ["LLM の評価レスポンスの JSON パースに失敗しました。"]


# ---------------------------------------------------------------------------
# メイン API
# ---------------------------------------------------------------------------


def evaluate(
    dot_code: str,
    topology_text: str,
    output_dir: Path,
    iteration: int = 0,
    threshold: int = 8,
) -> EvaluationResult:
    """DOT コードを評価し EvaluationResult を返す。

    評価は 2 段階で行う:
      1. ルールベース検証（正規表現による構造チェック）
      2. LLM レビュー（diagram-evaluator.md プロンプト使用）

    ルールベースで重大な問題が検出された場合はスコアに上限ペナルティを適用する。

    Args:
        dot_code: 評価対象の Graphviz DOT コード
        topology_text: parser.parse() が返した構造化トポロジテキスト
        output_dir: 評価結果 JSON の保存先
        iteration: 現在のループ回数（0 始まり）
        threshold: passed = True とするスコア閾値

    Returns:
        EvaluationResult
    """
    # ── Step 1: ルールベース検証 ──────────────────────────────────
    rule_checks = _run_rule_checks(dot_code, topology_text)

    # ── Step 2: LLM 評価 ──────────────────────────────────────────
    system_prompt = _load_prompt("diagram-evaluator.md")
    user_message = (
        f"## トポロジデータ\n\n{topology_text}\n\n"
        f"## 評価対象 DOT コード\n\n```dot\n{dot_code}\n```"
    )
    llm = get_llm()
    response = llm.chat(system=system_prompt, user=user_message)
    score, issues = _parse_llm_json(response)

    # ── Step 3: ルール違反を issues に追記しスコアにペナルティ適用 ──
    rule_issues: list[str] = []
    if not rule_checks.node_count_ok:
        rule_issues.append("DOT のノード数が入力データに対して不足しています。全デバイスを定義してください。")
        score = min(score, 5)
    if not rule_checks.edge_count_ok:
        rule_issues.append("DOT のエッジ数が入力データに対して不足しています。全接続を定義してください。")
        score = min(score, 5)
    if not rule_checks.has_subgraph_cluster:
        rule_issues.append("subgraph cluster が定義されていません。zone ごとにグループ化してください。")
        score = min(score, 7)
    if not rule_checks.has_taillabel:
        rule_issues.append("エッジに taillabel（送信元ポート名）が設定されていません。")
    if not rule_checks.has_headlabel:
        rule_issues.append("エッジに headlabel（宛先ポート名）が設定されていません。")

    # ルール由来の issues を先頭に追加（LLM issues が後続）
    issues = rule_issues + issues

    result = EvaluationResult(
        iteration=iteration,
        score=score,
        passed=score >= threshold,
        issues=issues,
        rule_checks=rule_checks,
    )

    # ── Step 4: JSON 保存 ─────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"eval_iter{iteration:02d}.json"
    json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    return result
