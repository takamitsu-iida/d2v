# コンフィグ適合検査ツール 実装計画

機器の実際のコンフィグが、設計 YAML（iida-network-model）通りに作られているかを検査する
`audit` サブコマンドの実装計画と進捗管理ドキュメント。

## ブランチ戦略

| ブランチ | 用途 |
|---------|------|
| `master` | 現行実装（変更なし・随時戻れる） |
| `feature/config-audit` | 本機能の開発ブランチ |

```bash
# master に戻す場合
git checkout master

# 開発再開
git checkout feature/config-audit
```

---

## 概念と全体像

```
設計 YAML (iida-network-model)
        +
機器コンフィグ (.txt)           LLM 抽出
  router-01.txt  ──────────── extractor.py ──→ ExtractedConfig
  fw-01.txt      ──────────── extractor.py ──→ ExtractedConfig
        |
        ↓ comparator.py（決定論的ルール）
        |
  AuditReport（逸脱一覧 + 重大度）
        |
        ↓ reporter.py
  Rich コンソール出力 / JSON
```

**設計方針**
- 抽出（LLM）と比較（決定論的ルール）を分離する（v2d / validator と同パターン）
- 機器マッチング: ファイル名 stem = `device-id`（例: `router-01.txt` → `device-id: "router-01"`）
- コンフィグ形式: Cisco IOS/IOS-XE をデフォルト。LLM によりベンダーを自動判別

---

## CLI インターフェース

```bash
# ディレクトリ指定（*.txt を自動検出）
uv run python main.py audit \
    -i examples/sample_topology_small.yaml \
    --config-dir configs/

# ファイル個別指定
uv run python main.py audit \
    -i design.yaml \
    --config configs/router-01.txt configs/fw-01.txt

# JSON 出力
uv run python main.py audit -i design.yaml --config-dir configs/ --format json
```

---

## ファイル構成

```
src/d2v/
  audit/
    __init__.py       公開 API: run()
    schema.py         Pydantic モデル（ExtractedConfig / AuditReport）
    extractor.py      LLM コンフィグ抽出
    comparator.py     決定論的比較ルール
    reporter.py       Rich / JSON 出力
    pipeline.py       オーケストレーション

prompts/
  config-extract.md   LLM 抽出プロンプト

tests/
  test_config_audit.py
  fixtures/
    config_router_ok.txt   設計通りの Cisco IOS コンフィグ例
    config_router_ng.txt   逸脱（IP ミスマッチ等）の例
```

---

## データモデル

### ExtractedConfig（LLM 抽出結果）

```
ExtractedInterface
  name: str               # GigabitEthernet0/1 等
  ip_address: str | None  # "10.1.0.1/30"
  description: str | None
  admin_state: up | down | unknown
  lag_group: str | None   # 所属 Port-channel 名

ExtractedBgpPeer
  peer_address: str
  remote_asn: int | None
  description: str | None

ExtractedConfig
  device_id: str          # ファイル名 stem で確定
  hostname: str | None    # コンフィグ内 hostname コマンドから
  vendor: ios | iosxe | iosxr | junos | eos | unknown
  interfaces: list[ExtractedInterface]
  bgp_peers: list[ExtractedBgpPeer]
  vlans: list[int]
  confidence: float
```

### AuditReport（比較結果）

```
AuditIssue
  rule: str
  severity: error | warning | info
  device_id: str
  message: str
  detail: str

AuditReport
  ok: bool
  counts: dict[str, int]
  issues: list[AuditIssue]
```

---

## 比較ルール一覧

| ルール ID | 確認内容 | 重大度 |
|-----------|----------|--------|
| `device-unmatched` | ファイル名 stem が `device-id` と一致しない | error |
| `iface-ip-mismatch` | I/F の IP が設計と不一致 | error |
| `iface-missing` | 設計にある IP 付き I/F がコンフィグに未定義 | error |
| `iface-extra` | 設計にない IP 付き I/F がコンフィグに存在 | info |
| `description-mismatch` | I/F description が設計の接続先と不一致 | warning |
| `lag-member-mismatch` | LAG メンバー I/F が設計と不一致 | error |
| `bgp-peer-missing` | 設計の BGP ピアがコンフィグにない | error |
| `bgp-asn-mismatch` | BGP AS 番号が設計と不一致 | error |
| `hostname-mismatch` | コンフィグの hostname が device-id と不一致 | warning |

---

## 実装フェーズと進捗

### Phase 1 — データモデル定義 ✅
**ファイル**: `src/d2v/audit/schema.py`

- [x] `ExtractedInterface` モデル
- [x] `ExtractedBgpPeer` モデル
- [x] `ExtractedConfig` モデル
- [x] `AuditIssue` モデル
- [x] `AuditReport` モデル（`from_issues()` クラスメソッド含む）

### Phase 2 — LLM 抽出器 ✅
**ファイル**: `src/d2v/audit/extractor.py`, `prompts/config-extract.md`

- [x] `prompts/config-extract.md`（ベンダー自動判別・JSON 抽出指示）
- [x] `extract_from_config(config_text, device_id) -> ExtractedConfig`
- [x] JSON パース + Pydantic 検証（`v2d/extractor.py` と同パターン）

### Phase 3 — 比較ルール ✅
**ファイル**: `src/d2v/audit/comparator.py`

- [x] `compare(model, configs) -> AuditReport` メイン関数
- [x] `rule_device_unmatched`
- [x] `rule_iface_ip_mismatch`
- [x] `rule_iface_missing`
- [x] `rule_iface_extra`
- [x] `rule_description_mismatch`
- [x] `rule_lag_member_mismatch`
- [x] `rule_bgp_peer_missing`
- [x] `rule_bgp_asn_mismatch`
- [x] `rule_hostname_mismatch`

### Phase 4 — 出力フォーマッタ ✅
**ファイル**: `src/d2v/audit/reporter.py`

- [x] Rich コンソール出力（デバイス別グループ表示）
- [x] JSON 出力（`--format json`）

### Phase 5 — パイプライン + CLI ✅
**ファイル**: `src/d2v/audit/pipeline.py`, `src/d2v/audit/__init__.py`, `main.py`

- [x] `pipeline.run(design_path, config_files) -> AuditResult`
- [x] `__init__.py` で `run` を公開
- [x] `main.py` に `audit` サブコマンド追加（`run_audit()`）
- [x] `--config-dir` / `--config` / `--format` オプション

### Phase 6 — テスト
**ファイル**: `tests/test_config_audit.py`, `tests/fixtures/`

- [ ] `tests/fixtures/config_router_ok.txt`（設計通りコンフィグ）
- [ ] `tests/fixtures/config_router_ng.txt`（逸脱コンフィグ）
- [ ] `extractor` ユニットテスト（LLM モック）
- [ ] `comparator` ユニットテスト（`ExtractedConfig` 直接生成）
- [ ] `pipeline` 統合テスト
