"""Real-Postgres isolation canaries for banking, tax, and payroll.

The reference assembly builds these optional modules but does not compose their
lineages.  Each case therefore creates a disposable database containing only
the kernel and one candidate module, then drives the real online tenant role.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from dotmac_banking.manifest import module as banking_module
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_payroll.manifest import module as payroll_module
from dotmac_tax.manifest import module as tax_module
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)


@dataclass(frozen=True, slots=True)
class ModuleCase:
    manifest: ModuleManifest
    distribution: str
    package_name: str
    schema: str
    probe_table: str
    seed_sql: str
    seed_values: dict[str, str]

    @property
    def versions(self) -> Path:
        return REPO_ROOT.joinpath(
            "packages",
            self.distribution,
            "src",
            self.package_name,
            "migrations",
            "versions",
        )


CASES = (
    ModuleCase(
        manifest=banking_module,
        distribution="dotmac-banking",
        package_name="dotmac_banking",
        schema="mod_banking",
        probe_table="bank_institutions",
        seed_sql=(
            "INSERT INTO mod_banking.bank_institutions "
            "(id, tenant_id, code, name, country_code, status) "
            "VALUES (:id, :tenant, :code, :name, 'NG', 'active')"
        ),
        seed_values={"code": "BANK-1", "name": "Configured institution"},
    ),
    ModuleCase(
        manifest=tax_module,
        distribution="dotmac-tax",
        package_name="dotmac_tax",
        schema="mod_tax",
        probe_table="tax_authorities",
        seed_sql=(
            "INSERT INTO mod_tax.tax_authorities "
            "(id, tenant_id, code, name, status) "
            "VALUES (:id, :tenant, :code, :name, 'active')"
        ),
        seed_values={"code": "AUTH-1", "name": "Configured authority"},
    ),
    ModuleCase(
        manifest=payroll_module,
        distribution="dotmac-payroll",
        package_name="dotmac_payroll",
        schema="mod_payroll",
        probe_table="pay_components",
        seed_sql=(
            "INSERT INTO mod_payroll.pay_components "
            "(id, tenant_id, component_code, name, kind, status) "
            "VALUES (:id, :tenant, :code, :name, 'information', 'active')"
        ),
        seed_values={"code": "INFO-1", "name": "Configured component"},
    ),
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these RLS proofs need PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture(params=CASES, ids=lambda case: case.manifest.code)
def migrated_module(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[ModuleCase, str, str]]:
    case: ModuleCase = request.param
    superuser = _superuser_url()
    name = f"{case.manifest.code}_rls_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations", f"{KERNEL_VERSIONS} {case.versions}"
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield case, admin_url, _url_for(superuser, name, user="app_user")
    finally:
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
    engine.dispose()
    return tenant_a, tenant_b


def _tenant_session(url: str, tenant_id: uuid.UUID) -> Session:
    session = Session(create_engine(url))
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, false)"),
        {"tenant": str(tenant_id)},
    )
    return session


def test_live_catalog_proves_every_declared_table_is_tenant_isolated(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    case, admin_url, _ = migrated_module
    registry = NamespaceRegistry.from_manifests([case.manifest])
    engine = create_engine(admin_url)
    with engine.connect() as connection:
        assert audit_live_schemas(connection, registry) == ()
        platform_reads = {
            table: connection.execute(
                text(
                    "SELECT has_table_privilege("
                    "'platform_api', CAST(:table AS text), 'SELECT')"
                ),
                {"table": f"{case.schema}.{table}"},
            ).scalar_one()
            for table in case.manifest.tables
        }
    engine.dispose()
    assert not any(platform_reads.values())


def test_online_role_cannot_read_or_write_across_tenants(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    case, admin_url, app_url = migrated_module
    tenant_a, tenant_b = _seed_tenants(admin_url)
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        connection.execute(
            text(case.seed_sql),
            {"id": uuid.uuid4(), "tenant": tenant_a, **case.seed_values},
        )
    admin.dispose()

    with _tenant_session(app_url, tenant_b) as session:
        count = session.scalar(
            text(
                f"SELECT count(*) FROM {case.schema}.{case.probe_table}"  # noqa: S608
            )
        )
        assert count == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            session.execute(
                text(case.seed_sql),
                {"id": uuid.uuid4(), "tenant": tenant_a, **case.seed_values},
            )


def test_payroll_consequence_facts_round_trip_on_postgres(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    """Two employees, a deduction and a settlement through real MONEY columns."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from dotmac_kernel.money import Currency, Money
    from dotmac_payroll import (
        EmployeeComponentInput,
        approve_payroll_run,
        assign_employee_pay_structure,
        build_liability_settlement_consequence,
        build_run_accounting_consequence,
        calculate_employee_payroll,
        create_payroll_run,
        finalize_payroll_run,
        record_liability_settlement,
    )
    from dotmac_payroll.models import PayrollLiability
    from sqlalchemy import select

    from tests.unit.test_payroll_service import _configured_payroll

    case, admin_url, app_url = migrated_module
    if case.manifest.code != "payroll":
        pytest.skip("payroll-only proof")
    tenant_a, _ = _seed_tenants(admin_url)
    ngn = Currency("NGN", 2)
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)
    with _tenant_session(app_url, tenant_a) as session:
        base, _, deduction, _, revision, _ = _configured_payroll(session, tenant_a)
        assign_employee_pay_structure(
            session,
            tenant_id=tenant_a,
            employee_ref="employee:opaque-2",
            revision_id=revision.id,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            source_ref="people-assignment:2",
            source_version="1",
        )
        run = create_payroll_run(
            session,
            tenant_id=tenant_a,
            run_ref="PG-RUN",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            currency=ngn,
            created_by_id=uuid.uuid4(),
            created_at=now,
        )
        for employee, gross, tax in (
            ("employee:opaque-1", "1000.10", "100.05"),
            ("employee:opaque-2", "2000.20", "300.15"),
        ):
            calculate_employee_payroll(
                session,
                tenant_id=tenant_a,
                run_id=run.id,
                employee_ref=employee,
                proration_factor=Decimal("1"),
                inputs=(
                    EmployeeComponentInput(base.id, Money.of(gross, ngn), "comp"),
                    EmployeeComponentInput(deduction.id, Money.of(tax, ngn), "tax"),
                ),
                calculated_at=now,
            )
        approve_payroll_run(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            approved_by_id=uuid.uuid4(),
            approved_at=now,
        )
        finalize_payroll_run(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            finalized_by_id=uuid.uuid4(),
            finalized_at=now,
        )
        fact = build_run_accounting_consequence(
            session, tenant_id=tenant_a, run_id=run.id
        )
        assert sum(x.debit for x in fact.entries) == sum(x.credit for x in fact.entries)
        assert sum(x.credit for x in fact.entries) > 0
        net = session.scalars(
            select(PayrollLiability).where(
                PayrollLiability.run_id == run.id,
                PayrollLiability.liability_key == "employee-net-pay",
            )
        ).first()
        settlement = record_liability_settlement(
            session,
            tenant_id=tenant_a,
            liability_id=net.id,
            amount=Money.of("10.01", ngn),
            settlement_ref="pg-pay:1",
            evidence_ref="bank:1",
            recorded_by_id=uuid.uuid4(),
            recorded_at=now,
        )
        settled = build_liability_settlement_consequence(
            session,
            tenant_id=tenant_a,
            settlement_id=settlement.id,
            bank_account_ref="ledger:bank",
            posting_date=date(2026, 9, 1),
        )
        assert [(x.debit, x.credit) for x in settled.entries] == [
            (Decimal("10.01"), Decimal("0.00")),
            (Decimal("0.00"), Decimal("10.01")),
        ]


def test_payroll_internal_recovery_and_cancel_on_postgres(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    """a4 columns and CHECKs through the real migration: internal recovery + cancel."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from dotmac_kernel.money import Currency, Money
    from dotmac_payroll import (
        EmployeeComponentInput,
        PayComponentInput,
        PayRuleInput,
        approve_payroll_run,
        assign_employee_pay_structure,
        build_run_accounting_consequence,
        calculate_employee_payroll,
        cancel_payroll_run,
        create_pay_component,
        create_pay_structure,
        create_payroll_run,
        finalize_payroll_run,
        publish_pay_structure_revision,
        record_liability_settlement,
    )
    from dotmac_payroll.models import PayrollLiability
    from sqlalchemy import select

    case, admin_url, app_url = migrated_module
    if case.manifest.code != "payroll":
        pytest.skip("payroll-only proof")
    tenant_a, _ = _seed_tenants(admin_url)
    ngn = Currency("NGN", 2)
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)

    def rule(component_id, sequence, **kw):
        return PayRuleInput(
            component_id, sequence, "input", None, None, (), False, **kw
        )

    with _tenant_session(app_url, tenant_a) as session:
        base = create_pay_component(
            session,
            tenant_id=tenant_a,
            command=PayComponentInput("B", "Base", "earning", "sal", None, None),
        )
        loan = create_pay_component(
            session,
            tenant_id=tenant_a,
            command=PayComponentInput("L", "Loan", "deduction", None, "1410", "desk"),
        )
        structure = create_pay_structure(
            session, tenant_id=tenant_a, code="S", name="S", currency=ngn
        )
        revision = publish_pay_structure_revision(
            session,
            tenant_id=tenant_a,
            structure_id=structure.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            net_payable_account_ref="2040",
            rules=(
                rule(base.id, 1),
                rule(loan.id, 2, settlement_mode="internal_recovery"),
            ),
            published_by_id=uuid.uuid4(),
            published_at=now,
        )
        assign_employee_pay_structure(
            session,
            tenant_id=tenant_a,
            employee_ref="employee:pg-a4",
            revision_id=revision.id,
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 7, 15),
            source_ref="people:1",
            source_version="1",
        )
        run = create_payroll_run(
            session,
            tenant_id=tenant_a,
            run_ref="PG-A4",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            currency=ngn,
            created_by_id=uuid.uuid4(),
            created_at=now,
        )
        calculate_employee_payroll(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            employee_ref="employee:pg-a4",
            proration_factor=Decimal("1"),
            inputs=(
                EmployeeComponentInput(base.id, Money.of("1000.10", ngn), "c"),
                EmployeeComponentInput(loan.id, Money.of("200.05", ngn), "l"),
            ),
            calculated_at=now,
        )
        approve_payroll_run(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            approved_by_id=uuid.uuid4(),
            approved_at=now,
        )
        finalize_payroll_run(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            finalized_by_id=uuid.uuid4(),
            finalized_at=now,
        )
        recovery = session.scalars(
            select(PayrollLiability).where(
                PayrollLiability.run_id == run.id,
                PayrollLiability.settlement_mode == "internal_recovery",
            )
        ).one()
        assert (recovery.status, recovery.beneficiary_type) == ("settled", "employee")
        assert recovery.amount_settled == recovery.amount == Decimal("200.05")
        fact = build_run_accounting_consequence(
            session, tenant_id=tenant_a, run_id=run.id
        )
        assert {x.account_ref: x.credit for x in fact.entries if x.credit} == {
            "1410": Decimal("200.05"),
            "2040": Decimal("800.05"),
        }
        with pytest.raises(Exception, match="internal recovery"):
            record_liability_settlement(
                session,
                tenant_id=tenant_a,
                liability_id=recovery.id,
                amount=Money.of("1.00", ngn),
                settlement_ref="x",
                evidence_ref="x",
                recorded_by_id=uuid.uuid4(),
                recorded_at=now,
            )
        draft = create_payroll_run(
            session,
            tenant_id=tenant_a,
            run_ref="PG-A4-CANCEL",
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            currency=ngn,
            created_by_id=uuid.uuid4(),
            created_at=now,
        )
        actor = uuid.uuid4()
        cancelled = cancel_payroll_run(
            session,
            tenant_id=tenant_a,
            run_id=draft.id,
            cancelled_by_id=actor,
            reason="duplicate run",
            cancelled_at=now,
        )
        assert (cancelled.status, cancelled.cancelled_by_id) == ("cancelled", actor)


def _alembic_config(case: ModuleCase):
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("version_locations", f"{KERNEL_VERSIONS} {case.versions}")
    return config


def test_payroll_settlement_reversal_on_postgres(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    """a5 reversal through the real migration, RLS role and append-only trigger."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from dotmac_kernel.money import Currency, Money
    from dotmac_payroll import (
        EmployeeComponentInput,
        PayComponentInput,
        PayrollConflict,
        PayrollRuleViolation,
        PayRuleInput,
        approve_payroll_run,
        assign_employee_pay_structure,
        build_settlement_reversal_consequence,
        calculate_employee_payroll,
        create_pay_component,
        create_pay_structure,
        create_payroll_run,
        finalize_payroll_run,
        publish_pay_structure_revision,
        record_liability_settlement,
        reverse_liability_settlement,
    )
    from dotmac_payroll.models import (
        PayrollLiability,
        PayrollLiabilitySettlement,
        PayrollSettlementReversal,
    )
    from sqlalchemy import select

    case, admin_url, app_url = migrated_module
    if case.manifest.code != "payroll":
        pytest.skip("payroll-only proof")
    tenant_a, tenant_b = _seed_tenants(admin_url)
    ngn = Currency("NGN", 2)
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)
    recorder, reverser = uuid.uuid4(), uuid.uuid4()

    def rule(component_id, sequence, **kw):
        return PayRuleInput(
            component_id, sequence, "input", None, None, (), False, **kw
        )

    def reverse(session, settlement, **kw):
        args = {
            "tenant_id": tenant_a,
            "settlement_id": settlement.id,
            "return_ref": "RET-PG-1",
            "reason": "account closed",
            "reversed_by_id": reverser,
            "reversed_at": now,
        }
        args.update(kw)
        return reverse_liability_settlement(session, **args)

    with _tenant_session(app_url, tenant_a) as session:
        base = create_pay_component(
            session,
            tenant_id=tenant_a,
            command=PayComponentInput("B", "Base", "earning", "sal", None, None),
        )
        loan = create_pay_component(
            session,
            tenant_id=tenant_a,
            command=PayComponentInput("L", "Loan", "deduction", None, "1410", "desk"),
        )
        structure = create_pay_structure(
            session, tenant_id=tenant_a, code="S", name="S", currency=ngn
        )
        revision = publish_pay_structure_revision(
            session,
            tenant_id=tenant_a,
            structure_id=structure.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            net_payable_account_ref="2040",
            rules=(
                rule(base.id, 1),
                rule(loan.id, 2, settlement_mode="internal_recovery"),
            ),
            published_by_id=uuid.uuid4(),
            published_at=now,
        )
        assign_employee_pay_structure(
            session,
            tenant_id=tenant_a,
            employee_ref="employee:pg-a5",
            revision_id=revision.id,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            source_ref="people:1",
            source_version="1",
        )
        run = create_payroll_run(
            session,
            tenant_id=tenant_a,
            run_ref="PG-A5",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            currency=ngn,
            created_by_id=uuid.uuid4(),
            created_at=now,
        )
        calculate_employee_payroll(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            employee_ref="employee:pg-a5",
            proration_factor=Decimal("1"),
            inputs=(
                EmployeeComponentInput(base.id, Money.of("1000.10", ngn), "c"),
                EmployeeComponentInput(loan.id, Money.of("200.05", ngn), "l"),
            ),
            calculated_at=now,
        )
        approve_payroll_run(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            approved_by_id=uuid.uuid4(),
            approved_at=now,
        )
        finalize_payroll_run(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            finalized_by_id=uuid.uuid4(),
            finalized_at=now,
        )
        liabilities = session.scalars(
            select(PayrollLiability).where(PayrollLiability.run_id == run.id)
        ).all()
        net = next(x for x in liabilities if x.liability_key == "employee-net-pay")
        recovery = next(
            x for x in liabilities if x.settlement_mode == "internal_recovery"
        )
        assert net.amount == Decimal("800.05")
        settlement = record_liability_settlement(
            session,
            tenant_id=tenant_a,
            liability_id=net.id,
            amount=Money.of("800.05", ngn),
            settlement_ref="PAY-PG-1",
            evidence_ref="bank:1",
            recorded_by_id=recorder,
            recorded_at=now,
        )
        # Reverser equal to the recorder is refused; nothing changes.
        with pytest.raises(PayrollRuleViolation, match="different person"):
            reverse(session, settlement, reversed_by_id=recorder)
        assert net.status == "settled"
        # Internal recovery refused even with a forged settlement row.
        forged = PayrollLiabilitySettlement(
            tenant_id=tenant_a,
            liability_id=recovery.id,
            amount=recovery.amount,
            settlement_ref="FORGED",
            evidence_ref="x",
            recorded_by_id=recorder,
            recorded_at=now,
        )
        session.add(forged)
        session.flush()
        with pytest.raises(PayrollRuleViolation, match="internal recovery"):
            reverse(session, forged)

        reversal = reverse(session, settlement)
        assert reversal.amount == Decimal("800.05")
        assert (net.status, net.amount_settled) == ("outstanding", Decimal("0"))
        # Replay no-op; different return reference conflicts.
        assert reverse(session, settlement).id == reversal.id
        with pytest.raises(PayrollConflict):
            reverse(session, settlement, return_ref="RET-OTHER")
        fact = build_settlement_reversal_consequence(
            session,
            tenant_id=tenant_a,
            reversal_id=reversal.id,
            bank_account_ref="bank:main",
            posting_date=date(2026, 8, 5),
        )
        assert (
            sum(e.debit for e in fact.entries)
            == sum(e.credit for e in fact.entries)
            == Decimal("800.05")
        )
        assert {e.account_ref: e.debit for e in fact.entries if e.debit} == {
            "bank:main": Decimal("800.05")
        }
        assert {e.account_ref: e.credit for e in fact.entries if e.credit} == {
            "2040": Decimal("800.05")
        }
        # Re-settleable under a new reference.
        again = record_liability_settlement(
            session,
            tenant_id=tenant_a,
            liability_id=net.id,
            amount=Money.of("800.05", ngn),
            settlement_ref="PAY-PG-2",
            evidence_ref="bank:2",
            recorded_by_id=recorder,
            recorded_at=now,
        )
        assert net.status == "settled" and again.id != settlement.id
        # A partially-settled liability cannot be reversed: reverse PAY-PG-2
        # then re-pay in two pieces.
        reverse(session, again, return_ref="RET-PG-2")
        part = record_liability_settlement(
            session,
            tenant_id=tenant_a,
            liability_id=net.id,
            amount=Money.of("300.00", ngn),
            settlement_ref="PAY-PG-3",
            evidence_ref="bank:3",
            recorded_by_id=recorder,
            recorded_at=now,
        )
        with pytest.raises(PayrollRuleViolation, match="fully settle"):
            reverse(session, part, return_ref="RET-PG-3")
        session.commit()

    # Append-only: the online role has no UPDATE/DELETE grant, and the evidence
    # trigger refuses even the owner role.
    with _tenant_session(app_url, tenant_a) as session:
        for statement in (
            "UPDATE mod_payroll.payroll_settlement_reversals SET reason = 'x'",
            "DELETE FROM mod_payroll.payroll_settlement_reversals",
        ):
            with pytest.raises(DBAPIError):
                session.execute(text(statement))
            session.rollback()
        # Tenant isolation: another tenant sees no reversals.
    with _tenant_session(app_url, tenant_b) as session:
        assert session.scalars(select(PayrollSettlementReversal)).all() == []
    admin = create_engine(admin_url)
    for statement in (
        "UPDATE mod_payroll.payroll_settlement_reversals SET reason = 'x'",
        "DELETE FROM mod_payroll.payroll_settlement_reversals",
    ):
        with pytest.raises(DBAPIError, match="append-only"), admin.begin() as conn:
            conn.execute(text(statement))
    admin.dispose()

    # Downgrade guard: refused while reversals exist (even though the schema
    # is otherwise unchanged), and the lineage is still at py_0003.
    from alembic import command

    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    with pytest.raises(RuntimeError, match="a5 downgrade refused"):
        command.downgrade(_alembic_config(case), "py_0002_mode_and_run_cancel")
    admin = create_engine(admin_url)
    with admin.connect() as conn:
        heads = conn.execute(text("SELECT version_num FROM public.alembic_version"))
        assert "py_0004_liability_adjustments" in heads.scalars().all()
    admin.dispose()


def test_payroll_a5_downgrade_succeeds_when_no_reversal_exists(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    from alembic import command

    case, admin_url, _ = migrated_module
    if case.manifest.code != "payroll":
        pytest.skip("payroll-only proof")
    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    config = _alembic_config(case)
    command.downgrade(config, "py_0002_mode_and_run_cancel")
    admin = create_engine(admin_url)
    with admin.connect() as conn:
        gone = conn.execute(
            text("SELECT to_regclass('mod_payroll.payroll_settlement_reversals')")
        ).scalar_one()
        assert gone is None
    admin.dispose()
    command.upgrade(config, "heads")


def test_payroll_a6_liability_adjustment_on_postgres(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    """Adjusted liability through real columns, CHECK, append-only and downgrade."""
    from datetime import UTC, date, datetime, timedelta
    from decimal import Decimal

    from dotmac_kernel.money import Currency, Money
    from dotmac_payroll import (
        EmployeeComponentInput,
        PayrollRuleViolation,
        adjust_liability,
        approve_payroll_run,
        assign_employee_pay_structure,
        build_liability_adjustment_consequence,
        build_run_accounting_consequence,
        calculate_employee_payroll,
        create_payroll_run,
        effective_liability_amount,
        finalize_payroll_run,
        record_liability_settlement,
    )
    from dotmac_payroll.models import PayrollLiability, PayrollLiabilityAdjustment
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from tests.unit.test_payroll_service import _configured_payroll

    case, admin_url, app_url = migrated_module
    if case.manifest.code != "payroll":
        pytest.skip("payroll-only proof")
    tenant_a, tenant_b = _seed_tenants(admin_url)
    ngn = Currency("NGN", 2)
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)
    later = datetime.now(UTC) + timedelta(minutes=5)
    actor = uuid.uuid4()
    with _tenant_session(app_url, tenant_a) as session:
        base, _, deduction, _, revision, _ = _configured_payroll(session, tenant_a)
        assign_employee_pay_structure(
            session,
            tenant_id=tenant_a,
            employee_ref="employee:pg-a6",
            revision_id=revision.id,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            source_ref="people-assignment:6",
            source_version="1",
        )
        run = create_payroll_run(
            session,
            tenant_id=tenant_a,
            run_ref="PG-A6",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            currency=ngn,
            created_by_id=uuid.uuid4(),
            created_at=now,
        )
        calculate_employee_payroll(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            employee_ref="employee:pg-a6",
            proration_factor=Decimal("1"),
            inputs=(
                EmployeeComponentInput(base.id, Money.of("1000.10", ngn), "c"),
                EmployeeComponentInput(deduction.id, Money.of("100.05", ngn), "t"),
            ),
            calculated_at=now,
        )
        approve_payroll_run(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            approved_by_id=uuid.uuid4(),
            approved_at=now,
        )
        finalize_payroll_run(
            session,
            tenant_id=tenant_a,
            run_id=run.id,
            finalized_by_id=uuid.uuid4(),
            finalized_at=now,
        )
        net = session.scalars(
            select(PayrollLiability).where(
                PayrollLiability.run_id == run.id,
                PayrollLiability.liability_key == "employee-net-pay",
            )
        ).one()
        original = net.amount
        before = build_run_accounting_consequence(
            session, tenant_id=tenant_a, run_id=run.id
        )
        row = adjust_liability(
            session,
            tenant_id=tenant_a,
            liability_id=net.id,
            amount=Money.of("-100.05", ngn),
            adjustment_ref="PG-ADJ-1",
            reason="unclaimed",
            evidence_ref="memo:1",
            offset_account_ref="ledger:unclaimed",
            adjusted_by_id=actor,
            adjusted_at=later,
        )
        assert (net.amount, net.amount_adjusted) == (original, Decimal("-100.05"))
        assert effective_liability_amount(net) == original - Decimal("100.05")
        assert (
            build_run_accounting_consequence(session, tenant_id=tenant_a, run_id=run.id)
            == before
        )
        fact = build_liability_adjustment_consequence(
            session,
            tenant_id=tenant_a,
            adjustment_id=row.id,
            posting_date=date(2026, 9, 1),
        )
        assert {e.account_ref: e.debit for e in fact.entries if e.debit} == {
            net.liability_account_ref: Decimal("100.05")
        }
        with pytest.raises(PayrollRuleViolation, match="exceeds the outstanding"):
            record_liability_settlement(
                session,
                tenant_id=tenant_a,
                liability_id=net.id,
                amount=Money.of(str(original), ngn),
                settlement_ref="PG-PAY-FULL",
                evidence_ref="bank:1",
                recorded_by_id=actor,
                recorded_at=now,
            )
        record_liability_settlement(
            session,
            tenant_id=tenant_a,
            liability_id=net.id,
            amount=Money.of(str(effective_liability_amount(net)), ngn),
            settlement_ref="PG-PAY-EFF",
            evidence_ref="bank:2",
            recorded_by_id=actor,
            recorded_at=now,
        )
        assert net.status == "settled"
        session.commit()
        # The DB CHECK backstops the service: settled above effective is refused.
        net.amount_settled = original
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    with _tenant_session(app_url, tenant_b) as session:
        assert session.scalars(select(PayrollLiabilityAdjustment)).all() == []
    with _tenant_session(app_url, tenant_a) as session:
        assert len(session.scalars(select(PayrollLiabilityAdjustment)).all()) == 1
        for statement in (
            "UPDATE mod_payroll.payroll_liability_adjustments SET reason = 'x'",
            "DELETE FROM mod_payroll.payroll_liability_adjustments",
        ):
            with pytest.raises(DBAPIError):
                session.execute(text(statement))
            session.rollback()
    admin = create_engine(admin_url)
    for statement in (
        "UPDATE mod_payroll.payroll_liability_adjustments SET reason = 'x'",
        "DELETE FROM mod_payroll.payroll_liability_adjustments",
    ):
        with pytest.raises(DBAPIError, match="append-only"), admin.begin() as conn:
            conn.execute(text(statement))
    admin.dispose()

    from alembic import command

    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    with pytest.raises(RuntimeError, match="a6 downgrade refused"):
        command.downgrade(_alembic_config(case), "py_0003_settlement_reversal")


def test_payroll_a6_downgrade_succeeds_when_no_adjustment_exists(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    from alembic import command

    case, admin_url, _ = migrated_module
    if case.manifest.code != "payroll":
        pytest.skip("payroll-only proof")
    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    config = _alembic_config(case)
    command.downgrade(config, "py_0003_settlement_reversal")
    admin = create_engine(admin_url)
    with admin.connect() as conn:
        assert (
            conn.execute(
                text("SELECT to_regclass('mod_payroll.payroll_liability_adjustments')")
            ).scalar_one()
            is None
        )
        cols = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE "
                "table_schema='mod_payroll' AND table_name='payroll_liabilities' "
                "AND column_name='amount_adjusted'"
            )
        ).scalar_one()
        assert cols == 0
    admin.dispose()
    command.upgrade(config, "heads")
