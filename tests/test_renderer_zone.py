"""renderer.inject_zone_constraints（2段階レイアウトの LLM 後処理注入）のテスト。

DOT を実際に ``dot`` でレンダリングする箇所は無く、文字列変換のみを検証する。
"""

from __future__ import annotations

from d2v import renderer


_LLM_DOT = """digraph G {
  rankdir=TB;
  node [shape=box];
  subgraph cluster_core {
    label="Core"; d2vzone="core";
    "core-sw-01" [d2vtype=switch];
  }
  subgraph cluster_dmz {
    label="DMZ"; d2vzone="dmz";
    "fw-01" [d2vtype=firewall];
  }
  subgraph cluster_srv {
    label="Server"; d2vzone="server";
    "web-01" [d2vtype=server];
  }
  "core-sw-01" -> "fw-01" [dir=none];
  "core-sw-01" -> "web-01" [dir=none];
}"""


def test_injects_anchors_and_constraints() -> None:
    out = renderer.inject_zone_constraints(_LLM_DOT)

    # 3 ゾーン分の不可視アンカーが挿入される。
    for zone in ("core", "dmz", "server"):
        assert f'"__za_{zone}"' in out
    # 段制約マーカーと rank=same が入る。
    assert "zone tier constraints" in out
    assert "rank=same" in out
    # core はハブなので単独段、dmz/server は同段になる。
    assert '{ rank=same; "__za_core"; }' in out
    assert '{ rank=same; "__za_dmz"; "__za_server"; }' in out


def test_injection_is_idempotent() -> None:
    once = renderer.inject_zone_constraints(_LLM_DOT)
    twice = renderer.inject_zone_constraints(once)
    assert once == twice


def test_no_d2vzone_is_passthrough() -> None:
    plain = """digraph G {
  "a" [d2vtype=switch];
  "b" [d2vtype=server];
  "a" -> "b";
}"""
    assert renderer.inject_zone_constraints(plain) == plain


def test_single_zone_is_passthrough() -> None:
    single = """digraph G {
  subgraph cluster_core {
    label="Core"; d2vzone="core";
    "core-sw-01" [d2vtype=switch];
    "core-sw-02" [d2vtype=switch];
  }
  "core-sw-01" -> "core-sw-02";
}"""
    assert renderer.inject_zone_constraints(single) == single


def test_anchor_placed_inside_cluster() -> None:
    out = renderer.inject_zone_constraints(_LLM_DOT)
    # core のアンカー宣言は cluster_core の閉じ括弧より前に来る。
    core_anchor = out.index('"__za_core"')
    # cluster_core ブロックの閉じ括弧位置（core-sw-01 の直後の "}"）
    core_body = out.index('"core-sw-01" [d2vtype=switch];')
    close_after_core = out.index("}", core_body)
    assert core_anchor < close_after_core
