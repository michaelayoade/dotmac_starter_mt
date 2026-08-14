"""Postgres isolation canaries for both ``dotmac-approvals`` planes.

The reference assembly builds but does not install ``dotmac-approvals``, so this
composes its lineage in a scratch database and drives the assertions as the
online roles. SQLite cannot prove row-level security, and a revocation is not a
property any ORM test can observe.

Two different isolation mechanisms are proven here, because the planes use
different ones:

- tenant tables: FORCEd RLS with a policy on `public.app_current_tenant_id()`;
- platform tables: no RLS at all, and `app_user` REVOKEd — there, the
  revocation IS the isolation (ADR-0023), and the control-plane role must still
  be able to work.
"""

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
APPROVALS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-approvals/src/dotmac_approvals/migrations/versions"
)

TENANT_TABLES = ("approval_policies", "approval_requests", "approval_decisions")
PLATFORM_TABLES = (
    "platform_approval_policies",
    "platform_approval_requests",
    "platform_approval_decisions",
)
DIGEST = "sha256:" + "a" * 64


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the RLS canary needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_scratch() -> Iterator[tuple[str, str, str]]:
    superuser = _superuser_url()
    name = f"approvals_rls_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
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
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {APPROVALS_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield (
            admin_url,
            _url_for(superuser, name, user="app_user"),
            _url_for(superuser, name, user="platform_api"),
        )
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_two_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant_id, "slug": slug, "name": slug.title()},
                )
                policy_id, request_id = uuid.uuid4(), uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO mod_approvals.approval_policies ("
                        "id, tenant_id, policy_code, version, levels, "
                        "allow_self_approval, document_digest"
                        ") VALUES (:id, :tenant, 'payment.release', 1, "
                        "CAST(:levels AS json), false, :digest)"
                    ),
                    {
                        "id": policy_id,
                        "tenant": tenant_id,
                        "levels": '[{"sequence": 1, "approver_kind": "role", '
                        '"approver_id": "r", "quorum": 1, "sod_rule": null, '
                        '"requires_mfa": false, "allow_delegation": true}]',
                        "digest": DIGEST,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_approvals.approval_requests ("
                        "id, tenant_id, policy_code, policy_version, subject_type, "
                        "subject_id, content_digest, requested_by, state, "
                        "current_level, idempotency_key"
                        ") VALUES (:id, :tenant, 'payment.release', 1, "
                        "'finance.payment', :subject, :digest, :actor, 'pending', "
                        "1, :key)"
                    ),
                    {
                        "id": request_id,
                        "tenant": tenant_id,
                        "subject": str(uuid.uuid4()),
                        "digest": DIGEST,
                        "actor": uuid.uuid4(),
                        "key": f"seed-{slug}",
                    },
                )
    finally:
        engine.dispose()
    return tenant_a, tenant_b


def test_every_tenant_table_has_forced_rls_and_a_tenant_policy(
    migrated_scratch: tuple[str, str, str],
) -> None:
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            for table in TENANT_TABLES:
                enabled, forced = conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = CAST(:t AS regclass)"
                    ),
                    {"t": f"mod_approvals.{table}"},
                ).one()
                assert enabled, table
                # FORCEd matters: without it the table owner bypasses the policy,
                # and migrations run as an owner.
                assert forced, table
                policies = [
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT policyname FROM pg_policies "
                            "WHERE schemaname = 'mod_approvals' AND tablename = :t"
                        ),
                        {"t": table},
                    )
                ]
                assert policies == [f"{table}_tenant_isolation"], table
    finally:
        engine.dispose()


def test_one_tenant_cannot_read_or_write_another_tenants_rows(
    migrated_scratch: tuple[str, str, str],
) -> None:
    admin_url, app_user_url, _ = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :t, false)"),
                {"t": str(tenant_a)},
            )
            visible = (
                conn.execute(
                    text("SELECT tenant_id FROM mod_approvals.approval_requests")
                )
                .scalars()
                .all()
            )
            assert visible == [tenant_a]

            # And the write side: WITH CHECK must refuse a row for someone else.
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_approvals.approval_requests ("
                        "id, tenant_id, policy_code, policy_version, subject_type,"
                        " subject_id, content_digest, requested_by, state, "
                        "current_level, idempotency_key) VALUES ("
                        ":id, :tenant, 'payment.release', 1, 'finance.payment', "
                        "'x', :digest, :actor, 'pending', 1, 'cross-tenant')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant_b,
                        "digest": DIGEST,
                        "actor": uuid.uuid4(),
                    },
                )
    finally:
        engine.dispose()


def test_platform_tables_are_unreadable_by_the_tenant_role(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """No RLS here by design — the REVOKE is the isolation."""
    admin_url, app_user_url, _ = migrated_scratch
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            for table in PLATFORM_TABLES:
                with pytest.raises(DBAPIError):
                    # The interpolated value is this file's own PLATFORM_TABLES
                    # literal, not input. noqa: the check cannot see that.
                    conn.execute(
                        text(f"SELECT 1 FROM mod_approvals.{table}")  # noqa: S608
                    )
                conn.rollback()
    finally:
        engine.dispose()

    checker = create_engine(admin_url)
    try:
        with checker.connect() as conn:
            for table in PLATFORM_TABLES:
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    granted = conn.execute(
                        text("SELECT has_table_privilege('app_user', :t, :p)"),
                        {"t": f"mod_approvals.{table}", "p": privilege},
                    ).scalar_one()
                    assert granted is False, f"app_user holds {privilege} on {table}"
    finally:
        checker.dispose()


def test_the_platform_runtime_role_can_still_operate(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """Specificity for the revocation above: proving `app_user` is locked out is
    only meaningful if the role that SHOULD work still does."""
    _, _, platform_url = migrated_scratch
    engine = create_engine(platform_url)
    try:
        with engine.begin() as conn:
            request_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_requests ("
                    "id, policy_code, policy_version, subject_type, subject_id, "
                    "content_digest, requested_by, state, current_level, "
                    "idempotency_key) VALUES (:id, 'fleet.plan', 1, 'fleet.plan', "
                    "'plan-1', :digest, :actor, 'pending', 1, 'plan-1')"
                ),
                {"id": request_id, "digest": DIGEST, "actor": uuid.uuid4()},
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM mod_approvals.platform_approval_requests"
                    )
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_no_foreign_key_crosses_the_planes_in_the_live_catalog(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The ORM-level assertion has a live counterpart, because what ships is the
    migration rather than the model."""
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT src.relname, tgt.relname
                    FROM pg_constraint c
                    JOIN pg_class src ON src.oid = c.conrelid
                    JOIN pg_class tgt ON tgt.oid = c.confrelid
                    JOIN pg_namespace n ON n.oid = src.relnamespace
                    WHERE c.contype = 'f' AND n.nspname = 'mod_approvals'
                    """
                )
            ).all()
        for source, target in rows:
            if target == "tenants":
                assert source in TENANT_TABLES
                continue
            source_is_platform = source in PLATFORM_TABLES
            target_is_platform = target in PLATFORM_TABLES
            assert (
                source_is_platform == target_is_platform
            ), f"{source} -> {target} crosses the tenant/platform boundary"
    finally:
        engine.dispose()


def test_a_duplicate_vote_is_refused_by_the_live_constraint(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """Distinct-actor quorum, proven where it actually holds under concurrency."""
    _, _, platform_url = migrated_scratch
    engine = create_engine(platform_url)
    actor = uuid.uuid4()
    try:
        with engine.begin() as conn:
            request_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_requests ("
                    "id, policy_code, policy_version, subject_type, subject_id, "
                    "content_digest, requested_by, state, current_level, "
                    "idempotency_key) VALUES (:id, 'fleet.plan', 1, 'fleet.plan', "
                    "'plan-2', :digest, :actor, 'pending', 1, 'plan-2')"
                ),
                {"id": request_id, "digest": DIGEST, "actor": actor},
            )
            for _ in range(1):
                conn.execute(
                    text(
                        "INSERT INTO mod_approvals.platform_approval_decisions ("
                        "id, request_id, level, actor_id, action, mfa_verified, "
                        "decided_at) VALUES (:id, :request, 1, :actor, 'approve', "
                        "false, now())"
                    ),
                    {"id": uuid.uuid4(), "request": request_id, "actor": actor},
                )
        with engine.begin() as conn, pytest.raises(DBAPIError):
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_decisions ("
                    "id, request_id, level, actor_id, action, mfa_verified, "
                    "decided_at) VALUES (:id, :request, 1, :actor, 'approve', "
                    "false, now())"
                ),
                {"id": uuid.uuid4(), "request": request_id, "actor": actor},
            )
    finally:
        engine.dispose()
