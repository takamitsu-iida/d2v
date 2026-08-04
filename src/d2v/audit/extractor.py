"""audit 抽出器: LLM で機器コンフィグテキストから ExtractedConfig を得る。"""

from __future__ import annotations

import json
import re

from d2v.audit.schema import ExtractedConfig
from d2v.llm import get_llm
from d2v.prompts import load_prompt

# v2d/extractor.py と同じ JSON パターン（コードフェンス優先→生オブジェクト）
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


class ExtractionError(RuntimeError):
    """コンフィグからの抽出に失敗したことを示すエラー。"""


def _parse_json(text: str) -> dict:
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


def extract_from_config(config_text: str, device_id: str) -> ExtractedConfig:
    """コンフィグテキストから構造化情報を抽出する。

    Args:
        config_text: ``show running-config`` 等の出力テキスト。
        device_id: ファイル名 stem で確定した device-id（プロンプトに埋め込む）。

    Returns:
        ExtractedConfig

    Raises:
        ExtractionError: LLM 応答のパースまたはスキーマ検証に失敗した場合。
    """
    system_prompt = load_prompt("config-extract.md")
    user_message = (
        f"device_id: {device_id}\n\n"
        "以下のコンフィグを解析し、スキーマに従った JSON のみを出力してください。\n\n"
        f"```\n{config_text}\n```"
    )

    llm = get_llm()
    response = llm.chat(system_prompt, user_message)

    data = _parse_json(response)
    # device_id は呼び出し元で確定済みのため LLM の値は上書きする
    data["device_id"] = device_id
    try:
        return ExtractedConfig.model_validate(data)
    except Exception as e:
        raise ExtractionError(f"抽出結果が ExtractedConfig スキーマに適合しません: {e}") from e
