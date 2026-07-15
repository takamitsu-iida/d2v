from __future__ import annotations

from openai import OpenAI

from .base import LLMClient


class OpenAIClient(LLMClient):
    """OpenAI API を使用する LLM クライアント。"""

    def __init__(self, api_key: str, model: str, max_tokens: int = 8192) -> None:
        # max_retries を引き上げ、レート制限（429）時に SDK が Retry-After を尊重した
        # 指数バックオフで自動リトライするようにする。
        self._client = OpenAI(api_key=api_key, max_retries=6)
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

    def chat_with_images(
        self, system: str, user: str, image_data_urls: list[str]
    ) -> str:
        content: list[dict] = [{"type": "text", "text": user}]
        for url in image_data_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        return response.choices[0].message.content or ""
