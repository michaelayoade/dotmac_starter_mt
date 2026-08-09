"""Database session.

`get_db` sets the `app.current_tenant` Postgres setting per request so RLS policies
can scope rows to the resolved tenant. `SET LOCAL` is transaction-scoped — the next
request from the connection pool starts with no setting and must set its own.

Code that runs OUTSIDE a request needs the same scope and has no `request.state`
to take it from. `tenant_session` is that boundary, the tenant-scoped sibling of
`platform_session`. Reaching for `SessionLocal` directly instead is the one
mistake this module cannot catch for you: RLS fails **closed**, so an unscoped
session returns zero rows rather than raising, and the caller cannot tell an
empty tenant from an invisible one. A `dotmac_academy_app` audit command did
exactly this and reported a clean estate against a database holding 333 banks.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from fastapi import Request
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from dotmac_kernel.config import settings

__all__ = [
    "conflict_savepoint",
    "engine",
    "get_db",
    "get_platform_db",
    "platform_engine",
    "platform_session",
    "set_tenant",
    "tenant_session",
]

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
platform_engine = create_engine(
    settings.platform_database_url or settings.database_url,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=2,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
PlatformSessionLocal = sessionmaker(
    bind=platform_engine, autocommit=False, autoflush=False
)


def set_tenant(db: Session, tenant_id: object) -> None:
    """Apply the RLS tenant scope to `db`. The one writer of the setting.

    Split out of `get_db` so the scope has a name. While it was inline there,
    it was reachable only from the request cycle, and every other caller had to
    know to reproduce the SQL — which is a thing you can only remember to do if
    you already know RLS fails closed.

    `SET LOCAL` is transaction-scoped, so this applies to the current
    transaction only and must be re-applied on a new session.
    """
    db.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


def get_db(request: Request) -> Generator[Session, None, None]:
    """Per-request DB session with tenant context applied for RLS.

    If `request.state.tenant` is None (platform-level routes), no tenant context is
    set — RLS policies will fail closed (zero rows) on any tenant-scoped table.
    Platform code uses a separate `get_platform_db` dependency with explicit grants,
    not the migration/admin role.
    """
    db = SessionLocal()
    try:
        tenant = getattr(request.state, "tenant", None)
        if tenant is not None:
            set_tenant(db, tenant.id)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_platform_db() -> Generator[Session, None, None]:
    """Online platform API DB session.

    Uses PLATFORM_DATABASE_URL (platform_api role) if set, else DATABASE_URL for local
    development. This role must not have BYPASSRLS; migrations and offline maintenance
    use MIGRATION_DATABASE_URL separately.
    """
    db = PlatformSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def platform_session() -> Generator[Session, None, None]:
    """Non-request platform-session boundary (commit on success, rollback on
    error) for code that runs OUTSIDE a request — lifespan seed hooks, jobs.

    Exists so `dotmac_kernel/db.py` stays the ONE module that constructs
    sessions (see `tests/architecture/test_session_authority.py` and
    ARCHITECTURE.md's "Transaction authority" section): callers get the
    same owned-boundary contract as `get_platform_db`, without reaching for
    `PlatformSessionLocal` themselves.
    """
    db = PlatformSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def tenant_session(tenant_id: object) -> Generator[Session, None, None]:
    """Non-request TENANT-scoped session boundary — CLI commands, jobs, workers.

    The sibling of `platform_session`, for the other half of the non-request
    world: work that acts as one tenant rather than as the platform. Commit on
    success, rollback on error, and the RLS scope applied before the caller gets
    the session, so there is no window in which a query can run unscoped.

    Takes a tenant id, not a slug: resolving a slug needs a query, and that
    query would itself have to decide which scope to run under. Callers resolve
    the tenant first — usually against `tenants`, which is not tenant-scoped —
    and pass the id in.

    Prefer this over `SessionLocal` in any non-request caller. An unscoped
    session does not raise; it returns nothing, which reads exactly like a
    tenant that has no data (see the module docstring).
    """
    db = SessionLocal()
    try:
        set_tenant(db, tenant_id)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def conflict_savepoint(db: Session) -> Generator[None, None, None]:
    """Run an expected-conflict mutation inside a SAVEPOINT (F3 fix).

    `get_db` owns the request's outer transaction and issues `SET LOCAL
    app.current_tenant` on it for RLS (see module docstring). The previous
    convention at every conflict site — `try: db.flush() except
    IntegrityError: db.rollback(); raise ConflictError(...)` — called a bare
    `db.rollback()`, which rolls back that ENTIRE outer transaction,
    discarding the `SET LOCAL` along with it. Any DB access the caller's
    `except ConflictError` handler performed afterwards (a web handler
    re-rendering a form from an already-loaded ORM object, or re-querying a
    list) then ran with no tenant context — under FORCE ROW LEVEL SECURITY
    that fails closed: either an `ObjectDeletedError` re-loading an expired
    attribute, or a silently empty result set. See
    `tests/test_conflict_rls_context.py` for the canaries and
    `.superpowers/sdd/task-2-report.md` for the captured pre-fix behavior.

    `db.begin_nested()` issues a `SAVEPOINT` scoped INSIDE the outer
    transaction. Used as a context manager, it commits the SAVEPOINT (a
    no-op release, not the outer COMMIT) if the block exits cleanly, or
    rolls back ONLY the SAVEPOINT — leaving the outer transaction and its
    `SET LOCAL` fully intact — if any exception propagates, then re-raises
    it unchanged (SQLAlchemy's `SessionTransaction.__exit__` never
    swallows).

    IMPORTANT — the mutation (`db.add(...)`, or setting attributes on an
    already-loaded object) must happen INSIDE the `with` block, never
    before it: entering a nested transaction
    (`Session.begin_nested()`/`_take_snapshot`) auto-flushes any
    already-pending/dirty objects on the session BEFORE the SAVEPOINT is
    actually established. `db.add(row)` (or a mutation) issued before
    `conflict_savepoint` would let THAT auto-flush emit the conflicting
    statement with no savepoint yet in place to protect the outer
    transaction — reintroducing the exact bug this helper exists to fix.
    Every conflict site follows this shape:

        try:
            with conflict_savepoint(db):
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            raise ConflictError("...") from exc

    A feature service must never call `db.rollback()` directly — that is
    the hard rule this helper exists to make easy to follow (see CLAUDE.md's
    "Hard rules" section and `tests/architecture/
    test_no_feature_rollback.py`, the enforcement test).
    """
    with db.begin_nested():
        yield
