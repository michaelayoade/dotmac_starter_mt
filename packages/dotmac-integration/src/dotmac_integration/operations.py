"""Health, audit and repair — the operator's surface over the ledgers.

Completes slice 2. Three things an integration control plane is useless without,
and each has one owner:

* **health** is DERIVED from the ledgers, never stored;
* **audit** is the KERNEL's platform ledger, adapted, never a third one;
* **repair** is explicit, authorised and evidenced — never automatic.

## Health is derived, and that is the whole design

A stored `health` column is a second writer over facts the ledgers already hold,
and it drifts the moment a worker dies between updating a delivery and updating
the summary. So `health_report` counts rows at read time. It is slower and it
cannot lie.

What it counts is chosen to answer "is anything silently stuck?", which is the
question a green dashboard usually fails to ask:

============================ =============================================
signal                       why it matters
============================ =============================================
``in_flight_expired``        a worker died holding a lease; nothing will
                             retry it until the lease is reclaimed
``retryable_overdue``        due in the past and still not attempted — the
                             dispatcher is not keeping up, or not running
``dead_letter``              gave up; needs a human
``reconciliation_required``  may have half-landed; needs a decision
``receipts_unprocessed``     arrived and verified, never acted on
``checkpoints_stale``        a poll has not advanced; the window between
                             cursors grows silently
============================ =============================================

## Repair resets the attempt budget, unlike the source

`dotmac_sub`'s `replay_delivery` returns a delivery to `pending` but leaves
`attempt_count` at the cap. A dead-lettered delivery replayed that way
dead-letters again on its first outcome — the replay is a no-op that looks like
an action. Here a replay resets the budget and records the prior count in the
audit event, because a replay IS an operator's decision to try again.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from dotmac_integration.models import (
    DeliveryAttempt,
    InboxReceipt,
    PollingCheckpoint,
)

__all__ = [
    "AUDIT_ACTION_PREFIX",
    "HealthReport",
    "NotRepairable",
    "health_report",
    "record_operation",
    "release_expired_leases",
    "replay_delivery",
    "replay_receipt",
]


class NotRepairable(RuntimeError):
    """This row is not in a state a repair command may move it from."""


#: Every audit action this module writes. A prefix, so integration operations
#: are greppable in one platform ledger beside every other platform action.
#:
#: The composed names are DECLARED on the module manifest (`audit_actions`), so
#: the vocabulary is reviewable in one place rather than inferred from string
#: concatenation scattered through this file. `test_integration_operations.py`
#: asserts the two agree — a prefix change that silently orphaned every
#: declaration would otherwise pass.
AUDIT_ACTION_PREFIX = "integration"


def record_operation(
    db: Any,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    actor_admin_id: UUID | None = None,
    details: dict | None = None,
) -> Any:
    """Write one platform audit event for an integration operation.

    An ADAPTER over `dotmac_kernel.audit.write_platform_audit_event`, for the
    same reason `idempotency` adapts the kernel's: the fleet has one platform
    audit ledger, and a module keeping its own would split the trail exactly
    when an incident needs it whole.

    The import is deferred — the kernel's audit module reaches persistence, and
    a top-level import would make this package unimportable without a
    configured database.
    """
    from dotmac_kernel.audit import write_platform_audit_event

    return write_platform_audit_event(
        db,
        actor_admin_id=actor_admin_id,
        action=f"{AUDIT_ACTION_PREFIX}.{action}",
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
    )


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Counts, at one moment, derived from the ledgers."""

    in_flight_expired: int = 0
    retryable_overdue: int = 0
    dead_letter: int = 0
    reconciliation_required: int = 0
    receipts_unprocessed: int = 0
    checkpoints_stale: int = 0

    @property
    def needs_attention(self) -> bool:
        """True when anything is stuck.

        `retryable_overdue` is included deliberately: a queue whose due work is
        in the past is not healthy just because nothing has failed yet.
        """
        return any(asdict(self).values())

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def health_report(
    db: Any,
    *,
    installation_id: UUID | None = None,
    now: datetime | None = None,
    stale_checkpoint_after_seconds: int = 24 * 60 * 60,
) -> HealthReport:
    """Count what is stuck. Derived at read time — nothing here is stored."""
    from sqlalchemy import func, or_, select

    moment = now or datetime.now(UTC)

    def _count(model: Any, *where: Any) -> int:
        query = select(func.count()).select_from(model).where(*where)
        if installation_id is not None and hasattr(model, "installation_id"):
            query = query.where(model.installation_id == installation_id)
        return int(db.execute(query).scalar_one() or 0)

    stale_before = datetime.fromtimestamp(
        moment.timestamp() - max(0, stale_checkpoint_after_seconds), tz=UTC
    )

    return HealthReport(
        in_flight_expired=_count(
            DeliveryAttempt,
            DeliveryAttempt.state == "in_flight",
            DeliveryAttempt.leased_until.is_not(None),
            DeliveryAttempt.leased_until < moment,
        ),
        retryable_overdue=_count(
            DeliveryAttempt,
            DeliveryAttempt.state.in_(("pending", "retryable")),
            DeliveryAttempt.next_attempt_at.is_not(None),
            DeliveryAttempt.next_attempt_at < moment,
        ),
        dead_letter=_count(DeliveryAttempt, DeliveryAttempt.state == "dead_letter"),
        reconciliation_required=_count(
            DeliveryAttempt, DeliveryAttempt.state == "reconciliation_required"
        ),
        receipts_unprocessed=_count(
            InboxReceipt, InboxReceipt.state.in_(("verified", "processing"))
        ),
        checkpoints_stale=_count(
            PollingCheckpoint,
            or_(
                PollingCheckpoint.advanced_at.is_(None),
                PollingCheckpoint.advanced_at < stale_before,
            ),
        ),
    )


# ── Repair commands ─────────────────────────────────────────────────────────

#: A delivery may only be replayed out of these. `delivered` is excluded on
#: purpose: replaying a success is how a provider gets charged twice.
_REPLAYABLE_DELIVERY_STATES = frozenset(
    {"dead_letter", "reconciliation_required", "retryable"}
)
_REPLAYABLE_RECEIPT_STATES = frozenset({"dead_letter", "retryable"})


def replay_delivery(
    db: Any,
    delivery: DeliveryAttempt,
    *,
    actor_admin_id: UUID | None = None,
    reason: str,
) -> DeliveryAttempt:
    """Return a stuck delivery to the queue, with its budget reset.

    `reason` is required. A repair with no stated reason is indistinguishable
    from a mistake when someone reads the trail six months later.
    """
    if delivery.state not in _REPLAYABLE_DELIVERY_STATES:
        raise NotRepairable(
            f"delivery is {delivery.state!r}; only "
            f"{sorted(_REPLAYABLE_DELIVERY_STATES)} may be replayed. Replaying "
            "a delivered effect is how a provider sees it twice"
        )
    previous_state, previous_attempts = delivery.state, delivery.attempt_count

    delivery.state = "pending"
    # Reset, unlike the source. Leaving the count at the cap makes the replay a
    # no-op that looks like an action: the very next outcome dead-letters it.
    delivery.attempt_count = 0
    delivery.next_attempt_at = None
    delivery.leased_until = None
    delivery.error_code = None
    delivery.error_detail = None

    record_operation(
        db,
        action="delivery.replayed",
        entity_type="delivery_attempt",
        entity_id=str(delivery.id),
        actor_admin_id=actor_admin_id,
        details={
            "reason": reason,
            "previous_state": previous_state,
            # The evidence the reset would otherwise destroy.
            "previous_attempt_count": previous_attempts,
        },
    )
    return delivery


def replay_receipt(
    db: Any,
    receipt: InboxReceipt,
    *,
    actor_admin_id: UUID | None = None,
    reason: str,
) -> InboxReceipt:
    """Return a stuck inbound receipt to `verified` for reprocessing."""
    if receipt.state not in _REPLAYABLE_RECEIPT_STATES:
        raise NotRepairable(
            f"receipt is {receipt.state!r}; only "
            f"{sorted(_REPLAYABLE_RECEIPT_STATES)} may be replayed"
        )
    previous_state, previous_attempts = receipt.state, receipt.attempt_count

    receipt.state = "verified"
    receipt.attempt_count = 0
    receipt.error_code = None
    receipt.error_detail = None

    record_operation(
        db,
        action="receipt.replayed",
        entity_type="inbox_receipt",
        entity_id=str(receipt.id),
        actor_admin_id=actor_admin_id,
        details={
            "reason": reason,
            "previous_state": previous_state,
            "previous_attempt_count": previous_attempts,
        },
    )
    return receipt


def release_expired_leases(
    db: Any, *, now: datetime | None = None, actor_admin_id: UUID | None = None
) -> int:
    """Return deliveries whose worker died to the queue. Returns the count.

    Idempotent and safe to run on a timer: it only touches rows whose lease has
    already expired, so running it twice changes nothing the first run did not.
    Unlike a replay it does NOT reset `attempt_count` — the attempt genuinely
    happened, and pretending otherwise would let a permanently failing delivery
    retry forever.
    """
    from sqlalchemy import select

    moment = now or datetime.now(UTC)
    stranded = list(
        db.execute(
            select(DeliveryAttempt).where(
                DeliveryAttempt.state == "in_flight",
                DeliveryAttempt.leased_until.is_not(None),
                DeliveryAttempt.leased_until < moment,
            )
        )
        .scalars()
        .all()
    )
    for delivery in stranded:
        delivery.state = "retryable"
        delivery.leased_until = None
        delivery.next_attempt_at = moment

    if stranded:
        record_operation(
            db,
            action="leases.released",
            entity_type="delivery_attempt",
            actor_admin_id=actor_admin_id,
            details={
                "count": len(stranded),
                "delivery_ids": [str(d.id) for d in stranded],
            },
        )
    return len(stranded)
