"""The Lane 3 vantage-topology record, ``lane3.vantage-topology.v1``.

``docs/LANE3_EXECUTION_TOPOLOGY.md`` § 4 moves Lane 3's private topology out
of repository variables and into ONE OpenBao KV v2 record,
``secret/dotmac/starter/lane3/vantage-topology``. Only Michael's provisioning
identity writes it (B7). The Lane 3 workflow identity (the
``lane3-exposure-rehearsal`` JWT role) reads it; nothing else does.

This module is the Starter-owned parser for that record. It is the contract
the B7 provisioning script writes against, so the two must accept and refuse
the same documents (see ``tests/unit/test_lane3_topology.py``, which carries
the parity fixtures).

## Values never leave the record

Every refusal names a FIELD, never a value: a refused record must not become a
log line, an exception message or a CI annotation that discloses a target
address or a vantage host. :class:`VantageTopologyV1` hides every value from
``repr``. Public evidence cites the record's KV **version** and the
value-free :meth:`VantageTopologyV1.structure` summary, never a plain digest
of the values (addresses are low-entropy, so a plain hash can be inverted by
brute force).

## Fail closed

The key set is closed at every level. A missing key, an extra key, a wrong
type, an empty or whitespace-padded string, a duplicate list entry or a
non-integer port refuses the whole record. There is no partial acceptance and
no default.

Stdlib only: it must be importable by a test with nothing installed. It does
not read OpenBao, the network or the filesystem; the caller supplies the
already-decoded record.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

SCHEMA = "lane3.vantage-topology.v1"
RECORD_PATH = "secret/dotmac/starter/lane3/vantage-topology"
OBSERVER_PRINCIPAL = "lane3obs"

#: Principals the inside-jump account may never be: the target-side observer,
#: root, and Gate-0's controller principal (the Gate-0 and Lane 3 CAs and
#: principals are disjoint by design).
RESERVED_JUMP_PRINCIPALS = frozenset(
    {"root", OBSERVER_PRINCIPAL, "dotmac-gate0-controller"}
)

_TOP_KEYS = frozenset(
    {
        "schema",
        "probe_vantage",
        "inside_vantage",
        "observer_principal",
        "targets",
        "former_private_paths",
        "probe_ports",
    }
)
_PROBE_VANTAGE_KEYS = frozenset({"key", "host", "ssh_user"})
_INSIDE_VANTAGE_KEYS = frozenset({"host", "jump_principal"})
_TARGET_KEYS = frozenset({"address", "far_end", "proxmox_slot"})


class TopologyRecordRefused(ValueError):
    """The record does not conform to ``lane3.vantage-topology.v1``.

    The message names the refused field only. It never carries a value.
    """


def _refuse(field: str) -> TopologyRecordRefused:
    return TopologyRecordRefused(f"lane3 topology record refused: {field}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _refuse(field)
    return value


def _closed(value: Any, keys: frozenset[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _refuse(field)
    return value


@dataclasses.dataclass(frozen=True)
class ProbeVantage:
    key: str = dataclasses.field(repr=False)
    host: str = dataclasses.field(repr=False)
    ssh_user: str = dataclasses.field(repr=False)


@dataclasses.dataclass(frozen=True)
class InsideVantage:
    host: str = dataclasses.field(repr=False)
    jump_principal: str = dataclasses.field(repr=False)


@dataclasses.dataclass(frozen=True)
class Target:
    address: str = dataclasses.field(repr=False)
    far_end: str = dataclasses.field(repr=False)
    proxmox_slot: str = dataclasses.field(repr=False)


@dataclasses.dataclass(frozen=True)
class VantageTopologyV1:
    """A validated record. Every value is hidden from ``repr``."""

    probe_vantage: ProbeVantage = dataclasses.field(repr=False)
    inside_vantage: InsideVantage = dataclasses.field(repr=False)
    targets: Mapping[str, Target] = dataclasses.field(repr=False)
    former_private_paths: tuple[str, ...] = dataclasses.field(repr=False)
    probe_ports: tuple[int, ...] = dataclasses.field(repr=False)
    observer_principal: str = OBSERVER_PRINCIPAL
    schema: str = SCHEMA

    def structure(self) -> dict[str, Any]:
        """The value-free summary public evidence may carry."""
        return {
            "schema": self.schema,
            "targets": len(self.targets),
            "former_private_paths": len(self.former_private_paths),
            "probe_ports": len(self.probe_ports),
        }

    def probe_vantage_ref(self, version: int) -> str:
        """The receipt's ``probe_vantage_ref``: ``<key>@<KV version>``, never values."""
        if type(version) is not int or version < 1:
            raise _refuse("version")
        return f"{self.probe_vantage.key}@{version}"


def parse_topology_record(
    record: Any, *, jump_principal: str | None = None
) -> VantageTopologyV1:
    """Validate a decoded ``lane3.vantage-topology.v1`` record, or refuse it.

    ``jump_principal``, when given, is the principal provisioned on the B6
    ``inside-jump`` role; the record's inside-vantage principal must equal it.
    """
    top = _closed(record, _TOP_KEYS, "top-level keys")
    if top["schema"] != SCHEMA:
        raise _refuse("schema")

    pv = _closed(top["probe_vantage"], _PROBE_VANTAGE_KEYS, "probe_vantage")
    probe = ProbeVantage(
        key=_text(pv["key"], "probe_vantage.key"),
        host=_text(pv["host"], "probe_vantage.host"),
        ssh_user=_text(pv["ssh_user"], "probe_vantage.ssh_user"),
    )
    if "@" in probe.key:
        # probe_vantage_ref is "<key>@<version>"; an "@" in the key makes it ambiguous.
        raise _refuse("probe_vantage.key")

    iv = _closed(top["inside_vantage"], _INSIDE_VANTAGE_KEYS, "inside_vantage")
    inside = InsideVantage(
        host=_text(iv["host"], "inside_vantage.host"),
        jump_principal=_text(iv["jump_principal"], "inside_vantage.jump_principal"),
    )
    if inside.jump_principal in RESERVED_JUMP_PRINCIPALS:
        raise _refuse("inside_vantage.jump_principal")
    if jump_principal is not None and inside.jump_principal != jump_principal:
        raise _refuse("inside_vantage.jump_principal")

    if top["observer_principal"] != OBSERVER_PRINCIPAL:
        raise _refuse("observer_principal")

    raw_targets = top["targets"]
    if not isinstance(raw_targets, Mapping) or not raw_targets:
        raise _refuse("targets")
    targets: dict[str, Target] = {}
    for host_id, entry in raw_targets.items():
        _text(host_id, "targets.<host_id>")
        t = _closed(entry, _TARGET_KEYS, "targets.<host_id>")
        address = _text(t["address"], "targets.<host_id>.address")
        far_end = _text(t["far_end"], "targets.<host_id>.far_end")
        if far_end != address:
            raise _refuse("targets.<host_id>.far_end")
        targets[host_id] = Target(
            address=address,
            far_end=far_end,
            proxmox_slot=_text(t["proxmox_slot"], "targets.<host_id>.proxmox_slot"),
        )

    paths = top["former_private_paths"]
    if (
        not isinstance(paths, list)
        or not paths
        or len(paths) != len(set(map(repr, paths)))
    ):
        raise _refuse("former_private_paths")
    former = tuple(_text(p, "former_private_paths[]") for p in paths)

    ports = top["probe_ports"]
    if (
        not isinstance(ports, list)
        or not ports
        or any(type(p) is not int or not 0 < p < 65536 for p in ports)
        or len(ports) != len(set(ports))
    ):
        raise _refuse("probe_ports")

    return VantageTopologyV1(
        probe_vantage=probe,
        inside_vantage=inside,
        targets=dict(targets),
        former_private_paths=former,
        probe_ports=tuple(ports),
    )
