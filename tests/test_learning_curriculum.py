"""Hosted canaries using genuine Kernel + learning migrations and app_user RLS."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from dotmac_learning.curriculum import (
    CurriculumConflict,
    CurriculumNotFound,
    create_course,
    upsert_chapter,
    delete_chapter,
    import_manual,
    load_bank,
    doc_from_db,
    public_catalog,
)
from dotmac_learning.curriculum_contracts import (
    CourseCreate,
    ChapterWrite,
    ManualImport,
    ImportedChapter,
    BankDocument,
    BankQuestion,
)
from dotmac_learning.models import Course
from dotmac_learning.result_contracts import TransactionChanged
from dotmac_learning.curriculum_models import Chapter, Question
from tests.test_learning_catalog import (
    learning_database as learning_database,
    _tenant,
    _scope,
)


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
        CourseCreate(tenant_id=tenant, slug=slug, title=slug, discipline="networking"),
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


def test_bank_replacement_requested_version_defaults_and_plain_course_reference(
    curriculum_case,
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = uuid4()  # source bank course UUID is intentionally not an FK
        q = BankQuestion(
            id="old",
            stem="Question",
            type="single",
            rubric_category="recall",
            competency="networking",
        )
        doc = BankDocument(
            course="logical", chapter=None, kind="final", version=9, questions=(q,)
        )
        bank = load_bank(db, tenant_id=tenant, course_id=course, doc=doc)
        old_id = db.scalar(select(Question.id).where(Question.bank_id == bank.id))
        next_q = q.model_copy(update={"id": "new", "category": "safety", "weight": 2})
        reloaded = load_bank(
            db,
            tenant_id=tenant,
            course_id=course,
            doc=doc.model_copy(update={"version": 2, "questions": (next_q,)}),
        )
        assert (reloaded.id, reloaded.version) == (bank.id, 2)
        assert db.scalar(select(Question).where(Question.id == old_id)) is None
        rebuilt = doc_from_db(
            db, tenant_id=tenant, bank_id=bank.id, course_slug="logical"
        )
        assert rebuilt.version == 2 and rebuilt.policy == {}
        (item,) = rebuilt.questions
        assert (
            item.id,
            item.category,
            item.options,
            item.correct,
            item.explanation,
            item.weight,
        ) == ("new", "safety", [], [], "", 2)


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
            )
            db.add(pending)
            course = _create(db, tenant)
            with pytest.raises(CurriculumConflict):
                _create(db, tenant)
            assert db.in_transaction() and pending.id is not None
            assert db.scalar(
                text("SELECT current_setting('app.current_tenant')")
            ) == str(tenant)
            bank = load_bank(
                db,
                tenant_id=tenant,
                course_id=course.id,
                doc=BankDocument(course="new", chapter=1, kind="chapter", questions=()),
            )
            assert bank.id is not None
            db.rollback()  # caller alone owns root outcome
        with db.begin():
            _scope(db, tenant)
            assert db.scalar(select(Course).where(Course.tenant_id == tenant)) is None


MUTATIONS = (
    "create_course",
    "upsert_chapter",
    "delete_chapter",
    "import_manual",
    "load_bank",
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
            ),
        )
    if name == "load_bank":
        return load_bank(
            db,
            tenant_id=tenant,
            course_id=course,
            doc=BankDocument(
                course="guard-base",
                kind="final",
                questions=(
                    BankQuestion(
                        id="Q1",
                        stem="Question",
                        type="single",
                        rubric_category="recall",
                        options=["A", "B"],
                        correct=["A"],
                    ),
                ),
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
        doc = BankDocument(
            course="numeric",
            kind="final",
            questions=(
                BankQuestion(
                    id="numeric",
                    stem="Value?",
                    type="numeric",
                    rubric_category="application",
                    correct=answer,
                ),
            ),
        )
        bank = load_bank(db, tenant_id=tenant, course_id=uuid4(), doc=doc)
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        rebuilt = doc_from_db(
            db, tenant_id=tenant, bank_id=bank.id, course_slug="numeric"
        )
        (stored,) = rebuilt.questions
        assert stored.correct == answer
        assert type(stored.correct) is type(answer)
        assert not isinstance(stored.correct, list)
