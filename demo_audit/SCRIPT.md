# config-audit デモ台本

## シナリオの背景（口頭で語る）

> 「ネットワーク設計書はあります。YAML で管理しています。
> でも、エンジニアが実機を設定したあと、**本当に設計書通りになっているか** ── 誰も確認できていません。
> IPアドレスの打ち間違い、BGP AS番号の誤り、設定漏れ。こういうミスが混入したまま本番に入ることがあります。
> `audit` コマンドは、設計YAMLと実機コンフィグを突き合わせて、その乖離を自動で洗い出します。」

---

## Scene 1 ── 設計書を見せる（30秒）

「まず設計書を確認します。これが今回の検査対象です。」

```bash
# トポロジ構成図（d2v で生成済み）を表示
cat examples/sample_topology_small.yaml | head -30
```

ポイント:
- `router-01`（wan-edge）、`fw-01`（wan-edge）、`core-sw-01`（core）の3台に着目
- 各インターフェースのIPアドレスと BGP の ASN が設計YAMLに記載されていることを示す

---

## Scene 2 ── 全台 OK のとき（30秒）

「まず、全台が設計通りに設定されている理想の状態を見てみます。」

```bash
uv run python main.py audit \
  -i examples/sample_topology_small.yaml \
  --config-dir demo_audit/configs/ok
```

**期待される出力:**
```
✓ 設計との逸脱は検出されませんでした。
```

ポイント:
- 緑のチェックマーク一行だけが出る「気持ちよさ」を示す
- 「CI で常にこの状態を維持できます」と伝える

---

## Scene 3 ── バグ入りコンフィグを検査（メインシーン・90秒）

「では、現実にありがちなミスが複数混入したケースを見てみます。
3台のコンフィグに意図的なバグを仕込んであります。何件検出されるか見てみましょう。」

```bash
uv run python main.py audit \
  -i examples/sample_topology_small.yaml \
  --config-dir demo_audit/configs/ng
```

**仕込んだバグ（発表時にホワイトボードに書いておくと効果的）:**

| # | デバイス | バグ内容 | 検出ルール |
|---|---------|---------|-----------|
| 1 | router-01 | GE0/1 の IP: `10.1.0.99/30`（設計: `10.1.0.1/30`）← 打ち間違い | `iface-ip-mismatch` |
| 2 | router-01 | description が設計と不一致 | `description-mismatch` |
| 3 | fw-01 | GE0/1 の IP: `10.1.1.2/30`（設計: `10.1.1.1/30`）← 裏表の取り違い | `iface-ip-mismatch` |
| 4 | fw-01 | GE0/2 が未設定（設定漏れ） | `iface-missing` |
| 5 | core-sw-01 | hostname: `CoreSW01`（設計ID: `core-sw-01`）← 命名規則の不統一 | `hostname-mismatch` |

**実際の出力例:**
```
検査結果  error=3  warning=2  info=3
────── core-sw-01 ──────
 warning  hostname-mismatch     hostname が device-id と不一致   design=core-sw-01 / config=CoreSW01
────── fw-01 ──────
 error    iface-ip-mismatch     GigabitEthernet0/1 IPアドレスが設計と不一致   design=10.1.1.1/30 / config=10.1.1.2/30
 error    iface-missing         設計にある GigabitEthernet0/2 がコンフィグに存在しない   design ip=10.1.2.1/30
────── router-01 ──────
 error    iface-ip-mismatch     GigabitEthernet0/1 IPアドレスが設計と不一致   design=10.1.0.1/30 / config=10.1.0.99/30
 warning  description-mismatch  GigabitEthernet0/1 description が設計と不一致   design='To Firewall' / config='To Upstream'
```

ポイント:
- デバイス別にグループ化して色分けされた Rich テーブルが出力される
- `error` は赤、`warning` は黄で直感的に重大度がわかる
- 「このコマンド1発で、3台のコンフィグを全部チェックしました」と強調

---

## Scene 4 ── CI/CD 連携（30秒）

「最後に、CI に組み込む場合の使い方を見せます。
JSON 出力と終了コードを使えば、GitHub Actions などに組み込んで
**コンフィグをコミットするたびに自動検査**できます。」

```bash
# JSON 出力（機械可読・パイプで jq に流せる）
uv run python main.py audit \
  -i examples/sample_topology_small.yaml \
  --config-dir demo_audit/configs/ng \
  --format json | python -m json.tool | head -40

# 終了コードを確認（error があれば 1 → CI を止める）
echo "終了コード: $?"
```

```bash
# strict モード（warning も不合格扱い）
uv run python main.py audit \
  -i examples/sample_topology_small.yaml \
  --config-dir demo_audit/configs/ng \
  --strict
echo "終了コード（strict）: $?"
```

ポイント:
- `ok: false` と `counts` が JSON で返ってくる
- `$?` が `1` になることで CI を止められると示す

---

## まとめトーク

> 「設計書（YAML）が "唯一の正解" になりました。
> `audit` コマンドで、実機が設計書通りかどうかを
> コマンド1発・CI自動化で継続的に確認できます。
>
> 抽出はLLMが行うので、Cisco IOS / IOS-XE のほか、
> Junos・EOS など**ベンダー非依存**で動きます。
> 比較は決定論的ルールなので、結果がブレません。」

---

## 補足：ファイル構成（聴衆に見せる場合）

```
demo_audit/configs/
├── ok/                   ← 設計通りのコンフィグ（Scene 2 用）
│   ├── router-01.txt
│   ├── fw-01.txt
│   └── core-sw-01.txt
└── ng/                   ← バグ入りコンフィグ（Scene 3 用）
    ├── router-01.txt     ← IP打ち間違い・BGP ASN誤り
    ├── fw-01.txt         ← 裏表の取り違い・設定漏れ
    └── core-sw-01.txt    ← ホスト名の不統一
```

---

## チェックリスト（デモ前確認）

- [ ] `uv sync` で依存インストール済み
- [ ] `.env` に LLM プロバイダーの API キーが設定済み（LLM 抽出に必要）
- [ ] `uv run python main.py audit --help` が通ること
- [ ] Scene 2 のコマンドを事前に一度実行して LLM の応答を確認しておく
- [ ] ターミナルのフォントを大きめ（18pt 以上）に設定する
