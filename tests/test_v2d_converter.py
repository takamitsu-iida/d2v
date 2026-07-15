"""v2d 中間表現スキーマと変換器のテスト。"""

from __future__ import annotations

import yaml

from d2v.v2d import converter
from d2v.v2d.schema import (
    ExtractedCluster,
    ExtractedDiagram,
    ExtractedEdge,
    ExtractedNode,
)


def _sample_diagram() -> ExtractedDiagram:
    return ExtractedDiagram(
        nodes=[
            ExtractedNode(id="n1", hostname="router-01", device_type="router",
                          loopback="10.0.0.1/32"),
            ExtractedNode(id="n2", hostname="fw-01", device_type="firewall"),
            ExtractedNode(id="n3", hostname="core-sw-01", device_type="switch"),
        ],
        edges=[
            ExtractedEdge(source="n1", target="n2", source_port="Gi0/1",
                          target_port="Gi0/0", segment="10.1.0.0/30"),
            ExtractedEdge(source="n2", target="n3", source_port="Gi0/1",
                          target_port="Gi1/0/1", segment="10.1.1.0/30"),
        ],
        clusters=[
            ExtractedCluster(id="c1", label="wan-edge", members=["n1", "n2"]),
            ExtractedCluster(id="c2", label="core", members=["n3"]),
        ],
    )


def test_schema_defaults():
    node = ExtractedNode(id="x")
    assert node.device_type == "unknown"
    assert node.confidence == 1.0
    assert node.hostname is None


def test_build_model_counts():
    model = converter.build_model(_sample_diagram())
    devices = model["network-model"]["physical-layer"]["device"]
    conns = model["network-model"]["physical-layer"]["physical-connection"]
    subnets = model["network-model"]["layer3-layer"]["ip-subnet"]
    assert len(devices) == 3
    assert len(conns) == 2
    assert len(subnets) == 2


def test_zone_from_cluster():
    model = converter.build_model(_sample_diagram())
    devices = {d["device-id"]: d for d in model["network-model"]["physical-layer"]["device"]}
    assert devices["router-01"]["zone"] == "wan-edge"
    assert devices["core-sw-01"]["zone"] == "core"


def test_interfaces_from_edges():
    model = converter.build_model(_sample_diagram())
    devices = {d["device-id"]: d for d in model["network-model"]["physical-layer"]["device"]}
    # fw-01 は 2 本のエッジで 2 つのインターフェースを持つ
    fw_ifaces = {i["interface-id"] for i in devices["fw-01"]["interface"]}
    assert fw_ifaces == {"Gi0/0", "Gi0/1"}


def test_to_yaml_is_parseable():
    text = converter.to_yaml(_sample_diagram())
    data = yaml.safe_load(text)
    assert "network-model" in data
    assert data["network-model"]["physical-layer"]["device"][0]["device-id"] == "router-01"


def test_synthesized_interface_when_port_missing():
    diagram = ExtractedDiagram(
        nodes=[ExtractedNode(id="a", hostname="a"), ExtractedNode(id="b", hostname="b")],
        edges=[ExtractedEdge(source="a", target="b")],  # ポート名なし
    )
    model = converter.build_model(diagram)
    devices = {d["device-id"]: d for d in model["network-model"]["physical-layer"]["device"]}
    assert devices["a"]["interface"][0]["interface-id"] == "if1"


def test_duplicate_hostname_gets_unique_device_id():
    diagram = ExtractedDiagram(
        nodes=[
            ExtractedNode(id="a", hostname="sw"),
            ExtractedNode(id="b", hostname="sw"),
        ],
    )
    model = converter.build_model(diagram)
    ids = [d["device-id"] for d in model["network-model"]["physical-layer"]["device"]]
    assert len(set(ids)) == 2  # 衝突回避で一意化される
