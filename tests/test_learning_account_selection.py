"""Hosted composition proof only; this test account table is not Academy adoption."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.models import Party
from dotmac_learning import instructor_reads as reads
from dotmac_learning import success_queue as queue
from dotmac_learning.account_contracts import ActiveAccountSelection
from dotmac_learning.attempt_contracts import AttemptAuditContext
from dotmac_learning.participation_models import Cohort, Enrollment
from dotmac_learning.success_contracts import SuccessTerms
from sqlalchemy import Column, MetaData, String, Table, Uuid, event, select, text
from sqlalchemy.exc import IntegrityError

from tests import test_learning_results as result_tests
from tests.test_learning_catalog import _person
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database
NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
# Genuine product-owned TEST storage, explicitly separate from identity activity.
ACCOUNTS = Table(
    "learning_test_product_accounts",
    MetaData(),
    Column("tenant_id", Uuid, nullable=False),
    Column("person_id", Uuid, nullable=False),
    Column("status", String, nullable=False),
    schema="public",
)


def selection(tenant):
    return ActiveAccountSelection(
        tenant,
        select(ACCOUNTS.c.person_id).where(
            ACCOUNTS.c.tenant_id == tenant, ACCOUNTS.c.status == "active"
        ),
    )


@pytest.fixture
def account_database(result_database):
    admin, app, tenant, person, _, _, other, other_person, *_ = result_database
    with admin.begin() as conn:
        conn.execute(
            text("""
            CREATE TABLE public.learning_test_product_accounts (
                tenant_id UUID NOT NULL,
                person_id UUID NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (tenant_id, person_id)
            )
        """)
        )
        conn.execute(
            text(
                "ALTER TABLE public.learning_test_product_accounts "
                "ENABLE ROW LEVEL SECURITY"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE public.learning_test_product_accounts "
                "FORCE ROW LEVEL SECURITY"
            )
        )
        conn.execute(
            text("""
            CREATE POLICY learning_test_account_scope
            ON public.learning_test_product_accounts
            USING (tenant_id =
                nullif(current_setting('app.current_tenant', true), '')::uuid)
            WITH CHECK (tenant_id =
                nullif(current_setting('app.current_tenant', true), '')::uuid)
        """)
        )
        conn.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE "
                "ON public.learning_test_product_accounts TO app_user"
            )
        )
    for tid, pid in ((tenant, person), (other, other_person)):
        with scoped(app, tid) as db:
            db.execute(
                ACCOUNTS.insert().values(tenant_id=tid, person_id=pid, status="active")
            )
    yield result_database


def seed(db, tenant, person, *, status="active"):
    cohort = Cohort(tenant_id=tenant, name="Account composition", discipline="test")
    db.add(cohort)
    db.flush()
    db.add(
        Enrollment(
            tenant_id=tenant,
            cohort_id=cohort.id,
            person_id=person,
            status=status,
            created_at=NOW - timedelta(days=30),
            role_in_cohort="student",
        )
    )
    db.flush()
    return cohort.id


@contextmanager
def statements(engine):
    seen = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def composed_rosters(seen):
    return [
        s
        for s in seen
        if s.lstrip().upper().startswith("SELECT")
        and "public.learning_test_product_accounts" in s
        and "mod_learning.enrollments" in s
    ]


@pytest.mark.parametrize("operation", ["segment", "sweep", "summary", "workspace"])
def test_atomic_transition_exposes_split_hybrid_but_composed_statement_excludes_it(
    account_database,
    operation,
):
    _, app, tenant, person, *_ = account_database
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person, status="dropped")
    with scoped(app, tenant) as db:
        assert db.scalar(text("SHOW transaction_isolation")) == "read committed"
        reader_pid = db.scalar(text("SELECT pg_backend_pid()"))
        # The obsolete eager population observes the first half of state A.
        old_population = frozenset(db.scalars(selection(tenant).active_person_ids))
        assert old_population == {person}
        assert (
            db.scalars(
                select(Enrollment.person_id).where(
                    Enrollment.tenant_id == tenant,
                    Enrollment.status == "active",
                    Enrollment.person_id.in_(selection(tenant).active_person_ids),
                )
            ).all()
            == []
        )
        with statements(app) as construction:
            accounts = selection(tenant)
        assert construction == []
        # Real separate backend atomically commits state B before the actual read.
        with scoped(app, tenant) as writer:
            assert writer.scalar(text("SHOW transaction_isolation")) == "read committed"
            assert writer.scalar(text("SELECT pg_backend_pid()")) != reader_pid
            writer.execute(
                ACCOUNTS.update()
                .where(
                    ACCOUNTS.c.tenant_id == tenant,
                    ACCOUNTS.c.person_id == person,
                )
                .values(status="suspended")
            )
            writer.execute(
                Enrollment.__table__.update()
                .where(
                    Enrollment.tenant_id == tenant,
                    Enrollment.person_id == person,
                )
                .values(status="active")
            )
        # Reproduce the invalid split-read hybrid against the same live database.
        assert db.scalars(
            select(Enrollment.person_id).where(
                Enrollment.tenant_id == tenant,
                Enrollment.status == "active",
                Enrollment.role_in_cohort == "student",
                Enrollment.person_id.in_(old_population),
            )
        ).all() == [person]
        with statements(app) as seen:
            if operation == "segment":
                assert (
                    queue.segment_members(
                        db,
                        tenant_id=tenant,
                        cohort_id=cohort,
                        segment="inactive",
                        terms=SuccessTerms(
                            inactive_days=7,
                            min_grade_pct=60,
                            certificate_grace_hours=6,
                            inactivity_high_factor=2,
                            overdue_high_count=3,
                            overdue_high_days=7,
                            overdue_title_limit=5,
                            below_passing_min_graded=2,
                            below_passing_min_weight_fraction=0.20,
                            below_passing_high_grade_factor=0.5,
                            almost_complete_fraction=0.8,
                            enabled_rules=(
                                "inactivity",
                                "overdue_work",
                                "below_passing",
                                "failed_final",
                                "certificate_blocked",
                            ),
                            severity_by_rule={
                                "failed_final": "high",
                                "certificate_blocked": "medium",
                            },
                        ),
                        accounts=accounts,
                        now=NOW,
                    )
                    == ()
                )
            elif operation == "sweep":
                assert (
                    queue.sweep(
                        db,
                        tenant_id=tenant,
                        terms=SuccessTerms(
                            inactive_days=7,
                            min_grade_pct=60,
                            certificate_grace_hours=6,
                            inactivity_high_factor=2,
                            overdue_high_count=3,
                            overdue_high_days=7,
                            overdue_title_limit=5,
                            below_passing_min_graded=2,
                            below_passing_min_weight_fraction=0.20,
                            below_passing_high_grade_factor=0.5,
                            almost_complete_fraction=0.8,
                            enabled_rules=(
                                "inactivity",
                                "overdue_work",
                                "below_passing",
                                "failed_final",
                                "certificate_blocked",
                            ),
                            severity_by_rule={
                                "failed_final": "high",
                                "certificate_blocked": "medium",
                            },
                        ),
                        accounts=accounts,
                        now=NOW,
                    ).opened
                    == 0
                )
            elif operation == "summary":
                (row,) = reads.cohorts_summary(
                    db, tenant_id=tenant, accounts=accounts
                ).cohorts
                assert row.active_student_count == 0
                assert [r.person.id for r in row.roster] == [person]
            else:
                row = reads.cohort_workspace(
                    db,
                    tenant_id=tenant,
                    cohort_id=cohort,
                    accounts=accounts,
                    now=NOW,
                    at_risk_pct=0.5,
                    stale_days=14,
                    limit=12,
                )
                assert row.active_student_count == 0 and row.student_total == 1
                assert row.average_progress == 0 and row.at_risk_count == 0
                assert row.students[0].person.id == person
        (composed,) = composed_rosters(seen)
        if operation in ("summary", "workspace"):
            # Membership belongs to the projected SELECT list, not roster WHERE.
            assert (
                "public.learning_test_product_accounts"
                in composed.split("FROM mod_learning.enrollments", 1)[0]
            )
        # No standalone account read is hidden alongside the composed statement.
        assert (
            len([s for s in seen if "public.learning_test_product_accounts" in s]) == 1
        )


def test_real_account_scope_nonnull_duplicates_and_identity_independence(
    account_database,
):
    admin, app, tenant, person, _, _, other, other_person, *_ = account_database
    with admin.begin() as conn:
        suspended = _person(conn, tenant)
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person)
        seed(
            db, tenant, person
        )  # Same person, distinct cohort; no deduplication drift.
        seed(db, tenant, suspended)
        db.execute(
            ACCOUNTS.insert().values(
                tenant_id=tenant, person_id=suspended, status="suspended"
            )
        )
        person_row = db.get(Party, person)
        suspended_row = db.get(Party, suspended)
        assert person_row is not None
        assert suspended_row is not None
        person_row.is_active = False
        suspended_row.is_active = True
        # Pending identity writes autoflush normally on the caller.
        with statements(app) as seen:
            summary = reads.cohorts_summary(
                db, tenant_id=tenant, accounts=selection(tenant)
            )
        assert any(s.lstrip().startswith("UPDATE") for s in seen)
        assert sum(c.active_student_count for c in summary.cohorts) == 2
        assert sum(len(c.roster) for c in summary.cohorts) == 3
        assert len(composed_rosters(seen)) == 1
        assert (
            queue.segment_members(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment="inactive",
                terms=SuccessTerms(
                    inactive_days=7,
                    min_grade_pct=60,
                    certificate_grace_hours=6,
                    inactivity_high_factor=2,
                    overdue_high_count=3,
                    overdue_high_days=7,
                    overdue_title_limit=5,
                    below_passing_min_graded=2,
                    below_passing_min_weight_fraction=0.20,
                    below_passing_high_grade_factor=0.5,
                    almost_complete_fraction=0.8,
                    enabled_rules=(
                        "inactivity",
                        "overdue_work",
                        "below_passing",
                        "failed_final",
                        "certificate_blocked",
                    ),
                    severity_by_rule={
                        "failed_final": "high",
                        "certificate_blocked": "medium",
                    },
                ),
                accounts=selection(tenant),
                now=NOW,
            )[0].id
            == person
        )
        assert db.scalars(selection(other).active_person_ids).all() == []
        assert db.scalars(selection(tenant).active_person_ids).all() == [person]
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(
                ACCOUNTS.insert().values(
                    tenant_id=tenant, person_id=None, status="active"
                )
            )
    with scoped(app, other) as db:
        assert (
            db.scalar(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
            is False
        )
        assert (
            db.scalar(
                text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            )
            is False
        )
        assert db.scalars(selection(other).active_person_ids).all() == [other_person]
        assert db.scalars(selection(tenant).active_person_ids).all() == []
        assert (
            reads.cohorts_summary(
                db, tenant_id=tenant, accounts=selection(tenant)
            ).cohorts
            == ()
        )
        assert (
            queue.segment_members(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment="inactive",
                terms=SuccessTerms(
                    inactive_days=7,
                    min_grade_pct=60,
                    certificate_grace_hours=6,
                    inactivity_high_factor=2,
                    overdue_high_count=3,
                    overdue_high_days=7,
                    overdue_title_limit=5,
                    below_passing_min_graded=2,
                    below_passing_min_weight_fraction=0.20,
                    below_passing_high_grade_factor=0.5,
                    almost_complete_fraction=0.8,
                    enabled_rules=(
                        "inactivity",
                        "overdue_work",
                        "below_passing",
                        "failed_final",
                        "certificate_blocked",
                    ),
                    severity_by_rule={
                        "failed_final": "high",
                        "certificate_blocked": "medium",
                    },
                ),
                accounts=selection(tenant),
                now=NOW,
            )
            == ()
        )
    with app.begin() as conn:
        assert conn.execute(select(ACCOUNTS.c.person_id)).all() == []


def test_composed_message_preserves_autoflush_and_required_writer_rollback(
    account_database,
):
    _, app, tenant, person, *_ = account_database
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person)
    seen = []

    def fail_required_delivery(caller, recipient, message, action_id):
        assert caller is db and recipient.id == person
        caller.execute(
            ACCOUNTS.update()
            .where(
                ACCOUNTS.c.tenant_id == tenant,
                ACCOUNTS.c.person_id == person,
            )
            .values(status="suspended")
        )
        raise RuntimeError("required product delivery failed")

    with pytest.raises(RuntimeError, match="required product delivery failed"):
        with scoped(app, tenant) as db:
            db.get(Cohort, cohort).name = "Pending caller write"
            enrollment = db.scalar(
                select(Enrollment).where(Enrollment.cohort_id == cohort)
            )
            enrollment.created_at = NOW - timedelta(days=40)
            with statements(app) as observed:
                seen = observed
                queue.message_segment(
                    db,
                    tenant_id=tenant,
                    cohort_id=cohort,
                    segment="inactive",
                    subject="Subject",
                    body="Body",
                    actor_person_id=person,
                    terms=SuccessTerms(
                        inactive_days=7,
                        min_grade_pct=60,
                        certificate_grace_hours=6,
                        inactivity_high_factor=2,
                        overdue_high_count=3,
                        overdue_high_days=7,
                        overdue_title_limit=5,
                        below_passing_min_graded=2,
                        below_passing_min_weight_fraction=0.20,
                        below_passing_high_grade_factor=0.5,
                        almost_complete_fraction=0.8,
                        enabled_rules=(
                            "inactivity",
                            "overdue_work",
                            "below_passing",
                            "failed_final",
                            "certificate_blocked",
                        ),
                        severity_by_rule={
                            "failed_final": "high",
                            "certificate_blocked": "medium",
                        },
                    ),
                    accounts=selection(tenant),
                    audit_context=AttemptAuditContext(tenant, "system", "academy"),
                    delivery=fail_required_delivery,
                    now=NOW,
                )
    assert len(composed_rosters(seen)) == 1
    assert any("UPDATE mod_learning.enrollments" in s for s in seen)
    with scoped(app, tenant) as db:
        assert db.get(Cohort, cohort).name == "Account composition"
        assert db.scalar(
            select(Enrollment.created_at).where(Enrollment.cohort_id == cohort)
        ) == NOW - timedelta(days=30)
        assert db.scalars(selection(tenant).active_person_ids).all() == [person]


@pytest.mark.parametrize("active", [False, True])
def test_receipt_delivery_composes_product_account_selection_on_caller_session(
    account_database, monkeypatch, active
):
    from dotmac_kernel import audit_actions
    from dotmac_kernel.audit import AuditEvent
    from dotmac_learning.manifest import module
    from dotmac_learning.success_contracts import SegmentMessageEffect

    from tests.test_learning_success_queue import TERMS

    monkeypatch.setattr(
        audit_actions,
        "_active_registry",
        audit_actions.AuditActionRegistry.from_manifests([module]),
    )
    _, app, tenant, person, *_ = account_database
    with scoped(app, tenant) as db:
        cohort = seed(db, tenant, person)
        db.execute(
            ACCOUNTS.update()
            .where(ACCOUNTS.c.person_id == person)
            .values(status="active" if active else "suspended")
        )
        db.get(Party, person).is_active = False
        root = db.get_transaction()
        delivered = []

        def deliver(caller, recipient, message, action_id):
            assert caller is db and caller.get_transaction() is root
            assert caller.get_nested_transaction() is None
            delivered.append(recipient.id)

        with statements(app) as seen:
            effect = queue.message_segment_with_receipt(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                segment="inactive",
                subject="Subject",
                body="Body",
                actor_person_id=person,
                terms=TERMS,
                accounts=selection(tenant),
                audit_context=AttemptAuditContext(tenant, "system", "academy"),
                delivery=deliver,
                now=NOW,
            )
        assert type(effect) is SegmentMessageEffect
        assert delivered == ([person] if active else [])
        assert effect.result.recipients == int(active)
        (composed,) = composed_rosters(seen)
        assert "public.learning_test_product_accounts" in composed
        audit = db.get(AuditEvent, effect.audit_event_id)
        assert audit is not None and audit.details["recipients"] == int(active)
        assert db.get_transaction() is root
