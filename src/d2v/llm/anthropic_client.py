from __future__ import annotations

import anthropic

from .base import LLMClient


class AnthropicClient(LLMClient):
    """Anthropic Claude API を使用する LLM クライアント。"""

    def __init__(self, api_key: str, model: str, max_tokens: int = 8192) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def chat(self, system: str, user: str) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text
