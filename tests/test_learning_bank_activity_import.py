"""Hosted canaries for the retired bank/Activity import and its successor.

The legacy ``import_bank_with_activity`` destructively replaced questions
and unconditionally reset an existing Activity's title/pass_threshold.
It is now a fail-closed shim; YAML lands via import-as-draft, which
never writes an Activity row (the pass-mark regression guard below).
"""

from uuid import uuid4

import pytest
from dotmac_learning import (
    BankAuditContext,
    BankImportRefused,
    ImportBankDocumentAsDraftCommand,
    LegacyBankWriterRetired,
    decoded_doc_from_db,
    import_bank_document_as_draft,
)
from dotmac_learning.bank_activity_contracts import (
    BankActivityTerms,
    ImportBankWithActivityCommand,
)
from dotmac_learning.bank_activity_import import import_bank_with_activity
from dotmac_learning.curriculum import create_course
from dotmac_learning.curriculum_contracts import (
    BankDocument,
    BankFact,
    BankQuestion,
    CourseCreate,
)
from dotmac_learning.curriculum_models import Question, QuestionBank
from dotmac_learning.models import Activity
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from tests import test_learning_curriculum as curriculum_fixtures
from tests.test_learning_catalog import _scope

learning_database = curriculum_fixtures.learning_database
curriculum_case = curriculum_fixtures.curriculum_case

BANK_ACTIVITY_TERMS = BankActivityTerms(
    pass_threshold_by_kind={"chapter": 0.0, "mid": 0.60, "final": 0.70},
    fallback_pass_threshold=0.0,
    new_activity_assessment_mode="graded",
    new_activity_grading="auto",
    new_activity_weight=1.0,
    new_activity_scoring_policy="best",
    new_activity_min_attempt_interval_seconds=0,
)


def _audit(tenant):
    return BankAuditContext(
        tenant_id=tenant, actor_type="system", source_application="tests"
    )


def _doc(*, kind="chapter", chapter=2, policy=None):
    return BankDocument(
        course="bank-course",
        chapter=chapter,
        kind=kind,
        questions=(
            BankQuestion(
                id="first",
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


def test_legacy_import_is_fail_closed_without_any_sql(curriculum_case):
    engine, (tenant, _) = curriculum_case
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _course(db, tenant)
    event.listen(engine, "before_cursor_execute", record)
    try:
        with Session(engine) as db, db.begin():
            _scope(db, tenant)
            statements.clear()
            with pytest.raises(LegacyBankWriterRetired):
                import_bank_with_activity(
                    db,
                    ImportBankWithActivityCommand(
                        tenant_id=tenant,
                        doc=_doc(),
                        terms=BANK_ACTIVITY_TERMS,
                        missing_question_weight=1,
                    ),
                )
            assert statements == []
            assert db.scalar(select(QuestionBank)) is None
            assert db.scalar(select(Activity)) is None
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_import_as_draft_never_resets_activity_pass_mark_or_title(curriculum_case):
    """Regression: re-import must not touch Activity policy (pass-mark bug)."""
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = _course(db, tenant)
        report = import_bank_document_as_draft(
            db,
            ImportBankDocumentAsDraftCommand(
                tenant_id=tenant,
                course_id=course.id,
                doc=_doc(policy={"pass_threshold": 0.25}),
                missing_question_weight=1,
            ),
            audit_context=_audit(tenant),
        )
        assert report.policy_proposals == (("pass_threshold", 0.25),)
        activity = Activity(
            tenant_id=tenant,
            course_id=course.id,
            chapter_number=2,
            type="mcq_test",
            bank_id=report.bank_id,
            title="Admin title",
            pass_threshold=0.9,
            assessment_mode="exam",
            grading="auto",
            weight=3.0,
            question_count=7,
            max_attempts=2,
        )
        db.add(activity)
        db.flush()
        activity_id = activity.id
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        # A second import of changed content replaces nothing on the
        # Activity: it is refused while the first draft exists, and the
        # admin-set fields survive byte-for-byte.
        with pytest.raises(BankImportRefused) as refusal:
            import_bank_document_as_draft(
                db,
                ImportBankDocumentAsDraftCommand(
                    tenant_id=tenant,
                    course_id=course.id,
                    doc=_doc(policy={"pass_threshold": 0.1}),
                    missing_question_weight=1,
                ),
                audit_context=_audit(tenant),
            )
        assert "draft already exists" in str(refusal.value)
        db.rollback()
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        stored = db.get(Activity, activity_id)
        assert stored.title == "Admin title"
        assert stored.pass_threshold == 0.9
        assert stored.assessment_mode == "exam"
        assert stored.question_count == 7
        assert stored.max_attempts == 2
        assert stored.bank_version_id is None


def test_decoded_reader_serves_historical_rows_with_one_query(curriculum_case):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        bank = QuestionBank(
            tenant_id=tenant,
            course_id=uuid4(),
            chapter_number=2,
            kind="chapter",
            version=4,
        )
        db.add(bank)
        db.flush()
        db.add(
            Question(
                tenant_id=tenant,
                bank_id=bank.id,
                ext_id="first",
                stem="Question",
                type="single",
                options=["A", "B"],
                correct=["A"],
                rubric_category="recall",
                explanation="",
                weight=1,
            )
        )
        db.flush()
        header = BankFact(
            id=bank.id,
            tenant_id=tenant,
            course_id=bank.course_id,
            chapter_number=2,
            kind="chapter",
            version=4,
        )
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            doc = decoded_doc_from_db(db, bank=header, course_slug="bank-course")
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert [q["id"] for q in doc.questions] == ["first"]
        assert doc.version == 4 and doc.policy == {}
        assert len(statements) == 1
        assert "question_banks" not in statements[0].lower()


def test_decoded_reader_stale_header_returns_empty_doc(curriculum_case):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        assert db.scalar(text("SELECT current_user")) == "app_user"
        header = BankFact(
            id=uuid4(),
            tenant_id=tenant,
            course_id=uuid4(),
            chapter_number=None,
            kind="final",
            version=1,
        )
        doc = decoded_doc_from_db(db, bank=header, course_slug="missing")
        assert doc.questions == [] and doc.policy == {}
