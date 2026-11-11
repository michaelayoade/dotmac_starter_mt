"""Private serialization and completeness join for exposure records.

`lane3_exposure_recording` holds the Executor boundary crossings only in
memory, deliberately: the raw records are private operational evidence
(real rules, real sockets), and a recording failure there must never
rewrite a host outcome. This module is the consumer that seam promised.
It turns the in-memory records into one closed canonical document a
private evidence bundle can carry, parses that document back strictly,
and joins the records against the authorized plan's exposure
reconciliations.

The join fails closed. Recording is best-effort by design, so a missing
record proves nothing: a gap in the sequence, a failed observation, a
mutation before any successful observation, or a reconciliation with no
successful `replace_rules` crossing all refuse rather than conclude.
Nothing here orders a host operation, reads a host, or admits anything;
it only refuses to let incomplete evidence read as publication proof.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any, cast

from dotmac_deployment_foundation.execution_plan_v2 import (
    ExposureReconciliationV1,
)
from dotmac_deployment_foundation.exposure import (
    HostObservation,
    ObservedChain,
    ObservedProxy,
    ObservedRule,
    ObservedSocket,
)
from dotmac_deployment_foundation.ingress import FirewallRule
from lane3_exposure_recording import ExposureCallRecord, ExposureMethod

EXPOSURE_CALLS_SCHEMA = "lane3.exposure-calls.v1"

_METHODS: frozenset[str] = frozenset(
    {"observe", "apply_compose", "replace_rules", "restore_chains"}
)
_MUTATING: frozenset[str] = frozenset(
    {"apply_compose", "replace_rules", "restore_chains"}
)


class ExposureEvidenceRefused(ValueError):
    """The records cannot be serialized, parsed, or joined as evidence."""


def _socket_document(socket: ObservedSocket) -> dict[str, Any]:
    return {
        "address": socket.address,
        "family": socket.family,
        "port": socket.port,
        "process": socket.process,
    }


def _proxy_document(proxy: ObservedProxy) -> dict[str, Any]:
    return {
        "container_port": proxy.container_port,
        "family": proxy.family,
        "host_ip": proxy.host_ip,
        "host_port": proxy.host_port,
        "pid": proxy.pid,
        "protocol": proxy.protocol,
    }


def _rule_document(rule: ObservedRule) -> dict[str, Any]:
    return {"arguments": rule.arguments, "chain": rule.chain, "family": rule.family}


def _chain_document(chain: ObservedChain) -> dict[str, Any]:
    return {
        "family": chain.family,
        "name": chain.name,
        "policy": chain.policy,
        "rules": [_rule_document(rule) for rule in chain.rules],
    }


def _observation_document(observation: HostObservation) -> dict[str, Any]:
    return {
        "chains": [_chain_document(chain) for chain in observation.chains],
        "closed_port_behaviour": observation.closed_port_behaviour,
        "proxies": [_proxy_document(proxy) for proxy in observation.proxies],
        "sockets": [_socket_document(socket) for socket in observation.sockets],
    }


def _firewall_rule_document(rule: FirewallRule) -> dict[str, Any]:
    return {
        "action": rule.action,
        "chain": rule.chain,
        "family": rule.family,
        "host_port": rule.host_port,
        "protocol": rule.protocol,
        "source_set": rule.source_set,
        "terminal": rule.terminal,
    }


def _call_document(record: ExposureCallRecord) -> dict[str, Any]:
    return {
        "chain": record.chain,
        "chains": [_chain_document(chain) for chain in record.chains],
        "command": list(record.command),
        "duration_seconds": record.duration_seconds,
        "exception_type": record.exception_type,
        "family": record.family,
        "method": record.method,
        "observation": (
            _observation_document(record.observation)
            if record.observation is not None
            else None
        ),
        "rules": [_firewall_rule_document(rule) for rule in record.rules],
        "sequence": record.sequence,
        "timeout_seconds": record.timeout_seconds,
    }


def exposure_calls_document(
    records: Sequence[ExposureCallRecord],
) -> dict[str, Any]:
    """The closed document for these records, in recorded order."""
    return {
        "calls": [_call_document(record) for record in records],
        "schema": EXPOSURE_CALLS_SCHEMA,
    }


def serialize_exposure_calls(records: Sequence[ExposureCallRecord]) -> bytes:
    """The canonical bytes a private evidence bundle carries.

    The records are joined before they are serialized only in the sense
    that a broken sequence is refused here too: bytes that the parser
    would reject must never be the bytes a run uploads.
    """
    _require_sequence(records)
    return (
        json.dumps(
            exposure_calls_document(records),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def exposure_calls_digest(records: Sequence[ExposureCallRecord]) -> str:
    """SHA-256 of the canonical bytes, for value-free terminal evidence."""
    return hashlib.sha256(serialize_exposure_calls(records)).hexdigest()


def _require_sequence(records: Sequence[ExposureCallRecord]) -> None:
    for index, record in enumerate(records):
        if record.sequence != index:
            raise ExposureEvidenceRefused(
                f"exposure record sequence is not contiguous from zero: "
                f"position {index} carries sequence {record.sequence}. "
                "A missing record proves nothing and cannot be evidence"
            )


def _closed(value: Any, keys: frozenset[str], *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExposureEvidenceRefused(f"{where} must be an object")
    found = frozenset(value)
    if found != keys:
        raise ExposureEvidenceRefused(
            f"{where} keys {sorted(found)} do not close over {sorted(keys)}"
        )
    return value


def _str(value: Any, *, where: str) -> str:
    if not isinstance(value, str):
        raise ExposureEvidenceRefused(f"{where} must be a string")
    return value


def _int(value: Any, *, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExposureEvidenceRefused(f"{where} must be an integer")
    return value


def _list(value: Any, *, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExposureEvidenceRefused(f"{where} must be a list")
    return value


def _parse_socket(value: Any, *, where: str) -> ObservedSocket:
    doc = _closed(
        value, frozenset({"address", "family", "port", "process"}), where=where
    )
    return ObservedSocket(
        family=_str(doc["family"], where=f"{where}.family"),
        address=_str(doc["address"], where=f"{where}.address"),
        port=_int(doc["port"], where=f"{where}.port"),
        process=_str(doc["process"], where=f"{where}.process"),
    )


def _parse_proxy(value: Any, *, where: str) -> ObservedProxy:
    doc = _closed(
        value,
        frozenset(
            {"container_port", "family", "host_ip", "host_port", "pid", "protocol"}
        ),
        where=where,
    )
    pid = doc["pid"]
    if pid is not None:
        pid = _int(pid, where=f"{where}.pid")
    return ObservedProxy(
        family=_str(doc["family"], where=f"{where}.family"),
        host_ip=_str(doc["host_ip"], where=f"{where}.host_ip"),
        host_port=_int(doc["host_port"], where=f"{where}.host_port"),
        container_port=_int(doc["container_port"], where=f"{where}.container_port"),
        protocol=_str(doc["protocol"], where=f"{where}.protocol"),
        pid=pid,
    )


def _parse_chain(value: Any, *, where: str) -> ObservedChain:
    doc = _closed(value, frozenset({"family", "name", "policy", "rules"}), where=where)
    rules = []
    for index, item in enumerate(_list(doc["rules"], where=f"{where}.rules")):
        rule_where = f"{where}.rules[{index}]"
        rule = _closed(
            item, frozenset({"arguments", "chain", "family"}), where=rule_where
        )
        rules.append(
            ObservedRule(
                family=_str(rule["family"], where=f"{rule_where}.family"),
                chain=_str(rule["chain"], where=f"{rule_where}.chain"),
                arguments=_str(rule["arguments"], where=f"{rule_where}.arguments"),
            )
        )
    return ObservedChain(
        family=_str(doc["family"], where=f"{where}.family"),
        name=_str(doc["name"], where=f"{where}.name"),
        policy=_str(doc["policy"], where=f"{where}.policy"),
        rules=tuple(rules),
    )


def _parse_observation(value: Any, *, where: str) -> HostObservation:
    doc = _closed(
        value,
        frozenset({"chains", "closed_port_behaviour", "proxies", "sockets"}),
        where=where,
    )
    return HostObservation(
        sockets=tuple(
            _parse_socket(item, where=f"{where}.sockets[{index}]")
            for index, item in enumerate(
                _list(doc["sockets"], where=f"{where}.sockets")
            )
        ),
        proxies=tuple(
            _parse_proxy(item, where=f"{where}.proxies[{index}]")
            for index, item in enumerate(
                _list(doc["proxies"], where=f"{where}.proxies")
            )
        ),
        chains=tuple(
            _parse_chain(item, where=f"{where}.chains[{index}]")
            for index, item in enumerate(_list(doc["chains"], where=f"{where}.chains"))
        ),
        closed_port_behaviour=_str(
            doc["closed_port_behaviour"], where=f"{where}.closed_port_behaviour"
        ),
    )


def _parse_firewall_rule(value: Any, *, where: str) -> FirewallRule:
    doc = _closed(
        value,
        frozenset(
            {
                "action",
                "chain",
                "family",
                "host_port",
                "protocol",
                "source_set",
                "terminal",
            }
        ),
        where=where,
    )
    terminal = doc["terminal"]
    if not isinstance(terminal, bool):
        raise ExposureEvidenceRefused(f"{where}.terminal must be a boolean")
    return FirewallRule(
        family=_str(doc["family"], where=f"{where}.family"),
        chain=_str(doc["chain"], where=f"{where}.chain"),
        protocol=_str(doc["protocol"], where=f"{where}.protocol"),
        host_port=_int(doc["host_port"], where=f"{where}.host_port"),
        action=_str(doc["action"], where=f"{where}.action"),
        source_set=_str(doc["source_set"], where=f"{where}.source_set"),
        terminal=terminal,
    )


_CALL_KEYS = frozenset(
    {
        "chain",
        "chains",
        "command",
        "duration_seconds",
        "exception_type",
        "family",
        "method",
        "observation",
        "rules",
        "sequence",
        "timeout_seconds",
    }
)


def _parse_call(value: Any, *, where: str) -> ExposureCallRecord:
    doc = _closed(value, _CALL_KEYS, where=where)
    method = _str(doc["method"], where=f"{where}.method")
    if method not in _METHODS:
        raise ExposureEvidenceRefused(
            f"{where}.method {method!r} is not an exposure method"
        )
    duration = doc["duration_seconds"]
    if isinstance(duration, bool) or not isinstance(duration, int | float):
        raise ExposureEvidenceRefused(f"{where}.duration_seconds must be a number")
    timeout = doc["timeout_seconds"]
    if timeout is not None:
        timeout = _int(timeout, where=f"{where}.timeout_seconds")
    observation = doc["observation"]
    return ExposureCallRecord(
        sequence=_int(doc["sequence"], where=f"{where}.sequence"),
        method=cast("ExposureMethod", method),
        family=_str(doc["family"], where=f"{where}.family"),
        chain=_str(doc["chain"], where=f"{where}.chain"),
        rules=tuple(
            _parse_firewall_rule(item, where=f"{where}.rules[{index}]")
            for index, item in enumerate(_list(doc["rules"], where=f"{where}.rules"))
        ),
        chains=tuple(
            _parse_chain(item, where=f"{where}.chains[{index}]")
            for index, item in enumerate(_list(doc["chains"], where=f"{where}.chains"))
        ),
        command=tuple(
            _str(item, where=f"{where}.command[{index}]")
            for index, item in enumerate(
                _list(doc["command"], where=f"{where}.command")
            )
        ),
        timeout_seconds=timeout,
        observation=(
            _parse_observation(observation, where=f"{where}.observation")
            if observation is not None
            else None
        ),
        exception_type=_str(doc["exception_type"], where=f"{where}.exception_type"),
        duration_seconds=float(duration),
    )


def parse_exposure_calls(data: bytes) -> tuple[ExposureCallRecord, ...]:
    """The records in canonical private bytes, or a refusal.

    Only the exact canonical serialization parses: the parsed records are
    re-serialized and compared byte for byte, so a reformatted, reordered
    or extended document is not the evidence a run uploaded.
    """
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExposureEvidenceRefused(
            f"exposure evidence is not UTF-8 JSON: {exc}"
        ) from exc
    doc = _closed(document, frozenset({"calls", "schema"}), where="exposure_calls")
    if doc["schema"] != EXPOSURE_CALLS_SCHEMA:
        raise ExposureEvidenceRefused(
            f"exposure evidence schema {doc['schema']!r} is not "
            f"{EXPOSURE_CALLS_SCHEMA!r}"
        )
    records = tuple(
        _parse_call(item, where=f"exposure_calls.calls[{index}]")
        for index, item in enumerate(_list(doc["calls"], where="exposure_calls.calls"))
    )
    if serialize_exposure_calls(records) != data:
        raise ExposureEvidenceRefused(
            "exposure evidence is not the canonical serialization of its records"
        )
    return records


def require_exposure_join(
    records: Sequence[ExposureCallRecord],
    reconciliations: Iterable[ExposureReconciliationV1],
) -> None:
    """Refuse unless the records completely join the authorized families.

    Completeness is the whole claim. The sequence must be contiguous from
    zero; a successful observation must precede the first mutating
    crossing; no failed observation may appear; and every authorized
    (family, chain) reconciliation needs at least one successful
    `replace_rules` crossing naming exactly that family and chain, whose
    recorded rules all belong to that same family and chain. Anything
    less is the incomplete evidence the recording seam warns about.
    """
    _require_sequence(records)
    observed = False
    replaced: set[tuple[str, str]] = set()
    for record in records:
        if record.method == "observe":
            if record.exception_type or record.observation is None:
                raise ExposureEvidenceRefused(
                    f"exposure record {record.sequence} is a failed "
                    "observation; the join cannot conclude from it"
                )
            observed = True
            continue
        if record.method in _MUTATING and not observed:
            raise ExposureEvidenceRefused(
                f"exposure record {record.sequence} mutates through "
                f"{record.method} before any successful observation"
            )
        if record.method == "replace_rules" and not record.exception_type:
            for rule in record.rules:
                if (rule.family, rule.chain) != (record.family, record.chain):
                    raise ExposureEvidenceRefused(
                        f"exposure record {record.sequence} names "
                        f"{record.family}/{record.chain} but carries a "
                        f"{rule.family}/{rule.chain} rule"
                    )
            replaced.add((record.family, record.chain))
    if not observed:
        raise ExposureEvidenceRefused(
            "exposure records contain no successful observation"
        )
    missing = [
        f"{item.family}/{item.chain}"
        for item in reconciliations
        if (item.family, item.chain) not in replaced
    ]
    if missing:
        raise ExposureEvidenceRefused(
            "no successful replace_rules crossing joins authorized "
            f"reconciliations: {', '.join(missing)}"
        )
