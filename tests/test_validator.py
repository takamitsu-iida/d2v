"""validator（セマンティック検証）Phase 0: 骨格・データモデルのテスト。"""

from __future__ import annotations

from rich.console import Console

from d2v.parser import TopologyModel
from d2v.validator import (
    SEVERITIES,
    NodeSelector,
    PolicySet,
    ValidationIssue,
    ValidationReport,
    ZoneRedundancyPolicy,
    ZoneTransitPolicy,
    explain,
    load_policies,
    render_report,
    rule,
    validate,
)


def test_validate_empty_model_returns_ok_report():
    report = validate(TopologyModel())
    assert isinstance(report, ValidationReport)
    assert report.ok is True
    assert report.issues == []
    assert report.counts == {sev: 0 for sev in SEVERITIES}


def test_from_issues_aggregates_counts_and_ok():
    issues = [
        ValidationIssue(rule="a", severity="error", message="x"),
        ValidationIssue(rule="b", severity="warning", message="y"),
        ValidationIssue(rule="c", severity="warning", message="z"),
        ValidationIssue(rule="d", severity="info", message="w"),
    ]
    report = ValidationReport.from_issues(issues)
    assert report.counts == {"error": 1, "warning": 2, "info": 1}
    assert report.ok is False


def test_from_issues_ok_when_no_error():
    report = ValidationReport.from_issues(
        [ValidationIssue(rule="a", severity="warning", message="y")]
    )
    assert report.ok is True
    assert report.counts["error"] == 0


def test_passed_strict_semantics():
    err = ValidationReport.from_issues(
        [ValidationIssue(rule="a", severity="error", message="x")]
    )
    warn = ValidationReport.from_issues(
        [ValidationIssue(rule="b", severity="warning", message="y")]
    )
    clean = ValidationReport.from_issues([])
    assert err.passed() is False and err.passed(strict=True) is False
    assert warn.passed() is True and warn.passed(strict=True) is False
    assert clean.passed() is True and clean.passed(strict=True) is True



def test_rule_decorator_registers_and_validate_runs_it():
    # 一時ルールを登録し、validate() が実行することを確認する。
    from d2v import validator

    sentinel = ValidationIssue(rule="tmp", severity="info", message="temp")

    @rule
    def _tmp_rule(_model: TopologyModel) -> list[ValidationIssue]:
        return [sentinel]

    try:
        report = validate(TopologyModel())
        assert sentinel in report.issues
        assert report.counts["info"] == 1
    finally:
        validator._RULES.remove(_tmp_rule)


def test_render_report_empty_is_renderable():
    console = Console(record=True, width=80)
    console.print(render_report(validate(TopologyModel())))
    out = console.export_text()
    assert "問題は検出されませんでした" in out


def test_render_report_with_issues_shows_summary():
    report = ValidationReport.from_issues(
        [
            ValidationIssue(
                rule="dangling-endpoint",
                severity="error",
                message="片側のみ定義されたリンクがあります。",
                targets=["conn-01"],
            )
        ]
    )
    console = Console(record=True, width=100)
    console.print(render_report(report))
    out = console.export_text()
    assert "error=1" in out
    assert "dangling-endpoint" in out
    assert "conn-01" in out


# ---------------------------------------------------------------------------
# Phase 1: 構造整合性・一意性ルール
# ---------------------------------------------------------------------------


def _model(devices=None, connections=None, subnets=None) -> TopologyModel:
    """テスト用に TopologyModel を組み立てる（load_model と同じ device_map 構築）。"""
    devices = devices or []
    device_map = {d["device-id"]: d for d in devices if d.get("device-id")}
    return TopologyModel(
        devices=devices,
        connections=connections or [],
        subnets=subnets or [],
        device_map=device_map,
    )


def _dev(did, *, ifaces=None, loopback=None, asn=None, zone=None, dtype=None):
    d = {"device-id": did}
    if ifaces is not None:
        d["interface"] = ifaces
    if loopback is not None:
        d["loopback"] = loopback
    if asn is not None:
        d["asn"] = asn
    if zone is not None:
        d["zone"] = zone
    if dtype is not None:
        d["device-type"] = dtype
    return d


def _conn(cid, a, b):
    """(device, iface) ペア 2 つから physical-connection を作る。"""
    return {
        "connection-id": cid,
        "endpoint": [
            {"device-id": a[0], "interface-id": a[1]},
            {"device-id": b[0], "interface-id": b[1]},
        ],
    }


def _rules_of(report):
    return {i.rule for i in report.issues}


def test_valid_topology_has_no_issues():
    # 冗長（3 ノードのリング）・IP/loopback/ASN 一意・サブネット整合で問題ゼロ。
    devices = [
        _dev("n1", ifaces=[
            {"interface-id": "a", "ip-address": "10.0.12.1/30"},
            {"interface-id": "b", "ip-address": "10.0.31.2/30"},
        ], loopback="1.1.1.1/32", asn=65001),
        _dev("n2", ifaces=[
            {"interface-id": "a", "ip-address": "10.0.12.2/30"},
            {"interface-id": "b", "ip-address": "10.0.23.1/30"},
        ], loopback="2.2.2.2/32", asn=65002),
        _dev("n3", ifaces=[
            {"interface-id": "a", "ip-address": "10.0.23.2/30"},
            {"interface-id": "b", "ip-address": "10.0.31.1/30"},
        ], loopback="3.3.3.3/32", asn=65003),
    ]
    conns = [
        _conn("n1__n2", ("n1", "a"), ("n2", "a")),
        _conn("n2__n3", ("n2", "b"), ("n3", "a")),
        _conn("n3__n1", ("n3", "b"), ("n1", "b")),
    ]
    subnets = [
        {"subnet-id": "l12", "prefix": "10.0.12.0/30"},
        {"subnet-id": "l23", "prefix": "10.0.23.0/30"},
        {"subnet-id": "l31", "prefix": "10.0.31.0/30"},
    ]
    report = validate(_model(devices, conns, subnets))
    assert report.ok is True
    assert report.issues == []



def test_dangling_endpoint_wrong_count():
    conn = {"connection-id": "c1", "endpoint": [{"device-id": "r1", "interface-id": "g0"}]}
    report = validate(_model([_dev("r1", ifaces=[{"interface-id": "g0"}])], [conn]))
    assert "dangling-endpoint" in _rules_of(report)
    assert report.ok is False


def test_dangling_endpoint_missing_device_id():
    conn = {
        "connection-id": "c1",
        "endpoint": [
            {"interface-id": "g0"},
            {"device-id": "r2", "interface-id": "g0"},
        ],
    }
    report = validate(_model([_dev("r2", ifaces=[{"interface-id": "g0"}])], [conn]))
    assert "dangling-endpoint" in _rules_of(report)


def test_unknown_device_ref():
    conn = _conn("c1", ("r1", "g0"), ("ghost", "g0"))
    report = validate(_model([_dev("r1", ifaces=[{"interface-id": "g0"}])], [conn]))
    issues = [i for i in report.issues if i.rule == "unknown-device-ref"]
    assert issues
    assert "ghost" in issues[0].targets


def test_unknown_interface_ref():
    conn = _conn("c1", ("r1", "g0"), ("r2", "does-not-exist"))
    devices = [
        _dev("r1", ifaces=[{"interface-id": "g0"}]),
        _dev("r2", ifaces=[{"interface-id": "g1"}]),
    ]
    report = validate(_model(devices, [conn]))
    assert "unknown-interface-ref" in _rules_of(report)


def test_self_loop():
    conn = _conn("c1", ("r1", "g0"), ("r1", "g1"))
    report = validate(_model([_dev("r1", ifaces=[{"interface-id": "g0"}, {"interface-id": "g1"}])], [conn]))
    assert "self-loop" in _rules_of(report)


def test_duplicate_device_id():
    devices = [_dev("r1"), _dev("r1"), _dev("r2")]
    report = validate(_model(devices))
    issues = [i for i in report.issues if i.rule == "duplicate-device-id"]
    assert len(issues) == 1
    assert issues[0].targets == ["r1"]


def test_duplicate_connection_same_ports():
    devices = [
        _dev("r1", ifaces=[{"interface-id": "g0"}]),
        _dev("r2", ifaces=[{"interface-id": "g0"}]),
    ]
    conns = [
        _conn("c1", ("r1", "g0"), ("r2", "g0")),
        _conn("c2", ("r2", "g0"), ("r1", "g0")),  # 逆順・同一ポート → 重複
    ]
    report = validate(_model(devices, conns))
    dup = [i for i in report.issues if i.rule == "duplicate-connection"]
    assert len(dup) == 1
    assert set(dup[0].targets) == {"c1", "c2"}


def test_parallel_links_different_ports_are_not_duplicate():
    devices = [
        _dev("r1", ifaces=[{"interface-id": "g0"}, {"interface-id": "g1"}]),
        _dev("r2", ifaces=[{"interface-id": "g0"}, {"interface-id": "g1"}]),
    ]
    conns = [
        _conn("c1", ("r1", "g0"), ("r2", "g0")),
        _conn("c2", ("r1", "g1"), ("r2", "g1")),  # 別ポート → LAG として正常
    ]
    report = validate(_model(devices, conns))
    assert "duplicate-connection" not in _rules_of(report)


def test_duplicate_loopback_with_notation():
    devices = [
        _dev("r1", loopback="10.0.0.1/32"),
        _dev("r2", loopback="10.0.0.1/32"),
    ]
    report = validate(_model(devices))
    dup = [i for i in report.issues if i.rule == "duplicate-loopback"]
    assert len(dup) == 1
    assert set(dup[0].targets) == {"r1", "r2"}


def test_duplicate_asn_is_info():
    devices = [_dev("r1", asn=65001), _dev("r2", asn=65001)]
    report = validate(_model(devices))
    dup = [i for i in report.issues if i.rule == "duplicate-asn"]
    assert len(dup) == 1
    assert dup[0].severity == "info"
    assert report.ok is True  # info のみなので ok


def test_ip_address_overlap():
    devices = [
        _dev("r1", ifaces=[{"interface-id": "g0", "ip-address": "10.1.1.1/30"}]),
        _dev("r2", ifaces=[{"interface-id": "g0", "ip-address": "10.1.1.1/24"}]),  # 同ホスト・別マスク
    ]
    report = validate(_model(devices))
    ov = [i for i in report.issues if i.rule == "ip-address-overlap"]
    assert len(ov) == 1
    assert set(ov[0].targets) == {"r1[g0]", "r2[g0]"}


def test_distinct_ips_no_overlap():
    devices = [
        _dev("r1", ifaces=[{"interface-id": "g0", "ip-address": "10.1.1.1/30"}]),
        _dev("r2", ifaces=[{"interface-id": "g0", "ip-address": "10.1.1.2/30"}]),
    ]
    report = validate(_model(devices))
    assert "ip-address-overlap" not in _rules_of(report)


# ---------------------------------------------------------------------------
# Phase 2: L3 整合性・到達性・冗長性
# ---------------------------------------------------------------------------


def _chain(n):
    """n1-n2-...-nN の直鎖トポロジ（冗長なし）を作る。"""
    devices = [_dev(f"n{i}") for i in range(1, n + 1)]
    conns = [
        _conn(f"c{i}", (f"n{i}", f"p{i}a"), (f"n{i+1}", f"p{i}b"))
        for i in range(1, n)
    ]
    return devices, conns


def _ring(n):
    """n1-n2-...-nN-n1 の環状トポロジ（冗長あり）を作る。"""
    devices = [_dev(f"n{i}") for i in range(1, n + 1)]
    conns = []
    for i in range(1, n + 1):
        j = i % n + 1
        conns.append(_conn(f"c{i}", (f"n{i}", f"p{i}a"), (f"n{j}", f"p{i}b")))
    return devices, conns


def test_subnet_overlap():
    subnets = [
        {"subnet-id": "a", "prefix": "10.1.0.0/24"},
        {"subnet-id": "b", "prefix": "10.1.0.128/25"},  # a に内包 → 重複
        {"subnet-id": "c", "prefix": "10.2.0.0/24"},
    ]
    report = validate(_model([], [], subnets))
    ov = [i for i in report.issues if i.rule == "subnet-overlap"]
    assert len(ov) == 1
    assert set(ov[0].targets) == {"a", "b"}


def test_iface_subnet_mismatch():
    devices = [
        _dev("r1", ifaces=[
            {"interface-id": "g0", "ip-address": "10.1.0.5/24"},   # 属する
            {"interface-id": "g1", "ip-address": "203.0.113.1/30"},  # どこにも属さない
        ]),
    ]
    subnets = [{"subnet-id": "lan", "prefix": "10.1.0.0/24"}]
    report = validate(_model(devices, [], subnets))
    mm = [i for i in report.issues if i.rule == "iface-subnet-mismatch"]
    assert len(mm) == 1
    assert mm[0].targets == ["r1[g1]"]


def test_iface_subnet_mismatch_skipped_without_subnets():
    devices = [_dev("r1", ifaces=[{"interface-id": "g0", "ip-address": "10.9.9.9/24"}])]
    report = validate(_model(devices))
    assert "iface-subnet-mismatch" not in _rules_of(report)


def test_p2p_mask_mismatch():
    devices = [
        _dev("r1", ifaces=[{"interface-id": "g0", "ip-address": "10.0.0.1/30"}]),
        _dev("r2", ifaces=[{"interface-id": "g0", "ip-address": "10.0.0.5/30"}]),  # 別ネットワーク
    ]
    conns = [_conn("c1", ("r1", "g0"), ("r2", "g0"))]
    report = validate(_model(devices, conns))
    assert "p2p-mask-mismatch" in _rules_of(report)


def test_p2p_mask_match_no_issue():
    devices = [
        _dev("r1", ifaces=[{"interface-id": "g0", "ip-address": "10.0.0.1/30"}]),
        _dev("r2", ifaces=[{"interface-id": "g0", "ip-address": "10.0.0.2/30"}]),
    ]
    conns = [_conn("c1", ("r1", "g0"), ("r2", "g0"))]
    report = validate(_model(devices, conns))
    assert "p2p-mask-mismatch" not in _rules_of(report)


def test_isolated_device():
    devices = [
        _dev("r1", ifaces=[{"interface-id": "g0"}]),
        _dev("r2", ifaces=[{"interface-id": "g0"}]),
        _dev("lonely"),  # 接続なし
    ]
    conns = [_conn("c1", ("r1", "g0"), ("r2", "g0"))]
    report = validate(_model(devices, conns))
    iso = [i for i in report.issues if i.rule == "isolated-device"]
    assert len(iso) == 1
    assert iso[0].targets == ["lonely"]


def test_chain_has_spof_and_bridges():
    devices, conns = _chain(4)  # n1-n2-n3-n4
    report = validate(_model(devices, conns))
    spof = {t for i in report.issues if i.rule == "spof-device" for t in i.targets}
    bridges = [i for i in report.issues if i.rule == "spof-bridge-link"]
    # 端点 n1/n4 は関節点でない。中間 n2/n3 が関節点。
    assert spof == {"n2", "n3"}
    # 直鎖の全リンク（3 本）が橋。
    assert len(bridges) == 3


def test_ring_has_no_spof_or_bridge():
    devices, conns = _ring(4)
    report = validate(_model(devices, conns))
    assert "spof-device" not in _rules_of(report)
    assert "spof-bridge-link" not in _rules_of(report)


def test_parallel_link_is_not_bridge():
    devices = [
        _dev("r1", ifaces=[{"interface-id": "g0"}, {"interface-id": "g1"}]),
        _dev("r2", ifaces=[{"interface-id": "g0"}, {"interface-id": "g1"}]),
    ]
    conns = [
        _conn("c1", ("r1", "g0"), ("r2", "g0")),
        _conn("c2", ("r1", "g1"), ("r2", "g1")),  # 並行リンク（LAG）
    ]
    report = validate(_model(devices, conns))
    assert "spof-bridge-link" not in _rules_of(report)


# ---------------------------------------------------------------------------
# Phase 3: ポリシー制約
# ---------------------------------------------------------------------------


def test_no_policies_no_policy_issues():
    devices, conns = _ring(4)
    report = validate(_model(devices, conns), policies=None)
    assert "zone-policy-violation" not in _rules_of(report)


def test_node_selector_matching():
    dev = {"device-id": "x", "zone": "core", "device-type": "switch"}
    assert NodeSelector(any="core").matches(dev)
    assert NodeSelector(any="switch").matches(dev)
    assert not NodeSelector(any="dmz").matches(dev)
    assert NodeSelector(zone="core", type="switch").matches(dev)
    assert not NodeSelector(zone="core", type="router").matches(dev)
    assert not NodeSelector().matches(dev)  # 空セレクタは何にも一致しない


def _transit_topology():
    """dmz-sw -(fw)- core-sw -- office-sw と、fw を迂回する裏道を切替可能に作る。"""
    devices = [
        _dev("dmz-sw", zone="dmz", dtype="switch"),
        _dev("fw", zone="wan-edge", dtype="firewall"),
        _dev("core-sw", zone="core", dtype="switch"),
        _dev("office-sw", zone="office", dtype="switch"),
    ]
    return devices


def test_zone_transit_compliant_when_only_path_via_firewall():
    devices = _transit_topology()
    conns = [
        _conn("c1", ("dmz-sw", "a"), ("fw", "a")),
        _conn("c2", ("fw", "b"), ("core-sw", "a")),
        _conn("c3", ("core-sw", "b"), ("office-sw", "a")),
    ]
    policies = PolicySet(zone_transit=[ZoneTransitPolicy(
        name="dmz-to-office-via-fw",
        src=NodeSelector(any="dmz"),
        dst=NodeSelector(any="office"),
        via=NodeSelector(any="firewall"),
    )])
    report = validate(_model(devices, conns), policies=policies)
    assert "zone-policy-violation" not in _rules_of(report)


def test_zone_transit_violation_when_bypass_exists():
    devices = _transit_topology()
    conns = [
        _conn("c1", ("dmz-sw", "a"), ("fw", "a")),
        _conn("c2", ("fw", "b"), ("core-sw", "a")),
        _conn("c3", ("core-sw", "b"), ("office-sw", "a")),
        _conn("bypass", ("dmz-sw", "z"), ("office-sw", "z")),  # firewall を迂回
    ]
    policies = PolicySet(zone_transit=[ZoneTransitPolicy(
        src=NodeSelector(any="dmz"),
        dst=NodeSelector(any="office"),
        via=NodeSelector(any="firewall"),
        severity="error",
    )])
    report = validate(_model(devices, conns), policies=policies)
    viol = [i for i in report.issues if i.rule == "zone-policy-violation"]
    assert len(viol) == 1
    assert viol[0].severity == "error"
    assert "office-sw" in viol[0].targets
    assert report.ok is False


def test_zone_redundancy_violation_in_chain():
    devices = [
        _dev("n1", zone="edge", dtype="router"),
        _dev("core", zone="core", dtype="switch"),
        _dev("n3", zone="edge", dtype="router"),
    ]
    conns = [
        _conn("c1", ("n1", "a"), ("core", "a")),
        _conn("c2", ("core", "b"), ("n3", "a")),
    ]
    policies = PolicySet(zone_redundancy=[ZoneRedundancyPolicy(
        name="core-redundant", selector=NodeSelector(zone="core"),
    )])
    report = validate(_model(devices, conns), policies=policies)
    viol = [i for i in report.issues if i.rule == "zone-redundancy-violation"]
    assert len(viol) == 1
    assert viol[0].targets == ["core"]
    assert viol[0].severity == "warning"


def test_zone_redundancy_compliant_in_ring():
    devices = [
        _dev("n1", zone="core", dtype="switch"),
        _dev("n2", zone="core", dtype="switch"),
        _dev("n3", zone="core", dtype="switch"),
    ]
    conns = [
        _conn("c1", ("n1", "a"), ("n2", "a")),
        _conn("c2", ("n2", "b"), ("n3", "a")),
        _conn("c3", ("n3", "b"), ("n1", "b")),
    ]
    policies = PolicySet(zone_redundancy=[ZoneRedundancyPolicy(
        selector=NodeSelector(zone="core"),
    )])
    report = validate(_model(devices, conns), policies=policies)
    assert "zone-redundancy-violation" not in _rules_of(report)


def test_load_policies_from_yaml(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "zone-transit:\n"
        "  - name: t1\n"
        "    from: dmz\n"
        "    to: office\n"
        "    via: firewall\n"
        "zone-redundancy:\n"
        "  - zone: core\n",
        encoding="utf-8",
    )
    policies = load_policies(p)
    assert len(policies.zone_transit) == 1
    assert policies.zone_transit[0].name == "t1"
    assert policies.zone_transit[0].src.any == "dmz"
    assert len(policies.zone_redundancy) == 1
    assert policies.zone_redundancy[0].selector.zone == "core"


def test_load_policies_empty_file(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    policies = load_policies(p)
    assert policies.zone_transit == []
    assert policies.zone_redundancy == []


# ---------------------------------------------------------------------------
# Phase 4: LLM 説明・修正案（--explain）
# ---------------------------------------------------------------------------


class _FakeLLM:
    """chat() で固定の JSON を返すテスト用 LLM。"""

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


def _report_two_issues():
    return ValidationReport.from_issues([
        ValidationIssue(rule="spof-device", severity="warning",
                        message="単一障害点です。", targets=["core"]),
        ValidationIssue(rule="duplicate-loopback", severity="error",
                        message="loopback 重複。", targets=["r1", "r2"]),
    ])


def test_explain_fills_explanation_and_suggestion():
    report = _report_two_issues()
    llm = _FakeLLM(
        '```json\n[{"index":0,"explanation":"core が落ちると分断されます。","suggestion":"core を二重化する。"},'
        '{"index":1,"explanation":"重複 IP は到達性を壊します。","suggestion":"loopback を一意にする。"}]\n```'
    )
    result = explain(report, TopologyModel(), llm=llm)
    assert result.issues[0].explanation.startswith("core が落ちる")
    assert result.issues[0].suggestion == "core を二重化する。"
    assert result.issues[1].explanation.startswith("重複 IP")
    # issue 集合（rule/severity/targets/message）は不変
    assert [i.rule for i in result.issues] == ["spof-device", "duplicate-loopback"]
    assert result.counts == report.counts
    assert len(llm.calls) == 1


def test_explain_ignores_fabricated_indices():
    report = _report_two_issues()
    # index 5 は存在しない issue → 無視される（捏造ガード）
    llm = _FakeLLM('[{"index":0,"explanation":"E0","suggestion":"S0"},'
                   '{"index":5,"explanation":"捏造","suggestion":"捏造"}]')
    result = explain(report, TopologyModel(), llm=llm)
    assert len(result.issues) == 2  # 追加されない
    assert result.issues[0].explanation == "E0"
    assert result.issues[1].explanation == ""  # index 1 は説明なし


def test_explain_broken_json_keeps_issues_unchanged():
    report = _report_two_issues()
    llm = _FakeLLM("これは JSON ではありません。")
    result = explain(report, TopologyModel(), llm=llm)
    assert len(result.issues) == 2
    assert all(i.explanation == "" and i.suggestion == "" for i in result.issues)
    assert [i.rule for i in result.issues] == ["spof-device", "duplicate-loopback"]


def test_explain_empty_report_skips_llm():
    llm = _FakeLLM("[]")
    result = explain(ValidationReport.from_issues([]), TopologyModel(), llm=llm)
    assert result.issues == []
    assert llm.calls == []  # LLM を呼ばない


def test_render_report_shows_explanation():
    report = ValidationReport.from_issues([
        ValidationIssue(rule="spof-device", severity="warning",
                        message="単一障害点です。", targets=["core"],
                        explanation="core が落ちると分断されます。",
                        suggestion="core を二重化する。"),
    ])
    console = Console(record=True, width=100)
    console.print(render_report(report))
    out = console.export_text()
    assert "詳細（--explain）" in out
    assert "core が落ちると分断されます。" in out
    assert "core を二重化する。" in out
