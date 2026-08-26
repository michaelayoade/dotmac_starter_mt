"""Durable polling attempt, failure and backoff evidence — module-owned.

The three-phase POLL engine (:mod:`dotmac_integration.polling`) could always
record a SUCCESS: a batch of receipts and an advanced cursor. It could record
nothing at all about a FAILURE. A poll that raised left the checkpoint exactly
as it found it, so the durable state could not answer three questions an
operator and a worker both need:

* how many times in a row has this job failed?
* when may it safely be tried again?
* what KIND of failure was it, and has it always been that kind?

An assembly that runs a poll worker has to answer them regardless. Without this
module it answers them itself — an attempt counter in the worker's memory, a
backoff constant in the worker's loop, a retry table in the assembly's own
schema. That is a parallel retry ledger and a second writer of a decision this
module already owns half of, which is exactly the failure ADR-0024 records and
the reason the checkpoint's optimistic lock lives here rather than in a
scheduler. So the whole of it lives here: the state on the checkpoint row, the
history in a never-rewritten table beside it, and the selection query a worker
loops over.

## What this module is NOT

**It is not a scheduler.** It never says "poll every five minutes". A polling
INTERVAL is a deployment's decision about one provider — a nightly bulk export
and a live tail do not share one — and a library that chose it would be putting
a schedule in shared code. What this module owns is a FLOOR:
:attr:`PollingCheckpoint.next_attempt_at` means *not before*, and a worker that
wakes on its own cadence asks :func:`due_polling_jobs` what is eligible now.

**It holds no provider vocabulary.** Every persisted failure is one of
:data:`POLL_FAILURE_CODES` — a CLOSED set derived from the exception types
:mod:`dotmac_integration.polling` itself defines. A connector's `error_code`, a
provider's response body and a caught exception's message never reach a column
here, and the strongest available form of that guarantee is used: **there is no
free-text column on the evidence table at all.** A rule enforced by the absence
of a place to break it cannot be broken by the next handler someone writes
(compare `tests/architecture/test_integration_error_text.py`, which has to scan
for the same defect on the tables that do have one).

The one exception proves it. :attr:`PollingAttemptFailure.connector_exception`
holds a connector exception's TYPE NAME, and only when it is already a bare
Python identifier — `TimeoutError`, not `TimeoutError: connecting to
https://api.example.com?key=hunter2`. `PollConnectorRaised` sanitizes it at the
raise site and this module re-checks it at the write, because a bounded
identifier is diagnostically load-bearing (a `TimeoutError` and a `KeyError`
are different bugs) while a message is an unbounded channel out of a plugin the
engine did not write.

## Backoff has one owner, and it is not this module either

:func:`poll_backoff_seconds` DELEGATES to
:func:`dotmac_integration.retry.retry_delay_seconds`. The exponential curve, its
ceiling and its guard against a shift overflow are defined once, for the outbox
and the poll loop alike, and a second copy here would drift the first time
someone tuned one of them. What this module adds is only the mapping from a
poll failure to an attempt number.

There is deliberately no provider `Retry-After` on this path: a POLL handler
returns events and a cursor or it raises, so there is no `Outcome` carrying a
provider's instruction. When the POLL contract grows one, it arrives as an
`Outcome` and passes straight through to the same function.

## And it never dead-letters

A failing delivery is eventually dead-lettered; a failing POLL job is not, and
must not be. A dead-lettered poll is a provider stream nobody is reading, and
the symptom is silence — no error, no queue depth, just facts that stop
arriving. So the backoff curve saturates at
:attr:`ExecutionPolicy.max_backoff_seconds` and the job stays selectable
forever. `attempt_count` is what an operator alerts on; there is no state to
transition into that would hide the job from
:func:`due_polling_jobs`.

Suppressing a poll job IS possible, and has one existing owner: disable the
binding or the installation. :func:`due_polling_jobs` joins both and selects
neither when either is off, which keeps "stop polling this" a lifecycle
gesture with an audit trail rather than a second, silent mechanism here.

## Starvation, and why the selection is keyset rather than offset

`ORDER BY (next_attempt_at, id) LIMIT n` alone is not enough for a worker that
walks more than one page: the rows it polls are UPDATED as it walks, so the
offsets of everything behind them shift and an offset-paged walk skips real
work — silently, and preferentially the rows just behind a busy one.

Keyset paging fixes it because the cursor is a VALUE, not a position:
:class:`PollPageKey` carries the `(next_attempt_at, id)` of the last row
returned, and the next page resumes strictly after that pair no matter what
happened to the rows in between.

The ordering does the rest of the work, in both directions:

* a HEALTHY job is stamped `next_attempt_at = <the moment it succeeded>`, so it
  goes to the back of the queue and the least-recently-polled job is always at
  the front. Round-robin, from one index, with no second concept;
* a FAILING job is pushed further back by its own backoff, so a job that fails
  fast in a loop cannot monopolise a worker — the thing an early, permanently
  broken checkpoint would otherwise do to every checkpoint created after it.

Neither claim is left as prose: `tests/test_integration_isolation.py` proves
both against PostgreSQL, including the case where the failing row is the OLDEST
one.

## Transactions

Every function here takes a caller-owned session, mutates and flushes, exactly
like the rest of this module. `dotmac_kernel.db` remains the one transaction
authority (hard rule 8) and nothing here commits or rolls back (hard rule 9).

Retention POLICY is not owned here. This table holds no payload, header or
provider content, but it grows. :func:`prune_poll_failure_history` therefore
owns the bounded deletion mechanic while requiring the product to supply an
explicit cutoff. There is no default age, environment read or hidden TTL in
the module; an assembly chooses the period and invokes the typed sweep.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base
from dotmac_kernel.namespaces import schema_table_args
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_integration.execution import CheckpointConflict
from dotmac_integration.models import (
    SCHEMA,
    CapabilityBinding,
    ConnectorInstallation,
    PollingCheckpoint,
)
from dotmac_integration.policy import DEFAULT_POLICY, ExecutionPolicy
from dotmac_integration.polling import (
    CursorInvalid,
    PollConnectorRaised,
    PollContractError,
    PollHandlerUnavailable,
    PollSecretsUnavailable,
    PollUnavailable,
)
from dotmac_integration.retry import retry_delay_seconds

__all__ = [
    "DEFAULT_POLL_PAGE_SIZE",
    "MAX_POLL_PAGE_SIZE",
    "POLL_FAILURE_CODES",
    "POLL_PAGE_SIZE_VAR",
    "POLL_SCHEDULE_PLATFORM_TABLES",
    "DuePollingJob",
    "PollFailurePageKey",
    "PollPageKey",
    "PollRetryState",
    "PollingAttemptFailure",
    "PollingJobUnknown",
    "RecordedPollFailure",
    "classify_poll_failure",
    "due_polling_jobs",
    "is_retry_eligible",
    "poll_backoff_seconds",
    "poll_failure_history",
    "prune_poll_failure_history",
    "record_poll_failure",
    "record_poll_success",
    "resolve_poll_page_size",
    "retry_state",
]

#: This module's contribution to the manifest's platform plane, declared beside
#: the code that owns the table rather than in one hand-maintained tuple — the
#: convention `retention` and `shadow` already follow, and what keeps two
#: concurrent slices from editing the same line.
POLL_SCHEDULE_PLATFORM_TABLES: tuple[str, ...] = ("polling_attempt_failures",)

#: THE closed vocabulary. Every member maps to an exception type this package
#: defines; nothing a connector or a provider says can widen it.
#:
#: `checkpoint_conflict` is here and is NOT a defect: it means another worker
#: advanced the cursor first, which is the optimistic lock doing its job. It is
#: recorded because a checkpoint that only ever loses races is a real
#: operational condition (two workers on one job) that is invisible otherwise —
#: and because a caller that discarded it would leave `attempt_count` frozen at
#: zero while the job made no progress at all.
POLL_FAILURE_CODES: Final[tuple[str, ...]] = (
    "checkpoint_unavailable",
    "cursor_invalid",
    "handler_unavailable",
    "secrets_unavailable",
    "contract_violated",
    "connector_raised",
    "checkpoint_conflict",
    "settlement_failed",
)

#: Rows per selection page. A knob with a documented default rather than a
#: literal inside a LIMIT: a deployment polling four bindings and one polling
#: four thousand do not want the same page.
POLL_PAGE_SIZE_VAR: Final = "INTEGRATION_POLL_PAGE_SIZE"
DEFAULT_POLL_PAGE_SIZE: Final = 100
MAX_POLL_PAGE_SIZE: Final = 1_000

#: A bare Python identifier, capped. Anything else is not a type name.
_SAFE_EXCEPTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,119}$")

_ENABLED = "enabled"


class PollingJobUnknown(LookupError):
    """No polling checkpoint with this identity exists.

    A `LookupError` rather than a `PollError`: nothing was attempted and no
    provider was reached. A caller handed over an identity that is not there,
    which is a programming or a lifecycle-ordering mistake, not a poll that
    went wrong.
    """


class PollingAttemptFailure(Base):
    """One failed polling attempt, appended and never rewritten until retention.

    Deliberately narrow. There is no message column, no payload column and no
    provider column — see this module's docstring for why the absence is the
    enforcement. What is here is what a retry decision or an incident actually
    reads back: which attempt in the current run of failures this was, what the
    engine called it, what the cursor version was at the time, how long the
    engine then waited, and when it will next be eligible.

    `checkpoint_version` is the quietly useful one. A run of
    `checkpoint_conflict` failures all carrying the SAME version says two
    workers are fighting over one job; a run carrying rising versions says the
    job is advancing and this worker keeps arriving second. Those need different
    fixes and are indistinguishable without the column.
    """

    __tablename__ = "polling_attempt_failures"
    __table_args__ = (
        CheckConstraint(
            "failure_code IN ('checkpoint_unavailable', 'cursor_invalid', "
            "'handler_unavailable', 'secrets_unavailable', 'contract_violated', "
            "'connector_raised', 'checkpoint_conflict', 'settlement_failed')",
            name="ck_polling_attempt_failures_code",
        ),
        CheckConstraint(
            "attempt_number >= 1", name="ck_polling_attempt_failures_attempt"
        ),
        CheckConstraint(
            "retry_in_seconds >= 0", name="ck_polling_attempt_failures_retry_in"
        ),
        CheckConstraint(
            "checkpoint_version >= 1", name="ck_polling_attempt_failures_version"
        ),
        Index(
            "ix_polling_attempt_failures_checkpoint_recent",
            "checkpoint_id",
            "observed_at",
            "id",
        ),
        Index(
            "ix_polling_attempt_failures_code_observed",
            "failure_code",
            "observed_at",
        ),
        schema_table_args(SCHEMA),
    )

    # An insertion-ordered key makes "the latest failure" deterministic even
    # when a database rounds two observations to the same instant.
    id: Mapped[int] = mapped_column(
        sa.BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    checkpoint_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        ForeignKey(f"{SCHEMA}.polling_checkpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Which CONSECUTIVE attempt this was, 1-based. Matches the
    #: `attempt_count` the checkpoint carried after this failure was recorded.
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The cursor version this attempt was working against.
    checkpoint_version: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_code: Mapped[str] = mapped_column(String(40), nullable=False)
    #: A connector exception's TYPE NAME, sanitized to a bare identifier at the
    #: raise site and re-checked here. Never a message. `None` for every failure
    #: the engine itself produced.
    connector_exception: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: The backoff actually applied, in seconds — stored rather than recomputed,
    #: because the policy that produced it can change and an incident asks what
    #: the engine DID, not what it would do today.
    retry_in_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


# ── Detached values ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PollPageKey:
    """Where a bounded selection walk left off.

    A VALUE, not an offset. See this module's docstring: the rows a worker just
    polled are the rows whose position an offset would have shifted.
    """

    next_attempt_at: datetime
    checkpoint_id: UUID


@dataclass(frozen=True, slots=True)
class PollFailurePageKey:
    """Where a bounded failure-history walk left off."""

    observed_at: datetime
    failure_id: int


@dataclass(frozen=True, slots=True)
class PollRetryState:
    """The checkpoint's current retry state, safe to read after the session."""

    checkpoint_id: UUID
    version: int
    attempt_count: int
    next_attempt_at: datetime
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_failure_code: str | None

    @property
    def failing(self) -> bool:
        """Whether the last attempt this job made was a failure."""
        return self.attempt_count > 0


@dataclass(frozen=True, slots=True)
class RecordedPollFailure:
    """A durable failure row, detached."""

    id: int
    checkpoint_id: UUID
    attempt_number: int
    checkpoint_version: int
    failure_code: str
    connector_exception: str | None
    retry_in_seconds: int
    next_attempt_at: datetime
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class DuePollingJob:
    """One eligible polling job, with the routing facts a worker needs.

    Enough to call :func:`dotmac_integration.polling.poll_once` and to log what
    was attempted, and nothing more: no configuration, no secret reference and
    no cursor. Those are resolved inside `prepare_poll`, under the manifest pin,
    at the moment of the poll — a selection that carried them would be handing a
    worker a stale copy of state the engine re-reads anyway.
    """

    checkpoint_id: UUID
    capability_binding_id: UUID
    installation_id: UUID
    connector_key: str
    capability_id: str
    job_key: str
    attempt_count: int
    next_attempt_at: datetime
    last_failure_code: str | None

    @property
    def page_key(self) -> PollPageKey:
        """Pass as `after=` to continue the walk strictly past this row."""
        return PollPageKey(
            next_attempt_at=self.next_attempt_at, checkpoint_id=self.checkpoint_id
        )


# ── Classification and backoff ──────────────────────────────────────────────


def classify_poll_failure(error: BaseException) -> str:
    """The engine's own name for what went wrong. Always in the closed set.

    Ordered most-specific first, because the poll exception hierarchy is a
    hierarchy: `CursorInvalid` and `PollHandlerUnavailable` are both
    `PollUnavailable`, and a broad-first chain would collapse three distinct
    operator remedies (fix the cursor envelope, fix the connector's handler
    mapping, enable the binding) into one code.

    Anything unrecognised is `settlement_failed` rather than a new code:
    widening the vocabulary is a schema change with a check constraint behind
    it, and a classifier that could invent a code would make the constraint the
    thing that fails at 3am instead of the thing that holds.
    """
    if isinstance(error, CursorInvalid):
        return "cursor_invalid"
    if isinstance(error, PollHandlerUnavailable):
        return "handler_unavailable"
    if isinstance(error, PollUnavailable):
        return "checkpoint_unavailable"
    if isinstance(error, PollSecretsUnavailable):
        return "secrets_unavailable"
    if isinstance(error, PollContractError):
        return "contract_violated"
    if isinstance(error, PollConnectorRaised):
        return "connector_raised"
    if isinstance(error, CheckpointConflict):
        return "checkpoint_conflict"
    return "settlement_failed"


def _connector_exception_name(error: BaseException) -> str | None:
    """A connector exception's bare type name, or `None`.

    Read from the attribute `PollConnectorRaised` sets, never from `str(error)`
    — the message is the channel a plugin's interpolated credential would
    travel through. Re-validated here even though the raise site already
    sanitized it: this function writes to a column, and the write site is where
    the guarantee has to hold.
    """
    if not isinstance(error, PollConnectorRaised):
        return None
    name = getattr(error, "exception_type", None)
    if not isinstance(name, str) or _SAFE_EXCEPTION_NAME.fullmatch(name) is None:
        return None
    return name


def poll_backoff_seconds(
    attempt_count: int, *, policy: ExecutionPolicy = DEFAULT_POLICY
) -> int:
    """Seconds to wait after `attempt_count` consecutive failures.

    :param attempt_count: failures SO FAR INCLUDING this one, so the first
        failure waits the base delay rather than double it.

    A delegation, not a formula. See this module's docstring: the curve, its
    ceiling and its overflow guard belong to
    :func:`dotmac_integration.retry.retry_delay_seconds`, and the poll loop
    having its own copy is how the two would drift.
    """
    if attempt_count < 0:
        raise ValueError("attempt_count cannot be negative")
    return retry_delay_seconds(attempt_count, None, policy=policy)


def _utc(value: datetime | None) -> datetime:
    moment = value or datetime.now(UTC)
    return (
        moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    ).astimezone(UTC)


def is_retry_eligible(state: PollRetryState, *, now: datetime | None = None) -> bool:
    """Whether the floor has passed. Pure, and the whole of the predicate.

    Deliberately NOT "should this be polled": whether a job is worth polling
    also depends on the binding and installation being enabled, which is a
    database question :func:`due_polling_jobs` answers in the same statement
    that applies this. Two callers, one rule, no second copy of the join.
    """
    return state.next_attempt_at <= _utc(now)


# ── Reads ───────────────────────────────────────────────────────────────────


def _state(checkpoint: PollingCheckpoint) -> PollRetryState:
    return PollRetryState(
        checkpoint_id=checkpoint.id,
        version=checkpoint.version,
        attempt_count=checkpoint.attempt_count,
        next_attempt_at=_utc(checkpoint.next_attempt_at),
        last_attempt_at=(
            _utc(checkpoint.last_attempt_at)
            if checkpoint.last_attempt_at is not None
            else None
        ),
        last_success_at=(
            _utc(checkpoint.last_success_at)
            if checkpoint.last_success_at is not None
            else None
        ),
        last_failure_at=(
            _utc(checkpoint.last_failure_at)
            if checkpoint.last_failure_at is not None
            else None
        ),
        last_failure_code=checkpoint.last_failure_code,
    )


def retry_state(db: Any, *, checkpoint_id: UUID) -> PollRetryState:
    """This job's current retry state. Raises :class:`PollingJobUnknown`."""
    checkpoint = db.get(PollingCheckpoint, checkpoint_id)
    if checkpoint is None:
        raise PollingJobUnknown(f"no polling checkpoint {checkpoint_id}")
    return _state(checkpoint)


def _require_limit(limit: int, *, what: str) -> int:
    if not 1 <= limit <= MAX_POLL_PAGE_SIZE:
        raise ValueError(
            f"{what} limit must be between 1 and {MAX_POLL_PAGE_SIZE}; "
            f"{limit} is not a bounded page"
        )
    return limit


def resolve_poll_page_size(source: Mapping[str, str]) -> int:
    """Read the selection page size from configuration, or take the default.

    A `Mapping` rather than `os.environ` reached for directly, for the reason
    :func:`dotmac_integration.retention.resolve_retention_policy` takes one: a
    deployment that keeps configuration elsewhere supplies it without this
    module growing a client.
    """
    raw = str(source.get(POLL_PAGE_SIZE_VAR, "") or "").strip()
    if not raw:
        return DEFAULT_POLL_PAGE_SIZE
    try:
        size = int(raw)
    except ValueError as exc:
        raise ValueError(f"{POLL_PAGE_SIZE_VAR}={raw!r} is not a whole number") from exc
    return _require_limit(size, what="poll selection")


def due_polling_jobs(
    db: Any,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_POLL_PAGE_SIZE,
    after: PollPageKey | None = None,
) -> tuple[DuePollingJob, ...]:
    """Eligible polling jobs, oldest floor first, one bounded keyset page.

    Selects and NOTHING ELSE: no row is claimed, leased or mutated, and two
    workers calling this concurrently both see the same page. That is
    deliberate and it is safe, because the poll path already has a claim
    mechanism — the checkpoint's optimistic `version`. Whichever worker settles
    first advances the cursor; the loser gets `CheckpointConflict`, which is a
    recorded failure code rather than a lost batch. Adding a lease here would be
    a SECOND claim over one row, and the existing one is the stronger of the two
    (it protects the cursor itself, not merely the intent to poll).

    :param now: the moment eligibility is judged against. Pass one explicit
        value for a whole multi-page walk. A job re-polled DURING the walk is
        stamped with a later floor and correctly drops out of the remainder of
        that walk rather than being offered twice.
    :param after: the previous page's last :attr:`DuePollingJob.page_key`.
    :param limit: bounded to `MAX_POLL_PAGE_SIZE`; an unbounded selection is
        how a worker with a large backlog holds one transaction open across it.
    """
    _require_limit(limit, what="poll selection")
    moment = _utc(now)

    query = (
        sa.select(
            PollingCheckpoint.id,
            PollingCheckpoint.capability_binding_id,
            PollingCheckpoint.job_key,
            PollingCheckpoint.attempt_count,
            PollingCheckpoint.next_attempt_at,
            PollingCheckpoint.last_failure_code,
            CapabilityBinding.capability_id,
            ConnectorInstallation.id.label("installation_id"),
            ConnectorInstallation.connector_key,
        )
        .join(
            CapabilityBinding,
            CapabilityBinding.id == PollingCheckpoint.capability_binding_id,
        )
        .join(
            ConnectorInstallation,
            ConnectorInstallation.id == CapabilityBinding.installation_id,
        )
        .where(
            # The SAME usability rule `selection._usable` states, expressed in
            # SQL because this one runs over a set rather than a pair. Disabling
            # a binding or an installation is the ONE way to stop a poll job,
            # and it stops it here.
            CapabilityBinding.state == _ENABLED,
            ConnectorInstallation.state == _ENABLED,
            PollingCheckpoint.next_attempt_at <= moment,
        )
    )
    if after is not None:
        boundary = _utc(after.next_attempt_at)
        # The keyset predicate, written as an explicit OR/AND rather than as a
        # row-value comparison `(a, b) > (:a, :b)`. The row-value form is
        # tidier and is what a Postgres-only module would use; this module is
        # unit-tested on SQLite as well, and the expanded form uses only
        # comparisons every dialect has always rendered identically. It reads
        # the same composite index.
        query = query.where(
            sa.or_(
                PollingCheckpoint.next_attempt_at > boundary,
                sa.and_(
                    PollingCheckpoint.next_attempt_at == boundary,
                    PollingCheckpoint.id > after.checkpoint_id,
                ),
            )
        )
    query = query.order_by(
        PollingCheckpoint.next_attempt_at.asc(),
        PollingCheckpoint.id.asc(),
    ).limit(limit)

    return tuple(
        DuePollingJob(
            checkpoint_id=row.id,
            capability_binding_id=row.capability_binding_id,
            installation_id=row.installation_id,
            connector_key=row.connector_key,
            capability_id=row.capability_id,
            job_key=row.job_key,
            attempt_count=row.attempt_count,
            next_attempt_at=_utc(row.next_attempt_at),
            last_failure_code=row.last_failure_code,
        )
        for row in db.execute(query).all()
    )


def poll_failure_history(
    db: Any,
    *,
    checkpoint_id: UUID,
    limit: int = DEFAULT_POLL_PAGE_SIZE,
    after: PollFailurePageKey | None = None,
) -> tuple[RecordedPollFailure, ...]:
    """This job's failures, most recent first, one bounded keyset page.

    Newest-first here and oldest-first in :func:`due_polling_jobs`, on purpose.
    A selection is a backlog worked from its far end; a failure history is read
    to answer "what just happened", and an operator paging from the start of a
    six-month run of failures to reach today's is being shown the least useful
    rows first.
    """
    _require_limit(limit, what="poll failure history")
    failure = PollingAttemptFailure
    query = sa.select(failure).where(failure.checkpoint_id == checkpoint_id)
    if after is not None:
        boundary = _utc(after.observed_at)
        query = query.where(
            sa.or_(
                failure.observed_at < boundary,
                sa.and_(
                    failure.observed_at == boundary,
                    failure.id < after.failure_id,
                ),
            )
        )
    query = query.order_by(failure.observed_at.desc(), failure.id.desc()).limit(limit)
    return tuple(_recorded(row) for row in db.execute(query).scalars().all())


def prune_poll_failure_history(
    db: Any,
    *,
    older_than: datetime,
    limit: int = DEFAULT_POLL_PAGE_SIZE,
) -> int:
    """Delete one bounded page older than a product-supplied cutoff.

    The cutoff is mandatory and timezone-aware. The module deliberately owns
    no retention duration: callers derive ``older_than`` from their approved
    product policy. Oldest rows leave first so an interrupted sweep has retired
    the maximum possible history on every committed page.
    """
    _require_limit(limit, what="poll failure retention")
    if older_than.tzinfo is None or older_than.utcoffset() is None:
        raise ValueError("poll failure retention cutoff must be timezone-aware")
    cutoff = _utc(older_than)
    selected = tuple(
        db.execute(
            sa.select(PollingAttemptFailure.id)
            .where(PollingAttemptFailure.observed_at < cutoff)
            .order_by(
                PollingAttemptFailure.observed_at.asc(),
                PollingAttemptFailure.id.asc(),
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    if not selected:
        return 0
    result = db.execute(
        sa.delete(PollingAttemptFailure).where(PollingAttemptFailure.id.in_(selected))
    )
    db.flush()
    return int(result.rowcount or 0)


def _recorded(row: PollingAttemptFailure) -> RecordedPollFailure:
    return RecordedPollFailure(
        id=row.id,
        checkpoint_id=row.checkpoint_id,
        attempt_number=row.attempt_number,
        checkpoint_version=row.checkpoint_version,
        failure_code=row.failure_code,
        connector_exception=row.connector_exception,
        retry_in_seconds=row.retry_in_seconds,
        next_attempt_at=_utc(row.next_attempt_at),
        observed_at=_utc(row.observed_at),
    )


# ── Writes ──────────────────────────────────────────────────────────────────


def _locked(db: Any, checkpoint_id: UUID) -> PollingCheckpoint:
    """The checkpoint row, locked for the rest of this transaction.

    `SELECT ... FOR UPDATE`, because incrementing `attempt_count` is a
    read-modify-write and two workers doing it unserialised both write the same
    number — turning the second failure of a pair into a first, which resets the
    backoff curve every time two workers collide and is precisely the case where
    backing off matters most.

    A conditional `UPDATE ... SET attempt_count = attempt_count + 1` would be
    atomic too, but the incremented value has to be READ BACK to compute the
    delay and to stamp the evidence row, and reading it back is a second
    statement that reopens the window. `RETURNING` would close it on PostgreSQL
    while not existing on the dialect the unit tests use.

    The lock is a no-op on SQLite, which renders no `FOR UPDATE` — which is why
    the concurrency claim is proved against PostgreSQL in
    `tests/test_integration_isolation.py` and NOT asserted by a unit test that
    could not observe it either way.
    """
    # `db` is `Any` — the session type stays the caller's, as everywhere in
    # this module — so the row comes back untyped. Narrowed once, here, rather
    # than left to leak an implicit `Any` out of a declared return type.
    row: object = db.execute(
        sa.select(PollingCheckpoint)
        .where(PollingCheckpoint.id == checkpoint_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not isinstance(row, PollingCheckpoint):
        raise PollingJobUnknown(f"no polling checkpoint {checkpoint_id}")
    return row


def record_poll_failure(
    db: Any,
    *,
    checkpoint_id: UUID,
    error: BaseException,
    now: datetime | None = None,
    policy: ExecutionPolicy = DEFAULT_POLICY,
) -> RecordedPollFailure:
    """Append durable evidence of one failed attempt and back the job off.

    Two writes, one transaction, and they must not come apart. An evidence row
    with no advanced floor would let the job be re-selected immediately and
    append another identical row on the next pass — an unbounded failure log
    that is also a hot loop against a provider that is already unhappy. An
    advanced floor with no evidence row is the state this module exists to
    end.

    `version` is deliberately untouched: see :class:`PollingCheckpoint`.
    """
    checkpoint = _locked(db, checkpoint_id)
    moment = _utc(now)
    code = classify_poll_failure(error)
    attempt = checkpoint.attempt_count + 1
    delay = poll_backoff_seconds(attempt, policy=policy)
    next_attempt_at = moment + timedelta(seconds=delay)

    checkpoint.attempt_count = attempt
    checkpoint.last_attempt_at = moment
    checkpoint.last_failure_at = moment
    checkpoint.last_failure_code = code
    checkpoint.next_attempt_at = next_attempt_at

    row = PollingAttemptFailure(
        checkpoint_id=checkpoint.id,
        attempt_number=attempt,
        checkpoint_version=checkpoint.version,
        failure_code=code,
        connector_exception=_connector_exception_name(error),
        retry_in_seconds=delay,
        next_attempt_at=next_attempt_at,
        observed_at=moment,
    )
    db.add(row)
    db.flush()
    return _recorded(row)


def record_poll_success(
    db: Any,
    *,
    checkpoint_id: UUID,
    now: datetime | None = None,
) -> PollRetryState:
    """Clear the backoff and send the job to the back of the queue.

    `attempt_count` returns to zero — the curve is about the CURRENT run of
    failures, and a job that has just succeeded is not in one. `next_attempt_at`
    becomes the moment of the success, which is a floor that has already passed:
    the job is eligible again immediately and merely ordered behind everything
    polled longer ago. That is the round-robin property, and it is why this
    module can own backoff without owning a schedule.

    `last_failure_code`/`last_failure_at` are cleared TOGETHER, keeping the
    pairing constraint true. The history of what failed is not lost by this —
    it is in `polling_attempt_failures`, where it is never rewritten and leaves
    only through the explicit bounded retention sweep. What is cleared is the
    CURRENT-state summary, which is supposed to describe now.

    Called by `polling.record_poll_batch` inside the settlement transaction, so
    a rolled-back settlement rolls back the reset with it. Exported because a
    product that drives the three phases itself needs the same seam.
    """
    checkpoint = _locked(db, checkpoint_id)
    moment = _utc(now)
    checkpoint.attempt_count = 0
    checkpoint.last_attempt_at = moment
    checkpoint.last_success_at = moment
    checkpoint.last_failure_at = None
    checkpoint.last_failure_code = None
    checkpoint.next_attempt_at = moment
    db.flush()
    return _state(checkpoint)
