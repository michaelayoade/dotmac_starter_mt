"""Authored-only hosted PostgreSQL canaries; genuine migrations and app_user RLS."""

from uuid import uuid4

import pytest
from dotmac_learning import AuthorChapterCommand, author_chapter
from dotmac_learning.curriculum import CurriculumNotFound, upsert_chapter
from dotmac_learning.curriculum_contracts import ChapterWrite
from dotmac_learning.curriculum_models import Chapter
from dotmac_learning.models import Course
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from tests import test_learning_curriculum as curriculum_fixtures
from tests.test_learning_catalog import _scope
from tests.test_learning_curriculum import _create

learning_database = curriculum_fixtures.learning_database
curriculum_case = curriculum_fixtures.curriculum_case


def _command(tenant, course, number=3):
    return AuthorChapterCommand(
        tenant_id=tenant,
        course_id=course,
        number=number,
        title="Authored",
        part="III",
        body_md="# unnormalized  markdown\n",
    )


def _observe_reads(db, events):
    def observe(state):
        if state.is_select:
            entity = state.statement.column_descriptions[0].get("entity")
            if entity in (Course, Chapter):
                events.append("course" if entity is Course else "chapter")

    event.listen(db, "do_orm_execute", observe)
    return observe


@pytest.mark.parametrize("existing", (False, True))
@pytest.mark.parametrize("savepoint", (False, True))
@pytest.mark.parametrize("origin", ("BEGIN", "AUTOBEGIN"))
def test_render_once_on_same_session_root_then_write_repeat_and_caller_rollback(
    curriculum_case, existing, savepoint, origin
):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = _create(db, tenant)
        command = _command(tenant, course.id)
        if existing:
            original = upsert_chapter(
                db, ChapterWrite(**command.model_dump(), body_html="old")
            )
            row = db.get(Chapter, original.id)
            row.order_index = 91
            row.source_hash = "retained"
            db.flush()
        starting_version = db.get(Course, course.id).version
    with Session(engine) as db:
        if origin == "BEGIN":
            db.begin()
        _scope(db, tenant)
        root = db.get_transaction()
        assert root.origin.name == origin
        nested = db.begin_nested() if savepoint else None
        pending = Course(
            tenant_id=tenant,
            slug="host-pending",
            title="Host",
            discipline="x",
            source_ref="host",
        )
        db.add(pending)
        events = []
        observer = _observe_reads(db, events)

        def render(markdown):
            events.append("render")
            assert markdown == command.body_md
            assert db.get_transaction() is root and root.is_active
            assert db.get_nested_transaction() is nested
            return "<p>trusted product HTML</p>"

        try:
            first = author_chapter(db, command, render_markdown=render)
            repeat = author_chapter(db, command, render_markdown=render)
        finally:
            event.remove(db, "do_orm_execute", observer)
        assert events == ["course", "render", "chapter"] * 2
        assert first.id == repeat.id
        assert first.body_html == "<p>trusted product HTML</p>"
        assert first.body_md == command.body_md
        assert (first.title, first.part) == (command.title, command.part)
        assert first.order_index == (91 if existing else command.number)
        assert first.source_hash == ("retained" if existing else "")
        assert db.get(Course, course.id).version == starting_version + 2
        assert pending.id is not None
        assert db.get_transaction() is root and root.is_active
        assert db.get_nested_transaction() is nested
        db.rollback()
        with db.begin():
            _scope(db, tenant)
            assert db.get(Course, course.id).version == starting_version
            assert (
                db.scalar(select(Course.id).where(Course.slug == "host-pending"))
                is None
            )
            row = db.scalar(select(Chapter).where(Chapter.course_id == course.id))
            if existing:
                assert row.id == original.id and row.body_html == "old"
            else:
                assert row is None


@pytest.mark.parametrize("wrong_tenant", (False, True))
def test_missing_or_cross_tenant_course_never_renders(curriculum_case, wrong_tenant):
    engine, (tenant, other) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, other)
        foreign_course = _create(db, other)
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        root = db.get_transaction()
        events = []
        observer = _observe_reads(db, events)
        try:
            with pytest.raises(CurriculumNotFound, match="course not found for tenant"):
                author_chapter(
                    db,
                    _command(tenant, foreign_course.id if wrong_tenant else uuid4()),
                    render_markdown=lambda _: pytest.fail("missing course rendered"),
                )
        finally:
            event.remove(db, "do_orm_execute", observer)
        assert events == ["course"]
        assert db.get_transaction() is root and root.is_active
        assert not db.new and not db.dirty


def test_renderer_failure_preserves_existing_chapter_version_and_root(curriculum_case):
    engine, (tenant, _) = curriculum_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = _create(db, tenant)
        command = _command(tenant, course.id)
        original = upsert_chapter(
            db, ChapterWrite(**command.model_dump(), body_html="old")
        )
        root = db.get_transaction()
        failure = RuntimeError("product renderer failed")
        events = []
        observer = _observe_reads(db, events)

        def render(markdown):
            events.append("render")
            assert db.get_transaction() is root
            raise failure

        try:
            with pytest.raises(RuntimeError) as caught:
                author_chapter(
                    db,
                    command.model_copy(update={"title": "Should not save"}),
                    render_markdown=render,
                )
        finally:
            event.remove(db, "do_orm_execute", observer)
        assert caught.value is failure
        assert events == ["course", "render"]
        assert db.get_transaction() is root and root.is_active
        db.expire_all()
        assert db.get(Course, course.id).version == 2
        retained = db.get(Chapter, original.id)
        assert retained.title == command.title and retained.body_html == "old"


def test_required_root_failure_before_sql_render_or_autobegin(curriculum_case):
    engine, (tenant, _) = curriculum_case
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with Session(engine) as db:
            with pytest.raises(TransactionChanged, match="already active caller root"):
                author_chapter(
                    db,
                    _command(tenant, uuid4()),
                    render_markdown=lambda _: pytest.fail("inactive root rendered"),
                )
            assert statements == []
            assert db.get_transaction() is None
            assert db.get_nested_transaction() is None
    finally:
        event.remove(engine, "before_cursor_execute", observe)
