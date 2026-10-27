"""Vendor-neutral typed operations.

Every operation takes a session and (where relevant) a neutral ``OntRef`` and
returns ``Ok | Unreadable | Unsupported``. They depend only on the dialect
interface: no vendor name, address format or command text appears here.

Fail closed: an operation the resolved profile does not DECLARE, or that the
dialect cannot compose and parse, is ``Unsupported``. There is no fallback to a
neighbouring model's, family's or vendor's syntax.
"""

from __future__ import annotations

from dotmac_device_cli.core.classifier import (
    Classified,
    Kind,
    OntInventory,
    classify_response,
    classify_table,
)
from dotmac_device_cli.core.dialect import Dialect, OntRef, dialect_for
from dotmac_device_cli.core.profile import CommandRejected
from dotmac_device_cli.core.results import Ok, Reason, Unreadable, Unsupported
from dotmac_device_cli.core.session import CliSession

Outcome = Ok[object] | Unreadable | Unsupported


def _gate(
    session: CliSession, operation: str, ref: OntRef | None, *, needs: str
) -> tuple[Dialect, str] | Unsupported:
    profile = session.profile
    if not profile.claims(operation):
        return Unsupported(
            operation, profile.id, "operation not declared by the profile"
        )
    dialect = dialect_for(profile.dialect)
    if dialect is None:
        return Unsupported(operation, profile.id, f"no dialect {profile.dialect!r}")
    if operation not in dialect.operations():
        return Unsupported(operation, profile.id, "dialect cannot compose and parse it")
    if needs != "none":
        if ref is None or ref.dialect != dialect.name:
            return Unsupported(
                operation, profile.id, "reference belongs to another dialect"
            )
        if (needs == "port") != ref.is_port:
            return Unsupported(
                operation, profile.id, f"operation needs a {needs} reference"
            )
    try:
        command = dialect.compose(operation, profile, ref)
    except KeyError:
        return Unsupported(operation, profile.id, "profile has no template for it")
    except CommandRejected as exc:
        return Unsupported(operation, profile.id, f"command rejected: {exc}")
    return dialect, command


def _read(
    session: CliSession, operation: str, command: str
) -> Ok[Classified] | Unreadable:
    raw = session.run_read(operation, command)
    if isinstance(raw, Unreadable):
        return raw
    return classify_response(session.profile, command, raw.value, raw.evidence)


def version(session: CliSession) -> Outcome:
    gate = _gate(session, "version", None, needs="none")
    if isinstance(gate, Unsupported):
        return gate
    _, command = gate
    result = _read(session, "version", command)
    if isinstance(result, Unreadable):
        return result
    if result.value.kind is Kind.ABSENT:
        return Unreadable(
            Reason.UNRECOGNIZED_RESPONSE, "unexpected absence", result.evidence
        )
    return Ok(result.value.body, result.evidence)


def list_onts(session: CliSession, port: OntRef) -> Outcome:
    """ONTs registered on a port. Absence is a value, never an empty list."""
    gate = _gate(session, "list_onts", port, needs="port")
    if isinstance(gate, Unsupported):
        return gate
    dialect, command = gate
    result = _read(session, "list_onts", command)
    if isinstance(result, Unreadable):
        return result
    if result.value.kind is Kind.ABSENT:
        return Unreadable(
            Reason.UNRECOGNIZED_RESPONSE, "unexpected absence", result.evidence
        )
    inventory: Ok[OntInventory] | Unreadable = classify_table(
        dialect.parse_ont_table(result.value.body), result.evidence
    )
    return inventory


def ont_detail(session: CliSession, ont: OntRef) -> Outcome:
    gate = _gate(session, "ont_detail", ont, needs="ont")
    if isinstance(gate, Unsupported):
        return gate
    return Unsupported("ont_detail", session.profile.id, "no parser shipped in 0.1.0a1")


def service_ports(session: CliSession, ont: OntRef) -> Outcome:
    gate = _gate(session, "service_ports", ont, needs="ont")
    if isinstance(gate, Unsupported):
        return gate
    return Unsupported(
        "service_ports", session.profile.id, "no parser shipped in 0.1.0a1"
    )


__all__ = [
    "Outcome",
    "list_onts",
    "ont_detail",
    "service_ports",
    "version",
]
