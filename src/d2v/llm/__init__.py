"""LLM ファクトリー。LLM_PROVIDER 環境変数でプロバイダーを切り替える。"""

from __future__ import annotations

from typing import NoReturn

from anthropic import AnthropicError
from openai import OpenAIError

from d2v.config import settings
from d2v.errors import LLMConfigError

from .anthropic_client import AnthropicClient
from .azure_openai_client import AzureOpenAIClient
from .base import LLMClient
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient

# ---------------------------------------------------------------------------
# エラーメッセージテンプレート
# ---------------------------------------------------------------------------

_SETUP_HINT = (
    "\n.env ファイルが存在しない場合はテンプレートをコピーして設定してください:\n"
    "  cp .env.example .env\n"
    "  # .env を開いて必要な値を設定する"
)

_MISSING_KEY_MESSAGES: dict[str, str] = {
    "openai": (
        "OpenAI API キーが設定されていません。\n"
        ".env に以下を追加してください:\n"
        "  LLM_PROVIDER=openai\n"
        "  OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx\n"
        "  OPENAI_MODEL=gpt-4o"
    ),
    "azure": (
        "Azure OpenAI の認証情報が設定されていません。\n"
        ".env に以下を追加してください:\n"
        "  LLM_PROVIDER=azure\n"
        "  AZURE_OPENAI_API_KEY=xxxxxxxxxxxxxxxxxxxx\n"
        "  AZURE_OPENAI_ENDPOINT=https://<ホスト>/.../gpt/<モデル>"
    ),
    "anthropic": (
        "Anthropic API キーが設定されていません。\n"
        ".env に以下を追加してください:\n"
        "  LLM_PROVIDER=anthropic\n"
        "  ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxx\n"
        "  ANTHROPIC_MODEL=claude-3-5-sonnet-20241022"
    ),
    "ollama": (
        "Ollama の接続先が設定されていないか、サーバーが起動していません。\n"
        ".env に以下を追加し、Ollama サーバーを起動してください:\n"
        "  LLM_PROVIDER=ollama\n"
        "  OLLAMA_BASE_URL=http://localhost:11434\n"
        "  OLLAMA_MODEL=llama3.1:70b"
    ),
}


def _credentials_message(provider: str) -> str:
    """プロバイダー別の「認証情報が不足」メッセージ（セットアップ手順付き）を返す。"""
    msg = _MISSING_KEY_MESSAGES.get(
        provider, f"プロバイダー '{provider}' の認証情報が設定されていません。"
    )
    return f"{msg}{_SETUP_HINT}"


def _abort_missing_credentials(provider: str) -> NoReturn:
    """認証情報不足を分かりやすいメッセージ付きの例外として送出する。"""
    raise LLMConfigError(_credentials_message(provider))


def get_llm() -> LLMClient:
    """環境変数 LLM_PROVIDER に応じた LLMClient インスタンスを返す。

    対応プロバイダー:
        openai    : OpenAIClient (デフォルト)
        azure     : AzureOpenAIClient (Azure OpenAI Service)
        anthropic : AnthropicClient
        ollama    : OllamaClient (ローカル LLM)

    .env が未設定の場合や API キーが空の場合は、スタックトレースではなく
    分かりやすいメッセージ付きの ``LLMConfigError`` を送出する。
    """
    provider = settings.llm_provider

    if provider == "openai":
        if not settings.openai_api_key.get_secret_value():
            _abort_missing_credentials("openai")
        try:
            return OpenAIClient(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.openai_model,
                max_tokens=settings.llm_max_tokens,
            )
        except OpenAIError as e:
            raise LLMConfigError(_credentials_message("openai")) from e

    if provider == "azure":
        if not (
            settings.azure_openai_api_key.get_secret_value()
            and settings.azure_openai_endpoint
        ):
            _abort_missing_credentials("azure")
        # AzureOpenAIClient は OpenAI SDK を使わない自前実装で、コンストラクタは
        # 設定を保持するだけで例外を投げない。認証・接続エラーは実リクエスト時に
        # LLMRequestError として送出される。
        return AzureOpenAIClient(
            api_key=settings.azure_openai_api_key.get_secret_value(),
            endpoint=settings.azure_openai_endpoint,
            max_tokens=settings.llm_max_tokens,
            max_retries=settings.max_retries,
        )

    if provider == "anthropic":
        if not settings.anthropic_api_key.get_secret_value():
            _abort_missing_credentials("anthropic")
        try:
            return AnthropicClient(
                api_key=settings.anthropic_api_key.get_secret_value(),
                model=settings.anthropic_model,
                max_tokens=settings.llm_max_tokens,
            )
        except AnthropicError as e:
            raise LLMConfigError(_credentials_message("anthropic")) from e

    if provider == "ollama":
        try:
            return OllamaClient(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                max_tokens=settings.llm_max_tokens,
            )
        except OpenAIError as e:
            raise LLMConfigError(_credentials_message("ollama")) from e

    raise LLMConfigError(
        f"未対応の LLM プロバイダー: '{provider}'。"
        "LLM_PROVIDER は 'openai' / 'azure' / 'anthropic' / 'ollama' のいずれかを指定してください。"
        f"{_SETUP_HINT}"
    )


__all__ = [
    "LLMClient",
    "get_llm",
    "OpenAIClient",
    "AzureOpenAIClient",
    "AnthropicClient",
    "OllamaClient",
]
