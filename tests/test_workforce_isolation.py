"""PostgreSQL RLS canary for workforce scheduling and dispatch."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
VERSIONS = (
    ROOT / "packages/dotmac-workforce" / "src/dotmac_workforce/migrations/versions"
)
TABLES = (
    "workforce_teams",
    "workforce_skills",
    "team_memberships",
    "worker_skills",
    "workforce_shifts",
    "workforce_availability",
    "dispatch_decisions",
)


def _url(base: str, database: str, user: str | None = None) -> str:
    prefix, _, _ = base.rpartition("/")
    if user:
        scheme, _, host = prefix.partition("://")
        prefix = f"{scheme}://{user}@{host.rpartition('@')[2]}"
    return f"{prefix}/{database}"


@pytest.fixture
def migrated() -> Iterator[tuple[str, str]]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    if not superuser:
        pytest.skip("PostgreSQL URL required")
    name = f"workforce_{uuid.uuid4().hex[:11]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin = _url(superuser, name, "app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
        os.environ["MIGRATION_DATABASE_URL"] = admin
        command.upgrade(cfg, "heads")
        yield admin, _url(superuser, name, "app_user")
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


def test_workforce_rows_are_cross_tenant_isolated(
    migrated: tuple[str, str],
) -> None:
    admin_url, app_url = migrated
    tenants = (uuid.uuid4(), uuid.uuid4())
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        for index, tenant in enumerate(tenants):
            team, skill, shift = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            values = {
                "tenant": tenant,
                "team": team,
                "skill": skill,
                "shift": shift,
                "worker": f"party:worker-{index}",
            }
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:tenant, :slug, :slug)"
                ),
                {"tenant": tenant, "slug": f"workforce-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_workforce.workforce_teams "
                    "(id, tenant_id, code, name, active) VALUES "
                    "(:team, :tenant, :code, 'Fiber', true)"
                ),
                values | {"code": f"fiber-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_workforce.workforce_skills "
                    "(id, tenant_id, code, name) VALUES "
                    "(:skill, :tenant, :code, 'Splicing')"
                ),
                values | {"code": f"splice-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_workforce.team_memberships "
                    "(id, tenant_id, team_id, worker_reference, active, joined_at) "
                    "VALUES (:id, :tenant, :team, :worker, true, now())"
                ),
                values | {"id": uuid.uuid4()},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_workforce.worker_skills "
                    "(id, tenant_id, worker_reference, skill_id, proficiency, "
                    "verified_at) VALUES "
                    "(:id, :tenant, :worker, :skill, 4, now())"
                ),
                values | {"id": uuid.uuid4()},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_workforce.workforce_shifts "
                    "(id, tenant_id, team_id, starts_at, ends_at, capacity) "
                    "VALUES (:shift, :tenant, :team, now(), "
                    "now() + interval '8 hours', 2)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO mod_workforce.workforce_availability "
                    "(id, tenant_id, worker_reference, starts_at, ends_at, "
                    "available, source_reference) VALUES "
                    "(:id, :tenant, :worker, now(), now() + interval '8 hours', "
                    "true, :source)"
                ),
                values | {"id": uuid.uuid4(), "source": f"schedule:{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_workforce.dispatch_decisions "
                    "(id, tenant_id, work_reference, team_id, worker_reference, "
                    "required_skill_id, shift_id, scheduled_for, decided_at, "
                    "rationale) VALUES "
                    "(:id, :tenant, :work, :team, :worker, :skill, :shift, "
                    "now(), now(), 'verified')"
                ),
                values | {"id": uuid.uuid4(), "work": f"work:{index}"},
            )
    admin.dispose()
    app = create_engine(app_url)
    try:
        for tenant in tenants:
            with app.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant)},
                )
                for table in TABLES:
                    query = f"SELECT tenant_id FROM mod_workforce.{table}"  # noqa: S608
                    assert set(connection.execute(text(query)).scalars()) == {tenant}
    finally:
        app.dispose()
