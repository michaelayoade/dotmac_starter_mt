"""Hosted PostgreSQL parity canaries for Academy's bank/Activity CLI intent."""

from datetime import date
from uuid import uuid4

import pytest
from dotmac_learning import BankDoc, BankDocumentHeader, decoded_doc_from_db
from dotmac_learning.bank_activity_contracts import (
    BankActivityTerms,
    ImportBankWithActivityCommand,
)
from dotmac_learning.bank_activity_import import import_bank_with_activity
from dotmac_learning.curriculum import create_course, lint_bank
from dotmac_learning.curriculum_contracts import (
    BankDocument,
    BankFact,
    BankQuestion,
    CourseCreate,
)
from dotmac_learning.curriculum_models import Question, QuestionBank
from dotmac_learning.models import Activity, Course
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_curriculum as curriculum_fixtures
from tests.test_learning_catalog import _scope

learning_database = curriculum_fixtures.learning_database
curriculum_case = curriculum_fixtures.curriculum_case


def _doc(*, kind="chapter", chapter=2, policy=None, question="first"):
    return BankDocument(
        course="bank-course",
        chapter=chapter,
        kind=kind,
        questions=(
            BankQuestion(
                id=question,
                stem="Question",
                type="single",
                rubric_category="recall",
                options=["A", "B"],
                correct=["A"],
                weight=1,
            ),
        ),
        policy={} if policy is None else policy,
    )


def _course(db, tenant):
    return create_course(
        db,
        CourseCreate(
            tenant_id=tenant,
            slug="bank-course",
            title="Bank",
            discipline="x",
            listed=False,
        ),
    )


def _import(db, tenant, doc):
    return import_bank_with_activity(
        db,
        ImportBankWithActivityCommand(
            tenant_id=tenant,
            doc=doc,
            terms=BANK_ACTIVITY_TERMS,
            missing_question_weight=1,
        ),
    )


def test_restricted_tenant_positive_foreign_negative_and_missing_course_no_bank_dml(
    curriculum_case,
):
    engine, (tenant, other) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        assert db.scalar(text("SELECT current_user")) == "app_user"
        assert not db.scalar(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
        course = _course(db, tenant)
        result = _import(db, tenant, _doc())
        assert result.created and result.title == "Chapter 2 test"
        db.flush()
        assert db.scalar(select(Activity.bank_id)) == result.bank_id
        assert db.get(QuestionBank, result.bank_id).course_id == course.id
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with Session(engine) as db, db.begin():
            _scope(db, other)
            assert _import(db, tenant, _doc()) is None
            assert _import(db, other, _doc()) is None
            assert db.scalar(select(Activity)) is None
            assert db.get(QuestionBank, result.bank_id) is None
            assert db.scalar(select(Question)) is None
        assert not any(
            statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            for statement in statements
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_reimport_replaces_questions_reuses_bank_activity_and_preserves_other_fields(
    curriculum_case,
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _course(db, tenant)
        first = _import(db, tenant, _doc())
        db.flush()
        activity = db.scalar(select(Activity))
        activity_id = activity.id
        old_question_id = db.scalar(select(Question.id))
        activity.type = "lab"
        activity.chapter_number = 99
        activity.grading = "manual"
        activity.weight = 3.5
        activity.question_count = 7
        activity.max_attempts = 9
        activity.assessment_mode = "exam"
        second = _import(db, tenant, _doc(question="replacement"))
        assert second.bank_id == first.bank_id and not second.created
        assert second.applied_policy == ()
        assert db.get(Question, old_question_id) is None
        assert db.scalar(select(Question.ext_id)) == "replacement"
        assert activity.id == activity_id
        assert (
            activity.type,
            activity.chapter_number,
            activity.grading,
            activity.weight,
        ) == ("lab", 99, "manual", 3.5)
        assert (
            activity.question_count,
            activity.max_attempts,
            activity.assessment_mode,
        ) == (7, 9, "exam")
        assert activity.title == "Chapter 2 test" and activity.pass_threshold == 0.0


@pytest.mark.parametrize(
    ("kind", "chapter", "threshold", "title"),
    (
        ("chapter", None, 0.0, "Chapter None test"),
        ("mid", 4, 0.60, "Mid assessment"),
        ("final", None, 0.70, "Final assessment"),
        ("entrance", None, 0.0, "Entrance assessment"),
    ),
)
def test_source_kind_titles_defaults_and_explicit_threshold(
    curriculum_case, kind, chapter, threshold, title
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _course(db, tenant)
        doc = _doc(kind=kind, chapter=chapter)
        result = _import(db, tenant, doc)
        pending = next(row for row in db.new if isinstance(row, Activity))
        assert pending.pass_threshold == threshold and result.title == title
        assert pending.chapter_number == chapter and pending.type == "mcq_test"
        assert pending.id is None
        db.flush()
        result = _import(
            db, tenant, doc.model_copy(update={"policy": {"pass_threshold": 0}})
        )
        assert result.applied_policy == ()
        assert db.scalar(select(Activity)).pass_threshold == 0


def test_policy_presence_raw_values_source_order_and_nullable_clearing(curriculum_case):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _course(db, tenant)
        policy = {"mode": "exam", "max_attempts": 3, "pool": 1, "pass_threshold": 0.25}
        result = _import(db, tenant, _doc(policy=policy))
        assert result.applied_policy == (
            ("pool", 1),
            ("max_attempts", 3),
            ("mode", "exam"),
        )
        activity = next(row for row in db.new if isinstance(row, Activity))
        assert (
            activity.question_count,
            activity.max_attempts,
            activity.assessment_mode,
        ) == (1, 3, "exam")
        db.flush()
        result = _import(db, tenant, _doc(policy={"pool": None, "max_attempts": None}))
        assert result.applied_policy == (("pool", None), ("max_attempts", None))
        assert activity.question_count is None and activity.max_attempts is None
        assert activity.assessment_mode == "exam"
        db.flush()


def test_explicit_null_new_row_keeps_source_staging_and_insert_defaults(
    curriculum_case,
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _course(db, tenant)
        result = _import(
            db,
            tenant,
            _doc(
                policy={
                    "pool": None,
                    "max_attempts": None,
                    "mode": None,
                    "pass_threshold": None,
                }
            ),
        )
        activity = next(row for row in db.new if isinstance(row, Activity))
        assert result.applied_policy == (
            ("pool", None),
            ("max_attempts", None),
            ("mode", None),
        )
        assert activity.assessment_mode is None and activity.pass_threshold is None
        assert activity.question_count is None and activity.max_attempts is None
        # The source ORM's insert defaults apply at the caller's flush; the intent
        # neither coerces explicit nulls nor predicts the resulting stored values.
        db.flush()
        assert activity.assessment_mode == "graded" and activity.pass_threshold == 0.0
        assert activity.question_count is None and activity.max_attempts is None


@pytest.mark.parametrize("key", ("mode", "pass_threshold"))
def test_linter_accepted_null_preserves_source_existing_row_constraint_failure(
    curriculum_case, key
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db:
        with db.begin():
            _scope(db, tenant)
            _course(db, tenant)
            _import(db, tenant, _doc())
            db.flush()
            doc = _doc(policy={key: None})
            # A balanced document proves policy-null acceptance by the public linter.
            balanced = tuple(
                BankQuestion(
                    id=str(i),
                    stem="Q",
                    type="truefalse",
                    rubric_category=category,
                    correct=["true"],
                    weight=1,
                )
                for i, category in enumerate(
                    ["recall"] * 2 + ["application"] * 5 + ["analysis"] * 3
                )
            )
            assert lint_bank(doc.model_copy(update={"questions": balanced})) == []
            _import(db, tenant, doc)
            activity = next(row for row in db.dirty if isinstance(row, Activity))
            column = "assessment_mode" if key == "mode" else key
            assert getattr(activity, column) is None
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()  # caller owns failure outcome


def test_original_session_bank_flush_final_activity_staging_and_outer_rollback(
    curriculum_case,
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db:
        with db.begin():
            _scope(db, tenant)
            _course(db, tenant)
            boundary = db.get_transaction()
            flushes = []

            def after_flush(session, context):
                flushes.append(session)

            event.listen(db, "after_flush", after_flush)
            try:
                result = _import(db, tenant, _doc())
            finally:
                event.remove(db, "after_flush", after_flush)
            assert flushes == [db, db]
            assert db.get_transaction() is boundary and not db.in_nested_transaction()
            activity = next(row for row in db.new if isinstance(row, Activity))
            assert activity.bank_id == result.bank_id and activity.id is None
            assert db.scalar(
                text("SELECT current_setting('app.current_tenant')")
            ) == str(tenant)
            db.rollback()
        with db.begin():
            _scope(db, tenant)
            for model in (Course, QuestionBank, Question, Activity):
                assert db.scalar(select(model)) is None


# Raw Academy decoder canaries deliberately avoid BankDocument construction.
def _raw_doc():
    return BankDoc(
        course="bank-course",
        chapter=2,
        kind="chapter",
        version=1,
        questions=[
            {
                "id": str(i),
                "stem": "Q",
                "type": "truefalse",
                "rubric_category": category,
                "correct": ["true"],
            }
            for i, category in enumerate(
                ["recall"] * 2 + ["application"] * 5 + ["analysis"] * 3
            )
        ],
    )


def test_raw_unknown_course_bad_weight_skips_without_dml(curriculum_case):
    engine, (tenant, _) = curriculum_case
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record)
    try:
        with Session(engine) as db, db.begin():
            _scope(db, tenant)
            doc = _raw_doc()
            doc.questions[0]["weight"] = "bad"
            assert lint_bank(doc) == []
            command = ImportBankWithActivityCommand(
                tenant_id=tenant,
                doc=doc,
                terms=BANK_ACTIVITY_TERMS,
                missing_question_weight=1,
            )
            assert command.doc is doc
            assert import_bank_with_activity(db, command) is None
            assert not db.new and db.is_active
        assert not any(
            s.lstrip().startswith(("insert", "update", "delete")) for s in statements
        )
        assert not any("activities" in s for s in statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.mark.parametrize("existing", (False, True))
@pytest.mark.parametrize("position", (0, 2))
@pytest.mark.parametrize(("bad", "error"), (("bad", ValueError), (None, TypeError)))
def test_raw_bad_weight_preserves_original_stage_and_caller_rollback(
    curriculum_case, existing, position, bad, error
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db:
        with db.begin():
            _scope(db, tenant)
            _course(db, tenant)
            if existing:
                original = _import(db, tenant, _raw_doc())
                db.flush()
                bank_id = original.bank_id
                activity_id = db.scalar(select(Activity.id))
                question_ids = tuple(
                    db.scalars(select(Question.id).order_by(Question.ext_id))
                )
        with db.begin():
            _scope(db, tenant)
            bank = db.get(QuestionBank, bank_id) if existing else None
            boundary = db.get_transaction()
            doc = _raw_doc()
            doc.version = 9
            doc.questions[position]["weight"] = bad
            assert lint_bank(doc) == []
            trace = []

            def record(conn, cursor, statement, parameters, context, executemany):
                trace.append(statement.lower())

            def flushed(session, context):
                trace.append("FLUSH")

            event.listen(engine, "before_cursor_execute", record)
            event.listen(db, "after_flush", flushed)
            try:
                with pytest.raises(error):
                    _import(db, tenant, doc)
            finally:
                event.remove(engine, "before_cursor_execute", record)
                event.remove(db, "after_flush", flushed)
            assert db.is_active and db.get_transaction() is boundary
            assert not db.in_nested_transaction()
            assert len([row for row in db.new if isinstance(row, Question)]) == position
            assert not any(isinstance(row, Activity) for row in db.new | db.dirty)
            assert not any("activities" in s for s in trace)
            if existing:
                assert bank.version == 9
                expected_prefix = str(
                    delete(Question.__table__).compile(dialect=engine.dialect)
                ).lower()
                assert any(s.lstrip().startswith(expected_prefix) for s in trace)
                assert "FLUSH" not in trace
            else:
                insert = next(
                    i
                    for i, s in enumerate(trace)
                    if s.lstrip().startswith(
                        f"insert into {QuestionBank.__table__.fullname}"
                    )
                )
                assert insert < trace.index("FLUSH")
                assert trace.count("FLUSH") == 1
            db.rollback()
        with db.begin():
            _scope(db, tenant)
            if existing:
                assert db.get(QuestionBank, bank_id).version == 1
                assert db.scalar(select(Activity.id)) == activity_id
                assert (
                    tuple(db.scalars(select(Question.id).order_by(Question.ext_id)))
                    == question_ids
                )
            else:
                assert db.scalar(select(QuestionBank)) is None
                assert db.scalar(select(Activity)) is None
                assert db.scalar(select(Question)) is None


@pytest.mark.parametrize("domain_key", ("category", "competency"))
def test_raw_numeric_fraction_date_competency_and_structured_replacement(
    curriculum_case, domain_key
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _course(db, tenant)
        doc = _raw_doc()
        doc.questions[0].update(
            type="numeric",
            correct=1.25,
            weight=1.9,
            metadata=object(),
        )
        doc.questions[0][domain_key] = date(2026, 10, 17)
        assert lint_bank(doc) == []
        first = _import(db, tenant, doc)
        db.flush()
        activity_id = db.scalar(select(Activity.id))
        row = db.scalar(select(Question).where(Question.ext_id == "0"))
        old_id = row.id
        assert (
            row.correct,
            row.weight,
            row.category,
            row.options,
            row.explanation,
        ) == (1.25, 1, "2026-10-17", [], "")
        # The writer accepts caller-owned structured JSON without coercing shape.
        doc.questions[0].update(
            options={"choices": [None, True, 2]}, correct={"answer": [False, 1.5]}
        )
        second = _import(db, tenant, doc)
        db.flush()
        assert second.bank_id == first.bank_id and not second.created
        assert db.scalar(select(Activity.id)) == activity_id
        replacement = db.scalar(select(Question).where(Question.ext_id == "0"))
        assert replacement.id != old_id
        assert replacement.options == {"choices": [None, True, 2]}
        assert replacement.correct == {"answer": [False, 1.5]}


@pytest.mark.parametrize("key", ("options", "correct"))
def test_raw_non_json_reaches_final_flush_serialization_failure_and_rollback(
    curriculum_case, key
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db:
        with db.begin():
            _scope(db, tenant)
            _course(db, tenant)
        with db.begin():
            _scope(db, tenant)
            doc = _raw_doc()
            doc.questions[0][key] = [date(2026, 10, 17)]
            assert lint_bank(doc) == []
            command = ImportBankWithActivityCommand(
                tenant_id=tenant,
                doc=doc,
                terms=BANK_ACTIVITY_TERMS,
                missing_question_weight=1,
            )
            assert command.doc is doc
            flushes = []
            statements = []

            def flushed(session, context):
                flushes.append(session)

            def executing(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement.lower())

            event.listen(db, "after_flush", flushed)
            event.listen(engine, "before_cursor_execute", executing)
            try:
                # psycopg serializes JSONB during cursor execution, so its
                # json.dumps TypeError reaches the caller without a SQLAlchemy
                # StatementError wrapper. The source loader's question flush
                # still fails before the Activity lookup or staging.
                with pytest.raises(TypeError, match="not JSON serializable"):
                    import_bank_with_activity(db, command)
            finally:
                event.remove(engine, "before_cursor_execute", executing)
                event.remove(db, "after_flush", flushed)
            assert any(
                statement.lstrip().startswith(
                    f"insert into {Question.__table__.fullname}"
                )
                for statement in statements
            )
            assert not any("activities" in statement for statement in statements)
            assert flushes == [db]  # bank creation succeeded; question flush failed
            assert not db.is_active
            assert not any(isinstance(row, Activity) for row in db.new)
            db.rollback()
        with db.begin():
            _scope(db, tenant)
            assert db.scalar(select(QuestionBank)) is None
            assert db.scalar(select(Question)) is None
            assert db.scalar(select(Activity)) is None


@pytest.mark.parametrize("key", ("mode", "pass_threshold"))
def test_raw_null_policy_insert_default_then_update_failure(curriculum_case, key):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db:
        with db.begin():
            _scope(db, tenant)
            _course(db, tenant)
            doc = _raw_doc()
            doc.policy.update(
                pool=None, max_attempts=None, mode=None, pass_threshold=None
            )
            assert lint_bank(doc) == []
            result = _import(db, tenant, doc)
            assert result.applied_policy == (
                ("pool", None),
                ("max_attempts", None),
                ("mode", None),
            )
            activity = next(row for row in db.new if isinstance(row, Activity))
            assert activity.assessment_mode is None and activity.pass_threshold is None
            db.flush()
            activity_id = activity.id
            assert (
                activity.assessment_mode == "graded" and activity.pass_threshold == 0.0
            )
        with db.begin():
            _scope(db, tenant)
            doc = _raw_doc()
            doc.policy[key] = None
            assert lint_bank(doc) == []
            _import(db, tenant, doc)
            activity = next(row for row in db.dirty if isinstance(row, Activity))
            assert (
                getattr(activity, "assessment_mode" if key == "mode" else key) is None
            )
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
        with db.begin():
            _scope(db, tenant)
            activity = db.get(Activity, activity_id)
            assert (
                activity.assessment_mode == "graded" and activity.pass_threshold == 0.0
            )


@pytest.mark.parametrize("use_fact", (False, True))
def test_decoded_reader_uses_seen_header_one_ordered_query_and_preserves_json(
    curriculum_case, use_fact
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = _course(db, tenant)
        doc = _raw_doc()
        doc.questions = [
            {
                "id": "z",
                "stem": "numeric",
                "type": "numeric",
                "correct": 2.5,
                "options": None,
                "rubric_category": "recall",
                "metadata": "ignored",
            },
            {
                "id": "a",
                "stem": "regex",
                "type": "regex",
                "correct": {"pattern": r"^a\d+$", "flags": [None, True]},
                "options": {"metadata": [1, False]},
                "rubric_category": "analysis",
            },
            {
                "id": "m",
                "stem": "null",
                "type": "truefalse",
                "correct": None,
                "options": None,
                "rubric_category": "application",
            },
        ]
        result = _import(db, tenant, doc)
        db.flush()
        seen_bank = db.get(QuestionBank, result.bank_id)
        header: BankDocumentHeader = (
            BankFact(
                id=result.bank_id,
                tenant_id=tenant,
                course_id=course.id,
                chapter_number=2,
                kind="chapter",
                version=1,
            )
            if use_fact
            else seen_bank
        )
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lower())

        event.listen(engine, "before_cursor_execute", record)
        try:
            raw = decoded_doc_from_db(db, bank=header, course_slug="already-seen")
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert len(statements) == 1
        assert statements[0].lstrip().startswith("select")
        assert f"from {Question.__table__.fullname}" in statements[0]
        assert f"order by {Question.__table__.fullname}.ext_id" in statements[0]
        assert QuestionBank.__table__.fullname not in statements[0]
        assert (raw.course, raw.chapter, raw.kind, raw.version, raw.policy) == (
            "already-seen",
            2,
            "chapter",
            1,
            {},
        )
        assert [q["id"] for q in raw.questions] == ["a", "m", "z"]
        assert all(
            list(q)
            == [
                "id",
                "stem",
                "type",
                "options",
                "correct",
                "rubric_category",
                "category",
                "explanation",
                "weight",
            ]
            for q in raw.questions
        )
        assert raw.questions[0]["correct"] == {
            "pattern": r"^a\d+$",
            "flags": [None, True],
        }
        assert raw.questions[0]["options"] == {"metadata": [1, False]}
        assert (
            raw.questions[1]["correct"] is None and raw.questions[1]["options"] is None
        )
        assert (
            raw.questions[2]["correct"] == 2.5 and raw.questions[2]["options"] is None
        )
        assert all(
            q["category"] is None and q["explanation"] == "" and q["weight"] == 1
            for q in raw.questions
        )


def test_decoded_reader_stale_header_returns_raw_empty_doc_with_one_query(
    curriculum_case,
):
    engine, (tenant, _) = curriculum_case
    header = BankFact(
        id=uuid4(),
        tenant_id=tenant,
        course_id=uuid4(),
        chapter_number=None,
        kind="stale",
        version=37,
    )
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lower())

        event.listen(engine, "before_cursor_execute", record)
        try:
            raw = decoded_doc_from_db(db, bank=header, course_slug="stale-slug")
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert (
            len(statements) == 1
            and QuestionBank.__table__.fullname not in statements[0]
        )
        assert (
            raw.course,
            raw.chapter,
            raw.kind,
            raw.version,
            raw.questions,
            raw.policy,
        ) == ("stale-slug", None, "stale", 37, [], {})
        assert not db.new and not db.dirty


BANK_ACTIVITY_TERMS = BankActivityTerms(
    pass_threshold_by_kind={"chapter": 0.0, "mid": 0.60, "final": 0.70},
    fallback_pass_threshold=0.0,
    new_activity_assessment_mode="graded",
    new_activity_grading="auto",
    new_activity_weight=1.0,
)


def test_configured_new_activity_terms_and_raw_missing_weight_preserve_update_omission(
    curriculum_case,
):
    engine, (tenant, _) = curriculum_case
    configured = BankActivityTerms({"chapter": 0.83}, 0.29, "practice", "manual", 4.5)
    changed = BankActivityTerms({"chapter": 0.41}, 0.11, "exam", "auto", 9.5)
    doc = BankDoc(
        "bank-course",
        2,
        "chapter",
        1,
        [
            {
                "id": "q",
                "stem": "Question",
                "type": "numeric",
                "rubric_category": "recall",
                "options": {"tolerance": 0.5},
                "correct": 42,
            },
            {
                "id": "explicit",
                "stem": "Question",
                "type": "single",
                "rubric_category": "recall",
                "options": [True, None],
                "correct": [True],
                "weight": 2.9,
            },
        ],
    )
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        boundary = db.get_transaction()
        _course(db, tenant)
        first = import_bank_with_activity(
            db,
            ImportBankWithActivityCommand(
                tenant_id=tenant,
                doc=doc,
                terms=configured,
                missing_question_weight=7,
            ),
        )
        db.flush()
        activity = db.scalar(select(Activity))
        assert (
            activity.pass_threshold,
            activity.assessment_mode,
            activity.grading,
            activity.weight,
        ) == (0.83, "practice", "manual", 4.5)
        questions = {q.ext_id: q for q in db.scalars(select(Question)).all()}
        assert questions["q"].weight == 7 and questions["explicit"].weight == 2
        assert questions["q"].options == {"tolerance": 0.5}
        assert questions["explicit"].options == [True, None]
        assert "weight" not in doc.questions[0]
        second = import_bank_with_activity(
            db,
            ImportBankWithActivityCommand(
                tenant_id=tenant,
                doc=doc,
                terms=changed,
                missing_question_weight=3,
            ),
        )
        assert first.bank_id == second.bank_id and not second.created
        assert (
            activity.pass_threshold,
            activity.assessment_mode,
            activity.grading,
            activity.weight,
        ) == (0.41, "practice", "manual", 4.5)
        doc.policy = {"mode": "exam", "pass_threshold": 0.96}
        import_bank_with_activity(
            db,
            ImportBankWithActivityCommand(
                tenant_id=tenant,
                doc=doc,
                terms=changed,
                missing_question_weight=3,
            ),
        )
        assert (activity.pass_threshold, activity.assessment_mode) == (0.96, "exam")
        assert db.get_transaction() is boundary
