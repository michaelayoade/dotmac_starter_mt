"""Settings (`domain_settings`) isolation canaries.

This table's RLS is special (see `alembic/versions/*_settings_table.py`):
tenants can READ their own rows AND platform-level rows (`tenant_id IS NULL`),
but can only WRITE rows they own. `platform_api` manages the NULL-tenant rows.

Three properties, each load-bearing:
(a) a row created by tenant A is invisible to tenant B.
(b) a platform-default row (tenant_id NULL, inserted via the RLS-bypassing
    admin engine) is visible to BOTH tenants.
(c) tenant A cannot INSERT a row with `tenant_id IS NULL` or tenant B's id —
    the DB write policy must reject it, not just silently no-op.

Requires a real Postgres (RLS doesn't exist on SQLite) — see
`tests/test_cross_tenant_isolation.py` for the fixture/setup convention this
file follows. Unlike the other isolation canaries, there is no router yet
(Task 3 scaffolds the feature only; the router lands in Task 5), so this file
talks to `domain_settings` directly over SQL as the `app_user` role rather than
through the HTTP API.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="module")
def tenant_engine() -> Generator[Engine, None, None]:
    """Engine bound as `app_user` — the RLS-enforced, tenant-facing role."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def tenant_sessionmaker(tenant_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=tenant_engine, autocommit=False, autoflush=False)


def _as_tenant(factory: sessionmaker[Session], tenant_id: uuid.UUID) -> Session:
    """A fresh `app_user` session with `app.current_tenant` set for this transaction."""
    session = factory()
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    return session


def _insert_row(
    session: Session,
    *,
    tenant_id: uuid.UUID | None,
    key: str,
    value_text: str = "value",
    domain: str = "branding",
) -> None:
    session.execute(
        text(
            "INSERT INTO domain_settings "
            "(id, tenant_id, domain, key, value_type, value_text) "
            "VALUES (:id, :tenant_id, :domain, :key, 'string', :value_text)"
        ),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "domain": domain,
            "key": key,
            "value_text": value_text,
        },
    )


def test_tenant_a_row_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    key = f"canary-a-{uuid.uuid4().hex[:8]}"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        _insert_row(a, tenant_id=tenant_a.id, key=key, value_text="tenant-a-value")
        a.commit()
    finally:
        a.close()

    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        rows = b.execute(
            text("SELECT key FROM domain_settings WHERE key = :key"), {"key": key}
        ).fetchall()
        assert rows == []
    finally:
        b.rollback()
        b.close()

    admin_session.execute(
        text("DELETE FROM domain_settings WHERE key = :key"), {"key": key}
    )
    admin_session.commit()


def test_platform_row_visible_to_both_tenants(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    key = f"canary-platform-{uuid.uuid4().hex[:8]}"
    _insert_row(
        admin_session,
        tenant_id=None,
        key=key,
        value_text="platform-default",
        domain="auth",
    )
    admin_session.commit()

    for tenant in (tenant_a, tenant_b):
        session = _as_tenant(tenant_sessionmaker, tenant.id)
        try:
            row = session.execute(
                text("SELECT value_text FROM domain_settings WHERE key = :key"),
                {"key": key},
            ).fetchone()
            assert row is not None, f"platform row invisible to tenant {tenant.slug}"
            assert row[0] == "platform-default"
        finally:
            session.rollback()
            session.close()

    admin_session.execute(
        text("DELETE FROM domain_settings WHERE key = :key"), {"key": key}
    )
    admin_session.commit()


def test_tenant_a_cannot_insert_null_tenant_row(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    key = f"canary-reject-null-{uuid.uuid4().hex[:8]}"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_row(a, tenant_id=None, key=key)
    finally:
        a.rollback()
        a.close()

    remaining = admin_session.execute(
        text("SELECT count(*) FROM domain_settings WHERE key = :key"), {"key": key}
    ).scalar_one()
    assert remaining == 0


def test_tenant_a_cannot_insert_tenant_b_row(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    key = f"canary-reject-cross-{uuid.uuid4().hex[:8]}"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_row(a, tenant_id=tenant_b.id, key=key)
    finally:
        a.rollback()
        a.close()

    remaining = admin_session.execute(
        text("SELECT count(*) FROM domain_settings WHERE key = :key"), {"key": key}
    ).scalar_one()
    assert remaining == 0


def test_platform_api_manages_only_null_tenant_rows(
    admin_session: Session,
    tenant_a,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """The extra `domain_settings_platform_all` policy: `platform_api` has table
    grants but no `app.current_tenant` set, so the base write policies (which
    compare against `app_current_tenant_id()`) would reject even a NULL-tenant
    insert. The dedicated `USING/WITH CHECK (tenant_id IS NULL)` policy for
    `platform_api` is what makes platform-row management possible at all —
    this test is the one that would fail if that policy were missing.
    """
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    parts = urlsplit(url)
    platform_url = urlunsplit(
        (
            parts.scheme,
            f"platform_api@{parts.hostname}:{parts.port}",
            parts.path,
            "",
            "",
        )
    )
    platform_engine = create_engine(platform_url, future=True)
    try:
        factory = sessionmaker(bind=platform_engine, autocommit=False, autoflush=False)
        session = factory()
        key = f"canary-platform-api-{uuid.uuid4().hex[:8]}"
        try:
            _insert_row(
                session, tenant_id=None, key=key, value_text="from-platform-api"
            )
            session.commit()

            row = session.execute(
                text("SELECT value_text FROM domain_settings WHERE key = :key"),
                {"key": key},
            ).fetchone()
            assert row is not None
            assert row[0] == "from-platform-api"

            # And platform_api cannot write into a tenant's own row.
            with pytest.raises(DBAPIError, match="row-level security"):
                _insert_row(session, tenant_id=tenant_a.id, key=f"{key}-tenant-owned")
            session.rollback()
        finally:
            session.rollback()
            session.close()

        admin_session.execute(
            text("DELETE FROM domain_settings WHERE key = :key"), {"key": key}
        )
        admin_session.commit()
    finally:
        platform_engine.dispose()
