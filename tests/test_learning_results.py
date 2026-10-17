"""Hosted-only real Kernel/learning migration result command qualification."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from dotmac_learning import events, results
from dotmac_learning.models import Activity, Score, Submission
from dotmac_learning.projections import best_scores_for
from dotmac_learning.references import (
    ActivityWrite,
    CourseWrite,
    ReferenceNotFound,
    create_activity,
    create_course,
    get_activity,
    get_course,
    update_activity,
    update_course,
)
from dotmac_learning.result_contracts import (
    AdmissionContention,
    AdmissionDecision,
    AdmissionRefused,
    GradedResult,
    LabResultCommand,
    MCQResultCommand,
    OverrideScoreCommand,
    ResultBadRequest,
    TransactionChanged,
)
from tests.test_learning_catalog import (
    _person,
    _scope,
    _tenant,
    learning_database as learning_database,
)


class Policy:
    def __init__(self, granted=0):
        self.granted = granted
        self.observations = []

    def evaluate(self, observation):
        self.observations.append(observation)
        return AdmissionDecision(
            observation.max_attempts is None
            or observation.used < observation.max_attempts + self.granted
        )


@contextmanager
def scoped(app, tenant):
    with Session(bind=app) as db, db.begin():
        _scope(db.connection(), tenant)
        yield db


@pytest.fixture
def result_database(learning_database):
    admin = create_engine(learning_database[0])
    app = create_engine(learning_database[1])
    tenant, other = uuid4(), uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            _tenant(conn, other)
            person = _person(conn, tenant)
            other_person = _person(conn, other)
            root_only = _person(conn, tenant, subtype=False)
            malformed_org = _person(conn, tenant, party_type="organization")
        course, activity = uuid4(), uuid4()
        with scoped(app, tenant) as db:
            create_course(
                db,
                CourseWrite(
                    id=course,
                    tenant_id=tenant,
                    slug=course.hex,
                    title="Course",
                    discipline="engineering",
                    source_ref="source",
                ),
            )
            create_activity(
                db,
                ActivityWrite(
                    id=activity,
                    tenant_id=tenant,
                    course_id=course,
                    title="Assessment",
                    max_attempts=2,
                    pass_threshold=0.7,
                ),
            )
        yield (
            admin,
            app,
            tenant,
            person,
            course,
            activity,
            other,
            other_person,
            root_only,
            malformed_org,
        )
    finally:
        app.dispose()
        admin.dispose()


def command(fixture, *, grading=True):
    _, _, tenant, person, _, activity, *_ = fixture
    return MCQResultCommand(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        answers={"q": "a"},
        grading=GradedResult(
            score=1,
            max_score=2,
            fraction=0.5,
            passed=False,
            per_item=[{"ext_id": "q", "correct": True}],
        )
        if grading
        else None,
    )


def test_reference_fields_ids_and_plain_uuid_meanings(result_database):
    _, app, tenant, _, course, activity, other, *_ = result_database
    with scoped(app, tenant) as db:
        original = get_course(db, tenant_id=tenant, course_id=course)
        assert original.id == course
        changed = update_course(
            db,
            CourseWrite(
                id=course,
                tenant_id=tenant,
                slug="changed",
                title="Changed",
                discipline="other",
                source_ref="import",
                version=9,
                status="draft",
                listed=True,
            ),
        )
        assert (changed.slug, changed.version, changed.status, changed.listed) == (
            "changed",
            9,
            "draft",
            True,
        )
        plain_course, bank = uuid4(), uuid4()
        updated = update_activity(
            db,
            ActivityWrite(
                id=activity,
                tenant_id=tenant,
                course_id=plain_course,
                title="Lab",
                type="lab",
                bank_id=bank,
                chapter_number=12,
                pass_threshold=0.8123,
                max_attempts=8,
                grading="manual",
                assessment_mode="exam",
                weight=2.5,
                question_count=17,
            ),
        )
        assert updated.course_id == plain_course and updated.bank_id == bank
        assert (updated.chapter_number, updated.weight, updated.question_count) == (
            12,
            2.5,
            17,
        )
    with scoped(app, other) as db:
        assert get_course(db, tenant_id=tenant, course_id=course) is None
        assert get_activity(db, tenant_id=tenant, activity_id=activity) is None
        with pytest.raises(ReferenceNotFound):
            update_course(
                db,
                CourseWrite(
                    id=course,
                    tenant_id=other,
                    slug="x",
                    title="x",
                    discipline="x",
                    source_ref="x",
                ),
            )


@pytest.mark.parametrize(
    "identity", ["other", "missing_profile", "organization", "absent"]
)
def test_tenant_and_actual_person_admission_refusal(result_database, identity):
    _, app, tenant, _, _, _, _, other_person, root_only, malformed_org = result_database
    person = {
        "other": other_person,
        "missing_profile": root_only,
        "organization": malformed_org,
        "absent": uuid4(),
    }[identity]
    with scoped(app, tenant) as db:
        policy = Policy()
        with pytest.raises(AdmissionRefused):
            results.record_mcq_result(
                db,
                command(result_database).model_copy(update={"person_id": person}),
                policy=policy,
            )
        assert not policy.observations
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


def test_count_entitlement_max_allocation_manual_and_override(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    with scoped(app, tenant) as db:
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.grading = "manual"
        db.add(
            Submission(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                attempt_no=10,
                answers={"historical": True},
            )
        )
        db.flush()
        policy = Policy()
        pending = results.record_mcq_result(
            db, command(result_database, grading=False), policy=policy
        )
        assert policy.observations[0].used == 1
        assert pending.submission.attempt_no == 11
        assert pending.score is None and pending.completion is None
        assert pending.events[0].status == "recorded"
        with pytest.raises(AdmissionRefused, match="No attempts"):
            results.record_mcq_result(
                db, command(result_database, grading=False), policy=Policy()
            )
        extra = results.record_mcq_result(
            db, command(result_database, grading=False), policy=Policy(granted=1)
        )
        assert extra.submission.attempt_no == 12
        first = results.override_score(
            db,
            OverrideScoreCommand(
                tenant_id=tenant,
                submission_id=pending.submission.id,
                score_value=4,
                max_score=5,
                reason="review",
            ),
        )
        second = results.override_score(
            db,
            OverrideScoreCommand(
                tenant_id=tenant,
                submission_id=pending.submission.id,
                score_value=0,
                max_score=0,
                reason="",
            ),
        )
        assert first.score.fraction == 0.8 and first.score.passed
        assert second.score.fraction == 0 and not second.score.passed
        assert first.score.override_reason == "review"
        assert db.scalar(select(func.count()).select_from(Score)) == 2
        best = best_scores_for(db, tenant_id=tenant, person_id=person, course_id=course)
        assert best[activity].id == first.score.id


def test_explicit_transaction_and_isolation_required(result_database):
    _, app, tenant, *_ = result_database
    with Session(bind=app) as db:
        with pytest.raises(TransactionChanged, match="already active"):
            results.record_mcq_result(db, command(result_database), policy=Policy())
    strict = app.execution_options(isolation_level="REPEATABLE READ")
    with scoped(strict, tenant) as db:
        with pytest.raises(TransactionChanged, match="READ COMMITTED"):
            results.record_mcq_result(db, command(result_database), policy=Policy())


def test_callback_no_capabilities_lock_retained_and_savepoint_change_refused(
    result_database,
):
    _, app, tenant, person, *_ = result_database

    class Probe:
        def evaluate(self, observation):
            for name in (
                "commit",
                "rollback",
                "connection",
                "get_transaction",
                "execute",
            ):
                with pytest.raises(AttributeError):
                    getattr(observation, name)
            with app.begin() as contender:
                _scope(contender, tenant)
                with pytest.raises(Exception) as caught, contender.begin_nested():
                    contender.execute(
                        text(
                            "SELECT id FROM public.parties WHERE id=:id "
                            "FOR NO KEY UPDATE NOWAIT"
                        ),
                        {"id": person},
                    )
                assert caught.value.orig.sqlstate == "55P03"
            return AdmissionDecision(True)

    with scoped(app, tenant) as db:
        accepted = results.record_mcq_result(
            db, command(result_database), policy=Probe()
        )
        assert accepted.score.fraction == 0.5

    class Escape:
        def evaluate(self, observation):
            db.begin_nested()
            return AdmissionDecision(True)

    with scoped(app, tenant) as db:
        with pytest.raises(TransactionChanged):
            results.record_mcq_result(db, command(result_database), policy=Escape())
        assert db.scalar(select(func.count()).select_from(Submission)) == 1
        db.get_nested_transaction().rollback()


@pytest.mark.parametrize(
    "table,column", [("party_persons", "party_id"), ("parties", "id")]
)
def test_identity_delete_contention_requires_caller_rollback(
    result_database, table, column
):
    admin, app, tenant, person, *_ = result_database
    with admin.connect() as deleting, deleting.begin():
        deleting.execute(
            text(f"DELETE FROM public.{table} WHERE {column}=:id"), {"id": person}
        )
        # Root deletion blocks admission. Bound that wait to keep the test finite.
        with pytest.raises(Exception) as caught:
            with scoped(app, tenant) as db:
                db.execute(text("SET LOCAL lock_timeout='150ms'"))
                results.record_mcq_result(db, command(result_database), policy=Policy())
        if table == "party_persons":
            assert isinstance(caught.value, AdmissionContention)
        else:
            assert caught.value.orig.sqlstate == "55P03"
    with scoped(app, tenant) as db:
        with pytest.raises(AdmissionRefused):
            results.record_mcq_result(db, command(result_database), policy=Policy())


@pytest.mark.parametrize("winner_rollback", [False, True])
def test_two_connections_final_attempt_commit_or_rollback(
    result_database, winner_rollback
):
    _, app, tenant, *_ = result_database
    with scoped(app, tenant) as db:
        row = db.scalar(
            select(Activity).where(Activity.id == command(result_database).activity_id)
        )
        row.max_attempts = 1
    admitted, started, release = Event(), Event(), Event()
    backend_pids = {}

    class HeldPolicy(Policy):
        def evaluate(self, observation):
            decision = super().evaluate(observation)
            admitted.set()
            assert release.wait(20)
            return decision

    class Rollback(Exception):
        pass

    def first():
        try:
            with scoped(app, tenant) as db:
                backend_pids["winner"] = db.scalar(text("SELECT pg_backend_pid()"))
                results.record_mcq_result(
                    db, command(result_database), policy=HeldPolicy()
                )
                if winner_rollback:
                    raise Rollback()
        except Rollback:
            return "rolled_back"
        return "recorded"

    def second():
        assert admitted.wait(10)
        policy = Policy()
        try:
            with scoped(app, tenant) as db:
                backend_pids["contender"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                value = results.record_mcq_result(
                    db, command(result_database), policy=policy
                )
                assert value.submission.attempt_no == 1
        except AdmissionRefused:
            assert policy.observations[0].used == 1
            return "refused"
        assert policy.observations[0].used == 0
        return "recorded"

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(first), pool.submit(second)
        try:
            assert started.wait(10)
            with app.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as observer:
                deadline = monotonic() + 5
                waiting = None
                while monotonic() < deadline:
                    waiting = observer.execute(
                        text(
                            "SELECT wait_event_type, pg_blocking_pids(pid) "
                            "FROM pg_stat_activity WHERE pid=:pid"
                        ),
                        {"pid": backend_pids["contender"]},
                    ).one_or_none()
                    if (
                        waiting is not None
                        and waiting[0] == "Lock"
                        and backend_pids["winner"] in waiting[1]
                    ):
                        break
                    sleep(0.01)  # Poll pacing; observed PostgreSQL state is the proof.
                else:
                    pytest.fail(
                        "Contender never waited on winner: "
                        f"pids={backend_pids}, state={waiting}"
                    )
        finally:
            release.set()
        assert one.result(timeout=15) == (
            "rolled_back" if winner_rollback else "recorded"
        )
        assert two.result(timeout=15) == ("recorded" if winner_rollback else "refused")
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 1
        assert db.scalar(select(func.count()).select_from(Score)) == 1


def test_required_failure_rolls_back_and_optional_sql_failure_leaves_root_usable(
    result_database, monkeypatch
):
    _, app, tenant, *_ = result_database

    class RequiredFailure(Exception):
        pass

    def fail(*args):
        raise RequiredFailure()

    with monkeypatch.context() as patch:
        patch.setattr(results.completion, "recompute_completion", fail)
        with pytest.raises(RequiredFailure), scoped(app, tenant) as db:
            results.record_mcq_result(db, command(result_database), policy=Policy())
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0
        assert db.scalar(select(func.count()).select_from(Score)) == 0

    def optional_sql_failure(db, payload, **kwargs):
        db.execute(text("SELECT 1/0"))

    monkeypatch.setattr(events, "record", optional_sql_failure)
    with scoped(app, tenant) as db:
        value = results.record_mcq_result(db, command(result_database), policy=Policy())
        assert [event.status for event in value.events] == ["failed", "failed"]
        assert db.scalar(text("SELECT 42")) == 42
        assert db.scalar(select(func.count()).select_from(Score)) == 1


def test_lab_separate_boundary_exact_answers_and_required_consequence(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    instance = uuid4()
    with scoped(app, tenant) as db:
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.type = "lab"
        db.flush()
        with pytest.raises(ResultBadRequest, match="lab workspace"):
            results.record_mcq_result(db, command(result_database), policy=Policy())
        value = results.record_lab_result(
            db,
            LabResultCommand(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                instance_id=instance,
                seed={"answer": 124},
                grading=GradedResult(
                    score=2,
                    max_score=2,
                    fraction=1,
                    passed=True,
                    per_item=[{"name": "ping", "passed": True}],
                ),
            ),
        )
        assert value.submission.answers == {
            "seed": {"answer": 124},
            "instance": str(instance),
        }
        assert value.score.per_item == [{"name": "ping", "passed": True}]
        assert value.completion.completion.status == "completed"
        assert value.completion.certificate is not None
        assert len(value.events) == 3


def test_kernel_autobegin_caller_rollback_owns_results_and_sentinel(result_database):
    _, app, tenant, *_ = result_database
    with Session(bind=app) as db:
        _scope(db.connection(), tenant)  # Actual Kernel tenant_scope starts AUTOBEGIN.
        sentinel_id = uuid4()
        create_course(
            db,
            CourseWrite(
                id=sentinel_id,
                tenant_id=tenant,
                slug=sentinel_id.hex,
                title="Sentinel",
                discipline="test",
                source_ref="test",
            ),
        )
        root = db.get_transaction()
        value = results.record_mcq_result(db, command(result_database), policy=Policy())
        assert value.score is not None and db.get_transaction() is root
        assert (
            get_course(db, tenant_id=tenant, course_id=sentinel_id).title == "Sentinel"
        )
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0
        assert db.scalar(select(func.count()).select_from(Score)) == 0
        assert get_course(db, tenant_id=tenant, course_id=sentinel_id) is None


def test_lab_result_succeeds_with_select_only_identity_privileges(result_database):
    admin, app, tenant, person, _, activity, *_ = result_database
    with scoped(app, tenant) as db:
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.type = "lab"
    # Isolated migrated database, no global role or Kernel DDL changes.
    with admin.begin() as conn:
        conn.execute(
            text("REVOKE UPDATE ON public.parties, public.party_persons FROM app_user")
        )
    with scoped(app, tenant) as db:
        assert not db.scalar(
            text("SELECT has_table_privilege(current_user, 'public.parties', 'UPDATE')")
        )
        assert not db.scalar(
            text(
                "SELECT has_table_privilege(current_user, "
                "'public.party_persons', 'UPDATE')"
            )
        )
        result = results.record_lab_result(
            db,
            LabResultCommand(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                instance_id=uuid4(),
                seed={"answer": 55},
                grading=GradedResult(score=1, max_score=1, fraction=1, passed=True),
            ),
        )
        assert result.completion.certificate is not None
        assert result.score.source == "auto"
        assert all(event.status == "recorded" for event in result.events)
    with pytest.raises(Exception) as caught:
        with scoped(app, tenant) as db:
            results.record_mcq_result(db, command(result_database), policy=Policy())
    assert caught.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("kind", ["exception", "refusal", "activity_changed"])
def test_policy_failure_and_changed_activity_never_append(result_database, kind):
    _, app, tenant, *_ = result_database

    class PolicyFailure(Exception):
        pass

    with scoped(app, tenant) as db:

        class BrokenPolicy:
            def evaluate(self, observation):
                if kind == "exception":
                    raise PolicyFailure()
                if kind == "activity_changed":
                    row = db.scalar(
                        select(Activity).where(Activity.id == observation.activity_id)
                    )
                    row.max_attempts = 99
                return AdmissionDecision(kind != "refusal")

        with pytest.raises(PolicyFailure if kind == "exception" else AdmissionRefused):
            results.record_mcq_result(
                db, command(result_database), policy=BrokenPolicy()
            )
        assert db.scalar(select(func.count()).select_from(Submission)) == 0
        assert db.scalar(select(func.count()).select_from(Score)) == 0


def test_certificate_failure_propagates_through_result_and_caller_savepoint(
    result_database, monkeypatch
):
    from dotmac_learning import certificates
    from dotmac_learning.models import Certificate, CourseCompletion, LearningEvent

    _, app, tenant, *_ = result_database

    class CertificateFailure(Exception):
        pass

    def fail(*args):
        raise CertificateFailure()

    monkeypatch.setattr(certificates, "issue_certificate", fail)
    with scoped(app, tenant) as db:
        sentinel = uuid4()
        create_course(
            db,
            CourseWrite(
                id=sentinel,
                tenant_id=tenant,
                slug=sentinel.hex,
                title="Sentinel",
                discipline="test",
                source_ref="test",
            ),
        )
        with pytest.raises(CertificateFailure), db.begin_nested():
            payload = command(result_database).model_copy(
                update={
                    "grading": GradedResult(
                        score=1, max_score=1, fraction=1, passed=True
                    )
                }
            )
            results.record_mcq_result(db, payload, policy=Policy())
        assert get_course(db, tenant_id=tenant, course_id=sentinel) is not None
        for model in (Submission, Score, CourseCompletion, Certificate, LearningEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.scalar(text("SELECT 7")) == 7


def test_mcq_grade_cannot_spoof_override_or_win_equal_fraction_ranking(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    # Even a trusted producer bypassing Pydantic validation cannot choose
    # ledger provenance. Ordinary validated grade facts exclude these fields.
    poisoned_grade = GradedResult(
        score=4,
        max_score=5,
        fraction=0.8,
        passed=True,
        per_item=[{"ext_id": "q", "correct": True}],
    ).model_copy(update={"source": "override", "override_reason": "forged"})
    payload = command(result_database).model_copy(update={"grading": poisoned_grade})
    with scoped(app, tenant) as db:
        first = results.record_mcq_result(db, payload, policy=Policy())
        assert first.score.source == "auto" and first.score.override_reason is None
        reviewed = results.override_score(
            db,
            OverrideScoreCommand(
                tenant_id=tenant,
                submission_id=first.submission.id,
                score_value=4,
                max_score=5,
                reason="review",
            ),
        )
        later = results.record_mcq_result(db, payload, policy=Policy())
        assert later.score.source == "auto" and later.score.override_reason is None
        assert later.score.fraction == 0.8 and later.score.passed
        assert later.score.per_item == [{"ext_id": "q", "correct": True}]
        best = best_scores_for(db, tenant_id=tenant, person_id=person, course_id=course)
        assert best[activity].id == reviewed.score.id
