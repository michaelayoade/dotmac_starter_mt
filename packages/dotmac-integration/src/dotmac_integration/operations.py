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

## Metrics are the same mechanism as health, answering a different question

`dispatch_metrics` sits beside `health_report` deliberately, and is derived at
read time for the identical reason: a stored gauge is a second writer over facts
the ledgers already hold, and it drifts the instant a worker dies between the
delivery update and the counter update.

The split between them is what each is FOR. `health_report` answers "is anything
silently stuck?" — a small set of attention signals an operator acts on.
`dispatch_metrics` answers "how is the queue behaving?" — depth, age, latency,
retries, failures, the continuous numbers a dashboard trends and an alert
thresholds. Merging them would give one object two audiences and force a
per-request latency scan on every health check.

**This module produces numbers; it does not export them.** There is no counter
registry, no scrape endpoint and no metrics client here — the same seam
`health_report` has always used, and the deployment (`dotmac_integrator`)
already owns the exporter. Adding one would put a second observability owner
inside a library that several assemblies compose, and would make this package
depend on whichever client the first adopter happened to prefer.

What this module DOES own is the NAMES. `DispatchMetrics.as_metrics()` returns
one flat mapping keyed by stable, language-neutral identifiers, so a dashboard
built against one deployment reads another one unchanged, and a rename is a
visible change to `METRIC_NAMES` rather than a silently-empty graph.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

from dotmac_integration.models import (
    ConnectorInstallation,
    DeliveryAttempt,
    InboxReceipt,
    PollingCheckpoint,
)
from dotmac_integration.policy import DEFAULT_POLICY, ExecutionPolicy

__all__ = [
    "AUDIT_ACTION_PREFIX",
    "METRIC_NAMES",
    "DispatchMetrics",
    "HealthReport",
    "NotRepairable",
    "dispatch_metrics",
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
    details: dict[str, object] | None = None,
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


# ── Operational metrics ─────────────────────────────────────────────────────

#: Every metric name this module publishes, and the ONLY place they are spelled.
#:
#: Stable and language-neutral, because they are a cross-repository contract:
#: `dotmac_integrator`'s exporter, its dashboards and its alert rules all key on
#: these strings, and a rename that looks like tidying makes a production graph
#: read zero forever rather than fail. Same rule — and the same reason — as
#: `retention.REDACTION_MARKER`.
#:
#: Conventions, followed without exception so a name can be predicted:
#:
#: * `integration_outbound_*` for the delivery queue, `integration_inbound_*`
#:   for receipts, `integration_connector_*` for installation state. The plane a
#:   number describes is in the name, not in a comment beside the dashboard;
#: * a unit suffix wherever the value is not a plain count (`_seconds`);
#: * `_total` ONLY for a monotonic-by-nature lifetime count. A windowed or
#:   point-in-time number never carries it, because a dashboard that computes a
#:   rate over a gauge produces a plausible and completely wrong line.
METRIC_NAMES: Final[tuple[str, ...]] = (
    "integration_outbound_queue_depth",
    "integration_outbound_in_flight",
    "integration_outbound_in_flight_expired",
    "integration_outbound_oldest_queued_age_seconds",
    "integration_outbound_dispatch_latency_seconds_max",
    "integration_outbound_dispatch_latency_seconds_mean",
    "integration_outbound_delivered_window",
    "integration_outbound_retry_scheduled",
    "integration_outbound_retries_total",
    "integration_outbound_failed_total",
    "integration_outbound_reconciliation_required",
    "integration_inbound_receipts_unprocessed",
    "integration_connector_installations_quarantined",
)


def _as_utc(moment: datetime) -> datetime:
    """A timezone-aware UTC datetime, whatever the driver handed back.

    SQLite returns naive datetimes for `DateTime(timezone=True)` columns while
    PostgreSQL returns aware ones, so an age computed without this is a
    `TypeError` on one backend and correct on the other — which is exactly the
    kind of defect that passes unit tests and fails in production.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class DispatchMetrics:
    """The outbound runtime's numbers at one moment. Derived, never stored."""

    #: Queued and not yet in flight — `pending` plus `retryable`. The number
    #: that grows when the dispatcher is halted, throttled or simply outrun.
    queue_depth: int = 0
    #: Claimed right now, lease still live. The dispatcher's real concurrency.
    in_flight: int = 0
    #: Claimed, lease expired. A worker died holding these.
    in_flight_expired: int = 0
    #: How long the oldest still-undelivered delivery has existed. Depth alone
    #: cannot distinguish a busy queue from a stalled one; age can.
    oldest_queued_age_seconds: float = 0.0
    #: Enqueue-to-delivered, over the policy's window. END TO END, not per
    #: attempt: it includes every retry and every backoff wait, because that is
    #: what the system on the other side experienced.
    dispatch_latency_seconds_max: float = 0.0
    dispatch_latency_seconds_mean: float = 0.0
    #: Deliveries completed inside the window — the denominator that makes the
    #: latency numbers readable (a max over two rows is not a percentile).
    delivered_window: int = 0
    #: Currently scheduled for another attempt. A gauge.
    retry_scheduled: int = 0
    #: Lifetime retries: every attempt after each delivery's first. Attempts
    #: minus deliveries-attempted, so one successful first try counts zero.
    retries_total: int = 0
    #: Gave up. Needs a human.
    failed_total: int = 0
    #: May have half-landed. Needs a decision, not a retry.
    reconciliation_required: int = 0
    #: Arrived, verified, not yet acted on.
    receipts_unprocessed: int = 0
    #: Installations the platform has stopped trusting. Nonzero means some
    #: portion of the queue is deliberately not moving — which is the fact that
    #: otherwise gets rediscovered from first principles at 3am.
    installations_quarantined: int = 0

    def as_metrics(self) -> dict[str, float]:
        """The flat, stably-named mapping an exporter renders.

        Keys are exactly `METRIC_NAMES`, in that order.
        """
        values: tuple[float, ...] = (
            self.queue_depth,
            self.in_flight,
            self.in_flight_expired,
            self.oldest_queued_age_seconds,
            self.dispatch_latency_seconds_max,
            self.dispatch_latency_seconds_mean,
            self.delivered_window,
            self.retry_scheduled,
            self.retries_total,
            self.failed_total,
            self.reconciliation_required,
            self.receipts_unprocessed,
            self.installations_quarantined,
        )
        return dict(zip(METRIC_NAMES, values, strict=True))


def dispatch_metrics(
    db: Any,
    *,
    installation_id: UUID | None = None,
    policy: ExecutionPolicy = DEFAULT_POLICY,
    now: datetime | None = None,
) -> DispatchMetrics:
    """Count and measure the outbound runtime. Nothing here is stored.

    `installation_id` narrows every delivery and receipt figure to one
    installation, so an operator can ask about the connector they are actually
    worried about instead of reading a fleet aggregate.

    Latency is computed in PYTHON from `created_at` and `delivered_at` rather
    than by a SQL date difference, because the date-difference function is
    spelled differently in every dialect and a metrics query is the last place
    to grow a backend branch. That is what `policy.metrics_sample_limit` bounds:
    the read stays portable, so it must stay finite.
    """
    from sqlalchemy import case, func, or_, select

    moment = now or datetime.now(UTC)
    window_start = moment - timedelta(seconds=policy.metrics_window_seconds)

    def _scoped(query: Any, model: Any) -> Any:
        if installation_id is not None and hasattr(model, "installation_id"):
            return query.where(model.installation_id == installation_id)
        return query

    def _count(model: Any, *where: Any) -> int:
        query = _scoped(select(func.count()).select_from(model).where(*where), model)
        return int(db.execute(query).scalar_one() or 0)

    queued_states = ("pending", "retryable")

    oldest = db.execute(
        _scoped(
            select(func.min(DeliveryAttempt.created_at)).where(
                DeliveryAttempt.state.in_((*queued_states, "in_flight"))
            ),
            DeliveryAttempt,
        )
    ).scalar_one_or_none()
    oldest_age = (
        max(0.0, (moment - _as_utc(oldest)).total_seconds())
        if oldest is not None
        else 0.0
    )

    # Two aggregates in one pass: total attempts, and how many rows were
    # attempted at all. Their difference is the retry count, and computing it
    # this way avoids both a per-row `GREATEST(attempt_count - 1, 0)` and an
    # aggregate `FILTER` clause — neither of which every supported backend
    # spells the same way, and a metrics query is the last place to grow a
    # dialect branch.
    attempts_total, attempted_rows = db.execute(
        _scoped(
            select(
                func.coalesce(func.sum(DeliveryAttempt.attempt_count), 0),
                func.coalesce(
                    func.sum(case((DeliveryAttempt.attempt_count >= 1, 1), else_=0)),
                    0,
                ),
            ).select_from(DeliveryAttempt),
            DeliveryAttempt,
        )
    ).one()

    delivered_rows = (
        db.execute(
            _scoped(
                select(DeliveryAttempt.created_at, DeliveryAttempt.delivered_at)
                .where(
                    DeliveryAttempt.state == "delivered",
                    DeliveryAttempt.delivered_at.is_not(None),
                    DeliveryAttempt.delivered_at >= window_start,
                )
                .order_by(DeliveryAttempt.delivered_at.desc())
                .limit(policy.metrics_sample_limit),
                DeliveryAttempt,
            )
        )
    ).all()
    latencies = [
        max(0.0, (_as_utc(delivered) - _as_utc(created)).total_seconds())
        for created, delivered in delivered_rows
        if created is not None and delivered is not None
    ]

    quarantined = int(
        db.execute(
            select(func.count())
            .select_from(ConnectorInstallation)
            .where(
                ConnectorInstallation.state == "quarantined",
                *(
                    (ConnectorInstallation.id == installation_id,)
                    if installation_id is not None
                    else ()
                ),
            )
        ).scalar_one()
        or 0
    )

    return DispatchMetrics(
        queue_depth=_count(DeliveryAttempt, DeliveryAttempt.state.in_(queued_states)),
        in_flight=_count(
            DeliveryAttempt,
            DeliveryAttempt.state == "in_flight",
            DeliveryAttempt.leased_until.is_not(None),
            DeliveryAttempt.leased_until >= moment,
        ),
        in_flight_expired=_count(
            DeliveryAttempt,
            DeliveryAttempt.state == "in_flight",
            or_(
                DeliveryAttempt.leased_until.is_(None),
                DeliveryAttempt.leased_until < moment,
            ),
        ),
        oldest_queued_age_seconds=oldest_age,
        dispatch_latency_seconds_max=max(latencies) if latencies else 0.0,
        dispatch_latency_seconds_mean=(
            sum(latencies) / len(latencies) if latencies else 0.0
        ),
        delivered_window=len(latencies),
        retry_scheduled=_count(DeliveryAttempt, DeliveryAttempt.state == "retryable"),
        retries_total=max(0, int(attempts_total or 0) - int(attempted_rows or 0)),
        failed_total=_count(DeliveryAttempt, DeliveryAttempt.state == "dead_letter"),
        reconciliation_required=_count(
            DeliveryAttempt, DeliveryAttempt.state == "reconciliation_required"
        ),
        receipts_unprocessed=_count(
            InboxReceipt, InboxReceipt.state.in_(("verified", "processing"))
        ),
        installations_quarantined=quarantined,
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
