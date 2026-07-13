from __future__ import annotations

from openai import OpenAI

from .base import LLMClient


class OpenAIClient(LLMClient):
    """OpenAI API を使用する LLM クライアント。"""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def chat(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""
