"""Real-PostgreSQL isolation and immutability canaries for dotmac-finance."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_finance.manifest import module
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
FINANCE_VERSIONS = (
    ROOT / "packages/dotmac-finance/src/dotmac_finance/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — finance RLS needs PostgreSQL")
    return url


def _url_for(base_url: str, database: str, *, user: str | None = None) -> str:
    prefix, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = prefix.partition("://")
        host = userhost.rpartition("@")[2]
        prefix = f"{scheme}://{user}@{host}"
    return f"{prefix}/{database}"


@pytest.fixture
def finance_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"finance_{uuid.uuid4().hex[:12]}"
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
    previous = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        config.set_main_option(
            "version_locations", f"{KERNEL_VERSIONS} {FINANCE_VERSIONS}"
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        if previous is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:name AND pid<>pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed(admin_url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b, book_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id,slug,name) "
                    "VALUES (:id,:slug,:name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
        for row_id, tenant_id in ((book_id, tenant_a), (uuid.uuid4(), tenant_b)):
            connection.execute(
                text(
                    "INSERT INTO mod_finance.asset_books "
                    "(id,tenant_id,asset_id,book_code,status,accounting_model,"
                    "depreciation_method,currency_code,minor_units,acquisition_cost,"
                    "gross_carrying_amount,accumulated_depreciation,"
                    "accumulated_impairment,carrying_amount,"
                    "unimpaired_carrying_amount,residual_value,useful_life_months,"
                    "depreciation_periods_taken,revaluation_reserve_balance,"
                    "prior_revaluation_loss_balance,impairment_loss_balance,"
                    "impairment_reserve_reduction_balance,available_for_use_on,"
                    "asset_account_ref,accumulated_depreciation_account_ref,"
                    "accumulated_impairment_account_ref,"
                    "depreciation_expense_account_ref,impairment_loss_account_ref,"
                    "revaluation_reserve_account_ref,disposal_gain_loss_account_ref,"
                    "source_ref,source_version,evidence_ref,version) VALUES "
                    "(:id,:tenant,:asset,'IFRS','active','cost','straight_line','NGN',2,"
                    "1000,1000,0,0,1000,1000,0,12,0,0,0,0,0,CURRENT_DATE,"
                    "'a','ad','ai','de','il','rr','dg','source','1','evidence',1)"
                ),
                {"id": row_id, "tenant": tenant_id, "asset": uuid.uuid4()},
            )
    engine.dispose()
    return tenant_a, tenant_b, book_id


def test_live_catalog_and_online_role_prove_tenant_isolation(
    finance_database: tuple[str, str],
) -> None:
    admin_url, online_url = finance_database
    tenant_a, tenant_b, _ = _seed(admin_url)
    registry = NamespaceRegistry.from_manifests([module])
    admin = create_engine(admin_url)
    with admin.connect() as connection:
        assert audit_live_schemas(connection, registry) == ()
    admin.dispose()
    online = create_engine(online_url)
    with online.connect() as connection:
        connection.execute(
            text("SELECT set_config('app.current_tenant',:tenant,false)"),
            {"tenant": str(tenant_b)},
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM mod_finance.asset_books")
            ).scalar_one()
            == 1
        )
        connection.execute(
            text("SELECT set_config('app.current_tenant',:tenant,false)"),
            {"tenant": str(tenant_a)},
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM mod_finance.asset_books")
            ).scalar_one()
            == 1
        )
    online.dispose()


def test_cross_tenant_child_relation_and_evidence_rewrite_are_impossible(
    finance_database: tuple[str, str],
) -> None:
    admin_url, _ = finance_database
    tenant_a, tenant_b, book_id = _seed(admin_url)
    engine = create_engine(admin_url)
    with engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text(
                "INSERT INTO mod_finance.accounting_events "
                "(id,tenant_id,book_id,sequence,event_type,effective_on,source_ref,"
                "source_version,evidence_ref,carrying_amount_before,"
                "carrying_amount_after,event_data,occurred_at) VALUES "
                "(:id,:tenant,:book,1,'capitalized',CURRENT_DATE,'x','1','e',"
                "1000,1000,'{}',now())"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_b, "book": book_id},
        )

    event_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mod_finance.accounting_events "
                "(id,tenant_id,book_id,sequence,event_type,effective_on,source_ref,"
                "source_version,evidence_ref,carrying_amount_before,"
                "carrying_amount_after,event_data,occurred_at) VALUES "
                "(:id,:tenant,:book,1,'capitalized',CURRENT_DATE,'x','1','e',"
                "1000,1000,'{}',now())"
            ),
            {"id": event_id, "tenant": tenant_a, "book": book_id},
        )
    with engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text(
                "UPDATE mod_finance.accounting_events SET evidence_ref='rewritten' "
                "WHERE id=:id"
            ),
            {"id": event_id},
        )
    engine.dispose()


def test_rls_canary_is_sensitive_to_a_disabled_guard(
    finance_database: tuple[str, str],
) -> None:
    admin_url, online_url = finance_database
    tenant_a, _, _ = _seed(admin_url)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    online = create_engine(online_url)
    try:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_finance.asset_books DISABLE ROW LEVEL SECURITY")
            )
        with online.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant',:tenant,false)"),
                {"tenant": str(tenant_a)},
            )
            visible = (
                connection.execute(
                    text("SELECT tenant_id FROM mod_finance.asset_books")
                )
                .scalars()
                .all()
            )
        assert len(visible) == 2 and tenant_a in visible
    finally:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_finance.asset_books ENABLE ROW LEVEL SECURITY")
            )
            connection.execute(
                text("ALTER TABLE mod_finance.asset_books FORCE ROW LEVEL SECURITY")
            )
        admin.dispose()
        online.dispose()
