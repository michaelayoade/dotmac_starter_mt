"""Pure ordering rule for versioned access inputs.

No database, clock or SQLAlchemy import: any consumer that keeps its own copy
of an access observation (for example a product enforcement adapter) calls
``decide_access_input`` and gets exactly the rule this package applies to its
own storage.

Ordering rule (per key = tenant + service_reference + signal):

* ``source_version`` is a non-negative integer; larger is newer. Only integer
  comparison is used. Timestamps (``observed_at``) never decide ordering.
* A stored row with no ``source_version`` (written by the V1 contract) is older
  than every versioned input.
* incoming >  stored  -> APPLY
* incoming == stored and the payload is identical -> IDEMPOTENT (no write)
* incoming == stored and the payload differs      -> REFUSE_CONFLICT
* incoming <  stored  -> REFUSE_STALE
* an unversioned (V1) write onto a versioned key  -> REFUSE_UNVERSIONED

Payload identity is ``(active, source_reference, observed_at, policy_version)``
with ``source_reference`` stripped and ``observed_at`` compared as an instant
(a naive datetime read back from a store is taken to be UTC).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from dotmac_service_access_policy.contracts import (
    AccessInputDecision,
    RecordAccessInputV2,
)

MAX_SOURCE_VERSION = 2**63 - 1


@dataclass(frozen=True, slots=True)
class StoredAccessStamp:
    """The stored fields the rule needs; ``source_version`` None means V1."""

    source_version: int | None
    active: bool
    source_reference: str
    observed_at: datetime
    policy_version: str | None


def _instant(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def validate_source_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("source_version must be an integer")
    if value < 0 or value > MAX_SOURCE_VERSION:
        raise ValueError("source_version must be between 0 and 2**63-1")
    return value


def _payload(
    active: bool, source_reference: str, observed_at: datetime, policy: str | None
) -> tuple[bool, str, datetime, str | None]:
    return (active, source_reference.strip(), _instant(observed_at), policy)


def decide_access_input(
    stored: StoredAccessStamp | None, incoming: RecordAccessInputV2
) -> AccessInputDecision:
    """Apply the ordering rule above; ``stored`` None means no row yet."""
    version = validate_source_version(incoming.source_version)
    if stored is None:
        return AccessInputDecision.APPLY
    if stored.source_version is None or version > stored.source_version:
        return AccessInputDecision.APPLY
    if version < stored.source_version:
        return AccessInputDecision.REFUSE_STALE
    same = _payload(
        stored.active,
        stored.source_reference,
        stored.observed_at,
        stored.policy_version,
    ) == _payload(
        incoming.active,
        incoming.source_reference,
        incoming.observed_at,
        incoming.policy_version.strip(),
    )
    return (
        AccessInputDecision.IDEMPOTENT if same else AccessInputDecision.REFUSE_CONFLICT
    )


def decide_unversioned_write(stored: StoredAccessStamp | None) -> AccessInputDecision:
    """V1 writes are allowed only while the key has never been versioned."""
    if stored is not None and stored.source_version is not None:
        return AccessInputDecision.REFUSE_UNVERSIONED
    return AccessInputDecision.APPLY


__all__ = [
    "MAX_SOURCE_VERSION",
    "StoredAccessStamp",
    "decide_access_input",
    "decide_unversioned_write",
    "validate_source_version",
]
