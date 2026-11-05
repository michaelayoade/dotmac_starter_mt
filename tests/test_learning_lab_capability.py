"""Hosted PostgreSQL qualification for the lr_0011 lab-result capability.

The SQL function ``mod_learning.record_lab_result_v1`` is the single lab
ledger writer: Python ``record_lab_result`` delegates to it, and a product
definer (owned by the adopting application, not this package) is the only
other intended caller. Learning grants no EXECUTE; composition grants it
to the roles that call the Python API. These tests simulate that grant
for ``app_user`` exactly where the delegated Python path needs it.
"""

import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_learning import LAB_RESULT_CAPABILITY, completion, events, results
from dotmac_learning.event_contracts import EventWrite
from dotmac_learning.models import (
    Activity,
    Certificate,
    CourseCompletion,
    LearningEvent,
    Score,
    Submission,
)
from dotmac_learning.progress_contracts import CompletionCommand
from dotmac_learning.references import (
    ActivityWrite,
    CourseWrite,
    create_activity,
    create_course,
)
from dotmac_learning.result_contracts import (
    AdmissionDecision,
    GradedResult,
    LabResultCommand,
    MCQResultCommand,
    OverrideScoreCommand,
    ResultBadRequest,
    ResultWrite,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import _person, _scope, _tenant

learning_database = learning_catalog.learning_database

_SIGNATURE = (
    "mod_learning.record_lab_result_v1(pg_catalog.uuid, pg_catalog.uuid, "
    "pg_catalog.uuid, pg_catalog.uuid, pg_catalog.jsonb, pg_catalog.float8, "
    "pg_catalog.float8, pg_catalog.float8, pg_catalog.bool, pg_catalog.jsonb)"
)
_CALL = text(
    "SELECT submission_id, attempt_no, score_id, completion_id, "
    "first_completion, certificate_id, certificate_created, course_id, "
    "prior_pass_exists, event_kinds, event_statuses, event_ids "
    "FROM mod_learning.record_lab_result_v1"
    "(CAST(:tenant_id AS pg_catalog.uuid), "
    "CAST(:person_id AS pg_catalog.uuid), "
    "CAST(:activity_id AS pg_catalog.uuid), "
    "CAST(:instance_id AS pg_catalog.uuid), "
    "CAST(:seed AS pg_catalog.jsonb), :score, :max_score, :fraction, "
    ":passed, CAST(:per_item AS pg_catalog.jsonb))"
)


@contextmanager
def scoped(app, tenant):
    with Session(bind=app) as db, db.begin():
        _scope(db.connection(), tenant)
        yield db


@pytest.fixture
def lab_database(learning_database):
    admin = create_engine(learning_database[0])
    app = create_engine(learning_database[1])
    tenant = uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
        course, lab, second = uuid4(), uuid4(), uuid4()
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
            for activity_id, title in ((lab, "Lab"), (second, "Second lab")):
                create_activity(
                    db,
                    ActivityWrite(
                        id=activity_id,
                        tenant_id=tenant,
                        course_id=course,
                        title=title,
                        pass_threshold=0.7,
                        type="lab",
                        assessment_mode="graded",
                        grading="auto",
                        weight=1.0,
                    ),
                )
        yield admin, app, tenant, person, course, lab, second
    finally:
        app.dispose()
        admin.dispose()


def _grant_python_caller(admin) -> None:
    """Composition grant for the role calling the Python API (test-only)."""
    with admin.begin() as conn:
        conn.execute(text(f"GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO app_user"))


def _lab_command(fixture, *, activity=None, passed=True, fraction=1.0):
    _, _, tenant, person, _, lab, _ = fixture
    return LabResultCommand(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity or lab,
        instance_id=uuid4(),
        seed={"answer": 124},
        grading=GradedResult(
            score=2 if passed else 0,
            max_score=2,
            fraction=fraction,
            passed=passed,
            per_item=[{"id": "ping", "pass": passed}],
        ),
    )


_LITERAL = r"'(?:[^']|'')*'"


def _assert_raise_lint(prosrc: str) -> None:
    """L9 lint: constant RAISE messages only, no dynamic SQL (D6/C7)."""
    assert "EXECUTE " not in prosrc.upper()
    assert "format(" not in prosrc
    raises = re.findall(r"\bRAISE\b[^;]*;", prosrc, flags=re.IGNORECASE)
    assert raises, "expected RAISE refusals in the lr_0011 function body"
    for statement in raises:
        # No %-style format placeholders anywhere in a RAISE.
        assert "%" not in statement, statement
        # Between the RAISE level and USING, only nothing or one
        # string literal may stand (optionally SQLSTATE-qualified):
        # a variable format argument such as
        # ``RAISE EXCEPTION v_msg USING ...`` must trip.
        head = re.match(
            r"\s*RAISE\b"
            r"(?:\s+(?:DEBUG|LOG|INFO|NOTICE|WARNING|EXCEPTION))?"
            r"(.*?)\bUSING\b",
            statement,
            flags=re.IGNORECASE | re.DOTALL,
        )
        assert head is not None, statement
        between = head.group(1).strip()
        assert (
            not between
            or re.fullmatch(_LITERAL, between) is not None
            or re.fullmatch(r"SQLSTATE\s+" + _LITERAL, between, flags=re.IGNORECASE)
            is not None
        ), f"non-literal RAISE format argument in: {statement}"
        # Every USING value (ERRCODE/MESSAGE/DETAIL/HINT) must be a
        # plain string literal: no concatenation, no identifiers,
        # no expressions built from caller or row data.
        for field, value in re.findall(
            r"\b(ERRCODE|MESSAGE|DETAIL|HINT)\s*=\s*([^,;]+)",
            statement,
            flags=re.IGNORECASE,
        ):
            assert re.fullmatch(
                _LITERAL, value.strip()
            ), f"non-literal USING {field} in: {statement}"


@pytest.mark.parametrize(
    "prosrc",
    [
        # %-style format placeholder in the RAISE message.
        "RAISE EXCEPTION 'bad %' USING ERRCODE = '22023', MESSAGE = 'x';",
        # Concatenated USING value built from caller/row data.
        "RAISE EXCEPTION USING ERRCODE = '22023', " "MESSAGE = 'x' || v_detail;",
        # Identifier (variable) USING value.
        "RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = v_message;",
        # format() building a message elsewhere in the body.
        "v_msg := format('x %s', v_detail);\n"
        "RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'x';",
        # Dynamic SQL elsewhere in the body.
        "EXECUTE v_sql;\n" "RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'x';",
        # Variable format argument between the level and USING.
        "RAISE EXCEPTION v_msg USING ERRCODE = '22023', MESSAGE = 'x';",
    ],
    ids=[
        "percent_placeholder",
        "concatenated_using_value",
        "identifier_using_value",
        "format_call",
        "execute_dynamic_sql",
        "variable_message",
    ],
)
def test_raise_lint_rejects_dynamic_constructions(prosrc):
    with pytest.raises(AssertionError):
        _assert_raise_lint(prosrc)


def test_capability_constant_acl_and_raise_lint(lab_database):
    admin, app, *_ = lab_database
    assert LAB_RESULT_CAPABILITY.startswith("mod_learning.record_lab_result_v1(")
    with admin.connect() as conn:
        row = conn.execute(
            text(
                "SELECT p.prosecdef, p.proconfig, p.prosrc, "
                "pg_get_userbyid(p.proowner) AS owner "
                "FROM pg_proc AS p "
                "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'mod_learning' "
                "AND p.proname = 'record_lab_result_v1'"
            )
        ).one()
        assert row.prosecdef is False
        assert row.owner == "app_admin"
        # Exact stored text is read back here; pin content and order
        # without depending on PostgreSQL's spacing normalization.
        assert row.proconfig is not None and len(row.proconfig) == 1
        setting = row.proconfig[0].removeprefix("search_path=").replace(" ", "")
        assert setting == "pg_catalog,pg_temp"
        _assert_raise_lint(row.prosrc)
        acl = conn.execute(
            text(
                "SELECT grantor.rolname, grantee.rolname, "
                "acl.privilege_type, acl.is_grantable "
                "FROM pg_proc AS p "
                "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                "CROSS JOIN aclexplode(p.proacl) AS acl "
                "JOIN pg_roles AS grantor ON grantor.oid = acl.grantor "
                "JOIN pg_roles AS grantee ON grantee.oid = acl.grantee "
                "WHERE n.nspname = 'mod_learning' "
                "AND p.proname = 'record_lab_result_v1'"
            )
        ).all()
        # Owner-only EXECUTE: revoked from PUBLIC, granted to nobody.
        assert {(r[0], r[1], r[2], r[3]) for r in acl} == {
            ("app_admin", "app_admin", "EXECUTE", False)
        }
        privilege = text(
            f"SELECT has_function_privilege(:role, '{_SIGNATURE}', 'EXECUTE')"
        )
        for role in ("public", "app_user", "platform_api"):
            assert conn.scalar(privilege, {"role": role}) is False
    with scoped(app, lab_database[2]) as db:
        with pytest.raises(Exception) as caught:
            db.execute(_CALL, _call_params(lab_database))
        assert caught.value.orig.sqlstate == "42501"


def _call_params(fixture, **overrides):
    _, _, tenant, person, _, lab, _ = fixture
    params = {
        "tenant_id": tenant,
        "person_id": person,
        "activity_id": lab,
        "instance_id": uuid4(),
        "seed": '{"answer": 124}',
        "score": 2.0,
        "max_score": 2.0,
        "fraction": 1.0,
        "passed": True,
        "per_item": '[{"id": "ping", "pass": true}]',
    }
    params.update(overrides)
    return params


def test_delegated_python_lab_result_records_full_path(lab_database):
    admin, app, tenant, _person, _course, _lab, second = lab_database
    _grant_python_caller(admin)
    with scoped(app, tenant) as db:
        value = results.record_lab_result(db, _lab_command(lab_database))
        assert value.submission.answers["seed"] == {"answer": 124}
        assert value.submission.attempt_no == 1
        assert value.score is not None and value.score.source == "auto"
        assert [event.status for event in value.events] == ["recorded"] * 3
        assert [event.event.kind for event in value.events] == [
            "submission_made",
            "work_graded",
            "lab_check_passed",
        ]
        # One of two activities passed: not yet complete.
        assert value.completion is not None
        assert value.completion.completion.status == "in_progress"
        assert value.completion.completion.pct == 0.5
        assert value.completion.first_completion is False
        assert value.completion.certificate is None

        done = results.record_lab_result(
            db, _lab_command(lab_database, activity=second)
        )
        assert done.completion.completion.status == "completed"
        assert done.completion.first_completion is True
        assert done.completion.certificate is not None
        assert done.completion.certificate.created is True
        assert done.completion.certificate.event is not None
        assert done.completion.certificate.event.status == "recorded"
        assert done.completion.certificate.event.event.kind == "certificate_earned"
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1
        assert db.scalar(select(func.count()).select_from(LearningEvent)) == 7


def test_sql_prior_pass_and_latest_policy_regression(lab_database):
    admin, app, tenant, _person, _course, lab, second = lab_database
    _grant_python_caller(admin)
    with scoped(app, tenant) as db:
        row = db.scalar(select(Activity).where(Activity.id == lab))
        row.scoring_policy = "latest"
        db.flush()
        results.record_lab_result(db, _lab_command(lab_database))
        results.record_lab_result(db, _lab_command(lab_database, activity=second))
        completed = db.scalar(select(CourseCompletion))
        assert completed.status == "completed" and completed.completed_at is not None
        # A later failing attempt wins under "latest": completion regresses,
        # the issued certificate is retained (Python parity).
        regressed = results.record_lab_result(
            db, _lab_command(lab_database, passed=False, fraction=0.0)
        )
        assert regressed.completion.completion.status == "in_progress"
        assert regressed.completion.completion.completed_at is None
        assert regressed.completion.certificate is None
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1
    with admin.begin() as conn:
        _scope(conn, tenant)
        direct = conn.execute(_CALL, _call_params(lab_database)).one()
        assert direct.prior_pass_exists is True
        assert direct.event_kinds[:3] == [
            "submission_made",
            "work_graded",
            "lab_check_passed",
        ]
    with scoped(app, tenant) as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 4
        assert db.scalar(select(func.count()).select_from(Score)) == 4


def test_python_delegation_expires_stale_completion(lab_database):
    admin, app, tenant, person, course, _lab, second = lab_database
    _grant_python_caller(admin)
    with scoped(app, tenant) as db:
        results.record_lab_result(db, _lab_command(lab_database))
        loaded = db.scalar(
            select(CourseCompletion).where(
                CourseCompletion.tenant_id == tenant,
                CourseCompletion.person_id == person,
                CourseCompletion.course_id == course,
            )
        )
        assert loaded.status == "in_progress"
        results.record_lab_result(db, _lab_command(lab_database, activity=second))
        # The previously loaded instance must not serve stale attributes.
        assert loaded.status == "completed"
        assert loaded.pct == 1.0
        assert loaded.completed_at is not None


@pytest.mark.parametrize(
    "overrides,sqlstate",
    [
        ({"seed": "[]"}, "22023"),
        ({"per_item": "{}"}, "22023"),
        ({"per_item": "[1]"}, "22023"),
        ({"fraction": None}, "22004"),
    ],
)
def test_sql_function_refuses_bad_shapes(lab_database, overrides, sqlstate):
    admin, _, tenant, *_ = lab_database
    with admin.begin() as conn:
        _scope(conn, tenant)
        with pytest.raises(Exception) as caught:
            conn.execute(_CALL, _call_params(lab_database, **overrides))
        assert caught.value.orig.sqlstate == sqlstate


def test_python_non_lab_activity_refused_before_any_write(lab_database):
    admin, app, tenant, _person, course, _lab, _ = lab_database
    _grant_python_caller(admin)
    mcq = uuid4()
    with scoped(app, tenant) as db:
        create_activity(
            db,
            ActivityWrite(
                id=mcq,
                tenant_id=tenant,
                course_id=course,
                title="Quiz",
                pass_threshold=0.7,
                type="mcq_test",
                assessment_mode="graded",
                grading="auto",
                weight=1.0,
            ),
        )
        with pytest.raises(ResultBadRequest, match="lab activity"):
            results.record_lab_result(db, _lab_command(lab_database, activity=mcq))
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


# ---------------------------------------------------------------------------
# L1 parity: the delegated SQL writer vs a test-only reference that
# reconstructs the pre-change Python writer (D4). The reference below is
# written from the pre-delegation semantics — MAX(attempt_no)+1
# submission with {"seed", "instance"} answers, auto score, the three
# lab events through events.emit, then completion.recompute_completion
# — using only surviving public modules. It imports nothing from the
# deleted lab branch of the old writer.
# ---------------------------------------------------------------------------


def _reference_record_lab_result(db, payload: LabResultCommand) -> ResultWrite:
    """Test-only reconstruction of the pre-change Python lab writer."""
    activity = db.scalar(
        select(Activity)
        .where(Activity.tenant_id == payload.tenant_id)
        .where(Activity.id == payload.activity_id)
        .execution_options(populate_existing=True)
    )
    if activity is None:
        raise ResultBadRequest("activity not found for tenant")
    if activity.type != "lab":
        raise ResultBadRequest("Lab results require a lab activity.")
    previous = db.scalar(
        select(func.coalesce(func.max(Submission.attempt_no), 0)).where(
            Submission.tenant_id == payload.tenant_id,
            Submission.activity_id == payload.activity_id,
            Submission.person_id == payload.person_id,
        )
    )
    sub = Submission(
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        activity_id=payload.activity_id,
        answers={"seed": payload.seed, "instance": str(payload.instance_id)},
        attempt_no=int(previous or 0) + 1,
    )
    db.add(sub)
    db.flush()
    emitted = [
        events.emit(
            db,
            EventWrite(
                tenant_id=payload.tenant_id,
                person_id=payload.person_id,
                kind="submission_made",
                course_id=activity.course_id,
                subject_id=sub.id,
                detail={
                    "activity_id": str(activity.id),
                    "attempt_no": sub.attempt_no,
                    "lab": True,
                },
            ),
        )
    ]
    grade = payload.grading
    values = grade.model_dump()
    values.update(source="auto", override_reason=None)
    score = Score(tenant_id=payload.tenant_id, submission_id=sub.id, **values)
    db.add(score)
    db.flush()
    emitted.append(
        events.emit(
            db,
            EventWrite(
                tenant_id=payload.tenant_id,
                person_id=payload.person_id,
                kind="work_graded",
                course_id=activity.course_id,
                subject_id=sub.id,
                detail={
                    "activity_id": str(activity.id),
                    "passed": score.passed,
                    "fraction": score.fraction,
                    "lab": True,
                },
            ),
        )
    )
    emitted.append(
        events.emit(
            db,
            EventWrite(
                tenant_id=payload.tenant_id,
                person_id=payload.person_id,
                kind="lab_check_passed" if score.passed else "lab_check_failed",
                course_id=activity.course_id,
                subject_id=activity.id,
                detail={
                    "instance": str(payload.instance_id),
                    "checks": len(grade.per_item),
                },
            ),
        )
    )
    consequence = completion.recompute_completion(
        db,
        CompletionCommand(
            tenant_id=payload.tenant_id,
            person_id=payload.person_id,
            course_id=activity.course_id,
        ),
    )
    return ResultWrite(
        results._submission_fact(sub),
        results._score_fact(score),
        consequence,
        tuple(emitted),
    )


class _AllowAttempts:
    """Admission policy for the Python MCQ path in L1 parity steps."""

    def evaluate(self, observation):
        return AdmissionDecision(
            observation.max_attempts is None
            or observation.used < observation.max_attempts
        )


def _make_side(
    admin, app, *, policies, course_kwargs, preseed_certificate=False, with_mcq=False
):
    """One fresh tenant/person/course with lab activities, for L1 twins."""
    tenant = uuid4()
    with admin.begin() as conn:
        _tenant(conn, tenant)
        person = _person(conn, tenant)
    course = uuid4()
    activities = [uuid4() for _ in policies]
    if with_mcq:
        # Appended last: mcq steps address it as activities[len(policies)].
        activities.append(uuid4())
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
                **course_kwargs,
            ),
        )
        for activity_id, policy in zip(
            activities[: len(policies)], policies, strict=True
        ):
            create_activity(
                db,
                ActivityWrite(
                    id=activity_id,
                    tenant_id=tenant,
                    course_id=course,
                    title="Lab",
                    pass_threshold=0.7,
                    type="lab",
                    assessment_mode="graded",
                    grading="auto",
                    weight=1.0,
                    scoring_policy=policy,
                ),
            )
        if with_mcq:
            create_activity(
                db,
                ActivityWrite(
                    id=activities[len(policies)],
                    tenant_id=tenant,
                    course_id=course,
                    title="Quiz",
                    pass_threshold=0.7,
                    type="mcq_test",
                    assessment_mode="graded",
                    grading="auto",
                    weight=1.0,
                ),
            )
        if preseed_certificate:
            db.add(
                Certificate(
                    tenant_id=tenant,
                    person_id=person,
                    course_id=course,
                    serial="CERT-PREEXIST001",
                    issued_at=datetime(2026, 1, 1, tzinfo=UTC),
                    expires_at=None,
                )
            )
    return tenant, person, course, activities


def _serial_shape(serial: str) -> str:
    if re.fullmatch(r"CERT-[0-9A-F]{12}", serial):
        return "CERT-<hex12>"
    return serial


class _Normalizer:
    """Map one side's generated ids onto side-independent labels."""

    def __init__(self, side, submissions):
        _tenant_id, _person_id, self.course, activities = side
        self.activity_idx = {a: i for i, a in enumerate(activities)}
        self.submission_label = {
            s.id: (f"submission:{self.activity_idx[s.activity_id]}#{s.attempt_no}")
            for s in submissions
        }

    def subject(self, subject_id):
        if subject_id is None:
            return None
        if subject_id == self.course:
            return "course"
        if subject_id in self.submission_label:
            return self.submission_label[subject_id]
        if subject_id in self.activity_idx:
            return f"activity:{self.activity_idx[subject_id]}"
        return f"other:{subject_id}"

    def detail(self, detail):
        if not isinstance(detail, dict):
            return detail
        out = {}
        for key, value in detail.items():
            if key == "activity_id":
                # Stored as str(activity uuid) by both writers.
                idx = {str(a): i for a, i in self.activity_idx.items()}.get(value)
                out[key] = f"activity:{idx}" if idx is not None else value
            elif key == "serial":
                out[key] = _serial_shape(str(value))
            else:
                out[key] = value
        return out

    def course_ref(self, course_id):
        if course_id is None:
            return None
        if course_id == self.course:
            return "course"
        return f"other:{course_id}"

    def emission(self, emission):
        if emission is None:
            return None
        if emission.event is None:
            return (emission.status, None, None, None, None)
        return (
            emission.status,
            emission.event.kind,
            self.subject(emission.event.subject_id),
            self.course_ref(emission.event.course_id),
            _freeze(self.detail(emission.event.detail)),
        )

    def result_write(self, value: ResultWrite):
        cert = None
        comp = value.completion
        if comp is not None and comp.certificate is not None:
            fact = comp.certificate.certificate
            cert = (
                _serial_shape(fact.serial),
                comp.certificate.created,
                (
                    fact.expires_at - fact.issued_at
                    if fact.expires_at is not None
                    else None
                ),
                self.emission(comp.certificate.event),
            )
        return (
            (
                self.activity_idx[value.submission.activity_id],
                value.submission.attempt_no,
                _freeze(dict(value.submission.answers)),
            ),
            (
                None
                if value.score is None
                else (
                    value.score.score,
                    value.score.max_score,
                    value.score.fraction,
                    value.score.passed,
                    _freeze(value.score.per_item),
                    value.score.source,
                    value.score.override_reason,
                )
            ),
            (
                None
                if comp is None
                else (
                    comp.completion.status,
                    comp.completion.pct,
                    comp.completion.completed_at is not None,
                    comp.first_completion,
                    cert,
                )
            ),
            tuple(self.emission(e) for e in value.events),
        )


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _snapshot(db, side):
    """All ledger rows for one side, minus ids/timestamps/serials."""
    tenant, person, course, _activities = side
    db.flush()
    submissions = list(
        db.scalars(
            select(Submission)
            .where(Submission.tenant_id == tenant, Submission.person_id == person)
            .order_by(Submission.activity_id, Submission.attempt_no)
        ).all()
    )
    norm = _Normalizer(side, submissions)
    sub_by_id = {s.id: s for s in submissions}
    scores = list(db.scalars(select(Score).where(Score.tenant_id == tenant)).all())
    completions = list(
        db.scalars(
            select(CourseCompletion).where(
                CourseCompletion.tenant_id == tenant,
                CourseCompletion.person_id == person,
                CourseCompletion.course_id == course,
            )
        ).all()
    )
    certificates = list(
        db.scalars(
            select(Certificate).where(
                Certificate.tenant_id == tenant,
                Certificate.person_id == person,
                Certificate.course_id == course,
            )
        ).all()
    )
    stored_events = list(
        db.scalars(
            select(LearningEvent).where(
                LearningEvent.tenant_id == tenant,
                LearningEvent.person_id == person,
            )
        ).all()
    )
    return {
        "submissions": sorted(
            (
                norm.activity_idx[s.activity_id],
                s.attempt_no,
                _freeze(dict(s.answers)),
            )
            for s in submissions
        ),
        "scores": sorted(
            (
                norm.activity_idx[sub_by_id[s.submission_id].activity_id],
                sub_by_id[s.submission_id].attempt_no,
                s.score,
                s.max_score,
                s.fraction,
                s.passed,
                _freeze(s.per_item),
                s.source,
                s.override_reason,
            )
            for s in scores
        ),
        "completions": sorted(
            (c.status, c.pct, c.completed_at is not None) for c in completions
        ),
        "certificates": sorted(
            (
                _serial_shape(c.serial),
                (c.expires_at - c.issued_at if c.expires_at is not None else None),
            )
            for c in certificates
        ),
        "events": sorted(
            (
                e.kind,
                norm.subject(e.subject_id),
                norm.course_ref(e.course_id),
                _freeze(norm.detail(dict(e.detail))),
            )
            for e in stored_events
        ),
    }, norm


def _run_scenario(app, side, steps, writer):
    """Replay identical steps on one side; return outcomes + snapshot."""
    tenant, person, _course, activities = side
    outcomes = []
    with scoped(app, tenant) as db:
        for step in steps:
            if step[0] == "lab":
                _, idx, instance, passed, fraction = step
                command = LabResultCommand(
                    tenant_id=tenant,
                    person_id=person,
                    activity_id=activities[idx],
                    instance_id=instance,
                    seed={"answer": 124},
                    grading=GradedResult(
                        score=2.0 * fraction,
                        max_score=2.0,
                        fraction=fraction,
                        passed=passed,
                        per_item=[{"id": "ping", "pass": passed}],
                    ),
                )
                outcomes.append(writer(db, command))
            elif step[0] == "mcq":
                _, idx, passed, fraction = step
                outcomes.append(
                    results.record_mcq_result(
                        db,
                        MCQResultCommand(
                            tenant_id=tenant,
                            person_id=person,
                            activity_id=activities[idx],
                            answers={"q": "a"},
                            grading=GradedResult(
                                score=2.0 * fraction,
                                max_score=2.0,
                                fraction=fraction,
                                passed=passed,
                                per_item=[{"ext_id": "q", "correct": passed}],
                            ),
                        ),
                        policy=_AllowAttempts(),
                    )
                )
            else:
                idx = step[1]
                score_value = step[2] if len(step) > 2 else 2.0
                latest = db.scalar(
                    select(Submission)
                    .where(
                        Submission.tenant_id == tenant,
                        Submission.person_id == person,
                        Submission.activity_id == activities[idx],
                    )
                    .order_by(Submission.attempt_no.desc())
                )
                assert latest is not None
                outcomes.append(
                    results.override_score(
                        db,
                        OverrideScoreCommand(
                            tenant_id=tenant,
                            submission_id=latest.id,
                            score_value=score_value,
                            max_score=2.0,
                            reason="parity override",
                        ),
                    )
                )
        snapshot, norm = _snapshot(db, side)
        return [norm.result_write(o) for o in outcomes], outcomes, snapshot


def _lab_step(idx, passed, fraction):
    return ("lab", idx, uuid4(), passed, fraction)


def _mcq_step(idx, passed, fraction):
    return ("mcq", idx, passed, fraction)


def _assert_parity(
    lab_database, *, policies, course_kwargs, steps, preseed=False, with_mcq=False
):
    admin, app, *_ = lab_database
    _grant_python_caller(admin)
    sql_side = _make_side(
        admin,
        app,
        policies=policies,
        course_kwargs=course_kwargs,
        preseed_certificate=preseed,
        with_mcq=with_mcq,
    )
    ref_side = _make_side(
        admin,
        app,
        policies=policies,
        course_kwargs=course_kwargs,
        preseed_certificate=preseed,
        with_mcq=with_mcq,
    )
    sql_outcomes, sql_raw, sql_snapshot = _run_scenario(
        app, sql_side, steps, results.record_lab_result
    )
    ref_outcomes, ref_raw, ref_snapshot = _run_scenario(
        app, ref_side, steps, _reference_record_lab_result
    )
    # Every emitted tuple (D4) and every written row must agree,
    # except generated ids, clock timestamps and serials.
    assert sql_outcomes == ref_outcomes
    assert sql_snapshot == ref_snapshot
    return sql_raw, ref_raw


def test_l1_parity_policy_competition_override_and_validity(lab_database):
    # first: an early pass survives a later fail. latest: a later fail
    # regresses until a final pass. best: a 1.0 override score beats
    # the 0.8 auto score on the same activity. Validity 30 days must
    # land as expires_at = issued_at + 30d on both sides.
    steps = [
        _lab_step(0, True, 1.0),
        _lab_step(0, False, 0.0),
        _lab_step(1, True, 1.0),
        _lab_step(1, False, 0.0),
        _lab_step(2, False, 0.0),
        _lab_step(2, True, 0.8),
        ("override", 2),
        _lab_step(1, True, 1.0),
    ]
    sql_raw, ref_raw = _assert_parity(
        lab_database,
        policies=("first", "latest", "best"),
        course_kwargs={
            "completion_fraction": 1.0,
            "certificate_auto_issue": True,
            "certificate_validity_days": 30,
        },
        steps=steps,
    )
    for raw in (sql_raw, ref_raw):
        final = raw[-1]
        assert final.completion is not None
        assert final.completion.completion.status == "completed"
        assert final.completion.first_completion is True
        cert = final.completion.certificate
        assert cert is not None and cert.created is True
        assert cert.event is not None and cert.event.status == "recorded"
        assert cert.certificate.expires_at is not None
        assert cert.certificate.expires_at - cert.certificate.issued_at == timedelta(
            days=30
        )


def test_l1_parity_fraction_below_one_and_auto_issue_off(lab_database):
    steps = [_lab_step(0, True, 1.0), _lab_step(1, True, 1.0)]
    sql_raw, ref_raw = _assert_parity(
        lab_database,
        policies=("best", "best", "best"),
        course_kwargs={
            "completion_fraction": 0.5,
            "certificate_auto_issue": False,
            "certificate_validity_days": None,
        },
        steps=steps,
    )
    for raw in (sql_raw, ref_raw):
        final = raw[-1]
        assert final.completion is not None
        # 2 of 3 passed >= 0.5: complete, but no certificate is issued.
        assert final.completion.completion.status == "completed"
        assert final.completion.first_completion is True
        assert final.completion.certificate is None


def test_l1_parity_existing_certificate_is_not_reissued(lab_database):
    steps = [
        _lab_step(0, True, 1.0),
        _lab_step(1, True, 1.0),
        _lab_step(2, True, 1.0),
    ]
    sql_raw, ref_raw = _assert_parity(
        lab_database,
        policies=("best", "latest", "first"),
        course_kwargs={
            "completion_fraction": 1.0,
            "certificate_auto_issue": True,
            "certificate_validity_days": 10,
        },
        steps=steps,
        preseed=True,
    )
    for raw in (sql_raw, ref_raw):
        final = raw[-1]
        assert final.completion is not None
        cert = final.completion.certificate
        assert cert is not None
        assert cert.created is False
        assert cert.event is None
        assert cert.certificate.serial == "CERT-PREEXIST001"
        # The pre-existing row keeps its stored expiry.
        assert cert.certificate.expires_at is None


def test_l1_parity_mcq_then_lab_sql_recompute_counts_mcq(lab_database):
    # The MCQ pass is written through the Python MCQ path on both
    # sides; the closing lab write's recompute (SQL on the delegated
    # side) must count that non-lab score, or the course — needing
    # both activities — would stay in progress there.
    steps = [_mcq_step(1, True, 1.0), _lab_step(0, True, 1.0)]
    sql_raw, ref_raw = _assert_parity(
        lab_database,
        policies=("best",),
        course_kwargs={
            "completion_fraction": 1.0,
            "certificate_auto_issue": True,
            "certificate_validity_days": 30,
        },
        steps=steps,
        with_mcq=True,
    )
    for raw in (sql_raw, ref_raw):
        first, final = raw
        assert first.completion is not None
        assert first.completion.completion.status == "in_progress"
        assert first.completion.completion.pct == 0.5
        assert final.completion is not None
        assert final.completion.completion.status == "completed"
        assert final.completion.completion.pct == 1.0
        assert final.completion.first_completion is True
        cert = final.completion.certificate
        assert cert is not None and cert.created is True


def test_l1_parity_lab_then_mcq_python_recompute_counts_lab(lab_database):
    # Reverse order: the closing MCQ write's Python recompute must
    # count the earlier lab score (SQL-written on the delegated
    # side, reference-written on the other) to complete the course.
    steps = [_lab_step(0, True, 1.0), _mcq_step(1, True, 1.0)]
    sql_raw, ref_raw = _assert_parity(
        lab_database,
        policies=("best",),
        course_kwargs={
            "completion_fraction": 1.0,
            "certificate_auto_issue": True,
            "certificate_validity_days": 30,
        },
        steps=steps,
        with_mcq=True,
    )
    for raw in (sql_raw, ref_raw):
        first, final = raw
        assert first.completion is not None
        assert first.completion.completion.status == "in_progress"
        assert first.completion.completion.pct == 0.5
        assert final.completion is not None
        assert final.completion.completion.status == "completed"
        assert final.completion.completion.pct == 1.0
        assert final.completion.first_completion is True
        cert = final.completion.certificate
        assert cert is not None and cert.created is True


def test_l1_parity_best_same_fraction_override_pass_beats_later_auto_fail(
    lab_database,
):
    # Discriminating best-policy case: the override score and the
    # later auto score share fraction 0.8 but differ in passed
    # (override passes at the 0.7 threshold; the externally graded
    # auto scores do not). Under "best" the earlier override pass
    # wins and the course stays completed; a best-implemented-as-
    # latest SQL recompute would pick the later auto fail and
    # regress it, breaking twin equality on the closing lab write.
    steps = [
        _lab_step(0, False, 0.8),
        ("override", 0, 1.6),
        _lab_step(0, False, 0.8),
    ]
    sql_raw, ref_raw = _assert_parity(
        lab_database,
        policies=("best",),
        course_kwargs={
            "completion_fraction": 1.0,
            "certificate_auto_issue": False,
            "certificate_validity_days": None,
        },
        steps=steps,
    )
    for raw in (sql_raw, ref_raw):
        auto, override, later = raw
        assert auto.score is not None
        assert auto.score.fraction == 0.8
        assert auto.score.passed is False
        assert auto.score.source == "auto"
        assert override.score is not None
        assert override.score.fraction == auto.score.fraction
        assert override.score.passed is True
        assert override.score.source == "override"
        assert later.score is not None
        assert later.score.fraction == override.score.fraction
        assert later.score.passed is False
        assert later.score.source == "auto"
        assert later.completion is not None
        assert later.completion.completion.status == "completed"
        assert later.completion.completion.pct == 1.0
        assert later.completion.first_completion is False


# ---------------------------------------------------------------------------
# L3 optional-event failure/suppression isolation.
# ---------------------------------------------------------------------------


def test_l3_failed_emissions_leave_required_rows(lab_database):
    admin, app, tenant, _person_id, _course, _lab, _second = lab_database
    _grant_python_caller(admin)
    with admin.begin() as conn:
        organization = _person(conn, tenant, subtype=False, party_type="organization")
    command = _lab_command(lab_database).model_copy(update={"person_id": organization})
    with scoped(app, tenant) as db:
        value = results.record_lab_result(db, command)
        # The event identity is not a person: all three optional
        # emissions fail, isolated exactly like events.emit savepoints.
        assert [event.status for event in value.events] == ["failed"] * 3
        assert all(event.event is None for event in value.events)
        # Required rows survive.
        assert value.score is not None
        assert value.completion is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.person_id == organization)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Score)
                .where(Score.submission_id == value.submission.id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(CourseCompletion)
                .where(CourseCompletion.person_id == organization)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningEvent)
                .where(LearningEvent.person_id == organization)
            )
            == 0
        )


def test_l3_suppressed_certificate_event_stays_out_of_events(lab_database):
    admin, app, tenant, person, course, _lab, second = lab_database
    _grant_python_caller(admin)
    with scoped(app, tenant) as db:
        # Pre-seed the once-per-subject certificate_earned row so the
        # issuance-time event conflicts and is suppressed.
        db.add(
            LearningEvent(
                tenant_id=tenant,
                person_id=person,
                kind="certificate_earned",
                course_id=course,
                subject_id=course,
                detail={"serial": "CERT-SEEDED00001"},
                occurred_at=datetime.now(UTC),
            )
        )
        db.flush()
        results.record_lab_result(db, _lab_command(lab_database))
        done = results.record_lab_result(
            db, _lab_command(lab_database, activity=second)
        )
        assert done.completion is not None
        cert = done.completion.certificate
        assert cert is not None and cert.created is True
        assert cert.event is not None
        assert cert.event.status == "suppressed"
        assert cert.event.event is None
        # The suppressed certificate event is not part of the lab
        # ResultWrite emissions, which remain the usual three.
        assert [event.status for event in done.events] == ["recorded"] * 3
        assert [
            event.event.kind for event in done.events if event.event is not None
        ] == ["submission_made", "work_graded", "lab_check_passed"]
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(LearningEvent)
                .where(LearningEvent.kind == "certificate_earned")
            )
            == 1
        )


# ---------------------------------------------------------------------------
# L6 pg_temp trap: same-named temporary domains and tables must never
# be resolved by the lr_0011 function (search_path = pg_catalog,
# pg_temp with every name qualified), directly or through Python.
# ---------------------------------------------------------------------------

_TRAP_DOMAINS = ("uuid", "text", "jsonb", "timestamptz", "varchar", "float8", "int4")
_TRAP_TABLES = (
    "activities",
    "courses",
    "submissions",
    "scores",
    "course_completions",
    "certificates",
    "learning_events",
    "parties",
)


def _plant_pg_temp_traps(execute) -> None:
    for name in _TRAP_DOMAINS:
        execute(
            text(f'CREATE DOMAIN pg_temp."{name}" AS pg_catalog.text CHECK (false)')
        )
    for name in _TRAP_TABLES:
        execute(text(f"CREATE TEMP TABLE {name} (trap pg_catalog.int4)"))
    execute(text("SET LOCAL search_path = pg_temp, public, mod_learning"))


def test_l6_pg_temp_traps_never_fire(lab_database):
    admin, app, tenant, _person_id, _course, _lab, second = lab_database
    # Direct SQL call on an admin connection carrying the traps.
    with admin.begin() as conn:
        _scope(conn, tenant)
        _plant_pg_temp_traps(conn.execute)
        direct = conn.execute(_CALL, _call_params(lab_database)).one()
        assert direct.attempt_no == 1
        assert direct.event_statuses[:3] == ["recorded"] * 3
        for name in _TRAP_TABLES:
            count_sql = f"SELECT count(*) FROM pg_temp.{name}"  # noqa: S608
            assert conn.scalar(text(count_sql)) == 0
    # The same traps on the Python caller Session, through
    # record_lab_result's delegation.
    _grant_python_caller(admin)
    with scoped(app, tenant) as db:
        _plant_pg_temp_traps(db.execute)
        value = results.record_lab_result(
            db, _lab_command(lab_database, activity=second)
        )
        assert value.submission.attempt_no == 1
        assert [event.status for event in value.events] == ["recorded"] * 3
        assert value.completion is not None
        assert value.completion.completion.status == "completed"
        for name in _TRAP_TABLES:
            count_sql = f"SELECT count(*) FROM pg_temp.{name}"  # noqa: S608
            assert db.scalar(text(count_sql)) == 0
        # Real rows landed in mod_learning, not in the temp shadows.
        assert db.scalar(select(func.count()).select_from(Submission)) == 2
        assert db.scalar(select(func.count()).select_from(Score)) == 2
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1
