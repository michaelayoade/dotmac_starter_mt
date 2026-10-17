"""Reading on genuine Kernel + learning migrations as restricted app_user.

Written before commands; unexecuted locally. No metadata DDL/stamped provider.
The shared fixture upgrades learning@head, including lr_0002.
"""

from uuid import uuid4

import pytest
from dotmac_learning import reading
from dotmac_learning.curriculum_models import (
    Chapter,
    ChapterNote,
    ChapterRead,
)
from dotmac_learning.models import LearningEvent
from dotmac_learning.reading_contracts import (
    AnnotationCommand,
    ChapterReadingCommand,
    ChapterSubtopicCommand,
    Heading,
    NoteCommand,
    SubtopicReadCommand,
)
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog_fixtures
from tests.test_learning_catalog import (
    _course,
    _person,
    _scope,
    _tenant,
)

learning_database = learning_catalog_fixtures.learning_database


@pytest.fixture
def reading_database(learning_database):
    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    tenant, foreign = uuid4(), uuid4()
    with admin.begin() as conn:
        _tenant(conn, tenant)
        _tenant(conn, foreign)
        person, other_person = _person(conn, tenant), _person(conn, tenant)
        foreign_person = _person(conn, foreign)
        course, foreign_course = _course(conn, tenant), _course(conn, foreign)
        chapter, second, foreign_chapter = uuid4(), uuid4(), uuid4()
        for cid, tid, coid, n in (
            (chapter, tenant, course, 1),
            (second, tenant, course, 2),
            (foreign_chapter, foreign, foreign_course, 1),
        ):
            conn.execute(
                text(
                    "INSERT INTO mod_learning.chapters "
                    "(id, tenant_id, course_id, number, title) "
                    "VALUES (:id, :tenant, :course, :n, 'Chapter')"
                ),
                {"id": cid, "tenant": tid, "course": coid, "n": n},
            )
    try:
        yield (
            app,
            admin,
            tenant,
            person,
            other_person,
            course,
            chapter,
            second,
            foreign,
            foreign_person,
            foreign_course,
            foreign_chapter,
        )
    finally:
        app.dispose()
        admin.dispose()


def _annotations(tenant, person, course, chapter):
    return AnnotationCommand(
        tenant_id=tenant, person_id=person, course_id=course, chapter_id=chapter
    )


def test_cross_tenant_queries_and_actual_simple_reference_scope(reading_database):
    (
        app,
        admin,
        tenant,
        person,
        _,
        course,
        chapter,
        _,
        foreign,
        foreign_person,
        foreign_course,
        foreign_chapter,
    ) = reading_database
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db, db.begin():
        _scope(conn, tenant)
        assert reading.toggle_bookmark(
            db, _annotations(tenant, person, course, chapter)
        )
        assert (
            reading.get_state(
                db, tenant_id=foreign, person_id=foreign_person, chapter_id=chapter
            ).bookmarked
            is False
        )
        with pytest.raises(DBAPIError), db.begin_nested():
            reading.toggle_bookmark(
                db,
                _annotations(foreign, foreign_person, foreign_course, foreign_chapter),
            )
        assert (
            reading.get_state(
                db, tenant_id=tenant, person_id=person, chapter_id=chapter
            ).bookmarked
            is True
        )
        # Source schema intentionally has SIMPLE ID FKs. Explicit tenant
        # ownership guards on high-level chapter commands do not change DDL.
        with pytest.raises(ValueError, match="chapter"):
            reading.toggle_chapter_read(
                db,
                ChapterReadingCommand(
                    tenant_id=tenant,
                    person_id=person,
                    course_id=course,
                    chapter_id=foreign_chapter,
                ),
            )
    with admin.connect() as conn, conn.begin():
        conn.execute(
            text(
                "INSERT INTO mod_learning.chapter_reads "
                "(tenant_id, person_id, chapter_id) "
                "VALUES (:tenant, :person, :chapter)"
            ),
            {"tenant": tenant, "person": uuid4(), "chapter": foreign_chapter},
        )
        # Plain UUID learner and cross-tenant chapter are SOURCE-accepted by
        # the actual simple FK, not evidence of composite containment.
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM mod_learning.chapter_reads "
                    "WHERE chapter_id=:id"
                ),
                {"id": foreign_chapter},
            )
            == 1
        )


def test_subtopic_normalization_idempotence_scope_and_heading_validation(
    reading_database,
):
    app, _, tenant, person, other, course, chapter, second, *_ = reading_database
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db, db.begin():
        _scope(conn, tenant)
        cmd = SubtopicReadCommand(
            tenant_id=tenant,
            person_id=person,
            chapter_id=chapter,
            subtopic_slug="  " + "x" * 70 + "  ",
        )
        assert reading.mark_complete(db, cmd) is True
        assert reading.mark_complete(db, cmd) is False
        assert (
            reading.mark_complete(db, cmd.model_copy(update={"subtopic_slug": None}))
            is False
        )
        assert reading.completed_slugs(
            db, tenant_id=tenant, person_id=person, chapter_id=chapter
        ) == {"x" * 64}
        assert (
            reading.completed_slugs(
                db, tenant_id=tenant, person_id=other, chapter_id=chapter
            )
            == set()
        )
        assert reading.completed_slugs_by_chapter(
            db, tenant_id=tenant, person_id=person, chapter_ids=[chapter, second]
        ) == {chapter: {"x" * 64}}
        assert (
            reading.completed_slugs_by_chapter(
                db, tenant_id=tenant, person_id=person, chapter_ids=[]
            )
            == {}
        )
        validated = ChapterSubtopicCommand(
            tenant_id=tenant,
            person_id=person,
            course_id=course,
            chapter_id=chapter,
            subtopic_slug="alpha",
            headings=(Heading(level="h2", title="Alpha"),),
        )
        fact = reading.complete_subtopic(db, validated)
        assert (
            fact.created
            and fact.chapter_complete
            and fact.completed == frozenset({"x" * 64, "alpha"})
        )
        assert reading.complete_subtopic(db, validated).created is False
        with pytest.raises(ValueError, match="subtopic"):
            reading.complete_subtopic(
                db, validated.model_copy(update={"subtopic_slug": "unknown"})
            )
        # SELECT/INSERT only is enforced by the actual migration grants.
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(text("DELETE FROM mod_learning.subtopic_reads"))


def test_annotation_toggle_raw_note_blank_delete_and_listing_scope(reading_database):
    app, _, tenant, person, other, course, chapter, second, *_ = reading_database
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db, db.begin():
        _scope(conn, tenant)
        cmd = _annotations(tenant, person, course, chapter)
        assert reading.toggle_bookmark(db, cmd) is True
        assert reading.toggle_bookmark(db, cmd) is False
        assert reading.toggle_bookmark(db, cmd) is True
        note_cmd = NoteCommand(**cmd.model_dump(), body="  <script>raw</script>\n ")
        note = reading.save_note(db, note_cmd)
        assert note.body == "<script>raw</script>"
        updated = reading.save_note(
            db, note_cmd.model_copy(update={"body": " updated "})
        )
        assert updated.id == note.id and updated.body == "updated"
        assert (
            reading.get_state(
                db, tenant_id=tenant, person_id=other, chapter_id=chapter
            ).note
            is None
        )
        assert (
            reading.list_for_person(
                db, tenant_id=tenant, person_id=person, visible_course_ids=()
            )
            == ()
        )
        groups = reading.list_for_person(
            db, tenant_id=tenant, person_id=person, visible_course_ids=(course,)
        )
        assert len(groups) == 1 and groups[0].course.id == course
        assert groups[0].chapters[0].chapter.id == chapter
        assert groups[0].chapters[0].note == updated
        assert reading.save_note(db, note_cmd.model_copy(update={"body": None})) is None
        assert (
            reading.save_note(db, note_cmd.model_copy(update={"body": " \n "})) is None
        )
        assert db.scalar(select(func.count()).select_from(ChapterNote)) == 0
        # Existing lookup scope excludes course_id and update keeps original
        # course reference, matching source bookmarks.save_note exactly.
        note = reading.save_note(db, note_cmd)
        course2 = _course(conn, tenant)
        preserved = reading.save_note(
            db, note_cmd.model_copy(update={"course_id": course2, "body": "new"})
        )
        assert preserved.id == note.id and preserved.course_id == course


def test_annotation_listing_filters_nonempty_visibility_and_orders_courses_and_chapters(
    reading_database,
):
    app, _, tenant, person, _, zulu_course, zulu_one, zulu_two, *_ = reading_database
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db, db.begin():
        _scope(conn, tenant)
        alpha_course = _course(conn, tenant)
        for course_id, title in (
            (zulu_course, "Zulu course"),
            (alpha_course, "Alpha course"),
        ):
            conn.execute(
                text("UPDATE mod_learning.courses SET title=:title WHERE id=:id"),
                {"title": title, "id": course_id},
            )
        alpha_two, alpha_one = uuid4(), uuid4()
        # Course creation and chapter insertion deliberately oppose the
        # source's course-title/chapter-number presentation order.
        db.add_all(
            [
                Chapter(
                    id=alpha_two,
                    tenant_id=tenant,
                    course_id=alpha_course,
                    number=2,
                    title="Alpha two",
                ),
                Chapter(
                    id=alpha_one,
                    tenant_id=tenant,
                    course_id=alpha_course,
                    number=1,
                    title="Alpha one",
                ),
            ]
        )
        db.flush()
        for course_id, chapter_id, body in (
            (zulu_course, zulu_two, "Zulu second note"),
            (zulu_course, zulu_one, "Zulu first note"),
            (alpha_course, alpha_two, "Alpha second note"),
            (alpha_course, alpha_one, "Alpha first note"),
        ):
            command = _annotations(tenant, person, course_id, chapter_id)
            assert reading.toggle_bookmark(db, command)
            reading.save_note(db, NoteCommand(**command.model_dump(), body=body))

        # These are explicit inputs to the trusted visibility-reader seam,
        # not a manufactured entitlement or evidence of adapter binding.
        groups = reading.list_for_person(
            db,
            tenant_id=tenant,
            person_id=person,
            visible_course_ids=(zulu_course, alpha_course),
        )
        assert [group.course.title for group in groups] == [
            "Alpha course",
            "Zulu course",
        ]
        assert [group.course.id for group in groups] == [alpha_course, zulu_course]
        assert [
            [entry.chapter.number for entry in group.chapters] for group in groups
        ] == [[1, 2], [1, 2]]
        assert [[entry.chapter.id for entry in group.chapters] for group in groups] == [
            [alpha_one, alpha_two],
            [zulu_one, zulu_two],
        ]
        assert all(entry.bookmarked for group in groups for entry in group.chapters)
        assert [[entry.note.body for entry in group.chapters] for group in groups] == [
            ["Alpha first note", "Alpha second note"],
            ["Zulu first note", "Zulu second note"],
        ]
        # A nonempty proper subset must exclude an otherwise annotated
        # course, including its notes and bookmarks, not merely handle ().
        subset = reading.list_for_person(
            db,
            tenant_id=tenant,
            person_id=person,
            visible_course_ids=(zulu_course,),
        )
        assert [group.course.id for group in subset] == [zulu_course]
        assert [entry.chapter.id for entry in subset[0].chapters] == [
            zulu_one,
            zulu_two,
        ]
        assert [entry.note.body for entry in subset[0].chapters] == [
            "Zulu first note",
            "Zulu second note",
        ]


def test_chapter_toggle_optional_event_dedupe_and_caller_rollback(reading_database):
    app, _, tenant, person, _, course, chapter, *_ = reading_database
    command = ChapterReadingCommand(
        tenant_id=tenant, person_id=person, course_id=course, chapter_id=chapter
    )
    with app.connect() as conn:
        tx = conn.begin()
        _scope(conn, tenant)
        with Session(bind=conn) as db, db.begin():
            first = reading.toggle_chapter_read(db, command)
            assert first.completed and first.event.status == "recorded"
            assert first.event.event.course_id == course
            assert first.event.event.subject_id == chapter
            assert reading.completed_chapter_ids(
                db, tenant_id=tenant, person_id=person, chapter_ids=[chapter]
            ) == {chapter}
            undone = reading.toggle_chapter_read(db, command)
            assert not undone.completed and undone.event is None
            again = reading.toggle_chapter_read(db, command)
            assert again.completed and again.event.status == "suppressed"
            assert db.scalar(select(func.count()).select_from(LearningEvent)) == 1
        tx.rollback()
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db, db.begin():
        _scope(conn, tenant)
        assert db.scalar(select(func.count()).select_from(ChapterRead)) == 0
        assert db.scalar(select(func.count()).select_from(LearningEvent)) == 0


def test_pending_host_write_failure_propagates_during_initial_lookup_autoflush(
    reading_database,
):
    app, _, tenant, person, _, course, chapter, *_ = reading_database
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db, db.begin():
        _scope(conn, tenant)
        # The initial chapter SELECT autoflushes this invalid required host
        # write. This proves propagation there, before the optional event
        # owner is reached; it does not isolate or qualify emit preflush.
        db.add(Chapter(tenant_id=tenant, course_id=uuid4(), number=2, title="Bad FK"))
        with pytest.raises(DBAPIError):
            reading.toggle_chapter_read(
                db,
                ChapterReadingCommand(
                    tenant_id=tenant,
                    person_id=person,
                    course_id=course,
                    chapter_id=chapter,
                ),
            )
        assert not db.is_active


def test_missing_caller_root_is_refused_without_implicit_begin(reading_database):
    app, _, tenant, person, _, course, chapter, *_ = reading_database
    with app.connect() as conn, Session(bind=conn) as db:
        with pytest.raises(TransactionChanged, match="caller root"):
            reading.toggle_bookmark(db, _annotations(tenant, person, course, chapter))
        assert db.get_transaction() is None


def test_optional_event_sql_failure_leaves_required_read_and_root_usable(
    reading_database, monkeypatch
):
    app, _, tenant, person, _, course, chapter, *_ = reading_database

    def failed_record(db, payload, **kwargs):
        db.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(reading.events, "record", failed_record)
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db, db.begin():
        _scope(conn, tenant)
        outcome = reading.toggle_chapter_read(
            db,
            ChapterReadingCommand(
                tenant_id=tenant, person_id=person, course_id=course, chapter_id=chapter
            ),
        )
        assert outcome.completed and outcome.event.status == "failed"
        assert db.scalar(select(func.count()).select_from(ChapterRead)) == 1
        assert db.scalar(select(func.count()).select_from(LearningEvent)) == 0
        assert db.scalar(text("SELECT 42")) == 42
        assert db.get_transaction().is_active


def test_source_plain_learner_and_simple_chapter_fk_are_not_hardened(reading_database):
    (
        app,
        _,
        tenant,
        person,
        _,
        course,
        chapter,
        _,
        _,
        _,
        foreign_course,
        foreign_chapter,
    ) = reading_database
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db, db.begin():
        _scope(conn, tenant)
        logical_person = uuid4()
        assert reading.mark_complete(
            db,
            SubtopicReadCommand(
                tenant_id=tenant,
                person_id=logical_person,
                chapter_id=foreign_chapter,
                subtopic_slug="source-simple-fk",
            ),
        )
        # Source annotations likewise have independent simple course/chapter
        # FKs. This intentionally characterizes their weak reference scope,
        # without presenting it as authorization or a fabricated Party.
        assert reading.toggle_bookmark(
            db, _annotations(tenant, logical_person, foreign_course, chapter)
        )
        with pytest.raises(DBAPIError), db.begin_nested():
            reading.save_note(
                db,
                NoteCommand(
                    tenant_id=tenant,
                    person_id=person,
                    course_id=course,
                    chapter_id=uuid4(),
                    body="required FK error",
                ),
            )
        assert db.scalar(text("SELECT 42")) == 42
