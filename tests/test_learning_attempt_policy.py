"""Hosted PostgreSQL policy, required Kernel audit, and caller rollback canaries."""

from uuid import uuid4

import pytest
from dotmac_kernel import audit_actions
from dotmac_kernel.audit import AuditEvent, MissingAuditActorError
from dotmac_learning import attempt_policy
from dotmac_learning.attempt_contracts import (
    AttemptAuditContext,
    GrantExtraAttemptCommand,
)
from dotmac_learning.attempt_models import AttemptGrant
from dotmac_learning.manifest import module
from dotmac_learning.models import Activity, Submission
from dotmac_learning.result_contracts import AdmissionRefused, MCQResultCommand
from sqlalchemy import func, select

from tests import test_learning_results as result_tests
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database


@pytest.fixture(autouse=True)
def declared_audit(monkeypatch):
    monkeypatch.setattr(
        audit_actions,
        "_active_registry",
        audit_actions.AuditActionRegistry.from_manifests([module]),
    )


def context(tenant, *, actor_type="system", actor_id=None):
    return AttemptAuditContext(
        tenant_id=tenant,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label="Assessment operator",
        source_application="academy",
    )


def command(tenant, person, activity, **values):
    return GrantExtraAttemptCommand(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        reason="  power cut  ",
        **values,
    )


def test_required_kernel_audit_exact_details_explicit_actor_and_caller_rollback(
    result_database,
):
    _, app, tenant, person, _, activity, *_ = result_database
    with scoped(app, tenant) as db:
        boundary = db.get_transaction()
        with db.begin_nested():
            nested = db.get_nested_transaction()
            grant = attempt_policy.grant_extra_attempt(
                db,
                command(tenant, person, activity, granted_by=person),
                audit_context=context(
                    tenant, actor_type="user", actor_id="principal-7"
                ),
            )
            assert db.get_transaction() is boundary
            assert db.get_nested_transaction() is nested
            audit = db.scalar(
                select(AuditEvent).where(AuditEvent.entity_id == str(activity))
            )
            assert audit.action == "attempt_granted"
            assert audit.entity_type == "activity"
            assert audit.actor_type == "user" and audit.actor_id == "principal-7"
            assert (
                audit.actor_party_id == person
                and audit.actor_label == "Assessment operator"
            )
            assert audit.source_application == "academy"
            assert audit.details == {
                "person_id": str(person),
                "extra_attempts": 1,
                "reason": "power cut",
            }
            assert grant.reason == "power cut"
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(AttemptGrant)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("failure", ["undeclared", "actor"])
def test_required_audit_failure_propagates_after_grant_flush(
    result_database, monkeypatch, failure
):
    _, app, tenant, person, _, activity, *_ = result_database
    with scoped(app, tenant) as db:
        if failure == "undeclared":
            monkeypatch.setattr(
                audit_actions, "_active_registry", audit_actions.AuditActionRegistry(())
            )
            error = audit_actions.UndeclaredAuditActionError
            authority = context(tenant)
        else:
            error = MissingAuditActorError
            authority = context(tenant, actor_type="user")
        with pytest.raises(error):
            attempt_policy.grant_extra_attempt(
                db, command(tenant, person, activity), audit_context=authority
            )
        assert db.scalar(select(func.count()).select_from(AttemptGrant)) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        db.rollback()
    with scoped(app, tenant) as db:
        assert (
            attempt_policy.granted_for(
                db, tenant_id=tenant, person_id=person, activity_id=activity
            )
            == 0
        )


def test_scope_binding_before_sql_and_grouped_grant_reads(result_database):
    _, app, tenant, person, _, activity, other, *_ = result_database
    with scoped(app, tenant) as db:
        with pytest.raises(ValueError, match="scope"):
            attempt_policy.grant_extra_attempt(
                db, command(tenant, person, activity), audit_context=context(other)
            )
        attempt_policy.grant_extra_attempt(
            db,
            command(tenant, person, activity, extra_attempts=2),
            audit_context=context(tenant),
        )
        assert attempt_policy.granted_by_activity(
            db, tenant_id=tenant, person_id=person
        ) == {activity: 2}
        assert (
            attempt_policy.granted_by_activity(db, tenant_id=other, person_id=person)
            == {}
        )
        assert (
            attempt_policy.granted_for(
                db, tenant_id=tenant, person_id=uuid4(), activity_id=activity
            )
            == 0
        )


def test_default_policy_same_session_grant_under_existing_admission_lock(
    result_database, monkeypatch
):
    _, app, tenant, person, _, activity, *_ = result_database
    calls = []
    real_read = attempt_policy.granted_for

    def read(db, **scope):
        calls.append((db, scope))
        return real_read(db, **scope)

    monkeypatch.setattr(attempt_policy, "granted_for", read)
    with scoped(app, tenant) as db:
        db.scalar(select(Activity).where(Activity.id == activity)).grading = "manual"
        payload = MCQResultCommand(
            tenant_id=tenant, person_id=person, activity_id=activity
        )
        for _ in range(2):
            attempt_policy.record_mcq_result_with_attempt_policy(db, payload)
        with pytest.raises(AdmissionRefused, match="No attempts remaining"):
            attempt_policy.record_mcq_result_with_attempt_policy(db, payload)
        attempt_policy.grant_extra_attempt(
            db, command(tenant, person, activity), audit_context=context(tenant)
        )
        with db.begin_nested():
            nested = db.get_nested_transaction()
            result = attempt_policy.record_mcq_result_with_attempt_policy(db, payload)
            assert result.submission.attempt_no == 3
            assert db.get_nested_transaction() is nested
        assert all(
            session is db
            and scope
            == {"tenant_id": tenant, "person_id": person, "activity_id": activity}
            for session, scope in calls
        )
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


@pytest.mark.parametrize(
    "extra,reason,message", [(0, "power", "at least one"), (1, "  ", "why")]
)
def test_source_grant_validation_before_append(result_database, extra, reason, message):
    _, app, tenant, person, _, activity, *_ = result_database
    with scoped(app, tenant) as db:
        payload = command(tenant, person, activity).model_copy(
            update={"reason": reason, "extra_attempts": extra}
        )
        with pytest.raises(ValueError, match=message):
            attempt_policy.grant_extra_attempt(
                db, payload, audit_context=context(tenant)
            )
        assert db.scalar(select(func.count()).select_from(AttemptGrant)) == 0


def test_bound_policy_rejects_observation_scope_before_grant_sql(
    result_database, monkeypatch
):
    from dotmac_learning.result_contracts import AdmissionObservation

    _, app, tenant, person, course, activity, *_ = result_database

    def forbidden(*args, **kwargs):
        raise AssertionError("scope mismatch must not reach a grant query")

    monkeypatch.setattr(attempt_policy, "granted_for", forbidden)
    with scoped(app, tenant) as db:
        policy = attempt_policy.BoundAttemptPolicy(db, tenant, person, activity)
        observation = AdmissionObservation(
            tenant_id=tenant,
            person_id=uuid4(),
            activity_id=activity,
            course_id=course,
            max_attempts=1,
            used=0,
            activity_type="mcq_test",
            grading="manual",
            pass_threshold=0.7,
            assessment_mode="graded",
            bank_id=None,
            question_count=None,
        )
        with pytest.raises(ValueError, match="scope"):
            policy.evaluate(observation)


def test_all_mutators_refuse_missing_root_before_autobegin(result_database):
    from dotmac_learning import attempts
    from dotmac_learning.attempt_contracts import (
        CloseAttemptCommand,
        OpenAttemptCommand,
    )
    from dotmac_learning.result_contracts import TransactionChanged
    from sqlalchemy.orm import Session

    _, app, tenant, person, _, activity, *_ = result_database
    scope = {"tenant_id": tenant, "person_id": person, "activity_id": activity}
    with Session(bind=app) as db:
        for call in (
            lambda: attempt_policy.grant_extra_attempt(
                db, command(tenant, person, activity), audit_context=context(tenant)
            ),
            lambda: attempt_policy.record_mcq_result_with_attempt_policy(
                db, MCQResultCommand(**scope)
            ),
            lambda: attempts.open_or_create_attempt(
                db, OpenAttemptCommand(**scope, all_ext_ids=[], count=0)
            ),
            lambda: attempts.close_open_attempt(db, CloseAttemptCommand(**scope)),
        ):
            with pytest.raises(TransactionChanged):
                call()
            assert not db.in_transaction()


def test_explicit_system_audit_context_does_not_invent_actor_from_granted_by(
    result_database,
):
    _, app, tenant, person, _, activity, *_ = result_database
    with scoped(app, tenant) as db:
        grant = attempt_policy.grant_extra_attempt(
            db,
            command(tenant, person, activity),
            audit_context=context(tenant, actor_type="system", actor_id="retake-job"),
        )
        audit = db.scalar(select(AuditEvent))
        assert audit.actor_type == "system" and audit.actor_id == "retake-job"
        assert audit.actor_party_id is None and grant.granted_by is None
        stored = db.scalar(select(AttemptGrant).where(AttemptGrant.id == grant.id))
        assert {name: getattr(stored, name) for name in grant.__dataclass_fields__} == {
            name: getattr(grant, name) for name in grant.__dataclass_fields__
        }


def test_default_wrapper_propagates_required_completion_failure_for_caller_rollback(
    result_database, monkeypatch
):
    from dotmac_learning import completion
    from dotmac_learning.models import Score
    from dotmac_learning.result_contracts import GradedResult

    _, app, tenant, person, _, activity, *_ = result_database
    failure = RuntimeError("required completion fault")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(completion, "recompute_completion", fail)
    with scoped(app, tenant) as db:
        with pytest.raises(RuntimeError) as caught:
            attempt_policy.record_mcq_result_with_attempt_policy(
                db,
                MCQResultCommand(
                    tenant_id=tenant,
                    person_id=person,
                    activity_id=activity,
                    grading=GradedResult(score=1, max_score=1, fraction=1, passed=True),
                ),
            )
        assert caught.value is failure
        assert db.scalar(select(func.count()).select_from(Submission)) == 1
        assert db.scalar(select(func.count()).select_from(Score)) == 1
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0
        assert db.scalar(select(func.count()).select_from(Score)) == 0
