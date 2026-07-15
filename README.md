# d2v — Diagram to Vision

`iida-network-model` YANG YAML で記述したネットワークトポロジを、LLM (OpenAI / Azure OpenAI / Anthropic / Ollama) を通じて Graphviz の構成図（PNG / SVG）に自動変換するツールです。
生成した図を自動評価し、スコアが閾値に達するまで自律的に改善するループ構造を持ちます。

逆方向の **v2d（vision-to-diagram）** も同梱しており、構成図の**画像**から `iida-network-model` YAML を生成できます（[v2d のセクション](#v2d--画像からトポロジ-yaml-を生成vision-to-diagram)を参照）。

```
YAML (iida-network-model)
        │
        ▼
    parser.py        ← トポロジを構造化テキストに変換
        │
        ▼
  partitioner.py     ← 大規模時に zone 単位で俯瞰図＋詳細図に自動分割
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
  --split-threshold N         このノード数を超え、かつ zone 情報がある場合に
                              俯瞰図＋ゾーン詳細図へ自動分割（デフォルト: 40）
  --no-split                  自動分割を無効化し、常に 1 枚の図として生成する
  --zone-opacity 0.0-1.0      ゾーン（cluster）背景色の不透明度。背景が濃いときに
                              下げると淡くなる（1.0=不透明。デフォルト: 0.4）
```

### 大規模トポロジの自動分割（俯瞰図＋ゾーン詳細）

ノード数が `--split-threshold`（デフォルト 40）を超え、かつ各デバイスに `zone` が
設定されている場合、図を複数枚に自動分割します。

- **俯瞰図（overview）**: 各ゾーンを 1 つのまとまりに集約し、ゾーン間のリンクを
  本数付きで示す全体地図。
- **ゾーン詳細図**: ゾーンごとの内部詳細。他ゾーンへ跨る接続は「外部ゾーン参照
  ノード（境界スタブ）」として破線で描画され、各図が自己完結します。関連する
  L3 サブネットも自動抽出されます。

分割することで 1 枚あたりのノード数・トークン量が減り、可読性の向上と LLM の
レート制限（TPM）緩和の両方に効きます。しきい値以下、または `zone` 未設定の
トポロジは従来どおり 1 枚で生成されます。

```bash
# 大規模トポロジ（73 ノード）→ 俯瞰図＋ゾーン詳細に自動分割
python main.py -i examples/sample_topology_large.yaml

# 分割を無効化して 1 枚で生成
python main.py -i examples/sample_topology_large.yaml --no-split

# 分割しきい値を 20 ノードに引き下げ
python main.py -i examples/sample_topology_large.yaml --split-threshold 20
```

### 実行例

```bash
# 小規模トポロジ（7 ノード）
python main.py -i examples/sample_topology_small.yaml

# 中規模トポロジ（23 ノード）、最大 5 回改善
python main.py -i examples/sample_topology_medium.yaml -n 5

# 大規模トポロジ（73 ノード）、zone 単位で自動分割
python main.py -i examples/sample_topology_large.yaml

# SVG で出力、閾値 9 点
python main.py -i examples/sample_topology_small.yaml -f svg -t 9
```

### 生成例（ギャラリー）

以下は上記コマンドで実際に生成した図です。矢印なしの物理リンク（ポート名・IP セグメント付き）、
淡いパステルのゾーン背景、バランスの取れた縦横比で描画されます。

#### 小規模トポロジ（7 ノード）

```bash
python main.py -i examples/sample_topology_small.yaml
```

![小規模トポロジの構成図](images/sample_topology_small_best.png)

#### 中規模トポロジ（23 ノード）

```bash
python main.py -i examples/sample_topology_medium.yaml -n 5
```

![中規模トポロジの構成図](images/sample_topology_medium_best.png)

#### 大規模トポロジ（73 ノード・自動分割）

ノード数がしきい値を超えると、全体を俯瞰する **俯瞰図** と、ゾーンごとの **詳細図** に自動分割されます。

```bash
python main.py -i examples/sample_topology_large.yaml
```

**俯瞰図（ゾーン単位の全体地図）**

![大規模トポロジの俯瞰図](images/sample_topology_large_overview.png)

**ゾーン詳細図**

代表的なゾーンを 3 つピックアップして掲載します。

**DC Fabric（Leaf/Spine）** — 多数の外部接続をゾーン集約ノードにまとめ、`rankdir=LR` で縦積みに調整

![DC Fabric ゾーン詳細図](images/sample_topology_large_zone-dc-fabric.png)

**DC Server（サーバ群）**

![DC Server ゾーン詳細図](images/sample_topology_large_zone-dc-server.png)

**DMZ（公開サーバ領域）**

![DMZ ゾーン詳細図](images/sample_topology_large_zone-dmz.png)

その他のゾーン詳細図:

| ゾーン | 図 |
|--------|-----|
| WAN Edge | [wan-edge](images/sample_topology_large_zone-wan-edge.png) |
| Security | [security](images/sample_topology_large_zone-security.png) |
| DC Core | [dc-core](images/sample_topology_large_zone-dc-core.png) |
| Campus 棟A / 棟B / 棟C | [bldg-a](images/sample_topology_large_zone-campus-bldg-a.png) / [bldg-b](images/sample_topology_large_zone-campus-bldg-b.png) / [bldg-c](images/sample_topology_large_zone-campus-bldg-c.png) |
| Management | [management](images/sample_topology_large_zone-management.png) |

多数の外部デバイスと接続するゾーン（DC Fabric など）は、他ゾーンへの境界を
「ゾーン集約ノード」にまとめ、横長になりすぎないよう `rankdir=LR` で縦積みに調整されます。

#### スコア推移

改善ループを 2 回以上行った場合、イテレーションごとのスコア推移グラフが出力されます。

![スコア推移グラフ](images/score_history.png)

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

分割時（`--split-threshold` 超過）は、図ごとにサブディレクトリを作成し、
ベスト画像を出力ルートに集約します。

```
output/
├── overview/               ← 俯瞰図の iter_NN・評価結果
├── zone-<zone名>/          ← 各ゾーン詳細図の iter_NN・評価結果
│   └── ...
├── <stem>_overview.png     ← 俯瞰図（ベスト）
├── <stem>_zone-<zone名>.png ← 各ゾーン詳細図（ベスト）
└── ...
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
- [`examples/sample_topology_large.yaml`](examples/sample_topology_large.yaml) — 73 ノード / 10 ゾーン（自動分割の対象）

## プロジェクト構成

```
d2v/
├── main.py                        ← CLI エントリポイント
├── src/d2v/
│   ├── config.py                  ← pydantic-settings による設定管理
│   ├── parser.py                  ← YAML → 構造化テキスト（TopologyModel）
│   ├── partitioner.py             ← zone 単位の俯瞰図＋詳細図への自動分割
│   ├── generator.py               ← LLM → DOT コード生成
│   ├── renderer.py                ← DOT → PNG / SVG
│   ├── evaluator.py               ← 品質評価（ルールベース + LLM）
│   ├── pipeline.py                ← 生成→評価→改善ループ
│   ├── visualizer.py              ← スコア推移グラフ（matplotlib）
│   ├── llm/                       ← LLM クライアント層
│   │   ├── __init__.py            ← get_llm() ファクトリ関数
│   │   ├── base.py                ← LLMClient 抽象基底クラス（vision 対応）
│   │   ├── openai_client.py
│   │   ├── azure_openai_client.py ← Azure OpenAI（api-key ヘッダー方式）
│   │   ├── anthropic_client.py
│   │   └── ollama_client.py
│   └── v2d/                       ← vision-to-diagram（画像 → YAML）
│       ├── preprocess.py          ← 画像の正規化・データURL化
│       ├── schema.py              ← 中間表現（ExtractedDiagram）
│       ├── extractor.py           ← vision LLM で画像 → 中間表現
│       ├── refine.py              ← 抽出結果の整合性補正
│       ├── converter.py           ← 中間表現 → iida-network-model YAML
│       ├── evaluate.py            ← 抽出精度の計測・d2v 再描画
│       └── pipeline.py            ← 画像 → YAML の一連フロー
├── prompts/
│   ├── diagram-system.md          ← DOT 生成システムプロンプト
│   ├── diagram-system-overview.md ← 俯瞰図用の生成プロンプト
│   ├── diagram-evaluator.md       ← 評価プロンプト（10 点満点）
│   ├── diagram-evaluator-overview.md ← 俯瞰図用の評価プロンプト
│   ├── diagram-improver.md        ← 改善プロンプト
│   └── v2d-extract.md             ← 画像 → 中間表現 抽出プロンプト
├── examples/
│   ├── sample_topology_small.yaml
│   ├── sample_topology_medium.yaml
│   └── sample_topology_large.yaml
├── tests/                         ← v2d の単体・回帰テスト（pytest）
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

## v2d — 画像からトポロジ YAML を生成（vision-to-diagram）

d2v の逆変換です。ネットワーク構成図の**画像（PNG / JPEG）**をマルチモーダル LLM で解析し、
`iida-network-model` YAML を生成します。出力は d2v の入力と同一スキーマなので、
**画像 → v2d → YAML → d2v → 図** の往復ループが可能です。

```
構成図画像 (PNG/JPEG)
        │
        ▼
   preprocess.py    ← 向き補正・RGB化・リサイズ・データURL化
        │
        ▼
   extractor.py     ← vision LLM で中間表現（ノード/エッジ/ゾーン）を抽出
        │
        ▼
   refine.py        ← 重複マージ・不整合エッジ除去・zone 補完
        │
        ▼
   converter.py     ← iida-network-model YAML へ変換
        │
        ▼
   evaluate.py      ← 正解との一致率計測 / d2v で再描画
```

### 使い方

```bash
# 画像からトポロジ YAML を生成
python main.py v2d --input diagram.png

# 出力先を指定
python main.py v2d -i diagram.png -o output/v2d

# 正解 YAML を指定して抽出精度を計測
python main.py v2d -i diagram.png -t examples/sample_topology_small.yaml

# 生成した YAML を d2v で再描画し往復ループを閉じる
python main.py v2d -i diagram.png --rerender
```

```
オプション:
  -i, --input IMAGE       入力画像ファイル（PNG / JPEG・必須）
  -o, --output-dir DIR    出力ディレクトリ（デフォルト: output/v2d）
  -t, --truth YAML        正解トポロジ YAML（指定すると抽出精度を計測）
      --rerender          生成 YAML を d2v で再描画（LLM を使用）
  -f, --format {png,svg}  再描画時の出力フォーマット（デフォルト: png）
```

> v2d は画像入力に対応した LLM が必要です（例: Azure OpenAI gpt-4o / gpt-5.x）。
> `.env` の `LLM_PROVIDER` を vision 対応プロバイダーに設定してください。

### 出力ファイル

```
output/v2d/
├── <stem>.yaml         ← 抽出した iida-network-model YAML（d2v で再描画可能）
└── <stem>.v2d.json     ← サイドカー（確信度・所見・補正内容・カウント）
```

サイドカー JSON には、総合確信度・ノード/エッジ/ゾーン数・d2v での再パース結果カウント・
整合性補正の内容（マージ/除去）・読み取れなかった箇所の所見・低確信度ノードが記録されます。

### 入力サンプル

d2v が生成した図（[`images/`](images/)）をそのまま v2d の入力に使えます（往復テスト）。

```bash
python main.py v2d -i images/sample_topology_small_best.png \
  -t examples/sample_topology_small.yaml --rerender
```

### 対応範囲と制約

**対応**: 矩形ノード＋直線/直交コネクタで描かれた構成図（draw.io / PowerPoint 書き出し /
Graphviz 由来 / d2v 出力）。ホスト名・IP・ポート名・ゾーン枠を持つもの。目安 30 ノード以下。

**制約・非対応**:
- 手描き・写真撮影で歪みや傾きが大きい画像
- 曲線が多用され交差が激しい「蜘蛛の巣」状の配線
- テキストを持たずアイコンだけのノード
- 50 ノード超の大規模図
- 幅 800px 未満（文字認識精度が低下。1200px 以上を推奨）

**既知の限界**: 微小な文字（loopback IP など）は誤読することがあります。読み取れない値は
捏造せず省略し、サイドカーの所見に記録します。構造（ノード・エッジ）は高精度で、
評価では **ノード F1 = 1.00 / エッジ F1 ≥ 0.83** を達成しています。

## ライセンス

MIT
