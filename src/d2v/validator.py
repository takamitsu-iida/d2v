"""iida-network-model のセマンティック検証（design lint）。

``parser`` が担うスキーマ検証（必須フィールド・型）を超えて、トポロジ
**設計そのものの論理的妥当性**をルールベースで機械検証する。

方針:
- 検出は決定論的（純 Python・LLM 非依存）。API キー無しでも動作する。
- 本モジュールは **検出のみ** を担う。理由・修正案の付与（``--explain``）は
  Phase 4 で LLM により行う（issue 集合は不変のまま説明を充填する）。

Phase 0: データモデル・``validate()`` 骨格・ルール登録機構・rich 整形ヘルパ。
Phase 1: 構造整合性・一意性ルール（壊れた参照・重複の検出）。
Phase 2: L3 整合性・到達性・冗長性（孤立/SPOF/橋の内製グラフ解析）。
Phase 3: ポリシー制約（宣言的な zone 間通信・冗長ポリシーの検証）。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from d2v.errors import InputError
from d2v.parser import TopologyModel, build_text

logger = logging.getLogger(__name__)

_YamlDict = dict[str, Any]

# 重大度は固定 3 段階。カウント・終了コード・整形の基準に使う。
SEVERITIES: tuple[str, ...] = ("error", "warning", "info")

_SEVERITY_STYLE: dict[str, str] = {
    "error": "bold red",
    "warning": "yellow",
    "info": "cyan",
}


# ---------------------------------------------------------------------------
# データモデル
# ---------------------------------------------------------------------------


class ValidationIssue(BaseModel):
    """検出した設計上の問題 1 件。"""

    rule: str                              # ルール ID（例: "dangling-endpoint"）
    severity: str                          # "error" | "warning" | "info"
    message: str                           # 機械生成の簡潔な説明
    targets: list[str] = Field(default_factory=list)  # 関係する device-id / connection-id / subnet-id
    explanation: str = ""                  # LLM が付与（--explain 時のみ）
    suggestion: str = ""                   # LLM が付与（--explain 時のみ）


class ValidationReport(BaseModel):
    """検証結果のまとめ。"""

    ok: bool                        # error が 0 件か
    counts: dict[str, int]          # {"error": n, "warning": m, "info": k}
    issues: list[ValidationIssue]

    @classmethod
    def from_issues(cls, issues: list[ValidationIssue]) -> "ValidationReport":
        """issue 群から件数集計と ``ok`` 判定を行いレポートを構築する。"""
        counts: dict[str, int] = {sev: 0 for sev in SEVERITIES}
        for issue in issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        ok = counts.get("error", 0) == 0
        return cls(ok=ok, counts=counts, issues=list(issues))

    def passed(self, *, strict: bool = False) -> bool:
        """検証合格判定。error があれば不合格。``strict`` では warning も不合格扱い。"""
        if self.counts.get("error", 0) > 0:
            return False
        if strict and self.counts.get("warning", 0) > 0:
            return False
        return True


# ---------------------------------------------------------------------------
# ポリシーモデル（Phase 3）
# ---------------------------------------------------------------------------


class NodeSelector(BaseModel):
    """ポリシーが対象とするデバイスの選択条件。

    ``zone`` / ``type``（device-type）を指定するとそれぞれ AND で一致判定する。
    ``any`` は文字列ショートハンドで、zone または device-type のいずれかと一致すれば良い。
    """

    zone: str | None = None
    type: str | None = None
    any: str | None = None

    def matches(self, dev: _YamlDict) -> bool:
        if self.any is not None:
            return dev.get("zone") == self.any or dev.get("device-type") == self.any
        if self.zone is None and self.type is None:
            return False
        if self.zone is not None and dev.get("zone") != self.zone:
            return False
        if self.type is not None and dev.get("device-type") != self.type:
            return False
        return True


class ZoneTransitPolicy(BaseModel):
    """``src`` から ``dst`` への通信は必ず ``via`` を経由すること。"""

    name: str = ""
    src: NodeSelector
    dst: NodeSelector
    via: NodeSelector
    severity: str = "error"


class ZoneRedundancyPolicy(BaseModel):
    """``selector`` に一致するデバイスは冗長（単一障害点でない）であること。"""

    name: str = ""
    selector: NodeSelector
    severity: str = "warning"


class PolicySet(BaseModel):
    """ポリシーファイル 1 つ分の宣言。"""

    zone_transit: list[ZoneTransitPolicy] = Field(default_factory=list)
    zone_redundancy: list[ZoneRedundancyPolicy] = Field(default_factory=list)


def _coerce_selector(value: object) -> NodeSelector:
    """ポリシー中のセレクタ（文字列 or {zone/type/any} 辞書）を NodeSelector に変換する。"""
    if isinstance(value, str):
        return NodeSelector(any=value)
    if isinstance(value, dict):
        return NodeSelector(
            zone=value.get("zone"),
            type=value.get("type"),
            any=value.get("any"),
        )
    raise InputError(f"ポリシーのセレクタが不正です: {value!r}")


def load_policies(path: Path) -> PolicySet:
    """ポリシー YAML を読み込み ``PolicySet`` を返す。

    フォーマット（いずれのキーも任意）::

        zone-transit:
          - name: dmz-to-office-via-firewall
            from: dmz          # 文字列は zone/device-type のどちらかに一致
            to: office
            via: firewall
        zone-redundancy:
          - zone: core         # zone: / type: でセレクタを指定
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise InputError(f"ポリシーファイルが見つかりません: {path}") from e
    except yaml.YAMLError as e:
        raise InputError(f"ポリシー YAML の解析に失敗しました: {e}") from e
    if raw is None:
        return PolicySet()
    if not isinstance(raw, dict):
        raise InputError("ポリシーファイルはマッピング（辞書）である必要があります。")

    transit: list[ZoneTransitPolicy] = []
    for entry in raw.get("zone-transit", []) or []:
        try:
            transit.append(ZoneTransitPolicy(
                name=entry.get("name", ""),
                src=_coerce_selector(entry["from"]),
                dst=_coerce_selector(entry["to"]),
                via=_coerce_selector(entry["via"]),
                severity=entry.get("severity", "error"),
            ))
        except KeyError as e:
            raise InputError(f"zone-transit ポリシーに必須キー {e} がありません。") from e

    redundancy: list[ZoneRedundancyPolicy] = []
    for entry in raw.get("zone-redundancy", []) or []:
        sel = NodeSelector(
            zone=entry.get("zone"),
            type=entry.get("type"),
            any=entry.get("any"),
        )
        redundancy.append(ZoneRedundancyPolicy(
            name=entry.get("name", ""),
            selector=sel,
            severity=entry.get("severity", "warning"),
        ))

    return PolicySet(zone_transit=transit, zone_redundancy=redundancy)



# ---------------------------------------------------------------------------
# ルール登録機構
# ---------------------------------------------------------------------------

RuleFunc = Callable[[TopologyModel], list[ValidationIssue]]

# Phase 1 以降で ``@rule`` により検証関数が登録される。Phase 0 では空。
_RULES: list[RuleFunc] = []


def rule(func: RuleFunc) -> RuleFunc:
    """検証ルールを登録するデコレータ。

    ``TopologyModel`` を受け取り ``ValidationIssue`` のリストを返す関数を
    ``_RULES`` に追加する。``validate()`` は登録順に全ルールを実行する。
    """
    _RULES.append(func)
    return func


# ---------------------------------------------------------------------------
# 検証本体
# ---------------------------------------------------------------------------


def validate(
    model: TopologyModel,
    *,
    policies: PolicySet | None = None,
) -> ValidationReport:
    """トポロジモデルを検証し ``ValidationReport`` を返す。

    ``policies`` を渡すと、登録済みルール（構造/一意性/L3/冗長）に加えて
    宣言的なゾーンポリシー（Phase 3）も検証する。
    """
    issues: list[ValidationIssue] = []
    for rule_func in _RULES:
        issues.extend(rule_func(model))
    if policies is not None:
        issues.extend(_run_policies(model, policies))
    return ValidationReport.from_issues(issues)


# ---------------------------------------------------------------------------
# ルール用ヘルパ
# ---------------------------------------------------------------------------


def _conn_target(conn: _YamlDict) -> str:
    """接続を指す識別子を返す（connection-id 優先、無ければ端点から合成）。"""
    cid = conn.get("connection-id")
    if cid:
        return str(cid)
    parts: list[str] = []
    for ep in conn.get("endpoint", []) or []:
        did = ep.get("device-id", "?")
        iid = ep.get("interface-id", "")
        parts.append(f"{did}[{iid}]" if iid else str(did))
    return " <-> ".join(parts) if parts else "(unknown-connection)"


def _iface_ids(dev: _YamlDict) -> set[str]:
    """デバイスが持つ interface-id の集合を返す。"""
    return {
        i.get("interface-id")
        for i in dev.get("interface", []) or []
        if i.get("interface-id")
    }


def _edge_key(conn: _YamlDict) -> frozenset[tuple[str | None, str | None]] | None:
    """無向・ポート込みの接続キーを返す（端点が 2 個でなければ None）。"""
    eps = conn.get("endpoint", []) or []
    if len(eps) != 2:
        return None
    a = (eps[0].get("device-id"), eps[0].get("interface-id"))
    b = (eps[1].get("device-id"), eps[1].get("interface-id"))
    return frozenset((a, b))


def _norm_ip(addr: object) -> str | None:
    """IP アドレス文字列をホスト部に正規化する（解析不能なら None）。"""
    if not isinstance(addr, str):
        return None
    try:
        return str(ipaddress.ip_interface(addr.strip()).ip)
    except ValueError:
        return None


def _iface_ip(dev: _YamlDict, interface_id: object) -> str | None:
    """デバイスの指定インターフェースの ip-address を返す（無ければ None）。"""
    for iface in dev.get("interface", []) or []:
        if iface.get("interface-id") == interface_id:
            return iface.get("ip-address")
    return None


def _build_graph(model: TopologyModel) -> dict[str, set[str]]:
    """physical-connection から無向グラフ（隣接リスト）を構築する。

    ノードは全 device-id（孤立ノードも含む）。自己ループ・未定義デバイス参照・
    端点数≠2 の接続は無視する（それぞれ別ルールが検出する）。
    """
    adj: dict[str, set[str]] = {
        d["device-id"]: set() for d in model.devices if d.get("device-id")
    }
    for conn in model.connections:
        eps = conn.get("endpoint", []) or []
        if len(eps) != 2:
            continue
        a, b = eps[0].get("device-id"), eps[1].get("device-id")
        if not a or not b or a == b or a not in adj or b not in adj:
            continue
        adj[a].add(b)
        adj[b].add(a)
    return adj


def _pair_multiplicity(model: TopologyModel) -> dict[frozenset[str], int]:
    """デバイス対ごとの物理接続本数を返す（並行リンク＝LAG の判定に使う）。"""
    counts: dict[frozenset[str], int] = {}
    for conn in model.connections:
        eps = conn.get("endpoint", []) or []
        if len(eps) != 2:
            continue
        a, b = eps[0].get("device-id"), eps[1].get("device-id")
        if not a or not b or a == b:
            continue
        key = frozenset((a, b))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _articulation_and_bridges(
    adj: dict[str, set[str]],
) -> tuple[set[str], list[frozenset[str]]]:
    """無向グラフの関節点（articulation point）と橋（bridge）を返す（Tarjan/DFS）。"""
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    timer = [0]
    aps: set[str] = set()
    bridges: list[frozenset[str]] = []

    def dfs(u: str, parent: str | None) -> None:
        disc[u] = low[u] = timer[0]
        timer[0] += 1
        children = 0
        for v in adj[u]:
            if v == parent:
                continue
            if v not in disc:
                children += 1
                dfs(v, u)
                low[u] = min(low[u], low[v])
                if parent is not None and low[v] >= disc[u]:
                    aps.add(u)
                if low[v] > disc[u]:
                    bridges.append(frozenset((u, v)))
            else:
                low[u] = min(low[u], disc[v])
        if parent is None and children > 1:
            aps.add(u)

    for node in adj:
        if node not in disc:
            dfs(node, None)
    return aps, bridges



# ---------------------------------------------------------------------------
# 検証ルール（Phase 1: 構造整合性・一意性）
# ---------------------------------------------------------------------------


@rule
def _check_dangling_endpoint(model: TopologyModel) -> list[ValidationIssue]:
    """physical-connection の端点が 2 個でない／device-id 欠落の接続を検出する。"""
    issues: list[ValidationIssue] = []
    for conn in model.connections:
        eps = conn.get("endpoint", []) or []
        target = _conn_target(conn)
        if len(eps) != 2:
            issues.append(ValidationIssue(
                rule="dangling-endpoint",
                severity="error",
                message=f"接続の endpoint が 2 個ではありません（{len(eps)} 個）。",
                targets=[target],
            ))
            continue
        if any(not ep.get("device-id") for ep in eps):
            issues.append(ValidationIssue(
                rule="dangling-endpoint",
                severity="error",
                message="endpoint に device-id が指定されていません。",
                targets=[target],
            ))
    return issues


@rule
def _check_unknown_device_ref(model: TopologyModel) -> list[ValidationIssue]:
    """存在しない device-id を参照する endpoint を検出する。"""
    issues: list[ValidationIssue] = []
    for conn in model.connections:
        target = _conn_target(conn)
        for ep in conn.get("endpoint", []) or []:
            did = ep.get("device-id")
            if did and did not in model.device_map:
                issues.append(ValidationIssue(
                    rule="unknown-device-ref",
                    severity="error",
                    message=f"存在しない device-id '{did}' を参照しています。",
                    targets=[target, did],
                ))
    return issues


@rule
def _check_unknown_interface_ref(model: TopologyModel) -> list[ValidationIssue]:
    """device に存在しない interface-id を参照する endpoint を検出する。"""
    issues: list[ValidationIssue] = []
    for conn in model.connections:
        target = _conn_target(conn)
        for ep in conn.get("endpoint", []) or []:
            did = ep.get("device-id")
            iid = ep.get("interface-id")
            dev = model.device_map.get(did) if did else None
            # device 参照エラー・interface-id 未指定は他ルール／対象外
            if dev is None or not iid:
                continue
            if iid not in _iface_ids(dev):
                issues.append(ValidationIssue(
                    rule="unknown-interface-ref",
                    severity="error",
                    message=f"device '{did}' に存在しない interface-id '{iid}' を参照しています。",
                    targets=[target],
                ))
    return issues


@rule
def _check_self_loop(model: TopologyModel) -> list[ValidationIssue]:
    """両端が同一デバイスの自己ループを検出する。"""
    issues: list[ValidationIssue] = []
    for conn in model.connections:
        eps = conn.get("endpoint", []) or []
        if len(eps) != 2:
            continue
        d0, d1 = eps[0].get("device-id"), eps[1].get("device-id")
        if d0 and d0 == d1:
            issues.append(ValidationIssue(
                rule="self-loop",
                severity="error",
                message=f"両端が同一デバイス '{d0}' の自己ループです。",
                targets=[_conn_target(conn)],
            ))
    return issues


@rule
def _check_duplicate_device_id(model: TopologyModel) -> list[ValidationIssue]:
    """device-id の重複を検出する。"""
    counts: dict[str, int] = {}
    for dev in model.devices:
        did = dev.get("device-id")
        if did:
            counts[did] = counts.get(did, 0) + 1
    return [
        ValidationIssue(
            rule="duplicate-device-id",
            severity="error",
            message=f"device-id '{did}' が {cnt} 回定義されています。",
            targets=[did],
        )
        for did, cnt in counts.items()
        if cnt > 1
    ]


@rule
def _check_duplicate_connection(model: TopologyModel) -> list[ValidationIssue]:
    """同一ノード・ポート対を結ぶ重複リンク（無向・ポート込み）を検出する。"""
    groups: dict[frozenset[tuple[str | None, str | None]], list[str]] = {}
    for conn in model.connections:
        key = _edge_key(conn)
        if key is None:
            continue
        groups.setdefault(key, []).append(_conn_target(conn))
    return [
        ValidationIssue(
            rule="duplicate-connection",
            severity="warning",
            message=f"同一のノード・ポート対を結ぶ接続が {len(labels)} 本重複しています。",
            targets=labels,
        )
        for labels in groups.values()
        if len(labels) > 1
    ]


@rule
def _check_duplicate_loopback(model: TopologyModel) -> list[ValidationIssue]:
    """複数デバイスで同一 loopback を検出する（IP を正規化して比較）。"""
    groups: dict[str, list[str]] = {}
    for dev in model.devices:
        lb = dev.get("loopback")
        if not lb:
            continue
        key = _norm_ip(lb) or str(lb)
        groups.setdefault(key, []).append(dev.get("device-id", ""))
    return [
        ValidationIssue(
            rule="duplicate-loopback",
            severity="error",
            message=f"loopback {key} が複数デバイスで重複しています。",
            targets=devs,
        )
        for key, devs in groups.items()
        if len(devs) > 1
    ]


@rule
def _check_duplicate_asn(model: TopologyModel) -> list[ValidationIssue]:
    """同一 ASN を複数デバイスが共有している状態を検出する（info）。"""
    groups: dict[Any, list[str]] = {}
    for dev in model.devices:
        asn = dev.get("asn")
        if asn is None:
            continue
        groups.setdefault(asn, []).append(dev.get("device-id", ""))
    return [
        ValidationIssue(
            rule="duplicate-asn",
            severity="info",
            message=(
                f"ASN {asn} を {len(devs)} 台が共有しています"
                "（iBGP なら正常・eBGP では要確認）。"
            ),
            targets=devs,
        )
        for asn, devs in groups.items()
        if len(devs) > 1
    ]


@rule
def _check_ip_address_overlap(model: TopologyModel) -> list[ValidationIssue]:
    """異なるインターフェースで同一ホスト IP が重複する状態を検出する。"""
    groups: dict[str, list[str]] = {}
    for dev in model.devices:
        did = dev.get("device-id", "")
        for iface in dev.get("interface", []) or []:
            host = _norm_ip(iface.get("ip-address"))
            if host is None:
                continue
            groups.setdefault(host, []).append(f"{did}[{iface.get('interface-id', '')}]")
    return [
        ValidationIssue(
            rule="ip-address-overlap",
            severity="error",
            message=f"IP アドレス {host} が複数のインターフェースに割り当てられています。",
            targets=locs,
        )
        for host, locs in groups.items()
        if len(locs) > 1
    ]


# ---------------------------------------------------------------------------
# 検証ルール（Phase 2: L3 整合性・到達性・冗長性）
# ---------------------------------------------------------------------------


@rule
def _check_subnet_overlap(model: TopologyModel) -> list[ValidationIssue]:
    """ip-subnet.prefix 同士の CIDR 重なりを検出する。"""
    nets: list[tuple[str, ipaddress._BaseNetwork]] = []
    for sn in model.subnets:
        prefix = sn.get("prefix")
        if not prefix:
            continue
        try:
            net = ipaddress.ip_network(str(prefix).strip(), strict=False)
        except ValueError:
            continue
        nets.append((sn.get("subnet-id") or str(prefix), net))

    issues: list[ValidationIssue] = []
    for i in range(len(nets)):
        for j in range(i + 1, len(nets)):
            sid_a, net_a = nets[i]
            sid_b, net_b = nets[j]
            if net_a.version == net_b.version and net_a.overlaps(net_b):
                issues.append(ValidationIssue(
                    rule="subnet-overlap",
                    severity="warning",
                    message=f"サブネット {net_a} と {net_b} が重複しています。",
                    targets=[sid_a, sid_b],
                ))
    return issues


@rule
def _check_iface_subnet_mismatch(model: TopologyModel) -> list[ValidationIssue]:
    """interface の ip-address がどの ip-subnet.prefix にも属さない状態を検出する。"""
    nets: list[ipaddress._BaseNetwork] = []
    for sn in model.subnets:
        prefix = sn.get("prefix")
        if not prefix:
            continue
        try:
            nets.append(ipaddress.ip_network(str(prefix).strip(), strict=False))
        except ValueError:
            continue
    if not nets:  # サブネット未宣言なら検証対象外
        return []

    issues: list[ValidationIssue] = []
    for dev in model.devices:
        did = dev.get("device-id", "")
        for iface in dev.get("interface", []) or []:
            addr = iface.get("ip-address")
            host = _norm_ip(addr)
            if host is None:
                continue
            ip = ipaddress.ip_address(host)
            if not any(ip in net for net in nets):
                issues.append(ValidationIssue(
                    rule="iface-subnet-mismatch",
                    severity="warning",
                    message=f"インターフェース IP {addr} がどの ip-subnet にも属しません。",
                    targets=[f"{did}[{iface.get('interface-id', '')}]"],
                ))
    return issues


@rule
def _check_p2p_mask_mismatch(model: TopologyModel) -> list[ValidationIssue]:
    """/30・/31 の P2P リンクで両端 IP が同一ネットワークに属さない状態を検出する。"""
    issues: list[ValidationIssue] = []
    for conn in model.connections:
        eps = conn.get("endpoint", []) or []
        if len(eps) != 2:
            continue
        d0 = model.device_map.get(eps[0].get("device-id"))
        d1 = model.device_map.get(eps[1].get("device-id"))
        if not d0 or not d1:
            continue
        a = _iface_ip(d0, eps[0].get("interface-id"))
        b = _iface_ip(d1, eps[1].get("interface-id"))
        if not isinstance(a, str) or not isinstance(b, str):
            continue
        try:
            ia = ipaddress.ip_interface(a.strip())
            ib = ipaddress.ip_interface(b.strip())
        except ValueError:
            continue
        # 両端とも /30・/31 の P2P リンクのみ対象
        if ia.network.prefixlen not in (30, 31) or ib.network.prefixlen not in (30, 31):
            continue
        if ia.network != ib.network:
            issues.append(ValidationIssue(
                rule="p2p-mask-mismatch",
                severity="info",
                message=f"P2P リンクの両端 IP（{ia} / {ib}）が同一ネットワークに属しません。",
                targets=[_conn_target(conn)],
            ))
    return issues


@rule
def _check_isolated_device(model: TopologyModel) -> list[ValidationIssue]:
    """どの physical-connection にも現れない孤立ノード（次数 0）を検出する。"""
    adj = _build_graph(model)
    return [
        ValidationIssue(
            rule="isolated-device",
            severity="warning",
            message=f"デバイス '{node}' はどの接続にも現れない孤立ノードです。",
            targets=[node],
        )
        for node in sorted(n for n, nb in adj.items() if not nb)
    ]


@rule
def _check_spof_device(model: TopologyModel) -> list[ValidationIssue]:
    """単一障害点になり得る関節点（cut vertex）を検出する。"""
    adj = _build_graph(model)
    aps, _ = _articulation_and_bridges(adj)
    return [
        ValidationIssue(
            rule="spof-device",
            severity="warning",
            message=f"デバイス '{node}' は単一障害点（関節点）です。停止すると到達性が分断されます。",
            targets=[node],
        )
        for node in sorted(aps)
    ]


@rule
def _check_spof_bridge_link(model: TopologyModel) -> list[ValidationIssue]:
    """切断すると分断される橋（bridge edge）を検出する（並行リンクは除外）。"""
    adj = _build_graph(model)
    _, bridges = _articulation_and_bridges(adj)
    mult = _pair_multiplicity(model)
    issues: list[ValidationIssue] = []
    for edge in bridges:
        if mult.get(edge, 0) > 1:  # 並行リンク（LAG）は冗長のため橋ではない
            continue
        a, b = sorted(edge)
        issues.append(ValidationIssue(
            rule="spof-bridge-link",
            severity="warning",
            message=f"リンク {a} <-> {b} は橋です。切断すると到達性が分断されます。",
            targets=[a, b],
        ))
    return issues


# ---------------------------------------------------------------------------
# ポリシー検証（Phase 3）
# ---------------------------------------------------------------------------


def _select_nodes(model: TopologyModel, selector: NodeSelector) -> set[str]:
    """セレクタに一致する device-id の集合を返す。"""
    return {
        d["device-id"]
        for d in model.devices
        if d.get("device-id") and selector.matches(d)
    }


def _reachable_from(
    adj: dict[str, set[str]],
    sources: set[str],
    blocked: set[str],
) -> set[str]:
    """blocked ノードを通らずに sources から到達できるノード集合を返す。"""
    seen: set[str] = set()
    stack = [s for s in sources if s not in blocked and s in adj]
    seen.update(stack)
    while stack:
        u = stack.pop()
        for v in adj.get(u, ()):
            if v in blocked or v in seen:
                continue
            seen.add(v)
            stack.append(v)
    return seen


def _real_bridge_nodes(model: TopologyModel, adj: dict[str, set[str]]) -> set[str]:
    """並行リンク（LAG）を除いた橋に接するノード集合を返す。"""
    _, bridges = _articulation_and_bridges(adj)
    mult = _pair_multiplicity(model)
    nodes: set[str] = set()
    for edge in bridges:
        if mult.get(edge, 0) > 1:
            continue
        nodes.update(edge)
    return nodes


def _check_zone_transit(
    model: TopologyModel, policy: ZoneTransitPolicy
) -> list[ValidationIssue]:
    """src→dst の通信が via を経由しない経路を持つ場合に違反を返す。"""
    src = _select_nodes(model, policy.src)
    dst = _select_nodes(model, policy.dst)
    if not src or not dst:
        return []
    via = _select_nodes(model, policy.via)
    adj = _build_graph(model)
    reachable = _reachable_from(adj, src, blocked=via)
    # via を経由せず到達できた dst（src 自身・via 自身は除く）が違反
    leaked = sorted((reachable & dst) - src - via)
    if not leaked:
        return []
    name = policy.name or "zone-transit"
    return [ValidationIssue(
        rule="zone-policy-violation",
        severity=policy.severity,
        message=(
            f"ポリシー '{name}' 違反: {leaked} へ "
            "指定の経由ゾーンを通らずに到達できます。"
        ),
        targets=leaked,
    )]


def _check_zone_redundancy(
    model: TopologyModel, policy: ZoneRedundancyPolicy
) -> list[ValidationIssue]:
    """セレクタ対象デバイスが単一障害点（関節点／橋の端点）なら違反を返す。"""
    matched = _select_nodes(model, policy.selector)
    if not matched:
        return []
    adj = _build_graph(model)
    aps, _ = _articulation_and_bridges(adj)
    non_redundant = aps | _real_bridge_nodes(model, adj)
    violating = sorted(matched & non_redundant)
    if not violating:
        return []
    name = policy.name or "zone-redundancy"
    return [ValidationIssue(
        rule="zone-redundancy-violation",
        severity=policy.severity,
        message=(
            f"ポリシー '{name}' 違反: {violating} は冗長化されていません"
            "（単一障害点です）。"
        ),
        targets=violating,
    )]


def _run_policies(model: TopologyModel, policies: PolicySet) -> list[ValidationIssue]:
    """ポリシーセットを検証し issue を返す。"""
    issues: list[ValidationIssue] = []
    for p in policies.zone_transit:
        issues.extend(_check_zone_transit(model, p))
    for p in policies.zone_redundancy:
        issues.extend(_check_zone_redundancy(model, p))
    return issues


# ---------------------------------------------------------------------------
# LLM 説明・修正案（Phase 4: --explain）
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json_array(text: str) -> str:
    """LLM 応答から JSON 配列部分を抽出する（コードブロック・前後テキストを除去）。"""
    m = _JSON_BLOCK_RE.search(text)
    raw = m.group(1) if m else text
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw.strip()


def _parse_explanations(text: str) -> dict[int, tuple[str, str]]:
    """LLM 応答を index → (explanation, suggestion) の対応表に変換する。"""
    try:
        data = json.loads(_extract_json_array(text))
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("design-lint の説明 JSON をパースできませんでした: %s", e)
        return {}
    result: dict[int, tuple[str, str]] = {}
    if not isinstance(data, list):
        return result
    for item in data:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool):
            continue
        result[idx] = (
            str(item.get("explanation", "") or ""),
            str(item.get("suggestion", "") or ""),
        )
    return result


def explain(
    report: ValidationReport,
    model: TopologyModel,
    llm: Any | None = None,
) -> ValidationReport:
    """検出済み issue に LLM で理由（explanation）と修正案（suggestion）を付与する。

    **issue 集合は不変**（rule / severity / message / targets は変更しない）。
    LLM が返した説明のうち、既存 issue の index に一致するものだけを充填する
    （新規 issue の捏造・index 不一致は無視する）。LLM 応答が壊れていた場合は
    説明なしのまま元の issue を保持する。
    """
    if not report.issues:
        return report

    if llm is None:
        from d2v.llm import get_llm

        llm = get_llm()

    from d2v.prompts import load_prompt

    payload = [
        {
            "index": i,
            "rule": iss.rule,
            "severity": iss.severity,
            "message": iss.message,
            "targets": iss.targets,
        }
        for i, iss in enumerate(report.issues)
    ]
    context = build_text(
        model.devices, model.connections, model.subnets, model.device_map
    )
    system = load_prompt("design-lint.md")
    user = (
        f"## トポロジ\n\n{context}\n\n"
        "## 検出済み issue（JSON）\n\n```json\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )
    response = llm.chat(system=system, user=user)
    mapping = _parse_explanations(response)

    new_issues = [
        iss.model_copy(update={
            "explanation": mapping.get(i, ("", ""))[0],
            "suggestion": mapping.get(i, ("", ""))[1],
        })
        for i, iss in enumerate(report.issues)
    ]
    return ValidationReport.from_issues(new_issues)


# ---------------------------------------------------------------------------
# rich 整形
# ---------------------------------------------------------------------------





def render_report(report: ValidationReport) -> RenderableType:
    """レポートを rich renderable（サマリ＋テーブル）に整形する。

    ``explain()`` で説明が付いている場合は、テーブルの下に理由・修正案を追記する。
    """
    if not report.issues:
        return Text("✓ 設計上の問題は検出されませんでした。", style="bold green")

    summary = Text()
    summary.append("検証結果  ", style="bold")
    for i, sev in enumerate(SEVERITIES):
        if i:
            summary.append("  ")
        summary.append(f"{sev}={report.counts.get(sev, 0)}", style=_SEVERITY_STYLE[sev])

    table = Table(show_header=True, header_style="bold")
    table.add_column("重大度", no_wrap=True)
    table.add_column("ルール", no_wrap=True)
    table.add_column("内容")
    table.add_column("対象")
    for issue in report.issues:
        style = _SEVERITY_STYLE.get(issue.severity, "")
        table.add_row(
            Text(issue.severity, style=style),
            issue.rule,
            issue.message,
            ", ".join(issue.targets),
        )

    renderables: list[RenderableType] = [summary, table]

    # --explain で説明が付いている場合は詳細を追記する。
    if any(iss.explanation or iss.suggestion for iss in report.issues):
        detail = Text()
        detail.append("\n詳細（--explain）\n", style="bold")
        for issue in report.issues:
            if not (issue.explanation or issue.suggestion):
                continue
            style = _SEVERITY_STYLE.get(issue.severity, "")
            detail.append(f"\n● {issue.rule} ", style=style)
            detail.append(f"[{', '.join(issue.targets)}]\n", style="dim")
            if issue.explanation:
                detail.append("  理由: ", style="bold")
                detail.append(f"{issue.explanation}\n")
            if issue.suggestion:
                detail.append("  修正案: ", style="bold")
                detail.append(f"{issue.suggestion}\n")
        renderables.append(detail)

    return Group(*renderables)
