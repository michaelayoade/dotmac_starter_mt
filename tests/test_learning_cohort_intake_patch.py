"""Hosted PostgreSQL cohort intake canaries; never run on a workstation."""

from uuid import uuid4

import pytest
from dotmac_learning import CohortIntakePatch, patch_cohort_intake
from dotmac_learning.curriculum_models import QuestionBank
from dotmac_learning.participation_contracts import ParticipationNotFound
from dotmac_learning.participation_models import Cohort
from dotmac_learning.result_contracts import TransactionChanged
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_cohort_commands as cohort_tests
from tests.test_learning_catalog import _course, _scope
from tests.test_learning_roster_commands import scoped

learning_database = cohort_tests.learning_database
roster_database = cohort_tests.roster_database


def patch(tenant, cohort, **fields):
    return CohortIntakePatch(tenant_id=tenant, cohort_id=cohort, **fields)


def test_cross_tenant_and_missing_cohort_refuse(roster_database):
    _, app, tenant, other, cohort, _, _, _ = roster_database
    for scope, identity in ((other, cohort), (tenant, uuid4())):
        with scoped(app, scope) as db:
            with pytest.raises(
                ParticipationNotFound, match="^cohort not found for tenant$"
            ):
                patch_cohort_intake(
                    db, patch(scope, identity, auto_accept_threshold=None)
                )


@pytest.fixture
def intake_banks(roster_database):
    _, app, tenant, other, _, _, _, _ = roster_database
    banks = []
    for scope in (tenant, other):
        with app.begin() as conn:
            _scope(conn, scope)
            bank = uuid4()
            conn.execute(
                QuestionBank.__table__.insert().values(
                    id=bank,
                    tenant_id=scope,
                    course_id=_course(conn, scope),
                    kind="final",
                    version=1,
                )
            )
            banks.append(bank)
    return tuple(banks)


def test_foreign_and_missing_bank_refuse_before_any_assignment(
    roster_database, intake_banks
):
    _, app, tenant, _, cohort, _, _, _ = roster_database
    _, foreign = intake_banks
    with scoped(app, tenant) as db:
        row = db.get(Cohort, cohort)
        before = (row.entrance_bank_id, row.entrance_time_limit_minutes)
        for bank in (foreign, uuid4()):
            with pytest.raises(
                ParticipationNotFound, match="^entrance bank not found for tenant$"
            ):
                patch_cohort_intake(
                    db,
                    patch(
                        tenant,
                        cohort,
                        entrance_bank_id=bank,
                        entrance_time_limit_minutes=43,
                    ),
                )
            assert (row.entrance_bank_id, row.entrance_time_limit_minutes) == before
            assert row not in db.dirty


def test_selected_fields_stage_preserving_omission_and_unrelated_values(
    roster_database, intake_banks
):
    _, app, tenant, _, cohort, _, _, _ = roster_database
    bank, _ = intake_banks
    with scoped(app, tenant) as db:
        row = db.get(Cohort, cohort)
        row.entrance_time_limit_minutes = 17
        row.auto_accept_threshold = 0.6
        row.auto_accept_below_action = "none"
        db.flush()
        untouched = (
            row.name,
            row.discipline,
            row.status,
            row.delivery_mode,
            row.auto_accept_below_action,
            row.created_at,
            row.updated_at,
        )
        with cohort_tests.observed(db) as (statements, flushes):
            assert (
                patch_cohort_intake(db, patch(tenant, cohort, entrance_bank_id=bank))
                is None
            )
            assert flushes == []
            assert len(statements) == 2
        assert row.entrance_bank_id == bank
        assert row.entrance_time_limit_minutes == 17
        assert row.auto_accept_threshold == 0.6
        assert (
            row.name,
            row.discipline,
            row.status,
            row.delivery_mode,
            row.auto_accept_below_action,
            row.created_at,
            row.updated_at,
        ) == untouched
        db.flush()
        patch_cohort_intake(
            db,
            patch(
                tenant,
                cohort,
                entrance_bank_id=None,
                entrance_time_limit_minutes=None,
                auto_accept_threshold=None,
            ),
        )
        assert (
            row.entrance_bank_id,
            row.entrance_time_limit_minutes,
            row.auto_accept_threshold,
        ) == (None, None, None)


def test_empty_patch_checks_existence_and_never_fills_defaults(roster_database):
    _, app, tenant, _, cohort, _, _, _ = roster_database
    with scoped(app, tenant) as db:
        row = db.get(Cohort, cohort)
        with cohort_tests.observed(db) as (statements, flushes):
            patch_cohort_intake(db, patch(tenant, cohort))
            assert len(statements) == 1 and flushes == []
        assert row not in db.dirty


def test_requires_existing_transaction_before_sql_or_autobegin(roster_database):
    _, app, tenant, _, cohort, _, _, _ = roster_database
    statements = []

    def executed(*args):
        statements.append(args)

    with Session(bind=app) as db:
        event.listen(app, "before_cursor_execute", executed)
        try:
            with pytest.raises(TransactionChanged):
                patch_cohort_intake(db, patch(tenant, cohort))
            assert db.get_transaction() is None and statements == []
        finally:
            event.remove(app, "before_cursor_execute", executed)


def test_caller_root_and_savepoint_rollback_preserved(roster_database):
    _, app, tenant, _, cohort, _, _, _ = roster_database
    with scoped(app, tenant) as db:
        original = db.get(Cohort, cohort).auto_accept_threshold
    with Session(bind=app) as db, db.begin():
        _scope(db.connection(), tenant)
        root = db.get_transaction()
        with db.begin_nested() as nested:
            patch_cohort_intake(db, patch(tenant, cohort, auto_accept_threshold=0.75))
            assert db.get_transaction() is root
            assert db.get_nested_transaction() is nested
            db.flush()
            nested.rollback()
        assert db.get(Cohort, cohort).auto_accept_threshold == original
        patch_cohort_intake(db, patch(tenant, cohort, auto_accept_threshold=0.85))
        db.flush()
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.get(Cohort, cohort).auto_accept_threshold == original


def test_normal_lookup_autoflush_and_error_are_preserved(roster_database):
    _, app, tenant, _, cohort, _, _, _ = roster_database
    with scoped(app, tenant) as db:
        row = db.get(Cohort, cohort)
        row.name = "pending caller change"
        with cohort_tests.observed(db) as (statements, flushes):
            patch_cohort_intake(db, patch(tenant, cohort, auto_accept_threshold=0.5))
            assert flushes == [db]
            assert statements[0].startswith("UPDATE mod_learning.cohorts")
        assert row.auto_accept_threshold == 0.5
    with scoped(app, tenant) as db:
        db.add(Cohort(tenant_id=tenant, name=None, discipline="invalid"))
        root = db.get_transaction()
        with pytest.raises(IntegrityError):
            patch_cohort_intake(db, patch(tenant, cohort, auto_accept_threshold=0.7))
        assert not root.is_active
        db.rollback()
