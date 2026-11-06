"""Hosted PostgreSQL qualification for the lr_0012 completion capabilities.

R3 (named 0.1.0a6): SQL is the sole completion-recompute and certificate
writer. ``mod_learning.recompute_completion_v1`` owns recompute (and the
auto-issue consequence); ``mod_learning.issue_certificate_v1`` owns
certificate issuance. Python ``recompute_completion``/``issue_certificate``
delegate; the Python mirror is retired. Learning grants no EXECUTE;
composition grants it to the roles that call the Python API, simulated
here for ``app_user`` exactly where the delegated paths need it.
"""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from dotmac_learning import (
    CERTIFICATE_CAPABILITY,
    COMPLETION_CAPABILITY,
    certificates,
    completion,
    results,
)
from dotmac_learning.models import Certificate, CourseCompletion
from dotmac_learning.progress_contracts import (
    CertificateCommand,
    CompletionCommand,
)
from dotmac_learning.references import (
    ActivityWrite,
    CourseWrite,
    create_activity,
    create_course,
)
from dotmac_learning.result_contracts import (
    AdmissionDecision,
    GradedResult,
    MCQResultCommand,
    OverrideScoreCommand,
)
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import _person, _scope, _tenant

learning_database = learning_catalog.learning_database

_RECOMPUTE_SIGNATURE = (
    "mod_learning.recompute_completion_v1(pg_catalog.uuid, pg_catalog.uuid, "
    "pg_catalog.uuid, pg_catalog.timestamptz)"
)
_ISSUE_SIGNATURE = (
    "mod_learning.issue_certificate_v1(pg_catalog.uuid, pg_catalog.uuid, "
    "pg_catalog.uuid, pg_catalog.timestamptz)"
)


class Policy:
    def evaluate(self, observation):
        return AdmissionDecision(True)


@contextmanager
def scoped(app, tenant):
    with Session(bind=app) as db, db.begin():
        _scope(db.connection(), tenant)
        yield db


@contextmanager
def scoped_rollback(app, tenant):
    with Session(bind=app) as db:
        trans = db.begin()
        try:
            _scope(db.connection(), tenant)
            yield db
        finally:
            trans.rollback()


def _grant(admin, *signatures):
    with admin.begin() as conn:
        for signature in signatures:
            conn.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO app_user"))


def _revoke(admin, *signatures):
    with admin.begin() as conn:
        for signature in signatures:
            conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {signature} FROM app_user"))


@pytest.fixture
def completion_database(learning_database):
    admin = create_engine(learning_database[0])
    app = create_engine(learning_database[1])
    tenant = uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
        course, activity = uuid4(), uuid4()
        manual_course, manual_activity = uuid4(), uuid4()
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
                    pass_threshold=0.7,
                    type="mcq_test",
                    assessment_mode="graded",
                    grading="auto",
                    weight=1.0,
                    scoring_policy="latest",
                ),
            )
            create_course(
                db,
                CourseWrite(
                    id=manual_course,
                    tenant_id=tenant,
                    slug=manual_course.hex,
                    title="Manual certificate course",
                    discipline="engineering",
                    source_ref="source",
                    status="published",
                    listed=False,
                    certificate_auto_issue=False,
                    certificate_validity_days=30,
                ),
            )
            create_activity(
                db,
                ActivityWrite(
                    id=manual_activity,
                    tenant_id=tenant,
                    course_id=manual_course,
                    title="Manual assessment",
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
            manual_course,
            manual_activity,
        )
    finally:
        app.dispose()
        admin.dispose()


def _mcq(tenant, person, activity, *, passed=True):
    return MCQResultCommand(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        answers={"q1": 2},
        grading=GradedResult(
            score=9 if passed else 1,
            max_score=10,
            fraction=0.9 if passed else 0.1,
            passed=passed,
            per_item=[{"id": "q1", "correct": passed}],
        ),
    )


def test_capability_constants_acl_and_lint(completion_database):
    from tests.test_learning_lab_capability import _assert_raise_lint

    admin, *_ = completion_database
    assert COMPLETION_CAPABILITY.startswith("mod_learning.recompute_completion_v1(")
    assert CERTIFICATE_CAPABILITY.startswith("mod_learning.issue_certificate_v1(")
    # Undo the shared fixture's composition grant to observe the
    # pristine ACL Learning itself installs (owner-only EXECUTE).
    _revoke(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)
    try:
        with admin.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT p.proname, p.prosecdef, p.proconfig, p.prosrc, "
                    "pg_get_userbyid(p.proowner) AS owner "
                    "FROM pg_proc AS p "
                    "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'mod_learning' "
                    "AND p.proname IN "
                    "('recompute_completion_v1', 'issue_certificate_v1')"
                )
            ).all()
            assert {row.proname for row in rows} == {
                "recompute_completion_v1",
                "issue_certificate_v1",
            }
            for row in rows:
                assert row.prosecdef is False
                assert row.owner == "app_admin"
                assert row.proconfig is not None and len(row.proconfig) == 1
                setting = row.proconfig[0].removeprefix("search_path=")
                assert setting.replace(" ", "") == "pg_catalog,pg_temp"
                _assert_raise_lint(row.prosrc)
            acl = conn.execute(
                text(
                    "SELECT p.proname, grantor.rolname, grantee.rolname, "
                    "acl.privilege_type, acl.is_grantable "
                    "FROM pg_proc AS p "
                    "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                    "CROSS JOIN aclexplode(p.proacl) AS acl "
                    "JOIN pg_roles AS grantor ON grantor.oid = acl.grantor "
                    "JOIN pg_roles AS grantee ON grantee.oid = acl.grantee "
                    "WHERE n.nspname = 'mod_learning' "
                    "AND p.proname IN "
                    "('recompute_completion_v1', 'issue_certificate_v1')"
                )
            ).all()
            assert {(r[0], r[1], r[2], r[3], r[4]) for r in acl} == {
                ("recompute_completion_v1", "app_admin", "app_admin", "EXECUTE", False),
                ("issue_certificate_v1", "app_admin", "app_admin", "EXECUTE", False),
            }
            for signature in (_RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE):
                privilege = text(
                    f"SELECT has_function_privilege(:role, '{signature}', 'EXECUTE')"
                )
                for role in ("public", "app_user", "platform_api"):
                    assert conn.scalar(privilege, {"role": role}) is False
    finally:
        _grant(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)


def test_mcq_completion_and_certificate_through_sql_writer(completion_database):
    admin, app, tenant, person, course, activity, *_ = completion_database
    _grant(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)
    try:
        with scoped(app, tenant) as db:
            value = results.record_mcq_result(
                db, _mcq(tenant, person, activity), policy=Policy()
            )
            assert value.completion is not None
            assert value.completion.first_completion is True
            assert value.completion.completion.status == "completed"
            assert value.completion.certificate is not None
            assert value.completion.certificate.created is True
            assert value.completion.certificate.event is not None
            assert value.completion.certificate.event.status == "recorded"
            cert = db.scalar(select(Certificate).where(Certificate.course_id == course))
            assert cert is not None
            assert cert.serial.startswith("CERT-")
    finally:
        _revoke(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)


def test_python_recompute_requires_the_sql_capability(completion_database):
    """Retirement trap: with EXECUTE withheld the Python API cannot write."""
    admin, app, tenant, person, course, *_ = completion_database
    _revoke(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)
    try:
        with scoped_rollback(app, tenant) as db:
            with pytest.raises(DBAPIError) as excinfo:
                completion.recompute_completion(
                    db,
                    CompletionCommand(
                        tenant_id=tenant, person_id=person, course_id=course
                    ),
                )
        assert getattr(excinfo.value.orig, "sqlstate", None) == "42501"
    finally:
        _grant(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)


def test_manual_issue_after_auto_issue_off(completion_database):
    admin, app, tenant, person, _, _, manual_course, manual_activity = (
        completion_database
    )
    _grant(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)
    try:
        with scoped(app, tenant) as db:
            value = results.record_mcq_result(
                db,
                _mcq(tenant, person, manual_activity),
                policy=Policy(),
            )
            assert value.completion is not None
            assert value.completion.first_completion is True
            assert value.completion.certificate is None
            issued = certificates.issue_certificate(
                db,
                CertificateCommand(
                    tenant_id=tenant, person_id=person, course_id=manual_course
                ),
            )
            assert issued.created is True
            assert issued.event is not None
            assert issued.event.status == "recorded"
            assert issued.certificate.expires_at is not None
            again = certificates.issue_certificate(
                db,
                CertificateCommand(
                    tenant_id=tenant, person_id=person, course_id=manual_course
                ),
            )
            assert again.created is False
            assert again.certificate.id == issued.certificate.id
            assert again.event is None
    finally:
        _revoke(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)


def test_issue_certificate_not_eligible_keeps_transaction_usable(
    completion_database,
):
    admin, app, tenant, person, course, *_ = completion_database
    _grant(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)
    try:
        with scoped_rollback(app, tenant) as db:
            with pytest.raises(certificates.CertificateNotEligible):
                certificates.issue_certificate(
                    db,
                    CertificateCommand(
                        tenant_id=tenant, person_id=person, course_id=course
                    ),
                )
            # The refusal is an outcome, not a SQL abort: the caller's
            # transaction must still answer queries.
            assert db.scalar(text("SELECT 1")) == 1
    finally:
        _revoke(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)


def test_override_regression_and_recompletion_parity(completion_database):
    admin, app, tenant, person, course, activity, *_ = completion_database
    _grant(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)
    try:
        # Separate transactions: 'latest' orders by attempt, then
        # created_at (transaction timestamp), then id -- scores written
        # in one transaction would tie on created_at by construction.
        with scoped(app, tenant) as db:
            value = results.record_mcq_result(
                db, _mcq(tenant, person, activity), policy=Policy()
            )
            assert value.completion is not None
            submission_id = value.submission.id
            certificate_id = value.completion.certificate.certificate.id
        with scoped(app, tenant) as db:
            regressed = results.override_score(
                db,
                OverrideScoreCommand(
                    tenant_id=tenant,
                    submission_id=submission_id,
                    score_value=0,
                    max_score=10,
                    reason="regression probe",
                ),
            )
            assert regressed.completion is not None
            assert regressed.completion.completion.status == "in_progress"
            assert regressed.completion.completion.completed_at is None
            assert regressed.completion.certificate is None
            stored = db.scalar(
                select(CourseCompletion).where(CourseCompletion.course_id == course)
            )
            assert stored.status == "in_progress"
        with scoped(app, tenant) as db:
            restored = results.override_score(
                db,
                OverrideScoreCommand(
                    tenant_id=tenant,
                    submission_id=submission_id,
                    score_value=10,
                    max_score=10,
                    reason="restore probe",
                ),
            )
            assert restored.completion is not None
            assert restored.completion.first_completion is True
            assert restored.completion.completion.status == "completed"
            assert restored.completion.certificate is not None
            assert restored.completion.certificate.created is False
            assert restored.completion.certificate.certificate.id == certificate_id
    finally:
        _revoke(admin, _RECOMPUTE_SIGNATURE, _ISSUE_SIGNATURE)
