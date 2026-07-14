#!/usr/bin/env python3
"""d2v: iida-network-model YAML → ネットワーク構成図（PNG / SVG）生成ツール。"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from d2v import parser, partitioner, pipeline, visualizer

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
    ap.add_argument(
        "--patience",
        type=int,
        default=1,
        metavar="N",
        help=(
            "ベストスコアが N 回連続で更新されない場合に改善ループを早期終了する"
            "（改善が頭打ちのときの無駄なイテレーションを省き高速化。デフォルト: 1）"
        ),
    )
    ap.add_argument(
        "--split-threshold",
        type=int,
        default=partitioner.DEFAULT_SPLIT_THRESHOLD,
        metavar="N",
        help=(
            "ノード数がこの値を超え、かつ zone 情報がある場合に俯瞰図＋ゾーン詳細図へ"
            f"自動分割する（デフォルト: {partitioner.DEFAULT_SPLIT_THRESHOLD}）"
        ),
    )
    ap.add_argument(
        "--no-split",
        action="store_true",
        help="自動分割を無効化し、常に 1 枚の図として生成する",
    )
    args = ap.parse_args()

    console.print(Panel(
        f"入力ファイル     : [bold cyan]{args.input}[/bold cyan]\n"
        f"出力ディレクトリ : [bold cyan]{args.output_dir}[/bold cyan]\n"
        f"フォーマット     : [bold cyan]{args.format}[/bold cyan]\n"
        f"最大イテレーション: [bold cyan]{args.max_iter}[/bold cyan]\n"
        f"合格スコア閾値   : [bold cyan]{args.threshold}/10[/bold cyan]\n"
        f"分割しきい値     : "
        + ("[bold cyan]無効[/bold cyan]" if args.no_split
           else f"[bold cyan]{args.split_threshold} ノード超で分割[/bold cyan]"),
        title="[bold blue]d2v  ネットワーク構成図ジェネレーター[/bold blue]",
        expand=False,
    ))

    # ── Step 1: トポロジ解析 ──────────────────────────────────────
    console.print(Rule("[bold]Step 1  トポロジ解析[/bold]"))
    model = parser.load_model(args.input)
    topology_text = parser.build_text(
        model.devices, model.connections, model.subnets, model.device_map
    )
    console.print(topology_text)

    # ── 分割計画の判定 ────────────────────────────────────────────
    diagrams = None if args.no_split else partitioner.plan(model, args.split_threshold)

    if diagrams is None:
        _run_single(args, topology_text)
    else:
        _run_split(args, diagrams)


def _run_single(args: argparse.Namespace, topology_text: str) -> None:
    """従来どおり 1 枚の図を生成する。"""
    # ── Step 2: 生成 → 評価 → 改善ループ ─────────────────────────
    console.print(Rule("[bold]Step 2  生成 → 評価 → 改善ループ[/bold]"))
    result = pipeline.run(
        topology_text=topology_text,
        output_dir=args.output_dir,
        stem=args.input.stem,
        fmt=args.format,
        max_iterations=args.max_iter,
        threshold=args.threshold,
        patience=args.patience,
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


def _run_split(args: argparse.Namespace, diagrams: list) -> None:
    """俯瞰図＋ゾーン詳細図を複数枚生成する。"""
    console.print(Rule(
        f"[bold]Step 2  分割生成（{len(diagrams)} 枚: 俯瞰図 + ゾーン詳細）[/bold]"
    ))
    console.print(
        f"  [yellow]ノード数がしきい値 {args.split_threshold} を超えたため、"
        f"zone 単位で {len(diagrams)} 枚に分割します。[/yellow]"
    )

    outputs: list[tuple[str, Path, int]] = []
    for idx, diag in enumerate(diagrams, start=1):
        console.print(Rule(
            f"[bold cyan]図 {idx}/{len(diagrams)}  {diag.title}[/bold cyan]"
        ))
        sub_dir = args.output_dir / diag.key
        sub_stem = f"{args.input.stem}_{diag.key}"
        result = pipeline.run(
            topology_text=diag.text,
            output_dir=sub_dir,
            stem=sub_stem,
            fmt=args.format,
            max_iterations=args.max_iter,
            threshold=args.threshold,
            patience=args.patience,
        )
        # ベスト画像を出力ルートへ集約
        final_path = args.output_dir / f"{sub_stem}.{args.format}"
        shutil.copy2(result.best_image, final_path)
        outputs.append((diag.title, final_path, result.best_result.score))

    # ── 分割サマリー ──────────────────────────────────────────────
    summary = Table(title="分割生成サマリー", show_header=True, header_style="bold")
    summary.add_column("#", style="dim", width=4, justify="center")
    summary.add_column("図", overflow="fold")
    summary.add_column("スコア", width=8, justify="center")
    summary.add_column("出力ファイル", overflow="fold")
    for i, (title, path, score) in enumerate(outputs, start=1):
        color = "green" if score >= args.threshold else "yellow" if score >= 6 else "red"
        summary.add_row(str(i), title, f"[{color}]{score}/10[/{color}]", str(path))
    console.print()
    console.print(summary)
    console.print(Panel(
        f"生成枚数         : [bold]{len(outputs)}[/bold] 枚\n"
        f"出力ディレクトリ : [bold]{args.output_dir}[/bold]",
        title="[bold green]✓ 完了[/bold green]",
        expand=False,
    ))


if __name__ == "__main__":
    main()
