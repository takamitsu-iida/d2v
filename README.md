# d2v — Network Diagram Generator

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



```
(.venv) iida@s400win:~/git/d2v$ ./main.py -i examples/sample_topology_large.yaml
╭──────── d2v  ネットワーク構成図ジェネレーター ─────────╮
│ 入力ファイル     : examples/sample_topology_large.yaml │
│ 出力ディレクトリ : output                              │
│ フォーマット     : png                                 │
│ 最大イテレーション: 3                                  │
│ 合格スコア閾値   : 8/10                                │
╰────────────────────────────────────────────────────────╯
──────────────────────────────────────────────────────────────────── Step 1  トポロジ解析 ────────────────────────────────────────────────────────────────────
## ノード一覧（73 台）

- inet-rtr-01 (Internet Edge Router #1)
    GigabitEthernet0/0  203.0.113.1/30  # To ISP
    Ethernet1/1  10.1.0.1/30  # Inter-router keepalive
    Ethernet1/2  10.1.0.9/30  # To FW-01
- inet-rtr-02 (Internet Edge Router #2)
    GigabitEthernet0/0  203.0.113.2/30  # To ISP
    Ethernet1/1  10.1.0.2/30  # Inter-router keepalive
    Ethernet1/2  10.1.0.13/30  # To FW-02
- fw-01 (Perimeter Firewall #1)
    Ethernet1/1  10.1.0.5/30  # HA sync link
    Ethernet1/2  10.1.0.10/30  # To Router-01
    Ethernet1/3  10.1.0.17/30  # To Spine-01
    Ethernet1/4  10.1.0.153/30  # To DMZ-SW-01
- fw-02 (Perimeter Firewall #2)
    Ethernet1/1  10.1.0.6/30  # HA sync link
    Ethernet1/2  10.1.0.14/30  # To Router-02
    Ethernet1/3  10.1.0.21/30  # To Spine-02
    Ethernet1/4  10.1.0.157/30  # To DMZ-SW-02
- spine-01 (DC Spine Switch #1)
    Ethernet1/1  10.1.0.18/30  # To FW-01
    Ethernet1/2  10.1.0.26/30  # Downlink to leaf-01
    Ethernet1/3  10.1.0.42/30  # Downlink to leaf-02
    Ethernet1/4  10.1.0.58/30  # Downlink to leaf-03
    Ethernet1/5  10.1.0.74/30  # Downlink to leaf-04
    Ethernet1/6  10.1.0.90/30  # Downlink to leaf-05
    Ethernet1/7  10.1.0.106/30  # Downlink to leaf-06
    Ethernet1/8  10.1.0.122/30  # Downlink to leaf-07
    Ethernet1/9  10.1.0.138/30  # Downlink to leaf-08
    Ethernet1/10  10.1.0.162/30  # To bldga-dist-01
    Ethernet1/11  10.1.0.170/30  # To bldgb-dist-01
    Ethernet1/12  10.1.0.178/30  # To bldgc-dist-01
- spine-02 (DC Spine Switch #2)
    Ethernet1/1  10.1.0.22/30  # To FW-02
    Ethernet1/2  10.1.0.30/30  # Downlink to leaf-01
    Ethernet1/3  10.1.0.46/30  # Downlink to leaf-02
    Ethernet1/4  10.1.0.62/30  # Downlink to leaf-03
    Ethernet1/5  10.1.0.78/30  # Downlink to leaf-04
    Ethernet1/6  10.1.0.94/30  # Downlink to leaf-05
    Ethernet1/7  10.1.0.110/30  # Downlink to leaf-06
    Ethernet1/8  10.1.0.126/30  # Downlink to leaf-07
    Ethernet1/9  10.1.0.142/30  # Downlink to leaf-08
    Ethernet1/10  10.1.0.166/30  # To bldga-dist-01
    Ethernet1/11  10.1.0.174/30  # To bldgb-dist-01
    Ethernet1/12  10.1.0.182/30  # To bldgc-dist-01
- spine-03 (DC Spine Switch #3)
    Ethernet1/1  10.1.0.34/30  # Downlink to leaf-01
    Ethernet1/2  10.1.0.50/30  # Downlink to leaf-02
    Ethernet1/3  10.1.0.66/30  # Downlink to leaf-03
    Ethernet1/4  10.1.0.82/30  # Downlink to leaf-04
    Ethernet1/5  10.1.0.98/30  # Downlink to leaf-05
    Ethernet1/6  10.1.0.114/30  # Downlink to leaf-06
    Ethernet1/7  10.1.0.130/30  # Downlink to leaf-07
    Ethernet1/8  10.1.0.146/30  # Downlink to leaf-08
    Ethernet1/9  10.1.0.185/30  # To Mgmt-SW
- spine-04 (DC Spine Switch #4)
    Ethernet1/1  10.1.0.38/30  # Downlink to leaf-01
    Ethernet1/2  10.1.0.54/30  # Downlink to leaf-02
    Ethernet1/3  10.1.0.70/30  # Downlink to leaf-03
    Ethernet1/4  10.1.0.86/30  # Downlink to leaf-04
    Ethernet1/5  10.1.0.102/30  # Downlink to leaf-05
    Ethernet1/6  10.1.0.118/30  # Downlink to leaf-06
    Ethernet1/7  10.1.0.134/30  # Downlink to leaf-07
    Ethernet1/8  10.1.0.150/30  # Downlink to leaf-08
- leaf-01 (DC Leaf Switch #1)
    Ethernet1/1  10.1.0.25/30  # Uplink to spine-01
    Ethernet1/2  10.1.0.29/30  # Uplink to spine-02
    Ethernet1/3  10.1.0.33/30  # Uplink to spine-03
    Ethernet1/4  10.1.0.37/30  # Uplink to spine-04
    Ethernet1/5  # To srv-p1-web-01
    Ethernet1/6  # To srv-p1-app-01
    Ethernet1/7  # To srv-p1-db-01
    Ethernet1/8  # To srv-p1-cache-01
- leaf-02 (DC Leaf Switch #2)
    Ethernet1/1  10.1.0.41/30  # Uplink to spine-01
    Ethernet1/2  10.1.0.45/30  # Uplink to spine-02
    Ethernet1/3  10.1.0.49/30  # Uplink to spine-03
    Ethernet1/4  10.1.0.53/30  # Uplink to spine-04
    Ethernet1/5  # To srv-p1-web-01
    Ethernet1/6  # To srv-p1-app-01
    Ethernet1/7  # To srv-p1-db-01
    Ethernet1/8  # To srv-p1-cache-01
- leaf-03 (DC Leaf Switch #3)
    Ethernet1/1  10.1.0.57/30  # Uplink to spine-01
    Ethernet1/2  10.1.0.61/30  # Uplink to spine-02
    Ethernet1/3  10.1.0.65/30  # Uplink to spine-03
    Ethernet1/4  10.1.0.69/30  # Uplink to spine-04
    Ethernet1/5  # To srv-p2-web-01
    Ethernet1/6  # To srv-p2-app-01
    Ethernet1/7  # To srv-p2-db-01
    Ethernet1/8  # To srv-p2-cache-01
- leaf-04 (DC Leaf Switch #4)
    Ethernet1/1  10.1.0.73/30  # Uplink to spine-01
    Ethernet1/2  10.1.0.77/30  # Uplink to spine-02
    Ethernet1/3  10.1.0.81/30  # Uplink to spine-03
    Ethernet1/4  10.1.0.85/30  # Uplink to spine-04
    Ethernet1/5  # To srv-p2-web-01
    Ethernet1/6  # To srv-p2-app-01
    Ethernet1/7  # To srv-p2-db-01
    Ethernet1/8  # To srv-p2-cache-01
- leaf-05 (DC Leaf Switch #5)
    Ethernet1/1  10.1.0.89/30  # Uplink to spine-01
    Ethernet1/2  10.1.0.93/30  # Uplink to spine-02
    Ethernet1/3  10.1.0.97/30  # Uplink to spine-03
    Ethernet1/4  10.1.0.101/30  # Uplink to spine-04
    Ethernet1/5  # To srv-p3-web-01
    Ethernet1/6  # To srv-p3-app-01
    Ethernet1/7  # To srv-p3-db-01
    Ethernet1/8  # To srv-p3-cache-01
- leaf-06 (DC Leaf Switch #6)
    Ethernet1/1  10.1.0.105/30  # Uplink to spine-01
    Ethernet1/2  10.1.0.109/30  # Uplink to spine-02
    Ethernet1/3  10.1.0.113/30  # Uplink to spine-03
    Ethernet1/4  10.1.0.117/30  # Uplink to spine-04
    Ethernet1/5  # To srv-p3-web-01
    Ethernet1/6  # To srv-p3-app-01
    Ethernet1/7  # To srv-p3-db-01
    Ethernet1/8  # To srv-p3-cache-01
- leaf-07 (DC Leaf Switch #7)
    Ethernet1/1  10.1.0.121/30  # Uplink to spine-01
    Ethernet1/2  10.1.0.125/30  # Uplink to spine-02
    Ethernet1/3  10.1.0.129/30  # Uplink to spine-03
    Ethernet1/4  10.1.0.133/30  # Uplink to spine-04
    Ethernet1/5  # To srv-p4-web-01
    Ethernet1/6  # To srv-p4-app-01
    Ethernet1/7  # To srv-p4-db-01
    Ethernet1/8  # To srv-p4-cache-01
- leaf-08 (DC Leaf Switch #8)
    Ethernet1/1  10.1.0.137/30  # Uplink to spine-01
    Ethernet1/2  10.1.0.141/30  # Uplink to spine-02
    Ethernet1/3  10.1.0.145/30  # Uplink to spine-03
    Ethernet1/4  10.1.0.149/30  # Uplink to spine-04
    Ethernet1/5  # To srv-p4-web-01
    Ethernet1/6  # To srv-p4-app-01
    Ethernet1/7  # To srv-p4-db-01
    Ethernet1/8  # To srv-p4-cache-01
- srv-p1-web-01 (Pod1 Web Server)
    eth/1  10.20.1.11/24  # NIC primary
    eth/2  # NIC standby
- srv-p1-app-01 (Pod1 App Server)
    eth/1  10.20.1.12/24  # NIC primary
    eth/2  # NIC standby
- srv-p1-db-01 (Pod1 Database Server)
    eth/1  10.20.1.13/24  # NIC primary
    eth/2  # NIC standby
- srv-p1-cache-01 (Pod1 Cache Server)
    eth/1  10.20.1.14/24  # NIC primary
    eth/2  # NIC standby
- srv-p2-web-01 (Pod2 Web Server)
    eth/1  10.20.2.11/24  # NIC primary
    eth/2  # NIC standby
- srv-p2-app-01 (Pod2 App Server)
    eth/1  10.20.2.12/24  # NIC primary
    eth/2  # NIC standby
- srv-p2-db-01 (Pod2 Database Server)
    eth/1  10.20.2.13/24  # NIC primary
    eth/2  # NIC standby
- srv-p2-cache-01 (Pod2 Cache Server)
    eth/1  10.20.2.14/24  # NIC primary
    eth/2  # NIC standby
- srv-p3-web-01 (Pod3 Web Server)
    eth/1  10.20.3.11/24  # NIC primary
    eth/2  # NIC standby
- srv-p3-app-01 (Pod3 App Server)
    eth/1  10.20.3.12/24  # NIC primary
    eth/2  # NIC standby
- srv-p3-db-01 (Pod3 Database Server)
    eth/1  10.20.3.13/24  # NIC primary
    eth/2  # NIC standby
- srv-p3-cache-01 (Pod3 Cache Server)
    eth/1  10.20.3.14/24  # NIC primary
    eth/2  # NIC standby
- srv-p4-web-01 (Pod4 Web Server)
    eth/1  10.20.4.11/24  # NIC primary
    eth/2  # NIC standby
- srv-p4-app-01 (Pod4 App Server)
    eth/1  10.20.4.12/24  # NIC primary
    eth/2  # NIC standby
- srv-p4-db-01 (Pod4 Database Server)
    eth/1  10.20.4.13/24  # NIC primary
    eth/2  # NIC standby
- srv-p4-cache-01 (Pod4 Cache Server)
    eth/1  10.20.4.14/24  # NIC primary
    eth/2  # NIC standby
- dmz-sw-01 (DMZ Switch #1)
    Ethernet1/1  10.1.0.154/30  # To FW-01
    Ethernet1/2  # DMZ inter-switch trunk
    Ethernet1/3  # To dmz-web-01
    Ethernet1/4  # To dmz-dns-01
- dmz-sw-02 (DMZ Switch #2)
    Ethernet1/1  10.1.0.158/30  # To FW-02
    Ethernet1/2  # DMZ inter-switch trunk
    Ethernet1/3  # To dmz-mail-01
    Ethernet1/4  # To dmz-proxy-01
- dmz-web-01 (Public Web Server)
    eth/1  10.30.0.11/24  # DMZ NIC
- dmz-mail-01 (Mail Gateway)
    eth/1  10.30.0.12/24  # DMZ NIC
- dmz-dns-01 (DNS Server)
    eth/1  10.30.0.13/24  # DMZ NIC
- dmz-proxy-01 (Reverse Proxy)
    eth/1  10.30.0.14/24  # DMZ NIC
- bldga-dist-01 (Building A Distribution L3SW)
    Ethernet1/1  10.1.0.161/30  # Uplink to Core-1
    Ethernet1/2  10.1.0.165/30  # Uplink to Core-2
    Ethernet1/3  # To Floor1 SW
    Ethernet1/4  # To Floor2 SW
    Ethernet1/5  # To Floor3 SW
- bldga-acc-01 (Building A Floor1 Access SW)
    Ethernet1/1  # Uplink to Dist
    Ethernet1/2  # To bldga-f1-pc-01
    Ethernet1/3  # To bldga-f1-pc-02
- bldga-f1-pc-01 (Building A F1 Client PC 1)
    eth/1  10.41.1.11/24  # LAN
- bldga-f1-pc-02 (Building A F1 Client PC 2)
    eth/1  10.41.1.12/24  # LAN
- bldga-acc-02 (Building A Floor2 Access SW)
    Ethernet1/1  # Uplink to Dist
    Ethernet1/2  # To bldga-f2-pc-01
    Ethernet1/3  # To bldga-f2-pc-02
- bldga-f2-pc-01 (Building A F2 Client PC 1)
    eth/1  10.41.2.11/24  # LAN
- bldga-f2-pc-02 (Building A F2 Client PC 2)
    eth/1  10.41.2.12/24  # LAN
- bldga-acc-03 (Building A Floor3 Access SW)
    Ethernet1/1  # Uplink to Dist
    Ethernet1/2  # To bldga-f3-pc-01
    Ethernet1/3  # To bldga-f3-pc-02
- bldga-f3-pc-01 (Building A F3 Client PC 1)
    eth/1  10.41.3.11/24  # LAN
- bldga-f3-pc-02 (Building A F3 Client PC 2)
    eth/1  10.41.3.12/24  # LAN
- bldgb-dist-01 (Building B Distribution L3SW)
    Ethernet1/1  10.1.0.169/30  # Uplink to Core-1
    Ethernet1/2  10.1.0.173/30  # Uplink to Core-2
    Ethernet1/3  # To Floor1 SW
    Ethernet1/4  # To Floor2 SW
    Ethernet1/5  # To Floor3 SW
- bldgb-acc-01 (Building B Floor1 Access SW)
    Ethernet1/1  # Uplink to Dist
    Ethernet1/2  # To bldgb-f1-pc-01
    Ethernet1/3  # To bldgb-f1-pc-02
- bldgb-f1-pc-01 (Building B F1 Client PC 1)
    eth/1  10.42.1.11/24  # LAN
- bldgb-f1-pc-02 (Building B F1 Client PC 2)
    eth/1  10.42.1.12/24  # LAN
- bldgb-acc-02 (Building B Floor2 Access SW)
    Ethernet1/1  # Uplink to Dist
    Ethernet1/2  # To bldgb-f2-pc-01
    Ethernet1/3  # To bldgb-f2-pc-02
- bldgb-f2-pc-01 (Building B F2 Client PC 1)
    eth/1  10.42.2.11/24  # LAN
- bldgb-f2-pc-02 (Building B F2 Client PC 2)
    eth/1  10.42.2.12/24  # LAN
- bldgb-acc-03 (Building B Floor3 Access SW)
    Ethernet1/1  # Uplink to Dist
    Ethernet1/2  # To bldgb-f3-pc-01
    Ethernet1/3  # To bldgb-f3-pc-02
- bldgb-f3-pc-01 (Building B F3 Client PC 1)
    eth/1  10.42.3.11/24  # LAN
- bldgb-f3-pc-02 (Building B F3 Client PC 2)
    eth/1  10.42.3.12/24  # LAN
- bldgc-dist-01 (Building C Distribution L3SW)
    Ethernet1/1  10.1.0.177/30  # Uplink to Core-1
    Ethernet1/2  10.1.0.181/30  # Uplink to Core-2
    Ethernet1/3  # To Floor1 SW
    Ethernet1/4  # To Floor2 SW
    Ethernet1/5  # To Floor3 SW
- bldgc-acc-01 (Building C Floor1 Access SW)
    Ethernet1/1  # Uplink to Dist
    Ethernet1/2  # To bldgc-f1-pc-01
    Ethernet1/3  # To bldgc-f1-pc-02
- bldgc-f1-pc-01 (Building C F1 Client PC 1)
    eth/1  10.43.1.11/24  # LAN
- bldgc-f1-pc-02 (Building C F1 Client PC 2)
    eth/1  10.43.1.12/24  # LAN
- bldgc-acc-02 (Building C Floor2 Access SW)
    Ethernet1/1  # Uplink to Dist
    Ethernet1/2  # To bldgc-f2-pc-01
    Ethernet1/3  # To bldgc-f2-pc-02
- bldgc-f2-pc-01 (Building C F2 Client PC 1)
    eth/1  10.43.2.11/24  # LAN
- bldgc-f2-pc-02 (Building C F2 Client PC 2)
    eth/1  10.43.2.12/24  # LAN
- bldgc-acc-03 (Building C Floor3 Access SW)
    Ethernet1/1  # Uplink to Dist
    Ethernet1/2  # To bldgc-f3-pc-01
    Ethernet1/3  # To bldgc-f3-pc-02
- bldgc-f3-pc-01 (Building C F3 Client PC 1)
    eth/1  10.43.3.11/24  # LAN
- bldgc-f3-pc-02 (Building C F3 Client PC 2)
    eth/1  10.43.3.12/24  # LAN
- mgmt-sw-01 (Out-of-Band Mgmt Switch)
    Ethernet1/1  10.1.0.186/30  # To DC Core
    Ethernet1/2  # To mgmt-nms-01
    Ethernet1/3  # To mgmt-syslog-01
    Ethernet1/4  # To mgmt-backup-01
    Ethernet1/5  # To mgmt-radius-01
- mgmt-nms-01 (Network Monitoring Server)
    eth/1  10.50.0.11/24  # Mgmt NIC
- mgmt-syslog-01 (Syslog/Log Server)
    eth/1  10.50.0.12/24  # Mgmt NIC
- mgmt-backup-01 (Config Backup Server)
    eth/1  10.50.0.13/24  # Mgmt NIC
- mgmt-radius-01 (RADIUS/AAA Server)
    eth/1  10.50.0.14/24  # Mgmt NIC

## 物理接続一覧（115 本）

- inet-rtr-01[Ethernet1/1](10.1.0.1/30)  <-->  inet-rtr-02[Ethernet1/1](10.1.0.2/30)
- fw-01[Ethernet1/1](10.1.0.5/30)  <-->  fw-02[Ethernet1/1](10.1.0.6/30)
- inet-rtr-01[Ethernet1/2](10.1.0.9/30)  <-->  fw-01[Ethernet1/2](10.1.0.10/30)
- inet-rtr-02[Ethernet1/2](10.1.0.13/30)  <-->  fw-02[Ethernet1/2](10.1.0.14/30)
- fw-01[Ethernet1/3](10.1.0.17/30)  <-->  spine-01[Ethernet1/1](10.1.0.18/30)
- fw-02[Ethernet1/3](10.1.0.21/30)  <-->  spine-02[Ethernet1/1](10.1.0.22/30)
- leaf-01[Ethernet1/1](10.1.0.25/30)  <-->  spine-01[Ethernet1/2](10.1.0.26/30)
- leaf-01[Ethernet1/2](10.1.0.29/30)  <-->  spine-02[Ethernet1/2](10.1.0.30/30)
- leaf-01[Ethernet1/3](10.1.0.33/30)  <-->  spine-03[Ethernet1/1](10.1.0.34/30)
- leaf-01[Ethernet1/4](10.1.0.37/30)  <-->  spine-04[Ethernet1/1](10.1.0.38/30)
- leaf-02[Ethernet1/1](10.1.0.41/30)  <-->  spine-01[Ethernet1/3](10.1.0.42/30)
- leaf-02[Ethernet1/2](10.1.0.45/30)  <-->  spine-02[Ethernet1/3](10.1.0.46/30)
- leaf-02[Ethernet1/3](10.1.0.49/30)  <-->  spine-03[Ethernet1/2](10.1.0.50/30)
- leaf-02[Ethernet1/4](10.1.0.53/30)  <-->  spine-04[Ethernet1/2](10.1.0.54/30)
- leaf-03[Ethernet1/1](10.1.0.57/30)  <-->  spine-01[Ethernet1/4](10.1.0.58/30)
- leaf-03[Ethernet1/2](10.1.0.61/30)  <-->  spine-02[Ethernet1/4](10.1.0.62/30)
- leaf-03[Ethernet1/3](10.1.0.65/30)  <-->  spine-03[Ethernet1/3](10.1.0.66/30)
- leaf-03[Ethernet1/4](10.1.0.69/30)  <-->  spine-04[Ethernet1/3](10.1.0.70/30)
- leaf-04[Ethernet1/1](10.1.0.73/30)  <-->  spine-01[Ethernet1/5](10.1.0.74/30)
- leaf-04[Ethernet1/2](10.1.0.77/30)  <-->  spine-02[Ethernet1/5](10.1.0.78/30)
- leaf-04[Ethernet1/3](10.1.0.81/30)  <-->  spine-03[Ethernet1/4](10.1.0.82/30)
- leaf-04[Ethernet1/4](10.1.0.85/30)  <-->  spine-04[Ethernet1/4](10.1.0.86/30)
- leaf-05[Ethernet1/1](10.1.0.89/30)  <-->  spine-01[Ethernet1/6](10.1.0.90/30)
- leaf-05[Ethernet1/2](10.1.0.93/30)  <-->  spine-02[Ethernet1/6](10.1.0.94/30)
- leaf-05[Ethernet1/3](10.1.0.97/30)  <-->  spine-03[Ethernet1/5](10.1.0.98/30)
- leaf-05[Ethernet1/4](10.1.0.101/30)  <-->  spine-04[Ethernet1/5](10.1.0.102/30)
- leaf-06[Ethernet1/1](10.1.0.105/30)  <-->  spine-01[Ethernet1/7](10.1.0.106/30)
- leaf-06[Ethernet1/2](10.1.0.109/30)  <-->  spine-02[Ethernet1/7](10.1.0.110/30)
- leaf-06[Ethernet1/3](10.1.0.113/30)  <-->  spine-03[Ethernet1/6](10.1.0.114/30)
- leaf-06[Ethernet1/4](10.1.0.117/30)  <-->  spine-04[Ethernet1/6](10.1.0.118/30)
- leaf-07[Ethernet1/1](10.1.0.121/30)  <-->  spine-01[Ethernet1/8](10.1.0.122/30)
- leaf-07[Ethernet1/2](10.1.0.125/30)  <-->  spine-02[Ethernet1/8](10.1.0.126/30)
- leaf-07[Ethernet1/3](10.1.0.129/30)  <-->  spine-03[Ethernet1/7](10.1.0.130/30)
- leaf-07[Ethernet1/4](10.1.0.133/30)  <-->  spine-04[Ethernet1/7](10.1.0.134/30)
- leaf-08[Ethernet1/1](10.1.0.137/30)  <-->  spine-01[Ethernet1/9](10.1.0.138/30)
- leaf-08[Ethernet1/2](10.1.0.141/30)  <-->  spine-02[Ethernet1/9](10.1.0.142/30)
- leaf-08[Ethernet1/3](10.1.0.145/30)  <-->  spine-03[Ethernet1/8](10.1.0.146/30)
- leaf-08[Ethernet1/4](10.1.0.149/30)  <-->  spine-04[Ethernet1/8](10.1.0.150/30)
- leaf-01[Ethernet1/5]  <-->  srv-p1-web-01(10.20.1.11/24)
- leaf-02[Ethernet1/5]  <-->  srv-p1-web-01
- leaf-01[Ethernet1/6]  <-->  srv-p1-app-01(10.20.1.12/24)
- leaf-02[Ethernet1/6]  <-->  srv-p1-app-01
- leaf-01[Ethernet1/7]  <-->  srv-p1-db-01(10.20.1.13/24)
- leaf-02[Ethernet1/7]  <-->  srv-p1-db-01
- leaf-01[Ethernet1/8]  <-->  srv-p1-cache-01(10.20.1.14/24)
- leaf-02[Ethernet1/8]  <-->  srv-p1-cache-01
- leaf-03[Ethernet1/5]  <-->  srv-p2-web-01(10.20.2.11/24)
- leaf-04[Ethernet1/5]  <-->  srv-p2-web-01
- leaf-03[Ethernet1/6]  <-->  srv-p2-app-01(10.20.2.12/24)
- leaf-04[Ethernet1/6]  <-->  srv-p2-app-01
- leaf-03[Ethernet1/7]  <-->  srv-p2-db-01(10.20.2.13/24)
- leaf-04[Ethernet1/7]  <-->  srv-p2-db-01
- leaf-03[Ethernet1/8]  <-->  srv-p2-cache-01(10.20.2.14/24)
- leaf-04[Ethernet1/8]  <-->  srv-p2-cache-01
- leaf-05[Ethernet1/5]  <-->  srv-p3-web-01(10.20.3.11/24)
- leaf-06[Ethernet1/5]  <-->  srv-p3-web-01
- leaf-05[Ethernet1/6]  <-->  srv-p3-app-01(10.20.3.12/24)
- leaf-06[Ethernet1/6]  <-->  srv-p3-app-01
- leaf-05[Ethernet1/7]  <-->  srv-p3-db-01(10.20.3.13/24)
- leaf-06[Ethernet1/7]  <-->  srv-p3-db-01
- leaf-05[Ethernet1/8]  <-->  srv-p3-cache-01(10.20.3.14/24)
- leaf-06[Ethernet1/8]  <-->  srv-p3-cache-01
- leaf-07[Ethernet1/5]  <-->  srv-p4-web-01(10.20.4.11/24)
- leaf-08[Ethernet1/5]  <-->  srv-p4-web-01
- leaf-07[Ethernet1/6]  <-->  srv-p4-app-01(10.20.4.12/24)
- leaf-08[Ethernet1/6]  <-->  srv-p4-app-01
- leaf-07[Ethernet1/7]  <-->  srv-p4-db-01(10.20.4.13/24)
- leaf-08[Ethernet1/7]  <-->  srv-p4-db-01
- leaf-07[Ethernet1/8]  <-->  srv-p4-cache-01(10.20.4.14/24)
- leaf-08[Ethernet1/8]  <-->  srv-p4-cache-01
- fw-01[Ethernet1/4](10.1.0.153/30)  <-->  dmz-sw-01[Ethernet1/1](10.1.0.154/30)
- fw-02[Ethernet1/4](10.1.0.157/30)  <-->  dmz-sw-02[Ethernet1/1](10.1.0.158/30)
- dmz-sw-01[Ethernet1/2]  <-->  dmz-sw-02[Ethernet1/2]
- dmz-sw-01[Ethernet1/3]  <-->  dmz-web-01(10.30.0.11/24)
- dmz-sw-02[Ethernet1/3]  <-->  dmz-mail-01(10.30.0.12/24)
- dmz-sw-01[Ethernet1/4]  <-->  dmz-dns-01(10.30.0.13/24)
- dmz-sw-02[Ethernet1/4]  <-->  dmz-proxy-01(10.30.0.14/24)
- bldga-dist-01[Ethernet1/1](10.1.0.161/30)  <-->  spine-01[Ethernet1/10](10.1.0.162/30)
- bldga-dist-01[Ethernet1/2](10.1.0.165/30)  <-->  spine-02[Ethernet1/10](10.1.0.166/30)
- bldga-dist-01[Ethernet1/3]  <-->  bldga-acc-01[Ethernet1/1]
- bldga-acc-01[Ethernet1/2]  <-->  bldga-f1-pc-01(10.41.1.11/24)
- bldga-acc-01[Ethernet1/3]  <-->  bldga-f1-pc-02(10.41.1.12/24)
- bldga-dist-01[Ethernet1/4]  <-->  bldga-acc-02[Ethernet1/1]
- bldga-acc-02[Ethernet1/2]  <-->  bldga-f2-pc-01(10.41.2.11/24)
- bldga-acc-02[Ethernet1/3]  <-->  bldga-f2-pc-02(10.41.2.12/24)
- bldga-dist-01[Ethernet1/5]  <-->  bldga-acc-03[Ethernet1/1]
- bldga-acc-03[Ethernet1/2]  <-->  bldga-f3-pc-01(10.41.3.11/24)
- bldga-acc-03[Ethernet1/3]  <-->  bldga-f3-pc-02(10.41.3.12/24)
- bldgb-dist-01[Ethernet1/1](10.1.0.169/30)  <-->  spine-01[Ethernet1/11](10.1.0.170/30)
- bldgb-dist-01[Ethernet1/2](10.1.0.173/30)  <-->  spine-02[Ethernet1/11](10.1.0.174/30)
- bldgb-dist-01[Ethernet1/3]  <-->  bldgb-acc-01[Ethernet1/1]
- bldgb-acc-01[Ethernet1/2]  <-->  bldgb-f1-pc-01(10.42.1.11/24)
- bldgb-acc-01[Ethernet1/3]  <-->  bldgb-f1-pc-02(10.42.1.12/24)
- bldgb-dist-01[Ethernet1/4]  <-->  bldgb-acc-02[Ethernet1/1]
- bldgb-acc-02[Ethernet1/2]  <-->  bldgb-f2-pc-01(10.42.2.11/24)
- bldgb-acc-02[Ethernet1/3]  <-->  bldgb-f2-pc-02(10.42.2.12/24)
- bldgb-dist-01[Ethernet1/5]  <-->  bldgb-acc-03[Ethernet1/1]
- bldgb-acc-03[Ethernet1/2]  <-->  bldgb-f3-pc-01(10.42.3.11/24)
- bldgb-acc-03[Ethernet1/3]  <-->  bldgb-f3-pc-02(10.42.3.12/24)
- bldgc-dist-01[Ethernet1/1](10.1.0.177/30)  <-->  spine-01[Ethernet1/12](10.1.0.178/30)
- bldgc-dist-01[Ethernet1/2](10.1.0.181/30)  <-->  spine-02[Ethernet1/12](10.1.0.182/30)
- bldgc-dist-01[Ethernet1/3]  <-->  bldgc-acc-01[Ethernet1/1]
- bldgc-acc-01[Ethernet1/2]  <-->  bldgc-f1-pc-01(10.43.1.11/24)
- bldgc-acc-01[Ethernet1/3]  <-->  bldgc-f1-pc-02(10.43.1.12/24)
- bldgc-dist-01[Ethernet1/4]  <-->  bldgc-acc-02[Ethernet1/1]
- bldgc-acc-02[Ethernet1/2]  <-->  bldgc-f2-pc-01(10.43.2.11/24)
- bldgc-acc-02[Ethernet1/3]  <-->  bldgc-f2-pc-02(10.43.2.12/24)
- bldgc-dist-01[Ethernet1/5]  <-->  bldgc-acc-03[Ethernet1/1]
- bldgc-acc-03[Ethernet1/2]  <-->  bldgc-f3-pc-01(10.43.3.11/24)
- bldgc-acc-03[Ethernet1/3]  <-->  bldgc-f3-pc-02(10.43.3.12/24)
- spine-03[Ethernet1/9](10.1.0.185/30)  <-->  mgmt-sw-01[Ethernet1/1](10.1.0.186/30)
- mgmt-sw-01[Ethernet1/2]  <-->  mgmt-nms-01(10.50.0.11/24)
- mgmt-sw-01[Ethernet1/3]  <-->  mgmt-syslog-01(10.50.0.12/24)
- mgmt-sw-01[Ethernet1/4]  <-->  mgmt-backup-01(10.50.0.13/24)
- mgmt-sw-01[Ethernet1/5]  <-->  mgmt-radius-01(10.50.0.14/24)

## L3 サブネット一覧（62 件）

- 10.1.0.0/30  (Edge router interconnect)
- 10.1.0.4/30  (Firewall HA heartbeat)
- 10.1.0.8/30  (Edge to FW #1)
- 10.1.0.12/30  (Edge to FW #2)
- 10.1.0.16/30  (FW to DC core #1)
- 10.1.0.20/30  (FW to DC core #2)
- 10.1.0.24/30  (Fabric leaf-01 <-> spine-01)
- 10.1.0.28/30  (Fabric leaf-01 <-> spine-02)
- 10.1.0.32/30  (Fabric leaf-01 <-> spine-03)
- 10.1.0.36/30  (Fabric leaf-01 <-> spine-04)
- 10.1.0.40/30  (Fabric leaf-02 <-> spine-01)
- 10.1.0.44/30  (Fabric leaf-02 <-> spine-02)
- 10.1.0.48/30  (Fabric leaf-02 <-> spine-03)
- 10.1.0.52/30  (Fabric leaf-02 <-> spine-04)
- 10.1.0.56/30  (Fabric leaf-03 <-> spine-01)
- 10.1.0.60/30  (Fabric leaf-03 <-> spine-02)
- 10.1.0.64/30  (Fabric leaf-03 <-> spine-03)
- 10.1.0.68/30  (Fabric leaf-03 <-> spine-04)
- 10.1.0.72/30  (Fabric leaf-04 <-> spine-01)
- 10.1.0.76/30  (Fabric leaf-04 <-> spine-02)
- 10.1.0.80/30  (Fabric leaf-04 <-> spine-03)
- 10.1.0.84/30  (Fabric leaf-04 <-> spine-04)
- 10.1.0.88/30  (Fabric leaf-05 <-> spine-01)
- 10.1.0.92/30  (Fabric leaf-05 <-> spine-02)
- 10.1.0.96/30  (Fabric leaf-05 <-> spine-03)
- 10.1.0.100/30  (Fabric leaf-05 <-> spine-04)
- 10.1.0.104/30  (Fabric leaf-06 <-> spine-01)
- 10.1.0.108/30  (Fabric leaf-06 <-> spine-02)
- 10.1.0.112/30  (Fabric leaf-06 <-> spine-03)
- 10.1.0.116/30  (Fabric leaf-06 <-> spine-04)
- 10.1.0.120/30  (Fabric leaf-07 <-> spine-01)
- 10.1.0.124/30  (Fabric leaf-07 <-> spine-02)
- 10.1.0.128/30  (Fabric leaf-07 <-> spine-03)
- 10.1.0.132/30  (Fabric leaf-07 <-> spine-04)
- 10.1.0.136/30  (Fabric leaf-08 <-> spine-01)
- 10.1.0.140/30  (Fabric leaf-08 <-> spine-02)
- 10.1.0.144/30  (Fabric leaf-08 <-> spine-03)
- 10.1.0.148/30  (Fabric leaf-08 <-> spine-04)
- 10.20.1.0/24  (Server Pod 1 LAN)
- 10.20.2.0/24  (Server Pod 2 LAN)
- 10.20.3.0/24  (Server Pod 3 LAN)
- 10.20.4.0/24  (Server Pod 4 LAN)
- 10.1.0.152/30  (FW to DMZ #1)
- 10.1.0.156/30  (FW to DMZ #2)
- 10.30.0.0/24  (DMZ segment)
- 10.1.0.160/30  (Building A to Core #1)
- 10.1.0.164/30  (Building A to Core #2)
- 10.41.1.0/24  (Building A Floor1 user LAN)
- 10.41.2.0/24  (Building A Floor2 user LAN)
- 10.41.3.0/24  (Building A Floor3 user LAN)
- 10.1.0.168/30  (Building B to Core #1)
- 10.1.0.172/30  (Building B to Core #2)
- 10.42.1.0/24  (Building B Floor1 user LAN)
- 10.42.2.0/24  (Building B Floor2 user LAN)
- 10.42.3.0/24  (Building B Floor3 user LAN)
- 10.1.0.176/30  (Building C to Core #1)
- 10.1.0.180/30  (Building C to Core #2)
- 10.43.1.0/24  (Building C Floor1 user LAN)
- 10.43.2.0/24  (Building C Floor2 user LAN)
- 10.43.3.0/24  (Building C Floor3 user LAN)
- 10.1.0.184/30  (DC core to management)
- 10.50.0.0/24  (Out-of-band management LAN)
────────────────────────────────────────────────────────────── Step 2  生成 → 評価 → 改善ループ ──────────────────────────────────────────────────────────────

── Iteration 1/3 ──
  [1/3] DOT コード生成中...
  [2/3] Graphviz レンダリング中...
  [3/3] LLM 評価中...
  スコア: 5/10  passed=False  ★ NEW BEST
    · DOT のノード数が入力データに対して不足しています。全デバイスを定義してください。
    · DOT のエッジ数が入力データに対して不足しています。全接続を定義してください。
    · トポロジデータには73台のノードが記載されているが、DOTコードにはすべてのノードが含まれていない。
    ... 他 6 件

── Iteration 2/3 ──
  [1/3] DOT コード生成中...
  [2/3] Graphviz レンダリング中...
  [3/3] LLM 評価中...
  スコア: 5/10  passed=False
    · DOT のノード数が入力データに対して不足しています。全デバイスを定義してください。
    · DOT のエッジ数が入力データに対して不足しています。全接続を定義してください。
    · 73 台のノードから構成されるトポロジデータに対して、生成された DOT コードには全てのノードが定義されていません。
    ... 他 4 件

── Iteration 3/3 ──
  [1/3] DOT コード生成中...
  [2/3] Graphviz レンダリング中...
Error: sample_topology_large: syntax error in line 25 near ']'

[レンダリングエラー] DOT コードの処理に失敗しました:
Command '[PosixPath('dot'), '-Kdot', '-Tpng', '-O', 'sample_topology_large']' returned non-zero exit status 1. [stderr: b"Error: sample_topology_large: syntax error in line 25 near ']'\n"]
DOT ファイルを確認してください: output/iter_02/sample_topology_large.dot

(.venv) iida@s400win:~/git/d2v$
```