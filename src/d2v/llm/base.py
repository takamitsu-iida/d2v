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
