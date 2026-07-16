"""インメモリのジョブ管理（作成・実行・進捗ストリーミング・結果保持）。

単一ユーザーのローカルツール想定のため、状態はプロセス内メモリに保持する。
LLM 呼び出しは同期ブロッキングのため ``ThreadPoolExecutor`` で実行し、進捗は
`threading.Condition` で待ち受ける SSE ジェネレータへ配信する（複数接続・遅延
接続でもリプレイ可能）。
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from d2v.progress import ProgressEvent
from d2v.web import service
from d2v.web.events import JobState, event_to_dict
from d2v.web.service import D2VJobError, D2VJobResult, D2VParams, V2DJobResult
from d2v.config import settings
from d2v.errors import D2VError

# 出力先（既存 output/ 規約を踏襲。webui/<job_id>/ に隔離）
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WEBUI_DIR = _ROOT / "output" / "webui"


class JobBusyError(Exception):
    """同時実行ジョブ数の上限に達している。"""


@dataclass
class Job:
    """1 ジョブの状態・進捗・結果を保持する。"""

    id: str
    kind: str                       # "d2v" / "v2d"
    output_dir: Path
    params: dict = field(default_factory=dict)
    state: JobState = JobState.QUEUED
    error: str | None = None
    result: D2VJobResult | V2DJobResult | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # 進捗イベントの蓄積とストリーミング同期
    events: list[ProgressEvent] = field(default_factory=list)
    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _done: bool = False

    # ── 進捗イベント ──────────────────────────────────────────────
    def add_event(self, event: ProgressEvent) -> None:
        """進捗イベントを追加し、購読中の SSE ジェネレータへ通知する。"""
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def mark_done(self) -> None:
        """ジョブ完了を記録し、全ストリームを終了へ導く。"""
        with self._cond:
            self._done = True
            self._cond.notify_all()

    def stream(self) -> Iterator[ProgressEvent]:
        """蓄積済みイベントをリプレイしつつ、新規イベントを逐次 yield する。

        完了後は残りを流し切ってから終了する。複数のジェネレータが独立した
        インデックスで並行購読できる。
        """
        idx = 0
        while True:
            with self._cond:
                while idx >= len(self.events) and not self._done:
                    self._cond.wait()
                new = self.events[idx:]
                idx += len(new)
                done_and_drained = self._done and idx >= len(self.events)
            for event in new:
                yield event
            if done_and_drained:
                break

    # ── シリアライズ ──────────────────────────────────────────────
    def to_dict(self) -> dict:
        """状態・結果メタを JSON 化可能な dict で返す。"""
        data: dict = {
            "id": self.id,
            "kind": self.kind,
            "state": self.state.value,
            "error": self.error,
            "created_at": self.created_at,
            "params": self.params,
        }
        if self.result is not None:
            if self.kind == "d2v":
                data["mode"] = self.result.mode
                data["outputs"] = [
                    {
                        "key": o.key,
                        "title": o.title,
                        "score": o.score,
                        "passed": o.passed,
                        "iterations": o.result.total_iterations,
                        # 画像はジョブ出力ディレクトリからの相対パス（配信用）
                        "image": _relpath(o.final_image, self.output_dir),
                    }
                    for o in self.result.outputs
                ]
            elif self.kind == "v2d":
                r = self.result
                data["v2d"] = {
                    "node_count": r.node_count,
                    "edge_count": r.edge_count,
                    "cluster_count": r.cluster_count,
                    "confidence": r.confidence,
                    "notes": r.notes,
                    "low_confidence_nodes": r.low_confidence_nodes,
                    "metrics": r.metrics,
                    "has_rerender": r.rerender_image is not None,
                    "rerender_score": r.rerender_score,
                }
        return data

    def summary(self) -> dict:
        """履歴一覧用のコンパクトな要約を返す。"""
        s: dict = {
            "id": self.id,
            "kind": self.kind,
            "state": self.state.value,
            "created_at": self.created_at,
            "label": "",
        }
        if self.kind == "d2v":
            s["label"] = self.params.get("example") or "YAML(貼り付け)"
        elif self.kind == "v2d":
            s["label"] = self.params.get("filename") or "画像"
        if self.result is not None:
            if self.kind == "d2v":
                outs = self.result.outputs
                s["mode"] = self.result.mode
                s["diagram_count"] = len(outs)
                s["best_score"] = max((o.score for o in outs), default=None)
            elif self.kind == "v2d":
                s["node_count"] = self.result.node_count
                s["edge_count"] = self.result.edge_count
                s["confidence"] = self.result.confidence
        return s


def _relpath(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return path.name


# ---------------------------------------------------------------------------
# ジョブレジストリ
# ---------------------------------------------------------------------------


class JobRegistry:
    """ジョブの生成・実行・取得を束ねるインメモリレジストリ。"""

    def __init__(self, base_dir: Path = WEBUI_DIR, max_workers: int = 2) -> None:
        self.base_dir = base_dir
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict]:
        """全ジョブの要約を新しい順で返す（履歴一覧用）。"""
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [j.summary() for j in jobs]

    def _ensure_capacity(self) -> None:
        """実行中/待機中ジョブが上限未満であることを保証する。"""
        with self._lock:
            active = sum(
                1 for j in self._jobs.values()
                if j.state in (JobState.QUEUED, JobState.RUNNING)
            )
        if active >= settings.webui_max_active_jobs:
            raise JobBusyError(
                f"同時に実行できるジョブは {settings.webui_max_active_jobs} 件までです。"
                "実行中のジョブの完了を待ってから再試行してください。"
            )

    def _new_job(self, kind: str, params: dict) -> Job:
        job_id = uuid.uuid4().hex[:12]
        output_dir = self.base_dir / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, kind=kind, output_dir=output_dir, params=params)
        with self._lock:
            self._jobs[job_id] = job
        return job

    # ── d2v ジョブ ────────────────────────────────────────────────
    def create_d2v_job(
        self, *, input_text: str, options: dict, request_meta: dict
    ) -> Job:
        """d2v ジョブを作成し、バックグラウンド実行を開始する。

        Args:
            input_text: 入力トポロジ YAML の本文。ジョブ専用ディレクトリへ保存する。
            options: D2VParams のフィールド（fmt / max_iter / threshold / ...）。
            request_meta: 元リクエスト情報（状態表示用）。
        """
        self._ensure_capacity()
        job = self._new_job("d2v", request_meta)
        input_path = job.output_dir / "input.yaml"
        input_path.write_text(input_text, encoding="utf-8")
        params = D2VParams(
            input_path=input_path,
            output_dir=job.output_dir,
            **options,
        )
        self._executor.submit(self._run_d2v, job, params)
        return job

    def _run_d2v(self, job: Job, params: D2VParams) -> None:
        job.state = JobState.RUNNING
        try:
            result = service.run_d2v_job(params, progress=job.add_event)
            job.result = result
            job.state = JobState.SUCCEEDED
        except D2VJobError as e:
            # ユーザー起因の検証エラー
            job.error = str(e)
            job.state = JobState.FAILED
            job.add_event(ProgressEvent(stage="error", message=str(e)))
        except D2VError as e:
            # LLM 認証エラー・プロンプト欠如・レンダリング失敗など
            job.error = str(e)
            job.state = JobState.FAILED
            job.add_event(ProgressEvent(stage="error", message=str(e)))
        except SystemExit:
            msg = (
                "処理が中断されました（LLM 認証エラーやレンダリング失敗の可能性）。"
                "サーバーログと .env の設定を確認してください。"
            )
            job.error = msg
            job.state = JobState.FAILED
            job.add_event(ProgressEvent(stage="error", message=msg))
        except Exception as e:  # noqa: BLE001 — API 例外を含め失敗として構造化
            job.error = f"{type(e).__name__}: {e}"
            job.state = JobState.FAILED
            job.add_event(ProgressEvent(stage="error", message=job.error))
        finally:
            job.mark_done()

    # ── v2d ジョブ ────────────────────────────────────────────────
    def create_v2d_job(
        self,
        *,
        image_bytes: bytes,
        image_filename: str,
        truth_text: str | None = None,
        rerender: bool = False,
        fmt: str = "png",
        request_meta: dict,
    ) -> Job:
        """v2d ジョブ（画像 → YAML）を作成し、バックグラウンド実行を開始する。"""
        self._ensure_capacity()
        job = self._new_job("v2d", request_meta)
        ext = Path(image_filename).suffix.lower() or ".png"
        image_path = job.output_dir / f"input{ext}"
        image_path.write_bytes(image_bytes)
        truth_path: Path | None = None
        if truth_text and truth_text.strip():
            truth_path = job.output_dir / "truth.yaml"
            truth_path.write_text(truth_text, encoding="utf-8")
        self._executor.submit(
            self._run_v2d, job, image_path, truth_path, rerender, fmt
        )
        return job

    def _run_v2d(
        self,
        job: Job,
        image_path: Path,
        truth_path: Path | None,
        rerender: bool,
        fmt: str,
    ) -> None:
        job.state = JobState.RUNNING
        try:
            result = service.run_v2d_job(
                image_path, job.output_dir, truth_path, rerender, fmt,
                progress=job.add_event,
            )
            job.result = result
            job.state = JobState.SUCCEEDED
        except D2VError as e:
            # LLM 認証エラー・プロンプト欠如など
            job.error = str(e)
            job.state = JobState.FAILED
            job.add_event(ProgressEvent(stage="error", message=str(e)))
        except SystemExit:
            msg = (
                "処理が中断されました（LLM 認証エラーや解析失敗の可能性）。"
                "サーバーログと .env の設定を確認してください。"
            )
            job.error = msg
            job.state = JobState.FAILED
            job.add_event(ProgressEvent(stage="error", message=msg))
        except Exception as e:  # noqa: BLE001 — 抽出/前処理エラーを構造化
            job.error = f"{type(e).__name__}: {e}"
            job.state = JobState.FAILED
            job.add_event(ProgressEvent(stage="error", message=job.error))
        finally:
            job.mark_done()


def sse_format(event: ProgressEvent) -> str:
    """ProgressEvent を SSE フレーム（``event:`` + ``data:``）へ整形する。"""
    import json

    payload = json.dumps(event_to_dict(event), ensure_ascii=False)
    return f"event: {event.stage}\ndata: {payload}\n\n"


# プロセス内シングルトン（app.py から共有）
registry = JobRegistry()
