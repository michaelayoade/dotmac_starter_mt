"""Outbound repair — replay by key, dead-letter inspection, ambiguous reconciliation.

Three operator capabilities that share one question: *may this stored outbound
command be turned back into a live effect?* Answering it in three places would
produce three answers, so this module is the single owner of that decision and
every entry point below routes through :func:`classify_repair`.

## What was already here, and is NOT rebuilt

======================================= ====================================
already owned elsewhere                 owner
======================================= ====================================
the outcome vocabulary                  :mod:`dotmac_integration.retry` —
                                        ``SUCCEEDED`` / ``RETRYABLE`` /
                                        ``TERMINAL`` /
                                        ``RECONCILIATION_REQUIRED``
returning a stuck delivery to the queue :func:`operations.replay_delivery`
at-most-once execution                  ``dotmac_kernel.idempotency``, via
                                        :mod:`dotmac_integration.idempotency`
the platform audit trail                ``dotmac_kernel.audit``, via
                                        :func:`operations.record_operation`
"is this payload a retention tombstone" :func:`retention.is_delivery_redacted`
"is this delivery under legal hold"     :func:`retention.active_delivery_hold_for`
======================================= ====================================

The four-state outcome vocabulary in particular is NOT re-declared here. A
second enum meaning the same four things is how a queue acquires two opinions
about whether an attempt may be retried, and `retry.OutcomeStatus` has held
`RECONCILIATION_REQUIRED` — the ambiguous state — since the port.

## Replay reuses the stored request, and the signature is the property

:func:`replay_by_idempotency_key` has no ``payload``, ``body`` or
``observation`` parameter, and it never rebuilds one from current state. What
is re-dispatched is exactly the row's own ``payload_json``, checked against the
``payload_digest`` recorded when it was enqueued. That is the same shape
`receipt_delivery.build_product_request` uses on the inbound side: a caller
cannot smuggle new content into a replay because there is no parameter through
which to pass it, and `test_replay_has_no_content_parameter` pins it.

The verification the ORIGINAL command had is therefore the digest. A row whose
payload was redacted by the retention sweep, lost, or no longer digests to what
was recorded has no verified request left, and :func:`repair_dead_letter`
REFUSES it rather than sending whatever bytes remain. A tombstone is not a
payload; re-deriving one from today's state would deliver something the
recorded evidence does not describe.

## A replay of a landed effect returns the outcome; it does not re-dispatch

`operations.replay_delivery` raises on a ``delivered`` row, which is right for
the mechanism and wrong for the operator: "replay this key" asked a question,
and the true answer is *it already landed, here is what happened*. So the
delivered case returns a :class:`ReplayDecision` carrying the
:class:`RecordedOutcome`, changes nothing, and writes nothing.

## An ambiguous outcome is reconciled, never blindly replayed

This module deliberately REFUSES to replay a ``reconciliation_required``
delivery, even though `operations.replay_delivery` would accept one. That state
means an attempt may have half-landed at the provider; replaying it risks doing
the effect twice and dead-lettering it hides it. It is resolved by
:func:`prepare_reconciliation` / :func:`reconcile_with_evidence` against
evidence the PROVIDER supplies, and only a provider-proven ``NOT_LANDED``
returns it to the queue.

## No session is held across the probe

Reconciliation has the same three phases as
:mod:`dotmac_integration.dispatch`, for the same reason: a transaction open
across a provider round-trip holds row locks for the duration of someone else's
outage. :class:`ReconciliationSubject` is a frozen value carrying everything the
probe needs and nothing that holds a connection, and
:class:`ProviderEvidenceProbe` takes no session — the boundary is enforced by
what a caller cannot pass.

## Nothing here is stored twice

There is no reconciliation table and no repair ledger. What was decided, by
whom and on what evidence is written to the fleet's ONE platform audit trail
through `operations.record_operation`; what a delivery currently is, is read
from `delivery_attempts` at report time. Both follow
`operations.health_report`'s rule: a stored summary is a second writer over
facts the ledger already holds, and it drifts the moment a worker dies
mid-update. This module therefore adds no migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Protocol, runtime_checkable
from uuid import UUID

import sqlalchemy as sa

from dotmac_integration.execution import payload_digest
from dotmac_integration.models import (
    CapabilityBinding,
    ConnectorInstallation,
    DeliveryAttempt,
)
from dotmac_integration.operations import record_operation, replay_delivery
from dotmac_integration.retention import (
    active_delivery_hold_for,
    is_delivery_redacted,
)
from dotmac_integration.retry import Outcome, OutcomeStatus

__all__ = [
    "DEAD_LETTER_PAGE_SIZE_VAR",
    "DEFAULT_DEAD_LETTER_PAGE_SIZE",
    "INCONCLUSIVE_CODE",
    "MAX_DEAD_LETTER_PAGE_SIZE",
    "REPAIR_AUDIT_ACTIONS",
    "REPAIR_REFUSAL_REASONS",
    "DeadLetterEntry",
    "DeliveryNotFound",
    "EvidenceStatus",
    "ProviderEvidence",
    "ProviderEvidenceProbe",
    "ProviderVerdict",
    "ReconciliationDecision",
    "ReconciliationSubject",
    "RecordedOutcome",
    "RepairRefused",
    "ReplayDecision",
    "RequestEvidence",
    "ambiguous_report",
    "classify_repair",
    "classify_request_evidence",
    "dead_letter_report",
    "inspect_delivery",
    "prepare_reconciliation",
    "reconcile_with_evidence",
    "repair_dead_letter",
    "replay_by_idempotency_key",
    "request_evidence",
    "resolve_dead_letter_page_size",
]


#: The audit actions this module writes, WITHOUT the module prefix — the shape
#: `lifecycle.ENDPOINT_AUDIT_ACTIONS` and `retention.RETENTION_AUDIT_ACTIONS`
#: use, so the manifest test composes the declared set from its writers instead
#: of restating it.
#:
#: ONE code, with the verdict in its details, rather than three. The three
#: verdicts are not three operations: they are one reconciliation whose answer
#: differed, and splitting them would make "how many deliveries were reconciled
#: last week" a query over a vocabulary instead of over one action.
#:
#: Requeueing itself gets no code here — `operations.replay_delivery` already
#: writes `integration.delivery.replayed`, and it stays the one writer of that
#: fact whether an operator or a reconciler asked for it.
REPAIR_AUDIT_ACTIONS: Final[tuple[str]] = ("delivery.reconciled",)


# ── Errors ──────────────────────────────────────────────────────────────────


class DeliveryNotFound(LookupError):
    """No outbound command carries that key for that installation.

    `LookupError`, not `RepairRefused`: nothing was refused, there was nothing
    to refuse. An assembly mapping this to 404 rather than 409 is reading it
    correctly.
    """


class RepairRefused(RuntimeError):
    """This delivery may not be turned back into a live effect, with its rule.

    Carries a NAMED `reason` from :data:`REPAIR_REFUSAL_REASONS` as well as the
    message, so a caller can branch on the rule without parsing prose — the
    shape `retention.RetentionRefused` already uses for the same kind of
    refusal.
    """

    def __init__(self, delivery_id: UUID | None, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.delivery_id = delivery_id
        self.reason = reason


# ── Request evidence ────────────────────────────────────────────────────────


class EvidenceStatus(str, Enum):
    """Whether the immutable record of what was attempted is still verifiable."""

    #: `payload_json` is present and digests to the recorded `payload_digest`.
    INTACT = "intact"
    #: No payload at all. Nothing to re-send.
    MISSING = "missing"
    #: The retention sweep replaced the content with its tombstone. The
    #: identity survived deliberately; the content did not, and a tombstone is
    #: not a payload.
    REDACTED = "redacted"
    #: A payload is present but does not digest to what was recorded. Something
    #: rewrote the row; re-sending it would deliver content the recorded
    #: evidence does not describe.
    DIGEST_MISMATCH = "digest_mismatch"


def _digest_candidates(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Every digest `enqueue_delivery` could legitimately have recorded.

    Two, not one, and the second is not defensive padding. `enqueue_delivery`
    digests the payload it was GIVEN and then stores `{"value": payload}` when
    that payload was not a mapping — so for a scalar or list payload the stored
    digest is over the unwrapped value while the stored JSON is the envelope.
    Recomputing only over the stored JSON would report `DIGEST_MISMATCH` for
    every non-mapping payload ever enqueued, and this module would refuse to
    repair rows that are perfectly intact.

    The envelope is recognised structurally — exactly one key, named `value` —
    which is precisely the shape `enqueue_delivery` writes and nothing else.
    """
    candidates = [payload_digest(payload)]
    if set(payload) == {"value"}:
        candidates.append(payload_digest(payload["value"]))
    return tuple(candidates)


def classify_request_evidence(delivery: DeliveryAttempt) -> EvidenceStatus:
    """Whether this row still carries the request it was enqueued with. Pure."""
    if is_delivery_redacted(delivery):
        return EvidenceStatus.REDACTED
    payload = delivery.payload_json
    if payload is None:
        return EvidenceStatus.MISSING
    if delivery.payload_digest not in _digest_candidates(payload):
        return EvidenceStatus.DIGEST_MISMATCH
    return EvidenceStatus.INTACT


@dataclass(frozen=True, slots=True)
class RequestEvidence:
    """What was attempted, read from the row and checked against its digest.

    The payload is exposed as a read-only mapping so a caller that holds this
    value cannot mutate the row's content through it — the same reason
    `receipt_delivery` deep-freezes an observation before fingerprinting it.
    """

    delivery_id: UUID
    installation_id: UUID
    capability_binding_id: UUID | None
    event_type: str
    idempotency_key: str
    payload_digest: str
    payload: Mapping[str, object]
    status: EvidenceStatus

    @property
    def is_verified(self) -> bool:
        return self.status is EvidenceStatus.INTACT


def request_evidence(delivery: DeliveryAttempt) -> RequestEvidence:
    """Project one row's request evidence.

    Never raises: inspection has to work on exactly the rows that cannot be
    repaired, which is the only reason anyone opens it.
    """
    payload = delivery.payload_json or {}
    return RequestEvidence(
        delivery_id=delivery.id,
        installation_id=delivery.installation_id,
        capability_binding_id=delivery.capability_binding_id,
        event_type=delivery.event_type,
        idempotency_key=delivery.idempotency_key,
        payload_digest=delivery.payload_digest,
        payload=MappingProxyType(dict(payload)),
        status=classify_request_evidence(delivery),
    )


# ── The one repair decision ─────────────────────────────────────────────────

#: Every named reason this module refuses to make a stored command live again.
#: A CLOSED set: "it looked wrong" is not a reason, and inventing a word for an
#: awkward row is how a refusal vocabulary stops meaning anything.
REPAIR_REFUSAL_REASONS: Final[tuple[str, ...]] = (
    "already_delivered",
    "already_queued",
    "in_flight",
    "ambiguous_outcome",
    "evidence_missing",
    "evidence_redacted",
    "evidence_digest_mismatch",
    "binding_missing",
    "raced",
    "not_dead_letter",
    "not_ambiguous",
)

_REFUSAL_DETAIL: Mapping[str, str] = MappingProxyType(
    {
        "already_delivered": (
            "this command already landed. Replaying a delivered effect is how a "
            "provider sees it twice"
        ),
        "already_queued": "this command is already queued and will be attempted",
        "in_flight": (
            "a worker holds the lease for this command. If that worker died, "
            "`operations.release_expired_leases` is what returns it to the queue "
            "— it owns lease recovery, and unlike a repair it does not reset the "
            "attempt budget, because the attempt genuinely happened"
        ),
        "ambiguous_outcome": (
            "this attempt's outcome is INDETERMINATE: it may have half-landed at "
            "the provider. Retrying risks doing the effect twice and "
            "dead-lettering hides it. Reconcile it against provider evidence "
            "(`prepare_reconciliation`) instead of replaying it"
        ),
        "evidence_missing": (
            "this command carries no stored request. There is nothing to "
            "re-send, and rebuilding a payload from current state would deliver "
            "something the recorded evidence does not describe"
        ),
        "evidence_redacted": (
            "the retention sweep replaced this command's content with its "
            "tombstone. The identity survived deliberately; the payload did not, "
            "and a tombstone is not a payload"
        ),
        "evidence_digest_mismatch": (
            "the stored payload no longer digests to the value recorded when the "
            "command was enqueued. Something rewrote the row, so what would be "
            "sent is not what was verified"
        ),
        "binding_missing": (
            "this command names no capability binding that still exists; there "
            "is nothing to route it to, so requeueing it would only park it in "
            "the queue forever"
        ),
        "raced": (
            "this command changed under the repair; nothing was written. Re-read "
            "it and decide again"
        ),
        "not_dead_letter": (
            "this command is not dead-lettered. `repair_dead_letter` is the "
            "operator command for terminally failed work only"
        ),
        "not_ambiguous": (
            "this command's outcome is not indeterminate, so there is nothing "
            "for provider evidence to resolve"
        ),
    }
)


def _binding_exists(db: Any, delivery: DeliveryAttempt) -> bool:
    """Whether the route this command was addressed by still exists.

    Existence, deliberately, and not `state == 'enabled'`. A disabled binding is
    an operator's temporary decision that is routinely reversed, and refusing a
    repair on it would force the operator to enable a connector before they may
    even queue the work. A binding that is GONE is different in kind: nothing
    will ever route the row, so requeueing it parks it silently — which is the
    outcome this whole module exists to make impossible. The binding's state is
    reported by :class:`DeadLetterEntry` either way, so the operator sees it.
    """
    if delivery.capability_binding_id is None:
        return False
    return (
        db.execute(
            sa.select(sa.literal(1)).where(
                CapabilityBinding.id == delivery.capability_binding_id
            )
        ).first()
        is not None
    )


def classify_repair(db: Any, delivery: DeliveryAttempt) -> str | None:
    """The named reason this command may not be made live again, or `None`.

    THE decision, in one place. `dead_letter_report` renders it, and every
    repair entry point enforces it, so an operator can never be shown a row as
    repairable that the repair command would then refuse — nor the reverse,
    which is worse, because it hides work that is genuinely stuck.
    """
    if delivery.state == "delivered":
        return "already_delivered"
    if delivery.state == "pending":
        return "already_queued"
    if delivery.state == "in_flight":
        return "in_flight"
    if delivery.state == "reconciliation_required":
        return "ambiguous_outcome"

    status = classify_request_evidence(delivery)
    if status is not EvidenceStatus.INTACT:
        return f"evidence_{status.value}"
    if not _binding_exists(db, delivery):
        return "binding_missing"
    return None


def _refuse(delivery: DeliveryAttempt, reason: str) -> RepairRefused:
    return RepairRefused(
        delivery.id, reason, f"delivery {delivery.id}: {_REFUSAL_DETAIL[reason]}"
    )


# ── Inspection ──────────────────────────────────────────────────────────────

#: Rows per inspection page. A knob with a documented default, not a literal
#: buried in a LIMIT: an operator paging a backlog of tens of thousands and one
#: paging a handful do not want the same page.
DEAD_LETTER_PAGE_SIZE_VAR: Final = "INTEGRATION_DEAD_LETTER_PAGE_SIZE"
DEFAULT_DEAD_LETTER_PAGE_SIZE: Final = 100
MAX_DEAD_LETTER_PAGE_SIZE: Final = 1_000


def resolve_dead_letter_page_size(source: Mapping[str, str]) -> int:
    """Read the page size from configuration, falling back to the default.

    A `Mapping` rather than `os.environ` reached for directly, for the reason
    `retention.resolve_retention_policy` takes one: a deployment that keeps
    configuration elsewhere supplies it without this module growing a client.

    Unlike a retention PERIOD this one has a default and may have one — a page
    size is an ergonomic choice, not a data-retention policy, so defaulting it
    smuggles nothing past anybody.
    """
    raw = str(source.get(DEAD_LETTER_PAGE_SIZE_VAR, "") or "").strip()
    if not raw:
        return DEFAULT_DEAD_LETTER_PAGE_SIZE
    try:
        size = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{DEAD_LETTER_PAGE_SIZE_VAR}={raw!r} is not a whole number"
        ) from exc
    if not 1 <= size <= MAX_DEAD_LETTER_PAGE_SIZE:
        raise ValueError(
            f"{DEAD_LETTER_PAGE_SIZE_VAR}={size} is outside "
            f"[1, {MAX_DEAD_LETTER_PAGE_SIZE}]"
        )
    return size


@dataclass(frozen=True, slots=True)
class DeadLetterEntry:
    """One terminally failed (or ambiguous) command, as an operator sees it.

    Everything needed to decide what to do without opening a database client:
    what was attempted, the route it was attempted against, the provider
    evidence the attempt produced, how it was classified, and whether the
    request that would be re-sent is still verifiable.

    DERIVED at read time. There is no dead-letter table and no `repairable`
    column — see this module's docstring.
    """

    delivery_id: UUID
    state: str
    installation_id: UUID
    connector_key: str | None
    installation_state: str | None
    capability_binding_id: UUID | None
    capability_id: str | None
    binding_state: str | None
    event_type: str
    idempotency_key: str
    payload_digest: str
    attempt_count: int
    created_at: datetime
    #: The CONNECTOR's classification of the last attempt, stored verbatim and
    #: never branched on — the boundary `retry.Outcome` documents.
    error_code: str | None
    error_detail: str | None
    #: Typed provider evidence. Arbitrary response bodies are unrepresentable
    #: here because they are unrepresentable in the row.
    provider_reference: str | None
    provider_status_code: int | None
    evidence: EvidenceStatus
    legal_hold: bool
    repairable: bool
    #: The named rule that makes `repairable` false. `None` when it is true.
    refusal: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "delivery_id": str(self.delivery_id),
            "state": self.state,
            "installation_id": str(self.installation_id),
            "connector_key": self.connector_key,
            "installation_state": self.installation_state,
            "capability_binding_id": (
                str(self.capability_binding_id)
                if self.capability_binding_id is not None
                else None
            ),
            "capability_id": self.capability_id,
            "binding_state": self.binding_state,
            "event_type": self.event_type,
            "idempotency_key": self.idempotency_key,
            "payload_digest": self.payload_digest,
            "attempt_count": self.attempt_count,
            "created_at": self.created_at.isoformat(),
            "error_code": self.error_code,
            "error_detail": self.error_detail,
            "provider_reference": self.provider_reference,
            "provider_status_code": self.provider_status_code,
            "evidence": self.evidence.value,
            "legal_hold": self.legal_hold,
            "repairable": self.repairable,
            "refusal": self.refusal,
        }


def _entry(
    db: Any,
    delivery: DeliveryAttempt,
    *,
    connector_key: str | None,
    installation_state: str | None,
    capability_id: str | None,
    binding_state: str | None,
) -> DeadLetterEntry:
    refusal = classify_repair(db, delivery)
    return DeadLetterEntry(
        delivery_id=delivery.id,
        state=delivery.state,
        installation_id=delivery.installation_id,
        connector_key=connector_key,
        installation_state=installation_state,
        capability_binding_id=delivery.capability_binding_id,
        capability_id=capability_id,
        binding_state=binding_state,
        event_type=delivery.event_type,
        idempotency_key=delivery.idempotency_key,
        payload_digest=delivery.payload_digest,
        attempt_count=delivery.attempt_count,
        created_at=delivery.created_at,
        error_code=delivery.error_code,
        error_detail=delivery.error_detail,
        provider_reference=delivery.provider_reference,
        provider_status_code=delivery.provider_status_code,
        evidence=classify_request_evidence(delivery),
        # The PUBLIC owner of "is this delivery held", called per row rather
        # than reimplemented as a second `released_at IS NULL` predicate here.
        # A page is bounded by `page_size`, so the cost is bounded too, and a
        # duplicated hold predicate that drifted would be worse than the query.
        legal_hold=active_delivery_hold_for(db, delivery.id) is not None,
        repairable=refusal is None,
        refusal=refusal,
    )


def _inspection_query() -> Any:
    """The projection every inspection reads.

    One definition, so the single-row reader, the paged reports and the
    reconciliation subject cannot drift into describing a delivery three
    different ways. Outer joins throughout: an installation or binding that was
    deleted is exactly the situation an operator opens this to see, and an
    inner join would silently hide the rows that matter most.
    """
    return (
        sa.select(
            DeliveryAttempt,
            ConnectorInstallation.connector_key,
            ConnectorInstallation.state.label("installation_state"),
            CapabilityBinding.capability_id,
            CapabilityBinding.state.label("binding_state"),
        )
        .outerjoin(
            ConnectorInstallation,
            ConnectorInstallation.id == DeliveryAttempt.installation_id,
        )
        .outerjoin(
            CapabilityBinding,
            CapabilityBinding.id == DeliveryAttempt.capability_binding_id,
        )
    )


def _report(
    db: Any,
    *,
    states: tuple[str, ...],
    installation_id: UUID | None,
    page_size: int,
) -> tuple[DeadLetterEntry, ...]:
    query = _inspection_query().where(DeliveryAttempt.state.in_(states))
    if installation_id is not None:
        query = query.where(DeliveryAttempt.installation_id == installation_id)
    query = query.order_by(
        # OLDEST FIRST. A backlog is worked from its far end; newest-first
        # paging shows an operator the rows they already know about and buries
        # the ones that have been stuck for a month. The id is the tiebreak, so
        # two rows created in the same millisecond do not page unstably.
        DeliveryAttempt.created_at.asc(),
        DeliveryAttempt.id.asc(),
    ).limit(page_size)
    return tuple(
        _entry(
            db,
            row[0],
            connector_key=row.connector_key,
            installation_state=row.installation_state,
            capability_id=row.capability_id,
            binding_state=row.binding_state,
        )
        for row in db.execute(query).all()
    )


def dead_letter_report(
    db: Any,
    *,
    installation_id: UUID | None = None,
    page_size: int = DEFAULT_DEAD_LETTER_PAGE_SIZE,
) -> tuple[DeadLetterEntry, ...]:
    """Terminally failed outbound commands, oldest first."""
    return _report(
        db,
        states=("dead_letter",),
        installation_id=installation_id,
        page_size=page_size,
    )


def ambiguous_report(
    db: Any,
    *,
    installation_id: UUID | None = None,
    page_size: int = DEFAULT_DEAD_LETTER_PAGE_SIZE,
) -> tuple[DeadLetterEntry, ...]:
    """Commands whose outcome is INDETERMINATE, oldest first.

    A separate function rather than a boolean flag on
    :func:`dead_letter_report`, because these are a different backlog with a
    different remedy: a dead letter needs a decision about the work, an
    ambiguous one needs evidence from the provider before any decision is even
    safe.
    """
    return _report(
        db,
        states=("reconciliation_required",),
        installation_id=installation_id,
        page_size=page_size,
    )


def inspect_delivery(db: Any, *, delivery_id: UUID) -> DeadLetterEntry:
    """One command, in any state. Raises :class:`DeliveryNotFound`."""
    row = db.execute(
        _inspection_query().where(DeliveryAttempt.id == delivery_id)
    ).first()
    if row is None:
        raise DeliveryNotFound(f"no delivery {delivery_id}")
    return _entry(
        db,
        row[0],
        connector_key=row.connector_key,
        installation_state=row.installation_state,
        capability_id=row.capability_id,
        binding_state=row.binding_state,
    )


# ── Replay and repair ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecordedOutcome:
    """What a command that already landed actually did.

    Returned instead of a re-dispatch. Provider evidence stays typed, while a
    domain-normalized result is returned only while retention still holds it.
    """

    delivered_at: datetime | None
    attempt_count: int
    provider_reference: str | None
    provider_status_code: int | None
    result: dict[str, object] | None

    @classmethod
    def of(cls, delivery: DeliveryAttempt) -> RecordedOutcome:
        return cls(
            delivered_at=delivery.delivered_at,
            attempt_count=delivery.attempt_count,
            provider_reference=delivery.provider_reference,
            provider_status_code=delivery.provider_status_code,
            result=delivery.result_json,
        )


@dataclass(frozen=True, slots=True)
class ReplayDecision:
    """What one replay or repair did — including deciding to do nothing.

    `requeued` is the whole answer to "was an effect re-armed?". A caller that
    read only the resulting state would have to infer it, and the inference is
    wrong for exactly the case that matters: a `delivered` row comes back
    unchanged and un-dispatched.
    """

    delivery_id: UUID
    idempotency_key: str
    requeued: bool
    state: str
    previous_state: str
    #: The attempt budget the command had spent before the decision. Preserved
    #: here because a requeue resets it, and the count is the evidence.
    previous_attempt_count: int
    #: The named rule when nothing was requeued; `None` when something was.
    reason: str | None
    #: Present only when the effect had already landed.
    outcome: RecordedOutcome | None = None
    evidence: RequestEvidence | None = None


def _requeue_verified(
    db: Any,
    delivery: DeliveryAttempt,
    *,
    reason: str,
    actor_admin_id: UUID | None,
) -> ReplayDecision:
    """Re-arm a command whose stored request is still verifiable.

    The gate is :func:`classify_repair` and the mechanism is
    `operations.replay_delivery` — this function owns NEITHER. It owns the
    ORDER: verified first, requeued second, so a row that cannot prove what it
    was may never reach the queue.
    """
    refusal = classify_repair(db, delivery)
    if refusal is not None:
        raise _refuse(delivery, refusal)

    evidence = request_evidence(delivery)
    previous_state, previous_attempts = delivery.state, delivery.attempt_count
    replay_delivery(db, delivery, actor_admin_id=actor_admin_id, reason=reason)
    return ReplayDecision(
        delivery_id=delivery.id,
        idempotency_key=delivery.idempotency_key,
        requeued=True,
        state=delivery.state,
        previous_state=previous_state,
        previous_attempt_count=previous_attempts,
        reason=None,
        evidence=evidence,
    )


def _unchanged(
    delivery: DeliveryAttempt, *, reason: str, outcome: RecordedOutcome | None
) -> ReplayDecision:
    """A decision that wrote nothing, with the reason it wrote nothing."""
    return ReplayDecision(
        delivery_id=delivery.id,
        idempotency_key=delivery.idempotency_key,
        requeued=False,
        state=delivery.state,
        previous_state=delivery.state,
        previous_attempt_count=delivery.attempt_count,
        reason=reason,
        outcome=outcome,
        evidence=request_evidence(delivery),
    )


def replay_by_idempotency_key(
    db: Any,
    *,
    installation_id: UUID,
    idempotency_key: str,
    reason: str,
    actor_admin_id: UUID | None = None,
) -> ReplayDecision:
    """Replay the outbound command an installation enqueued under this key.

    Addressed the way the PRODUCT addressed it. `(installation_id,
    idempotency_key)` is the outbox's unique constraint, so the key an operator
    has in a support ticket resolves to exactly one command without them having
    to find its internal id first.

    **There is no content parameter, and that is the design.** What is
    re-dispatched is the row's own stored request, checked against the digest
    recorded when it was enqueued; a caller cannot supply a payload because
    there is nowhere to put one. `test_replay_has_no_content_parameter` pins the
    signature, in the same way `receipt_delivery`'s
    `test_delivery_is_addressed_only_from_trusted_state` pins the inbound one.

    A command that already landed comes back with its :class:`RecordedOutcome`
    and is NOT re-dispatched. A command whose outcome is indeterminate is
    refused and pointed at reconciliation. A command whose stored request can no
    longer be verified is refused outright.
    """
    key = idempotency_key.strip()
    if not key:
        raise DeliveryNotFound("an idempotency key is required to address a command")

    delivery = db.execute(
        sa.select(DeliveryAttempt).where(
            DeliveryAttempt.installation_id == installation_id,
            DeliveryAttempt.idempotency_key == key,
        )
    ).scalar_one_or_none()
    if delivery is None:
        raise DeliveryNotFound(
            f"installation {installation_id} has no outbound command under key "
            f"{key!r}"
        )

    refusal = classify_repair(db, delivery)
    if refusal == "already_delivered":
        # The question was answered, not refused. Nothing is written and
        # nothing is sent: the effect is already at the provider, and the
        # recorded outcome is what the operator actually wanted to know.
        return _unchanged(
            delivery, reason=refusal, outcome=RecordedOutcome.of(delivery)
        )
    if refusal == "already_queued":
        # Also not a refusal: the command is going to be attempted. Resetting
        # its budget would be an action with no effect anyone asked for.
        return _unchanged(delivery, reason=refusal, outcome=None)

    return _requeue_verified(db, delivery, reason=reason, actor_admin_id=actor_admin_id)


def repair_dead_letter(
    db: Any,
    *,
    delivery_id: UUID,
    reason: str,
    actor_admin_id: UUID | None = None,
) -> ReplayDecision:
    """THE operator repair for a terminally failed outbound command.

    Deliberately narrower than :func:`replay_by_idempotency_key`: it accepts
    only a `dead_letter` row, so "repair" names one situation rather than
    becoming a general-purpose state mover. Anything else — including a
    `reconciliation_required` row, which needs provider evidence first — is
    refused by name.

    A dead-letter row whose stored request can no longer be verified is NEVER
    silently retried into a live effect. That is the whole point of routing
    through :func:`classify_repair`, and
    `test_repair_refuses_a_dead_letter_whose_evidence_was_redacted` is the
    proof.
    """
    delivery = db.get(DeliveryAttempt, delivery_id)
    if delivery is None:
        raise DeliveryNotFound(f"no delivery {delivery_id}")
    if delivery.state != "dead_letter":
        raise _refuse(delivery, "not_dead_letter")
    return _requeue_verified(db, delivery, reason=reason, actor_admin_id=actor_admin_id)


# ── Ambiguous-outcome reconciliation ────────────────────────────────────────


class ProviderVerdict(str, Enum):
    """What the PROVIDER says it holds — the only admissible input here.

    Three answers, and `UNKNOWN` is the one people leave out. Leaving it out
    forces a connector that genuinely cannot tell into claiming one of the
    other two, which is how a reconciler turns "I do not know" into either a
    duplicate send or a lost effect.
    """

    #: The provider holds the effect. It happened; it must not be sent again.
    LANDED = "landed"
    #: The provider proves it never received the effect. Only this verdict
    #: makes a re-dispatch safe.
    NOT_LANDED = "not_landed"
    #: The provider cannot say. The command stays ambiguous and a human decides.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    """A probe's answer, bounded to what this module may persist.

    `detail` is RETURNED and never stored — see
    :func:`reconcile_with_evidence`. Everything a probe writes is authored by
    connector code that was handed materialized secrets, which is exactly why
    `dispatch.invoke` persists only an exception's type name. The verdict is a
    closed enum this module owns; the reference and status code are the same
    two typed, bounded columns the outbox already stores.
    """

    verdict: ProviderVerdict
    provider_reference: str | None = None
    provider_status_code: int | None = None
    #: Free text for the caller's log. Never persisted, never audited.
    detail: str | None = None

    def __post_init__(self) -> None:
        # The bounds live in `retry.Outcome.__post_init__` and are reused
        # rather than restated: two copies of "1..500 characters, HTTP
        # 100..599" that are supposed to agree eventually do not. Constructing
        # the validated value object IS the validation, and its normalised
        # reference is taken back.
        probe = Outcome(
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            provider_reference=self.provider_reference,
            provider_status_code=self.provider_status_code,
        )
        object.__setattr__(self, "provider_reference", probe.provider_reference)


@dataclass(frozen=True, slots=True)
class ReconciliationSubject:
    """Everything a probe needs, and nothing that holds a connection.

    A frozen value, not an ORM object: handing a probe a live instance would
    hand it a session, and phase 2's contract is that there is no session. The
    correlation a provider is actually asked about — the idempotency key the
    effect was sent under, and whatever reference the last attempt observed —
    is carried explicitly.
    """

    delivery_id: UUID
    installation_id: UUID
    connector_key: str | None
    capability_binding_id: UUID | None
    capability_id: str | None
    event_type: str
    idempotency_key: str
    payload_digest: str
    #: The attempt this subject was read at. Settlement presents it back, and
    #: the database refuses a settlement whose attempt no longer matches.
    attempt_count: int
    provider_reference: str | None
    provider_status_code: int | None


@runtime_checkable
class ProviderEvidenceProbe(Protocol):
    """Ask the provider what it holds. Implemented by a connector, never here.

    It receives NO session, by signature — the same boundary `dispatch.invoke`
    enforces by omitting `db` from its parameters. A probe that wants a
    database has to be given one by someone breaking this contract visibly.
    """

    def probe(self, subject: ReconciliationSubject) -> ProviderEvidence: ...


def prepare_reconciliation(db: Any, *, delivery_id: UUID) -> ReconciliationSubject:
    """Phase 1. Read the ambiguous command into a session-free value.

    Refuses anything that is not `reconciliation_required`: reconciling a row
    with a known outcome would let provider evidence overwrite a settled fact.
    """
    row = db.execute(
        _inspection_query().where(DeliveryAttempt.id == delivery_id)
    ).first()
    if row is None:
        raise DeliveryNotFound(f"no delivery {delivery_id}")

    delivery: DeliveryAttempt = row[0]
    if delivery.state != "reconciliation_required":
        raise _refuse(delivery, "not_ambiguous")

    return ReconciliationSubject(
        delivery_id=delivery.id,
        installation_id=delivery.installation_id,
        connector_key=row.connector_key,
        capability_binding_id=delivery.capability_binding_id,
        capability_id=row.capability_id,
        event_type=delivery.event_type,
        idempotency_key=delivery.idempotency_key,
        payload_digest=delivery.payload_digest,
        attempt_count=delivery.attempt_count,
        provider_reference=delivery.provider_reference,
        provider_status_code=delivery.provider_status_code,
    )


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    """What one reconciliation resolved.

    `requeued` is false for two of the three verdicts, and that asymmetry is
    the design: only a provider-proven absence makes a re-dispatch safe.
    """

    delivery_id: UUID
    verdict: ProviderVerdict
    previous_state: str
    state: str
    requeued: bool
    #: The probe's free text, passed straight back to the caller. Not stored.
    detail: str | None = None


#: The error code a reconciliation writes when the provider could not answer.
#: A literal this module owns, not connector text — the row keeps a marker an
#: operator can filter on without the module persisting a string a plugin
#: authored while holding materialized secrets.
INCONCLUSIVE_CODE: Final = "reconcile_inconclusive"

#: The state an ambiguous command must still be in for a reconciliation to
#: settle it. Named once so the guard and the audit trail cannot disagree.
_AMBIGUOUS_STATE: Final = "reconciliation_required"


def reconcile_with_evidence(
    db: Any,
    subject: ReconciliationSubject,
    evidence: ProviderEvidence,
    *,
    reason: str,
    actor_admin_id: UUID | None = None,
    now: datetime | None = None,
) -> ReconciliationDecision:
    """Phase 3. Resolve an ambiguous command from provider evidence.

    ONE conditional UPDATE, guarded by the subject's own identity — this
    delivery, still ambiguous, still at the attempt the subject was read at.
    `rowcount != 1` means something else moved the row while the probe was in
    flight, and this reconciler's answer is stale: it is refused as `raced`
    rather than written over whatever the winner decided.

    ==================== ==================================================
    verdict              what happens
    ==================== ==================================================
    ``LANDED``           closed as `delivered` with the provider's own
                         reference. **Nothing is dispatched** — the effect is
                         already at the provider, and re-arming the queue is
                         precisely the duplicate this exists to prevent.
    ``NOT_LANDED``       returned to the queue with a reset budget, but ONLY
                         after the stored request is verified. Provider-proven
                         absence makes a re-dispatch safe; a missing payload
                         still makes it impossible.
    ``UNKNOWN``          stays ambiguous, marked
                         `reconcile_inconclusive`. Not retried, not failed.
    ==================== ==================================================

    The probe's `detail` is returned to the caller and written nowhere. It is
    authored by connector code that was handed materialized secrets, and
    `dispatch.invoke` persists only an exception's TYPE name for that reason;
    a reconciliation detail is the same category of text.
    """
    moment = now or datetime.now(UTC)
    delivery = db.get(DeliveryAttempt, subject.delivery_id)
    if delivery is None:
        raise DeliveryNotFound(f"no delivery {subject.delivery_id}")

    values: dict[str, Any]
    if evidence.verdict is ProviderVerdict.LANDED:
        values = {
            "state": "delivered",
            "delivered_at": moment,
            "next_attempt_at": None,
            "leased_until": None,
            "error_code": None,
            "error_detail": None,
            # Keep the evidence the last attempt observed when the probe
            # supplies none: a reconciliation that erased a correlation
            # reference would remove the only handle a later provider callback
            # could be matched by.
            "provider_reference": (
                evidence.provider_reference or subject.provider_reference
            ),
            "provider_status_code": (
                evidence.provider_status_code
                if evidence.provider_status_code is not None
                else subject.provider_status_code
            ),
        }
    elif evidence.verdict is ProviderVerdict.NOT_LANDED:
        # The SAME gate every other re-arming path uses. Provider-proven
        # absence says re-dispatching is safe; it does not say the bytes still
        # exist. A redacted or rewritten request is refused here exactly as it
        # is refused for an operator repair.
        status = classify_request_evidence(delivery)
        if status is not EvidenceStatus.INTACT:
            raise _refuse(delivery, f"evidence_{status.value}")
        if not _binding_exists(db, delivery):
            raise _refuse(delivery, "binding_missing")
        values = {
            "state": "pending",
            # Reset, for `operations.replay_delivery`'s reason: leaving the
            # count at the cap makes the requeue a no-op that looks like an
            # action, because the very next outcome dead-letters it again.
            "attempt_count": 0,
            "next_attempt_at": None,
            "leased_until": None,
            "error_code": None,
            "error_detail": None,
        }
    else:
        values = {"error_code": INCONCLUSIVE_CODE, "error_detail": None}

    result = db.execute(
        sa.update(DeliveryAttempt)
        .where(
            DeliveryAttempt.id == subject.delivery_id,
            DeliveryAttempt.state == _AMBIGUOUS_STATE,
            DeliveryAttempt.attempt_count == subject.attempt_count,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise _refuse(delivery, "raced")
    db.refresh(delivery)

    record_operation(
        db,
        action="delivery.reconciled",
        entity_type="delivery_attempt",
        entity_id=str(subject.delivery_id),
        actor_admin_id=actor_admin_id,
        details={
            "reason": reason,
            "verdict": evidence.verdict.value,
            "previous_state": _AMBIGUOUS_STATE,
            "resulting_state": delivery.state,
            "previous_attempt_count": subject.attempt_count,
            "idempotency_key": subject.idempotency_key,
            "payload_digest": subject.payload_digest,
            # Typed, bounded provider evidence only. `evidence.detail` is
            # deliberately absent: it is connector-authored free text, and this
            # ledger outlives the process, the request and the credential.
            "provider_reference": evidence.provider_reference,
            "provider_status_code": evidence.provider_status_code,
        },
    )

    return ReconciliationDecision(
        delivery_id=subject.delivery_id,
        verdict=evidence.verdict,
        previous_state=_AMBIGUOUS_STATE,
        state=delivery.state,
        requeued=evidence.verdict is ProviderVerdict.NOT_LANDED,
        detail=evidence.detail,
    )
