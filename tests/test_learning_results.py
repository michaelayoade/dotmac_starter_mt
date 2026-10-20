"""Hosted-only real Kernel/learning migration result command qualification."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from dotmac_kernel.models import Party, PartyPerson
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
    LabResultRead,
    MCQResultCommand,
    OverrideScoreCommand,
    ResultBadRequest,
    TransactionChanged,
)
from pydantic import ValidationError
from sqlalchemy import (
    create_engine,
    event,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import _person, _scope, _tenant

learning_database = learning_catalog.learning_database


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
                    status="published",
                    listed=False,
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
                    type="mcq_test",
                    assessment_mode="graded",
                    grading="auto",
                    weight=1.0,
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
                    status="published",
                    listed=False,
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
        relation = {
            "party_persons": PartyPerson.__table__,
            "parties": Party.__table__,
        }[table]
        deleting.execute(relation.delete().where(relation.c[column] == person))
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
                status="published",
                listed=False,
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
                status="published",
                listed=False,
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


# These canaries run only on hosted CI, against the actual restricted role.
def submit_command(fixture, **changes):
    from dotmac_learning.result_contracts import SubmitMCQCommand

    _, _, tenant, person, _, activity, *_ = fixture
    return SubmitMCQCommand(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        answers={"q": ["yes"]},
        **{"missing_question_weight": 1, **changes},
    )


def seed_submit_questions(db, fixture, *, correct=None, manual=False):
    from dotmac_learning.curriculum_models import Question, QuestionBank

    _, _, tenant, _, course, activity, *_ = fixture
    bank = QuestionBank(tenant_id=tenant, course_id=course, kind="practice", version=1)
    db.add(bank)
    db.flush()
    row = db.scalar(select(Activity).where(Activity.id == activity))
    row.bank_id = bank.id
    row.grading = "manual" if manual else "auto"
    for ext_id in ("q", "second"):
        db.add(
            Question(
                tenant_id=tenant,
                bank_id=bank.id,
                ext_id=ext_id,
                stem="S",
                type="single",
                rubric_category="recall",
                correct=["yes"] if correct is None else correct,
                options=["yes", "no"],
                weight=1,
            )
        )
    db.flush()
    return bank.id


@pytest.mark.parametrize("refusal", ["missing", "exhausted"])
@pytest.mark.parametrize("fault", ["reader", "grader"])
def test_submit_admission_precedes_question_and_grade_faults(
    result_database, monkeypatch, refusal, fault
):
    from dotmac_learning import assessment_reads, grading

    _, app, tenant, person, _, activity, *_ = result_database
    with scoped(app, tenant) as db:
        seed_submit_questions(db, result_database, correct=[{}])
        if refusal == "exhausted":
            row = db.scalar(select(Activity).where(Activity.id == activity))
            row.max_attempts = 0
        payload = submit_command(result_database)
        if refusal == "missing":
            payload = payload.model_copy(update={"person_id": uuid4()})
        called = []

        def fail(*args, **kwargs):
            called.append(fault)
            raise AssertionError("unadmitted reader/grader called")

        monkeypatch.setattr(
            assessment_reads if fault == "reader" else grading,
            "questions_for" if fault == "reader" else "grade_submission",
            fail,
        )
        with pytest.raises(AdmissionRefused):
            results.submit_mcq_activity(db, payload)
        assert called == []
        assert db.scalar(select(func.count()).select_from(Submission)) == 0
        assert db.scalar(select(func.count()).select_from(Score)) == 0


@pytest.mark.parametrize("manual", [False, True])
def test_submit_question_failure_precedes_append(result_database, monkeypatch, manual):
    from dotmac_learning import assessment_reads
    from dotmac_learning.models import CourseCompletion, LearningEvent

    _, app, tenant, *_ = result_database
    with scoped(app, tenant) as db:
        seed_submit_questions(db, result_database, manual=manual)
        boundary = (db.get_transaction(), db.get_nested_transaction())

        def fail(*args, **kwargs):
            raise LookupError("question reader fault")

        monkeypatch.setattr(assessment_reads, "questions_for", fail)
        with pytest.raises(LookupError, match="question reader"):
            results.submit_mcq_activity(db, submit_command(result_database))
        assert (db.get_transaction(), db.get_nested_transaction()) == boundary
        for model in (Submission, Score, CourseCompletion, LearningEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("nested", [False, True])
def test_submit_malformed_json_grade_stages_submission_event_until_caller_rollback(
    result_database, nested
):
    from dotmac_learning.models import CourseCompletion, LearningEvent

    _, app, tenant, *_ = result_database
    with scoped(app, tenant) as db:
        seed_submit_questions(db, result_database, correct=[{}])
    with Session(bind=app) as db:
        _scope(db.connection(), tenant)
        savepoint = db.begin_nested() if nested else None
        boundary = (db.get_transaction(), db.get_nested_transaction())
        with pytest.raises(TypeError):
            results.submit_mcq_activity(db, submit_command(result_database))
        assert (db.get_transaction(), db.get_nested_transaction()) == boundary
        assert db.scalar(select(func.count()).select_from(Submission)) == 1
        assert db.scalar(select(func.count()).select_from(LearningEvent)) == 1
        assert db.scalar(select(LearningEvent.kind)) == "submission_made"
        for model in (Score, CourseCompletion):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        if savepoint is not None:
            savepoint.rollback()
            assert db.get_transaction() is boundary[0]
            assert db.scalar(select(func.count()).select_from(Submission)) == 0
        db.rollback()
    with scoped(app, tenant) as db:
        for model in (Submission, Score, CourseCompletion, LearningEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_submit_manual_reads_questions_but_skips_malformed_grade(
    result_database, monkeypatch
):
    from dotmac_learning import grading

    _, app, tenant, *_ = result_database
    with scoped(app, tenant) as db:
        seed_submit_questions(db, result_database, correct=[{}], manual=True)

        def fail(*args, **kwargs):
            raise AssertionError("manual grader called")

        monkeypatch.setattr(grading, "grade_submission", fail)
        value = results.submit_mcq_activity(db, submit_command(result_database))
        assert value.score is None and value.completion is None
        assert [event.status for event in value.events] == ["recorded"]
        assert value.submission.answers == {"q": ["yes"]}


@pytest.mark.parametrize(
    "selected", [None, [], ["second", "q", "q", "unknown"], ["unknown"]]
)
def test_submit_question_subset_and_scalar_correct_use_source_grader(
    result_database, selected
):
    from dotmac_learning.assessment_reads import questions_for
    from dotmac_learning.grading import grade_submission

    _, app, tenant, *_ = result_database
    with scoped(app, tenant) as db:
        bank = seed_submit_questions(db, result_database, correct="yes")
        payload = submit_command(result_database, only_ext_ids=selected)
        questions = questions_for(
            db, tenant_id=tenant, bank_id=bank, only_ext_ids=selected
        )
        expected = grade_submission(
            payload.answers, questions, 0.7, missing_question_weight=1
        )
        value = results.submit_mcq_activity(db, payload)
        assert value.score.per_item == expected.per_item
        assert value.score.score == expected.score
        assert value.score.max_score == expected.max_score
        assert value.score.passed == expected.passed
        assert [item["id"] for item in value.score.per_item] == [
            q["ext_id"] for q in questions
        ]


def test_submit_absent_bank_skips_reader_and_grades_empty(result_database, monkeypatch):
    from dotmac_learning import assessment_reads

    _, app, tenant, *_ = result_database

    def fail(*args, **kwargs):
        raise AssertionError("absent bank queried")

    monkeypatch.setattr(assessment_reads, "questions_for", fail)
    with scoped(app, tenant) as db:
        value = results.submit_mcq_activity(db, submit_command(result_database))
        assert value.score.score == value.score.max_score == value.score.fraction == 0
        assert not value.score.passed and value.score.per_item == []


def test_submit_type_and_tenant_refusal_precede_learner_lock(
    result_database, monkeypatch
):
    _, app, tenant, _, _, activity, other, *_ = result_database
    with scoped(app, tenant) as db:
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.type = "lab"

    def fail(*args, **kwargs):
        raise AssertionError("invalid activity reached learner lock")

    monkeypatch.setattr(results, "_lock_learner", fail)
    with scoped(app, tenant) as db:
        assert not db.scalar(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")
        )
        with pytest.raises(ResultBadRequest, match="lab workspace"):
            results.submit_mcq_activity(db, submit_command(result_database))
        with pytest.raises(ResultBadRequest, match="activity not found"):
            results.submit_mcq_activity(
                db,
                submit_command(result_database).model_copy(
                    update={"activity_id": uuid4()}
                ),
            )
    with scoped(app, other) as db:
        with pytest.raises(ResultBadRequest, match="activity not found"):
            results.submit_mcq_activity(db, submit_command(result_database))


@pytest.mark.parametrize("winner_rollback", [False, True])
def test_submit_two_connections_final_attempt_commit_or_rollback(
    result_database, winner_rollback, monkeypatch
):
    from dotmac_learning.attempt_policy import BoundAttemptPolicy

    _, app, tenant, *_ = result_database
    with scoped(app, tenant) as db:
        row = db.scalar(
            select(Activity).where(Activity.id == command(result_database).activity_id)
        )
        row.max_attempts = 1
    admitted, started, release = Event(), Event(), Event()
    backend_pids = {}

    original_evaluate = BoundAttemptPolicy.evaluate
    observed = {}

    def held_evaluate(policy, observation):
        decision = original_evaluate(policy, observation)
        pid = policy.db.scalar(text("SELECT pg_backend_pid()"))
        if pid == backend_pids["winner"]:
            admitted.set()
            assert release.wait(20)
        else:
            observed["contender"] = observation
        return decision

    monkeypatch.setattr(BoundAttemptPolicy, "evaluate", held_evaluate)

    class Rollback(Exception):
        pass

    def first():
        try:
            with scoped(app, tenant) as db:
                backend_pids["winner"] = db.scalar(text("SELECT pg_backend_pid()"))
                results.submit_mcq_activity(db, submit_command(result_database))
                if winner_rollback:
                    raise Rollback()
        except Rollback:
            return "rolled_back"
        return "recorded"

    def second():
        assert admitted.wait(10)
        try:
            with scoped(app, tenant) as db:
                backend_pids["contender"] = db.scalar(text("SELECT pg_backend_pid()"))
                started.set()
                value = results.submit_mcq_activity(db, submit_command(result_database))
                assert value.submission.attempt_no == 1
        except AdmissionRefused:
            assert observed["contender"].used == 1
            return "refused"
        assert observed["contender"].used == 0
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


def test_submit_same_session_uncommitted_grant_count_and_max_gap(result_database):
    from dotmac_learning.attempt_models import AttemptGrant

    _, app, tenant, person, _, activity, *_ = result_database
    with Session(bind=app) as db:
        _scope(db.connection(), tenant)
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.max_attempts = 1
        db.add(
            Submission(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                attempt_no=19,
                answers={},
            )
        )
        db.flush()
        with pytest.raises(AdmissionRefused, match="No attempts"):
            results.submit_mcq_activity(db, submit_command(result_database))
        # Seed a pending source grant. Admission's exact Session must autoflush/read it.
        db.add(
            AttemptGrant(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                reason="retake",
                extra_attempts=1,
                granted_by=person,
            )
        )
        value = results.submit_mcq_activity(db, submit_command(result_database))
        assert value.submission.attempt_no == 20
        with pytest.raises(AdmissionRefused, match="No attempts"):
            results.submit_mcq_activity(db, submit_command(result_database))
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0
        assert db.scalar(select(func.count()).select_from(AttemptGrant)) == 0


def test_submit_changed_policy_observation_refuses_before_questions(
    result_database, monkeypatch
):
    from dotmac_learning import assessment_reads
    from dotmac_learning.attempt_policy import BoundAttemptPolicy

    _, app, tenant, *_ = result_database
    original = BoundAttemptPolicy.evaluate

    def changed(policy, observation):
        decision = original(policy, observation)
        row = policy.db.scalar(
            select(Activity).where(Activity.id == observation.activity_id)
        )
        row.max_attempts = 99
        return decision

    def fail(*args, **kwargs):
        raise AssertionError("changed observation reached questions")

    monkeypatch.setattr(BoundAttemptPolicy, "evaluate", changed)
    monkeypatch.setattr(assessment_reads, "questions_for", fail)
    with scoped(app, tenant) as db:
        seed_submit_questions(db, result_database)
        with pytest.raises(AdmissionRefused, match="observations changed"):
            results.submit_mcq_activity(db, submit_command(result_database))
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


def test_submit_uses_admitted_terms_after_question_read(result_database, monkeypatch):
    from dotmac_learning import assessment_reads

    _, app, tenant, _, course, activity, *_ = result_database
    original = assessment_reads.questions_for

    def edit_after_admission(db, **kwargs):
        questions = original(db, **kwargs)
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.grading = "manual"
        row.pass_threshold = 1.0
        return questions

    monkeypatch.setattr(assessment_reads, "questions_for", edit_after_admission)
    with scoped(app, tenant) as db:
        seed_submit_questions(db, result_database)
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.pass_threshold = 0.4
        value = results.submit_mcq_activity(db, submit_command(result_database))
        assert value.score.fraction == 0.5 and value.score.passed
        assert value.completion.completion.course_id == course


def test_submit_certificate_failure_propagates_caller_savepoint(
    result_database, monkeypatch
):
    from dotmac_learning import certificates
    from dotmac_learning.models import Certificate, CourseCompletion, LearningEvent

    _, app, tenant, *_ = result_database

    class CertificateFailure(Exception):
        pass

    def fail(*args, **kwargs):
        raise CertificateFailure()

    monkeypatch.setattr(certificates, "issue_certificate", fail)
    with scoped(app, tenant) as db:
        seed_submit_questions(db, result_database)
        root = db.get_transaction()
        with pytest.raises(CertificateFailure), db.begin_nested():
            payload = submit_command(result_database).model_copy(
                update={"answers": {"q": ["yes"], "second": ["yes"]}}
            )
            results.submit_mcq_activity(db, payload)
        assert db.get_transaction() is root
        for model in (Submission, Score, CourseCompletion, Certificate, LearningEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.scalar(text("SELECT 7")) == 7


def test_submit_optional_event_sql_fault_leaves_required_result_and_root_usable(
    result_database, monkeypatch
):
    _, app, tenant, *_ = result_database

    def fail(db, payload, **kwargs):
        db.execute(text("SELECT 1/0"))

    monkeypatch.setattr(events, "record", fail)
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        value = results.submit_mcq_activity(db, submit_command(result_database))
        assert [event.status for event in value.events] == ["failed", "failed"]
        assert db.get_transaction() is root
        assert db.scalar(text("SELECT 42")) == 42
        assert db.scalar(select(func.count()).select_from(Submission)) == 1
        assert db.scalar(select(func.count()).select_from(Score)) == 1


def test_submit_real_question_sql_failure_requires_caller_savepoint_rollback(
    result_database, monkeypatch
):
    from dotmac_learning import assessment_reads
    from dotmac_learning.models import CourseCompletion, LearningEvent
    from sqlalchemy.exc import DBAPIError

    _, app, tenant, *_ = result_database

    def fail(db, **kwargs):
        db.execute(text("SELECT 1/0"))

    monkeypatch.setattr(assessment_reads, "questions_for", fail)
    with scoped(app, tenant) as db:
        seed_submit_questions(db, result_database)
        root = db.get_transaction()
        savepoint = db.begin_nested()
        with pytest.raises(DBAPIError) as caught:
            results.submit_mcq_activity(db, submit_command(result_database))
        assert caught.value.orig.sqlstate == "22012"
        assert db.get_transaction() is root
        assert db.get_nested_transaction() is savepoint
        savepoint.rollback()
        for model in (Submission, Score, CourseCompletion, LearningEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.scalar(text("SELECT 7")) == 7


def test_submit_requires_active_caller_transaction_and_read_committed(result_database):
    _, app, tenant, *_ = result_database
    with Session(bind=app) as db:
        with pytest.raises(TransactionChanged, match="already active"):
            results.submit_mcq_activity(db, submit_command(result_database))
    strict = app.execution_options(isolation_level="REPEATABLE READ")
    with scoped(strict, tenant) as db:
        with pytest.raises(TransactionChanged, match="READ COMMITTED"):
            results.submit_mcq_activity(db, submit_command(result_database))


# Read canaries are source-authored first; execution belongs to hosted Postgres CI.
def _lab_read_record(fixture, *, instance=None, fraction=0.5):
    _, app, tenant, person, _, activity, *_ = fixture
    instance = instance or uuid4()
    with scoped(app, tenant) as db:
        row = db.scalar(select(Activity).where(Activity.id == activity))
        row.type = "lab"
        db.flush()
        value = results.record_lab_result(
            db,
            LabResultCommand(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                instance_id=instance,
                seed={"nested": {"value": 12}},
                grading=GradedResult(
                    score=fraction,
                    max_score=1,
                    fraction=fraction,
                    passed=False,
                    per_item=[{"nested": {"passed": False}}],
                ),
            ),
        )
    return (
        LabResultRead(
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            instance_id=instance,
            claimed_at=value.submission.created_at,
            finished_at=value.submission.created_at,
        ),
        value,
    )


def test_lab_read_cross_tenant_isolation_first(result_database):
    admin, app, tenant, _, _, _, other, *_ = result_database
    query, value = _lab_read_record(result_database)
    with scoped(app, other) as db:
        assert results.read_lab_result(db, query) is None
        assert (
            results.read_lab_result(db, query.model_copy(update={"tenant_id": other}))
            is None
        )
    # Privileged access cannot turn a foreign query tenant into provenance.
    with Session(bind=admin) as db:
        assert (
            results.read_lab_result(db, query.model_copy(update={"tenant_id": other}))
            is None
        )
    with scoped(app, tenant) as db:
        assert results.read_lab_result(db, query) == value.score


@pytest.mark.parametrize("field", ["person_id", "activity_id", "instance_id"])
def test_lab_read_exact_identity_no_activity_best_fallback(result_database, field):
    _, app, tenant, *_ = result_database
    query, _ = _lab_read_record(result_database, fraction=0.9)
    unrelated, _ = _lab_read_record(result_database, fraction=0.2)
    with scoped(app, tenant) as db:
        assert (
            results.read_lab_result(db, query.model_copy(update={field: uuid4()}))
            is None
        )
        assert results.read_lab_result(db, unrelated).fraction == 0.2
        assert results.read_lab_result(db, query).fraction == 0.9


@pytest.mark.parametrize(
    "bounds",
    ["missing_claim", "missing_finish", "both_missing", "inverted", "before", "after"],
)
def test_lab_read_missing_inverted_and_other_windows_fail_closed(
    result_database, bounds
):
    _, app, tenant, *_ = result_database
    query, _ = _lab_read_record(result_database)
    at = query.claimed_at
    delta = timedelta(microseconds=1)
    updates = {
        "missing_claim": {"claimed_at": None},
        "missing_finish": {"finished_at": None},
        "both_missing": {"claimed_at": None, "finished_at": None},
        "inverted": {"claimed_at": at + delta, "finished_at": at},
        "before": {"claimed_at": at - delta, "finished_at": at - delta},
        "after": {"claimed_at": at + delta, "finished_at": at + delta},
    }[bounds]
    with scoped(app, tenant) as db:
        assert results.read_lab_result(db, query.model_copy(update=updates)) is None
        assert results.read_lab_result(db, query) is not None


def test_lab_read_inclusive_edges_and_separate_operation_windows(result_database):
    admin, app, tenant, *_ = result_database
    query, first = _lab_read_record(result_database)
    second_query, second = _lab_read_record(
        result_database, instance=query.instance_id, fraction=0.2
    )
    later = query.claimed_at + timedelta(seconds=10)
    with admin.begin() as conn:
        conn.execute(
            Submission.__table__.update()
            .where(Submission.id == second.submission.id)
            .values(created_at=later)
        )
    with scoped(app, tenant) as db:
        for updates in (
            {},
            {"claimed_at": query.claimed_at - timedelta(seconds=1)},
            {"finished_at": query.finished_at + timedelta(seconds=1)},
        ):
            assert (
                results.read_lab_result(db, query.model_copy(update=updates)).id
                == first.score.id
            )
        assert (
            results.read_lab_result(
                db,
                second_query.model_copy(
                    update={"claimed_at": later, "finished_at": later}
                ),
            ).id
            == second.score.id
        )


def test_lab_read_latest_score_then_id_retains_accepted_ranking(result_database):
    admin, app, tenant, *_ = result_database
    query, value = _lab_read_record(result_database, fraction=0.9)
    with scoped(app, tenant) as db:
        override = results.override_score(
            db,
            OverrideScoreCommand(
                tenant_id=tenant,
                submission_id=value.submission.id,
                score_value=0.2,
                max_score=1,
                reason="later lower score",
            ),
        )
    later = value.score.created_at + timedelta(seconds=1)
    with admin.begin() as conn:
        conn.execute(
            Score.__table__.update()
            .where(Score.id == override.score.id)
            .values(created_at=later)
        )
        # Mutable current activity type is not historical result provenance.
        conn.execute(
            Activity.__table__.update()
            .where(Activity.id == query.activity_id)
            .values(type="mcq_test")
        )
    with scoped(app, tenant) as db:
        assert results.read_lab_result(db, query).id == override.score.id
    with admin.begin() as conn:
        conn.execute(
            Score.__table__.update()
            .where(Score.id == value.score.id)
            .values(created_at=later)
        )
    with scoped(app, tenant) as db:
        assert results.read_lab_result(db, query).id == max(
            value.score.id, override.score.id
        )


@pytest.mark.parametrize("side", ["score", "submission"])
def test_lab_read_real_composite_fk_refuses_mismatched_tenant_pair(
    result_database, side
):
    admin, app, tenant, _, _, _, other, *_ = result_database
    query, value = _lab_read_record(result_database)
    # The genuine composite FK forbids the impossible cross-tenant pair.
    # Do not disable constraints, weaken RLS or fabricate rows to test the join.
    with admin.connect() as conn, conn.begin():
        with pytest.raises(IntegrityError) as caught, conn.begin_nested():
            if side == "score":
                conn.execute(
                    Score.__table__.update()
                    .where(Score.id == value.score.id)
                    .values(tenant_id=other)
                )
            else:
                conn.execute(
                    Submission.__table__.update()
                    .where(Submission.id == value.submission.id)
                    .values(tenant_id=other)
                )
        assert caught.value.orig.sqlstate == "23503"
    with scoped(app, tenant) as db:
        assert results.read_lab_result(db, query).id == value.score.id


def test_lab_read_deep_detached_public_fact_and_frozen_query(result_database):
    import dotmac_learning

    _, app, tenant, *_ = result_database
    query, value = _lab_read_record(result_database)
    assert dotmac_learning.LabResultRead is LabResultRead
    assert dotmac_learning.read_lab_result is results.read_lab_result
    with pytest.raises(ValidationError, match="frozen"):
        query.instance_id = uuid4()
    with scoped(app, tenant) as db:
        fact = dotmac_learning.read_lab_result(db, query)
        assert fact == value.score
        assert not hasattr(fact, "answers")
        with pytest.raises(FrozenInstanceError):
            fact.score = 999
        fact.per_item[0]["nested"]["passed"] = True
        assert results.read_lab_result(db, query).per_item == value.score.per_item
    assert fact.id == value.score.id


@pytest.mark.parametrize("nested", [False, True])
def test_lab_read_no_sql_writes_or_flush_preserves_caller_pending_history(
    result_database, nested
):
    admin, app, tenant, person, _, activity, *_ = result_database
    query, value = _lab_read_record(result_database)
    with scoped(app, tenant) as db:
        sub = db.get(Submission, value.submission.id)
        score = db.get(Score, value.score.id)
        savepoint = db.begin_nested() if nested else None
        root = db.get_transaction()
        # Fresh Core values must win over stale identity-map values, while
        # same-value, null and genuinely dirty caller state stays untouched.
        with admin.begin() as conn:
            conn.execute(
                Score.__table__.update()
                .where(Score.id == score.id)
                .values(score=0.25, per_item=[{"fresh": {"nested": [1]}}])
            )
        score.score = score.score
        score.override_reason = None
        score.per_item = [{"pending": True}]
        sub.answers = {"instance": str(uuid4())}
        sub.activity_id = uuid4()
        pending = Submission(
            tenant_id=tenant,
            person_id=person,
            activity_id=activity,
            answers={"pending": True},
            attempt_no=100,
        )
        db.add(pending)
        before_dirty, before_new = set(db.dirty), set(db.new)
        histories = {
            (obj, name): deepcopy(inspect(obj).attrs[name].history)
            for obj, names in (
                (score, ("score", "override_reason", "per_item")),
                (sub, ("answers", "activity_id")),
            )
            for name in names
        }
        statements, effects = [], []

        def sql(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        def effect(*_args):
            effects.append("transaction_or_flush_effect")

        connection = db.connection()
        event.listen(connection, "before_cursor_execute", sql)
        watched = (
            "before_flush",
            "after_commit",
            "after_rollback",
            "after_transaction_create",
        )
        for name in watched:
            event.listen(db, name, effect)
        try:
            fact = results.read_lab_result(db, query)
            assert fact.score == 0.25
            assert fact.per_item == [{"fresh": {"nested": [1]}}]
            assert (
                results.read_lab_result(
                    db, query.model_copy(update={"instance_id": uuid4()})
                )
                is None
            )
            assert db.get_transaction() is root
            assert db.get_nested_transaction() is savepoint
            assert set(db.dirty) == before_dirty and set(db.new) == before_new
            assert all(
                inspect(obj).attrs[name].history == history
                for (obj, name), history in histories.items()
            )
            assert effects == []
            assert len(statements) == 2
            assert all(
                statement.lstrip().upper().startswith("SELECT")
                for statement in statements
            )
            assert db.autoflush is True
        finally:
            event.remove(connection, "before_cursor_execute", sql)
            for name in watched:
                event.remove(db, name, effect)
        db.expunge(pending)
        db.expire_all()
        assert db.scalar(text("SELECT current_setting('app.current_tenant')")) == str(
            tenant
        )


def test_lab_read_flushed_caller_result_does_not_claim_commit(result_database):
    _, app, tenant, person, _, activity, *_ = result_database
    original, _ = _lab_read_record(result_database)
    instance = uuid4()
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        value = results.record_lab_result(
            db,
            LabResultCommand(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                instance_id=instance,
                seed={},
                grading=GradedResult(score=0, max_score=1, fraction=0, passed=False),
            ),
        )
        query = original.model_copy(
            update={
                "instance_id": instance,
                "claimed_at": value.submission.created_at,
                "finished_at": value.submission.created_at,
            }
        )
        assert results.read_lab_result(db, query) == value.score
        assert db.get_transaction() is root
        db.rollback()  # Caller retains the complete rollback decision.
    with scoped(app, tenant) as db:
        assert results.read_lab_result(db, query) is None
        assert results.read_lab_result(db, original) is not None
