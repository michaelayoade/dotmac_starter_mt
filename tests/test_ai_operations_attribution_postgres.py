"""Hosted PostgreSQL evidence lane for the acknowledgement-attribution
slice (`ao_0001` -> `ao_0002` -> `ao_0003`).

## Why this file exists, and why it cannot be a `tests/unit/` file

`tests/unit/test_ai_operations.py` proves this package's LOGIC against
SQLite, and its own docstrings now state plainly (round 15-18) that
SQLite is NOT a valid persistence round-trip for authoritative
attribution: SQLite's `DATETIME` type discards `tzinfo` on every round
trip, so `authoritative_acknowledgement` — correctly, by Michael's
explicit ruling — RAISES rather than inferring an offset it cannot
prove. Those SQLite tests are the proof of that DECLARED BOUNDARY, not a
substitute for round-tripping attribution through a real database. This
file is the substitute: it proves the actual property (an aware UTC
instant, an actor and evidence, all persisted, reloaded through a
genuinely different session, and resolved paired) against real
PostgreSQL, whose `timestamptz` genuinely preserves the instant.

## Requires real Postgres, and uses the REAL migration chain

`make test-db-up` / `make test-integration` (`TEST_DATABASE_URL`,
`TEST_MIGRATION_DATABASE_URL` — see `tests/conftest.py`'s `admin_engine`/
`admin_session` fixtures, reused here rather than reimplemented).

`dotmac-ai-operations` is explicitly "Tenant-only; not composed by
Starter" (`COMPATIBILITY.md`), so its migration lineage is NOT part of
the starter+kernel `alembic upgrade head` chain `make test-db-up` already
ran before this file's tests execute. This file therefore applies its
own schema by running the REAL, PUBLISHED `ao_0001_ai_operations`
migration followed by the REAL `ao_0002_insight_evidence_binding` and
`ao_0003_ack_attribution` migrations — through Alembic's ambient `op`
proxy, exactly as written, not reimplemented — and downgrades all three
in reverse when the module's tests finish.

Deliberately NOT built from current ORM metadata: `Base.metadata.
create_all()` would prove only that the ORM agrees with itself, which is
exactly the gap a real migration chain closes. `require_prerequisites`
inside `ao_0001.upgrade()` depends on `tenant_scope_catalog.v1` and
`module_database_roles.v1` already being satisfied in the live database
— true here because `make test-db-up` already ran the full
starter+kernel baseline chain first.

`admin_session`/`admin_engine` (RLS bypassed, per `tests/conftest.py`'s
own documentation) are used throughout rather than a plain `app_user`
session, since this file is about the attribution slice's correctness,
not tenant-isolation/RLS correctness (covered elsewhere).

## Not executed by the agent that wrote it

Written to be correct on reading; hosted CI (`make test-integration`) is
the acceptance owner for whether it actually is. No assertion here is
weakened to make it look safer than it has been proven to be.
"""

from __future__ import annotations

import concurrent.futures
import queue
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from dotmac_ai_operations import (
    AIAcknowledgementAttribution,
    AIEvidenceBinding,
    AIOperationRefused,
    InsightInput,
    acknowledge_insight,
    activate_policy_version,
    authoritative_acknowledgement,
    create_insight,
    create_policy,
    publish_policy_version,
    record_attempt,
    start_operation,
)
from dotmac_ai_operations.migrations.versions import ao_0001_ai_operations as ao_0001
from dotmac_ai_operations.migrations.versions import (
    ao_0002_insight_evidence_binding as ao_0002,
)
from dotmac_ai_operations.migrations.versions import (
    ao_0003_ack_attribution as ao_0003,
)
from dotmac_ai_operations.models import AIInsight
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="module")
def _ai_operations_schema(admin_engine) -> Generator[None, None, None]:
    """Apply the REAL `ao_0001` -> `ao_0002` -> `ao_0003` migrations, once
    for this module, through Alembic's ambient `op` proxy — not a
    reimplementation, and not built from ORM metadata. Downgrades all
    three, in reverse, once every test in this module has run, leaving
    the shared test database as it found it.

    `admin_engine` is session-scoped in `tests/conftest.py` and already
    `pytest.skip`s the whole module when `TEST_DATABASE_URL` is not set —
    inherited here rather than reimplemented.
    """
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    with admin_engine.connect() as conn:
        migration_context = MigrationContext.configure(conn)
        with Operations.context(migration_context):
            ao_0001.upgrade()
            ao_0002.upgrade()
            ao_0003.upgrade()
        conn.commit()

    try:
        yield
    finally:
        with admin_engine.connect() as conn:
            migration_context = MigrationContext.configure(conn)
            with Operations.context(migration_context):
                ao_0003.downgrade()
                ao_0002.downgrade()
                ao_0001.downgrade()
            conn.commit()


def _build_acknowledged_insight(
    db: Session,
    *,
    tenant_id,
    insight_key: str,
    operation_key: str,
    at: datetime,
    actor_ref: str,
    evidence_locator_ref: str,
    evidence_digest: str,
    actor_attribution_ref: str,
) -> AIInsight:
    """The full chain a real caller drives: policy -> operation -> insight
    -> acknowledgement, through the REAL production functions — not a
    hand-built row, so every invariant those functions enforce is
    actually exercised."""
    policy = create_policy(
        db,
        tenant_id=tenant_id,
        code=f"conversation.intake.{insight_key}",
        title="Conversation intake",
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant_id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant_id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant_id,
        operation_key=operation_key,
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref=operation_key,
        input_digest="a" * 64,
        started_at=at,
    )
    # `create_insight` requires BOTH a succeeded operation AND a matching
    # succeeded attempt (`service.py`) — `record_attempt` is what makes
    # both true. Without it `create_insight` raises `AIOperationRefused`
    # before this helper reaches attribution persistence at all (round 20
    # finding: this exact omission made both PostgreSQL tests fail in
    # setup and prove nothing).
    record_attempt(
        db,
        tenant_id=tenant_id,
        operation_id=operation.id,
        attempt_key=f"attempt:{insight_key}",
        outcome="succeeded",
        output_ref=f"output:{insight_key}",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    insight = create_insight(
        db,
        tenant_id=tenant_id,
        operation_id=operation.id,
        command=InsightInput(
            insight_key, "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )
    acknowledge_insight(
        db,
        tenant_id=tenant_id,
        insight_id=insight.id,
        actor_ref=actor_ref,
        action_evidence=AIEvidenceBinding(
            locator_namespace="ticketing",
            locator_ref=evidence_locator_ref,
            content_digest=evidence_digest,
            media_type="text/plain",
        ),
        actor_attribution=AIAcknowledgementAttribution(
            actor_namespace="platform-identity",
            actor_type="human",
            actor_ref=actor_attribution_ref,
            acknowledged_at=at,
        ),
        acknowledged_at=at,
    )
    return insight


def test_attribution_round_trips_the_exact_aware_instant_through_postgres(
    _ai_operations_schema: None,
    admin_engine,
    admin_session: Session,
    tenant_a,
) -> None:
    """Item 3: writes the canonical UTC attribution, refreshes (inside
    `acknowledge_insight`'s own `db.refresh`), COMMITS, then reloads
    through a GENUINELY DIFFERENT `Session` bound to the same engine — a
    distinct ORM session, identity map, Python object and database
    reload, proven by `reloaded is not insight` below. Narrower claim,
    stated precisely: `Session(admin_engine)` may reuse a pooled DBAPI
    connection the writer already released, so this does NOT prove a
    distinct PHYSICAL connection — only a distinct session is
    established here, which is what the property under test needs
    (a fresh SELECT, through the ORM's own identity map having no prior
    knowledge of this row, not the same in-memory object the write
    produced). `authoritative_acknowledgement` is then called on that
    freshly-reloaded row, with NO reattachment and NO repair (the same
    discipline the SQLite tests are held to): on real PostgreSQL,
    `timestamptz` genuinely preserves the instant, so this is expected to
    SUCCEED where the SQLite equivalent must raise — proving the property
    SQLite structurally cannot.
    """
    at = datetime(2026, 9, 14, 10, 30, 0, tzinfo=UTC)

    insight = _build_acknowledged_insight(
        admin_session,
        tenant_id=tenant_a.id,
        insight_key="insight:pg-roundtrip",
        operation_key="message:pg-roundtrip",
        at=at,
        actor_ref="agent:pg-writer",
        evidence_locator_ref="ticket:pg-roundtrip",
        evidence_digest="a" * 64,
        actor_attribution_ref="user:pg-writer",
    )
    admin_session.commit()

    with Session(admin_engine) as reload_session:
        reloaded = reload_session.get(AIInsight, insight.id)
        assert reloaded is not None

        # Sensitivity: this must be a GENUINE round trip, not the same
        # in-memory object — a naive `datetime` here would mean this
        # session accidentally shared identity with the write, and
        # everything below would prove nothing about persistence.
        assert reloaded is not insight

        resolved = authoritative_acknowledgement(reloaded)

        assert resolved.evidence is not None
        assert resolved.evidence.locator_ref == "ticket:pg-roundtrip"
        assert resolved.evidence.content_digest == "a" * 64
        assert resolved.evidence.media_type == "text/plain"

        assert resolved.attribution is not None
        assert resolved.attribution.actor_ref == "user:pg-writer"
        assert resolved.attribution.actor_namespace == "platform-identity"
        assert resolved.attribution.actor_type == "human"
        # THE aware instant, exactly — not merely equal by value, but
        # genuinely timezone-aware: `_require_aware` inside
        # `AIAcknowledgementAttribution` would have already raised on
        # construction if PostgreSQL had returned a naive value the way
        # SQLite does, so `resolved.attribution` existing at all is part
        # of the proof, and the exact-equality check below proves the
        # INSTANT, not just its awareness.
        assert resolved.attribution.acknowledged_at.tzinfo is not None
        assert resolved.attribution.acknowledged_at == at


def test_attribution_survives_an_n1_legacy_overwrite_through_postgres(
    _ai_operations_schema: None,
    admin_engine,
    admin_session: Session,
    tenant_b,
) -> None:
    """Item 4 — the central N-1 guarantee of this slice, proved to
    completion (not merely up to the point SQLite's declared boundary
    allows).

    The CURRENT writer acknowledges with typed evidence AND typed
    attribution. An OLDER, N-1 writer's own write then lands on the SAME
    row afterward — raw SQL, unconditional (no `WHERE status` guard, the
    same shape `tests/unit/test_ai_operations.py`'s SQLite Plant 1 uses),
    touching only the legacy columns a pre-`ao_0003` `acknowledge_insight`
    would have known about. `:id` is bound through `sa.bindparam("id",
    type_=sa.Uuid())` for the identical reason established against
    SQLite (matching the exact typed construct the ORM insert used,
    rather than trusting an untyped string/driver default).
    """
    at = datetime(2026, 9, 14, 9, 0, 0, tzinfo=UTC)
    older_at = datetime(2026, 9, 13, 8, 0, 0, tzinfo=UTC)

    insight = _build_acknowledged_insight(
        admin_session,
        tenant_id=tenant_b.id,
        insight_key="insight:pg-n1",
        operation_key="message:pg-n1",
        at=at,
        actor_ref="agent:current-writer",
        evidence_locator_ref="ticket:pg-n1-current",
        evidence_digest="c" * 64,
        actor_attribution_ref="user:current-writer",
    )
    admin_session.commit()

    # `mod_aiops` is a fixed literal, not interpolated user input — written
    # directly rather than via an f-string so this reads as the constant
    # it is, not a query built from variable parts.
    admin_session.execute(
        sa.text(
            "UPDATE mod_aiops.ai_insights SET acknowledged_by_ref = :actor, "
            "acknowledged_at = :at, action_evidence_ref = :locator "
            "WHERE id = :id"
        ).bindparams(
            sa.bindparam("id", type_=sa.Uuid()),
            sa.bindparam("at", type_=sa.DateTime(timezone=True)),
        ),
        {
            "actor": "agent:old-writer",
            "at": older_at,
            "locator": "old-writer-locator",
            "id": insight.id,
        },
    )
    admin_session.commit()

    with Session(admin_engine) as reload_session:
        reloaded = reload_session.get(AIInsight, insight.id)
        assert reloaded is not None
        assert reloaded is not insight

        # Sensitivity check: the N-1 write GENUINELY landed. Without this,
        # every assertion below would prove nothing about surviving an
        # overwrite that never actually happened.
        assert reloaded.acknowledged_by_ref == "agent:old-writer"
        assert reloaded.acknowledged_at == older_at

        resolved = authoritative_acknowledgement(reloaded)

        assert resolved.evidence is not None
        assert resolved.evidence.locator_ref == "ticket:pg-n1-current"
        assert resolved.evidence.content_digest == "c" * 64
        assert resolved.evidence.media_type == "text/plain"

        assert resolved.attribution is not None
        assert resolved.attribution.actor_ref == "user:current-writer"
        assert resolved.attribution.actor_namespace == "platform-identity"
        assert resolved.attribution.actor_type == "human"
        # The TYPED acknowledgement time, not just actor identity — this
        # is the field the N-1 write's own `acknowledged_at = :at`
        # overwrite (above) targets on the LEGACY column; the typed
        # `attribution_acknowledged_at` still naming the CURRENT writer's
        # `at`, not the older writer's `older_at`, is what proves time
        # survived the overwrite, not only identity.
        assert resolved.attribution.acknowledged_at.tzinfo is not None
        assert resolved.attribution.acknowledged_at == at


# ---------------------------------------------------------------------------
# Genuine current-writer interleaving, forced deterministically.
#
# `tests/unit/test_ai_operations.py`'s
# `test_acknowledge_insight_refuses_a_row_already_acknowledged` and
# `test_acknowledge_insight_pairs_evidence_and_attribution_from_one_call`
# already say, in their own docstrings, that they are SEQUENTIAL: their
# second `acknowledge_insight` call is refused at the preliminary SELECT
# (the row is already `"acknowledged"` by the time it runs), never at the
# conditional UPDATE's `rowcount` check. Both would stay green if the
# UPDATE's `.where(AIInsight.status == "advisory")` clause, or the
# `rowcount != 1` refusal that depends on it, were deleted outright — a
# gap those docstrings name honestly rather than claim past.
#
# This is that missing proof, against real PostgreSQL, forced rather than
# hoped for.
# ---------------------------------------------------------------------------

# Ordering invariant, ENFORCED below rather than merely stated in a
# comment: the poll deadline must be comfortably shorter than
# `lock_timeout`, which must be shorter than `statement_timeout`, which
# must be shorter than the future timeout. If the poll deadline were ever
# allowed to reach or exceed `lock_timeout`, a loaded CI runner could let
# the loser's own `lock_timeout` fire before `assert blocked` observes
# `wait_event_type == 'Lock'` — turning a clean, diagnosable assertion
# failure into an unrelated `OperationalError` surfacing out of the
# worker thread instead. Every later constant is DERIVED from the one
# before it (never hand-tuned independently) so this relationship cannot
# silently drift when someone changes one number without the others.
#
# `_EVENT_TIMEOUT` bounds the pure-Python `threading.Event`/`queue.Queue`
# waits (never a SQL statement), so it is NOT part of the SQL-side
# `lock_timeout`/`statement_timeout` chain above — its own two bounds are
# asserted separately, for the two reasons that are actually load-bearing
# for it specifically: it must EXCEED `_LOCK_POLL_DEADLINE`, because the
# winner's `may_commit.wait` has to outlast the worst-case poll (the test
# body only calls `may_commit.set()` after the poll loop returns) or the
# winner could give up waiting before the test ever releases it; and it
# must stay BELOW `_FUTURE_TIMEOUT`, so a genuinely stuck worker times out
# and returns its own diagnostic outcome dict before `Future.result(
# timeout=_FUTURE_TIMEOUT)` raises a less informative `TimeoutError` over
# the top of it. Where it sits relative to `_LOCK_TIMEOUT_SECONDS`/
# `_STATEMENT_TIMEOUT_SECONDS` is NOT load-bearing (it bounds no SQL
# statement), so that relationship is deliberately left unconstrained.
_LOCK_POLL_DEADLINE = 10.0
_LOCK_TIMEOUT_SECONDS = _LOCK_POLL_DEADLINE * 3  # 30.0 — comfortably above the poll
_STATEMENT_TIMEOUT_SECONDS = _LOCK_TIMEOUT_SECONDS * 2  # 60.0
_EVENT_TIMEOUT = _STATEMENT_TIMEOUT_SECONDS  # thread-signal waits, own bounds below
_FUTURE_TIMEOUT = _STATEMENT_TIMEOUT_SECONDS * 2  # 120.0

assert (
    0 < _LOCK_POLL_DEADLINE < _LOCK_TIMEOUT_SECONDS < _STATEMENT_TIMEOUT_SECONDS
    < _FUTURE_TIMEOUT
), (
    "timeout ordering invariant violated: poll deadline < lock_timeout < "
    "statement_timeout < future timeout must hold, or a loaded CI runner "
    "can turn a clean 'assert blocked' failure into an unrelated "
    "OperationalError — see the comment above these constants"
)
assert _LOCK_POLL_DEADLINE < _EVENT_TIMEOUT < _FUTURE_TIMEOUT, (
    "_EVENT_TIMEOUT must exceed _LOCK_POLL_DEADLINE (the winner's "
    "may_commit wait must outlast the worst-case poll before the test "
    "releases it) and stay below _FUTURE_TIMEOUT (a stuck worker must "
    "time out and return its own diagnostic outcome before "
    "Future.result() raises a less informative TimeoutError over it) — "
    "see the comment above these constants"
)

_LOCK_TIMEOUT = f"{int(_LOCK_TIMEOUT_SECONDS)}s"
_STATEMENT_TIMEOUT = f"{int(_STATEMENT_TIMEOUT_SECONDS)}s"
_LOCK_POLL_INTERVAL = 0.05


def _bound_waits(session: Session) -> None:
    """No statement in a racing session may wait forever, including the
    one that is SUPPOSED to block on the other session's row lock — same
    discipline `tests/test_external_identity_login_race.py` uses, and for
    the identical reason its own docstring records: an unbounded canary in
    this repo once cost twelve CI hours.
    """
    session.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    session.execute(sa.text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'"))


def _advisory_insight_for_race(
    db: Session, *, tenant_id, insight_key: str, operation_key: str, at: datetime
) -> AIInsight:
    """The setup half only — everything `acknowledge_insight` requires
    BEFORE the row is still genuinely `"advisory"`, through the real
    production functions. Deliberately stops short of calling
    `acknowledge_insight` itself, unlike `_build_acknowledged_insight`
    above — the race below is what calls it, twice, from two independent
    sessions.
    """
    policy = create_policy(
        db,
        tenant_id=tenant_id,
        code=f"conversation.intake.{insight_key}",
        title="Conversation intake",
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant_id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant_id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant_id,
        operation_key=operation_key,
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref=operation_key,
        input_digest="a" * 64,
        started_at=at,
    )
    record_attempt(
        db,
        tenant_id=tenant_id,
        operation_id=operation.id,
        attempt_key=f"attempt:{insight_key}",
        outcome="succeeded",
        output_ref=f"output:{insight_key}",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    return create_insight(
        db,
        tenant_id=tenant_id,
        operation_id=operation.id,
        command=InsightInput(
            insight_key, "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )


def _race_winner(
    factory: sessionmaker[Session],
    *,
    tenant_id,
    insight_id,
    at: datetime,
    row_still_advisory: threading.Event,
    may_commit: threading.Event,
) -> dict[str, object]:
    """Calls `acknowledge_insight` and RETURNS — the transaction it ran
    inside stays open, uncommitted, so the row lock its own `UPDATE` took
    is still held. Only commits once told to, via `may_commit`, which the
    test sets ONLY after it has independently confirmed (through
    `pg_stat_activity`, never a sleep) that the loser is genuinely blocked
    on that lock.
    """
    session = factory()
    try:
        _bound_waits(session)
        acknowledge_insight(
            session,
            tenant_id=tenant_id,
            insight_id=insight_id,
            actor_ref="agent:race-winner",
            action_evidence=AIEvidenceBinding(
                locator_namespace="ticketing",
                locator_ref="ticket:race-winner",
                content_digest="c" * 64,
                media_type="text/plain",
            ),
            actor_attribution=AIAcknowledgementAttribution(
                actor_namespace="platform-identity",
                actor_type="human",
                actor_ref="user:race-winner",
                acknowledged_at=at,
            ),
            acknowledged_at=at,
        )
        # The UPDATE above has executed and its row lock is held, still
        # uncommitted — this is the signal the loser (and the test body)
        # wait for before doing anything that depends on that ordering.
        row_still_advisory.set()
        if not may_commit.wait(timeout=_EVENT_TIMEOUT):
            session.rollback()
            return {"outcome": "timed_out_waiting_to_commit"}
        session.commit()
        return {"outcome": "committed"}
    except Exception as exc:  # returned, not raised — see module docstring
        # convention this file and the external-identity race file share:
        # a worker's failure should read as "these two things both
        # happened", not an opaque exception surfaced through a future.
        session.rollback()
        return {"outcome": "error", "error": repr(exc)}
    finally:
        session.close()


def _race_loser(
    factory: sessionmaker[Session],
    *,
    tenant_id,
    insight_id,
    at: datetime,
    row_still_advisory: threading.Event,
    backend_pid_queue: queue.Queue,
) -> dict[str, object]:
    """Waits for the winner's UPDATE to have run (but not committed),
    then calls `acknowledge_insight` itself. Its own preliminary SELECT
    reads the last COMMITTED row version under READ COMMITTED — still
    `"advisory"`, since the winner has not committed — so it passes the
    precondition and proceeds to its own conditional UPDATE, which then
    blocks on the winner's row lock. Publishes its backend pid BEFORE
    making that blocking call, so the test body can independently confirm
    the block via `pg_stat_activity` rather than assuming it.
    """
    if not row_still_advisory.wait(timeout=_EVENT_TIMEOUT):
        return {"outcome": "setup_failed", "reason": "winner never signalled"}
    session = factory()
    try:
        _bound_waits(session)
        backend_pid = session.execute(sa.text("SELECT pg_backend_pid()")).scalar()
        backend_pid_queue.put(backend_pid)
        try:
            acknowledge_insight(
                session,
                tenant_id=tenant_id,
                insight_id=insight_id,
                actor_ref="agent:race-loser",
                action_evidence=AIEvidenceBinding(
                    locator_namespace="ticketing",
                    locator_ref="ticket:race-loser",
                    content_digest="d" * 64,
                    media_type="text/plain",
                ),
                actor_attribution=AIAcknowledgementAttribution(
                    actor_namespace="platform-identity",
                    actor_type="human",
                    actor_ref="user:race-loser",
                    acknowledged_at=at,
                ),
                acknowledged_at=at,
            )
        except AIOperationRefused as exc:
            session.rollback()
            return {"outcome": "refused", "message": str(exc)}
        # Reached only if the UPDATE unexpectedly matched — the defect
        # this whole test exists to catch.
        session.rollback()
        return {"outcome": "unexpectedly_acknowledged"}
    finally:
        session.close()


def test_two_overlapping_writers_interleave_under_read_committed(
    _ai_operations_schema: None,
    admin_engine,
    admin_session: Session,
    tenant_a,
) -> None:
    """Forces the exact interleaving
    `test_acknowledge_insight_pairs_evidence_and_attribution_from_one_call`
    (SQLite, sequential) cannot reach: BOTH sessions' preliminary SELECT
    observes the row as `"advisory"` before EITHER session's UPDATE is
    visible to the other.

    ## The forcing device

    1. The winner session calls `acknowledge_insight` and returns without
       committing — its own `UPDATE`'s row lock is held, uncommitted.
    2. The loser session's `acknowledge_insight` only starts after the
       winner signals its UPDATE has run. Its SELECT, under READ
       COMMITTED, sees the last COMMITTED row version — still
       `"advisory"`, since the winner has not committed — so it passes the
       precondition. Its own UPDATE then blocks on the winner's row lock.
    3. The test independently confirms the loser is genuinely blocked by
       polling `pg_stat_activity` for its backend pid showing
       `wait_event_type = 'Lock'`, under a bounded deadline — never a
       `time.sleep()` that merely usually works, which would be a canary
       green for the wrong reason (the exact defect class this repair
       exists to remove).
    4. Only then does the test release the winner to commit.
    5. The loser's blocked UPDATE unblocks. Under READ COMMITTED, a
       waiting `UPDATE` re-reads the just-committed row version once its
       lock is granted and re-evaluates `WHERE status = 'advisory'`
       against that post-commit state — the row is now `"acknowledged"`,
       so it matches nothing, `rowcount == 0`, and `acknowledge_insight`
       raises `AIOperationRefused`.

    Both sessions therefore provably observed `"advisory"` before either
    commit was visible to the other — the property the two SQLite tests
    say they do not reach, and now do not need to claim.

    ## What is asserted, and why each assertion is load-bearing

    - Exactly one commit (`winner_result["outcome"] == "committed"`) and
      exactly one refusal (`loser_result["outcome"] == "refused"`) — not
      two commits (which would mean the conditional UPDATE let both
      writers through) and not two refusals (which would mean the winner
      itself failed for an unrelated reason, proving nothing about the
      loser).
    - The final row, read through a THIRD, genuinely independent session
      (`Session(admin_engine)`, never either racing session's own
      objects), carries the winner's evidence (`ticket:race-winner`) and
      attribution (`user:race-winner`) ENTIRELY — winner and loser were
      given deliberately distinguishable evidence/actor values precisely
      so a mix (e.g. winner's evidence paired with the loser's
      attribution) would be visible here rather than passing by
      coincidence.
    - The same row's LEGACY `acknowledged_by_ref`/`acknowledged_at`
      columns are also checked against the winner. These two are
      DEFENCE-IN-DEPTH, not the mechanism this test turns on, and are
      stated that way deliberately rather than left to be misread as
      load-bearing: under CURRENT semantics the loser's
      `acknowledge_insight` call raises before touching the database
      again and its session is rolled back, so nothing else could have
      written these columns and the assertions are redundant with the
      typed-attribution checks above. They earn their place anyway
      because `acknowledged_by_ref`/`acknowledged_at` are the TWO columns
      an N-1 writer (see this module's
      `test_attribution_survives_an_n1_legacy_overwrite_through_postgres`)
      and the current writer BOTH write — unlike the typed evidence/
      attribution columns, which are safe precisely because an N-1 writer
      never touches them at all. Asserting only the typed columns would
      prove the winner's row only on the half of the schema that was
      never actually in contention between two CURRENT-writer callers;
      these two lines are what closes that half for this test's own
      current-writer race, not a new mechanism.

    ## Sensitivity — a DESIGN claim, not a measured one

    This test has not been executed; hosted CI is the acceptance owner
    (see the module docstring). Stated as a design claim rather than a
    verified one:

    - Deleting the UPDATE's `.where(AIInsight.status == "advisory")`
      clause would make the loser's UPDATE match the row unconditionally
      once unblocked, regardless of the winner's commit — `rowcount == 1`
      for BOTH sessions, so `loser_result["outcome"]` would become
      `"unexpectedly_acknowledged"` instead of `"refused"`, and the final
      row would carry the LOSER's evidence/attribution (whichever
      session's UPDATE physically landed last), not the winner's — both
      the `"refused"` assertion and the winner-only evidence/attribution
      assertions would fail.
    - Deleting the `rowcount != 1` refusal (but keeping the `WHERE`
      clause) would make the loser's UPDATE affect zero rows silently —
      no exception at all, `acknowledge_insight` would return normally,
      and `db.refresh(insight)` inside it would simply reload the
      unchanged (winner's) row. `loser_result["outcome"]` would then be
      `"unexpectedly_acknowledged"` (the call did not raise), which fails
      the `== "refused"` assertion just as directly.

    If either removal were made and this test still passed, the design
    claim above would be false for this code shape — that has not been
    tested, only reasoned through against the actual `service.py` source
    read for this repair.
    """
    at = datetime(2026, 9, 14, 11, 0, 0, tzinfo=UTC)

    insight = _advisory_insight_for_race(
        admin_session,
        tenant_id=tenant_a.id,
        insight_key="insight:pg-race",
        operation_key="message:pg-race",
        at=at,
    )
    admin_session.commit()

    factory = sessionmaker(bind=admin_engine, autocommit=False, autoflush=False)
    row_still_advisory = threading.Event()
    may_commit = threading.Event()
    backend_pid_queue: queue.Queue = queue.Queue()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        winner_future = pool.submit(
            _race_winner,
            factory,
            tenant_id=tenant_a.id,
            insight_id=insight.id,
            at=at,
            row_still_advisory=row_still_advisory,
            may_commit=may_commit,
        )
        loser_future = pool.submit(
            _race_loser,
            factory,
            tenant_id=tenant_a.id,
            insight_id=insight.id,
            at=at,
            row_still_advisory=row_still_advisory,
            backend_pid_queue=backend_pid_queue,
        )

        try:
            loser_pid = backend_pid_queue.get(timeout=_EVENT_TIMEOUT)
        except queue.Empty:
            pytest.fail(
                "the loser never published its backend pid — it did not "
                "reach the point of calling acknowledge_insight, so the "
                "race was never set up"
            )

        # Poll pg_stat_activity, on its OWN short-lived autocommit
        # connection (never either racing session), until the loser is
        # GENUINELY waiting on a row lock — not assumed, not slept for.
        blocked = False
        deadline = time.monotonic() + _LOCK_POLL_DEADLINE
        with admin_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as poll_conn:
            while time.monotonic() < deadline:
                wait_event_type = poll_conn.execute(
                    sa.text(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                    ),
                    {"pid": loser_pid},
                ).scalar()
                if wait_event_type == "Lock":
                    blocked = True
                    break
                time.sleep(_LOCK_POLL_INTERVAL)

        assert blocked, (
            "the loser's UPDATE was never observed waiting on the winner's "
            "row lock within the poll deadline — the interleaving this "
            "test exists to force did not happen, so nothing below would "
            "prove anything about overlapping writers"
        )

        # Only now — proven, not assumed, blocked — let the winner commit.
        may_commit.set()

        winner_result = winner_future.result(timeout=_FUTURE_TIMEOUT)
        loser_result = loser_future.result(timeout=_FUTURE_TIMEOUT)

    assert winner_result["outcome"] == "committed", winner_result
    assert loser_result["outcome"] == "refused", loser_result

    with Session(admin_engine) as reload_session:
        reloaded = reload_session.get(AIInsight, insight.id)
        assert reloaded is not None
        assert reloaded.status == "acknowledged"

        resolved = authoritative_acknowledgement(reloaded)
        assert resolved.evidence is not None
        assert resolved.evidence.locator_ref == "ticket:race-winner"
        assert resolved.evidence.content_digest == "c" * 64
        assert resolved.attribution is not None
        assert resolved.attribution.actor_ref == "user:race-winner"
        assert resolved.attribution.actor_namespace == "platform-identity"
        assert resolved.attribution.acknowledged_at.tzinfo is not None
        assert resolved.attribution.acknowledged_at == at

        # Defence-in-depth over the LEGACY columns — see the docstring's
        # "What is asserted" section for why these are not load-bearing
        # under current semantics (the loser rolls back and never writes
        # anything else) but are still asserted: these are the two
        # columns BOTH an N-1 writer and the current writer touch, unlike
        # the typed columns above, which are safe only because an N-1
        # writer never reaches them at all.
        assert reloaded.acknowledged_by_ref == "agent:race-winner"
        assert reloaded.acknowledged_at == at
