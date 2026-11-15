"""PostgreSQL RLS and immutable-evidence canaries for both finance owners."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_accounting.manifest import module as accounting_module
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_payables.manifest import module as payables_module
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ACCOUNTING_VERSIONS = (
    REPO_ROOT / "packages/dotmac-accounting/src/dotmac_accounting/migrations/versions"
)
PAYABLES_VERSIONS = (
    REPO_ROOT / "packages/dotmac-payables/src/dotmac_payables/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — finance RLS needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_finance() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"accounting_payables_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ACCOUNTING_VERSIONS} {PAYABLES_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        for tenant, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id,:slug,:name)"
                ),
                {"id": tenant, "slug": slug, "name": slug.title()},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_accounting.account_categories "
                    "(id,tenant_id,code,name,account_class,is_active) "
                    "VALUES (:id,:tenant,:code,:name,'ASSET',true)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "code": f"{slug}-asset",
                    "name": "Assets",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_payables.supplier_invoices "
                    "(id,tenant_id,number,supplier_ref,supplier_name_snapshot,"
                    "supplier_document_number,invoice_date,received_date,currency_code,"
                    "exchange_rate,liability_account_ref,subtotal,tax_amount,total_amount,"
                    "payment_schedule,request_fingerprint,status,created_at) "
                    "VALUES (:id,:tenant,:number,:supplier,'Supplier',:external,"
                    "DATE '2026-08-01',"
                    "DATE '2026-08-02','NGN',1,'account:2100',100,0,100,"
                    '\'[{"due_date":"2026-08-31","amount":"100"}]\'::jsonb,'
                    ":fingerprint,'DRAFT',now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "number": f"{slug}-inv",
                    "supplier": f"party:{slug}",
                    "external": f"ext-{slug}",
                    "fingerprint": "a" * 64,
                },
            )
    engine.dispose()
    return tenant_a, tenant_b


def _append_draft_ledger_evidence(
    conn: Connection, *, tenant: uuid.UUID, period: uuid.UUID
) -> uuid.UUID:
    category = conn.scalar(
        text(
            "SELECT id FROM mod_accounting.account_categories "
            "WHERE tenant_id=:tenant ORDER BY code LIMIT 1"
        ),
        {"tenant": tenant},
    )
    assert category is not None
    account, journal = uuid.uuid4(), uuid.uuid4()
    debit_line, credit_line = uuid.uuid4(), uuid.uuid4()
    debit_ledger, credit_ledger = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_accounting.accounts "
            "(id,tenant_id,category_id,code,name,kind,normal_balance,"
            "is_active,posting_allowed) VALUES "
            "(:id,:tenant,:category,'1000','Evidence','POSTING','DEBIT',true,true)"
        ),
        {"id": account, "tenant": tenant, "category": category},
    )
    conn.execute(
        text(
            "INSERT INTO mod_accounting.journal_entries "
            "(id,tenant_id,fiscal_period_id,number,kind,status,entry_date,"
            "posting_date,description,currency_code,exchange_rate,total_debit,"
            "total_credit,total_debit_functional,total_credit_functional,"
            "source_owner,source_document_kind,source_document_id,source_version,"
            "source_fingerprint,request_fingerprint) VALUES "
            "(:id,:tenant,:period,'EVIDENCE-1','STANDARD','DRAFT',DATE '2026-08-19',"
            "DATE '2026-08-19','Evidence canary','NGN',1,100,100,100,100,"
            "'test','canary','1','1',:fingerprint,:fingerprint)"
        ),
        {
            "id": journal,
            "tenant": tenant,
            "period": period,
            "fingerprint": "b" * 64,
        },
    )
    conn.execute(
        text(
            "INSERT INTO mod_accounting.journal_lines "
            "(id,tenant_id,journal_id,line_number,account_id,debit,credit,"
            "debit_functional,credit_functional) VALUES "
            "(:debit_line,:tenant,:journal,1,:account,100,0,100,0),"
            "(:credit_line,:tenant,:journal,2,:account,0,100,0,100)"
        ),
        {
            "debit_line": debit_line,
            "credit_line": credit_line,
            "tenant": tenant,
            "journal": journal,
            "account": account,
        },
    )
    statement = text(
        "INSERT INTO mod_accounting.posted_ledger_lines "
        "(id,tenant_id,journal_id,journal_line_id,fiscal_period_id,account_id,"
        "account_code,journal_number,entry_date,posting_date,currency_code,"
        "debit,credit,original_debit,original_credit,exchange_rate,source_owner,"
        "source_document_kind,source_document_id,source_version,source_fingerprint,"
        "posted_by,posted_at) VALUES "
        "(:id,:tenant,:journal,:line,:period,:account,'1000','EVIDENCE-1',"
        "DATE '2026-08-19',DATE '2026-08-19','NGN',:debit,:credit,:debit,:credit,"
        "1,'test','canary','1','1',:fingerprint,'user:test',now())"
    )
    common = {
        "tenant": tenant,
        "journal": journal,
        "period": period,
        "account": account,
        "fingerprint": "b" * 64,
    }
    conn.execute(
        statement,
        [
            {
                **common,
                "id": debit_ledger,
                "line": debit_line,
                "debit": 100,
                "credit": 0,
            },
            {
                **common,
                "id": credit_ledger,
                "line": credit_line,
                "debit": 0,
                "credit": 100,
            },
        ],
    )
    return debit_ledger


def test_live_catalog_and_online_reads_are_tenant_isolated(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_finance
    registry = NamespaceRegistry.from_manifests([accounting_module, payables_module])
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert audit_live_schemas(conn, registry) == ()
    engine.dispose()
    tenant_a, tenant_b = _seed_tenants(admin_url)
    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant_a)},
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM mod_accounting.account_categories"))
            == 1
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM mod_payables.supplier_invoices"))
            == 1
        )
        with pytest.raises(DBAPIError):
            conn.execute(
                text(
                    "INSERT INTO mod_accounting.account_categories "
                    "(id,tenant_id,code,name,account_class,is_active) "
                    "VALUES (:id,:tenant,'forged','Forged','ASSET',true)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_b},
            )
    app.dispose()


def test_database_refuses_evidence_rewrite_even_for_admin(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        transaction = conn.begin()
        year, period, event = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO mod_accounting.fiscal_years "
                "(id,tenant_id,code,name,start_date,end_date,created_at) "
                "VALUES (:id,:tenant,'FY26','FY 2026',DATE '2026-01-01',"
                "DATE '2026-12-31',now())"
            ),
            {"id": year, "tenant": tenant},
        )
        conn.execute(
            text(
                "INSERT INTO mod_accounting.fiscal_periods "
                "(id,tenant_id,fiscal_year_id,period_number,name,start_date,"
                "end_date,is_adjustment,status,created_at) "
                "VALUES (:id,:tenant,:year,8,'August',DATE '2026-08-01',"
                "DATE '2026-08-31',false,'FUTURE',now())"
            ),
            {"id": period, "tenant": tenant, "year": year},
        )
        conn.execute(
            text(
                "UPDATE mod_accounting.fiscal_periods SET status='OPEN' "
                "WHERE tenant_id=:tenant AND id=:period"
            ),
            {"tenant": tenant, "period": period},
        )
        conn.execute(
            text(
                "INSERT INTO mod_accounting.period_events "
                "(id,tenant_id,period_id,event_kind,from_status,to_status,actor_ref,"
                "approval_reference,evidence,occurred_at) "
                "VALUES (:id,:tenant,:period,'open','FUTURE','OPEN','user:test',"
                "NULL,'{}'::jsonb,now())"
            ),
            {"id": event, "tenant": tenant, "period": period},
        )
        ledger = _append_draft_ledger_evidence(conn, tenant=tenant, period=period)
        with pytest.raises(DBAPIError), conn.begin_nested():
            conn.execute(
                text(
                    "UPDATE mod_accounting.posted_ledger_lines "
                    "SET description='rewritten' WHERE id=:id"
                ),
                {"id": ledger},
            )
        with pytest.raises(DBAPIError), conn.begin_nested():
            conn.execute(
                text(
                    "UPDATE mod_accounting.period_events "
                    "SET actor_ref='rewritten' WHERE id=:id"
                ),
                {"id": event},
            )
        liability_event = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO mod_payables.liability_events "
                "(id,tenant_id,event_kind,document_kind,document_id,supplier_ref,"
                "currency_code,amount,source_reference,source_fingerprint,occurred_at) "
                "VALUES (:id,:tenant,'invoice_recognized','supplier_invoice',"
                ":document,'party:alpha','NGN',100,'approval:test',:fingerprint,now())"
            ),
            {
                "id": liability_event,
                "tenant": tenant,
                "document": uuid.uuid4(),
                "fingerprint": "c" * 64,
            },
        )
        with pytest.raises(DBAPIError), conn.begin_nested():
            conn.execute(
                text(
                    "UPDATE mod_payables.liability_events "
                    "SET amount=999 WHERE id=:id"
                ),
                {"id": liability_event},
            )
        transaction.rollback()
    engine.dispose()


def test_empty_historical_receipt_migration_can_downgrade(
    migrated_finance: tuple[str, str],
) -> None:
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = import_module(
        "dotmac_payables.migrations.versions.pa_0003_historical_invoice"
    )
    engine = create_engine(migrated_finance[0])
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert (
            conn.scalar(
                text("SELECT to_regclass('mod_payables.historical_invoice_receipts')")
            )
            is None
        )
    engine.dispose()


def test_historical_admission_concurrency_rls_fk_and_admin_immutability(
    migrated_finance: tuple[str, str],
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import fields, replace
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from dotmac_kernel.cache import TenantScope
    from dotmac_payables import (
        Conflict,
        HistoricalInvoiceReceipt,
        HistoricalUnpaidInvoiceCommand,
        HistoricalUnpaidInvoiceEvidence,
        LiabilityEvent,
        PaymentObligation,
        SupplierInvoice,
        admit_historical_unpaid_invoice,
    )
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    admin_url, app_url = migrated_finance
    tenant_a, tenant_b = _seed_tenants(admin_url)
    admin = create_engine(admin_url)
    app = create_engine(app_url)
    now = datetime(2026, 11, 11, tzinfo=UTC)
    with Session(admin) as db, db.begin():
        invoice = db.scalar(
            select(SupplierInvoice).where(SupplierInvoice.tenant_id == tenant_a)
        )
        assert invoice is not None
        invoice.status = "SUBMITTED"
        db.flush()
        invoice.status = "APPROVED"
        db.flush()
        invoice_id = invoice.id
        supplied = {
            "tenant_id": tenant_a,
            "invoice_id": invoice_id,
            "source_owner": "legacy",
            "source_invoice_id": "original:1",
            "source_snapshot_sha256": "a" * 64,
            "source_status": "POSTED",
            "invoice_type": "STANDARD",
            "paid_amount": Decimal(0),
            "allocation_count": 0,
            "withholding_tax_amount": Decimal(0),
            "prepayment_amount": Decimal(0),
            "due_date": date(2026, 8, 31),
            "original_posted_at": now,
            "source_journal_ref": "source:journal",
            "destination_journal_ref": "destination:journal",
            "accounting_evidence_fingerprint": "cv1:" + "b" * 64,
            "facts_fingerprint": "cv1:" + "c" * 64,
        }
        evidence = HistoricalUnpaidInvoiceEvidence(
            **{
                field.name: supplied[field.name]
                if field.name in supplied
                else getattr(invoice, field.name)
                for field in fields(HistoricalUnpaidInvoiceEvidence)
            }
        )
    command = HistoricalUnpaidInvoiceCommand(
        "legacy", "original:1", invoice_id, "a" * 64, evidence.fingerprint()
    )

    class Verifier:
        def verify(self, *, scope, command, destination):
            return evidence

    def admit(key):
        with Session(app) as db, db.begin():
            db.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_a)},
            )
            receipt = admit_historical_unpaid_invoice(
                db,
                scope=TenantScope(tenant_a),
                command=command,
                verifier=Verifier(),
                idempotency_key=key,
                idempotency_expires_at=None,
                admitted_at=now,
            )
            db.refresh(receipt)
            event = db.get(LiabilityEvent, receipt.event_id)
            assert event is not None
            for instant in (receipt.original_posted_at, event.occurred_at):
                assert instant.tzinfo is not None
                assert instant.utcoffset() is not None
                assert instant.astimezone(UTC) == now
            return receipt.id

    # Independent keys contend on the permanent invoice identity.
    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = list(pool.map(admit, ("concurrent-a", "concurrent-b")))
    assert winners[0] == winners[1]
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_b)},
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM mod_payables.historical_invoice_receipts")
            )
            == 0
        )
        with pytest.raises(DBAPIError), conn.begin_nested():
            conn.execute(
                text(
                    "UPDATE mod_payables.historical_invoice_receipts "
                    "SET source_owner='forged'"
                )
            )
    with admin.begin() as conn:
        from importlib import import_module

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        migration = import_module(
            "dotmac_payables.migrations.versions.pa_0003_historical_invoice"
        )
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="forward correction"):
                migration.downgrade()
        assert (
            conn.scalar(
                text(
                    "SELECT length(fingerprint) FROM public.idempotency_records "
                    "WHERE scope='payables.admit_historical_unpaid_invoice' LIMIT 1"
                )
            )
            == 64
        )
        assert (
            conn.scalar(
                text(
                    "SELECT length(evidence_fingerprint) "
                    "FROM mod_payables.historical_invoice_receipts LIMIT 1"
                )
            )
            == 68
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM mod_payables.historical_invoice_receipts")
            )
            == 1
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM mod_payables.payment_obligations"))
            == 1
        )
        for mutation in (
            "UPDATE mod_payables.historical_invoice_receipts SET source_owner='forged'",
            "DELETE FROM mod_payables.historical_invoice_receipts",
        ):
            with pytest.raises(DBAPIError), conn.begin_nested():
                conn.execute(text(mutation))
        invoice_b = conn.scalar(
            select(SupplierInvoice.id).where(SupplierInvoice.tenant_id == tenant_b)
        )
        obligation_b, event_b = uuid.uuid4(), uuid.uuid4()
        conn.execute(
            PaymentObligation.__table__.insert().values(
                id=obligation_b,
                tenant_id=tenant_b,
                invoice_id=invoice_b,
                sequence=1,
                due_date=date(2026, 8, 31),
                currency_code="NGN",
                original_amount=100,
                outstanding_amount=100,
                status="OPEN",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            LiabilityEvent.__table__.insert().values(
                id=event_b,
                tenant_id=tenant_b,
                obligation_id=obligation_b,
                event_kind="historical_invoice_recognized",
                document_kind="supplier_invoice",
                document_id=invoice_b,
                supplier_ref="party:bravo",
                currency_code="NGN",
                amount=100,
                source_reference="bravo",
                source_fingerprint="a" * 64,
                occurred_at=now,
            )
        )
        original = dict(
            conn.execute(select(HistoricalInvoiceReceipt.__table__)).mappings().one()
        )
        valid = {
            **original,
            "id": uuid.uuid4(),
            "tenant_id": tenant_b,
            "invoice_id": invoice_b,
            "obligation_id": obligation_b,
            "event_id": event_b,
        }
        # Exactly one reference crosses tenants per case; assert its own FK.
        for column in ("invoice_id", "obligation_id", "event_id"):
            with pytest.raises(DBAPIError) as caught, conn.begin_nested():
                conn.execute(
                    HistoricalInvoiceReceipt.__table__.insert().values(
                        **{**valid, column: original[column]}
                    )
                )
            assert caught.value.orig.diag.constraint_name == (
                "fk_historical_invoice_receipts_" + column.removesuffix("_id")
            )
    race_evidence = {}
    with admin.begin() as conn:
        header = dict(
            conn.execute(
                select(SupplierInvoice.__table__).where(
                    SupplierInvoice.id == invoice_id
                )
            )
            .mappings()
            .one()
        )
        for sequence in (1, 2):
            candidate = {
                **header,
                "id": uuid.uuid4(),
                "number": f"race-{sequence}",
                "supplier_document_number": f"race-source-{sequence}",
            }
            conn.execute(
                SupplierInvoice.__table__.insert().values(
                    **{**candidate, "status": "DRAFT"}
                )
            )
            for state in ("SUBMITTED", "APPROVED"):
                conn.execute(
                    SupplierInvoice.__table__.update()
                    .where(SupplierInvoice.id == candidate["id"])
                    .values(status=state)
                )
            race_evidence[candidate["id"]] = replace(
                evidence,
                invoice_id=candidate["id"],
                number=candidate["number"],
                supplier_document_number=candidate["supplier_document_number"],
                source_invoice_id="shared-race-source",
            )

    class RaceVerifier:
        def verify(self, *, scope, command, destination):
            return race_evidence[command.invoice_id]

    def race_admit(candidate_id):
        candidate_evidence = race_evidence[candidate_id]
        candidate_command = replace(
            command,
            invoice_id=candidate_id,
            source_invoice_id="shared-race-source",
            evidence_fingerprint=candidate_evidence.fingerprint(),
        )
        with Session(app) as db, db.begin():
            db.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_a)},
            )
            try:
                admit_historical_unpaid_invoice(
                    db,
                    scope=TenantScope(tenant_a),
                    command=candidate_command,
                    verifier=RaceVerifier(),
                    idempotency_key=str(candidate_id),
                    idempotency_expires_at=None,
                    admitted_at=now,
                )
                return "admitted"
            except Conflict:
                assert (
                    db.scalar(text("SELECT public.app_current_tenant_id()")) == tenant_a
                )
                assert (
                    db.scalar(
                        select(SupplierInvoice.id).where(
                            SupplierInvoice.id == candidate_id
                        )
                    )
                    == candidate_id
                )
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(race_admit, race_evidence)) == ["admitted", "conflict"]
    with admin.connect() as conn:
        for model, column in (
            (HistoricalInvoiceReceipt, HistoricalInvoiceReceipt.invoice_id),
            (PaymentObligation, PaymentObligation.invoice_id),
            (LiabilityEvent, LiabilityEvent.document_id),
        ):
            assert (
                len(
                    list(
                        conn.scalars(select(model.id).where(column.in_(race_evidence)))
                    )
                )
                == 1
            )
    admin.dispose()
    app.dispose()


def _seed_payment(admin_url: str, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """A DRAFT supplier payment with one allocation; returns (payment, obligation)."""
    engine = create_engine(admin_url)
    invoice = uuid.uuid4()
    obligation, payment = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mod_payables.supplier_invoices "
                "(id,tenant_id,number,supplier_ref,supplier_name_snapshot,"
                "supplier_document_number,invoice_date,received_date,currency_code,"
                "exchange_rate,liability_account_ref,subtotal,tax_amount,total_amount,"
                "payment_schedule,request_fingerprint,status,created_at) VALUES "
                "(:id,:tenant,'PINV-PAY','party:s','S','D-1',DATE '2026-08-01',"
                "DATE '2026-08-02','NGN',1,'account:2000',100,0,100,"
                '\'[{"due_date":"2026-08-31","amount":"100"}]\'::jsonb,'
                ":fp,'DRAFT',now())"
            ),
            {"id": invoice, "tenant": tenant, "fp": "a" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO mod_payables.payment_obligations "
                "(id,tenant_id,invoice_id,sequence,due_date,currency_code,"
                "original_amount,outstanding_amount,status) VALUES "
                "(:id,:tenant,:invoice,1,DATE '2026-08-31','NGN',100,100,'OPEN')"
            ),
            {"id": obligation, "tenant": tenant, "invoice": invoice},
        )
        conn.execute(
            text(
                "INSERT INTO mod_payables.supplier_payments "
                "(id,tenant_id,number,supplier_ref,supplier_name_snapshot,"
                "currency_code,exchange_rate,value_date,cash_account_ref,"
                "payer_instrument_ref,gross_amount,wht_amount,net_amount,"
                "request_fingerprint,status,prepared_by,prepared_at) VALUES "
                "(:id,:tenant,'SPAY-1','party:s','S','NGN',1,DATE '2026-09-02',"
                "'account:1010','instrument:1',100,0,100,:fp,'DRAFT','user:p',now())"
            ),
            {"id": payment, "tenant": tenant, "fp": "a" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO mod_payables.supplier_payment_allocations "
                "(id,tenant_id,payment_id,line_number,obligation_id,amount,created_at) "
                "VALUES (:id,:tenant,:payment,1,:obligation,100,now())"
            ),
            {
                "id": uuid.uuid4(),
                "tenant": tenant,
                "payment": payment,
                "obligation": obligation,
            },
        )
    engine.dispose()
    return payment, obligation


def test_supplier_payment_lifecycle_is_enforced_by_the_database(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_finance
    tenant_a, tenant_b = _seed_tenants(admin_url)
    payment, _ = _seed_payment(admin_url, tenant_a)
    engine = create_engine(admin_url)

    def attempt(sql: str, **params: object) -> None:
        with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                conn.execute(text(sql), {"id": payment, **params})

    # Facts are frozen, delete is never allowed, skipping approval is refused.
    attempt("UPDATE mod_payables.supplier_payments SET gross_amount=1 WHERE id=:id")
    attempt("DELETE FROM mod_payables.supplier_payments WHERE id=:id")
    attempt(
        "UPDATE mod_payables.supplier_payments SET status='RECORDED',"
        "approved_by='user:a',approved_at=now(),approval_reference='r',"
        "recorded_by='user:r',recorded_at=now(),bank_reference='b' WHERE id=:id"
    )
    # Approver must not be the preparer (row check, even for the admin role).
    attempt(
        "UPDATE mod_payables.supplier_payments SET status='APPROVED',"
        "approved_by='USER:P',approved_at=now(),approval_reference='r' WHERE id=:id"
    )
    # Lines are append-only.
    attempt(
        "UPDATE mod_payables.supplier_payment_allocations SET amount=1 "
        "WHERE payment_id=:id"
    )
    attempt(
        "DELETE FROM mod_payables.supplier_payment_allocations WHERE payment_id=:id"
    )
    with engine.begin() as conn:  # the legal path
        conn.execute(
            text(
                "UPDATE mod_payables.supplier_payments SET status='APPROVED',"
                "approved_by='user:a',approved_at=now(),approval_reference='r' "
                "WHERE id=:id"
            ),
            {"id": payment},
        )
    # No new lines once the payment is no longer a draft.
    attempt(
        "INSERT INTO mod_payables.supplier_payment_withholdings "
        "(id,tenant_id,payment_id,line_number,account_ref,amount,created_at) "
        "VALUES (gen_random_uuid(),:tenant,:id,1,'account:2300',1,now())",
        tenant=tenant_a,
    )
    # Terminal states cannot move again.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_payables.supplier_payments SET status='CANCELLED',"
                "cancelled_by='user:c',cancelled_at=now(),cancel_reason='x' "
                "WHERE id=:id"
            ),
            {"id": payment},
        )
    attempt("UPDATE mod_payables.supplier_payments SET status='APPROVED' WHERE id=:id")
    engine.dispose()

    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant_b)},
        )
        for count_sql in (
            "SELECT count(*) FROM mod_payables.supplier_payments",
            "SELECT count(*) FROM mod_payables.supplier_payment_allocations",
            "SELECT count(*) FROM mod_payables.supplier_payment_withholdings",
        ):
            assert conn.scalar(text(count_sql)) == 0
    app.dispose()


def test_supplier_payment_migration_downgrade_refuses_once_populated(
    migrated_finance: tuple[str, str],
) -> None:
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    migration = import_module(
        "dotmac_payables.migrations.versions.pa_0004_supplier_payments"
    )
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        transaction = conn.begin()
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()  # empty: allowed
        assert (
            conn.scalar(text("SELECT to_regclass('mod_payables.supplier_payments')"))
            is None
        )
        transaction.rollback()
    _seed_payment(admin_url, tenant)
    with engine.connect() as conn:
        transaction = conn.begin()
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="forward correction"):
                migration.downgrade()
        transaction.rollback()
    engine.dispose()


def test_recorded_payment_and_approval_evidence_are_frozen_by_the_database(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    payment, _ = _seed_payment(admin_url, tenant)
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_payables.supplier_payments SET status='APPROVED',"
                "approved_by='user:a',approved_at=now(),approval_reference='r' "
                "WHERE id=:id"
            ),
            {"id": payment},
        )

    def refused(sql: str) -> None:
        with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                conn.execute(text(sql), {"id": payment})

    # A valid transition may not rewrite the approval evidence.
    refused(
        "UPDATE mod_payables.supplier_payments SET status='RECORDED',"
        "approved_by='user:z',recorded_by='user:r',recorded_at=now(),"
        "bank_reference='b' WHERE id=:id"
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_payables.supplier_payments SET status='RECORDED',"
                "recorded_by='user:r',recorded_at=now(),bank_reference='b' "
                "WHERE id=:id"
            ),
            {"id": payment},
        )
    for status_sql in (
        "UPDATE mod_payables.supplier_payments SET status='DRAFT' WHERE id=:id",
        "UPDATE mod_payables.supplier_payments SET status='APPROVED' WHERE id=:id",
        "UPDATE mod_payables.supplier_payments SET status='CANCELLED' WHERE id=:id",
        "UPDATE mod_payables.supplier_payments SET status='RECORDED' WHERE id=:id",
    ):
        refused(status_sql)
    refused(
        "UPDATE mod_payables.supplier_payments SET bank_reference='other' WHERE id=:id"
    )
    engine.dispose()


# --- concurrency (real PostgreSQL row locks) -----------------------------------


def _concurrency_fixture(admin_url: str):
    """Committed tenant + approved invoice (with receipt) + obligation of 1000."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from dotmac_kernel.cache import TenantScope
    from dotmac_payables import models as pm
    from dotmac_payables.contracts import (
        CreateSupplierInvoice,
        ObligationSchedule,
        PayableLineInput,
    )
    from dotmac_payables.service import approve_invoice, create_invoice, submit_invoice
    from sqlalchemy.orm import Session

    tenant, _ = _seed_tenants(admin_url)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    engine = create_engine(admin_url)
    with Session(engine) as db:
        scope = TenantScope(tenant)
        invoice = create_invoice(
            db,
            scope=scope,
            command=CreateSupplierInvoice(
                number="PINV-C-1",
                supplier_ref="party:s",
                supplier_name_snapshot="S",
                supplier_document_number="C-1",
                invoice_date=date(2026, 8, 1),
                received_date=date(2026, 8, 2),
                currency_code="NGN",
                exchange_rate=Decimal("1"),
                liability_account_ref="account:2000",
                lines=(
                    PayableLineInput(
                        description="x",
                        quantity=Decimal("1"),
                        unit_price=Decimal("1000"),
                        posting_account_ref="account:5100",
                    ),
                ),
                schedule=(ObligationSchedule(date(2026, 8, 31), Decimal("1000")),),
            ),
            idempotency_key="c:inv",
            idempotency_expires_at=None,
            recorded_at=now,
        )
        submit_invoice(
            db,
            scope=scope,
            invoice_id=invoice.id,
            submitted_by="u:ap",
            submitted_at=now,
        )
        approve_invoice(
            db,
            scope=scope,
            invoice_id=invoice.id,
            approval_reference="a",
            approved_by="u:c",
            idempotency_key="c:appr",
            idempotency_expires_at=None,
            approved_at=now,
        )
        db.add(
            pm.AccountingReceipt(
                tenant_id=tenant,
                document_kind="supplier_invoice",
                document_id=invoice.id,
                consequence_fingerprint="e" * 64,
                accounting_reference="j",
                accounting_evidence_fingerprint="f" * 64,
                recorded_at=now,
            )
        )
        obligation = db.scalars(
            __import__("sqlalchemy")
            .select(pm.PaymentObligation)
            .where(pm.PaymentObligation.invoice_id == invoice.id)
        ).one()
        obligation_id = obligation.id
        db.commit()
    engine.dispose()
    return tenant, obligation_id


def _payment_command(obligation_id: uuid.UUID, number: str):
    from datetime import date
    from decimal import Decimal

    from dotmac_payables.contracts import (
        CreateSupplierPayment,
        SupplierPaymentAllocationInput,
    )

    return CreateSupplierPayment(
        number=number,
        supplier_ref="party:s",
        supplier_name_snapshot="S",
        currency_code="NGN",
        exchange_rate=Decimal("1"),
        value_date=date(2026, 9, 2),
        cash_account_ref="account:1010",
        payer_instrument_ref="instrument:1",
        allocations=(SupplierPaymentAllocationInput(obligation_id, Decimal("1000")),),
        prepared_by="u:prep",
    )


def _run_pair(admin_url: str, first, second):
    """Run ``first`` (holds its transaction open briefly) and ``second``
    concurrently, each in its own session and transaction. Returns both outcomes
    as ('ok', value) or ('err', exception)."""
    import threading
    import time

    from sqlalchemy.orm import Session

    started = threading.Event()
    results: dict[str, tuple[str, object]] = {}

    def worker(name: str, fn, hold: bool) -> None:
        engine = create_engine(admin_url)
        try:
            with Session(engine) as db:
                try:
                    value = fn(db)
                    if hold:
                        started.set()
                        time.sleep(1.0)
                    db.commit()
                    results[name] = ("ok", value)
                except Exception as exc:
                    db.rollback()
                    results[name] = ("err", exc)
                    started.set()
        finally:
            engine.dispose()

    a = threading.Thread(target=worker, args=("first", first, True))
    a.start()
    started.wait(10)
    b = threading.Thread(target=worker, args=("second", second, False))
    b.start()
    a.join(30)
    b.join(30)
    return results["first"], results["second"]


def test_concurrent_creates_on_one_obligation_allow_exactly_one(
    migrated_finance: tuple[str, str],
) -> None:
    from datetime import UTC, datetime

    from dotmac_kernel.cache import TenantScope
    from dotmac_payables.contracts import InvalidAmount
    from dotmac_payables.service import create_supplier_payment

    admin_url, _ = migrated_finance
    tenant, obligation = _concurrency_fixture(admin_url)
    now = datetime(2026, 9, 1, tzinfo=UTC)

    def make(number: str):
        return lambda db: create_supplier_payment(
            db,
            scope=TenantScope(tenant),
            command=_payment_command(obligation, number),
            idempotency_key=f"k:{number}",
            idempotency_expires_at=None,
            prepared_at=now,
        ).id

    first, second = _run_pair(admin_url, make("SPAY-A"), make("SPAY-B"))
    assert first[0] == "ok"
    assert second[0] == "err" and isinstance(second[1], InvalidAmount)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert (
            conn.scalar(text("SELECT count(*) FROM mod_payables.supplier_payments"))
            == 1
        )
    engine.dispose()


def _approved_payment(admin_url: str, tenant, obligation, number="SPAY-R"):
    from datetime import UTC, datetime

    from dotmac_kernel.cache import TenantScope
    from dotmac_payables.service import (
        approve_supplier_payment,
        create_supplier_payment,
    )
    from sqlalchemy.orm import Session

    now = datetime(2026, 9, 1, tzinfo=UTC)
    engine = create_engine(admin_url)
    with Session(engine) as db:
        payment = create_supplier_payment(
            db,
            scope=TenantScope(tenant),
            command=_payment_command(obligation, number),
            idempotency_key=f"k:{number}",
            idempotency_expires_at=None,
            prepared_at=now,
        )
        pid = payment.id
        approve_supplier_payment(
            db,
            scope=TenantScope(tenant),
            payment_id=pid,
            approval_reference="ap",
            approved_by="u:appr",
            idempotency_key=f"a:{number}",
            idempotency_expires_at=None,
            approved_at=now,
        )
        db.commit()
    engine.dispose()
    return pid


def test_concurrent_recorders_settle_the_obligation_once(
    migrated_finance: tuple[str, str],
) -> None:
    from datetime import UTC, datetime

    from dotmac_kernel.cache import TenantScope
    from dotmac_payables.contracts import InvalidTransition
    from dotmac_payables.service import record_supplier_payment

    admin_url, _ = migrated_finance
    tenant, obligation = _concurrency_fixture(admin_url)
    pid = _approved_payment(admin_url, tenant, obligation)
    now = datetime(2026, 9, 2, tzinfo=UTC)

    def record(actor: str, ref: str):
        return lambda db: record_supplier_payment(
            db,
            scope=TenantScope(tenant),
            payment_id=pid,
            bank_reference=ref,
            recorded_by=actor,
            idempotency_key=f"r:{actor}",
            idempotency_expires_at=None,
            recorded_at=now,
        ).id

    first, second = _run_pair(
        admin_url, record("u:rec1", "BR-1"), record("u:rec2", "BR-2")
    )
    assert first[0] == "ok"
    assert second[0] == "err" and isinstance(second[1], InvalidTransition)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM mod_payables.settlement_observations")
            )
            == 1
        )
        assert (
            conn.scalar(
                text(
                    "SELECT outstanding_amount FROM mod_payables.payment_obligations "
                    "WHERE id=:id"
                ),
                {"id": obligation},
            )
            == 0
        )
    engine.dispose()


def test_approve_racing_cancel_leaves_a_consistent_reservation(
    migrated_finance: tuple[str, str],
) -> None:
    from datetime import UTC, datetime

    from dotmac_kernel.cache import TenantScope
    from dotmac_payables.service import (
        approve_supplier_payment,
        cancel_supplier_payment,
        create_supplier_payment,
    )
    from sqlalchemy.orm import Session

    admin_url, _ = migrated_finance
    tenant, obligation = _concurrency_fixture(admin_url)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    engine = create_engine(admin_url)
    with Session(engine) as db:
        pid = create_supplier_payment(
            db,
            scope=TenantScope(tenant),
            command=_payment_command(obligation, "SPAY-X"),
            idempotency_key="k:x",
            idempotency_expires_at=None,
            prepared_at=now,
        ).id
        db.commit()
    engine.dispose()

    first, second = _run_pair(
        admin_url,
        lambda db: approve_supplier_payment(
            db,
            scope=TenantScope(tenant),
            payment_id=pid,
            approval_reference="ap",
            approved_by="u:appr",
            idempotency_key="a:x",
            idempotency_expires_at=None,
            approved_at=now,
        ).status,
        lambda db: cancel_supplier_payment(
            db,
            scope=TenantScope(tenant),
            payment_id=pid,
            reason="race",
            cancelled_by="u:c",
            idempotency_key="c:x",
            idempotency_expires_at=None,
            cancelled_at=now,
        ).status,
    )
    assert first[0] == "ok"
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        status = conn.scalar(
            text("SELECT status FROM mod_payables.supplier_payments WHERE id=:id"),
            {"id": pid},
        )
        reserved = conn.scalar(
            text(
                "SELECT coalesce(sum(a.amount),0) FROM "
                "mod_payables.supplier_payment_allocations a JOIN "
                "mod_payables.supplier_payments p ON p.id=a.payment_id "
                "WHERE a.obligation_id=:o AND p.status IN ('DRAFT','APPROVED')"
            ),
            {"o": obligation},
        )
    engine.dispose()
    # Approve ran first, so cancel then moved APPROVED -> CANCELLED: the
    # reservation is released and the final state is terminal and consistent.
    assert second[0] == "ok"
    assert status == "CANCELLED"
    assert reserved == 0
