from __future__ import annotations

import random
import sys
import time

import httpx

from .base import LLMClient

# リトライ対象の HTTP ステータス（レート制限・一時的なサーバエラー）
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AzureOpenAIClient(LLMClient):
    """社内 Azure OpenAI（api-key ヘッダー方式の REST エンドポイント）クライアント。

    エンドポイント URL にモデルまで含まれ（例: ``.../chat-ai/gpt/gpt-5.1``）、
    ``api-key`` ヘッダーで認証する形式に対応する。標準の Azure OpenAI SDK が使う
    ``api-version`` クエリや ``deployment`` パスは不要なため、httpx で直接 POST する。
    レート制限（429）や一時的なサーバエラーは指数バックオフで自動リトライする。
    """

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        max_tokens: int = 8192,
        timeout: float = 120.0,
        max_retries: int = 6,
    ) -> None:
        self._endpoint = endpoint
        self._headers = {
            "Content-Type": "application/json",
            "api-key": api_key,
        }
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries

    def _retry_after_seconds(self, response: httpx.Response, attempt: int) -> float:
        """次のリトライまでの待機秒数を決める。

        サーバが ``Retry-After`` ヘッダーを返していればそれを優先し、無ければ
        指数バックオフ（2^attempt 秒）にジッタを加えた値を用いる。
        """
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        # 指数バックオフ + ジッタ（1, 2, 4, 8, ... 秒。上限 60 秒）
        return min(2 ** attempt, 60) + random.uniform(0, 1)

    def chat(self, system: str, user: str) -> str:
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # gpt-5.x 系は max_tokens 非対応で max_completion_tokens を要求する
            "max_completion_tokens": self._max_tokens,
        }
        return self._post(body)

    def chat_with_images(
        self, system: str, user: str, image_data_urls: list[str]
    ) -> str:
        # OpenAI 互換の vision 形式（content 配列に text と image_url を混在）
        content: list[dict] = [{"type": "text", "text": user}]
        for url in image_data_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "max_completion_tokens": self._max_tokens,
        }
        return self._post(body)

    def _post(self, body: dict) -> str:
        """共通の POST 処理。429/5xx・接続エラーを指数バックオフで自動リトライする。"""
        last_error: str = ""
        for attempt in range(self._max_retries + 1):
            try:
                res = httpx.post(
                    self._endpoint,
                    headers=self._headers,
                    json=body,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as e:
                # 接続エラーもリトライ対象（一時的なネットワーク不調を想定）
                last_error = f"接続エラー: {e}"
                if attempt < self._max_retries:
                    time.sleep(min(2 ** attempt, 60) + random.uniform(0, 1))
                    continue
                print(
                    f"\n[エラー] Azure OpenAI エンドポイントへの接続に失敗しました: {e}\n",
                    file=sys.stderr,
                )
                sys.exit(1)

            if res.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                wait = self._retry_after_seconds(res, attempt)
                print(
                    f"  [リトライ] HTTP {res.status_code}（レート制限等）。"
                    f"{wait:.1f} 秒待機して再試行します "
                    f"({attempt + 1}/{self._max_retries})。",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue

            if res.status_code >= 400:
                print(
                    f"\n[エラー] Azure OpenAI エンドポイントが HTTP {res.status_code} "
                    f"を返しました:\n{res.text}\n",
                    file=sys.stderr,
                )
                sys.exit(1)

            data = res.json()
            return data["choices"][0]["message"]["content"] or ""

        # リトライを使い切った場合
        print(
            f"\n[エラー] Azure OpenAI エンドポイントへのリクエストが "
            f"{self._max_retries} 回のリトライ後も失敗しました。{last_error}\n",
            file=sys.stderr,
        )
        sys.exit(1)
