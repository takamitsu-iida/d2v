from __future__ import annotations

from abc import ABC, abstractmethod


class LLMClient(ABC):
    """LLM クライアントの抽象基底クラス。

    すべての LLM プロバイダー実装はこのクラスを継承し、
    chat() メソッドを実装する。
    """

    @abstractmethod
    def chat(self, system: str, user: str) -> str:
        """システムプロンプトとユーザーメッセージを渡し、応答テキストを返す。

        Args:
            system: システムプロンプト（役割・制約の定義）
            user: ユーザーメッセージ（入力データ）

        Returns:
            LLM が生成した応答テキスト
        """
        ...

    def chat_with_images(
        self, system: str, user: str, image_data_urls: list[str]
    ) -> str:
        """画像付きでチャットし、応答テキストを返す（vision 対応クライアント用）。

        既定では未対応として例外を送出する。画像入力に対応するクライアント
        （OpenAI 互換 / Azure OpenAI / Ollama 等）でオーバーライドする。

        Args:
            system: システムプロンプト
            user: ユーザーメッセージ（テキスト指示）
            image_data_urls: ``data:image/png;base64,...`` 形式のデータ URL 一覧

        Returns:
            LLM が生成した応答テキスト
        """
        raise NotImplementedError(
            f"{type(self).__name__} は画像入力（vision）に対応していません。"
        )
