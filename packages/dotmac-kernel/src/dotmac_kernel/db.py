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
from typing import Any

from fastapi import Request
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from dotmac_kernel._transactions import conflict_savepoint
from dotmac_kernel.config import settings

__all__ = [
    "conflict_savepoint",
    "engine",
    "get_db",
    "get_platform_db",
    "platform_engine",
    "platform_session",
    "resolver_session",
    "set_tenant",
    "tenant_session",
    "tenant_session_by_slug",
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


def set_tenant(
    db: Session, tenant_id: object, *, transaction_local: bool = True
) -> None:
    """Apply the RLS tenant scope to `db`. The one writer of the setting.

    `transaction_local` decides how long the scope lasts, and both values are
    unsafe in the other's place:

    * `True` (SET LOCAL) for `get_db`. Its session is pooled, so a scope that
      outlived the transaction would be inherited by the next request to borrow
      that connection — one tenant reading another's rows.
    * `False` for a session that commits more than once. A commit ends the
      transaction and takes `SET LOCAL` with it; `expire_on_commit` then
      reloads attributes on the next statement, which runs unscoped, and RLS
      fails closed — a row the session itself just wrote comes back as
      `ObjectDeletedError`. `tenant_session` uses this, and resets on exit.

    Split out of `get_db` so the scope has a name. While it was inline there,
    it was reachable only from the request cycle, and every other caller had to
    know to reproduce the SQL — which is a thing you can only remember to do if
    you already know RLS fails closed.
    """
    db.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, :is_local)"),
        {"tenant_id": str(tenant_id), "is_local": transaction_local},
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
        # Session-level, not SET LOCAL: callers commit inside this block (a CLI
        # loop, a worker draining a queue), and a transaction-local scope would
        # be discarded by the first of those commits — leaving the rest of the
        # block running unscoped against a fail-closed policy.
        set_tenant(db, tenant_id, transaction_local=False)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        # The engine is shared, so this connection goes back to the pool. A
        # session-level setting that survived would be inherited by whoever
        # borrows it next — the exact cross-tenant leak `get_db` uses SET LOCAL
        # to avoid. Reset before close, and never let a failed reset mask the
        # caller's own exception.
        try:
            db.execute(text("RESET app.current_tenant"))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


@contextmanager
def resolver_session() -> Generator[Session, None, None]:
    """An UNSCOPED session on the main engine, for deciding which tenant to scope to.

    The one legitimate reason to run without a tenant scope, and until now the
    one thing the public surface had no name for. `TenantResolverMiddleware`
    needs it — you cannot scope to a tenant you are still identifying from a
    Host header — so the kernel reached for `SessionLocal` internally while
    forbidding consumers the same import. That is not a rule with an exception;
    it is a missing primitive, and an assembly reimplementing tenant resolution
    had no choice but to break the rule.

    Why not the alternatives:

    * `tenant_session_by_slug` needs a slug; a resolver has a host.
    * `platform_session` fits semantically — resolution is a platform concern —
      but runs on `platform_engine`, which is `pool_size=2, max_overflow=2`.
      Resolution happens on EVERY request, so that would cap an entire
      application at four connections.

    Read-only by construction: it always rolls back and never commits, so it
    cannot become a back door for unscoped writes.

    "Unscoped" does NOT mean "sees everything". RLS fails closed, so on a
    tenant-scoped table this session sees NOTHING — which is correct, and is why
    it is only useful for the tenancy tables. `tenants` and `tenant_domains` are
    deliberately not RLS-protected precisely because they are read to DECIDE a
    scope and so cannot depend on one.

    It also RESETs the tenant setting before yielding. That is correctness, not
    paranoia — a scope inherited from a pooled connection would filter the
    resolver's own lookup, and because RLS fails closed the symptom would be a
    valid host resolving to no tenant at all.
    """
    db = SessionLocal()
    try:
        db.execute(text("RESET app.current_tenant"))
        yield db
    finally:
        # Detach before rolling back, in that order and deliberately. A resolver
        # exists to hand something back, and its result outlives the session:
        # `TenantResolverMiddleware` puts the Tenant on `request.state` and later
        # middleware reads `tenant.id` long after this block has exited.
        #
        # `rollback()` EXPIRES every instance still in the session, so the object
        # would come back alive but hollow — the next attribute access tries to
        # refresh against a closed session and raises DetachedInstanceError.
        # `expunge_all()` first removes the instances, so the rollback has
        # nothing to expire and they keep the values already loaded.
        db.expunge_all()
        db.rollback()
        db.close()


@contextmanager
def tenant_session_by_slug(slug: str) -> Generator[tuple[Session, Any], None, None]:
    """`tenant_session`, for callers that have a slug rather than an id.

    Yields `(db, tenant)`. This exists because every assembly's CLI needs the
    same two steps — look a tenant up by the slug an operator typed, then act as
    that tenant — and each one solving it privately means each one reaching for
    `SessionLocal`, which is the import the public-surface test forbids.

    The lookup and the scope share one session on purpose. `tenants` is not
    tenant-scoped, so querying it before any scope is set is legal; doing it here
    means the returned `Tenant` is still attached when the caller gets it, and
    that no second connection is taken to resolve a name.

    That legality is also the trap this closes. Because the lookup succeeds
    unscoped, a caller who resolves a tenant and then forgets the scope gets a
    working query followed by silence — which is exactly how
    `dotmac_academy_app` shipped an audit command that reported a clean estate
    it could not see.

    Raises `NotFoundError` rather than yielding `None`: a CLI handed a `None`
    tends to carry on and produce an empty report, which is the failure this
    module exists to stop being quiet.
    """
    from dotmac_kernel.exceptions import NotFoundError

    # Local import: `models` pulls in every declarative class, and `db` is
    # imported early by consumers that only want an engine.
    from dotmac_kernel.models import Tenant

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).one_or_none()
        if tenant is None:
            raise NotFoundError(f"No tenant with slug {slug!r}.")
        set_tenant(db, tenant.id, transaction_local=False)
        yield db, tenant
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        try:
            db.execute(text("RESET app.current_tenant"))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
