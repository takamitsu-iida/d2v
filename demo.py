#!/usr/bin/env -S uv run python3
"""d2v デモランナー。

使い方:
    uv run python demo.py              # シーン 1〜5 を順に playback
    uv run python demo.py --scene 1    # 指定シーンのみ
    uv run python demo.py --live       # シーン 1 を live LLM で実行
    uv run python demo.py --open       # 生成画像を自動で表示（xdg-open）
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEMO_DIR = ROOT / "demo"
CACHE_DIR = DEMO_DIR / "cache"
FIXTURES_DIR = DEMO_DIR / "fixtures"
EXAMPLES_DIR = ROOT / "examples"

sys.path.insert(0, str(ROOT / "src"))

from rich.columns import Columns
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console()

# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------

def _open(path: Path) -> None:
    """OS のビューアで開く。xdg-open がなければパスを表示するだけ。"""
    if not path.exists():
        console.print(f"  [yellow]⚠ ファイルが見つかりません: {path}[/yellow]")
        return
    try:
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        console.print(f"  [dim]→ 画像を開きました: {path.relative_to(ROOT)}[/dim]")
    except FileNotFoundError:
        console.print(f"  [dim]出力ファイル: {path.relative_to(ROOT)}[/dim]")


def _progress_bar(label: str, seconds: float = 1.4, width: int = 20) -> None:
    """playback 演出用のアニメーション進捗バー。"""
    steps = 20
    interval = seconds / steps
    with Progress(
        TextColumn(f"  {label}"),
        BarColumn(bar_width=width, complete_style="cyan", finished_style="green"),
        TextColumn("{task.percentage:>3.0f}%"),
        console=console,
        transient=False,
    ) as prog:
        task = prog.add_task("", total=steps)
        for _ in range(steps):
            time.sleep(interval)
            prog.advance(task, 1)


def _score_panel(score: int, threshold: int, passed: bool) -> Panel:
    color = "bold green" if passed else ("bold yellow" if score >= 7 else "bold red")
    mark = "✓  閾値到達！" if passed else ("改善中..." if score >= 7 else "要改善")
    t = Text(justify="center")
    t.append(f"  {score}", style=color)
    t.append("  /  10    ", style="dim")
    t.append(mark, style=color)
    return Panel(t, expand=False, border_style=color.replace("bold ", ""))


def _cache_check(scene: str) -> bool:
    """キャッシュが存在するか確認し、なければ警告を出す。"""
    path = CACHE_DIR / scene
    if not path.exists():
        console.print(
            f"  [red]✗ キャッシュ {scene}/ が見つかりません。"
            "先に prep_cache.py を実行してください:[/red]\n"
            f"    uv run python demo/prep_cache.py"
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Scene 1: YAML → 構成図（コアマジック）
# ---------------------------------------------------------------------------

def scene1_core_magic(*, live: bool = False, open_images: bool = False) -> None:
    console.print(Panel(
        "[bold]Act 1[/bold]   [cyan]YAML → 構成図[/cyan]\n\n"
        "人が書いたトポロジ定義から、LLM が Graphviz DOT コードを生成し、\n"
        "評価・改善を繰り返して最高品質の構成図を出力します。",
        title="[bold blue]d2v  ネットワーク構成図ジェネレーター[/bold blue]",
        expand=False,
    ))
    time.sleep(0.8)

    if live:
        _scene1_live(open_images)
    else:
        _scene1_playback(open_images)


def _scene1_live(open_images: bool) -> None:
    """Live モード: main.py を subprocess で実行（本物の LLM 呼び出し）。"""
    console.print(
        Panel(
            f"  入力ファイル : [bold cyan]{EXAMPLES_DIR / 'sample_topology_small.yaml'}[/bold cyan]\n"
            "  モード       : [bold green]LIVE（実際の LLM を使用）[/bold green]",
            expand=False,
        )
    )
    cmd = [
        sys.executable, str(ROOT / "main.py"),
        "-i", str(EXAMPLES_DIR / "sample_topology_small.yaml"),
        "-n", "3", "-t", "10",
    ]
    subprocess.run(cmd, check=False)

    if open_images:
        out_dir = ROOT / "output" / "sample_topology_small"
        candidates = list(out_dir.glob("*_best.png")) + list(out_dir.glob("*.png"))
        if candidates:
            _open(candidates[0])


def _scene1_playback(open_images: bool) -> None:
    """Playback モード: demo/cache/scene1_small/ から事前生成結果を再生。"""
    if not _cache_check("scene1_small"):
        return

    scores_path = CACHE_DIR / "scene1_small" / "scores.json"
    scores: list[dict] = json.loads(scores_path.read_text(encoding="utf-8"))
    total = len(scores)
    threshold = 10  # prep_cache と揃える

    # 入力トポロジ情報
    console.print(Panel(
        "  入力ファイル : [bold cyan]examples/sample_topology_small.yaml[/bold cyan]\n"
        "  規模         : 7 ノード / 4 ゾーン / 8 リンク\n"
        "  モード       : [dim]playback（事前生成済み）[/dim]",
        expand=False,
    ))
    time.sleep(0.6)

    best_iter = max(scores, key=lambda s: s["score"])

    for entry in scores:
        i, score, passed = entry["iter"], entry["score"], entry["passed"]
        label = "Generating..." if i == 0 else "Improving... "

        console.print(f"\n  [bold cyan]── Iteration {i + 1} / {total} ──[/bold cyan]")
        _progress_bar(f"[1/3] {label}", seconds=1.5)
        _progress_bar("[2/3] Rendering... ", seconds=0.6)
        _progress_bar("[3/3] Evaluating...", seconds=1.2)

        console.print(_score_panel(score, threshold, passed))
        time.sleep(0.3)

        # 改善指摘を 2 件まで表示
        for issue in entry.get("issues", [])[:2]:
            short = issue[:72] + ("…" if len(issue) > 72 else "")
            console.print(f"    [dim]· {escape(short)}[/dim]")

        time.sleep(0.4)

    # ベストイテレーションとベスト画像
    best_img = CACHE_DIR / "scene1_small" / "best.png"
    console.print()
    console.print(Panel(
        f"  ベスト結果   : [bold green]Iteration {best_iter['iter'] + 1}"
        f"  スコア {best_iter['score']} / 10[/bold green]\n"
        f"  出力画像     : [bold]{best_img.relative_to(ROOT)}[/bold]",
        title="[bold green]✓ 生成完了[/bold green]",
        expand=False,
    ))

    if open_images:
        _open(best_img)


# ---------------------------------------------------------------------------
# Scene 2: スケール（73 ノード自動分割）
# ---------------------------------------------------------------------------

_LARGE_ZONES = [
    ("zone-wan-edge",     "WAN Edge"),
    ("zone-security",     "Security"),
    ("zone-dc-core",      "DC Core"),
    ("zone-dc-fabric",    "DC Fabric (Leaf/Spine)"),
    ("zone-dc-server",    "DC Server"),
    ("zone-dmz",          "DMZ"),
    ("zone-campus-bldg-a","Campus 棟A"),
    ("zone-campus-bldg-b","Campus 棟B"),
    ("zone-campus-bldg-c","Campus 棟C"),
    ("zone-management",   "Management"),
]


def scene2_scale(*, open_images: bool = False) -> None:
    if not _cache_check("scene2_large"):
        return

    console.print(Panel(
        "[bold]Act 2[/bold]   [cyan]エンタープライズスケール[/cyan]\n\n"
        "73 ノード / 10 ゾーンのトポロジを zone 単位で自動分割し、\n"
        "俯瞰図 + ゾーン詳細図 11 枚を並列生成します。",
        expand=False,
    ))
    time.sleep(0.8)

    console.print(Panel(
        "  入力ファイル    : [bold cyan]examples/sample_topology_large.yaml[/bold cyan]\n"
        "  規模            : 73 ノード / 10 ゾーン / 82 リンク",
        expand=False,
    ))
    time.sleep(0.5)

    # ── Phase 1: 閾値検出 ──
    console.print()
    _progress_bar("  トポロジを解析中...", seconds=0.6)
    console.print()
    console.print(Panel(
        "  ノード数 [bold red]73[/bold red] が分割閾値 [bold]40[/bold] を超えました\n"
        "  → [yellow]自動分割モード[/yellow]  俯瞰図 1 枚 ＋ ゾーン詳細 10 枚を[bold]並列生成[/bold]します",
        title="[bold yellow]⚡ 自動分割モード[/bold yellow]",
        border_style="yellow",
        expand=False,
    ))
    time.sleep(0.8)

    # ── Phase 2: 並列生成（全バーを同時に進める）──
    cache2 = CACHE_DIR / "scene2_large"
    all_items = [("overview", "俯瞰図 (overview)")] + _LARGE_ZONES
    steps = 12
    console.print()
    with Progress(
        TextColumn("  {task.description}"),
        BarColumn(bar_width=16, complete_style="cyan", finished_style="green"),
        TextColumn("{task.percentage:>3.0f}%"),
        console=console,
        transient=False,
    ) as prog:
        task_ids = [
            prog.add_task(f"{label:<28}", total=steps)
            for _, label in all_items
        ]
        for step in range(steps):
            for tid in task_ids:
                prog.advance(tid, 1)
            time.sleep(0.10)

    console.print()
    console.print(Panel(
        f"  合計 [bold green]{len(all_items)} 枚[/bold green]を生成しました\n"
        "  俯瞰図 1 枚  ＋  ゾーン詳細 10 枚",
        title="[bold green]✓ 並列生成完了[/bold green]",
        expand=False,
    ))
    time.sleep(0.6)

    # ── Phase 3: focus drilldown ──
    console.print()
    console.print(Rule("[dim]フォーカス（drilldown）[/dim]", style="dim"))
    console.print()
    console.print(
        "  [dim]さらに特定ノード周辺だけを抽出して拡大表示できます:[/dim]"
    )
    time.sleep(0.4)

    focus_items = [
        ("focus-spine-01-1hop.png",          "spine-01  の近傍 1-hop"),
        ("focus-spine-01-spine-02-1hop.png",  "spine-01 + spine-02  の近傍 1-hop"),
    ]
    for fname, label in focus_items:
        img = cache2 / fname
        exists_mark = "[green]✓[/green]" if img.exists() else "[dim]—[/dim]"
        _progress_bar(f"  {label:<38}", seconds=0.3)
        t = Text()
        t.append(f"  ")
        t.append("✓  " if img.exists() else "—  ", style="green" if img.exists() else "dim")
        t.append(label, style="bold")
        t.append("  →  ", style="dim")
        t.append(str(img.relative_to(ROOT)), style="dim")
        t.truncate(console.width - 2, overflow="ellipsis")
        console.print(t)
        time.sleep(0.15)

    if open_images:
        _open(cache2 / "overview.png")
        time.sleep(0.4)
        _open(cache2 / "zone-dc-fabric.png")
        time.sleep(0.4)
        _open(cache2 / "focus-spine-01-1hop.png")


# ---------------------------------------------------------------------------
# Scene 3: v2d 逆変換（構成図 → YAML）
# ---------------------------------------------------------------------------

def scene3_v2d(*, open_images: bool = False) -> None:
    if not _cache_check("scene3_v2d"):
        return

    cache3 = CACHE_DIR / "scene3_v2d"
    sidecar = json.loads((cache3 / "output.v2d.json").read_text(encoding="utf-8"))
    yaml_text = (cache3 / "output.yaml").read_text(encoding="utf-8")

    console.print(Panel(
        "[bold]Act 3[/bold]   [cyan]v2d — 構成図 → YAML 逆変換[/cyan]\n\n"
        "既存の構成図画像を渡すと、iida-network-model YAML を自動復元します。\n"
        "ホワイトボード写真・Visio PNG・スクリーンショットも対象です。",
        expand=False,
    ))
    time.sleep(0.8)

    console.print(Panel(
        f"  入力画像 : [bold cyan]{(cache3 / 'input.png').relative_to(ROOT)}[/bold cyan]\n"
        f"  モード   : [dim]playback[/dim]",
        expand=False,
    ))
    time.sleep(0.5)

    console.print()
    _progress_bar("  画像を解析中（vision LLM）...", seconds=2.0)
    _progress_bar("  YAML を生成中...             ", seconds=1.2)

    # YAML の先頭 25 行をタイプライター風に表示
    console.print()
    console.print("  [bold]── 復元 YAML（抜粋）" + "─" * 40 + "[/bold]")
    yaml_lines = yaml_text.splitlines()[:25]
    for line in yaml_lines:
        console.print(f"  {line}")
        time.sleep(0.04)
    console.print("  [dim]  ... （以降省略）[/dim]")
    console.print("  [bold]" + "─" * 58 + "[/bold]")
    time.sleep(0.4)

    counts = sidecar.get("counts", {})
    conf = sidecar.get("overall_confidence", 0.0)
    conf_color = "green" if conf >= 0.8 else "yellow" if conf >= 0.5 else "red"
    console.print()
    console.print(Panel(
        f"  ノード数  : [bold]{counts.get('nodes', '?')}[/bold]\n"
        f"  リンク数  : [bold]{counts.get('edges', '?')}[/bold]\n"
        f"  ゾーン数  : [bold]{counts.get('clusters', '?')}[/bold]\n"
        f"  総合確信度: [{conf_color}]{conf:.2f}[/{conf_color}]",
        title="[bold green]✓ 抽出完了[/bold green]",
        expand=False,
    ))

    if open_images:
        _open(cache3 / "input.png")


# ---------------------------------------------------------------------------
# Scene 4: validate（設計バグ発見）
# ---------------------------------------------------------------------------

_SEVERITY_STYLE = {"error": "bold red", "warning": "yellow", "info": "cyan"}
_SEVERITY_ICON  = {"error": "❌", "warning": "⚠", "info": "ℹ"}

# 80 カラム端末でも折り返さない短縮メッセージ
_RULE_SHORT = {
    "ip-address-overlap":    "IP アドレス重複",
    "iface-subnet-mismatch": "サブネット不一致",
    "isolated-device":       "孤立ノード",
    "spof-device":           "単一障害点（停止で分断）",
    "spof-bridge-link":      "橋リンク（切断で分断）",
}


def scene4_validate(*, open_images: bool = False) -> None:
    if not _cache_check("scene5_validate"):
        return

    report = json.loads(
        (CACHE_DIR / "scene5_validate" / "report.json").read_text(encoding="utf-8")
    )
    issues = report.get("issues", [])

    console.print(Panel(
        "[bold]Act 4-1[/bold]   [cyan]validate — 設計バグを AI が指摘[/cyan]\n\n"
        "トポロジ YAML を渡すだけで、単一障害点・IP 矛盾・孤立ノードなど\n"
        "設計上の問題を決定論的に検出します（LLM 非依存）。",
        expand=False,
    ))
    time.sleep(0.8)

    console.print(Panel(
        f"  入力ファイル : [bold cyan]{(FIXTURES_DIR / 'flawed.yaml').relative_to(ROOT)}[/bold cyan]\n"
        "  内容         : IP 重複・孤立デバイス・SPOF を意図的に含む設計",
        expand=False,
    ))
    time.sleep(0.6)

    console.print()
    _progress_bar("  [1/3] トポロジ読み込み...  ", seconds=0.4)
    _progress_bar("  [2/3] グラフ構築中...      ", seconds=0.5)
    _progress_bar("  [3/3] セマンティック検証...", seconds=1.0)
    console.print()

    # issues を 1 件ずつ表示（targets で対象デバイスを明示）
    bridge_total = sum(1 for i in issues if i["rule"] == "spof-bridge-link")
    seen_bridge = False

    for issue in issues:
        rule    = issue["rule"]
        sev     = issue.get("severity", "warning")
        msg     = issue["message"]
        targets = issue.get("targets", [])
        icon    = _SEVERITY_ICON.get(sev, "·")
        sty     = _SEVERITY_STYLE.get(sev, "")

        if rule == "spof-bridge-link":
            if seen_bridge:
                continue
            seen_bridge = True

        # インターフェース名を除去して短いデバイス名に
        cleaned = [re.sub(r"\[.*\]", "", t) for t in targets[:2]]
        if rule == "spof-bridge-link":
            target_str = f"{bridge_total} links"
        elif len(cleaned) == 2:
            target_str = f"{cleaned[0]}/{cleaned[1]}"
        else:
            target_str = cleaned[0] if cleaned else ""

        short_msg = _RULE_SHORT.get(rule, msg[:28] + ("…" if len(msg) > 28 else ""))

        t = Text()
        t.append(f"  {icon}  ", style=sty)
        t.append(f"{sev.upper():<7}", style=sty)
        t.append("  ")
        t.append(f"{rule:<22}", style="bold")
        t.append("  ")
        t.append(f"{target_str:<14}", style="cyan")
        t.append("  ")
        t.append(short_msg, style="dim")
        t.truncate(console.width - 2, overflow="ellipsis")
        console.print(t)
        time.sleep(0.42)

    # 設計評価サマリ（固定文言）
    time.sleep(0.5)
    console.print()
    console.print(Panel(
        "[italic dim]"
        "「この設計は可用性に深刻な問題を抱えています。\n"
        "  fw-01・core-sw-01・office-sw-01 が単一障害点であり、\n"
        "  いずれかが停止するとネットワークが分断されます。\n"
        "  IP アドレス 10.1.1.1 の重複も検出されています。\n"
        "  本番投入前に冗長構成と IP 設計の見直しが必要です。」[/italic dim]",
        title="[bold yellow]⚡ 設計評価サマリ[/bold yellow]",
        border_style="yellow",
        expand=False,
    ))
    time.sleep(0.3)

    counts = report.get("counts", {})
    console.print()
    console.print(Panel(
        f"  結果 : [bold red]NG[/bold red]  /  "
        f"error [bold red]{counts.get('error', 0)}[/bold red] 件  "
        f"warning [bold yellow]{counts.get('warning', 0)}[/bold yellow] 件",
        title="[bold red]✗ 設計上の問題を検出[/bold red]" if not report.get("ok")
              else "[bold green]✓ 問題なし[/bold green]",
        border_style="red" if not report.get("ok") else "green",
        expand=False,
    ))


# ---------------------------------------------------------------------------
# Scene 5: diff（障害影響範囲の可視化）
# ---------------------------------------------------------------------------

def scene5_diff(*, open_images: bool = False) -> None:
    if not _cache_check("scene4_diff"):
        return

    summary = json.loads(
        (CACHE_DIR / "scene4_diff" / "summary.json").read_text(encoding="utf-8")
    )
    diff_img = CACHE_DIR / "scene4_diff" / "diff.png"

    console.print(Panel(
        "[bold]Act 4-2[/bold]   [cyan]diff — 障害影響範囲（Blast Radius）[/cyan]\n\n"
        "変更前後の YAML を比較し、構造差分を検出。\n"
        "さらに機器/リンク除去による到達不能範囲を算出します（決定論的）。",
        expand=False,
    ))
    time.sleep(0.8)

    before_rel = (FIXTURES_DIR / "before.yaml").relative_to(ROOT)
    after_rel  = (FIXTURES_DIR / "after.yaml").relative_to(ROOT)
    console.print(Panel(
        f"  変更前 : [bold cyan]{before_rel}[/bold cyan]  （12 ノード・正常稼働）\n"
        f"  変更後 : [bold cyan]{after_rel}[/bold cyan]  （spine-01 障害停止）",
        expand=False,
    ))
    time.sleep(0.6)

    console.print()
    _progress_bar("  [1/2] 構造差分を検出中...", seconds=0.8)

    nodes_removed = summary.get("nodes_removed", [])
    edges_removed = summary.get("edges_removed", [])
    nodes_added   = summary.get("nodes_added", [])
    edges_added   = summary.get("edges_added", [])

    console.print()
    # 削除された要素を見やすく列挙
    if nodes_removed:
        console.print(f"  [dim]▶ 削除ノード[/dim]")
        for n in nodes_removed:
            console.print(f"    [bold red]✕  {n}[/bold red]")
            time.sleep(0.2)
    if edges_removed:
        console.print(f"  [dim]▶ 削除リンク[/dim]")
        for e in edges_removed:
            pretty = e.replace("__", "  ↔  ")
            console.print(f"    [red]─  {pretty}[/red]")
            time.sleep(0.2)
    if nodes_added or edges_added:
        for n in nodes_added:
            console.print(f"    [green]✚  {n}[/green]")
        for e in edges_added:
            console.print(f"    [green]+  {e.replace('__', '  ↔  ')}[/green]")
            time.sleep(0.15)

    console.print()
    _progress_bar("  [2/2] 影響範囲を算出中...", seconds=0.8)
    console.print()
    time.sleep(0.3)

    # Blast Radius パネル
    blast = summary.get("blast_radius", [])
    blast_count = summary.get("blast_radius_count", 0)

    blast_text = Text(justify="center")
    blast_text.append("\n  Blast Radius\n\n", style="bold")
    blast_text.append(f"  {blast_count} 台", style="bold red" if blast_count > 0 else "bold green")
    blast_text.append("  が到達不能\n\n", style="bold")
    for did in blast:
        blast_text.append(f"  · {did}\n", style="red")

    console.print(Panel(
        blast_text,
        title="[bold red]⚠  障害波及範囲[/bold red]",
        border_style="red" if blast_count > 0 else "green",
        expand=False,
    ))
    time.sleep(0.5)

    # 差分図
    console.print()
    console.print(Panel(
        f"  差分図 : [bold]{diff_img.relative_to(ROOT)}[/bold]\n"
        "  凡例   : [red]削除=赤破線[/red]  [green]追加=緑[/green]  橙=変更  灰=変更なし",
        title="[bold green]✓ 差分図を生成しました[/bold green]",
        expand=False,
    ))

    if open_images:
        _open(diff_img)


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

SCENES: dict[int, tuple[str, object]] = {
    1: ("YAML → 構成図（コアマジック）", scene1_core_magic),
    2: ("スケール（73 ノード自動分割）", scene2_scale),
    3: ("v2d 逆変換（構成図 → YAML）", scene3_v2d),
    4: ("validate（設計バグ発見）",     scene4_validate),
    5: ("diff（障害影響範囲）",          scene5_diff),
}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="d2v デモ（playback モード）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="シーン一覧:\n" + "\n".join(
            f"  {k}: {v[0]}" for k, v in SCENES.items()
        ),
    )
    ap.add_argument(
        "--scene", "-s", type=int, choices=list(SCENES), metavar="N",
        help="実行するシーン番号（省略時は 1〜5 を順に実行）",
    )
    ap.add_argument(
        "--live", action="store_true",
        help="シーン 1 を live LLM で実行する（main.py を呼び出す）",
    )
    ap.add_argument(
        "--open", action="store_true",
        help="各シーンの完了時に生成画像を xdg-open で自動表示する",
    )
    args = ap.parse_args()

    targets = {args.scene: SCENES[args.scene]} if args.scene else SCENES

    console.print()
    console.print(Rule("[bold blue]  d2v  デモ  [/bold blue]", style="blue"))
    console.print()

    for scene_num, (desc, fn) in targets.items():
        console.print()
        console.print(Rule(f"[bold white on blue]  Scene {scene_num}  {desc}  [/bold white on blue]"))
        console.print()

        kwargs: dict = {"open_images": args.open}
        if scene_num == 1:
            kwargs["live"] = args.live

        try:
            fn(**kwargs)  # type: ignore[call-arg]
        except Exception as e:
            console.print(f"\n  [bold red]✗ エラー: {e}[/bold red]")
            import traceback
            traceback.print_exc()

        time.sleep(1.0)

    console.print()
    console.print(Rule("[bold blue]  デモ終了  [/bold blue]", style="blue"))
    console.print()


if __name__ == "__main__":
    main()
