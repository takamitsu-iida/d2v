"""パイプライン実行結果のスコア推移をグラフで可視化する。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from d2v.pipeline import IterationRecord


def plot_score_history(
    records: list[IterationRecord],
    output_path: Path,
    threshold: int = 8,
) -> Path:
    """イテレーション毎のスコア推移を折れ線グラフで保存する。

    Args:
        records: pipeline.run() が返した IterationRecord のリスト
        output_path: グラフ画像の保存先
        threshold: 合格ライン（点線で表示）

    Returns:
        保存した画像ファイルの Path
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # ヘッドレス環境対応
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
    except ImportError:
        print(
            "\n[警告] matplotlib が見つかりません。スコアグラフをスキップします。\n"
            "  pip install matplotlib  でインストールできます。\n",
            file=sys.stderr,
        )
        return output_path

    iterations = [r.iteration for r in records]
    scores = [r.result.score for r in records]
    best_iter = max(records, key=lambda r: r.result.score).iteration
    best_score = max(scores)

    fig, ax = plt.subplots(figsize=(8, 4))

    # スコア推移
    ax.plot(
        iterations, scores,
        "o-",
        color="#1A73E8",
        linewidth=2,
        markersize=8,
        label="Score",
        zorder=3,
    )

    # 合格ライン
    ax.axhline(
        y=threshold,
        color="#C5221F",
        linestyle="--",
        linewidth=1.5,
        label=f"Threshold ({threshold}pts)",
        zorder=2,
    )

    # ベストスコアのマーカー
    ax.scatter(
        [best_iter],
        [best_score],
        color="#137333",
        s=140,
        zorder=5,
        label=f"Best: {best_score}/10 (Iter {best_iter})",
    )

    # 各点にスコア値を表示
    for it, sc in zip(iterations, scores):
        ax.annotate(
            str(sc),
            (it, sc),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            fontsize=9,
            color="#1A73E8",
        )

    ax.set_xlabel("Iteration", fontsize=11)
    ax.set_ylabel("Score (1-10)", fontsize=11)
    ax.set_title("d2v Evaluation Score History", fontsize=13, fontweight="bold")
    ax.set_ylim(0, 11)
    ax.yaxis.set_major_locator(ticker.MultipleLocator(1))
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_path
