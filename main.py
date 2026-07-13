#!/usr/bin/env python3
"""d2v: iida-network-model YAML → ネットワーク構成図（PNG / SVG）生成ツール。"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from d2v import parser, pipeline, visualizer

console = Console()


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="d2v",
        description="iida-network-model YAML からネットワーク構成図を生成します。",
    )
    ap.add_argument(
        "--input", "-i",
        required=True,
        type=Path,
        metavar="TOPOLOGY_YAML",
        help="入力トポロジ YAML ファイルのパス",
    )
    ap.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path("output"),
        metavar="DIR",
        help="出力ディレクトリ（デフォルト: output）",
    )
    ap.add_argument(
        "--format", "-f",
        choices=["png", "svg"],
        default="png",
        help="出力フォーマット（デフォルト: png）",
    )
    ap.add_argument(
        "--max-iter", "-n",
        type=int,
        default=3,
        metavar="N",
        help="最大イテレーション数（デフォルト: 3）",
    )
    ap.add_argument(
        "--threshold", "-t",
        type=int,
        default=8,
        metavar="SCORE",
        help="合格スコア閾値 1〜10（デフォルト: 8）",
    )
    args = ap.parse_args()

    console.print(Panel(
        f"入力ファイル     : [bold cyan]{args.input}[/bold cyan]\n"
        f"出力ディレクトリ : [bold cyan]{args.output_dir}[/bold cyan]\n"
        f"フォーマット     : [bold cyan]{args.format}[/bold cyan]\n"
        f"最大イテレーション: [bold cyan]{args.max_iter}[/bold cyan]\n"
        f"合格スコア閾値   : [bold cyan]{args.threshold}/10[/bold cyan]",
        title="[bold blue]d2v  ネットワーク構成図ジェネレーター[/bold blue]",
        expand=False,
    ))

    # ── Step 1: トポロジ解析 ──────────────────────────────────────
    console.print(Rule("[bold]Step 1  トポロジ解析[/bold]"))
    topology_text = parser.parse(args.input)
    console.print(topology_text)

    # ── Step 2: 生成 → 評価 → 改善ループ ─────────────────────────
    console.print(Rule("[bold]Step 2  生成 → 評価 → 改善ループ[/bold]"))
    result = pipeline.run(
        topology_text=topology_text,
        output_dir=args.output_dir,
        stem=args.input.stem,
        fmt=args.format,
        max_iterations=args.max_iter,
        threshold=args.threshold,
    )

    # ── 最終サマリー ──────────────────────────────────────────────
    best = result.best_result
    score_color = (
        "green" if best.score >= args.threshold
        else "yellow" if best.score >= 6
        else "red"
    )
    # スコア推移グラフの生成
    plot_path: Path | None = None
    if len(result.records) > 1:
        plot_path = visualizer.plot_score_history(
            result.records,
            args.output_dir / "score_history.png",
            args.threshold,
        )

    console.print(Panel(
        f"最終スコア       : [{score_color}]{best.score}/10[/{score_color}]\n"
        f"イテレーション数 : {result.total_iterations}/{args.max_iter}\n"
        f"出力ファイル     : [bold]{result.best_image}[/bold]"
        + (f"\nスコアグラフ     : [bold]{plot_path}[/bold]" if plot_path else ""),
        title="[bold green]✓ 完了[/bold green]",
        expand=False,
    ))


if __name__ == "__main__":
    main()
