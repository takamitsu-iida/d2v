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

    def chat_with_images(
        self, system: str, user: str, image_data_urls: list[str]
    ) -> str:
        # Anthropic は base64 画像を独自形式で受け取るためデータ URL を分解する
        content: list[dict] = []
        for url in image_data_urls:
            media_type = "image/png"
            data = url
            if url.startswith("data:"):
                header, _, data = url.partition(",")
                if ";" in header and header.startswith("data:"):
                    media_type = header[len("data:"):].split(";", 1)[0] or media_type
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            })
        content.append({"type": "text", "text": user})
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        return message.content[0].text
