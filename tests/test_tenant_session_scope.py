"""Canary — non-request callers must get the RLS scope, and fail loudly if not.

`platform_session` gave non-request *platform* code an owned boundary. There
was no equivalent for non-request *tenant* code, so CLI commands, jobs and
workers reached for `SessionLocal` directly — the import the public-surface
test forbids, for exactly this reason.

The failure mode is what makes it worth a canary rather than a docstring. RLS
fails **closed**: an unscoped session does not raise, it returns zero rows. A
caller cannot tell "this tenant has no data" from "I cannot see this tenant's
data", so the bug presents as a clean result.

That is not hypothetical. `dotmac_academy_app` carries a fork of
`dotmac_kernel.db`; its `audit-banks` command opened a bare `SessionLocal` and
printed `TOTAL 0 0` against a production database holding 333 question banks
for the tenant it was asked about. Its `load-banks` command was blind the same
way and had deployed nothing for 37 commits without ever failing.

`test_a_bare_session_is_blind_not_loud` is the one that documents the danger:
it asserts the *broken* behaviour is silent, which is why the boundary has to
be the easy thing to reach for.

Each test seeds its own row rather than leaning on `tenant_a` having any — the
fixture provisions a tenant, not its contents, and a canary that depends on
someone else's seed data tells you about the seed, not the scope.

Requires a real Postgres — FORCE RLS is the whole point (`make test-db-up` +
`TEST_DATABASE_URL`/`TEST_MIGRATION_DATABASE_URL`, as with the other canaries
in this directory).
"""

from __future__ import annotations

import uuid

import pytest
from dotmac_kernel.db import (
    SessionLocal,
    resolver_session,
    runtime,
    set_tenant,
    tenant_scope,
    tenant_session,
    tenant_session_by_slug,
)
from dotmac_kernel.exceptions import NotFoundError
from dotmac_kernel.models import Role, Tenant
from dotmac_kernel.session_runtime import DatabaseRuntime
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session


def _seed_role(admin_session: Session, tenant, label: str) -> Role:
    """A role belonging to `tenant`, written with the BYPASSRLS admin session."""
    suffix = uuid.uuid4().hex[:8]
    role = Role(tenant_id=tenant.id, slug=f"{label}-{suffix}", name=f"{label}-{suffix}")
    admin_session.add(role)
    admin_session.commit()
    return role


def _slugs(db: Session) -> set[str]:
    return set(db.scalars(select(Role.slug)).all())


def test_a_bare_session_is_blind_not_loud(admin_session: Session, tenant_a) -> None:
    """The bug this boundary exists to prevent: zero rows, no error.

    Asserting the broken behaviour on purpose. If RLS ever failed *open*, this
    test flips red and the whole rationale above it needs rereading.
    """
    role = _seed_role(admin_session, tenant_a, "blind-canary")
    try:
        db = SessionLocal()
        try:
            assert role.slug not in _slugs(db)  # silent, not an exception
        finally:
            db.rollback()
            db.close()
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_tenant_session_applies_the_scope(admin_session: Session, tenant_a) -> None:
    role = _seed_role(admin_session, tenant_a, "scope-canary")
    try:
        with tenant_session(tenant_a.id) as db:
            assert role.slug in _slugs(db)
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_tenant_session_does_not_widen_to_other_tenants(
    admin_session: Session, tenant_a, tenant_b
) -> None:
    """The obvious over-correction is a BYPASSRLS role, which would make every
    non-request caller cross-tenant. Pin that this is a scope, not a bypass."""
    role = _seed_role(admin_session, tenant_a, "widen-canary")
    try:
        with tenant_session(tenant_b.id) as db:
            assert role.slug not in _slugs(db)
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_the_scope_survives_a_commit_inside_the_block(
    admin_session: Session, tenant_a
) -> None:
    """The 0.1.0a28 defect. Every intended caller commits more than once.

    `SET LOCAL` dies with the transaction, so the first commit inside the block
    left the rest of it unscoped — and against a fail-closed policy that shows
    up as a row the session just wrote coming back missing, not as an error.
    """
    role = _seed_role(admin_session, tenant_a, "commit-canary")
    try:
        with tenant_session(tenant_a.id) as db:
            assert role.slug in _slugs(db)
            db.commit()
            assert role.slug in _slugs(db)
            db.commit()
            assert role.slug in _slugs(db)
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_the_scope_is_reset_before_the_connection_is_reused(
    admin_session: Session, tenant_a
) -> None:
    """A session-level scope must not ride the pooled connection out.

    That would be the cross-tenant leak `get_db` uses SET LOCAL to avoid, so the
    fix for one hazard must not introduce the other.
    """
    role = _seed_role(admin_session, tenant_a, "reset-canary")
    try:
        with tenant_session(tenant_a.id) as db:
            assert role.slug in _slugs(db)

        leaked = SessionLocal()
        try:
            assert role.slug not in _slugs(leaked)
        finally:
            leaked.rollback()
            leaked.close()
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_the_scope_is_applied_before_the_caller_gets_the_session(tenant_a) -> None:
    """No window in which a query can run unscoped.

    `set_tenant` inside the `with` body would be too late for anything the
    caller ran on its first line — so the boundary must do it, not the caller.
    """
    with tenant_session(tenant_a.id) as db:
        sql = text("SELECT current_setting('app.current_tenant', true)")
        current = db.execute(sql).scalar()
        assert current == str(tenant_a.id)


def test_tenant_session_rolls_back_on_error(admin_session: Session, tenant_a) -> None:
    """Same owned-boundary contract as `platform_session`."""
    slug = f"rollback-canary-{uuid.uuid4().hex[:8]}"

    with pytest.raises(RuntimeError):
        with tenant_session(tenant_a.id) as db:
            db.add(Role(tenant_id=tenant_a.id, slug=slug, name=slug))
            db.flush()
            raise RuntimeError("boom")

    assert slug not in _slugs(admin_session)


def test_set_tenant_is_what_get_db_uses(admin_session: Session, tenant_a) -> None:
    """`set_tenant` is the one writer of the setting; `get_db` calls it too.

    Pinned because the previous shape — the SQL inline in `get_db` — is what
    left every other caller having to know to reproduce it.
    """
    role = _seed_role(admin_session, tenant_a, "setter-canary")
    try:
        db = SessionLocal()
        try:
            set_tenant(db, tenant_a.id)
            assert role.slug in _slugs(db)
        finally:
            db.rollback()
            db.close()
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_by_slug_resolves_and_scopes_in_one_session(
    admin_session: Session, tenant_a
) -> None:
    """The two steps every assembly CLI needs, without touching SessionLocal."""
    role = _seed_role(admin_session, tenant_a, "slug-canary")
    try:
        with tenant_session_by_slug(tenant_a.slug) as (db, tenant):
            assert tenant.id == tenant_a.id
            assert role.slug in _slugs(db)
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_by_slug_yields_an_attached_tenant(tenant_a) -> None:
    """Attached, not detached — the caller reads attributes off it.

    Resolving in a separate session would hand back a detached instance whose
    next attribute access either refreshes or raises. Sharing one session is
    what makes `tenant.id` safe to use inside the block.
    """
    with tenant_session_by_slug(tenant_a.slug) as (db, tenant):
        assert tenant.slug == tenant_a.slug
        assert tenant in db


def test_by_slug_raises_rather_than_yielding_none(tenant_a) -> None:
    """A CLI handed a None carries on and prints an empty report."""
    with pytest.raises(NotFoundError):
        with tenant_session_by_slug("no-such-tenant-abcdef") as (_db, _t):
            raise AssertionError("body must not run")


def test_by_slug_does_not_widen_to_other_tenants(
    admin_session: Session, tenant_a, tenant_b
) -> None:
    role = _seed_role(admin_session, tenant_a, "slug-widen-canary")
    try:
        with tenant_session_by_slug(tenant_b.slug) as (db, _tenant):
            assert role.slug not in _slugs(db)
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_by_slug_resets_the_scope_on_exit(admin_session: Session, tenant_a) -> None:
    """Same pooled-connection hazard as `tenant_session`."""
    role = _seed_role(admin_session, tenant_a, "slug-reset-canary")
    try:
        with tenant_session_by_slug(tenant_a.slug) as (db, _tenant):
            assert role.slug in _slugs(db)

        leaked = SessionLocal()
        try:
            assert role.slug not in _slugs(leaked)
        finally:
            leaked.rollback()
            leaked.close()
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_resolver_session_reads_the_tenancy_tables(
    admin_session: Session, tenant_a, tenant_b
) -> None:
    """It must see tenants it has not scoped to — that is the whole job.

    This works because `tenants` and `tenant_domains` are deliberately NOT
    RLS-protected: they are read to DECIDE a scope, so they cannot themselves
    depend on one.
    """
    with resolver_session() as db:
        slugs = {t.slug for t in db.query(Tenant).all()}
    assert {tenant_a.slug, tenant_b.slug} <= slugs


def test_resolver_session_cannot_read_tenant_scoped_rows(
    admin_session: Session, tenant_a
) -> None:
    """Unscoped means fails CLOSED, not "sees everything".

    Worth pinning as a security property: `resolver_session` must not become a
    way to read another tenant's data. On an RLS-protected table it sees
    nothing at all, which is correct — and is why it is only useful for the
    tenancy tables.
    """
    role = _seed_role(admin_session, tenant_a, "resolver-scope-canary")
    try:
        with resolver_session() as db:
            assert role.slug not in _slugs(db)
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_resolver_session_clears_an_inherited_scope(
    admin_session: Session, tenant_a, tenant_b
) -> None:
    """A scope left on a pooled connection must not filter the resolver.

    Without the RESET, a connection still scoped to tenant A would hide tenant
    B from `tenants` — and because RLS fails closed the symptom would be a valid
    host resolving to nothing.
    """
    leaked = SessionLocal()
    try:
        set_tenant(leaked, tenant_a.id, transaction_local=False)
    finally:
        leaked.rollback()
        leaked.close()

    with resolver_session() as db:
        slugs = {t.slug for t in db.query(Tenant).all()}
    assert tenant_b.slug in slugs


def test_resolver_session_cannot_write_the_tenancy_tables(tenant_a) -> None:
    """Read-only is enforced by the ROLE, not merely by the rollback.

    `app_user` holds SELECT on `tenants`/`tenant_domains` and nothing else, so a
    resolver cannot mutate the tables it reads even by mistake. That is a
    stronger guarantee than "we roll back", and it is worth pinning: if these
    grants ever widened, `resolver_session` would quietly become an unscoped
    write path.
    """
    slug = f"resolver-write-{uuid.uuid4().hex[:8]}"
    with pytest.raises(ProgrammingError):
        with resolver_session() as db:
            db.add(Tenant(slug=slug, name=slug))
            db.flush()


def test_resolver_result_is_usable_after_the_block(tenant_a) -> None:
    """A resolver hands something back, and its caller reads it later.

    `TenantResolverMiddleware` puts the Tenant on `request.state`; the
    rate-limit and observability middleware read `.id` well after the session
    has closed. Rolling back without expunging first EXPIRES the instance, so it
    comes back alive but hollow and the next attribute access raises
    DetachedInstanceError — which is exactly how this shipped and how CI caught
    it.
    """
    with resolver_session() as db:
        found = db.query(Tenant).filter(Tenant.slug == tenant_a.slug).one()

    # Outside the block, on a closed session: must not raise.
    assert found.slug == tenant_a.slug
    assert found.id == tenant_a.id


# ── the scope mechanic itself (kernel 0.1.0a100) ─────────────────────────────
#
# `tenant_session` is now `tenant_scope` plus an owned boundary, and
# `tenant_scope` is available on its own for a product that owns its session
# lifecycle. The tests above cover the boundary; these cover the mechanic —
# and they are here rather than in `tests/unit/` because every property below
# is only observable against a real fail-closed policy.


def test_tenant_scope_scopes_a_caller_owned_session(
    admin_session: Session, tenant_a
) -> None:
    """The seam for a product with its own session factory.

    It gets the kernel's scope discipline without the kernel's engines — which
    is the whole reason the runtime became instantiable.
    """
    role = _seed_role(admin_session, tenant_a, "caller-owned-canary")
    try:
        db = SessionLocal()
        try:
            assert role.slug not in _slugs(db)  # unscoped: fails closed
            with tenant_scope(db, tenant_a.id):
                assert role.slug in _slugs(db)
        finally:
            db.rollback()
            db.close()
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_tenant_scope_re_arms_after_a_commit(admin_session: Session, tenant_a) -> None:
    """`SET LOCAL` dies with the transaction; the `after_begin` listener is
    what puts it back. Without the re-arm, everything after the first commit
    inside the block runs unscoped — and reads as an empty tenant."""
    role = _seed_role(admin_session, tenant_a, "rearm-canary")
    try:
        db = SessionLocal()
        try:
            with tenant_scope(db, tenant_a.id):
                assert role.slug in _slugs(db)
                db.commit()
                assert role.slug in _slugs(db)
                db.commit()
                assert role.slug in _slugs(db)
        finally:
            db.rollback()
            db.close()
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_the_scope_ends_with_the_block_not_with_the_session(
    admin_session: Session, tenant_a
) -> None:
    """The half a session-level setting got wrong.

    A caller-owned session outlives the `with`, so a scope that outlived it too
    would silently keep answering as that tenant — including after the caller
    moved on to other work. Leaving the block must un-scope it.
    """
    role = _seed_role(admin_session, tenant_a, "block-bound-canary")
    try:
        db = SessionLocal()
        try:
            with tenant_scope(db, tenant_a.id):
                assert role.slug in _slugs(db)
            db.commit()  # ends the last scoped transaction
            assert role.slug not in _slugs(db)
        finally:
            db.rollback()
            db.close()
    finally:
        admin_session.delete(role)
        admin_session.commit()


def test_a_legacy_setting_is_primed_with_the_same_value(tenant_a) -> None:
    """The ERP compatibility case, proven rather than asserted in prose.

    A product mid-migration has tables whose policies predate the module
    lineage contract and read their own setting. Both names must carry the SAME
    tenant, primed together — two statements could leave one armed and the
    other stale, which is not an error, it is a working scope over the wrong
    rows.
    """
    legacy = DatabaseRuntime(
        engine=runtime.engine,
        legacy_tenant_settings=("app.current_org_probe",),
    )
    with legacy.tenant_session(tenant_a.id) as db:
        read = text(
            "SELECT current_setting('app.current_tenant', true), "
            "current_setting('app.current_org_probe', true)"
        )
        canonical, org = db.execute(read).one()
        assert canonical == str(tenant_a.id)
        assert org == str(tenant_a.id)


def test_the_canonical_setting_is_never_displaced_by_a_legacy_one(
    admin_session: Session, tenant_a
) -> None:
    """Declaring a legacy setting must not weaken the isolation that every
    composed module's RLS policy depends on. `app.current_tenant` is a
    cross-repository contract; the legacy name rides alongside it, never
    instead of it."""
    role = _seed_role(admin_session, tenant_a, "legacy-alongside-canary")
    try:
        legacy = DatabaseRuntime(
            engine=runtime.engine,
            legacy_tenant_settings=("app.current_org_probe",),
        )
        with legacy.tenant_session(tenant_a.id) as db:
            assert role.slug in _slugs(db)
    finally:
        admin_session.delete(role)
        admin_session.commit()
