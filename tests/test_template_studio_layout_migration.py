"""`ts_0005_layout_slug` upgrades a populated database and downgrades cleanly.

Runs on a scratch database cloned from nothing: the lineage is upgraded to
`ts_0004` first, a text-only revision is written, then `ts_0005` is applied. The
point is the populated-table case the plain `alembic upgrade heads` in CI never
exercises: a nullable add must leave existing rows exactly as they were (NULL =
no layout), keep the table's RLS and grants, and be reversible.

Requires PostgreSQL (`TEST_MIGRATION_DATABASE_URL`, a superuser).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent

BEFORE = "ts_0004_html_body"
AFTER = "ts_0005_layout_slug"


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_MIGRATION_DATABASE_URL not set — needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


def _columns(conn) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        text(
            "SELECT column_name, data_type, is_nullable FROM "
            "information_schema.columns WHERE table_schema = 'mod_tstudio' "
            "AND table_name = 'template_versions'"
        )
    )
    return {name: (kind, nullable) for name, kind, nullable in rows}


def test_ts_0005_adds_a_nullable_column_without_touching_existing_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command
    from alembic.config import Config

    superuser = _superuser_url()
    name = f"ts_layout_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    try:
        command.upgrade(cfg, BEFORE)
        engine = create_engine(admin_url)
        tenant, template, version = (uuid.uuid4() for _ in range(3))
        with engine.begin() as conn:
            assert "layout_slug" not in _columns(conn)
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, 'layout migration proof')"
                ),
                {"id": tenant, "slug": f"layout-proof-{tenant.hex[:8]}"},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_tstudio.templates "
                    "(id, tenant_id, slug, channel, context, name, is_active) "
                    "VALUES (:t, :tenant, 'old', 'email', 'default', 'Old', true)"
                ),
                {"t": template, "tenant": tenant},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_tstudio.template_versions "
                    "(id, tenant_id, template_id, version, body, variables) "
                    "VALUES (:v, :tenant, :t, 1, 'legacy text', '[]'::jsonb)"
                ),
                {"v": version, "tenant": tenant, "t": template},
            )

        command.upgrade(cfg, AFTER)
        with engine.begin() as conn:
            assert _columns(conn)["layout_slug"] == ("character varying", "YES")
            row = conn.execute(
                text(
                    "SELECT body, layout_slug FROM mod_tstudio.template_versions "
                    "WHERE id = :v"
                ),
                {"v": version},
            ).one()
            assert tuple(row) == ("legacy text", None)  # no backfill; NULL
            # RLS is still enabled AND forced, and the policy is untouched.
            flags = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'mod_tstudio.template_versions'::regclass"
                )
            ).one()
            assert tuple(flags) == (True, True)
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_policies WHERE schemaname = "
                        "'mod_tstudio' AND tablename = 'template_versions'"
                    )
                ).scalar_one()
                == 1
            )
            # The table-level grant covers the new column for the online roles.
            for role in ("app_user", "platform_api"):
                assert conn.execute(
                    text(
                        "SELECT has_column_privilege(:r, "
                        "'mod_tstudio.template_versions', 'layout_slug', 'SELECT')"
                    ),
                    {"r": role},
                ).scalar_one()
            conn.execute(
                text(
                    "UPDATE mod_tstudio.template_versions SET layout_slug = 'brand' "
                    "WHERE id = :v"
                ),
                {"v": version},
            )

        command.downgrade(cfg, BEFORE)
        with engine.begin() as conn:
            assert "layout_slug" not in _columns(conn)
            body = conn.execute(
                text("SELECT body FROM mod_tstudio.template_versions WHERE id = :v"),
                {"v": version},
            ).scalar_one()
            assert body == "legacy text"  # the text part survives a downgrade

        command.upgrade(cfg, AFTER)  # and the step is re-appliable
        with engine.begin() as conn:
            assert _columns(conn)["layout_slug"] == ("character varying", "YES")
        engine.dispose()
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()
