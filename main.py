#!/usr/bin/env python3
"""d2v: iida-network-model YAML → ネットワーク構成図（PNG / SVG）生成ツール。

サブコマンド:
  （なし）        d2v: YAML → 構成図（従来どおり `python main.py -i topology.yaml`）
  v2d            vision-to-diagram: 構成図画像 → iida-network-model YAML
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from d2v import parser, partitioner, pipeline, visualizer

console = Console()


def main() -> None:
    # v2d サブコマンドは専用ハンドラへ振り分ける（従来の d2v CLI は後方互換のまま維持）
    if len(sys.argv) > 1 and sys.argv[1] == "v2d":
        run_v2d(sys.argv[2:])
        return
    run_d2v()


def run_d2v() -> None:
    ap = argparse.ArgumentParser(
        prog="d2v",
        description="iida-network-model YAML からネットワーク構成図を生成します。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
使い方の例（examples/ に同梱のトポロジを利用）:
  # 小規模トポロジ（7 ノード）を生成
  python main.py -i examples/sample_topology_small.yaml

  # 中規模トポロジ（23 ノード）、最大 5 回まで改善
  python main.py -i examples/sample_topology_medium.yaml -n 5

  # 大規模トポロジ（73 ノード）、zone 単位で俯瞰図＋詳細図に自動分割
  python main.py -i examples/sample_topology_large.yaml

  # 自動分割を無効化して 1 枚で生成
  python main.py -i examples/sample_topology_large.yaml --no-split

  # spine-01 を中心に 2 ホップ以内のノードだけを集中図として抽出
  python main.py -i examples/sample_topology_large.yaml --focus spine-01 --hops 2

  # spine-01 と spine-02 の 2 台を中心に 1 ホップ以内（和集合）を抽出
  python main.py -i examples/sample_topology_large.yaml --focus spine-01 spine-02

  # 指定したゾーンだけを描画対象にする（複数指定でまとめて 1 枚）
  python main.py -i examples/sample_topology_large.yaml --zone dc-core dc-fabric

  # SVG で出力、合格スコア閾値を 9 点に設定
  python main.py -i examples/sample_topology_small.yaml -f svg -t 9

  # 画像からトポロジ YAML を生成（v2d サブコマンド）
  python main.py v2d -i images/sample_topology_small_best.png
""",
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
    ap.add_argument(
        "--focus",
        type=str,
        nargs="+",
        default=None,
        metavar="DEVICE_ID",
        help=(
            "注目ノード（device-id）を指定すると、そのノードから --hops ホップ以内の"
            "ノードだけを抜き出した集中図を 1 枚生成する（分割は行わない）。"
            "複数指定（例: --focus spine-01 spine-02）すると、いずれかのノードから"
            "到達できる範囲の和集合を 1 枚のサブグラフとして抽出する"
        ),
    )
    ap.add_argument(
        "--hops",
        type=int,
        default=1,
        metavar="N",
        help="--focus 指定時に注目ノードから何ホップ先まで含めるか（1 または 2 を推奨。デフォルト: 1）",
    )
    ap.add_argument(
        "--zone",
        type=str,
        nargs="+",
        default=None,
        metavar="ZONE",
        help=(
            "指定したゾーンだけを描画対象にした図を 1 枚生成する（分割は行わない）。"
            "複数指定（例: --zone dc-core dc-fabric）するとまとめて 1 枚に描画する。"
            "対象外ゾーンへ跨る接続は境界スタブとして表示される"
        ),
    )
    ap.add_argument(
        "--zone-opacity",
        type=float,
        default=0.4,
        metavar="0.0-1.0",
        help=(
            "ゾーン（cluster）背景色の不透明度。背景が濃いときに下げると淡くなる"
            "（1.0=不透明、例: 0.4でかなり淡く。デフォルト: 0.4）"
        ),
    )
    # 引数なしで実行された場合はエラーにせずヘルプを表示して終了する
    if len(sys.argv) == 1:
        ap.print_help()
        return
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

    # ── 注目ノード集中図モード / ゾーン限定モード ─────────────────
    if args.focus is not None and args.zone is not None:
        console.print(
            "\n[bold red]✗ --focus と --zone は同時に指定できません。"
            "どちらか一方を指定してください。[/bold red]\n"
        )
        sys.exit(1)
    if args.focus is not None:
        _run_focus(args, model)
        return
    if args.zone is not None:
        _run_zone(args, model)
        return

    # ── 分割計画の判定 ────────────────────────────────────────────
    diagrams = None if args.no_split else partitioner.plan(model, args.split_threshold)

    if diagrams is None:
        _run_single(args, topology_text)
    else:
        _run_split(args, diagrams)


def _run_zone(args: argparse.Namespace, model: "parser.TopologyModel") -> None:
    """指定したゾーンだけを描画対象にした図を 1 枚生成する。"""
    # 存在しないゾーンを検出して分かりやすくエラー表示する
    known = partitioner.available_zones(model)
    missing = [z for z in args.zone if z not in known]
    if missing:
        available = ", ".join(known) or "(なし)"
        console.print(
            f"\n[bold red]✗ ゾーン {', '.join(missing)} "
            "がトポロジに存在しません。[/bold red]\n"
            f"[dim]利用可能なゾーン: {available}[/dim]\n"
        )
        sys.exit(1)

    diagram = partitioner.zone_plan(model, args.zone)
    if diagram is None:
        console.print("\n[bold red]✗ ゾーン限定図を生成できませんでした。[/bold red]\n")
        sys.exit(1)

    console.print(Rule(f"[bold]Step 2  {diagram.title}[/bold]"))
    console.print(diagram.text)

    sub_stem = f"{args.input.stem}_{diagram.key}"
    result = pipeline.run(
        topology_text=diagram.text,
        output_dir=args.output_dir / diagram.key,
        stem=sub_stem,
        fmt=args.format,
        max_iterations=args.max_iter,
        threshold=args.threshold,
        patience=args.patience,
        zone_opacity=args.zone_opacity,
    )

    # ベスト画像を出力ルートへ集約
    final_path = args.output_dir / f"{sub_stem}.{args.format}"
    shutil.copy2(result.best_image, final_path)

    best = result.best_result
    score_color = (
        "green" if best.score >= args.threshold
        else "yellow" if best.score >= 6
        else "red"
    )
    console.print(Panel(
        f"最終スコア       : [{score_color}]{best.score}/10[/{score_color}]\n"
        f"イテレーション数 : {result.total_iterations}/{args.max_iter}\n"
        f"出力ファイル     : [bold]{final_path}[/bold]",
        title="[bold green]✓ 完了[/bold green]",
        expand=False,
    ))


def _run_focus(args: argparse.Namespace, model: "parser.TopologyModel") -> None:
    """注目ノード（1 台以上）から --hops ホップ以内の集中図を 1 枚生成する。"""
    if args.hops < 1:
        console.print("\n[bold red]✗ --hops は 1 以上を指定してください。[/bold red]\n")
        sys.exit(1)

    # 存在しない注目ノードを検出して分かりやすくエラー表示する
    missing = [fid for fid in args.focus if fid not in model.device_map]
    if missing:
        available = ", ".join(sorted(model.device_map)) or "(なし)"
        console.print(
            f"\n[bold red]✗ 注目ノード {', '.join(missing)} "
            "がトポロジに存在しません。[/bold red]\n"
            f"[dim]利用可能な device-id: {available}[/dim]\n"
        )
        sys.exit(1)

    diagram = partitioner.focus_plan(model, args.focus, args.hops)
    if diagram is None:
        console.print("\n[bold red]✗ 集中図を生成できませんでした。[/bold red]\n")
        sys.exit(1)

    console.print(Rule(f"[bold]Step 2  {diagram.title}[/bold]"))
    console.print(diagram.text)

    sub_stem = f"{args.input.stem}_{diagram.key}"
    result = pipeline.run(
        topology_text=diagram.text,
        output_dir=args.output_dir / diagram.key,
        stem=sub_stem,
        fmt=args.format,
        max_iterations=args.max_iter,
        threshold=args.threshold,
        patience=args.patience,
        zone_opacity=args.zone_opacity,
    )

    # ベスト画像を出力ルートへ集約
    final_path = args.output_dir / f"{sub_stem}.{args.format}"
    shutil.copy2(result.best_image, final_path)

    best = result.best_result
    score_color = (
        "green" if best.score >= args.threshold
        else "yellow" if best.score >= 6
        else "red"
    )
    console.print(Panel(
        f"最終スコア       : [{score_color}]{best.score}/10[/{score_color}]\n"
        f"イテレーション数 : {result.total_iterations}/{args.max_iter}\n"
        f"出力ファイル     : [bold]{final_path}[/bold]",
        title="[bold green]✓ 完了[/bold green]",
        expand=False,
    ))


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
        zone_opacity=args.zone_opacity,
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
            zone_opacity=args.zone_opacity,
            # 俯瞰図はゾーン単位の全体地図用プロンプトを使う（個別デバイスを展開しない）
            system_prompt_file=(
                "diagram-system-overview.md" if diag.key == "overview"
                else "diagram-system.md"
            ),
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


# ---------------------------------------------------------------------------
# v2d サブコマンド（vision-to-diagram: 画像 → YAML）
# ---------------------------------------------------------------------------


def run_v2d(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        prog="d2v v2d",
        description="ネットワーク構成図の画像から iida-network-model YAML を生成します。",
    )
    ap.add_argument(
        "--input", "-i",
        required=True,
        type=Path,
        metavar="IMAGE",
        help="入力画像ファイル（PNG / JPEG）",
    )
    ap.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path("output/v2d"),
        metavar="DIR",
        help="出力ディレクトリ（デフォルト: output/v2d）",
    )
    ap.add_argument(
        "--truth", "-t",
        type=Path,
        default=None,
        metavar="YAML",
        help="正解トポロジ YAML（指定すると抽出精度を計測する）",
    )
    ap.add_argument(
        "--rerender",
        action="store_true",
        help="生成した YAML を d2v で再描画し往復ループを閉じる（LLM を使用）",
    )
    ap.add_argument(
        "--format", "-f",
        choices=["png", "svg"],
        default="png",
        help="再描画時の出力フォーマット（デフォルト: png）",
    )
    args = ap.parse_args(argv)

    # 遅延インポート（画像処理系の依存を v2d 実行時のみ読み込む）
    from d2v.v2d import evaluate as v2d_evaluate
    from d2v.v2d import pipeline as v2d_pipeline
    from d2v.v2d.extractor import ExtractionError
    from d2v.v2d.preprocess import ImagePreprocessError

    console.print(Panel(
        f"入力画像         : [bold cyan]{args.input}[/bold cyan]\n"
        f"出力ディレクトリ : [bold cyan]{args.output_dir}[/bold cyan]\n"
        f"精度計測         : "
        + (f"[bold cyan]{args.truth}[/bold cyan]" if args.truth else "[dim]なし[/dim]")
        + "\n再描画           : "
        + ("[bold cyan]あり[/bold cyan]" if args.rerender else "[dim]なし[/dim]"),
        title="[bold blue]v2d  画像 → トポロジ YAML 変換[/bold blue]",
        expand=False,
    ))

    # ── 抽出 → 補正 → YAML 出力 ──────────────────────────────────
    console.print(Rule("[bold]Step 1  画像解析 → YAML 生成[/bold]"))
    console.print("  [dim]vision LLM で構造抽出中...[/dim]")
    try:
        result = v2d_pipeline.run(args.input, args.output_dir)
    except ImagePreprocessError as e:
        console.print(f"\n[bold red]✗ 入力画像エラー:[/bold red] {e}\n")
        sys.exit(1)
    except ExtractionError as e:
        console.print(f"\n[bold red]✗ 抽出エラー:[/bold red] {e}\n")
        sys.exit(1)

    # ── 抽出サマリー ──────────────────────────────────────────────
    conf_color = (
        "green" if result.confidence >= 0.8
        else "yellow" if result.confidence >= 0.5
        else "red"
    )
    console.print(Panel(
        f"ノード数         : [bold]{result.node_count}[/bold]\n"
        f"エッジ数         : [bold]{result.edge_count}[/bold]\n"
        f"ゾーン数         : [bold]{result.cluster_count}[/bold]\n"
        f"総合確信度       : [{conf_color}]{result.confidence:.2f}[/{conf_color}]\n"
        f"YAML             : [bold]{result.yaml_path}[/bold]\n"
        f"サイドカー       : [bold]{result.sidecar_path}[/bold]",
        title="[bold green]✓ 抽出完了[/bold green]",
        expand=False,
    ))
    for note in result.diagram.notes[:8]:
        console.print(f"  [dim]· {note}[/dim]")

    # ── 精度計測（正解 YAML があれば） ───────────────────────────
    if args.truth:
        console.print(Rule("[bold]Step 2  抽出精度の計測[/bold]"))
        metrics = v2d_evaluate.evaluate_files(result.yaml_path, args.truth)
        console.print(metrics.summary())

    # ── 再描画（d2v 往復） ────────────────────────────────────────
    if args.rerender:
        console.print(Rule("[bold]Step 3  d2v で再描画（往復ループ）[/bold]"))
        rr = v2d_evaluate.rerender_with_d2v(
            result.yaml_path,
            args.output_dir / "rerender",
            fmt=args.format,
        )
        console.print(Panel(
            f"再描画画像       : [bold]{rr.best_image}[/bold]\n"
            f"再描画スコア     : [bold]{rr.best_result.score}/10[/bold]",
            title="[bold green]✓ 再描画完了[/bold green]",
            expand=False,
        ))


if __name__ == "__main__":
    main()
