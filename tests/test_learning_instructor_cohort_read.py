"""Hosted public point-read canary; no local execution or fake owner."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_learning import ReportCohort, instructor_cohort_by_id
from dotmac_learning.participation_models import Cohort
from sqlalchemy import event, select, text

from tests import test_learning_results as fixtures
from tests.test_learning_results import scoped

learning_database = fixtures.learning_database
result_database = fixtures.result_database


def test_point_read_is_tenant_scoped_detached_single_select_and_caller_owned(
    result_database,
):
    admin, app, tenant, _, _, _, foreign, *_ = result_database
    cid, foreign_id = uuid4(), uuid4()
    with admin.begin() as conn:
        conn.execute(
            Cohort.__table__.insert().values(
                id=foreign_id,
                tenant_id=foreign,
                name="Hidden foreign cohort",
                discipline="Test",
            )
        )
    with scoped(app, tenant) as db:
        cohort = Cohort(
            id=cid, tenant_id=tenant, name="Point cohort", discipline="Test"
        )
        db.add(cohort)
        db.flush()
        root = db.get_transaction()
        statements = []
        connection = db.connection()

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(connection, "before_cursor_execute", capture)
        try:
            fact = instructor_cohort_by_id(db, tenant_id=tenant, cohort_id=cid)
        finally:
            event.remove(connection, "before_cursor_execute", capture)
        assert len(statements) == 1 and statements[0].lstrip().upper().startswith(
            "SELECT"
        )
        assert isinstance(fact, ReportCohort)
        assert (
            fact.id == cid and fact.tenant_id == tenant and fact.name == "Point cohort"
        )
        assert fact.discipline == "Test" and fact.delivery_mode == cohort.delivery_mode
        assert (
            fact.created_at == cohort.created_at
            and fact.updated_at == cohort.updated_at
        )
        assert fact.status == cohort.status
        with pytest.raises((FrozenInstanceError, AttributeError)):
            fact.name = "mutated"
        assert instructor_cohort_by_id(db, tenant_id=tenant, cohort_id=uuid4()) is None
        assert instructor_cohort_by_id(db, tenant_id=foreign, cohort_id=cid) is None
        assert (
            instructor_cohort_by_id(db, tenant_id=tenant, cohort_id=foreign_id) is None
        )
        assert (
            instructor_cohort_by_id(db, tenant_id=foreign, cohort_id=foreign_id) is None
        )
        assert (
            db.get_transaction() is root
            and not db.new
            and not db.dirty
            and not db.deleted
        )
        assert db.scalar(text("SELECT current_user")) == "app_user"
        # Resetting scope cannot reveal the uncommitted owner row from another tenant.
        db.rollback()
    with admin.connect() as conn:
        assert conn.scalar(select(Cohort.id).where(Cohort.id == cid)) is None
