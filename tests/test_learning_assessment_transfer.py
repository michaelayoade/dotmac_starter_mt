"""Hosted CURRENT 23-table canaries; no cutover or completeness authority."""

from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from dotmac_learning.assessment_transfer import (
    export_snapshot_v4,
    import_snapshot_v4,
    verify_snapshot_v4,
)
from dotmac_learning.assessment_transfer_contracts import (
    ActivityAttemptRowV4,
    AttemptGrantRowV4,
    LearningSnapshotV4,
    SnapshotClosureErrorV4,
)
from dotmac_learning.attempt_models import ATTEMPT_MODELS
from dotmac_learning.curriculum_transfer import export_snapshot_v2, verify_snapshot_v2
from dotmac_learning.participation_transfer import (
    export_snapshot_v3,
    verify_snapshot_v3,
)
from dotmac_learning.transfer import export_snapshot, verify_snapshot
from dotmac_learning.transfer_contracts import (
    DestinationNotEmpty,
    Float8Bits,
    JsonbText,
    SnapshotMismatch,
    SnapshotValidationError,
    TransferConnectionError,
    UnsupportedTimestampRepresentation,
)
from sqlalchemy import DDL, bindparam, create_engine, event, func, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.dml import Insert

from tests import test_learning_participation_transfer as previous
from tests.test_learning_catalog import _scope, _tenant
from tests.test_learning_transfer import NOW, ONE, ZERO

learning_database = previous.learning_database
transfer_database = previous.transfer_database
NEW_TABLES = ("activity_attempts", "attempt_grants")
TABLES = (*previous.TABLES, *NEW_TABLES)
RELATIONS = {
    **previous.RELATIONS,
    **{m.__tablename__: m.__table__ for m in ATTEMPT_MODELS},
}


def _snapshot(tenants, people):
    base = previous._snapshot(tenants, people)
    attempts, grants = [], []
    for activity in base.activities[:2]:
        identity = {
            "tenant_id": activity.tenant_id,
            "created_at": NOW,
            "updated_at": NOW + timedelta(microseconds=1),
        }
        logical_person = uuid4()
        open_row = ActivityAttemptRowV4(
            id=uuid4(),
            activity_id=activity.id,
            person_id=logical_person,
            question_ext_ids=JsonbText('["q2", "q1"]'),
            started_at=NOW,
            submitted_at=None,
            **identity,
        )
        attempts.extend(
            (
                open_row,
                replace(
                    open_row, id=uuid4(), submitted_at=NOW + timedelta(microseconds=2)
                ),
            )
        )
        grants.extend(
            (
                AttemptGrantRowV4(
                    id=uuid4(),
                    activity_id=activity.id,
                    person_id=logical_person,
                    extra_attempts=-7,
                    reason="  原 reason\n ",
                    granted_by=None,
                    **identity,
                ),
                AttemptGrantRowV4(
                    id=uuid4(),
                    activity_id=activity.id,
                    person_id=uuid4(),
                    extra_attempts=0,
                    reason="",
                    granted_by=uuid4(),
                    **identity,
                ),
            )
        )
    return LearningSnapshotV4(
        version=4,
        **{f.name: getattr(base, f.name) for f in fields(base) if f.name != "version"},
        activity_attempts=tuple(attempts),
        attempt_grants=tuple(grants),
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


def test_v4_restricted_actor_refuses_before_hidden_rows_are_treated_as_empty(
    transfer_database,
):
    owner, app, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v4(conn, snapshot)
    with app.begin() as conn:
        _scope(conn, tenants[2])
        for operation in (
            lambda: export_snapshot_v4(conn, (tenants[2],)),
            lambda: import_snapshot_v4(conn, _empty(snapshot)),
            lambda: verify_snapshot_v4(conn, _empty(snapshot)),
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
def test_v4_bypass_actor_missing_each_kernel_column_refuses_before_insert(
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
                lambda: export_snapshot_v4(conn, tenants),
                lambda: import_snapshot_v4(conn, snapshot),
                lambda: verify_snapshot_v4(conn, _empty(snapshot)),
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


def test_v4_bypass_actor_needs_only_exact_kernel_fk_column_grants(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        actor = _transfer_column_reader(conn)
        try:
            assert import_snapshot_v4(conn, snapshot).verification.matched
            assert verify_snapshot_v4(conn, snapshot).matched
            assert _keyed(export_snapshot_v4(conn, tenants)) == _keyed(snapshot)
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
            import_snapshot_v4(conn, snapshot)
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
def test_v4_physical_autocommit_refusal_precedes_all_sql_for_all_three_apis(
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
                    lambda: export_snapshot_v4(conn, tenants),
                    lambda: import_snapshot_v4(conn, snapshot),
                    lambda: verify_snapshot_v4(conn, snapshot),
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


def test_independent_verifier_detects_same_count_mutations_of_every_212_column(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v4(conn, snapshot)
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
                    }:
                        changed = uuid4()
                    elif field.name in {"completed_at", "submitted_at"}:
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
                    verify_snapshot_v4(conn, planted)
                assert any(
                    item.table == table
                    and (item.column == field.name or field.name == "id")
                    for item in caught.value.mismatches
                )


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v4_new_table_keys_and_current_deletes_are_independently_verified(
    transfer_database, table
):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v4(conn, initial)
        row = getattr(initial, table)[0]
        # Snapshot key mutations exercise both directions without invalid FK SQL.
        for rows in (
            getattr(initial, table)[1:],
            (replace(row, id=uuid4()),) + getattr(initial, table)[1:],
        ):
            with pytest.raises(SnapshotMismatch):
                verify_snapshot_v4(conn, replace(initial, **{table: rows}))
        relation = RELATIONS[table]
        if table in {"tracks", "cohort_tracks"}:
            conn.execute(RELATIONS["enrollments"].update().values(track_id=None))
        conn.execute(relation.delete().where(relation.c.id == row.id))
        current = export_snapshot_v4(conn, tenants)
        assert row.id not in {item.id for item in getattr(current, table)}
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v4(conn, initial)
        _clear(conn)
        assert import_snapshot_v4(conn, current).verification.matched
        assert _keyed(export_snapshot_v4(conn, tenants)) == _keyed(current)


@pytest.mark.parametrize("operation", ["export", "import", "verify"])
def test_v4_inactive_root_refuses_before_any_sql_or_autobegin(
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
                    export_snapshot_v4(conn, tenants)
                elif operation == "import":
                    import_snapshot_v4(conn, snapshot)
                else:
                    verify_snapshot_v4(conn, snapshot)
            assert statements == [] and conn.get_transaction() is None
        finally:
            event.remove(conn, "before_cursor_execute", observe)


@pytest.mark.parametrize("nested", [False, True])
def test_v4_detects_caller_transaction_replacement(transfer_database, nested):
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
                export_snapshot_v4(conn, tenants)
            assert replaced == [True]
        finally:
            event.remove(conn, "after_cursor_execute", replace_transaction)
            conn.rollback()


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v4_rejects_each_new_catalog_drift_before_any_insert(transfer_database, table):
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
                lambda: export_snapshot_v4(conn, tenants),
                lambda: import_snapshot_v4(conn, snapshot),
                lambda: verify_snapshot_v4(conn, snapshot),
            ):
                with pytest.raises(TransferConnectionError):
                    operation()
                assert inserts == [] and conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v4_requires_whole_visibility_of_each_new_table(transfer_database, table):
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
                lambda: export_snapshot_v4(conn, tenants),
                lambda: import_snapshot_v4(conn, snapshot),
                lambda: verify_snapshot_v4(conn, snapshot),
            ):
                with pytest.raises(TransferConnectionError):
                    operation()
                assert inserts == [] and conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()


def test_v4_roundtrip_all_212_fields_and_legacy_scope(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    assert sum(len(fields(getattr(snapshot, t)[0])) for t in TABLES) == 212
    with owner.connect() as conn, conn.begin() as root, conn.begin_nested() as nested:
        observed = import_snapshot_v4(conn, snapshot)
        assert observed.verification.matched and len(observed.inserted_rows) == 23
        assert _keyed(export_snapshot_v4(conn, tenants)) == _keyed(snapshot)
        assert verify_snapshot_v4(conn, snapshot).matched
        assert export_snapshot_v4(conn, (tenants[2],)) == replace(
            _empty(snapshot), tenant_ids=(tenants[2],)
        )
        old = (
            export_snapshot(conn, tenants),
            export_snapshot_v2(conn, tenants),
            export_snapshot_v3(conn, tenants),
        )
        conn.execute(RELATIONS["attempt_grants"].update().values(reason="later"))
        assert [
            len(op(conn, s).row_counts)
            for op, s in zip(
                (verify_snapshot, verify_snapshot_v2, verify_snapshot_v3),
                old,
                strict=True,
            )
        ] == [7, 14, 21]
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v4(conn, snapshot)
        assert (
            conn.get_transaction() is root and conn.get_nested_transaction() is nested
        )


@pytest.mark.parametrize("table", NEW_TABLES)
@pytest.mark.parametrize("cross_tenant", [False, True])
def test_v4_actual_activity_closure_precedes_any_insert(
    transfer_database, table, cross_tenant
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    other = next(a for a in snapshot.activities if a.tenant_id != rows[0].tenant_id)
    snapshot = replace(
        snapshot,
        **{
            table: (
                replace(rows[0], activity_id=other.id if cross_tenant else uuid4()),
            )
            + rows[1:]
        },
    )
    with owner.begin() as conn:
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotClosureErrorV4) as caught:
                import_snapshot_v4(conn, snapshot)
            assert caught.value.column == "activity_id" and inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize(
    "value",
    [
        "null",
        "true",
        "-7",
        '"raw"',
        "[]",
        '["q2", "q1"]',
        '{"raw": 12345678901234567890.12345678901234567890}',
    ],
)
def test_v4_raw_json_all_historical_kinds(transfer_database, value):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        activity_attempts=(
            replace(snapshot.activity_attempts[0], question_ext_ids=JsonbText(value)),
        )
        + snapshot.activity_attempts[1:],
    )
    with owner.begin() as conn:
        assert import_snapshot_v4(conn, snapshot).verification.matched
        assert export_snapshot_v4(conn, tenants).activity_attempts == tuple(
            sorted(snapshot.activity_attempts, key=lambda r: r.id)
        )


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("attempt_grants", "reason", "sensitive\x00raw"),
        ("attempt_grants", "reason", "sensitive\ud800raw"),
        ("attempt_grants", "extra_attempts", 2**31),
        ("attempt_grants", "extra_attempts", -(2**31) - 1),
        ("activity_attempts", "question_ext_ids", JsonbText('{"sensitive":')),
        ("activity_attempts", "question_ext_ids", JsonbText('{ "sensitive": 1 }')),
        ("activity_attempts", "question_ext_ids", JsonbText('["\ud800"]')),
        (
            "activity_attempts",
            "submitted_at",
            datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        ),
    ],
)
def test_v4_representation_refusal_is_payload_safe_before_any_insert(
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
        pending = uuid4()
        _tenant(conn, pending)
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotValidationError) as caught:
                import_snapshot_v4(conn, snapshot)
            assert "sensitive" not in str(caught.value) and inserts == []
            assert conn.get_transaction() is root
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


@pytest.mark.parametrize("stored", ["infinity", "-infinity", "10000-01-01 00:00:00+00"])
@pytest.mark.parametrize(
    "column", ["started_at", "submitted_at", "created_at", "updated_at"]
)
def test_v4_timestamp_binary_readback_refuses_non_native_history(
    transfer_database, stored, column
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v4(conn, snapshot)
        conn.execute(
            RELATIONS["activity_attempts"]
            .update()
            .values(
                {
                    column: sql_cast(
                        bindparam("stored"),
                        RELATIONS["activity_attempts"].c[column].type,
                    )
                }
            ),
            {"stored": stored},
        )
        for operation in (
            lambda: export_snapshot_v4(conn, tenants),
            lambda: verify_snapshot_v4(conn, snapshot),
        ):
            with pytest.raises(UnsupportedTimestampRepresentation):
                operation()


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v4_new_only_destination_and_outside_extent_refuse(transfer_database, table):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        # Deliberately plant one orphaned destination row with the superuser
        # fixture. Only this new table is nonempty: the whole-store guard must
        # refuse it before relying on FK closure or selected-tenant emptiness.
        row = getattr(snapshot, table)[0]
        values = {field.name: getattr(row, field.name) for field in fields(row)}
        if table == "activity_attempts":
            values["question_ext_ids"] = sql_cast(bindparam("raw_json"), JSONB)
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            RELATIONS[table].insert().values(values),
            {"raw_json": "null"} if table == "activity_attempts" else {},
        )
        conn.execute(text("SET LOCAL session_replication_role = origin"))
        assert all(
            count == (1 if name == table else 0)
            for name, count in _counts(conn).items()
        )
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(DestinationNotEmpty):
                import_snapshot_v4(
                    conn, replace(_empty(snapshot), tenant_ids=(tenants[2],))
                )
            assert inserts == []
            with pytest.raises(SnapshotMismatch) as caught:
                verify_snapshot_v4(
                    conn, replace(_empty(snapshot), tenant_ids=(tenants[2],))
                )
            assert any(
                m.table == table and m.reason == "outside_extent"
                for m in caught.value.mismatches
            )
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize("table", NEW_TABLES)
def test_v4_current_new_facts_updates_and_deletes_restore(transfer_database, table):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v4(conn, initial)
        original = getattr(initial, table)[0]
        values = {f.name: getattr(original, f.name) for f in fields(original)}
        values.update(
            id=uuid4(), person_id=uuid4(), updated_at=NOW + timedelta(seconds=1)
        )
        if table == "activity_attempts":
            values["question_ext_ids"] = sql_cast(bindparam("raw_json"), JSONB)
        conn.execute(
            RELATIONS[table].insert().values(values),
            {"raw_json": '{"current": true}'} if table == "activity_attempts" else {},
        )
        conn.execute(
            RELATIONS[table].delete().where(RELATIONS[table].c.id == original.id)
        )
        survivor = getattr(initial, table)[-1]
        changed = {"updated_at": NOW + timedelta(seconds=2)}
        if table == "activity_attempts":
            changed.update(
                submitted_at=NOW,
                question_ext_ids=sql_cast(bindparam("changed_json"), JSONB),
            )
        else:
            changed.update(
                extra_attempts=-3, reason="  changed raw reason\n ", granted_by=None
            )
        conn.execute(
            RELATIONS[table]
            .update()
            .where(RELATIONS[table].c.id == survivor.id)
            .values(changed),
            {"changed_json": "false"} if table == "activity_attempts" else {},
        )
        current = export_snapshot_v4(conn, tenants)
        with pytest.raises(SnapshotMismatch):
            verify_snapshot_v4(conn, initial)
        _clear(conn)
        assert import_snapshot_v4(conn, current).verification.matched
        assert _keyed(export_snapshot_v4(conn, tenants)) == _keyed(current)


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("activity_attempts", "question_ext_ids", "null"),
        ("attempt_grants", "extra_attempts", 19),
        ("activity_attempts", "started_at", NOW + timedelta(seconds=1)),
    ],
)
def test_v4_independent_readback_detects_distorted_bind(
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
                import_snapshot_v4(conn, snapshot)
            assert any(m.column == column for m in caught.value.mismatches)
        finally:
            event.remove(conn, "before_cursor_execute", distort)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}


def test_export_all_current_old_and_new_facts_after_mutations_restores_empty_store(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot_v4(conn, initial)
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
        current = export_snapshot_v4(conn, tenants)
        assert _keyed(current) != _keyed(initial)
    with owner.connect() as conn:
        root = conn.begin()
        _clear(conn)
        assert import_snapshot_v4(conn, current).verification.matched
        assert _keyed(export_snapshot_v4(conn, tenants)) == _keyed(current)
        root.rollback()
        with conn.begin():
            assert _keyed(export_snapshot_v4(conn, tenants)) == _keyed(current)


@pytest.mark.parametrize("value", [-(2**31), 2**31 - 1])
def test_v4_signed_int32_extremes_and_nullable_history_are_exact(
    transfer_database, value
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        attempt_grants=(replace(snapshot.attempt_grants[0], extra_attempts=value),)
        + snapshot.attempt_grants[1:],
    )
    with owner.begin() as conn:
        assert import_snapshot_v4(conn, snapshot).verification.matched
        assert _keyed(export_snapshot_v4(conn, tenants)) == _keyed(snapshot)


def test_v4_duplicate_open_source_constraint_is_not_repaired(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        activity_attempts=(
            *snapshot.activity_attempts,
            replace(snapshot.activity_attempts[0], id=uuid4()),
        ),
    )
    with owner.connect() as conn:
        root = conn.begin()
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(DBAPIError):
                import_snapshot_v4(conn, snapshot)
            assert "courses" in inserts and "activity_attempts" in inserts
            assert conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("courses", "title", "private\x00payload"),
        ("cohorts", "name", "private\ud800payload"),
        ("tracks", "slug", "x" * 81),
        ("questions", "weight", 2**31),
    ],
)
def test_v4_retains_all_old21_physical_preflight_before_insert(
    transfer_database, table, column, value
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    snapshot = replace(
        snapshot, **{table: (replace(rows[0], **{column: value}),) + rows[1:]}
    )
    with owner.begin() as conn:
        inserts, listener = _inserts(conn)
        try:
            with pytest.raises(SnapshotValidationError) as caught:
                import_snapshot_v4(conn, snapshot)
            assert "private" not in str(caught.value) and inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", listener)


@pytest.mark.parametrize("state", ["22023", "42501", "57014"])
def test_v4_required_write_sqlstates_propagate_for_caller_rollback(
    transfer_database, state
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        # Test-controlled SQLSTATE is one of the three closed constants above.
        conn.execute(
            DDL(
                "CREATE FUNCTION public.fail_assessment_transfer() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'required write failed' "
                "USING ERRCODE='%(state)s'; END $$",
                context={"state": state},
            )
        )
        conn.execute(
            DDL(
                "CREATE TRIGGER fail_assessment_transfer BEFORE INSERT ON %(fullname)s "
                "FOR EACH ROW EXECUTE FUNCTION public.fail_assessment_transfer()"
            ).against(RELATIONS["attempt_grants"])
        )
        try:
            with pytest.raises(DBAPIError):
                import_snapshot_v4(conn, snapshot)
            assert conn.get_transaction() is root
        finally:
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}


def test_v4_trigger_created_outside_extent_new_row_is_independently_refused(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants[:2], people[:2])
    with owner.connect() as conn:
        root = conn.begin()
        conn.execute(
            text(
                "UPDATE public.tenants SET slug='assessment-plant-target' WHERE id=:id"
            ),
            {"id": tenants[2]},
        )
        conn.execute(
            text("""CREATE FUNCTION public.extra_assessment_row() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE other_tenant uuid; course uuid; activity uuid;
        BEGIN
          SELECT id INTO other_tenant FROM public.tenants
          WHERE slug='assessment-plant-target';
          course := gen_random_uuid(); activity := gen_random_uuid();
          INSERT INTO mod_learning.courses
          (id,tenant_id,slug,title,discipline,source_ref)
          VALUES (course, other_tenant, course::text, '', '', '');
          INSERT INTO mod_learning.activities(id,tenant_id,course_id,type,title)
          VALUES (activity, other_tenant, course, 'quiz', 'outside');
          INSERT INTO mod_learning.activity_attempts
          (id,tenant_id,activity_id,person_id,question_ext_ids,started_at)
          VALUES (gen_random_uuid(),other_tenant,activity,
                  gen_random_uuid(),'null',now());
          RETURN NEW;
        END $$""")
        )
        conn.execute(
            DDL(
                "CREATE TRIGGER extra_assessment_row AFTER INSERT ON %(fullname)s "
                "FOR EACH ROW EXECUTE FUNCTION public.extra_assessment_row()"
            ).against(RELATIONS["attempt_grants"])
        )
        try:
            with pytest.raises(SnapshotMismatch) as caught:
                import_snapshot_v4(conn, snapshot)
            assert any(
                m.table == "activity_attempts" and m.reason == "outside_extent"
                for m in caught.value.mismatches
            )
            assert conn.get_transaction() is root
        finally:
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}


def test_v4_empty_23_table_extent_is_valid(transfer_database):
    owner, _, tenants, people = transfer_database
    snapshot = replace(_empty(_snapshot(tenants, people)), tenant_ids=(tenants[2],))
    with owner.begin() as conn:
        assert len(import_snapshot_v4(conn, snapshot).verification.row_counts) == 23
        assert export_snapshot_v4(conn, (tenants[2],)) == snapshot


@pytest.mark.parametrize("state", ["42501", "57014"])
def test_v4_preflight_permission_and_timeout_errors_are_not_reclassified(
    transfer_database, state
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.connect() as conn:
        root = conn.begin()
        conn.execute(
            DDL(
                "CREATE FUNCTION public.fail_assessment_preflight() RETURNS integer "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'preflight unavailable' "
                "USING ERRCODE='%(state)s'; END $$",
                context={"state": state},
            )
        )
        injected = []
        inserts, listener = _inserts(conn)

        def fail_probe(connection, _cursor, statement, _parameters, _context, _many):
            if "SELECT CAST(CAST" in statement and not injected:
                injected.append(True)
                connection.execute(text("SELECT public.fail_assessment_preflight()"))

        event.listen(conn, "before_cursor_execute", fail_probe)
        try:
            with pytest.raises(DBAPIError):
                import_snapshot_v4(conn, snapshot)
            assert injected == [True] and inserts == []
            assert conn.get_transaction() is root
        finally:
            event.remove(conn, "before_cursor_execute", fail_probe)
            event.remove(conn, "before_cursor_execute", listener)
            root.rollback()
        with conn.begin():
            assert set(_counts(conn).values()) == {0}
