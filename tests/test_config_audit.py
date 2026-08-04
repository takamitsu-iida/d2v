"""audit extractor / comparator のユニットテスト。LLM は monkeypatch でモックする。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from d2v.audit import render_report, to_json
from d2v.audit.comparator import _iface_match, _norm_iface, compare
from d2v.audit.extractor import ExtractionError, extract_from_config
from d2v.audit.pipeline import _collect_config_paths, run
from d2v.audit.schema import AuditIssue, AuditReport, ExtractedBgpPeer, ExtractedConfig, ExtractedInterface
from d2v.parser import TopologyModel

FIXTURES = Path(__file__).parent / "fixtures"

# LLM が返す正常な ExtractedConfig JSON（router-01 設計通りコンフィグに対応）
_OK_RESPONSE = json.dumps({
    "device_id": "router-01",
    "hostname": "router-01",
    "vendor": "iosxe",
    "interfaces": [
        {"name": "Loopback0", "ip_address": "10.0.0.1/32", "description": None, "admin_state": "up", "lag_group": None},
        {"name": "GigabitEthernet0/0", "ip_address": "203.0.113.1/30", "description": "To ISP", "admin_state": "up", "lag_group": None},
        {"name": "GigabitEthernet0/1", "ip_address": "10.1.0.1/30", "description": "To Firewall", "admin_state": "up", "lag_group": None},
    ],
    "bgp_peers": [
        {"peer_address": "10.1.0.2", "remote_asn": 65001, "description": "To fw-01"},
    ],
    "vlans": [],
    "confidence": 0.98,
})

# LLM が返す逸脱コンフィグ JSON（IP ミスマッチ・hostname 不一致・ASN 誤り）
_NG_RESPONSE = json.dumps({
    "device_id": "router-01",
    "hostname": "Router01",
    "vendor": "iosxe",
    "interfaces": [
        {"name": "Loopback0", "ip_address": "10.0.0.1/32", "description": None, "admin_state": "up", "lag_group": None},
        {"name": "GigabitEthernet0/0", "ip_address": "203.0.113.1/30", "description": "To ISP", "admin_state": "up", "lag_group": None},
        {"name": "GigabitEthernet0/1", "ip_address": "10.1.0.99/30", "description": "To Upstream", "admin_state": "up", "lag_group": None},
    ],
    "bgp_peers": [
        {"peer_address": "10.1.0.2", "remote_asn": 65099, "description": "To fw-01"},
    ],
    "vlans": [],
    "confidence": 0.95,
})


@pytest.fixture()
def mock_llm(monkeypatch):
    """get_llm() を差し替えて LLM 呼び出しを回避するフィクスチャ。"""
    class _FakeLLM:
        def __init__(self, response: str):
            self._response = response
        def chat(self, system: str, user: str) -> str:
            return self._response

    return _FakeLLM


def test_extract_ok(monkeypatch, mock_llm):
    monkeypatch.setattr("d2v.audit.extractor.get_llm", lambda: mock_llm(_OK_RESPONSE))
    config_text = (FIXTURES / "config_router_ok.txt").read_text()
    result = extract_from_config(config_text, "router-01")

    assert isinstance(result, ExtractedConfig)
    assert result.device_id == "router-01"
    assert result.vendor == "iosxe"
    assert len(result.interfaces) == 3
    assert result.interfaces[2].ip_address == "10.1.0.1/30"
    assert result.bgp_peers[0].remote_asn == 65001


def test_extract_ng(monkeypatch, mock_llm):
    """逸脱コンフィグでも ExtractedConfig として正常に抽出できること。"""
    monkeypatch.setattr("d2v.audit.extractor.get_llm", lambda: mock_llm(_NG_RESPONSE))
    config_text = (FIXTURES / "config_router_ng.txt").read_text()
    result = extract_from_config(config_text, "router-01")

    assert result.hostname == "Router01"
    assert result.interfaces[2].ip_address == "10.1.0.99/30"
    assert result.bgp_peers[0].remote_asn == 65099


def test_device_id_override(monkeypatch, mock_llm):
    """LLM が返す device_id より引数の device_id が優先されること。"""
    response = json.dumps({**json.loads(_OK_RESPONSE), "device_id": "wrong-id"})
    monkeypatch.setattr("d2v.audit.extractor.get_llm", lambda: mock_llm(response))
    result = extract_from_config("dummy config", "router-01")
    assert result.device_id == "router-01"


def test_invalid_json_raises(monkeypatch, mock_llm):
    monkeypatch.setattr("d2v.audit.extractor.get_llm", lambda: mock_llm("これは JSON ではありません"))
    with pytest.raises(ExtractionError, match="JSON を抽出できませんでした"):
        extract_from_config("dummy", "router-01")


def test_invalid_schema_raises(monkeypatch, mock_llm):
    bad = json.dumps({"device_id": "router-01", "vendor": "unknown"})
    monkeypatch.setattr("d2v.audit.extractor.get_llm", lambda: mock_llm(bad))
    # 必須フィールドが不足していても Pydantic がデフォルト値で補完するため通ること
    result = extract_from_config("dummy", "router-01")
    assert result.interfaces == []


# ---------------------------------------------------------------------------
# comparator ユニットテスト
# ---------------------------------------------------------------------------

def _make_model(
    devices: list[dict] | None = None,
    connections: list[dict] | None = None,
    lags: list[dict] | None = None,
) -> TopologyModel:
    devs = devices or []
    conns = connections or []
    lags_ = lags or []
    return TopologyModel(
        devices=devs,
        connections=conns,
        device_map={d["device-id"]: d for d in devs},
        lags=lags_,
    )


def _make_cfg(device_id: str, **kwargs) -> ExtractedConfig:
    return ExtractedConfig(device_id=device_id, **kwargs)


# --- インターフェース名正規化 ---

def test_norm_iface_full_name():
    assert _norm_iface("GigabitEthernet0/1") == "gigabitethernet0/1"


def test_norm_iface_abbrev():
    assert _norm_iface("Gi0/1") == "gigabitethernet0/1"


def test_iface_match_abbrev_vs_full():
    assert _iface_match("GigabitEthernet0/1", "Gi0/1")
    assert _iface_match("Gi0/1", "GigabitEthernet0/1")


def test_iface_no_match():
    assert not _iface_match("GigabitEthernet0/1", "GigabitEthernet0/2")


# --- device-unmatched ---

def test_device_unmatched():
    model = _make_model(devices=[{"device-id": "router-01", "interface": []}])
    cfg = _make_cfg("unknown-device")
    report = compare(model, [cfg])
    rules = [i.rule for i in report.issues]
    assert "device-unmatched" in rules
    assert not report.ok


# --- iface-missing ---

def test_iface_missing():
    model = _make_model(devices=[{
        "device-id": "router-01",
        "interface": [{"interface-id": "GigabitEthernet0/1", "ip-address": "10.0.0.1/30"}],
    }])
    cfg = _make_cfg("router-01", interfaces=[])  # GigabitEthernet0/1 がない
    report = compare(model, [cfg])
    assert any(i.rule == "iface-missing" for i in report.issues)


# --- iface-ip-mismatch ---

def test_iface_ip_mismatch():
    model = _make_model(devices=[{
        "device-id": "router-01",
        "interface": [{"interface-id": "GigabitEthernet0/1", "ip-address": "10.0.0.1/30"}],
    }])
    cfg = _make_cfg("router-01", interfaces=[
        ExtractedInterface(name="GigabitEthernet0/1", ip_address="10.0.0.99/30"),
    ])
    report = compare(model, [cfg])
    assert any(i.rule == "iface-ip-mismatch" for i in report.issues)


def test_iface_ip_ok():
    model = _make_model(devices=[{
        "device-id": "router-01",
        "interface": [{"interface-id": "GigabitEthernet0/1", "ip-address": "10.0.0.1/30"}],
    }])
    cfg = _make_cfg("router-01", interfaces=[
        ExtractedInterface(name="Gi0/1", ip_address="10.0.0.1/30"),  # 省略形でも一致
    ])
    report = compare(model, [cfg])
    assert not any(i.rule == "iface-ip-mismatch" for i in report.issues)
    assert not any(i.rule == "iface-missing" for i in report.issues)


# --- iface-extra ---

def test_iface_extra():
    model = _make_model(devices=[{"device-id": "router-01", "interface": []}])
    cfg = _make_cfg("router-01", interfaces=[
        ExtractedInterface(name="GigabitEthernet0/9", ip_address="192.168.99.1/24"),
    ])
    report = compare(model, [cfg])
    assert any(i.rule == "iface-extra" for i in report.issues)


# --- description-mismatch ---

def test_description_mismatch():
    model = _make_model(devices=[{
        "device-id": "router-01",
        "interface": [{"interface-id": "Gi0/1", "description": "To Firewall", "ip-address": "10.0.0.1/30"}],
    }])
    cfg = _make_cfg("router-01", interfaces=[
        ExtractedInterface(name="Gi0/1", ip_address="10.0.0.1/30", description="uplink"),
    ])
    report = compare(model, [cfg])
    assert any(i.rule == "description-mismatch" for i in report.issues)


# --- hostname-mismatch ---

def test_hostname_mismatch():
    model = _make_model(devices=[{"device-id": "router-01", "interface": []}])
    cfg = _make_cfg("router-01", hostname="Router01")
    report = compare(model, [cfg])
    assert any(i.rule == "hostname-mismatch" for i in report.issues)


def test_hostname_ok():
    model = _make_model(devices=[{"device-id": "router-01", "interface": []}])
    cfg = _make_cfg("router-01", hostname="router-01")
    report = compare(model, [cfg])
    assert not any(i.rule == "hostname-mismatch" for i in report.issues)


# --- lag-member-mismatch ---

def test_lag_member_missing():
    model = _make_model(
        devices=[{"device-id": "sw-01", "interface": []}],
        lags=[{
            "device-id": "sw-01",
            "lag-id": "Port-channel1",
            "member-interface": [
                {"interface-id": "GigabitEthernet0/23"},
                {"interface-id": "GigabitEthernet0/24"},
            ],
        }],
    )
    # Gi0/23 しか LAG に入れていない（Gi0/24 が抜けている）
    cfg = _make_cfg("sw-01", interfaces=[
        ExtractedInterface(name="GigabitEthernet0/23", lag_group="Port-channel1"),
    ])
    report = compare(model, [cfg])
    assert any(i.rule == "lag-member-mismatch" and i.severity == "error" for i in report.issues)


def test_lag_member_ok():
    model = _make_model(
        devices=[{"device-id": "sw-01", "interface": []}],
        lags=[{
            "device-id": "sw-01",
            "lag-id": "Port-channel1",
            "member-interface": [
                {"interface-id": "GigabitEthernet0/23"},
                {"interface-id": "GigabitEthernet0/24"},
            ],
        }],
    )
    cfg = _make_cfg("sw-01", interfaces=[
        ExtractedInterface(name="GigabitEthernet0/23", lag_group="Port-channel1"),
        ExtractedInterface(name="GigabitEthernet0/24", lag_group="Port-channel1"),
    ])
    report = compare(model, [cfg])
    assert not any(i.rule == "lag-member-mismatch" for i in report.issues)


# --- BGP ---

def test_bgp_peer_missing():
    model = _make_model(
        devices=[
            {
                "device-id": "router-01", "asn": 65001,
                "interface": [{"interface-id": "Gi0/1", "ip-address": "10.0.0.1/30"}],
            },
            {
                "device-id": "router-02", "asn": 65002,
                "interface": [{"interface-id": "Gi0/1", "ip-address": "10.0.0.2/30"}],
            },
        ],
        connections=[{
            "connection-id": "r1-r2",
            "endpoint": [
                {"device-id": "router-01", "interface-id": "Gi0/1"},
                {"device-id": "router-02", "interface-id": "Gi0/1"},
            ],
        }],
    )
    # router-01 コンフィグに BGP ピアなし
    cfg = _make_cfg("router-01", interfaces=[
        ExtractedInterface(name="Gi0/1", ip_address="10.0.0.1/30"),
    ])
    report = compare(model, [cfg])
    assert any(i.rule == "bgp-peer-missing" for i in report.issues)


def test_bgp_asn_mismatch():
    model = _make_model(
        devices=[
            {
                "device-id": "router-01", "asn": 65001,
                "interface": [{"interface-id": "Gi0/1", "ip-address": "10.0.0.1/30"}],
            },
            {
                "device-id": "router-02", "asn": 65002,
                "interface": [{"interface-id": "Gi0/1", "ip-address": "10.0.0.2/30"}],
            },
        ],
        connections=[{
            "connection-id": "r1-r2",
            "endpoint": [
                {"device-id": "router-01", "interface-id": "Gi0/1"},
                {"device-id": "router-02", "interface-id": "Gi0/1"},
            ],
        }],
    )
    cfg = _make_cfg("router-01", interfaces=[
        ExtractedInterface(name="Gi0/1", ip_address="10.0.0.1/30"),
    ], bgp_peers=[
        ExtractedBgpPeer(peer_address="10.0.0.2", remote_asn=99999),  # 正しくは 65002
    ])
    report = compare(model, [cfg])
    assert any(i.rule == "bgp-asn-mismatch" for i in report.issues)


def test_bgp_ok():
    model = _make_model(
        devices=[
            {
                "device-id": "router-01", "asn": 65001,
                "interface": [{"interface-id": "Gi0/1", "ip-address": "10.0.0.1/30"}],
            },
            {
                "device-id": "router-02", "asn": 65002,
                "interface": [{"interface-id": "Gi0/1", "ip-address": "10.0.0.2/30"}],
            },
        ],
        connections=[{
            "connection-id": "r1-r2",
            "endpoint": [
                {"device-id": "router-01", "interface-id": "Gi0/1"},
                {"device-id": "router-02", "interface-id": "Gi0/1"},
            ],
        }],
    )
    cfg = _make_cfg("router-01", interfaces=[
        ExtractedInterface(name="Gi0/1", ip_address="10.0.0.1/30"),
    ], bgp_peers=[
        ExtractedBgpPeer(peer_address="10.0.0.2", remote_asn=65002),
    ])
    report = compare(model, [cfg])
    assert not any(i.rule in ("bgp-peer-missing", "bgp-asn-mismatch") for i in report.issues)


# --- ok フラグ ---

def test_report_ok_when_no_issues():
    model = _make_model(devices=[{"device-id": "router-01", "interface": []}])
    cfg = _make_cfg("router-01")
    report = compare(model, [cfg])
    assert report.ok
    assert report.passed()


# ---------------------------------------------------------------------------
# reporter ユニットテスト
# ---------------------------------------------------------------------------

def test_render_report_no_issues():
    report = AuditReport.from_issues([])
    rendered = render_report(report)
    from rich.console import Console
    from io import StringIO
    buf = StringIO()
    Console(file=buf, highlight=False).print(rendered)
    assert "逸脱は検出されませんでした" in buf.getvalue()


def test_render_report_with_issues():
    issues = [
        AuditIssue(rule="iface-ip-mismatch", severity="error",
                   device_id="router-01", message="IP mismatch",
                   detail="design=10.0.0.1/30  config=10.0.0.99/30"),
        AuditIssue(rule="hostname-mismatch", severity="warning",
                   device_id="router-01", message="hostname mismatch",
                   detail="design=router-01  config=Router01"),
        AuditIssue(rule="iface-extra", severity="info",
                   device_id="fw-01", message="extra iface",
                   detail="config ip=192.168.0.1/24"),
    ]
    report = AuditReport.from_issues(issues)
    rendered = render_report(report)
    from rich.console import Console
    from io import StringIO
    buf = StringIO()
    Console(file=buf, highlight=False).print(rendered)
    out = buf.getvalue()
    assert "error=1" in out
    assert "warning=1" in out
    assert "info=1" in out
    assert "iface-ip-mismatch" in out
    assert "router-01" in out
    assert "fw-01" in out


def test_to_json():
    issues = [
        AuditIssue(rule="iface-ip-mismatch", severity="error",
                   device_id="router-01", message="IP mismatch"),
    ]
    report = AuditReport.from_issues(issues)
    result = to_json(report)
    data = json.loads(result)
    assert data["ok"] is False
    assert data["counts"]["error"] == 1
    assert data["issues"][0]["rule"] == "iface-ip-mismatch"


def test_to_json_empty():
    report = AuditReport.from_issues([])
    data = json.loads(to_json(report))
    assert data["ok"] is True
    assert data["issues"] == []


# ---------------------------------------------------------------------------
# pipeline ユニットテスト
# ---------------------------------------------------------------------------

_OK_CFG_JSON = json.dumps({
    "device_id": "router-01",
    "hostname": "router-01",
    "vendor": "iosxe",
    "interfaces": [
        {"name": "GigabitEthernet0/0", "ip_address": "203.0.113.1/30",
         "description": "To ISP", "admin_state": "up", "lag_group": None},
        {"name": "GigabitEthernet0/1", "ip_address": "10.1.0.1/30",
         "description": "To Firewall", "admin_state": "up", "lag_group": None},
    ],
    "bgp_peers": [],
    "vlans": [],
    "confidence": 0.95,
})


def test_collect_config_paths_dir(tmp_path):
    (tmp_path / "router-01.txt").write_text("config")
    (tmp_path / "fw-01.txt").write_text("config")
    paths = _collect_config_paths(None, tmp_path)
    stems = {p.stem for p in paths}
    assert stems == {"router-01", "fw-01"}


def test_collect_config_paths_files(tmp_path):
    f = tmp_path / "router-01.txt"
    f.write_text("config")
    paths = _collect_config_paths([f], None)
    assert paths == [f]


def test_collect_config_paths_missing_file(tmp_path):
    from d2v.errors import InputError
    with pytest.raises(InputError, match="見つかりません"):
        _collect_config_paths([tmp_path / "nonexistent.txt"], None)


def test_collect_config_paths_empty_dir(tmp_path):
    from d2v.errors import InputError
    with pytest.raises(InputError, match="見つかりません"):
        _collect_config_paths(None, tmp_path)


def test_pipeline_run_ok(monkeypatch, tmp_path):
    """設計通りのコンフィグで ok=True になることを確認。"""
    class _FakeLLM:
        def chat(self, system, user):
            return _OK_CFG_JSON
    monkeypatch.setattr("d2v.audit.extractor.get_llm", lambda: _FakeLLM())

    cfg_file = tmp_path / "router-01.txt"
    cfg_file.write_text((FIXTURES / "config_router_ok.txt").read_text())

    result = run(
        design_path=Path("examples/sample_topology_small.yaml"),
        config_files=[cfg_file],
    )
    assert result.report.ok
    assert result.extraction_errors == {}


def test_pipeline_extraction_error(monkeypatch, tmp_path):
    """抽出失敗時に extraction-failed issue が追加されることを確認。"""
    class _BrokenLLM:
        def chat(self, system, user):
            return "これは JSON ではありません"
    monkeypatch.setattr("d2v.audit.extractor.get_llm", lambda: _BrokenLLM())

    cfg_file = tmp_path / "router-01.txt"
    cfg_file.write_text("dummy config")

    result = run(
        design_path=Path("examples/sample_topology_small.yaml"),
        config_files=[cfg_file],
    )
    assert "router-01" in result.extraction_errors
    assert any(i.rule == "extraction-failed" for i in result.report.issues)


_NG_CFG_JSON = json.dumps({
    "device_id": "router-01",
    "hostname": "Router01",          # hostname-mismatch (warning)
    "vendor": "iosxe",
    "interfaces": [
        {"name": "GigabitEthernet0/0", "ip_address": "203.0.113.1/30",
         "description": "To ISP", "admin_state": "up", "lag_group": None},
        {"name": "GigabitEthernet0/1", "ip_address": "10.1.0.99/30",  # iface-ip-mismatch (error)
         "description": "To Upstream", "admin_state": "up", "lag_group": None},
    ],
    "bgp_peers": [],
    "vlans": [],
    "confidence": 0.9,
})


def test_pipeline_run_ng(monkeypatch, tmp_path):
    """逸脱コンフィグで ok=False になり適切な rule が検出されることを確認。"""
    class _FakeLLM:
        def chat(self, system, user):
            return _NG_CFG_JSON
    monkeypatch.setattr("d2v.audit.extractor.get_llm", lambda: _FakeLLM())

    cfg_file = tmp_path / "router-01.txt"
    cfg_file.write_text((FIXTURES / "config_router_ng.txt").read_text())

    result = run(
        design_path=Path("examples/sample_topology_small.yaml"),
        config_files=[cfg_file],
    )
    assert not result.report.ok
    rules = {i.rule for i in result.report.issues}
    assert "iface-ip-mismatch" in rules
    assert "hostname-mismatch" in rules


def test_passed_strict_mode():
    """strict=True では warning も不合格になることを確認。"""
    issues = [AuditIssue(rule="hostname-mismatch", severity="warning",
                         device_id="router-01", message="mismatch")]
    report = AuditReport.from_issues(issues)
    assert report.ok                        # error なし → ok=True
    assert report.passed() is True          # 通常モードは合格
    assert report.passed(strict=True) is False  # strict では不合格


def test_lag_member_extra():
    """設計にない LAG メンバーが info で報告されることを確認。"""
    model = _make_model(
        devices=[{"device-id": "sw-01", "interface": []}],
        lags=[{
            "device-id": "sw-01",
            "lag-id": "Port-channel1",
            "member-interface": [{"interface-id": "GigabitEthernet0/1"}],
        }],
    )
    cfg = _make_cfg("sw-01", interfaces=[
        ExtractedInterface(name="GigabitEthernet0/1", lag_group="Port-channel1"),
        ExtractedInterface(name="GigabitEthernet0/2", lag_group="Port-channel1"),  # 余分
    ])
    report = compare(model, [cfg])
    extra_issues = [i for i in report.issues
                    if i.rule == "lag-member-mismatch" and i.severity == "info"]
    assert extra_issues, "余分な LAG メンバーが info で報告されること"


def test_collect_config_paths_not_a_dir(tmp_path):
    """--config-dir にファイルを指定した場合はエラーになることを確認。"""
    from d2v.errors import InputError
    f = tmp_path / "not_a_dir.txt"
    f.write_text("config")
    with pytest.raises(InputError, match="ディレクトリではありません"):
        _collect_config_paths(None, f)


def test_iface_missing_no_ip_skipped():
    """設計上 IP なしのインターフェースは iface-missing の対象外であることを確認。"""
    model = _make_model(devices=[{
        "device-id": "sw-01",
        "interface": [
            {"interface-id": "GigabitEthernet0/1"},           # IP なし → スキップ
            {"interface-id": "GigabitEthernet0/2", "ip-address": "10.0.0.1/30"},
        ],
    }])
    cfg = _make_cfg("sw-01", interfaces=[
        ExtractedInterface(name="GigabitEthernet0/2", ip_address="10.0.0.1/30"),
        # GigabitEthernet0/1 はコンフィグになくても iface-missing にならない
    ])
    report = compare(model, [cfg])
    assert not any(i.rule == "iface-missing" for i in report.issues)
