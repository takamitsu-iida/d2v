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


<br><br><br>

---

<br><br><br>


# webui (ブラウザ GUI 化) 実装計画

## 目的

これまで CLI (`python main.py ...`) で提供してきた d2v / v2d を、
**ブラウザ上でパラメータを指定し、ブラウザ上で結果（画像・YAML・評価）を確認できる
GUI アプリケーション**として提供する。既存の CLI は後方互換のまま残し、
GUI はその上に乗せる「もう 1 つのフロントエンド」として追加する。

```
                ┌───────────────────────────┐
                │   ブラウザ (SPA フロント)   │
                │  パラメータ入力 / 結果表示   │
                └────────────┬──────────────┘
                             │ REST + SSE(進捗)
                             ▼
                ┌───────────────────────────┐
                │   FastAPI (src/d2v/web)    │
                │   jobs.py: 非同期ジョブ管理  │
                └────────────┬──────────────┘
                             │ 直接呼び出し（共通化）
                             ▼
                ┌───────────────────────────┐
                │  service.py（CLI と共有）    │
                │  parser / partitioner /     │
                │  pipeline / v2d.pipeline    │
                └───────────────────────────┘
```

---

## 設計方針

| 項目 | 決定 | 理由 |
|------|------|------|
| バックエンド | **FastAPI + Uvicorn** | 既存 Python パイプラインを直接再利用できる。非同期・SSE・ファイルアップロードに強い。Gradio/Streamlit より UI とジョブ制御の自由度が高い（focus/zone/split の分岐や複数枚出力、進捗ログを表現しやすい） |
| フロントエンド | **静的 SPA（HTML + Vanilla JS + CSS）** | 追加ビルド基盤（npm/バンドラ）を持ち込まず、FastAPI から静的配信するだけで完結。将来 React 等へ差し替え可能な薄い構成 |
| 進捗表示 | **SSE (Server-Sent Events)** | LLM ループは長時間。サーバ→クライアントの一方向ストリームで十分。WebSocket より実装が単純 |
| ジョブ実行 | **バックグラウンドスレッド + インメモリ ジョブレジストリ** | LLM 呼び出しは同期ブロッキング。イベントループを塞がないようスレッドプールで実行し、進捗をキュー経由で SSE に流す |
| CLI との共通化 | **`src/d2v/web/service.py` に分岐ロジックを集約** | `main.py` の `_run_single/_run_split/_run_focus/_run_zone` 相当を再利用可能な純関数として切り出し、CLI・Web の両方から呼ぶ（ロジック二重化を防ぐ） |
| 進捗フック | **`pipeline.run(..., progress_callback=None)` を追加** | 既定 `None` で従来どおり rich 表示。GUI 実行時のみコールバックでイベントを受け取り SSE へ転送（既存挙動を壊さない） |
| 既存 CLI | **完全維持** | `python main.py -i ...` / `python main.py v2d -i ...` は不変。GUI は `python main.py serve` など別サブコマンドで起動 |

---

## ディレクトリ構成（目標）

```
src/d2v/web/
├── __init__.py
├── app.py            # FastAPI アプリ定義・ルーティング・静的配信
├── service.py        # d2v/v2d オーケストレーション（CLI と共通のジョブ本体）
├── jobs.py           # ジョブレジストリ（生成/実行/進捗キュー/結果保持）
├── events.py         # 進捗イベント・ジョブ状態の Pydantic モデル
└── static/
    ├── index.html    # SPA 本体（d2v / v2d タブ）
    ├── app.js        # フォーム制御・API 呼び出し・SSE 購読・結果描画
    └── style.css
```

---

## API 設計（案）

| メソッド | パス | 用途 |
|---------|------|------|
| GET | `/` | SPA（index.html）を返す |
| GET | `/api/meta` | 利用可能な LLM プロバイダ・既定パラメータ・`examples/` 一覧・既定閾値等 |
| GET | `/api/examples/{name}` | サンプル YAML の内容を返す（プレビュー用） |
| POST | `/api/d2v/jobs` | d2v ジョブ作成（パラメータ JSON or YAML 本文）→ `job_id` を返す |
| POST | `/api/v2d/jobs` | v2d ジョブ作成（画像アップロード, multipart）→ `job_id` |
| GET | `/api/jobs/{id}` | ジョブ状態・結果メタ（スコア・出力ファイル一覧・複数枚情報） |
| GET | `/api/jobs/{id}/events` | **SSE**: 進捗イベントストリーム（開始/生成/評価/スコア/完了/エラー） |
| GET | `/api/jobs/{id}/image?name=...` | 生成画像（PNG/SVG）を返す |
| GET | `/api/jobs/{id}/artifact?name=...` | DOT ソース・評価 JSON・v2d YAML/サイドカー等 |
| DELETE | `/api/jobs/{id}` | ジョブと成果物の破棄（任意） |

**パラメータ（d2v ジョブ）**: 既存 CLI と 1:1 対応させる。
`input`（examples 選択 / アップロード / 貼り付け YAML のいずれか）,
`format`, `max_iter`, `threshold`, `patience`, `mode`（`auto|single|split|focus|zone`）,
`split_threshold`, `no_split`, `focus[]`, `hops`, `zone[]`, `zone_opacity`。

**パラメータ（v2d ジョブ）**: `image`(必須アップロード), `truth`(任意), `rerender`(bool), `format`。

---

## 実装フェーズ

### Phase 0: 技術選定と基盤
**目標**: FastAPI アプリが起動し、静的 SPA の雛形を配信できる。

- [x] `pyproject.toml` に `[project.optional-dependencies].web`（`fastapi`, `uvicorn[standard]`, `python-multipart`）を追加
- [x] `src/d2v/web/app.py` に最小 FastAPI アプリ + `/api/meta` + 静的配信を実装
- [x] `main.py` に `serve` サブコマンド（`python main.py serve --host 127.0.0.1 --port 8000`）を追加。既定は localhost バインド
- [x] `static/index.html` に空の 2 タブ（d2v / v2d）雛形

**完了の定義**: `python main.py serve` でブラウザから空 UI と `/api/meta` を確認できる。

**実装（2026-07-16）**
- `pyproject.toml` に optional extra `web`（fastapi / uvicorn[standard] / python-multipart）を追加。GUI 未使用者へ依存を強制しない。
- `src/d2v/web/`（新パッケージ）: `app.py`（FastAPI アプリ・`GET /`・`GET /api/meta`・`/static` マウント）、`static/index.html`・`app.js`・`style.css`（2 タブ SPA 雛形＋メタ情報読み込み＋タブ切替）。
- `main.py` に `serve` サブコマンド分岐を追加（`python main.py serve --host/--port/--reload`）。既定 `127.0.0.1`。uvicorn 未インストール時は `pip install -e '.[web]'` を促して終了。既存 d2v/v2d CLI は不変。
- 動作確認: `python main.py serve` 起動 →`/api/meta`（provider=azure・examples 3 件・既定値）を JSON 応答、`/` が SPA を返し、`/static/app.js`・`style.css` が 200。
- 注意: `./main.py` の shebang は system python のため、GUI 起動は venv の python（`.venv/bin/python main.py serve` または venv 有効化後の `python main.py serve`）を使う。

---

### Phase 1: CLI ロジックの共通化（リファクタ）
**目標**: `main.py` の生成分岐を Web からも呼べる純関数に切り出す。

- [x] `src/d2v/web/service.py` に `run_d2v_job(params, output_dir, progress)` を実装
      （`auto/single/split/focus/zone` の分岐を集約し、`parser` → `partitioner` → `pipeline.run` を駆動）
- [x] `src/d2v/pipeline.py` の `run()` に `progress_callback: Callable | None = None` を追加し、
      各ステップ（生成開始/レンダリング/評価スコア/ベスト更新/イテレーション完了）でイベントを emit（既定 None で従来挙動）
- [x] `main.py` を `service.py` 経由に置き換え（CLI 出力は progress_callback を rich 表示に橋渡し）
- [x] 既存 CLI の回帰確認（`--focus` / `--zone` / `--no-split` / v2d が不変で動作）

**完了の定義**: CLI の全モードが `service.py` 経由で従来どおり動作し、進捗イベントを購読できる。

**実装（2026-07-16）**
- `src/d2v/progress.py`（新規）: UI 非依存の `ProgressEvent` / `ProgressCallback` / `emit()` を定義。CLI(rich) と Web(SSE) で同一イベント源を共有する。
- `src/d2v/pipeline.py`: `run()` に `progress_callback` を追加。iteration_start / generate / render / render_failed / evaluate / score / passed / early_stop / pipeline_done を**追加的に** emit（既存の rich コンソール表示は維持し、既定 None で従来挙動を完全保持）。
- `src/d2v/web/service.py`（新規）: `D2VParams` / `DiagramOutput` / `D2VJobResult` / `D2VJobError` と `run_d2v_job()` を実装。`main.py` の single/split/focus/zone 分岐・検証・境界図構築・ベスト画像集約をここへ集約。検証エラーは `sys.exit` ではなく `D2VJobError` を送出（Web で捕捉可能）。job レベルの topology / plan / diagram_start / diagram_done / job_done を emit。
- `main.py`: run_d2v を service 経由へ置換。`_run_single/_run_split/_run_focus/_run_zone` を削除し、`_cli_progress`（job レベルイベントを rich 表示。イテレーション詳細は pipeline が自前表示）＋ `_print_job_summary/_print_single_summary/_print_split_summary` に再編。未使用となった `parser/pipeline/shutil` の import を除去。
- 回帰確認: `python -m pytest tests/ -q` → **25 passed**。`python main.py -i examples/sample_topology_large.yaml --zone dc-core` を実行し、Step ヘッダ・イテレーション詳細・サマリーパネル・出力パスが従来どおり（9/10、passed）であることを確認。v2d CLI は不変。

---

### Phase 2: ジョブ管理と進捗ストリーミング
**目標**: 非同期ジョブの作成・実行・進捗配信を成立させる。

- [x] `src/d2v/web/events.py`: `JobStatus`(queued/running/succeeded/failed) と `ProgressEvent`(type/iteration/score/message/timestamp) を Pydantic 定義
- [x] `src/d2v/web/jobs.py`: インメモリ `JobRegistry`。ジョブ生成 → `ThreadPoolExecutor` で `service.run_*_job` 実行 → 進捗を `queue.Queue` に蓄積 → 結果・成果物パスを保持
- [x] `/api/d2v/jobs`（作成）・`/api/jobs/{id}`（状態）・`/api/jobs/{id}/events`（SSE）を実装
- [x] 各ジョブは一意な作業ディレクトリ（`output/webui/<job_id>/`）へ出力
- [x] LLM 認証エラー・レート制限・生成失敗を `failed` として構造化返却（CLI の sys.exit を Web で捕捉できるよう例外化）

**完了の定義**: API から d2v ジョブを投げ、SSE で iteration ごとのスコア進捗を受信し、完了時に結果メタを取得できる。

**実装（2026-07-16）**
- `src/d2v/web/events.py`（新規）: `JobState`(queued/running/succeeded/failed) と `event_to_dict()`（`ProgressEvent` → SSE 用 JSON。Path 等を再帰的に str 化する `_sanitize`）。
- `src/d2v/web/jobs.py`（新規）: `Job`（状態・結果・進捗蓄積）と `JobRegistry`。進捗配信は `queue.Queue` ではなく `threading.Condition` + イベントリスト方式に変更（遅延接続でも全イベントをリプレイでき、複数 SSE 接続が独立インデックスで並行購読できるため堅牢）。`ThreadPoolExecutor(max_workers=2)` で `service.run_d2v_job` を実行し、`job.add_event` を progress_callback として渡す。`D2VJobError`→検証エラー、`SystemExit`→認証/レンダリング失敗、その他例外を全て `failed` として構造化。`output/webui/<job_id>/` に隔離出力。
- `src/d2v/web/app.py`: `D2VJobRequest`(Pydantic, CLI 引数と 1:1・範囲バリデーション付き)、`POST /api/d2v/jobs`（example 名のパストラバーサル防止・YAML サイズ上限 1MB）、`GET /api/jobs/{id}`（状態＋結果メタ）、`GET /api/jobs/{id}/events`（`StreamingResponse` による SSE）を追加。
- 動作確認: small サンプルで job 作成 → SSE で topology/plan/iteration_start/generate/render/evaluate/score/passed/pipeline_done/job_done/end の全イベントを受信 → 状態 `succeeded`（score 10, image=input_best.png）。異常系も確認: パストラバーサル example → **400**、不正 YAML → job `failed`（サーバ継続・クラッシュせず）。429 レート制限は既存リトライで自動処理。

---

### Phase 3: d2v フロントエンド（入力と結果表示）
**目標**: ブラウザだけで d2v を実行し画像を確認できる。

- [x] 入力パネル: サンプル選択 / ファイルアップロード / YAML 直接貼り付けの切替
- [x] パラメータフォーム: format, max-iter, threshold, patience, mode, split-threshold, no-split, focus+hops, zone(複数選択), zone-opacity（CLI と 1:1）
- [x] 実行 → SSE 進捗ログ（イテレーション・スコアの逐次表示）＋スコア推移スパークライン
- [x] 結果ビュー: 生成画像プレビュー（ズーム/パン）、分割時は複数枚をタブ/ギャラリー表示、俯瞰図＋ゾーン詳細の区別
- [x] 詳細タブ: DOT ソース表示（シンタックス強調）、評価 JSON（スコア・指摘事項）、各成果物のダウンロードリンク
- [x] YAML 妥当性の事前チェック（`parser.load_model` 相当をサーバ側 dry-run するエンドポイント、任意）

**完了の定義**: サンプル選択 → 実行 → 画像・スコア・DOT・評価をブラウザで確認しダウンロードできる。

**実装（2026-07-16）**
- `app.py`: 成果物配信エンドポイントを追加。`GET /api/examples/{name}`（プレビュー）、`GET /api/jobs/{id}/image|dot|eval`（key で図を選択、パストラバーサル不要な key 方式）。
- `static/index.html`: d2v パネルを実装。左カラム＝入力（サンプル/ファイル/貼り付けの切替・プレビュー）＋パラメータフォーム（format/max-iter/threshold/patience/mode/split-threshold/no-split/focus+hops/zone/zone-opacity を CLI と 1:1）、右カラム＝進捗＋結果。
- `static/app.js`: メタ読込→フォーム初期化、モード別オプション表示、ジョブ投入、`EventSource` による SSE 購読で進捗ログを逐次表示、スコア推移を SVG スパークライン描画。完了後に結果（画像/DOT/評価タブ・複数枚の図切替タブ・ダウンロードリンク）を描画。`escapeHtml` で XSS 対策。
- `static/style.css`: 2 カラムレイアウト・フォーム・進捗ログ・結果ビューのスタイル。
- ブラウザ実機確認（統合ブラウザ）: small サンプル×single で実行 → SSE 進捗が逐次表示、スパークライン「スコア推移: 10」、結果画像（WAN/Core/DMZ/Office の cluster・IP ラベル付き）を表示、DOT タブ（DOT ソース）・評価タブ（10/10・全ルールチェック✓・指摘なし）・画像/DOT ダウンロードリンクまで全て動作。dry-run は「プレビュー」ボタン＋ `parser` によるジョブ実行時検証で担保（不正 YAML は job failed）。

---

### Phase 4: v2d フロントエンド（画像 → YAML）
**目標**: 画像アップロードから YAML・精度・再描画をブラウザで確認できる。

- [x] v2d タブ: 画像ドラッグ&ドロップ、プレビュー、`truth` YAML 任意指定、`rerender` トグル
- [x] `/api/v2d/jobs`（multipart アップロード）→ SSE 進捗 → 抽出サマリ（node/edge/cluster/confidence）表示
- [x] 出力: 生成 YAML の表示・コピー・ダウンロード、サイドカー JSON、notes/low-confidence の表示
- [x] `truth` 指定時は精度（ノード/エッジ F1・種別/ゾーン/loopback 一致率）を表形式で表示
- [x] `rerender` 時は再描画画像を並べて元画像と視覚比較
- [x] アップロード検証: 拡張子（png/jpg/jpeg）・サイズ上限・MIME 検査（OWASP: 不正ファイル/パストラバーサル対策）

**完了の定義**: 画像を上げると YAML・確信度・（指定時）精度と再描画をブラウザで確認できる。

**実装（2026-07-16）**
- `service.py`: `V2DJobResult` と `run_v2d_job()` を追加。`v2d.pipeline.run`（抽出→補正→YAML）→ 任意で `v2d.evaluate.evaluate_files`（精度）→ `rerender_with_d2v`（往復再描画）を束ね、`v2d_extract/v2d_extracted/v2d_metrics/v2d_rerender/job_done` を emit。metrics は PRF を構造化 dict 化。
- `jobs.py`: `create_v2d_job`（画像バイトをジョブ dir に保存・truth 保存）・`_run_v2d` を追加。`Job.result` を `D2VJobResult | V2DJobResult` に拡張し、`to_dict` を kind で分岐（v2d は counts/confidence/notes/low_confidence/metrics/rerender を返す）。
- `app.py`: `POST /api/v2d/jobs`（multipart, `UploadFile`。拡張子/MIME/サイズ(12MB) 検査）、`GET /api/jobs/{id}/v2d/yaml|sidecar|original|rerender` を追加。
- `index.html` / `app.js` / `style.css`: v2d パネル（ドラッグ&ドロップ＋プレビュー、rerender/format/truth、SSE 進捗、抽出サマリ、YAML/所見/精度/元画像・再描画の詳細タブ、YAML・サイドカーのダウンロード、精度テーブル）。`FormData` でアップロード。
- 動作確認: `sample_topology_small_best.png` ＋ 正解 YAML で API 実行 → SSE 全イベント受信、`succeeded`（nodes=7/edges=6/clusters=4・confidence 0.98、**ノード F1=1.00・エッジ F1=0.83**、種別 1.00/ゾーン 0.57/loopback 0.00）。統合ブラウザで v2d タブ→結果描画（サマリ・YAML・精度テーブル・所見・元画像表示・ダウンロード）を確認。異常系（拡張子/MIME/サイズ）は 400/413 で拒否。

---

### Phase 5: 履歴・ギャラリー・利便性（任意）
**目標**: 実行履歴と成果物を扱いやすくする。

- [x] ジョブ履歴一覧（当該セッションのジョブとスコア・サムネイル）
- [x] `output/webui/` の既存成果物ブラウズ（overview/zone/focus の階層表示）
- [x] v2d → d2v ワンクリック連携（抽出 YAML をそのまま d2v タブへ流し込み再描画）
- [x] パラメータのプリセット保存 / 共有用リンク（クエリ埋め込み）

**完了の定義**: 過去ジョブを一覧・再表示でき、v2d→d2v の往復を GUI で完結できる。

**実装（2026-07-16）**
- `jobs.py`: `Job.summary()`（履歴用コンパクト要約）と `JobRegistry.list_jobs()`（新しい順）を追加。
- `app.py`: `GET /api/jobs`（全ジョブ要約一覧）を追加。
- フロント（`index.html` / `app.js` / `style.css`）:
  - **履歴ドロワー**: ヘッダの「履歴」ボタンで開閉。d2v/v2d 種別バッジ・ラベル・状態・スコア/確信度・**サムネイル**（d2v はベスト画像、v2d は元画像）を表示。成功ジョブはクリックで該当タブへ切替え結果を再表示（`output/webui/<job_id>/` の成果物を配信経由で参照するため、overview/zone/focus を含む分割ジョブも複数図タブで閲覧可能）。
  - **v2d → d2v 連携**: v2d 結果の「この YAML で d2v 図を生成 →」ボタンで、抽出 YAML を d2v タブの貼り付け欄へ流し込み（ワンクリックで往復）。
  - **共有リンク**: 「現在の設定を URL にコピー」で d2v パラメータ（＋サンプル選択）をクエリ化しクリップボードへ。ページ読込時に `applyQueryParams` でフォームへ復元（プリセット/共有）。
- 動作確認（統合ブラウザ）: 履歴ドロワーに 2 ジョブ（d2v 9/10・v2d node7/edge6/0.99）＋サムネイル表示、クリックで結果再表示、共有 URL（`?mode=zone&threshold=6&zone_opacity=0.6&no_split=1...`）のラウンドトリップ復元、v2d→d2v で抽出 YAML の流し込みを確認。テスト **25 passed**。

---

### Phase 6: セキュリティ・運用・テスト
**目標**: ローカルツールとして安全・安定に運用できる。

- [x] 既定 `127.0.0.1` バインド。外部公開時の注意を README に明記（認証は範囲外/リバースプロキシ前提）
- [x] 入力サイズ上限・同時実行ジョブ数の上限・タイムアウト
- [x] 静的/成果物配信のパス正規化（`output/webui/<job_id>` 配下限定、パストラバーサル防止）
- [x] 画像/YAML の検証を共通化しエラーを構造化（4xx）
- [x] `tests/` に API テスト（FastAPI `TestClient`）: ジョブ作成・状態遷移・成果物取得・不正入力拒否（LLM はモック）
- [x] README に「GUI の使い方」節を追加（起動・パラメータ・スクリーンショット）

**完了の定義**: `pytest` が緑。ローカルで安全に起動でき、README だけで GUI を使い始められる。

**実装（2026-07-16）**
- `jobs.py`: 同時実行ジョブ数の上限（`MAX_ACTIVE_JOBS=4`）を追加。`_ensure_capacity()` で queued/running を数え、超過時は `JobBusyError`。`app.py` は d2v/v2d の作成で捕捉し **429** を返す。実行は `ThreadPoolExecutor(max_workers=2)` で多重度を制限。
- パス安全性: 成果物配信は client からパスを受け取らない **key 方式**（d2v）／サーバ保持パス（v2d の original/sidecar/rerender）で、パストラバーサルの余地がない。examples は親ディレクトリ一致を検証。静的配信は `StaticFiles`。
- 入力検証（構造化 4xx）: YAML 1MB / 画像 12MB 上限、画像の拡張子・MIME 検査、source/format の妥当性 → 400/413。未完了/不明ジョブは 409/404。
- `tests/test_web_api.py`（新規, 13 件）: `TestClient` で meta・examples（正常＋トラバーサル 404）・d2v 検証（bad source/missing example/traversal/oversize）・**d2v ライフサイクル**（service をフェイク差し替え → 状態遷移 → image/dot/eval 取得 → 履歴掲載）・v2d 検証（拡張子/MIME/空）・**v2d ライフサイクル**（yaml/sidecar/original 取得・未実施 rerender は 404）。LLM 不要で高速。
- README: 「ブラウザ GUI（Web UI）」節を追加（`pip install -e '.[web]'` / `python main.py serve` / できること / セキュリティ・運用注意）。プロジェクト構成に `web/`・`progress.py` を反映。
- 検証: `python -m pytest tests/ -q` → **38 passed**（既存 25 + API 13）。

**完了サマリ**: Phase 0〜6 まで全て完了。CLI を後方互換のまま維持しつつ、d2v/v2d をブラウザで実行・確認できる GUI を追加した。

---

## 技術的決定事項（webui）

| 項目 | 決定 | 理由 |
|------|------|------|
| 追加依存 | `fastapi` / `uvicorn[standard]` / `python-multipart` を **optional extra `web`** に | GUI を使わない利用者に追加依存を強制しない |
| 起動方法 | `python main.py serve`（内部で uvicorn 起動） | 既存エントリポイントに統一。CLI 体験を崩さない |
| 状態管理 | インメモリ（プロセス内） | 単一ユーザーのローカルツール想定。永続化は将来課題 |
| 出力先 | `output/webui/<job_id>/` | 既存 `output/` 規約を踏襲。`.gitignore` 済み |
| 進捗契約 | `pipeline.run` の `progress_callback`（純データイベント） | CLI(rich) と Web(SSE) で同一イベント源を共有 |

## リスクと対策（webui）

- **長時間 LLM ジョブでの UX 低下** → SSE で逐次進捗＋キャンセル（ジョブ破棄）を用意
- **同時実行によるレート制限（TPM）超過** → 同時ジョブ数を制限し、429 は既存リトライに委譲
- **CLI ロジック二重化** → Phase 1 の `service.py` 共通化を先に完了させる
- **セキュリティ（ローカルツールの油断）** → 既定 localhost・入力検証・パス限定を Phase 6 で担保

## マイルストーン（webui）

### W1: 起動
- [x] `python main.py serve` で空 UI が出る（Phase 0）

### W2: 共通化
- [x] CLI が service.py 経由で不変動作＋進捗イベント購読（Phase 1-2）

### W3: d2v GUI
- [x] ブラウザで生成→画像/スコア確認（Phase 3）

### W4: v2d GUI
- [x] ブラウザで画像→YAML/精度確認（Phase 4）

### W5: 仕上げ
- [x] 履歴・セキュリティ・テスト・README（Phase 5-6）

---

## 直近の次アクション（webui）

- [x] `web` extra を pyproject に追加し `python main.py serve` の雛形を通す
- [x] `pipeline.run` に `progress_callback` を追加し CLI を `service.py` 経由へ寄せる
- [x] SSE で 1 ジョブぶんの進捗が流れる最小経路を通す
- [x] d2v フォーム（サンプル選択→実行→画像表示）を最小構成で成立させる


<br><br><br>

---

<br><br><br>


# validate (セマンティック検証 / design lint) 実装計画

## 目的

`parser` が担う **YANG スキーマ検証**（必須フィールドの有無・型）を超えて、
トポロジ **設計そのものの論理的妥当性**をルールベースで機械検証し、
検出した問題を LLM が「なぜ問題か・どう直すか」の自然言語で補足する
**セマンティック検証（design lint）レイヤー**を追加する。

図の見やすさ（`evaluator` が担当）とは対象が異なり、
こちらは **YAML の内容（設計）が正しいか**を検証する。d2v の作図・v2d の抽出いずれの
入り口から来た YAML にも適用でき、作図前の事前チェックとしても機能する。

```
YAML (iida-network-model)
        │
        ▼
   parser.load_model()          ← スキーマ検証（既存）
        │  TopologyModel
        ▼
   validator.validate()         ← セマンティック検証（本計画）
        │  ・構造整合性（宙ぶらりんリンク/未定義参照/重複）
        │  ・一意性（device-id/ASN/loopback/IP 重複・重なり）
        │  ・到達性/冗長性（孤立ノード/SPOF/冗長経路欠如）
        │  ・L3 整合性（interface IP と ip-subnet の整合）
        │  ・ポリシー制約（任意・宣言的ルール）
        ▼
   ValidationReport (issues[])
        │  --explain 時のみ
        ▼
   validator.explain()          ← LLM が理由・修正案を付与
```

---

## 設計方針

| 項目 | 決定 | 理由 |
|------|------|------|
| 検証エンジン | **純 Python ルールベース**（LLM 非依存） | 決定論的・高速・API キー不要。CI やコミット前フックでも回せる |
| LLM の役割 | **説明・修正案の付与のみ**（`--explain` 時） | 検出は機械で確定させ、LLM は非決定的な「解説」に限定して誤検出を防ぐ |
| 入力 | `parser.load_model()` の `TopologyModel` | 既存パーサ資産を再利用し、作図・v2d と同じモデルを検証 |
| 重大度 | `error` / `warning` / `info` の 3 段階 | 「設計ミス」と「要確認」を区別。終了コード/CI ゲートに使う |
| グラフ解析 | 隣接リストを内製（外部依存なし）で連結性・関節点を判定 | `networkx` を持ち込まず軽量に保つ（partitioner と同方針） |
| 出力 | `ValidationReport`（Pydantic）＋ JSON ＋ rich テキスト | 機械可読（AI/CI 向け）と人間可読の両立 |
| 例外方針 | ライブラリ層は `errors.D2VError` 系を送出、UI 層が終了方法を決定 | 既存の parser/evaluator と同一規約 |

---

## ディレクトリ構成（目標）

```
src/d2v/
├── validator.py            # 本体（ルール群 + ValidationReport + explain）
└── errors.py               # ValidationError（設計上の致命的違反用・任意）

prompts/
└── design-lint.md          # --explain 用：issue 群 → 理由/修正案の LLM プロンプト

tests/
└── test_validator.py       # ルール単体テスト（LLM 不要）
```

> 単一モジュール `validator.py` に集約する（`evaluator.py` と同じ粒度）。
> ルールが増えて肥大化した場合のみ `src/d2v/validate/` パッケージへ分割する。

---

## データモデル（案）

```python
class ValidationIssue(BaseModel):
    rule: str            # ルール ID（例: "dangling-endpoint", "spof-device"）
    severity: str        # "error" | "warning" | "info"
    message: str         # 機械生成の簡潔な説明
    targets: list[str]   # 関係する device-id / connection-id / subnet-id
    explanation: str = ""  # LLM が付与（--explain 時のみ）
    suggestion: str = ""   # LLM が付与（--explain 時のみ）

class ValidationReport(BaseModel):
    ok: bool                       # error が 0 件か
    counts: dict[str, int]         # {"error": n, "warning": m, "info": k}
    issues: list[ValidationIssue]
```

---

## 検証ルール（初期セット）

| ルール ID | 重大度 | 内容 |
|-----------|:------:|------|
| `dangling-endpoint` | error | `physical-connection.endpoint` が 2 要素でない／片側のみ定義 |
| `unknown-device-ref` | error | endpoint が存在しない `device-id` を参照 |
| `unknown-interface-ref` | error | endpoint が device に無い `interface-id` を参照 |
| `duplicate-device-id` | error | `device-id` の重複 |
| `duplicate-connection` | warning | 同一ノード・ポート対の重複リンク（無向・ポート込み） |
| `self-loop` | error | 両 endpoint が同一 device の自己ループ |
| `duplicate-loopback` | error | 複数 device で同一 `loopback` |
| `duplicate-asn` | info | 同一 `asn` を複数 device が使用（eBGP 設計では要確認・iBGP では正常） |
| `ip-address-overlap` | error | 異なるインターフェースで同一/重複する `ip-address` |
| `subnet-overlap` | warning | `ip-subnet.prefix` 同士の CIDR 重なり |
| `iface-subnet-mismatch` | warning | interface の `ip-address` がどの `ip-subnet.prefix` にも属さない |
| `p2p-mask-mismatch` | info | /30・/31 リンクの両端 IP がプレフィックス不整合 |
| `isolated-device` | warning | どの physical-connection にも現れない孤立ノード |
| `spof-device` | warning | グラフの関節点（cut vertex）— 単一障害点になり得る機器 |
| `spof-bridge-link` | warning | 橋（bridge edge）— 落ちると分断されるリンク |
| `no-redundant-path` | info | 特定ノード対（core↔外部等）に冗長経路が無い |
| `zone-policy-violation` | error/warning | 宣言済みゾーン間ポリシーへの違反（任意・Phase 3） |

> CIDR 重なり・所属判定は標準ライブラリ `ipaddress` で実装（追加依存なし）。
> SPOF/bridge は DFS ベースの関節点・橋検出（Tarjan）を内製する。

---

## 実装フェーズ

### Phase 0: 基盤とデータモデル
**目標**: `validator` の骨格とレポート型を用意し、CLI から空実行できる。

- [x] `src/d2v/validator.py` に `ValidationIssue` / `ValidationReport`（Pydantic）を定義
- [x] `validate(model: TopologyModel, *, policies=None) -> ValidationReport` の枠を実装（ルールは空）
- [ ] `errors.py` に必要なら `ValidationError`（致命的違反用・任意）を追加 → 現状 `validate()` は例外を送出せずレポートを返す設計のため**見送り**（必要になった Phase で追加）
- [x] rich でのレポート整形（重大度別カラー・件数サマリ）ヘルパ

**完了の定義**: `validate(model)` が空の `ValidationReport(ok=True)` を返し、整形表示できる。✅

**実装（2026-07-16）**
- `src/d2v/validator.py`（新規）: `ValidationIssue`（rule/severity/targets/message＋LLM 用 explanation/suggestion）と `ValidationReport`（ok/counts/issues）を Pydantic 定義。`ValidationReport.from_issues()` で件数集計と `ok`（error 0 件）判定を一元化。
- ルール登録機構: `@rule` デコレータで検証関数（`TopologyModel -> list[ValidationIssue]`）を `_RULES` に登録し、`validate()` が登録順に全ルールを実行して集約。Phase 0 ではルール未登録のため空レポートを返す。Phase 1 以降は `@rule` を付けるだけで拡張できる。
- 重大度は `error`/`warning`/`info` の固定 3 段階（`SEVERITIES`）。検出は決定論的（LLM 非依存）で、`explanation`/`suggestion` は Phase 4 の `--explain` 用に予約。
- rich 整形 `render_report()`: 問題なしは緑の 1 行、問題ありは重大度別カラーの件数サマリ＋テーブル（重大度/ルール/内容/対象）を返す renderable。
- `errors.py` の `ValidationError` は上記理由で見送り。
- `tests/test_validator.py`（新規, 6 件）: 空モデルで `ok=True`・件数集計/ok 判定・`@rule` 登録と実行・`render_report` の空/issue 表示を検証。`python -m pytest tests/ -q` → **80 passed**（既存 74 + validator 6）。

---

### Phase 1: 構造整合性・一意性ルール
**目標**: LLM 不要で確実に検出できる「壊れた参照・重複」を実装する。

- [x] `dangling-endpoint` / `unknown-device-ref` / `unknown-interface-ref` / `self-loop`
- [x] `duplicate-device-id` / `duplicate-connection` / `duplicate-loopback` / `duplicate-asn`
- [x] `ip-address-overlap`（`ipaddress` で正規化して比較）
- [x] 各ルールの単体テスト（正常系＋違反系）

**完了の定義**: 破損参照・重複を持つサンプルで対応 issue が過不足なく検出される。✅

**実装（2026-07-16）**
- `src/d2v/validator.py` に Phase 1 の 9 ルールを `@rule` 登録で追加（決定論・LLM 非依存）:
  - 構造整合性: `dangling-endpoint`（端点数≠2／device-id 欠落・error）, `unknown-device-ref`（未定義 device 参照・error）, `unknown-interface-ref`（device に無い interface 参照・error）, `self-loop`（両端同一・error）
  - 一意性: `duplicate-device-id`（error）, `duplicate-connection`（無向・ポート込みキーで重複判定・warning）, `duplicate-loopback`（IP 正規化して比較・error）, `duplicate-asn`（共有は iBGP 正常/eBGP 要確認・info）, `ip-address-overlap`（ホスト IP 正規化で衝突検出・error）
  - 補助関数: `_conn_target`（connection-id 優先の識別子）, `_iface_ids`, `_edge_key`（無向・ポート込み `frozenset` キー）, `_norm_ip`（`ipaddress.ip_interface` でホスト部正規化・解析不能は None）。追加依存なし（標準 `ipaddress`）。
  - 設計判断: 重複リンク判定は**ポート込み**のため、別ポートの並行リンク（LAG）は誤検出しない。loopback/IP は表記差（マスク違い等）を正規化で吸収。
- `tests/test_validator.py` に Phase 1 テスト 13 件を追加（正常トポロジで 0 件、各違反の検出、LAG 非検出、表記差の重複検出、info のみは `ok=True` 等）。
- 検証: 実サンプル 3 件（small/medium/large）で**誤検出ゼロ**（すべて「問題なし」）を確認。`python -m pytest tests/ -q` → **93 passed**（既存 80 + Phase 1 13）。

---

### Phase 2: L3 整合性・到達性・冗長性
**目標**: IP 整合とグラフ構造（孤立/SPOF/冗長）を検証する。

- [x] `subnet-overlap` / `iface-subnet-mismatch` / `p2p-mask-mismatch`（`ipaddress` 利用）
- [x] physical-connection から無向グラフ（隣接リスト）を構築するヘルパ
- [x] `isolated-device`（次数 0 の検出）
- [x] `spof-device` / `spof-bridge-link`（Tarjan の関節点・橋検出を内製）
- [ ] `no-redundant-path`（指定ノード対の 2 経路存在チェック・任意）→ ノード対の指定が必要なため **Phase 3（ポリシー）へ移送**。SPOF/橋の検出で冗長欠如は概ねカバー済み
- [x] グラフ系ルールの単体テスト（線形/冗長/分断トポロジ）

**完了の定義**: 冗長のない構成で SPOF・橋が、冗長構成では検出されないことを確認できる。✅

**実装（2026-07-16）**
- `src/d2v/validator.py` に Phase 2 の 6 ルールを追加（決定論・追加依存なし＝標準 `ipaddress`）:
  - L3 整合性: `subnet-overlap`（prefix 同士の CIDR 重なり・warning）, `iface-subnet-mismatch`（interface IP がどの ip-subnet にも属さない・warning／サブネット未宣言時はスキップ）, `p2p-mask-mismatch`（両端 /30・/31 の P2P リンクで別ネットワーク・info）
  - 到達性・冗長性: `isolated-device`（次数 0・warning）, `spof-device`（関節点＝cut vertex・warning）, `spof-bridge-link`（橋＝bridge edge・warning／並行リンク LAG は冗長として除外）
  - グラフ解析ヘルパ（外部依存なし）: `_build_graph`（physical-connection→無向隣接リスト。自己ループ/未定義参照/端点数≠2 は無視）, `_articulation_and_bridges`（Tarjan/DFS で関節点・橋を一括算出）, `_pair_multiplicity`（デバイス対の接続本数＝LAG 判定）, `_iface_ip`。
  - 設計判断: `spof-bridge-link` は並行リンク（LAG, 多重度>1）を橋から除外し冗長を誤検出しない。`iface-subnet-mismatch` はサブネット宣言がある場合のみ動作。`no-redundant-path` はノード対指定が要るため Phase 3 へ移送。
- `tests/test_validator.py` に Phase 2 テスト 10 件を追加（`_chain`/`_ring` ヘルパで線形＝SPOF/橋あり・環状＝なし、subnet 重複、iface 不一致とスキップ、P2P 一致/不一致、孤立、LAG は橋でない）。Phase 1 の「正常トポロジ」テストは 2 ノード単一リンクが正しく橋になるため、冗長な 3 ノードリングへ更新。
- 検証: small サンプルで非冗長ツリーの SPOF 4 台・橋 6 本・外部リンクの iface-subnet-mismatch を**すべて真陽性**として検出（error=0）。冗長リングでは SPOF/橋ゼロ。`python -m pytest tests/ -q` → **102 passed**（既存 93 + Phase 2 9 純増）。

---

### Phase 3: ポリシー制約（宣言的ルール・任意）
**目標**: 社内設計標準を機械可読にし、違反を検証する。

- [x] ゾーン間通信ポリシーの記法を定義（例: `server → external は firewall 経由必須`）
- [x] ポリシーファイル（YAML）読み込みと `zone-policy-violation` 判定
- [x] 「core は必ず冗長」等のゾーン単位冗長ポリシー
- [x] ポリシー検証の単体テスト

**完了の定義**: サンプルポリシーに反する構成で違反が、準拠構成では検出されない。✅

**実装（2026-07-16）**
- `src/d2v/validator.py` にポリシー機構を追加（追加依存なし）:
  - モデル: `NodeSelector`（`zone`/`type`＝device-type の AND、または文字列 `any`＝zone かデバイス種別のいずれか一致）, `ZoneTransitPolicy`（src→dst は via 経由必須）, `ZoneRedundancyPolicy`（対象は冗長であること）, `PolicySet`。
  - `load_policies(path)`: ポリシー YAML（`zone-transit` の from/to/via・`zone-redundancy` の zone/type）を読み込み `PolicySet` を返す（不正キー/破損は `InputError`）。
  - 検証: `_check_zone_transit`（via ノードを除いたグラフで src→dst が到達可能なら**迂回経路あり**として `zone-policy-violation`）, `_check_zone_redundancy`（対象が関節点／橋の端点なら `zone-redundancy-violation`。LAG は冗長として除外）。BFS ヘルパ `_reachable_from`、Phase 2 のグラフ解析を再利用。
  - `validate(model, *, policies=PolicySet|None)` を拡張し、登録ルールに続けてポリシーを実行。
- `examples/sample_policy.yaml`（新規）: サンプルポリシー（dmz→office は firewall 経由必須・core は冗長）。
- 設計判断: `no-redundant-path`（Phase 2 から移送）はゾーン冗長ポリシー（`zone-redundancy`）として汎用化して実現。通信ポリシーの rule id は `zone-policy-violation`、冗長ポリシーは `zone-redundancy-violation` に分離し filter しやすくした。
- `tests/test_validator.py` に Phase 3 テスト 8 件（セレクタ一致、via 経由のみ＝準拠・迂回あり＝違反、直鎖 core＝違反・リング core＝準拠、YAML ロード/空ファイル）。`_dev` に `dtype` 引数を追加。
- 検証: small トポロジ＋`sample_policy.yaml` で、dmz→office は firewall 経由のみのため**通信ポリシー準拠**、core-sw-01 は関節点のため**冗長ポリシー違反**を検出。`python -m pytest tests/ -q` → **110 passed**（既存 102 + Phase 3 8）。

---

### Phase 4: LLM 説明・修正案（--explain）
**目標**: 検出済み issue に理由と具体的修正案を付与する。

- [x] `prompts/design-lint.md`（issue 群＋トポロジ要約 → 各 issue の理由/修正案 JSON）
- [x] `validator.explain(report, model, llm) -> ValidationReport`（`explanation`/`suggestion` を充填）
- [x] LLM が捏造した issue を追加しない（機械検出を正・LLM は付与のみ）ガード
- [x] LLM をモックした経路テスト

**完了の定義**: `--explain` で各 issue に日本語の理由・修正案が付き、issue 集合は不変。✅

**実装（2026-07-16）**
- `prompts/design-lint.md`（新規）: 検出済み issue（index/rule/severity/message/targets の JSON）＋トポロジ要約を受け取り、**各 index に 1 対 1 で** `explanation`/`suggestion` を返す抽出プロンプト。新規 issue 追加・severity/rule 変更・捏造を明確に禁止。
- `src/d2v/validator.py` に `explain(report, model, llm=None) -> ValidationReport` を実装:
  - issue を JSON 化＋`parser.build_text` のトポロジ文脈を添えて LLM に渡し、`index → (explanation, suggestion)` を回収して充填。
  - **捏造ガード**: 既存 issue の index に一致する説明のみ適用。範囲外 index・非 int・型不正は無視し、**issue 集合（rule/severity/message/targets・件数・ok）は不変**。応答が壊れていれば説明なしで元の issue を保持（`_extract_json_array`/`_parse_explanations` が JSON 抽出失敗を握りつぶし、機械検出結果を壊さない）。
  - LLM は遅延 import（`get_llm`）。空レポートは LLM を呼ばず即返し。
- `render_report` を拡張し、`explain` 済みのとき テーブル下に「詳細（--explain）」として rule/対象・理由・修正案を追記（説明が無い通常表示は不変）。
- `tests/test_validator.py` に Phase 4 テスト 5 件（`_FakeLLM` モックで充填・**捏造 index 無視**・壊れた JSON で不変・空レポートは LLM 未呼び出し・render の詳細表示）。
- 検証: `python -m pytest tests/ -q` → **115 passed**（既存 110 + Phase 4 5）。API キー不要（LLM はモック）。

---

### Phase 5: CLI・Web・テスト統合
**目標**: CLI/GUI から検証を実行でき、作図前チェックにも組み込む。

- [x] `main.py` に `validate` サブコマンド（`python main.py validate -i topology.yaml [--explain] [--policy p.yaml] [--json]`）
- [x] 終了コード規約（error>0 で 1、warning のみは 0＋警告表示。`--strict` で warning も 1）
- [x] `d2v` 実行時の任意事前検証フック（`--precheck` で error があれば作図前に停止）
- [x] Web: `POST /api/validate`（YAML/サンプル/アップロード）＋結果パネル（重大度別・explain）
- [x] `tests/test_validator.py` と Web API テストを追加

**完了の定義**: CLI/GUI で検証でき、`pytest` が緑。作図前チェックとして使える。✅

**実装（2026-07-16）**
- `ValidationReport.passed(strict=False)` を追加（error>0 で不合格・strict では warning も不合格）。CLI/Web の終了・合否判定を一元化。
- CLI: `main.py` に `validate` サブコマンド分岐と `run_validate()` を追加（`-i/--input`・`--policy`・`--explain`・`--json`・`--strict`）。rich 表示または `--json`、終了コードは `report.passed(strict=...)` に従う。`run_d2v()` に `--precheck` を追加し、作図前に `validator.validate` を実行して error があれば作図せず `exit(1)`。既存 d2v/v2d/serve CLI は不変。
- Web: `POST /api/validate`（source=example/text、`strict`/`explain`）を**同期**追加（検証は LLM 不要で高速なためジョブ化しない）。`explain=True` のときのみ LLM で説明を付与し、失敗しても `explain_error` を添えて**検証結果は必ず返す**。`_read_yaml_source` に入力解決を共通化（パストラバーサル・サイズ検証つき）。`get_meta` の examples 一覧から**ポリシーファイルを除外**（`sample_policy.yaml` がトポロジ選択に混ざらないよう修正）。
- GUI: SPA に「検証: design lint」タブを追加（`index.html`/`app.js`/`style.css`）。サンプル/貼り付け・strict/explain・重大度別カラーの結果テーブル・`--explain` 詳細・合否バッジ・エラー表示。統合ブラウザで large サンプルを検証し「合格 (error 0 / warning 54 / info 0)」＋テーブル描画を確認。
- テスト: `tests/test_validator.py` に `passed()` の strict 判定、`tests/test_web_api.py` に `/api/validate` 6 件（正常・text の error 検出・strict 不合格・不正 YAML 400・パストラバーサル 400）を追加。
- 検証: CLI で strict=1・error=1・warning のみ=0 の終了コード、GUI 実機動作を確認。`python -m pytest tests/ -q` → **121 passed**（既存 115 + Phase 5 6）。

---

## 技術的決定事項（validate）

| 項目 | 決定 | 理由 |
|------|------|------|
| 追加依存 | **なし**（標準 `ipaddress` + 内製グラフ解析） | 軽量・CI で回しやすい。partitioner と同じく外部グラフ依存を避ける |
| 検出/説明の分離 | 検出＝機械（確定）／説明＝LLM（任意） | 誤検出・捏造を防ぎ、キー無しでも中核機能が動く |
| モデル共有 | `parser.TopologyModel` を入力に統一 | 作図・v2d・検証で同一モデルを使い回す |

## リスクと対策（validate）

- **誤検出（意図的な iBGP 同一 ASN 等）** → 重大度を info/warning に段階化し `--strict` で制御
- **大規模での SPOF 計算コスト** → Tarjan は線形（O(V+E)）で実装、必要なら対象を限定
- **ポリシー記法の複雑化** → Phase 3 は任意。まずは単純な zone 間ルールに限定

## マイルストーン（validate）

### V1: 骨格 — [x] `validate()` が空レポートを返す（Phase 0）
### V2: 構造/一意性 — [x] 破損参照・重複を検出（Phase 1）
### V3: L3/グラフ — [x] SPOF・孤立・IP 整合を検出（Phase 2）
### V4: 説明 — [x] `--explain` で理由/修正案（Phase 4）
### V5: 統合 — [x] CLI/GUI・事前チェック・テスト（Phase 5）

> ポリシー制約（Phase 3）も完了。**validate は全フェーズ完了**。

## 直近の次アクション（validate）

- [x] `ValidationIssue` / `ValidationReport` を定義し `validate()` 骨格を通す
- [x] Phase 1 の構造整合性ルールとテストを最優先で実装
- [x] 内製グラフ解析（隣接リスト＋Tarjan）ヘルパを用意
- [x] `main.py validate` サブコマンドで rich レポートを表示


<br><br><br>

---

<br><br><br>


# diff (意味的 diff + 図の差分ハイライト) 実装計画

## 目的

2 つのトポロジ YAML（変更前 / 変更後）の**構造的な差分**を検出し、
以下 2 つの形で提示する:

1. **意味的 diff（AI/機械可読）**: 行 diff ではなく「spine-03 を追加し leaf 全台に接続」
   のような**構造変化**として要約（`TopologyDiff` JSON ＋ LLM 自然言語サマリ）。
2. **図の差分ハイライト（人間向け）**: 追加=緑・削除=赤・変更=橙で色分けした
   **差分図**を `renderer` 拡張で描画。

ネットワーク設計は「変更の連続」であり、変更レビュー・影響把握の中核機能となる。
将来的な影響分析（blast radius）の土台にもなる。

```
before.yaml ──▶ parser.load_model() ──┐
                                       ├──▶ diff.compare() ──▶ TopologyDiff
after.yaml  ──▶ parser.load_model() ──┘         │
                                                ├──▶ diff.summarize()  ← LLM 自然言語要約（任意）
                                                └──▶ diff.render_diff() ← 差分ハイライト図（union グラフ）
                                                            add=緑 / del=赤 / change=橙
```

---

## 設計方針

| 項目 | 決定 | 理由 |
|------|------|------|
| 比較単位 | `parser.TopologyModel`（device/connection/subnet） | 行ではなく**構造**で比較し、整形差やキー順に影響されない |
| マッチング | `device-id` / `connection-id`（無ければ endpoint 対）/ `subnet-id` を安定キーに | 同一実体を追跡し、属性変更を「change」として検出 |
| 差分計算 | **純 Python・決定論的**（LLM 非依存） | 差分の正しさは機械で確定。LLM は要約のみ |
| LLM の役割 | **自然言語サマリのみ**（任意） | 「何がどう変わったか」を人間向けに言語化 |
| 図の描画 | `renderer` を拡張し**和集合グラフ**を色分け描画 | 変更前後を 1 枚に重ね、追加/削除/変更を一目で把握 |
| 出力 | `TopologyDiff`（Pydantic）＋ JSON ＋差分 PNG/SVG＋テキスト要約 | 機械可読と人間可読の両立 |
| 例外方針 | ライブラリ層は `errors.D2VError` 系、UI 層が終了方法を決定 | 既存規約に準拠 |

---

## ディレクトリ構成（目標）

```
src/d2v/
├── diff.py                 # 構造 diff（TopologyDiff）+ 要約 + 差分 DOT 生成
└── renderer.py             # 差分ハイライト描画の拡張（既存を拡張）

prompts/
└── diagram-diff.md         # TopologyDiff → 自然言語サマリの LLM プロンプト

tests/
└── test_diff.py            # 構造 diff の単体テスト（LLM 不要）
```

---

## データモデル（案）

```python
class AttrChange(BaseModel):
    field: str          # 変更フィールド（例: "zone", "loopback", "device-type"）
    before: str | None
    after: str | None

class NodeChange(BaseModel):
    device_id: str
    changes: list[AttrChange]

class TopologyDiff(BaseModel):
    nodes_added:   list[str]         # device-id
    nodes_removed: list[str]
    nodes_changed: list[NodeChange]
    edges_added:   list[str]         # connection-id / "a:if <-> b:if"
    edges_removed: list[str]
    zones_added:   list[str]
    zones_removed: list[str]
    subnets_added: list[str]
    subnets_removed: list[str]
    summary: str = ""                # LLM 要約（任意）
    def is_empty(self) -> bool: ...  # 変更なし判定
```

---

## 実装フェーズ

### Phase 0: 構造 diff エンジン
**目標**: 2 つの `TopologyModel` から `TopologyDiff` を決定論的に算出する。

- [x] `src/d2v/diff.py` に上記 Pydantic モデルを定義
- [x] `compare(before: TopologyModel, after: TopologyModel) -> TopologyDiff` を実装
  - [x] ノード: `device-id` で added/removed、共通は属性（device-type/zone/asn/loopback/interface）を比較し `NodeChange`
  - [x] エッジ: `connection-id` 優先、無ければ**無向・ポート込みの正規化キー**で added/removed
  - [x] ゾーン: device の zone 集合の差分
  - [x] サブネット: `subnet-id`/`prefix` の added/removed
- [x] rich でのテキスト要約（＋/−/~ 記号・件数サマリ）
- [x] 単体テスト（追加/削除/属性変更/エッジ張替え/変更なし）

**完了の定義**: 既知の変更ペアで `TopologyDiff` が過不足なく算出され、テキスト表示できる。✅

**実装（2026-07-16）**
- `src/d2v/diff.py`（新規・追加依存なし）:
  - モデル: `AttrChange`（field/before/after）, `NodeChange`（device_id/changes）, `TopologyDiff`（nodes/edges/zones/subnets の added/removed＋`nodes_changed`＋`summary`＋`is_empty()`）。
  - `compare(before, after)`: ノードは `device-id` で added/removed、共通ノードは device-type/zone/asn/loopback/**interface-id 集合**を比較して `NodeChange` を生成。エッジは**無向・ポート込みの正規化キー**（端点の {device-id, interface-id} 集合）で同一物理リンクを識別（`connection-id` は表示ラベルのみ）。ゾーンは device の zone 集合差分、サブネットは `subnet-id`優先（無ければ prefix）で added/removed。
  - `render_diff()`: 変化なしは緑 1 行、変化ありは件数サマリ＋ +/−/~ 記号つきの色分けテキスト（追加=緑・削除=赤・変更=橙）。
  - 設計判断: エッジ識別を connection-id ではなく**端点キー**にしたため、connection-id のリネームでは差分が出ず、実際のリンク張替え（ポート/相手変更）のみ add/remove として検出する（validator の `_edge_key` と同方針）。
- `tests/test_diff.py`（新規, 12 件）: 同一=空、ノード add/remove/属性変更、interface 集合変更、エッジ add/remove、**connection-id リネームは無変化**、無向一致、ゾーン/サブネット add/remove、render の空/変更表示、`is_empty` 既定。
- 検証: small サンプルを改変（pc-01 削除・office-sw-01 の zone 変更）した diff で削除/変更/ゾーン差分を正しく算出。`python -m pytest tests/ -q` → **133 passed**（既存 121 + diff 12）。

---

### Phase 1: 図の差分ハイライト
**目標**: 変更前後を重ねた**差分図**を色分け描画する。

- [x] `diff.build_diff_dot(before, after, topo_diff) -> str`：和集合グラフの DOT を生成
  - [x] 追加ノード/エッジ=緑、削除=赤（点線）、変更ノード=橙、無変更=淡色
  - [x] 変更ノードは changed 属性を tooltip/ラベル補助で明示
  - [x] 既存の zone(cluster)・アイコン・IP ラベル規約を踏襲
- [x] `renderer` に差分描画呼び出しを追加（凡例＝add/del/change を図中に描画）
- [x] 差分 PNG/SVG を `output/diff/` へ保存（DOT ソースも）

**完了の定義**: 変更前後の YAML から、追加/削除/変更が色分けされた 1 枚の差分図が出力される。✅

**実装（2026-07-16）**
- `src/d2v/diff.py` に差分図生成を追加:
  - `build_diff_dot(before, after, diff)`: 和集合グラフの Graphviz DOT を決定論的に生成。ノードは追加=緑(#137333)・削除=赤(#C5221F・破線)・変更=橙(#E37400)・無変更=淡灰(#9AA0A6)で塗り分け、エッジも同様に色分け（削除は破線・太線）。device-type→絵文字アイコン（🌐🔀🧱💻）、zone ごとに `subgraph cluster` 化、変更ノードはラベルに「(変更: field...)」＋tooltip に before→after を明示。図中に**凡例 cluster**（追加/削除/変更/変更なし）を描画。
  - `render_diff_diagram(...)`: `build_diff_dot` → `renderer.render` で PNG/SVG を出力（`output/diff/` に画像と `.dot` ソースを保存）。renderer は改変せず既存 `render()` を再利用（矢じり除去・cluster 淡色化・縦横比フィットをそのまま活用）。
- `tests/test_diff.py` に Phase 1 テスト 3 件（DOT の色/構造/アイコン/凡例/変更ラベル、無変更ノードの色、実レンダリングでの画像＋DOT 保存。Graphviz 未導入時は skip）。
- 検証: small サンプルを改変（office-sw-01 の zone 変更・new-srv-01 追加＋接続・pc-01 削除）した差分図を生成し、追加=緑/削除=赤点線/変更=橙「(変更: zone)」・zone クラスタ・凡例が正しく描画されることを目視確認。`python -m pytest tests/ -q` → **136 passed**（既存 133 + Phase 1 3）。

---

### Phase 2: LLM 自然言語サマリ（任意）
**目標**: 構造 diff を人間向けの要約文にする。

- [x] `prompts/diagram-diff.md`（`TopologyDiff` → 箇条書き要約・影響の一言コメント）
- [x] `diff.summarize(topo_diff, llm) -> str`（`summary` を充填。捏造せず diff 内容のみ言語化）
- [x] LLM をモックした経路テスト

**完了の定義**: `--summarize` で「何がどう変わったか」の日本語要約が付く。✅

**実装（2026-07-16）**
- `prompts/diagram-diff.md`（新規）: 構造差分 JSON を受け取り、変更点を箇条書き＋影響の一言コメントで日本語要約させるプロンプト。**JSON に無い変更の推測・捏造を禁止**し、空の区分には触れないよう指示。
- `src/d2v/diff.py` に `summarize(diff, llm=None) -> TopologyDiff` を追加:
  - `diff.model_dump(exclude={"summary"})` を LLM に渡し、応答（自然言語）を `summary` に充填した**新しい `TopologyDiff` を返す**（`render_diff` が `summary` を末尾に表示）。
  - 変化なし（`is_empty()`）は LLM を呼ばず即返し。`_strip_code_fence` でコードフェンスを除去。構造差分（nodes/edges/…）は不変。
  - 署名は計画の `-> str` ではなく、`explain()` と同じく**更新済みモデルを返す**方式に統一（rich 表示・CLI/Web 連携がしやすいため）。
- `tests/test_diff.py` に Phase 2 テスト 4 件（`_FakeLLM` モックで summary 充填・コードフェンス除去・**空 diff は LLM 未呼び出し**・render に summary 表示）。
- 検証: `python -m pytest tests/ -q` → **140 passed**（既存 136 + Phase 2 4）。API キー不要（LLM はモック）。

---

### Phase 3: 影響分析（blast radius・任意）
**目標**: 変更・障害の影響範囲をグラフ探索で提示する。

- [x] `impact(model, removed_devices|removed_edges) -> ImpactReport`：到達不能になるノード集合
- [x] 差分図上での影響範囲ハイライト（削除により分断される領域）
- [x] `validator` の SPOF 検出ロジック（Tarjan）を共有

**完了の定義**: ある機器/リンクを落としたときの到達不能範囲を提示できる。✅

**実装（2026-07-16）**
- `src/d2v/diff.py` に影響分析を追加（追加依存なし）:
  - `ImpactReport`（removed_devices/removed_edges/reachable/unreachable/components＋`is_isolating()`）。
  - `impact(model, *, removed_devices=None, removed_edges=None)`: **`validator._build_graph` を共有**して無向グラフを構築し、指定機器/リンクを除去後の残存連結成分を `_components`（BFS）で算出。最大成分（同数なら最小 device-id を含む成分＝決定的）を到達可能コアとし、切り離されたノードを**到達不能（blast radius）**として返す。
  - `render_impact()`: 除去対象・到達不能ノード・残存成分数を rich テキストで表示。
  - `build_impact_dot()` / `render_impact_diagram()`: 除去機器=灰(✖・破線)・到達不能=赤・到達可能=緑で塗り分け、切断リンクを赤破線で描く影響ハイライト図を `output/diff/` に出力。
  - SPOF との整合: `validator` が SPOF 判定する機器（関節点）を `impact` に渡すと、その機器が分断する具体的な範囲が得られる（両者は同じグラフ表現を共有）。
- `tests/test_diff.py` に Phase 3 テスト 7 件（末端除去=分断なし、関節点除去=片側孤立、中央リンク除去=二分、環状=耐性あり、除去なし=全到達可能、render テキスト、impact DOT のハイライト）。
- 検証: small サンプルで fw-01（SPOF）除去 → 到達不能 3 台（router-01, dmz-sw-01, web-server-01）・残存 3 成分を算出し、影響図（除去=灰✖・到達不能=赤・到達可能=緑・切断リンク=赤破線）を目視確認。`python -m pytest tests/ -q` → **147 passed**（既存 140 + Phase 3 7）。

---

### Phase 4: CLI・Web・テスト統合
**目標**: CLI/GUI から差分を実行・閲覧できる。

- [x] `main.py` に `diff` サブコマンド（`python main.py diff --before a.yaml --after b.yaml [--summarize] [--json] [--format]`）
- [x] 終了コード規約（差分ありで 1／`--exit-zero` で常に 0。CI の変更検知に使える）
- [x] Web: `POST /api/diff`（before/after を選択・アップロード）→ 差分図＋TopologyDiff＋要約
- [~] Web: v2d/d2v 履歴の 2 ジョブ選択で差分表示（GUI 上の変更レビュー）→ diff タブは example/貼り付けの before/after 比較に対応。履歴ジョブ直結は今後の拡張（job source 未実装）
- [x] `tests/test_diff.py` と Web API テストを追加

**完了の定義**: CLI/GUI で差分図・構造 diff・要約を確認でき、`pytest` が緑。✅

**実装（2026-07-16）**
- CLI: `main.py` に `diff` サブコマンド分岐と `run_diff()` を追加（`-b/--before`・`-a/--after`・`-o/--output-dir`・`--summarize`・`--format`・`--no-image`・`--json`・`--exit-zero`）。構造差分を rich 表示または `--json`、差分図を `output/diff/<before>__<after>.<fmt>` に生成。**終了コードは差分ありで 1・`--exit-zero` または差分なしで 0**（CI の変更検知に使える）。既存 CLI は不変。
- Web: `POST /api/diff`（before/after を `{source, example|yaml_text}` で受け、`summarize`/`image`/`format` 対応）を同期追加。構造差分 JSON＋任意の LLM 要約を返し、差分図は `output/webui/diff/<token>/` に生成して `GET /api/diff/image/{token}` で配信（token は uuid・辞書引きでパストラバーサル不可）。`_read_yaml_source`/`_load_model_from_text` を共通化（validate と共有）。
- GUI: SPA に「diff: 差分」タブを追加（`index.html`/`app.js`/`style.css`）。before/after それぞれサンプル選択・貼り付け、`--summarize`、フォーマット選択。結果は件数バッジ・自然言語要約・変更点リスト（追加=緑/削除=赤/変更=橙）・差分図（タブ切替＋ダウンロード）。統合ブラウザで差分比較フロー（変化なし表示まで）を確認。
- テスト: `tests/test_web_api.py` に `/api/diff` 5 件（text 比較・同一=空・画像生成＋配信・不明 token 404・不正 YAML 400）。`tests/test_diff.py` は Phase 0〜3 で構造 diff/図/要約/影響を網羅済み。
- 検証: CLI で差分あり=exit 1・`--exit-zero`/同一=exit 0 を確認。GUI で diff タブ動作確認。`python -m pytest tests/ -q` → **152 passed**（既存 147 + Web diff 5）。

---

## 技術的決定事項（diff）

| 項目 | 決定 | 理由 |
|------|------|------|
| 追加依存 | **なし**（内製の構造比較＋既存 `renderer`） | 軽量・決定論的。graphviz 以外を増やさない |
| マッチキー | `device-id` / `connection-id` / `subnet-id` を安定キーに | 実体追跡で「変更」を正しく検出（張替えの誤検出を回避） |
| 差分図方式 | 和集合グラフを 1 枚に色分け | 変更前後の 2 枚並置より変化点を把握しやすい |
| 出力先 | `output/diff/<name>/` | 既存 `output/` 規約を踏襲（`.gitignore` 済み） |

## リスクと対策（diff）

- **ID 変更（rename）を add+del と誤認** → 属性類似度による rename 推定を将来オプション化（初期は add/del 表示＋注記）
- **大規模差分図の可読性低下** → 変更に関与するノード近傍のみ描画する `--changed-only` を用意
- **エッジキーの不安定さ**（ポート未定義） → 無向・ポート込み正規化キーで吸収し、connection-id 優先

## マイルストーン（diff）

### D1: 構造 diff — [x] `compare()` が `TopologyDiff` を返す（Phase 0）
### D2: 差分図 — [x] add/del/change を色分け描画（Phase 1）
### D3: 要約 — [x] `--summarize` で自然言語サマリ（Phase 2）
### D4: 統合 — [x] CLI/GUI・履歴比較・テスト（Phase 4）

> 影響分析（Phase 3・blast radius）も完了。**diff は全フェーズ完了**（履歴ジョブ直結のみ今後の拡張）。

## 直近の次アクション（diff）

- [x] `TopologyDiff` モデルと `compare()` を実装しテストを通す
- [x] エッジの無向・ポート込み正規化キーを確定する
- [x] `build_diff_dot()` で和集合グラフの色分け描画を成立させる
- [x] `main.py diff` サブコマンドで差分図＋構造 diff を出力