"""edit_assist の カーソル行 → 注目ノード解決ロジックのテスト。"""

from __future__ import annotations

from pathlib import Path

from d2v import edit_assist

_YAML = Path("examples/sample_topology_small.yaml").read_text(encoding="utf-8")
_LINES = _YAML.splitlines()


def _line_of(needle: str) -> int:
    """needle を含む最初の行番号（1 始まり）を返す。"""
    for i, ln in enumerate(_LINES):
        if needle in ln:
            return i + 1
    raise AssertionError(f"not found: {needle}")


def test_resolve_inside_device_block():
    line = _line_of('device-id: "core-sw-01"')
    res = edit_assist.resolve_focus(_YAML, line + 1)  # interface 行あたり
    assert res.focus_ids == ["core-sw-01"]
    assert res.context == "device"
    assert res.device_lines["core-sw-01"] == _line_of('device-id: "core-sw-01"')


def test_resolve_inside_connection_returns_both_endpoints():
    line = _line_of("fw-01__core-sw-01")
    res = edit_assist.resolve_focus(_YAML, line + 2)
    assert res.context == "connection"
    assert set(res.focus_ids) == {"fw-01", "core-sw-01"}


def test_resolve_fallback_to_nearest_above():
    # 末尾（最後のブロックより下）でも直前のブロックにフォールバックする
    res = edit_assist.resolve_focus(_YAML, len(_LINES) + 5)
    assert res.focus_ids  # 空でない
    assert res.context in ("device", "connection")


def test_resolve_broken_yaml_returns_empty():
    res = edit_assist.resolve_focus("network-model: [unclosed\n", 1)
    assert res.focus_ids == []
    assert res.context == "none"
    assert res.device_lines == {}


def test_device_lines_cover_all_devices():
    _, device_lines = edit_assist._parse_spans(_YAML)
    for did in ("router-01", "fw-01", "core-sw-01", "web-server-01", "pc-01"):
        assert did in device_lines
        assert device_lines[did] >= 1


def test_symbol_lines_maps_devices_and_connections():
    sym = edit_assist.symbol_lines(_YAML)
    # device-id
    assert sym["fw-01"] == _line_of('device-id: "fw-01"')
    # connection-id
    assert sym["router-01__fw-01"] == _line_of('connection-id: "router-01__fw-01"')


def test_symbol_lines_broken_yaml_returns_empty():
    assert edit_assist.symbol_lines("network-model: [unclosed\n") == {}
