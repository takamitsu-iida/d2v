"""進捗イベントの共通定義（CLI・Web GUI で共有する）。

`pipeline.run()` や `web.service` はここで定義した ``ProgressEvent`` を
``ProgressCallback`` 経由で emit する。CLI は rich 表示へ、Web GUI は SSE へ
橋渡しする。UI 層に依存しないよう、このモジュールは標準ライブラリのみを使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ProgressEvent:
    """進捗の 1 イベント。

    Attributes:
        stage: イベント種別。
            ジョブ全体（web.service が emit）:
              ``topology`` / ``plan`` / ``diagram_start`` / ``diagram_done`` / ``job_done``
            パイプライン内（pipeline.run が emit）:
              ``iteration_start`` / ``generate`` / ``render`` / ``render_failed`` /
              ``evaluate`` / ``score`` / ``passed`` / ``early_stop`` / ``pipeline_done``
        message: 人間可読なメッセージ（任意）。
        iteration: 対象イテレーション/図のインデックス（0 起点、任意）。
        total: 全体数（イテレーション数・図の枚数など、任意）。
        score: 評価スコア（``score`` イベントなどで設定、任意）。
        passed: 合格判定（任意）。
        is_best: ベスト更新か（``score`` イベントで設定、任意）。
        extra: 追加データ（key/title/text/image など、stage 依存）。
    """

    stage: str
    message: str = ""
    iteration: int | None = None
    total: int | None = None
    score: int | None = None
    passed: bool | None = None
    is_best: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# 進捗コールバックの型。None を許容し、未指定なら何もしない。
ProgressCallback = Callable[[ProgressEvent], None]


def emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    """コールバックが設定されていればイベントを渡す（None なら何もしない）。"""
    if callback is not None:
        callback(event)
