"""Unit tests for `dotmac_kernel.idempotency` (ADR-0014) — SQLite, no RLS.

Covers the contract itself. Several cases are named after the specific product
defect they pin against, because the point of extracting this facility was that
six implementations disagreed; see `docs/inventories/idempotency-sources.md`.
Tenant isolation of the ledger is proven separately on Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.exceptions import BadRequestError, ConflictError
from dotmac_kernel.idempotency import (
    IdempotencyConflict,
    execute_once,
    execute_once_platform,
    fingerprint_of,
    purge_expired,
)
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.models import Tenant
from sqlalchemy import select
from sqlalchemy.orm import Session


# ── fingerprint ──────────────────────────────────────────────────────────────
def test_fingerprint_is_stable_across_key_order() -> None:
    """Dict ordering must not change the digest, or a client that serialises its
    payload differently between attempts would look like a conflicting request."""
    assert fingerprint_of({"a": 1, "b": 2}) == fingerprint_of({"b": 2, "a": 1})


def test_fingerprint_distinguishes_different_payloads() -> None:
    assert fingerprint_of({"amount": 100}) != fingerprint_of({"amount": 101})


def test_fingerprint_handles_non_json_types() -> None:
    """A UUID or Decimal in the payload must fingerprint rather than raise."""
    from decimal import Decimal
    from uuid import uuid4

    value = uuid4()
    assert fingerprint_of({"id": value, "amt": Decimal("1.5")}) == fingerprint_of(
        {"id": value, "amt": Decimal("1.5")}
    )


def test_fingerprint_accepts_an_object_exposing_model_dump() -> None:
    """Duck-typed pydantic support, without the kernel importing pydantic."""

    class Payload:
        def model_dump(self, mode: str = "python") -> dict[str, object]:
            return {"a": 1}

    assert fingerprint_of(Payload()) == fingerprint_of({"a": 1})


# ── at-most-once execution ───────────────────────────────────────────────────
def test_execute_once_runs_the_operation_and_records_it(
    db: Session, tenant_row: Tenant
) -> None:
    calls: list[int] = []

    def op(session: Session) -> dict[str, object]:
        calls.append(1)
        return {"created": "x"}

    outcome = execute_once(
        db,
        tenant_id=tenant_row.id,
        scope="billing.charge",
        key="k1",
        operation=op,
        fingerprint=fingerprint_of({"amount": 100}),
        correlation_id="corr-1",
    )

    assert outcome.replayed is False
    assert outcome.result == {"created": "x"}
    assert calls == [1]

    record = db.execute(
        select(IdempotencyRecord).where(IdempotencyRecord.key == "k1")
    ).scalar_one()
    assert record.scope == "billing.charge"
    assert record.status == "executed"
    assert record.operation == "billing.charge"
    assert record.correlation_id == "corr-1"
    assert record.expires_at is None


def test_execute_once_replays_instead_of_re_running(
    db: Session, tenant_row: Tenant
) -> None:
    calls: list[int] = []

    def op(session: Session) -> dict[str, object]:
        calls.append(1)
        return {"n": len(calls)}

    kwargs = {"tenant_id": tenant_row.id, "scope": "s", "key": "same", "operation": op}
    first = execute_once(db, **kwargs)  # type: ignore[arg-type]
    second = execute_once(db, **kwargs)  # type: ignore[arg-type]

    assert first.replayed is False
    assert second.replayed is True
    assert second.result == first.result == {"n": 1}
    assert calls == [1]  # exactly once


def test_replayed_flag_distinguishes_a_replay_from_a_first_execution(
    db: Session, tenant_row: Tenant
) -> None:
    """Neither ERP nor Sub's API-level mechanism exposed this, so a caller could
    not tell a fresh execution from a replay — it is part of the contract here."""
    op = lambda session: {"v": 1}  # noqa: E731
    kwargs = {"tenant_id": tenant_row.id, "scope": "s", "key": "flag", "operation": op}
    assert execute_once(db, **kwargs).replayed is False  # type: ignore[arg-type]
    assert execute_once(db, **kwargs).replayed is True  # type: ignore[arg-type]


def test_the_same_key_in_a_different_scope_is_a_different_operation(
    db: Session, tenant_row: Tenant
) -> None:
    calls: list[str] = []

    def make(tag: str):
        def op(session: Session) -> dict[str, object]:
            calls.append(tag)
            return {"tag": tag}

        return op

    execute_once(
        db, tenant_id=tenant_row.id, scope="scope.a", key="k", operation=make("a")
    )
    execute_once(
        db, tenant_id=tenant_row.id, scope="scope.b", key="k", operation=make("b")
    )
    assert calls == ["a", "b"]


def test_a_different_tenant_reusing_the_key_still_executes(
    db: Session, tenant_row: Tenant
) -> None:
    """The ledger is keyed by tenant, so one tenant's key can never suppress
    another's operation. Sub's shared table has no tenant dimension at all, and
    a shared facility must not inherit that."""
    other = Tenant(slug="other", name="Other")
    db.add(other)
    db.flush()

    calls: list[str] = []

    def make(tag: str):
        def op(session: Session) -> dict[str, object]:
            calls.append(tag)
            return {}

        return op

    execute_once(db, tenant_id=tenant_row.id, scope="s", key="k", operation=make("t1"))
    execute_once(db, tenant_id=other.id, scope="s", key="k", operation=make("t2"))
    assert calls == ["t1", "t2"]


# ── conflict on a reused key with a different request ────────────────────────
def test_same_key_different_fingerprint_is_a_conflict(
    db: Session, tenant_row: Tenant
) -> None:
    op = lambda session: {"ok": True}  # noqa: E731
    execute_once(
        db,
        tenant_id=tenant_row.id,
        scope="s",
        key="k",
        operation=op,
        fingerprint=fingerprint_of({"amount": 100}),
    )

    with pytest.raises(IdempotencyConflict):
        execute_once(
            db,
            tenant_id=tenant_row.id,
            scope="s",
            key="k",
            operation=op,
            fingerprint=fingerprint_of({"amount": 999}),
        )


def test_idempotency_conflict_is_a_conflict_error() -> None:
    """So an assembly that already maps domain errors to HTTP returns 409 with
    no extra wiring."""
    assert issubclass(IdempotencyConflict, ConflictError)


def test_a_missing_fingerprint_on_either_side_replays_rather_than_conflicts(
    db: Session, tenant_row: Tenant
) -> None:
    """`None` means the caller asserts the key alone identifies the request —
    true for a transport-generated command id. Second-guessing that would break
    `messaging.process_once`, which records no fingerprint."""
    op = lambda session: {"ok": True}  # noqa: E731
    execute_once(db, tenant_id=tenant_row.id, scope="s", key="k", operation=op)

    replayed = execute_once(
        db,
        tenant_id=tenant_row.id,
        scope="s",
        key="k",
        operation=op,
        fingerprint=fingerprint_of({"anything": 1}),
    )
    assert replayed.replayed is True


# ── the crash path: no stuck placeholder (ADR-0014 § 5) ──────────────────────
def test_a_failing_operation_records_nothing_so_the_retry_re_drives(
    db: Session, tenant_row: Tenant
) -> None:
    """ERP reserves a `202 "Request in progress"` row BEFORE the effect, so a
    request that dies leaves a placeholder replayed to every retry for 24 hours.
    Here the row and the effect commit together, so a failure leaves no marker
    and the retry runs for real."""
    attempts: list[int] = []

    def flaky(session: Session) -> dict[str, object]:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        return {"ok": True}

    with pytest.raises(RuntimeError):
        execute_once(
            db, tenant_id=tenant_row.id, scope="s", key="retry-me", operation=flaky
        )

    assert (
        db.execute(
            select(IdempotencyRecord).where(IdempotencyRecord.key == "retry-me")
        ).all()
        == []
    ), "a failed attempt must leave no ledger row"

    outcome = execute_once(
        db, tenant_id=tenant_row.id, scope="s", key="retry-me", operation=flaky
    )
    assert outcome.replayed is False
    assert outcome.result == {"ok": True}
    assert len(attempts) == 2


# ── key validation ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad_key", ["", "   "])
def test_an_empty_key_is_rejected(
    db: Session, tenant_row: Tenant, bad_key: str
) -> None:
    with pytest.raises(BadRequestError):
        execute_once(
            db,
            tenant_id=tenant_row.id,
            scope="s",
            key=bad_key,
            operation=lambda session: {},
        )


def test_an_over_long_key_is_rejected_not_truncated(
    db: Session, tenant_row: Tenant
) -> None:
    """Truncating would silently merge two distinct requests into one ledger
    entry — the second would replay the first's result."""
    with pytest.raises(BadRequestError):
        execute_once(
            db,
            tenant_id=tenant_row.id,
            scope="s",
            key="x" * 201,
            operation=lambda session: {},
        )


def test_an_empty_scope_is_rejected(db: Session, tenant_row: Tenant) -> None:
    with pytest.raises(BadRequestError):
        execute_once(
            db,
            tenant_id=tenant_row.id,
            scope="",
            key="k",
            operation=lambda session: {},
        )


# ── retention ────────────────────────────────────────────────────────────────
def test_purge_removes_expired_rows_only(db: Session, tenant_row: Tenant) -> None:
    now = datetime.now(UTC)
    execute_once(
        db,
        tenant_id=tenant_row.id,
        scope="s",
        key="stale",
        operation=lambda session: {},
        expires_at=now - timedelta(hours=1),
    )
    execute_once(
        db,
        tenant_id=tenant_row.id,
        scope="s",
        key="fresh",
        operation=lambda session: {},
        expires_at=now + timedelta(hours=1),
    )

    assert purge_expired(db, now=now) == 1
    remaining = db.execute(select(IdempotencyRecord.key)).scalars().all()
    assert remaining == ["fresh"]


def test_purge_never_removes_a_row_with_no_expiry(
    db: Session, tenant_row: Tenant
) -> None:
    """A NULL `expires_at` means the product made no retention decision. Purging
    it would let a replay re-execute — Sub's table has no expiry at all, and the
    kernel must not invent one on its behalf."""
    execute_once(
        db,
        tenant_id=tenant_row.id,
        scope="s",
        key="forever",
        operation=lambda session: {},
    )
    assert purge_expired(db, now=datetime.now(UTC) + timedelta(days=3650)) == 0
    assert db.execute(select(IdempotencyRecord.key)).scalars().all() == ["forever"]


def test_purged_key_becomes_executable_again(db: Session, tenant_row: Tenant) -> None:
    """Retention and replay are the same decision: once a key is purged the
    operation is no longer suppressed. This is why expiry must be explicit."""
    now = datetime.now(UTC)
    calls: list[int] = []

    def op(session: Session) -> dict[str, object]:
        calls.append(1)
        return {"n": len(calls)}

    kwargs = {"tenant_id": tenant_row.id, "scope": "s", "key": "k", "operation": op}
    execute_once(db, **kwargs, expires_at=now - timedelta(hours=1))  # type: ignore[arg-type]
    purge_expired(db, now=now)
    second = execute_once(db, **kwargs)  # type: ignore[arg-type]

    assert second.replayed is False
    assert calls == [1, 1]


# ── platform variant ─────────────────────────────────────────────────────────
def test_execute_once_platform_replays_without_a_tenant(db: Session) -> None:
    calls: list[int] = []

    def op(session: Session) -> dict[str, object]:
        calls.append(1)
        return {"n": len(calls)}

    first = execute_once_platform(db, scope="vendor.account", key="p1", operation=op)
    second = execute_once_platform(db, scope="vendor.account", key="p1", operation=op)

    assert first.replayed is False
    assert second.replayed is True
    assert second.result == {"n": 1}
    assert calls == [1]


def test_execute_once_platform_conflicts_on_a_different_fingerprint(
    db: Session,
) -> None:
    op = lambda session: {}  # noqa: E731
    execute_once_platform(
        db, scope="s", key="p", operation=op, fingerprint=fingerprint_of({"a": 1})
    )
    with pytest.raises(IdempotencyConflict):
        execute_once_platform(
            db, scope="s", key="p", operation=op, fingerprint=fingerprint_of({"a": 2})
        )
