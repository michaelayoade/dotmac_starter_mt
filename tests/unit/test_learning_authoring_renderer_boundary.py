"""Authored-only sequencing characterization; no database or product renderer."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_learning import AuthorChapterCommand, author_chapter
from dotmac_learning.curriculum import CurriculumNotFound, upsert_chapter
from dotmac_learning.curriculum_contracts import ChapterWrite
from dotmac_learning.curriculum_models import Chapter
from dotmac_learning.models import Course
from dotmac_learning.result_contracts import TransactionChanged
from pydantic import ValidationError


def _command():
    return AuthorChapterCommand(
        tenant_id=uuid4(),
        course_id=uuid4(),
        number=2,
        title="Edited",
        part="II",
        body_md="# raw  text\n",
    )


class Boundary:
    is_active = True


class RecordingSession:
    def __init__(self, command, *, missing=False, existing=False):
        self.command = command
        self.events = []
        self.root = Boundary()
        self.nested = None
        self.course = None if missing else SimpleNamespace(version=7)
        self.chapter = (
            SimpleNamespace(
                **command.model_dump(),
                id=uuid4(),
                body_html="old",
                source_hash="retained",
                order_index=99,
            )
            if existing
            else None
        )
        self.added = []

    def get_transaction(self):
        return self.root

    def get_nested_transaction(self):
        return self.nested

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        params = tuple(statement.compile().params.values())
        assert self.command.tenant_id in params
        assert self.command.course_id in params
        if entity is Course:
            self.events.append("course")
            row = self.course
        else:
            assert entity is Chapter and self.command.number in params
            self.events.append("chapter")
            row = self.chapter
        return SimpleNamespace(first=lambda: row)

    def add(self, row):
        self.events.append("add")
        self.added.append(row)
        self.chapter = row

    def flush(self):
        self.events.append("flush")
        assert self.course.version == 8
        if self.added:
            self.chapter.id = uuid4()
            self.chapter.source_hash = ""


@pytest.mark.parametrize("existing", (False, True))
def test_scoped_course_render_chapter_write_order_and_shared_upsert_behavior(existing):
    command = _command()
    db = RecordingSession(command, existing=existing)
    root = db.root

    def render(markdown):
        db.events.append("render")
        assert markdown == command.body_md
        assert db.root is root and db.course.version == 7
        return "<p>trusted result</p>"

    fact = author_chapter(db, command, render_markdown=render)
    assert db.events == ["course", "render", "chapter"] + (
        [] if existing else ["add"]
    ) + ["flush"]
    assert fact.body_md == command.body_md
    assert fact.body_html == "<p>trusted result</p>"
    assert (fact.title, fact.part) == (command.title, command.part)
    assert fact.order_index == (99 if existing else 2)
    assert fact.source_hash == ("retained" if existing else "")
    assert db.root is root

    rendered = ChapterWrite(**command.model_dump(), body_html=fact.body_html)
    legacy_db = RecordingSession(command, existing=existing)
    legacy_fact = upsert_chapter(legacy_db, rendered)
    assert legacy_db.events == [x for x in db.events if x != "render"]
    for name in ("title", "part", "body_md", "body_html", "order_index", "source_hash"):
        assert getattr(legacy_fact, name) == getattr(fact, name)


def test_missing_course_refuses_before_renderer_or_chapter_work():
    command = _command()
    db = RecordingSession(command, missing=True)

    def render(markdown):
        pytest.fail("missing course must not render")

    with pytest.raises(CurriculumNotFound, match="course not found for tenant"):
        author_chapter(db, command, render_markdown=render)
    assert db.events == ["course"]
    assert db.added == []


def test_renderer_exception_identity_propagates_without_chapter_work():
    command = _command()
    db = RecordingSession(command, existing=True)
    before = vars(db.chapter).copy()
    failure = RuntimeError("renderer failure")

    def render(markdown):
        db.events.append("render")
        raise failure

    with pytest.raises(RuntimeError) as caught:
        author_chapter(db, command, render_markdown=render)
    assert caught.value is failure
    assert db.events == ["course", "render"]
    assert vars(db.chapter) == before and db.course.version == 7


def test_inactive_root_refuses_before_lookup_or_renderer():
    command = _command()
    db = RecordingSession(command)
    db.root = None
    with pytest.raises(TransactionChanged, match="already active caller root"):
        author_chapter(db, command, render_markdown=lambda _: pytest.fail("render"))
    assert db.events == []


@pytest.mark.parametrize("change", ("root", "nested"))
def test_renderer_boundary_change_is_detected_before_chapter_query(change):
    command = _command()
    db = RecordingSession(command)

    def render(markdown):
        db.events.append("render")
        setattr(db, change, Boundary())
        return "rendered"

    with pytest.raises(TransactionChanged, match="changed the caller transaction"):
        author_chapter(db, command, render_markdown=render)
    assert db.events == ["course", "render"]
    assert db.course.version == 7 and db.added == []


def test_source_command_forbids_rendered_html_and_retains_chapter_write_fields():
    command = _command()
    assert set(type(command).model_fields) == set(ChapterWrite.model_fields) - {
        "body_html"
    }
    with pytest.raises(ValidationError):
        AuthorChapterCommand(**command.model_dump(), body_html="untrusted")
