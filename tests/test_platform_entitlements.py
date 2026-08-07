"""The platform surface's entitlement writes, against real Postgres.

This path cannot be proven in the unit lane: it establishes RLS tenant context
with `set_config` (the `provision_tenant` idiom) because `platform_api` has no
BYPASSRLS and `tenant_entitlement_grants` carries a single
`tenant_id = app_current_tenant_id()` policy. SQLite has no such function, and
faking one would mean testing a code path no deployment runs.

What this pins, beyond "the write works":

- the operator can set an entitlement for ANY tenant, which is the whole point
  of the platform plane — and the RLS policy would otherwise forbid it, since
  `app_current_tenant_id()` is NULL for `platform_api`;
- revoking leaves a row saying `granted = false` rather than deleting it, so
  "we took it away" stays distinguishable from "they never had it";
- provenance records WHICH operator did it.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="module")
def platform_engine() -> Generator[Engine, None, None]:
    url = os.getenv("TEST_PLATFORM_DATABASE_URL")
    if not url:
        pytest.skip("TEST_PLATFORM_DATABASE_URL not set — needs real Postgres")
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def platform_session(platform_engine: Engine) -> Generator[Session, None, None]:
    """A `platform_api` session.

    NOTE the argument order in every test below: `tenant_a` FIRST, then this.
    pytest finalises in reverse setup order, so the tenant fixture — whose
    teardown deletes the tenant row and cascades into the grant table — must be
    set up first to be torn down LAST. With the order reversed, this session is
    still holding grant-row locks when that DELETE runs, and the two fixtures
    deadlock.
    """
    factory = sessionmaker(bind=platform_engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _set_tenant_context(session: Session, tenant_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )


def test_platform_api_cannot_touch_grants_without_tenant_context(
    tenant_a, platform_session: Session
) -> None:
    """The constraint that shapes the whole screen.

    Without a tenant context, `app_current_tenant_id()` is NULL for
    `platform_api` and `NULL = NULL` is not true — so the role reads NOTHING,
    despite holding SELECT. This is why the platform entitlements screens are
    per-tenant rather than one fleet-wide matrix.
    """
    rows = platform_session.execute(
        text("SELECT id FROM tenant_entitlement_grants WHERE tenant_id = :tid"),
        {"tid": str(tenant_a.id)},
    ).fetchall()
    assert rows == []


def test_with_context_the_operator_can_grant_for_any_tenant(
    tenant_a, platform_session: Session
) -> None:
    from dotmac_kernel.capabilities import CapabilityCatalogue
    from dotmac_kernel.entitlements import grant_entitlement

    catalogue = CapabilityCatalogue({"custom_fields.use": "custom_fields"})
    _set_tenant_context(platform_session, tenant_a.id)
    grant_entitlement(
        platform_session,
        tenant_id=tenant_a.id,
        capability_code="custom_fields.use",
        catalogue=catalogue,
        granted=False,
        source="platform-admin:ops@example.com",
    )
    platform_session.flush()

    row = platform_session.execute(
        text(
            "SELECT granted, source FROM tenant_entitlement_grants "
            "WHERE tenant_id = :tid AND capability_code = 'custom_fields.use'"
        ),
        {"tid": str(tenant_a.id)},
    ).one()
    # Revoked, not absent — the two are different answers.
    assert row.granted is False
    assert row.source == "platform-admin:ops@example.com"

    # Release the row locks BEFORE the tenant fixture's teardown runs. Without
    # this the session sits `idle in transaction` holding locks on
    # `tenant_entitlement_grants`, and `DELETE FROM tenants` (which cascades to
    # them) blocks until the test suite times out — a deadlock between two
    # fixtures, not a failure the assertions would ever show.
    platform_session.rollback()
