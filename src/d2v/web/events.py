"""Web GUI のジョブ状態・進捗イベントのシリアライズ定義。

進捗イベント本体は `d2v.progress.ProgressEvent`（UI 非依存）を再利用し、
ここでは Web API / SSE 向けの状態列挙と JSON 変換を定義する。
"""

from __future__ import annotations

from enum import Enum

from d2v.progress import ProgressEvent


class JobState(str, Enum):
    """ジョブのライフサイクル状態。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def event_to_dict(event: ProgressEvent) -> dict:
    """ProgressEvent を JSON 化可能な dict へ変換する（SSE 送出用）。"""
    return {
        "stage": event.stage,
        "message": event.message,
        "iteration": event.iteration,
        "total": event.total,
        "score": event.score,
        "passed": event.passed,
        "is_best": event.is_best,
        "extra": _sanitize(event.extra),
    }


def _sanitize(value):
    """dict/list を JSON 安全な値へ再帰変換する（Path 等を str 化）。"""
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
