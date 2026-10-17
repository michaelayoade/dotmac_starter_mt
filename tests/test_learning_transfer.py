"""Hosted PostgreSQL transfer canaries using the genuine unchanged lineages.

These observations do not prove Academy's reverse cutover, ACK ownership,
reference closure, operational writer fence, or compatible old assembly.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import DDL, create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from dotmac_learning.models import TENANT_MODELS
from dotmac_learning.transfer import export_snapshot, import_snapshot, verify_snapshot
from dotmac_learning.transfer_contracts import (
    ActivityRowV1,
    CertificateRowV1,
    CourseCompletionRowV1,
    CourseRowV1,
    DestinationNotEmpty,
    Float8Bits,
    JsonbText,
    LearningEventRowV1,
    LearningSnapshotV1,
    ScoreRowV1,
    SnapshotMismatch,
    SnapshotValidationError,
    SubmissionRowV1,
    TransferConnectionError,
    UnsupportedFloatRepresentation,
    UnsupportedTimestampRepresentation,
)
from tests.test_forms_workflow_isolation import _superuser_url, _url_for
from tests.test_learning_catalog import (
    _person,
    _scope,
    _tenant,
    learning_database as learning_database,
)

TABLES = (
    "courses",
    "activities",
    "submissions",
    "scores",
    "course_completions",
    "certificates",
    "learning_events",
)
# Descriptors only: the fixture still creates storage exclusively through
# genuine Kernel and learning migrations, never metadata.create_all().
_LEARNING_TABLES = {model.__tablename__: model.__table__ for model in TENANT_MODELS}
NOW = datetime(2020, 2, 29, 23, 59, 58, 123456, tzinfo=UTC)
ZERO = Float8Bits("0000000000000000")
NEGATIVE_ZERO = Float8Bits("8000000000000000")
ONE = Float8Bits("3ff0000000000000")


@pytest.fixture
def transfer_database(learning_database):
    admin_url, app_url = learning_database
    database = make_url(admin_url).database
    assert database is not None
    owner = create_engine(_url_for(_superuser_url(), database))
    app = create_engine(app_url)
    tenants = (uuid4(), uuid4(), uuid4())
    try:
        with owner.begin() as conn:
            for tenant in tenants:
                _tenant(conn, tenant)
            people = tuple(_person(conn, tenant) for tenant in tenants)
        yield owner, app, tenants, people
    finally:
        owner.dispose()
        app.dispose()


def _snapshot(tenants, people):
    courses, activities, submissions, scores, completions, certificates, events = (
        [] for _ in TABLES
    )
    for index, (tenant, person) in enumerate(zip(tenants[:2], people[:2], strict=True)):
        identity = {
            "tenant_id": tenant,
            "created_at": NOW,
            "updated_at": NOW + timedelta(microseconds=1),
        }
        course = CourseRowV1(
            id=uuid4(),
            slug=f"historical-{index}",
            title="History\n雪",
            discipline="legacy",
            source_ref="opaque:original",
            version=-3,
            status="retired",
            listed=bool(index),
            **identity,
        )
        courses.append(course)
        activity = ActivityRowV1(
            id=uuid4(),
            course_id=course.id,
            chapter_number=None,
            type="manual",
            bank_id=uuid4(),
            title="Historical",
            pass_threshold=NEGATIVE_ZERO,
            max_attempts=None,
            assessment_mode="historic",
            grading="manual",
            weight=ONE,
            question_count=None,
            **identity,
        )
        activities.append(activity)
        # Two submissions, the second intentionally manual and without any score.
        for attempt, answers in enumerate(
            (
                JsonbText("null"),
                JsonbText('[1.23456789012345678901234567890, "null", null]'),
            ),
            start=1,
        ):
            submission = SubmissionRowV1(
                id=uuid4(),
                activity_id=activity.id,
                person_id=uuid4(),
                answers=answers,
                attempt_no=attempt,
                **identity,
            )
            submissions.append(submission)
            if attempt == 1:
                for source, detail in (
                    ("legacy", JsonbText("42")),
                    (
                        "override",
                        JsonbText('{"precise": 12345678901234567890.1234567890}'),
                    ),
                ):
                    scores.append(
                        ScoreRowV1(
                            id=uuid4(),
                            submission_id=submission.id,
                            score=NEGATIVE_ZERO,
                            max_score=ONE,
                            fraction=ZERO,
                            passed=True,
                            per_item=detail,
                            source=source,
                            override_reason="" if source == "override" else None,
                            **identity,
                        )
                    )
        completions.append(
            CourseCompletionRowV1(
                id=uuid4(),
                person_id=uuid4(),
                course_id=course.id,
                status="historic",
                pct=NEGATIVE_ZERO,
                completed_at=None,
                **identity,
            )
        )
        certificates.append(
            CertificateRowV1(
                id=uuid4(),
                person_id=uuid4(),
                course_id=course.id,
                serial=f"original-{index}",
                issued_at=NOW - timedelta(days=1),
                **identity,
            )
        )
        # Repeated NULL-subject once kinds and repeated daily view events remain facts.
        for kind, detail in (
            ("chapter_completed", "null"),
            ("chapter_completed", '"null"'),
            ("course_viewed", "true"),
            ("course_viewed", "[1, null]"),
        ):
            events.append(
                LearningEventRowV1(
                    id=uuid4(),
                    person_id=person,
                    course_id=None,
                    kind=kind,
                    subject_id=None,
                    detail=JsonbText(detail),
                    occurred_at=NOW,
                    **identity,
                )
            )
    return LearningSnapshotV1(
        version=1,
        tenant_ids=tuple(tenants),
        courses=tuple(courses),
        activities=tuple(activities),
        submissions=tuple(submissions),
        scores=tuple(scores),
        course_completions=tuple(completions),
        certificates=tuple(certificates),
        learning_events=tuple(events),
    )


def _empty(snapshot):
    return replace(snapshot, **{table: () for table in TABLES})


def _clear(conn):
    for table in reversed(TABLES):
        conn.execute(_LEARNING_TABLES[table].delete())


def _keyed(snapshot):
    return {
        table: {row.id: row for row in getattr(snapshot, table)} for table in TABLES
    }


def test_restricted_tenant_actor_is_refused_before_hidden_destination_is_considered(
    transfer_database,
):
    owner, app, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot(conn, snapshot)
    with app.connect() as conn, conn.begin():
        _scope(
            conn, tenants[2]
        )  # no visible learning rows is not whole-store emptiness
        with pytest.raises(TransferConnectionError):
            import_snapshot(conn, _empty(snapshot))
        with pytest.raises(TransferConnectionError):
            export_snapshot(conn, (tenants[2],))
        with pytest.raises(TransferConnectionError):
            verify_snapshot(conn, _empty(snapshot))


def test_populated_every_field_roundtrip_and_zero_row_tenant_preserve_root_and_nested(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    expected = _snapshot(tenants, people)
    with owner.connect() as conn, conn.begin() as root, conn.begin_nested() as nested:
        outcome = import_snapshot(conn, expected)
        assert outcome.verification.matched
        assert outcome.verification.tenant_ids == tenants
        assert conn.get_transaction() is root and root.is_active
        assert conn.get_nested_transaction() is nested and nested.is_active
        assert _keyed(export_snapshot(conn, tenants)) == _keyed(expected)
        assert verify_snapshot(conn, expected).matched
        assert export_snapshot(conn, (tenants[2],)) == replace(
            _empty(expected), tenant_ids=(tenants[2],)
        )
        with pytest.raises(DestinationNotEmpty):
            import_snapshot(conn, expected)


def test_entire_store_empty_requirement_refuses_other_tenant_and_partial_store(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot(
            conn,
            replace(
                snapshot,
                courses=(snapshot.courses[0],),
                **{table: () for table in TABLES[1:]},
            ),
        )
        with pytest.raises(DestinationNotEmpty):
            import_snapshot(conn, replace(_empty(snapshot), tenant_ids=(tenants[2],)))


def test_no_active_root_is_refused_without_autobegin(transfer_database):
    owner, _, tenants, people = transfer_database
    with owner.connect() as conn:
        for operation in (
            lambda: export_snapshot(conn, tenants),
            lambda: import_snapshot(conn, _empty(_snapshot(tenants, people))),
        ):
            with pytest.raises(TransferConnectionError):
                operation()
            assert not conn.in_transaction()


def test_missing_genuine_kernel_tenant_is_refused_prewrite(transfer_database):
    owner, _, tenants, people = transfer_database
    with owner.begin() as conn:
        with pytest.raises(SnapshotValidationError):
            import_snapshot(
                conn, replace(_empty(_snapshot(tenants, people)), tenant_ids=(uuid4(),))
            )
        assert all(
            conn.scalar(select(func.count()).select_from(_LEARNING_TABLES[table])) == 0
            for table in TABLES
        )


@pytest.mark.parametrize(
    "mutation", ["foreign_tenant_activity", "missing_person_subtype"]
)
def test_genuine_composite_and_person_foreign_keys_are_not_invented_or_bypassed(
    transfer_database, mutation
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    if mutation == "foreign_tenant_activity":
        changed = replace(
            snapshot.submissions[0], activity_id=snapshot.activities[1].id
        )
        snapshot = replace(snapshot, submissions=(changed,) + snapshot.submissions[1:])
    else:
        with owner.begin() as conn:
            missing_profile = _person(conn, tenants[0], subtype=False)
        changed = replace(snapshot.learning_events[0], person_id=missing_profile)
        snapshot = replace(
            snapshot, learning_events=(changed,) + snapshot.learning_events[1:]
        )
    with owner.connect() as conn:
        root = conn.begin()
        with pytest.raises(DBAPIError):
            import_snapshot(conn, snapshot)
        assert conn.get_transaction() is root
        root.rollback()
        with conn.begin():
            assert all(
                conn.scalar(select(func.count()).select_from(_LEARNING_TABLES[table]))
                == 0
                for table in TABLES
            )


@pytest.mark.parametrize("table", TABLES)
def test_required_table_failure_propagates_and_caller_root_rollback_restores_all_work(
    transfer_database, table
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    with owner.begin() as conn:
        conn.execute(
            text(
                "CREATE FUNCTION public.fail_learning_transfer() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                "'injected transfer failure'; END $$"
            )
        )
        conn.execute(
            DDL(
                "CREATE TRIGGER fail_learning_transfer BEFORE INSERT "
                "ON %(fullname)s FOR EACH ROW "
                "EXECUTE FUNCTION public.fail_learning_transfer()"
            ).against(_LEARNING_TABLES[table])
        )
    pending_tenant = uuid4()
    with owner.connect() as conn:
        root = conn.begin()
        _tenant(conn, pending_tenant)
        with pytest.raises(DBAPIError):
            import_snapshot(conn, snapshot)
        assert conn.get_transaction() is root and root.is_active
        root.rollback()
        with conn.begin():
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM public.tenants WHERE id=:id"),
                    {"id": pending_tenant},
                )
                == 0
            )
            assert all(
                conn.scalar(select(func.count()).select_from(_LEARNING_TABLES[name]))
                == 0
                for name in TABLES
            )


@pytest.mark.parametrize(
    "bits",
    [
        "7ff0000000000000",
        "fff0000000000000",
        "7ff8000000000000",
        "7ff0000000000001",
        "fff8000000000001",
    ],
)
def test_special_float_representation_roundtrips_or_refuses_before_any_insert(
    transfer_database, bits
):
    owner, _, tenants, people = transfer_database
    expected = _snapshot(tenants, people)
    expected = replace(
        expected,
        activities=(replace(expected.activities[0], weight=Float8Bits(bits)),)
        + expected.activities[1:],
    )
    inserts = []

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO mod_learning."):
            inserts.append(statement)

    event.listen(owner, "before_cursor_execute", observe)
    try:
        with owner.begin() as conn:
            try:
                import_snapshot(conn, expected)
            except UnsupportedFloatRepresentation:
                assert inserts == []
                assert all(
                    conn.scalar(
                        select(func.count()).select_from(_LEARNING_TABLES[table])
                    )
                    == 0
                    for table in TABLES
                )
            else:
                assert verify_snapshot(conn, expected).matched
                assert export_snapshot(conn, tenants).activities[0].weight in (
                    Float8Bits(bits),
                    expected.activities[1].weight,
                )
    finally:
        event.remove(owner, "before_cursor_execute", observe)


@pytest.mark.parametrize(
    "mutation",
    ["omitted", "planted", "same_count_key", "field", "json_kind", "signed_zero"],
)
def test_independent_verifier_detects_both_directions_keys_and_every_field(
    transfer_database, mutation
):
    owner, _, tenants, people = transfer_database
    expected = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot(conn, expected)
        event_row = expected.learning_events[0]
        params = {
            "id": event_row.id,
            "tenant": event_row.tenant_id,
            "person": event_row.person_id,
            "new_id": uuid4(),
        }
        sql = {
            "omitted": "DELETE FROM mod_learning.learning_events WHERE id=:id",
            "planted": (
                "INSERT INTO mod_learning.learning_events "
                "(id,tenant_id,person_id,kind,detail,occurred_at) "
                "VALUES (:new_id,:tenant,:person,'course_viewed','{}',now())"
            ),
            "same_count_key": (
                "UPDATE mod_learning.learning_events SET id=:new_id WHERE id=:id"
            ),
            "field": (
                "UPDATE mod_learning.learning_events SET kind='other' WHERE id=:id"
            ),
            "json_kind": (
                "UPDATE mod_learning.learning_events "
                "SET detail='\"null\"'::jsonb WHERE id=:id"
            ),
            "signed_zero": (
                "UPDATE mod_learning.activities SET pass_threshold=0 WHERE id=:id"
            ),
        }[mutation]
        if mutation == "signed_zero":
            params["id"] = expected.activities[0].id
        conn.execute(text(sql), params)
        with pytest.raises(SnapshotMismatch) as caught:
            verify_snapshot(conn, expected)
        assert caught.value.mismatches
        assert all(item.table in TABLES for item in caught.value.mismatches)
        assert '"null"' not in str(caught.value)


def test_full_current_export_after_mutations_restores_empty_transaction(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    initial = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot(conn, initial)
    with owner.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_learning.course_completions SET status='regressed',"
                "pct=0,completed_at=NULL,updated_at=:now"
            ),
            {"now": NOW + timedelta(days=2)},
        )
        conn.execute(
            text(
                "UPDATE mod_learning.activities "
                "SET bank_id=:bank,grading='auto' WHERE id=:id"
            ),
            {"bank": uuid4(), "id": initial.activities[0].id},
        )
        conn.execute(
            text("DELETE FROM mod_learning.scores WHERE id=:id"),
            {"id": initial.scores[0].id},
        )
        conn.execute(
            text(
                "INSERT INTO mod_learning.learning_events "
                "(id,tenant_id,person_id,kind,detail,occurred_at) "
                "VALUES (:id,:tenant,:person,'lab_check_passed','[true,null]',:now)"
            ),
            {
                "id": uuid4(),
                "tenant": tenants[0],
                "person": people[0],
                "now": NOW + timedelta(days=3),
            },
        )
        current = export_snapshot(conn, tenants)
        assert _keyed(current) != _keyed(initial)
    # This is a package-store restore exercise; the assembly owns compatibility storage.
    with owner.connect() as conn:
        root = conn.begin()
        _clear(conn)
        assert import_snapshot(conn, current).verification.matched
        assert _keyed(export_snapshot(conn, tenants)) == _keyed(current)
        root.rollback()
        with conn.begin():
            assert _keyed(export_snapshot(conn, tenants)) == _keyed(current)


@pytest.mark.parametrize(
    "table,column",
    [("submissions", "answers"), ("scores", "per_item"), ("learning_events", "detail")],
)
@pytest.mark.parametrize(
    "storage_text",
    [
        "null",
        '"null"',
        "12345678901234567890.1234567890",
        '[true, null, "雪"]',
        '{"nested": [null, 1]}',
    ],
)
def test_each_not_null_jsonb_column_accepts_every_historical_json_kind(
    transfer_database, table, column, storage_text
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    rows = getattr(snapshot, table)
    snapshot = replace(
        snapshot,
        **{table: (replace(rows[0], **{column: JsonbText(storage_text)}),) + rows[1:]},
    )
    with owner.begin() as conn:
        import_snapshot(conn, snapshot)
        assert verify_snapshot(conn, snapshot).matched
        actual = _keyed(export_snapshot(conn, tenants))[table][rows[0].id]
        assert getattr(actual, column) == JsonbText(storage_text)


@pytest.mark.parametrize("bad_text", ["{", '{"unclosed":', "1e1000000"])
def test_late_json_syntax_or_numeric_failure_occurs_before_first_insert(
    transfer_database, bad_text
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        learning_events=snapshot.learning_events[:-1]
        + (replace(snapshot.learning_events[-1], detail=JsonbText(bad_text)),),
    )
    inserts = []

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO mod_learning."):
            inserts.append(statement)

    event.listen(owner, "before_cursor_execute", observe)
    try:
        with owner.connect() as conn:
            root = conn.begin()
            with pytest.raises(DBAPIError):
                import_snapshot(conn, snapshot)
            assert inserts == []
            root.rollback()
    finally:
        event.remove(owner, "before_cursor_execute", observe)


def test_full_nullable_history_is_present_even_when_unconstrained_by_domain(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = _snapshot(tenants, people)
    snapshot = replace(
        snapshot,
        activities=(
            snapshot.activities[0],
            replace(
                snapshot.activities[1],
                chapter_number=-9,
                bank_id=None,
                max_attempts=-4,
                question_count=-1,
            ),
        ),
        course_completions=(
            snapshot.course_completions[0],
            replace(snapshot.course_completions[1], completed_at=NOW),
        ),
    )
    with owner.begin() as conn:
        assert import_snapshot(conn, snapshot).verification.matched
        assert _keyed(export_snapshot(conn, tenants)) == _keyed(snapshot)


def test_static_column_contract_refuses_catalogue_drift_before_any_insert(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    with owner.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE mod_learning.learning_events "
                "ADD COLUMN undeclared_history text"
            )
        )
        with pytest.raises(TransferConnectionError, match="column contract"):
            import_snapshot(conn, _snapshot(tenants, people))
        assert all(
            conn.scalar(select(func.count()).select_from(_LEARNING_TABLES[table])) == 0
            for table in TABLES
        )


def test_participant_detects_root_replaced_by_caller_hook(transfer_database):
    owner, _, tenants, people = transfer_database
    with owner.connect() as conn:
        root = conn.begin()
        changed = False

        def replace_root(
            hooked_conn, _cursor, _statement, _parameters, _context, _executemany
        ):
            nonlocal changed
            if not changed:
                changed = True
                root.rollback()
                hooked_conn.begin()

        event.listen(conn, "after_cursor_execute", replace_root)
        try:
            with pytest.raises(TransferConnectionError, match="continuity"):
                export_snapshot(conn, tenants)
        finally:
            event.remove(conn, "after_cursor_execute", replace_root)
            conn.rollback()


def test_independent_verifier_checks_every_persisted_column(transfer_database):
    # Every single-field plant retains the database row count. A verifier that
    # merely counts or compares keys therefore cannot satisfy this canary.
    from dataclasses import fields

    owner, _, tenants, people = transfer_database
    expected = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot(conn, expected)
        for table in TABLES:
            rows = getattr(expected, table)
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
                    expected,
                    **{table: (replace(original, **{field.name: changed}),) + rows[1:]},
                )
                with pytest.raises(SnapshotMismatch) as caught:
                    verify_snapshot(conn, planted)
                assert any(
                    item.table == table
                    and (item.column == field.name or field.name == "id")
                    for item in caught.value.mismatches
                )


def test_import_full_store_verification_refuses_trigger_created_outside_extent(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    snapshot = replace(_snapshot(tenants, people), tenant_ids=tenants[:2])
    with owner.begin() as conn:
        conn.execute(
            text("""CREATE FUNCTION public.plant_other_tenant() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE other_tenant uuid;
        BEGIN
          SELECT id INTO other_tenant FROM public.tenants
          WHERE slug = 'transfer-plant-target';
          INSERT INTO mod_learning.courses
          (id,tenant_id,slug,title,discipline,source_ref)
          VALUES (gen_random_uuid(), other_tenant, gen_random_uuid()::text,
                  'planted', '', '');
          RETURN NULL;
        END $$""")
        )
        conn.execute(
            text("UPDATE public.tenants SET slug='transfer-plant-target' WHERE id=:id"),
            {"id": tenants[2]},
        )
        conn.execute(
            text(
                "CREATE TRIGGER plant_other_tenant AFTER INSERT "
                "ON mod_learning.certificates FOR EACH STATEMENT "
                "EXECUTE FUNCTION public.plant_other_tenant()"
            )
        )
    with owner.connect() as conn:
        root = conn.begin()
        with pytest.raises(SnapshotMismatch) as caught:
            import_snapshot(conn, snapshot)
        assert any(item.reason == "outside_extent" for item in caught.value.mismatches)
        root.rollback()
        with conn.begin():
            assert all(
                conn.scalar(select(func.count()).select_from(_LEARNING_TABLES[table]))
                == 0
                for table in TABLES
            )


@pytest.mark.parametrize(
    "mode", ["execution_option", "engine_default", "direct_driver"]
)
def test_autocommit_logical_root_is_refused_before_any_sql_for_all_operations(
    transfer_database, mode
):
    owner, _, tenants, people = transfer_database
    expected = _snapshot(tenants, people)
    alternate = (
        create_engine(owner.url, isolation_level="AUTOCOMMIT")
        if mode == "engine_default"
        else None
    )
    engine = alternate or owner
    try:
        with engine.connect() as conn:
            driver = conn.connection.dbapi_connection
            assert driver is not None
            if mode == "execution_option":
                conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            elif mode == "direct_driver":
                driver.autocommit = True
            root = conn.begin()  # SQLAlchemy's logical root is insufficient here.
            observed_sql = []

            def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
                observed_sql.append(statement)

            event.listen(conn, "before_cursor_execute", observe)
            try:
                for operation in (
                    lambda: export_snapshot(conn, tenants),
                    lambda: import_snapshot(conn, expected),
                    lambda: verify_snapshot(conn, expected),
                ):
                    with pytest.raises(TransferConnectionError, match="autocommit"):
                        operation()
                    assert observed_sql == []
                    assert conn.get_transaction() is root
            finally:
                event.remove(conn, "before_cursor_execute", observe)
                root.rollback()
                if mode == "direct_driver":
                    driver.autocommit = False
        with owner.begin() as conn:
            assert all(
                conn.scalar(select(func.count()).select_from(_LEARNING_TABLES[table]))
                == 0
                for table in TABLES
            )
    finally:
        if alternate is not None:
            alternate.dispose()


@pytest.mark.parametrize(
    "stored",
    ["infinity", "-infinity", "10000-01-01 00:00:00+00", "0001-01-01 00:00:00 BC"],
)
def test_export_and_verifier_refuse_sql_timestamps_outside_native_contract(
    transfer_database, stored
):
    owner, _, tenants, people = transfer_database
    expected = _snapshot(tenants, people)
    with owner.begin() as conn:
        import_snapshot(conn, expected)
        conn.execute(
            text(
                "UPDATE mod_learning.learning_events "
                "SET occurred_at=CAST(:stored AS timestamptz) WHERE id=:id"
            ),
            {"stored": stored, "id": expected.learning_events[0].id},
        )
        for operation in (
            lambda: export_snapshot(conn, tenants),
            lambda: verify_snapshot(conn, expected),
        ):
            with pytest.raises(UnsupportedTimestampRepresentation) as caught:
                operation()
            assert "learning_events" in str(caught.value)
            assert "occurred_at" in str(caught.value)
            assert stored not in str(caught.value)


def test_timestamp_bind_distortion_is_refused_before_first_insert(transfer_database):
    owner, _, tenants, people = transfer_database
    expected = _snapshot(tenants, people)
    inserts = []

    def distort(_conn, _cursor, statement, parameters, _context, _executemany):
        if statement.startswith("INSERT INTO mod_learning."):
            inserts.append(statement)
        if "timestamptz_send" in statement:
            assert isinstance(parameters, dict)
            parameters = {
                key: value + timedelta(microseconds=1)
                if isinstance(value, datetime)
                else value
                for key, value in parameters.items()
            }
        return statement, parameters

    with owner.begin() as conn:
        event.listen(conn, "before_cursor_execute", distort, retval=True)
        try:
            with pytest.raises(UnsupportedTimestampRepresentation):
                import_snapshot(conn, expected)
            assert inserts == []
            assert all(
                conn.scalar(select(func.count()).select_from(_LEARNING_TABLES[table]))
                == 0
                for table in TABLES
            )
        finally:
            event.remove(conn, "before_cursor_execute", distort)


def test_timestamp_verifier_compares_exact_instants_across_dst_fold_and_utc(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    expected = _snapshot(tenants, people)
    first = datetime(2020, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Rome"), fold=0)
    second = first.replace(fold=1)
    row = replace(expected.certificates[0], issued_at=first)
    expected = replace(expected, certificates=(row,) + expected.certificates[1:])
    with owner.begin() as conn:
        import_snapshot(conn, expected)
        utc_snapshot = replace(
            expected,
            certificates=(replace(row, issued_at=first.astimezone(UTC)),)
            + expected.certificates[1:],
        )
        assert verify_snapshot(conn, utc_snapshot).matched
        planted = replace(
            expected,
            certificates=(replace(row, issued_at=second),) + expected.certificates[1:],
        )
        with pytest.raises(SnapshotMismatch) as caught:
            verify_snapshot(conn, planted)
        assert any(item.column == "issued_at" for item in caught.value.mismatches)


def test_timestamp_preflight_does_not_deduplicate_distinct_dst_fold_instants(
    transfer_database,
):
    owner, _, tenants, people = transfer_database
    expected = _snapshot(tenants, people)
    first = datetime(2020, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Rome"), fold=0)
    expected = replace(
        expected,
        certificates=(
            replace(expected.certificates[0], issued_at=first),
            replace(expected.certificates[1], issued_at=first.replace(fold=1)),
        ),
    )
    inserts = []

    def distort(_conn, _cursor, statement, parameters, _context, _executemany):
        if statement.startswith("INSERT INTO mod_learning."):
            inserts.append(statement)
        if "timestamptz_send" in statement:
            assert isinstance(parameters, dict)
            parameters = {
                key: value.replace(fold=0)
                if isinstance(value, datetime) and value.fold == 1
                else value
                for key, value in parameters.items()
            }
        return statement, parameters

    with owner.begin() as conn:
        event.listen(conn, "before_cursor_execute", distort, retval=True)
        try:
            with pytest.raises(UnsupportedTimestampRepresentation):
                import_snapshot(conn, expected)
            assert inserts == []
        finally:
            event.remove(conn, "before_cursor_execute", distort)
