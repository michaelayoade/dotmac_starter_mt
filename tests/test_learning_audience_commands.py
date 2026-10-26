"""Hosted PostgreSQL explicit-roster classification and review queue parity."""

from uuid import uuid4

import pytest
from dotmac_kernel.models import Party, PartyPerson
from dotmac_learning import audience
from dotmac_learning.participation_contracts import ClassifyRosterCommand
from dotmac_learning.participation_models import Enrollment
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests import test_learning_roster_commands as roster_tests
from tests.test_learning_catalog import _person, _scope
from tests.test_learning_participation_catalog import _cohort
from tests.test_learning_roster_commands import scoped

learning_database = learning_catalog.learning_database
roster_database = roster_tests.roster_database


def test_audience_cross_tenant_isolation(roster_database):
    _, app, tenant, other, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=people[0],
                role_in_cohort="student",
                status="active",
            )
        )
    with scoped(app, other) as db:
        result = audience.classify_from_roster(
            db,
            ClassifyRosterCommand(
                tenant_id=other, roster={"peer@x": "ERP"}, mark_rest_external=True
            ),
        )
        assert (
            result.staff
            == result.external
            == result.unclassified
            == result.unchanged
            == 0
        )
        assert audience.unclassified(db, tenant_id=other) == ()
        assert audience.staff_person_ids(db, tenant_id=other) == ()
    with scoped(app, tenant) as db:
        assert db.scalar(select(Enrollment.audience)) is None


def test_explicit_roster_last_collision_reference_bytes_and_no_domain_guess(
    roster_database,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        for person in people:
            db.add(
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=person,
                    role_in_cohort="student",
                    status="active",
                )
            )
        result = audience.classify_from_roster(
            db,
            ClassifyRosterCommand(
                tenant_id=tenant,
                roster={
                    " PEER@X ": "old",
                    "peer@x": " ERP-1 ",
                    "fresh@x": "",
                    "": "discard",
                    None: "discard none email",
                    "active@x": None,
                },
            ),
        )
        assert (result.staff, result.unclassified) == (1, 2)
        row = db.scalar(select(Enrollment).where(Enrollment.person_id == people[0]))
        assert (row.audience, row.employee_ref) == ("staff", " ERP-1 ")
        result = audience.classify_from_roster(
            db, ClassifyRosterCommand(tenant_id=tenant, roster={})
        )
        assert result.unclassified == 3
        assert (row.audience, row.employee_ref) == ("staff", " ERP-1 ")
        result = audience.classify_from_roster(
            db,
            ClassifyRosterCommand(tenant_id=tenant, roster={}, mark_rest_external=True),
        )
        assert result.external == 3 and row.employee_ref is None
        row.employee_ref = "old external ref"
        result = audience.classify_from_roster(
            db,
            ClassifyRosterCommand(tenant_id=tenant, roster={}, mark_rest_external=True),
        )
        assert result.unchanged == 3 and row.employee_ref == "old external ref"


def test_audience_active_student_only_distinct_per_group_and_queue_source_order(
    roster_database,
):
    admin, app, tenant, _, cohort, people, _, _ = roster_database
    # Genuine profiles and enrolments deliberately arrive in reverse email order.
    with admin.begin() as conn:
        queued_people = []
        for email, first, last in (
            ("z-review@x", "Zulu", "Review"),
            ("a-review@x", "Alpha", "Review"),
        ):
            person = _person(conn, tenant)
            conn.execute(
                Party.__table__.update().where(Party.id == person).values(email=email)
            )
            conn.execute(
                PartyPerson.__table__.update()
                .where(PartyPerson.party_id == person)
                .values(first_name=first, last_name=last)
            )
            queued_people.append(person)
    with app.begin() as conn:
        _scope(conn, tenant)
        second, third = _cohort(conn, tenant), _cohort(conn, tenant)
    with scoped(app, tenant) as db:
        for person in queued_people:
            db.add(
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=person,
                    role_in_cohort="student",
                    status="active",
                )
            )
        db.execute(
            Party.__table__.update()
            .where(Party.id == people[0])
            .values(is_active=False)
        )
        db.execute(
            PartyPerson.__table__.update()
            .where(PartyPerson.party_id == people[0])
            .values(first_name=" First ", last_name="Last")
        )
        for cid, aud, ref in (
            (cohort, None, None),
            (second, None, None),
            (third, "staff", "EMP"),
        ):
            db.add(
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cid,
                    person_id=people[0],
                    audience=aud,
                    employee_ref=ref,
                    role_in_cohort="student",
                    status="active",
                )
            )
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=people[1],
                role_in_cohort="instructor",
                status="active",
            )
        )
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=people[2],
                status="dropped",
                role_in_cohort="student",
            )
        )
        db.flush()
        counts = audience.counts_by_audience(db, tenant_id=tenant)
        assert (counts.staff, counts.external, counts.unclassified) == (1, 0, 3)
        queue = audience.unclassified(db, tenant_id=tenant)
        assert [(x.email, x.name) for x in queue] == [
            ("a-review@x", "Alpha Review"),
            ("peer@x", "First  Last"),
            ("z-review@x", "Zulu Review"),
        ]
        assert audience.staff_person_ids(db, tenant_id=tenant) == (people[0],)
        result = audience.classify_from_roster(
            db, ClassifyRosterCommand(tenant_id=tenant, roster={"peer@x": "EMP"})
        )
        assert result.staff == 2 and result.unchanged == 1
        assert result.unclassified == 2


def test_audience_transaction_precheck_autoflush_failure_and_caller_rollback(
    roster_database,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    command = ClassifyRosterCommand(tenant_id=tenant, roster={"peer@x": "ERP"})
    with Session(bind=app) as db:
        with pytest.raises(TransactionChanged):
            audience.classify_from_roster(db, command)
        assert db.get_transaction() is None
    with scoped(app, tenant) as db:
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=people[0],
                role_in_cohort="student",
                status="active",
            )
        )
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        with db.begin_nested() as nested:
            audience.classify_from_roster(db, command)
            assert (
                db.get_transaction() is root and db.get_nested_transaction() is nested
            )
            nested.rollback()
        assert db.scalar(select(Enrollment.audience)) is None
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=uuid4(),
                person_id=people[1],
                role_in_cohort="student",
                status="active",
            )
        )
        with pytest.raises(IntegrityError):
            audience.classify_from_roster(db, command)
        assert not root.is_active
        db.rollback()
