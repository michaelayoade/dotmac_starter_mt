"""Receipt delivery's concurrency contract, specified against REAL Postgres.

These canaries were written BEFORE the persistence they describe, and were
merged red — `xfail(strict=True, raises=ProgrammingError)`, a much narrower
claim than "allowed to fail": they had to fail, and to fail *because the column
was missing*. `ig_0005_receipt_delivery` landed the columns and
`receipt_delivery.ReceiptClaims` landed the statements, so the markers are gone
and every canary below runs for real.

The ratchet that guarded the staging is inverted rather than deleted: it
asserted the columns were ABSENT so the block could not be silently forgotten,
and now asserts they are PRESENT so a migration cannot silently remove one.

## The statements under test are the ones that ship

`CLAIM_SQL` and `SETTLE_SQL` are imported from `receipt_delivery`, not retyped
here. A canary carrying its own copy of the SQL proves that copy races
correctly and says nothing about the module — the same mistake as asserting a
race against a fake, one layer down.

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

## Sequential contention vs a genuine race

Most canaries below issue their two statements SEQUENTIALLY. For a conditional
UPDATE that is a real proof of the predicate — an already-claimed row must not
be claimable again — but it is NOT a proof of simultaneity, and a sequential
"race" that never races is a recurring defect in this fleet. So the central
claim, that two workers cannot both take one receipt, is ALSO made with two
genuine threads meeting at a barrier:
`test_two_concurrent_workers_produce_exactly_one_claim`.

Every rendezvous and worker wait is bounded (`_RACE_TIMEOUT`). An unbounded
concurrency test is not a usable guard: the same pattern once consumed a CI
job's full six-hour limit before being cancelled, which is a much worse failure
than a red test.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from dotmac_integration.receipt_delivery import claim_statement, settle_statement
from sqlalchemy import create_engine, text

# Reused, not re-declared: a second copy of the scratch-database fixture would
# drift from the one in the module that owns it, and this suite has no reason to
# compose the lineages differently.
from tests.test_integration_isolation import (  # noqa: F401
    _installation_and_binding,
    migrated_scratch,
)

#: Seconds. Short and explicit — a barrier or lock wait that can hang forever
#: turns a failing guard into a stuck CI job rather than a red one.
_RACE_TIMEOUT = 20.0

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
        "destination_revision_id",
    }
)

#: THE STATEMENTS THAT SHIP. Imported, never retyped — a canary that retyped
#: the SQL would prove its own copy races correctly and say nothing about the
#: module, which is the same mistake as asserting a race against a fake.
CLAIM_SQL = claim_statement()
SETTLE_SQL = settle_statement()


def _settle_params(receipt_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    """Settle binds, with the columns a given canary does not care about
    defaulted. The statement writes ten columns; most canaries are about the
    WHERE clause, and spelling all ten at each call site would bury the one
    parameter that is actually under test."""
    params: dict[str, object] = {
        "id": receipt_id,
        "attempt": 1,
        "state": "processed",
        "acceptance": "accepted",
        "product_ref": None,
        "error_code": None,
        "error_detail": None,
        "fingerprint": None,
        "idempotency_key": None,
        "correlation_id": None,
        "backoff": None,
    }
    params.update(overrides)
    return params


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


def test_every_column_the_engine_claims_against_exists(
    migrated_scratch: tuple[str, str],  # noqa: F811
) -> None:
    """Every column the engine claims against exists, named in one place.

    This was an ABSENCE ratchet while the columns were staged behind another
    team: it asserted they were missing so the block could not be forgotten.
    `ig_0005_receipt_delivery` landed them, so it now asserts the opposite —
    they are PRESENT, and a migration that dropped or renamed one fails here
    rather than at the first claim in production.

    `REQUIRED_COLUMNS` is still the single place the set is written, so the
    statements above and this guard cannot disagree about what the engine
    needs.
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

    # Not vacuous: the table must carry its pre-existing columns too, or an
    # empty result would fail the check below for the wrong reason — a lineage
    # that never applied, rather than a column that went missing.
    assert {"id", "state", "attempt_count", "payload_digest"} <= live

    missing = REQUIRED_COLUMNS - live
    assert not missing, (
        f"receipt delivery columns {sorted(missing)} are gone. The claim and "
        "settle statements bind them by name, so every delivery would fail at "
        "the database rather than at review."
    )


# ── Two workers cannot both claim ───────────────────────────────────────────


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


def test_the_race_harness_actually_races(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """Proves the THREADING HARNESS before anything relies on it.

    The receipt race below is blocked, so it cannot yet show that
    `_claim_concurrently` runs at all. A harness nobody has seen succeed is not
    evidence of anything, so the identical harness is pointed at
    `delivery_attempts` — which already HAS a lease and the conditional claim it
    needs — and must produce exactly one winner without hanging.

    **What this does and does not claim.** `[0, 1]` is the correct outcome
    whether the two statements genuinely collide or merely run in turn, so
    neither this test nor the receipt race can assert that a collision *did*
    occur. What the threaded form buys is COVERAGE of a different database path:
    when the two UPDATEs do overlap, the loser blocks on the winner's row lock
    and PostgreSQL re-evaluates its predicate against the newly committed row.
    A sequential test never reaches that path, and a claim can be wrong there
    while looking right in turn-taking. The barrier makes the overlap likely; it
    cannot make it certain.

    The load-bearing evidence that the predicate is what decides remains the
    sensitivity proof below, which removes it and observes both workers win.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    delivery_id = uuid.uuid4()
    with setup.begin() as conn:
        installation_id, binding_id = _installation_and_binding(conn, request)
        conn.execute(
            text(
                "INSERT INTO mod_intg.delivery_attempts ("
                "id, installation_id, capability_binding_id, event_type, "
                "idempotency_key, payload_digest, state) VALUES ("
                ":id, :inst, :binding, 'e', :key, :digest, 'pending')"
            ),
            {
                "id": delivery_id,
                "inst": installation_id,
                "binding": binding_id,
                "key": f"race-{uuid.uuid4().hex[:8]}",
                "digest": "b" * 64,
            },
        )
    setup.dispose()

    delivery_claim = text(
        "UPDATE mod_intg.delivery_attempts SET state = 'in_flight', "
        "attempt_count = attempt_count + 1, "
        "leased_until = now() + interval '300 seconds' "
        "WHERE id = :id AND state NOT IN "
        "('delivered', 'dead_letter', 'reconciliation_required') "
        "AND (leased_until IS NULL OR leased_until < now()) "
        "AND (next_attempt_at IS NULL OR next_attempt_at <= now())"
    )

    results = _claim_concurrently(admin_url, delivery_claim, delivery_id)

    assert results == [0, 1], (
        f"the race harness reported {results} against a table that DOES have a "
        "lease. The harness is not producing contention, so the receipt race "
        "it is shared with would prove nothing once unblocked"
    )


def _claim_concurrently(
    admin_url: str, statement: object, row_id: uuid.UUID
) -> list[int]:
    """Two threads, two engines, meeting at a barrier immediately before the write.

    Shared by the receipt race and the harness proof above, so the mechanism
    those two tests rely on is identical rather than merely similar.

    Every wait is bounded: an unbounded concurrency test is not a usable guard,
    and the same pattern once consumed a CI job's full six-hour limit before
    being cancelled — a far worse failure than a red test.
    """
    start = threading.Barrier(2, timeout=_RACE_TIMEOUT)

    def worker() -> int:
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                # Open the transaction BEFORE the rendezvous, so the barrier
                # separates the two UPDATEs and not the connection setup.
                transaction = conn.begin()
                start.wait()
                rowcount = conn.execute(statement, {"id": row_id}).rowcount  # type: ignore[arg-type]
                transaction.commit()
                return int(rowcount)
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker), pool.submit(worker)]
        # `result()` re-raises whatever the worker raised — including the
        # ProgrammingError the blocked canary is expected to fail with, which is
        # what keeps `raises=ProgrammingError` honest across a thread boundary.
        return sorted(f.result(timeout=_RACE_TIMEOUT) for f in futures)


def test_two_concurrent_workers_produce_exactly_one_claim(
    migrated_scratch: tuple[str, str],  # noqa: F811
    request: pytest.FixtureRequest,
) -> None:
    """The same claim, issued by two real threads instead of in turn.

    The sequential version above proves the predicate excludes an
    already-claimed row. It never reaches the path that matters most for a
    lease: two UPDATEs overlapping, where the loser blocks on the winner's row
    lock and PostgreSQL then re-evaluates its predicate against the newly
    committed row. A claim can be correct when turn-taking and wrong there.

    Two threads, two engines, a barrier immediately before the UPDATE, every
    wait bounded, and worker exceptions re-raised on the main thread rather than
    swallowed into a passing result. See
    `test_the_race_harness_actually_races` for what this form does and does not
    let anyone claim.
    """
    admin_url, _ = migrated_scratch
    setup = create_engine(admin_url)
    with setup.begin() as conn:
        receipt_id = _receipt(conn, request)
    setup.dispose()

    results = _claim_concurrently(admin_url, CLAIM_SQL, receipt_id)

    assert results == [0, 1], (
        f"two concurrent workers reported {results}; exactly one must win a "
        "claim, or both would call the product with one observation"
    )

    check = create_engine(admin_url)
    with check.connect() as conn:
        attempts = conn.execute(
            text("SELECT attempt_count FROM mod_intg.inbox_receipts WHERE id = :id"),
            {"id": receipt_id},
        ).scalar_one()
    check.dispose()
    assert attempts == 1, (
        "the losing worker still incremented the attempt counter — the claim "
        "was not atomic even though only one caller saw a rowcount"
    )


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
            _settle_params(
                receipt_id,
                state="processed",
                acceptance="accepted",
                product_ref="msg_1",
                attempt=1,
            ),
        ).rowcount
        assert settled == 0, "an expired lease settled an attempt it no longer held"

        state = conn.execute(
            text("SELECT state FROM mod_intg.inbox_receipts WHERE id = :id"),
            {"id": receipt_id},
        ).scalar_one()
    engine.dispose()
    assert state == "processing", "the lost settlement still mutated the receipt"


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
            _settle_params(
                receipt_id,
                state="dead_letter",
                acceptance="rejected",
                product_ref=None,
                attempt=1,
            ),
        ).rowcount
    with holder.begin() as b:
        won = b.execute(
            SETTLE_SQL,
            _settle_params(
                receipt_id,
                state="processed",
                acceptance="accepted",
                product_ref="msg_1",
                attempt=2,
            ),
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
