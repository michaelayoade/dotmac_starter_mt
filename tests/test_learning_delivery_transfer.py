"""Hosted real-lineage V5 canaries, authored first and never locally run."""

import struct
from dataclasses import fields, replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_learning.delivery_models import DELIVERY_MODELS
from dotmac_learning.delivery_transfer import (
    export_snapshot_v5,
    import_snapshot_v5,
    verify_snapshot_v5,
)
from dotmac_learning.delivery_transfer_contracts import (
    AnnouncementRowV5,
    ClassSessionRowV5,
    LearningSnapshotV5,
    OfferingActivityRowV5,
    SnapshotClosureErrorV5,
    SuccessQueueEntryRowV5,
)
from dotmac_learning.transfer_contracts import (
    DestinationNotEmpty,
    Float8Bits,
    JsonbText,
    SnapshotMismatch,
    SnapshotValidationError,
    TransferConnectionError,
    UnsupportedTimestampRepresentation,
)
from sqlalchemy import (
    DDL,
    DateTime,
    bindparam,
    create_engine,
    event,
    func,
    select,
    text,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.dml import Insert

from tests import test_learning_assessment_transfer as previous
from tests.test_learning_catalog import _person, _scope, _tenant
from tests.test_learning_transfer import NOW, ONE, ZERO

learning_database = previous.learning_database
transfer_database = previous.transfer_database
NEW_TABLES = (
    "offering_activities",
    "class_sessions",
    "announcements",
    "success_queue_entries",
)
TABLES = (*previous.TABLES, *NEW_TABLES)
RELATIONS = {
    **previous.RELATIONS,
    **{m.__tablename__: m.__table__ for m in DELIVERY_MODELS},
}


def _snapshot(tenants, people):
    base = previous._snapshot(tenants, people)
    additions = {name: [] for name in NEW_TABLES}
    for index, tenant in enumerate(tenants[:2]):
        identity = {
            "id": uuid4(),
            "tenant_id": tenant,
            "created_at": NOW,
            "updated_at": NOW + timedelta(microseconds=1),
        }
        activity = next(r for r in base.activities if r.tenant_id == tenant)
        cohort = base.cohorts[index]
        offering = base.course_offerings[index]
        additions["offering_activities"].append(
            OfferingActivityRowV5(
                **identity,
                offering_id=offering.id,
                activity_id=activity.id,
                release_at=None,
                due_at=NOW,
            )
        )
        identity["id"] = uuid4()
        additions["class_sessions"].append(
            ClassSessionRowV5(
                **identity,
                cohort_id=cohort.id,
                offering_id=uuid4(),
                session_type="historic",
                title="  原 title  ",
                starts_at=NOW,
                ends_at=None,
                instructor_person_id=uuid4(),
                location=None,
                join_url=None,
                notes=None,
                status="historic",
            )
        )
        identity["id"] = uuid4()
        additions["announcements"].append(
            AnnouncementRowV5(
                **identity,
                cohort_id=None,
                author_person_id=people[index],
                title="title",
                body_md="  原 markdown  ",
                body_html="<p>historic</p>",
            )
        )
        identity["id"] = uuid4()
        additions["success_queue_entries"].append(
            SuccessQueueEntryRowV5(
                **identity,
                person_id=people[index],
                cohort_id=uuid4(),
                reason_kind="historic",
                supporting_facts=JsonbText("null"),
                severity="historic",
                detected_at=NOW,
                assigned_to=uuid4(),
                recommended_action="  原 action  ",
                status="open",
                resolved_at=None,
                resolved_by=uuid4(),
                score_hint=Float8Bits("8000000000000000"),
            )
        )
    return LearningSnapshotV5(
        version=5,
        **{f.name: getattr(base, f.name) for f in fields(base) if f.name != "version"},
        **{name: tuple(rows) for name, rows in additions.items()},
    )


def _empty(snapshot):
    return replace(snapshot, **{name: () for name in TABLES})


def _counts(conn):
    return {
        name: conn.scalar(select(func.count()).select_from(RELATIONS[name]))
        for name in TABLES
    }


def _clear(conn):
    for name in reversed(TABLES):
        conn.execute(RELATIONS[name].delete())


_inserts = previous._inserts


def test_v5_restricted_actor_refuses_before_hidden_rows_are_treated_as_empty(
    transfer_database,
):
    owner, app, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v5(conn, snapshot)
    with app.begin() as conn:
        _scope(conn, tenants[2])
        for operation in (
            lambda: export_snapshot_v5(conn, (tenants[2],)),
            lambda: import_snapshot_v5(conn, _empty(snapshot)),
            lambda: verify_snapshot_v5(conn, _empty(snapshot)),
        ):
            with pytest.raises(TransferConnectionError):
                operation()


def test_independent_verifier_detects_same_count_mutations_of_every_260_column(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v5(conn, snapshot)
        assert sum(len(fields(getattr(snapshot, table)[0])) for table in TABLES) == 260
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
                    elif field.name in {
                        "bank_id",
                        "course_id",
                        "subject_id",
                        "granted_by",
                        "cohort_id",
                        "assigned_to",
                        "resolved_by",
                        "instructor_person_id",
                        "offering_id",
                    }:
                        changed = uuid4()
                    elif field.name in {
                        "completed_at",
                        "submitted_at",
                        "release_at",
                        "due_at",
                        "ends_at",
                        "resolved_at",
                    }:
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
                    verify_snapshot_v5(conn, planted)
                assert any(
                    item.table == table
                    and (item.column == field.name or field.name == "id")
                    for item in caught.value.mismatches
                )


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v5_new_table_keys_and_current_deletes_are_independently_verified(
    transfer_database, table
):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v5(conn, initial)
        row = getattr(initial, table)[0]
        # Snapshot key mutations exercise both directions without invalid FK SQL.
        for rows in (
            getattr(initial, table)[1:],
            (replace(row, id=uuid4()),) + getattr(initial, table)[1:],
        ):
            with pytest.raises(SnapshotMismatch):
                verify_snapshot_v5(conn, replace(initial, **{table: rows}))
        relation = RELATIONS[table]
        if table in {"tracks", "cohort_tracks"}:
            conn.execute(RELATIONS["enrollments"].update().values(track_id=None))
        conn.execute(relation.delete().where(relation.c.id == row.id))
        current = export_snapshot_v5(conn, tenants)
        assert row.id not in {item.id for item in getattr(current, table)}
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v5(conn, initial)
        _clear(conn)
        assert import_snapshot_v5(conn, current).verification.matched
        assert _keyed(export_snapshot_v5(conn, tenants)) == _keyed(current)


@pytest.mark.parametrize("operation", ["export", "import", "verify"])
def test_v5_inactive_root_refuses_before_any_sql_or_autobegin(
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
                    export_snapshot_v5(conn, tenants)
                elif operation == "import":
                    import_snapshot_v5(conn, snapshot)
                else:
                    verify_snapshot_v5(conn, snapshot)
            assert statements == [] and conn.get_transaction() is None
        finally:
            event.remove(conn, "before_cursor_execute", observe)


@pytest.mark.parametrize("nested", [False, True])
def test_v5_detects_caller_transaction_replacement(transfer_database, nested):
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
                export_snapshot_v5(conn, tenants)
            assert replaced == [True]
        finally:
            event.remove(conn, "after_cursor_execute", replace_transaction)
            conn.rollback()


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v5_rejects_each_new_catalog_drift_before_any_insert(transfer_database, table):
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
                lambda: export_snapshot_v5(conn, tenants),
                lambda: import_snapshot_v5(conn, snapshot),
                lambda: verify_snapshot_v5(conn, snapshot),
            ):
                with pytest.raises(TransferConnectionError):
                    operation()
                assert inserts == [] and conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v5_requires_whole_visibility_of_each_new_table(transfer_database, table):
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
                lambda: export_snapshot_v5(conn, tenants),
                lambda: import_snapshot_v5(conn, snapshot),
                lambda: verify_snapshot_v5(conn, snapshot),
            ):
                with pytest.raises(TransferConnectionError):
                    operation()
                assert inserts == [] and conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v5_new_only_destination_and_outside_extent_refuse(transfer_database, table):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        # Deliberately plant one orphaned destination row with the superuser
        # fixture. Only this new table is nonempty: the whole-store guard must
        # refuse it before relying on FK closure or selected-tenant emptiness.
        row = getattr(snapshot, table)[0]
        values, parameters = _raw_values(row)
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            RELATIONS[table].insert().values(values),
            parameters,
        )
        conn.execute(text("SET LOCAL session_replication_role = origin"))
        assert all(
            count == (1 if name == table else 0)
            for name, count in _counts(conn).items()
        )
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(DestinationNotEmpty):
                import_snapshot_v5(
                    conn, replace(_empty(snapshot), tenant_ids=(tenants[2],))
                )
            assert inserts == []
            with pytest.raises(SnapshotMismatch) as caught:
                verify_snapshot_v5(
                    conn, replace(_empty(snapshot), tenant_ids=(tenants[2],))
                )
            assert any(
                m.table == table and m.reason == "outside_extent"
                for m in caught.value.mismatches
            )
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v5_current_new_facts_updates_and_deletes_restore(transfer_database, table):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v5(conn, initial)
        original = getattr(initial, table)[0]
        # Independent new fact after deletion avoids inventing a second unique
        # pacing key and retains every actual reference from the genuine fixture.
        conn.execute(
            RELATIONS[table].delete().where(RELATIONS[table].c.id == original.id)
        )
        new = replace(original, id=uuid4(), updated_at=NOW + timedelta(seconds=1))
        values, parameters = _raw_values(new)
        conn.execute(RELATIONS[table].insert().values(values), parameters)
        survivor = getattr(initial, table)[-1]
        changed = {"updated_at": NOW + timedelta(seconds=2)}
        if table == "offering_activities":
            changed["due_at"] = NOW + timedelta(days=1)
        elif table == "success_queue_entries":
            changed.update(
                status="resolved", resolved_at=NOW, recommended_action="current"
            )
        else:
            changed["title"] = "current"
        conn.execute(
            RELATIONS[table]
            .update()
            .where(RELATIONS[table].c.id == survivor.id)
            .values(changed)
        )
        current = export_snapshot_v5(conn, tenants)
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v5(conn, initial)
        _clear(conn)
        assert import_snapshot_v5(conn, current).verification.matched
        assert _keyed(export_snapshot_v5(conn, tenants)) == _keyed(current)


_KERNEL_COLUMN_GRANTS = previous._KERNEL_COLUMN_GRANTS


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


def _keyed(snapshot):
    return {name: {row.id: row for row in getattr(snapshot, name)} for name in TABLES}


def _raw_values(row):
    values = {f.name: getattr(row, f.name) for f in fields(row)}
    parameters = {}
    for name, value in tuple(values.items()):
        if isinstance(value, JsonbText):
            values[name] = sql_cast(bindparam("raw_" + name), JSONB)
            parameters["raw_" + name] = str(value)
        elif isinstance(value, Float8Bits):
            values[name] = struct.unpack("!d", bytes.fromhex(value.hex))[0]
    return values, parameters


@pytest.mark.parametrize(
    "table,column,parent",
    [
        ("offering_activities", "offering_id", "course_offerings"),
        ("offering_activities", "activity_id", "activities"),
        ("class_sessions", "cohort_id", "cohorts"),
        ("announcements", "cohort_id", "cohorts"),
        ("announcements", "author_person_id", "people"),
        ("success_queue_entries", "person_id", "people"),
    ],
)
@pytest.mark.parametrize("cross_tenant", [False, True])
def test_actual_new_fk_closure_refuses_before_any_insert(
    transfer_database, table, column, parent, cross_tenant
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    value = (
        (people[1] if parent == "people" else getattr(snapshot, parent)[-1].id)
        if cross_tenant
        else uuid4()
    )
    rows = getattr(snapshot, table)
    bad = replace(
        snapshot, **{table: (replace(rows[0], **{column: value}),) + rows[1:]}
    )
    with owner.begin() as conn:
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotClosureErrorV5) as caught:
                import_snapshot_v5(conn, bad)
            assert caught.value.table == table and caught.value.column == column
            assert inserts == [] and all(v == 0 for v in _counts(conn).values())
        finally:
            event.remove(conn, "before_cursor_execute", listener)


# Independent source key census: no transfer descriptors or ORM constraints form
# this oracle. An extra id preserves strict snapshot key validity.
_SOURCE_KEYS = (
    ("courses", ("tenant_id", "slug")),
    ("submissions", ("tenant_id", "activity_id", "person_id", "attempt_no")),
    ("course_completions", ("tenant_id", "person_id", "course_id")),
    ("certificates", ("tenant_id", "person_id", "course_id")),
    ("certificates", ("tenant_id", "serial")),
    ("chapters", ("tenant_id", "course_id", "number")),
    ("questions", ("tenant_id", "bank_id", "ext_id")),
    ("chapter_reads", ("tenant_id", "person_id", "chapter_id")),
    ("subtopic_reads", ("tenant_id", "person_id", "chapter_id", "subtopic_slug")),
    ("chapter_bookmarks", ("tenant_id", "person_id", "chapter_id")),
    ("chapter_notes", ("tenant_id", "person_id", "chapter_id")),
    ("enrollments", ("tenant_id", "cohort_id", "person_id")),
    ("tracks", ("tenant_id", "slug")),
    ("track_courses", ("tenant_id", "track_id", "course_id")),
    ("cohort_tracks", ("tenant_id", "cohort_id", "track_id")),
    ("course_offerings", ("tenant_id", "cohort_id", "course_id")),
    ("course_prerequisites", ("tenant_id", "course_id", "requires_course_id")),
    ("offering_activities", ("tenant_id", "offering_id", "activity_id")),
    ("activity_attempts", ("tenant_id", "activity_id", "person_id")),
    ("success_queue_entries", ("tenant_id", "person_id", "reason_kind")),
    ("learning_events", ("tenant_id", "person_id", "kind", "subject_id")),
)


@pytest.mark.parametrize("table,columns", _SOURCE_KEYS)
def test_all_source_unique_keys_preflight_before_first_old_insert(
    transfer_database, table, columns
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    row = rows[0]
    if table == "activity_attempts":
        row = replace(row, submitted_at=None)
    if table == "learning_events":
        row = replace(row, kind="chapter_completed", subject_id=uuid4())
    snapshot = replace(snapshot, **{table: (row,) + rows[1:]})
    duplicate = replace(row, id=uuid4())
    assert all(getattr(row, column) == getattr(duplicate, column) for column in columns)
    snapshot = replace(snapshot, **{table: (*getattr(snapshot, table), duplicate)})
    with owner.begin() as conn:
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(
                SnapshotValidationError, match="duplicate source unique key"
            ):
                import_snapshot_v5(conn, snapshot)
            assert inserts == [] and all(v == 0 for v in _counts(conn).values())
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize("status", ["acknowledged", "resolved", "historic"])
def test_partial_queue_unique_allows_closed_historical_duplicates(
    transfer_database, status
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    row = replace(snapshot.success_queue_entries[0], status=status)
    snapshot = replace(snapshot, success_queue_entries=(row, replace(row, id=uuid4())))
    with owner.begin() as conn:
        assert import_snapshot_v5(conn, snapshot).verification.matched


def test_event_partial_unique_preserves_null_distinct_semantics(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    row = replace(
        snapshot.learning_events[0], kind="chapter_completed", subject_id=None
    )
    snapshot = replace(snapshot, learning_events=(row, replace(row, id=uuid4())))
    with owner.begin() as conn:
        assert import_snapshot_v5(conn, snapshot).verification.matched


@pytest.mark.parametrize("raw", ["null", "false", "-7", '"history"', "[]", '{"a": 1}'])
def test_supporting_facts_every_json_storage_kind_roundtrips(transfer_database, raw):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    row, other = snapshot.success_queue_entries
    snapshot = replace(
        snapshot,
        success_queue_entries=(replace(row, supporting_facts=JsonbText(raw)), other),
    )
    with owner.begin() as conn:
        assert import_snapshot_v5(conn, snapshot).verification.matched
        actual = conn.scalar(
            text(
                "SELECT supporting_facts::text FROM "
                "mod_learning.success_queue_entries WHERE id=:id"
            ),
            {"id": row.id},
        )
        assert actual == raw


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("class_sessions", "title", "sensitive\x00"),
        ("announcements", "body_html", "sensitive\ud800"),
        ("success_queue_entries", "reason_kind", "雪" * 31),
        ("class_sessions", "join_url", " " * 501),
        ("success_queue_entries", "supporting_facts", JsonbText('{"sensitive":')),
    ],
)
def test_new_representations_refuse_without_payload_before_any_insert(
    transfer_database, table, column, value
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    snapshot = replace(
        snapshot, **{table: (replace(rows[0], **{column: value}),) + rows[1:]}
    )
    with owner.connect() as conn:
        root = conn.begin()
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotValidationError) as caught:
                import_snapshot_v5(conn, snapshot)
            assert "sensitive" not in str(caught.value)
            assert inserts == [] and conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()


@pytest.mark.parametrize("nested", [False, True])
def test_caller_owned_root_and_nested_roundtrip_without_outcomes(
    transfer_database, nested
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        inner = conn.begin_nested() if nested else None
        assert import_snapshot_v5(conn, snapshot).verification.matched
        assert conn.get_transaction() is root and conn.get_nested_transaction() is inner
        assert _keyed(export_snapshot_v5(conn, tenants)) == _keyed(snapshot)
        if inner is not None:
            inner.rollback()
            assert all(v == 0 for v in _counts(conn).values())
        root.rollback()


@pytest.mark.parametrize("operation", ["export", "import", "verify"])
def test_physical_autocommit_refuses_before_all_sql(transfer_database, operation):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        root = conn.begin()
        statements = []

        def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(conn, "before_cursor_execute", observe)
        try:
            with pytest.raises(TransferConnectionError):
                if operation == "export":
                    export_snapshot_v5(conn, tenants)
                elif operation == "import":
                    import_snapshot_v5(conn, snapshot)
                else:
                    verify_snapshot_v5(conn, snapshot)
            assert statements == []
        finally:
            event.remove(conn, "before_cursor_execute", observe)
            root.rollback()


@pytest.mark.parametrize(
    "table,column",
    [("announcements", "author_person_id"), ("success_queue_entries", "person_id")],
)
def test_new_person_fks_require_actual_profile(transfer_database, table, column):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        unprofiled = _person(conn, tenants[0], subtype=False)
        rows = getattr(snapshot, table)
        bad = replace(
            snapshot, **{table: (replace(rows[0], **{column: unprofiled}),) + rows[1:]}
        )
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotClosureErrorV5):
                import_snapshot_v5(conn, bad)
            assert inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("success_queue_entries", "supporting_facts", "false"),
        ("success_queue_entries", "score_hint", 0.0),
        ("class_sessions", "starts_at", NOW + timedelta(microseconds=1)),
        ("announcements", "body_html", "<p>distorted</p>"),
    ],
)
def test_independent_new_readback_detects_distorted_binds(
    transfer_database, table, column, value
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()

        def distort(_conn, _cursor, _statement, parameters, context, _executemany):
            compiled = context.compiled
            if (
                compiled is not None
                and isinstance(compiled.statement, Insert)
                and compiled.statement.table.name == table
            ):
                parameters[column] = value

        event.listen(conn, "before_cursor_execute", distort)
        try:
            with pytest.raises(SnapshotMismatch) as caught:
                import_snapshot_v5(conn, snapshot)
            assert any(
                m.table == table and m.column == column for m in caught.value.mismatches
            )
        finally:
            event.remove(conn, "before_cursor_execute", distort)
            root.rollback()
        with conn.begin():
            assert all(v == 0 for v in _counts(conn).values())


@pytest.mark.parametrize("stored", ["infinity", "-infinity", "10000-01-01 00:00:00+00"])
@pytest.mark.parametrize(
    "table,column",
    [
        ("offering_activities", "release_at"),
        ("class_sessions", "ends_at"),
        ("success_queue_entries", "resolved_at"),
    ],
)
def test_nullable_timestamp_binary_transport_refuses_non_native_history(
    transfer_database, stored, table, column
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v5(conn, snapshot)
        relation = RELATIONS[table]
        conn.execute(
            relation.update().values(
                {column: sql_cast(bindparam("raw"), DateTime(timezone=True))}
            ),
            {"raw": stored},
        )
        for operation in (
            lambda: export_snapshot_v5(conn, tenants),
            lambda: verify_snapshot_v5(conn, snapshot),
        ):
            with pytest.raises(UnsupportedTimestampRepresentation):
                operation()


@pytest.mark.parametrize(
    "bits",
    [
        "0000000000000000",
        "8000000000000000",
        "7ff0000000000000",
        "fff0000000000000",
        "7ff8000000000000",
    ],
)
def test_score_hint_binary_float_roundtrips_exactly(transfer_database, bits):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    row, other = snapshot.success_queue_entries
    snapshot = replace(
        snapshot,
        success_queue_entries=(replace(row, score_hint=Float8Bits(bits)), other),
    )
    with owner.begin() as conn:
        assert import_snapshot_v5(conn, snapshot).verification.matched
        assert (
            conn.scalar(
                text(
                    "SELECT encode(float8send(score_hint), 'hex') FROM "
                    "mod_learning.success_queue_entries WHERE id=:id"
                ),
                {"id": row.id},
            )
            == bits
        )


def test_old_mismatch_does_not_skip_new_tables(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v5(conn, snapshot)
        conn.execute(RELATIONS["courses"].update().values(title="changed"))
        for table in NEW_TABLES:
            conn.execute(RELATIONS[table].delete())
        with pytest.raises(SnapshotMismatch) as caught:
            verify_snapshot_v5(conn, snapshot)
        assert {m.table for m in caught.value.mismatches} >= {"courses", *NEW_TABLES}


@pytest.mark.parametrize(
    "missing",
    [
        ("public.parties", "id"),
        ("public.parties", "tenant_id"),
        ("public.party_persons", "party_id"),
    ],
)
def test_v5_bypass_actor_missing_each_kernel_column_refuses_before_insert(
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
                lambda: export_snapshot_v5(conn, tenants),
                lambda: import_snapshot_v5(conn, snapshot),
                lambda: verify_snapshot_v5(conn, _empty(snapshot)),
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


def test_v5_bypass_actor_needs_only_exact_kernel_fk_column_grants(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        actor = _transfer_column_reader(conn)
        try:
            assert import_snapshot_v5(conn, snapshot).verification.matched
            assert verify_snapshot_v5(conn, snapshot).matched
            assert _keyed(export_snapshot_v5(conn, tenants)) == _keyed(snapshot)
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


@pytest.mark.parametrize("table", NEW_TABLES)
def test_each_new_table_failure_propagates_and_root_rollback_restores_all(
    transfer_database, table
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        conn.execute(
            text(
                "CREATE FUNCTION public.fail_delivery_transfer() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'injected failure'; END $$"
            )
        )
        conn.execute(
            DDL(
                "CREATE TRIGGER fail_delivery_transfer BEFORE INSERT "
                "ON %(fullname)s FOR EACH ROW "
                "EXECUTE FUNCTION public.fail_delivery_transfer()"
            ).against(RELATIONS[table])
        )
    pending = uuid4()
    with owner.connect() as conn:
        root = conn.begin()
        _tenant(conn, pending)
        with pytest.raises(DBAPIError):
            import_snapshot_v5(conn, snapshot)
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
    "mode", ["execution_option", "engine_default", "direct_driver"]
)
def test_v5_physical_autocommit_refusal_precedes_all_sql_for_all_three_apis(
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
                    lambda: export_snapshot_v5(conn, tenants),
                    lambda: import_snapshot_v5(conn, snapshot),
                    lambda: verify_snapshot_v5(conn, snapshot),
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
