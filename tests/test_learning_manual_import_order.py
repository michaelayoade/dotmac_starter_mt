"""Authored-only hosted canaries on genuine migrated PostgreSQL and app_user RLS."""

import dotmac_learning as learning
import pytest
from dotmac_learning.curriculum_models import Chapter
from dotmac_learning.models import Course
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_curriculum as curriculum_fixtures
from tests.test_learning_catalog import _scope

learning_database = curriculum_fixtures.learning_database
curriculum_case = curriculum_fixtures.curriculum_case


def _header(tenant, **overrides):
    values = {
        "tenant_id": tenant,
        "slug": "manual",
        "title": "Manual",
        "discipline": "x",
        "source_ref": "v1",
    }
    values.update(overrides)
    return learning.ManualImportHeader(**values)


def _doc(number=1, **overrides):
    values = {
        "number": number,
        "title": "Chapter",
        "part": "I",
        "body_html": "old",
        "source_hash": "one",
    }
    values.update(overrides)
    return learning.ImportedChapter(**values)


def _reads(db, trace):
    def observe(state):
        if state.is_select:
            entity = state.statement.column_descriptions[0].get("entity")
            if entity in (Course, Chapter):
                trace.append("course" if entity is Course else "chapter")

    event.listen(db, "do_orm_execute", observe)
    return observe


def test_cross_tenant_same_slug_isolated_and_foreign_command_refused(curriculum_case):
    engine, (tenant, other) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, other)
        foreign = learning.import_manual_chapters(
            db, _header(other), chapters=(_doc(),)
        )
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        own = learning.import_manual_chapters(db, _header(tenant), chapters=(_doc(),))
        assert own.id != foreign.id and own.tenant_id == tenant
        assert learning.get_course(db, tenant_id=other, course_id=foreign.id) is None
        assert (
            len(learning.authoring_chapters(db, tenant_id=tenant, course_id=own.id))
            == 1
        )
        root = db.get_transaction()
        with pytest.raises(IntegrityError), db.begin_nested():
            learning.import_manual_chapters(
                db, _header(other, slug="forbidden"), chapters=()
            )
        assert db.get_transaction() is root and root.is_active


@pytest.mark.parametrize("autoflush", (False, True))
@pytest.mark.parametrize("savepoint", (False, True))
@pytest.mark.parametrize("existing", (False, True))
@pytest.mark.parametrize("finish", ("commit", "rollback"))
def test_later_parser_failure_stages_prior_work_for_caller(
    curriculum_case, autoflush, savepoint, existing, finish
):
    engine, (tenant, _) = curriculum_case
    if existing:
        with Session(engine) as setup, setup.begin():
            _scope(setup, tenant)
            learning.import_manual_chapters(setup, _header(tenant), chapters=(_doc(),))
    with Session(engine, autoflush=autoflush) as db:
        db.begin()
        _scope(db, tenant)
        root = db.get_transaction()
        nested = db.begin_nested() if savepoint else None
        trace = []
        observe = _reads(db, trace)
        failure = ValueError("second source cannot decode")

        def chapters():
            trace.append("parse-one")
            # Inspection uses identity-map state, before any query can autoflush.
            course = next(
                row for row in db.identity_map.values() if isinstance(row, Course)
            )
            assert course.source_ref == "v2"
            assert course.version == (2 if existing else 1)
            yield _doc(title="Changed", body_html="changed", source_hash="two")
            trace.append("parse-two")
            if existing:
                row = next(
                    row for row in db.identity_map.values() if isinstance(row, Chapter)
                )
                assert row.title == "Changed" and row in db.dirty
            else:
                row = next(row for row in db.new if isinstance(row, Chapter))
                assert row.title == "Changed"
            assert course.version == (2 if existing else 1)
            raise failure

        try:
            with pytest.raises(ValueError) as caught:
                learning.import_manual_chapters(
                    db, _header(tenant, source_ref="v2"), chapters=chapters()
                )
        finally:
            event.remove(db, "do_orm_execute", observe)
        assert caught.value is failure
        assert trace == ["course", "parse-one", "chapter", "parse-two"]
        assert db.get_transaction() is root and root.is_active
        assert db.get_nested_transaction() is nested
        if finish == "commit":
            if nested is not None:
                nested.commit()
            db.commit()
        else:
            db.rollback()
        with db.begin():
            _scope(db, tenant)
            stored = db.scalar(select(Course).where(Course.slug == "manual"))
            if finish == "rollback" and not existing:
                assert stored is None
            else:
                assert stored.source_ref == ("v2" if finish == "commit" else "v1")
                assert stored.version == (2 if existing else 1)
                row = db.scalar(select(Chapter).where(Chapter.course_id == stored.id))
                assert row.title == ("Changed" if finish == "commit" else "Chapter")


@pytest.mark.parametrize("existing", (False, True))
def test_first_source_failure_after_course_and_metadata_before_chapter_query(
    curriculum_case, existing
):
    engine, (tenant, _) = curriculum_case
    with Session(engine, autoflush=False) as db, db.begin():
        _scope(db, tenant)
        if existing:
            learning.import_manual_chapters(db, _header(tenant), chapters=())
        trace = []
        observe = _reads(db, trace)
        failure = OSError("source unreadable")

        def chapters():
            trace.append("parse")
            course = next(
                row for row in db.identity_map.values() if isinstance(row, Course)
            )
            assert (
                course.id is not None
                and course.title == "Changed"
                and course.version == 1
            )
            raise failure
            yield _doc()  # pragma: no cover - this is deliberately a failing iterator

        try:
            with pytest.raises(OSError) as caught:
                learning.import_manual_chapters(
                    db, _header(tenant, title="Changed"), chapters=chapters()
                )
        finally:
            event.remove(db, "do_orm_execute", observe)
        assert caught.value is failure
        assert trace == ["course", "parse"]
        assert db.get_transaction().is_active


@pytest.mark.parametrize("stage", ("course-query", "new-course-flush", "chapter-query"))
def test_database_failure_precedes_next_source_at_legacy_stage(curriculum_case, stage):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        if stage == "chapter-query":
            learning.import_manual_chapters(db, _header(tenant), chapters=())
        trace = []
        # A real NOT NULL violation in a caller object, not a mocked DB exception.
        pending = Course(
            tenant_id=tenant, slug="dirty", title=None, discipline="x", source_ref="x"
        )
        if stage == "course-query":
            db.add(pending)
        if stage == "new-course-flush":
            db.autoflush = False
            db.add(pending)

        def chapters():
            trace.append("parse-one")
            db.add(pending)
            yield _doc()
            trace.append("parse-two")
            raise ValueError("later source failure")

        with pytest.raises(IntegrityError):
            learning.import_manual_chapters(db, _header(tenant), chapters=chapters())
        assert trace == (["parse-one"] if stage == "chapter-query" else [])
        assert not db.get_transaction().is_active
        db.rollback()


@pytest.mark.parametrize("autoflush", (False, True))
def test_duplicate_numbers_follow_caller_autoflush(curriculum_case, autoflush):
    engine, (tenant, _) = curriculum_case
    with Session(engine, autoflush=autoflush) as db, db.begin():
        _scope(db, tenant)
        trace = []

        def chapters():
            trace.append("first")
            yield _doc()
            trace.append("second")
            yield _doc(title="Second", source_hash="two")

        if autoflush:
            course = learning.import_manual_chapters(
                db, _header(tenant), chapters=chapters()
            )
            rows = learning.authoring_chapters(
                db, tenant_id=tenant, course_id=course.id
            )
            assert len(rows) == 1 and rows[0].title == "Second" and course.version == 2
        else:
            with pytest.raises(IntegrityError):
                learning.import_manual_chapters(
                    db, _header(tenant), chapters=chapters()
                )
            db.rollback()
        assert trace == ["first", "second"]


@pytest.mark.parametrize("origin", ("BEGIN", "AUTOBEGIN"))
@pytest.mark.parametrize("savepoint", (False, True))
def test_empty_metadata_skip_updates_counts_body_source_order_and_no_final_refresh(
    curriculum_case, origin, savepoint
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db:
        if origin == "BEGIN":
            db.begin()
        _scope(db, tenant)
        root = db.get_transaction()
        assert root.origin.name == origin
        nested = db.begin_nested() if savepoint else None
        empty = learning.import_manual_chapters(db, _header(tenant), chapters=())
        assert empty.version == 1
        assert (
            learning.authoring_chapters(db, tenant_id=tenant, course_id=empty.id) == ()
        )
        first = learning.import_manual_chapters(
            db, _header(tenant), chapters=(_doc(7), _doc(2))
        )
        assert first.version == 2
        rows = learning.authoring_chapters(db, tenant_id=tenant, course_id=first.id)
        assert len(rows) == 2
        chapter = db.get(Chapter, next(row.id for row in rows if row.number == 7))
        assert chapter.body_md == "" and chapter.order_index == 7
        chapter.body_md = "editable source"
        chapter.order_index = 91
        db.flush()
        trace = []
        observe = _reads(db, trace)
        try:
            skipped = learning.import_manual_chapters(
                db,
                _header(tenant),
                chapters=(_doc(7, title="Ignore same hash", body_html="ignore"),),
            )
        finally:
            event.remove(db, "do_orm_execute", observe)
        assert trace == ["course", "chapter"]  # no refresh SELECT after the final flush
        assert (
            skipped.version == 2
            and chapter.title == "Chapter"
            and chapter.body_html == "old"
        )
        updated = learning.import_manual_chapters(
            db,
            _header(tenant, title="New", discipline="fiber", source_ref="v2"),
            chapters=(
                _doc(7, title="Updated", part="II", body_html="new", source_hash="two"),
            ),
        )
        assert updated.version == 3
        assert (updated.title, updated.discipline, updated.source_ref) == (
            "New",
            "fiber",
            "v2",
        )
        assert (
            chapter.title,
            chapter.part,
            chapter.body_html,
            chapter.source_hash,
        ) == ("Updated", "II", "new", "two")
        assert chapter.body_md == "editable source" and chapter.order_index == 91
        meta = learning.import_manual_chapters(
            db, _header(tenant, source_ref="v3"), chapters=()
        )
        assert meta.version == 4
        assert (
            len(learning.authoring_chapters(db, tenant_id=tenant, course_id=first.id))
            == 2
        )
        assert first.version == 2  # intentional immutable value boundary
        assert db.get_transaction() is root and root.is_active
        assert db.get_nested_transaction() is nested
        db.rollback()


@pytest.mark.parametrize("when", ("yield", "exhaustion"))
@pytest.mark.parametrize(
    "action", ("commit", "rollback", "replace-root", "begin-nested", "end-nested")
)
def test_iterator_transaction_change_refused_before_chapter_sql(
    curriculum_case, when, action
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db:
        db.begin()
        _scope(db, tenant)
        if action == "end-nested":
            db.begin_nested()
        trace = []
        observe = _reads(db, trace)

        def chapters():
            if when == "exhaustion":
                yield _doc()
            if action == "commit":
                db.commit()
            elif action == "rollback":
                db.rollback()
            elif action == "replace-root":
                db.rollback()
                db.begin()
                _scope(db, tenant)
            elif action == "begin-nested":
                db.begin_nested()
            else:
                db.get_nested_transaction().commit()
            if when == "yield":
                yield _doc()

        try:
            with pytest.raises(learning.TransactionChanged):
                learning.import_manual_chapters(
                    db, _header(tenant), chapters=chapters()
                )
        finally:
            event.remove(db, "do_orm_execute", observe)
        assert trace == (["course", "chapter"] if when == "exhaustion" else ["course"])
        db.rollback()


def test_inactive_root_before_iterating_or_sql(curriculum_case):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db:
        trace = []
        observe = _reads(db, trace)

        def chapters():
            pytest.fail("iterated without caller root")
            yield _doc()

        try:
            with pytest.raises(
                learning.TransactionChanged, match="already active caller root"
            ):
                learning.import_manual_chapters(
                    db, _header(tenant), chapters=chapters()
                )
        finally:
            event.remove(db, "do_orm_execute", observe)
        assert trace == [] and db.get_transaction() is None


@pytest.mark.parametrize("existing", (False, True))
def test_empty_source_fact_projection_has_no_default_or_refresh_select(
    curriculum_case, existing
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        if existing:
            learning.import_manual_chapters(db, _header(tenant), chapters=())
        statements = []

        def observe(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", observe)
        try:
            fact = learning.import_manual_chapters(
                db, _header(tenant, source_ref="v2"), chapters=()
            )
            # Access every field while the SQL observer remains installed.
            values = [getattr(fact, name) for name in fact.__dataclass_fields__]
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 1  # original course lookup only
        assert fact.version == (2 if existing else 1)
        assert fact.status == "published" and fact.listed is False
        assert fact.created_at is not None and fact.updated_at is not None
        assert len(values) == len(learning.CourseFact.__dataclass_fields__)


def test_original_tuple_contract_and_refresh_query_unchanged(curriculum_case):
    engine, (tenant, _) = curriculum_case
    header = _header(tenant)
    assert tuple(header.model_fields) == (
        "tenant_id",
        "slug",
        "title",
        "discipline",
        "source_ref",
    )
    command = learning.ManualImport(**header.model_dump())
    assert isinstance(command, learning.ManualImportHeader) and command.chapters == ()
    assert tuple(command.model_fields) == (*tuple(header.model_fields), "chapters")
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        trace = []
        observe = _reads(db, trace)
        try:
            first = learning.import_manual(
                db, command.model_copy(update={"chapters": (_doc(),)})
            )
            repeat = learning.import_manual(
                db, command.model_copy(update={"chapters": (_doc(),)})
            )
        finally:
            event.remove(db, "do_orm_execute", observe)
        assert trace == ["course", "chapter", "course"] * 2
        assert first.id == repeat.id and first.version == repeat.version == 2
