"""Prove ph_0002 preserves unknown freshness provenance from ph_0001."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
ASSEMBLY = ROOT / "alembic/versions"
HEALTH = (
    ROOT
    / "packages/dotmac-platform-health/src/dotmac_platform_health/migrations/versions"
)


def _url(base: str, database: str, user: str | None = None) -> str:
    prefix, _, _ = base.rpartition("/")
    if user:
        scheme, _, authority = prefix.partition("://")
        prefix = f"{scheme}://{user}@{authority.rpartition('@')[2]}"
    return f"{prefix}/{database}"


@contextmanager
def _scratch_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    if not superuser:
        pytest.skip("TEST_DATABASE_URL not set — upgrade proofs need PostgreSQL")
    name = f"health_upgrade_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        for role in ("platform_api", "app_user"):
            conn.execute(text(f"GRANT CONNECT ON DATABASE \"{name}\" TO {role}"))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    setup.dispose()
    admin_url = _url(superuser, name, user="app_admin")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    try:
        yield admin_url
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


def _config():
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option(
        "version_locations", f"{KERNEL} {ASSEMBLY} {HEALTH}"
    )
    return config


def test_ph_0002_does_not_backfill_ph_0001_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command

    with _scratch_database(monkeypatch) as admin_url:
        command.upgrade(_config(), "ph_0001_platform_health")
        component_id = uuid.uuid4()
        observation_id = uuid.uuid4()
        engine = create_engine(admin_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mod_health.health_components "
                    "(id, code, display_name, freshness_seconds, active) "
                    "VALUES (:id, 'api', 'API', 7, true)"
                ),
                {"id": component_id},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_health.health_observations "
                    "(id, component_id, source_ref, observation_key, "
                    "request_fingerprint, state, observed_at, received_at, "
                    "summary, labels) VALUES (:id, :component, 'agent', "
                    "'legacy', :fingerprint, 'healthy', now(), now(), 'ok', '{}'::jsonb)"
                ),
                {
                    "id": observation_id,
                    "component": component_id,
                    "fingerprint": "a" * 64,
                },
            )
        engine.dispose()

        command.upgrade(_config(), "ph_0002_freshness_snapshot")
        engine = create_engine(admin_url)
        with engine.begin() as conn:
            assert conn.scalar(
                text(
                    "SELECT freshness_seconds FROM mod_health.health_observations "
                    "WHERE id = :id"
                ),
                {"id": observation_id},
            ) is None
            conn.execute(
                text(
                    "INSERT INTO mod_health.health_observations "
                    "(id, component_id, source_ref, observation_key, "
                    "request_fingerprint, state, freshness_seconds, observed_at, "
                    "received_at, summary, labels) VALUES (:id, :component, "
                    "'agent', 'null-ok', :fingerprint, 'healthy', NULL, now(), "
                    "now(), 'ok', '{}'::jsonb)"
                ),
                {
                    "id": uuid.uuid4(),
                    "component": component_id,
                    "fingerprint": "b" * 64,
                },
            )
            savepoint = conn.begin_nested()
            try:
                conn.execute(
                    text(
                        "INSERT INTO mod_health.health_observations "
                        "(id, component_id, source_ref, observation_key, "
                        "request_fingerprint, state, freshness_seconds, observed_at, "
                        "received_at, summary, labels) VALUES (:id, :component, "
                        "'agent', 'bad', :fingerprint, 'healthy', 0, now(), now(), "
                        "'bad', '{}'::jsonb)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "component": component_id,
                        "fingerprint": "c" * 64,
                    },
                )
            except IntegrityError:
                savepoint.rollback()
            else:
                savepoint.rollback()
                pytest.fail("non-positive freshness unexpectedly admitted")
            conn.execute(
                text(
                    "INSERT INTO mod_health.health_observations "
                    "(id, component_id, source_ref, observation_key, "
                    "request_fingerprint, state, freshness_seconds, observed_at, "
                    "received_at, summary, labels) VALUES (:id, :component, "
                    "'agent', 'positive', :fingerprint, 'healthy', 1, now(), "
                    "now(), 'ok', '{}'::jsonb)"
                ),
                {
                    "id": uuid.uuid4(),
                    "component": component_id,
                    "fingerprint": "d" * 64,
                },
            )
        engine.dispose()
