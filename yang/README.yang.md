# iida-network-model YANG モデル解説

## 概要

`iida-network-model.yang` は、企業ネットワークの**構成定義**と**運用管理**を YANG 1.1 で表現するためのデータモデルです。物理層・L2・L3 の三層に加え、管理プレーンをカバーし、IPv4/IPv6 デュアルスタック・リンク冗長化・ダイナミックルーティングを統合的に記述できます。

- **Namespace**: `urn:iida:params:xml:ns:yang:iida-network-model`
- **Prefix**: `nnm`
- **YANG バージョン**: 1.1
- **検証コマンド**: `python3 -m pyang --strict -p yang yang/iida-network-model.yang`

### 依存モジュール

| モジュール            | prefix  | 用途                                       |
| --------------------- | ------- | ------------------------------------------ |
| `ietf-inet-types`     | `inet`  | `ip-address`, `ip-prefix`, `mac-address` 等 |
| `ietf-yang-types`     | `yang`  | `mac-address`, `date-and-time`             |

---

## モジュール構造

```
network-model
├── physical-layer
│   ├── device[]                   # ネットワーク機器リスト
│   │   ├── (device-identification grouping)
│   │   ├── vendor / model / os-version / serial-number
│   │   ├── role                   # spine/leaf/core/distribution/access/border/oob
│   │   ├── location               # site-id/building/room/rack-id/rack-unit
│   │   ├── hw-revision / install-date / end-of-life-date
│   │   ├── asn / loopback         # BGP ASN とループバックアドレス
│   │   ├── zone                   # 図示用ゾーン名
│   │   └── interface[]            # 物理インタフェースリスト
│   │       ├── (interface-identification grouping)
│   │       ├── description / port-type / hardware-mac-address
│   │       ├── port-speed-gbps
│   │       └── ip-address         # CIDR 形式 (IPv4/IPv6)
│   └── physical-connection[]      # 物理ケーブル接続
│       ├── connection-id
│       ├── endpoint[]             # 両端デバイス・IF (min/max 2)
│       │   └── lag-ref            # LAG メンバー明示参照 (optional)
│       ├── cable-type
│       └── length-meters
│
├── layer2-layer
│   ├── vlan[]                     # VLAN 定義
│   ├── layer2-interface-config[]  # access/trunk/layer3 モード設定
│   └── link-aggregation[]         # LAG/LACP/MLAG 設定
│       ├── mode (static/lacp-active/lacp-passive)
│       ├── lacp-rate / min-links
│       ├── member-interface[]
│       └── mlag                   # MLAG/vPC ピア設定
│
├── layer3-layer
│   ├── ip-subnet[]                # サブネット定義
│   ├── layer3-interface-config[]  # インタフェースの IP アドレス設定
│   │   └── addresses[]            # 複数アドレス対応 (IPv4/IPv6)
│   ├── host-config[]              # ホスト向け L3 設定
│   ├── static-route[]             # スタティックルート
│   ├── first-hop-redundancy[]     # VRRP/HSRP/GLBP グループ設定
│   ├── route-policy[]             # 再利用可能ルートポリシー
│   └── routing-config[]           # デバイス単位のダイナミックルーティング
│       ├── bgp                    # ピアグループ / ネイバー / address-family
│       ├── ospf                   # プロセス / エリア
│       └── redistribution[]       # プロトコル間再配送
│
└── management
    ├── management-vlan            # 管理 VLAN (global)
    └── device-management[]        # デバイス単位の管理設定
        ├── management-ip-address / management-gateway
        ├── in-band-interface-id
        ├── management-loopback
        └── out-of-band            # OOB 管理設定
```

---

## pyang ツリー出力

`python3 -m pyang -f tree yang/iida-network-model.yang` の実行結果。

記法の凡例:
- `+--rw` — 読み書き可能な設定ノード (`config true`)
- `*` — リスト (`list`)
- `?` — 省略可能 (`not mandatory`)
- `!` — presence コンテナ（明示的に生成しない限り存在しない。BGP/OSPF が該当）
- `->` — leafref（他ノードへの参照）

```
module: iida-network-model
  +--rw network-model
     +--rw physical-layer
     |  +--rw device* [device-id]
     |  |  +--rw device-id           string
     |  |  +--rw device-name?        string
     |  |  +--rw device-type?        enumeration
     |  |  +--rw vendor?             string
     |  |  +--rw model?              string
     |  |  +--rw os-version?         string
     |  |  +--rw serial-number?      string
     |  |  +--rw role?               enumeration
     |  |  +--rw location
     |  |  |  +--rw site-id?     string
     |  |  |  +--rw building?    string
     |  |  |  +--rw room?        string
     |  |  |  +--rw rack-id?     string
     |  |  |  +--rw rack-unit?   uint8
     |  |  +--rw hw-revision?        string
     |  |  +--rw install-date?       yang:date-and-time
     |  |  +--rw end-of-life-date?   yang:date-and-time
     |  |  +--rw asn?                uint32
     |  |  +--rw loopback?           inet:ip-prefix
     |  |  +--rw zone?               string
     |  |  +--rw interface* [interface-id]
     |  |     +--rw interface-id            string
     |  |     +--rw description?            string
     |  |     +--rw port-type?              enumeration
     |  |     +--rw hardware-mac-address?   yang:mac-address
     |  |     +--rw port-speed-gbps?        uint16
     |  |     +--rw ip-address?             inet:ip-prefix
     |  +--rw physical-connection* [connection-id]
     |     +--rw connection-id    string
     |     +--rw endpoint* [device-id]
     |     |  +--rw device-id       -> ../../../device/device-id
     |     |  +--rw interface-id?   -> ../../../device[device-id=current()/../device-id]/interface/interface-id
     |     |  +--rw lag-ref?        -> ../../../../layer2-layer/link-aggregation[device-id=current()/../device-id]/lag-id
     |     +--rw cable-type?      string
     |     +--rw length-meters?   decimal64
     +--rw layer2-layer
     |  +--rw vlan* [vlan-id]
     |  |  +--rw vlan-id    uint16
     |  |  +--rw name?      string
     |  +--rw layer2-interface-config* [device-id interface-id]
     |  |  +--rw device-id       -> ../../../physical-layer/device/device-id
     |  |  +--rw interface-id    -> ../../../physical-layer/device[device-id=current()/../device-id]/interface/interface-id
     |  |  +--rw l2-mode?        enumeration
     |  |  +--rw access-vlan     -> ../../vlan/vlan-id
     |  |  +--rw trunk-vlans* [vlan-id]
     |  |  |  +--rw vlan-id    -> ../../../vlan/vlan-id
     |  |  +--rw native-vlan?    -> ../../vlan/vlan-id
     |  +--rw link-aggregation* [device-id lag-id]
     |     +--rw device-id           -> ../../../physical-layer/device/device-id
     |     +--rw lag-id              string
     |     +--rw mode?               enumeration
     |     +--rw lacp-rate?          enumeration
     |     +--rw min-links?          uint16
     |     +--rw member-interface* [interface-id]
     |     |  +--rw interface-id    -> ../../../../physical-layer/device[device-id=current()/../../device-id]/interface/interface-id
     |     +--rw mlag
     |        +--rw enabled?          boolean
     |        +--rw domain-id?        string
     |        +--rw peer-device-id?   -> ../../../../physical-layer/device/device-id
     |        +--rw peer-lag-id?      string
     +--rw layer3-layer
     |  +--rw ip-subnet* [subnet-id]
     |  |  +--rw subnet-id             string
     |  |  +--rw prefix?               inet:ip-prefix
     |  |  +--rw description?          string
     |  |  +--rw associated-vlan-id?   -> ../../../layer2-layer/vlan/vlan-id
     |  +--rw layer3-interface-config* [device-id interface-id]
     |  |  +--rw device-id             -> ../../../physical-layer/device/device-id
     |  |  +--rw interface-id          -> ../../../physical-layer/device[device-id=current()/../device-id]/interface/interface-id
     |  |  +--rw addresses* [ip-address]
     |  |  |  +--rw ip-address              inet:ip-address
     |  |  |  +--rw prefix-length?          uint8
     |  |  |  +--rw associated-subnet-id?   -> ../../../ip-subnet/subnet-id
     |  |  +--rw ip-routing-enabled?   boolean
     |  |  +--rw routing
     |  |     +--rw ospf-area?   string
     |  +--rw host-config* [device-id]
     |  |  +--rw device-id                -> ../../../physical-layer/device/device-id
     |  |  +--rw primary-ip-address?      inet:ip-address
     |  |  +--rw primary-prefix-length?   uint8
     |  |  +--rw default-gateway?         inet:ip-address
     |  |  +--rw associated-subnet-id?    -> ../../ip-subnet/subnet-id
     |  +--rw static-route* [destination-prefix next-hop]
     |  |  +--rw device-id?                 -> ../../../physical-layer/device/device-id
     |  |  +--rw destination-prefix         inet:ip-prefix
     |  |  +--rw next-hop                   inet:ip-address
     |  |  +--rw administrative-distance?   uint8
     |  +--rw first-hop-redundancy* [device-id interface-id group-id]
     |  |  +--rw device-id             -> ../../../physical-layer/device/device-id
     |  |  +--rw interface-id          -> ../../../physical-layer/device[device-id=current()/../device-id]/interface/interface-id
     |  |  +--rw group-id              uint16
     |  |  +--rw protocol?             enumeration
     |  |  +--rw virtual-ip-address?   inet:ip-address
     |  |  +--rw priority?             uint8
     |  |  +--rw preempt?              boolean
     |  +--rw route-policy* [name]
     |  |  +--rw name           string
     |  |  +--rw description?   string
     |  |  +--rw statement* [sequence]
     |  |     +--rw sequence                uint32
     |  |     +--rw action?                 enumeration
     |  |     +--rw match-prefix?           inet:ip-prefix
     |  |     +--rw set-local-preference?   uint32
     |  |     +--rw set-metric?             uint32
     |  +--rw routing-config* [device-id]
     |     +--rw device-id         -> ../../../physical-layer/device/device-id
     |     +--rw router-id?        inet:ip-address
     |     +--rw bgp!
     |     |  +--rw local-asn     uint32
     |     |  +--rw peer-group* [name]
     |     |  |  +--rw name             string
     |     |  |  +--rw peer-asn?        uint32
     |     |  |  +--rw import-policy?   -> ../../../../route-policy/name
     |     |  |  +--rw export-policy?   -> ../../../../route-policy/name
     |     |  +--rw neighbor* [neighbor-address]
     |     |     +--rw neighbor-address    inet:ip-address
     |     |     +--rw peer-asn?           uint32
     |     |     +--rw peer-group?         -> ../../peer-group/name
     |     |     +--rw description?        string
     |     |     +--rw address-family*     enumeration
     |     |     +--rw import-policy?      -> ../../../../route-policy/name
     |     |     +--rw export-policy?      -> ../../../../route-policy/name
     |     +--rw ospf!
     |     |  +--rw process-id?   string
     |     |  +--rw area* [area-id]
     |     |     +--rw area-id      string
     |     |     +--rw area-type?   enumeration
     |     +--rw redistribution* [source-protocol destination-protocol]
     |        +--rw source-protocol         enumeration
     |        +--rw destination-protocol    enumeration
     |        +--rw route-policy?           -> ../../../route-policy/name
     |        +--rw metric?                 uint32
     +--rw management
        +--rw management-vlan?     -> ../../layer2-layer/vlan/vlan-id
        +--rw device-management* [device-id]
           +--rw device-id                   -> ../../../physical-layer/device/device-id
           +--rw management-ip-address?      inet:ip-address
           +--rw management-prefix-length?   uint8
           +--rw management-gateway?         inet:ip-address
           +--rw in-band-interface-id?       -> ../../../physical-layer/device[device-id=current()/../device-id]/interface/interface-id
           +--rw management-loopback?        inet:ip-prefix
           +--rw out-of-band
              +--rw enabled?         boolean
              +--rw interface-id?    -> ../../../../physical-layer/device[device-id=current()/../../device-id]/interface/interface-id
              +--rw ip-address?      inet:ip-address
              +--rw prefix-length?   uint8
```

<br><br>

---

## 主要コンテナ・リストの説明

### `physical-layer/device`

ネットワーク機器の完全な属性を保持します。`device-type` は機能分類 (router/switch/server/firewall/host/load-balancer)、`role` はファブリック内の役割 (spine/leaf/core/distribution/access/border/oob) を表し、両者は直交します。`location` は文字列ではなく `site-id/building/room/rack-id/rack-unit` に構造化されており、資産管理・保守に直接利用できます。

### `layer2-layer/link-aggregation`

LAG (Link Aggregation Group) を表します。`mode` で static/LACP を選択し、`mlag` サブコンテナで MLAG/vPC の対向デバイス・ドメインを記述します。`physical-connection/endpoint/lag-ref` から LAG ID を参照することで、どの物理接続が LAG メンバーかを明示できます。

### `layer3-layer/first-hop-redundancy`

デバイス・インタフェース・グループ ID の 3 キーで VRRP/HSRP/GLBP グループを定義します。`virtual-ip-address` がホストのデフォルトゲートウェイとして機能します。

### `layer3-layer/routing-config`

デバイス単位の BGP・OSPF 設定と再配送ポリシーを統合します。BGP では `peer-group` を定義して `neighbor` から参照することでテンプレート化でき、`import-policy`/`export-policy` は `route-policy` リストへの leafref です。

### `management`

管理プレーンを物理・L2・L3 から独立したコンテナとして分離します。グローバル管理 VLAN とデバイス単位の設定 (in-band IP、OOB インタフェース、管理ループバック) を保持します。

---

## リビジョン履歴

| リビジョン   | 主な変更内容                                                                                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-07-17   | IPv6 対応 (`inet:ip-address`/`ip-prefix` へ一般化)、LAG/MLAG 追加、VRRP/HSRP/GLBP 追加、BGP/OSPF/再配送追加、`management` コンテナ追加、`port-type` ラベル修正 |
| 2026-07-01   | `device` に `asn`/`loopback` 追加、`interface` に `ip-address` 追加、`physical-connection` エンドポイントをリスト化 (IP CLOS 対応)                             |
| 2023-11-20   | 初版 (OpenConfig 的構造を参考に作成)                                                                                                                          |

---

## モジュール分割計画 (Modularization Plan)

### 背景

現状の `iida-network-model.yang`（892行）は単一ファイルに全定義が集約されており、
ACL・QoS・NAT など新機能を追加するたびに可読性とメンテナンス性が低下する。
YANG 1.1 の **サブモジュール** と **独立モジュール＋augment** を組み合わせ、
必要な機能だけを選択的に取り込める構造へ移行する。

---

### 設計方針

YANG の分割メカニズムを用途別に使い分ける。

| メカニズム | キーワード | 名前空間 | 適用基準 |
|---|---|---|---|
| **サブモジュール** | `belongs-to` / `include` | 共有（親と同一） | コアトポロジーの一部。leafref が他層と相互依存する定義 |
| **独立モジュール** | `import` / `augment` | 独立 | オプション機能。プロジェクトによって使ったり使わなかったりするポリシー・サービス定義 |

---

### ファイル構成（移行後）

```
yang/
├── iida-network-model.yang              # メインモジュール（include 宣言と top-level container のみ）
│
├── # ── サブモジュール（belongs-to iida-network-model）────────────────
├── iida-network-model-physical.yang     # 物理層: device, interface, physical-connection
├── iida-network-model-l2.yang           # L2: vlan, link-aggregation, layer2-interface-config
├── iida-network-model-l3.yang           # L3: ip-subnet, layer3-interface-config, routing-config,
│                                        #     host-config, static-route, first-hop-redundancy, route-policy
├── iida-network-model-mgmt.yang         # 管理プレーン: management-vlan, device-management, OOB
│
└── # ── 独立モジュール（augment でコアモデルへ拡張）─────────────────
    iida-network-acl.yang                # ACL: アクセスリスト・通信フィルタ（任意）
    iida-network-qos.yang                # QoS: 帯域制御・ポリシーマップ（任意）
    iida-network-nat.yang                # NAT: アドレス変換・PAT（任意）
```

---

### 各ファイルの責務と augment ターゲット

#### サブモジュール

| ファイル | 主な定義 | leafref の参照先 |
|---|---|---|
| `iida-network-model-physical.yang` | `grouping device-identification`, `grouping interface-identification`, `physical-layer` container | — |
| `iida-network-model-l2.yang` | `layer2-layer` container (vlan, link-aggregation, layer2-interface-config) | `physical-layer/device` |
| `iida-network-model-l3.yang` | `layer3-layer` container (ip-subnet, routing-config, route-policy, FHR, static-route) | `physical-layer/device`, `layer2-layer/vlan` |
| `iida-network-model-mgmt.yang` | `management` container | `physical-layer/device`, `layer2-layer/vlan` |

#### 独立モジュール

| ファイル | augment ターゲット | 主な定義 |
|---|---|---|
| `iida-network-acl.yang` | `.../layer3-interface-config` | ACL 名・ACE（permit/deny、src/dst prefix、プロトコル）、ingress/egress 適用 |
| `iida-network-qos.yang` | `.../layer3-interface-config`, `.../device` | ポリシーマップ、クラスマップ、シェーピング/ポリシング、マーキング |
| `iida-network-nat.yang` | `.../device`, `.../layer3-interface-config` | NAT プール、スタティック NAT、ダイナミック NAT、PAT、inside/outside 指定 |

---

### 実装ステップ

#### Phase 1 — 既存モデルのサブモジュール分割（リファクタリング）

1. `iida-network-model-physical.yang` を作成し、`grouping` 定義と `physical-layer` container を移動
2. `iida-network-model-l2.yang` を作成し、`layer2-layer` container を移動
3. `iida-network-model-l3.yang` を作成し、`layer3-layer` container を移動
4. `iida-network-model-mgmt.yang` を作成し、`management` container を移動
5. メインモジュールを `include` 宣言と空の `network-model` container のみに精簡
6. `pyang --strict` で全サブモジュールを一括検証しエラーがないことを確認

> **注意**: サブモジュール間の leafref はサブモジュールが import なしに親モジュールのスコープを共有するため、
> パスの書き方は変わらない。

#### Phase 2 — 独立拡張モジュールの新規作成

1. `iida-network-acl.yang` を作成（ACL 定義 + `augment` でインタフェースに適用ポイントを追加）
2. `iida-network-qos.yang` を作成（QoS ポリシー + `augment`）
3. `iida-network-nat.yang` を作成（NAT プール/エントリ + `augment`）
4. 各モジュールを単体・組み合わせで `pyang` 検証

---

### 検証コマンド（移行後）

```bash
# -p yang でサブモジュールの検索パスを指定する（必須）

# コアのみ
python3 -m pyang --strict -p yang yang/iida-network-model.yang

# ACL 拡張込み
python3 -m pyang --strict -p yang yang/iida-network-model.yang yang/iida-network-acl.yang

# 全モジュール一括
python3 -m pyang --strict -p yang yang/iida-network-model.yang \
    yang/iida-network-acl.yang \
    yang/iida-network-qos.yang \
    yang/iida-network-nat.yang

# ツリー表示（全モジュール展開）
python3 -m pyang -f tree -p yang yang/iida-network-model.yang \
    yang/iida-network-acl.yang \
    yang/iida-network-qos.yang \
    yang/iida-network-nat.yang
```

---

### 進捗管理

凡例: `[ ]` TODO / `[~]` IN-PROGRESS / `[x]` DONE

#### Phase 1 — サブモジュール分割

| # | タスク | 状態 |
|---|---|---|
| 1-1 | `iida-network-model-physical.yang` 作成（grouping + physical-layer） | [x] |
| 1-2 | `iida-network-model-l2.yang` 作成（layer2-layer） | [x] |
| 1-3 | `iida-network-model-l3.yang` 作成（layer3-layer） | [x] |
| 1-4 | `iida-network-model-mgmt.yang` 作成（management） | [x] |
| 1-5 | メインモジュールを include 宣言のみに精簡 | [x] |
| 1-6 | `pyang --strict` で全サブモジュール検証 | [x] |

#### Phase 2 — 独立拡張モジュール

| # | タスク | 状態 |
|---|---|---|
| 2-1 | `iida-network-acl.yang` 作成（ACL + augment） | [x] |
| 2-2 | `iida-network-qos.yang` 作成（QoS + augment） | [x] |
| 2-3 | `iida-network-nat.yang` 作成（NAT + augment） | [x] |
| 2-4 | 各拡張モジュールの pyang 検証 | [x] |

---

## 改善計画 (Improvement Plan) — 進捗管理

企業向けネットワークの「構成定義」と「運用管理」を充足させるための優先度★★★の追加項目。着手時にステータスを更新すること。

凡例: `[ ]` TODO / `[~]` IN-PROGRESS / `[x]` DONE

### 構成定義 (Design)

| # | 項目 | 状態 | 説明 |
|---|------|------|------|
| 1 | IPv6 対応 | [x] | `inet:ipv4-*` を `inet:ip-address`/`inet:ip-prefix` へ一般化し、`prefix-length` を 0..128 へ拡張。デュアルスタックを表現可能にした。 |
| 2 | 冗長化 (Redundancy) | [x] | ゲートウェイ冗長 (VRRP/HSRP/GLBP) を `layer3-layer/first-hop-redundancy`、リンク集約 (LAG/LACP, MLAG) を `layer2-layer/link-aggregation` として追加した。 |
| 3 | ダイナミックルーティング (Dynamic Routing) | [x] | `layer3-layer` に `routing-config` (BGP ネイバー/ピアグループ/アドレスファミリ、OSPF エリア、再配送) と再利用可能な `route-policy` を追加した。 |

### 運用管理 (Operations)

| # | 項目 | 状態 | 説明 |
|---|------|------|------|
| 4 | 管理系設定 (Management) | [x] | `network-model/management` を新設。管理 VLAN、デバイス別の管理 IP/ゲートウェイ、In-band 管理 IF、管理用ループバック、Out-of-Band 管理を追加した。 |
| 5 | 監視・テレメトリ (Monitoring / Telemetry) | [ ] | SNMP (community/v3)、Syslog 宛先、NetFlow/sFlow、ストリーミングテレメトリ。 |
| 6 | AAA / アクセス管理 | [ ] | TACACS+/RADIUS サーバ、ローカルユーザ、権限レベル。 |
| 7 | 運用状態 (Operational State / state data) | [ ] | `admin-status`/`oper-status` など `config false` データ。現状は明示的に割愛しているため運用管理向けに追加する。 |

### フィジカルレイヤー強化 (Physical Layer Enhancements)

| # | 項目 | 状態 | 対象 | 説明 |
|---|------|------|------|------|
| 8  | `serial-number` | [x] | device | 資産管理・保守対応の基本情報。 |
| 9  | `role` | [x] | device | spine/leaf/core/distribution/access/border/oob など運用上の役割。`device-type` より細粒度のファブリック役割を表現する。 |
| 10 | `location` 構造化 | [x] | device | `site-id`/`building`/`room`/`rack-id`/`rack-unit` に分割。 |
| 11 | `install-date` / `end-of-life-date` | [x] | device | ライフサイクル管理用。 |
| 12 | `hw-revision` | [x] | device | 同モデル内のハードウェアリビジョン差異を記録する。 |
| 13 | `connector-type` | [ ] | interface | RJ45/SFP/SFP+/QSFP28/QSFP-DD などコネクタ形状。`port-type` と直交する。 |
| 14 | `mtu` | [ ] | interface | ジャンボフレーム等の MTU 設定値。 |
| 15 | `transceiver` コンテナ | [ ] | interface | vendor/part-number/wavelength-nm など光トランシーバ情報。 |
| 16 | `auto-negotiate` / `duplex` | [ ] | interface | 速度・デュプレックス交渉設定。 |
| 17 | `fec-mode` | [ ] | interface | 25G 以上の高速ポート向け Forward Error Correction モード。 |
| 18 | `breakout-mode` | [ ] | interface | QSFP28 の 4x25G 等ブレイクアウト構成。 |
| 19 | `circuit-id` | [ ] | physical-connection | WAN リンクの通信事業者回線番号。 |
| 20 | `connection-type` | [ ] | physical-connection | direct/patch-panel/cross-connect/wan など接続経路種別。 |
| 21 | `tx-power-dbm` / `rx-power-dbm` | [ ] | physical-connection | 光リンクの設計値・疎通確認用の光学特性。 |
