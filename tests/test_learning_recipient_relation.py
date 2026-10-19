"""Hosted current-head PostgreSQL relation canaries, not Academy adoption proof."""

from dotmac_kernel.models import Party
from dotmac_learning import audience
from dotmac_learning.participation_models import Cohort, Enrollment
from sqlalchemy import event, select, text

from tests import test_learning_catalog as learning_catalog
from tests import test_learning_roster_commands as roster_tests
from tests.test_learning_catalog import _scope
from tests.test_learning_participation_catalog import _cohort
from tests.test_learning_roster_commands import scoped

learning_database = learning_catalog.learning_database
roster_database = roster_tests.roster_database


def test_relation_nonbypass_tenant_positive_negative_and_wrong_tenant_cohort(
    roster_database,
):
    _, app, tenant, other, cohort, people, outsider, _ = roster_database
    with app.begin() as conn:
        _scope(conn, other)
        other_cohort = _cohort(conn, other)
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
        db.add(
            Enrollment(
                tenant_id=other,
                cohort_id=other_cohort,
                person_id=outsider,
                role_in_cohort="student",
                status="active",
            )
        )
    for current, own_person, own_cohort, foreign, foreign_cohort in (
        (tenant, people[0], cohort, other, other_cohort),
        (other, outsider, other_cohort, tenant, cohort),
    ):
        with scoped(app, current) as db:
            assert db.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            ).one() == ("app_user", False, False)
            relation = audience.active_student_person_ids_select(tenant_id=current)
            assert db.scalars(relation).all() == [own_person]
            assert db.scalars(
                audience.active_student_person_ids_select(
                    tenant_id=current, cohort_id=own_cohort
                )
            ).all() == [own_person]
            assert (
                db.scalars(
                    audience.active_student_person_ids_select(tenant_id=foreign)
                ).all()
                == []
            )
            assert (
                db.scalars(
                    audience.active_student_person_ids_select(
                        tenant_id=current, cohort_id=foreign_cohort
                    )
                ).all()
                == []
            )


def test_relation_active_students_dedup_optional_cohort_and_no_cohort_status_filter(
    roster_database,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with app.begin() as conn:
        _scope(conn, tenant)
        second, third = _cohort(conn, tenant), _cohort(conn, tenant)
    with scoped(app, tenant) as db:
        db.get(Cohort, second).status = "archived"
        db.add_all(
            [
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=people[0],
                    role_in_cohort="student",
                    status="active",
                ),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=second,
                    person_id=people[0],
                    role_in_cohort="student",
                    status="active",
                ),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=people[1],
                    role_in_cohort="instructor",
                    status="active",
                ),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    person_id=people[2],
                    status="dropped",
                    role_in_cohort="student",
                ),
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=third,
                    person_id=people[1],
                    role_in_cohort="student",
                    status="active",
                ),
            ]
        )
        recipients = db.scalars(
            audience.active_student_person_ids_select(tenant_id=tenant)
        ).all()
        assert len(recipients) == 2 and set(recipients) == {people[0], people[1]}
        for cid, expected in (
            (cohort, [people[0]]),
            (second, [people[0]]),
            (third, [people[1]]),
        ):
            assert (
                db.scalars(
                    audience.active_student_person_ids_select(
                        tenant_id=tenant, cohort_id=cid
                    )
                ).all()
                == expected
            )


def test_relation_construction_is_sql_free_outer_execution_autoflushes_original_session(
    roster_database,
):
    _, app, tenant, _, cohort, people, _, _ = roster_database
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        pending = Enrollment(
            tenant_id=tenant,
            cohort_id=cohort,
            person_id=people[0],
            role_in_cohort="student",
            status="active",
        )
        db.add(pending)
        statements, flushed = [], []

        def observe(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        def before_flush(session, context, instances):
            flushed.append(session)

        event.listen(app, "before_cursor_execute", observe)
        event.listen(db, "before_flush", before_flush)
        try:
            relation = audience.active_student_person_ids_select(tenant_id=tenant)
            outer = select(Party.id).where(
                Party.tenant_id == tenant, Party.id.in_(relation)
            )
            assert statements == [] and flushed == [] and pending in db.new
            assert db.scalars(outer).all() == [people[0]]
            assert flushed == [db] and pending not in db.new
            assert db.get_transaction() is root and root.is_active
            selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
            assert len(selects) == 1
            assert (
                "IN (SELECT DISTINCT mod_learning.enrollments.person_id" in selects[0]
            )
            assert any(s.lstrip().upper().startswith("INSERT") for s in statements)
        finally:
            event.remove(db, "before_flush", before_flush)
            event.remove(app, "before_cursor_execute", observe)
