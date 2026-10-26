"""Hosted fourteen-table snapshot canaries over genuine unchanged lineages.

Package-store restore is not Academy ACK/reference/fence or compatible-assembly
cutover proof. The V1 protocol remains explicitly limited to seven tables.
"""

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from dotmac_learning.curriculum_models import CURRICULUM_MODELS
from dotmac_learning.curriculum_transfer import (
    export_snapshot_v2,
    import_snapshot_v2,
    verify_snapshot_v2,
)
from dotmac_learning.curriculum_transfer_contracts import (
    ChapterBookmarkRowV2,
    ChapterNoteRowV2,
    ChapterReadRowV2,
    ChapterRowV2,
    LearningSnapshotV2,
    QuestionBankRowV2,
    QuestionRowV2,
    SnapshotClosureErrorV2,
    SubtopicReadRowV2,
)
from dotmac_learning.models import TENANT_MODELS
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
from tests.test_learning_transfer import (
    NOW,
    ONE,
    ZERO,
)
from tests.test_learning_transfer import (
    _snapshot as first_snapshot,
)

learning_database = learning_catalog_fixtures.learning_database
transfer_database = learning_transfer_fixtures.transfer_database

NEW_TABLES = (
    "chapters",
    "question_banks",
    "questions",
    "chapter_reads",
    "subtopic_reads",
    "chapter_bookmarks",
    "chapter_notes",
)
TABLES = (
    "courses",
    "activities",
    "submissions",
    "scores",
    "course_completions",
    "certificates",
    "learning_events",
    *NEW_TABLES,
)
RELATIONS = {
    model.__tablename__: model.__table__ for model in TENANT_MODELS + CURRICULUM_MODELS
}


def _snapshot(tenants, people):
    first = first_snapshot(tenants, people)
    additions = {name: [] for name in NEW_TABLES}
    for index, tenant in enumerate(tenants[:2]):
        identity = {
            "tenant_id": tenant,
            "created_at": NOW,
            "updated_at": NOW + timedelta(microseconds=1),
        }
        chapter = ChapterRowV2(
            id=uuid4(),
            course_id=first.courses[index].id,
            number=-9 + index,
            title="原章",
            part="",
            body_html="<p>original</p>\n",
            body_md="# Original\n",
            source_hash="historic",
            order_index=-4,
            **identity,
        )
        bank = QuestionBankRowV2(
            id=uuid4(),
            course_id=uuid4(),
            chapter_number=None if index == 0 else -2,
            kind="historic",
            version=-7,
            **identity,
        )
        additions["chapters"].append(chapter)
        additions["question_banks"].append(bank)
        additions["questions"].append(
            QuestionRowV2(
                id=uuid4(),
                bank_id=bank.id,
                ext_id="original",
                stem="Untrimmed\n",
                type="historic",
                options=JsonbText("null"),
                correct=JsonbText('[12345678901234567890.1234567890, "null", null]'),
                rubric_category="historic",
                category=None if index == 0 else "",
                explanation="",
                weight=-7,
                **identity,
            )
        )
        additions["chapter_reads"].append(
            ChapterReadRowV2(
                id=uuid4(), person_id=uuid4(), chapter_id=chapter.id, **identity
            )
        )
        additions["subtopic_reads"].append(
            SubtopicReadRowV2(
                id=uuid4(),
                person_id=uuid4(),
                chapter_id=chapter.id,
                subtopic_slug="  old-heading  ",
                **identity,
            )
        )
        additions["chapter_bookmarks"].append(
            ChapterBookmarkRowV2(
                id=uuid4(),
                person_id=uuid4(),
                course_id=first.courses[index].id,
                chapter_id=chapter.id,
                **identity,
            )
        )
        additions["chapter_notes"].append(
            ChapterNoteRowV2(
                id=uuid4(),
                person_id=uuid4(),
                course_id=first.courses[index].id,
                chapter_id=chapter.id,
                body="  <raw private note>\n \t",
                **identity,
            )
        )
    return LearningSnapshotV2(
        version=2,
        **{
            field.name: getattr(first, field.name)
            for field in fields(first)
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


def test_v2_restricted_actor_refuses_before_hidden_rows_are_treated_as_empty(
    transfer_database,
):
    owner, app, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v2(conn, snapshot)
    with app.begin() as conn:
        _scope(conn, tenants[2])
        for operation in (
            lambda: export_snapshot_v2(conn, (tenants[2],)),
            lambda: import_snapshot_v2(conn, _empty(snapshot)),
            lambda: verify_snapshot_v2(conn, _empty(snapshot)),
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
    # RD-1 probe (lr_0010): frozen exports must see whether any
    # question_bank_versions row exists in the tenant extent; the
    # exact-column grant below is the probe's only privilege need.
    conn.execute(
        DDL(
            "GRANT SELECT (tenant_id) "
            "ON mod_learning.question_bank_versions TO %(actor)s",
            context=context,
        )
    )
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
def test_v2_bypass_actor_missing_each_kernel_column_refuses_before_insert(
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
                lambda: export_snapshot_v2(conn, tenants),
                lambda: import_snapshot_v2(conn, snapshot),
                lambda: verify_snapshot_v2(conn, _empty(snapshot)),
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


def test_v2_bypass_actor_needs_only_exact_kernel_fk_column_grants(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        actor = _transfer_column_reader(conn)
        try:
            assert import_snapshot_v2(conn, snapshot).verification.matched
            assert verify_snapshot_v2(conn, snapshot).matched
            assert _keyed(export_snapshot_v2(conn, tenants)) == _keyed(snapshot)
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


def test_v2_two_tenant_all_fields_roundtrip_zero_tenant_and_v1_scope_are_distinct(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn, conn.begin() as root, conn.begin_nested() as nested:
        observed = import_snapshot_v2(conn, snapshot)
        assert observed.verification.version == 2 and observed.verification.matched
        assert len(observed.verification.row_counts) == len(TABLES) == 14
        assert _keyed(export_snapshot_v2(conn, tenants)) == _keyed(snapshot)
        assert verify_snapshot_v2(conn, snapshot).matched
        assert export_snapshot_v2(conn, (tenants[2],)) == replace(
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
            verify_snapshot_v2(conn, snapshot)


def test_v2_all_fourteen_empty_is_valid_for_a_genuine_zero_row_tenant(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = replace(_empty(_snapshot(tenants, people)), tenant_ids=(tenants[2],))
    with owner.begin() as conn:
        assert import_snapshot_v2(conn, snapshot).verification.matched
        assert export_snapshot_v2(conn, (tenants[2],)) == snapshot


@pytest.mark.parametrize("table", ["courses", "question_banks"])
def test_v2_refuses_partial_destination_across_any_tenant_and_all_fourteen_tables(
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
        else:
            conn.execute(
                RELATIONS[table]
                .insert()
                .values(
                    id=uuid4(), tenant_id=tenants[2], course_id=uuid4(), kind="historic"
                )
            )
        with pytest.raises(DestinationNotEmpty):
            import_snapshot_v2(conn, _snapshot(tenants[:2], people[:2]))


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
            import_snapshot_v2(conn, snapshot)
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
            with pytest.raises(SnapshotClosureErrorV2) as caught:
                import_snapshot_v2(conn, snapshot)
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
            with pytest.raises(SnapshotClosureErrorV2):
                import_snapshot_v2(conn, snapshot)
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
        assert import_snapshot_v2(conn, snapshot).verification.matched
        assert _keyed(export_snapshot_v2(conn, tenants)) == _keyed(snapshot)


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
        import_snapshot_v2(conn, snapshot)
        selected = export_snapshot_v2(conn, (tenants[0],))
        with pytest.raises(SnapshotMismatch) as caught:
            verify_snapshot_v2(conn, selected)
        assert any(item.reason == "outside_extent" for item in caught.value.mismatches)
        _clear(conn)
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotClosureErrorV2):
                import_snapshot_v2(conn, selected)
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
        assert import_snapshot_v2(conn, snapshot).verification.matched
        assert _keyed(export_snapshot_v2(conn, tenants)) == _keyed(snapshot)


@pytest.mark.parametrize("mutation", ["timestamp_overflow"])
def test_all_fourteen_representations_are_preflighted_before_the_first_old_insert(
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
                import_snapshot_v2(conn, snapshot)
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
                import_snapshot_v2(conn, snapshot)
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
                import_snapshot_v2(conn, snapshot)
            except UnsupportedFloatRepresentation:
                assert inserts == []
            else:
                actual = export_snapshot_v2(conn, tenants)
                assert {row.id: row.weight for row in actual.activities}[
                    snapshot.activities[0].id
                ] == Float8Bits(bits)
                assert verify_snapshot_v2(conn, snapshot).matched
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}


@pytest.mark.parametrize(
    "mode", ["execution_option", "engine_default", "direct_driver"]
)
def test_v2_physical_autocommit_refusal_precedes_all_sql_for_all_three_apis(
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
                    lambda: export_snapshot_v2(conn, tenants),
                    lambda: import_snapshot_v2(conn, snapshot),
                    lambda: verify_snapshot_v2(conn, snapshot),
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
        import_snapshot_v2(conn, snapshot)
        conn.execute(
            text(
                "UPDATE mod_learning.chapter_notes "
                "SET updated_at=CAST(:value AS timestamptz) WHERE id=:id"
            ),
            {"value": stored, "id": snapshot.chapter_notes[0].id},
        )
        for operation in (
            lambda: export_snapshot_v2(conn, tenants),
            lambda: verify_snapshot_v2(conn, snapshot),
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
        assert import_snapshot_v2(conn, snapshot).verification.matched
        utc_snapshot = replace(
            snapshot,
            chapter_notes=(replace(row, updated_at=first.astimezone(UTC)),)
            + snapshot.chapter_notes[1:],
        )
        assert verify_snapshot_v2(conn, utc_snapshot).matched
        plant = replace(
            snapshot,
            chapter_notes=(replace(row, updated_at=first.replace(fold=1)),)
            + snapshot.chapter_notes[1:],
        )
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v2(conn, plant)


def test_independent_verifier_detects_same_count_mutations_of_every_135_column(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v2(conn, snapshot)
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
                    verify_snapshot_v2(conn, planted)
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
        import_snapshot_v2(conn, snapshot)
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
            verify_snapshot_v2(conn, snapshot)


def test_export_all_current_old_and_new_facts_after_mutations_restores_empty_store(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v2(conn, initial)
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
        current = export_snapshot_v2(conn, tenants)
        assert _keyed(current) != _keyed(initial)
    with owner.connect() as conn:
        root = conn.begin()
        _clear(conn)
        assert import_snapshot_v2(conn, current).verification.matched
        assert _keyed(export_snapshot_v2(conn, tenants)) == _keyed(current)
        root.rollback()
        with conn.begin():
            assert _keyed(export_snapshot_v2(conn, tenants)) == _keyed(current)
