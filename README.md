# d2v — Diagram to Vision

`iida-network-model` YANG YAML で記述したネットワークトポロジを、LLM (OpenAI / Anthropic / Ollama) を通じて Graphviz の構成図（PNG / SVG）に自動変換するツールです。
生成した図を自動評価し、スコアが閾値に達するまで自律的に改善するループ構造を持ちます。

```
YAML (iida-network-model)
        │
        ▼
    parser.py        ← トポロジを構造化テキストに変換
        │
        ▼
   generator.py      ← LLM に DOT コードを生成させる
        │
        ▼
   evaluator.py      ← ルールベース + LLM で品質評価（1〜10点）
        │  score < threshold
        ▼
   improver (pipeline.py) ← LLM に改善させてループ
        │  score ≥ threshold or max_iter
        ▼
    renderer.py      ← DOT → PNG / SVG
        │
        ▼
   visualizer.py     ← スコア推移グラフ (score_history.png)
```

## 必要環境

| 項目 | バージョン |
|------|-----------|
| Python | 3.11 以上 |
| Graphviz (system) | 2.40 以上 |
| LLM API キー | OpenAI / Anthropic / Ollama のいずれか |

Graphviz のインストール（Ubuntu/Debian）：

```bash
sudo apt install graphviz
```

絵文字アイコン（🌐 🔀 🔌 🧱 💻）を図に表示するには、絵文字フォントも必要です。
未インストールだと `01F310` のようなコードポイントの箱で表示されます。

```bash
# システム全体にインストールする場合
sudo apt install fonts-noto-color-emoji

# sudo が使えない場合はユーザー領域に配置
mkdir -p ~/.local/share/fonts
curl -fsSL -o ~/.local/share/fonts/NotoColorEmoji.ttf \
  https://github.com/googlefonts/noto-emoji/raw/main/fonts/NotoColorEmoji.ttf
fc-cache -f
```

## セットアップ

```bash
# 1. リポジトリのクローン
git clone https://github.com/yourname/d2v.git
cd d2v

# 2. 仮想環境の作成とパッケージインストール
python -m venv .venv
source .venv/bin/activate
pip install -e .

# 3. 環境変数ファイルの作成
cp .env.example .env
# .env を編集して API キーと LLM_PROVIDER を設定
```

### `.env` の設定例

```dotenv
# OpenAI を使う場合
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# Anthropic を使う場合
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# ローカル Ollama を使う場合
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:70b
```

## 使い方

```bash
python main.py --input examples/sample_topology_small.yaml
```

```
オプション:
  -i, --input TOPOLOGY_YAML   入力 YAML ファイルのパス（必須）
  -o, --output-dir DIR        出力ディレクトリ（デフォルト: output）
  -f, --format {png,svg}      出力フォーマット（デフォルト: png）
  -n, --max-iter N            最大イテレーション数（デフォルト: 3）
  -t, --threshold SCORE       合格スコア閾値 1〜10（デフォルト: 8）
```

### 実行例

```bash
# 小規模トポロジ（7 ノード）
python main.py -i examples/sample_topology_small.yaml

# 中規模トポロジ（23 ノード）、最大 5 回改善
python main.py -i examples/sample_topology_medium.yaml -n 5

# SVG で出力、閾値 9 点
python main.py -i examples/sample_topology_small.yaml -f svg -t 9
```

### 出力ファイル

```
output/
├── iter_00/
│   ├── <stem>.dot          ← DOT ソースファイル
│   ├── <stem>.png          ← 生成画像
│   └── eval_iter00.json    ← 評価結果 JSON
├── iter_01/
│   └── ...
├── <stem>_best.png         ← 最高スコアの画像（コピー）
└── score_history.png       ← スコア推移グラフ（2 回以上の場合）
```

## トポロジ YAML の書き方

`iida-network-model` フォーマットに従って YAML を記述します。
YANG モデル定義: [`yang/iida-network-model.yang`](yang/iida-network-model.yang)

```yaml
network-model:
  physical-layer:
    device:
      - device-id: "router-01"
        device-name: "Internet Router"
        device-type: router        # router / switch / server / firewall / host / load-balancer
        zone: wan-edge             # subgraph cluster のゾーン名（任意）
        asn: 65000
        loopback: "10.0.0.1/32"
        interface:
          - interface-id: "GigabitEthernet0/1"
            description: "To Firewall"
            ip-address: "10.1.0.1/30"
      # ... 他のデバイス

    physical-connection:
      - connection-id: "link-01"
        endpoint:
          - device-id: "router-01"
            interface-id: "GigabitEthernet0/1"
          - device-id: "fw-01"
            interface-id: "GigabitEthernet0/0"
      # ... 他のリンク
```

サンプルファイル:
- [`examples/sample_topology_small.yaml`](examples/sample_topology_small.yaml) — 7 ノード / 4 ゾーン
- [`examples/sample_topology_medium.yaml`](examples/sample_topology_medium.yaml) — 23 ノード / 6 ゾーン

## プロジェクト構成

```
d2v/
├── main.py                        ← CLI エントリポイント
├── src/d2v/
│   ├── config.py                  ← pydantic-settings による設定管理
│   ├── parser.py                  ← YAML → 構造化テキスト
│   ├── generator.py               ← LLM → DOT コード生成
│   ├── renderer.py                ← DOT → PNG / SVG
│   ├── evaluator.py               ← 品質評価（ルールベース + LLM）
│   ├── pipeline.py                ← 生成→評価→改善ループ
│   ├── visualizer.py              ← スコア推移グラフ（matplotlib）
│   └── llm/                       ← LLM クライアント層
│       ├── __init__.py            ← get_llm() ファクトリ関数
│       ├── base.py                ← LLMClient 抽象基底クラス
│       ├── openai_client.py
│       ├── anthropic_client.py
│       └── ollama_client.py
├── prompts/
│   ├── diagram-system.md          ← DOT 生成システムプロンプト
│   ├── diagram-evaluator.md       ← 評価プロンプト（10 点満点）
│   └── diagram-improver.md        ← 改善プロンプト
├── examples/
│   ├── sample_topology_small.yaml
│   └── sample_topology_medium.yaml
└── yang/
    └── iida-network-model.yang    ← YANG モデル定義
```

## 評価基準

LLM は以下の観点で 10 点満点で評価します。ルールベースチェックでペナルティが加算されます。

| 観点 | 配点 |
|------|------|
| 完全性（ノード・リンクの欠落なし） | 3 点 |
| ラベル網羅性（taillabel / headlabel / IP） | 3 点 |
| ゾーン分類（subgraph cluster の適切な設定） | 2 点 |
| デザイン品質（視認性・線の交差最小化） | 2 点 |

## ライセンス

MIT
