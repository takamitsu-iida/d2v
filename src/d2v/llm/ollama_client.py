from __future__ import annotations

from openai import OpenAI

from .base import LLMClient


class OllamaClient(LLMClient):
    """Ollama（ローカル LLM）を使用するクライアント。

    Ollama は OpenAI 互換 API を提供するため、openai パッケージ経由で利用する。
    """

    def __init__(self, base_url: str, model: str, max_tokens: int = 8192) -> None:
        self._client = OpenAI(
            api_key="ollama",  # Ollama は認証不要だが api_key は必須引数
            base_url=f"{base_url.rstrip('/')}/v1",
        )
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
