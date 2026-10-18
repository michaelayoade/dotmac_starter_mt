"""Selective reference writes on real migrated PostgreSQL under restricted roles."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_learning.models import Activity, Course
from dotmac_learning.references import (
    ActivityPatch,
    CoursePatch,
    ReferenceNotFound,
    patch_activity,
    patch_course,
)
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import _scope, _tenant

learning_database = learning_catalog.learning_database


@pytest.fixture
def reference_database(learning_database):
    admin, app = (create_engine(url) for url in learning_database)
    tenant, other, course, activity, bank = (uuid4() for _ in range(5))
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            _tenant(conn, other)
        with app.begin() as conn:
            _scope(conn, tenant)
            assert conn.scalar(text("SELECT current_user")) == "app_user"
            conn.execute(
                Course.__table__.insert().values(
                    id=course,
                    tenant_id=tenant,
                    slug="original",
                    title="Course",
                    discipline="networking",
                    source_ref="product",
                    version=7,
                    status="draft",
                    listed=True,
                )
            )
            conn.execute(
                Activity.__table__.insert().values(
                    id=activity,
                    tenant_id=tenant,
                    course_id=course,
                    title="Original",
                    chapter_number=8,
                    type="lab",
                    bank_id=bank,
                    pass_threshold=0.9,
                    max_attempts=6,
                    assessment_mode="exam",
                    grading="manual",
                    weight=4.0,
                    question_count=12,
                )
            )
        yield app, tenant, other, course, activity, bank
    finally:
        app.dispose()
        admin.dispose()


def test_patches_reject_other_scope_and_missing_exact_tenant_reference(
    reference_database,
):
    app, tenant, other, course, activity, _ = reference_database
    for scope in (other, tenant):
        with Session(bind=app) as db, db.begin():
            _scope(db.connection(), scope)
            for command, mutate, identifier, name in (
                (CoursePatch, patch_course, course, "course"),
                (ActivityPatch, patch_activity, activity, "activity"),
            ):
                # Under B, even claiming A cannot make A's row visible through RLS.
                requests = [
                    (other, identifier),
                    (tenant, uuid4()),
                ]
                if scope == other:
                    requests.append((tenant, identifier))
                for requested_tenant, requested_id in requests:
                    with pytest.raises(
                        ReferenceNotFound, match=f"^{name} not found for tenant$"
                    ):
                        mutate(
                            db,
                            command(
                                id=requested_id,
                                tenant_id=requested_tenant,
                                title="Wrong",
                            ),
                        )


def test_lab_bank_and_course_stages_preserve_omitted_fields_and_outer_rollback(
    reference_database,
):
    app, tenant, _, course, activity, bank = reference_database
    with Session(bind=app) as db:
        with db.begin() as outer:
            _scope(db.connection(), tenant)
            row = db.scalar(select(Activity).where(Activity.id == activity))
            root = db.get_transaction()
            flushed = []

            def observe(session, context, instances):
                flushed.append(session)

            event.listen(db, "before_flush", observe)
            try:
                # Academy changed-lab path supplies only title/chapter/threshold.
                result = patch_activity(
                    db,
                    ActivityPatch(
                        id=activity,
                        tenant_id=tenant,
                        title="Changed lab",
                        chapter_number=None,
                        pass_threshold=0.8,
                    ),
                )
                assert flushed == [] and row in db.dirty
                assert db.get_transaction() is root
                assert (result.title, result.chapter_number, result.pass_threshold) == (
                    "Changed lab",
                    None,
                    0.8,
                )
                assert (
                    result.course_id,
                    result.type,
                    result.bank_id,
                    result.max_attempts,
                    result.assessment_mode,
                    result.grading,
                    result.weight,
                    result.question_count,
                ) == (
                    course,
                    "lab",
                    bank,
                    6,
                    "exam",
                    "manual",
                    4.0,
                    12,
                )
                with pytest.raises(FrozenInstanceError):
                    result.title = "mutable"
                # The next caller SELECT autoflushes the previous stage.
                policy = {"pool": None}
                provided = (
                    {"question_count": policy["pool"]} if "pool" in policy else {}
                )
                bank_result = patch_activity(
                    db,
                    ActivityPatch(
                        id=activity,
                        tenant_id=tenant,
                        title="Imported bank",
                        pass_threshold=0.6,
                        **provided,
                    ),
                )
                assert flushed == [db] and row in db.dirty
                assert (
                    bank_result.question_count,
                    bank_result.max_attempts,
                    bank_result.assessment_mode,
                    bank_result.grading,
                    bank_result.weight,
                ) == (
                    None,
                    6,
                    "exam",
                    "manual",
                    4.0,
                )
                course_result = patch_course(
                    db, CoursePatch(id=course, tenant_id=tenant, status="completed")
                )
                assert flushed == [db, db]
                assert (
                    course_result.slug,
                    course_result.title,
                    course_result.discipline,
                    course_result.source_ref,
                    course_result.version,
                    course_result.listed,
                ) == (
                    "original",
                    "Course",
                    "networking",
                    "product",
                    7,
                    True,
                )
                assert course_result.status == "completed"
                assert db.get_transaction() is root
                # The caller's flush still belongs to its root transaction.
                db.flush()
                assert flushed == [db, db, db]
            finally:
                event.remove(db, "before_flush", observe)
            outer.rollback()
    with Session(bind=app) as db, db.begin():
        _scope(db.connection(), tenant)
        row = db.scalar(select(Activity).where(Activity.id == activity))
        assert (
            row.title,
            row.chapter_number,
            row.pass_threshold,
            row.question_count,
        ) == ("Original", 8, 0.9, 12)
        assert db.scalar(select(Course.status).where(Course.id == course)) == "draft"


def test_explicit_nullable_clears_and_policy_zero_values_survive(reference_database):
    app, tenant, _, _, activity, _ = reference_database
    with Session(bind=app) as db, db.begin():
        _scope(db.connection(), tenant)
        result = patch_activity(
            db,
            ActivityPatch(
                id=activity,
                tenant_id=tenant,
                bank_id=None,
                max_attempts=None,
                question_count=0,
                assessment_mode="practice",
                weight=0.0,
            ),
        )
        assert (
            result.bank_id,
            result.max_attempts,
            result.question_count,
            result.assessment_mode,
            result.weight,
        ) == (None, None, 0, "practice", 0.0)
        db.flush()
        db.expire_all()
        row = db.scalar(select(Activity).where(Activity.id == activity))
        assert (
            row.bank_id,
            row.max_attempts,
            row.question_count,
            row.assessment_mode,
            row.weight,
            row.title,
            row.grading,
        ) == (None, None, 0, "practice", 0.0, "Original", "manual")


def test_demo_seed_selected_fields_preserve_existing_unassigned_values(
    reference_database,
):
    app, tenant, _, course, activity, bank = reference_database
    with Session(bind=app) as db, db.begin():
        _scope(db.connection(), tenant)
        # The Academy demo assessment update leaves title/chapter/pool/mode alone.
        assessment = patch_activity(
            db,
            ActivityPatch(
                id=activity,
                tenant_id=tenant,
                bank_id=bank,
                type="mcq_test",
                pass_threshold=0.7,
                max_attempts=3,
                grading="auto",
                weight=1.0,
            ),
        )
        assert (
            assessment.title,
            assessment.chapter_number,
            assessment.question_count,
            assessment.assessment_mode,
            assessment.course_id,
        ) == (
            "Original",
            8,
            12,
            "exam",
            course,
        )
        # Its existing lab path supplies an even narrower set.
        lab = patch_activity(
            db,
            ActivityPatch(
                id=activity,
                tenant_id=tenant,
                type="lab",
                chapter_number=1,
                pass_threshold=0.8,
                grading="manual",
            ),
        )
        assert (
            lab.bank_id,
            lab.max_attempts,
            lab.question_count,
            lab.assessment_mode,
            lab.weight,
            lab.title,
        ) == (bank, 3, 12, "exam", 1.0, "Original")


def test_reference_create_keeps_explicit_zero_weight_and_out_of_range_threshold(
    reference_database,
):
    from dotmac_learning.references import ActivityWrite, create_activity

    app, tenant, _, course, _, _ = reference_database
    with Session(bind=app) as db, db.begin():
        _scope(db.connection(), tenant)
        created = create_activity(
            db,
            ActivityWrite(
                id=uuid4(),
                tenant_id=tenant,
                course_id=course,
                title="Legacy values",
                type="mcq_test",
                pass_threshold=1.1,
                assessment_mode="graded",
                grading="auto",
                weight=0,
            ),
        )
        assert created.pass_threshold == 1.1 and created.weight == 0.0
        updated = patch_activity(
            db,
            ActivityPatch(
                id=created.id,
                tenant_id=tenant,
                pass_threshold=1.1,
                weight=0,
            ),
        )
        assert updated.pass_threshold == created.pass_threshold
        assert updated.weight == created.weight
