"""Hosted real Alembic Success Queue parity; never execute on a local daemon."""

from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel import audit_actions
from dotmac_kernel.audit import AuditEvent, MissingAuditActorError
from dotmac_kernel.models import Party, PartyPerson
from dotmac_learning import success_queue as queue
from dotmac_learning.account_contracts import ActiveAccountSelection
from dotmac_learning.attempt_contracts import AttemptAuditContext
from dotmac_learning.delivery_models import OfferingActivity, SuccessQueueEntry
from dotmac_learning.manifest import module
from dotmac_learning.models import (
    Activity,
    Certificate,
    Course,
    CourseCompletion,
    LearningEvent,
    Score,
    Submission,
)
from dotmac_learning.participation_models import Cohort, CourseOffering, Enrollment
from dotmac_learning.success_contracts import (
    SuccessBadRequest,
    SuccessNotFound,
    SuccessTerms,
    SweepResult,
)
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer
from sqlalchemy.orm.attributes import NO_VALUE

from tests import test_learning_results as result_tests
from tests.test_learning_catalog import _person
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database
NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)
TERMS = SuccessTerms(7, 60, 6, 2, 3, 7, 5, 2, 0.20, 0.5, 0.8)


def population(tenant, *people):
    """Explicit trusted source account observations; no identity inference."""
    return ActiveAccountSelection(
        tenant, select(Party.id).where(Party.tenant_id == tenant, Party.id.in_(people))
    )


@pytest.fixture(autouse=True)
def declared_audit(monkeypatch):
    monkeypatch.setattr(
        audit_actions,
        "_active_registry",
        audit_actions.AuditActionRegistry.from_manifests([module]),
    )


def context(tenant):
    return AttemptAuditContext(
        tenant, "system", "academy", actor_label="Success operator"
    )


def seed(db, tenant, person, course, *, days=20, role="student", status="active"):
    cohort = Cohort(tenant_id=tenant, name="Success", discipline="engineering")
    db.add(cohort)
    db.flush()
    db.add_all(
        [
            Enrollment(
                tenant_id=tenant,
                person_id=person,
                cohort_id=cohort.id,
                role_in_cohort=role,
                status=status,
                created_at=NOW - timedelta(days=days),
            ),
            CourseOffering(
                tenant_id=tenant, cohort_id=cohort.id, course_id=course, status="active"
            ),
        ]
    )
    db.flush()
    return cohort.id


def entry(
    db,
    tenant,
    person,
    *,
    reason="inactivity",
    status="open",
    facts=None,
    severity="medium",
    cohort=None,
):
    row = SuccessQueueEntry(
        tenant_id=tenant,
        person_id=person,
        cohort_id=cohort,
        reason_kind=reason,
        supporting_facts={} if facts is None else facts,
        severity=severity,
        detected_at=NOW,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def score(db, tenant, person, activity, *, fraction=0.5, attempt=1, passed=False):
    sub = Submission(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        answers={},
        attempt_no=attempt,
    )
    db.add(sub)
    db.flush()
    db.add(
        Score(
            tenant_id=tenant,
            submission_id=sub.id,
            score=fraction,
            max_score=1,
            fraction=fraction,
            passed=passed,
            per_item=[],
        )
    )
    db.flush()


def test_sweep_refresh_history_detachment_and_non_bypass_isolation(result_database):
    _, app, tenant, person, course, _, other, *_ = result_database
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, course)
        history = entry(db, tenant, person, status="resolved", facts=["arbitrary", 2])
        assert queue.sweep(
            db,
            tenant_id=tenant,
            terms=TERMS,
            now=NOW,
            accounts=population(tenant, person),
        ) == SweepResult(1, 0, 0)
        current = db.scalar(
            select(SuccessQueueEntry).where(SuccessQueueEntry.status == "open")
        )
        current.assigned_to = person
        current.score_hint = 17
        current.status = "acknowledged"
        original_id = current.id
        assert (
            queue.sweep(
                db,
                tenant_id=tenant,
                terms=TERMS,
                now=NOW + timedelta(hours=1),
                accounts=population(tenant, person),
            ).refreshed
            == 1
        )
        assert (
            current.id,
            current.assigned_to,
            current.status,
            current.score_hint,
        ) == (
            original_id,
            person,
            "acknowledged",
            17,
        )
        assert current.cohort_id == cohort and history.supporting_facts == [
            "arbitrary",
            2,
        ]
        rows = queue.list_entries(db, tenant_id=tenant)
        assert isinstance(rows, tuple) and len(rows) == 2
        assert rows[0].person.id == person
        with pytest.raises(FrozenInstanceError):
            rows[0].entry.status = "changed"
        rows[0].entry.supporting_facts["detached"] = True
        assert "detached" not in current.supporting_facts
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    with scoped(app, other) as db:
        assert db.execute(
            text(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        ).one() == (False, False)
        assert db.scalar(select(func.count()).select_from(SuccessQueueEntry)) == 0
        assert queue.list_entries(db, tenant_id=tenant) == ()
        assert (
            queue.segment_members(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment="inactive",
                terms=TERMS,
                now=NOW,
                accounts=population(tenant, person),
            )
            == ()
        )


@pytest.mark.parametrize(
    "days,severity", [(6, None), (7, "medium"), (13, "medium"), (14, "high")]
)
def test_inactivity_threshold_and_never_active_anchor(result_database, days, severity):
    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db:
        hit = queue._rule_inactivity(
            db,
            tenant_id=tenant,
            person_id=person,
            enrolled_since=NOW - timedelta(days=days),
            now=NOW,
            terms=SuccessTerms(7, 60, 6, 2, 3, 7, 5, 2, 0.20, 0.5, 0.8),
        )
        if severity is None:
            assert hit is None
        else:
            assert hit[1] == severity and hit[0]["never_active"] is True
            assert hit[0]["days_inactive"] == days
        assert (
            queue._rule_inactivity(
                db,
                tenant_id=tenant,
                person_id=person,
                enrolled_since=None,
                now=NOW,
                terms=SuccessTerms(7, 60, 6, 2, 3, 7, 5, 2, 0.20, 0.5, 0.8),
            )
            is None
        )
        db.add(
            LearningEvent(
                tenant_id=tenant,
                person_id=person,
                kind="activity_started",
                occurred_at=NOW - timedelta(days=2),
                detail={},
            )
        )
        db.flush()
        assert (
            queue._rule_inactivity(
                db,
                tenant_id=tenant,
                person_id=person,
                enrolled_since=NOW - timedelta(days=100),
                now=NOW,
                terms=SuccessTerms(7, 60, 6, 2, 3, 7, 5, 2, 0.20, 0.5, 0.8),
            )
            is None
        )


@pytest.mark.parametrize(
    "hours,count,severity",
    [
        (0, 1, None),
        (-24, 1, "medium"),
        (-24, 3, "high"),
        (-191, 1, "medium"),
        (-192, 1, "high"),
    ],
)
def test_overdue_strict_time_count_and_day_boundary(
    result_database, hours, count, severity
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, course)
        offering = db.scalar(
            select(CourseOffering).where(CourseOffering.cohort_id == cohort)
        )
        for i in range(count):
            aid = activity
            if i:
                a = Activity(
                    tenant_id=tenant,
                    course_id=course,
                    title=f"Work {i}",
                    chapter_number=i,
                    type="mcq_test",
                    pass_threshold=0.0,
                    assessment_mode="graded",
                    grading="auto",
                    weight=1.0,
                )
                db.add(a)
                db.flush()
                aid = a.id
            db.add(
                OfferingActivity(
                    tenant_id=tenant,
                    offering_id=offering.id,
                    activity_id=aid,
                    due_at=NOW + timedelta(hours=hours),
                )
            )
        db.flush()
        hit = queue._rule_overdue(
            db,
            tenant_id=tenant,
            person_id=person,
            now=NOW,
            terms=SuccessTerms(7, 60, 6, 2, 3, 7, 5, 2, 0.20, 0.5, 0.8),
        )
        if severity is None:
            assert hit is None
        else:
            assert hit[1] == severity and hit[0]["overdue_count"] == count
            score(db, tenant, person, activity, passed=True, fraction=1)
            after = queue._rule_overdue(
                db,
                tenant_id=tenant,
                person_id=person,
                now=NOW,
                terms=SuccessTerms(7, 60, 6, 2, 3, 7, 5, 2, 0.20, 0.5, 0.8),
            )
            assert (
                after is None if count == 1 else after[0]["overdue_count"] == count - 1
            )


@pytest.mark.parametrize(
    "graded,weights,pct,expected",
    [
        (1, [1, 1], 0, None),
        (2, [1, 1, 9], 0, None),
        (2, [1, 1, 8], 0.29, "high"),
        (2, [1, 1, 8], 0.30, "medium"),
        (2, [1, 1, 8], 0.60, None),
        (2, [-1, -1, 12], 0, None),
    ],
)
def test_below_passing_volume_weight_and_severity_boundaries(
    result_database, graded, weights, pct, expected
):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        original = db.get(Activity, activity)
        original.weight = weights[0]
        ids = [activity]
        for index, weight in enumerate(weights[1:], 1):
            row = Activity(
                tenant_id=tenant,
                course_id=course,
                chapter_number=index,
                title=f"Activity {index}",
                weight=weight,
                type="mcq_test",
                pass_threshold=0.0,
                assessment_mode="graded",
                grading="auto",
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
        for aid in ids[:graded]:
            score(db, tenant, person, aid, fraction=pct)
        hit = queue._rule_below_passing(
            db,
            tenant_id=tenant,
            person_id=person,
            course_ids=[course, course],
            terms=SuccessTerms(7, 60, 6, 2, 3, 7, 5, 2, 0.20, 0.5, 0.8),
        )
        if expected is None:
            assert hit is None
        else:
            assert hit[1] == expected and hit[0]["graded_count"] == 2
            assert hit[0]["graded_weight_pct"] == 20


def test_failed_final_uses_best_and_effective_attempt_entitlement(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        final = db.get(Activity, activity)
        final.chapter_number = None
        score(db, tenant, person, activity, attempt=1)
        assert (
            queue._rule_failed_final(
                db, tenant_id=tenant, person_id=person, course_ids=[course]
            )
            is None
        )
        score(db, tenant, person, activity, attempt=2)
        hit = queue._rule_failed_final(
            db, tenant_id=tenant, person_id=person, course_ids=[course]
        )
        assert hit[1] == "high" and hit[0]["attempts_used"] == 2
        from dotmac_learning.attempt_models import AttemptGrant

        db.add(
            AttemptGrant(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                extra_attempts=1,
                reason="Support",
            )
        )
        db.flush()
        assert (
            queue._rule_failed_final(
                db, tenant_id=tenant, person_id=person, course_ids=[course]
            )
            is None
        )
        score(db, tenant, person, activity, attempt=3, fraction=1, passed=True)
        assert (
            queue._rule_failed_final(
                db, tenant_id=tenant, person_id=person, course_ids=[course]
            )
            is None
        )


@pytest.mark.parametrize(
    "age,expected",
    [
        (timedelta(hours=6) - timedelta(microseconds=1), False),
        (timedelta(hours=6), True),
    ],
)
def test_certificate_grace_is_inclusive_and_certificate_suppresses(
    result_database, age, expected
):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=course,
                status="completed",
                pct=1,
                completed_at=NOW - age,
            )
        )
        db.flush()
        hit = queue._rule_certificate_blocked(
            db, tenant_id=tenant, person_id=person, now=NOW, grace_hours=6
        )
        assert (hit is not None) is expected
        db.add(
            Certificate(
                tenant_id=tenant,
                person_id=person,
                course_id=course,
                serial=uuid4().hex,
                issued_at=NOW,
            )
        )
        db.flush()
        assert (
            queue._rule_certificate_blocked(
                db, tenant_id=tenant, person_id=person, now=NOW, grace_hours=6
            )
            is None
        )


def test_auto_resolve_only_population_and_preserve_arbitrary_history(result_database):
    admin, app, tenant, person, course, *_ = result_database
    with admin.begin() as conn:
        excluded = _person(conn, tenant)
    with scoped(app, tenant) as db:
        seed(db, tenant, person, course, days=1)
        seed(db, tenant, excluded, course, days=30)
        db.get(Party, excluded).is_active = True
        live = entry(db, tenant, person, facts={"old": [1, "value"]})
        stale = entry(db, tenant, excluded, facts=["legacy"])
        assert (
            queue.sweep(
                db,
                tenant_id=tenant,
                terms=TERMS,
                now=NOW,
                accounts=population(tenant, person),
            ).auto_resolved
            == 1
        )
        assert live.status == "resolved" and live.resolved_by is None
        assert live.supporting_facts == {
            "old": [1, "value"],
            "auto_resolved": "rule no longer fires",
        }
        assert stale.status == "open" and stale.supporting_facts == ["legacy"]
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_nonobject_live_autoresolve_preserves_source_error(result_database):
    _, app, tenant, person, course, *_ = result_database
    with pytest.raises(TypeError):
        with scoped(app, tenant) as db:
            seed(db, tenant, person, course, days=1)
            entry(db, tenant, person, facts=["source cannot spread this"])
            queue.sweep(
                db,
                tenant_id=tenant,
                terms=TERMS,
                now=NOW,
                accounts=population(tenant, person),
            )


def test_open_only_uniqueness_allows_acknowledged_history(result_database):
    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db:
        entry(db, tenant, person, status="acknowledged")
        entry(db, tenant, person, status="acknowledged")
        entry(db, tenant, person)
        with pytest.raises(IntegrityError), db.begin_nested():
            entry(db, tenant, person)
        assert db.scalar(select(func.count()).select_from(SuccessQueueEntry)) == 3


@pytest.mark.parametrize(
    "status,action,valid,after",
    [
        ("open", "acknowledge", True, "acknowledged"),
        ("acknowledged", "acknowledge", False, "acknowledged"),
        ("resolved", "acknowledge", False, "resolved"),
        ("open", "resolve", True, "resolved"),
        ("acknowledged", "resolve", True, "resolved"),
        ("resolved", "resolve", False, "resolved"),
        ("resolved", "assign", True, "resolved"),
        ("open", "assign", True, "acknowledged"),
    ],
)
def test_transition_source_predicates_required_audit_and_caller_continuity(
    result_database, status, action, valid, after
):
    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db, db.begin_nested():
        row = entry(db, tenant, person, status=status)
        root, nested = db.get_transaction(), db.get_nested_transaction()
        kwargs = {
            "tenant_id": tenant,
            "entry_id": row.id,
            "actor_person_id": person,
            "audit_context": context(tenant),
            "action": action,
        }
        if valid:
            fact = queue.transition(db, **kwargs)
            assert fact.status == after
            audit = db.scalar(select(AuditEvent))
            assert audit.action == f"success_queue.{action}"
            assert audit.actor_party_id == person
            if action == "assign":
                assert fact.assigned_to == person
        else:
            with pytest.raises(SuccessBadRequest, match="invalid transition"):
                queue.transition(db, **kwargs)
            assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert db.get_transaction() is root and db.get_nested_transaction() is nested


def test_audit_scope_rejected_before_sql_and_missing_entry_error(result_database):
    _, app, tenant, person, _, _, other, *_ = result_database
    with scoped(app, tenant) as db:
        seen = []

        def observe(*args):
            seen.append(args)

        event.listen(db.bind, "before_cursor_execute", observe)
        try:
            with pytest.raises(ValueError, match="tenant scope"):
                queue.transition(
                    db,
                    tenant_id=tenant,
                    entry_id=uuid4(),
                    action="assign",
                    actor_person_id=person,
                    audit_context=context(other),
                )
            assert seen == []
        finally:
            event.remove(db.bind, "before_cursor_execute", observe)
        with pytest.raises(SuccessNotFound, match="queue entry not found"):
            queue.transition(
                db,
                tenant_id=tenant,
                entry_id=uuid4(),
                action="assign",
                actor_person_id=person,
                audit_context=context(tenant),
            )


def test_almost_complete_boundary_and_cohort_only_course_selection(result_database):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, course, days=1)
        comp = CourseCompletion(
            tenant_id=tenant,
            person_id=person,
            course_id=course,
            status="in_progress",
            pct=0.8,
        )
        db.add(comp)
        db.flush()

        def members(segment):
            return queue.segment_members(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment=segment,
                terms=TERMS,
                now=NOW,
                accounts=population(tenant, person),
            )

        assert [p.id for p in members("almost_complete")] == [person]
        comp.pct = 0.79999
        assert members("almost_complete") == ()
        comp.pct, comp.status = 1, "completed"
        assert members("almost_complete") == ()
        assert (
            members("failed_final")
            == members("below_passing")
            == members("overdue_work")
            == ()
        )
        with pytest.raises(SuccessBadRequest, match="unknown segment"):
            members("low_attendance")


def test_inactive_segment_all_role_anchor_and_explicit_account_population(
    result_database,
):
    admin, app, tenant, person, course, *_ = result_database
    with admin.begin() as conn:
        other_person = _person(conn, tenant)
    with scoped(app, tenant) as db:
        target = seed(db, tenant, person, course, days=1)
        seed(db, tenant, person, course, days=30, role="instructor")
        db.add(
            Enrollment(
                tenant_id=tenant,
                person_id=other_person,
                cohort_id=target,
                role_in_cohort="student",
                status="active",
                created_at=NOW - timedelta(days=20),
            )
        )
        db.get(PartyPerson, person).last_name = "A"
        db.get(PartyPerson, other_person).last_name = "Z"
        db.flush()
        assert [
            p.id
            for p in queue.segment_members(
                db,
                tenant_id=tenant,
                cohort_id=target,
                segment="inactive",
                terms=TERMS,
                now=NOW,
                accounts=population(tenant, person, other_person),
            )
        ] == [person, other_person]
        # Sweep takes only student anchors; the older instructor anchor is segment-only.
        counts = queue.sweep(
            db,
            tenant_id=tenant,
            terms=TERMS,
            now=NOW,
            accounts=population(tenant, person, other_person),
        )
        assert counts.opened == 1
        assert db.scalar(select(SuccessQueueEntry.person_id)) == other_person
        db.get(Party, other_person).is_active = True
        assert [
            p.id
            for p in queue.segment_members(
                db,
                tenant_id=tenant,
                cohort_id=target,
                segment="inactive",
                terms=TERMS,
                now=NOW,
                accounts=population(tenant, person),
            )
        ] == [person]


def test_message_required_sequential_delivery_audit_zero_audience_and_scope(
    result_database,
):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db, db.begin_nested():
        cohort = seed(db, tenant, person, course)
        root, nested = db.get_transaction(), db.get_nested_transaction()
        seen = []

        def deliver(caller, recipient, message, action_id):
            assert caller is db and caller.get_transaction() is root
            assert caller.get_nested_transaction() is nested
            assert message.subject == "Subject" and message.body == "<b>Body</b>\nLine"
            assert message.tenant_id == tenant and message.cohort_id == cohort
            assert message.idempotency_prefix == "segmsg"
            assert len(action_id) == 12 and int(action_id, 16) >= 0
            seen.append(recipient.id)

        result = queue.message_segment(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            segment="inactive",
            subject=" Subject ",
            body=" <b>Body</b>\nLine ",
            actor_person_id=person,
            terms=TERMS,
            audit_context=context(tenant),
            delivery=deliver,
            now=NOW,
            accounts=population(tenant, person),
        )
        assert result.recipients == 1 and seen == [person]
        audit = db.scalar(select(AuditEvent))
        assert audit.details["action_id"] == result.action_id
        assert audit.action == "success_queue.message_segment"
        zero = queue.message_segment(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            segment="failed_final",
            subject="Subject",
            body="Body",
            actor_person_id=person,
            terms=TERMS,
            audit_context=context(tenant),
            delivery=deliver,
            now=NOW,
            accounts=population(tenant, person),
        )
        assert zero.recipients == 0 and seen == [person]
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2
        with pytest.raises(
            SuccessBadRequest, match="subject and message are both required"
        ):
            queue.message_segment(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment="inactive",
                subject=" ",
                body="Body",
                actor_person_id=person,
                terms=TERMS,
                audit_context=context(tenant),
                delivery=deliver,
                accounts=population(tenant, person),
            )
        assert db.get_transaction() is root and db.get_nested_transaction() is nested


def test_partial_delivery_failure_propagates_and_caller_rollback_removes_every_write(
    result_database,
):
    admin, app, tenant, person, course, *_ = result_database
    with admin.begin() as conn:
        second = _person(conn, tenant)
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, course)
        db.add(
            Enrollment(
                tenant_id=tenant,
                person_id=second,
                cohort_id=cohort,
                role_in_cohort="student",
                status="active",
                created_at=NOW - timedelta(days=20),
            )
        )
        db.flush()
    seen = []
    with pytest.raises(RuntimeError, match="required delivery"):
        with scoped(app, tenant) as db:
            root = db.get_transaction()

            def failing(caller, recipient, message, action_id):
                assert caller is db and caller.get_transaction() is root
                assert caller.get_nested_transaction() is None
                entry(caller, tenant, recipient.id, reason="delivery_probe")
                seen.append(recipient.id)
                if len(seen) == 2:
                    raise RuntimeError("required delivery")

            queue.message_segment(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment="inactive",
                subject="Subject",
                body="Body",
                actor_person_id=person,
                terms=TERMS,
                audit_context=context(tenant),
                delivery=failing,
                now=NOW,
                accounts=population(tenant, person, second),
            )
    assert len(seen) == 2
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(SuccessQueueEntry)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("operation", ["transition", "message"])
@pytest.mark.parametrize("failure", ["registry", "actor"])
def test_real_required_audit_failure_propagates_and_caller_rolls_back(
    result_database, monkeypatch, operation, failure
):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, course)
        row_id = entry(db, tenant, person).id
    if failure == "registry":
        monkeypatch.setattr(
            audit_actions, "_active_registry", audit_actions.AuditActionRegistry(())
        )
        expected_error = audit_actions.UndeclaredAuditActionError
        attribution = context(tenant)
    else:
        expected_error = MissingAuditActorError
        # A genuine Party enrichment must never substitute for the human principal.
        attribution = AttemptAuditContext(tenant, "user", "academy")
    delivered = []
    with pytest.raises(expected_error):
        with scoped(app, tenant) as db:
            root = db.get_transaction()

            def deliver(caller, recipient, message, action_id):
                assert caller is db and caller.get_transaction() is root
                assert caller.get_nested_transaction() is None
                entry(caller, tenant, recipient.id, reason="audit_delivery_probe")
                delivered.append(recipient.id)

            if operation == "transition":
                queue.transition(
                    db,
                    tenant_id=tenant,
                    entry_id=row_id,
                    action="resolve",
                    actor_person_id=person,
                    audit_context=attribution,
                )
            else:
                queue.message_segment(
                    db,
                    tenant_id=tenant,
                    cohort_id=cohort,
                    segment="inactive",
                    subject="Subject",
                    body="Body",
                    actor_person_id=person,
                    terms=TERMS,
                    audit_context=attribution,
                    delivery=deliver,
                    now=NOW,
                    accounts=population(tenant, person),
                )
    assert delivered == ([person] if operation == "message" else [])
    with scoped(app, tenant) as db:
        assert db.get(SuccessQueueEntry, row_id).status == "open"
        assert db.scalar(select(func.count()).select_from(SuccessQueueEntry)) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_source_unsorted_first_cohort_min_anchor_and_nonunique_courses(result_database):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        first = seed(db, tenant, person, course, days=1)
        second = seed(db, tenant, person, course, days=20)
        raw = db.execute(
            select(Enrollment.person_id, Enrollment.cohort_id, Enrollment.created_at)
            .join(
                Party,
                (Party.id == Enrollment.person_id)
                & (Party.tenant_id == Enrollment.tenant_id),
            )
            .join(PartyPerson, PartyPerson.party_id == Party.id)
            .where(
                Enrollment.tenant_id == tenant,
                Enrollment.role_in_cohort == "student",
                Enrollment.status == "active",
                Party.id.in_(population(tenant, person).active_person_ids),
            )
        ).all()
        assert queue._active_course_ids(db, tenant, [first, second]) == [course, course]
        queue.sweep(
            db,
            tenant_id=tenant,
            terms=TERMS,
            now=NOW,
            accounts=population(tenant, person),
        )
        row = db.scalar(select(SuccessQueueEntry))
        assert row.cohort_id == raw[0].cohort_id
        assert row.supporting_facts["days_inactive"] == 20


def test_upsert_refreshes_only_source_first_open_or_ack_and_keeps_history(
    result_database,
):
    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db:
        a = entry(db, tenant, person, status="acknowledged", facts={"a": 1})
        b = entry(db, tenant, person, status="acknowledged", facts={"b": 2})
        expected = db.scalars(
            select(SuccessQueueEntry).where(
                SuccessQueueEntry.tenant_id == tenant,
                SuccessQueueEntry.person_id == person,
                SuccessQueueEntry.reason_kind == "inactivity",
                SuccessQueueEntry.status.in_(["open", "acknowledged"]),
            )
        ).first()
        expected.assigned_to, expected.score_hint = person, 9
        assert (
            queue._upsert(
                db,
                tenant_id=tenant,
                person_id=person,
                cohort_id=None,
                reason="inactivity",
                facts={"new": 3},
                severity="high",
                action="source action",
                now=NOW,
            )
            == "refreshed"
        )
        assert expected.supporting_facts == {"new": 3}
        assert expected.assigned_to == person and expected.score_hint == 9
        untouched = b if expected.id == a.id else a
        assert untouched.supporting_facts in ({"a": 1}, {"b": 2})


def test_queue_sort_filters_unknown_severity_and_inactive_person_read(result_database):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, course)
        db.get(Party, person).is_active = False
        for index, severity in enumerate(("unknown", "low", "medium", "high")):
            row = entry(
                db,
                tenant,
                person,
                reason=f"reason{index}",
                severity=severity,
                cohort=cohort,
            )
            row.detected_at = NOW + timedelta(days=4 - index)
        db.flush()
        rows = queue.list_entries(db, tenant_id=tenant)
        assert [row.entry.severity for row in rows] == [
            "high",
            "medium",
            "low",
            "unknown",
        ]
        assert (
            len(
                queue.list_entries(
                    db,
                    tenant_id=tenant,
                    severity="medium",
                    status="open",
                    cohort_id=cohort,
                )
            )
            == 1
        )
        assert rows[-1].cohort_name == "Success"


def test_below_passing_equal_worst_retains_input_first(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        alternate = Course(
            tenant_id=tenant,
            title="Alternate",
            slug=uuid4().hex,
            discipline="engineering",
            source_ref="source",
            status="published",
            listed=False,
        )
        db.add(alternate)
        db.flush()
        for cid in (course, alternate.id):
            for index in range(2):
                row = Activity(
                    tenant_id=tenant,
                    course_id=cid,
                    title=f"Work {index}",
                    weight=1,
                    type="mcq_test",
                    pass_threshold=0.0,
                    assessment_mode="graded",
                    grading="auto",
                )
                db.add(row)
                db.flush()
                score(db, tenant, person, row.id, fraction=0.4)
        # Existing ungraded source fixture activity leaves course's graded share >20%.
        hit = queue._rule_below_passing(
            db,
            tenant_id=tenant,
            person_id=person,
            course_ids=[alternate.id, course],
            terms=SuccessTerms(7, 60, 6, 2, 3, 7, 5, 2, 0.20, 0.5, 0.8),
        )
        assert hit[0]["course_id"] == str(alternate.id)


def test_unavailable_account_dropped_instructor_never_enter_sweep_population(
    result_database,
):
    admin, app, tenant, person, course, *_ = result_database
    with admin.begin() as conn:
        inactive = _person(conn, tenant)
        dropped = _person(conn, tenant)
        instructor = _person(conn, tenant)
    with scoped(app, tenant) as db:
        target = seed(db, tenant, person, course)
        seed(db, tenant, inactive, course)
        seed(db, tenant, dropped, course, status="dropped")
        seed(db, tenant, instructor, course, role="instructor")
        db.get(Party, inactive).is_active = True
        assert queue.sweep(
            db,
            tenant_id=tenant,
            terms=TERMS,
            now=NOW,
            accounts=population(tenant, person, dropped, instructor),
        ) == SweepResult(1, 0, 0)
        assert db.scalar(select(SuccessQueueEntry.person_id)) == person
        assert [
            p.id
            for p in queue.segment_members(
                db,
                tenant_id=tenant,
                cohort_id=target,
                segment="inactive",
                terms=TERMS,
                now=NOW,
                accounts=population(tenant, person, dropped, instructor),
            )
        ] == [person]


def test_success_account_population_is_independent_of_identity_activity(
    result_database,
):
    admin, app, tenant, person, course, *_ = result_database
    with admin.begin() as conn:
        unavailable = _person(conn, tenant)
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, course)
        db.add(
            Enrollment(
                tenant_id=tenant,
                person_id=unavailable,
                cohort_id=cohort,
                status="active",
                role_in_cohort="student",
                created_at=NOW - timedelta(days=20),
            )
        )
        db.get(Party, person).is_active = False
        db.get(Party, unavailable).is_active = True
        db.flush()
        accounts = population(tenant, person)
        assert (
            queue.sweep(
                db, tenant_id=tenant, terms=TERMS, accounts=accounts, now=NOW
            ).opened
            == 1
        )
        assert db.scalar(select(SuccessQueueEntry.person_id)) == person
        assert [
            p.id
            for p in queue.segment_members(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment="inactive",
                terms=TERMS,
                accounts=accounts,
                now=NOW,
            )
        ] == [person]
        delivered = []

        def deliver(caller, recipient, message, action_id):
            assert caller is db
            delivered.append(recipient.id)

        result = queue.message_segment(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            segment="inactive",
            subject="Subject",
            body="Body",
            actor_person_id=person,
            terms=TERMS,
            accounts=accounts,
            audit_context=context(tenant),
            delivery=deliver,
            now=NOW,
        )
        assert result.recipients == 1 and delivered == [person]


@pytest.mark.parametrize("operation", ["sweep", "segment", "message"])
def test_account_population_scope_rejected_before_any_sql(result_database, operation):
    _, app, tenant, person, _, _, other, *_ = result_database
    with scoped(app, tenant) as db:
        seen = []

        def observe(*args):
            seen.append(args)

        event.listen(db.bind, "before_cursor_execute", observe)
        try:
            wrong = population(other, person)
            with pytest.raises(ValueError, match="account population tenant scope"):
                if operation == "sweep":
                    queue.sweep(db, tenant_id=tenant, terms=TERMS, accounts=wrong)
                elif operation == "segment":
                    queue.segment_members(
                        db,
                        tenant_id=tenant,
                        cohort_id=uuid4(),
                        segment="inactive",
                        terms=TERMS,
                        accounts=wrong,
                    )
                else:
                    queue.message_segment(
                        db,
                        tenant_id=tenant,
                        cohort_id=uuid4(),
                        segment="inactive",
                        subject="Subject",
                        body="Body",
                        actor_person_id=person,
                        terms=TERMS,
                        accounts=wrong,
                        audit_context=context(tenant),
                        delivery=lambda *args: None,
                    )
            assert seen == []
        finally:
            event.remove(db.bind, "before_cursor_execute", observe)


def test_success_account_population_is_required_keyword_without_default():
    from inspect import Parameter, signature

    for operation in (queue.sweep, queue.segment_members, queue.message_segment):
        parameter = signature(operation).parameters["accounts"]
        assert parameter.default is Parameter.empty
        assert parameter.kind is Parameter.KEYWORD_ONLY


def test_configured_inactivity_sweep_segments_and_audited_delivery_share_terms(
    result_database,
):
    from dataclasses import replace

    _, app, tenant, person, course, _, other, *_ = result_database
    terms = replace(TERMS, inactive_days=3, inactivity_high_factor=3)
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, course, days=8)
        root = db.get_transaction()
        result = queue.sweep(
            db,
            tenant_id=tenant,
            terms=terms,
            accounts=population(tenant, person),
            now=NOW,
        )
        assert result.opened >= 1
        hits = queue.list_entries(db, tenant_id=tenant)
        inactivity = next(
            row.entry for row in hits if row.entry.reason_kind == "inactivity"
        )
        assert inactivity.severity == "medium"
        assert inactivity.supporting_facts["threshold_days"] == 3
        calls = []

        def delivery(session, recipient, message, action_id):
            assert session is db and session.get_transaction() is root
            calls.append((recipient.id, message.subject, action_id))

        sent = queue.message_segment(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            segment="inactive",
            subject="Configured",
            body="Window",
            actor_person_id=person,
            terms=terms,
            accounts=population(tenant, person),
            audit_context=context(tenant),
            delivery=delivery,
            now=NOW,
        )
        assert sent.recipients == 1 and calls == [
            (person, "Configured", sent.action_id)
        ]
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.tenant_id == tenant)
                .where(AuditEvent.action == "success_queue.message_segment")
            )
            == 1
        )
        assert db.get_transaction() is root
    with scoped(app, other) as db:
        assert (
            queue.segment_members(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment="inactive",
                terms=terms,
                accounts=population(tenant, person),
                now=NOW,
            )
            == ()
        )
        assert queue.list_entries(db, tenant_id=tenant) == ()


@pytest.mark.parametrize("receipts", [False, True])
def test_transition_receipt_single_execution_and_accepted_same_assignee_audit(
    result_database, receipts
):
    from uuid import UUID

    from dotmac_learning.success_contracts import QueueFact, SuccessTransitionEffect

    _, app, tenant, person, *_ = result_database
    operation = queue.transition_with_receipt if receipts else queue.transition
    with scoped(app, tenant) as db, db.begin_nested():
        row = entry(db, tenant, person, status="acknowledged")
        row.assigned_to = person
        db.flush()
        root, nested = db.get_transaction(), db.get_nested_transaction()
        for expected_count in (1, 2):
            observed = operation(
                db,
                tenant_id=tenant,
                entry_id=row.id,
                action="assign",
                actor_person_id=person,
                assigned_to=person,
                audit_context=context(tenant),
            )
            assert type(observed) is (
                SuccessTransitionEffect if receipts else QueueFact
            )
            fact = observed.entry if receipts else observed
            assert fact.status == "acknowledged" and fact.assigned_to == person
            audits = db.scalars(select(AuditEvent)).all()
            assert len(audits) == expected_count
            if receipts:
                assert isinstance(observed.audit_event_id, UUID)
                audit = db.get(AuditEvent, observed.audit_event_id)
                assert audit is not None and audit.id == observed.audit_event_id
                assert audit.action == "success_queue.assign"
                assert audit.entity_id == str(row.id)
                assert audit.is_success is None and audit.occurred_at is not None
            assert db.get_transaction() is root
            assert db.get_nested_transaction() is nested


@pytest.mark.parametrize("receipts", [False, True])
@pytest.mark.parametrize("recipients", [0, 1])
def test_message_receipt_single_execution_actual_audit_even_empty_audience(
    result_database, receipts, recipients
):
    from uuid import UUID

    from dotmac_learning.success_contracts import (
        SegmentMessageEffect,
        SegmentMessageResult,
    )

    _, app, tenant, person, course, *_ = result_database
    operation = (
        queue.message_segment_with_receipt if receipts else queue.message_segment
    )
    with scoped(app, tenant) as db, db.begin_nested():
        cohort = seed(db, tenant, person, course)
        root, nested = db.get_transaction(), db.get_nested_transaction()
        seen = []

        def deliver(caller, recipient, message, action_id):
            assert caller is db and caller.get_transaction() is root
            assert caller.get_nested_transaction() is nested
            assert caller.scalar(select(func.count()).select_from(AuditEvent)) == 0
            assert message.subject == "Subject" and message.body == "Body"
            seen.append((recipient.id, action_id))

        observed = operation(
            db,
            tenant_id=tenant,
            cohort_id=cohort,
            segment="inactive",
            subject=" Subject ",
            body=" Body ",
            actor_person_id=person,
            terms=TERMS,
            accounts=population(tenant, *([person] if recipients else [])),
            audit_context=context(tenant),
            delivery=deliver,
            now=NOW,
        )
        assert type(observed) is (
            SegmentMessageEffect if receipts else SegmentMessageResult
        )
        result = observed.result if receipts else observed
        assert type(result) is SegmentMessageResult and result.recipients == recipients
        assert seen == ([(person, result.action_id)] if recipients else [])
        (audit,) = db.scalars(select(AuditEvent)).all()
        if receipts:
            assert isinstance(observed.audit_event_id, UUID)
            assert audit.id == observed.audit_event_id
        assert audit.action == "success_queue.message_segment"
        assert audit.details["action_id"] == result.action_id
        assert audit.details["recipients"] == recipients
        assert audit.is_success is None and audit.occurred_at is not None
        assert db.get_transaction() is root and db.get_nested_transaction() is nested


@pytest.mark.parametrize("receipts", [False, True])
def test_transition_refreshes_stale_identity_before_status_validation(
    result_database, receipts
):
    from tests.test_learning_catalog import _scope

    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db:
        row_id = entry(db, tenant, person).id
    with scoped(app, tenant) as db:
        cached = db.get(SuccessQueueEntry, row_id)
        assert cached.status == "open"
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                SuccessQueueEntry.__table__.update()
                .where(SuccessQueueEntry.id == row_id)
                .values(status="resolved")
            )
        operation = queue.transition_with_receipt if receipts else queue.transition
        with pytest.raises(SuccessBadRequest, match="from 'resolved'"):
            operation(
                db,
                tenant_id=tenant,
                entry_id=row_id,
                action="acknowledge",
                actor_person_id=person,
                audit_context=context(tenant),
            )
        assert cached.status == "resolved"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_receipt_callback_failure_propagates_without_effects_after_caller_rollback(
    result_database,
):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, course)
    seen = []
    with pytest.raises(RuntimeError, match="delivery failed"):
        with scoped(app, tenant) as db:
            root = db.get_transaction()

            def deliver(caller, recipient, message, action_id):
                assert caller is db and caller.get_transaction() is root
                entry(caller, tenant, recipient.id, reason="receipt_delivery_probe")
                seen.append(recipient.id)
                raise RuntimeError("delivery failed")

            queue.message_segment_with_receipt(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment="inactive",
                subject="Subject",
                body="Body",
                actor_person_id=person,
                terms=TERMS,
                accounts=population(tenant, person),
                audit_context=context(tenant),
                delivery=deliver,
                now=NOW,
            )
    assert seen == [person]
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(SuccessQueueEntry)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("receipts", [False, True])
def test_transition_waits_for_actual_row_writer_and_checks_current_status(
    result_database, receipts
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from tests.test_learning_catalog import _scope

    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db:
        row_id = entry(db, tenant, person).id
    cached, written, started, release = Event(), Event(), Event(), Event()
    pids = {}

    def winner():
        assert cached.wait(10)
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(text("SET LOCAL statement_timeout='15s'"))
            pids["winner"] = conn.scalar(text("SELECT pg_backend_pid()"))
            conn.execute(
                SuccessQueueEntry.__table__.update()
                .where(SuccessQueueEntry.id == row_id)
                .values(status="resolved", resolved_at=NOW, resolved_by=person)
            )
            written.set()
            assert release.wait(10)
        return "committed"

    def contender():
        with scoped(app, tenant) as db:
            db.execute(text("SET LOCAL statement_timeout='15s'"))
            row = db.get(SuccessQueueEntry, row_id)
            assert row.status == "open"
            pids["contender"] = db.scalar(text("SELECT pg_backend_pid()"))
            cached.set()
            assert written.wait(10)
            root = db.get_transaction()
            with db.begin_nested():
                nested = db.get_nested_transaction()
                started.set()
                operation = (
                    queue.transition_with_receipt if receipts else queue.transition
                )
                with pytest.raises(SuccessBadRequest, match="from 'resolved'"):
                    operation(
                        db,
                        tenant_id=tenant,
                        entry_id=row_id,
                        action="acknowledge",
                        actor_person_id=person,
                        audit_context=context(tenant),
                    )
                assert row.status == "resolved" and row.resolved_by == person
                assert db.get_transaction() is root
                assert db.get_nested_transaction() is nested
                assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        return "current status checked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(winner), pool.submit(contender)
        try:
            assert started.wait(10)
            with app.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as observer:
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    waiting = observer.execute(
                        text(
                            "SELECT wait_event_type, pg_blocking_pids(pid) "
                            "FROM pg_stat_activity WHERE pid=:pid"
                        ),
                        {"pid": pids["contender"]},
                    ).one_or_none()
                    if (
                        waiting is not None
                        and waiting[0] == "Lock"
                        and pids["winner"] in waiting[1]
                    ):
                        break
                    sleep(0.01)
                else:
                    pytest.fail("queue transition did not wait for the row writer")
        finally:
            release.set()
            cached.set()
            written.set()
        assert one.result(timeout=20) == "committed"
        assert two.result(timeout=20) == "current status checked"
    with scoped(app, tenant) as db:
        assert db.get(SuccessQueueEntry, row_id).status == "resolved"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("receipts", [False, True])
@pytest.mark.parametrize("mode", ["disabled", "suppressed", "native"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("allowed", [False, True])
def test_pending_success_guard_and_different_scalar_keep_actual_single_audit(
    result_database, receipts, mode, nested, allowed
):
    from tests.test_learning_catalog import _scope

    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db:
        row_id = entry(db, tenant, person).id
    operation = queue.transition_with_receipt if receipts else queue.transition
    with scoped(app, tenant) as db:
        row = db.get(SuccessQueueEntry, row_id)
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                SuccessQueueEntry.__table__.update()
                .where(SuccessQueueEntry.id == row_id)
                .values(severity="high")
            )
        root = db.get_transaction()
        savepoint = db.begin_nested() if nested else None
        # SQLAlchemy materializes a caller savepoint lazily on the first SELECT.
        expected_read_sql = (["savepoint"] if nested else []) + ["select"]
        db.autoflush = mode != "disabled"
        row.recommended_action = "caller pending"
        if not allowed:
            row.status = "resolved"
            row.resolved_at = NOW
            row.resolved_by = person
        histories = {
            key: inspect(row).attrs[key].history
            for key in ("status", "resolved_at", "resolved_by", "recommended_action")
        }
        seen, flushes = [], []

        def observe(conn, cursor, statement, parameters, execution_context, many):
            seen.append(statement.lstrip().split()[0].lower())

        def flushed(*args):
            flushes.append(True)

        event.listen(app, "before_cursor_execute", observe)
        event.listen(db, "before_flush", flushed)
        try:
            with db.no_autoflush if mode == "suppressed" else nullcontext():
                kwargs = {
                    "tenant_id": tenant,
                    "entry_id": row_id,
                    "action": "acknowledge",
                    "actor_person_id": person,
                    "audit_context": context(tenant),
                }
                if allowed:
                    observed = operation(db, **kwargs)
                    fact = observed.entry if receipts else observed
                    assert fact.status == "acknowledged"
                    assert fact.recommended_action == "caller pending"
                    assert fact.severity == "high"
                    (audit,) = db.scalars(select(AuditEvent)).all()
                    assert audit.action == "success_queue.acknowledge"
                    assert audit.entity_id == str(row_id)
                    if receipts:
                        assert observed.audit_event_id == audit.id
                    assert (
                        db.scalar(select(SuccessQueueEntry.recommended_action))
                        == "caller pending"
                    )
                else:
                    with pytest.raises(SuccessBadRequest, match="from 'resolved'"):
                        operation(db, **kwargs)
                    assert row.status == "resolved" and row.resolved_at == NOW
                    if mode != "native":
                        assert {
                            key: inspect(row).attrs[key].history for key in histories
                        } == histories
                        assert seen == expected_read_sql and flushes == []
                    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
                assert row.severity == "high"
                assert db.get(SuccessQueueEntry, row_id) is row
                assert db.get_transaction() is root
                assert db.get_nested_transaction() is savepoint
        finally:
            event.remove(app, "before_cursor_execute", observe)
            event.remove(db, "before_flush", flushed)
        if not allowed:
            db.flush()
            assert db.scalar(select(SuccessQueueEntry.status)) == "resolved"
        db.rollback()
    with scoped(app, tenant) as db:
        row = db.get(SuccessQueueEntry, row_id)
        assert row.status == "open" and row.recommended_action == ""
        assert row.severity == "high"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("receipts", [False, True])
@pytest.mark.parametrize("clean", ["expired", "deferred"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("recommended_action", None),
        ("recommended_action", "DELETE"),
        ("status", "open"),
        ("id", "KEY"),
        ("tenant_id", "KEY"),
        ("supporting_facts", {"pending": True}),
    ],
)
def test_success_refusal_preserves_touched_scalar_history_and_pending_keys(
    result_database, receipts, clean, field, value
):
    from tests.test_learning_catalog import _scope

    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db:
        row_id = entry(db, tenant, person).id
    with scoped(app, tenant) as db:
        row = db.scalars(
            select(SuccessQueueEntry)
            .options(defer(SuccessQueueEntry.severity))
            .where(SuccessQueueEntry.id == row_id)
        ).one()
        if clean == "expired":
            db.expire(row, ["severity"])
        assert inspect(row).attrs.severity.loaded_value is NO_VALUE
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(
                SuccessQueueEntry.__table__.update()
                .where(SuccessQueueEntry.id == row_id)
                .values(severity="high")
            )
        db.autoflush = False
        if value == "DELETE":
            delattr(row, field)
        else:
            setattr(row, field, uuid4() if value == "KEY" else value)
        attribute = inspect(row).attrs[field]
        before = (attribute.loaded_value, attribute.history)
        assert field not in inspect(row).unmodified
        operation = queue.transition_with_receipt if receipts else queue.transition
        with pytest.raises(SuccessBadRequest, match="invalid transition"):
            operation(
                db,
                tenant_id=tenant,
                entry_id=row_id,
                action="invalid",
                actor_person_id=person,
                audit_context=context(tenant),
            )
        assert (attribute.loaded_value, attribute.history) == before
        assert row.severity == "high" and inspect(row).identity == (row_id,)
        db.rollback()


@pytest.mark.parametrize("receipts", [False, True])
def test_success_tenant_context_mismatch_precedes_execute(
    result_database, monkeypatch, receipts
):
    _, app, tenant, person, _, _, other, *_ = result_database
    with scoped(app, tenant) as db:
        row_id = entry(db, tenant, person).id

        def forbidden(*args, **kwargs):
            raise AssertionError("context mismatch must precede SQL")

        monkeypatch.setattr(db, "scalar", forbidden)
        monkeypatch.setattr(db, "scalars", forbidden)
        monkeypatch.setattr(db, "execute", forbidden)
        operation = queue.transition_with_receipt if receipts else queue.transition
        with pytest.raises(ValueError, match="tenant scope"):
            operation(
                db,
                tenant_id=tenant,
                entry_id=row_id,
                action="acknowledge",
                actor_person_id=person,
                audit_context=context(other),
            )


@pytest.mark.parametrize("receipts", [False, True])
def test_pending_action_waits_for_actual_row_writer_and_current_status(
    result_database, receipts
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from tests.test_learning_catalog import _scope

    _, app, tenant, person, *_ = result_database
    with scoped(app, tenant) as db:
        row_id = entry(db, tenant, person).id
    cached, written, started, release = Event(), Event(), Event(), Event()
    pids = {}

    def winner():
        assert cached.wait(10)
        with app.begin() as conn:
            _scope(conn, tenant)
            conn.execute(text("SET LOCAL statement_timeout='15s'"))
            pids["winner"] = conn.scalar(text("SELECT pg_backend_pid()"))
            conn.execute(
                SuccessQueueEntry.__table__.update()
                .where(SuccessQueueEntry.id == row_id)
                .values(status="resolved", resolved_at=NOW, resolved_by=person)
            )
            written.set()
            assert release.wait(10)
        return "committed"

    def contender():
        with scoped(app, tenant) as db:
            db.execute(text("SET LOCAL statement_timeout='15s'"))
            row = db.get(SuccessQueueEntry, row_id)
            assert row.status == "open"
            pids["contender"] = db.scalar(text("SELECT pg_backend_pid()"))
            cached.set()
            assert written.wait(10)
            root = db.get_transaction()
            with db.begin_nested():
                nested = db.get_nested_transaction()
                db.autoflush = False
                row.recommended_action = "pending through lock wait"
                history = inspect(row).attrs.recommended_action.history
                started.set()
                operation = (
                    queue.transition_with_receipt if receipts else queue.transition
                )
                with pytest.raises(SuccessBadRequest, match="from 'resolved'"):
                    operation(
                        db,
                        tenant_id=tenant,
                        entry_id=row_id,
                        action="acknowledge",
                        actor_person_id=person,
                        audit_context=context(tenant),
                    )
                assert row.status == "resolved" and row.resolved_by == person
                assert row.recommended_action == "pending through lock wait"
                assert inspect(row).attrs.recommended_action.history == history
                assert db.get_transaction() is root
                assert db.get_nested_transaction() is nested
                assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        db.rollback()
        return "current status checked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(winner), pool.submit(contender)
        try:
            assert started.wait(10)
            with app.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as observer:
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    waiting = observer.execute(
                        text(
                            "SELECT wait_event_type, pg_blocking_pids(pid) "
                            "FROM pg_stat_activity WHERE pid=:pid"
                        ),
                        {"pid": pids["contender"]},
                    ).one_or_none()
                    if (
                        waiting is not None
                        and waiting[0] == "Lock"
                        and pids["winner"] in waiting[1]
                    ):
                        break
                    sleep(0.01)
                else:
                    pytest.fail("queue transition did not wait for the row writer")
        finally:
            release.set()
            cached.set()
            written.set()
        assert one.result(timeout=20) == "committed"
        assert two.result(timeout=20) == "current status checked"
    with scoped(app, tenant) as db:
        assert db.get(SuccessQueueEntry, row_id).status == "resolved"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("receipts", [False, True])
@pytest.mark.parametrize(
    "state",
    ["transient", "pending", "external_deleted", "pending_delete", "flushed_delete"],
)
def test_success_native_membership_and_missing_sql(result_database, receipts, state):
    from tests.test_learning_catalog import _scope

    _, app, tenant, person, *_ = result_database
    operation = queue.transition_with_receipt if receipts else queue.transition
    if state not in {"transient", "pending"}:
        with scoped(app, tenant) as db:
            row_id = entry(db, tenant, person).id
    else:
        row_id = uuid4()
    with scoped(app, tenant) as db:
        db.autoflush = False
        if state in {"transient", "pending"}:
            row = SuccessQueueEntry(
                id=row_id,
                tenant_id=tenant,
                person_id=person,
                reason_kind="inactivity",
                supporting_facts={},
                severity="medium",
                detected_at=NOW,
                status="open",
            )
            if state == "pending":
                db.add(row)
        else:
            row = db.get(SuccessQueueEntry, row_id)
            if state == "external_deleted":
                with app.begin() as conn:
                    _scope(conn, tenant)
                    conn.execute(
                        SuccessQueueEntry.__table__.delete().where(
                            SuccessQueueEntry.id == row_id
                        )
                    )
            else:
                db.delete(row)
                if state == "flushed_delete":
                    db.flush()
        kwargs = {
            "tenant_id": tenant,
            "entry_id": row_id,
            "action": "invalid",
            "actor_person_id": person,
            "audit_context": context(tenant),
        }
        if state == "pending_delete":
            with pytest.raises(SuccessBadRequest):
                operation(db, **kwargs)
            assert row in db.deleted and row not in db.new
        else:
            with pytest.raises(SuccessNotFound):
                operation(db, **kwargs)
        if state == "transient":
            assert inspect(row).transient
        elif state == "pending":
            assert row in db.new and inspect(row).pending
        elif state == "flushed_delete":
            assert inspect(row).deleted
        db.rollback()


@pytest.mark.parametrize("receipts", [False, True])
def test_success_flagged_json_and_invalid_unrelated_pending_row(
    result_database, receipts
):
    from sqlalchemy.orm.attributes import flag_modified

    _, app, tenant, person, *_ = result_database
    operation = queue.transition_with_receipt if receipts else queue.transition
    with scoped(app, tenant) as db:
        row_id = entry(db, tenant, person).id
        unrelated_id = entry(db, tenant, person, reason="unrelated").id
    with scoped(app, tenant) as db:
        row = db.get(SuccessQueueEntry, row_id)
        unrelated = db.get(SuccessQueueEntry, unrelated_id)
        db.autoflush = False
        row.supporting_facts["caller"] = True
        flag_modified(row, "supporting_facts")
        unrelated.severity = None
        history = inspect(row).attrs.supporting_facts.history
        with pytest.raises(SuccessBadRequest):
            operation(
                db,
                tenant_id=tenant,
                entry_id=row_id,
                action="invalid",
                actor_person_id=person,
                audit_context=context(tenant),
            )
        assert inspect(row).attrs.supporting_facts.history == history
        assert row.supporting_facts == {"caller": True}
        assert unrelated.severity is None and db.is_active
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        db.rollback()


@pytest.mark.parametrize("receipts", [False, True])
def test_success_native_flush_failure_requires_caller_rollback(
    result_database, receipts
):
    _, app, tenant, person, *_ = result_database
    operation = queue.transition_with_receipt if receipts else queue.transition
    with scoped(app, tenant) as db:
        row_id = entry(db, tenant, person).id
    with scoped(app, tenant) as db:
        row = db.get(SuccessQueueEntry, row_id)
        db.autoflush = False
        row.severity = None
        with pytest.raises(IntegrityError):
            operation(
                db,
                tenant_id=tenant,
                entry_id=row_id,
                action="acknowledge",
                actor_person_id=person,
                audit_context=context(tenant),
            )
        assert not db.is_active
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.get(SuccessQueueEntry, row_id).status == "open"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_success_queue_scalar_projection_matches_actual_mapped_keys():
    mapper = inspect(SuccessQueueEntry)
    assert set(mapper.column_attrs.keys()) == set(
        SuccessQueueEntry.__table__.columns.keys()
    )
    assert all(
        attribute.key == attribute.columns[0].key and len(attribute.columns) == 1
        for attribute in mapper.column_attrs
    )


def test_certificate_blocked_excludes_auto_issue_off_courses(result_database):
    _, app, tenant, person, course, *_ = result_database
    with scoped(app, tenant) as db:
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=course,
                status="completed",
                pct=1,
                completed_at=NOW - timedelta(days=2),
            )
        )
        db.flush()
        assert (
            queue._rule_certificate_blocked(
                db, tenant_id=tenant, person_id=person, now=NOW, grace_hours=6
            )
            is not None
        )
        row = db.scalar(select(Course).where(Course.id == course))
        row.certificate_auto_issue = False
        db.flush()
        assert (
            queue._rule_certificate_blocked(
                db, tenant_id=tenant, person_id=person, now=NOW, grace_hours=6
            )
            is None
        )
