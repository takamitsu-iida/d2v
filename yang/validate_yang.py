#!/usr/bin/env python3
"""
Validate YAML topology files against iida-network-model YANG schema.

Checks:
  - List key presence and uniqueness
  - Enum values (device-type, role, port-type, l2-mode, lag-mode, protocol …)
  - Type constraints (range, pattern, ip-address, ip-prefix)
  - leafref consistency across layers
  - YANG 'when' conditions (e.g. access-vlan mandatory when l2-mode=access)
  - mandatory leaves (bgp/local-asn, fhr/action …)
"""

from __future__ import annotations
import ipaddress
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# ── YANG enumeration sets ─────────────────────────────────────────────────────

DEVICE_TYPE  = {"router","switch","server","firewall","host","load-balancer"}
DEVICE_ROLE  = {"spine","leaf","core","distribution","access","border","oob","other"}
PORT_TYPE    = {"ethernet","ethernet-copper","ethernet-fiber","serial","other"}
L2_MODE      = {"access","trunk","layer3"}
LAG_MODE     = {"static","lacp-active","lacp-passive"}
LACP_RATE    = {"slow","fast"}
FHR_PROTO    = {"vrrp","hsrp","glbp"}
RT_ACTION    = {"permit","deny"}
BGP_AF       = {"ipv4-unicast","ipv6-unicast","l2vpn-evpn"}
OSPF_AREA_T  = {"standard","stub","nssa"}
REDIST_SRC   = {"connected","static","ospf","bgp","rip"}
REDIST_DST   = {"ospf","bgp"}

RE_DEVICE_ID = re.compile(r'^[a-zA-Z0-9_-]+$')
RE_IF_ID     = re.compile(r'^[a-zA-Z0-9_./-]+$')


# ── helper ────────────────────────────────────────────────────────────────────

def _list(obj: Any) -> list:
    """Return obj as a list, treating None as []."""
    return obj if isinstance(obj, list) else []


def _dict(obj: Any) -> dict:
    return obj if isinstance(obj, dict) else {}


# ── Validator ─────────────────────────────────────────────────────────────────

class Validator:
    def __init__(self, data: dict, filename: str) -> None:
        self.data     = data
        self.filename = filename
        self.errors:   list[str] = []
        self.warnings: list[str] = []

        # collected keys for leafref resolution
        self.device_ids:       set[str]       = set()
        self.iface_ids:        dict[str, set[str]] = {}   # device_id → {if_id}
        self.vlan_ids:         set[int]        = set()
        self.subnet_ids:       set[str]        = set()
        self.lag_ids:          dict[str, set[str]] = {}   # device_id → {lag_id}
        self.route_policy_names: set[str]      = set()

    # ── reporting ──────────────────────────────────────────────────────────────

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    # ── type checkers ──────────────────────────────────────────────────────────

    def chk_ip_prefix(self, val: Any, path: str) -> None:
        if not isinstance(val, str):
            self.err(f"{path}: expected ip-prefix string, got {type(val).__name__} {val!r}")
            return
        try:
            ipaddress.ip_interface(val)
        except ValueError:
            self.err(f"{path}: invalid ip-prefix {val!r}")

    def chk_ip_address(self, val: Any, path: str) -> None:
        if not isinstance(val, str):
            self.err(f"{path}: expected ip-address string, got {type(val).__name__} {val!r}")
            return
        try:
            ipaddress.ip_address(val)
        except ValueError:
            self.err(f"{path}: invalid ip-address {val!r}")

    def chk_enum(self, val: Any, allowed: set, path: str) -> None:
        if val not in allowed:
            self.err(f"{path}: invalid enum {val!r}, expected one of {sorted(allowed)}")

    def chk_pattern(self, val: Any, pat: re.Pattern, path: str) -> None:
        if not isinstance(val, str) or not pat.match(val):
            self.err(f"{path}: {val!r} does not match pattern {pat.pattern}")

    def chk_uint(self, val: Any, lo: int, hi: int, path: str) -> None:
        if not isinstance(val, int) or isinstance(val, bool):
            self.err(f"{path}: expected integer, got {type(val).__name__} {val!r}")
            return
        if not (lo <= val <= hi):
            self.err(f"{path}: value {val} out of range [{lo}..{hi}]")

    # ── leafref helpers ────────────────────────────────────────────────────────

    def chk_device_ref(self, did: Any, path: str) -> bool:
        if did not in self.device_ids:
            self.err(f"{path}: leafref to unknown device-id {did!r}")
            return False
        return True

    def chk_iface_ref(self, did: str, ifid: Any, path: str) -> None:
        if did in self.device_ids and ifid not in self.iface_ids.get(did, set()):
            self.err(f"{path}: leafref to unknown interface-id {ifid!r} on device {did!r}")

    def chk_vlan_ref(self, vid: Any, path: str) -> None:
        if vid not in self.vlan_ids:
            self.err(f"{path}: leafref to unknown vlan-id {vid}")

    def chk_subnet_ref(self, sid: Any, path: str) -> None:
        if sid not in self.subnet_ids:
            self.err(f"{path}: leafref to unknown subnet-id {sid!r}")

    def chk_policy_ref(self, name: Any, path: str) -> None:
        if name not in self.route_policy_names:
            self.err(f"{path}: leafref to unknown route-policy {name!r}")

    # ── Phase 0: collect keys ─────────────────────────────────────────────────

    def _collect_keys(self) -> None:
        nm   = _dict(self.data.get("network-model"))
        phys = _dict(nm.get("physical-layer"))
        for dev in _list(phys.get("device")):
            did = dev.get("device-id")
            if did:
                self.device_ids.add(did)
                self.iface_ids[did] = {
                    i["interface-id"]
                    for i in _list(dev.get("interface"))
                    if i.get("interface-id")
                }

        l2 = _dict(nm.get("layer2-layer"))
        for vlan in _list(l2.get("vlan")):
            vid = vlan.get("vlan-id")
            if vid is not None:
                self.vlan_ids.add(vid)
        for lag in _list(l2.get("link-aggregation")):
            did = lag.get("device-id")
            lid = lag.get("lag-id")
            if did and lid:
                self.lag_ids.setdefault(did, set()).add(lid)

        l3 = _dict(nm.get("layer3-layer"))
        for sub in _list(l3.get("ip-subnet")):
            sid = sub.get("subnet-id")
            if sid:
                self.subnet_ids.add(sid)
        for rp in _list(l3.get("route-policy")):
            if rp.get("name"):
                self.route_policy_names.add(rp["name"])

    # ── physical-layer ────────────────────────────────────────────────────────

    def _validate_physical(self, phys: dict) -> None:
        seen_dids: set[str] = set()
        for i, dev in enumerate(_list(phys.get("device"))):
            p = f"physical-layer.device[{i}]"
            did = dev.get("device-id")
            if not did:
                self.err(f"{p}: missing required key 'device-id'")
            else:
                self.chk_pattern(did, RE_DEVICE_ID, f"{p}.device-id")
                if did in seen_dids:
                    self.err(f"{p}: duplicate device-id {did!r}")
                seen_dids.add(did)
                p = f"physical-layer.device[{did}]"

            if "device-type" in dev:
                self.chk_enum(dev["device-type"], DEVICE_TYPE, f"{p}.device-type")
            if "role" in dev:
                self.chk_enum(dev["role"], DEVICE_ROLE, f"{p}.role")
            if "loopback" in dev:
                self.chk_ip_prefix(dev["loopback"], f"{p}.loopback")
            if "asn" in dev:
                self.chk_uint(dev["asn"], 1, 4_294_967_295, f"{p}.asn")
            loc = _dict(dev.get("location"))
            if "rack-unit" in loc:
                self.chk_uint(loc["rack-unit"], 1, 52, f"{p}.location.rack-unit")

            seen_ifids: set[str] = set()
            for j, iface in enumerate(_list(dev.get("interface"))):
                ip = f"{p}.interface[{j}]"
                ifid = iface.get("interface-id")
                if not ifid:
                    self.err(f"{ip}: missing required key 'interface-id'")
                else:
                    self.chk_pattern(ifid, RE_IF_ID, f"{ip}.interface-id")
                    if ifid in seen_ifids:
                        self.err(f"{ip}: duplicate interface-id {ifid!r}")
                    seen_ifids.add(ifid)
                    ip = f"{p}.interface[{ifid}]"
                if "port-type" in iface:
                    self.chk_enum(iface["port-type"], PORT_TYPE, f"{ip}.port-type")
                if "port-speed-gbps" in iface:
                    self.chk_uint(iface["port-speed-gbps"], 0, 65_535, f"{ip}.port-speed-gbps")
                if "ip-address" in iface:
                    self.chk_ip_prefix(iface["ip-address"], f"{ip}.ip-address")

        seen_cids: set[str] = set()
        for i, conn in enumerate(_list(phys.get("physical-connection"))):
            p = f"physical-layer.physical-connection[{i}]"
            cid = conn.get("connection-id")
            if not cid:
                self.err(f"{p}: missing required key 'connection-id'")
            else:
                if cid in seen_cids:
                    self.err(f"{p}: duplicate connection-id {cid!r}")
                seen_cids.add(cid)
                p = f"physical-layer.physical-connection[{cid}]"

            eps = _list(conn.get("endpoint"))
            if len(eps) != 2:
                self.err(f"{p}: endpoint list must have exactly 2 entries (has {len(eps)})")

            seen_ep_dids: set[str] = set()
            for ep in eps:
                ep_did   = ep.get("device-id")
                ep_ifid  = ep.get("interface-id")
                ep_lagref = ep.get("lag-ref")
                if not ep_did:
                    self.err(f"{p}.endpoint: missing device-id")
                    continue
                if ep_did in seen_ep_dids:
                    self.err(f"{p}.endpoint[{ep_did}]: duplicate device-id")
                seen_ep_dids.add(ep_did)
                if self.chk_device_ref(ep_did, f"{p}.endpoint[{ep_did}].device-id"):
                    if ep_ifid:
                        self.chk_iface_ref(ep_did, ep_ifid,
                                           f"{p}.endpoint[{ep_did}].interface-id")
                    if ep_lagref and ep_lagref not in self.lag_ids.get(ep_did, set()):
                        self.warn(f"{p}.endpoint[{ep_did}].lag-ref: "
                                  f"'{ep_lagref}' not found in link-aggregation for device '{ep_did}'")

    # ── layer2-layer ──────────────────────────────────────────────────────────

    def _validate_l2(self, l2: dict) -> None:
        seen_vids: set[int] = set()
        for i, vlan in enumerate(_list(l2.get("vlan"))):
            p = f"layer2-layer.vlan[{i}]"
            vid = vlan.get("vlan-id")
            if vid is None:
                self.err(f"{p}: missing required key 'vlan-id'")
            else:
                self.chk_uint(vid, 1, 4094, f"{p}.vlan-id")
                if vid in seen_vids:
                    self.err(f"{p}: duplicate vlan-id {vid}")
                seen_vids.add(vid)

        seen_l2if: set[tuple] = set()
        for i, l2if in enumerate(_list(l2.get("layer2-interface-config"))):
            p = f"layer2-layer.layer2-interface-config[{i}]"
            did  = l2if.get("device-id")
            ifid = l2if.get("interface-id")
            if not did or not ifid:
                self.err(f"{p}: missing required key 'device-id' or 'interface-id'")
            else:
                key = (did, ifid)
                if key in seen_l2if:
                    self.err(f"{p}: duplicate key ({did}, {ifid})")
                seen_l2if.add(key)
                p = f"layer2-layer.layer2-interface-config[{did},{ifid}]"
                if self.chk_device_ref(did, f"{p}.device-id"):
                    self.chk_iface_ref(did, ifid, f"{p}.interface-id")

            mode = l2if.get("l2-mode")
            if mode:
                self.chk_enum(mode, L2_MODE, f"{p}.l2-mode")
            if mode == "access" and "access-vlan" not in l2if:
                self.err(f"{p}: 'access-vlan' is mandatory when l2-mode = 'access'")
            if "access-vlan" in l2if:
                self.chk_vlan_ref(l2if["access-vlan"], f"{p}.access-vlan")
            for tv in _list(l2if.get("trunk-vlans")):
                self.chk_vlan_ref(tv.get("vlan-id"), f"{p}.trunk-vlans")
            if "native-vlan" in l2if:
                self.chk_vlan_ref(l2if["native-vlan"], f"{p}.native-vlan")

        seen_lag: set[tuple] = set()
        for i, lag in enumerate(_list(l2.get("link-aggregation"))):
            p = f"layer2-layer.link-aggregation[{i}]"
            did = lag.get("device-id")
            lid = lag.get("lag-id")
            if not did or not lid:
                self.err(f"{p}: missing required key 'device-id' or 'lag-id'")
            else:
                key = (did, lid)
                if key in seen_lag:
                    self.err(f"{p}: duplicate key ({did}, {lid})")
                seen_lag.add(key)
                p = f"layer2-layer.link-aggregation[{did},{lid}]"
                self.chk_device_ref(did, f"{p}.device-id")
                self.chk_pattern(lid, RE_IF_ID, f"{p}.lag-id")

            if "mode" in lag:
                self.chk_enum(lag["mode"], LAG_MODE, f"{p}.mode")
            if "lacp-rate" in lag:
                self.chk_enum(lag["lacp-rate"], LACP_RATE, f"{p}.lacp-rate")
            if "min-links" in lag:
                self.chk_uint(lag["min-links"], 0, 65_535, f"{p}.min-links")
            for j, mi in enumerate(_list(lag.get("member-interface"))):
                mi_ifid = mi.get("interface-id")
                if did and mi_ifid:
                    self.chk_iface_ref(did, mi_ifid, f"{p}.member-interface[{j}].interface-id")
            mlag = _dict(lag.get("mlag"))
            if mlag.get("enabled"):
                peer_did = mlag.get("peer-device-id")
                if peer_did:
                    self.chk_device_ref(peer_did, f"{p}.mlag.peer-device-id")

    # ── layer3-layer ──────────────────────────────────────────────────────────

    def _validate_l3(self, l3: dict) -> None:
        # ip-subnet
        seen_sids: set[str] = set()
        for i, sub in enumerate(_list(l3.get("ip-subnet"))):
            p = f"layer3-layer.ip-subnet[{i}]"
            sid = sub.get("subnet-id")
            if not sid:
                self.err(f"{p}: missing required key 'subnet-id'")
            else:
                if sid in seen_sids:
                    self.err(f"{p}: duplicate subnet-id {sid!r}")
                seen_sids.add(sid)
                p = f"layer3-layer.ip-subnet[{sid}]"
            if "prefix" in sub:
                self.chk_ip_prefix(sub["prefix"], f"{p}.prefix")
            if "associated-vlan-id" in sub:
                self.chk_vlan_ref(sub["associated-vlan-id"], f"{p}.associated-vlan-id")

        # layer3-interface-config
        seen_l3if: set[tuple] = set()
        for i, l3if in enumerate(_list(l3.get("layer3-interface-config"))):
            p = f"layer3-layer.layer3-interface-config[{i}]"
            did  = l3if.get("device-id")
            ifid = l3if.get("interface-id")
            if not did or not ifid:
                self.err(f"{p}: missing required key 'device-id' or 'interface-id'")
            else:
                key = (did, ifid)
                if key in seen_l3if:
                    self.err(f"{p}: duplicate key ({did}, {ifid})")
                seen_l3if.add(key)
                p = f"layer3-layer.layer3-interface-config[{did},{ifid}]"
                if self.chk_device_ref(did, f"{p}.device-id"):
                    self.chk_iface_ref(did, ifid, f"{p}.interface-id")
            for j, addr in enumerate(_list(l3if.get("addresses"))):
                ap = f"{p}.addresses[{j}]"
                if "ip-address" not in addr:
                    self.err(f"{ap}: missing required key 'ip-address'")
                else:
                    self.chk_ip_address(addr["ip-address"], f"{ap}.ip-address")
                if "prefix-length" in addr:
                    self.chk_uint(addr["prefix-length"], 0, 128, f"{ap}.prefix-length")
                if "associated-subnet-id" in addr:
                    self.chk_subnet_ref(addr["associated-subnet-id"], f"{ap}.associated-subnet-id")

        # host-config
        seen_hc: set[str] = set()
        for i, hc in enumerate(_list(l3.get("host-config"))):
            p = f"layer3-layer.host-config[{i}]"
            did = hc.get("device-id")
            if not did:
                self.err(f"{p}: missing required key 'device-id'")
            else:
                if did in seen_hc:
                    self.err(f"{p}: duplicate device-id {did!r}")
                seen_hc.add(did)
                p = f"layer3-layer.host-config[{did}]"
                self.chk_device_ref(did, f"{p}.device-id")
            if "primary-ip-address" in hc:
                self.chk_ip_address(hc["primary-ip-address"], f"{p}.primary-ip-address")
            if "primary-prefix-length" in hc:
                self.chk_uint(hc["primary-prefix-length"], 0, 128, f"{p}.primary-prefix-length")
            if "default-gateway" in hc:
                self.chk_ip_address(hc["default-gateway"], f"{p}.default-gateway")
            if "associated-subnet-id" in hc:
                self.chk_subnet_ref(hc["associated-subnet-id"], f"{p}.associated-subnet-id")

        # static-route
        seen_rt: set[tuple] = set()
        for i, rt in enumerate(_list(l3.get("static-route"))):
            p = f"layer3-layer.static-route[{i}]"
            dst = rt.get("destination-prefix")
            nh  = rt.get("next-hop")
            if not dst or not nh:
                self.err(f"{p}: missing required key 'destination-prefix' or 'next-hop'")
            else:
                key = (dst, nh)
                if key in seen_rt:
                    self.err(f"{p}: duplicate key ({dst}, {nh})")
                seen_rt.add(key)
                self.chk_ip_prefix(dst, f"{p}.destination-prefix")
                self.chk_ip_address(nh, f"{p}.next-hop")
            if "device-id" in rt:
                self.chk_device_ref(rt["device-id"], f"{p}.device-id")

        # first-hop-redundancy
        seen_fhr: set[tuple] = set()
        for i, fhr in enumerate(_list(l3.get("first-hop-redundancy"))):
            p   = f"layer3-layer.first-hop-redundancy[{i}]"
            did  = fhr.get("device-id")
            ifid = fhr.get("interface-id")
            gid  = fhr.get("group-id")
            if did is None or ifid is None or gid is None:
                self.err(f"{p}: missing required key device-id/interface-id/group-id")
            else:
                key = (did, ifid, gid)
                if key in seen_fhr:
                    self.err(f"{p}: duplicate key ({did}, {ifid}, {gid})")
                seen_fhr.add(key)
                p = f"layer3-layer.first-hop-redundancy[{did},{ifid},{gid}]"
                if self.chk_device_ref(did, f"{p}.device-id"):
                    self.chk_iface_ref(did, ifid, f"{p}.interface-id")
                self.chk_uint(gid, 0, 4095, f"{p}.group-id")
            if "protocol" in fhr:
                self.chk_enum(fhr["protocol"], FHR_PROTO, f"{p}.protocol")
            if "virtual-ip-address" in fhr:
                self.chk_ip_address(fhr["virtual-ip-address"], f"{p}.virtual-ip-address")
            if "priority" in fhr:
                self.chk_uint(fhr["priority"], 0, 255, f"{p}.priority")

        # route-policy
        seen_rp: set[str] = set()
        for i, rp in enumerate(_list(l3.get("route-policy"))):
            p    = f"layer3-layer.route-policy[{i}]"
            name = rp.get("name")
            if not name:
                self.err(f"{p}: missing required key 'name'")
            else:
                if name in seen_rp:
                    self.err(f"{p}: duplicate name {name!r}")
                seen_rp.add(name)
                p = f"layer3-layer.route-policy[{name}]"
            seen_seqs: set[int] = set()
            for j, stmt in enumerate(_list(rp.get("statement"))):
                sp  = f"{p}.statement[{j}]"
                seq = stmt.get("sequence")
                if seq is None:
                    self.err(f"{sp}: missing required key 'sequence'")
                else:
                    if seq in seen_seqs:
                        self.err(f"{sp}: duplicate sequence {seq}")
                    seen_seqs.add(seq)
                if "action" in stmt:
                    self.chk_enum(stmt["action"], RT_ACTION, f"{sp}.action")
                if "match-prefix" in stmt:
                    self.chk_ip_prefix(stmt["match-prefix"], f"{sp}.match-prefix")

        # routing-config
        seen_rc: set[str] = set()
        for i, rc in enumerate(_list(l3.get("routing-config"))):
            p   = f"layer3-layer.routing-config[{i}]"
            did = rc.get("device-id")
            if not did:
                self.err(f"{p}: missing required key 'device-id'")
            else:
                if did in seen_rc:
                    self.err(f"{p}: duplicate device-id {did!r}")
                seen_rc.add(did)
                p = f"layer3-layer.routing-config[{did}]"
                self.chk_device_ref(did, f"{p}.device-id")
            if "router-id" in rc:
                self.chk_ip_address(rc["router-id"], f"{p}.router-id")

            bgp = _dict(rc.get("bgp"))
            if bgp:
                if "local-asn" not in bgp:
                    self.err(f"{p}.bgp: missing mandatory leaf 'local-asn'")
                pg_names = {pg["name"] for pg in _list(bgp.get("peer-group")) if pg.get("name")}
                for j, pg in enumerate(_list(bgp.get("peer-group"))):
                    pgp = f"{p}.bgp.peer-group[{j}]"
                    for leaf in ("import-policy", "export-policy"):
                        if leaf in pg:
                            self.chk_policy_ref(pg[leaf], f"{pgp}.{leaf}")
                for j, nb in enumerate(_list(bgp.get("neighbor"))):
                    np_ = f"{p}.bgp.neighbor[{j}]"
                    nbaddr = nb.get("neighbor-address")
                    if not nbaddr:
                        self.err(f"{np_}: missing required key 'neighbor-address'")
                    else:
                        self.chk_ip_address(nbaddr, f"{np_}.neighbor-address")
                        np_ = f"{p}.bgp.neighbor[{nbaddr}]"
                    if "peer-group" in nb and nb["peer-group"] not in pg_names:
                        self.err(f"{np_}.peer-group: leafref to unknown peer-group {nb['peer-group']!r}")
                    for leaf in ("import-policy", "export-policy"):
                        if leaf in nb:
                            self.chk_policy_ref(nb[leaf], f"{np_}.{leaf}")
                    for af in _list(nb.get("address-family")):
                        self.chk_enum(af, BGP_AF, f"{np_}.address-family[]")

            ospf = _dict(rc.get("ospf"))
            if ospf:
                for j, area in enumerate(_list(ospf.get("area"))):
                    ap = f"{p}.ospf.area[{j}]"
                    if "area-id" not in area:
                        self.err(f"{ap}: missing required key 'area-id'")
                    if "area-type" in area:
                        self.chk_enum(area["area-type"], OSPF_AREA_T, f"{ap}.area-type")

            for j, rd in enumerate(_list(rc.get("redistribution"))):
                rdp = f"{p}.redistribution[{j}]"
                if "source-protocol" not in rd or "destination-protocol" not in rd:
                    self.err(f"{rdp}: missing required keys source-protocol/destination-protocol")
                else:
                    self.chk_enum(rd["source-protocol"],      REDIST_SRC, f"{rdp}.source-protocol")
                    self.chk_enum(rd["destination-protocol"], REDIST_DST, f"{rdp}.destination-protocol")
                if "route-policy" in rd:
                    self.chk_policy_ref(rd["route-policy"], f"{rdp}.route-policy")

    # ── management ────────────────────────────────────────────────────────────

    def _validate_mgmt(self, mgmt: dict) -> None:
        if "management-vlan" in mgmt:
            self.chk_vlan_ref(mgmt["management-vlan"], "management.management-vlan")

        seen_dm: set[str] = set()
        for i, dm in enumerate(_list(mgmt.get("device-management"))):
            p   = f"management.device-management[{i}]"
            did = dm.get("device-id")
            if not did:
                self.err(f"{p}: missing required key 'device-id'")
            else:
                if did in seen_dm:
                    self.err(f"{p}: duplicate device-id {did!r}")
                seen_dm.add(did)
                p = f"management.device-management[{did}]"
                self.chk_device_ref(did, f"{p}.device-id")
            if "management-ip-address" in dm:
                self.chk_ip_address(dm["management-ip-address"], f"{p}.management-ip-address")
            if "management-prefix-length" in dm:
                self.chk_uint(dm["management-prefix-length"], 0, 128, f"{p}.management-prefix-length")
            if "management-gateway" in dm:
                self.chk_ip_address(dm["management-gateway"], f"{p}.management-gateway")
            if "management-loopback" in dm:
                self.chk_ip_prefix(dm["management-loopback"], f"{p}.management-loopback")
            if "in-band-interface-id" in dm and did in self.device_ids:
                self.chk_iface_ref(did, dm["in-band-interface-id"], f"{p}.in-band-interface-id")
            oob = _dict(dm.get("out-of-band"))
            if oob.get("enabled"):
                if "ip-address" in oob:
                    self.chk_ip_address(oob["ip-address"], f"{p}.out-of-band.ip-address")
                if "prefix-length" in oob:
                    self.chk_uint(oob["prefix-length"], 0, 128, f"{p}.out-of-band.prefix-length")
                if "interface-id" in oob and did in self.device_ids:
                    self.chk_iface_ref(did, oob["interface-id"], f"{p}.out-of-band.interface-id")

    # ── entry point ───────────────────────────────────────────────────────────

    def validate(self) -> bool:
        if not isinstance(self.data, dict) or "network-model" not in self.data:
            self.err("top-level key 'network-model' is missing")
            return False

        self._collect_keys()
        nm = _dict(self.data["network-model"])

        if "physical-layer" in nm:
            self._validate_physical(_dict(nm["physical-layer"]))
        else:
            self.warn("'physical-layer' section is absent")

        if "layer2-layer" in nm:
            self._validate_l2(_dict(nm["layer2-layer"]))

        if "layer3-layer" in nm:
            self._validate_l3(_dict(nm["layer3-layer"]))

        if "management" in nm:
            self._validate_mgmt(_dict(nm["management"]))

        return len(self.errors) == 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def validate_file(path: Path) -> tuple[int, int]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    v = Validator(data, path.name)
    v.validate()
    for e in v.errors:
        print(f"  [ERROR]  {e}")
    for w in v.warnings:
        print(f"  [WARN]   {w}")
    return len(v.errors), len(v.warnings)


def main() -> None:
    targets = [
        Path("examples/sample_topology_small.yaml"),
        Path("examples/sample_topology_medium.yaml"),
        Path("examples/sample_topology_large.yaml"),
    ]
    total_errors = total_warnings = 0
    for f in targets:
        if not f.exists():
            print(f"\n[SKIP] {f} not found")
            continue
        print(f"\n{'─'*64}")
        print(f"  {f}")
        print(f"{'─'*64}")
        errs, warns = validate_file(f)
        total_errors   += errs
        total_warnings += warns
        status = "OK" if errs == 0 else "FAIL"
        print(f"  → {status}  ({errs} errors, {warns} warnings)")

    print(f"\n{'═'*64}")
    print(f"  TOTAL: {total_errors} errors, {total_warnings} warnings")
    print(f"{'═'*64}")
    sys.exit(0 if total_errors == 0 else 1)


if __name__ == "__main__":
    main()
