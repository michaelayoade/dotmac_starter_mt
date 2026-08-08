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
file follows. The bulk of this file predates the router (Task 3 scaffolded
the feature only) and talks to `domain_settings` directly over SQL as the
`app_user` role. `test_tenant_a_put_max_per_entity_does_not_affect_tenant_b`
at the bottom is the Task 5 API-level canary, added once the router landed —
it goes through the real HTTP API (`PUT`/`GET /settings/...`) instead.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import client_for, provision_and_login


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


# ---------------------------------------------------------------------------
# Task 5 API-level canary: PUT/GET /settings/... through the real HTTP API.
# ---------------------------------------------------------------------------


def test_tenant_a_put_max_per_entity_does_not_affect_tenant_b(
    app_client: TestClient,
    admin_session: Session,
    tenant_a,
    tenant_b,
) -> None:
    """The canary the settings API exists for: a tenant admin's `PUT` writes
    only THEIR row. `custom_fields/max_per_entity` defaults to 20 (see
    `app/features/settings/spec.py`) — tenant A overrides it to 5; tenant B
    must keep resolving the spec default via `GET`, unaffected. Each
    tenant's admin is provisioned via `provision_and_login`
    (`tests/conftest.py`) — registration no longer grants admin (Task 2).
    """
    a = client_for(app_client, tenant_a.slug)
    a_token = provision_and_login(
        admin_session, tenant_a, a, "settings-a@tenant-a.example.com"
    )

    put_resp = a.put(
        "/settings/custom_fields/max_per_entity",
        headers={"Authorization": f"Bearer {a_token}"},
        json={"value": 5},
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["value"] == 5
    assert put_resp.json()["source"] == "tenant"

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_token = provision_and_login(
        admin_session, tenant_b, b, "settings-b@tenant-b.example.org"
    )
    b_list = b.get(
        "/settings/custom_fields", headers={"Authorization": f"Bearer {b_token}"}
    )
    assert b_list.status_code == 200, b_list.text
    b_entry = next(item for item in b_list.json() if item["key"] == "max_per_entity")
    assert b_entry["value"] == 20
    assert b_entry["source"] == "default"

    a_list = a.get(
        "/settings/custom_fields", headers={"Authorization": f"Bearer {a_token}"}
    )
    assert a_list.status_code == 200, a_list.text
    a_entry = next(item for item in a_list.json() if item["key"] == "max_per_entity")
    assert a_entry["value"] == 5
    assert a_entry["source"] == "tenant"


# ---------------------------------------------------------------------------
# domain_setting_history — the same split-policy exception, plus append-only
#
# `_SPLIT_POLICY_EXCEPTIONS` in `tests/test_rls_catalog.py` only accepts a
# nullable-tenant table that pays for the exception with a behavioural canary.
# This is that canary. It pins the half a naive policy breaks (a tenant must
# still READ platform-scope history) AND the half unique to this table: history
# is append-only, so no tenant may rewrite or delete the record of a change.
# ---------------------------------------------------------------------------


def _insert_history(
    session: Session,
    *,
    tenant_id: uuid.UUID | None,
    key: str,
    domain: str = "branding",
) -> None:
    session.execute(
        text(
            "INSERT INTO domain_setting_history "
            "(id, tenant_id, domain, key, action, value_after) "
            "VALUES (:id, :tenant_id, :domain, :key, 'create', 'v')"
        ),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "domain": domain,
            "key": key,
        },
    )


def test_history_of_tenant_a_is_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    key = f"hist-a-{uuid.uuid4().hex[:8]}"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        _insert_history(a, tenant_id=tenant_a.id, key=key)
        a.commit()
    finally:
        a.close()

    b = _as_tenant(tenant_sessionmaker, tenant_b.id)
    try:
        rows = b.execute(
            text("SELECT key FROM domain_setting_history WHERE key = :key"),
            {"key": key},
        ).fetchall()
        assert rows == []
    finally:
        b.rollback()
        b.close()

    admin_session.execute(
        text("DELETE FROM domain_setting_history WHERE key = :key"), {"key": key}
    )
    admin_session.commit()


def test_platform_history_is_visible_to_every_tenant(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """The half a naive `tenant_id = current_tenant` policy breaks."""
    key = f"hist-platform-{uuid.uuid4().hex[:8]}"
    admin_session.execute(
        text(
            "INSERT INTO domain_setting_history "
            "(id, tenant_id, domain, key, action, value_after) "
            "VALUES (:id, NULL, 'branding', :key, 'create', 'v')"
        ),
        {"id": str(uuid.uuid4()), "key": key},
    )
    admin_session.commit()

    for tenant in (tenant_a, tenant_b):
        session = _as_tenant(tenant_sessionmaker, tenant.id)
        try:
            rows = session.execute(
                text("SELECT key FROM domain_setting_history WHERE key = :key"),
                {"key": key},
            ).fetchall()
            assert len(rows) == 1
        finally:
            session.rollback()
            session.close()

    admin_session.execute(
        text("DELETE FROM domain_setting_history WHERE key = :key"), {"key": key}
    )
    admin_session.commit()


def test_tenant_cannot_write_history_for_another_tenant(
    tenant_a, tenant_b, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises(DBAPIError):
            _insert_history(a, tenant_id=tenant_b.id, key="hist-forged")
            a.flush()
    finally:
        a.rollback()
        a.close()


def test_tenant_cannot_write_platform_scope_history(
    tenant_a, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises(DBAPIError):
            _insert_history(a, tenant_id=None, key="hist-platform-forged")
            a.flush()
    finally:
        a.rollback()
        a.close()


def test_history_is_append_only_for_a_tenant(
    admin_session: Session, tenant_a, tenant_sessionmaker: sessionmaker[Session]
) -> None:
    """A tenant that could rewrite or delete a history row could erase the
    record of the change the row exists to explain. Enforced twice: no
    UPDATE/DELETE policy, and no UPDATE/DELETE grant."""
    key = f"hist-append-{uuid.uuid4().hex[:8]}"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        _insert_history(a, tenant_id=tenant_a.id, key=key)
        a.commit()
    finally:
        a.close()

    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises(DBAPIError):
            a.execute(
                text(
                    "UPDATE domain_setting_history SET value_after = 'tampered' "
                    "WHERE key = :key"
                ),
                {"key": key},
            )
    finally:
        a.rollback()
        a.close()

    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        with pytest.raises(DBAPIError):
            a.execute(
                text("DELETE FROM domain_setting_history WHERE key = :key"),
                {"key": key},
            )
    finally:
        a.rollback()
        a.close()

    admin_session.execute(
        text("DELETE FROM domain_setting_history WHERE key = :key"), {"key": key}
    )
    admin_session.commit()


# ---------------------------------------------------------------------------
# The settings read cache, against two real tenants
#
# `tests/unit/test_settings_cache.py` proves the key shape and the invalidation
# arithmetic. This proves the property those imply but cannot demonstrate: with
# a cache installed and TWO tenants whose rows really exist under RLS, tenant B
# reads its own value and not A's, and A's write does not evict B's entry.
#
# A static check cannot see this. `dotmac_erp`'s unscoped key passed every one
# of its own tests.
# ---------------------------------------------------------------------------


def test_cached_reads_do_not_cross_tenants(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    from dotmac_kernel import settings_cache as settings_cache_module
    from dotmac_kernel import settings_resolver
    from dotmac_kernel.cache import MemoryCache
    from dotmac_kernel.settings_models import SettingDomain

    def write(tenant, value: int) -> None:
        """One write in its own transaction.

        A fresh session per commit is not ceremony: `_as_tenant` issues
        `SET LOCAL app.current_tenant`, which is scoped to the transaction, so
        reusing a session after `commit()` leaves it with no tenant context and
        RLS correctly hides the row it just wrote.
        """
        session = _as_tenant(tenant_sessionmaker, tenant.id)
        try:
            settings_resolver.upsert_by_key(
                session,
                SettingDomain.audit,
                "retention_days",
                value,
                tenant_id=tenant.id,
            )
            session.commit()
        finally:
            session.close()

    def read(tenant) -> object:
        session = _as_tenant(tenant_sessionmaker, tenant.id)
        try:
            return settings_resolver.resolve_value(
                session, SettingDomain.audit, "retention_days", tenant_id=tenant.id
            )
        finally:
            session.rollback()
            session.close()

    store = MemoryCache()
    settings_cache_module.install_settings_cache(store)
    try:
        write(tenant_a, 11)
        write(tenant_b, 22)

        # A reads first and populates the cache; B must not be served A's entry.
        assert read(tenant_a) == 11
        assert read(tenant_b) == 22

        # A's write evicts A's entry and leaves B's intact.
        write(tenant_a, 33)
        assert settings_cache_module.cached(
            "audit", "retention_days", tenant_id=tenant_b.id
        ) == (22, "tenant")
        assert read(tenant_a) == 33
        assert read(tenant_b) == 22
    finally:
        settings_cache_module.install_settings_cache(None)
        admin_session.execute(
            text(
                "DELETE FROM domain_settings WHERE domain = 'audit' "
                "AND key = 'retention_days'"
            )
        )
        admin_session.commit()
