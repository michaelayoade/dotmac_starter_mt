"""Hosted twenty-one-table snapshot canaries over genuine unchanged lineages.

Package-store restore is not Academy ACK/reference/fence or compatible-assembly
cutover proof. The V1 protocol remains explicitly limited to seven tables.
"""

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from dotmac_learning.curriculum_models import CURRICULUM_MODELS
from dotmac_learning.curriculum_transfer import export_snapshot_v2, verify_snapshot_v2
from dotmac_learning.curriculum_transfer_contracts import (
    SnapshotClosureErrorV2,
)
from dotmac_learning.models import TENANT_MODELS
from dotmac_learning.participation_models import PARTICIPATION_MODELS
from dotmac_learning.participation_transfer import (
    export_snapshot_v3,
    import_snapshot_v3,
    verify_snapshot_v3,
)
from dotmac_learning.participation_transfer_contracts import (
    CohortRowV3,
    CohortTrackRowV3,
    CourseOfferingRowV3,
    CoursePrerequisiteRowV3,
    EnrollmentRowV3,
    LearningSnapshotV3,
    SnapshotClosureErrorV3,
    TrackCourseRowV3,
    TrackRowV3,
)
from dotmac_learning.transfer import export_snapshot, verify_snapshot
from dotmac_learning.transfer_contracts import (
    DestinationNotEmpty,
    Float8Bits,
    JsonbText,
    SnapshotMismatch,
    SnapshotValidationError,
    TransferConnectionError,
    UnsupportedFloatRepresentation,
    UnsupportedTimestampRepresentation,
)
from sqlalchemy import DDL, bindparam, create_engine, event, func, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.dml import Insert

from tests import test_learning_catalog as learning_catalog_fixtures
from tests import test_learning_transfer as learning_transfer_fixtures
from tests.test_learning_catalog import (
    _person,
    _scope,
    _tenant,
)
from tests.test_learning_curriculum_transfer import _snapshot as curriculum_snapshot
from tests.test_learning_transfer import (
    NOW,
    ONE,
    ZERO,
)

learning_database = learning_catalog_fixtures.learning_database
transfer_database = learning_transfer_fixtures.transfer_database


NEW_TABLES = (
    "cohorts",
    "tracks",
    "cohort_tracks",
    "track_courses",
    "enrollments",
    "course_offerings",
    "course_prerequisites",
)
TABLES = (
    "courses",
    "activities",
    "submissions",
    "scores",
    "course_completions",
    "certificates",
    "learning_events",
    "chapters",
    "question_banks",
    "questions",
    "chapter_reads",
    "subtopic_reads",
    "chapter_bookmarks",
    "chapter_notes",
    *NEW_TABLES,
)
RELATIONS = {
    model.__tablename__: model.__table__
    for model in TENANT_MODELS + CURRICULUM_MODELS + PARTICIPATION_MODELS
}


def _snapshot(tenants, people):
    base = curriculum_snapshot(tenants, people)
    additions = {name: [] for name in NEW_TABLES}
    for index, (tenant, person) in enumerate(zip(tenants[:2], people[:2], strict=True)):
        identity = {
            "tenant_id": tenant,
            "created_at": NOW,
            "updated_at": NOW + timedelta(microseconds=1),
        }
        course = base.courses[index]
        cohort = CohortRowV3(
            id=uuid4(),
            name="  原 cohort  ",
            discipline="",
            status="historic",
            delivery_mode="historic",
            entrance_bank_id=uuid4(),
            entrance_time_limit_minutes=-7,
            auto_accept_threshold=Float8Bits("8000000000000000"),
            **identity,
        )
        track = TrackRowV3(
            id=uuid4(), slug="historic", name="  track  ", status="historic", **identity
        )
        additions["cohorts"].append(cohort)
        additions["tracks"].append(track)
        additions["cohort_tracks"].append(
            CohortTrackRowV3(
                id=uuid4(),
                cohort_id=cohort.id,
                track_id=track.id,
                status="historic",
                **identity,
            )
        )
        additions["track_courses"].append(
            TrackCourseRowV3(
                id=uuid4(),
                track_id=track.id,
                course_id=course.id,
                order_index=-7,
                **identity,
            )
        )
        additions["enrollments"].append(
            EnrollmentRowV3(
                id=uuid4(),
                cohort_id=cohort.id,
                person_id=person,
                track_id=track.id,
                role_in_cohort="historic",
                status="historic",
                audience="staff",
                employee_ref="  raw ref  ",
                access_ends_at=NOW,
                **identity,
            )
        )
        additions["course_offerings"].append(
            CourseOfferingRowV3(
                id=uuid4(),
                cohort_id=cohort.id,
                course_id=course.id,
                status="historic",
                starts_at=NOW,
                ends_at=NOW,
                **identity,
            )
        )
        additions["course_prerequisites"].append(
            CoursePrerequisiteRowV3(
                id=uuid4(),
                course_id=course.id,
                requires_course_id=course.id,
                **identity,
            )
        )
    return LearningSnapshotV3(
        version=3,
        **{
            field.name: getattr(base, field.name)
            for field in fields(base)
            if field.name != "version"
        },
        **{name: tuple(rows) for name, rows in additions.items()},
    )


def _empty(snapshot):
    return replace(snapshot, **{name: () for name in TABLES})


def _keyed(snapshot):
    return {name: {row.id: row for row in getattr(snapshot, name)} for name in TABLES}


def _counts(conn):
    return {
        name: conn.scalar(select(func.count()).select_from(RELATIONS[name]))
        for name in TABLES
    }


def _clear(conn):
    for name in reversed(TABLES):
        conn.execute(RELATIONS[name].delete())


def _inserts(conn):
    statements = []

    def observe(_conn, _cursor, _statement, _parameters, context, _executemany):
        compiled = context.compiled
        if (
            compiled is not None
            and isinstance(compiled.statement, Insert)
            and compiled.statement.table.schema == "mod_learning"
        ):
            statements.append(compiled.statement.table.name)

    event.listen(conn, "before_cursor_execute", observe)
    return statements, observe


def test_v3_restricted_actor_refuses_before_hidden_rows_are_treated_as_empty(
    transfer_database,
):
    owner, app, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v3(conn, snapshot)
    with app.begin() as conn:
        _scope(conn, tenants[2])
        for operation in (
            lambda: export_snapshot_v3(conn, (tenants[2],)),
            lambda: import_snapshot_v3(conn, _empty(snapshot)),
            lambda: verify_snapshot_v3(conn, _empty(snapshot)),
        ):
            with pytest.raises(TransferConnectionError):
                operation()


_KERNEL_COLUMN_GRANTS = (
    ("public.parties", "id", "GRANT SELECT (id) ON public.parties TO %(actor)s"),
    (
        "public.parties",
        "tenant_id",
        "GRANT SELECT (tenant_id) ON public.parties TO %(actor)s",
    ),
    (
        "public.party_persons",
        "party_id",
        "GRANT SELECT (party_id) ON public.party_persons TO %(actor)s",
    ),
)


def _transfer_column_reader(conn, missing=None):
    # UUID-generated test identifiers contain only lowercase ASCII/underscores.
    # Role, grants and SET LOCAL all belong to the caller's rolled-back root.
    actor = "learning_column_reader_" + uuid4().hex
    context = {"actor": actor}
    conn.execute(
        DDL(
            "CREATE ROLE %(actor)s NOSUPERUSER BYPASSRLS NOLOGIN NOINHERIT",
            context=context,
        )
    )
    conn.execute(
        DDL("GRANT USAGE ON SCHEMA public, mod_learning TO %(actor)s", context=context)
    )
    conn.execute(DDL("GRANT SELECT ON public.tenants TO %(actor)s", context=context))
    for name in TABLES:
        conn.execute(
            DDL(
                "GRANT SELECT, INSERT ON %(fullname)s TO %(actor)s", context=context
            ).against(RELATIONS[name])
        )
    for relation, column, statement in _KERNEL_COLUMN_GRANTS:
        if (relation, column) != missing:
            conn.execute(DDL(statement, context=context))
    conn.execute(DDL("SET LOCAL ROLE %(actor)s", context=context))
    assert conn.execute(
        text(
            "SELECT rolsuper, rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=current_user"
        )
    ).one() == (False, True)
    for relation, column, _statement in _KERNEL_COLUMN_GRANTS:
        assert conn.scalar(
            text(
                "SELECT pg_catalog.has_column_privilege("
                "current_user, :relation, :column, 'SELECT')"
            ),
            {"relation": relation, "column": column},
        ) is ((relation, column) != missing)
    for relation, private_column in (
        ("public.parties", "display_name"),
        ("public.party_persons", "first_name"),
    ):
        assert (
            conn.scalar(
                text(
                    "SELECT pg_catalog.has_table_privilege("
                    "current_user, :relation, 'SELECT')"
                ),
                {"relation": relation},
            )
            is False
        )
        assert (
            conn.scalar(
                text(
                    "SELECT pg_catalog.has_column_privilege("
                    "current_user, :relation, :column, 'SELECT')"
                ),
                {"relation": relation, "column": private_column},
            )
            is False
        )
    return actor


@pytest.mark.parametrize(
    "missing",
    [
        ("public.parties", "id"),
        ("public.parties", "tenant_id"),
        ("public.party_persons", "party_id"),
    ],
)
def test_v3_bypass_actor_missing_each_kernel_column_refuses_before_insert(
    transfer_database, missing
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    pending = uuid4()
    with owner.connect() as conn:
        root = conn.begin()
        _tenant(conn, pending)
        actor = _transfer_column_reader(conn, missing)
        inserts, listener = _inserts(conn)
        try:
            for operation in (
                lambda: export_snapshot_v3(conn, tenants),
                lambda: import_snapshot_v3(conn, snapshot),
                lambda: verify_snapshot_v3(conn, _empty(snapshot)),
            ):
                with pytest.raises(TransferConnectionError) as caught:
                    operation()
                assert str(caught.value) == "whole Kernel FK-column visibility required"
                assert inserts == [] and set(_counts(conn).values()) == {0}
                assert conn.get_transaction() is root and root.is_active
                assert (
                    conn.scalar(
                        text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                        {"id": pending},
                    )
                    == 1
                )
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()
        with conn.begin():
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname=:actor"
                    ),
                    {"actor": actor},
                )
                == 0
            )
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                    {"id": pending},
                )
                == 0
            )
            assert set(_counts(conn).values()) == {0}


def test_v3_bypass_actor_needs_only_exact_kernel_fk_column_grants(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        actor = _transfer_column_reader(conn)
        try:
            assert import_snapshot_v3(conn, snapshot).verification.matched
            assert verify_snapshot_v3(conn, snapshot).matched
            assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(snapshot)
            assert conn.get_transaction() is root and root.is_active
        finally:
            root.rollback()
        with conn.begin():
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname=:actor"
                    ),
                    {"actor": actor},
                )
                == 0
            )
            assert set(_counts(conn).values()) == {0}


def test_v3_two_tenant_all_fields_roundtrip_zero_tenant_and_v1_scope_are_distinct(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn, conn.begin() as root, conn.begin_nested() as nested:
        observed = import_snapshot_v3(conn, snapshot)
        assert observed.verification.version == 3 and observed.verification.matched
        assert len(observed.verification.row_counts) == len(TABLES) == 21
        assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(snapshot)
        assert verify_snapshot_v3(conn, snapshot).matched
        assert export_snapshot_v3(conn, (tenants[2],)) == replace(
            _empty(snapshot), tenant_ids=(tenants[2],)
        )
        assert conn.get_transaction() is root and root.is_active
        assert conn.get_nested_transaction() is nested and nested.is_active
        old = export_snapshot(conn, tenants)
        assert old.version == 1 and len(verify_snapshot(conn, old).row_counts) == 7
        conn.execute(
            RELATIONS["chapter_notes"]
            .update()
            .where(RELATIONS["chapter_notes"].c.id == snapshot.chapter_notes[0].id)
            .values(body="changed")
        )
        assert verify_snapshot(
            conn, old
        ).matched  # V1 attests only its seven-table extent.
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v3(conn, snapshot)


def test_v3_all_twenty_one_empty_is_valid_for_a_genuine_zero_row_tenant(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = replace(_empty(_snapshot(tenants, people)), tenant_ids=(tenants[2],))
    with owner.begin() as conn:
        assert import_snapshot_v3(conn, snapshot).verification.matched
        assert export_snapshot_v3(conn, (tenants[2],)) == snapshot


@pytest.mark.parametrize("table", ["courses", "question_banks", "tracks", "cohorts"])
def test_v3_refuses_partial_destination_across_any_tenant_and_all_twenty_one_tables(
    transfer_database, table
):
    owner, _, tenants, people = transfer_database
    with owner.begin() as conn:
        if table == "courses":
            conn.execute(
                RELATIONS[table]
                .insert()
                .values(
                    id=uuid4(),
                    tenant_id=tenants[2],
                    slug="partial",
                    title="",
                    discipline="",
                    source_ref="",
                )
            )
        elif table == "question_banks":
            conn.execute(
                RELATIONS[table]
                .insert()
                .values(
                    id=uuid4(), tenant_id=tenants[2], course_id=uuid4(), kind="historic"
                )
            )
        elif table == "tracks":
            conn.execute(
                RELATIONS[table]
                .insert()
                .values(
                    id=uuid4(), tenant_id=tenants[2], slug="partial", name="partial"
                )
            )
        else:
            conn.execute(
                RELATIONS[table]
                .insert()
                .values(id=uuid4(), tenant_id=tenants[2], name="partial", discipline="")
            )
        with pytest.raises(DestinationNotEmpty):
            import_snapshot_v3(conn, _snapshot(tenants[:2], people[:2]))


@pytest.mark.parametrize("table", NEW_TABLES)
def test_each_new_table_failure_propagates_and_root_rollback_restores_all(
    transfer_database, table
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        conn.execute(
            text(
                "CREATE FUNCTION public.fail_curriculum_transfer() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'injected failure'; END $$"
            )
        )
        conn.execute(
            DDL(
                "CREATE TRIGGER fail_curriculum_transfer BEFORE INSERT "
                "ON %(fullname)s FOR EACH ROW "
                "EXECUTE FUNCTION public.fail_curriculum_transfer()"
            ).against(RELATIONS[table])
        )
    pending = uuid4()
    with owner.connect() as conn:
        root = conn.begin()
        _tenant(conn, pending)
        with pytest.raises(DBAPIError):
            import_snapshot_v3(conn, snapshot)
        assert conn.get_transaction() is root
        root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                    {"id": pending},
                )
                == 0
            )


@pytest.mark.parametrize(
    "table,column",
    [
        ("chapters", "course_id"),
        ("questions", "bank_id"),
        ("chapter_reads", "chapter_id"),
        ("subtopic_reads", "chapter_id"),
        ("chapter_bookmarks", "course_id"),
        ("chapter_bookmarks", "chapter_id"),
        ("chapter_notes", "course_id"),
        ("chapter_notes", "chapter_id"),
        ("submissions", "activity_id"),
        ("scores", "submission_id"),
        ("course_completions", "course_id"),
        ("certificates", "course_id"),
    ],
)
def test_actual_learning_fk_closure_refuses_before_any_old_or_new_insert(
    transfer_database, table, column
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    snapshot = replace(
        snapshot, **{table: (replace(rows[0], **{column: uuid4()}),) + rows[1:]}
    )
    with owner.begin() as conn:
        pending = uuid4()
        _tenant(conn, pending)
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(
                (SnapshotClosureErrorV2, SnapshotClosureErrorV3)
            ) as caught:
                import_snapshot_v3(conn, snapshot)
            assert caught.value.table == table and caught.value.column == column
            assert inserts == [] and set(_counts(conn).values()) == {0}
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                    {"id": pending},
                )
                == 1
            )
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize(
    "mutation", ["missing_party", "missing_profile", "wrong_tenant"]
)
def test_real_event_kernel_fk_closure_refuses_prewrite_without_invented_party_type_rule(
    transfer_database, mutation
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        if mutation == "missing_party":
            person = uuid4()
        elif mutation == "missing_profile":
            person = _person(conn, tenants[0], subtype=False)
        else:
            person = people[1]
        snapshot = replace(
            snapshot,
            learning_events=(replace(snapshot.learning_events[0], person_id=person),)
            + snapshot.learning_events[1:],
        )
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises((SnapshotClosureErrorV2, SnapshotClosureErrorV3)):
                import_snapshot_v3(conn, snapshot)
            assert inserts == [] and set(_counts(conn).values()) == {0}
        finally:
            event.remove(conn, "before_cursor_execute", listener)


def test_source_simple_fks_plain_uuids_and_profiled_organization_history_are_preserved(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        historical_org = _person(conn, tenants[0], party_type="organization")
        snapshot = replace(
            snapshot,
            chapter_reads=(
                replace(snapshot.chapter_reads[0], chapter_id=snapshot.chapters[1].id),
            )
            + snapshot.chapter_reads[1:],
            chapter_notes=(
                replace(
                    snapshot.chapter_notes[0],
                    course_id=snapshot.courses[1].id,
                    chapter_id=snapshot.chapters[1].id,
                ),
            )
            + snapshot.chapter_notes[1:],
            learning_events=(
                replace(snapshot.learning_events[0], person_id=historical_org),
            )
            + snapshot.learning_events[1:],
        )
        assert import_snapshot_v3(conn, snapshot).verification.matched
        assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(snapshot)


def test_selected_export_preserves_simple_fk_escape_and_empty_import_refuses(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        chapter_reads=(
            replace(snapshot.chapter_reads[0], chapter_id=snapshot.chapters[1].id),
        )
        + snapshot.chapter_reads[1:],
    )
    with owner.begin() as conn:
        import_snapshot_v3(conn, snapshot)
        selected = export_snapshot_v3(conn, (tenants[0],))
        with pytest.raises(SnapshotMismatch) as caught:
            verify_snapshot_v3(conn, selected)
        assert any(item.reason == "outside_extent" for item in caught.value.mismatches)
        _clear(conn)
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises((SnapshotClosureErrorV2, SnapshotClosureErrorV3)):
                import_snapshot_v3(conn, selected)
            assert inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize("column", ["options", "correct"])
@pytest.mark.parametrize(
    "value",
    [
        "null",
        '"null"',
        "12345678901234567890.1234567890",
        "[true, null]",
        '{"text": "雪"}',
    ],
)
def test_question_jsonb_all_storage_kinds_roundtrip_exactly(
    transfer_database, column, value
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        questions=(replace(snapshot.questions[0], **{column: JsonbText(value)}),)
        + snapshot.questions[1:],
    )
    with owner.begin() as conn:
        assert import_snapshot_v3(conn, snapshot).verification.matched
        assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(snapshot)


@pytest.mark.parametrize("mutation", ["timestamp_overflow"])
def test_all_twenty_one_representations_are_preflighted_before_the_first_old_insert(
    transfer_database, mutation
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    from datetime import timezone

    snapshot = replace(
        snapshot,
        chapter_notes=(
            replace(
                snapshot.chapter_notes[0],
                updated_at=datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))),
            ),
        )
        + snapshot.chapter_notes[1:],
    )
    error = UnsupportedTimestampRepresentation
    with owner.connect() as conn:
        root = conn.begin()
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(error):
                import_snapshot_v3(conn, snapshot)
            assert inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}


@pytest.mark.parametrize(
    "table,column", [("questions", "options"), ("submissions", "answers")]
)
@pytest.mark.parametrize("value", ['{"sensitive_bad_json":', '["\ud800"]'])
def test_representation_errors_are_payload_free_and_leave_rollback_to_caller(
    transfer_database, table, column, value
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    snapshot = replace(
        snapshot,
        **{table: (replace(rows[0], **{column: JsonbText(value)}),) + rows[1:]},
    )
    pending = uuid4()
    with owner.connect() as conn:
        root = conn.begin()
        _tenant(conn, pending)
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotValidationError) as caught:
                import_snapshot_v3(conn, snapshot)
            assert type(caught.value) is SnapshotValidationError
            assert str(caught.value) == "unsupported storage representation"
            assert caught.value.__cause__ is None and caught.value.__suppress_context__
            assert conn.get_transaction() is root and root.is_active
            assert inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                    {"id": pending},
                )
                == 0
            )


@pytest.mark.parametrize(
    "bits", ["7ff0000000000001", "fff0000000000000", "8000000000000000"]
)
def test_old_float_bits_are_exact_or_explicitly_refused_before_all_inserts(
    transfer_database, bits
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        activities=(replace(snapshot.activities[0], weight=Float8Bits(bits)),)
        + snapshot.activities[1:],
    )
    with owner.connect() as conn:
        root = conn.begin()
        inserts, listener = _inserts(conn)
        try:
            try:
                import_snapshot_v3(conn, snapshot)
            except UnsupportedFloatRepresentation:
                assert inserts == []
            else:
                actual = export_snapshot_v3(conn, tenants)
                assert {row.id: row.weight for row in actual.activities}[
                    snapshot.activities[0].id
                ] == Float8Bits(bits)
                assert verify_snapshot_v3(conn, snapshot).matched
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}


@pytest.mark.parametrize(
    "mode", ["execution_option", "engine_default", "direct_driver"]
)
def test_v3_physical_autocommit_refusal_precedes_all_sql_for_all_three_apis(
    transfer_database, mode
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    alternate = (
        create_engine(owner.url, isolation_level="AUTOCOMMIT")
        if mode == "engine_default"
        else None
    )
    try:
        with (alternate or owner).connect() as conn:
            driver = conn.connection.dbapi_connection
            assert driver is not None
            if mode == "execution_option":
                conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            elif mode == "direct_driver":
                driver.autocommit = True
            root = conn.begin()
            statements = []

            def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
                statements.append(statement)

            event.listen(conn, "before_cursor_execute", observe)
            try:
                for operation in (
                    lambda: export_snapshot_v3(conn, tenants),
                    lambda: import_snapshot_v3(conn, snapshot),
                    lambda: verify_snapshot_v3(conn, snapshot),
                ):
                    with pytest.raises(TransferConnectionError):
                        operation()
                    assert statements == [] and conn.get_transaction() is root
            finally:
                event.remove(conn, "before_cursor_execute", observe)
                root.rollback()
                if mode == "direct_driver":
                    driver.autocommit = False
    finally:
        if alternate is not None:
            alternate.dispose()


@pytest.mark.parametrize("stored", ["infinity", "-infinity", "10000-01-01 00:00:00+00"])
def test_new_history_timestamps_use_binary_guarded_readback(transfer_database, stored):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v3(conn, snapshot)
        conn.execute(
            text(
                "UPDATE mod_learning.chapter_notes "
                "SET updated_at=CAST(:value AS timestamptz) WHERE id=:id"
            ),
            {"value": stored, "id": snapshot.chapter_notes[0].id},
        )
        for operation in (
            lambda: export_snapshot_v3(conn, tenants),
            lambda: verify_snapshot_v3(conn, snapshot),
        ):
            with pytest.raises(UnsupportedTimestampRepresentation):
                operation()


def test_new_history_timestamps_compare_fold_instants_exactly(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    first = datetime(2020, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Rome"), fold=0)
    row = replace(snapshot.chapter_notes[0], updated_at=first)
    snapshot = replace(snapshot, chapter_notes=(row,) + snapshot.chapter_notes[1:])
    with owner.begin() as conn:
        assert import_snapshot_v3(conn, snapshot).verification.matched
        utc_snapshot = replace(
            snapshot,
            chapter_notes=(replace(row, updated_at=first.astimezone(UTC)),)
            + snapshot.chapter_notes[1:],
        )
        assert verify_snapshot_v3(conn, utc_snapshot).matched
        plant = replace(
            snapshot,
            chapter_notes=(replace(row, updated_at=first.replace(fold=1)),)
            + snapshot.chapter_notes[1:],
        )
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v3(conn, plant)


def test_independent_verifier_detects_same_count_mutations_of_every_194_column(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v3(conn, snapshot)
        for table in TABLES:
            rows = getattr(snapshot, table)
            original = rows[0]
            for field in fields(original):
                value = getattr(original, field.name)
                if value is None:
                    if field.name in {
                        "chapter_number",
                        "max_attempts",
                        "question_count",
                    }:
                        changed = 17
                    elif field.name in {"bank_id", "course_id", "subject_id"}:
                        changed = uuid4()
                    elif field.name == "completed_at":
                        changed = NOW
                    else:
                        changed = "planted"
                elif isinstance(value, Float8Bits):
                    changed = ONE if value != ONE else ZERO
                elif isinstance(value, JsonbText):
                    changed = JsonbText("false")
                elif isinstance(value, bool):
                    changed = not value
                elif isinstance(value, int):
                    changed = value + 1
                elif isinstance(value, str):
                    changed = "planted"
                elif isinstance(value, datetime):
                    changed = value + timedelta(microseconds=1)
                else:
                    changed = tenants[2] if field.name == "tenant_id" else uuid4()
                planted = replace(
                    snapshot,
                    **{table: (replace(original, **{field.name: changed}),) + rows[1:]},
                )
                with pytest.raises(SnapshotMismatch) as caught:
                    verify_snapshot_v3(conn, planted)
                assert any(
                    item.table == table
                    and (item.column == field.name or field.name == "id")
                    for item in caught.value.mismatches
                )


@pytest.mark.parametrize(
    "mutation", ["omitted", "extra", "same_count_key", "json_kind"]
)
def test_independent_new_table_verifier_checks_both_key_directions_and_json_kind(
    transfer_database, mutation
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v3(conn, snapshot)
        question = snapshot.questions[0]
        if mutation == "omitted":
            conn.execute(
                RELATIONS["questions"]
                .delete()
                .where(RELATIONS["questions"].c.id == question.id)
            )
        elif mutation == "extra":
            row = snapshot.chapter_notes[0]
            conn.execute(
                RELATIONS["chapter_notes"]
                .insert()
                .values(
                    id=uuid4(),
                    tenant_id=row.tenant_id,
                    person_id=uuid4(),
                    course_id=row.course_id,
                    chapter_id=row.chapter_id,
                    body="planted",
                )
            )
        elif mutation == "same_count_key":
            conn.execute(
                RELATIONS["questions"]
                .update()
                .where(RELATIONS["questions"].c.id == question.id)
                .values(id=uuid4())
            )
        else:
            conn.execute(
                RELATIONS["questions"]
                .update()
                .where(RELATIONS["questions"].c.id == question.id)
                .values(options=sql_cast(bindparam("json_storage"), JSONB)),
                {"json_storage": '"null"'},
            )
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v3(conn, snapshot)


def test_export_all_current_old_and_new_facts_after_mutations_restores_empty_store(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v3(conn, initial)
    with owner.begin() as conn:
        chapter, bank = initial.chapters[0], initial.question_banks[0]
        conn.execute(
            RELATIONS["chapters"]
            .update()
            .where(RELATIONS["chapters"].c.id == chapter.id)
            .values(
                title="Changed",
                body_md="# Changed\n",
                body_html="<p>Changed</p>",
                source_hash="newhash",
                order_index=17,
            )
        )
        conn.execute(
            RELATIONS["question_banks"]
            .update()
            .where(RELATIONS["question_banks"].c.id == bank.id)
            .values(version=17)
        )
        conn.execute(
            RELATIONS["questions"]
            .delete()
            .where(RELATIONS["questions"].c.bank_id == bank.id)
        )
        conn.execute(
            text(
                "INSERT INTO mod_learning.questions "
                "(id,tenant_id,bank_id,ext_id,stem,type,options,correct,"
                "rubric_category,weight) VALUES (:id,:tenant,:bank,'new','New',"
                "'single',CAST(:options AS jsonb),'[1]','recall',2)"
            ),
            {
                "id": uuid4(),
                "tenant": tenants[0],
                "bank": bank.id,
                "options": '["later"]',
            },
        )
        conn.execute(
            RELATIONS["chapter_reads"]
            .delete()
            .where(RELATIONS["chapter_reads"].c.id == initial.chapter_reads[0].id)
        )
        conn.execute(
            RELATIONS["chapter_reads"]
            .insert()
            .values(
                id=uuid4(),
                tenant_id=tenants[0],
                person_id=uuid4(),
                chapter_id=chapter.id,
            )
        )
        conn.execute(
            RELATIONS["chapter_notes"]
            .update()
            .where(RELATIONS["chapter_notes"].c.id == initial.chapter_notes[0].id)
            .values(body="  later <raw>\n ")
        )
        conn.execute(
            RELATIONS["chapters"]
            .delete()
            .where(RELATIONS["chapters"].c.id == initial.chapters[1].id)
        )
        conn.execute(
            RELATIONS["scores"]
            .delete()
            .where(RELATIONS["scores"].c.id == initial.scores[0].id)
        )
        conn.execute(
            RELATIONS["scores"]
            .insert()
            .values(
                id=uuid4(),
                tenant_id=tenants[0],
                submission_id=initial.submissions[0].id,
                score=1.0,
                max_score=2.0,
                fraction=0.5,
                passed=False,
                per_item=[],
            )
        )
        conn.execute(
            text(
                "INSERT INTO mod_learning.learning_events "
                "(id,tenant_id,person_id,kind,detail,occurred_at) "
                "VALUES (:id,:tenant,:person,'chapter_completed','null',:now)"
            ),
            {
                "id": uuid4(),
                "tenant": tenants[0],
                "person": people[0],
                "now": NOW + timedelta(days=2),
            },
        )
        current = export_snapshot_v3(conn, tenants)
        assert _keyed(current) != _keyed(initial)
    with owner.connect() as conn:
        root = conn.begin()
        _clear(conn)
        assert import_snapshot_v3(conn, current).verification.matched
        assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(current)
        root.rollback()
        with conn.begin():
            assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(current)


@pytest.mark.parametrize(
    "table,column",
    [
        ("cohort_tracks", "cohort_id"),
        ("cohort_tracks", "track_id"),
        ("track_courses", "track_id"),
        ("track_courses", "course_id"),
        ("enrollments", "cohort_id"),
        ("enrollments", "track_id"),
        ("course_offerings", "cohort_id"),
        ("course_offerings", "course_id"),
        ("course_prerequisites", "course_id"),
        ("course_prerequisites", "requires_course_id"),
    ],
)
@pytest.mark.parametrize("cross_tenant", [False, True])
def test_v3_actual_composite_fk_closure_precedes_every_insert(
    transfer_database, table, column, cross_tenant
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    target = getattr(rows[1], column) if cross_tenant else uuid4()
    snapshot = replace(
        snapshot, **{table: (replace(rows[0], **{column: target}),) + rows[1:]}
    )
    with owner.begin() as conn:
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotClosureErrorV3):
                import_snapshot_v3(conn, snapshot)
            assert inserts == [] and set(_counts(conn).values()) == {0}
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize(
    "mutation", ["missing_party", "missing_profile", "wrong_tenant"]
)
def test_v3_enrollment_requires_both_genuine_identity_fks_prewrite(
    transfer_database, mutation
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        person = (
            uuid4()
            if mutation == "missing_party"
            else _person(conn, tenants[0], subtype=False)
            if mutation == "missing_profile"
            else people[1]
        )
        snapshot = replace(
            snapshot,
            enrollments=(replace(snapshot.enrollments[0], person_id=person),)
            + snapshot.enrollments[1:],
        )
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotClosureErrorV3):
                import_snapshot_v3(conn, snapshot)
            assert inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", listener)


def test_v3_null_track_matches_simple_and_logical_bank_is_unconstrained(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        enrollments=tuple(
            replace(
                row,
                track_id=None,
                audience=None,
                employee_ref=None,
                access_ends_at=None,
            )
            for row in snapshot.enrollments
        ),
        cohort_tracks=(),
        cohorts=tuple(
            replace(
                row,
                auto_accept_threshold=None,
                entrance_bank_id=uuid4(),
                entrance_time_limit_minutes=None,
            )
            for row in snapshot.cohorts
        ),
        course_offerings=tuple(
            replace(row, starts_at=None, ends_at=None)
            for row in snapshot.course_offerings
        ),
    )
    with owner.begin() as conn:
        assert import_snapshot_v3(conn, snapshot).verification.matched
        assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(snapshot)


@pytest.mark.parametrize(
    "bits",
    ["0000000000000000", "8000000000000000", "3fe0000000000000", "7ff8000000000001"],
)
def test_v3_nullable_threshold_exact_bits_or_prewrite_refusal(transfer_database, bits):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        cohorts=(replace(snapshot.cohorts[0], auto_accept_threshold=Float8Bits(bits)),)
        + snapshot.cohorts[1:],
    )
    with owner.connect() as conn:
        root = conn.begin()
        inserts, listener = _inserts(conn)
        try:
            try:
                import_snapshot_v3(conn, snapshot)
            except UnsupportedFloatRepresentation:
                assert inserts == []
            else:
                exported = export_snapshot_v3(conn, tenants)
                assert {row.id: row.auto_accept_threshold for row in exported.cohorts}[
                    snapshot.cohorts[0].id
                ] == Float8Bits(bits)
                assert _keyed(exported) == _keyed(snapshot)
                assert verify_snapshot_v3(conn, snapshot).matched
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()


def test_v3_readback_detects_distorted_threshold_bind(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()

        def distort(_conn, _cursor, _statement, parameters, context, _executemany):
            compiled = context.compiled
            if (
                compiled is not None
                and isinstance(compiled.statement, Insert)
                and compiled.statement.table.name == "cohorts"
            ):
                parameters["auto_accept_threshold"] = 0.5

        event.listen(conn, "before_cursor_execute", distort)
        try:
            with pytest.raises(SnapshotMismatch) as caught:
                import_snapshot_v3(conn, snapshot)
            assert any(
                item.column == "auto_accept_threshold"
                for item in caught.value.mismatches
            )
            assert conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", distort)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v3_new_table_keys_and_current_deletes_are_independently_verified(
    transfer_database, table
):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v3(conn, initial)
        row = getattr(initial, table)[0]
        # Snapshot key mutations exercise both directions without invalid FK SQL.
        for rows in (
            getattr(initial, table)[1:],
            (replace(row, id=uuid4()),) + getattr(initial, table)[1:],
        ):
            with pytest.raises(SnapshotMismatch):
                verify_snapshot_v3(conn, replace(initial, **{table: rows}))
        relation = RELATIONS[table]
        if table in {"tracks", "cohort_tracks"}:
            conn.execute(RELATIONS["enrollments"].update().values(track_id=None))
        conn.execute(relation.delete().where(relation.c.id == row.id))
        current = export_snapshot_v3(conn, tenants)
        assert row.id not in {item.id for item in getattr(current, table)}
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v3(conn, initial)
        _clear(conn)
        assert import_snapshot_v3(conn, current).verification.matched
        assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(current)


@pytest.mark.parametrize("operation", ["export", "import", "verify"])
def test_v3_inactive_root_refuses_before_any_sql_or_autobegin(
    transfer_database, operation
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        statements = []

        def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(conn, "before_cursor_execute", observe)
        try:
            with pytest.raises(TransferConnectionError):
                if operation == "export":
                    export_snapshot_v3(conn, tenants)
                elif operation == "import":
                    import_snapshot_v3(conn, snapshot)
                else:
                    verify_snapshot_v3(conn, snapshot)
            assert statements == [] and conn.get_transaction() is None
        finally:
            event.remove(conn, "before_cursor_execute", observe)


@pytest.mark.parametrize("nested", [False, True])
def test_v3_detects_caller_transaction_replacement(transfer_database, nested):
    owner, _, tenants, people = transfer_database
    with owner.connect() as conn:
        root = conn.begin()
        inner = conn.begin_nested() if nested else None
        replaced = []

        def replace_transaction(
            _conn, _cursor, _statement, _parameters, _context, _executemany
        ):
            if not replaced:
                replaced.append(True)
                if inner is not None:
                    inner.rollback()
                else:
                    root.rollback()
                    conn.begin()

        event.listen(conn, "after_cursor_execute", replace_transaction)
        try:
            with pytest.raises(TransferConnectionError):
                export_snapshot_v3(conn, tenants)
            assert replaced == [True]
        finally:
            event.remove(conn, "after_cursor_execute", replace_transaction)
            conn.rollback()


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v3_rejects_each_new_catalog_drift_before_any_insert(transfer_database, table):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        conn.execute(
            DDL("ALTER TABLE %(fullname)s ADD COLUMN unexpected integer").against(
                RELATIONS[table]
            )
        )
        inserts, listener = _inserts(conn)
        try:
            for operation in (
                lambda: export_snapshot_v3(conn, tenants),
                lambda: import_snapshot_v3(conn, snapshot),
                lambda: verify_snapshot_v3(conn, snapshot),
            ):
                with pytest.raises(TransferConnectionError):
                    operation()
                assert inserts == [] and conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()


@pytest.mark.parametrize(
    "table,column",
    [
        ("enrollments", "access_ends_at"),
        ("course_offerings", "starts_at"),
        ("course_offerings", "ends_at"),
    ],
)
def test_v3_new_nullable_timestamp_preflight_precedes_old_inserts(
    transfer_database, table, column
):
    from datetime import timezone

    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    snapshot = replace(
        snapshot,
        **{
            table: (
                replace(
                    rows[0],
                    **{column: datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1)))},
                ),
            )
            + rows[1:]
        },
    )
    with owner.connect() as conn:
        root = conn.begin()
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(UnsupportedTimestampRepresentation):
                import_snapshot_v3(conn, snapshot)
            assert inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()


def test_v3_existing_track_without_cohort_link_is_refused_before_any_insert(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = replace(_snapshot(tenants, people), cohort_tracks=())
    with owner.begin() as conn:
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotClosureErrorV3) as caught:
                import_snapshot_v3(conn, snapshot)
            assert caught.value.table == "enrollments"
            assert caught.value.column == "track_id"
            assert inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v3_requires_whole_visibility_of_each_new_table(transfer_database, table):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        actor = _transfer_column_reader(conn)
        # The actor has BYPASSRLS plus every base-table and Kernel-column grant.
        conn.execute(DDL("RESET ROLE"))
        conn.execute(
            DDL(
                "REVOKE SELECT ON %(fullname)s FROM %(actor)s", context={"actor": actor}
            ).against(RELATIONS[table])
        )
        conn.execute(DDL("SET LOCAL ROLE %(actor)s", context={"actor": actor}))
        inserts, listener = _inserts(conn)
        try:
            for operation in (
                lambda: export_snapshot_v3(conn, tenants),
                lambda: import_snapshot_v3(conn, snapshot),
                lambda: verify_snapshot_v3(conn, snapshot),
            ):
                with pytest.raises(TransferConnectionError):
                    operation()
                assert inserts == [] and conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()


def test_v3_independent_readback_refuses_trigger_created_outside_extent(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants[:2], people[:2])
    with owner.connect() as conn:
        root = conn.begin()
        conn.execute(
            text(
                "UPDATE public.tenants SET slug='participation-plant-target' "
                "WHERE id=:id"
            ),
            {"id": tenants[2]},
        )
        conn.execute(
            text("""CREATE FUNCTION public.extra_participation_row() RETURNS trigger
            LANGUAGE plpgsql AS $$
            DECLARE other_tenant uuid;
            BEGIN
              SELECT id INTO other_tenant FROM public.tenants
              WHERE slug='participation-plant-target';
              INSERT INTO mod_learning.tracks(id,tenant_id,slug,name,status)
              VALUES (gen_random_uuid(), other_tenant, gen_random_uuid()::text,
                      'outside', 'historic');
              RETURN NEW;
            END $$""")
        )
        conn.execute(
            DDL(
                "CREATE TRIGGER extra_participation_row AFTER INSERT ON %(fullname)s "
                "FOR EACH ROW EXECUTE FUNCTION public.extra_participation_row()"
            ).against(RELATIONS["cohorts"])
        )
        try:
            with pytest.raises(SnapshotMismatch) as caught:
                import_snapshot_v3(conn, snapshot)
            assert any(
                item.table == "tracks" and item.reason == "outside_extent"
                for item in caught.value.mismatches
            )
            assert conn.get_transaction() is root
        finally:
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}


def test_v1_v2_remain_scoped_when_only_participation_history_changes(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v3(conn, snapshot)
        v1 = export_snapshot(conn, tenants)
        v2 = export_snapshot_v2(conn, tenants)
        conn.execute(RELATIONS["cohorts"].update().values(name="later"))
        assert len(verify_snapshot(conn, v1).row_counts) == 7
        assert len(verify_snapshot_v2(conn, v2).row_counts) == 14
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v3(conn, snapshot)


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v3_new_current_facts_and_updates_restore_exactly(transfer_database, table):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v3(conn, initial)
        original = getattr(initial, table)[0]
        values = {
            field.name: getattr(original, field.name) for field in fields(original)
        }
        values["id"] = uuid4()
        # Satisfy only actual unique/FK/check constraints for a later current fact.
        if table == "tracks":
            values["slug"] = "later"
        elif table in {"enrollments", "cohort_tracks", "course_offerings"}:
            values["cohort_id"] = initial.cohorts[1].id
            values["tenant_id"] = tenants[1]
            if table == "enrollments":
                values["person_id"] = _person(conn, tenants[1])
                values["track_id"] = None
            elif table == "cohort_tracks":
                # Existing pair is already unique; create a fresh track.
                values["track_id"] = uuid4()
                conn.execute(
                    RELATIONS["tracks"]
                    .insert()
                    .values(
                        id=values["track_id"],
                        tenant_id=tenants[1],
                        slug="later",
                        name="later",
                    )
                )
            else:
                # Same-tenant extra course supplies a distinct offering.
                course_id = uuid4()
                conn.execute(
                    RELATIONS["courses"]
                    .insert()
                    .values(
                        id=course_id,
                        tenant_id=tenants[1],
                        slug="later",
                        title="Later",
                        discipline="",
                        source_ref="",
                    )
                )
                values["course_id"] = course_id
        elif table in {"track_courses", "course_prerequisites"}:
            course_id = uuid4()
            conn.execute(
                RELATIONS["courses"]
                .insert()
                .values(
                    id=course_id,
                    tenant_id=tenants[0],
                    slug="later",
                    title="Later",
                    discipline="",
                    source_ref="",
                )
            )
            values["course_id"] = course_id
        if table == "cohorts":
            values["auto_accept_threshold"] = -0.0
        conn.execute(RELATIONS[table].insert().values(values))
        conn.execute(
            RELATIONS[table]
            .update()
            .where(RELATIONS[table].c.id == original.id)
            .values(updated_at=NOW + timedelta(days=3))
        )
        current = export_snapshot_v3(conn, tenants)
        assert values["id"] in {row.id for row in getattr(current, table)}
        assert _keyed(current) != _keyed(initial)
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v3(conn, initial)
        _clear(conn)
        assert import_snapshot_v3(conn, current).verification.matched
        assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(current)


@pytest.mark.parametrize(
    "table,column",
    [
        ("cohorts", "name"),
        ("enrollments", "employee_ref"),
        ("courses", "title"),
        ("questions", "stem"),
        ("chapter_notes", "body"),
    ],
)
@pytest.mark.parametrize("value", ["sensitive_unpaired_\ud800", "sensitive_nul_\x00"])
def test_v3_all_slice_text_representations_refuse_payload_free_before_any_insert(
    transfer_database, table, column, value
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    snapshot = replace(
        snapshot, **{table: (replace(rows[0], **{column: value}),) + rows[1:]}
    )
    pending = uuid4()
    with owner.connect() as conn:
        root = conn.begin()
        _tenant(conn, pending)
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotValidationError) as caught:
                import_snapshot_v3(conn, snapshot)
            assert type(caught.value) is SnapshotValidationError
            assert str(caught.value) == "unsupported storage representation"
            assert caught.value.__cause__ is None and caught.value.__suppress_context__
            assert inserts == [] and set(_counts(conn).values()) == {0}
            assert conn.get_transaction() is root and root.is_active
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                    {"id": pending},
                )
                == 1
            )
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                    {"id": pending},
                )
                == 0
            )


_INTEGER_EXAMPLES = [
    ("courses", "version"),
    ("questions", "weight"),
    ("submissions", "attempt_no"),
    ("cohorts", "entrance_time_limit_minutes"),
    ("track_courses", "order_index"),
]
_VARCHAR_COLUMNS = [
    (name, stored.name, stored.type.length)
    for name, relation in RELATIONS.items()
    for stored in relation.c
    if isinstance(getattr(stored.type, "length", None), int)
]


def _assert_v3_representation_refused_prewrite(transfer_database, table, column, value):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    snapshot = replace(
        snapshot, **{table: (replace(rows[0], **{column: value}),) + rows[1:]}
    )
    pending = uuid4()
    with owner.connect() as conn:
        root = conn.begin()
        _tenant(conn, pending)
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotValidationError) as caught:
                import_snapshot_v3(conn, snapshot)
            assert type(caught.value) is SnapshotValidationError
            assert str(caught.value) == "unsupported storage representation"
            assert caught.value.__cause__ is None and caught.value.__suppress_context__
            assert inserts == [] and set(_counts(conn).values()) == {0}
            assert conn.get_transaction() is root and root.is_active
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                    {"id": pending},
                )
                == 1
            )
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                    {"id": pending},
                )
                == 0
            )


@pytest.mark.parametrize("table,column", _INTEGER_EXAMPLES)
@pytest.mark.parametrize("value", [2**31, -(2**31) - 1])
def test_v3_all_slice_integer_overflow_refuses_payload_free_before_any_insert(
    transfer_database, table, column, value
):
    _assert_v3_representation_refused_prewrite(transfer_database, table, column, value)


@pytest.mark.parametrize("table,column", _INTEGER_EXAMPLES)
@pytest.mark.parametrize("value", [2**31 - 1, -(2**31)])
def test_v3_integer_storage_boundaries_preserve_valid_historical_values(
    transfer_database, table, column, value
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    snapshot = replace(
        snapshot, **{table: (replace(rows[0], **{column: value}),) + rows[1:]}
    )
    with owner.begin() as conn:
        assert import_snapshot_v3(conn, snapshot).verification.matched
        assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(snapshot)
        assert verify_snapshot_v3(conn, snapshot).matched


@pytest.mark.parametrize("table,column,capacity", _VARCHAR_COLUMNS)
@pytest.mark.parametrize("overflow", ["雪", " "])
def test_v3_every_varchar_capacity_refuses_character_and_trailing_space_overflow(
    transfer_database, table, column, capacity, overflow
):
    # PostgreSQL can truncate excess trailing spaces; full storage equality must
    # refuse that before the delegated writer just as it refuses other overflow.
    _assert_v3_representation_refused_prewrite(
        transfer_database, table, column, "雪" * capacity + overflow
    )


@pytest.mark.parametrize(
    "table,column,capacity",
    [
        ("courses", "title", 200),
        ("questions", "ext_id", 60),
        ("chapters", "title", 200),
        ("cohorts", "name", 120),
        ("enrollments", "employee_ref", 64),
        ("tracks", "name", 160),
    ],
)
@pytest.mark.parametrize("last_character", ["雪", " "])
def test_v3_varchar_character_capacity_preserves_multibyte_text_and_trailing_spaces(
    transfer_database, table, column, capacity, last_character
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    value = "雪" * (capacity - 1) + last_character
    snapshot = replace(
        snapshot, **{table: (replace(rows[0], **{column: value}),) + rows[1:]}
    )
    with owner.begin() as conn:
        assert import_snapshot_v3(conn, snapshot).verification.matched
        assert _keyed(export_snapshot_v3(conn, tenants)) == _keyed(snapshot)
        assert verify_snapshot_v3(conn, snapshot).matched
