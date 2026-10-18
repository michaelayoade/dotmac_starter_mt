"""Hosted-only migrated analytics policy and non-bypass two-tenant canaries."""

from uuid import uuid4

from dotmac_learning import analytics
from dotmac_learning.models import Score, Submission
from dotmac_learning.projections import best_scores_for
from dotmac_learning.references import (
    ActivityWrite,
    CourseWrite,
    create_activity,
    create_course,
)
from sqlalchemy import text

from tests import test_learning_results as learning_results
from tests.test_learning_catalog import _person
from tests.test_learning_results import scoped

learning_database = learning_results.learning_database
result_database = learning_results.result_database


def append(
    db,
    tenant,
    person,
    activity,
    attempt,
    fraction,
    items,
    *,
    source="auto",
    passed=False,
):
    sub = Submission(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        attempt_no=attempt,
        answers={},
    )
    db.add(sub)
    db.flush()
    score = Score(
        tenant_id=tenant,
        submission_id=sub.id,
        score=fraction,
        max_score=1,
        fraction=fraction,
        passed=passed,
        per_item=items,
        source=source,
    )
    db.add(score)
    db.flush()
    return score.id


def test_analysis_best_per_person_strict_fraction_and_actual_rls(result_database):
    admin, app, tenant, person, course, activity, other, other_person, *_ = (
        result_database
    )
    with admin.begin() as conn:
        second = _person(conn, tenant)
    with scoped(app, tenant) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        append(db, tenant, person, activity, 1, 0.1, [{"id": "loser", "correct": True}])
        auto_id = append(
            db, tenant, person, activity, 2, 0.5, [{"id": "q", "correct": False}]
        )
        override_id = append(
            db,
            tenant,
            person,
            activity,
            3,
            0.5,
            [{"id": "tie", "correct": True}],
            source="override",
            passed=True,
        )
        second_id = append(
            db, tenant, second, activity, 1, 0.8, [{"id": "q", "correct": True}]
        )
        # SQL ties deliberately remain unspecified. Determine expected first
        # encountered maximum from the exact unordered source query.
        pairs = db.execute(analytics._activity_scores_query(tenant, activity)).all()
        expected = {}
        for pid, row in pairs:
            if pid not in expected or row.fraction > expected[pid].fraction:
                expected[pid] = row
        actual = analytics._best_scores_for_activity(
            db, tenant_id=tenant, activity_id=activity
        )
        actual_ids = {score.id for score in actual}
        assert len(actual) == 2 and second_id in actual_ids
        assert len(actual_ids & {auto_id, override_id}) == 1
        assert tuple(s.id for s in actual) == tuple(s.id for s in expected.values())
        assert (
            best_scores_for(db, tenant_id=tenant, person_id=person, course_id=course)[
                activity
            ].source
            == "override"
        )
        root = db.get_transaction()
        facts = analytics.item_analysis(db, tenant_id=tenant, activity_id=activity)
        assert db.get_transaction() is root
        assert facts and sum(f.responses for f in facts) == 2
        assert facts == analytics._analysis_from(tuple(expected.values()))
        assert "loser" not in {f.id for f in facts}
        # Another activity is excluded even for the same person.
        aid = uuid4()
        create_activity(
            db, ActivityWrite(id=aid, tenant_id=tenant, course_id=course, title="Other")
        )
        append(db, tenant, person, aid, 1, 1, [{"id": "excluded", "correct": True}])
        assert (
            analytics.item_analysis(db, tenant_id=tenant, activity_id=activity) == facts
        )
    assert all(not hasattr(f, "_sa_instance_state") for f in facts)
    with scoped(app, other) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        # An A tenant parameter under B scope must still expose no A rows.
        assert analytics.item_analysis(db, tenant_id=tenant, activity_id=activity) == ()
        cid, aid = uuid4(), uuid4()
        create_course(
            db,
            CourseWrite(
                id=cid,
                tenant_id=other,
                slug=cid.hex,
                title="B",
                discipline="B",
                source_ref="test",
            ),
        )
        create_activity(
            db, ActivityWrite(id=aid, tenant_id=other, course_id=cid, title="B")
        )
        append(db, other, other_person, aid, 1, 1, [{"id": "own", "correct": True}])
        own = analytics.item_analysis(db, tenant_id=other, activity_id=aid)
        assert tuple((f.id, f.responses, f.correct, f.p_value) for f in own) == (
            ("own", 1, 1, 1),
        )
        assert analytics.item_analysis(db, tenant_id=tenant, activity_id=aid) == ()
