"""Receipt delivery's concurrency contract, specified against REAL Postgres.

These canaries were written BEFORE the persistence they describe, and they fail
until it exists. That order is deliberate: a canary written after the code tends
to describe whatever the code happens to do, whereas a failing canary written
first is the specification the code has to meet.

## Why they are RED, and what makes that safe to merge

`inbox_receipts` has no `leased_until`, no `next_attempt_at` and no product
outcome columns yet. Adding them is a receipt-state model change, and this
programme stages that ownership: Team 2 is still finishing `models.py` and
`ig_0003`, and Team 3's trusted destination (PR #184) is not merged. Writing
those columns now would collide with `models.py` mid-edit, and a migration that
rewrote another team's revision could not be replayed or audited.

So each blocked canary carries `xfail(strict=True, raises=ProgrammingError)`,
which is a much narrower claim than "this test is allowed to fail":

* it must FAIL — a canary that started passing without the columns would be
  testing nothing, and `strict=True` turns that into an error;
* it must fail with `ProgrammingError` — i.e. *because the column is missing*.
  A typo, a bad fixture or a broken predicate raises something else and the
  suite goes red, so these cannot rot into green-by-accident.

When the columns land, every one of these XPASSes, `strict=True` fails the
build, and the marker must be removed. That is the ratchet: the block cannot be
forgotten, and it cannot be silently extended.

## What is proved here that a unit test cannot

`tests/unit/test_integration_receipt_delivery.py` proves the ORDERING and the
decisions. It cannot prove a race: SQLite cannot demonstrate two sessions
contending for one row, and asserting a race against a fake store would only
test the fake. Every claim below is made with two real sessions against the
mechanism that actually ships.

Each guard also carries a SENSITIVITY PROOF (ADR-0018): the guard is shown to
FAIL on the shape it forbids, not merely to pass on the shape it allows. A
predicate that accepted everything would satisfy the happy path of every test
here, so "it passed" is only evidence once the detector is shown to bite.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

# Reused, not re-declared: a second copy of the scratch-database fixture would
# drift from the one in the module that owns it, and this suite has no reason to
# compose the lineages differently.
from tests.test_integration_isolation import (  # noqa: F401
    _installation_and_binding,
    migrated_scratch,
)

#: Every column receipt delivery needs and does not yet have. Named in ONE place
#: so the ratchet below and the blocked marker cannot disagree about what the
#: blocker actually is.
REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {
        # the lease — the claim's time bound
        "leased_until",
        # due scheduling — honours the retry curve the engine already computes
        "next_attempt_at",
        # replay vs conflict, mirroring the kernel's own fingerprint column
        "delivery_fingerprint",
        # the key the PRODUCT deduplicates on, recorded so a reconciler can ask
        # the product about the same delivery this engine attempted
        "delivery_idempotency_key",
        "correlation_id",
        # typed product outcome + reconciliation evidence
        "product_acceptance",
        "product_ref",
        # the destination this was actually delivered to, as provenance
        "destination_application",
        "destination_contract_version",
        "destination_config_revision_id",
    }
)

_BLOCKED = pytest.mark.xfail(
    strict=True,
    raises=ProgrammingError,
    reason=(
        "BLOCKED on staged ownership: receipt-state columns arrive after Team 2 "
        "lands models.py/ig_0003 and Team 3 freezes the destination identity "
        "(PR #184). Written first, on purpose — see this module's docstring. "
        "Remove this marker in the same change that adds the columns."
    ),
)

#: Phase 1. The claim ig_0004 must support: a CONDITIONAL UPDATE where
#: `rowcount == 1` IS the claim, exactly as `execution.claim_delivery` already
#: does for the outbox.
CLAIM_SQL = text(
    "UPDATE mod_intg.inbox_receipts SET state = 'processing', "
    "attempt_count = attempt_count + 1, "
    "leased_until = now() + interval '300 seconds' "
    "WHERE id = :id "
    "AND state NOT IN ('processed', 'dead_letter', 'reconciliation_required') "
    "AND (leased_until IS NULL OR leased_until < now()) "
    "AND (next_attempt_at IS NULL OR next_attempt_at <= now())"
)

#: Phase 3. Guarded by the CLAIM's identity: this receipt, this attempt, and a
#: lease that has not expired.
SETTLE_SQL = text(
    "UPDATE mod_intg.inbox_receipts SET state = :state, leased_until = NULL, "
    "processed_at = now(), product_acceptance = :acceptance, "
    "product_ref = :product_ref "
    "WHERE id = :id AND state = 'processing' AND attempt_count = :attempt "
    "AND leased_until IS NOT NULL AND leased_until >= now()"
)


def _receipt(  # type: ignore[no-untyped-def]
    conn,
    request,
    *,
    state: str = "verified",
    attempt_count: int = 0,
) -> uuid.UUID:
    """One receipt in a state of the caller's choosing, ready to be claimed.

    `state` and `attempt_count` are named parameters rather than a `**columns`
    splat: the splat built the column list by string concatenation, which is
    both an injection shape and — since `state` is already in the fixed column
    list — a duplicate-column error waiting for the first caller that overrides
    it. Bound parameters throughout.
    """
    installation_id, binding_id = _installation_and_binding(conn, request)
    receipt_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_intg.inbox_receipts ("
            "id, installation_id, capability_binding_id, provider_event_id, "
            "event_type, payload_digest, state, attempt_count) VALUES ("
            ":id, :inst, :binding, :event, 'message.received', :digest, "
            ":state, :attempt_count)"
        ),
        {
            "id": receipt_id,
            "inst": installation_id,
            "binding": binding_id,
            "event": f"evt_{uuid.uuid4().hex[:10]}",
            "digest": "a" * 64,
            "state": state,
            "attempt_count": attempt_count,
        },
    )
    return receipt_id


# ── The ratchet ─────────────────────────────────────────────────────────────


def test_the_blocker_is_exactly_the_missing_receipt_delivery_columns(
    migrated_scratch: tuple[str, str],  # noqa: F811
) -> None:
    """Names the handoff this suite is waiting on, and fails when it arrives.

    Without this, the blocked canaries above could stay `xfail` forever and
    nobody would notice the block had been lifted. This asserts the columns are
    ABSENT, so the moment the migration lands it goes red and points at the
    markers that must come off — the two-directional ratchet ADR-0018 asks for,
    rather than a note in a backlog.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        live = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'mod_intg' "
                    "AND table_name = 'inbox_receipts'"
                )
            )
        }
    engine.dispose()

    # Not vacuous: the table must actually exist and carry its known columns,
    # or an empty result would satisfy "none of mine are present" while proving
    # the lineage never applied.
    assert {"id", "state", "attempt_count", "payload_digest"} <= live

    present = REQUIRED_COLUMNS & live
    assert not present, (
        f"receipt delivery columns {sorted(present)} now exist. The staged "
        "handoff from Teams 2 and 3 has landed: remove the `_BLOCKED` markers "
        "in this module and implement the store against these columns."
    )


# ── Two workers cannot both claim ───────────────────────────────────────────


@_BLOCKED
def test_only_one_worker_can_claim_a_receipt(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """THE defect, proved as a race rather than asserted.

    `execution.claim_receipt` assigns `state` in Python: two workers both read
    the row, both see it unclaimed, and both believe they hold it. Here both
    issue the same conditional UPDATE and exactly one must report a row —
    because the database evaluated the predicate, and the loser sees 0.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    with setup.begin() as conn:
        receipt_id = _receipt(conn, request)
    setup.dispose()

    first, second = create_engine(admin_url), create_engine(admin_url)
    with first.begin() as a:
        won = a.execute(CLAIM_SQL, {"id": receipt_id}).rowcount
    with second.begin() as b:
        lost = b.execute(CLAIM_SQL, {"id": receipt_id}).rowcount
    first.dispose()
    second.dispose()

    assert (won, lost) == (1, 0), "two workers both claimed one receipt"

    check = create_engine(admin_url)
    with check.connect() as conn:
        attempts = conn.execute(
            text("SELECT attempt_count FROM mod_intg.inbox_receipts WHERE id = :id"),
            {"id": receipt_id},
        ).scalar_one()
    check.dispose()
    assert attempts == 1, "the losing claim still incremented the attempt counter"


@_BLOCKED
def test_the_claim_without_its_lease_predicate_lets_both_workers_win(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """The sensitivity proof for the race above (ADR-0018).

    The test above is only evidence if the lease predicate is what decides it.
    So the SAME race is run with that predicate removed — the in-memory shape
    `claim_receipt` has today — and both workers must win. If this also produced
    one winner, something incidental (a lock, the test's own serialisation)
    would be doing the work and the guard would be unproven.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    with setup.begin() as conn:
        receipt_id = _receipt(conn, request)
    setup.dispose()

    unguarded = text(
        "UPDATE mod_intg.inbox_receipts SET state = 'processing', "
        "attempt_count = attempt_count + 1, "
        "leased_until = now() + interval '300 seconds' "
        "WHERE id = :id AND state <> 'processed'"
    )
    first, second = create_engine(admin_url), create_engine(admin_url)
    with first.begin() as a:
        won = a.execute(unguarded, {"id": receipt_id}).rowcount
    with second.begin() as b:
        also_won = b.execute(unguarded, {"id": receipt_id}).rowcount
    first.dispose()
    second.dispose()

    assert (won, also_won) == (1, 1), (
        "the unguarded claim produced one winner, so the guarded test above "
        "proves nothing about the lease"
    )


# ── Stale-lease recovery ────────────────────────────────────────────────────


@_BLOCKED
def test_an_expired_lease_returns_the_receipt_to_the_queue(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """A worker that died mid-attempt must not strand the receipt forever.

    This is the recovery path that lets an unexpected exception propagate
    without settling: the lease expires, and the next worker takes it over.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        receipt_id = _receipt(conn, request, state="processing", attempt_count=1)
        conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts "
                "SET leased_until = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": receipt_id},
        )
        assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 1
        attempts = conn.execute(
            text("SELECT attempt_count FROM mod_intg.inbox_receipts WHERE id = :id"),
            {"id": receipt_id},
        ).scalar_one()
    engine.dispose()
    assert attempts == 2, "recovery must count as a new attempt, not reuse the old one"


@_BLOCKED
def test_a_live_lease_is_not_recoverable(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """The sensitivity proof for recovery: a lease that has NOT expired must
    hold. A predicate that recovered everything would pass the test above while
    handing a live attempt to a second worker."""
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        receipt_id = _receipt(conn, request, state="processing", attempt_count=1)
        conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts "
                "SET leased_until = now() + interval '300 seconds' "
                "WHERE id = :id"
            ),
            {"id": receipt_id},
        )
        assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 0
    engine.dispose()


@_BLOCKED
def test_a_receipt_scheduled_for_a_later_attempt_is_not_due(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """Due scheduling, against a real clock.

    `retry.retry_delay_seconds` already computes a backoff curve. Without
    `next_attempt_at` in the claim predicate nothing honours it, and a failing
    product is hammered instead of backed off.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        receipt_id = _receipt(conn, request, state="retryable", attempt_count=1)
        conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts "
                "SET next_attempt_at = now() + interval '1 hour' WHERE id = :id"
            ),
            {"id": receipt_id},
        )
        assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 0

        # Sensitivity: once it IS due the very same predicate must claim it,
        # or "not claimable" would be proving a broken query rather than backoff.
        conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts "
                "SET next_attempt_at = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": receipt_id},
        )
        assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 1
    engine.dispose()


# ── Settlement after lease expiry — the LostClaim case ──────────────────────


@_BLOCKED
def test_a_worker_whose_lease_expired_cannot_settle(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """The `LostClaim` case, at the database.

    A worker whose lease expired during a slow product call has already been
    superseded. Its settlement must change NOTHING — `rowcount == 0` is how it
    finds out, and `deliver_receipt` turns that into a typed `LostClaim`.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        receipt_id = _receipt(conn, request, state="processing", attempt_count=1)
        conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts "
                "SET leased_until = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": receipt_id},
        )
        settled = conn.execute(
            SETTLE_SQL,
            {
                "id": receipt_id,
                "state": "processed",
                "acceptance": "accepted",
                "product_ref": "msg_1",
                "attempt": 1,
            },
        ).rowcount
        assert settled == 0, "an expired lease settled an attempt it no longer held"

        state = conn.execute(
            text("SELECT state FROM mod_intg.inbox_receipts WHERE id = :id"),
            {"id": receipt_id},
        ).scalar_one()
    engine.dispose()
    assert state == "processing", "the lost settlement still mutated the receipt"


@_BLOCKED
def test_the_settlement_race_after_a_takeover_has_exactly_one_winner(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """The full sequence, with two real sessions.

    Worker A claims attempt 1 and starts a slow call. Its lease expires, worker
    B takes over as attempt 2, and both then try to settle. B must win, because
    A's guard names attempt 1 — and A's outcome, computed from a call whose
    result is no longer authoritative, must not overwrite it.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    with setup.begin() as conn:
        receipt_id = _receipt(conn, request)
        # Worker A claims.
        assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 1
        # A's lease expires while it is calling the product.
        conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts "
                "SET leased_until = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": receipt_id},
        )
        # Worker B takes over: attempt 2, fresh lease.
        assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 1
    setup.dispose()

    late, holder = create_engine(admin_url), create_engine(admin_url)
    with late.begin() as a:
        stale = a.execute(
            SETTLE_SQL,
            {
                "id": receipt_id,
                "state": "dead_letter",
                "acceptance": "rejected",
                "product_ref": None,
                "attempt": 1,
            },
        ).rowcount
    with holder.begin() as b:
        won = b.execute(
            SETTLE_SQL,
            {
                "id": receipt_id,
                "state": "processed",
                "acceptance": "accepted",
                "product_ref": "msg_1",
                "attempt": 2,
            },
        ).rowcount
    late.dispose()
    holder.dispose()

    assert (stale, won) == (0, 1)

    check = create_engine(admin_url)
    with check.connect() as conn:
        state, ref = conn.execute(
            text(
                "SELECT state, product_ref FROM mod_intg.inbox_receipts "
                "WHERE id = :id"
            ),
            {"id": receipt_id},
        ).one()
    check.dispose()
    assert (state, ref) == ("processed", "msg_1"), (
        "the superseded worker overwrote the outcome of the worker that "
        "actually held the claim"
    )


@_BLOCKED
def test_settlement_without_its_identity_guard_lets_the_stale_worker_win(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """The sensitivity proof for settlement (ADR-0018).

    The same stale settlement is issued WITHOUT the attempt/lease guard — the
    read-compare-write shape — and it must succeed, overwriting the holder's
    outcome. If it did not, the guarded tests above would be passing for some
    other reason and the guard would be unproven.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        receipt_id = _receipt(conn, request, state="processing", attempt_count=2)
        conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts "
                "SET leased_until = now() + interval '300 seconds' "
                "WHERE id = :id"
            ),
            {"id": receipt_id},
        )
        unguarded = conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts SET state = 'dead_letter', "
                "product_acceptance = 'rejected' WHERE id = :id"
            ),
            {"id": receipt_id},
        ).rowcount
    engine.dispose()

    assert unguarded == 1, (
        "the unguarded settlement changed nothing, so the guarded settlement "
        "tests prove nothing about the attempt/lease identity"
    )


# ── Rollback ────────────────────────────────────────────────────────────────


@_BLOCKED
def test_a_rolled_back_claim_leaves_the_receipt_claimable(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """The claim is a TRANSACTION, not a side effect.

    A claim that survived its own rollback would leak a lease on work nobody is
    doing, and the receipt would sit unclaimable until the lease aged out — with
    no worker and no error to explain it.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    with setup.begin() as conn:
        receipt_id = _receipt(conn, request)
    setup.dispose()

    engine = create_engine(admin_url)
    with engine.connect() as conn:
        transaction = conn.begin()
        assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 1
        transaction.rollback()

        row = conn.execute(
            text(
                "SELECT state, attempt_count, leased_until "
                "FROM mod_intg.inbox_receipts WHERE id = :id"
            ),
            {"id": receipt_id},
        ).one()
        assert row == ("verified", 0, None), "the rolled-back claim persisted"

        # and the receipt is genuinely available again
        with conn.begin():
            assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 1
    engine.dispose()


@_BLOCKED
def test_a_committed_claim_is_not_claimable_again(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """The sensitivity proof for rollback: a COMMITTED claim must hold.

    Without this, the rollback test would also pass against a claim predicate
    that never excluded anything — "claimable again" would be true because it
    was always true, not because the rollback worked.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    with setup.begin() as conn:
        receipt_id = _receipt(conn, request)
    setup.dispose()

    engine = create_engine(admin_url)
    with engine.begin() as conn:
        assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 1
    with engine.begin() as conn:
        assert conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 0
    engine.dispose()


# ── Replay vs conflict, at the database ─────────────────────────────────────


@_BLOCKED
def test_a_finished_receipt_is_never_reclaimed(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """`processed`, `dead_letter` and `reconciliation_required` are final.

    Reclaiming a processed receipt would repeat a product consequence that
    already exists — the exact failure the idempotency key defends against, and
    there is no reason to rely on that defence when the claim can simply refuse.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        for finished in ("processed", "dead_letter", "reconciliation_required"):
            receipt_id = _receipt(conn, request, state=finished, attempt_count=1)
            assert (
                conn.execute(CLAIM_SQL, {"id": receipt_id}).rowcount == 0
            ), f"a {finished} receipt was re-claimed"

        # Sensitivity: a retryable receipt in the same table IS claimable, so
        # the refusals above are the state predicate and not a broken statement.
        live = _receipt(conn, request, state="retryable", attempt_count=1)
        assert conn.execute(CLAIM_SQL, {"id": live}).rowcount == 1
    engine.dispose()
