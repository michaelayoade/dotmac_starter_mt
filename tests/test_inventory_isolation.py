"""PostgreSQL RLS and cross-tenant canaries for dotmac-inventory."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
INVENTORY_VERSIONS = (
    REPO_ROOT / "packages/dotmac-inventory/src/dotmac_inventory/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the inventory canary needs Postgres")
    return url


def _url_for(base_url: str, database: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{database}"


@pytest.fixture
def inventory_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"inventory_{uuid.uuid4().hex[:12]}"
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
    previous_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {INVENTORY_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        if previous_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous_url
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


def _seed(admin_url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    item_a = uuid.uuid4()
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
        connection.execute(
            text(
                "INSERT INTO mod_inventory.items "
                "(id, tenant_id, sku, name, base_uom, costing_method, "
                "track_lots, track_serials, is_active) VALUES "
                "(:a, :ta, 'SKU-1', 'Alpha item', 'each', "
                "'weighted_average', false, false, true), "
                "(:b, :tb, 'SKU-1', 'Bravo item', 'each', "
                "'weighted_average', false, false, true)"
            ),
            {"a": item_a, "ta": tenant_a, "b": uuid.uuid4(), "tb": tenant_b},
        )
    engine.dispose()
    return tenant_a, tenant_b, item_a


def test_a_tenant_sees_only_its_items(inventory_database: tuple[str, str]) -> None:
    admin_url, app_user_url = inventory_database
    tenant_a, tenant_b, _ = _seed(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            assert connection.execute(
                text("SELECT tenant_id FROM mod_inventory.items")
            ).scalars().all() == [tenant_a]
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_b)},
            )
            assert connection.execute(
                text("SELECT tenant_id FROM mod_inventory.items")
            ).scalars().all() == [tenant_b]
    finally:
        engine.dispose()


def test_cross_tenant_balance_reference_is_impossible(
    inventory_database: tuple[str, str],
) -> None:
    admin_url, _ = inventory_database
    _, tenant_b, item_a = _seed(admin_url)
    engine = create_engine(admin_url)
    try:
        with engine.begin() as connection:
            warehouse_b = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO mod_inventory.warehouses "
                    "(id, tenant_id, code, name, allows_receipts, "
                    "allows_issues, is_active) VALUES "
                    "(:id, :tenant, 'B', 'Bravo', true, true, true)"
                ),
                {"id": warehouse_b, "tenant": tenant_b},
            )
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        "INSERT INTO mod_inventory.stock_balances "
                        "(id, tenant_id, item_id, warehouse_id, "
                        "quantity_on_hand, quantity_reserved, total_value, "
                        "current_unit_cost) VALUES "
                        "(:id, :tenant, :item, :warehouse, 0, 0, 0, 0)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant_b,
                        "item": item_a,
                        "warehouse": warehouse_b,
                    },
                )
    finally:
        engine.dispose()


def test_stock_movement_evidence_cannot_be_rewritten(
    inventory_database: tuple[str, str],
) -> None:
    admin_url, _ = inventory_database
    tenant_id, _, item_id = _seed(admin_url)
    warehouse_id = uuid.uuid4()
    movement_id = uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO mod_inventory.warehouses "
                    "(id, tenant_id, code, name, allows_receipts, "
                    "allows_issues, is_active) VALUES "
                    "(:id, :tenant, 'A', 'Alpha', true, true, true)"
                ),
                {"id": warehouse_id, "tenant": tenant_id},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inventory.stock_movements "
                    "(id, tenant_id, movement_group_id, kind, item_id, "
                    "warehouse_id, quantity_delta, unit_cost, value_delta, "
                    "cost_variance, quantity_after, value_after, currency_code, "
                    "source_ref, occurred_at) VALUES "
                    "(:id, :tenant, :group, 'receipt', :item, :warehouse, "
                    "1, 10, 10, 0, 1, 10, 'NGN', 'canary:receipt', now())"
                ),
                {
                    "id": movement_id,
                    "tenant": tenant_id,
                    "group": uuid.uuid4(),
                    "item": item_id,
                    "warehouse": warehouse_id,
                },
            )
        with engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"):
                connection.execute(
                    text(
                        "UPDATE mod_inventory.stock_movements "
                        "SET value_delta = 999 WHERE id = :id"
                    ),
                    {"id": movement_id},
                )
    finally:
        engine.dispose()


def test_rls_canary_detects_a_disabled_guard(
    inventory_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = inventory_database
    tenant_a, _, _ = _seed(admin_url)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    online = create_engine(app_user_url)
    try:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_inventory.items DISABLE ROW LEVEL SECURITY")
            )
        with online.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            visible = (
                connection.execute(
                    text("SELECT tenant_id FROM mod_inventory.items ORDER BY tenant_id")
                )
                .scalars()
                .all()
            )
        assert len(visible) == 2 and tenant_a in visible
    finally:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_inventory.items ENABLE ROW LEVEL SECURITY")
            )
            connection.execute(
                text("ALTER TABLE mod_inventory.items FORCE ROW LEVEL SECURITY")
            )
        online.dispose()
        admin.dispose()
