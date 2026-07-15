"""v2d 前処理: ネットワーク図の画像を読み込み、vision LLM へ渡せる形に正規化する。

マルチモーダル LLM 方式を主軸とするため、前処理は軽量に保つ:
  - EXIF 情報に基づく撮影向きの補正
  - RGB へ変換（アルファ・パレット画像の正規化）
  - 最大辺を上限に収める縮小（縦横比は維持、拡大はしない）
  - base64 データ URL 化（OpenAI 互換 vision 形式でそのまま送れる）
  - 入力制約（対応拡張子・推奨解像度）の検証と警告

傾き補正・二値化などの重い CV 前処理は OCR 方式を採る場合に追加する（Phase 3）。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from d2v.config import settings

# 対応する画像拡張子
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg"}

# 推奨する最小の画像幅（これ未満は文字認識精度が落ちる恐れがあるため警告）
DEFAULT_MIN_WIDTH = 800


class ImagePreprocessError(ValueError):
    """入力画像が前処理の前提を満たさないことを示すエラー。"""


@dataclass
class PreprocessedImage:
    """前処理済み画像の情報。

    Attributes:
        source: 元画像のパス
        width: 正規化後の幅（px）
        height: 正規化後の高さ（px）
        data_url: ``data:image/png;base64,...`` 形式のデータ URL
        original_size: 元画像の (幅, 高さ)
        warnings: 入力制約に関する警告メッセージ
    """

    source: Path
    width: int
    height: int
    data_url: str
    original_size: tuple[int, int]
    warnings: list[str] = field(default_factory=list)


def image_to_data_url(img: Image.Image) -> str:
    """PIL 画像を PNG の base64 データ URL に変換する。"""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def load_and_preprocess(
    path: str | Path,
    max_dim: int | None = None,
    min_width: int = DEFAULT_MIN_WIDTH,
) -> PreprocessedImage:
    """画像を読み込み、vision LLM へ渡せる正規化済み画像を返す。

    Args:
        path: 入力画像ファイルのパス（PNG / JPEG）
        max_dim: 最大辺のピクセル上限（None なら設定値 ``v2d_max_image_dim``）。
            これを超える画像は縦横比を維持して縮小する（拡大はしない）。
        min_width: 推奨する最小幅。下回る場合は警告に記録する（エラーにはしない）。

    Returns:
        PreprocessedImage

    Raises:
        ImagePreprocessError: ファイルが存在しない、または非対応の拡張子/破損画像の場合。
    """
    path = Path(path)
    if not path.exists():
        raise ImagePreprocessError(f"画像ファイルが見つかりません: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ImagePreprocessError(
            f"非対応の画像形式です: {path.suffix}（対応: "
            f"{', '.join(sorted(SUPPORTED_SUFFIXES))}）"
        )

    max_dim = max_dim if max_dim is not None else settings.v2d_max_image_dim
    warnings: list[str] = []

    try:
        with Image.open(path) as im:
            # 撮影向き（EXIF Orientation）を補正してから RGB 化する
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            original_size = im.size  # (幅, 高さ)

            orig_w, orig_h = original_size
            if orig_w < min_width:
                warnings.append(
                    f"画像幅 {orig_w}px は推奨下限 {min_width}px 未満です。"
                    "文字（ホスト名・IP・ポート名）の認識精度が落ちる可能性があります。"
                )

            # 最大辺が上限を超える場合のみ縦横比を維持して縮小する
            longest = max(orig_w, orig_h)
            if longest > max_dim:
                scale = max_dim / longest
                new_size = (round(orig_w * scale), round(orig_h * scale))
                im = im.resize(new_size, Image.LANCZOS)

            width, height = im.size
            data_url = image_to_data_url(im)
    except ImagePreprocessError:
        raise
    except Exception as e:  # PIL の読み込み失敗（破損ファイル等）
        raise ImagePreprocessError(f"画像を読み込めませんでした: {path}（{e}）") from e

    return PreprocessedImage(
        source=path,
        width=width,
        height=height,
        data_url=data_url,
        original_size=original_size,
        warnings=warnings,
    )
