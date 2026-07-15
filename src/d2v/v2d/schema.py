"""v2d 中間表現: 画像解析（vision LLM / OCR）の結果を格納するスキーマ。

vision LLM でも OCR 方式でも、抽出結果はこの ``ExtractedDiagram`` に揃える。
後段（YAML 変換・評価）はこの中間表現のみに依存するため、解析手段を差し替えても
下流を変更せずに済む。各要素は ``confidence``（0.0〜1.0）を持ち、読み取りの
確からしさを表す。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# iida-network-model の device-type に揃えたデバイス種別
DeviceType = Literal[
    "router",
    "switch",
    "server",
    "firewall",
    "host",
    "load-balancer",
    "unknown",
]

# エッジ（線）の見た目。破線は境界リンクや外部参照を示すことが多い。
EdgeStyle = Literal["solid", "dashed", "unknown"]


class ExtractedNode(BaseModel):
    """検出したノード（デバイス）。"""

    id: str = Field(description="図内で一意な仮 ID（クラスタ・エッジからの参照キー）")
    hostname: str | None = Field(default=None, description="ラベルから読み取ったホスト名")
    device_type: DeviceType = Field(default="unknown", description="アイコン/形状/語からの推定種別")
    zone: str | None = Field(default=None, description="所属ゾーン名（クラスタ由来）")
    loopback: str | None = Field(default=None, description="管理 IP / loopback（読み取れれば）")
    raw_label: str | None = Field(default=None, description="認識した生ラベル（改行含む）")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractedEdge(BaseModel):
    """検出したエッジ（物理リンク）。"""

    source: str = Field(description="始点ノードの id")
    target: str = Field(description="終点ノードの id")
    source_port: str | None = Field(default=None, description="始点側インターフェース名")
    target_port: str | None = Field(default=None, description="終点側インターフェース名")
    segment: str | None = Field(default=None, description="中央ラベルのセグメント IP/プレフィックス")
    style: EdgeStyle = Field(default="solid")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractedCluster(BaseModel):
    """検出したクラスタ（ゾーン枠）。"""

    id: str = Field(description="図内で一意な仮 ID")
    label: str | None = Field(default=None, description="ゾーン名（枠の見出し）")
    members: list[str] = Field(default_factory=list, description="所属ノードの id 一覧")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractedDiagram(BaseModel):
    """1 枚のネットワーク図から抽出した構造の中間表現。"""

    nodes: list[ExtractedNode] = Field(default_factory=list)
    edges: list[ExtractedEdge] = Field(default_factory=list)
    clusters: list[ExtractedCluster] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description="未割当テキストや解析上の所見（曖昧点・読み取れなかった箇所など）",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="図全体の総合確信度")
