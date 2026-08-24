"""Payload retention — age out the CONTENT, never the identity.

A verified inbound event is two different things stored in one row. It is
**evidence** — this binding has already seen this provider event, its content
digested to `payload_digest`, and this is what it caused — and it is
**content**, the provider's message body and transport headers. The first must
outlive the second, and this module is the seam where they part company.

## Why deleting the row at the content boundary is the wrong answer

Providers redeliver. Meta retries a webhook for days, and a replayed queue or a
restored backup can resurface a months-old event at any time. Deduplication
lives in `(capability_binding_id, provider_event_id)`, a UNIQUE constraint on
`inbox_receipts` — so a deleted receipt is not a tidied receipt, it is a
receipt whose redelivery becomes a **new** event. The product then processes a
customer conversation a second time, months late, with no record that it had
already answered.

The content sweep never deletes a row and never touches a single column that
deduplication, ordering or outcome comparison reads. A separate evidence sweep
deletes the CLOSED row only after its longer, independently ruled period:

======================== ====================================================
preserved after content  why
======================== ====================================================
``capability_binding_id`` half of the deduplication key
``provider_event_id``     the other half
``payload_digest``        tells a redelivery from a provider identity
                          collision — see `execution.receive_verified`
``event_type``            what kind of thing this was
``state`` / ``processed_at`` that it reached an outcome, and when
``attempt_count``         how much work it took
``received_at``           when it arrived, and therefore its age
======================== ====================================================

What is redacted is `payload_json`, `headers_json` and the *values* inside
`consequence_json`. A redelivery of a redacted receipt still finds the row,
still matches the digest, and is still answered "already received" — the
identity survives its content.

## A tombstone, not a NULL

`payload_json` is nullable, and a provider event genuinely may carry no body.
Nulling a redacted payload therefore erases the distinction between "there was
nothing" and "there was something and it aged out", which is exactly the
question an audit asks. Redaction writes a namespaced marker object instead —
:data:`REDACTION_MARKER` — carrying when, under which retention period, and on
whose legal authority. The marker is also what makes the sweep idempotent: a
receipt already carrying it is not a candidate, so running the sweep twice
changes nothing the first run did not.

## Asymmetric key handling, deliberately

The tombstone keeps header NAMES and consequence KEYS, and keeps neither the
payload's keys nor anybody's values. Header names and consequence keys are
*our* vocabulary — `x-hub-signature-256`, `ticket_id` — and knowing a signature
header was present is real evidence. A payload's top-level keys are the
PROVIDER's, and a provider is free to key a map by a phone number. Keeping
payload key names would therefore leak subscriber identifiers through a
structure that looks like schema. Only the digest and the key count survive.

## Retention is configured, never defaulted

There is no default retention period in this file, and there must never be
one. A period baked into a library is a policy decision smuggled past the
person who is accountable for it — and "90 days" would become the fleet's
data-retention posture by accident. :func:`resolve_retention_policy` reads both
periods and the accountable owner, and REFUSES when any is absent; it never
invents a value, in any environment. A hold has to be answerable to someone,
and a library cannot name that person.

## Legal hold refuses, loudly

A held receipt is never redacted, and the refusal is a recorded `RetentionRefusal`
with a reason — not a row quietly missing from a batch. The hold is enforced
twice: the sweep will not select a held receipt, and the per-row UPDATE carries
`NOT EXISTS (active hold)` in its own WHERE clause, so a hold placed after the
candidate was read still wins.

## Health is counted, never stored

:func:`retention_backlog` counts rows at read time, like
`operations.health_report` and for the same reason: a stored summary is a
second writer over facts the ledger already holds, and it drifts the moment a
sweep dies half-way. There is no retention status column anywhere in this
module, and `tests/architecture/test_integration_retention_policy.py` fails the
build if one appears.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    DateTime,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_integration.execution import payload_digest
from dotmac_integration.models import DeliveryAttempt, InboxReceipt
from dotmac_integration.operations import record_operation

__all__ = [
    "REDACTION_MARKER",
    "RETENTION_AUDIT_ACTIONS",
    "RETENTION_DAYS_VAR",
    "RETENTION_LEGAL_POLICY_OWNER_VAR",
    "RETENTION_REPLAY_EVIDENCE_DAYS_VAR",
    "RETENTION_PLATFORM_TABLES",
    "REDACTABLE_COLUMNS",
    "REFUSAL_REASONS",
    "DeliveryLegalHold",
    "DeliveryRetentionRefusal",
    "DeliveryRetentionSweep",
    "ReceiptLegalHold",
    "RetentionBacklog",
    "ReplayEvidenceSweep",
    "RetentionNotConfigured",
    "RetentionPolicy",
    "RetentionRefusal",
    "RetentionRefused",
    "RetentionSweep",
    "active_delivery_hold_for",
    "active_hold_for",
    "classify_delivery",
    "classify_receipt",
    "is_delivery_redacted",
    "is_redacted",
    "place_delivery_legal_hold",
    "place_legal_hold",
    "purge_expired_delivery_payloads",
    "purge_expired_payloads",
    "purge_expired_replay_evidence",
    "redact_delivery",
    "redact_receipt",
    "release_delivery_legal_hold",
    "release_legal_hold",
    "resolve_retention_policy",
    "retention_backlog",
]

#: The module's immutable namespace. Same allocation as every other `mod_intg`
#: table — retention is a concern OF this module, not a module of its own.
SCHEMA: Final = module_schema("intg")

#: The tables this file owns, composed onto the manifest's `platform_tables` by
#: `manifest.py`. Declared here rather than appended to `models.PLATFORM_TABLES`
#: so the area that owns the table also declares it, and so two concurrent
#: slices do not edit one tuple.
RETENTION_PLATFORM_TABLES: tuple[str, ...] = (
    "receipt_legal_holds",
    "delivery_legal_holds",
)

#: The tombstone key. NAMESPACED because it is written into a column whose other
#: contents are provider-controlled: a bare `redacted` could collide with a
#: provider field and make a live payload read as an aged-out one.
#:
#: This exact string is a CROSS-REPOSITORY contract. `dotmac_integrator`'s
#: retention-backlog gauge counts receipts still holding real content by
#: matching on it, so changing it silently makes that metric read zero forever.
#: `test_integration_retention.py::test_the_redaction_marker_is_a_wire_contract`
#: pins the literal.
REDACTION_MARKER: Final = "__dotmac_redacted__"

#: The only columns redaction may write. Everything else on a receipt is
#: identity, ordering or outcome evidence — see this module's docstring — and
#: `test_redaction_touches_only_the_redactable_columns` diffs the whole row
#: against this tuple rather than trusting the list.
REDACTABLE_COLUMNS: Final[tuple[str, ...]] = (
    "payload_json",
    "headers_json",
    "consequence_json",
)

#: Every reason a receipt may be refused, as a CLOSED set. A refusal with an
#: ad-hoc reason string cannot be counted, alerted on or reviewed, and inventing
#: a word for an awkward case is how a refusal ledger stops meaning anything
#: (the same rule `EXTRACTION.toml`'s dispositions follow).
REFUSAL_REASONS: Final[tuple[str, ...]] = (
    "legal_hold",
    "leased",
    "unresolved",
    "reconciliation_required",
    "not_expired",
    "already_redacted",
    "no_payload",
    "raced",
)

#: A receipt a worker is holding right now. `processing` IS the claim on the
#: inbox side — `claim_receipt` sets it, `record_receipt_outcome` clears it —
#: and redacting a claimed receipt destroys the payload the claiming worker is
#: at that moment trying to interpret.
_LEASED_STATES: Final[frozenset[str]] = frozenset({"processing"})

#: Reached no outcome, or reached one an operator may still reverse.
#: `dead_letter` and `retryable` are BOTH here on purpose: `operations
#: .replay_receipt` moves either back to `verified` for reprocessing, and a
#: replay of a redacted receipt is a replay with nothing to replay.
_UNRESOLVED_STATES: Final[frozenset[str]] = frozenset(
    {"verified", "retryable", "dead_letter"}
)

#: The one state retention may act on: the receipt did what it was going to do,
#: `consequence_json` records what that was, and nothing will read the body
#: again.
_RESOLVED_STATE: Final = "processed"

#: Environment variable names. Values are Michael's to set; this module only
#: knows what to call them.
RETENTION_DAYS_VAR: Final = "INTEGRATION_PAYLOAD_RETENTION_DAYS"
RETENTION_REPLAY_EVIDENCE_DAYS_VAR: Final = "INTEGRATION_REPLAY_EVIDENCE_RETENTION_DAYS"
RETENTION_LEGAL_POLICY_OWNER_VAR: Final = "INTEGRATION_RETENTION_LEGAL_POLICY_OWNER"
RETENTION_BATCH_SIZE_VAR: Final = "INTEGRATION_RETENTION_BATCH_SIZE"

_DEFAULT_BATCH_SIZE: Final = 500
_MAX_BATCH_SIZE: Final = 10_000


class RetentionNotConfigured(RuntimeError):
    """Retention was asked to run before someone decided what it should do.

    Raised rather than defaulted. A library that guessed here would publish a
    data-retention posture nobody approved, and the guess would be invisible
    precisely because it worked.
    """


class RetentionRefused(RuntimeError):
    """This receipt or delivery may not be redacted, with its named rule."""

    def __init__(self, receipt_id: UUID | None, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.receipt_id = receipt_id
        self.reason = reason


# ── Policy ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """How long a payload is kept, and who is accountable for holds.

    No field has a default, and that is the design. `RetentionPolicy()`
    is a `TypeError`, so there is no way to obtain a policy without stating
    every decision — which is what keeps a period out of this library and in
    the deployment that has to answer for it.
    """

    #: Days a payload is kept after the event was RECEIVED. Received, not
    #: processed: a receipt that never processed still ages, and keying on
    #: `processed_at` would make a permanently stuck receipt immortal.
    payload_retention_days: int
    #: Days the deduplication identity and delivery outcome survive after the
    #: content is redacted. This is a separate legal decision, not an extension
    #: of the content period.
    replay_evidence_retention_days: int
    #: Who authorises and reviews legal holds. A hold with no accountable owner
    #: is a row nobody will ever dare release.
    legal_policy_owner: str
    #: Rows per sweep. Batched so a first run against years of history is a
    #: series of short transactions rather than one lock held for an hour.
    batch_size: int = _DEFAULT_BATCH_SIZE

    def __post_init__(self) -> None:
        if self.payload_retention_days < 1:
            raise ValueError(
                f"payload_retention_days={self.payload_retention_days} is not a "
                "retention period. Zero would redact a payload the moment the "
                "receipt was processed, before any replay could use it"
            )
        if self.replay_evidence_retention_days <= self.payload_retention_days:
            raise ValueError(
                "replay_evidence_retention_days must outlive "
                "payload_retention_days. Otherwise a provider redelivery can "
                "arrive after its identity was destroyed but while its content "
                "period still says the event is known"
            )
        if not self.legal_policy_owner.strip():
            raise ValueError(
                "legal_policy_owner must name an accountable owner. A hold "
                "nobody owns is a hold nobody will release"
            )
        if not 1 <= self.batch_size <= _MAX_BATCH_SIZE:
            raise ValueError(
                f"batch_size={self.batch_size} is outside [1, {_MAX_BATCH_SIZE}]"
            )

    def cutoff(self, now: datetime) -> datetime:
        """The instant before which a payload has aged out."""
        return now - timedelta(days=self.payload_retention_days)

    def evidence_cutoff(self, now: datetime) -> datetime:
        """The instant before which closed replay evidence has aged out."""
        return now - timedelta(days=self.replay_evidence_retention_days)


def resolve_retention_policy(source: Mapping[str, str]) -> RetentionPolicy:
    """Build a policy from configuration, or refuse and say what is missing.

    `source` is a mapping rather than `os.environ` reached for directly, so a
    deployment that keeps configuration somewhere else supplies it without this
    module growing a client — the same shape `ExecutionPolicy` uses, and the
    reason ADR-0009's "a secret is held, never dereferenced" is not violated
    here: nothing on this path reaches a network.

    There is no partial success. A period with no legal owner is a purge with
    nobody to refuse it, and an owner with no period is an owner of nothing.
    """
    missing = [
        name
        for name in (
            RETENTION_DAYS_VAR,
            RETENTION_REPLAY_EVIDENCE_DAYS_VAR,
            RETENTION_LEGAL_POLICY_OWNER_VAR,
        )
        if not str(source.get(name, "")).strip()
    ]
    if missing:
        raise RetentionNotConfigured(
            "payload retention is not configured: "
            + ", ".join(sorted(missing))
            + " must be set. This module ships no default retention period and "
            "no default legal-policy owner, because a default here becomes the "
            "deployment's data-retention policy without anyone deciding it"
        )

    raw_days = str(source[RETENTION_DAYS_VAR]).strip()
    raw_evidence_days = str(source[RETENTION_REPLAY_EVIDENCE_DAYS_VAR]).strip()
    try:
        days = int(raw_days)
        evidence_days = int(raw_evidence_days)
    except ValueError as exc:
        raise RetentionNotConfigured(
            "payload and replay-evidence retention periods must both be whole "
            "numbers of days"
        ) from exc

    raw_batch = str(source.get(RETENTION_BATCH_SIZE_VAR, "") or "").strip()
    try:
        batch = int(raw_batch) if raw_batch else _DEFAULT_BATCH_SIZE
    except ValueError as exc:
        raise RetentionNotConfigured(
            f"{RETENTION_BATCH_SIZE_VAR}={raw_batch!r} is not a whole number"
        ) from exc

    try:
        return RetentionPolicy(
            payload_retention_days=days,
            replay_evidence_retention_days=evidence_days,
            legal_policy_owner=str(source[RETENTION_LEGAL_POLICY_OWNER_VAR]).strip(),
            batch_size=batch,
        )
    except ValueError as exc:
        raise RetentionNotConfigured(str(exc)) from exc


# ── Legal hold ──────────────────────────────────────────────────────────────


class ReceiptLegalHold(Base):
    """A standing instruction that one receipt's content must not age out.

    PLATFORM plane, like every other `mod_intg` table: no `tenant_id`, no RLS,
    GRANTed to the platform roles and REVOKEd from `app_user` (ADR-0023).

    A hold is INSERTED and later released; it is never deleted, because "was
    this ever held, and by whom?" is the question a disclosure request asks
    after the hold has been lifted. `released_at IS NULL` is what makes a hold
    active, and a partial unique index enforces at most one active hold per
    receipt so "is it held?" has one answer.
    """

    __tablename__ = "receipt_legal_holds"
    __table_args__ = (
        # PARTIAL unique: many released holds may accumulate on one receipt over
        # the years, but two ACTIVE holds would give "is it held?" two rows and
        # two owners, and releasing one would look like releasing the hold.
        Index(
            "uq_receipt_legal_holds_active",
            "receipt_id",
            unique=True,
            postgresql_where=sa.text("released_at IS NULL"),
            sqlite_where=sa.text("released_at IS NULL"),
        ),
        Index("ix_receipt_legal_holds_released", "released_at"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    # A deliberate durable reference, not a cascading FK. `ig_0011` adds two
    # database triggers: a hold may only be created for a live receipt, and an
    # actively held receipt may not be deleted. Once a hold is released, the
    # receipt's replay evidence may reach its own retention limit and disappear
    # while this exact UUID and the hold history survive.
    receipt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    #: Why. Required — a hold with no stated reason cannot be reviewed, and the
    #: person who could explain it has left by the time anyone asks.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: The accountable owner, copied from the policy AT PLACEMENT. Copied rather
    #: than looked up later: the point of the field is who owned this decision
    #: when it was made, which a current configuration value cannot answer.
    policy_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    placed_by: Mapped[str] = mapped_column(String(160), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeliveryLegalHold(Base):
    """A standing instruction that one outbound payload must not age out.

    The table parallels :class:`ReceiptLegalHold` because the two ledgers have
    different owners and foreign keys. A polymorphic UUID would turn database
    referential integrity into a service convention.
    """

    __tablename__ = "delivery_legal_holds"
    __table_args__ = (
        Index(
            "uq_delivery_legal_holds_active",
            "delivery_id",
            unique=True,
            postgresql_where=sa.text("released_at IS NULL"),
            sqlite_where=sa.text("released_at IS NULL"),
        ),
        Index("ix_delivery_legal_holds_released", "released_at"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    delivery_id: Mapped[UUID] = mapped_column(
        Uuid(),
        sa.ForeignKey(f"{SCHEMA}.delivery_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    placed_by: Mapped[str] = mapped_column(String(160), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


def _active_delivery_hold_exists() -> Any:
    """Correlated SQL predicate for an active hold on a delivery."""

    return (
        sa.select(sa.literal(1))
        .select_from(DeliveryLegalHold)
        .where(
            DeliveryLegalHold.delivery_id == DeliveryAttempt.id,
            DeliveryLegalHold.released_at.is_(None),
        )
        .exists()
    )


def active_delivery_hold_for(db: Any, delivery_id: UUID) -> DeliveryLegalHold | None:
    hold = db.execute(
        sa.select(DeliveryLegalHold).where(
            DeliveryLegalHold.delivery_id == delivery_id,
            DeliveryLegalHold.released_at.is_(None),
        )
    ).scalar_one_or_none()
    if hold is None or isinstance(hold, DeliveryLegalHold):
        return hold
    raise TypeError(f"expected a DeliveryLegalHold, got {type(hold).__name__}")


def _active_hold_exists() -> Any:
    """`EXISTS (an unreleased hold on this receipt)`, as SQL.

    A correlated subquery rather than a Python set of held ids: the set would be
    read once and then be wrong for the rest of the sweep, and the whole point of
    a legal hold is that it wins even when it arrives late.
    """
    return (
        sa.select(sa.literal(1))
        .select_from(ReceiptLegalHold)
        .where(
            ReceiptLegalHold.receipt_id == InboxReceipt.id,
            ReceiptLegalHold.released_at.is_(None),
        )
        .exists()
    )


def active_hold_for(db: Any, receipt_id: UUID) -> ReceiptLegalHold | None:
    """The active hold on a receipt, or None."""
    hold = db.execute(
        sa.select(ReceiptLegalHold).where(
            ReceiptLegalHold.receipt_id == receipt_id,
            ReceiptLegalHold.released_at.is_(None),
        )
    ).scalar_one_or_none()
    # A checked narrowing rather than a cast: `db` is `Any` here, as everywhere
    # in this package, so the session's return type is unprovable and a cast
    # would ASSERT what this asks.
    if hold is None or isinstance(hold, ReceiptLegalHold):
        return hold
    raise TypeError(f"expected a ReceiptLegalHold, got {type(hold).__name__}")


#: The audit actions this module writes, WITHOUT the module prefix — the same
#: shape `lifecycle.ENDPOINT_AUDIT_ACTIONS` uses, so the manifest test can
#: compose the declared set from its writers instead of restating it. A code
#: listed here and written nowhere, or written and not listed, is what the
#: manifest-declaration rule exists to catch.
RETENTION_AUDIT_ACTIONS: tuple[str, ...] = (
    "retention.evidence.purged",
    "retention.payloads.redacted",
    "retention.hold.placed",
    "retention.hold.released",
)


def place_legal_hold(
    db: Any,
    receipt: InboxReceipt,
    *,
    policy: RetentionPolicy,
    reason: str,
    placed_by: str,
    actor_admin_id: UUID | None = None,
) -> ReceiptLegalHold:
    """Hold one receipt's content indefinitely. Idempotent per receipt.

    Placing a hold on an already-held receipt returns the existing hold rather
    than raising: a second hold request during an incident is a duplicate
    instruction, not an error, and refusing it invites someone to release the
    first one to "fix" it.
    """
    stated = reason.strip()
    if not stated:
        raise ValueError(
            "a legal hold requires a stated reason; an unexplained hold is "
            "indistinguishable from a mistake six months later"
        )
    if not placed_by.strip():
        raise ValueError("a legal hold requires the identity that placed it")

    existing = active_hold_for(db, receipt.id)
    if existing is not None:
        return existing

    hold = ReceiptLegalHold(
        receipt_id=receipt.id,
        reason=stated,
        policy_owner=policy.legal_policy_owner,
        placed_by=placed_by.strip(),
        placed_at=datetime.now(UTC),
    )
    db.add(hold)
    db.flush()

    record_operation(
        db,
        action="retention.hold.placed",
        entity_type="inbox_receipt",
        entity_id=str(receipt.id),
        actor_admin_id=actor_admin_id,
        # No `provider_event_id` and no payload. An audit detail is read by more
        # people and shipped to more places than the row it describes; the
        # internal receipt id identifies it without carrying a provider's
        # subscriber-linked identifier into a log pipeline.
        details={
            "reason": stated,
            "policy_owner": policy.legal_policy_owner,
            "placed_by": placed_by.strip(),
            "already_redacted": is_redacted(receipt),
        },
    )
    return hold


def release_legal_hold(
    db: Any,
    hold: ReceiptLegalHold,
    *,
    released_by: str,
    reason: str,
    actor_admin_id: UUID | None = None,
) -> ReceiptLegalHold:
    """Lift a hold, keeping the row as the record that it existed."""
    stated = reason.strip()
    if not stated:
        raise ValueError("releasing a legal hold requires a stated reason")
    if hold.released_at is not None:
        raise RetentionRefused(
            hold.receipt_id,
            "legal_hold",
            f"hold {hold.id} was already released at {hold.released_at}",
        )

    hold.released_at = datetime.now(UTC)
    hold.released_by = released_by.strip()
    hold.release_reason = stated

    record_operation(
        db,
        action="retention.hold.released",
        entity_type="inbox_receipt",
        entity_id=str(hold.receipt_id),
        actor_admin_id=actor_admin_id,
        details={
            "reason": stated,
            "released_by": released_by.strip(),
            "policy_owner": hold.policy_owner,
        },
    )
    return hold


def place_delivery_legal_hold(
    db: Any,
    delivery: DeliveryAttempt,
    *,
    policy: RetentionPolicy,
    reason: str,
    placed_by: str,
    actor_admin_id: UUID | None = None,
) -> DeliveryLegalHold:
    """Hold one outbound payload. Idempotent while a hold is active."""

    stated = reason.strip()
    actor = placed_by.strip()
    if not stated:
        raise ValueError("a delivery legal hold requires a stated reason")
    if not actor:
        raise ValueError("a delivery legal hold requires the identity that placed it")
    existing = active_delivery_hold_for(db, delivery.id)
    if existing is not None:
        return existing
    hold = DeliveryLegalHold(
        delivery_id=delivery.id,
        reason=stated,
        policy_owner=policy.legal_policy_owner,
        placed_by=actor,
        placed_at=datetime.now(UTC),
    )
    db.add(hold)
    db.flush()
    record_operation(
        db,
        action="retention.hold.placed",
        entity_type="delivery_attempt",
        entity_id=str(delivery.id),
        actor_admin_id=actor_admin_id,
        details={
            "reason": stated,
            "policy_owner": policy.legal_policy_owner,
            "placed_by": actor,
            "already_redacted": is_delivery_redacted(delivery),
        },
    )
    return hold


def release_delivery_legal_hold(
    db: Any,
    hold: DeliveryLegalHold,
    *,
    released_by: str,
    reason: str,
    actor_admin_id: UUID | None = None,
) -> DeliveryLegalHold:
    """Release an outbound hold while preserving its history row."""

    stated = reason.strip()
    actor = released_by.strip()
    if not stated:
        raise ValueError("releasing a delivery legal hold requires a stated reason")
    if not actor:
        raise ValueError("releasing a delivery legal hold requires an identity")
    if hold.released_at is not None:
        raise RetentionRefused(
            hold.delivery_id,
            "legal_hold",
            f"delivery hold {hold.id} was already released at {hold.released_at}",
        )
    hold.released_at = datetime.now(UTC)
    hold.released_by = actor
    hold.release_reason = stated
    record_operation(
        db,
        action="retention.hold.released",
        entity_type="delivery_attempt",
        entity_id=str(hold.delivery_id),
        actor_admin_id=actor_admin_id,
        details={
            "reason": stated,
            "released_by": actor,
            "policy_owner": hold.policy_owner,
        },
    )
    return hold


# ── Classification ──────────────────────────────────────────────────────────


def _as_utc(value: datetime) -> datetime:
    """Read a stored timestamp as UTC-aware.

    Not decoration. Postgres returns `timestamptz` aware; SQLite — which the
    unit suite runs on — returns the same column naive, and comparing the two
    raises `TypeError` rather than answering the question. A retention decision
    that crashes on one backend and not the other is a decision nobody can
    trust, so the boundary normalises once, here.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def is_redacted(receipt: InboxReceipt) -> bool:
    """Has this receipt's payload already aged out.

    Checks for the marker OBJECT, not merely the key: a provider that happened
    to send `{"__dotmac_redacted__": "yes"}` must not make a live payload read
    as an aged-out one.
    """
    payload = receipt.payload_json
    if not isinstance(payload, dict):
        return False
    return isinstance(payload.get(REDACTION_MARKER), dict)


def classify_receipt(
    receipt: InboxReceipt,
    *,
    policy: RetentionPolicy,
    now: datetime,
    held: bool,
) -> str | None:
    """The reason this receipt may NOT be redacted, or None if it may.

    Order is load-bearing. A legal hold is checked FIRST, so it refuses a
    receipt that is otherwise perfectly eligible — long processed, long
    expired, still carrying its body. Checking expiry first would make a hold
    look like it worked while the row it protects had already gone.
    """
    if held:
        return "legal_hold"
    if receipt.state in _LEASED_STATES:
        return "leased"
    if receipt.state == "reconciliation_required":
        return "reconciliation_required"
    if receipt.state in _UNRESOLVED_STATES:
        return "unresolved"
    if receipt.state != _RESOLVED_STATE:
        # A state this module has never heard of. Refuse rather than assume it
        # is safe: a new terminal state added upstream must opt IN to ageing.
        return "unresolved"
    if is_redacted(receipt):
        return "already_redacted"
    if receipt.payload_json is None:
        return "no_payload"
    if _as_utc(receipt.received_at) >= policy.cutoff(now):
        return "not_expired"
    return None


# ── Redaction ───────────────────────────────────────────────────────────────


def _tombstone(
    receipt: InboxReceipt, *, policy: RetentionPolicy, moment: datetime
) -> dict[str, object]:
    """What replaces the payload: evidence about the payload, never the payload.

    `key_count` rather than the key NAMES. A provider is free to key a map by a
    subscriber's phone number, so payload key names are provider DATA wearing
    schema's clothes — see this module's docstring.
    """
    payload = receipt.payload_json
    return {
        REDACTION_MARKER: {
            "redacted_at": moment.isoformat(),
            "retention_days": policy.payload_retention_days,
            "legal_policy_owner": policy.legal_policy_owner,
            # A COPY of the identity digest, so a reader looking only at this
            # column can still tell a redelivery from a collision without
            # trusting that the sibling column was left alone.
            "payload_digest": receipt.payload_digest,
            "key_count": len(payload) if isinstance(payload, dict) else 0,
        }
    }


def _redacted_headers(
    receipt: InboxReceipt, *, moment: datetime
) -> dict[str, object] | None:
    """Header NAMES survive; header VALUES do not.

    A name is our own vocabulary and its presence is evidence — that
    `x-hub-signature-256` was on the request is exactly what an "was this
    verified?" question needs, and the name leaks nothing. The value is the
    signature itself.
    """
    headers = receipt.headers_json
    if headers is None:
        return None
    names = sorted(str(name) for name in headers) if isinstance(headers, dict) else []
    return {
        REDACTION_MARKER: {
            "redacted_at": moment.isoformat(),
            "header_names": names,
        }
    }


def _redacted_consequence(
    receipt: InboxReceipt, *, moment: datetime
) -> dict[str, object] | None:
    """The OUTCOME survives as a digest and its key names; its values do not.

    This is the half of the row a late redelivery is compared against. Keeping
    the digest means "did replaying this produce the same consequence?" is still
    answerable after the values are gone — which is the difference between a
    safe replay and a guess. Consequence keys are written by this fleet, not by
    a provider, so unlike payload keys they are safe to keep.
    """
    consequence = receipt.consequence_json
    if consequence is None:
        return None
    keys = sorted(str(k) for k in consequence) if isinstance(consequence, dict) else []
    return {
        REDACTION_MARKER: {
            "redacted_at": moment.isoformat(),
            "consequence_digest": payload_digest(consequence),
            "consequence_keys": keys,
        }
    }


def redact_receipt(
    db: Any,
    receipt: InboxReceipt,
    *,
    policy: RetentionPolicy,
    now: datetime | None = None,
) -> InboxReceipt:
    """Age out one receipt's content. Raises `RetentionRefused` if it may not.

    Two gates, deliberately, and the second is the one that matters under
    concurrency:

    1. :func:`classify_receipt` in Python, which produces the NAMED refusal an
       operator reads;
    2. a CONDITIONAL UPDATE whose WHERE clause repeats every condition —
       including `NOT EXISTS (active hold)`. A receipt claimed by a worker, or
       held by a lawyer, in the microseconds between the two is refused by the
       database, which is the only party that can decide a race.

    `rowcount == 1` is the redaction, in the same idiom as
    `execution.claim_delivery`.
    """
    moment = now or datetime.now(UTC)
    held = active_hold_for(db, receipt.id) is not None
    reason = classify_receipt(receipt, policy=policy, now=moment, held=held)
    if reason is not None:
        raise RetentionRefused(
            receipt.id,
            reason,
            f"receipt {receipt.id} is refused for retention: {reason} "
            f"(state={receipt.state!r})",
        )

    result = db.execute(
        sa.update(InboxReceipt)
        .where(
            InboxReceipt.id == receipt.id,
            InboxReceipt.state == _RESOLVED_STATE,
            InboxReceipt.received_at < policy.cutoff(moment),
            InboxReceipt.payload_json.is_not(None),
            ~_active_hold_exists(),
        )
        .values(
            payload_json=_tombstone(receipt, policy=policy, moment=moment),
            headers_json=_redacted_headers(receipt, moment=moment),
            consequence_json=_redacted_consequence(receipt, moment=moment),
        )
        # The DATABASE evaluates the predicate. Letting SQLAlchemy re-run it in
        # Python to synchronise the session would move the decision back into
        # the process that lost the race.
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise RetentionRefused(
            receipt.id,
            "raced",
            f"receipt {receipt.id} changed under the sweep — its state, age or "
            "legal hold no longer permits redaction. Nothing was written",
        )
    db.refresh(receipt)
    return receipt


# ── Sweep ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RetentionRefusal:
    """One receipt this sweep would not touch, and why."""

    receipt_id: UUID
    reason: str


@dataclass(frozen=True, slots=True)
class RetentionSweep:
    """What one batch did, and what it deliberately did not do."""

    cutoff: datetime
    batch_size: int
    #: Rows redacted by this run.
    redacted: int = 0
    #: Ids redacted, so the audit record names them. Internal UUIDs only —
    #: never a `provider_event_id`.
    redacted_ids: tuple[UUID, ...] = ()
    #: Rows this run refused, individually. Populated when a candidate was read
    #: and then lost its eligibility — a hold arriving mid-sweep, a claim.
    refusals: tuple[RetentionRefusal, ...] = ()
    #: The whole expired population, counted by why it was left alone. This is
    #: the number an operator actually needs: "8 redacted" says nothing about
    #: the 4,000 receipts a stuck queue is keeping alive.
    refused_by_reason: Mapping[str, int] = field(default_factory=dict)

    @property
    def refused_total(self) -> int:
        return sum(self.refused_by_reason.values())

    def as_dict(self) -> dict[str, object]:
        return {
            "cutoff": self.cutoff.isoformat(),
            "batch_size": self.batch_size,
            "redacted": self.redacted,
            "refused_total": self.refused_total,
            "refused_by_reason": dict(self.refused_by_reason),
        }


def _expired_with_content(cutoff: datetime) -> list[Any]:
    """The window retention cares about: aged, and still holding real content.

    `received_at`, not `processed_at`: a receipt that never processed still
    ages, and keying age on processing would make a permanently stuck receipt
    immortal — the exact row most likely to hold a message body.
    """
    return [
        InboxReceipt.received_at < cutoff,
        InboxReceipt.payload_json.is_not(None),
        # Already-redacted rows leave the population. This is what makes the
        # sweep idempotent: the second run has no candidates, writes nothing and
        # records no audit event.
        #
        # `.as_string()` is load-bearing, not decoration. Without it SQLAlchemy
        # wraps the extracted element for SQLite as
        # `JSON_QUOTE(JSON_EXTRACT(...))`, and `JSON_QUOTE(NULL)` returns the
        # STRING 'null' rather than SQL NULL — so `IS NULL` was never true and
        # the predicate excluded every row, retiring nothing. It rendered
        # correctly on Postgres, which is what made it survive: the canaries
        # that run against real Postgres were unaffected, and only the SQLite
        # unit suite could see it.
        InboxReceipt.payload_json[REDACTION_MARKER].as_string().is_(None),
    ]


def _refusal_counts(db: Any, cutoff: datetime) -> dict[str, int]:
    """Count the expired population by the reason it is being kept.

    Aggregates rather than a row scan, and computed over the WHOLE expired
    window rather than over one batch — a batch is a unit of work, not a unit of
    reporting, and reporting per batch would let 4,000 stuck receipts hide
    behind a batch size of 500.
    """
    base = _expired_with_content(cutoff)
    held = _active_hold_exists()

    def _count(*where: Any) -> int:
        query = (
            sa.select(sa.func.count()).select_from(InboxReceipt).where(*base, *where)
        )
        return int(db.execute(query).scalar_one() or 0)

    counts = {
        "legal_hold": _count(held),
        "leased": _count(~held, InboxReceipt.state.in_(sorted(_LEASED_STATES))),
        "reconciliation_required": _count(
            ~held, InboxReceipt.state == "reconciliation_required"
        ),
        "unresolved": _count(~held, InboxReceipt.state.in_(sorted(_UNRESOLVED_STATES))),
    }
    # Zeroes are dropped so a report reads as a list of problems rather than a
    # table of mostly-noughts; `refused_total` still sums correctly.
    return {reason: count for reason, count in counts.items() if count}


def purge_expired_payloads(
    db: Any,
    *,
    policy: RetentionPolicy,
    now: datetime | None = None,
    actor_admin_id: UUID | None = None,
) -> RetentionSweep:
    """Redact one batch of expired payloads. Idempotent, batched, audited.

    Safe to run on a timer and safe to run twice: the marker written by the
    first run removes those rows from the candidate set, so a second run finds
    nothing, writes nothing and — deliberately — records no audit event. A
    cleanup that audited its own no-ops would bury the runs that did something.
    """
    moment = now or datetime.now(UTC)
    cutoff = policy.cutoff(moment)

    candidates = list(
        db.execute(
            sa.select(InboxReceipt)
            .where(
                *_expired_with_content(cutoff),
                InboxReceipt.state == _RESOLVED_STATE,
                ~_active_hold_exists(),
            )
            # Oldest first. A sweep that cannot finish should have retired the
            # oldest content, not an arbitrary slice.
            .order_by(InboxReceipt.received_at)
            .limit(policy.batch_size)
        )
        .scalars()
        .all()
    )

    redacted: list[UUID] = []
    refusals: list[RetentionRefusal] = []
    for receipt in candidates:
        try:
            redact_receipt(db, receipt, policy=policy, now=moment)
        except RetentionRefused as refused:
            # Recorded, never swallowed. A candidate that lost its eligibility
            # between the read and the write is exactly the event a silent
            # `continue` would hide.
            refusals.append(RetentionRefusal(receipt.id, refused.reason))
            continue
        redacted.append(receipt.id)

    sweep = RetentionSweep(
        cutoff=cutoff,
        batch_size=policy.batch_size,
        redacted=len(redacted),
        redacted_ids=tuple(redacted),
        refusals=tuple(refusals),
        refused_by_reason=_refusal_counts(db, cutoff),
    )

    if redacted or refusals:
        record_operation(
            db,
            action="retention.payloads.redacted",
            entity_type="inbox_receipt",
            actor_admin_id=actor_admin_id,
            details={
                **sweep.as_dict(),
                "receipt_ids": [str(identifier) for identifier in redacted],
                "refusals": [
                    {"receipt_id": str(r.receipt_id), "reason": r.reason}
                    for r in refusals
                ],
                # `legal_policy_owner` is recorded on the RUN, so "who was
                # accountable when this content was destroyed" is answerable
                # from the ledger and not only from today's configuration.
                "legal_policy_owner": policy.legal_policy_owner,
                "retention_days": policy.payload_retention_days,
            },
        )
    return sweep


# ── Outbound payload retention ─────────────────────────────────────────────


def is_delivery_redacted(delivery: DeliveryAttempt) -> bool:
    """Whether the outbox payload has been replaced by a retention tombstone."""

    payload = delivery.payload_json
    return isinstance(payload, dict) and isinstance(payload.get(REDACTION_MARKER), dict)


def classify_delivery(
    delivery: DeliveryAttempt,
    *,
    policy: RetentionPolicy,
    now: datetime,
    held: bool,
) -> str | None:
    """The named reason an outbound payload may not be redacted."""

    if held:
        return "legal_hold"
    if delivery.state == "in_flight":
        return "leased"
    if delivery.state == "reconciliation_required":
        return "reconciliation_required"
    # Dead-letter and retryable deliveries remain replayable through the
    # operations API, so their payload is unresolved content, not old evidence.
    if delivery.state != "delivered":
        return "unresolved"
    if is_delivery_redacted(delivery):
        return "already_redacted"
    if delivery.payload_json is None:
        return "no_payload"
    if _as_utc(delivery.created_at) >= policy.cutoff(now):
        return "not_expired"
    return None


def _delivery_tombstone(
    delivery: DeliveryAttempt, *, policy: RetentionPolicy, moment: datetime
) -> dict[str, object]:
    payload = delivery.payload_json
    return {
        REDACTION_MARKER: {
            "redacted_at": moment.isoformat(),
            "retention_days": policy.payload_retention_days,
            "legal_policy_owner": policy.legal_policy_owner,
            "payload_digest": delivery.payload_digest,
            "key_count": len(payload) if isinstance(payload, dict) else 0,
        }
    }


def redact_delivery(
    db: Any,
    delivery: DeliveryAttempt,
    *,
    policy: RetentionPolicy,
    now: datetime | None = None,
) -> DeliveryAttempt:
    """Redact one delivered payload with a hold-aware conditional update."""

    moment = now or datetime.now(UTC)
    held = active_delivery_hold_for(db, delivery.id) is not None
    refusal = classify_delivery(delivery, policy=policy, now=moment, held=held)
    if refusal is not None:
        detail = (
            f"delivery {delivery.id} has an active legal hold"
            if refusal == "legal_hold"
            else f"delivery {delivery.id} cannot be redacted: {refusal}"
        )
        raise RetentionRefused(delivery.id, refusal, detail)

    result = db.execute(
        sa.update(DeliveryAttempt)
        .where(
            DeliveryAttempt.id == delivery.id,
            DeliveryAttempt.state == "delivered",
            DeliveryAttempt.created_at < policy.cutoff(moment),
            DeliveryAttempt.payload_json.is_not(None),
            ~_active_delivery_hold_exists(),
        )
        .values(
            payload_json=_delivery_tombstone(delivery, policy=policy, moment=moment)
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise RetentionRefused(
            delivery.id,
            "raced",
            f"delivery {delivery.id} changed under the sweep; nothing was written",
        )
    db.refresh(delivery)
    return delivery


@dataclass(frozen=True, slots=True)
class DeliveryRetentionRefusal:
    """One outbound delivery this sweep lost to a concurrent refusal."""

    delivery_id: UUID
    reason: str


@dataclass(frozen=True, slots=True)
class DeliveryRetentionSweep:
    """What one bounded outbound-content sweep changed and refused."""

    cutoff: datetime
    batch_size: int
    redacted: int = 0
    redacted_ids: tuple[UUID, ...] = ()
    refusals: tuple[DeliveryRetentionRefusal, ...] = ()
    refused_by_reason: Mapping[str, int] = field(default_factory=dict)

    @property
    def refused_total(self) -> int:
        return sum(self.refused_by_reason.values())

    def as_dict(self) -> dict[str, object]:
        return {
            "cutoff": self.cutoff.isoformat(),
            "batch_size": self.batch_size,
            "redacted": self.redacted,
            "refused_total": self.refused_total,
            "refused_by_reason": dict(self.refused_by_reason),
        }


def _expired_delivery_content(cutoff: datetime) -> list[Any]:
    return [
        DeliveryAttempt.created_at < cutoff,
        DeliveryAttempt.payload_json.is_not(None),
        DeliveryAttempt.payload_json[REDACTION_MARKER].as_string().is_(None),
    ]


def _delivery_refusal_counts(db: Any, cutoff: datetime) -> dict[str, int]:
    base = _expired_delivery_content(cutoff)
    held = _active_delivery_hold_exists()

    def _count(*where: Any) -> int:
        return int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(DeliveryAttempt)
                .where(*base, *where)
            ).scalar_one()
            or 0
        )

    counts = {
        "legal_hold": _count(held),
        "leased": _count(~held, DeliveryAttempt.state == "in_flight"),
        "reconciliation_required": _count(
            ~held, DeliveryAttempt.state == "reconciliation_required"
        ),
        "unresolved": _count(
            ~held,
            DeliveryAttempt.state.not_in(
                ("delivered", "in_flight", "reconciliation_required")
            ),
        ),
    }
    return {reason: count for reason, count in counts.items() if count}


def purge_expired_delivery_payloads(
    db: Any,
    *,
    policy: RetentionPolicy,
    now: datetime | None = None,
    actor_admin_id: UUID | None = None,
) -> DeliveryRetentionSweep:
    """Redact one oldest-first batch of delivered outbox payloads."""

    moment = now or datetime.now(UTC)
    cutoff = policy.cutoff(moment)
    candidates = list(
        db.execute(
            sa.select(DeliveryAttempt)
            .where(
                *_expired_delivery_content(cutoff),
                DeliveryAttempt.state == "delivered",
                ~_active_delivery_hold_exists(),
            )
            .order_by(DeliveryAttempt.created_at, DeliveryAttempt.id)
            .limit(policy.batch_size)
        )
        .scalars()
        .all()
    )
    redacted: list[UUID] = []
    refusals: list[DeliveryRetentionRefusal] = []
    for delivery in candidates:
        try:
            redact_delivery(db, delivery, policy=policy, now=moment)
        except RetentionRefused as refused:
            refusals.append(DeliveryRetentionRefusal(delivery.id, refused.reason))
            continue
        redacted.append(delivery.id)

    sweep = DeliveryRetentionSweep(
        cutoff=cutoff,
        batch_size=policy.batch_size,
        redacted=len(redacted),
        redacted_ids=tuple(redacted),
        refusals=tuple(refusals),
        refused_by_reason=_delivery_refusal_counts(db, cutoff),
    )
    if redacted or refusals:
        record_operation(
            db,
            action="retention.payloads.redacted",
            entity_type="delivery_attempt",
            actor_admin_id=actor_admin_id,
            details={
                **sweep.as_dict(),
                "delivery_ids": [str(identifier) for identifier in redacted],
                "refusals": [
                    {"delivery_id": str(item.delivery_id), "reason": item.reason}
                    for item in refusals
                ],
                "legal_policy_owner": policy.legal_policy_owner,
                "retention_days": policy.payload_retention_days,
            },
        )
    return sweep


# ── Replay-evidence sweep ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReplayEvidenceSweep:
    """Closed receipt evidence removed after its separately ruled period."""

    cutoff: datetime
    batch_size: int
    purged: int = 0
    purged_ids: tuple[UUID, ...] = ()
    refused_by_reason: Mapping[str, int] = field(default_factory=dict)

    @property
    def refused_total(self) -> int:
        return sum(self.refused_by_reason.values())

    def as_dict(self) -> dict[str, object]:
        return {
            "cutoff": self.cutoff.isoformat(),
            "batch_size": self.batch_size,
            "purged": self.purged,
            "refused_total": self.refused_total,
            "refused_by_reason": dict(self.refused_by_reason),
        }


def _has_redaction_marker() -> Any:
    """SQL predicate for a payload whose content-retention sweep completed."""

    return InboxReceipt.payload_json[REDACTION_MARKER].as_string().is_not(None)


def _evidence_refusal_counts(db: Any, cutoff: datetime) -> dict[str, int]:
    """Explain why aged replay evidence remains instead of hiding the backlog."""

    expired = InboxReceipt.received_at < cutoff
    held = _active_hold_exists()

    def _count(*where: Any) -> int:
        return int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(InboxReceipt)
                .where(expired, *where)
            ).scalar_one()
            or 0
        )

    counts = {
        "legal_hold": _count(held),
        "unresolved": _count(~held, InboxReceipt.state != _RESOLVED_STATE),
        "not_redacted": _count(
            ~held,
            InboxReceipt.state == _RESOLVED_STATE,
            ~_has_redaction_marker(),
        ),
    }
    return {reason: count for reason, count in counts.items() if count}


def purge_expired_replay_evidence(
    db: Any,
    *,
    policy: RetentionPolicy,
    now: datetime | None = None,
    actor_admin_id: UUID | None = None,
) -> ReplayEvidenceSweep:
    """Delete one batch of closed replay evidence after its finite lifetime.

    A row is eligible only after the content sweep has written its tombstone,
    the receipt reached its final outcome, its independently ruled evidence
    period elapsed, and no active legal hold exists. The DELETE repeats every
    predicate, so a hold placed after selection still wins in the database.

    Released legal-hold rows deliberately survive. ``ig_0011`` replaces their
    cascading FK with triggers that preserve both structural guarantees: a new
    hold must name an existing receipt, and an active hold blocks receipt
    deletion. A released hold is history, so its receipt UUID remains after the
    receipt evidence reaches this limit.
    """

    moment = now or datetime.now(UTC)
    cutoff = policy.evidence_cutoff(moment)
    candidate_ids = tuple(
        db.execute(
            sa.select(InboxReceipt.id)
            .where(
                InboxReceipt.received_at < cutoff,
                InboxReceipt.state == _RESOLVED_STATE,
                _has_redaction_marker(),
                ~_active_hold_exists(),
            )
            .order_by(InboxReceipt.received_at, InboxReceipt.id)
            .limit(policy.batch_size)
        )
        .scalars()
        .all()
    )

    purged_ids: tuple[UUID, ...] = ()
    if candidate_ids:
        purged_ids = tuple(
            db.execute(
                sa.delete(InboxReceipt)
                .where(
                    InboxReceipt.id.in_(candidate_ids),
                    InboxReceipt.received_at < cutoff,
                    InboxReceipt.state == _RESOLVED_STATE,
                    _has_redaction_marker(),
                    ~_active_hold_exists(),
                )
                .returning(InboxReceipt.id)
                .execution_options(synchronize_session="fetch")
            )
            .scalars()
            .all()
        )

    sweep = ReplayEvidenceSweep(
        cutoff=cutoff,
        batch_size=policy.batch_size,
        purged=len(purged_ids),
        purged_ids=purged_ids,
        refused_by_reason=_evidence_refusal_counts(db, cutoff),
    )
    if purged_ids:
        record_operation(
            db,
            action="retention.evidence.purged",
            entity_type="inbox_receipt",
            actor_admin_id=actor_admin_id,
            details={
                **sweep.as_dict(),
                "receipt_ids": [str(identifier) for identifier in purged_ids],
                "legal_policy_owner": policy.legal_policy_owner,
                "replay_evidence_retention_days": (
                    policy.replay_evidence_retention_days
                ),
            },
        )
    return sweep


# ── Derived backlog ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RetentionBacklog:
    """Counts, at one moment, derived from the ledger. Nothing here is stored."""

    #: Expired and still holding real content, whatever the reason.
    expired_with_payload: int = 0
    #: Of those, what the next sweep would actually redact.
    eligible_now: int = 0
    #: The rest, by the rule keeping them.
    refused_by_reason: Mapping[str, int] = field(default_factory=dict)
    #: Age of the oldest expired receipt still holding content. A COUNT alone
    #: hides one ancient stuck row behind a healthy-looking total.
    oldest_expired_age_seconds: int | None = None
    #: Active legal holds, at any age.
    active_legal_holds: int = 0

    @property
    def needs_attention(self) -> bool:
        """True when content is being kept past its period for any reason.

        `eligible_now` counts too: work the sweep has not got to is a backlog,
        not health, and a scheduler that stopped looks identical to a quiet
        night unless someone counts it.
        """
        return bool(self.expired_with_payload)

    def as_dict(self) -> dict[str, object]:
        return {
            "expired_with_payload": self.expired_with_payload,
            "eligible_now": self.eligible_now,
            "refused_by_reason": dict(self.refused_by_reason),
            "oldest_expired_age_seconds": self.oldest_expired_age_seconds,
            "active_legal_holds": self.active_legal_holds,
        }


def retention_backlog(
    db: Any, *, policy: RetentionPolicy, now: datetime | None = None
) -> RetentionBacklog:
    """How much content is being kept past its period, and why.

    Derived at read time. There is no retention status column on any model in
    this module and there must never be one: a stored summary is a second
    writer over facts the ledger already holds, and it goes stale the instant a
    sweep dies half-way — which is the moment it is read.
    """
    moment = now or datetime.now(UTC)
    cutoff = policy.cutoff(moment)
    base = _expired_with_content(cutoff)

    expired = int(
        db.execute(
            sa.select(sa.func.count()).select_from(InboxReceipt).where(*base)
        ).scalar_one()
        or 0
    )
    eligible = int(
        db.execute(
            sa.select(sa.func.count())
            .select_from(InboxReceipt)
            .where(
                *base,
                InboxReceipt.state == _RESOLVED_STATE,
                ~_active_hold_exists(),
            )
        ).scalar_one()
        or 0
    )
    oldest = db.execute(
        sa.select(sa.func.min(InboxReceipt.received_at)).where(*base)
    ).scalar_one_or_none()
    holds = int(
        db.execute(
            sa.select(sa.func.count())
            .select_from(ReceiptLegalHold)
            .where(ReceiptLegalHold.released_at.is_(None))
        ).scalar_one()
        or 0
    )

    age: int | None = None
    if oldest is not None:
        age = max(0, int((moment - _as_utc(oldest)).total_seconds()))

    return RetentionBacklog(
        expired_with_payload=expired,
        eligible_now=eligible,
        refused_by_reason=_refusal_counts(db, cutoff),
        oldest_expired_age_seconds=age,
        active_legal_holds=holds,
    )
