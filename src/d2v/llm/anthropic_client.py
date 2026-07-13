from __future__ import annotations

import anthropic

from .base import LLMClient


class AnthropicClient(LLMClient):
    """Anthropic Claude API を使用する LLM クライアント。"""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def chat(self, system: str, user: str) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=8192,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text
