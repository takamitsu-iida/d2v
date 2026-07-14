from __future__ import annotations

from openai import OpenAI

from .base import LLMClient


class OpenAIClient(LLMClient):
    """OpenAI API を使用する LLM クライアント。"""

    def __init__(self, api_key: str, model: str, max_tokens: int = 8192) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def chat(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""
