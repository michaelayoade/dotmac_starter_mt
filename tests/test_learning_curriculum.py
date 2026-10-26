"""Hosted canaries using genuine Kernel + learning migrations and app_user RLS."""

from uuid import uuid4

import pytest
from dotmac_learning import (
    BankAuditContext,
    CreateBankDraftCommand,
    ImportBankDocumentAsDraftCommand,
    LegacyBankWriterRetired,
    PublishBankVersionCommand,
    create_bank_draft,
    import_bank_document_as_draft,
    publish_bank_version,
)
from dotmac_learning.curriculum import (
    CurriculumConflict,
    CurriculumNotFound,
    create_course,
    delete_chapter,
    doc_from_db,
    import_manual,
    load_bank,
    public_catalog,
    upsert_chapter,
)
from dotmac_learning.curriculum_contracts import (
    BankDocument,
    BankQuestion,
    ChapterWrite,
    CourseCreate,
    ImportedChapter,
    ManualImport,
)
from dotmac_learning.curriculum_models import Chapter, Question
from dotmac_learning.models import Course
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog_fixtures
from tests.test_learning_catalog import (
    _scope,
    _tenant,
)

learning_database = learning_catalog_fixtures.learning_database


@pytest.fixture
def curriculum_case(learning_database):
    admin = create_engine(learning_database[0])
    actor = create_engine(learning_database[1])
    tenants = (uuid4(), uuid4())
    try:
        with admin.begin() as conn:
            for tenant in tenants:
                _tenant(conn, tenant)
        yield actor, tenants
    finally:
        actor.dispose()
        admin.dispose()


def _create(db, tenant, slug="new"):
    return create_course(
        db,
        CourseCreate(
            tenant_id=tenant,
            slug=slug,
            title=slug,
            discipline="networking",
            listed=False,
        ),
    )


def test_draft_duplicate_slug_and_public_catalog_separate_fields(curriculum_case):
    engine, (tenant, other) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        draft = _create(db, tenant, " New-Course ")
        assert (draft.slug, draft.status, draft.source_ref, draft.version) == (
            "new-course",
            "draft",
            "in-app",
            1,
        )
        with pytest.raises(CurriculumConflict, match="already exists"):
            _create(db, tenant, "NEW-COURSE")
        row = db.get(Course, draft.id)
        row.listed = True
        db.flush()
        assert public_catalog(db, tenant_id=tenant) == ()
        row.status = "published"
        db.flush()
        assert public_catalog(db, tenant_id=tenant)[0].chapter_count == 0
        upsert_chapter(
            db,
            ChapterWrite(
                tenant_id=tenant,
                course_id=draft.id,
                number=1,
                title="Intro",
                body_md="# Intro",
                body_html="<h1>Intro</h1>",
            ),
        )
        item = public_catalog(db, tenant_id=tenant)[0]
        assert (
            item.course.status == "published"
            and item.course.listed
            and item.chapter_count == 1
        )
        row.listed = False
        db.flush()
        assert public_catalog(db, tenant_id=tenant) == ()
    with Session(engine) as db, db.begin():
        _scope(db, other)
        assert public_catalog(db, tenant_id=other) == ()
        assert _create(db, other, "new-course").tenant_id == other
        with pytest.raises(CurriculumNotFound, match="course not found for tenant"):
            upsert_chapter(
                db,
                ChapterWrite(
                    tenant_id=other,
                    course_id=draft.id,
                    number=1,
                    title="X",
                    body_md="",
                    body_html="",
                ),
            )


def test_authoring_always_bumps_and_delete_matches_course(curriculum_case):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = _create(db, tenant)
        other = _create(db, tenant, "other")
        command = ChapterWrite(
            tenant_id=tenant,
            course_id=course.id,
            number=1,
            title="Intro",
            body_md="x",
            body_html="<p>x</p>",
        )
        first = upsert_chapter(db, command)
        repeat = upsert_chapter(db, command)
        assert first.id == repeat.id
        assert db.get(Course, course.id).version == 3
        with pytest.raises(CurriculumNotFound, match="chapter not found for tenant"):
            delete_chapter(
                db, tenant_id=tenant, course_id=other.id, chapter_id=first.id
            )
        assert db.get(Course, other.id).version == 1
        delete_chapter(db, tenant_id=tenant, course_id=course.id, chapter_id=first.id)
        assert db.get(Course, course.id).version == 4
        assert db.scalar(select(Chapter).where(Chapter.id == first.id)) is None


def test_import_metadata_hash_idempotence_retains_removed_chapters(curriculum_case):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        chapter = ImportedChapter(
            number=1,
            title="Welcome",
            part="I",
            body_html="<h1>Welcome</h1>",
            source_hash="a" * 64,
        )
        command = ManualImport(
            tenant_id=tenant,
            slug="foundation",
            title="Foundation",
            discipline="networking",
            source_ref="foundation@0.1.0",
            chapters=(chapter,),
            new_course_status="published",
            new_course_listed=False,
            new_course_completion_fraction=1.0,
            new_course_certificate_auto_issue=True,
            new_course_certificate_validity_days=None,
            new_course_prerequisite_composition="all",
        )
        first = import_manual(db, command)
        assert (first.version, first.status, first.listed) == (2, "published", False)
        assert import_manual(db, command).version == 2
        assert (
            import_manual(
                db,
                command.model_copy(update={"discipline": "fiber", "source_ref": "v2"}),
            ).version
            == 3
        )
        changed = chapter.model_copy(
            update={"title": "Changed", "source_hash": "b" * 64}
        )
        assert (
            import_manual(
                db, command.model_copy(update={"chapters": (changed,)})
            ).version
            == 4
        )
        assert (
            import_manual(db, command.model_copy(update={"chapters": ()})).version == 4
        )
        row = db.scalar(select(Chapter).where(Chapter.course_id == first.id))
        assert row.title == "Changed" and row.body_md == "" and row.order_index == 1


def _audit(tenant):
    return BankAuditContext(
        tenant_id=tenant,
        actor_type="system",
        source_application="tests",
    )


def _clean_questions(prefix="q"):
    """Ten lint-clean questions: 2/5/3 rubric mix, equal-length options."""
    rubric = ["recall"] * 2 + ["application"] * 5 + ["analysis"] * 3
    return tuple(
        BankQuestion(
            id=f"{prefix}{i}",
            stem=f"Question {i}?",
            type="single",
            rubric_category=rubric[i],
            options=["aaaa", "bbbb", "cccc", "dddd"],
            correct=["aaaa"],
            weight=1,
        )
        for i in range(10)
    )


def _import_and_publish(db, tenant, course, doc):
    report = import_bank_document_as_draft(
        db,
        ImportBankDocumentAsDraftCommand(
            tenant_id=tenant, course_id=course, doc=doc, missing_question_weight=1
        ),
        audit_context=_audit(tenant),
    )
    assert report.lint_violations == ()
    result = publish_bank_version(
        db,
        PublishBankVersionCommand(tenant_id=tenant, version_id=report.version_id),
        audit_context=_audit(tenant),
    )
    assert result.published
    return report.bank_id, result.version


def test_bank_versions_publish_immutable_history_and_plain_course_reference(
    curriculum_case,
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = uuid4()  # source bank course UUID is intentionally not an FK
        doc = BankDocument(
            course="logical",
            chapter=None,
            kind="final",
            version=9,  # ignored: Learning allocates version numbers
            questions=_clean_questions("old-"),
        )
        bank_id, first = _import_and_publish(db, tenant, course, doc)
        assert first.version_no == 1
        old_ids = set(
            db.scalars(select(Question.id).where(Question.bank_id == bank_id)).all()
        )
        assert len(old_ids) == 10
        changed = tuple(
            q.model_copy(update={"category": "safety", "weight": 2})
            for q in _clean_questions("new-")
        )
        bank_id2, second = _import_and_publish(
            db,
            tenant,
            course,
            doc.model_copy(update={"version": 2, "questions": changed}),
        )
        assert bank_id2 == bank_id and second.version_no == 2
        # Published v1 rows survive untouched under their own version.
        assert (
            set(
                db.scalars(
                    select(Question.id).where(Question.bank_version_id == first.id)
                ).all()
            )
            == old_ids
        )
        rebuilt = doc_from_db(
            db, tenant_id=tenant, bank_id=bank_id, course_slug="logical"
        )
        assert rebuilt.version == 2 and rebuilt.policy == {}
        assert [q.id for q in rebuilt.questions] == sorted(q.id for q in changed)
        assert {q.category for q in rebuilt.questions} == {"safety"}
        assert {q.weight for q in rebuilt.questions} == {2}


def test_load_bank_is_a_fail_closed_shim(curriculum_case):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        doc = BankDocument(course="logical", kind="final", questions=_clean_questions())
        with pytest.raises(LegacyBankWriterRetired):
            load_bank(
                db,
                tenant_id=tenant,
                course_id=uuid4(),
                doc=doc,
                missing_question_weight=1,
            )
        from dotmac_learning.curriculum_models import QuestionBank

        assert db.scalar(select(QuestionBank)) is None
        assert db.scalar(select(Question)) is None


def test_caller_root_rollback_and_pending_host_work_survive_expected_rejections(
    curriculum_case,
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db:
        with db.begin():
            _scope(db, tenant)
            pending = Course(
                tenant_id=tenant,
                slug="host-pending",
                title="Host",
                discipline="x",
                source_ref="host",
                status="published",
                listed=False,
            )
            db.add(pending)
            course = _create(db, tenant)
            with pytest.raises(CurriculumConflict):
                _create(db, tenant)
            assert db.in_transaction() and pending.id is not None
            assert db.scalar(
                text("SELECT current_setting('app.current_tenant')")
            ) == str(tenant)
            draft = create_bank_draft(
                db,
                CreateBankDraftCommand(
                    tenant_id=tenant,
                    course_id=course.id,
                    chapter_number=1,
                    kind="chapter",
                ),
                audit_context=_audit(tenant),
            )
            assert draft.id is not None
            db.rollback()  # caller alone owns root outcome
        with db.begin():
            _scope(db, tenant)
            assert db.scalar(select(Course).where(Course.tenant_id == tenant)) is None


MUTATIONS = (
    "create_course",
    "upsert_chapter",
    "delete_chapter",
    "import_manual",
)


def _mutation(db, name, tenant, course, chapter):
    if name == "create_course":
        return _create(db, tenant, "guard-created")
    if name == "upsert_chapter":
        return upsert_chapter(
            db,
            ChapterWrite(
                tenant_id=tenant,
                course_id=course,
                number=1,
                title="Changed",
                body_md="changed",
                body_html="<p>changed</p>",
            ),
        )
    if name == "delete_chapter":
        return delete_chapter(
            db, tenant_id=tenant, course_id=course, chapter_id=chapter
        )
    if name == "import_manual":
        return import_manual(
            db,
            ManualImport(
                tenant_id=tenant,
                slug="guard-base",
                title="Imported",
                discipline="networking",
                source_ref="imported",
                chapters=(),
                new_course_status="published",
                new_course_listed=False,
                new_course_completion_fraction=1.0,
                new_course_certificate_auto_issue=True,
                new_course_certificate_validity_days=None,
                new_course_prerequisite_composition="all",
            ),
        )
    raise AssertionError(name)


def _guard_base(engine, tenant):
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = _create(db, tenant, "guard-base")
        chapter = upsert_chapter(
            db,
            ChapterWrite(
                tenant_id=tenant,
                course_id=course.id,
                number=1,
                title="Intro",
                body_md="intro",
                body_html="<p>intro</p>",
            ),
        )
        return course.id, chapter.id


@pytest.mark.parametrize("name", MUTATIONS)
def test_inactive_valid_mutation_refuses_before_sql_or_autobegin(curriculum_case, name):
    engine, (tenant, _) = curriculum_case
    course, chapter = _guard_base(engine, tenant)
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with Session(engine) as db:
            assert db.get_transaction() is None
            with pytest.raises(TransactionChanged) as refusal:
                _mutation(db, name, tenant, course, chapter)
            assert (
                str(refusal.value)
                == "An already active caller root transaction is required."
            )
            assert statements == []
            assert db.get_transaction() is None
            assert db.get_nested_transaction() is None
    finally:
        event.remove(engine, "before_cursor_execute", observe)


@pytest.mark.parametrize("origin", ("BEGIN", "AUTOBEGIN"))
@pytest.mark.parametrize("savepoint", (False, True))
def test_mutations_preserve_caller_root_nested_identity_and_pending_work(
    curriculum_case, origin, savepoint
):
    engine, (tenant, _) = curriculum_case
    course, chapter = _guard_base(engine, tenant)
    with Session(engine) as db:
        if origin == "BEGIN":
            db.begin()
        _scope(db, tenant)  # caller's first SQL may create its AUTOBEGIN root
        root = db.get_transaction()
        assert root.origin.name == origin
        nested = db.begin_nested() if savepoint else None
        pending = Course(
            tenant_id=tenant,
            slug="guard-pending",
            title="Host pending",
            discipline="x",
            source_ref="host",
            status="published",
            listed=False,
        )
        db.add(pending)
        for name in MUTATIONS:
            _mutation(db, name, tenant, course, chapter)
            assert db.get_transaction() is root and root.is_active
            assert db.get_nested_transaction() is nested
            assert nested is None or nested.is_active
        assert pending.id is not None
        assert db.scalar(select(Course.id).where(Course.id == pending.id)) == pending.id
        assert db.scalar(text("SELECT current_setting('app.current_tenant')")) == str(
            tenant
        )
        db.rollback()  # caller alone removes its nested + root changes
        assert db.get_transaction() is None and db.get_nested_transaction() is None
        with db.begin():
            _scope(db, tenant)
            assert (
                db.scalar(
                    select(Course.id).where(
                        Course.slug.in_(["guard-created", "guard-pending"])
                    )
                )
                is None
            )
            retained = db.scalar(select(Chapter).where(Chapter.id == chapter))
            assert retained.title == "Intro"


@pytest.mark.parametrize("answer", (42, 42.5, "42.5"))
def test_numeric_bank_answer_keeps_json_scalar_kind_through_database(
    curriculum_case, answer
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        from dotmac_learning.curriculum_models import QuestionBank

        bank = QuestionBank(
            tenant_id=tenant,
            course_id=uuid4(),
            chapter_number=None,
            kind="final",
            version=1,
        )
        db.add(bank)
        db.flush()
        db.add(
            Question(
                tenant_id=tenant,
                bank_id=bank.id,
                ext_id="numeric",
                stem="Value?",
                type="numeric",
                options=[],
                correct=answer,
                rubric_category="application",
                explanation="",
                weight=1,
            )
        )
        db.flush()
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        rebuilt = doc_from_db(
            db, tenant_id=tenant, bank_id=bank.id, course_slug="numeric"
        )
        (stored,) = rebuilt.questions
        assert stored.correct == answer
        assert type(stored.correct) is type(answer)
        assert not isinstance(stored.correct, list)
