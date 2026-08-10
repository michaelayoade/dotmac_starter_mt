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
    set_tenant,
    tenant_session,
    tenant_session_by_slug,
)
from dotmac_kernel.exceptions import NotFoundError
from dotmac_kernel.models import Role
from sqlalchemy import select, text
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
