"""Hosted PostgreSQL roster parity with genuine Kernel person profiles."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from dotmac_kernel.models import Party
from dotmac_learning import roster
from dotmac_learning.participation_contracts import (
    ActivateEnrollmentCommand,
    AssignFacilitatorCommand,
    BulkEnrollCommand,
    FacilitatorEligibility,
    ParticipationBadRequest,
    ParticipationForbidden,
    ParticipationNotFound,
    RosterAuthority,
    RosterStateCommand,
)
from dotmac_learning.participation_models import Enrollment
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import _person, _scope, _tenant
from tests.test_learning_participation_catalog import _cohort

learning_database = learning_catalog.learning_database


@contextmanager
def scoped(app, tenant):
    with Session(bind=app) as db, db.begin():
        _scope(db.connection(), tenant)
        yield db


@pytest.fixture
def roster_database(learning_database):
    admin, app = (create_engine(url) for url in learning_database)
    tenant, other = uuid4(), uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            _tenant(conn, other)
            people = [_person(conn, tenant) for _ in range(3)]
            outsider = _person(conn, other)
            root_only = _person(conn, tenant, subtype=False)
            for person, email in zip(
                people, ("peer@x", "fresh@x", "active@x"), strict=True
            ):
                conn.execute(
                    Party.__table__.update()
                    .where(Party.id == person)
                    .values(email=email)
                )
            conn.execute(
                Party.__table__.update()
                .where(Party.id == root_only)
                .values(email="root@x")
            )
        with app.begin() as conn:
            _scope(conn, tenant)
            cohort = _cohort(conn, tenant)
        yield admin, app, tenant, other, cohort, people, outsider, root_only
    finally:
        app.dispose()
        admin.dispose()


def test_roster_cross_tenant_and_authority_scope_isolation(roster_database):
    _, app, tenant, other, cohort, people, _, _ = roster_database
    with scoped(app, other) as db:
        with pytest.raises(
            ParticipationNotFound, match="^cohort not found for tenant$"
        ):
            roster.bulk_enroll(
                db,
                BulkEnrollCommand(
                    tenant_id=other, cohort_id=cohort, emails=["fresh@x"]
                ),
                authority=RosterAuthority(other, True),
            )
    with scoped(app, tenant) as db:
        with pytest.raises(ParticipationForbidden):
            roster.bulk_enroll(
                db,
                BulkEnrollCommand(
                    tenant_id=tenant, cohort_id=cohort, emails=["fresh@x"]
                ),
                authority=RosterAuthority(other, True),
            )
        assert (
            db.scalar(select(Enrollment).where(Enrollment.person_id == people[1]))
            is None
        )


def test_bulk_order_dedupe_missing_inactive_account_and_admin_continue(roster_database):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=people[0],
                role_in_cohort="instructor",
                status="dropped",
            )
        )
        db.execute(
            Party.__table__.update()
            .where(Party.id == people[1])
            .values(is_active=False)
        )
        result = roster.bulk_enroll(
            db,
            BulkEnrollCommand(
                tenant_id=tenant,
                cohort_id=cohort,
                emails=[
                    " PEER@X ",
                    "fresh@x",
                    "FRESH@X",
                    "root@x",
                    "ghost@x",
                    None,
                    "",
                ],
            ),
            authority=RosterAuthority(tenant, False),
        )
        assert result.admin_required == ("peer@x",)
        assert result.enrolled == ("fresh@x",)
        assert result.not_found == ("root@x", "ghost@x")
        result = roster.bulk_enroll(
            db,
            BulkEnrollCommand(
                tenant_id=tenant, cohort_id=cohort, emails=["peer@x", "fresh@x"]
            ),
            authority=RosterAuthority(tenant, True),
        )
        assert result.reactivated == ("peer@x",)
        assert result.already_active == ("fresh@x",)


def test_activation_active_nonstudent_noop_before_admin_gate_and_flush_free(
    roster_database,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        row = Enrollment(
            tenant_id=tenant,
            cohort_id=cohort,
            person_id=people[0],
            role_in_cohort="instructor",
            status="active",
        )
        db.add(row)
        db.flush()
        result = roster.activate_enrollment(
            db,
            ActivateEnrollmentCommand(tenant_id=tenant, enrollment_id=row.id),
            authority=RosterAuthority(tenant, False),
        )
        assert (result.outcome, result.previous_status) == ("already_active", "active")
        row.status = "dropped"
        with pytest.raises(
            ParticipationBadRequest, match="only an admin can reactivate"
        ):
            roster.activate_enrollment(
                db,
                ActivateEnrollmentCommand(tenant_id=tenant, enrollment_id=row.id),
                authority=RosterAuthority(tenant, False),
            )


def test_state_nonstudent_gate_precedes_invalid_state_then_student_not_found(
    roster_database,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=people[0],
                role_in_cohort="instructor",
                status="active",
            )
        )
        with pytest.raises(ParticipationForbidden, match="Forbidden"):
            roster.set_roster_state(
                db,
                RosterStateCommand(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=people[0],
                    state="bogus",
                ),
                authority=RosterAuthority(tenant, False),
            )
        with pytest.raises(ParticipationNotFound, match="invalid roster state: bogus"):
            roster.set_roster_state(
                db,
                RosterStateCommand(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=people[1],
                    state="bogus",
                ),
                authority=RosterAuthority(tenant, False),
            )
        with pytest.raises(ParticipationNotFound, match="enrollment not found"):
            roster.set_roster_state(
                db,
                RosterStateCommand(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=people[1],
                    state="dropped",
                ),
                authority=RosterAuthority(tenant, False),
            )


def test_facilitator_admin_first_profile_active_and_eligibility_binding(
    roster_database,
):
    _, app, tenant, other, cohort, people, outsider, root_only = roster_database
    with scoped(app, tenant) as db:
        command = AssignFacilitatorCommand(
            tenant_id=tenant, cohort_id=cohort, person_id=root_only
        )
        with pytest.raises(ParticipationBadRequest, match="only an admin can assign"):
            roster.assign_facilitator(
                db,
                command,
                authority=RosterAuthority(tenant, False),
                eligibility=FacilitatorEligibility(
                    tenant, root_only, True, account_is_active=True
                ),
            )
        with pytest.raises(ParticipationNotFound, match="person not found for tenant"):
            roster.assign_facilitator(
                db,
                command,
                authority=RosterAuthority(tenant, True),
                eligibility=FacilitatorEligibility(
                    tenant, root_only, True, account_is_active=True
                ),
            )
        command = AssignFacilitatorCommand(
            tenant_id=tenant, cohort_id=cohort, person_id=people[0]
        )
        with pytest.raises(ParticipationForbidden):
            roster.assign_facilitator(
                db,
                command,
                authority=RosterAuthority(tenant, True),
                eligibility=FacilitatorEligibility(
                    other, outsider, True, account_is_active=True
                ),
            )
        with pytest.raises(ParticipationBadRequest, match="instructor role"):
            roster.assign_facilitator(
                db,
                command,
                authority=RosterAuthority(tenant, True),
                eligibility=FacilitatorEligibility(
                    tenant, people[0], False, account_is_active=True
                ),
            )
        fact = roster.assign_facilitator(
            db,
            command,
            authority=RosterAuthority(tenant, True),
            eligibility=FacilitatorEligibility(
                tenant, people[0], True, account_is_active=True
            ),
        )
        assert (fact.role_in_cohort, fact.status, fact.track_id) == (
            "instructor",
            "active",
            None,
        )


def test_roster_requires_root_before_autoflush_and_preserves_nested_caller_rollback(
    roster_database,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    command = BulkEnrollCommand(tenant_id=tenant, cohort_id=cohort, emails=["fresh@x"])
    with Session(bind=app) as db:
        with pytest.raises(TransactionChanged):
            roster.bulk_enroll(db, command, authority=RosterAuthority(tenant, True))
        assert db.get_transaction() is None
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        with db.begin_nested() as nested:
            roster.bulk_enroll(db, command, authority=RosterAuthority(tenant, True))
            assert (
                db.get_transaction() is root and db.get_nested_transaction() is nested
            )
            nested.rollback()
        assert (
            db.scalar(select(Enrollment).where(Enrollment.person_id == people[1]))
            is None
        )
        db.add(Enrollment(tenant_id=tenant, cohort_id=uuid4(), person_id=people[0]))
        with pytest.raises(IntegrityError):
            roster.bulk_enroll(db, command, authority=RosterAuthority(tenant, True))
        assert not root.is_active
        db.rollback()


def test_bulk_already_active_nonstudent_can_reassign_track_and_facilitator_clears_it(
    roster_database,
):
    from tests.test_learning_participation_catalog import _row, _track

    _, app, tenant, _, cohort, people, _, _ = roster_database
    with app.begin() as conn:
        _scope(conn, tenant)
        track = _track(conn, tenant)
        _row(conn, "cohort_tracks", tenant, cohort_id=cohort, track_id=track)
    with scoped(app, tenant) as db:
        row = Enrollment(
            tenant_id=tenant,
            cohort_id=cohort,
            person_id=people[0],
            role_in_cohort="instructor",
            status="active",
        )
        db.add(row)
        result = roster.bulk_enroll(
            db,
            BulkEnrollCommand(
                tenant_id=tenant,
                cohort_id=cohort,
                emails=["peer@x"],
                track_id=track,
            ),
            authority=RosterAuthority(tenant, False),
        )
        assert result.already_active == ("peer@x",)
        assert row.track_id == track
        row.status = "waitlisted"
        fact = roster.assign_facilitator(
            db,
            AssignFacilitatorCommand(
                tenant_id=tenant, cohort_id=cohort, person_id=people[0]
            ),
            authority=RosterAuthority(tenant, True),
            eligibility=FacilitatorEligibility(
                tenant, people[0], True, account_is_active=True
            ),
        )
        assert fact.id == row.id
        assert (fact.status, fact.role_in_cohort, fact.track_id) == (
            "active",
            "instructor",
            None,
        )
        db.execute(
            Party.__table__.update().where(Party.id == people[1]).values(is_active=True)
        )
        with pytest.raises(ParticipationBadRequest, match="active user"):
            roster.assign_facilitator(
                db,
                AssignFacilitatorCommand(
                    tenant_id=tenant, cohort_id=cohort, person_id=people[1]
                ),
                authority=RosterAuthority(tenant, True),
                eligibility=FacilitatorEligibility(
                    tenant, people[1], True, account_is_active=False
                ),
            )


def test_activation_loaded_helper_does_not_flush_and_id_wrapper_autoflushes(
    roster_database,
):
    from sqlalchemy import event

    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        row = Enrollment(
            tenant_id=tenant, cohort_id=cohort, person_id=people[0], status="dropped"
        )
        db.add(row)
        db.flush()
        calls = []

        def flushed(session, context, instances):
            calls.append(session)

        event.listen(db, "before_flush", flushed)
        try:
            result = roster._activate_enrollment(
                row, authority=RosterAuthority(tenant, False)
            )
            assert result.previous_status == "dropped"
            assert calls == [] and row in db.dirty
            roster.activate_enrollment(
                db,
                ActivateEnrollmentCommand(tenant_id=tenant, enrollment_id=row.id),
                authority=RosterAuthority(tenant, False),
            )
            assert calls == [db]
        finally:
            event.remove(db, "before_flush", flushed)


@pytest.mark.parametrize(
    "identity_active,account_active", [(True, False), (False, True)]
)
def test_facilitator_account_availability_independent_of_identity(
    roster_database,
    identity_active,
    account_active,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        db.get(Party, people[0]).is_active = identity_active
        db.flush()
        command = AssignFacilitatorCommand(
            tenant_id=tenant, cohort_id=cohort, person_id=people[0]
        )
        eligibility = FacilitatorEligibility(
            tenant, people[0], account_active, account_is_active=account_active
        )
        if account_active:
            fact = roster.assign_facilitator(
                db,
                command,
                authority=RosterAuthority(tenant, True),
                eligibility=eligibility,
            )
            assert fact.person_id == people[0] and fact.role_in_cohort == "instructor"
        else:
            with pytest.raises(ParticipationBadRequest, match="active user"):
                roster.assign_facilitator(
                    db,
                    command,
                    authority=RosterAuthority(tenant, True),
                    eligibility=eligibility,
                )


@pytest.mark.parametrize("wrong_scope", ["tenant", "person"])
def test_facilitator_observation_scope_rejected_before_sql(
    roster_database, wrong_scope
):
    from sqlalchemy import event

    _, app, tenant, other, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        seen = []

        def observe(*args):
            seen.append(args)

        event.listen(db.bind, "before_cursor_execute", observe)
        try:
            with pytest.raises(
                ParticipationForbidden, match="eligibility does not cover"
            ):
                roster.assign_facilitator(
                    db,
                    AssignFacilitatorCommand(
                        tenant_id=tenant, cohort_id=cohort, person_id=people[0]
                    ),
                    authority=RosterAuthority(tenant, True),
                    eligibility=FacilitatorEligibility(
                        other if wrong_scope == "tenant" else tenant,
                        people[1] if wrong_scope == "person" else people[0],
                        False,
                        account_is_active=False,
                    ),
                )
            assert seen == []
        finally:
            event.remove(db.bind, "before_cursor_execute", observe)


def test_facilitator_account_observation_is_required_without_default():
    from dataclasses import MISSING, fields

    observed = {field.name: field for field in fields(FacilitatorEligibility)}
    assert observed["account_is_active"].default is MISSING
    with pytest.raises(TypeError):
        FacilitatorEligibility(uuid4(), uuid4(), True)
