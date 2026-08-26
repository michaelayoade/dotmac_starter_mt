"""The reference assembly's database runtime — ONE instance of
`dotmac_kernel.session_runtime.DatabaseRuntime`, built from the kernel's own
`Settings` at import.

This module's public names are unchanged and mean exactly what they meant
before. What changed is where the behaviour lives: the boundary, the RLS
priming and the pooled-connection discipline moved to `session_runtime`, where
a product can INSTANTIATE them with its own configuration, credentials and
tenant identity. This module is the reference assembly exercising that seam
first — if the starter cannot be expressed as one instance of the runtime, no
adopting product can be either.

**Still eager, deliberately.** The engines are built at module scope, so
importing this module requires a parseable `DATABASE_URL`. That is the contract
two architecture guards assert on purpose
(`test_kernel_imports_without_a_database.py`,
`test_packages_import_without_a_database.py`): a module reachable from the
package root must not drag engine construction into a bare
`import dotmac_kernel`, and the way that stays detectable is for entering the
OWNER to keep costing a DSN. Laziness here would make both guards pass for the
wrong reason.

## Which boundary to reach for

`get_db` sets the `app.current_tenant` Postgres setting per request so RLS
policies can scope rows to the resolved tenant. `SET LOCAL` is
transaction-scoped — the next request from the connection pool starts with no
setting and must set its own.

Code that runs OUTSIDE a request needs the same scope and has no
`request.state` to take it from. `tenant_session` is that boundary, the
tenant-scoped sibling of `platform_session`. Reaching for `SessionLocal`
directly instead is the one mistake this module cannot catch for you: RLS fails
**closed**, so an unscoped session returns zero rows rather than raising, and
the caller cannot tell an empty tenant from an invisible one. A
`dotmac_academy_app` audit command did exactly this and reported a clean estate
against a database holding 333 banks.
"""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from dotmac_kernel._transactions import conflict_savepoint
from dotmac_kernel.config import settings
from dotmac_kernel.session_runtime import DatabaseRuntime

__all__ = [
    "conflict_savepoint",
    "engine",
    "get_db",
    "get_platform_db",
    "platform_engine",
    "platform_session",
    "resolver_session",
    "runtime",
    "set_tenant",
    "tenant_scope",
    "tenant_session",
    "tenant_session_by_slug",
]

#: The reference assembly's instance. Public so a consumer that already depends
#: on this module's configuration can pass the runtime where one is wanted,
#: rather than re-deriving engines from the same environment a second time.
#:
#: `legacy_tenant_settings` is empty here and must stay empty: the starter has
#: no pre-lineage tables, so it has nothing to be compatible WITH. The field
#: exists for products mid-migration (see the runtime's module docstring).
runtime = DatabaseRuntime.from_urls(
    database_url=settings.database_url,
    platform_database_url=settings.platform_database_url or settings.database_url,
)

# Bound once, so each name is a single stable object for the life of the
# process. FastAPI keys `dependency_overrides` on dependency identity, and
# three unit tests monkeypatch these very attributes, so re-deriving them per
# access would quietly break both.
engine = runtime.engine
platform_engine = runtime.platform_engine
SessionLocal = runtime.session_factory
PlatformSessionLocal = runtime.platform_session_factory

set_tenant = runtime.set_tenant
tenant_scope = runtime.tenant_scope
platform_session = runtime.platform_session
tenant_session = runtime.tenant_session
tenant_session_by_slug = runtime.tenant_session_by_slug
resolver_session = runtime.resolver_session


def get_db(request: Request) -> Generator[Session, None, None]:
    """Per-request DB session with tenant context applied for RLS.

    The web adapter over `runtime.request_session`, and the reason the runtime
    itself takes a tenant id rather than a request: reading tenancy off request
    state — and carrying the `Request` annotation FastAPI needs in order to
    inject one — is a framework concern, while the transaction boundary is not.
    A product with its own request pipeline writes its own four lines here and
    inherits everything below them.

    If `request.state.tenant` is None (platform-level routes) no tenant context
    is set, and RLS fails closed (zero rows) on any tenant-scoped table.
    Platform code uses `get_platform_db`, which has explicit grants rather than
    the migration/admin role.
    """
    tenant = getattr(request.state, "tenant", None)
    yield from runtime.request_session(None if tenant is None else tenant.id)


def get_platform_db() -> Generator[Session, None, None]:
    """Online platform API DB session.

    Uses `PLATFORM_DATABASE_URL` (the `platform_api` role) when set, else
    `DATABASE_URL` for local development. That role must not have BYPASSRLS;
    migrations and offline maintenance use `MIGRATION_DATABASE_URL` separately.
    """
    yield from runtime.platform_request_session()
