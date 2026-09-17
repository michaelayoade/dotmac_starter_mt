"""Sub invoice-accounting-sync feed item -> normalized source-only observation.

This connector's job stops at producing a normalized :class:`InboundEvent`
carrying only SOURCE-DERIVED fields. It never includes ``organization_id`` or
an ``idempotency_key``-shaped field — those are supplied downstream by the
generic Integration engine and the destination-specific ``ObservationPortClient``,
which know the binding's configured destination org and mint the delivery
idempotency key. Mapping Sub's ``account_id`` onto ERP's ``organization_id``
would conflate two different systems' identifiers and is exactly the
"provider metadata selects destination scope" anti-pattern this fleet's
architecture forbids.

## The fingerprint — the single highest-risk guess in this module

``projection_fingerprint`` is supposed to match, byte-for-byte, whatever ERP's
own shadow mapper (``app/services/dotmac_sub/invoice_sync_shadow.py`` in the
``dotmac_erp`` worktree) independently computes from the SAME Sub source
record. That worktree was not reachable from here this session, so the exact
algorithm below is a BEST-EFFORT reconstruction from the packet's description
("normalized typed record -> recursive Decimal/datetime/enum-safe conversion
-> sorted compact JSON -> SHA-256, lowercase hex"), not a verified port. If
ERP's canonicalization differs in any of these ways, every fingerprint this
connector emits will silently mismatch ERP's own recomputation:

* which fields are IN the fingerprinted record (this module fingerprints
  every source-derived business fact below: contract version, invoice and
  account identity, status/kind, disposition, header money, optional dates
  and issues -- but not ``capability_id``, which is connector/Integration
  scaffolding, not a Sub-sourced fact);
* how a ``Decimal`` is stringified (here: Python's default ``str(Decimal)``,
  which preserves the exact scale/precision Sub sent — a different
  normalization, e.g. quantizing to 2dp, would disagree);
* how a ``datetime`` is stringified (here: ``datetime.isoformat()`` on a
  value parsed via ``datetime.fromisoformat`` after normalizing a trailing
  ``Z`` to ``+00:00`` — a microsecond-precision or offset-format difference
  changes the digest);
* key ordering (here: ``json.dumps(..., sort_keys=True)``, so this part is
  robust to *this* side's own key order, but not to ERP disagreeing on which
  keys are included at all).

This MUST be reconciled with ERP's slice 1b before either side trusts a
computed fingerprint for drift detection — flagged prominently in the task
report, not silently assumed correct.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Final

from dotmac_integration.spi import InboundEvent

#: Duplicated (not imported) from ``plugin.py`` by design, mirroring how
#: existing connectors (e.g. ``dotmac_connector_paystack``) keep each
#: module's own copy of small identity constants rather than adding a
#: cross-module import for a single string.
CAPABILITY_ID: Final = "invoices.accounting_sync.observation.v1"
CONNECTOR_KEY: Final = "sub_accounting"

#: The exact contract version Sub's feed is documented (this session, not
#: verified against ``dotmac_sub``'s live schema) to publish. A mismatch is
#: refused rather than coerced.
CONTRACT_VERSION: Final = "invoice-accounting-sync.v2"

#: The one disposition token this module treats as "blocked" for the
#: disposition/issues consistency rule. Every other non-empty disposition
#: string is treated as "not blocked". The exact vocabulary Sub publishes
#: beyond "blocked" (e.g. "ready"/"not_applicable") was not confirmed from
#: this worktree — see the module docstring and the task report.
BLOCKED_DISPOSITION: Final = "blocked"

_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z]{3}")

__all__ = [
    "BLOCKED_DISPOSITION",
    "CONTRACT_VERSION",
    "SubAccountingMappingError",
    "map_item",
]


class SubAccountingMappingError(ValueError):
    """One feed item could not be turned into a trustworthy observation."""


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _uuid(value: object) -> str | None:
    # Validated, never reformatted: the exact string Sub sent travels
    # unchanged into the observation and, for the invoice id, back into the
    # next poll's cursor.
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    return value


def _currency(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if _CURRENCY_RE.fullmatch(code) is not None else None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, int):
        candidate = Decimal(value)
    elif isinstance(value, str) and value.strip():
        try:
            candidate = Decimal(value)
        except InvalidOperation:
            return None
    else:
        return None
    return None if candidate.is_nan() or candidate.is_infinite() else candidate


def _parse_timestamp(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SubAccountingMappingError(f"{label} is invalid") from None
    if parsed.tzinfo is None:
        raise SubAccountingMappingError(f"{label} has no timezone")
    return parsed


def _timestamp(value: object, *, label: str) -> tuple[str, datetime]:
    """Return (exact original string, parsed tz-aware datetime)."""
    text = _text(value)
    if text is None:
        raise SubAccountingMappingError(f"{label} is invalid")
    return text, _parse_timestamp(text, label=label)


def _optional_timestamp(value: object, *, label: str) -> tuple[str, datetime] | None:
    if value is None:
        return None
    return _timestamp(value, label=label)


def _issues(
    value: object,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Map Sub's ``line_id`` to ``source_line_id``.

    Returns two parallel lists: the payload shape (money as exact decimal
    strings, for the wire) and the fingerprint shape (money as ``Decimal``,
    for canonicalization) — see :func:`_canonicalize`.
    """
    if value is None:
        return [], []
    if not isinstance(value, list):
        raise SubAccountingMappingError("issues is not a list")
    payload_issues: list[dict[str, object]] = []
    fingerprint_issues: list[dict[str, object]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            raise SubAccountingMappingError("issue entry is not an object")
        code = _text(entry.get("code"))
        source_line_id = _text(entry.get("line_id"))
        expected_amount = _decimal(entry.get("expected_amount"))
        actual_amount = _decimal(entry.get("actual_amount"))
        if (
            code is None
            or source_line_id is None
            or expected_amount is None
            or actual_amount is None
        ):
            raise SubAccountingMappingError("issue entry is missing a required field")
        payload_issues.append(
            {
                "code": code,
                "source_line_id": source_line_id,
                "expected_amount": str(expected_amount),
                "actual_amount": str(actual_amount),
            }
        )
        fingerprint_issues.append(
            {
                "code": code,
                "source_line_id": source_line_id,
                "expected_amount": expected_amount,
                "actual_amount": actual_amount,
            }
        )
    return payload_issues, fingerprint_issues


def _canonicalize(value: object) -> object:
    """Recursive Decimal/datetime/enum-safe conversion, per the packet."""
    if isinstance(value, Mapping):
        return {key: _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _fingerprint(record: Mapping[str, object]) -> str:
    canonical = _canonicalize(record)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def map_item(raw: Mapping[str, object]) -> tuple[InboundEvent, datetime, str]:
    """Translate one Sub feed item into a normalized POLL observation.

    Returns the event plus ``(source_updated_at, source_invoice_id)`` as typed
    values, so the caller (``polling.py``) can enforce strictly-increasing
    page positions across the whole page — this function only ever sees one
    item and cannot compare across a page by itself.

    Raises :class:`SubAccountingMappingError` on any malformed field, an
    unsupported ``contract_version``, or a disposition/issues inconsistency
    (a blocked observation with no issues, or a non-blocked observation
    carrying issues) — mirroring ERP's own ``RecordInvoiceSyncOutcome``
    construction rule as defense in depth, not a replacement for it.
    """
    contract_version = _text(raw.get("contract_version"))
    if contract_version != CONTRACT_VERSION:
        raise SubAccountingMappingError(
            f"unsupported contract_version {contract_version!r}"
        )

    source_invoice_id = _uuid(raw.get("id"))
    if source_invoice_id is None:
        raise SubAccountingMappingError("id is not a valid UUID")

    source_account_id = _uuid(raw.get("account_id"))
    if source_account_id is None:
        raise SubAccountingMappingError("account_id is not a valid UUID")

    updated_at_text, updated_at = _timestamp(raw.get("updated_at"), label="updated_at")

    source_kind = _text(raw.get("status"))
    if source_kind is None:
        raise SubAccountingMappingError("status is missing")

    disposition = _text(raw.get("disposition"))
    if disposition is None:
        raise SubAccountingMappingError("disposition is missing")

    total_amount = _decimal(raw.get("total_amount"))
    currency = _currency(raw.get("currency"))
    if total_amount is None or currency is None:
        raise SubAccountingMappingError("header money fields are invalid")

    issued = _optional_timestamp(raw.get("issued_at"), label="issued_at")
    due = _optional_timestamp(raw.get("due_at"), label="due_at")

    payload_issues, fingerprint_issues = _issues(raw.get("issues"))

    is_blocked = disposition == BLOCKED_DISPOSITION
    if is_blocked and not payload_issues:
        raise SubAccountingMappingError("blocked disposition carries no issues")
    if not is_blocked and payload_issues:
        raise SubAccountingMappingError("non-blocked disposition carries issues")

    fingerprint_record: dict[str, object] = {
        "contract_version": contract_version,
        "source_invoice_id": source_invoice_id,
        "source_account_id": source_account_id,
        "source_updated_at": updated_at,
        "source_kind": source_kind,
        "disposition": disposition,
        "source_total_amount": total_amount,
        "source_currency": currency,
        "issues": fingerprint_issues,
    }
    if issued is not None:
        fingerprint_record["source_issued_at"] = issued[1]
    if due is not None:
        fingerprint_record["source_due_at"] = due[1]

    payload: dict[str, object] = {
        "capability_id": CAPABILITY_ID,
        "contract_version": contract_version,
        "source_invoice_id": source_invoice_id,
        "source_account_id": source_account_id,
        "source_updated_at": updated_at_text,
        "source_kind": source_kind,
        "disposition": disposition,
        "source_total_amount": str(total_amount),
        "source_currency": currency,
        "issues": payload_issues,
    }
    if issued is not None:
        payload["source_issued_at"] = issued[0]
    if due is not None:
        payload["source_due_at"] = due[0]

    payload["projection_fingerprint"] = _fingerprint(fingerprint_record)

    event = InboundEvent(
        provider_event_id=f"{CONNECTOR_KEY}:{source_invoice_id}:{updated_at_text}",
        event_type=CAPABILITY_ID,
        payload=payload,
    )
    return event, updated_at, source_invoice_id
