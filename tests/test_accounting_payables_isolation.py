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
