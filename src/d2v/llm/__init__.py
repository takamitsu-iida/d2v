"""LLM ファクトリー。LLM_PROVIDER 環境変数でプロバイダーを切り替える。"""

from __future__ import annotations

import sys

from d2v.config import settings

from .anthropic_client import AnthropicClient
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


def _abort_missing_credentials(provider: str) -> None:
    """認証情報不足を分かりやすく報告してプロセスを終了する。"""
    msg = _MISSING_KEY_MESSAGES.get(
        provider, f"プロバイダー '{provider}' の認証情報が設定されていません。"
    )
    print(f"\n[設定エラー] {msg}{_SETUP_HINT}\n", file=sys.stderr)
    sys.exit(1)


def get_llm() -> LLMClient:
    """環境変数 LLM_PROVIDER に応じた LLMClient インスタンスを返す。

    対応プロバイダー:
        openai    : OpenAIClient (デフォルト)
        anthropic : AnthropicClient
        ollama    : OllamaClient (ローカル LLM)

    .env が未設定の場合や API キーが空の場合は、スタックトレースではなく
    分かりやすいエラーメッセージを表示してプロセスを終了する。
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
        except Exception:
            _abort_missing_credentials("openai")

    if provider == "anthropic":
        if not settings.anthropic_api_key.get_secret_value():
            _abort_missing_credentials("anthropic")
        try:
            return AnthropicClient(
                api_key=settings.anthropic_api_key.get_secret_value(),
                model=settings.anthropic_model,
                max_tokens=settings.llm_max_tokens,
            )
        except Exception:
            _abort_missing_credentials("anthropic")

    if provider == "ollama":
        try:
            return OllamaClient(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                max_tokens=settings.llm_max_tokens,
            )
        except Exception:
            _abort_missing_credentials("ollama")

    print(
        f"\n[設定エラー] 未対応の LLM プロバイダー: '{provider}'\n"
        "LLM_PROVIDER は 'openai' / 'anthropic' / 'ollama' のいずれかを指定してください。"
        f"{_SETUP_HINT}\n",
        file=sys.stderr,
    )
    sys.exit(1)


__all__ = [
    "LLMClient",
    "get_llm",
    "OpenAIClient",
    "AnthropicClient",
    "OllamaClient",
]
