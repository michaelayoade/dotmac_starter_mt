"""Concurrency-safe allocation and formatting of configured document series.

One engine, both planes. The plane is chosen by the `Scope` value the caller
passes, and there is no other switch: a `TenantScope` reads and writes the
tenant tables, a `PlatformScope` the platform ones. Nothing infers a plane from
a missing tenant column.

What this owner does NOT do, because the sources did and it is why they are
being replaced:

* it never reads a clock. `reference_date` is a required business input, so a
  backdated document cannot silently take today's period;
* it never reads a settings store or the environment. Configuration is a row,
  supplied by the installing product (ADR-0009, ADR-0011);
* it never invents a series. An unconfigured `series_code` fails closed rather
  than auto-creating one with a guessed prefix;
* it never commits. Allocation joins the caller's transaction and rolls back
  with it, so a failed invoice does not consume a committed number;
* it never rewinds a counter. Repair advances to proven evidence or refuses.

The formatter is a single function used by allocation AND preview. ERP has
three, two of which disagree — its preview hardcodes a four-digit segment, so
for every series whose width is not four the preview is a lie.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_numbering.models import (
    RESET_POLICIES,
    AllocationReceipt,
    NumberSeries,
    PlatformAllocationReceipt,
    PlatformNumberSeries,
)

#: Widest counter this module will format. Beyond it the caller has almost
#: certainly passed a repair value from the wrong column, and a silently
#: enormous document number is worse than a refusal.
MAX_VALUE: Final[int] = 10**15


class NumberingError(Exception):
    """Fail-closed numbering error with a stable code."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = f"numbering.{code}"
        self.message = message
        self.details = dict(details)


def _error(code: str, message: str, **details: object) -> NumberingError:
    return NumberingError(code, message, **details)


# ── Periods and formatting ──────────────────────────────────────────────────


def period_for(reference_date: date, reset_policy: str) -> str | None:
    """The period a date belongs to under a reset policy.

    Returned as a ZERO-PADDED, lexically ordered string so `"2026-02" >
    "2026-01"` and `"2027" > "2026"` are both true. Ordering is the whole point:
    ERP compares periods for INEQUALITY, so an allocation dated last year looks
    like a new period and rewinds the counter.
    """
    if reset_policy == "never":
        return None
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


def format_number(series: NumberSeries | PlatformNumberSeries, *, value: int, reference_date: date) -> str:
    """The one formatter. Used by allocation and preview alike.

    Segments are joined by the configured separator and empty segments are
    dropped, so a series with no prefix does not produce a leading separator.
    """
    if value < 1:
        raise _error("invalid_value", "A formatted value must be positive.", value=value)
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
    series: NumberSeries | PlatformNumberSeries, *, reference_date: date
) -> str:
    """What the NEXT allocation would look like, without allocating.

    Deliberately the same code path as allocation. A preview that disagrees
    with what is issued is worse than no preview.
    """
    return format_number(series, value=series.next_value, reference_date=reference_date)


def request_fingerprint(
    *, series_code: str, reference_date: date, scope_segment: str
) -> str:
    """Digest of the allocation request.

    Its own value, never packed into the idempotency key (ADR-0014). Covers
    what the caller asked for; excludes the actor and the wall clock, so a
    retried request matches while a changed request conflicts.
    """
    payload = json.dumps(
        {
            "series_code": series_code,
            "reference_date": reference_date.isoformat(),
            "scope": scope_segment,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Allocation:
    """One allocated number and the receipt that proves it."""

    formatted_number: str
    value: int
    series_code: str
    reference_date: date
    replayed: bool


def _scope_segment(scope: Scope) -> str:
    return f"t={scope.tenant_id}" if isinstance(scope, TenantScope) else "platform"


def _tables(scope: Scope):
    if isinstance(scope, TenantScope):
        return NumberSeries, AllocationReceipt
    if isinstance(scope, PlatformScope):
        return PlatformNumberSeries, PlatformAllocationReceipt
    raise _error(
        "unknown_scope",
        "Allocation requires an explicit TenantScope or PlatformScope.",
        scope=type(scope).__name__,
    )


def _locked_series(db: Session, scope: Scope, series_code: str):
    """The series row, locked FOR UPDATE.

    The lock is taken before anything is read from it, so two concurrent
    allocations serialise on the row rather than both reading the same
    `next_value`. Sub's shape is right here and ERP's is not; neither has ever
    proven it, because Sub's tests run on SQLite where the lock is a no-op.
    """
    series_model, _ = _tables(scope)
    stmt = select(series_model).where(series_model.series_code == series_code)
    if isinstance(scope, TenantScope):
        stmt = stmt.where(series_model.tenant_id == scope.tenant_id)
    row = db.execute(stmt.with_for_update()).scalar_one_or_none()
    if row is None:
        raise _error(
            "series_not_configured",
            "No configured series for this code. A series is configured "
            "explicitly; this module never invents one.",
            series_code=series_code,
            scope=_scope_segment(scope),
        )
    return row


# ── The command ─────────────────────────────────────────────────────────────


def allocate(
    db: Session,
    *,
    scope: Scope,
    series_code: str,
    reference_date: date,
    idempotency_key: str,
    allocated_by: str | None = None,
) -> Allocation:
    """Reserve the next value of a configured series.

    Joins the caller's transaction: nothing is committed here, so a rolled-back
    consuming transaction does not consume a number.

    Replaying the same `idempotency_key` returns the original formatted number
    exactly. Replaying it with a different request is a conflict, never a
    second allocation and never a silent no-op.
    """
    if not series_code:
        raise _error("missing_series_code", "A series code is required.")
    if not idempotency_key:
        raise _error(
            "missing_idempotency_key", "An allocation requires an idempotency identity."
        )
    if not isinstance(reference_date, date) or isinstance(reference_date, datetime):
        # A datetime would carry a time and, worse, a timezone the caller may
        # not have thought about — and the reset boundary is a DATE decision.
        raise _error(
            "invalid_reference_date",
            "reference_date must be a date, and must be supplied explicitly: "
            "this module reads no clock.",
        )

    series_model, receipt_model = _tables(scope)
    fingerprint = request_fingerprint(
        series_code=series_code,
        reference_date=reference_date,
        scope_segment=_scope_segment(scope),
    )

    replay = _find_receipt(db, scope, receipt_model, series_code, idempotency_key)
    if replay is not None:
        if replay.request_fingerprint != fingerprint:
            raise _error(
                "idempotency_conflict",
                "This idempotency key already allocated a different request.",
                series_code=series_code,
                idempotency_key=idempotency_key,
            )
        return Allocation(
            formatted_number=replay.formatted_number,
            value=replay.allocated_value,
            series_code=series_code,
            reference_date=replay.reference_date,
            replayed=True,
        )

    series = _locked_series(db, scope, series_code)
    period = period_for(reference_date, series.reset_policy)

    # Reset only when the supplied date is in a LATER period than the counter.
    # Strictly greater, never merely different: a backdated allocation must
    # continue the current period rather than restart it.
    if period is not None and (
        series.current_period is None or period > series.current_period
    ):
        series.next_value = 1
        series.current_period = period

    value = int(series.next_value)
    if value >= MAX_VALUE:
        raise _error(
            "series_exhausted",
            "This series has reached the maximum supported value.",
            series_code=series_code,
            value=value,
        )

    formatted = format_number(series, value=value, reference_date=reference_date)
    series.next_value = value + 1

    receipt = receipt_model(
        series_code=series_code,
        allocated_value=value,
        formatted_number=formatted,
        reference_date=reference_date,
        period=period,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        allocated_at=datetime.now(UTC),
        allocated_by=allocated_by,
    )
    if isinstance(scope, TenantScope):
        receipt.tenant_id = scope.tenant_id
    db.add(receipt)
    db.flush()

    return Allocation(
        formatted_number=formatted,
        value=value,
        series_code=series_code,
        reference_date=reference_date,
        replayed=False,
    )


def _find_receipt(db: Session, scope: Scope, receipt_model, series_code: str, key: str):
    stmt = select(receipt_model).where(
        receipt_model.series_code == series_code,
        receipt_model.idempotency_key == key,
    )
    if isinstance(scope, TenantScope):
        stmt = stmt.where(receipt_model.tenant_id == scope.tenant_id)
    return db.execute(stmt).scalar_one_or_none()


def advance_to_at_least(
    db: Session,
    *,
    scope: Scope,
    series_code: str,
    proven_minimum: int,
    note: str | None = None,
) -> int:
    """Repair a counter forward to caller-proven evidence.

    Advance-only, by construction. A counter behind the numbers already issued
    hands out a duplicate on its next call, and that is the only condition this
    exists to fix — so a `proven_minimum` at or below the current value is a
    no-op, and there is no path here that lowers a counter or removes a
    receipt. ERP's `reset_sequence` does both, which is how a committed number
    is reused.
    """
    if proven_minimum < 1:
        raise _error(
            "invalid_proven_minimum",
            "Repair evidence must be a positive value.",
            proven_minimum=proven_minimum,
        )
    if proven_minimum >= MAX_VALUE:
        raise _error(
            "invalid_proven_minimum",
            "Repair evidence exceeds the maximum supported value.",
            proven_minimum=proven_minimum,
        )
    series = _locked_series(db, scope, series_code)
    if proven_minimum + 1 > series.next_value:
        series.next_value = proven_minimum + 1
        db.flush()
    return int(series.next_value)


__all__ = [
    "MAX_VALUE",
    "Allocation",
    "NumberingError",
    "advance_to_at_least",
    "allocate",
    "format_number",
    "period_for",
    "preview",
    "request_fingerprint",
]
