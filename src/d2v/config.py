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

    # LLM プロバイダー選択: "openai" / "azure" / "anthropic" / "ollama"
    llm_provider: Literal["openai", "azure", "anthropic", "ollama"] = "openai"

    # OpenAI
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o"

    # Azure OpenAI Service（api-key ヘッダー方式の REST エンドポイント）
    azure_openai_api_key: SecretStr = SecretStr("")
    # モデルまで含んだ完全なエンドポイント URL
    # 例: https://api.ai-service.global.fujitsu.com/ai-foundation/chat-ai/gpt/gpt-5.1
    azure_openai_endpoint: str = ""

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

    # 分割詳細図で、1 つの外部ゾーンからの境界スタブがこの数を超えたら
    # ゾーン単位で 1 ノードに集約する（詳細図が横に伸びすぎるのを防ぐ）。
    boundary_agg_threshold: int = 3

    # 図の目標縦横比（幅/高さ）。横長すぎる図は rankdir=LR で縦積みにして
    # この比に近づける。3:4（縦3・横4）なら 4/3≈1.333。0 以下で無効化。
    diagram_aspect_ratio: float = 4 / 3

    # v2d（画像→トポロジ）: vision LLM へ渡す画像の最大辺ピクセル。
    # これを超える画像は縦横比を保って縮小する（トークン量と精度のバランス）。
    v2d_max_image_dim: int = 2048


settings = Settings()
