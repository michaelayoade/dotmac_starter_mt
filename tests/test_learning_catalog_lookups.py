"""Hosted PostgreSQL indexed fact reads; authored, never executed locally."""

from uuid import uuid4

from dotmac_learning import (
    ChapterFact,
    CourseFact,
    catalog_chapter_by_number,
    catalog_course_by_slug,
)
from dotmac_learning.curriculum_models import Chapter
from dotmac_learning.models import Course
from dotmac_learning.references import CourseWrite, create_course
from sqlalchemy import select

from tests import test_learning_results as fixtures
from tests.test_learning_results import scoped

learning_database = fixtures.learning_database
result_database = fixtures.result_database


def test_lookup_scope_missing_course_binding_and_pending_flush(result_database):
    admin, app, tenant, _, course, _, foreign, *_ = result_database
    with scoped(app, tenant) as db:
        original = db.get(Course, course)
        root = db.get_transaction()
        one = Chapter(
            tenant_id=tenant, course_id=course, number=1, title="Pending Unicode 学"
        )
        db.add(one)
        # Public reads preserve ordinary Session autoflush; no hidden commit.
        fact = catalog_chapter_by_number(
            db, tenant_id=tenant, course_id=course, number=1
        )
        assert isinstance(fact, ChapterFact) and fact.id == one.id
        assert fact.title == "Pending Unicode 学" and one not in db.new
        assert db.get_transaction() is root
        assert (
            catalog_chapter_by_number(db, tenant_id=tenant, course_id=uuid4(), number=1)
            is None
        )
        assert (
            catalog_chapter_by_number(db, tenant_id=foreign, course_id=course, number=1)
            is None
        )
        assert (
            catalog_chapter_by_number(db, tenant_id=tenant, course_id=course, number=99)
            is None
        )
        course_fact = catalog_course_by_slug(db, tenant_id=tenant, slug=original.slug)
        assert isinstance(course_fact, CourseFact) and course_fact.id == course
        assert catalog_course_by_slug(db, tenant_id=foreign, slug=original.slug) is None
        assert catalog_course_by_slug(db, tenant_id=tenant, slug="missing") is None
        one_id = one.id
        db.rollback()
    with admin.connect() as conn:
        assert conn.scalar(select(Chapter.id).where(Chapter.id == one_id)) is None


def test_same_slug_and_number_are_tenant_and_course_local(result_database):
    _, app, tenant, _, course, _, foreign, *_ = result_database
    with scoped(app, tenant) as db:
        slug = db.get(Course, course).slug
        second = uuid4()
        create_course(
            db,
            CourseWrite(
                id=second,
                tenant_id=tenant,
                slug=second.hex,
                title="Second",
                discipline="test",
                source_ref="test",
                status="draft",
                listed=False,
            ),
        )
        a = Chapter(tenant_id=tenant, course_id=course, number=7, title="Original")
        b = Chapter(tenant_id=tenant, course_id=second, number=7, title="Second")
        db.add_all([a, b])
        db.flush()
        aid, bid = a.id, b.id
    with scoped(app, foreign) as db:
        foreign_course = uuid4()
        create_course(
            db,
            CourseWrite(
                id=foreign_course,
                tenant_id=foreign,
                slug=slug,
                title="Foreign",
                discipline="test",
                source_ref="test",
                status="draft",
                listed=False,
            ),
        )
        db.add(
            Chapter(
                tenant_id=foreign, course_id=foreign_course, number=7, title="Foreign"
            )
        )
    with scoped(app, tenant) as db:
        assert catalog_course_by_slug(db, tenant_id=tenant, slug=slug).id == course
        assert (
            catalog_chapter_by_number(
                db, tenant_id=tenant, course_id=course, number=7
            ).id
            == aid
        )
        assert (
            catalog_chapter_by_number(
                db, tenant_id=tenant, course_id=second, number=7
            ).id
            == bid
        )
        assert catalog_course_by_slug(db, tenant_id=foreign, slug=slug) is None
    with scoped(app, foreign) as db:
        assert (
            catalog_course_by_slug(db, tenant_id=foreign, slug=slug).id
            == foreign_course
        )


def test_disabled_autoflush_preserves_pending_objects_and_caller_flush(result_database):
    _, app, tenant, _, course, *_ = result_database
    with scoped(app, tenant) as db:
        db.autoflush = False
        root = db.get_transaction()
        chapter = Chapter(
            tenant_id=tenant, course_id=course, number=10, title="Caller pending"
        )
        db.add(chapter)
        assert (
            catalog_chapter_by_number(db, tenant_id=tenant, course_id=course, number=10)
            is None
        )
        assert chapter in db.new and db.get_transaction() is root
        db.flush()
        assert (
            catalog_chapter_by_number(
                db, tenant_id=tenant, course_id=course, number=10
            ).id
            == chapter.id
        )
        assert db.get_transaction() is root
