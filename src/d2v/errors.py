"""d2v 共通の例外階層。

ライブラリ層（parser / generator / evaluator / renderer / llm など）は
``sys.exit`` せず、これらの例外を送出する。UI 層が終了方法を決める:

- CLI（``main.py``）は捕捉して赤字メッセージを表示し、終了コード 1 で終了する。
- Web 層（``web/jobs.py``）は失敗ジョブへ変換し、進捗イベントとして通知する。

これによりライブラリをプログラムから再利用しやすくなる。
"""

from __future__ import annotations


class D2VError(Exception):
    """d2v の基底例外。すべてのユーザー向けエラーはこれを継承する。"""


class PromptNotFoundError(D2VError):
    """プロンプトファイルが見つからない。"""


class InputError(D2VError):
    """入力ファイル（トポロジ YAML 等）の読み込み・解析エラー。"""


class LLMConfigError(D2VError):
    """LLM プロバイダーの設定・認証情報エラー（未設定・不正な値など）。"""


class LLMRequestError(D2VError):
    """LLM API リクエストの失敗（接続エラー・HTTP エラー・リトライ枯渇など）。"""


class GraphvizNotFoundError(D2VError):
    """Graphviz 実行ファイルが見つからない（環境起因の回復不能エラー）。"""


class RenderFailedError(D2VError):
    """有効な図を 1 枚も生成できなかった。"""
