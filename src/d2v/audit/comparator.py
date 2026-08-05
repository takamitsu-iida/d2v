"""audit 比較器: 設計 TopologyModel と LLM 抽出 ExtractedConfig を決定論的に比較する。"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from d2v.audit.schema import AuditIssue, AuditReport, ExtractedConfig
from d2v.parser import TopologyModel

logger = logging.getLogger(__name__)

_YamlDict = dict[str, Any]

# ---------------------------------------------------------------------------
# インターフェース名正規化
# ---------------------------------------------------------------------------

# Cisco IOS 省略形 → 正式名 の対応（長い方から順に並べる）
_IOS_ABBREV: list[tuple[str, str]] = [
    ("tengigabitethernet", "tengigabitethernet"),
    ("hundredgigabitethernet", "hundredgigabitethernet"),
    ("gigabitethernet", "gigabitethernet"),
    ("fastethernet", "fastethernet"),
    ("ethernet", "ethernet"),
    ("loopback", "loopback"),
    ("management", "management"),
    ("port-channel", "port-channel"),
    ("tunnel", "tunnel"),
    ("vlan", "vlan"),
    # 省略形
    ("tengig", "tengigabitethernet"),
    ("hundgig", "hundredgigabitethernet"),
    ("gi", "gigabitethernet"),
    ("fa", "fastethernet"),
    ("te", "tengigabitethernet"),
    ("hu", "hundredgigabitethernet"),
    ("et", "ethernet"),
    ("lo", "loopback"),
    ("ma", "management"),
    ("po", "port-channel"),
    ("tu", "tunnel"),
    ("vl", "vlan"),
]


def _expand_iface(name: str) -> str:
    """省略形インターフェース名を正式名に展開する（小文字前提）。"""
    for abbr, full in _IOS_ABBREV:
        if name.startswith(abbr) and (not name.startswith(full) or abbr == full):
            suffix = name[len(abbr):]
            # 展開後が同じならそのまま返す（無限展開防止）
            if abbr != full:
                return full + suffix
    return name


def _norm_iface(name: str) -> str:
    """インターフェース名を正規化して比較用キーを返す。"""
    n = name.strip().lower().replace(" ", "")
    return _expand_iface(n)


def _iface_match(design_name: str, config_name: str) -> bool:
    """設計名とコンフィグ名が同じインターフェースを指すか判定する。"""
    return _norm_iface(design_name) == _norm_iface(config_name)


def _norm_lag_group(lag_group: str) -> str:
    """LLM が channel-group 番号（例: '1'）のみを返した場合に Port-channelN に正規化する。"""
    s = lag_group.strip()
    if s.isdigit():
        return f"port-channel{s}"
    return _norm_iface(s)


# ---------------------------------------------------------------------------
# IP アドレス正規化
# ---------------------------------------------------------------------------

def _norm_ip(ip_str: str) -> str:
    """CIDR 表記の IP アドレスを正規化する（大文字小文字・空白除去）。

    パース失敗時はそのまま小文字返し（逸脱として検出させる）。
    """
    try:
        return str(ipaddress.ip_interface(ip_str.strip()).with_prefixlen)
    except ValueError:
        return ip_str.strip().lower()


# ---------------------------------------------------------------------------
# 内部ヘルパ
# ---------------------------------------------------------------------------

def _issue(rule: str, severity: str, device_id: str, message: str, detail: str = "") -> AuditIssue:
    return AuditIssue(rule=rule, severity=severity, device_id=device_id,
                      message=message, detail=detail)


def _config_iface_map(cfg: ExtractedConfig) -> dict[str, Any]:
    """正規化名 → ExtractedInterface の辞書を返す。"""
    return {_norm_iface(i.name): i for i in cfg.interfaces}


# ---------------------------------------------------------------------------
# 比較ルール
#
# 新しいルールを追加する手順:
#   1. `_check_<topic>(cfg, design_dev, ...) -> list[AuditIssue]` 関数を作成し、
#      `_issue("rule-id", "error|warning|info", did, message, detail)` で issue を生成する。
#   2. 関数内で検出した issue を list に追加して return する。
#   3. `compare()` の各デバイスループ末尾で `issues.extend(_check_<topic>(...))` を呼ぶ。
#   4. schema.py の AuditIssue に新フィールドが必要な場合はそちらも更新する。
#   5. AUDIT.md の「比較ルール一覧」テーブルにルール ID・内容・重大度を追記する。
# ---------------------------------------------------------------------------

def _check_hostname(cfg: ExtractedConfig, design_dev: _YamlDict) -> list[AuditIssue]:
    """hostname がコンフィグ内の値と device-id が不一致なら warning。"""
    issues: list[AuditIssue] = []
    if cfg.hostname is None:
        return issues
    did = cfg.device_id
    if cfg.hostname.lower() != did.lower():
        issues.append(_issue(
            "hostname-mismatch", "warning", did,
            f"hostname がコンフィグと device-id で不一致",
            f"design={did}  config={cfg.hostname}",
        ))
    return issues


def _check_interfaces(cfg: ExtractedConfig, design_dev: _YamlDict) -> list[AuditIssue]:
    """IP アドレス・description の設計対比を行う。"""
    issues: list[AuditIssue] = []
    did = cfg.device_id
    config_map = _config_iface_map(cfg)

    for d_iface in design_dev.get("interface", []):
        d_name: str = d_iface.get("interface-id", "")
        d_ip: str = d_iface.get("ip-address", "") or ""
        d_desc: str = d_iface.get("description", "") or ""
        norm_d = _norm_iface(d_name)

        matched = config_map.get(norm_d)

        if d_ip:
            # IP が設計に記載されているインターフェースは必ずコンフィグに存在すること
            if matched is None:
                issues.append(_issue(
                    "iface-missing", "error", did,
                    f"設計にある {d_name} がコンフィグに存在しない",
                    f"design ip={d_ip}",
                ))
                continue

            c_ip = matched.ip_address or ""
            if c_ip and _norm_ip(c_ip) != _norm_ip(d_ip):
                issues.append(_issue(
                    "iface-ip-mismatch", "error", did,
                    f"{d_name} の IP アドレスが設計と不一致",
                    f"design={_norm_ip(d_ip)}  config={_norm_ip(c_ip)}",
                ))

        if d_desc and matched is not None:
            c_desc = matched.description or ""
            if c_desc and c_desc.strip() != d_desc.strip():
                issues.append(_issue(
                    "description-mismatch", "warning", did,
                    f"{d_name} の description が設計と不一致",
                    f"design={d_desc!r}  config={c_desc!r}",
                ))

    # 設計にない IP 付きインターフェースが実機にある場合（info）
    design_norms = {_norm_iface(i.get("interface-id", "")) for i in design_dev.get("interface", [])}
    for c_iface in cfg.interfaces:
        if c_iface.ip_address and _norm_iface(c_iface.name) not in design_norms:
            issues.append(_issue(
                "iface-extra", "info", did,
                f"設計にない IP 付きインターフェース {c_iface.name} がコンフィグに存在する",
                f"config ip={c_iface.ip_address}",
            ))

    return issues


def _check_lags(cfg: ExtractedConfig, design_dev: _YamlDict, model: TopologyModel) -> list[AuditIssue]:
    """LAG メンバーインターフェースの設計対比を行う。"""
    issues: list[AuditIssue] = []
    did = cfg.device_id

    # このデバイスの LAG エントリ一覧
    device_lags = [lag for lag in model.lags if lag.get("device-id") == did]
    if not device_lags:
        return issues

    for lag_entry in device_lags:
        lag_id: str = lag_entry.get("lag-id", "")
        design_members: set[str] = {
            _norm_iface(m.get("interface-id", ""))
            for m in lag_entry.get("member-interface", [])
        }
        # コンフィグ上でこの LAG に所属しているインターフェース
        config_members: set[str] = {
            _norm_iface(i.name)
            for i in cfg.interfaces
            if i.lag_group and _norm_lag_group(i.lag_group) == _norm_iface(lag_id)
        }

        missing = design_members - config_members
        extra = config_members - design_members

        for m in sorted(missing):
            issues.append(_issue(
                "lag-member-mismatch", "error", did,
                f"{lag_id}: 設計メンバー {m} がコンフィグの LAG に存在しない",
                f"lag={lag_id}",
            ))
        for m in sorted(extra):
            issues.append(_issue(
                "lag-member-mismatch", "info", did,
                f"{lag_id}: 設計にないインターフェース {m} がコンフィグの LAG に存在する",
                f"lag={lag_id}",
            ))

    return issues


def _build_bgp_peers(
    device_id: str,
    model: TopologyModel,
    config_map: dict[str, ExtractedConfig],
) -> list[tuple[str, int]]:
    """設計から期待される BGP ピア (peer_ip, remote_asn) 一覧を生成する。

    両端ともに asn が設定されている接続のみを対象とする。
    peer_ip は相手デバイスの接続インターフェース IP（/prefix なし）。
    """
    this_dev = model.device_map.get(device_id, {})
    if not this_dev.get("asn"):
        return []

    peers: list[tuple[str, int]] = []
    for conn in model.connections:
        endpoints = conn.get("endpoint", [])
        if len(endpoints) != 2:
            continue
        ep_a, ep_b = endpoints[0], endpoints[1]
        # このデバイスがどちらかの端点か確認
        if ep_a.get("device-id") == device_id:
            my_ep, peer_ep = ep_a, ep_b
        elif ep_b.get("device-id") == device_id:
            my_ep, peer_ep = ep_b, ep_a
        else:
            continue

        peer_did = peer_ep.get("device-id", "")
        peer_dev = model.device_map.get(peer_did, {})
        peer_asn = peer_dev.get("asn")
        if not peer_asn:
            continue

        # 相手デバイスの接続インターフェース IP を取得
        peer_iface_id = peer_ep.get("interface-id", "")
        peer_ip_cidr = ""
        for iface in peer_dev.get("interface", []):
            if iface.get("interface-id") == peer_iface_id:
                peer_ip_cidr = iface.get("ip-address", "") or ""
                break
        if not peer_ip_cidr:
            continue

        try:
            peer_ip = str(ipaddress.ip_interface(peer_ip_cidr).ip)
        except ValueError:
            continue

        peers.append((peer_ip, int(peer_asn)))

    return peers


def _check_bgp(
    cfg: ExtractedConfig,
    design_dev: _YamlDict,
    model: TopologyModel,
    config_map: dict[str, ExtractedConfig],
) -> list[AuditIssue]:
    """BGP ピアの設計対比を行う。"""
    issues: list[AuditIssue] = []
    did = cfg.device_id

    expected_peers = _build_bgp_peers(did, model, config_map)
    if not expected_peers:
        return issues

    config_peer_map: dict[str, int | None] = {
        p.peer_address.strip(): p.remote_asn
        for p in cfg.bgp_peers
    }

    for peer_ip, remote_asn in expected_peers:
        if peer_ip not in config_peer_map:
            issues.append(_issue(
                "bgp-peer-missing", "error", did,
                f"設計の BGP ピア {peer_ip} (AS{remote_asn}) がコンフィグに存在しない",
                f"expected peer={peer_ip} remote-as={remote_asn}",
            ))
        else:
            c_asn = config_peer_map[peer_ip]
            if c_asn is not None and c_asn != remote_asn:
                issues.append(_issue(
                    "bgp-asn-mismatch", "error", did,
                    f"BGP ピア {peer_ip} の AS 番号が設計と不一致",
                    f"design AS{remote_asn}  config AS{c_asn}",
                ))

    return issues


# ---------------------------------------------------------------------------
# メインエントリポイント
# ---------------------------------------------------------------------------

def compare(model: TopologyModel, configs: list[ExtractedConfig]) -> AuditReport:
    """設計モデルとコンフィグ抽出結果を比較して AuditReport を返す。

    Args:
        model: ``parser.load_model()`` で読み込んだ設計モデル。
        configs: ``extractor.extract_from_config()`` で抽出したコンフィグ一覧。

    Returns:
        AuditReport（issues が空なら ok=True）。
    """
    issues: list[AuditIssue] = []
    config_map: dict[str, ExtractedConfig] = {cfg.device_id: cfg for cfg in configs}

    for cfg in configs:
        did = cfg.device_id
        if did not in model.device_map:
            issues.append(_issue(
                "device-unmatched", "error", did,
                f"コンフィグファイルの device-id '{did}' が設計 YAML に存在しない",
            ))
            continue

        design_dev = model.device_map[did]
        issues.extend(_check_hostname(cfg, design_dev))
        issues.extend(_check_interfaces(cfg, design_dev))
        issues.extend(_check_lags(cfg, design_dev, model))
        issues.extend(_check_bgp(cfg, design_dev, model, config_map))
        # 新ルールを追加した場合はここに issues.extend(_check_<topic>(...)) を追加する

    return AuditReport.from_issues(issues)
