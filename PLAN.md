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
- [ ] 大規模トポロジ（50 ノード以上）でのテスト
- [x] 評価スコアの推移を可視化（matplotlib でグラフ出力）
- [x] `diagram-system.md` のプロンプト改善（`compound=true` / `newrank=true` / `rank=same` ヒント / カラーパレット拡張）
- [ ] `diagram-evaluator.md` の評価基準見直し（APIキー設定後に実行）
- [ ] `diagram-improver.md` の改善指示精度向上（APIキー設定後に実行）

**完了の定義**: YANG YAML で記述した実トポロジで品質スコア 8/10 以上を安定して達成できる。

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

- [ ] 0. 要件定義
- [ ] 1. 入力形式の整理
- [ ] 2. 中間表現の設計
- [ ] 3. OCR / 図形検出の実装
- [ ] 4. ノード・エッジ推定の実装
- [ ] 5. YAML 変換の実装
- [ ] 6. 再描画・評価の実装
- [ ] 7. CLI への統合
- [ ] 8. テスト整備
- [ ] 9. ドキュメント整備

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
- [ ] 入力制約を定義
- [ ] 出力 YAML 仕様を定義
- [ ] 評価指標を定義

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
- [ ] 前処理パイプライン設計
- [ ] サンプル画像収集
- [ ] 失敗ケースの分類

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
- [ ] JSON スキーマ定義
- [ ] YAML 変換ルール定義
- [ ] 既存 `iida-network-model` との対応表作成

---

### Phase 3: OCR / 図形検出
**目的**: テキストと図形を分離して検出する。

**候補技術**
- OCR: `pytesseract` / `easyocr`
- 画像処理: `opencv-python`
- 図形検出: ルールベース + 必要に応じて ML

**進捗**
- [ ] OCR 実装
- [ ] 矩形検出実装
- [ ] 線分検出実装
- [ ] 矢印検出実装

---

### Phase 4: ノード・エッジ推定
**目的**: 検出結果から図の構造を復元する。

**処理**
- テキストを最寄りノードへ紐付け
- 線の端点とノードを対応付け
- cluster / zone を推定
- ラベルの意味を補完

**進捗**
- [ ] ノード候補抽出
- [ ] エッジ候補抽出
- [ ] ノード間対応ロジック実装
- [ ] クラスタ推定実装

---

### Phase 5: YAML 変換
**目的**: `iida-network-model` YAML を生成する。

**進捗**
- [ ] JSON → YAML 変換器作成
- [ ] 既存 parser と整合確認
- [ ] サンプル出力作成

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
- [ ] 再描画処理追加
- [ ] 比較指標実装
- [ ] 失敗例の可視化

---

### Phase 7: CLI への統合
**目的**: 既存 `d2v` に `v2d` コマンドを追加する。

**案**
- `python main.py vision-to-diagram --input sample.png`
- もしくは `main.py` にサブコマンド追加

**進捗**
- [ ] CLI 設計
- [ ] 引数設計
- [ ] 実行ログ整備

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
- [ ] 単体テスト追加
- [ ] サンプル画像テスト追加
- [ ] 回帰テスト追加

---

### Phase 9: ドキュメント整備
**目的**: 使い方と制約を明確化する。

**進捗**
- [ ] README に v2d を追記
- [ ] 入力サンプル追加
- [ ] 制約事項を追記

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