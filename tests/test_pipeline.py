"""pipeline.run のループ制御テスト。

generator / renderer / evaluator をフェイクへ差し替え、LLM も Graphviz も使わずに
「合格による早期終了」「patience による早期終了」「最大イテレーション到達」
「ベストスコア選択」「レンダリング失敗のリカバリ」「全失敗時の例外」を検証する。
"""

from __future__ import annotations

import pytest

from d2v import pipeline
from d2v.errors import RenderFailedError
from d2v.evaluator import EvaluationResult, RuleCheckResult
from d2v.progress import ProgressEvent

_RULE_OK = RuleCheckResult(
    node_count_ok=True,
    edge_count_ok=True,
    has_taillabel=True,
    has_headlabel=True,
    has_subgraph_cluster=True,
    has_ip_labels=True,
)


@pytest.fixture
def patch_pipeline(monkeypatch):
    """scores とレンダリング失敗イテレーションを指定してフェイクを差し込む。

    戻り値は generate に渡された improvement_hints の記録リスト。
    """

    def _install(scores, *, render_fail_iters=()):
        fail = set(render_fail_iters)
        gen_hints: list[list[str] | None] = []

        def fake_generate(topology_text, improvement_hints=None, system_prompt_file="diagram-system.md"):
            gen_hints.append(improvement_hints)
            return "digraph G { a -> b; }"

        def fake_render(dot_code, output_dir, stem="diagram", fmt="png", zone_opacity=0.4):
            i = len(gen_hints) - 1  # 直前の generate に対応するイテレーション
            if i in fail:
                raise pipeline.renderer.RenderError("構文エラー", output_dir / f"{stem}.dot")
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / f"{stem}.{fmt}"
            path.write_bytes(b"img")
            return path

        def fake_evaluate(dot_code, topology_text, output_dir, iteration=0, threshold=8, is_overview=False):
            score = scores[iteration]
            return EvaluationResult(
                iteration=iteration,
                score=score,
                passed=score >= threshold,
                issues=[f"issue-{iteration}"],
                rule_checks=_RULE_OK,
            )

        monkeypatch.setattr(pipeline.generator, "generate", fake_generate)
        monkeypatch.setattr(pipeline.renderer, "render", fake_render)
        monkeypatch.setattr(pipeline.evaluator, "evaluate", fake_evaluate)
        return gen_hints

    return _install


def test_passes_on_first_iteration(patch_pipeline, tmp_path):
    patch_pipeline([9])
    result = pipeline.run("topo", tmp_path, max_iterations=3, threshold=8, patience=1)
    assert result.total_iterations == 1
    assert result.best_result.score == 9
    assert result.best_result.passed is True
    assert result.best_image.exists()


def test_runs_to_max_when_never_passing(patch_pipeline, tmp_path):
    # スコアが毎回改善するので patience では止まらず、最大回数まで回る
    patch_pipeline([5, 6, 7])
    result = pipeline.run("topo", tmp_path, max_iterations=3, threshold=8, patience=1)
    assert result.total_iterations == 3
    assert result.best_result.score == 7


def test_early_stop_on_patience(patch_pipeline, tmp_path):
    # iter0 で 7 → 以降改善しないので patience=1 で iter1 終了時に打ち切り
    patch_pipeline([7, 6, 6, 6])
    result = pipeline.run("topo", tmp_path, max_iterations=4, threshold=8, patience=1)
    assert result.total_iterations == 2
    assert result.best_result.score == 7


def test_best_is_highest_score(patch_pipeline, tmp_path):
    # スコアが 6 → 9 → 4 と推移。ベストは iter1 の 9。patience を無効化して全周回す
    patch_pipeline([6, 9, 4])
    result = pipeline.run("topo", tmp_path, max_iterations=3, threshold=11, patience=3)
    assert result.total_iterations == 3
    assert result.best_result.score == 9
    assert result.best_result.iteration == 1


def test_render_failure_is_recovered(patch_pipeline, tmp_path):
    # iter0 はレンダリング失敗（記録されない）→ iter1 で合格
    gen_hints = patch_pipeline([None, 9], render_fail_iters={0})
    result = pipeline.run("topo", tmp_path, max_iterations=3, threshold=8, patience=2)
    # 成功したイテレーションのみ記録される
    assert result.total_iterations == 1
    assert result.best_result.score == 9
    # iter1 の generate にはレンダリング失敗が改善ヒントとして渡る
    assert gen_hints[1] is not None
    assert any("Graphviz" in h for h in gen_hints[1])


def test_all_render_failures_raise(patch_pipeline, tmp_path):
    patch_pipeline([None, None, None], render_fail_iters={0, 1, 2})
    with pytest.raises(RenderFailedError):
        pipeline.run("topo", tmp_path, max_iterations=3, threshold=8, patience=3)


def test_progress_callback_emits_stages(patch_pipeline, tmp_path):
    patch_pipeline([9])
    events: list[ProgressEvent] = []
    pipeline.run(
        "topo", tmp_path, max_iterations=1, threshold=8,
        progress_callback=events.append,
    )
    stages = {e.stage for e in events}
    assert {"generate", "render", "evaluate", "score", "passed", "pipeline_done"} <= stages


# ---------------------------------------------------------------------------
# _should_early_stop（純粋関数）
# ---------------------------------------------------------------------------


def test_should_early_stop_when_streak_reaches_patience():
    # streak(1) >= patience(1) かつ最終回でない → True
    assert pipeline._should_early_stop(1, 1, iteration=0, max_iterations=3) is True


def test_should_not_early_stop_below_patience():
    # streak(1) < patience(2) → False
    assert pipeline._should_early_stop(1, 2, iteration=0, max_iterations=3) is False


def test_should_not_early_stop_on_last_iteration():
    # 最終回（iteration+1 == max_iterations）は早期終了不要 → False
    assert pipeline._should_early_stop(5, 1, iteration=2, max_iterations=3) is False
