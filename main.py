#!/usr/bin/env python3
"""d2v: iida-network-model YAML → ネットワーク構成図（PNG / SVG）生成ツール。

サブコマンド:
  （なし）        d2v: YAML → 構成図（従来どおり `python main.py -i topology.yaml`）
  v2d            vision-to-diagram: 構成図画像 → iida-network-model YAML
  validate       セマンティック検証（design lint）: 設計上の問題を検出
  diff           2 つのトポロジの意味的 diff ＋ 差分図
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from d2v import partitioner, visualizer
from d2v.errors import D2VError
from d2v.progress import ProgressEvent
from d2v.web import service
from d2v.web.service import D2VJobError, D2VParams

console = Console()


def main() -> None:
    # v2d サブコマンドは専用ハンドラへ振り分ける（従来の d2v CLI は後方互換のまま維持）
    if len(sys.argv) > 1 and sys.argv[1] == "v2d":
        run_v2d(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        run_serve(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        run_validate(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "diff":
        run_diff(sys.argv[2:])
        return
    # ── [削除可能] 決定論的ジェネレータ（LLM 非依存）: src/d2v/detgen.py と
    #    この 4 行を消せば丸ごと撤去できる ─────────────────────────
    if len(sys.argv) > 1 and sys.argv[1] == "dot":
        from d2v.detgen import run_cli
        run_cli(sys.argv[2:])
        return
    # ──────────────────────────────────────────────────────────────
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

  # 指定したノードだけ（相互接続のみ）を描画する
  python main.py -i examples/sample_topology_large.yaml --focus spine-01 spine-02 --hops 0

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
            "到達できる範囲の和集合を 1 枚のサブグラフとして抽出する。"
            "--hops 0 を指定すると、指定したノードだけ（相互接続のみ）を描画する"
        ),
    )
    ap.add_argument(
        "--hops",
        type=int,
        default=1,
        metavar="N",
        help="--focus 指定時に注目ノードから何ホップ先まで含めるか（0 で指定ノードのみ。1 または 2 を推奨。デフォルト: 1）",
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
    ap.add_argument(
        "--precheck",
        action="store_true",
        help=(
            "作図前にセマンティック検証（design lint）を実行し、error があれば"
            "作図せず終了する（`validate` サブコマンド相当の事前チェック）"
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

    # ── 事前検証（--precheck）: error があれば作図せず終了 ──────────
    if args.precheck:
        from d2v import validator
        from d2v.parser import load_model

        console.print(Rule("[bold]事前検証（design lint）[/bold]"))
        try:
            model = load_model(args.input)
        except D2VError as e:
            console.print(f"\n[bold red]✗ {e}[/bold red]\n")
            sys.exit(1)
        report = validator.validate(model)
        console.print(validator.render_report(report))
        if report.counts.get("error", 0) > 0:
            console.print(
                "\n[bold red]✗ 設計上の error を検出したため作図を中止しました。"
                "[/bold red] [dim](--precheck)[/dim]\n"
            )
            sys.exit(1)
        console.print()

    # ── 生成ジョブ実行（service 経由。single/split/focus/zone を自動判別）──
    params = D2VParams(
        input_path=args.input,
        output_dir=args.output_dir,
        fmt=args.format,
        max_iter=args.max_iter,
        threshold=args.threshold,
        patience=args.patience,
        no_split=args.no_split,
        split_threshold=args.split_threshold,
        focus=args.focus,
        hops=args.hops,
        zone=args.zone,
        zone_opacity=args.zone_opacity,
    )
    try:
        result = service.run_d2v_job(params, progress=_cli_progress)
    except (D2VJobError, D2VError) as e:
        console.print(f"\n[bold red]✗ {e}[/bold red]\n")
        sys.exit(1)

    _print_job_summary(args, result)


def _cli_progress(event: ProgressEvent) -> None:
    """service が emit するジョブレベルの進捗を rich で表示する。

    パイプライン内の詳細（イテレーション・スコア等）は pipeline.run が自身の
    コンソールに出力するため、ここではジョブレベルのイベントのみを扱う。
    """
    if event.stage == "topology":
        console.print(Rule("[bold]Step 1  トポロジ解析[/bold]"))
        console.print(event.extra.get("text", ""))
    elif event.stage == "plan":
        mode = event.extra.get("mode")
        if mode == "single":
            console.print(Rule("[bold]Step 2  生成 → 評価 → 改善ループ[/bold]"))
        elif mode == "split":
            total = event.total or 0
            console.print(Rule(
                f"[bold]Step 2  分割生成（{total} 枚: 俯瞰図 + ゾーン詳細）[/bold]"
            ))
            console.print(
                f"  [yellow]ノード数がしきい値 {event.extra.get('split_threshold')} を超えたため、"
                f"zone 単位で {total} 枚に分割します。[/yellow]"
            )
    elif event.stage == "diagram_start":
        idx = (event.iteration or 0) + 1
        total = event.total or 0
        title = event.extra.get("title", "")
        if event.extra.get("mode") == "split":
            console.print(Rule(f"[bold cyan]図 {idx}/{total}  {title}[/bold cyan]"))
        else:
            console.print(Rule(f"[bold]Step 2  {title}[/bold]"))
        console.print(event.extra.get("text", ""))


def _print_job_summary(args: argparse.Namespace, result: "service.D2VJobResult") -> None:
    """ジョブ完了後の最終サマリーをモード別に表示する。"""
    if result.mode == "split":
        _print_split_summary(args, result)
    else:
        _print_single_summary(args, result)


def _print_single_summary(args: argparse.Namespace, result: "service.D2VJobResult") -> None:
    """single / focus / zone（1 枚）の最終サマリー。"""
    output = result.outputs[0]
    best = output.result.best_result
    score_color = (
        "green" if best.score >= args.threshold
        else "yellow" if best.score >= 6
        else "red"
    )
    # スコア推移グラフの生成（複数イテレーションがあった場合のみ）
    plot_path: Path | None = None
    if len(output.result.records) > 1:
        plot_path = visualizer.plot_score_history(
            output.result.records,
            args.output_dir / "score_history.png",
            args.threshold,
        )

    console.print(Panel(
        f"最終スコア       : [{score_color}]{best.score}/10[/{score_color}]\n"
        f"イテレーション数 : {output.result.total_iterations}/{args.max_iter}\n"
        f"出力ファイル     : [bold]{output.final_image}[/bold]"
        + (f"\nスコアグラフ     : [bold]{plot_path}[/bold]" if plot_path else ""),
        title="[bold green]✓ 完了[/bold green]",
        expand=False,
    ))


def _print_split_summary(args: argparse.Namespace, result: "service.D2VJobResult") -> None:
    """分割生成（複数枚）の最終サマリー。"""
    summary = Table(title="分割生成サマリー", show_header=True, header_style="bold")
    summary.add_column("#", style="dim", width=4, justify="center")
    summary.add_column("図", overflow="fold")
    summary.add_column("スコア", width=8, justify="center")
    summary.add_column("出力ファイル", overflow="fold")
    for i, output in enumerate(result.outputs, start=1):
        score = output.score
        color = "green" if score >= args.threshold else "yellow" if score >= 6 else "red"
        summary.add_row(
            str(i), output.title, f"[{color}]{score}/10[/{color}]", str(output.final_image)
        )
    console.print()
    console.print(summary)
    console.print(Panel(
        f"生成枚数         : [bold]{len(result.outputs)}[/bold] 枚\n"
        f"出力ディレクトリ : [bold]{args.output_dir}[/bold]",
        title="[bold green]✓ 完了[/bold green]",
        expand=False,
    ))


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
    except D2VError as e:
        console.print(f"\n[bold red]✗ {e}[/bold red]\n")
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


# ---------------------------------------------------------------------------
# validate サブコマンド（セマンティック検証 / design lint）
# ---------------------------------------------------------------------------


def run_validate(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        prog="d2v validate",
        description="iida-network-model YAML の設計上の問題を検出します（design lint）。",
    )
    ap.add_argument(
        "--input", "-i",
        required=True,
        type=Path,
        metavar="TOPOLOGY_YAML",
        help="検証対象のトポロジ YAML",
    )
    ap.add_argument(
        "--policy",
        type=Path,
        default=None,
        metavar="POLICY_YAML",
        help="ポリシーファイル（zone-transit / zone-redundancy）を追加検証する",
    )
    ap.add_argument(
        "--explain",
        action="store_true",
        help="検出した各 issue に LLM で理由・修正案を付与する（LLM を使用）",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="結果を JSON で出力する（機械可読・CI 連携用）",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="warning も不合格（終了コード 1）として扱う",
    )
    args = ap.parse_args(argv)

    from d2v import validator
    from d2v.parser import load_model

    try:
        model = load_model(args.input)
        policies = validator.load_policies(args.policy) if args.policy else None
    except D2VError as e:
        console.print(f"\n[bold red]✗ {e}[/bold red]\n")
        sys.exit(1)

    report = validator.validate(model, policies=policies)

    if args.explain and report.issues:
        try:
            report = validator.explain(report, model)
        except D2VError as e:
            console.print(f"[yellow]説明の生成に失敗しました（検出結果のみ表示）: {e}[/yellow]")

    if args.json:
        console.print_json(report.model_dump_json())
    else:
        console.print(Panel(
            f"入力ファイル : [bold cyan]{args.input}[/bold cyan]"
            + (f"\nポリシー     : [bold cyan]{args.policy}[/bold cyan]" if args.policy else ""),
            title="[bold blue]d2v  セマンティック検証（design lint）[/bold blue]",
            expand=False,
        ))
        console.print(validator.render_report(report))

    sys.exit(0 if report.passed(strict=args.strict) else 1)


# ---------------------------------------------------------------------------
# diff サブコマンド（意味的 diff + 差分図）
# ---------------------------------------------------------------------------


def run_diff(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        prog="d2v diff",
        description="2 つのトポロジ YAML の構造差分を検出し、差分図を生成します。",
    )
    ap.add_argument("--before", "-b", required=True, type=Path, metavar="BEFORE_YAML",
                    help="変更前のトポロジ YAML")
    ap.add_argument("--after", "-a", required=True, type=Path, metavar="AFTER_YAML",
                    help="変更後のトポロジ YAML")
    ap.add_argument("--output-dir", "-o", type=Path, default=Path("output/diff"),
                    metavar="DIR", help="差分図の出力先（デフォルト: output/diff）")
    ap.add_argument("--summarize", action="store_true",
                    help="差分を LLM で自然言語要約する（LLM を使用）")
    ap.add_argument("--format", "-f", choices=["png", "svg"], default="png",
                    help="差分図の出力フォーマット（デフォルト: png）")
    ap.add_argument("--no-image", action="store_true",
                    help="差分図を生成せず、構造差分のみ出力する")
    ap.add_argument("--json", action="store_true",
                    help="構造差分を JSON で出力する")
    ap.add_argument("--exit-zero", action="store_true",
                    help="差分があっても終了コード 0 を返す（既定は差分ありで 1）")
    args = ap.parse_args(argv)

    from d2v import diff as diff_mod
    from d2v.parser import load_model

    try:
        before = load_model(args.before)
        after = load_model(args.after)
    except D2VError as e:
        console.print(f"\n[bold red]✗ {e}[/bold red]\n")
        sys.exit(1)

    topo_diff = diff_mod.compare(before, after)

    if args.summarize and not topo_diff.is_empty():
        try:
            topo_diff = diff_mod.summarize(topo_diff)
        except D2VError as e:
            console.print(f"[yellow]要約の生成に失敗しました（差分のみ表示）: {e}[/yellow]")

    if args.json:
        console.print_json(topo_diff.model_dump_json())
    else:
        console.print(Panel(
            f"変更前 : [bold cyan]{args.before}[/bold cyan]\n"
            f"変更後 : [bold cyan]{args.after}[/bold cyan]",
            title="[bold blue]d2v  トポロジ差分（意味的 diff）[/bold blue]",
            expand=False,
        ))
        console.print(diff_mod.render_diff(topo_diff))

    # 差分図の生成
    if not args.no_image and not topo_diff.is_empty():
        try:
            image = diff_mod.render_diff_diagram(
                before, after, topo_diff, args.output_dir,
                stem=f"{args.before.stem}__{args.after.stem}", fmt=args.format,
            )
            console.print(Panel(
                f"差分図 : [bold]{image}[/bold]",
                title="[bold green]✓ 差分図を生成しました[/bold green]",
                expand=False,
            ))
        except D2VError as e:
            console.print(f"[yellow]差分図の生成に失敗しました: {e}[/yellow]")

    # 終了コード: 差分ありで 1（CI の変更検知用）。--exit-zero で常に 0。
    if args.exit_zero or topo_diff.is_empty():
        sys.exit(0)
    sys.exit(1)


# ---------------------------------------------------------------------------
# serve サブコマンド（ブラウザ GUI: FastAPI + Uvicorn 起動）
# ---------------------------------------------------------------------------


def run_serve(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        prog="d2v serve",
        description="ブラウザ GUI（FastAPI）を起動します。",
    )
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        metavar="HOST",
        help="バインドするホスト（デフォルト: 127.0.0.1）。外部公開は自己責任で",
    )
    ap.add_argument(
        "--port", "-p",
        type=int,
        default=8000,
        metavar="PORT",
        help="待ち受けポート（デフォルト: 8000）",
    )
    ap.add_argument(
        "--reload",
        action="store_true",
        help="コード変更を検知して自動リロード（開発用）",
    )
    args = ap.parse_args(argv)

    try:
        import uvicorn
    except ModuleNotFoundError:
        console.print(
            "\n[bold red]✗ GUI に必要な依存がインストールされていません。[/bold red]\n"
            "[dim]次のいずれかを実行してください:[/dim]\n"
            "  [bold]uv sync --extra web[/bold]   [dim]（uv の場合）[/dim]\n"
            "  [bold]pip install -e '.[web]'[/bold]   [dim]（pip の場合）[/dim]\n"
        )
        sys.exit(1)

    url = f"http://{args.host}:{args.port}"
    console.print(Panel(
        f"URL              : [bold cyan]{url}[/bold cyan]\n"
        f"ホスト           : [bold cyan]{args.host}[/bold cyan]\n"
        f"ポート           : [bold cyan]{args.port}[/bold cyan]\n"
        f"自動リロード     : "
        + ("[bold cyan]あり[/bold cyan]" if args.reload else "[dim]なし[/dim]"),
        title="[bold blue]d2v  ブラウザ GUI[/bold blue]",
        expand=False,
    ))
    console.print("  [dim]停止するには Ctrl+C[/dim]\n")

    uvicorn.run(
        "d2v.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
