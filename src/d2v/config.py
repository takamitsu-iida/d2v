from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM プロバイダー選択: "openai" / "anthropic" / "ollama"
    llm_provider: Literal["openai", "anthropic", "ollama"] = "openai"

    # OpenAI
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o"

    # Anthropic
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # Ollama（ローカル LLM）
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:70b"

    # 生成トークン上限
    # 大規模トポロジ（数十ノード・百本超の接続）でも DOT 出力が途中で
    # 途切れないよう十分大きな値を確保する。モデルの上限を超えるとエラーに
    # なるため、利用モデルに合わせて .env で調整可能。
    llm_max_tokens: int = 8192

    # エージェント設定
    max_retries: int = 5


settings = Settings()
