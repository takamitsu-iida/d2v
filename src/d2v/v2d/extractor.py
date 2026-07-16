"""v2d 抽出器: マルチモーダル LLM でネットワーク図の画像から中間表現を抽出する。

前処理（``preprocess``）で正規化した画像を vision 対応 LLM に渡し、
``ExtractedDiagram``（中間表現）を得る。OCR/CV 方式を採る場合も、同じ
``ExtractedDiagram`` を返す別実装として差し替えられる。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from d2v.llm import get_llm
from d2v.prompts import load_prompt
from d2v.v2d.preprocess import PreprocessedImage, load_and_preprocess
from d2v.v2d.schema import ExtractedDiagram

# 応答から JSON を取り出す正規表現（コードフェンス優先→生オブジェクト）
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


class ExtractionError(RuntimeError):
    """画像からの中間表現抽出に失敗したことを示すエラー。"""


def _parse_json(text: str) -> dict:
    """LLM 応答から JSON オブジェクトを抽出してパースする。"""
    m = _JSON_BLOCK_RE.search(text)
    raw = m.group(1) if m else None
    if raw is None:
        m2 = _JSON_OBJ_RE.search(text)
        if not m2:
            raise ExtractionError("LLM 応答から JSON を抽出できませんでした。")
        raw = m2.group()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"LLM 応答の JSON パースに失敗しました: {e}") from e


def extract_from_image(
    image_path: str | Path,
    max_dim: int | None = None,
) -> tuple[ExtractedDiagram, PreprocessedImage]:
    """画像ファイルからネットワーク構造（中間表現）を抽出する。

    Args:
        image_path: 入力画像ファイルのパス（PNG / JPEG）
        max_dim: 画像の最大辺ピクセル上限（None なら設定値）

    Returns:
        (ExtractedDiagram, PreprocessedImage) のタプル

    Raises:
        ExtractionError: 応答の解析やスキーマ検証に失敗した場合。
    """
    pre = load_and_preprocess(image_path, max_dim=max_dim)
    system_prompt = load_prompt("v2d-extract.md")
    user_message = (
        "このネットワーク構成図の画像を解析し、スキーマに従った JSON のみを出力してください。"
    )

    llm = get_llm()
    response = llm.chat_with_images(system_prompt, user_message, [pre.data_url])

    data = _parse_json(response)
    try:
        diagram = ExtractedDiagram.model_validate(data)
    except Exception as e:
        raise ExtractionError(f"抽出結果が中間表現スキーマに適合しません: {e}") from e
    return diagram, pre
