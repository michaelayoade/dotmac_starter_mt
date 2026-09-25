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

import contextlib
import importlib.util
import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from dotmac_approvals.contracts import (
    Actor,
    ApprovalEvent,
    ApprovalLevel,
    ApprovalState,
    ApproverKind,
    DecisionAction,
    PolicyRevision,
    WithdrawalReferenceConflict,
)
from dotmac_approvals.outbox import withdraw_platform_approval
from dotmac_approvals.service import (
    get_platform_request,
    get_tenant_request,
    publish_platform_policy_version,
    publish_tenant_policy_version,
    record_platform_decision,
    record_tenant_decision,
    request_platform_approval,
    request_tenant_approval,
)
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
APPROVALS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-approvals/src/dotmac_approvals/migrations/versions"
)

TENANT_TABLES = (
    "approval_policies",
    "approval_requests",
    "approval_decisions",
    "approval_withdrawals",
)
PLATFORM_TABLES = (
    "platform_approval_policies",
    "platform_approval_requests",
    "platform_approval_decisions",
    "platform_approval_withdrawals",
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
        cfg.attributes["module_plane_selections"] = (
            ModulePlaneSelection(
                module="approvals",
                planes=(ModulePlane.TENANT, ModulePlane.PLATFORM),
            ),
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


@pytest.fixture
def platform_only_scratch() -> Iterator[str]:
    """The real Vendor shape: kernel tenant objects exist, but this module's
    explicit assembly declaration selects only its platform plane."""
    superuser = _superuser_url()
    name = f"approvals_platform_{uuid.uuid4().hex[:12]}"
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
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {APPROVALS_VERSIONS}",
        )
        cfg.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="approvals", planes=(ModulePlane.PLATFORM,)),
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url
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


@pytest.fixture
def tenant_only_scratch() -> Iterator[str]:
    """A real composed lineage with only the approvals tenant plane."""
    superuser = _superuser_url()
    name = f"approvals_tenant_{uuid.uuid4().hex[:12]}"
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
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {APPROVALS_VERSIONS}",
        )
        cfg.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="approvals", planes=(ModulePlane.TENANT,)),
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url
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


def test_platform_only_selection_is_not_inferred_from_provider_availability(
    platform_only_scratch: str,
) -> None:
    engine = create_engine(platform_only_scratch)
    try:
        with engine.connect() as conn:
            # Kernel 0001 really ran: the false premise in ADR-0027 is now a
            # canary. The tenant catalogue exists and is truthfully bindable.
            assert conn.execute(text("SELECT to_regclass('public.tenants')")).scalar()
            for table in PLATFORM_TABLES:
                assert conn.execute(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"mod_approvals.{table}"},
                ).scalar()
            for table in TENANT_TABLES:
                assert (
                    conn.execute(
                        text("SELECT to_regclass(:table)"),
                        {"table": f"mod_approvals.{table}"},
                    ).scalar()
                    is None
                )
    finally:
        engine.dispose()


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


def _approved_tenant_request(
    db: Session, tenant_id: uuid.UUID, *, key: str
) -> uuid.UUID:
    """A genuinely approved tenant request, through the public owner."""
    approver, requester = uuid.uuid4(), uuid.uuid4()
    db.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    revision = PolicyRevision(
        policy_code=f"fleet.{key}",
        version=1,
        levels=(
            ApprovalLevel(
                sequence=1,
                approver_kind=ApproverKind.USER,
                approver_id=str(approver),
                quorum=1,
            ),
        ),
    )
    publish_tenant_policy_version(db, tenant_id=tenant_id, revision=revision)
    request_id = request_tenant_approval(
        db,
        tenant_id=tenant_id,
        policy_code=f"fleet.{key}",
        policy_version=1,
        subject_type="fleet.plan",
        subject_id=key,
        content_digest=DIGEST,
        requested_by=requester,
        idempotency_key=key,
    ).request_id
    record_tenant_decision(
        db,
        tenant_id=tenant_id,
        request_id=request_id,
        actor=Actor(actor_id=approver),
        action=DecisionAction.APPROVE,
        content_digest=DIGEST,
    )
    return request_id


def test_withdrawal_evidence_and_the_definer_function_obey_tenant_isolation(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """Real evidence exists in tenant B: tenant A reads none of it, B reads it,
    and the SECURITY DEFINER withdrawal function refuses both cross-tenant
    shapes without writing evidence or an outbox row."""
    from dotmac_approvals.outbox import withdraw_tenant_approval

    admin_url, app_user_url, _ = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)
    admin = create_engine(admin_url)
    try:
        with Session(admin) as db, db.begin():
            withdrawn_b = _approved_tenant_request(db, tenant_b, key="b-withdrawn")
            withdraw_tenant_approval(
                db,
                tenant_id=tenant_b,
                request_id=withdrawn_b,
                actor=Actor(actor_id=uuid.uuid4()),
                authority_ref="cp-review",
                reason="isolation proof",
                external_ref="b-withdrawal",
            )
        with Session(admin) as db, db.begin():
            target_b = _approved_tenant_request(db, tenant_b, key="b-target")

        def counts() -> tuple[int, int]:
            with admin.connect() as conn:
                evidence = conn.execute(
                    text("SELECT count(*) FROM mod_approvals.approval_withdrawals")
                ).scalar_one()
                events = conn.execute(
                    text("SELECT count(*) FROM public.outbox_events")
                ).scalar_one()
            return evidence, events

        before = counts()
        assert before[0] == 1
    finally:
        admin.dispose()

    runtime = create_engine(app_user_url)
    try:
        for tenant, expected in ((tenant_a, 0), (tenant_b, 1)):
            with runtime.connect() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :t, false)"),
                    {"t": str(tenant)},
                )
                seen = conn.execute(
                    text("SELECT count(*) FROM mod_approvals.approval_withdrawals")
                ).scalar_one()
                assert seen == expected, (tenant, seen)

        call = text(
            "SELECT mod_approvals.record_tenant_withdrawal("
            ":tenant_id, :request_id, :withdrawal_id, :actor_id, "
            "'cp-review', 'cross-tenant', :external_ref)"
        )
        for claimed_tenant, ref in (
            (tenant_b, "forged-other-tenant"),
            (tenant_a, "forged-own-tenant"),
        ):
            with runtime.connect() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :t, false)"),
                    {"t": str(tenant_a)},
                )
                with pytest.raises(DBAPIError):
                    conn.execute(
                        call,
                        {
                            "tenant_id": claimed_tenant,
                            "request_id": target_b,
                            "withdrawal_id": uuid.uuid4(),
                            "actor_id": uuid.uuid4(),
                            "external_ref": ref,
                        },
                    )
                conn.rollback()
    finally:
        runtime.dispose()

    admin = create_engine(admin_url)
    try:
        assert counts() == before
    finally:
        admin.dispose()


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


def test_withdrawal_rows_are_immutable_even_for_the_table_owner(
    migrated_scratch: tuple[str, str, str],
) -> None:
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        approver, requester = uuid.uuid4(), uuid.uuid4()
        revision = PolicyRevision(
            policy_code="fleet.immutable",
            version=1,
            levels=(
                ApprovalLevel(
                    sequence=1,
                    approver_kind=ApproverKind.USER,
                    approver_id=str(approver),
                    quorum=1,
                ),
            ),
        )
        with Session(engine) as db, db.begin():
            publish_platform_policy_version(db, revision=revision)
            request_id = request_platform_approval(
                db,
                policy_code="fleet.immutable",
                policy_version=1,
                subject_type="fleet.plan",
                subject_id="immutable-plan",
                content_digest=DIGEST,
                requested_by=requester,
                idempotency_key="immutable-plan",
            ).request_id
            record_platform_decision(
                db,
                request_id=request_id,
                actor=Actor(actor_id=approver),
                action=DecisionAction.APPROVE,
                content_digest=DIGEST,
            )
        with engine.begin() as conn:
            withdrawal_id = uuid.uuid4()
            conn.execute(
                text(
                    "SELECT mod_approvals.record_platform_withdrawal("
                    ":request, :id, :actor, 'review-1', 'invalid', 'ref-1')"
                ),
                {
                    "id": withdrawal_id,
                    "request": request_id,
                    "actor": uuid.uuid4(),
                },
            )
        for statement in (
            "UPDATE mod_approvals.platform_approval_withdrawals "
            "SET reason = 'changed' WHERE id = :id",
            "DELETE FROM mod_approvals.platform_approval_withdrawals WHERE id = :id",
        ):
            with engine.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
                conn.execute(text(statement), {"id": withdrawal_id})
        with engine.begin() as conn, pytest.raises(DBAPIError):
            conn.execute(
                text(
                    "DELETE FROM mod_approvals.platform_approval_requests "
                    "WHERE id = :id"
                ),
                {"id": request_id},
            )
    finally:
        engine.dispose()


def test_online_role_cannot_forge_withdrawn_or_evidence(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The DB function, not direct online DML, owns the paired transition."""
    admin_url, _, platform_url = migrated_scratch
    admin = create_engine(admin_url)
    online = create_engine(platform_url)
    request_id = uuid.uuid4()
    try:
        with admin.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_requests "
                    "(id, policy_code, policy_version, subject_type, subject_id, "
                    "content_digest, requested_by, state, current_level, "
                    "idempotency_key, completed_at) VALUES "
                    "(:id, 'fleet.plan', 1, 'fleet.plan', 'forgery', "
                    ":digest, :actor, 'approved', 1, 'forgery', now())"
                ),
                {"id": request_id, "digest": DIGEST, "actor": uuid.uuid4()},
            )
        with online.begin() as conn, pytest.raises(DBAPIError):
            conn.execute(
                text(
                    "UPDATE mod_approvals.platform_approval_requests "
                    "SET state = 'withdrawn' WHERE id = :id"
                ),
                {"id": request_id},
            )
        with admin.begin() as conn, pytest.raises(DBAPIError):
            # The owner cannot bypass the paired-evidence invariant either.
            conn.execute(
                text(
                    "UPDATE mod_approvals.platform_approval_requests "
                    "SET state = 'withdrawn' WHERE id = :id"
                ),
                {"id": request_id},
            )
        with online.begin() as conn, pytest.raises(DBAPIError):
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_withdrawals "
                    "(id, request_id, actor_id, authority_ref, reason, "
                    "effective_at, external_ref, approved_at) VALUES "
                    "(:id, :request, :actor, 'fake', 'fake', now(), 'fake', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "request": request_id,
                    "actor": uuid.uuid4(),
                },
            )
        with pytest.raises(DBAPIError):
            with admin.begin() as conn:
                # The deferred trigger fires at COMMIT, not INSERT.
                conn.execute(
                    text(
                        "INSERT INTO mod_approvals.platform_approval_withdrawals "
                        "(id, request_id, actor_id, authority_ref, reason, "
                        "effective_at, external_ref, approved_at) "
                        "SELECT :id, id, :actor, 'orphan', 'orphan', now(), 'orphan', "
                        "completed_at FROM mod_approvals.platform_approval_requests "
                        "WHERE id = :request"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "request": request_id,
                        "actor": uuid.uuid4(),
                    },
                )
    finally:
        admin.dispose()
        online.dispose()


@pytest.mark.parametrize("state", ("pending", "rejected"))
def test_direct_evidence_cannot_relabel_a_nonapproved_request(
    migrated_scratch: tuple[str, str, str], state: str
) -> None:
    """Even the owner cannot insert withdrawal evidence for a non-approval."""
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            request_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_requests "
                    "(id, policy_code, policy_version, subject_type, subject_id, "
                    "content_digest, requested_by, state, current_level, "
                    "idempotency_key) VALUES (:id, 'fleet.plan', 1, 'fleet.plan', "
                    ":subject, :digest, :actor, :state, 1, :key)"
                ),
                {
                    "id": request_id,
                    "subject": f"nonapproved-{state}",
                    "digest": DIGEST,
                    "actor": uuid.uuid4(),
                    "state": state,
                    "key": f"nonapproved-{state}",
                },
            )
        with engine.begin() as conn, pytest.raises(DBAPIError):
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_withdrawals "
                    "(id, request_id, actor_id, authority_ref, reason, "
                    "effective_at, external_ref, approved_at) VALUES "
                    "(:id, :request, :actor, 'fake', 'fake', now(), :ref, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "request": request_id,
                    "actor": uuid.uuid4(),
                    "ref": f"fake-{state}",
                },
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("tenant_plane", (True, False))
def test_direct_database_withdrawal_always_emits_exactly_one_event(
    migrated_scratch: tuple[str, str, str], tenant_plane: bool
) -> None:
    """Direct EXECUTE is authoritative and cannot omit the durable event."""
    admin_url, app_user_url, platform_url = migrated_scratch
    engine = create_engine(admin_url)
    tenant_id = uuid.uuid4()
    requester, approver = uuid.uuid4(), uuid.uuid4()
    revision = PolicyRevision(
        policy_code="fleet.direct",
        version=1,
        levels=(
            ApprovalLevel(
                sequence=1,
                approver_kind=ApproverKind.USER,
                approver_id=str(approver),
                quorum=1,
            ),
        ),
    )
    try:
        if tenant_plane:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, 'Direct')"
                    ),
                    {"id": tenant_id, "slug": f"direct-{tenant_id.hex[:12]}"},
                )
        with Session(engine) as db, db.begin():
            if tenant_plane:
                db.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": str(tenant_id)},
                )
                publish_tenant_policy_version(
                    db, tenant_id=tenant_id, revision=revision
                )
                request_id = request_tenant_approval(
                    db,
                    tenant_id=tenant_id,
                    policy_code="fleet.direct",
                    policy_version=1,
                    subject_type="fleet.plan",
                    subject_id="direct-plan",
                    content_digest=DIGEST,
                    requested_by=requester,
                    idempotency_key="direct-plan",
                ).request_id
                record_tenant_decision(
                    db,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    actor=Actor(actor_id=approver),
                    action=DecisionAction.APPROVE,
                    content_digest=DIGEST,
                )
            else:
                publish_platform_policy_version(db, revision=revision)
                request_id = request_platform_approval(
                    db,
                    policy_code="fleet.direct",
                    policy_version=1,
                    subject_type="fleet.plan",
                    subject_id="direct-plan",
                    content_digest=DIGEST,
                    requested_by=requester,
                    idempotency_key="direct-plan",
                ).request_id
                record_platform_decision(
                    db,
                    request_id=request_id,
                    actor=Actor(actor_id=approver),
                    action=DecisionAction.APPROVE,
                    content_digest=DIGEST,
                )

        if tenant_plane:
            command = text(
                "SELECT mod_approvals.record_tenant_withdrawal("
                ":tenant, :request, :withdrawal, :actor, "
                "'cp-review', 'invalidated', 'direct-ref')"
            )
            outbox_select = text(
                "SELECT payload, status, attempts, correlation_id "
                "FROM public.outbox_events"
            )
            outbox_count = text("SELECT count(*) FROM public.outbox_events")
        else:
            command = text(
                "SELECT mod_approvals.record_platform_withdrawal("
                ":request, :withdrawal, :actor, "
                "'cp-review', 'invalidated', 'direct-ref')"
            )
            outbox_select = text(
                "SELECT payload, status, attempts, correlation_id "
                "FROM public.platform_outbox_events"
            )
            outbox_count = text("SELECT count(*) FROM public.platform_outbox_events")
        parameters = {
            "tenant": tenant_id,
            "request": request_id,
            "withdrawal": uuid.uuid4(),
            "actor": requester,
        }
        # Execute as the actual online role, not as the migration owner.
        online = create_engine(app_user_url if tenant_plane else platform_url)
        with online.begin() as conn:
            if tenant_plane:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": str(tenant_id)},
                )
            conn.execute(command, parameters)
        online.dispose()

        with Session(engine) as db, db.begin():
            if tenant_plane:
                db.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": str(tenant_id)},
                )
                detail = get_tenant_request(
                    db, tenant_id=tenant_id, request_id=request_id
                )
            else:
                detail = get_platform_request(db, request_id=request_id)
            assert detail is not None and detail.withdrawal is not None
            expected = ApprovalEvent(
                event_type="approval.withdrawn",
                subject_type=detail.request.subject_type,
                subject_id=detail.request.subject_id,
                request_id=request_id,
                policy_code=detail.request.policy_code,
                policy_version=detail.request.policy_version,
                content_digest=detail.request.content_digest,
                state=ApprovalState.WITHDRAWN,
                withdrawal=detail.withdrawal,
            ).payload()
            row = db.execute(outbox_select).one()
            assert row.payload == expected
            assert (row.status, row.attempts, row.correlation_id) == (
                "pending",
                0,
                "direct-ref",
            )

        online = create_engine(app_user_url if tenant_plane else platform_url)
        with pytest.raises(DBAPIError):
            with online.begin() as conn:
                if tenant_plane:
                    conn.execute(
                        text("SELECT set_config('app.current_tenant', :t, true)"),
                        {"t": str(tenant_id)},
                    )
                conn.execute(command, parameters)
        online.dispose()
        with engine.begin() as conn:
            if tenant_plane:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": str(tenant_id)},
                )
            assert conn.execute(outbox_count).scalar_one() == 1
    finally:
        engine.dispose()


def test_owner_composed_withdrawal_cannot_omit_outbox(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """Paired raw DML by the table owner must still stage the typed event."""
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    approver, requester = uuid.uuid4(), uuid.uuid4()
    revision = PolicyRevision(
        policy_code="fleet.owner",
        version=1,
        levels=(
            ApprovalLevel(
                sequence=1,
                approver_kind=ApproverKind.USER,
                approver_id=str(approver),
                quorum=1,
            ),
        ),
    )
    try:
        with Session(engine) as db, db.begin():
            publish_platform_policy_version(db, revision=revision)
            request_id = request_platform_approval(
                db,
                policy_code="fleet.owner",
                policy_version=1,
                subject_type="fleet.plan",
                subject_id="owner-plan",
                content_digest=DIGEST,
                requested_by=requester,
                idempotency_key="owner-plan",
            ).request_id
            record_platform_decision(
                db,
                request_id=request_id,
                actor=Actor(actor_id=approver),
                action=DecisionAction.APPROVE,
                content_digest=DIGEST,
            )
        withdrawal_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_withdrawals "
                    "(id, request_id, actor_id, authority_ref, reason, "
                    "effective_at, external_ref, approved_at) "
                    "SELECT :withdrawal, id, :actor, 'cp-review', "
                    "'invalidated', clock_timestamp(), 'owner-ref', completed_at "
                    "FROM mod_approvals.platform_approval_requests WHERE id = :request"
                ),
                {
                    "withdrawal": withdrawal_id,
                    "actor": requester,
                    "request": request_id,
                },
            )
            conn.execute(
                text(
                    "UPDATE mod_approvals.platform_approval_requests "
                    "SET state = 'withdrawn' WHERE id = :request"
                ),
                {"request": request_id},
            )
        with Session(engine) as db, db.begin():
            detail = get_platform_request(db, request_id=request_id)
            assert detail is not None and detail.withdrawal is not None
            expected = ApprovalEvent(
                event_type="approval.withdrawn",
                subject_type=detail.request.subject_type,
                subject_id=detail.request.subject_id,
                request_id=request_id,
                policy_code=detail.request.policy_code,
                policy_version=detail.request.policy_version,
                content_digest=detail.request.content_digest,
                state=ApprovalState.WITHDRAWN,
                withdrawal=detail.withdrawal,
            ).payload()
            rows = db.execute(
                text(
                    "SELECT id, payload, event_type FROM public.platform_outbox_events"
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].id == withdrawal_id
            assert rows[0].event_type == "approval.withdrawn"
            assert rows[0].payload == expected
    finally:
        engine.dispose()


def _ap_0003_operations(conn: Connection, planes: tuple[ModulePlane, ...]):
    """Run one revision against a real connection, with explicit plane intent."""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    path = APPROVALS_VERSIONS / "ap_0003_withdrawals.py"
    spec = importlib.util.spec_from_file_location("ap_0003_live_probe", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(MigrationContext.configure(conn))
    migration.selected_module_planes = lambda _code: frozenset(planes)
    return migration


def _seed_withdrawal_for_downgrade(admin_url: str, *, tenant: bool) -> None:
    """Create real approved history and withdraw through the public owner."""
    from dotmac_approvals.outbox import withdraw_tenant_approval

    engine = create_engine(admin_url)
    tenant_id = uuid.uuid4()
    requester, approver = uuid.uuid4(), uuid.uuid4()
    revision = PolicyRevision(
        policy_code="fleet.downgrade",
        version=1,
        levels=(
            ApprovalLevel(
                sequence=1,
                approver_kind=ApproverKind.USER,
                approver_id=str(approver),
                quorum=1,
            ),
        ),
    )
    try:
        if tenant:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, 'Downgrade')"
                    ),
                    {"id": tenant_id, "slug": f"downgrade-{tenant_id.hex[:12]}"},
                )
        with Session(engine) as db, db.begin():
            if tenant:
                db.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": str(tenant_id)},
                )
                publish_tenant_policy_version(
                    db, tenant_id=tenant_id, revision=revision
                )
                request_id = request_tenant_approval(
                    db,
                    tenant_id=tenant_id,
                    policy_code="fleet.downgrade",
                    policy_version=1,
                    subject_type="fleet.plan",
                    subject_id="downgrade-plan",
                    content_digest=DIGEST,
                    requested_by=requester,
                    idempotency_key="downgrade-plan",
                ).request_id
                record_tenant_decision(
                    db,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    actor=Actor(actor_id=approver),
                    action=DecisionAction.APPROVE,
                    content_digest=DIGEST,
                )
                withdraw_tenant_approval(
                    db,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    actor=Actor(actor_id=requester),
                    authority_ref="cp-review",
                    reason="downgrade refusal proof",
                    external_ref="downgrade-ref",
                )
            else:
                publish_platform_policy_version(db, revision=revision)
                request_id = request_platform_approval(
                    db,
                    policy_code="fleet.downgrade",
                    policy_version=1,
                    subject_type="fleet.plan",
                    subject_id="downgrade-plan",
                    content_digest=DIGEST,
                    requested_by=requester,
                    idempotency_key="downgrade-plan",
                ).request_id
                record_platform_decision(
                    db,
                    request_id=request_id,
                    actor=Actor(actor_id=approver),
                    action=DecisionAction.APPROVE,
                    content_digest=DIGEST,
                )
                withdraw_platform_approval(
                    db,
                    request_id=request_id,
                    actor=Actor(actor_id=requester),
                    authority_ref="cp-review",
                    reason="downgrade refusal proof",
                    external_ref="downgrade-ref",
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "scratch_name, planes, evidence_is_tenant",
    (
        ("tenant_only_scratch", (ModulePlane.TENANT,), True),
        ("platform_only_scratch", (ModulePlane.PLATFORM,), False),
        (
            "migrated_scratch",
            (ModulePlane.TENANT, ModulePlane.PLATFORM),
            False,
        ),
    ),
)
def test_withdrawal_downgrade_refuses_evidence_before_any_drop(
    request: pytest.FixtureRequest,
    scratch_name: str,
    planes: tuple[ModulePlane, ...],
    evidence_is_tenant: bool,
) -> None:
    scratch = request.getfixturevalue(scratch_name)
    admin_url = scratch[0] if isinstance(scratch, tuple) else scratch
    _seed_withdrawal_for_downgrade(admin_url, tenant=evidence_is_tenant)
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            migration = _ap_0003_operations(conn, planes)
            with pytest.raises(RuntimeError, match="immutable withdrawal evidence"):
                migration.downgrade()
            for plane in planes:
                table = (
                    "mod_approvals.approval_withdrawals"
                    if plane is ModulePlane.TENANT
                    else "mod_approvals.platform_approval_withdrawals"
                )
                assert (
                    conn.execute(
                        text("SELECT to_regclass(:name)"), {"name": table}
                    ).scalar_one()
                    is not None
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "scratch_name, planes",
    (
        ("tenant_only_scratch", (ModulePlane.TENANT,)),
        ("platform_only_scratch", (ModulePlane.PLATFORM,)),
        ("migrated_scratch", (ModulePlane.TENANT, ModulePlane.PLATFORM)),
    ),
)
def test_empty_withdrawal_downgrade_can_reupgrade_exact_selection(
    request: pytest.FixtureRequest,
    scratch_name: str,
    planes: tuple[ModulePlane, ...],
) -> None:
    scratch = request.getfixturevalue(scratch_name)
    admin_url = scratch[0] if isinstance(scratch, tuple) else scratch
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            migration = _ap_0003_operations(conn, planes)
            migration.downgrade()
            migration.upgrade()
            for plane in planes:
                table = (
                    "mod_approvals.approval_withdrawals"
                    if plane is ModulePlane.TENANT
                    else "mod_approvals.platform_approval_withdrawals"
                )
                assert (
                    conn.execute(
                        text("SELECT to_regclass(:name)"), {"name": table}
                    ).scalar_one()
                    is not None
                )
    finally:
        engine.dispose()


def test_competing_platform_withdrawals_have_one_durable_winner(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The request lock and unique row agree under real PostgreSQL concurrency."""
    _, _, platform_url = migrated_scratch
    engine = create_engine(platform_url)
    approver, requester = uuid.uuid4(), uuid.uuid4()
    revision = PolicyRevision(
        policy_code="fleet.plan",
        version=1,
        levels=(
            ApprovalLevel(
                sequence=1,
                approver_kind=ApproverKind.USER,
                approver_id=str(approver),
                quorum=1,
            ),
        ),
    )
    try:
        with Session(engine) as db, db.begin():
            publish_platform_policy_version(db, revision=revision)
            request_id = request_platform_approval(
                db,
                policy_code="fleet.plan",
                policy_version=1,
                subject_type="fleet.plan",
                subject_id="plan-race",
                content_digest=DIGEST,
                requested_by=requester,
                idempotency_key="plan-race",
            ).request_id
            record_platform_decision(
                db,
                request_id=request_id,
                actor=Actor(actor_id=approver),
                action=DecisionAction.APPROVE,
                content_digest=DIGEST,
            )

        start = Barrier(2)

        def attempt(ref: str) -> tuple[str, int]:
            start.wait(timeout=10)
            try:
                with Session(engine) as db, db.begin():
                    outcome = withdraw_platform_approval(
                        db,
                        request_id=request_id,
                        actor=Actor(actor_id=requester),
                        authority_ref="cp-review",
                        reason="plan invalidated",
                        external_ref=ref,
                    )
                    return "accepted", len(outcome.events)
            except WithdrawalReferenceConflict:
                return "conflict", 0

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ("revoke-a", "revoke-b")))
        assert sorted(results) == [("accepted", 1), ("conflict", 0)]
        winning_ref = next(
            ref
            for ref, result in zip(("revoke-a", "revoke-b"), results, strict=True)
            if result[0] == "accepted"
        )
        with Session(engine) as db, db.begin():
            replay = withdraw_platform_approval(
                db,
                request_id=request_id,
                actor=Actor(actor_id=requester),
                authority_ref="cp-review",
                reason="plan invalidated",
                external_ref=winning_ref,
            )
            assert replay.events == ()
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM "
                        "mod_approvals.platform_approval_withdrawals "
                        "WHERE request_id = :request"
                    ),
                    {"request": request_id},
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM public.platform_outbox_events "
                        "WHERE event_type = 'approval.withdrawn'"
                    )
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_cross_request_reference_race_preserves_unrelated_caller_work(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """A unique-reference loser gets a typed conflict, not an aborted session."""
    _, _, platform_url = migrated_scratch
    engine = create_engine(platform_url)
    approver, requester = uuid.uuid4(), uuid.uuid4()
    revision = PolicyRevision(
        policy_code="fleet.race",
        version=1,
        levels=(
            ApprovalLevel(
                sequence=1,
                approver_kind=ApproverKind.USER,
                approver_id=str(approver),
                quorum=1,
            ),
        ),
    )
    try:
        with Session(engine) as db, db.begin():
            publish_platform_policy_version(db, revision=revision)
            request_ids = []
            for index in (1, 2):
                request_id = request_platform_approval(
                    db,
                    policy_code="fleet.race",
                    policy_version=1,
                    subject_type="fleet.plan",
                    subject_id=f"plan-cross-{index}",
                    content_digest=DIGEST,
                    requested_by=requester,
                    idempotency_key=f"cross-{index}",
                ).request_id
                record_platform_decision(
                    db,
                    request_id=request_id,
                    actor=Actor(actor_id=approver),
                    action=DecisionAction.APPROVE,
                    content_digest=DIGEST,
                )
                request_ids.append(request_id)

        start = Barrier(2)

        def attempt(index: int) -> str:
            with Session(engine) as db, db.begin():
                # This write belongs to the caller, not withdrawal. A failed
                # reference race must not wipe its outer transaction.
                request_platform_approval(
                    db,
                    policy_code="fleet.race",
                    policy_version=1,
                    subject_type="fleet.plan",
                    subject_id=f"unrelated-{index}",
                    content_digest=DIGEST,
                    requested_by=requester,
                    idempotency_key=f"unrelated-{index}",
                )
                start.wait(timeout=10)
                try:
                    withdraw_platform_approval(
                        db,
                        request_id=request_ids[index],
                        actor=Actor(actor_id=requester),
                        authority_ref="cp-review",
                        reason="same external act",
                        external_ref="cross-shared-ref",
                    )
                except WithdrawalReferenceConflict:
                    return "conflict"
                return "accepted"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, (0, 1)))
        assert sorted(results) == ["accepted", "conflict"]
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM "
                        "mod_approvals.platform_approval_withdrawals "
                        "WHERE external_ref = 'cross-shared-ref'"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM public.platform_outbox_events "
                        "WHERE event_type = 'approval.withdrawn'"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM mod_approvals.platform_approval_requests "
                        "WHERE idempotency_key LIKE 'unrelated-%'"
                    )
                ).scalar_one()
                == 2
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


@contextlib.contextmanager
def _bound_prerequisites() -> Iterator[None]:
    """Install this assembly's bindings, and put back whatever was there.

    Bindings are process state; a test that installs and walks away makes the
    NEXT test's result depend on file order.
    """
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        yield
    finally:
        install_prerequisite_bindings(previous)


# ── `outbox_relay.v1` is declared, and verified against the real catalogue ──
#
# `dotmac_approvals.outbox` enqueues into the kernel relay at REQUEST time and
# `ap_0001` creates neither table, so from a1 through a4 the dependency existed
# only inside two function bodies. `ap_0002` declares it; these prove the
# declaration reaches the verifier, and that the verifier is looking at the
# things that actually matter rather than at a table name.


def _relay_requires() -> tuple[str, ...]:
    """The tuple `ap_0002` itself verifies, loaded from the migration.

    Read from the module rather than restated here on purpose: a test that
    hard-codes `("outbox_relay.v1",)` still passes after someone empties the
    migration's own tuple, which is the mistake it exists to catch.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ap_0002_probe", APPROVALS_VERSIONS / "ap_0002_outbox_relay.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.REQUIRES)


@contextlib.contextmanager
def _broken_relay(admin_url: str, statement: str) -> Iterator[Connection]:
    """Apply one DDL break, hand back the connection, roll it back.

    The break lives in an open transaction on the SAME connection the verifier
    reads, so the damage is visible to the check and to nothing else — no
    second migrated database per case.
    """
    engine = create_engine(admin_url)
    conn = engine.connect()
    transaction = conn.begin()
    try:
        conn.execute(text(statement))
        yield conn
    finally:
        transaction.rollback()
        conn.close()
        engine.dispose()


def test_the_declared_relay_prerequisite_is_satisfied_after_migration(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The positive half: what `ap_0002` verifies passes on a real database."""
    from dotmac_kernel.migrations.verify import require_prerequisites

    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    with _bound_prerequisites(), engine.connect() as conn:
        require_prerequisites(conn, _relay_requires())
    engine.dispose()


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        pytest.param(
            "ALTER TABLE public.outbox_events RENAME TO outbox_events_gone",
            "does not exist",
            id="tenant-relay-table-absent",
        ),
        pytest.param(
            "ALTER TABLE public.platform_outbox_events "
            "RENAME TO platform_outbox_events_gone",
            "does not exist",
            id="platform-relay-table-absent",
        ),
        pytest.param(
            "ALTER TABLE public.outbox_events NO FORCE ROW LEVEL SECURITY",
            "FORCE",
            id="tenant-relay-unforced",
        ),
        pytest.param(
            "REVOKE EXECUTE ON FUNCTION public.claim_outbox_batch(text, integer, "
            "integer) FROM outbox_dispatcher",
            "EXECUTE",
            id="dispatcher-cannot-claim",
        ),
    ],
)
def test_the_relay_prerequisite_refuses_a_provider_missing_one_effect(
    migrated_scratch: tuple[str, str, str], statement: str, expected: str
) -> None:
    """One break per case, each asserting the message for THAT observable.

    Deliberately a subset of the kernel's own 25 refusals
    (`tests/test_outbox_relay_prerequisite.py`), not a copy of them: the kernel
    owns proving its verifier, and this module owns proving that ITS
    declaration reaches that verifier against a database its own lineage
    migrated. Duplicating the full matrix here would give one invariant two
    owners that drift.
    """
    from dotmac_kernel.migrations.verify import (
        PrerequisiteNotSatisfiedError,
        require_prerequisites,
    )

    admin_url, _, _ = migrated_scratch
    with _bound_prerequisites(), _broken_relay(admin_url, statement) as conn:
        with pytest.raises(PrerequisiteNotSatisfiedError, match=expected):
            require_prerequisites(conn, _relay_requires())


def test_the_relay_refusals_are_not_refusing_everything(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The specificity companion.

    Every case above damages the relay and expects a refusal, so all of them
    would still pass against a verifier that refused unconditionally — or if
    `_broken_relay`'s open transaction poisoned the connection for any query.
    This breaks something the relay contract does not mention (this module's
    own table) and requires SILENCE.
    """
    from dotmac_kernel.migrations.verify import require_prerequisites

    admin_url, _, _ = migrated_scratch
    with (
        _bound_prerequisites(),
        _broken_relay(
            admin_url, "ALTER TABLE mod_approvals.approval_requests RENAME TO gone"
        ) as conn,
    ):
        require_prerequisites(conn, _relay_requires())


def test_the_declared_database_catalog_matches_the_live_migrated_schema(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The manifest's catalogue contribution — including the ap_0003 tables —
    is observed against the migrated PostgreSQL schema (both planes) and must
    show zero drift; a declared fact is never trusted as hand-derived."""
    import dotmac_approvals
    from dotmac_approvals.manifest import module
    from dotmac_kernel import (
        ComposedDatabaseLineageHeadV1,
        DatabaseCatalogOwnerKind,
        DatabaseCatalogOwnerV1,
        ModuleDatabaseCatalogSnapshot,
        compare_module_database_catalog,
        observe_postgres_tables_columns,
    )

    admin_url, _, _ = migrated_scratch
    snapshot = ModuleDatabaseCatalogSnapshot.from_manifest(
        module,
        distribution_name="dotmac-approvals",
        distribution_version=dotmac_approvals.__version__,
        composed_lineage_head=ComposedDatabaseLineageHeadV1(
            DatabaseCatalogOwnerV1(DatabaseCatalogOwnerKind.MODULE, module.code),
            "ap_0003_withdrawals",
        ),
    )
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            observation = observe_postgres_tables_columns(
                conn, schemas=("mod_approvals",)
            )
    finally:
        engine.dispose()
    comparison = compare_module_database_catalog(snapshot, observation)
    drifts = [
        f"{d.table}.{d.column} {d.attribute.value} {d.direction.value}: "
        f"declared={d.declared!r} observed={d.observed!r}"
        for d in comparison.drifts
    ]
    assert drifts == [], "\n".join(drifts)
    assert comparison.measurement_issues == ()
    declared_tables = {table.name for table in snapshot.tables}
    assert {"approval_withdrawals", "platform_approval_withdrawals"} <= declared_tables
