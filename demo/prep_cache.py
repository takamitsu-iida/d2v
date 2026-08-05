#!/usr/bin/env -S uv run python3
"""デモキャッシュの事前生成スクリプト。

実行方法:
    uv run python demo/prep_cache.py            # 未生成のシーンのみ実行
    uv run python demo/prep_cache.py --force    # 全シーンを強制再生成
    uv run python demo/prep_cache.py --scene 1  # シーン指定

シーン一覧:
    1 - small topology d2v 生成（LLM 必須）
    2 - large topology 画像コピー（images/ から。LLM 不要）
    3 - v2d 逆変換（LLM 必須）
    4 - diff 差分図生成（LLM 不要）
    5 - validate 設計検証（LLM 不要）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from pathlib import Path

# src/ を sys.path に追加（uv run 外から直接 python で実行した場合の保険）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DEMO_DIR = ROOT / "demo"
CACHE_DIR = DEMO_DIR / "cache"
FIXTURES_DIR = DEMO_DIR / "fixtures"
IMAGES_DIR = ROOT / "images"
EXAMPLES_DIR = ROOT / "examples"

from rich.console import Console
from rich.rule import Rule

console = Console()


# ---------------------------------------------------------------------------
# Scene 1: small topology → d2v 生成（LLM 必須）
# ---------------------------------------------------------------------------

def prep_scene1(force: bool) -> None:
    out = CACHE_DIR / "scene1_small"
    if not force and (out / "scores.json").exists():
        console.print("[dim]  scene1: スキップ（既に生成済み）[/dim]")
        return

    console.print("[bold cyan]  small topology を d2v で生成中...[/bold cyan]")
    out.mkdir(parents=True, exist_ok=True)

    from d2v.web.service import D2VParams, run_d2v_job

    tmp_out = ROOT / "output" / "_demo_scene1"
    params = D2VParams(
        input_path=EXAMPLES_DIR / "sample_topology_small.yaml",
        output_dir=tmp_out,
        fmt="png",
        max_iter=3,
        threshold=10,  # 最高点を狙わせ、複数 iteration を確実に走らせる
        patience=2,
        no_split=True,
    )
    result = run_d2v_job(params)
    pipe_result = result.outputs[0].result

    # イテレーション別画像 + スコア履歴
    scores = []
    for rec in pipe_result.records:
        shutil.copy(rec.image_path, out / f"iter_{rec.iteration:02d}.png")
        scores.append({
            "iter": rec.iteration,
            "score": rec.result.score,
            "passed": rec.result.passed,
            "issues": rec.result.issues[:3],  # 先頭 3 件のみ保存
        })

    shutil.copy(result.outputs[0].final_image, out / "best.png")
    (out / "scores.json").write_text(
        json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    score_summary = " → ".join(f"{s['score']}" for s in scores)
    console.print(
        f"  [green]✓ scene1 完了: {len(scores)} iter / スコア推移 {score_summary}[/green]"
    )


# ---------------------------------------------------------------------------
# Scene 2: large topology 画像コピー（images/ から。LLM 不要）
# ---------------------------------------------------------------------------

def prep_scene2(force: bool) -> None:
    out = CACHE_DIR / "scene2_large"
    if not force and (out / "overview.png").exists():
        console.print("[dim]  scene2: スキップ（既に生成済み）[/dim]")
        return

    console.print("[bold cyan]  large topology 画像を images/ からコピー中...[/bold cyan]")
    out.mkdir(parents=True, exist_ok=True)

    copies = {
        "overview.png": IMAGES_DIR / "sample_topology_large_overview.png",
        "zone-wan-edge.png": IMAGES_DIR / "sample_topology_large_zone-wan-edge.png",
        "zone-security.png": IMAGES_DIR / "sample_topology_large_zone-security.png",
        "zone-dc-core.png": IMAGES_DIR / "sample_topology_large_zone-dc-core.png",
        "zone-dc-fabric.png": IMAGES_DIR / "sample_topology_large_zone-dc-fabric.png",
        "zone-dc-server.png": IMAGES_DIR / "sample_topology_large_zone-dc-server.png",
        "zone-dmz.png": IMAGES_DIR / "sample_topology_large_zone-dmz.png",
        "zone-campus-bldg-a.png": IMAGES_DIR / "sample_topology_large_zone-campus-bldg-a.png",
        "zone-campus-bldg-b.png": IMAGES_DIR / "sample_topology_large_zone-campus-bldg-b.png",
        "zone-campus-bldg-c.png": IMAGES_DIR / "sample_topology_large_zone-campus-bldg-c.png",
        "zone-management.png": IMAGES_DIR / "sample_topology_large_zone-management.png",
        "focus-spine-01-1hop.png": IMAGES_DIR / "sample_topology_large_focus-spine-01-1hop.png",
        "focus-spine-01-spine-02-1hop.png": IMAGES_DIR / "sample_topology_large_focus-spine-01-spine-02-1hop.png",
    }

    copied, missing = 0, 0
    for dest_name, src_path in copies.items():
        if src_path.exists():
            shutil.copy(src_path, out / dest_name)
            copied += 1
        else:
            console.print(f"    [yellow]⚠ {src_path.name} が見つかりません[/yellow]")
            missing += 1

    if missing:
        console.print(
            f"  [yellow]⚠ {missing} 枚が未コピー。"
            "先に large topology を生成してください:[/yellow]\n"
            "    uv run python main.py -i examples/sample_topology_large.yaml\n"
            "    uv run python main.py -i examples/sample_topology_large.yaml "
            "--focus spine-01\n"
            "    uv run python main.py -i examples/sample_topology_large.yaml "
            "--focus spine-01 spine-02"
        )
    else:
        console.print(f"  [green]✓ scene2 完了: {copied} 枚コピー[/green]")


# ---------------------------------------------------------------------------
# Scene 3: v2d — small topology 画像 → YAML（LLM 必須）
# ---------------------------------------------------------------------------

def prep_scene3(force: bool) -> None:
    out = CACHE_DIR / "scene3_v2d"
    if not force and (out / "output.yaml").exists():
        console.print("[dim]  scene3: スキップ（既に生成済み）[/dim]")
        return

    # 入力画像: scene1 のベスト画像 > images/ にある既存画像 の順で探す
    candidates = [
        CACHE_DIR / "scene1_small" / "best.png",
        IMAGES_DIR / "sample_topology_small_best.png",
    ]
    input_img = next((p for p in candidates if p.exists()), None)
    if input_img is None:
        console.print(
            "  [red]✗ v2d 用の入力画像が見つかりません。"
            "scene1 を先に実行してください。[/red]"
        )
        return

    console.print(f"[bold cyan]  v2d 実行中: {input_img.name} → YAML...[/bold cyan]")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(input_img, out / "input.png")

    from d2v.v2d import pipeline as v2d_pipeline

    result = v2d_pipeline.run(input_img, out / "_tmp")
    shutil.copy(result.yaml_path, out / "output.yaml")
    shutil.copy(result.sidecar_path, out / "output.v2d.json")

    console.print(
        f"  [green]✓ scene3 完了: "
        f"{result.node_count} ノード / {result.edge_count} リンク / "
        f"確信度 {result.confidence:.2f}[/green]"
    )


# ---------------------------------------------------------------------------
# Scene 4: diff — before/after fixtures → 差分図（LLM 不要）
# ---------------------------------------------------------------------------

def prep_scene4(force: bool) -> None:
    out = CACHE_DIR / "scene4_diff"
    if not force and (out / "diff.png").exists():
        console.print("[dim]  scene4: スキップ（既に生成済み）[/dim]")
        return

    before_path = FIXTURES_DIR / "before.yaml"
    after_path = FIXTURES_DIR / "after.yaml"
    if not before_path.exists() or not after_path.exists():
        console.print(
            "  [red]✗ demo/fixtures/before.yaml または after.yaml が見つかりません[/red]"
        )
        return

    console.print("[bold cyan]  diff 差分図を生成中...[/bold cyan]")
    out.mkdir(parents=True, exist_ok=True)

    from d2v import diff as diff_mod
    from d2v.parser import load_model

    before_model = load_model(before_path)
    after_model = load_model(after_path)
    topo_diff = diff_mod.compare(before_model, after_model)

    # 差分図（決定論的・LLM 不要）
    diff_mod.render_diff_diagram(
        before_model, after_model, topo_diff,
        out, stem="diff", fmt="png",
    )

    # blast radius（before モデルに対して nodes_removed を適用）
    impact = diff_mod.impact(
        before_model,
        removed_devices=topo_diff.nodes_removed,
    )

    summary = {
        "nodes_removed": topo_diff.nodes_removed,
        "edges_removed": topo_diff.edges_removed,
        "nodes_added": topo_diff.nodes_added,
        "edges_added": topo_diff.edges_added,
        "blast_radius": impact.unreachable,
        "blast_radius_count": len(impact.unreachable),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    console.print(
        f"  [green]✓ scene4 完了: "
        f"-{len(topo_diff.nodes_removed)} ノード / "
        f"-{len(topo_diff.edges_removed)} リンク / "
        f"blast radius {len(impact.unreachable)} 台 "
        f"({', '.join(impact.unreachable)})[/green]"
    )


# ---------------------------------------------------------------------------
# Scene 5: validate — flawed.yaml → 設計バグ検出（LLM 不要）
# ---------------------------------------------------------------------------

def prep_scene5(force: bool) -> None:
    out = CACHE_DIR / "scene5_validate"
    if not force and (out / "report.json").exists():
        console.print("[dim]  scene5: スキップ（既に生成済み）[/dim]")
        return

    flawed_path = FIXTURES_DIR / "flawed.yaml"
    if not flawed_path.exists():
        console.print("  [red]✗ demo/fixtures/flawed.yaml が見つかりません[/red]")
        return

    console.print("[bold cyan]  validate 実行中（設計バグ検出）...[/bold cyan]")
    out.mkdir(parents=True, exist_ok=True)

    from d2v import validator
    from d2v.parser import load_model

    model = load_model(flawed_path)
    report = validator.validate(model)

    (out / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )

    errors = report.counts.get("error", 0)
    warnings = report.counts.get("warning", 0)
    rules = [i.rule for i in report.issues]
    console.print(
        f"  [green]✓ scene5 完了: "
        f"error {errors} 件 / warning {warnings} 件 "
        f"({', '.join(rules)})[/green]"
    )


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

SCENES: dict[int, tuple[str, object]] = {
    1: ("small topology d2v 生成（LLM 必須）", prep_scene1),
    2: ("large topology 画像コピー（LLM 不要）", prep_scene2),
    3: ("v2d 逆変換（LLM 必須）", prep_scene3),
    4: ("diff 差分図（LLM 不要）", prep_scene4),
    5: ("validate 設計検証（LLM 不要）", prep_scene5),
}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="d2v デモキャッシュを事前生成します。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(
            f"  {k}: {v[0]}" for k, v in SCENES.items()
        ),
    )
    ap.add_argument(
        "--force", "-f", action="store_true",
        help="既存キャッシュを上書きして全シーンを再生成する",
    )
    ap.add_argument(
        "--scene", "-s", type=int, choices=list(SCENES), metavar="N",
        help="指定シーンのみ実行する（1〜5）",
    )
    args = ap.parse_args()

    targets = {args.scene: SCENES[args.scene]} if args.scene else SCENES

    console.print(Rule("[bold blue]d2v デモキャッシュ生成[/bold blue]"))
    console.print(f"  出力先 : {CACHE_DIR}")
    console.print(f"  モード  : {'--force（全再生成）' if args.force else '未生成のみ'}\n")

    failed: list[int] = []
    for scene_num, (desc, fn) in targets.items():
        console.print(Rule(f"[bold]Scene {scene_num}: {desc}[/bold]"))
        try:
            fn(args.force)  # type: ignore[call-arg]
        except Exception as e:
            console.print(f"  [bold red]✗ エラー: {e}[/bold red]")
            traceback.print_exc()
            failed.append(scene_num)

    console.print()
    if failed:
        console.print(Rule(f"[bold red]完了（失敗シーン: {failed}）[/bold red]"))
        sys.exit(1)
    else:
        console.print(Rule("[bold green]完了[/bold green]"))


if __name__ == "__main__":
    main()
