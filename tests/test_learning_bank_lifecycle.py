"""Hosted PostgreSQL canaries for the question-bank version lifecycle.

Authored against genuine Kernel + Learning migrations (lr_0010 head) and
app_user RLS, following the curriculum fixture. Never run locally.
"""

from uuid import uuid4

import dotmac_learning as learning
import pytest
from dotmac_kernel.audit import AuditEvent
from dotmac_learning.curriculum_models import Question, QuestionBank
from dotmac_learning.models import Activity, Course
from dotmac_learning.participation_models import CoursePrerequisite
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from tests import test_learning_curriculum as curriculum_fixtures
from tests.test_learning_catalog import _person, _scope, _tenant

learning_database = curriculum_fixtures.learning_database


@pytest.fixture
def bank_case(learning_database):
    admin = create_engine(learning_database[0])
    actor = create_engine(learning_database[1])
    tenants = (uuid4(), uuid4())
    try:
        with admin.begin() as conn:
            for tenant in tenants:
                _tenant(conn, tenant)
            person = _person(conn, tenants[0])
        yield actor, tenants, person
    finally:
        actor.dispose()
        admin.dispose()


def _audit(tenant):
    # Kernel requires an actor id for non-system actors; the test
    # operator is a system actor, as in the sibling hosted suites.
    return learning.BankAuditContext(
        tenant_id=tenant, actor_type="system", source_application="tests"
    )


def _clean_doc(correct="aaaa", prefix="q", policy=None):
    rubric = ["recall"] * 2 + ["application"] * 5 + ["analysis"] * 3
    return learning.BankDocument(
        course="bank-course",
        chapter=1,
        kind="chapter",
        version=99,
        questions=tuple(
            learning.BankQuestion(
                id=f"{prefix}{i}",
                stem=f"Question {i}?",
                type="single",
                rubric_category=rubric[i],
                options=["aaaa", "bbbb", "cccc", "dddd"],
                correct=[correct],
                weight=1,
            )
            for i in range(10)
        ),
        policy={} if policy is None else policy,
    )


def _course(db, tenant):
    return learning.create_draft_course(
        db,
        learning.CourseCreate(
            tenant_id=tenant,
            slug="bank-course",
            title="Bank",
            discipline="x",
            listed=False,
        ),
    )


def _import(db, tenant, course_id, doc, **kwargs):
    return learning.import_bank_document_as_draft(
        db,
        learning.ImportBankDocumentAsDraftCommand(
            tenant_id=tenant,
            course_id=course_id,
            doc=doc,
            missing_question_weight=1,
            **kwargs,
        ),
        audit_context=_audit(tenant),
    )


def _publish(db, tenant, version_id, **kwargs):
    return learning.publish_bank_version(
        db,
        learning.PublishBankVersionCommand(
            tenant_id=tenant, version_id=version_id, **kwargs
        ),
        audit_context=_audit(tenant),
    )


def test_publish_repoint_attempt_pin_and_pinned_grading(bank_case):
    engine, (tenant, _other), person = bank_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        assert db.scalar(text("SELECT current_user")) == "app_user"
        course = _course(db, tenant)
        report = _import(db, tenant, course.id, _clean_doc())
        assert report.outcome == "created" and report.lint_violations == ()
        first = _publish(db, tenant, report.version_id)
        assert first.published and first.version.version_no == 1
        bank = db.get(QuestionBank, report.bank_id)
        assert bank.version == 1 and bank.current_version_id == first.version.id
        activity = Activity(
            tenant_id=tenant,
            course_id=course.id,
            chapter_number=1,
            type="mcq_test",
            bank_id=report.bank_id,
            title="Chapter 1 test",
            pass_threshold=0.5,
            grading="auto",
            weight=1.0,
        )
        db.add(activity)
        db.flush()
        assert activity.bank_version_id is None  # publish never repoints
        learning.repoint_activity_bank_version(
            db,
            learning.RepointActivityBankVersionCommand(
                tenant_id=tenant,
                activity_id=activity.id,
                version_id=first.version.id,
            ),
            audit_context=_audit(tenant),
        )
        opened = learning.open_or_create_attempt(
            db,
            learning.OpenAttemptCommand(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity.id,
                all_ext_ids=[f"q{i}" for i in range(10)],
                count=10,
            ),
        )
        assert opened.attempt.bank_version_id == first.version.id
        activity_id, bank_id, v1_id = activity.id, report.bank_id, first.version.id
    # Publish v2 with flipped keys; the open sitting still grades on v1.
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        draft = learning.create_bank_draft(
            db,
            learning.CreateBankDraftCommand(
                tenant_id=tenant,
                course_id=course.id,
                chapter_number=1,
                kind="chapter",
            ),
            audit_context=_audit(tenant),
        )
        for question in learning.bank_version_questions(
            db, tenant_id=tenant, version_id=draft.id
        ).questions:
            learning.upsert_draft_question(
                db,
                learning.UpsertDraftQuestionCommand(
                    tenant_id=tenant,
                    version_id=draft.id,
                    question=learning.DraftQuestionWrite(
                        ext_id=question.ext_id,
                        stem=question.stem,
                        type=question.type,
                        rubric_category=question.rubric_category,
                        options=question.options,
                        correct=["bbbb"],
                        category=question.category,
                        explanation=question.explanation,
                        weight=question.weight,
                    ),
                ),
                audit_context=_audit(tenant),
            )
        second = _publish(db, tenant, draft.id)
        assert second.published and second.version.version_no == 2
        activity = db.get(Activity, activity_id)
        assert activity.bank_version_id == v1_id  # still not auto-repointed
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        result = learning.submit_mcq_activity(
            db,
            learning.SubmitMCQCommand(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity_id,
                answers={f"q{i}": ["aaaa"] for i in range(10)},
                only_ext_ids=[f"q{i}" for i in range(10)],
                missing_question_weight=1,
            ),
        )
        assert result.score is not None and result.score.passed
        assert result.score.fraction == 1.0  # graded against pinned v1
        serving = learning.questions_for(db, tenant_id=tenant, bank_id=bank_id)
        assert {tuple(q["correct"]) for q in serving} == {("bbbb",)}


def test_import_conflict_matrix_and_lint_gate(bank_case):
    engine, (tenant, _), _person_id = bank_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = _course(db, tenant)
        _import(db, tenant, course.id, _clean_doc())
        course_id = course.id
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        # A second import over an untouched imported draft needs the flag.
        # No rollback: the caller catches the refusal and commits, which
        # is the only way the staged refusal audit persists.
        with pytest.raises(learning.BankImportRefused):
            _import(db, tenant, course_id, _clean_doc("bbbb", prefix="r"))
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        refusals = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "question_bank.import_refused")
        )
        assert refusals == 1
        replaced = _import(
            db,
            tenant,
            course_id,
            _clean_doc("bbbb", prefix="r"),
            replace_imported_draft=True,
        )
        assert replaced.outcome == "replaced_imported_draft"
        replaced_id = replaced.version_id
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        # Touch the imported draft in the UI sense: replace is now refused.
        learning.delete_draft_question(
            db,
            learning.DeleteDraftQuestionCommand(
                tenant_id=tenant, version_id=replaced_id, ext_id="r0"
            ),
            audit_context=_audit(tenant),
        )
        # Restore the deleted question so the draft stays publishable.
        learning.upsert_draft_question(
            db,
            learning.UpsertDraftQuestionCommand(
                tenant_id=tenant,
                version_id=replaced_id,
                question=learning.DraftQuestionWrite(
                    ext_id="r0",
                    stem="Question 0?",
                    type="single",
                    rubric_category="recall",
                    options=["aaaa", "bbbb", "cccc", "dddd"],
                    correct=["bbbb"],
                    weight=1,
                ),
            ),
            audit_context=_audit(tenant),
        )
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        with pytest.raises(learning.BankImportRefused):
            _import(
                db,
                tenant,
                course_id,
                _clean_doc(),
                replace_imported_draft=True,
            )
        db.rollback()
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = db.get(Course, course_id)
        published = _publish(db, tenant, replaced_id)
        assert published.published and published.version.version_no == 1
        # Hash-equal re-import of the published content is a no-op.
        again = _import(db, tenant, course.id, _clean_doc("bbbb", prefix="r"))
        assert again.outcome == "noop_hash_equal"
        # A lint-failing document lands as an unrepublishable draft.
        bad = learning.BankDocument(
            course="bank-course",
            chapter=3,
            kind="chapter",
            questions=(
                learning.BankQuestion(
                    id="only",
                    stem="S",
                    type="single",
                    rubric_category="recall",
                    options=["A"],
                    correct=["A"],
                    weight=1,
                ),
            ),
        )
        failing = _import(db, tenant, course.id, bad)
        assert failing.lint_violations != ()
        with pytest.raises(learning.BankPublishRefused):
            _publish(db, tenant, failing.version_id)
        # Published rows are immutable to draft commands.
        with pytest.raises(learning.BankVersionImmutable):
            learning.delete_draft_question(
                db,
                learning.DeleteDraftQuestionCommand(
                    tenant_id=tenant,
                    version_id=published.version.id,
                    ext_id="r1",
                ),
                audit_context=_audit(tenant),
            )


def test_retire_sitting_projection_and_tenant_isolation(bank_case):
    engine, (tenant, other), person = bank_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = _course(db, tenant)
        report = _import(db, tenant, course.id, _clean_doc())
        first = _publish(db, tenant, report.version_id)
        sitting = learning.sitting_questions(
            db, tenant_id=tenant, version_id=first.version.id, sitter_id=person
        )
        assert len(sitting) == 10
        subset = learning.sitting_questions(
            db,
            tenant_id=tenant,
            version_id=first.version.id,
            sitter_id=person,
            only_ext_ids=["q1", "q0"],
        )
        assert [q.ext_id for q in subset] == ["q1", "q0"]
        # A requested question outside the version refuses, never shortens.
        with pytest.raises(learning.BankVersionNotFound):
            learning.sitting_questions(
                db,
                tenant_id=tenant,
                version_id=first.version.id,
                sitter_id=person,
                only_ext_ids=["q0", "missing"],
            )
        # Stored lint is draft-only: a published record is never rewritten.
        with pytest.raises(learning.BankVersionImmutable):
            learning.lint_bank_version(
                db, tenant_id=tenant, version_id=first.version.id
            )
        assert all(not hasattr(q, "correct") for q in sitting)
        assert all(not hasattr(q, "explanation") for q in sitting)
        exported = learning.export_bank_version(
            db,
            tenant_id=tenant,
            version_id=first.version.id,
            course_slug="bank-course",
            audit_context=_audit(tenant),
        )
        assert exported.version == 1 and len(exported.questions) == 10
        retired = learning.retire_bank_version(
            db,
            learning.RetireBankVersionCommand(
                tenant_id=tenant,
                version_id=first.version.id,
                reason="git_exposure",
            ),
            audit_context=_audit(tenant),
        )
        assert retired.state == "retired"
        bank = db.get(QuestionBank, report.bank_id)
        assert bank.current_version_id is None
        # The legacy mirror is last-published semantics: retiring the
        # current version does not rewrite it.
        assert bank.version == 1
        # A retired version serves no new sitting projection.
        with pytest.raises(learning.BankLifecycleConflict):
            learning.sitting_questions(
                db, tenant_id=tenant, version_id=first.version.id, sitter_id=person
            )
        history = learning.bank_version_history(
            db, tenant_id=tenant, bank_id=report.bank_id
        )
        assert [h.state for h in history] == ["retired"]
        # History carries no content fields.
        assert not hasattr(history[0], "questions")
    with Session(engine) as db, db.begin():
        _scope(db, other)
        with pytest.raises(learning.BankVersionNotFound):
            learning.bank_version_questions(
                db, tenant_id=other, version_id=first.version.id
            )
        assert db.scalars(select(Question)).all() == []


def test_prerequisite_reconcile_deletes_only_yaml_import_rows(bank_case):
    engine, (tenant, _), _person_id = bank_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        first = _course(db, tenant)
        second = learning.create_draft_course(
            db,
            learning.CourseCreate(
                tenant_id=tenant,
                slug="second",
                title="Second",
                discipline="x",
                listed=False,
            ),
        )
        db.add_all(
            [
                CoursePrerequisite(
                    tenant_id=tenant,
                    course_id=second.id,
                    requires_course_id=first.id,
                    origin="ui",
                ),
            ]
        )
        db.flush()
        result = learning.reconcile_prerequisites(
            db,
            learning.ReconcilePrerequisitesCommand(
                tenant_id=tenant, discipline="x", declarations=()
            ),
        )
        assert result.removed == 0
        assert len(db.scalars(select(CoursePrerequisite)).all()) == 1
        # File-owned rows are still reconciled away when undeclared.
        db.add(
            CoursePrerequisite(
                tenant_id=tenant,
                course_id=first.id,
                requires_course_id=second.id,
                origin="yaml_import",
            )
        )
        db.flush()
        result = learning.reconcile_prerequisites(
            db,
            learning.ReconcilePrerequisitesCommand(
                tenant_id=tenant, discipline="x", declarations=()
            ),
        )
        assert result.removed == 1
        remaining = db.scalars(select(CoursePrerequisite)).all()
        assert [row.origin for row in remaining] == ["ui"]


def test_prerequisite_without_origin_is_ui_and_survives_reconcile(bank_case):
    engine, (tenant, _), _person_id = bank_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        first = _course(db, tenant)
        second = learning.create_draft_course(
            db,
            learning.CourseCreate(
                tenant_id=tenant,
                slug="second",
                title="Second",
                discipline="x",
                listed=False,
            ),
        )
        # The natural ORM construction names no origin: the standing
        # default must be fail-closed (ui), never file-owned.
        db.add(
            CoursePrerequisite(
                tenant_id=tenant,
                course_id=second.id,
                requires_course_id=first.id,
            )
        )
        db.flush()
        stored = db.scalars(select(CoursePrerequisite)).one()
        assert stored.origin == "ui"
        result = learning.reconcile_prerequisites(
            db,
            learning.ReconcilePrerequisitesCommand(
                tenant_id=tenant, discipline="x", declarations=()
            ),
        )
        assert result.removed == 0
        assert len(db.scalars(select(CoursePrerequisite)).all()) == 1
