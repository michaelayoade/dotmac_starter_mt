"""Provider-neutral AI policy, attempt and advisory-insight lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from uuid import uuid4

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
from dotmac_ai_operations.models import TENANT_MODELS, AIExecutionAttempt, AIInsight
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _sqlite_aiops_migration_engine():
    """A narrow SQLite harness with the migration's schema name attached."""
    engine = create_engine(
        "sqlite://",
        poolclass=sa.pool.StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS mod_aiops")
    return engine


def _legacy_ai_insights_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "ai_insights",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("insight_key", sa.String(200), nullable=False),
        sa.Column("insight_type", sa.String(120), nullable=False),
        sa.Column("advisory_value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("source_output_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("acknowledged_by_ref", sa.String(200)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("action_evidence_ref", sa.String(240)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="mod_aiops",
    )


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_aiops": None}},
    )
    Base.metadata.create_all(
        engine, tables=[Tenant.__table__, *(m.__table__ for m in TENANT_MODELS)]
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tenant(db: Session):
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(row)
    db.flush()
    return row


def test_policy_version_and_operation_emit_provider_neutral_intent(db: Session) -> None:
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("transcription", "classification"),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, intent = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="call:42",
        policy_version_id=version.id,
        operation_kind="transcription",
        input_ref="file:opaque",
        input_digest="a" * 64,
        started_at=at,
    )
    assert intent.capability == "ai.transcription.execute"
    assert intent.operation_id == operation.id and not hasattr(intent, "provider")
    with pytest.raises(AIOperationRefused, match="allowed"):
        start_operation(
            db,
            tenant_id=tenant.id,
            operation_key="call:43",
            policy_version_id=version.id,
            operation_kind="summarization",
            input_ref="file:2",
            input_digest="b" * 64,
            started_at=at,
        )


def test_attempt_observations_are_immutable_and_insights_remain_advisory(
    db: Session,
) -> None:
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:7",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:7",
        input_digest="a" * 64,
        started_at=at,
    )
    attempt = record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:1",
        outcome="succeeded",
        output_ref="output:7",
        output_digest="b" * 64,
        provider_observation="provider-observed",
        model_observation="model-observed",
        request_observation="request-observed",
        error_code=None,
        observed_at=at,
    )
    assert operation.status == "succeeded"
    assert (
        record_attempt(
            db,
            tenant_id=tenant.id,
            operation_id=operation.id,
            attempt_key="attempt:1",
            outcome="succeeded",
            output_ref="output:7",
            output_digest="b" * 64,
            provider_observation="provider-observed",
            model_observation="model-observed",
            request_observation="request-observed",
            error_code=None,
            observed_at=at,
        ).id
        == attempt.id
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            "insight:7", "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )
    assert insight.status == "advisory"
    acknowledge_insight(
        db,
        tenant_id=tenant.id,
        insight_id=insight.id,
        actor_ref="agent:2",
        action_evidence=AIEvidenceBinding(
            locator_namespace="ticketing",
            locator_ref="ticket:42",
            content_digest="c" * 64,
            media_type="text/plain",
        ),
        actor_attribution=None,
        acknowledged_at=at,
    )
    assert insight.status == "acknowledged"
    # Private-by-convention attributes (round 15 correction): read here
    # directly, deliberately, the same way `_legacy_action_evidence_ref`
    # is read directly elsewhere in this file — this is the exact
    # documented, unsupported bypass, used here on purpose to verify the
    # persisted value independently of `authoritative_acknowledgement`.
    assert insight._action_evidence_locator_namespace == "ticketing"
    assert insight._action_evidence_locator_ref == "ticket:42"
    assert insight._action_evidence_content_digest == "c" * 64
    assert insight._action_evidence_media_type == "text/plain"


def test_ao_0002_expands_ai_insights_and_legacy_typed_evidence_coexist() -> None:
    """Runs the REAL ``ao_0002_insight_evidence_binding.upgrade()`` against
    a hand-built ``ai_insights`` table carrying `ao_0001`'s COLUMN NAMES
    AND TYPES only — deliberately NOT the current ORM metadata, which
    already declares the expanded columns and would prove nothing about
    the migration itself. This fixture is narrower than the real
    `ao_0001`-published table: an attached SQLite database preserves the
    ``mod_aiops`` spelling but cannot prove PostgreSQL schema semantics; the
    fixture omits foreign key constraints, unique constraints and row-level
    security the real migration also creates, and its ``operation_id`` values
    are orphaned (no matching `ai_operations` row) in a way the real schema's
    foreign key would reject. What it proves is exactly the additive-column and
    mixed-row-precedence behaviour below — not full schema fidelity to
    `ao_0001`.

    It exercises three rows against that migrated (narrower) schema:

    1. a legacy-only row, written via a RAW INSERT naming
       ``action_evidence_ref`` directly (exactly how a pre-``ao_0002``
       application wrote it — no current model or service function is
       involved in this write);
    2. a typed-only row, written through the current
       ``acknowledge_insight``; and
    3. a MIXED row carrying BOTH representations at once — a raw legacy
       write followed by ``acknowledge_insight`` writing a typed binding
       onto the SAME row, simulating the race an old and a new writer can
       produce on one row — used to prove precedence, not merely
       coexistence across separate rows (a test that only checks two
       separate rows would still pass even if precedence were inverted or
       simply undefined for the case where both representations occupy
       ONE row, which is exactly the case precedence exists for).

    All three are reloaded through the CURRENT ORM model (``AIInsight``,
    which maps the unchanged ``action_evidence_ref`` column to the
    private ``_legacy_action_evidence_ref`` attribute) via a genuinely
    FRESH session with no shared identity map, so the assertions can only
    pass if the values actually round-tripped through storage.

    This fails if ``ao_0002`` ever renamed or dropped
    ``action_evidence_ref`` (the raw INSERT names that column directly,
    independent of the current ORM model, and the ORM reload maps the
    same physical column — a rename desyncs the two and breaks the
    insert or the reload), fails if either write path's columns leak
    into the other's, and fails if ``authoritative_acknowledgement``'s
    evidence precedence is ever inverted on the mixed row specifically.
    """
    from dotmac_ai_operations.migrations.versions import (
        ao_0002_insight_evidence_binding as ao_0002,
    )

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    engine = _sqlite_aiops_migration_engine()

    # Every OTHER tenant table is unaffected by this migration; build them
    # from the current ORM metadata exactly like the `db` fixture does.
    other_tables = [m.__table__ for m in TENANT_MODELS if m is not AIInsight]
    Base.metadata.create_all(engine, tables=[Tenant.__table__, *other_tables])

    # `ai_insights`, deliberately NOT from current ORM metadata: `ao_0001`'s
    # column names and types, built independently in the attached schema
    # (without FKs, unique constraints or RLS — see the docstring) so the migration
    # under test is the only thing that can add the new columns.
    legacy_ai_insights = _legacy_ai_insights_table()
    legacy_ai_insights.metadata.create_all(engine)

    tenant_id = uuid4()
    operation_id = uuid4()
    legacy_insight_id = uuid4()
    typed_insight_id = uuid4()
    mixed_insight_id = uuid4()
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)

    with engine.begin() as conn:
        conn.execute(
            sa.insert(Tenant.__table__).values(
                id=tenant_id, slug=f"tenant-{tenant_id.hex[:8]}", name="Tenant"
            )
        )

    # Run the REAL migration function against the real table, through
    # Alembic's ambient `op` proxy — not a reimplementation of what it does.
    with engine.connect() as conn:
        migration_context = MigrationContext.configure(conn)
        with Operations.context(migration_context):
            ao_0002.upgrade()
        conn.commit()

    # A raw INSERT naming `action_evidence_ref` directly: what a
    # pre-`ao_0002` application actually wrote, independent of any current
    # code in this package.
    with engine.begin() as conn:
        conn.execute(
            sa.insert(legacy_ai_insights).values(
                id=legacy_insight_id,
                tenant_id=tenant_id,
                operation_id=operation_id,
                insight_key="insight:legacy",
                insight_type="routing_suggestion",
                advisory_value="subscriber-support",
                confidence=0.91,
                source_output_digest="b" * 64,
                status="acknowledged",
                acknowledged_by_ref="agent:legacy",
                acknowledged_at=at,
                action_evidence_ref="legacy-ticket:1",
            )
        )
        # The MIXED row: a legacy locator written raw (status stays
        # "advisory" so `acknowledge_insight` below can still find and
        # act on it — that is exactly what lets an old writer's direct
        # column write and a new writer's `acknowledge_insight` call land
        # on the SAME row).
        conn.execute(
            sa.insert(legacy_ai_insights).values(
                id=mixed_insight_id,
                tenant_id=tenant_id,
                operation_id=operation_id,
                insight_key="insight:mixed",
                insight_type="routing_suggestion",
                advisory_value="subscriber-support",
                confidence=0.91,
                source_output_digest="b" * 64,
                status="advisory",
                action_evidence_ref="mixed-legacy-ticket",
            )
        )

    # The typed row goes through the current service function, in its own
    # session bound to the same (now-migrated) engine.
    with Session(engine) as write_session:
        write_session.add(
            AIInsight(
                id=typed_insight_id,
                tenant_id=tenant_id,
                operation_id=operation_id,
                insight_key="insight:typed",
                insight_type="routing_suggestion",
                advisory_value="subscriber-support",
                confidence=0.91,
                source_output_digest="b" * 64,
                status="advisory",
            )
        )
        write_session.flush()
        acknowledge_insight(
            write_session,
            tenant_id=tenant_id,
            insight_id=typed_insight_id,
            actor_ref="agent:typed",
            action_evidence=AIEvidenceBinding(
                locator_namespace="ticketing",
                locator_ref="ticket:99",
                content_digest="d" * 64,
                media_type="text/plain",
            ),
            actor_attribution=None,
            acknowledged_at=at,
        )
        # The new writer's `acknowledge_insight` call lands on the SAME
        # row that already carries the legacy locator written raw above —
        # this is the mixed row.
        acknowledge_insight(
            write_session,
            tenant_id=tenant_id,
            insight_id=mixed_insight_id,
            actor_ref="agent:new-writer",
            action_evidence=AIEvidenceBinding(
                locator_namespace="ticketing",
                locator_ref="ticket:authoritative",
                content_digest="e" * 64,
                media_type="text/plain",
            ),
            actor_attribution=None,
            acknowledged_at=at,
        )
        write_session.commit()

    # Genuinely FRESH session — no shared identity map with either write
    # above — so these reads can only pass if storage actually round-tripped.
    with Session(engine) as read_session:
        reloaded_legacy = read_session.get(AIInsight, legacy_insight_id)
        reloaded_typed = read_session.get(AIInsight, typed_insight_id)
        reloaded_mixed = read_session.get(AIInsight, mixed_insight_id)
        assert reloaded_legacy is not None
        assert reloaded_typed is not None
        assert reloaded_mixed is not None

        # The legacy row: descriptive locator only, no digest, no media
        # type — it must not appear to carry a binding it never had.
        assert reloaded_legacy._legacy_action_evidence_ref == "legacy-ticket:1"
        assert reloaded_legacy._action_evidence_locator_namespace is None
        assert reloaded_legacy._action_evidence_locator_ref is None
        assert reloaded_legacy._action_evidence_content_digest is None
        assert reloaded_legacy._action_evidence_media_type is None
        assert authoritative_acknowledgement(reloaded_legacy).evidence is None

        # The typed row: full binding, and the LEGACY column untouched by
        # the new authoritative write path (acknowledge_insight never
        # writes it).
        assert reloaded_typed._legacy_action_evidence_ref is None
        assert reloaded_typed._action_evidence_locator_namespace == "ticketing"
        assert reloaded_typed._action_evidence_locator_ref == "ticket:99"
        assert reloaded_typed._action_evidence_content_digest == "d" * 64
        assert reloaded_typed._action_evidence_media_type == "text/plain"
        typed_evidence = authoritative_acknowledgement(reloaded_typed).evidence
        assert typed_evidence is not None
        assert typed_evidence.locator_ref == "ticket:99"

        # The MIXED row: BOTH representations genuinely present on ONE
        # row — this is the case precedence exists for, not a coexistence
        # check across separate rows.
        assert reloaded_mixed._legacy_action_evidence_ref == "mixed-legacy-ticket"
        assert reloaded_mixed._action_evidence_locator_namespace == "ticketing"
        assert reloaded_mixed._action_evidence_locator_ref == "ticket:authoritative"
        assert reloaded_mixed._action_evidence_content_digest == "e" * 64
        assert reloaded_mixed._action_evidence_media_type == "text/plain"
        mixed_evidence = authoritative_acknowledgement(reloaded_mixed).evidence
        assert mixed_evidence is not None
        assert mixed_evidence.locator_ref == "ticket:authoritative"


def test_ao_0003_shadows_attribution_against_current_and_legacy_writers() -> None:
    """Plants 1, 2 and 4 together, run against the REAL
    ``ao_0002_insight_evidence_binding.upgrade()`` then REAL
    ``ao_0003_ack_attribution.upgrade()``/``downgrade()``,
    exercised through Alembic's ambient ``op`` proxy — not reimplemented.
    Same narrower-fixture caveat as the ``ao_0002`` coexistence test above:
    the attachment preserves the schema spelling, not PostgreSQL schema
    semantics; this omits FKs/uniques/RLS and proves the additive-column and
    precedence behaviour, not full schema fidelity to ``ao_0001``.

    TWO SQLite-SPECIFIC REPRESENTATION MISMATCHES were found and fixed
    here (round 15/16), and both are the SAME CLASS of defect as the
    canonical-time bug already fixed in ``record_attempt``: a test
    fixture silently disagreeing with the storage layer about how a
    value is represented, producing two "truths" for one value.

    1. A UUID bound as a plain hyphenated ``str(some_uuid)`` in a raw
       ``sa.text()`` statement matches NO row on SQLite: ``sa.Uuid()``'s
       bind_processor stores a Python ``UUID`` as its 32-character
       ``.hex`` (verified by reading
       ``sqlalchemy.sql.sqltypes.Uuid.bind_processor``), not the
       36-character hyphenated form. Every raw-SQL statement below that
       targets a row by id binds ``:id`` through
       ``sa.bindparam("id", type_=sa.Uuid())`` and passes the actual
       ``UUID`` object, not a stringified one — the SAME typed construct
       the ORM insert used, so the stored representation matches.
    2. SQLite's ``DATETIME`` type discards ``tzinfo`` on every round
       trip (verified by reading
       ``sqlalchemy.dialects.sqlite.base.DATETIME.bind_processor``/
       ``.result_processor``: the bind side extracts only
       year/month/day/hour/minute/second/microsecond, with no offset
       component at all, and the result side parses the stored string
       back as a NAIVE ``datetime``). This is a genuine PostgreSQL/SQLite
       BEHAVIOUR DIFFERENCE, not by itself a defect in this package:
       ``timestamptz`` on real PostgreSQL always returns an AWARE value.
       Comparisons against a cross-session/cross-connection reload use
       the NAIVE form of the expected value (``.replace(tzinfo=None)``)
       where a bare value comparison suffices.

       ROUND 18 CORRECTION, recorded because the earlier fix here was
       itself wrong in a way worth naming: an EARLIER version of this
       test reattached ``UTC`` to a reloaded naive value BEFORE passing
       it into ``authoritative_acknowledgement`` — which meant the
       accessor never saw what SQLite actually returned; it saw a value
       the test had already fixed. Michael's ruling: a test that repairs
       its input before calling the thing under test cannot fail for the
       reason it exists. This file no longer does that anywhere.

       ROUND 19 RULING (settled, not an open question): Michael decided
       ``authoritative_acknowledgement`` STAYS STRICT and refuses a naive
       timestamp — accessor-level UTC reattachment was considered and
       explicitly REJECTED, because it would manufacture an offset the
       accessor cannot prove, and an acknowledgement integrity envelope
       binding actor, evidence and time is not the place for an inferred
       timestamp. Calling ``authoritative_acknowledgement`` on a
       genuinely unrepaired SQLite-reloaded object with fully-populated
       typed attribution therefore CORRECTLY raises, via
       ``AIAcknowledgementAttribution``'s ``_require_aware`` check, which
       is not weakened anywhere in this package to accommodate SQLite.
       The ``pytest.raises`` assertions in the individual plants below
       (see PROPERTY 2 in each) are the PROOF that this declared boundary
       holds, not a report of an unresolved gap — SQLite is a fast logic
       harness for everything else this suite proves, never a valid
       persistence round-trip for authoritative attribution.
       ``tests/test_ai_operations_attribution_postgres.py`` proves the
       same call's happy path against real PostgreSQL, whose
       ``timestamptz`` genuinely preserves the instant.

    **PLANT 1 — the one that proves the design.** A row is acknowledged
    through the CURRENT ``acknowledge_insight`` (typed evidence AND typed
    attribution together). An OLDER, N-1 writer's own write then lands on
    the SAME row afterward: raw SQL, unconditional (no ``WHERE status``
    guard — that guard postdates this simulated writer), touching only
    the columns a pre-``ao_0003`` ``acknowledge_insight`` would have known
    about (``acknowledged_by_ref``, ``acknowledged_at``,
    ``action_evidence_ref``). The reload afterward asserts the N-1 write
    GENUINELY landed (``acknowledged_by_ref`` is now the OLD writer's
    value — without this assertion, a no-op N-1 write would make every
    other assertion below prove nothing) AND that the typed evidence AND
    typed attribution's VALUES are BOTH still exactly the CURRENT writer's
    — checked field-by-field via DIRECT attribute reads, deliberately not
    through ``authoritative_acknowledgement`` (see PROPERTY 2 below for
    why). That must hold not because of a lock or a constraint (there is
    no lock and, deliberately, no CHECK constraint here) but because the
    N-1 writer's raw ``UPDATE`` literally never names the typed columns —
    the same reason ``ao_0002``'s evidence already survived this exact
    shape of overwrite.

    **Michael's rulings, applied here and in Plant 5 below.** Round 18: a
    test must not repair a reloaded value before invoking the production
    code under test — that would let the check answer without being able
    to refuse. So three properties are proven SEPARATELY, against values
    with NO test repair applied anywhere: (1) SQLite genuinely stored the
    once-canonicalised UTC instant, in its own supported naive
    representation, asserted directly; (2) whether PRODUCTION code
    (``authoritative_acknowledgement``) restores the declared aware-UTC
    value from what SQLite actually returns — proven by calling it,
    unmodified, and observing what it genuinely does; (3) no repair
    before that call. Round 19, on the finding from (2): Michael ruled
    ``authoritative_acknowledgement`` STAYS STRICT — it must NOT restore
    awareness by inferring an offset it cannot prove, because an
    acknowledgement integrity envelope binding actor, evidence and time
    is not the place for an inferred timestamp. So the ``RAISES`` outcome
    below is the settled, DECLARED boundary, not an open finding: on
    PostgreSQL, ``timestamptz`` always returns an aware value and the
    boundary holds without any change; on SQLite it structurally cannot,
    and ``authoritative_acknowledgement`` correctly refuses rather than
    guessing. ``tests/test_ai_operations_attribution_postgres.py`` proves
    the paired happy path this same call takes there.

    **PLANT 2.** A row acknowledged ENTIRELY by an N-1-style writer (raw
    SQL: status/actor/time/legacy-locator only, matching exactly what a
    pre-``ao_0003`` ``acknowledge_insight`` would have written, never
    touching the four typed attribution columns) synthesises NO typed
    attribution: ``authoritative_acknowledgement(...).attribution`` is
    ``None``, not a value reconstructed from the legacy actor/time.

    **PLANT 4.** After ``ao_0003.upgrade()``, a row's pre-existing legacy
    ``acknowledged_by_ref``/``acknowledged_at`` VALUES (written before this
    migration ran) are unchanged — not only present, checked by value
    (against the NAIVE form of the expected value, per the SQLite
    ``DATETIME`` limitation above) — and ALL FOUR new ``attribution_*``
    columns exist and are NULL (not a sample of two; ``NULL`` is
    unaffected by the tzinfo issue, since there is no datetime object to
    lose awareness from). After ``ao_0003.downgrade()``, the four new
    columns are gone from the live schema (proven by reflection, not
    merely by not querying them) AND ``acknowledged_by_ref``/
    ``acknowledged_at``'s VALUES — not just their names as surviving
    columns — are unchanged, read through a properly-typed Core
    ``Table`` for the same reason the upgrade half
    avoids raw ``sa.text()``.
    """
    from dotmac_ai_operations.migrations.versions import (
        ao_0002_insight_evidence_binding as ao_0002,
    )
    from dotmac_ai_operations.migrations.versions import (
        ao_0003_ack_attribution as ao_0003,
    )

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    engine = _sqlite_aiops_migration_engine()
    other_tables = [m.__table__ for m in TENANT_MODELS if m is not AIInsight]
    Base.metadata.create_all(engine, tables=[Tenant.__table__, *other_tables])

    legacy_ai_insights = _legacy_ai_insights_table()
    legacy_ai_insights.metadata.create_all(engine)

    tenant_id = uuid4()
    operation_id = uuid4()
    plant1_id = uuid4()
    plant2_id = uuid4()
    plant4_id = uuid4()
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    older_at = datetime(2026, 8, 20, 9, tzinfo=UTC)

    with engine.begin() as conn:
        conn.execute(
            sa.insert(Tenant.__table__).values(
                id=tenant_id, slug=f"tenant-{tenant_id.hex[:8]}", name="Tenant"
            )
        )
        # A row already legacy-acknowledged BEFORE ao_0003 runs, for Plant 4.
        conn.execute(
            sa.insert(legacy_ai_insights).values(
                id=plant4_id,
                tenant_id=tenant_id,
                operation_id=operation_id,
                insight_key="insight:plant4",
                insight_type="routing_suggestion",
                advisory_value="subscriber-support",
                confidence=0.91,
                source_output_digest="b" * 64,
                status="acknowledged",
                acknowledged_by_ref="agent:pre-migration",
                acknowledged_at=older_at,
            )
        )

    # Run the REAL migrations, through Alembic's ambient `op` proxy.
    with engine.connect() as conn:
        migration_context = MigrationContext.configure(conn)
        with Operations.context(migration_context):
            ao_0002.upgrade()
            ao_0003.upgrade()
        conn.commit()

    # PLANT 4, upgrade half: the pre-existing legacy row's actor/time
    # survived `ao_0003.upgrade()` untouched, and ALL FOUR new columns
    # exist and are NULL (not just two of them — a migration that only
    # added, say, the first two of four columns would still pass a
    # two-column check). Read through the ORM, not raw `sa.text()`: a
    # DateTime(timezone=True) column's raw driver representation on
    # SQLite is not reliably comparable to a Python `datetime` without
    # the ORM's own type decoding, and this assertion needs to compare
    # `acknowledged_at` by value, not merely check it is non-NULL.
    with Session(engine) as plant4_upgrade_session:
        plant4_row = plant4_upgrade_session.get(AIInsight, plant4_id)
        assert plant4_row is not None
        assert plant4_row.acknowledged_by_ref == "agent:pre-migration"
        # NAIVE comparison, deliberately — see the module/function
        # docstring's SQLite `DATETIME` note: this session genuinely
        # round-trips through storage, so `tzinfo` is gone on reload,
        # but the UTC wall-clock value is not.
        assert plant4_row.acknowledged_at == older_at.replace(tzinfo=None)
        assert plant4_row._attribution_actor_namespace is None
        assert plant4_row._attribution_actor_type is None
        assert plant4_row._attribution_actor_ref is None
        assert plant4_row._attribution_acknowledged_at is None

    # PLANT 1: the current writer acknowledges with BOTH typed evidence
    # and typed attribution.
    with Session(engine) as write_session:
        write_session.add(
            AIInsight(
                id=plant1_id,
                tenant_id=tenant_id,
                operation_id=operation_id,
                insight_key="insight:plant1",
                insight_type="routing_suggestion",
                advisory_value="subscriber-support",
                confidence=0.91,
                source_output_digest="b" * 64,
                status="advisory",
            )
        )
        write_session.flush()
        acknowledge_insight(
            write_session,
            tenant_id=tenant_id,
            insight_id=plant1_id,
            actor_ref="agent:current-writer",
            action_evidence=AIEvidenceBinding(
                locator_namespace="ticketing",
                locator_ref="ticket:current",
                content_digest="f" * 64,
                media_type="text/plain",
            ),
            actor_attribution=AIAcknowledgementAttribution(
                actor_namespace="platform-identity",
                actor_type="human",
                actor_ref="user:current-writer",
                acknowledged_at=at,
            ),
            acknowledged_at=at,
        )
        write_session.commit()

    # An OLDER, N-1 writer's own unconditional write lands afterward, on
    # the SAME row — raw SQL, no status guard (that guard postdates this
    # simulated writer), touching only the columns a pre-`ao_0003`
    # `acknowledge_insight` would have known about.
    #
    # BOTH `id` and `at` are bound through the SAME typed constructs
    # SQLAlchemy uses for the ORM columns (`sa.Uuid()`, matching
    # `uuid_pk()`'s column type; `sa.DateTime(timezone=True)`, matching
    # `AIInsight.acknowledged_at`'s), not passed as a plain hyphenated
    # `str(...)` or a bare `datetime` object. This is deliberate, not
    # decorative: on SQLite, `Uuid()`'s bind_processor stores a Python
    # `UUID` as its 32-character `.hex` (no hyphens) — a raw
    # `str(plant1_id)` (36 characters, hyphenated) matches NO row at
    # all, and the UPDATE would silently affect zero rows, so the
    # sensitivity assertion below (which is what proves this simulated
    # overwrite genuinely happened) would fail for a reason having
    # nothing to do with the property under test. `sa.DateTime
    # (timezone=True)` is bound explicitly for the identical reason: an
    # unbound raw `datetime` handed to `sa.text()` goes through Python's
    # own (deprecated) sqlite3 adapter instead of SQLAlchemy's own
    # bind/result processors, which is a DIFFERENT, unverified code path
    # from every other write in this test file.
    with engine.begin() as conn:
        conn.execute(
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
                "id": plant1_id,
            },
        )
        # PLANT 2: an ENTIRELY N-1-style acknowledgement — raw SQL only,
        # never touching a typed column of either kind.
        conn.execute(
            sa.insert(legacy_ai_insights).values(
                id=plant2_id,
                tenant_id=tenant_id,
                operation_id=operation_id,
                insight_key="insight:plant2",
                insight_type="routing_suggestion",
                advisory_value="subscriber-support",
                confidence=0.91,
                source_output_digest="b" * 64,
                status="acknowledged",
                acknowledged_by_ref="agent:n1-only",
                acknowledged_at=older_at,
                action_evidence_ref="n1-only-locator",
            )
        )

    with Session(engine) as read_session:
        reloaded1 = read_session.get(AIInsight, plant1_id)
        reloaded2 = read_session.get(AIInsight, plant2_id)
        assert reloaded1 is not None
        assert reloaded2 is not None

        # Sensitivity check: the N-1 write GENUINELY landed. Without this,
        # every assertion below would prove nothing about surviving an
        # overwrite that never actually happened. NAIVE comparison,
        # deliberately — see the SQLite `DATETIME` note above.
        assert reloaded1.acknowledged_by_ref == "agent:old-writer"
        assert reloaded1.acknowledged_at == older_at.replace(tzinfo=None)

        # Michael's ruling (round 18): a test that repairs its input
        # before calling the thing under test cannot fail for the reason
        # it exists. The three properties below are proven SEPARATELY,
        # against `reloaded1` COMPLETELY UNMODIFIED — no reattachment, no
        # repair, anywhere before this point or after it.

        # PROPERTY 1 — SQLite genuinely stored the once-canonicalised UTC
        # instant, in its own supported NAIVE representation. Asserted
        # directly against the raw reloaded value. `.tzinfo is None` is
        # part of the proof, not an aside: it confirms this exercises the
        # real SQLite round trip (see the module docstring's `DATETIME`
        # citation), not a value already fixed by the test.
        assert reloaded1._attribution_acknowledged_at is not None
        assert reloaded1._attribution_acknowledged_at.tzinfo is None
        assert reloaded1._attribution_acknowledged_at == at.replace(tzinfo=None)

        # Evidence AND attribution's non-time fields survived, verified
        # by DIRECT read (deliberately bypassing the paired accessor —
        # see PROPERTY 2 immediately below for why the accessor cannot be
        # exercised on this object at all without repairing it first).
        assert reloaded1._action_evidence_locator_namespace == "ticketing"
        assert reloaded1._action_evidence_locator_ref == "ticket:current"
        assert reloaded1._action_evidence_content_digest == "f" * 64
        assert reloaded1._action_evidence_media_type == "text/plain"
        assert reloaded1._attribution_actor_namespace == "platform-identity"
        assert reloaded1._attribution_actor_type == "human"
        assert reloaded1._attribution_actor_ref == "user:current-writer"

        # PROPERTY 2 — MICHAEL'S RULING (round 19), recorded as a settled
        # DECLARED BOUNDARY, not an open question: `authoritative_
        # acknowledgement` stays STRICT and refuses a naive timestamp,
        # full stop. Accessor-level UTC reattachment was considered and
        # explicitly REJECTED — it would manufacture an offset the
        # accessor cannot prove, and an acknowledgement integrity
        # envelope binding actor, evidence and time is not the place for
        # an inferred timestamp ("an envelope whose timestamp was
        # inferred is not an envelope"). This assertion is therefore not
        # a report of an unresolved gap; it is the proof that the
        # declared boundary holds exactly as decided: SQLite cannot
        # supply what `authoritative_acknowledgement` correctly demands,
        # so it correctly RAISES, every time, on SQLite, after a genuine
        # round trip — see `AIAcknowledgementAttribution`'s
        # `_require_aware`, which is NOT weakened anywhere in this
        # package to accommodate SQLite, and must not be. Establishing
        # the paired happy path this same call takes on real PostgreSQL
        # (whose `timestamptz` always returns an aware value, so the
        # boundary holds there without any special handling) is
        # `tests/test_ai_operations_attribution_postgres.py`'s job, not
        # this file's — SQLite is a fast logic harness for everything
        # else this suite proves, not a valid persistence round-trip for
        # authoritative attribution, and this assertion is where that
        # limit is recorded on purpose.
        with pytest.raises(ValueError, match="acknowledged_at"):
            authoritative_acknowledgement(reloaded1)

        resolved2 = authoritative_acknowledgement(reloaded2)
        assert resolved2.evidence is None
        assert resolved2.attribution is None

    # PLANT 4, downgrade half.
    with engine.connect() as conn:
        migration_context = MigrationContext.configure(conn)
        with Operations.context(migration_context):
            ao_0003.downgrade()
        conn.commit()

    columns = {
        c["name"]
        for c in sa.inspect(engine).get_columns("ai_insights", schema="mod_aiops")
    }
    assert "attribution_actor_namespace" not in columns
    assert "attribution_actor_type" not in columns
    assert "attribution_actor_ref" not in columns
    assert "attribution_acknowledged_at" not in columns
    assert "acknowledged_by_ref" in columns
    assert "acknowledged_at" in columns
    assert "action_evidence_locator_namespace" in columns

    # Column NAMES surviving is necessary but not sufficient — VALUES must
    # survive too, not merely exist as columns downgrade happened not to
    # touch by name collision. `legacy_ai_insights` (defined above, with
    # a properly typed `acknowledged_at` column) is queried through Core,
    # not raw `sa.text()`, so the value is actually decoded into a
    # Python `datetime` object rather than left as the driver's raw
    # string representation — but it is decoded NAIVE (SQLite's
    # `DATETIME` type discards `tzinfo` on every round trip; see the
    # module docstring), so the comparison below is against the NAIVE
    # form of the expected value, deliberately.
    with engine.connect() as conn:
        plant4_after_downgrade = conn.execute(
            sa.select(legacy_ai_insights).where(legacy_ai_insights.c.id == plant4_id)
        ).one()
    assert plant4_after_downgrade.acknowledged_by_ref == "agent:pre-migration"
    assert plant4_after_downgrade.acknowledged_at == older_at.replace(tzinfo=None)


def test_authoritative_acknowledgement_prefers_typed_evidence_over_legacy(
    db: Session,
) -> None:
    """Precedence rule (round 10, finding 1): when a single row somehow
    carries BOTH a legacy locator and a full typed binding — e.g. an old,
    rolled-back writer and a new writer both touched the same row — the
    typed binding is authoritative and the legacy locator is descriptive
    residue only, never the reverse.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:13",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:13",
        input_digest="a" * 64,
        started_at=at,
    )
    # `create_insight` requires BOTH a succeeded operation AND a matching
    # succeeded attempt (`service.py`) — `record_attempt` is what makes
    # both true; skipping it makes `create_insight` raise
    # `AIOperationRefused` before this test reaches anything it is named
    # for (round 20 finding).
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:13",
        outcome="succeeded",
        output_ref="output:13",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            "insight:13", "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )
    acknowledge_insight(
        db,
        tenant_id=tenant.id,
        insight_id=insight.id,
        actor_ref="agent:typed",
        action_evidence=AIEvidenceBinding(
            locator_namespace="ticketing",
            locator_ref="ticket:authoritative",
            content_digest="e" * 64,
            media_type="text/plain",
        ),
        actor_attribution=None,
        acknowledged_at=at,
    )
    # Simulate the race: an old writer's direct column write landing on
    # the SAME row after the typed binding was already there.
    insight._legacy_action_evidence_ref = "stale-legacy-ticket"
    db.flush()

    resolved = authoritative_acknowledgement(insight).evidence
    assert resolved is not None
    assert resolved.locator_ref == "ticket:authoritative"


@pytest.mark.parametrize(
    "missing_field",
    [
        "action_evidence_locator_namespace",
        "action_evidence_locator_ref",
        "action_evidence_content_digest",
        "action_evidence_media_type",
    ],
)
def test_authoritative_acknowledgement_returns_none_evidence_for_partial_binding(
    db: Session, missing_field: str
) -> None:
    """Round 13 finding 4: `authoritative_acknowledgement(insight).evidence`
    is `None` unless ALL FOUR typed columns are populated (`models.py` documents
    that a partial binding can exist — e.g. from a race or a direct
    column write), but until now only the all-null and fully-populated
    cases were tested. Deleting any ONE of the four `is not None`
    predicates in the completeness check would leave every prior test
    green: the other three predicates would still gate on their own
    fields, and a row missing only THIS test's `missing_field` would then
    reach `AIEvidenceBinding` construction with `None` for it and raise
    there (blank/`None` fields are refused at construction) instead of
    this function returning `None`. Parametrized over all four fields so
    dropping any single predicate is caught by the corresponding case,
    not just some of them.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key=f"message:partial:{missing_field}",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:partial",
        input_digest="a" * 64,
        started_at=at,
    )
    # `create_insight` requires BOTH a succeeded operation AND a matching
    # succeeded attempt (round 20 finding — see the identical fix note
    # elsewhere in this file).
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key=f"attempt:partial:{missing_field}",
        outcome="succeeded",
        output_ref="output:partial",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            f"insight:partial:{missing_field}",
            "routing_suggestion",
            "subscriber-support",
            0.91,
            "b" * 64,
        ),
        created_at=at,
    )
    # Direct column writes, not through `acknowledge_insight` — that
    # function only ever writes all four together or all four `None`.
    # This simulates the partial state `models.py` documents as possible
    # (a race, or an out-of-band write), which is exactly the shape this
    # test needs to exercise the completeness check on its own.
    full_values: dict[str, str | None] = {
        "action_evidence_locator_namespace": "ticketing",
        "action_evidence_locator_ref": "ticket:partial",
        "action_evidence_content_digest": "e" * 64,
        "action_evidence_media_type": "text/plain",
    }
    full_values[missing_field] = None
    for column, value in full_values.items():
        # Private-by-convention attribute names (round 15): the dict above
        # keys by the readable, unprefixed name for parametrize-id
        # legibility; the actual attribute this package's model exposes
        # is the underscore-prefixed one.
        setattr(insight, f"_{column}", value)
    db.flush()

    assert authoritative_acknowledgement(insight).evidence is None


@pytest.mark.parametrize(
    "missing_field",
    [
        "attribution_actor_namespace",
        "attribution_actor_type",
        "attribution_actor_ref",
        "attribution_acknowledged_at",
    ],
)
def test_authoritative_acknowledgement_returns_none_attribution_for_partial_binding(
    db: Session, missing_field: str
) -> None:
    """Plant 3: partial attribution is REFUSED at the read side — never
    combined with the legacy `acknowledged_by_ref`/`acknowledged_at`
    columns to manufacture a complete-looking result. Mirrors the
    equivalent partial-EVIDENCE sensitivity test above (round 13 finding
    4) for the identical reason: deleting any ONE of the four
    `is not None` predicates in `authoritative_acknowledgement`'s
    attribution completeness check would leave every prior test green,
    and a row missing only `missing_field` would then reach
    `AIAcknowledgementAttribution` construction with `None` for it and
    raise there instead of `.attribution` returning `None`.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key=f"message:partial-attr:{missing_field}",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:partial-attr",
        input_digest="a" * 64,
        started_at=at,
    )
    # `create_insight` requires BOTH a succeeded operation AND a matching
    # succeeded attempt (round 20 finding).
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key=f"attempt:partial-attr:{missing_field}",
        outcome="succeeded",
        output_ref="output:partial-attr",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            f"insight:partial-attr:{missing_field}",
            "routing_suggestion",
            "subscriber-support",
            0.91,
            "b" * 64,
        ),
        created_at=at,
    )
    # Direct column writes, not through `acknowledge_insight` — that
    # function only ever writes all four attribution columns together or
    # all four `None`. This simulates the partial state that IS possible
    # out-of-band (a race with a hypothetical future writer, or direct
    # column manipulation), the same way the evidence equivalent does.
    full_values: dict[str, str | datetime | None] = {
        "attribution_actor_namespace": "platform-identity",
        "attribution_actor_type": "human",
        "attribution_actor_ref": "user:partial",
        "attribution_acknowledged_at": at,
    }
    full_values[missing_field] = None
    for column, value in full_values.items():
        # Private-by-convention attribute names (round 15): see the
        # matching comment in the evidence equivalent above.
        setattr(insight, f"_{column}", value)
    # Legacy actor/time ARE present on this row, deliberately — proving
    # partial typed attribution is not silently combined with them to
    # produce a complete-looking answer.
    insight.status = "acknowledged"
    insight.acknowledged_by_ref = "agent:legacy-fallback"
    insight.acknowledged_at = at
    db.flush()

    resolved = authoritative_acknowledgement(insight)
    assert resolved.attribution is None


def test_acknowledge_insight_rejects_bare_string_evidence(db: Session) -> None:
    """No silent coercion at the service boundary either: a bare locator
    string offered as `action_evidence` is refused, never wrapped into an
    `AIEvidenceBinding` on the caller's behalf."""
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:8",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:8",
        input_digest="a" * 64,
        started_at=at,
    )
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:8",
        outcome="succeeded",
        output_ref="output:8",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            "insight:8", "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )
    with pytest.raises(AIOperationRefused, match="AIEvidenceBinding"):
        acknowledge_insight(
            db,
            tenant_id=tenant.id,
            insight_id=insight.id,
            actor_ref="agent:2",
            action_evidence="ticket:42",  # type: ignore[arg-type]
            actor_attribution=None,
            acknowledged_at=at,
        )


def test_acknowledge_insight_rejects_bare_string_attribution(db: Session) -> None:
    """The identical refusal, for attribution: a bare actor string offered
    as `actor_attribution` is refused, never wrapped into an
    `AIAcknowledgementAttribution` on the caller's behalf."""
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:9",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:9",
        input_digest="a" * 64,
        started_at=at,
    )
    # `create_insight` requires BOTH a succeeded operation AND a matching
    # succeeded attempt (round 20 finding).
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:9",
        outcome="succeeded",
        output_ref="output:9",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            "insight:9", "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )
    with pytest.raises(AIOperationRefused, match="AIAcknowledgementAttribution"):
        acknowledge_insight(
            db,
            tenant_id=tenant.id,
            insight_id=insight.id,
            actor_ref="agent:2",
            action_evidence=None,
            actor_attribution="user:9",  # type: ignore[arg-type]
            acknowledged_at=at,
        )


def test_acknowledge_insight_refuses_disagreeing_acknowledged_at(db: Session) -> None:
    """One evaluation, one stored fact, no second path that can disagree
    (Michael, holding the same standard `record_attempt`'s
    `_canonicalize_utc` fix established for `observed_at`): a caller that
    passes a DIFFERENT instant for `actor_attribution.acknowledged_at`
    than for the top-level `acknowledged_at` parameter is refused, not
    silently resolved by writing one of the two disagreeing values. This
    would fail (no exception raised, and one of the two values silently
    wins) if that self-consistency check were ever removed.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    other_at = datetime(2026, 8, 21, 9, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:10",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:10",
        input_digest="a" * 64,
        started_at=at,
    )
    # `create_insight` requires BOTH a succeeded operation AND a matching
    # succeeded attempt (round 20 finding).
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:10",
        outcome="succeeded",
        output_ref="output:10",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            "insight:10", "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )
    with pytest.raises(AIOperationRefused, match="must name the same instant"):
        acknowledge_insight(
            db,
            tenant_id=tenant.id,
            insight_id=insight.id,
            actor_ref="agent:2",
            action_evidence=None,
            actor_attribution=AIAcknowledgementAttribution(
                actor_namespace="platform-identity",
                actor_type="human",
                actor_ref="user:10",
                acknowledged_at=other_at,
            ),
            acknowledged_at=at,
        )


def test_acknowledge_insight_refuses_a_row_already_acknowledged(db: Session) -> None:
    """Single-threaded regression test for the logic-level half of
    `acknowledge_insight`'s conditional update.

    NOTE on what this test actually proves, stated precisely because the
    obvious framing ("delete the UPDATE's `.where(AIInsight.status ==
    'advisory')` clause and this goes red") is not accurate for this exact
    code shape and would be a false claim to leave standing: the
    function's SELECT and its UPDATE both filter on the identical
    predicate (`tenant_id`, `id`, `status == "advisory"`). This test
    acknowledges the same insight twice, sequentially, through the real
    function both times — after the first call the row's `status` is
    `"acknowledged"`, so the second call's own SELECT returns `None` and
    raises via the `insight is None` branch; execution never reaches the
    UPDATE or its `rowcount` check at all. So the real break condition
    here is removing the SELECT's `AIInsight.status == "advisory"` filter
    (or the `insight is None` refusal that depends on it) — not the
    UPDATE's clause in isolation. Genuinely exercising the UPDATE-specific
    rowcount branch requires a row that is still `"advisory"` when the
    SELECT runs and no longer `"advisory"` by the time the UPDATE runs — a
    true interleaving between two overlapping callers, which cannot be
    produced deterministically in a single synchronous call without
    execution to confirm an injected hook actually lands at the right
    instant. That gap is left open and stated here, but it is no longer an
    OPEN gap in this lane overall:
    `tests/test_ai_operations_attribution_postgres.py::test_two_overlapping_writers_interleave_under_read_committed`
    forces the genuine interleaving, against real PostgreSQL, and is where
    the UPDATE-specific `rowcount` branch is actually proven. This test
    stays exactly as narrow as described above — it does not newly prove
    the interleaving property, only names where that property now lives.

    What this test DOES genuinely prove: a second caller cannot
    acknowledge a row that a first caller already acknowledged — the
    pre-condition check the SELECT enforces on every call, not merely at
    construction of the conditional UPDATE.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:14",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:14",
        input_digest="a" * 64,
        started_at=at,
    )
    # `create_insight` requires BOTH a succeeded operation AND a matching
    # succeeded attempt (round 20 finding).
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:14",
        outcome="succeeded",
        output_ref="output:14",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            "insight:14", "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )
    acknowledge_insight(
        db,
        tenant_id=tenant.id,
        insight_id=insight.id,
        actor_ref="agent:first",
        action_evidence=None,
        actor_attribution=None,
        acknowledged_at=at,
    )
    assert insight.status == "acknowledged"
    with pytest.raises(AIOperationRefused, match="advisory insight and actor"):
        acknowledge_insight(
            db,
            tenant_id=tenant.id,
            insight_id=insight.id,
            actor_ref="agent:second",
            action_evidence=None,
            actor_attribution=None,
            acknowledged_at=at,
        )


def test_acknowledge_insight_pairs_evidence_and_attribution_from_one_call(
    db: Session,
) -> None:
    """Plant 5, NARROWED to the claim it can actually reach — stated
    plainly rather than left named for a property it does not prove.

    What this test does NOT prove: genuine CONCURRENT/overlapping
    callers. The two `acknowledge_insight` calls below run sequentially,
    in one thread, against one session — by the time the second call's
    own preliminary SELECT runs, the first call's `UPDATE` has already
    committed and flipped `status` to `"acknowledged"`, so the second call
    is refused there (the same mechanism
    `test_acknowledge_insight_refuses_a_row_already_acknowledged`
    documents and narrows identically). Deleting the UPDATE's `status ==
    "advisory"` predicate AND its `rowcount` refusal would NOT turn this
    test red, because the SELECT's identical predicate already refuses
    the second call first — exactly the reviewer-identified gap. A
    genuine overlapping-writers proof would need real interleaving
    (threads/processes with a forced lock-ordering device, the shape
    `tests/test_external_identity_login_race.py` uses against real
    Postgres), which is execution this lane cannot perform; this test
    does not claim to be that proof — that proof now exists, as
    `tests/test_ai_operations_attribution_postgres.py::test_two_overlapping_writers_interleave_under_read_committed`,
    against real PostgreSQL with forced interleaving. This test's own
    claim stays exactly as narrow as stated above.

    What this test DOES prove, entirely through DIRECT attribute reads on
    `insight` exactly as `acknowledge_insight` left it (no test repair —
    Michael's round-18 ruling): only the first call's evidence and
    attribution are ever persisted, never a mix of the two calls' inputs
    (e.g. the first call's evidence paired with the second call's
    attribution).

    What it additionally records — the settled, DECLARED boundary from
    Michael's round-19 ruling, not merely surfaced but deliberately
    proven: on SQLite, `authoritative_acknowledgement(insight)` — called
    on the SAME object `acknowledge_insight` just returned, immediately
    after a successful call, no fresh session even needed — RAISES,
    because `acknowledge_insight`'s own internal `db.refresh(insight)`
    already round-trips `_attribution_acknowledged_at` through SQLite's
    `DATETIME` type, which returns it NAIVE, and
    `authoritative_acknowledgement` correctly refuses to restore
    awareness by inferring an offset it cannot prove. So the ORIGINAL
    claim this test's name still describes ("pairs evidence and
    attribution from one call") is proven only via direct attribute
    access here, NOT via the paired accessor's happy path — see the
    `pytest.raises` block below,
    `test_ao_0003_shadows_attribution_against_current_and_
    legacy_writers`'s fuller explanation, and
    `tests/test_ai_operations_attribution_postgres.py`, which proves the
    same call's happy path against real PostgreSQL instead.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:15",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:15",
        input_digest="a" * 64,
        started_at=at,
    )
    # `create_insight` requires BOTH a succeeded operation AND a matching
    # succeeded attempt (round 20 finding).
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:15",
        outcome="succeeded",
        output_ref="output:15",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    insight = create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            "insight:15", "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )
    acknowledge_insight(
        db,
        tenant_id=tenant.id,
        insight_id=insight.id,
        actor_ref="agent:winner",
        action_evidence=AIEvidenceBinding(
            locator_namespace="ticketing",
            locator_ref="ticket:winner",
            content_digest="a" * 64,
            media_type="text/plain",
        ),
        actor_attribution=AIAcknowledgementAttribution(
            actor_namespace="platform-identity",
            actor_type="human",
            actor_ref="user:winner",
            acknowledged_at=at,
        ),
        acknowledged_at=at,
    )
    with pytest.raises(AIOperationRefused, match="advisory insight and actor"):
        acknowledge_insight(
            db,
            tenant_id=tenant.id,
            insight_id=insight.id,
            actor_ref="agent:loser",
            action_evidence=AIEvidenceBinding(
                locator_namespace="ticketing",
                locator_ref="ticket:loser",
                content_digest="b" * 64,
                media_type="text/plain",
            ),
            actor_attribution=AIAcknowledgementAttribution(
                actor_namespace="platform-identity",
                actor_type="human",
                actor_ref="user:loser",
                acknowledged_at=at,
            ),
            acknowledged_at=at,
        )

    # `acknowledge_insight`'s own `db.refresh(insight)` re-fetches every
    # attribute from SQLite, whose `DATETIME` type discards `tzinfo` on
    # every round trip (see `test_ao_0003_shadows_attribution_against_
    # current_and_legacy_writers`'s docstring for the full explanation
    # and the source citations) — this happens even within the SAME
    # session, since it is a property of the column type/dialect, not
    # session identity. `insight._attribution_acknowledged_at` is
    # therefore NAIVE here — genuinely, from production's own internal
    # refresh, with NO test repair applied (Michael's round-18 ruling: a
    # test that repairs its input before calling the thing under test
    # cannot fail for the reason it exists).

    # PROPERTY 1 — SQLite genuinely stored the once-canonicalised UTC
    # instant in its own naive representation.
    assert insight._attribution_acknowledged_at is not None
    assert insight._attribution_acknowledged_at.tzinfo is None
    assert insight._attribution_acknowledged_at == at.replace(tzinfo=None)

    # Evidence and attribution's non-time fields, verified directly.
    assert insight._action_evidence_locator_ref == "ticket:winner"
    assert insight._attribution_actor_ref == "user:winner"

    # PROPERTY 2 — MICHAEL'S RULING (round 19), a settled DECLARED
    # BOUNDARY, not an open gap: `authoritative_acknowledgement` stays
    # STRICT and must not infer an offset it cannot prove. This assertion
    # is the proof the boundary holds, called on `insight` EXACTLY AS
    # `acknowledge_insight` LEFT IT (after its own internal `db.refresh`),
    # with no repair. It means `acknowledge_insight` ITSELF, not merely a
    # later read, returns an `insight` whose typed attribution cannot be
    # resolved through `authoritative_acknowledgement` on SQLite,
    # immediately after a successful call — see the fuller explanation in
    # `test_ao_0003_shadows_attribution_against_current_and_legacy_writers`
    # and `tests/test_ai_operations_attribution_postgres.py`, which
    # proves the same call's happy path on real PostgreSQL instead.
    with pytest.raises(ValueError, match="acknowledged_at"):
        authoritative_acknowledgement(insight)


def _advisory_insight(db: Session, *, tag: str) -> AIInsight:
    """Shared setup for the `acknowledge_insight` timestamp-boundary
    sensitivity tests below: a tenant, an active policy version, a
    succeeded operation and attempt, and one still-`"advisory"` insight —
    everything `acknowledge_insight` needs before the boundary this
    module is testing runs at all.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key=f"message:{tag}",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref=f"message:{tag}",
        input_digest="a" * 64,
        started_at=at,
    )
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key=f"attempt:{tag}",
        outcome="succeeded",
        output_ref=f"output:{tag}",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    return create_insight(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        command=InsightInput(
            f"insight:{tag}", "routing_suggestion", "subscriber-support", 0.91, "b" * 64
        ),
        created_at=at,
    )


def test_acknowledge_insight_reports_a_raising_tzinfo_as_ai_operation_refused(
    db: Session,
) -> None:
    """The runtime/security finding this test closes: `acknowledge_insight`
    used to call `_aware(acknowledged_at, ...)` and
    `_canonicalize_utc(acknowledged_at)` with NO enclosing `try`, so a
    caller-supplied `tzinfo` whose `utcoffset()` raises (here
    `_ExplodingTzinfo`, a `RuntimeError` — deliberately NOT `ValueError`,
    the exact exception type that defeated the narrowed version of this
    same boundary in `record_attempt` three times) propagated raw instead
    of `AIOperationRefused`. This would fail (a raw `RuntimeError` would
    propagate) if the `_aware`/`_canonicalize_utc` pair were ever moved
    back outside `acknowledge_insight`'s `try`, or if that `try`'s
    `except` were narrowed to a subset excluding `RuntimeError`.
    """
    db_tenant_insight = _advisory_insight(db, tag="hostile-tzinfo")
    exploding_at = datetime(2026, 8, 21, 8, tzinfo=_ExplodingTzinfo())
    with pytest.raises(AIOperationRefused, match="boom"):
        acknowledge_insight(
            db,
            tenant_id=db_tenant_insight.tenant_id,
            insight_id=db_tenant_insight.id,
            actor_ref="agent:hostile-tzinfo",
            action_evidence=None,
            actor_attribution=None,
            acknowledged_at=exploding_at,
        )


def test_acknowledge_insight_reports_canonicalization_overflow_as_ai_operation_refused(
    db: Session,
) -> None:
    """`acknowledged_at` at `datetime.max` with a large negative UTC
    offset overflows when `_canonicalize_utc` calls `.astimezone(UTC)`
    (converting to UTC effectively ADDS hours past `datetime.max`, which
    raises `OverflowError` — the same construction already confirmed
    against the stdlib for `record_attempt`'s equivalent test). This
    would fail (a raw `OverflowError` would propagate instead of
    `AIOperationRefused`) if `_canonicalize_utc`'s call in
    `acknowledge_insight` ever moved back outside its `try`, or if that
    `try`'s `except` were narrowed to exclude `OverflowError`.
    """
    insight = _advisory_insight(db, tag="overflow")
    overflowing_at = datetime(
        9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone(timedelta(hours=-14))
    )
    with pytest.raises(AIOperationRefused):
        acknowledge_insight(
            db,
            tenant_id=insight.tenant_id,
            insight_id=insight.id,
            actor_ref="agent:overflow",
            action_evidence=None,
            actor_attribution=None,
            acknowledged_at=overflowing_at,
        )


def test_acknowledge_insight_refuses_non_datetime_timestamp(
    db: Session,
) -> None:
    """A non-`datetime` `acknowledged_at` (here `None`) must be reported
    as `AIOperationRefused`, matching `"acknowledged_at"`, through
    `acknowledge_insight`'s own boundary — the same `isinstance` guard
    `_aware` already holds for `record_attempt`, combined with
    `acknowledge_insight`'s own `try`/`except Exception`.

    Two DIFFERENT perturbations turn this red, each for a different
    reason — stated separately rather than folded into one claim, since
    neither alone produces the naive "raw `AttributeError` escapes"
    picture:

    - Drop `_aware`'s `isinstance` check but keep the `try`: `None
      .utcoffset()` raises `AttributeError`, but the surrounding
      `except Exception` still catches it and re-raises it as
      `AIOperationRefused("'NoneType' object has no attribute
      'utcoffset'")`. The test still fails, but on the `match=
      "acknowledged_at"` regex — that message names neither
      `acknowledged_at` nor `None`'s type the way the real message does
      — not because an unwrapped exception escaped.
    - Keep the `isinstance` check but remove `acknowledge_insight`'s
      `try`: `_aware` raises a plain `ValueError` naming `acknowledged_at`
      and its type, which now propagates unwrapped.
      `pytest.raises(AIOperationRefused)` still fails, but because
      `AIOperationRefused` is a `ValueError` SUBCLASS — the raised
      `ValueError` is not an instance of it — not because the message is
      wrong.

    Only removing BOTH the `isinstance` check AND the `try` produces the
    literal unwrapped `AttributeError` a first read of this test's intent
    might expect. The test is a sound regression guard against either
    perturbation regardless — this note exists only so the mechanism is
    described accurately, since a wrong "why this fails" claim is exactly
    this lane's recurring defect class.
    """
    insight = _advisory_insight(db, tag="wrong-type")
    with pytest.raises(AIOperationRefused, match="acknowledged_at"):
        acknowledge_insight(
            db,
            tenant_id=insight.tenant_id,
            insight_id=insight.id,
            actor_ref="agent:wrong-type",
            action_evidence=None,
            actor_attribution=None,
            acknowledged_at=None,  # type: ignore[arg-type]
        )


def test_record_attempt_refuses_malformed_input_as_ai_operation_refused(
    db: Session,
) -> None:
    """Drives an invalid outcome THROUGH `record_attempt` (not directly
    through `AIExecutionObservation`) and asserts it is reported as
    `AIOperationRefused` — the exception contract `record_attempt`'s
    callers already have with every other refusal in this module. This
    would fail if `record_attempt` ever let `AIExecutionObservation`'s raw
    `ValueError` propagate unwrapped, or if validation were removed
    entirely (in which case no exception would be raised at all).
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:9",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:9",
        input_digest="a" * 64,
        started_at=at,
    )
    with pytest.raises(AIOperationRefused):
        record_attempt(
            db,
            tenant_id=tenant.id,
            operation_id=operation.id,
            attempt_key="attempt:9",
            outcome="not-a-real-outcome",
            output_ref=None,
            output_digest=None,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
            observed_at=at,
        )
    assert operation.status == "pending"


def test_record_attempt_normalizes_blank_output_pair_through_the_service(
    db: Session,
) -> None:
    """Drives a whitespace-only `output_ref`/`output_digest` pair THROUGH
    `record_attempt` and asserts the PERSISTED row has both normalized to
    `None` — genuinely persisted: the session is expired before reloading,
    so the assertions can only pass by re-reading from the database, not
    by inspecting the in-memory object `record_attempt` already returned
    (which would pass even if the row were never added or flushed at all).
    This would fail if `record_attempt` ever went back to reading raw
    parameters instead of the constructed, normalized
    `AIExecutionObservation`, or stopped persisting the row.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:10",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:10",
        input_digest="a" * 64,
        started_at=at,
    )
    attempt = record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:10",
        outcome="failed",
        output_ref="   ",
        output_digest="   ",
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code="E_PROVIDER",
        observed_at=at,
    )
    db.expire_all()  # discard in-memory state; force a real re-SELECT below
    reloaded = db.get(AIExecutionAttempt, attempt.id)
    assert reloaded is not None
    assert reloaded.output_ref is None
    assert reloaded.output_digest is None


def test_record_attempt_fingerprint_includes_observed_at(db: Session) -> None:
    """Reusing an attempt key with identical validated fields but a
    DIFFERENT `observed_at` must be reported as a reused key with
    different content, not silently return the earlier row — this would
    fail if the idempotency fingerprint ever dropped `observed_at` again.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    later = datetime(2026, 8, 21, 9, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:11",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:11",
        input_digest="a" * 64,
        started_at=at,
    )
    record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:11",
        outcome="succeeded",
        output_ref="output:11",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at,
    )
    with pytest.raises(AIOperationRefused, match="reused"):
        record_attempt(
            db,
            tenant_id=tenant.id,
            operation_id=operation.id,
            attempt_key="attempt:11",
            outcome="succeeded",
            output_ref="output:11",
            output_digest="b" * 64,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
            observed_at=later,
        )


def test_record_attempt_fingerprint_is_representation_independent(
    db: Session,
) -> None:
    """Two `observed_at` values naming the SAME instant under different
    UTC offsets (`2026-08-21T09:00:00+01:00` and
    `2026-08-21T08:00:00+00:00`) — which a `timestamptz` column stores
    identically — must NOT conflict as a "reused key with different
    observation": the fingerprint must canonicalize before hashing. This
    would fail (raise `AIOperationRefused`) if the fingerprint ever hashed
    `observed_at.isoformat()` directly again, since the round 9
    sensitivity test only varied the UTC hour and could not catch a
    representation-only difference.

    This ALSO asserts what merely checking replay identity (`second.id ==
    first.id`) cannot: that the PERSISTED `observed_at` (and
    `operation.completed_at`) are the CANONICAL UTC value, not the raw
    caller-supplied representation. Without this, reverting
    `record_attempt` to persist the ORIGINAL `observed_at` while still
    canonicalizing only the fingerprint input would leave `second.id ==
    first.id` passing (the digest still matches) while silently storing a
    non-UTC-offset representation — exactly the round 14 finding
    `_canonicalize_utc` closed. The FIRST call deliberately uses the
    NON-UTC-offset representation so this is a genuine check, not one
    that happens to pass because the offset representation was never
    exercised at write time.
    """
    tenant = _tenant(db)
    at_utc = datetime(2026, 8, 21, 8, tzinfo=UTC)
    same_instant_plus_one_hour_offset = datetime(
        2026, 8, 21, 9, tzinfo=timezone(timedelta(hours=1))
    )
    assert at_utc == same_instant_plus_one_hour_offset  # same instant, by construction
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at_utc,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at_utc
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:14",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:14",
        input_digest="a" * 64,
        started_at=at_utc,
    )
    # Deliberately the NON-UTC-offset representation on the WRITE that
    # actually creates the row — see the docstring above for why this
    # ordering is what makes the persisted-value assertions below genuine
    # rather than accidentally passing because `at_utc` was already
    # canonical.
    first = record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:14",
        outcome="succeeded",
        output_ref="output:14",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=same_instant_plus_one_hour_offset,
    )
    second = record_attempt(
        db,
        tenant_id=tenant.id,
        operation_id=operation.id,
        attempt_key="attempt:14",
        outcome="succeeded",
        output_ref="output:14",
        output_digest="b" * 64,
        provider_observation=None,
        model_observation=None,
        request_observation=None,
        error_code=None,
        observed_at=at_utc,
    )
    assert second.id == first.id

    # The PERSISTED value, not just the digest match: `first.observed_at`
    # must be the canonical UTC value (`tzinfo` fixed to `timezone.utc`,
    # hour `8`), not the raw `+01:00`-offset value `first` was actually
    # called with. Reverting `record_attempt` to persist the ORIGINAL
    # `observed_at` while still canonicalizing only the fingerprint input
    # would leave `second.id == first.id` above passing while this fails.
    assert first.observed_at == at_utc
    assert first.observed_at.tzinfo is UTC
    # Same session, same identity map: `operation` is the exact object
    # `record_attempt` mutated in place, so this reads the persisted
    # write directly, not a stale pre-call snapshot.
    assert operation.completed_at == at_utc
    assert operation.completed_at is not None
    assert operation.completed_at.tzinfo is UTC


def test_record_attempt_refuses_naive_observed_at_as_ai_operation_refused(
    db: Session,
) -> None:
    """A naive `observed_at` must be reported as `AIOperationRefused`
    through `record_attempt`, not a raw `ValueError` — `_aware` runs
    inside the same wrapping `try` as `AIExecutionObservation`'s own
    validation, so every refusal this function can produce shares one
    exception type. This would fail (raise `ValueError` instead) if
    `_aware` were ever moved back outside that `try`.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:15",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:15",
        input_digest="a" * 64,
        started_at=at,
    )
    with pytest.raises(AIOperationRefused, match="observed_at"):
        record_attempt(
            db,
            tenant_id=tenant.id,
            operation_id=operation.id,
            attempt_key="attempt:15",
            outcome="succeeded",
            output_ref="output:15",
            output_digest="b" * 64,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
            observed_at=datetime(2026, 8, 21, 8),  # naive on purpose
        )


def test_record_attempt_refuses_non_datetime_observed_at_as_ai_operation_refused(
    db: Session,
) -> None:
    """Round 12 finding 4: `_aware` used to call `.utcoffset()` without
    checking `value` was a `datetime` at all, so a non-`datetime`
    `observed_at` (``None``, a string, ...) raised an uncaught
    `AttributeError` instead of `AIOperationRefused` — contradicting the
    documented exception contract. This would fail (raise `AttributeError`
    instead) if `_aware` ever dropped its `isinstance` check.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:16",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:16",
        input_digest="a" * 64,
        started_at=at,
    )
    with pytest.raises(AIOperationRefused, match="observed_at"):
        record_attempt(
            db,
            tenant_id=tenant.id,
            operation_id=operation.id,
            attempt_key="attempt:16",
            outcome="succeeded",
            output_ref="output:16",
            output_digest="b" * 64,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
            observed_at="2026-08-21T08:00:00+00:00",  # type: ignore[arg-type]
        )


class _ExplodingTzinfo(tzinfo):
    """A caller-controlled `tzinfo` that raises from `utcoffset()` — the
    exact shape round 13's finding 3 named: `_aware` and
    `_canonicalize_utc` both call methods on a `tzinfo` this package does
    not control, and it can raise anything, not just `ValueError`/
    `OverflowError`."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        raise RuntimeError("boom: untrusted tzinfo raised")

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "EXPLODE"


def test_record_attempt_reports_a_raising_tzinfo_as_ai_operation_refused(
    db: Session,
) -> None:
    """Round 13 finding 3: `(ValueError, OverflowError)` still let a
    caller-supplied `tzinfo` whose `utcoffset()` raises an arbitrary
    exception (here `RuntimeError`) leak past `record_attempt` raw,
    contradicting the exception contract for the fourth consecutive
    round. `record_attempt`'s `try` now catches `Exception` broadly
    instead of a named subset. This would fail (a raw `RuntimeError`
    would propagate instead of `AIOperationRefused`) if that `except`
    were ever narrowed back to `(ValueError, OverflowError)` or any other
    subset that does not include `RuntimeError`.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:17",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:17",
        input_digest="a" * 64,
        started_at=at,
    )
    exploding_observed_at = datetime(2026, 8, 21, 8, tzinfo=_ExplodingTzinfo())
    with pytest.raises(AIOperationRefused, match="boom"):
        record_attempt(
            db,
            tenant_id=tenant.id,
            operation_id=operation.id,
            attempt_key="attempt:17",
            outcome="succeeded",
            output_ref="output:17",
            output_digest="b" * 64,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
            observed_at=exploding_observed_at,
        )


def test_record_attempt_reports_canonicalization_overflow_as_ai_operation_refused(
    db: Session,
) -> None:
    """The claimed `OverflowError` handling has never had a regression
    test until now. `observed_at` at `datetime.max` with a large negative
    UTC offset overflows when `_canonicalize_utc` calls
    `.astimezone(UTC)` (converting to UTC effectively ADDS hours past
    `datetime.max`, which raises `OverflowError`, confirmed directly
    against the stdlib before writing this test). This would fail (a raw
    `OverflowError` would propagate instead of `AIOperationRefused`) if
    canonicalization ever moved back outside `record_attempt`'s `try`, or
    if the `try`'s `except` were narrowed to exclude `OverflowError`.
    """
    tenant = _tenant(db)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    policy = create_policy(
        db, tenant_id=tenant.id, code="conversation.intake", title="Conversation intake"
    )
    version = publish_policy_version(
        db,
        tenant_id=tenant.id,
        policy_id=policy.id,
        allowed_operation_kinds=("classification",),
        input_contract_ref="sub:conversation:v1",
        published_at=at,
    )
    activate_policy_version(
        db, tenant_id=tenant.id, version_id=version.id, activated_at=at
    )
    operation, _ = start_operation(
        db,
        tenant_id=tenant.id,
        operation_key="message:18",
        policy_version_id=version.id,
        operation_kind="classification",
        input_ref="message:18",
        input_digest="a" * 64,
        started_at=at,
    )
    overflowing_observed_at = datetime(
        9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone(timedelta(hours=-14))
    )
    with pytest.raises(AIOperationRefused):
        record_attempt(
            db,
            tenant_id=tenant.id,
            operation_id=operation.id,
            attempt_key="attempt:18",
            outcome="succeeded",
            output_ref="output:18",
            output_digest="b" * 64,
            provider_observation=None,
            model_observation=None,
            request_observation=None,
            error_code=None,
            observed_at=overflowing_observed_at,
        )
