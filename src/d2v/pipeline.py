"""生成 → 評価 → 改善ループを制御するパイプライン。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from d2v import evaluator, generator, renderer
from d2v.errors import RenderFailedError
from d2v.evaluator import EvaluationResult
from d2v.progress import ProgressCallback, ProgressEvent, emit
from d2v.prompts import load_prompt

console = Console()


# ---------------------------------------------------------------------------
# データモデル
# ---------------------------------------------------------------------------


@dataclass
class IterationRecord:
    """1 イテレーションの記録。"""

    iteration: int
    dot_code: str
    image_path: Path
    result: EvaluationResult
    is_best: bool = False


@dataclass
class PipelineResult:
    """パイプライン全体の実行結果。"""

    best_dot: str
    best_result: EvaluationResult
    best_image: Path
    best_legend: Path | None = None
    records: list[IterationRecord] = field(default_factory=list)

    @property
    def total_iterations(self) -> int:
        return len(self.records)


# ---------------------------------------------------------------------------
# 改善プロンプト
# ---------------------------------------------------------------------------


def _improve(topology_text: str, dot_code: str, issues: list[str]) -> str:
    """評価結果の改善点を LLM に渡し、修正済み DOT コードを返す。"""
    from d2v.llm import get_llm
    from d2v.generator import _extract_dot  # DOT 抽出ユーティリティを再利用

    system_prompt = load_prompt("diagram-improver.md")
    issues_text = "\n".join(f"- {issue}" for issue in issues)
    user_message = (
        f"## トポロジデータ\n\n{topology_text}\n\n"
        f"## 現在の DOT コード\n\n```dot\n{dot_code}\n```\n\n"
        f"## 評価で指摘された改善点\n\n{issues_text}"
    )

    llm = get_llm()
    response = llm.chat(system=system_prompt, user=user_message)
    return _extract_dot(response)


def _should_early_stop(
    no_improve_streak: int,
    patience: int,
    iteration: int,
    max_iterations: int,
) -> bool:
    """改善が頭打ちのとき早期終了すべきか判定する（純粋関数）。

    ベストスコアが ``patience`` 回連続で更新されず、かつ次のイテレーションが
    残っている（最終回ではない）場合に True を返す。

    Args:
        no_improve_streak: ベスト非更新が続いた回数。
        patience: 早期終了を許容する非更新の連続回数。
        iteration: 現在のイテレーション番号（0 始まり）。
        max_iterations: 最大イテレーション数。
    """
    return no_improve_streak >= patience and iteration + 1 < max_iterations


# ---------------------------------------------------------------------------
# メイン API
# ---------------------------------------------------------------------------


def run(
    topology_text: str,
    output_dir: Path,
    stem: str = "diagram",
    fmt: str = "png",
    max_iterations: int = 3,
    threshold: int = 8,
    patience: int = 1,
    zone_opacity: float = 0.4,
    system_prompt_file: str = "diagram-system.md",
    progress_callback: ProgressCallback | None = None,
) -> PipelineResult:
    """生成 → 評価 → 改善ループを実行する。

    フロー（1 イテレーション）:
      1. LLM で DOT コードを生成（初回: ヒントなし、2 回目以降: 前回の改善点を渡す）
      2. Graphviz でレンダリング → ``output/iter_NN/`` に保存
      3. 評価 → EvaluationResult を取得
      4. ベストスコアを更新（スコアが下がった場合は直前を保持）
      5. passed == True、ベスト非更新が patience 回連続、または最大イテレーション
         到達でループ終了

    Args:
        topology_text: parser.parse() が返した構造化トポロジテキスト
        output_dir: ルート出力ディレクトリ
        stem: 出力ファイル名ベース（拡張子なし）
        fmt: 画像フォーマット ("png" or "svg")
        max_iterations: 最大イテレーション数
        threshold: passed = True とするスコア閾値
        patience: ベストスコアが更新されない状態が何回連続したら早期終了するか
            （改善が頭打ちの場合に無駄なイテレーションを省き高速化する）
        zone_opacity: ゾーン（cluster）背景色の不透明度 0.0〜1.0。1.0 未満のとき
            背景色を淡く（透過）してレンダリングする。
        system_prompt_file: DOT 生成に使うシステムプロンプトファイル名。俯瞰図など
            用途に応じて切り替える（デフォルトはデバイス詳細図用）。
        progress_callback: 進捗イベントのコールバック（Web GUI 用）。None なら
            従来どおり rich のコンソール表示のみ。設定時は各ステップで
            ``ProgressEvent`` を追加的に emit する（コンソール表示は維持）。

    Returns:
        PipelineResult（ベストスコアの DOT・画像・評価結果を含む）
    """
    best_record: IterationRecord | None = None
    records: list[IterationRecord] = []
    improvement_hints: list[str] | None = None
    no_improve_streak = 0

    for i in range(max_iterations):
        iter_dir = output_dir / f"iter_{i:02d}"
        console.print(f"\n[bold cyan]── Iteration {i + 1}/{max_iterations} ──[/bold cyan]")
        emit(progress_callback, ProgressEvent(
            stage="iteration_start", iteration=i, total=max_iterations,
            message=f"Iteration {i + 1}/{max_iterations}",
        ))

        # ── 生成 ────────────────────────────────────────────────
        console.print("  [dim][1/3] DOT コード生成中...[/dim]")
        emit(progress_callback, ProgressEvent(
            stage="generate", iteration=i, total=max_iterations,
            message="DOT コード生成中",
        ))
        dot_code = generator.generate(
            topology_text, improvement_hints, system_prompt_file=system_prompt_file
        )

        # ── レンダリング ─────────────────────────────────────────
        console.print("  [dim][2/3] Graphviz レンダリング中...[/dim]")
        emit(progress_callback, ProgressEvent(
            stage="render", iteration=i, total=max_iterations,
            message="Graphviz レンダリング中",
        ))
        try:
            img_path = renderer.render(
                dot_code, iter_dir, stem=stem, fmt=fmt, zone_opacity=zone_opacity
            )
        except renderer.RenderError as e:
            # DOT の構文エラー等でレンダリングに失敗した場合、パイプラインを
            # 停止させず、エラー内容を改善ヒントとして次イテレーションへ渡す。
            console.print(
                f"  [red]✗ レンダリング失敗:[/red] {e}\n"
                f"  [dim]  DOT ファイル: {e.dot_path}[/dim]"
            )
            console.print(
                "  [yellow]  → エラーを改善点として次イテレーションで修正を試みます。[/yellow]"
            )
            emit(progress_callback, ProgressEvent(
                stage="render_failed", iteration=i, total=max_iterations,
                message=f"レンダリング失敗: {e}",
            ))
            improvement_hints = [
                "生成した DOT コードが Graphviz の構文エラーでレンダリングに失敗しました。"
                "有効な DOT 構文になるよう修正してください。"
                f"Graphviz のエラー: {e}"
            ]
            continue

        # ── 評価 ─────────────────────────────────────────────────
        console.print("  [dim][3/3] LLM 評価中...[/dim]")
        emit(progress_callback, ProgressEvent(
            stage="evaluate", iteration=i, total=max_iterations,
            message="LLM 評価中",
        ))
        result = evaluator.evaluate(
            dot_code=dot_code,
            topology_text=topology_text,
            output_dir=iter_dir,
            iteration=i,
            threshold=threshold,
            is_overview="overview" in system_prompt_file,
        )

        # ── ベスト更新判定 ───────────────────────────────────────
        is_best = best_record is None or result.score > best_record.result.score
        record = IterationRecord(
            iteration=i,
            dot_code=dot_code,
            image_path=img_path,
            result=result,
            is_best=is_best,
        )
        records.append(record)
        if is_best:
            best_record = record

        # ── 進捗表示 ─────────────────────────────────────────────
        score_color = (
            "green" if result.score >= threshold
            else "yellow" if result.score >= 6
            else "red"
        )
        best_mark = "  [bold yellow]★ NEW BEST[/bold yellow]" if is_best else ""
        console.print(
            f"  スコア: [{score_color}]{result.score}/10[/{score_color}]  "
            f"passed={result.passed}{best_mark}"
        )
        for issue in result.issues[:3]:
            console.print(f"  [dim]  · {issue}[/dim]")
        if len(result.issues) > 3:
            console.print(f"  [dim]  ... 他 {len(result.issues) - 3} 件[/dim]")
        emit(progress_callback, ProgressEvent(
            stage="score", iteration=i, total=max_iterations,
            score=result.score, passed=result.passed, is_best=is_best,
            message=f"スコア {result.score}/10",
            extra={"issues": list(result.issues)},
        ))

        # ── 終了判定 ─────────────────────────────────────────────
        if result.passed:
            console.print(
                f"\n  [bold green]✓ スコア {result.score} が閾値 {threshold} に達しました。"
                f"ループ終了。[/bold green]"
            )
            emit(progress_callback, ProgressEvent(
                stage="passed", iteration=i, total=max_iterations,
                score=result.score, passed=True,
                message=f"スコア {result.score} が閾値 {threshold} に到達",
            ))
            break

        # ── 早期終了判定（改善が頭打ちなら打ち切って高速化）───────
        no_improve_streak = 0 if is_best else no_improve_streak + 1
        if _should_early_stop(no_improve_streak, patience, i, max_iterations):
            console.print(
                f"\n  [yellow]→ スコアが {no_improve_streak} 回連続で改善しなかったため"
                f"早期終了します（ベスト: {best_record.result.score}/10）。[/yellow]"
            )
            emit(progress_callback, ProgressEvent(
                stage="early_stop", iteration=i, total=max_iterations,
                score=best_record.result.score,
                message=f"改善頭打ちで早期終了（ベスト: {best_record.result.score}/10）",
            ))
            break

        # ── 次イテレーション準備（改善点を渡す）─────────────────
        improvement_hints = result.issues

    # ── ベスト成果物をルートにコピー ─────────────────────────────
    if best_record is None:
        # 全イテレーションがレンダリングに失敗した場合
        raise RenderFailedError(
            "全イテレーションでレンダリングに失敗しました。有効な図を生成できませんでした。"
            "各 iter ディレクトリの .dot ファイルを確認してください。"
        )

    best_final_image = output_dir / f"{stem}_best.{fmt}"
    shutil.copy2(best_record.image_path, best_final_image)

    # 凡例は別ファイル（<stem>_legend.<fmt>）として出力されるため、ベスト図と
    # 並べて参照できるよう best 側にも一緒にコピーする（存在する場合のみ）。
    best_legend_src = best_record.image_path.with_name(f"{stem}_legend.{fmt}")
    best_final_legend: Path | None = None
    if best_legend_src.exists():
        best_final_legend = output_dir / f"{stem}_best_legend.{fmt}"
        shutil.copy2(best_legend_src, best_final_legend)

    # ── サマリーテーブル ──────────────────────────────────────────
    _print_summary(records, threshold)

    emit(progress_callback, ProgressEvent(
        stage="pipeline_done",
        score=best_record.result.score,
        passed=best_record.result.passed,
        total=len(records),
        message=f"完了（ベスト {best_record.result.score}/10, {len(records)} イテレーション）",
        extra={"best_image": str(best_final_image)},
    ))

    return PipelineResult(
        best_dot=best_record.dot_code,
        best_result=best_record.result,
        best_image=best_final_image,
        best_legend=best_final_legend,
        records=records,
    )


def _print_summary(records: list[IterationRecord], threshold: int) -> None:
    """イテレーション結果のサマリーテーブルを表示する。"""
    table = Table(
        title="イテレーション結果サマリー",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Iter", style="dim", width=5, justify="center")
    table.add_column("スコア", width=7, justify="center")
    table.add_column("合格", width=5, justify="center")
    table.add_column("Best", width=5, justify="center")
    table.add_column("主な改善点", overflow="fold")

    for rec in records:
        color = (
            "green" if rec.result.score >= threshold
            else "yellow" if rec.result.score >= 6
            else "red"
        )
        first_issue = rec.result.issues[0] if rec.result.issues else "—"
        table.add_row(
            str(rec.iteration),
            f"[{color}]{rec.result.score}/10[/{color}]",
            "✓" if rec.result.passed else "✗",
            "[yellow]★[/yellow]" if rec.is_best else "",
            first_issue,
        )

    console.print()
    console.print(table)
