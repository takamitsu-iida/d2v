"""audit レポーター: AuditReport を Rich または JSON に整形する。"""

from __future__ import annotations

import json

from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from d2v.audit.schema import AuditReport

_SEVERITY_STYLE: dict[str, str] = {
    "error": "bold red",
    "warning": "yellow",
    "info": "cyan",
}

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def render_report(report: AuditReport) -> RenderableType:
    """AuditReport を Rich renderable（サマリ＋デバイス別テーブル）に整形する。"""
    if not report.issues:
        return Text("✓ 設計との逸脱は検出されませんでした。", style="bold green")

    # サマリ行
    summary = Text()
    summary.append("検査結果  ", style="bold")
    for i, sev in enumerate(("error", "warning", "info")):
        if i:
            summary.append("  ")
        summary.append(f"{sev}={report.counts.get(sev, 0)}", style=_SEVERITY_STYLE[sev])

    # デバイス別にグループ化してテーブルを構築
    device_order: list[str] = []
    by_device: dict[str, list] = {}
    for issue in report.issues:
        if issue.device_id not in by_device:
            device_order.append(issue.device_id)
            by_device[issue.device_id] = []
        by_device[issue.device_id].append(issue)

    renderables: list[RenderableType] = [summary]

    for did in device_order:
        device_issues = sorted(
            by_device[did], key=lambda x: _SEVERITY_ORDER.get(x.severity, 9)
        )
        renderables.append(Rule(f"[bold]{did}[/bold]"))

        table = Table(show_header=True, header_style="bold", box=None)
        table.add_column("重大度", no_wrap=True, min_width=8)
        table.add_column("ルール", no_wrap=True, min_width=22)
        table.add_column("内容")
        table.add_column("補足", style="dim")

        for issue in device_issues:
            style = _SEVERITY_STYLE.get(issue.severity, "")
            table.add_row(
                Text(issue.severity, style=style),
                issue.rule,
                issue.message,
                issue.detail,
            )
        renderables.append(table)

    return Group(*renderables)


def to_json(report: AuditReport, *, indent: int = 2) -> str:
    """AuditReport を JSON 文字列に変換する。"""
    return json.dumps(report.model_dump(), ensure_ascii=False, indent=indent)
