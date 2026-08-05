# d2v デモ実装計画

## コンセプト：「YAML を渡したら、AI が図を描いて、問題まで指摘してくれる」

---

## 1. 「おぉ」ポイントの整理

デモで聴衆が「おぉ」と反応する瞬間を先に定義する。

| # | 「おぉ」ポイント | なぜ刺さるか |
|---|-----------------|--------------|
| 1 | **YAML → 美しい構成図** が 1 コマンドで出る | 「手で書いてた図が消える」という体験 |
| 2 | **スコアが上がっていく** AI 改善ループの可視化 | AI が自分で反省して直す様子が見える |
| 3 | **73 ノード → 俯瞰図＋10ゾーン図** に自動分割 | スケールへの不安が一瞬で消える |
| 4 | **既存の図（PNG）→ YAML** に逆変換（v2d） | 「え、読めるの？」という驚き |
| 5 | **設計のバグを AI が指摘**（validate） | 「人間のレビューが要らないかも」という感覚 |
| 6 | **障害前後の diff → 影響範囲図** が出る | 「これがほしかった」という共感 |

---

## 2. ナラティブ（デモストーリー）

5〜6 分の一本道ストーリーとして構成する。

```
[opening]
「ネットワーク設計書を書いたら、構成図は自動で作りたい。
 作った図の問題は AI に指摘してほしい。それが d2v です。」

[Act 1]  YAML → 構成図（1.5 分）
  └─ 小規模（7 ノード）を live で実行 → スコア推移を見せながら図が完成

[Act 2]  スケール（1 分）
  └─ 73 ノードを playback → 俯瞰図 → ゾーン詳細 → focus drilldown

[Act 3]  逆変換（45 秒）
  └─ 既存 PNG を渡す → YAML が出てくる

[Act 4]  設計インテリジェンス（1.5 分）
  └─ validate: SPOF・IP 矛盾・宙ぶらりんリンク を検出
  └─ diff:     before/after → 差分図 + blast radius

[closing]
「YAML → 図 → 検証 → diff → 設計書。全部つながっています。」
  └─ Web UI のスクリーンショット（または live ブラウザ）
```

---

## 3. デモ形式の選択

デモは「信頼性」と「視覚インパクト」のトレードオフ。以下の 3 形式を用意し、
状況に応じて切り替える。

| 形式 | 信頼性 | インパクト | 用途 |
|------|--------|-----------|------|
| **A: `demo.py` playback** | ◎ 完全再現保証 | ○ リアルな端末演出 | 学会・講演・録画 |
| **B: live CLI + Web UI** | △ LLM 次第 | ◎ 本物感最大 | 少人数・技術者向け |
| **C: スライド＋録画動画** | ◎ | ○ | オンライン配信・資料送付 |

**推奨**: 形式 A を基本とし、時間と回線が十分なら Act 1 だけ形式 B（live）に差し替える。

---

## 4. 実装タスク一覧

### Task 1: デモキャッシュの生成（最優先）

既存の `images/` にあるものを活用しつつ、デモ専用の出力を `demo/cache/` に事前生成する。

```
demo/
└── cache/
    ├── scene1_small/        # Act 1 用
    │   ├── iter_00.png
    │   ├── iter_01.png
    │   ├── iter_02.png      ← ベスト
    │   └── scores.json      # [{"iter":0,"score":6.1}, ...]
    ├── scene2_large/        # Act 2 用
    │   ├── overview.png
    │   ├── zone-dc-fabric.png
    │   ├── zone-dc-server.png
    │   └── focus-spine-01-1hop.png
    ├── scene3_v2d/          # Act 3 用
    │   ├── input.png        # v2d に渡す入力画像
    │   └── output.yaml      # 復元された YAML
    ├── scene4_diff/         # Act 4-diff 用
    │   ├── before.png
    │   ├── after.png
    │   └── diff.png
    └── scene5_validate/     # Act 4-validate 用
        └── report.json      # validate 結果 JSON
```

**実装方法**: `demo/prep_cache.py` スクリプトを作り、上記を一括生成。

---

### Task 2: デモ用フィクスチャ YAML の作成

```
demo/
└── fixtures/
    ├── before.yaml   # spine-01 が生きている状態（small を拡張）
    ├── after.yaml    # spine-01 のリンク 2 本を削除した状態
    └── flawed.yaml   # 意図的に設計バグを仕込んだ YAML
                      #   - fw-01 が SPOF（冗長なし）
                      #   - server-02 の IF が宙ぶらりん
                      #   - IP アドレス重複
```

---

### Task 3: `demo.py` — デモランナー本体

**インターフェース**:

```bash
uv run python demo.py              # シーン 1〜5 を順に playback
uv run python demo.py --scene 1    # Act 1 だけ
uv run python demo.py --live       # Act 1 だけ live LLM 実行
uv run python demo.py --open       # 生成画像を自動で xdg-open
```

**構造** (`demo.py`):

```python
# Scene 関数は全て同じシグネチャ
def scene1_core_magic(live: bool, open_images: bool) -> None: ...
def scene2_scale(open_images: bool) -> None: ...
def scene3_v2d(open_images: bool) -> None: ...
def scene4_validate(open_images: bool) -> None: ...
def scene5_diff(open_images: bool) -> None: ...
```

**rich による演出**:

```
┌─────────────────────────────────────────────┐
│  Act 1  YAML → 構成図                        │
└─────────────────────────────────────────────┘

 入力: examples/sample_topology_small.yaml
       └─ 7 ノード / 4 ゾーン / 8 リンク

 [iter 0] Generating...  ████████░░░░  LLM へ送信中
 [iter 0] Evaluating...  ████████████  スコア: 6.1 / 10  ✗
           Issues: ゾーン背景が重なっている / ラベルが見切れている

 [iter 1] Improving...   ████████░░░░  LLM が修正中
 [iter 1] Evaluating...  ████████████  スコア: 7.8 / 10  ✗

 [iter 2] Improving...   ████████░░░░
 [iter 2] Evaluating...  ████████████  スコア: 8.4 / 10  ✓  閾値到達

 ✓ ベスト画像: output/sample_topology_small_best.png
```

**スコアが上がる視覚演出**が最大のポイント。スコアを大きめのテキストで表示し、
上昇するたびに緑色でフラッシュさせる。

---

### Task 4: Act 2 演出 — スケール reveal

大規模トポロジは live 実行が困難なため、playback 専用。ただし演出に工夫を加える。

```
 [大規模トポロジ: 73 ノード / 10 ゾーン]

 ノード数が閾値（40）を超えました。自動分割モードで実行します。

 俯瞰図 (overview)         ████████████  生成完了
 ゾーン: wan-edge           ████████████  生成完了
 ゾーン: security           ████████████  生成完了
 ゾーン: dc-core            ████████████  生成完了
 ゾーン: dc-fabric          ████████████  生成完了
 ゾーン: dc-server          ████████████  生成完了
 ゾーン: dmz                ████████████  生成完了
 ゾーン: campus-bldg-a      ████████████  生成完了
 ゾーン: campus-bldg-b      ████████████  生成完了
 ゾーン: campus-bldg-c      ████████████  生成完了
 ゾーン: management         ████████████  生成完了

 合計 11 枚の図を生成しました（うち俯瞰図 1 枚、ゾーン詳細 10 枚）
```

各ゾーンのプログレスバーが順に埋まっていく演出（`time.sleep` で間隔を再現）。

---

### Task 5: Act 3 演出 — v2d reveal

```
 [v2d: 構成図 → YAML]

 入力画像: demo/cache/scene3_v2d/input.png

 画像を解析中...  ████████░░░░
 YAML を復元中...  ████████████

 ─── 出力 YAML（抜粋）─────────────────────────────
 network-model:
   physical-layer:
     device:
       - device-id: "router-01"         ← 読み取れました
         device-type: router
         zone: wan-edge
       - device-id: "fw-01"
         device-type: firewall
 ──────────────────────────────────────────────────

 ✓ 7 台のデバイス / 6 本のリンクを復元しました
```

YAML がタイプライター風にストリーム表示されると「読み取っている感」が増す。

---

### Task 6: Act 4-validate 演出 — 設計バグ発見

```
 [validate: demo/fixtures/flawed.yaml]

 セマンティック検証中...

 ❌ [SPOF]        fw-01 — 冗長経路なし。fw-01 の障害でゾーン分断
 ❌ [DANGLING-IF] server-02.GigabitEthernet0/1 — 接続先なし（宙ぶらりん）
 ❌ [IP-CONFLICT] 10.1.1.0/30 — router-01 と core-sw-01 で重複

 ── AI サマリ ──────────────────────────────────────
 「この設計は可用性に深刻な問題を抱えています。特に fw-01 の冗長化が
  なく、単一障害点となっています。本番投入前に冗長構成を検討してください。」
 ──────────────────────────────────────────────────
```

赤いアイコンと大文字のカテゴリ名が「AI がバグを見つけた」感を演出する。

---

### Task 7: Act 4-diff 演出 — 障害影響範囲

```
 [diff: before.yaml → after.yaml]
 （spine-01 の uplink 2 本を切断した場合の影響）

 差分検出中...

  変更なし  : 5 台
  ─── 削除  : link-spine01-core01 / link-spine01-core02
  ─── 孤立  : leaf-03 / leaf-04 / server-05 / server-06 / server-07

 Blast radius: 5 台のサーバが到達不能

 差分図を生成中...  ████████████

 ✓ 差分図: demo/cache/scene4_diff/diff.png
   削除リンク → 赤破線 / 孤立ノード → グレーアウト
```

「blast radius: N 台」という数字が一番刺さる表現。

---

### Task 8: Web UI デモモード（オプション）

`GET /api/demo/tour` エンドポイントを追加し、デモシナリオ情報を返す。
フロントエンドに「Demo Tour」ボタンを追加し、クリックすると:

1. sample_topology_small.yaml を自動ロード
2. 「実行」ボタンをハイライト → ユーザが押す
3. 進捗 SSE をリアルタイムで表示
4. 完了後に図を自動で大きく表示（fullscreen モーダル）

Web UI ルートのみの変更なので、既存 API に影響しない。

---

### Task 9: `demo/SCRIPT.md` — 発表者台本

各シーンに以下を記載:
- セリフの例文
- タイミング（「ここで Enter を押す」「3 秒待つ」）
- LLM が遅い場合の fallback セリフ
- 想定質問と回答

---

## 5. 実装の優先順位

```
Priority 1 (デモ前日まで必須)
  [x] Task 1: demo/cache/ の生成（prep_cache.py + 実行）
  [x] Task 2: demo/fixtures/ の作成
  [x] Task 3: demo.py コア実装（playback モード）
  [x] Task 9: SCRIPT.md 作成

Priority 2 (あると大幅に映える)
  [x] Task 6: validate 演出の polish
  [x] Task 7: diff 演出の polish
  [x] Task 4: スケール reveal アニメーション

Priority 3 (余裕があれば)
  [ ] Task 5: v2d タイプライター演出
  [ ] Task 8: Web UI デモモード
```

---

## 6. ファイル構成（目標）

```
d2v/
├── demo.py                    ← デモランナー（新規）
├── demo/
│   ├── SCRIPT.md              ← 発表者台本（新規）
│   ├── prep_cache.py          ← キャッシュ事前生成スクリプト（新規）
│   ├── cache/                 ← 事前生成済み出力（新規）
│   │   ├── scene1_small/
│   │   ├── scene2_large/
│   │   ├── scene3_v2d/
│   │   ├── scene4_diff/
│   │   └── scene5_validate/
│   └── fixtures/              ← デモ専用 YAML（新規）
│       ├── before.yaml
│       ├── after.yaml
│       └── flawed.yaml
└── （既存ファイルはすべてそのまま）
```

---

## 7. 依存追加

`demo.py` は `rich` を使う。pyproject.toml の `[project.optional-dependencies]` に追加。

```toml
[project.optional-dependencies]
demo = ["rich>=13.0"]
```

インストール: `uv sync --extra demo`

---

## 8. リスク対策

| リスク | 対策 |
|--------|------|
| LLM API が当日タイムアウト | playback モードを必ず用意。`--live` は任意 |
| 生成図のクオリティがばらつく | `demo/cache/` は事前に何度も生成して「一番映える」ものを選定 |
| 画面が小さくて図が見えない | Web UI fullscreen モーダル（Task 8）、または `xdg-open` で別ウインドウ |
| 質問「なぜ Graphviz?」 | 「追加インフラ不要・SVG 自己完結・LLM が DOT を書くのが得意」と答える |
| 質問「商用 LLM 必須?」 | 「Ollama でローカル実行できます。GPT-4o 推奨だが claude / llama も動作確認済み」 |

---

## 9. デモ当日のチェックリスト

```
[ ] uv sync --extra demo が通る
[ ] .env の LLM キーが有効（live モード用）
[ ] demo/cache/ が全シーン分存在する
[ ] uv run python demo.py --scene 1 が正常完了（playback）
[ ] uv run python demo.py --scene 1 --live が正常完了（live）
[ ] Web UI: uv run uvicorn d2v.web.app:app が起動する
[ ] 発表スライド or SCRIPT.md を手元に用意
[ ] バックアップ: 全画像を PDF/PowerPoint に貼り付けたスライドを保険として用意
```
