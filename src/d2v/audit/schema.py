"""audit データモデル: LLM 抽出結果と検査レポートの Pydantic スキーマ。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AdminState = Literal["up", "down", "unknown"]
VendorType = Literal["ios", "iosxe", "iosxr", "junos", "eos", "unknown"]

SEVERITIES: tuple[str, ...] = ("error", "warning", "info")


# ---------------------------------------------------------------------------
# LLM 抽出結果モデル
# ---------------------------------------------------------------------------


class ExtractedInterface(BaseModel):
    """コンフィグから抽出した 1 インターフェースの情報。"""

    name: str = Field(description="インターフェース名（例: GigabitEthernet0/1）")
    ip_address: str | None = Field(default=None, description="IP アドレス（CIDR 表記, 例: 10.1.0.1/30）")
    description: str | None = Field(default=None, description="description コマンドの文字列")
    admin_state: AdminState = Field(default="unknown", description="shutdown なら down")
    lag_group: str | None = Field(default=None, description="所属 Port-channel / LAG 名")


class ExtractedBgpPeer(BaseModel):
    """コンフィグから抽出した BGP ネイバー 1 件。"""

    peer_address: str = Field(description="ネイバー IP アドレス")
    remote_asn: int | None = Field(default=None, description="remote-as 番号")
    description: str | None = Field(default=None)


class ExtractedConfig(BaseModel):
    """1 台分のコンフィグから LLM が抽出した構造化情報。"""

    device_id: str = Field(description="ファイル名 stem で確定した device-id")
    hostname: str | None = Field(default=None, description="hostname コマンドで設定された名前")
    vendor: VendorType = Field(default="unknown", description="コンフィグ形式から推定したベンダー")
    interfaces: list[ExtractedInterface] = Field(default_factory=list)
    bgp_peers: list[ExtractedBgpPeer] = Field(default_factory=list)
    vlans: list[int] = Field(default_factory=list, description="設定されている VLAN ID 一覧")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="抽出全体の確信度")


# ---------------------------------------------------------------------------
# 検査レポートモデル
# ---------------------------------------------------------------------------


class AuditIssue(BaseModel):
    """設計との逸脱 1 件。"""

    rule: str = Field(description="ルール ID（例: iface-ip-mismatch）")
    severity: str = Field(description="error | warning | info")
    device_id: str = Field(description="対象の device-id")
    message: str = Field(description="簡潔な説明")
    detail: str = Field(default="", description="設計値・実際値など補足情報")


class AuditReport(BaseModel):
    """全デバイスの検査結果まとめ。"""

    ok: bool = Field(description="error が 0 件なら True")
    counts: dict[str, int] = Field(description='{"error": n, "warning": m, "info": k}')
    issues: list[AuditIssue]

    @classmethod
    def from_issues(cls, issues: list[AuditIssue]) -> "AuditReport":
        counts: dict[str, int] = {sev: 0 for sev in SEVERITIES}
        for issue in issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        ok = counts.get("error", 0) == 0
        return cls(ok=ok, counts=counts, issues=list(issues))

    def passed(self, *, strict: bool = False) -> bool:
        """error があれば不合格。strict=True では warning も不合格扱い。"""
        if self.counts.get("error", 0) > 0:
            return False
        if strict and self.counts.get("warning", 0) > 0:
            return False
        return True
