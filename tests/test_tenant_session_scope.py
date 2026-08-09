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

Requires a real Postgres — FORCE RLS is the whole point (`make test-db-up` +
`TEST_DATABASE_URL`/`TEST_MIGRATION_DATABASE_URL`, as with the other canaries
in this directory).
"""

from __future__ import annotations

import pytest
from dotmac_kernel.db import SessionLocal, set_tenant, tenant_session
from dotmac_kernel.models import Role
from sqlalchemy import select, text
from sqlalchemy.orm import Session


def _role_names(db: Session) -> set[str]:
    return set(db.scalars(select(Role.name)).all())


def test_a_bare_session_is_blind_not_loud(admin_session: Session, tenant_a) -> None:
    """The bug this boundary exists to prevent: zero rows, no error.

    Asserting the broken behaviour on purpose. If RLS ever failed *open*, this
    test flips red and the whole rationale below it needs rereading.
    """
    seeded = _role_names(admin_session)
    assert seeded, "fixture should have given tenant_a at least one role"

    db = SessionLocal()
    try:
        assert _role_names(db) == set()  # silent, not an exception
    finally:
        db.rollback()
        db.close()


def test_tenant_session_applies_the_scope(tenant_a) -> None:
    with tenant_session(tenant_a.id) as db:
        assert _role_names(db), "a scoped session must see its own tenant's rows"


def test_tenant_session_does_not_widen_to_other_tenants(admin_session: Session, tenant_a, tenant_b) -> None:
    """The obvious over-correction is a BYPASSRLS role, which would make every
    non-request caller cross-tenant. Pin that this is a scope, not a bypass."""
    a_role = Role(tenant_id=tenant_a.id, name="scope-canary-a", description="")
    admin_session.add(a_role)
    admin_session.commit()
    try:
        with tenant_session(tenant_b.id) as db:
            assert "scope-canary-a" not in _role_names(db)
    finally:
        admin_session.delete(a_role)
        admin_session.commit()


def test_the_scope_is_applied_before_the_caller_gets_the_session(tenant_a) -> None:
    """No window in which a query can run unscoped.

    `set_tenant` inside the `with` body would be too late for anything the
    caller ran on its first line — so the boundary must do it, not the caller.
    """
    with tenant_session(tenant_a.id) as db:
        current = db.execute(text("SELECT current_setting('app.current_tenant', true)")).scalar()
        assert current == str(tenant_a.id)


def test_tenant_session_rolls_back_on_error(admin_session: Session, tenant_a) -> None:
    """Same owned-boundary contract as `platform_session`."""
    with pytest.raises(RuntimeError):
        with tenant_session(tenant_a.id) as db:
            db.add(Role(tenant_id=tenant_a.id, name="rollback-canary", description=""))
            db.flush()
            raise RuntimeError("boom")

    assert "rollback-canary" not in _role_names(admin_session)


def test_set_tenant_is_what_get_db_uses(tenant_a) -> None:
    """`set_tenant` is the one writer of the setting; `get_db` calls it too.

    Pinned because the previous shape — the SQL inline in `get_db` — is what
    left every other caller having to know to reproduce it.
    """
    db = SessionLocal()
    try:
        set_tenant(db, tenant_a.id)
        assert _role_names(db)
    finally:
        db.rollback()
        db.close()
