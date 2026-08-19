"""Configure a series, allocate from it, repair it. One engine, both planes.

The plane is chosen by the `Scope` value the caller passes and by nothing else.

## At-most-once is the kernel's, not ours

Replay, fingerprint comparison and the concurrent-key race belong to
`dotmac_kernel.idempotency` and to no one else (hard rule 23). This module
calls `execute_once` / `execute_once_platform` and keeps the allocation receipt
as DOMAIN evidence — what number was issued, for which period, against which
business date — never as a second replay mechanism.

That is not a formality. A hand-rolled "look up the receipt, then allocate"
has a hole the kernel does not: two concurrent calls with the same key both
miss the lookup, both allocate, and the loser raises `IntegrityError` instead
of replaying the winner. The kernel runs the operation inside a savepoint and
converts exactly that race into a replay-or-conflict.

## What this owner refuses

* to read a clock for a business decision — `reference_date` is required;
* to read settings or the environment — configuration is a row;
* to invent a series — an unconfigured code fails closed;
* to commit — allocation joins the caller's transaction;
* to rewind a counter or rewrite a receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, TypeVar, cast
from uuid import uuid4

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from dotmac_numbering.models import (
    NO_PERIOD,
    RESET_POLICIES,
    AllocationReceipt,
    NumberSeries,
    PlatformAllocationReceipt,
    PlatformNumberSeries,
    PlatformSeriesCounter,
    PlatformSeriesRepair,
    SeriesCounter,
    SeriesRepair,
)

SeriesRow = NumberSeries | PlatformNumberSeries
CounterRow = SeriesCounter | PlatformSeriesCounter
ModelSet = tuple[
    type[NumberSeries] | type[PlatformNumberSeries],
    type[SeriesCounter] | type[PlatformSeriesCounter],
    type[AllocationReceipt] | type[PlatformAllocationReceipt],
    type[SeriesRepair] | type[PlatformSeriesRepair],
]
_Model = TypeVar("_Model")

MAX_VALUE: Final[int] = 10**15
MAX_DIGITS: Final[int] = 18

#: Kernel idempotency scope, namespaced to this module so a key spent here can
#: never collide with a product's own.
ALLOCATE_SCOPE: Final[str] = "numbering.allocate"


class NumberingError(Exception):
    """Fail-closed numbering error with a stable code."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = f"numbering.{code}"
        self.message = message
        self.details = dict(details)


def _error(code: str, message: str, **details: object) -> NumberingError:
    return NumberingError(code, message, **details)


# ── Configuration ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SeriesConfiguration:
    """A validated series definition.

    Typed rather than a dict so a consumer configures a series through this
    owner instead of writing `mod_numbering` tables directly — which is what
    the first draft of this module forced them to do.
    """

    series_code: str
    prefix: str = ""
    suffix: str = ""
    separator: str = "-"
    min_digits: int = 6
    include_year: bool = False
    year_digits: int = 4
    include_month: bool = False
    reset_policy: str = "never"
    start_value: int = 1

    def validate(self) -> None:
        if not self.series_code or len(self.series_code) > 80:
            raise _error(
                "invalid_series_code", "A series code is required, max 80 characters."
            )
        if self.reset_policy not in RESET_POLICIES:
            raise _error(
                "unknown_reset_policy",
                "A series must declare a registered reset policy.",
                reset_policy=self.reset_policy,
                registered=list(RESET_POLICIES),
            )
        if not 1 <= self.min_digits <= MAX_DIGITS:
            raise _error(
                "invalid_min_digits",
                f"Digit width must be between 1 and {MAX_DIGITS}.",
                min_digits=self.min_digits,
            )
        if self.year_digits not in (2, 4):
            raise _error(
                "invalid_year_digits",
                "Year width must be 2 or 4.",
                year_digits=self.year_digits,
            )
        if self.start_value < 1 or self.start_value >= MAX_VALUE:
            raise _error(
                "invalid_start_value",
                "Start value must be positive and below the maximum.",
                start_value=self.start_value,
            )
        # Reset/format coherence. A yearly-resetting series that does not print
        # the year reuses the same string every January — the number stops
        # identifying the document, and the receipt's period is the only thing
        # that still distinguishes them.
        if self.reset_policy == "yearly" and not self.include_year:
            raise _error(
                "incoherent_reset",
                "A yearly-resetting series must include the year in the "
                "number, or every year reissues the same strings.",
            )
        if self.reset_policy == "monthly" and not (
            self.include_year and self.include_month
        ):
            raise _error(
                "incoherent_reset",
                "A monthly-resetting series must include both year and month "
                "in the number, or every month reissues the same strings.",
            )
        if self.include_month and not self.include_year:
            raise _error(
                "incoherent_format",
                "A month without a year is ambiguous across years.",
            )


#: Fields that shape what a rendered number LOOKS like, or which period it
#: belongs to. Once a series has a counter or a receipt, changing one of these
#: can reproduce a number already issued — a new period scheme restarts at the
#: start value, and the value-and-period unique key cannot see that the string
#: collides. They freeze; `uq_..._rendered` is the backstop if one ever slips.
IDENTITY_SHAPING_FIELDS: Final[tuple[str, ...]] = (
    "prefix",
    "suffix",
    "separator",
    "min_digits",
    "include_year",
    "year_digits",
    "include_month",
    "reset_policy",
)


def _series_has_history(db: Session, scope: Scope, series_code: str) -> bool:
    """True once the series has a counter or a receipt.

    Either is enough: a counter means a number may already have been rendered
    even if the receipt was rolled back with its caller.
    """
    _, counter_model, receipt_model, _ = _models(scope)
    for model in (counter_model, receipt_model):
        stmt = select(model.id).where(model.series_code == series_code).limit(1)
        if db.execute(_tenant_filter(stmt, scope, model)).first() is not None:
            return True
    return False


def configure_series(
    db: Session, *, scope: Scope, configuration: SeriesConfiguration
) -> NumberSeries | PlatformNumberSeries:
    """Create or update a series definition.

    Configuration is deliberately separate from counter state: this can never
    move a counter, and allocation can never reconfigure a series. Updating a
    definition does not retro-format any issued number, because a receipt
    stores the formatted string rather than re-deriving it.
    """
    configuration.validate()
    existing = _find_series(db, scope, configuration.series_code, lock=True)
    if existing is not None:
        _assert_safe_transition(db, scope, existing, configuration)
    target = existing
    if target is None:
        if isinstance(scope, TenantScope):
            target = NumberSeries(
                tenant_id=scope.tenant_id,
                series_code=configuration.series_code,
            )
        elif isinstance(scope, PlatformScope):
            target = PlatformNumberSeries(series_code=configuration.series_code)
        else:
            raise _error(
                "unknown_scope",
                "An explicit TenantScope or PlatformScope is required.",
                scope=type(scope).__name__,
            )
        db.add(target)
    for field in (
        "prefix",
        "suffix",
        "separator",
        "min_digits",
        "year_digits",
        "reset_policy",
        "start_value",
    ):
        setattr(target, field, getattr(configuration, field))
    target.include_year = int(configuration.include_year)
    target.include_month = int(configuration.include_month)
    db.flush()
    return cast(SeriesRow, target)


def _assert_safe_transition(
    db: Session,
    scope: Scope,
    existing: SeriesRow,
    configuration: SeriesConfiguration,
) -> None:
    """Refuse a reconfiguration that could reissue an existing number.

    Before the series has handed anything out, any change is safe. Afterwards
    the identity-shaping fields are frozen: retiring the series and configuring
    a new code is the honest way to change how its numbers look, because it
    leaves the issued ones addressable under the code that issued them.

    `start_value` is deliberately NOT frozen — it seeds counters for periods
    that have not begun, and cannot move one that has.
    """
    changed = [
        field
        for field in IDENTITY_SHAPING_FIELDS
        if _normalise(getattr(existing, field))
        != _normalise(getattr(configuration, field))
    ]
    if changed and _series_has_history(db, scope, configuration.series_code):
        raise _error(
            "unsafe_configuration_change",
            "This series has already allocated, so the fields that shape its "
            "numbers are frozen. Changing them could reproduce a number "
            "already issued. Configure a new series_code instead.",
            series_code=configuration.series_code,
            frozen_fields=changed,
        )


def _normalise(value: object) -> object:
    """`include_year` is an int column and a bool on the configuration."""
    return int(value) if isinstance(value, bool) else value


# ── Periods and formatting ──────────────────────────────────────────────────


def period_for(reference_date: date, reset_policy: str) -> str:
    """The period key a date belongs to.

    Zero-padded and lexically ordered, so `"2026-02" > "2026-01"` and
    `"2027" > "2026"` both hold. Never null: a non-resetting series uses `*`,
    so every unique key that includes the period is total.
    """
    if reset_policy == "never":
        return NO_PERIOD
    if reset_policy == "yearly":
        return f"{reference_date.year:04d}"
    if reset_policy == "monthly":
        return f"{reference_date.year:04d}-{reference_date.month:02d}"
    raise _error(
        "unknown_reset_policy",
        "A series must declare a registered reset policy.",
        reset_policy=reset_policy,
        registered=list(RESET_POLICIES),
    )


def format_number(
    series: NumberSeries | PlatformNumberSeries, *, value: int, reference_date: date
) -> str:
    """The one formatter, shared by allocation and preview."""
    if value < 1:
        raise _error(
            "invalid_value", "A formatted value must be positive.", value=value
        )
    segments: list[str] = []
    if series.prefix:
        segments.append(series.prefix)
    if series.include_year:
        year = reference_date.year
        segments.append(
            f"{year % 100:02d}" if series.year_digits == 2 else f"{year:04d}"
        )
    if series.include_month:
        segments.append(f"{reference_date.month:02d}")
    segments.append(str(value).zfill(series.min_digits))
    if series.suffix:
        segments.append(series.suffix)
    return series.separator.join(segments)


def preview(
    db: Session, *, scope: Scope, series_code: str, reference_date: date
) -> str:
    """What the next allocation for this date would look like.

    Reads the counter for the date's OWN period, so a preview for a backdated
    document shows that period's next number rather than the current one.
    """
    # No lock: preview decides nothing and writes nothing, so a concurrent
    # reconfiguration merely means the preview was a moment stale.
    series = _require_series(db, scope, series_code)
    period = period_for(reference_date, series.reset_policy)
    counter = _find_counter(db, scope, series_code, period)
    value = counter.next_value if counter is not None else series.start_value
    return format_number(series, value=value, reference_date=reference_date)


# ── Plane plumbing ──────────────────────────────────────────────────────────


def _models(scope: Scope) -> ModelSet:
    if isinstance(scope, TenantScope):
        return NumberSeries, SeriesCounter, AllocationReceipt, SeriesRepair
    if isinstance(scope, PlatformScope):
        return (
            PlatformNumberSeries,
            PlatformSeriesCounter,
            PlatformAllocationReceipt,
            PlatformSeriesRepair,
        )
    raise _error(
        "unknown_scope",
        "An explicit TenantScope or PlatformScope is required.",
        scope=type(scope).__name__,
    )


def _tenant_filter(
    stmt: Select[tuple[_Model]], scope: Scope, model: Any
) -> Select[tuple[_Model]]:
    if isinstance(scope, TenantScope):
        return stmt.where(model.tenant_id == scope.tenant_id)
    return stmt


def _find_series(
    db: Session, scope: Scope, series_code: str, *, lock: bool = False
) -> SeriesRow | None:
    """The series row, optionally locked FOR UPDATE.

    LOCK ORDER IS SERIES, THEN PERIOD COUNTER — everywhere, without exception.
    Taking them in the other order anywhere would deadlock against every path
    that takes them in this one.
    """
    series_model, *_ = _models(scope)
    stmt = select(series_model).where(series_model.series_code == series_code)
    stmt = _tenant_filter(stmt, scope, series_model)
    if lock:
        stmt = stmt.with_for_update()
    return cast(SeriesRow | None, db.execute(stmt).scalar_one_or_none())


def _require_series(
    db: Session, scope: Scope, series_code: str, *, lock: bool = False
) -> SeriesRow:
    series = _find_series(db, scope, series_code, lock=lock)
    if series is None:
        raise _error(
            "series_not_configured",
            "No configured series for this code. A series is configured "
            "explicitly through configure_series; this module never invents one.",
            series_code=series_code,
        )
    return series


def _find_counter(
    db: Session, scope: Scope, series_code: str, period_key: str
) -> CounterRow | None:
    _, counter_model, *_ = _models(scope)
    stmt = select(counter_model).where(
        counter_model.series_code == series_code,
        counter_model.period_key == period_key,
    )
    return cast(
        CounterRow | None,
        db.execute(_tenant_filter(stmt, scope, counter_model)).scalar_one_or_none(),
    )


def _locked_counter(
    db: Session, scope: Scope, series: SeriesRow, period_key: str
) -> CounterRow:
    """The counter for this period, created if absent, then locked.

    `INSERT ... ON CONFLICT DO NOTHING` before `SELECT ... FOR UPDATE` is the
    one shape Sub genuinely contributes. ERP does query-then-insert, so two
    first allocations race on the unique constraint before either holds a lock.
    """
    _, counter_model, *_ = _models(scope)
    values: dict[str, object] = {
        "id": uuid4(),
        "series_code": series.series_code,
        "period_key": period_key,
        "next_value": series.start_value,
    }
    if isinstance(scope, TenantScope):
        values["tenant_id"] = scope.tenant_id
    db.execute(pg_insert(counter_model).values(**values).on_conflict_do_nothing())

    stmt = select(counter_model).where(
        counter_model.series_code == series.series_code,
        counter_model.period_key == period_key,
    )
    counter = db.execute(
        _tenant_filter(stmt, scope, counter_model).with_for_update()
    ).scalar_one()
    return cast(CounterRow, counter)


# ── Allocation ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Allocation:
    """One allocated number and the period it belongs to."""

    formatted_number: str
    value: int
    series_code: str
    period_key: str
    reference_date: date
    replayed: bool


def allocate(
    db: Session,
    *,
    scope: Scope,
    series_code: str,
    reference_date: date,
    idempotency_key: str,
    allocated_by: str | None = None,
) -> Allocation:
    """Reserve the next value of a configured series for its period.

    At-most-once is delegated to `dotmac_kernel.idempotency`, which owns the
    replay, the fingerprint conflict and the concurrent-key race.
    """
    if not series_code:
        raise _error("missing_series_code", "A series code is required.")
    if not idempotency_key:
        raise _error(
            "missing_idempotency_key", "An allocation requires an idempotency identity."
        )
    if not isinstance(reference_date, date) or isinstance(reference_date, datetime):
        raise _error(
            "invalid_reference_date",
            "reference_date must be a date and must be supplied explicitly: "
            "this module reads no clock.",
        )

    # Imported here, not at module scope: `dotmac_kernel.idempotency` pulls
    # `dotmac_kernel.db`, which builds an engine on import, and this package
    # must import without a DATABASE_URL (a wheel is inspected before any
    # deployment configures one).
    from dotmac_kernel.idempotency import (
        execute_once,
        execute_once_platform,
        fingerprint_of,
    )

    fingerprint = fingerprint_of(
        {
            "series_code": series_code,
            "reference_date": reference_date.isoformat(),
        }
    )

    def operation(session: Session) -> dict[str, object]:
        return _do_allocate(
            session,
            scope=scope,
            series_code=series_code,
            reference_date=reference_date,
            idempotency_key=idempotency_key,
            allocated_by=allocated_by,
        )

    if isinstance(scope, TenantScope):
        outcome = execute_once(
            db,
            tenant_id=scope.tenant_id,
            scope=ALLOCATE_SCOPE,
            key=idempotency_key,
            operation=operation,
            operation_name=f"{ALLOCATE_SCOPE}:{series_code}",
            fingerprint=fingerprint,
        )
    else:
        _models(scope)  # refuse an unknown scope before spending a key
        outcome = execute_once_platform(
            db,
            scope=ALLOCATE_SCOPE,
            key=idempotency_key,
            operation=operation,
            operation_name=f"{ALLOCATE_SCOPE}:{series_code}",
            fingerprint=fingerprint,
        )

    result = outcome.result
    formatted_number = result["formatted_number"]
    value = result["value"]
    period_key = result["period_key"]
    result_reference_date = result["reference_date"]
    if (
        not isinstance(formatted_number, str)
        or not isinstance(value, int)
        or isinstance(value, bool)
        or not isinstance(period_key, str)
        or not isinstance(result_reference_date, str)
    ):
        raise _error(
            "invalid_allocation_result",
            "The idempotency ledger returned an invalid allocation result.",
        )
    return Allocation(
        formatted_number=formatted_number,
        value=value,
        series_code=series_code,
        period_key=period_key,
        reference_date=date.fromisoformat(result_reference_date),
        replayed=outcome.replayed,
    )


def _do_allocate(
    db: Session,
    *,
    scope: Scope,
    series_code: str,
    reference_date: date,
    idempotency_key: str,
    allocated_by: str | None,
) -> dict[str, object]:
    # Series first, then the period counter. Without the series lock a first
    # allocation can read old configuration while a concurrent
    # `configure_series` commits new values, and the number is then formatted
    # from a configuration that no longer exists.
    series = _require_series(db, scope, series_code, lock=True)
    period_key = period_for(reference_date, series.reset_policy)
    counter = _locked_counter(db, scope, series, period_key)

    value = int(counter.next_value)
    if value >= MAX_VALUE:
        raise _error(
            "series_exhausted",
            "This series has reached the maximum supported value for the period.",
            series_code=series_code,
            period_key=period_key,
        )

    formatted = format_number(series, value=value, reference_date=reference_date)
    counter.next_value = value + 1

    _, _, receipt_model, _ = _models(scope)
    receipt = receipt_model(
        series_code=series_code,
        period_key=period_key,
        allocated_value=value,
        formatted_number=formatted,
        reference_date=reference_date,
        idempotency_key=idempotency_key,
        # `allocated_at` is a server default. Transaction time records
        # evidence; it never influences the period, the format or the value.
        allocated_by=allocated_by,
    )
    if isinstance(scope, TenantScope):
        cast(AllocationReceipt, receipt).tenant_id = scope.tenant_id
    db.add(receipt)
    db.flush()

    return {
        "formatted_number": formatted,
        "value": value,
        "period_key": period_key,
        "reference_date": reference_date.isoformat(),
    }


# ── Repair ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Repair:
    """The outcome of one repair, and the evidence row that records it."""

    series_code: str
    period_key: str
    previous_next_value: int
    new_next_value: int
    changed: bool


def advance_to_at_least(
    db: Session,
    *,
    scope: Scope,
    series_code: str,
    period_key: str,
    proven_minimum: int,
    reason: str,
    repaired_by: str,
) -> Repair:
    """Move ONE period's counter forward to caller-proven evidence.

    Advance-only by construction, and it names the period it repairs: a series
    has one counter per period, so a repair that could not say which one would
    be guessing.

    Every attempt that changes a counter writes immutable evidence. A repair
    moves a number that will be printed on a document; doing it without a
    durable record of who, why and from what is how the sources lost the
    ability to explain their own sequences.
    """
    if proven_minimum < 1 or proven_minimum >= MAX_VALUE:
        raise _error(
            "invalid_proven_minimum",
            "Repair evidence must be positive and below the maximum.",
            proven_minimum=proven_minimum,
        )
    if not reason:
        raise _error("missing_reason", "A repair must state why it was made.")
    if not repaired_by:
        raise _error("missing_actor", "A repair must name an accountable actor.")

    series = _require_series(db, scope, series_code, lock=True)
    _validate_period_key(period_key, series.reset_policy)
    counter = _locked_counter(db, scope, series, period_key)

    previous = int(counter.next_value)
    target = proven_minimum + 1
    changed = target > previous
    if changed:
        counter.next_value = target

    _, _, _, repair_model = _models(scope)
    evidence = repair_model(
        series_code=series_code,
        period_key=period_key,
        previous_next_value=previous,
        new_next_value=int(counter.next_value),
        proven_minimum=proven_minimum,
        reason=reason,
        repaired_by=repaired_by,
    )
    if isinstance(scope, TenantScope):
        cast(SeriesRepair, evidence).tenant_id = scope.tenant_id
    db.add(evidence)
    db.flush()

    return Repair(
        series_code=series_code,
        period_key=period_key,
        previous_next_value=previous,
        new_next_value=int(counter.next_value),
        changed=changed,
    )


def _validate_period_key(period_key: str, reset_policy: str) -> None:
    """A malformed period key would create a counter nothing ever reads."""
    if reset_policy == "never":
        if period_key != NO_PERIOD:
            raise _error(
                "invalid_period_key",
                f"A non-resetting series has one counter, keyed {NO_PERIOD!r}.",
                period_key=period_key,
            )
        return
    if reset_policy == "yearly":
        valid = len(period_key) == 4 and period_key.isdigit()
    else:
        valid = (
            len(period_key) == 7
            and period_key[4] == "-"
            and period_key[:4].isdigit()
            and period_key[5:].isdigit()
            and 1 <= int(period_key[5:]) <= 12
        )
    if not valid:
        raise _error(
            "invalid_period_key",
            "The period key does not match the series reset policy.",
            period_key=period_key,
            reset_policy=reset_policy,
        )


__all__ = [
    "ALLOCATE_SCOPE",
    "MAX_DIGITS",
    "MAX_VALUE",
    "Allocation",
    "NumberingError",
    "Repair",
    "SeriesConfiguration",
    "advance_to_at_least",
    "allocate",
    "configure_series",
    "format_number",
    "period_for",
    "preview",
]
