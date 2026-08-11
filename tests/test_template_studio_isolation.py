"""Tenant-isolation canary for the FIRST module schema (`mod_tstudio`).

Both of Template Studio's tables are tenant-scoped RLS tables created by
`ts_0001_templates`. This proves, against real Postgres, that:

- a tenant cannot READ another tenant's templates or versions;
- a tenant cannot INSERT a row stamped with another tenant's id (the policy's
  `WITH CHECK`);
- the composite `(tenant_id, template_id)` foreign key makes a cross-tenant
  version reference unrepresentable even when RLS is bypassed.

That last one is the reason the FK is composite rather than a plain
`template_id`, and it is checked as `app_admin` — RLS is not the only defence,
and a proof that only exercises RLS would not notice if the constraint were
quietly simplified.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# The schema is spelled LITERALLY in the SQL below, not interpolated: a
# canary that built its own table names could drift from the migration it
# is meant to prove, and the interpolation reads as an injection vector to
# every scanner that looks at it.
SCHEMA = "mod_tstudio"


@pytest.fixture(scope="module")
def tenant_engine() -> Generator[Engine, None, None]:
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
    session = factory()
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    return session


def _insert_template(session: Session, *, tenant_id: uuid.UUID, slug: str) -> uuid.UUID:
    tid = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO mod_tstudio.templates "
            "(id, tenant_id, slug, channel, context, name, is_active) "
            "VALUES (:id, :tenant_id, :slug, 'email', 'default', :slug, true)"
        ),
        {"id": str(tid), "tenant_id": str(tenant_id), "slug": slug},
    )
    return tid


def _insert_version(
    session: Session, *, tenant_id: uuid.UUID, template_id: uuid.UUID
) -> uuid.UUID:
    vid = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO mod_tstudio.template_versions "
            "(id, tenant_id, template_id, version, body, variables) "
            "VALUES (:id, :tenant_id, :template_id, 1, 'hello', '[]'::jsonb)"
        ),
        {"id": str(vid), "tenant_id": str(tenant_id), "template_id": str(template_id)},
    )
    return vid


def test_template_in_tenant_a_is_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        tid = _insert_template(a, tenant_id=tenant_a.id, slug="welcome-a")
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM mod_tstudio.templates WHERE id = :id"),
                {"id": str(tid)},
            ).fetchall()
            assert rows == []  # tenant B: invisible
        finally:
            b.rollback()
            b.close()

        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            row = a2.execute(
                text("SELECT id FROM mod_tstudio.templates WHERE id = :id"),
                {"id": str(tid)},
            ).fetchone()
            assert row is not None  # tenant A: visible
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM mod_tstudio.templates WHERE id = :id"), {"id": str(tid)}
        )
        admin_session.commit()


def test_version_in_tenant_a_is_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        tid = _insert_template(a, tenant_id=tenant_a.id, slug="welcome-versions")
        vid = _insert_version(a, tenant_id=tenant_a.id, template_id=tid)
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            rows = b.execute(
                text("SELECT id FROM mod_tstudio.template_versions WHERE id = :id"),
                {"id": str(vid)},
            ).fetchall()
            assert rows == []
        finally:
            b.rollback()
            b.close()
    finally:
        admin_session.execute(
            text("DELETE FROM mod_tstudio.templates WHERE id = :id"), {"id": str(tid)}
        )
        admin_session.commit()


def test_with_check_denies_a_cross_tenant_template_insert(
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """Tenant B cannot INSERT a template stamped with tenant A's id."""
    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert_template(b, tenant_id=tenant_a.id, slug="smuggled")
    finally:
        b.rollback()
        b.close()


def test_with_check_denies_a_cross_tenant_version_insert(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        tid = _insert_template(a, tenant_id=tenant_a.id, slug="welcome-wc")
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            with pytest.raises(DBAPIError, match="row-level security"):
                _insert_version(b, tenant_id=tenant_a.id, template_id=tid)
        finally:
            b.rollback()
            b.close()
    finally:
        admin_session.execute(
            text("DELETE FROM mod_tstudio.templates WHERE id = :id"), {"id": str(tid)}
        )
        admin_session.commit()


def test_composite_fk_blocks_a_cross_tenant_version_even_as_admin(
    admin_session: Session,
    tenant_a,
    tenant_b,
) -> None:
    """RLS is not the only defence.

    Run as `app_admin` (RLS bypassed) to isolate the CONSTRAINT: a version row
    stamped with tenant B but pointing at tenant A's template must violate the
    composite `(tenant_id, template_id)` foreign key. A plain single-column FK
    would happily accept this row.
    """
    tid = uuid.uuid4()
    admin_session.execute(
        text(
            "INSERT INTO mod_tstudio.templates "
            "(id, tenant_id, slug, channel, context, name, is_active) "
            "VALUES (:id, :tenant_id, 'fk-probe', 'email', 'default', 'FK probe', true)"
        ),
        {"id": str(tid), "tenant_id": str(tenant_a.id)},
    )
    admin_session.commit()
    try:
        with pytest.raises(IntegrityError):
            admin_session.execute(
                text(
                    "INSERT INTO mod_tstudio.template_versions "
                    "(id, tenant_id, template_id, version, body, variables) "
                    "VALUES (:id, :tenant_id, :template_id, 1, 'x', '[]'::jsonb)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": str(tenant_b.id),
                    "template_id": str(tid),
                },
            )
        admin_session.rollback()
    finally:
        admin_session.execute(
            text("DELETE FROM mod_tstudio.templates WHERE id = :id"), {"id": str(tid)}
        )
        admin_session.commit()
