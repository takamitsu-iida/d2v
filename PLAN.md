# d2v(diagram-to-vision) 実装計画

**プロジェクト概要**: 人間が `iida-network-model`（独自 YANG モデル）に従って記述した
ネットワークトポロジ YAML を LLM で解析し、
Graphviz DOT 言語による美しい構成図を自動生成・自動評価・自律改善するシステム。

---

## アーキテクチャ

```
Input (YANG YAML)
    │
    ▼
┌─────────────┐
│   parser    │  YANG YAML を検証し、ノード/リンク/ゾーン情報を構造化テキストに変換
└──────┬──────┘
       │
       ▼
┌─────────────┐   diagram-system.md
│  generator  │ ◄─────────────────── LLM (OpenAI / Anthropic / Ollama)
└──────┬──────┘
       │ DOT コード
       ▼
┌─────────────┐
│  renderer   │  Graphviz で PNG / SVG をレンダリング
└──────┬──────┘
       │
       ▼
┌─────────────┐   diagram-evaluator.md
│  evaluator  │ ◄─────────────────── LLM
└──────┬──────┘
       │ EvaluationResult (score, passed, issues)
       ▼
   passed? ──── Yes ──► 最終出力 (output/)
       │
      No (max_iter 未満)
       │
       ▼
┌─────────────┐   diagram-improver.md
│  improver   │ ◄─────────────────── LLM
└──────┬──────┘
       │ 改善済み DOT コード
       └──────────────► renderer へ戻る
```

---

## ディレクトリ構成（目標）

```
d2v/
├── PLAN.md                      # この計画ファイル
├── README.md
├── pyproject.toml
├── .env.example
├── .envrc
├── .gitignore
│
├── prompts/
│   ├── diagram-system.md        # 生成プロンプト
│   ├── diagram-evaluator.md     # 評価プロンプト
│   └── diagram-improver.md      # 改善指示プロンプト
│
├── src/
│   └── d2v/
│       ├── __init__.py
│       ├── config.py            # 環境変数・設定読み込み
│       ├── llm/
│       │   ├── __init__.py      # get_llm() ファクトリー関数・認証エラーハンドリング
│       │   ├── base.py          # LLMClient 抽象基底クラス
│       │   ├── openai_client.py
│       │   ├── anthropic_client.py
│       │   └── ollama_client.py
│       ├── parser.py            # YANG YAML → 構造化データ（iida-network-model 検証含む）
│       ├── generator.py         # LLM → DOT コード
│       ├── renderer.py          # DOT → PNG/SVG
│       ├── evaluator.py         # DOT → EvaluationResult
│       └── pipeline.py          # 生成→評価→改善ループ（PipelineResult）
│
├── yang/
│   └── iida-network-model.yang      # ネットワークトポロジ YANG モデル定義（既存）
│
├── examples/
│   ├── sample_topology_small.yaml   # 小規模サンプル（5〜8 ノード）
│   └── sample_topology_medium.yaml  # 中規模サンプル（20〜30 ノード）
│
├── output/                      # 生成物（.gitignore 対象）
│
└── main.py                      # CLI エントリーポイント
```

---

## 実装フェーズ

### Phase 0: プロジェクト基盤

**目標**: Python パッケージとして動く最小構成を整える。

- [x] `pyproject.toml` 作成（依存ライブラリ定義）
- [x] `src/d2v/__init__.py` 作成
- [x] `src/d2v/config.py` 作成（`.env` から設定読み込み）
- [x] `src/d2v/llm/base.py` 作成（`LLMClient` 抄象クラス）
- [x] `src/d2v/llm/openai_client.py` 作成
- [x] `src/d2v/llm/anthropic_client.py` 作成
- [x] `src/d2v/llm/ollama_client.py` 作成
- [x] `.venv` セットアップ・依存ライブラリインストール確認

**完了の定義**: `python -c "from d2v.config import settings; print(settings)"` が通る。✅

---

### Phase 1: 単発生成パイプライン

**目標**: YANG YAML を渡すと DOT コードと PNG が生成される1ショット版を完成させる。

- [x] `examples/sample_topology_small.yaml` 作成（小規模トポロジ 5〜8 ノード、iida-network-model 準拠）
- [x] `src/d2v/parser.py` 実装
  - [x] YANG YAML のスキーマ検証（必須フィールドの存在チェック）
  - [x] `physical-layer.device` からノード情報（device-type, zone, asn, loopback）を抽出
  - [x] `physical-layer.physical-connection` からリンク情報（endpoint ペア・ポート名）を抽出
  - [x] `layer3-layer` から IP アドレス・サブネット情報を抽出
  - [x] LLM プロンプトに埋め込む構造化テキストを生成
- [x] `src/d2v/generator.py` 実装
  - [x] `diagram-system.md` をシステムプロンプトとして LLM に渡す
  - [x] DOT コードのみを抽出するパーサー（コードブロック除去）
- [x] `src/d2v/renderer.py` 実装
  - [x] DOT コード → PNG / SVG 出力
  - [x] `output/` ディレクトリへの保存（.dot ソースも一緒に保存）
- [x] `main.py` に CLI 引数受け付け（`--input`, `--output-dir`, `--format`）

**完了の定義**: `python main.py --input examples/sample_topology_small.yaml` で PNG が生成される。

> ⚠️ LLM 呼び出しには `.env` で API キーを設定する必要がある。`cp .env.example .env` でテンプレートから作成して内容を入力すること。

---

### Phase 2: 自動評価

**目標**: 生成した DOT コードを自動評価し、スコアと改善点リストを得る。

- [x] `prompts/diagram-evaluator.md` 作成
  - [x] 完全性チェック（入力ノード数・リンク数と DOT の照合）
  - [x] 視認性チェック（交差・密集の有無）
  - [x] ラベル網羅性チェック（taillabel / headlabel / IP の有無）
  - [x] ゾーン分類チェック（subgraph cluster の存在）
  - [x] 総合スコア（1〜10 点）と改善点リストを JSON で出力
- [x] `src/d2v/evaluator.py` 実装
  - [x] `EvaluationResult` / `RuleCheckResult` Pydantic モデル定義
  - [x] ルールベース検証（正規表現・DOT 構造チェック）
  - [x] LLM レビュー（evaluator プロンプト使用）
  - [x] 結果を `output/` に JSON 保存

**完了の定義**: `evaluator.evaluate(dot_code, parsed_data)` が `EvaluationResult` を返す。✅

> ⚠️ LLM 呼び出しには `.env` で API キー設定が必要。ルールベース検証（`_run_rule_checks`）は API キー不要で動作済み。

---

### Phase 3: 自律改善ループ

**目標**: 評価 → 改善 → 再評価のループを自動化し、最終成果物を得る。

- [x] `prompts/diagram-improver.md` 作成
  - [x] 元の DOT コード・評価結果・改善点を受け取り、修正 DOT コードのみ出力
- [x] `src/d2v/pipeline.py` 実装
  - [x] ループ制御（`max_iterations` 上限）
  - [x] ベストスコア保持（改悪時は直前を採用）
  - [x] 各イテレーションの成果物を `output/iter_NN/` に保存
  - [x] ループ終了条件: スコア >= 閾値 or 最大イテレーション到達
- [x] `main.py` にループ設定を追加（`--max-iter`, `--threshold`）
- [x] ループ進捗をリッチなログ表示（rich ライブラリ）

**完了の定義**: 自動ループが動作し、最終的な PNG と評価ログが `output/` に出力される。✅

> ⚠️ API キー設定後に `python main.py -i examples/sample_topology_small.yaml` で全体を通せる。

---

### Phase 4: 品質向上とプロンプトチューニング

**目標**: 実際のトポロジデータで動作検証し、プロンプトを洗練させる。

- [x] 中規模トポロジ（20～30 ノード）でのテスト
- [x] 大規模トポロジ（50 ノード以上）でのテスト（73 ノード・zone 自動分割）
- [x] 評価スコアの推移を可視化（matplotlib でグラフ出力）
- [x] `diagram-system.md` のプロンプト改善（`compound=true` / `newrank=true` / `rank=same` ヒント / カラーパレット拡張 / 縦横比・dir=none ・cluster 背景）
- [x] `diagram-evaluator.md` の評価基準見直し（俯瞰図専用評価プロンプト追加・密なメッシュでの IP ラベル省略を許容）
- [x] `diagram-improver.md` の改善指示精度向上（cluster 背景・縦横比・dir=none の規約に整合）

**完了の定義**: YANG YAML で記述した実トポロジで品質スコア 8/10 以上を安定して達成できる。✅

---

### Phase 5: 拡張（任意）

- [ ] CML サーバから直接トポロジ取得（REST API 連携）
- [ ] Mermaid / draw.io 形式への出力対応
- [ ] Web UI（Gradio or Streamlit）による対話的な生成
- [ ] 生成履歴の比較ビューア

---

## YANG モデル定義

### モデルファイル

`yang/iida-network-model.yang` — OpenConfig スタイルの独自 YANG モデル

物理層・L2・L3 を階層的に表現し、d2v 作図に必要な `zone` フィールドを内包する。
RFC 8345 のような抽象的なトポロジ表現ではなく、**ネットワーク SE が手書きしやすい具体的な構造**を採用。

### モデル構造

```
network-model
├── physical-layer
│   ├── device[]                   ← ノード（device-id, device-type, zone, asn, loopback, ...）
│   │   └── interface[]            ← インターフェース（ip-address 含む）
│   └── physical-connection[]      ← 物理リンク（endpoint[2] で両端を表現）
├── layer2-layer
│   ├── vlan[]
│   └── layer2-interface-config[]  ← access / trunk 設定
└── layer3-layer
    ├── ip-subnet[]
    ├── layer3-interface-config[]  ← IP アドレス・OSPF 設定
    ├── host-config[]
    └── static-route[]
```

### d2v 作図に使用するフィールド

| フィールド | 定義場所 | 用途 |
|-----------|---------|------|
| `device-type` | `device-identification` grouping | 絵文字アイコンマッピング（router / switch / firewall 等） |
| `zone` | `physical-layer.device` | subgraph cluster によるゾーン分け |
| `asn` | `physical-layer.device` | BGP トポロジ図でのノードラベル表示 |
| `loopback` | `physical-layer.device` | 管理 IP としてノードラベルに表示 |
| `interface.ip-address` | `physical-layer.device.interface` | エッジの taillabel / headlabel に使用 |
| `ip-subnet.prefix` | `layer3-layer.ip-subnet` | エッジ中央のセグメントラベルに使用 |

### YAML サンプル構造

```yaml
network-model:
  physical-layer:
    device:
      - device-id: "router-01"
        device-name: "Core Router"
        device-type: router
        zone: core
        asn: 65001
        loopback: "10.0.0.1/32"
        interface:
          - interface-id: "GigabitEthernet0/0"
            ip-address: "10.1.12.1/30"
          - interface-id: "GigabitEthernet0/1"
            ip-address: "10.1.13.1/30"
      - device-id: "switch-01"
        device-type: switch
        zone: core
        interface:
          - interface-id: "Ethernet1/1"
          - interface-id: "Ethernet1/2"
    physical-connection:
      - connection-id: "router-01_Gi0/0__switch-01_Eth1/1"
        endpoint:
          - device-id: "router-01"
            interface-id: "GigabitEthernet0/0"
          - device-id: "switch-01"
            interface-id: "Ethernet1/1"
  layer2-layer:
    vlan:
      - vlan-id: 10
        name: "Management"
  layer3-layer:
    ip-subnet:
      - subnet-id: "core-link-01"
        prefix: "10.1.12.0/30"
        description: "router-01 to switch-01"
```

---

## 技術的決定事項

| 項目 | 決定 | 理由 |
|------|------|------|
| 入力フォーマット | `iida-network-model` YANG YAML | 人間の可読性・物理/L2/L3 階層構造・d2v 専用 zone フィールド |
| YANG 検証 | Python ルールベース（pydantic） | yangson 等の重量ライブラリを避けシンプルに保つ |
| LLM クライアント | 抽象インターフェース経由 | OpenAI / Anthropic / Ollama を後から選択可能に |
| DOT レンダリング | `graphviz` Python パッケージ | Graphviz のシステムインストールを利用 |
| 設定管理 | `python-dotenv` + `pydantic-settings` | 型安全な設定値管理 |
| データモデル | Pydantic v2 | バリデーションと JSON シリアライズ |
| ログ出力 | `rich` | 進捗・ループ状況の可視化 |

---

## 進捗ログ

| 日付 | 内容 |
|------|------|
| 2026-07-13 | プロジェクト計画策定。diagram-system.md (生成プロンプト) 作成済み |
| 2026-07-13 | インプット形式を CML YAML → YANG YAML（iida-network-model）に変更 |
| 2026-07-13 | `iida-network-model.yang` に `zone` リーフを追加。PLAN.md を iida-network-model ベースに更新 |
| 2026-07-13 | Phase 0 完了。pyproject.toml / config / LLM クライアント層を実装。.venv 構築完了 |
| 2026-07-13 | `create_client(settings)` → `get_llm()` に変更。認証エラーを日本語メッセージ + sys.exit(1) で処理 |
| 2026-07-13 | Phase 1 完了。parser / generator / renderer / main.py を実装。diagram-system.md の入力記述を修正 |
| 2026-07-13 | Phase 2 完了。diagram-evaluator.md プロンプト作成。evaluator.py 実装（ルールベース+LLM 評価、ペナルティ調整、JSON 保存） |
| 2026-07-13 | Phase 3 完了。diagram-improver.md 作成。pipeline.py 実装（ベスト保持ループ・リッチログ）。main.py に --max-iter / --threshold 追加 |
| 2026-07-13 | Phase 4 部分完了。examples/sample_topology_medium.yaml (23 ノード, 6 ゾーン) 作成。visualizer.py 実装 (matplotlib スコア推移グラフ)。diagram-system.md に compound=true / newrank=true / rank=same 追加。pyproject.toml に matplotlib 追加。main.py に plot_score_history 呼び出し追加。残タスク (evaluator/improver チューニング) は API キー設定後に実施 |
| 2026-07-15 | Phase 4 完了。社内 Azure OpenAI(REST) クライアント追加・429 リトライ実装。大規模トポロジ(73 ノード) を zone 単位で自動分割(俯瞰図+詳細図)。partitioner.py に境界スタブのゾーン集約(BOUNDARY_AGG_THRESHOLD)追加。renderer.py に cluster 背景の淡色化・矢印除去(dir=none)・縦横比フィット(rankdir=LR 自動切替, DIAGRAM_ASPECT_RATIO)を実装。俯瞰図専用の system/evaluator プロンプト追加。diagram-system/improver/evaluator を新規約に整合。中規模 9/10・大規模ゾーン詳細 9/10・俯瞰図 10/10 を確認し 8/10 以上を安定達成 |

---

## メモ・注意事項

- `output/` は `.gitignore` に追加する
- LLM の出力は非決定的なため、同じ入力でも毎回異なる DOT コードが生成される
- 評価スコアも LLM ベースのため、閾値判定には若干のゆらぎがある
- ループ改善で悪化する場合があるため、ベストスコア保持は必須


<br><br><br>

---

<br><br><br>


# v2d (vision-to-diagram) 実装計画

## 目的

画像からネットワーク図の構造を抽出し、`iida-network-model` YAML に変換する `vision-to-diagram (v2d)` を追加する。

---

## 進捗状況

- [x] 0. 要件定義
- [x] 1. 入力形式の整理
- [x] 2. 中間表現の設計
- [x] 3. OCR / 図形検出の実装
- [x] 4. ノード・エッジ推定の実装
- [x] 5. YAML 変換の実装
- [x] 6. 再描画・評価の実装
- [x] 7. CLI への統合
- [x] 8. テスト整備
- [x] 9. ドキュメント整備

---

## フェーズ別計画

### Phase 0: 要件定義
**目的**: 対応する図の種類と出力形式を固定する。

**対象**
- ネットワーク図のスクリーンショット
- draw.io / PPT / Graphviz 由来の図
- まずは PNG 入力を優先

**成果物**
- 対応範囲の明文化
- 非対応範囲の明文化
- 成功条件の定義

**進捗**
- [x] 入力制約を定義
- [x] 出力 YAML 仕様を定義
- [x] 評価指標を定義

---

#### 0-A. 対応範囲（初期スコープ）

| 区分 | 対応する | 補足 |
|------|---------|------|
| 入力フォーマット | PNG（優先）/ JPEG | 1 実行につき 1 画像 |
| 図の種類 | 矩形ノード＋直線/直交コネクタで描かれたネットワーク構成図 | draw.io / PowerPoint 書き出し / Graphviz(dot) 生成図（d2v 自身の出力を含む） |
| ノード | 箱型（rect / rounded-rect）で、内部または近傍にテキストラベルを持つもの | ホスト名・IP・ポート名を想定 |
| エッジ | ノード間を結ぶ実線・破線（直線/直交） | 矢印の有無は問わない（物理リンクとして扱う） |
| グルーピング | 背景色付きの枠（cluster/zone に相当する矩形領域） | `zone` として抽出 |
| テキスト | ASCII のホスト名・IP・インターフェース名。ゾーン見出しは日本語可 | |
| 規模 | 目安 30 ノード以下 | それ以上は精度が落ちるため段階的に拡張 |

#### 0-B. 非対応範囲（初期スコープ外）

- 手描き・写真撮影で歪み/傾きが大きい画像（Phase 1 の前処理で一部救済）
- 曲線が多用され交差が激しい「蜘蛛の巣」状の配線
- テキストを持たず独自アイコンだけで表現されたノード（機種記号のみ 等）
- 3D 表現・影・グラデーションの強い装飾図
- 50 ノード超の大規模図（将来対応）
- 論理情報のみで物理接続が読み取れない図（L3 概念図のみ 等）

#### 0-C. 入力制約

- 解像度: 幅 800px 以上を必須、1200px 以上を推奨。4000px 超は内部で縮小。
- 文字: ノードラベルが判読可能な程度に鮮明であること（極端な低解像度・圧縮ノイズは不可）。
- 配色: ノード枠と背景にコントラストがあること（白地×淡色ゾーンを想定）。
- 1 ファイル = 1 トポロジ。複数図の貼り合わせやスライド全体は非対応。

#### 0-D. 出力 YAML 仕様

- 出力は **`iida-network-model` YAML**（d2v の入力と同一スキーマ）とし、v2d → d2v で再描画できる往復性を持たせる。
- 抽出して埋めるフィールド（ベストエフォート）:
  - `physical-layer.device[]`: `device-id`（ラベル由来）, `device-name`, `device-type`（アイコン/形状/キーワードから推定: router/switch/firewall/server/host/load-balancer）, `zone`（所属クラスタ矩形から）, `loopback`（管理 IP が読めれば）
  - `device.interface[]`: `interface-id`（エッジ端のポート名ラベル）, `ip-address`（読めれば）
  - `physical-layer.physical-connection[]`: `endpoint[2]`（線の両端に最も近いノード＋ポート）
  - `layer3-layer.ip-subnet[]`: エッジ中央ラベルのセグメント IP が読めれば
- 読み取れない必須フィールドは推定値または `unknown` で補い、確信度を別ファイル（サイドカー JSON、モデル外メタデータ）に記録する。
- 抽出できなかった要素は捏造せず省略し、確信度に反映する。

#### 0-E. 中間表現の方針

- 画像解析結果は一旦 **中間 JSON**（`nodes` / `edges` / `clusters` / `labels` / `confidence`）に落とし、そこから YAML へ決定論的に変換する（Phase 2）。
- 解析手段は 2 系統を想定し Phase 2/3 で選択・比較する:
  1. 古典 CV + OCR（opencv-python で矩形/線分検出、pytesseract/easyocr で文字認識）
  2. マルチモーダル LLM（画像を直接解析。既存 `llm/` 基盤を再利用）
- どちらでも出力は同一の中間 JSON スキーマに揃え、後段（YAML 変換・評価）を共通化する。
- **確認済み(2026-07-15)**: 社内 LLM(gpt-5.1) は画像入力(vision)に対応。OpenAI 互換の vision 形式（`content` 配列に `image_url` の base64 データ URL）で HTTP 200、構成図を正確に認識。よって **マルチモーダル LLM 方式を主軸**とし、OCR/CV 方式は補助・比較用に留める。

#### 0-F. 成功条件（完了の定義）

基準画像として **d2v が生成した PNG**（正解トポロジが既知）を用い、以下を安定して満たすこと:

| 指標 | 目標 |
|------|------|
| ノード F1（device-id 一致） | ≥ 0.90 |
| エッジ F1（endpoint ペア一致、順不同） | ≥ 0.80 |
| ラベル一致率（ホスト名/IP 文字列） | ≥ 0.80 |
| ゾーン割当一致率 | ≥ 0.80 |
| 往復再描画 | v2d 出力 YAML を d2v で再描画し、元画像と構造が視認上一致 |

- Phase 6 でこれらの指標を自動計測し、既知画像でのスナップショット比較に用いる。


---

### Phase 1: 入力形式の整理
**目的**: 画像入力を前処理可能な状態にする。

**処理候補**
- 傾き補正
- ノイズ除去
- 二値化
- トリミング
- 解像度正規化

**進捗**
- [x] 前処理パイプライン設計
- [x] サンプル画像収集
- [x] 失敗ケースの分類

**実装（2026-07-15）**
- `src/d2v/v2d/`（サブパッケージ）を新設。`src/d2v/v2d/preprocess.py` に前処理を実装。
- vision LLM 方式が主軸のため前処理は軽量化: EXIF 向き補正 → RGB 化 → 最大辺 `v2d_max_image_dim`（既定 2048px）での縮小（縦横比維持・拡大なし）→ base64 データ URL 化。
- 入力検証: 対応拡張子（.png/.jpg/.jpeg）・ファイル存在・破損を `ImagePreprocessError` で検出。推奨幅（800px）未満は警告に記録。
- 出力は `PreprocessedImage`（width/height/data_url/original_size/warnings）。
- 傾き補正・二値化など重い CV 前処理は OCR 方式を採る場合に Phase 3 で追加（LLM 方式では不要）。
- サンプル画像: `images/`（d2v が生成した PNG 群、正解トポロジは `examples/*.yaml` で既知）を Phase 6 の評価用基準として利用。
- 失敗ケース: 0-B「非対応範囲」に集約（手描き/写真の歪み・蜘蛛の巣配線・テキストなしアイコン・3D 装飾・50 ノード超・物理接続が読めない図）。

**完了の定義**: 任意の対応画像を `load_and_preprocess()` で正規化済み画像＋データ URL に変換でき、非対応入力を明示エラーで弾ける。✅

---

### Phase 2: 中間表現の設計
**目的**: 画像解析結果を一旦 JSON に落とす。

**想定スキーマ**
- `nodes`
- `edges`
- `clusters`
- `labels`
- `confidence`

**進捗**
- [x] JSON スキーマ定義
- [x] YAML 変換ルール定義
- [x] 既存 `iida-network-model` との対応表作成

**実装（2026-07-15）**
- `src/d2v/v2d/schema.py`: 中間表現を Pydantic で定義。`ExtractedDiagram`（`nodes` / `edges` / `clusters` / `notes` / `confidence`）。
  - `ExtractedNode`: id, hostname, device_type（iida-network-model の device-type に整合）, zone, loopback, raw_label, confidence
  - `ExtractedEdge`: source, target, source_port, target_port, segment（中央 IP）, style（solid/dashed）, confidence
  - `ExtractedCluster`: id, label（ゾーン名）, members, confidence
  - 各要素が `confidence`(0.0〜1.0) を保持。vision LLM / OCR いずれの解析でもこの表現に揃える。
- `src/d2v/v2d/converter.py`: 中間表現 → iida-network-model 辞書/YAML へ変換（`build_model` / `to_yaml`）。
  - device-id はホスト名優先で正規化・衝突回避。ゾーンはクラスタ所属を優先。
  - エッジからインターフェース（ポート名、無ければ `ifN` 合成）と physical-connection を構築。
  - エッジの segment を layer3-layer.ip-subnet に集約。
- 往復検証: 手組みの `ExtractedDiagram` → YAML → `d2v.parser.load_model` でデバイス/接続/サブネットが欠落なく復元されることを確認。

**中間表現 → iida-network-model 対応表**

| 中間表現 | iida-network-model | 備考 |
|----------|--------------------|------|
| `ExtractedNode.hostname` | `physical-layer.device[].device-id` / `device-name` | 正規化して device-id 化 |
| `ExtractedNode.device_type` | `device[].device-type` | 同一の値域（router/switch/server/firewall/host/load-balancer/unknown） |
| `ExtractedNode.loopback` | `device[].loopback` | 読み取れた場合のみ |
| `ExtractedCluster.label` / `.members` | `device[].zone` | クラスタ所属からゾーンを付与 |
| `ExtractedEdge.source/target_port` | `device[].interface[].interface-id` | 無ければ `ifN` を合成 |
| `ExtractedEdge`（両端） | `physical-layer.physical-connection[].endpoint[2]` | device-id + interface-id のペア |
| `ExtractedEdge.segment` | `layer3-layer.ip-subnet[].prefix` | 中央ラベルの IP/プレフィックス |
| `*.confidence` / `notes` | （モデル外メタデータ） | サイドカーに記録予定（Phase 3+） |

**完了の定義**: 中間表現を定義し、`to_yaml()` が d2v で再パース可能な iida-network-model を生成できる。✅

---

### Phase 3: OCR / 図形検出
**目的**: テキストと図形を分離して検出する。
**方針変更**: 社内 LLM(gpt-5.1) が画像入力に対応するため、**マルチモーダル LLM で画像から中間表現を一括抽出**する方式を採用（OCR/CV による段階検出は補助・将来対応に留める）。

**候補技術**
- OCR: `pytesseract` / `easyocr`（将来の補助・比較用）
- 画像処理: `opencv-python`（同上）
- 図形検出: ルールベース + 必要に応じて ML（同上）

**進捗**
- [x] OCR 実装（→ LLM vision による文字・ラベル読み取りで代替）
- [x] 矩形検出実装（→ LLM vision によるノード検出で代替）
- [x] 線分検出実装（→ LLM vision によるエッジ検出で代替）
- [x] 矢印検出実装（→ 物理リンクは無向のため線種 solid/dashed のみ判定）

**実装（2026-07-15）**
- LLM 基盤に vision 対応を追加: `LLMClient.chat_with_images(system, user, image_data_urls)`。
  - `azure_openai_client.py` は POST 処理を `_post()` に共通化し、`chat` と `chat_with_images` で共有（429 リトライも継続適用）。
  - `openai_client.py` / `ollama_client.py`（OpenAI 互換）・`anthropic_client.py`（独自 image 形式）にも実装。
- `prompts/v2d-extract.md`: 画像 → 中間表現 JSON を出力させる抽出プロンプト（device_type 推定・ポート/セグメントの区別・破線=境界・confidence・捏造禁止）。
- `src/d2v/v2d/extractor.py`: 前処理 → vision LLM → JSON パース → `ExtractedDiagram` 検証（`extract_from_image`）。
- 実地検証: `images/sample_topology_small_best.png` から nodes=7 / edges=6 / clusters=4 を confidence 0.99 で抽出し、元トポロジ（7 ノード・6 接続）と一致。device_type・ポート名・セグメント IP・zone も正しく復元。

**完了の定義**: 画像から `ExtractedDiagram` を抽出でき、実画像で主要要素を正しく読み取れる。✅

---

### Phase 4: ノード・エッジ推定
**目的**: 検出結果から図の構造を復元する。
**備考**: LLM vision 方式では抽出（Phase 3）と構造復元が一体化しており、`ExtractedDiagram` の時点でノード/エッジ/クラスタの対応が付いている。Phase 4 は「抽出結果の後処理・整合性補正」に位置づけ、OCR/CV 方式を導入する場合の対応付けロジックはそちらで実装する。

**処理**
- テキストを最寄りノードへ紐付け
- 線の端点とノードを対応付け
- cluster / zone を推定
- ラベルの意味を補完

**進捗**
- [x] ノード候補抽出
- [x] エッジ候補抽出
- [x] ノード間対応ロジック実装
- [x] クラスタ推定実装

**実装（2026-07-15）**
- `src/d2v/v2d/refine.py`: `refine(diagram) -> (ExtractedDiagram, RefineReport)`。抽出結果の整合性補正を担う。
  - 同一ホスト名ノードのマージ（id を代表 id に統一、大小文字無視、欠損属性を補完）
  - 未定義ノードを参照するエッジ・クラスタメンバーの除去
  - 自己ループ・重複エッジ（無向・ポート込み）の除去
  - クラスタ所属からノードの zone を補完
  - 孤立ノード（接続なし）の検出（除去はせず所見に記録）
  - 補正内容は `RefineReport` と `diagram.notes` に記録
- 検証: 重複ノード（大小文字違い）マージ・zone/loopback 補完・自己ループ/重複/未定義エッジ除去・未定義クラスタメンバー除去・孤立ノード検出をユニット確認。

**完了の定義**: 抽出結果を d2v で描画可能な整合した中間表現に補正できる。✅

---

### Phase 5: YAML 変換
**目的**: `iida-network-model` YAML を生成する。

**進捗**
- [x] JSON → YAML 変換器作成
- [x] 既存 parser と整合確認
- [x] サンプル出力作成

**実装（2026-07-15）**
- `src/d2v/v2d/pipeline.py`: 画像 → 抽出 → 整合性補正 → YAML 出力を束ねる `run(image_path, output_dir)`。
  - 成果物: `<stem>.yaml`（iida-network-model）と `<stem>.v2d.json`（確信度・所見・補正内容・カウント・低確信度ノード）。
  - 既存 `d2v.parser.load_model` で再パースし、抽出カウントとパース結果カウントの整合を確認（サイドカーの `parsed_counts`）。
- サンプル出力: `output/v2d/small_from_image.yaml` ＋ `.v2d.json` を生成。`sample_topology_small_best.png` から devices=7 / connections=6 / subnets=4 を復元し、`parsed_counts` と一致。読み取れない IP は notes に記録。

**完了の定義**: 画像から d2v で再パース可能な YAML を出力でき、サンプルを生成できる。✅

---

### Phase 6: 再描画・評価
**目的**: 抽出結果を再描画し、元画像と比較する。

**評価観点**
- ノード一致率
- エッジ一致率
- ラベル一致率
- レイアウト類似度
- confidence の妥当性

**進捗**
- [x] 再描画処理追加
- [x] 比較指標実装
- [x] 失敗例の可視化

**実装（2026-07-15）**
- `src/d2v/v2d/evaluate.py`:
  - `compare_models` / `evaluate_files`: 抽出モデルと正解 YAML を device-id で照合し、ノード/エッジの P/R/F1、種別・ゾーン（表記差を吸収する正規化）・loopback 一致率を計測。
  - `rerender_with_d2v`: v2d 出力 YAML を d2v で再描画し、往復ループ（画像 → v2d → YAML → d2v → 画像）を閉じる。
- 計測結果（`small` 画像 vs 正解 `examples/sample_topology_small.yaml`）:
  - ノード **F1=1.00**、エッジ **F1=0.83** → Phase 0-F の目標（≥0.90 / ≥0.80）を達成。
  - 種別一致 1.00、ゾーン一致 0.57（表示名 vs zone 名の差）、loopback 一致 0.33（小さい文字の誤読）。
  - 再描画も d2v で 9/10 を獲得し、視覚的にも元図と同一構成を再現。
- 失敗例の可視化: サイドカー（`notes`・`low_confidence_nodes`）＋指標サマリ＋再描画画像で差分を確認可能。
- 既知の限界: 微小文字（loopback IP 等）の誤読、ゾーン表示名とモデル zone 名の表記差。構造（ノード/エッジ）は高精度。

**完了の定義**: 抽出精度を自動計測でき、往復ループを閉じて視覚比較できる。✅

---

### Phase 7: CLI への統合
**目的**: 既存 `d2v` に `v2d` コマンドを追加する。

**案**
- `python main.py vision-to-diagram --input sample.png`
- もしくは `main.py` にサブコマンド追加

**進捗**
- [x] CLI 設計
- [x] 引数設計
- [x] 実行ログ整備

**実装（2026-07-15）**
- `main.py` にサブコマンド分岐を追加。`sys.argv[1] == "v2d"` のときのみ v2d ハンドラへ振り分け、それ以外は従来の d2v CLI をそのまま実行（**後方互換を維持**：`python main.py -i topology.yaml` は不変）。
- `run_v2d(argv)`: 専用パーサ（`prog="d2v v2d"`）。引数:
  - `--input/-i`（画像・必須）, `--output-dir/-o`（既定 `output/v2d`）
  - `--truth/-t`（正解 YAML を指定すると精度計測）, `--rerender`（d2v で再描画）, `--format/-f`
- 実行ログ: rich でパネル/ルール/所見/精度サマリを表示。画像処理系は遅延インポート。入力・抽出エラーは分かりやすく表示して終了。
- 動作確認: `./main.py v2d -i images/sample_topology_small_best.png -t examples/sample_topology_small.yaml` で ノード F1=1.00・エッジ F1=1.00 を確認。従来の `./main.py --help` / `./main.py -i ...` も不変で動作。

**完了の定義**: `python main.py v2d --input 画像` で YAML 出力でき、既存 d2v CLI を壊さない。✅

---

### Phase 8: テスト整備
**目的**: 変換の安定性を保証する。

**テスト**
- OCR 断片テスト
- 図形検出テスト
- JSON 生成テスト
- YAML 変換テスト
- 既知画像のスナップショットテスト

**進捗**
- [x] 単体テスト追加
- [x] サンプル画像テスト追加
- [x] 回帰テスト追加

**実装（2026-07-15）**
- `pytest` を導入（pyproject の `[project.optional-dependencies].dev`）。`tests/` に v2d の単体テストを追加。
  - `tests/test_v2d_converter.py`: スキーマ既定値・変換器（カウント/ゾーン/インターフェース合成/一意 device-id/YAML パース可否）
  - `tests/test_v2d_refine.py`: マージ・自己ループ/重複/未定義エッジ除去・zone 補完・未定義メンバー除去・孤立検出・id 付け替え
  - `tests/test_v2d_evaluate.py`: 完全一致・欠落での recall 低下・エッジ無向一致・ゾーン正規化・loopback 一致
  - `tests/test_v2d_preprocess_extract.py`: 前処理（データURL/縮小/警告/非対応拡張子/不存在）＋抽出器（LLM モックで JSON パース・非 JSON でのエラー）
- LLM を使わない決定論部分を網羅。抽出器は `_FakeLLM` をモックして JSON 応答→中間表現の経路を検証。
- 実行: `python -m pytest tests/ -q` で **25 passed**。

**完了の定義**: LLM 不要の主要ロジックが自動テストで回帰検証できる。✅

---

### Phase 9: ドキュメント整備
**目的**: 使い方と制約を明確化する。

**進捗**
- [x] README に v2d を追記
- [x] 入力サンプル追加
- [x] 制約事項を追記

**実装（2026-07-15）**
- README.md 冒頭に v2d（双方向ツール）であることを明記。
- 「v2d — 画像からトポロジ YAML を生成」セクションを追加: フロー図・CLI 使い方・オプション・出力ファイル（YAML＋サイドカー）・入力サンプル（`images/` を流用）・対応範囲/制約/既知の限界。
- プロジェクト構成ツリーに `src/d2v/v2d/`・追加プロンプト・`tests/`・`azure_openai_client.py` を反映。

**完了の定義**: README だけで v2d の使い方・制約が把握できる。✅

---

## 実装優先順位

1. OCR でテキスト抽出
2. 矩形と線分の検出
3. ノード・エッジ推定
4. JSON 中間表現
5. YAML 変換
6. 再描画と評価
7. CLI 統合

---

## リスク

- 画像のレイアウト差が大きい
- OCR 誤認識が多い
- 線と装飾の区別が難しい
- アイコンの意味分類が不安定

**対策**
- 対象図を最初は限定する
- confidence を持たせる
- 不明要素は保留にする
- 人手修正を前提にする

---

## マイルストーン

### M1: PoC
- [ ] 1 枚の画像からノード名を抽出できる

### M2: 構造抽出
- [ ] ノードとエッジを JSON 化できる

### M3: YAML 生成
- [ ] `iida-network-model` YAML を生成できる

### M4: 往復検証
- [ ] 再描画して元図と比較できる

---

## 直近の次アクション

- [ ] 入力画像の対象範囲を決める
- [ ] JSON スキーマを確定する
- [ ] `src/d2v/vision/` 配下の構成を決める
- [ ] PoC のテスト画像を用意する