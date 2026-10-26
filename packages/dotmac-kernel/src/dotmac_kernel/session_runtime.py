"""The kernel's engine/session/tenant-context runtime, as an INSTANTIABLE class.

`dotmac_kernel.db` is one instance of this — the reference assembly's — built
at import from the kernel's own `Settings`. That module stays exactly what it
was: eager, module-scoped, configured from the environment. What changes is
that it is no longer the only way to have the behaviour.

## Why this exists

"Build once" does not mean every application shares one running database or one
session. It means the reusable implementation is written once, here, and
INSTANTIATED independently by each product. Before this module, the two were
conflated: the transaction boundary, the RLS priming, the pooled-connection
reset discipline and the fail-closed-lookup traps were all real, general,
hard-won behaviour — and all of it was welded to two module-level
`create_engine` calls reading the kernel's own `DATABASE_URL` and priming
exactly one Postgres setting.

A product with its own deployment configuration, its own credentials and its
own legacy tenant setting therefore could not adopt the behaviour. It could
only reimplement it, which is how a second session factory, a second commit
boundary and a second RLS-priming path grow inside an adopting product — each
one a place where the discipline in `db.py`'s docstrings has to be remembered
again rather than inherited.

So the facility becomes configurable and assembly-instantiated. The product
supplies deployment configuration, credentials and its own tenant identity; the
kernel supplies the boundary.

## What is NOT configurable, deliberately

**The canonical tenant setting name.** Every module lineage in the fleet
writes RLS policies as `tenant_id = public.app_current_tenant_id()`, and that
function reads `current_setting('app.current_tenant', true)`
(`dotmac_kernel.migrations.verify` pins the semantics). The name is a
cross-repository contract baked into shipped migrations, not a deployment
knob — a runtime that primed something else would produce a database where
every composed module's policy silently matches nothing, and RLS fails CLOSED,
so the symptom is zero rows rather than an error.

`legacy_tenant_settings` therefore does not REPLACE that name; it primes
additional names ALONGSIDE it, in the same statement and with the same value,
for tables a product has not yet moved onto a composed module lineage. It is a
transitional compatibility set that only ever shrinks (see the field's
docstring), never an alternative tenancy scheme.

## The scope mechanic is ported, not invented (hard rule 22)

`tenant_scope` — transaction-local priming plus an `after_begin` listener that
re-arms every subsequent transaction — is `dotmac_erp`'s production
implementation (`app/db/session_context.py::tenant_scope_for_session` over
`app/rls.py::set_current_organization_on_connection`), not a kernel invention.
It is strictly better than the session-level setting this runtime replaced, and
the difference is a leak rather than a preference:

* Session-level (`SET`) survives commits, which is why it was reached for — but
  it also survives the session, riding the pooled connection out to whoever
  borrows it next. That hazard is real enough that the old code carried a
  reset-and-commit dance in a `finally`, and the dance is only as good as the
  process surviving to run it.
* Transaction-local (`SET LOCAL`) cannot leak, because nothing outlives the
  transaction — but on its own it dies at the first commit inside the block,
  leaving the rest of a CLI loop or a worker drain running unscoped against a
  fail-closed policy.

Re-arming on `after_begin` takes the safe half of each: every transaction is
scoped, and no transaction leaves a trace on the connection. There is nothing
to reset, so there is no reset that can be skipped.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, NoReturn

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.engine import NestedTransaction, RootTransaction
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

if TYPE_CHECKING:
    from dotmac_kernel.identity_provisioning import IdentityProvisioningAssemblyV1

__all__ = [
    "CANONICAL_TENANT_SETTING",
    "DatabaseRuntime",
    "NoDatabaseRuntimeError",
    "RuntimeBindingError",
    "TenantLookup",
    "bind_database_runtime",
    "resolve_database_runtime",
]

#: The one Postgres setting every composed module's RLS policy reads, via
#: `public.app_current_tenant_id()`. Not a knob — see the module docstring.
CANONICAL_TENANT_SETTING = "app.current_tenant"

#: Resolve a tenant from a human-supplied identifier, using the session it is
#: handed. Returns `(tenant_id, tenant)`; raising is how "no such tenant" is
#: reported, because a `None` lets a CLI carry on and print an empty report.
TenantLookup = Callable[[Session, str], "tuple[Any, Any]"]

# A setting name is INTERPOLATED into SQL, not bound: `set_config`'s first
# argument must be a literal, so a placeholder is not available there. Every
# name is therefore validated against the Postgres identifier grammar before it
# can reach a statement — `schema.name`, lowercase, no quoting, no separators.
_SETTING_NAME = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


class _TransactionMode(Enum):
    """One of the two per-transaction PostgreSQL isolation modes this
    runtime knows how to apply. Deliberately NOT a general execution-options
    passthrough (see the module docstring's "one capability" scope) — every
    value here maps to exactly one `readonly_session`/`serializable_session`
    surface, and there is no way for a caller to reach `_EXECUTION_OPTIONS`
    with an arbitrary key.
    """

    READ_ONLY = "read_only"
    SERIALIZABLE = "serializable"


#: The exact SQLAlchemy execution options each mode applies, keyed by
#: `_TransactionMode`. `isolation_level` is portable SQLAlchemy;
#: `postgresql_readonly` is the PG-dialect option that gets a
#: DBAPI-**enforced** write refusal, not merely a label (see
#: `readonly_session`'s docstring). Both are "transactional" SQLAlchemy
#: connection characteristics (`sqlalchemy.engine.characteristics
#: .IsolationLevelCharacteristic`/`dialects.postgresql.base
#: .PGReadOnlyConnectionCharacteristic`, both `transactional = True`).
#:
#: MEASURED (pinned `sqlalchemy==2.0.51`,
#: `site-packages/sqlalchemy/orm/session.py`
#: `SessionTransaction._connection_for_bind`, lines 1157-1168): applying
#: execution options to a bind that already has a connection established
#: does NOT raise. It emits an `SAWarning` ("Connection is already
#: established for the given bind; execution_options ignored") and returns
#: the existing connection UNCHANGED — a late call is silently discarded,
#: not refused. So the ordering guarantee below cannot be delegated to
#: SQLAlchemy; `_isolated_session` checks the session's own state itself,
#: before calling `Session.connection(execution_options=...)`, and raises
#: `_IsolationModeTooLateError` on its own behalf if a connection/transaction
#: is already established for the bind.
_EXECUTION_OPTIONS: dict[_TransactionMode, dict[str, Any]] = {
    _TransactionMode.READ_ONLY: {
        "isolation_level": "REPEATABLE READ",
        "postgresql_readonly": True,
    },
    _TransactionMode.SERIALIZABLE: {
        "isolation_level": "SERIALIZABLE",
    },
}


def _default_tenant_lookup(db: Session, slug: str) -> tuple[Any, Any]:
    """Resolve a kernel `Tenant` by slug — the reference assembly's identity.

    A product whose tenancy authority is its own table (ERP's `Organization`,
    for instance) supplies its own `tenant_lookup` instead of inheriting this
    one. That is the whole point of the seam: the kernel owns the session and
    the scope, the product owns who the tenants are.
    """
    from dotmac_kernel.exceptions import NotFoundError

    # Local import: `models` pulls in every declarative class, and a runtime is
    # commonly constructed by an assembly that only wants an engine.
    from dotmac_kernel.models import Tenant

    tenant = db.query(Tenant).filter(Tenant.slug == slug).one_or_none()
    if tenant is None:
        raise NotFoundError(f"No tenant with slug {slug!r}.")
    return tenant.id, tenant


class DatabaseRuntime:
    """One product's engines, session factories and tenant-scope discipline.

    Construct ONE per deployment, in the assembly, and pass its bound methods
    where the framework wants callables (`get_db` as a FastAPI dependency, for
    instance). The bound methods are created once at construction and kept, so
    their identity is stable — `dependency_overrides` keyed on them works.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        platform_engine: Engine | None = None,
        legacy_tenant_settings: Sequence[str] = (),
        tenant_lookup: TenantLookup | None = None,
    ) -> None:
        """
        `platform_engine` defaults to `engine`. Deployments that give the
        online platform role its own credential pass a second engine; the
        distinction is a privilege boundary, not a performance one, and it is
        why the platform pool is deliberately small (see `from_urls`).

        `legacy_tenant_settings` primes ADDITIONAL Postgres settings with the
        same tenant value, in the same statement as the canonical one. It
        exists for tables whose RLS policies predate the module lineage
        contract — ERP's `app.current_organization_id` is the motivating case.

        This set is a RATCHET: a product declares it, exposes it through
        `legacy_tenant_settings`, and asserts in its own architecture test that
        the count only ever falls. Each table moved onto a composed module
        lineage brings it closer to empty, and empty is the finished state. It
        is not a general "extra GUCs" feature and must not accumulate names
        that are not being actively retired.
        """
        self._engine = engine
        self._platform_engine = (
            platform_engine if platform_engine is not None else engine
        )
        self._legacy_tenant_settings = self._validate_legacy(legacy_tenant_settings)
        self._tenant_lookup = tenant_lookup or _default_tenant_lookup

        self._session_factory = sessionmaker(
            bind=self._engine, autocommit=False, autoflush=False
        )
        self._platform_session_factory = sessionmaker(
            bind=self._platform_engine, autocommit=False, autoflush=False
        )

        # Built once so the callables have stable identity (see class docstring).
        self._scope_sql = self._build_scope_sql()
        self._reset_sql = tuple(text(f"RESET {name}") for name in self.tenant_settings)

    # ── construction ────────────────────────────────────────────────────────

    @classmethod
    def from_urls(
        cls,
        *,
        database_url: str,
        platform_database_url: str | None = None,
        hide_parameters: bool = False,
        pool_size: int = 5,
        max_overflow: int = 10,
        platform_pool_size: int = 2,
        platform_max_overflow: int = 2,
        legacy_tenant_settings: Sequence[str] = (),
        tenant_lookup: TenantLookup | None = None,
    ) -> DatabaseRuntime:
        """Build the engines from DSNs the deployment resolved.

        The platform pool is small on purpose: that role serves the control
        plane, not the request path, and sizing it like the tenant pool would
        multiply connections for a surface that handles a fraction of the
        traffic. A product that needs different numbers passes its own engines
        to `__init__` rather than growing knobs here.
        """
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            hide_parameters=hide_parameters,
            echo=False,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        platform_engine = create_engine(
            platform_database_url or database_url,
            pool_pre_ping=True,
            hide_parameters=hide_parameters,
            echo=False,
            pool_size=platform_pool_size,
            max_overflow=platform_max_overflow,
        )
        return cls(
            engine=engine,
            platform_engine=platform_engine,
            legacy_tenant_settings=legacy_tenant_settings,
            tenant_lookup=tenant_lookup,
        )

    @staticmethod
    def _validate_legacy(names: Sequence[str]) -> tuple[str, ...]:
        seen: list[str] = []
        for name in names:
            if name == CANONICAL_TENANT_SETTING:
                raise ValueError(
                    f"{name!r} is primed by every tenant scope already; listing "
                    "it as a legacy setting would make the runtime write it "
                    "twice and imply it is optional. Remove it."
                )
            if not _SETTING_NAME.match(name):
                raise ValueError(
                    f"{name!r} is not a valid Postgres setting name "
                    "(lowercase 'schema.name'). Setting names are interpolated "
                    "into SQL because set_config cannot bind them, so only the "
                    "plain grammar is accepted."
                )
            if name in seen:
                raise ValueError(f"{name!r} declared twice.")
            seen.append(name)
        return tuple(seen)

    def _build_scope_sql(self) -> Any:
        calls = ", ".join(
            f"pg_catalog.set_config('{name}', :tenant_id, :is_local)"
            for name in self.tenant_settings
        )
        # One statement, so a scope is never half-applied: a failure between two
        # separate statements would leave the canonical setting armed and a
        # legacy one stale, which reads as a working scope over the wrong rows.
        return text(f"SELECT {calls}")

    # ── declared surface ────────────────────────────────────────────────────

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def platform_engine(self) -> Engine:
        return self._platform_engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        return self._session_factory

    @property
    def platform_session_factory(self) -> sessionmaker[Session]:
        return self._platform_session_factory

    @property
    def legacy_tenant_settings(self) -> tuple[str, ...]:
        """The transitional names, for a product's own shrink-only ratchet."""
        return self._legacy_tenant_settings

    @property
    def tenant_settings(self) -> tuple[str, ...]:
        """Every setting a tenant scope primes — canonical first, always."""
        return (CANONICAL_TENANT_SETTING, *self._legacy_tenant_settings)

    # ── tenant context ──────────────────────────────────────────────────────

    def set_tenant(
        self, db: Session, tenant_id: object, *, transaction_local: bool = True
    ) -> None:
        """Apply the RLS tenant scope to `db`. The one writer of the settings.

        `transaction_local` decides how long the scope lasts, and both values
        are unsafe in the other's place:

        * `True` (SET LOCAL) for `get_db`. Its session is pooled, so a scope
          that outlived the transaction would be inherited by the next request
          to borrow that connection — one tenant reading another's rows.
        * `False` for a session that commits more than once. A commit ends the
          transaction and takes `SET LOCAL` with it; `expire_on_commit` then
          reloads attributes on the next statement, which runs unscoped, and
          RLS fails closed — a row the session itself just wrote comes back as
          `ObjectDeletedError`. `tenant_session` uses this, and resets on exit.
        """
        db.execute(
            self._scope_sql,
            {"tenant_id": str(tenant_id), "is_local": transaction_local},
        )

    def _reset_tenant(self, db: Session) -> None:
        for statement in self._reset_sql:
            db.execute(statement)

    @contextmanager
    def tenant_scope(
        self, db: Session, tenant_id: object
    ) -> Generator[Session, None, None]:
        """Scope a CALLER-OWNED session to one tenant, for as long as the block
        lasts and no longer.

        The mechanic, and why it is two things rather than one (see the module
        docstring for the full rationale):

        1. An `after_begin` listener primes the settings on every transaction
           this session opens from now on. That is what makes the scope survive
           a commit inside the block — the next statement begins a new
           transaction, and the listener arms it before that statement runs.
        2. An immediate prime for the transaction that is ALREADY open, which
           the listener cannot retroactively reach. `tenant_session_by_slug`
           depends on this: it must resolve the tenant before it can scope to
           it, and resolving opens a transaction.

        Everything is transaction-local, so the connection carries nothing back
        to the pool and there is no reset to forget. The listener is removed on
        exit, which is what bounds the scope to the block — the session itself
        stays usable, unscoped, afterwards.

        This is the seam for a product that owns its own session lifecycle and
        wants the kernel's scope discipline without the kernel's session
        factory. `tenant_session` is this plus an owned boundary.
        """
        params = {"tenant_id": str(tenant_id), "is_local": True}

        def _arm(_session: Session, _transaction: Any, connection: Any) -> None:
            # `connection`, not the session: re-entering the session here would
            # begin the very transaction this listener is handling.
            connection.execute(self._scope_sql, params)

        event.listen(db, "after_begin", _arm)
        try:
            if db.in_transaction():
                db.execute(self._scope_sql, params)
            yield db
        finally:
            event.remove(db, "after_begin", _arm)

    # ── request boundaries ──────────────────────────────────────────────────
    #
    # Generators, not framework dependencies, and they take a tenant id rather
    # than a request. The web adapter — reading tenancy off request state,
    # carrying whatever annotation the framework needs to inject it — belongs
    # to the product; `dotmac_kernel.db.get_db` is the reference assembly's.
    # Keeping the boundary framework-free is what lets a product with its own
    # request pipeline (ERP's dependency-primed org context, say) adopt the
    # transaction discipline without adopting a router stack.

    def request_session(
        self, tenant_id: object | None = None
    ) -> Generator[Session, None, None]:
        """Per-request tenant boundary: commit on success, roll back on error.

        `tenant_id=None` (platform-level routes) sets NO tenant context, and
        RLS then fails closed — zero rows on any tenant-scoped table. That is
        the intended reading: a request with no tenant has no business seeing
        tenant rows. Platform code uses `platform_request_session`, whose role
        has explicit grants rather than the migration/admin credential.

        The scope is transaction-local, because this session is pooled: one
        that outlived its transaction would be inherited by the next request to
        borrow the connection — one tenant reading another's rows. A request
        boundary commits once, at the end, so there is no in-block commit for
        that scope to die at (which is the case `tenant_scope` exists for).
        """
        db = self._session_factory()
        try:
            if tenant_id is not None:
                self.set_tenant(db, tenant_id)
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def platform_request_session(self) -> Generator[Session, None, None]:
        """Per-request platform boundary, on the platform engine.

        That role must not have BYPASSRLS; migrations and offline maintenance
        use a separate credential.
        """
        db = self._platform_session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── non-request boundaries ──────────────────────────────────────────────

    @contextmanager
    def platform_session(self) -> Generator[Session, None, None]:
        """Non-request platform boundary — lifespan seed hooks, jobs.

        Commit on success, rollback on error, close always. Exists so callers
        get the same owned-boundary contract as `get_platform_db` without
        reaching for a session factory themselves.
        """
        db = self._platform_session_factory()
        scope = _PlatformScope(self, db)
        _platform_scopes[db] = scope
        scope.listen()
        escaping_protected_failure = False
        try:
            yield db
            scope.commit()
        except BaseException as exc:
            scope.poison()
            escaping_protected_failure = scope.protected
            if scope.protected:
                try:
                    db.rollback()
                except BaseException:
                    scope.poison()
            elif isinstance(exc, Exception):
                db.rollback()
            raise
        finally:
            if escaping_protected_failure:
                # Preserve the escaping protected exception's exact identity,
                # while still attempting every cleanup and registry removal.
                try:
                    db.close()
                except BaseException:
                    scope.poison()
                try:
                    scope.dispose()
                except BaseException:
                    scope.poison()
                finally:
                    _platform_scopes.pop(db, None)
            else:
                # Ordinary unprotected cleanup retains its existing semantics.
                try:
                    db.close()
                finally:
                    try:
                        scope.dispose()
                    finally:
                        _platform_scopes.pop(db, None)

    @contextmanager
    def _isolated_session(
        self, mode: _TransactionMode
    ) -> Generator[Session, None, None]:
        """Owned non-request boundary running under `mode` for its ONE
        transaction. Shared implementation behind `readonly_session` and
        `serializable_session` — not itself public (see `_TransactionMode`).

        THE ordering guarantee this exists to prove: `Session.connection
        (execution_options=...)` is the FIRST operation this method performs
        on `db` — before the caller (or a nested `tenant_scope`) can issue
        any statement, including a tenant GUC. `SessionTransaction
        ._connection_for_bind` applies `conn.execution_options(**options)`
        BEFORE it calls `conn.begin()`, and BEFORE it dispatches
        `after_begin` — so calling `db.connection(execution_options=...)`
        here, ahead of anything else, guarantees the mode is set ahead of
        the transaction's BEGIN and ahead of any `after_begin` listener
        (Sub's tenant-GUC hook among them; `tenant_scope`'s own listener
        follows the identical composition — see its docstring below).

        THIS GUARANTEE IS NOT SQLALCHEMY'S TO KEEP. `isolation_level` and
        `postgresql_readonly` are both "transactional" connection
        characteristics, but SQLAlchemy 2.0.51's
        `SessionTransaction._connection_for_bind` does not raise when
        execution options are applied to a bind that already has a
        connection established — it warns (`SAWarning`) and returns the
        existing connection with the options silently discarded (measured;
        see `_EXECUTION_OPTIONS`'s comment for the exact file/line). A
        caller that reached this method with an already-transacted `db`
        would therefore get a session that silently runs at the DEFAULT
        isolation, not the one its name promises — the dominant failure
        shape this ordering guarantee exists to rule out.

        So the refusal is the Kernel's own, checked BEFORE the first
        statement this method issues: `db.in_transaction()` is `True` only
        once a transaction has begun on this session (autobegin included),
        which is also the precondition under which SQLAlchemy would have
        silently discarded the options above. `_isolated_session` always
        constructs a fresh session immediately above, so this branch is not
        reachable through today's only two callers, `readonly_session` and
        `serializable_session` — it exists so a FUTURE caller that reuses or
        otherwise pre-touches a session before reaching this method fails
        loudly instead of inheriting the default isolation under a name
        that promises otherwise. `_IsolationModeTooLateError` stays private
        (not in `__all__`, not in `public_exports.json`) for exactly that
        reason: no public path can trigger it today, and it subclasses
        `RuntimeError` so a caller that already catches `RuntimeError` loses
        nothing — the PR that introduces the future caller is the right
        place to promote the name, with that caller as its named consumer.

        Same disposal contract as `platform_session`/`tenant_session`:
        commit on success, rollback on error, close always — no second
        transaction boundary.
        """
        db = self._session_factory()
        try:
            if db.in_transaction():
                raise _IsolationModeTooLateError(
                    f"Cannot apply isolation mode {mode.value!r}: this "
                    "session has already begun a transaction (a statement "
                    "ran, or a connection was already established, before "
                    "the mode could be applied). SQLAlchemy does not raise "
                    "in this situation -- it silently discards the late "
                    "execution options and keeps running at the DEFAULT "
                    "isolation, so the Kernel refuses here instead of "
                    "handing back a session whose isolation contradicts its "
                    "name."
                )
            db.connection(execution_options=_EXECUTION_OPTIONS[mode])
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def readonly_session(self) -> Generator[Session, None, None]:
        """A REPEATABLE READ transaction PostgreSQL also refuses writes on.

        `postgresql_readonly=True` is not a label the application merely
        respects — it puts the underlying Postgres transaction in `READ
        ONLY` mode, so an `INSERT`/`UPDATE`/`DELETE` inside the block is
        refused by the SERVER (Postgres error `25006
        read_only_sql_transaction`, surfaced as `sqlalchemy.exc
        .InternalError`), not merely by convention. Sub's
        `migration_source_export.py` is the motivating caller: an export
        that must see one consistent snapshot and must never itself write to
        it.

        Compose with `tenant_scope(db, tenant_id)` INSIDE the block for a
        tenant-scoped read-only transaction — the mode is already
        established by the time `tenant_scope` issues its first GUC
        statement (see `_isolated_session`'s docstring), so the ordering
        guarantee holds for the combination too.
        """
        with self._isolated_session(_TransactionMode.READ_ONLY) as db:
            yield db

    @contextmanager
    def serializable_session(self) -> Generator[Session, None, None]:
        """A SERIALIZABLE, write-capable transaction.

        The strictest PostgreSQL isolation level, applied before the first
        statement so every read the transaction takes part in also becomes
        part of its serializable history — a level chosen after the
        transaction already has statements queued would only protect what
        came after it applied. Sub's `party_identity_backfill.py` is the
        motivating caller: a write that must not observe, or be observed as,
        a partial identity merge under concurrent traffic.

        Compose with `tenant_scope(db, tenant_id)` inside the block, same as
        `readonly_session`.
        """
        with self._isolated_session(_TransactionMode.SERIALIZABLE) as db:
            yield db

    @contextmanager
    def tenant_session(self, tenant_id: object) -> Generator[Session, None, None]:
        """Non-request TENANT-scoped boundary — CLI commands, jobs, workers.

        The sibling of `platform_session`, for the other half of the
        non-request world: work that acts as one tenant rather than as the
        platform. The scope is applied before the caller gets the session, so
        there is no window in which a query can run unscoped.

        Takes a tenant id, not a name: resolving a name needs a query, and that
        query would itself have to decide which scope to run under. Callers
        resolve first — usually against a tenancy table, which is not
        tenant-scoped — and pass the id in.

        Prefer this over a bare session factory in any non-request caller. An
        unscoped session does not raise; it returns nothing, which reads
        exactly like a tenant that has no data.
        """
        db = self._session_factory()
        try:
            with self.tenant_scope(db, tenant_id):
                yield db
                db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def tenant_session_by_slug(
        self, slug: str
    ) -> Generator[tuple[Session, Any], None, None]:
        """`tenant_session`, for callers holding a name rather than an id.

        Yields `(db, tenant)`. Every assembly's CLI needs the same two steps —
        look a tenant up by what an operator typed, then act as that tenant —
        and each one solving it privately means each one building its own
        session.

        The lookup and the scope share one session on purpose. A tenancy table
        is not tenant-scoped, so querying it before any scope is set is legal;
        doing it here means the resolved tenant is still attached when the
        caller gets it, and no second connection is taken to resolve a name.

        That legality is also the trap this closes. Because the lookup succeeds
        unscoped, a caller who resolves a tenant and then forgets the scope
        gets a working query followed by silence.

        Which table is consulted is the PRODUCT's decision, supplied as
        `tenant_lookup`. The lookup raises rather than returning `None`: a CLI
        handed a `None` tends to carry on and produce an empty report, which is
        the failure this runtime exists to stop being quiet.
        """
        db = self._session_factory()
        try:
            tenant_id, tenant = self._tenant_lookup(db, slug)
            # The lookup has already opened a transaction, and the resolved
            # tenant is attached to it. `tenant_scope` primes that open
            # transaction immediately rather than waiting for the next one,
            # which is why the caller's first scoped statement is safe.
            with self.tenant_scope(db, tenant_id):
                yield db, tenant
                db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def resolver_session(self) -> Generator[Session, None, None]:
        """An UNSCOPED session on the main engine, for deciding which tenant to
        scope to.

        The one legitimate reason to run without a tenant scope. A tenant
        resolver needs it — you cannot scope to a tenant you are still
        identifying from a Host header — and without a name for that need, a
        resolver has no option but to build its own session.

        Why not the alternatives: `tenant_session_by_slug` needs a name and a
        resolver has a host; `platform_session` fits semantically but runs on
        the platform engine, whose pool is deliberately tiny, and resolution
        happens on EVERY request.

        Read-only by construction: it always rolls back and never commits, so
        it cannot become a back door for unscoped writes.

        "Unscoped" does NOT mean "sees everything". RLS fails closed, so on a
        tenant-scoped table this session sees NOTHING — which is correct, and
        is why it is only useful for the tenancy tables. Those are deliberately
        not RLS-protected precisely because they are read to DECIDE a scope and
        so cannot depend on one.

        It RESETs the tenant settings before yielding. That is correctness, not
        paranoia — a scope inherited from a pooled connection would filter the
        resolver's own lookup, and because RLS fails closed the symptom would
        be a valid host resolving to no tenant at all.
        """
        db = self._session_factory()
        try:
            self._reset_tenant(db)
            yield db
        finally:
            # Detach before rolling back, in that order and deliberately. A
            # resolver exists to hand something back, and its result outlives
            # the session: the caller puts the tenant on request state and
            # reads its id long after this block has exited.
            #
            # `rollback()` EXPIRES every instance still in the session, so the
            # object would come back alive but hollow — the next attribute
            # access tries to refresh against a closed session and raises
            # DetachedInstanceError. `expunge_all()` first removes the
            # instances, so the rollback has nothing to expire and they keep
            # the values already loaded.
            db.expunge_all()
            db.rollback()
            db.close()


# ── the process's one sealed runtime binding ────────────────────────────────
#
# `dotmac_kernel.db` is one INSTANCE of `DatabaseRuntime` — the reference
# assembly's, eager, built from the kernel's own `Settings` at import. Kernel
# code that needs a runtime (auth, middleware, startup checks, a machine-key
# dependency, a CLI/worker entry point) must not import that instance
# directly: doing so makes the kernel's OWN behaviour depend on the reference
# assembly's configuration even for a product that supplied its own.
#
# ONE process supports ONE binding — not a per-application slot, and not a
# freely-mutable global either. `bind_database_runtime` seals it through
# exactly THREE named transitions, kept separate on purpose (see that
# function's own docstring for why filing one under another is the exact
# defect this seam exists to close):
#
# * IDEMPOTENCE — the IDENTICAL runtime, at the IDENTICAL policy, changes
#   nothing (a second `create_app` call composing the same spec must not
#   fail on that alone);
# * MONOTONIC PROMOTION — the SAME runtime, `required` going False -> True.
#   The binding DOES change; permitted only because strictness tightens;
# * REFUSAL — everything else: a DIFFERENT runtime (any policy), or the SAME
#   runtime with `required` going True -> False (a downgrade). A second
#   application in this process binding something else is a configuration
#   conflict, never a silent clobber of the first, and there is no public
#   function that clears a binding or downgrades its strictness.
#
# ## Why a boolean check is not enough
#
# An earlier version of this checked `"dotmac_kernel.db" in sys.modules` in
# `bind_database_runtime` and a plain `is_binding_sealed_strict()` boolean in
# `dotmac_kernel.db` — two INDEPENDENT reads, which has a real TOCTOU window:
# a binder can see `dotmac_kernel.db` absent, `db.py` can begin importing and
# observe strict mode as still false, THEN the binder seals strict mode,
# and THEN `db.py` finishes constructing the reference engines anyway. Module
# import is not atomic, so both checks pass and the invariant is violated.
#
# `_claim_lock` + `_reference_claimed` replace both booleans with ONE
# mutually-exclusive state transition, guarded by a real lock (not the GIL,
# not import-lock semantics): `_claim_reference_runtime_import` (called by
# `dotmac_kernel.db`, before it constructs any engine) and
# `bind_database_runtime` (`required=True`) both contend for the SAME lock.
# Whichever acquires it first wins — either the reference import is claimed
# first and a later strict bind refuses against that recorded claim, or a
# strict bind seals first and the reference import refuses against THAT.
# There is no gap between deciding and recording: both happen inside the same
# critical section.
_claim_lock = threading.Lock()
_reference_claimed = False
_binding: _Binding | None = None


class NoDatabaseRuntimeError(RuntimeError):
    """`resolve_database_runtime` could not produce a runtime.

    Raised in two distinct cases, both meaning "there is no runtime to hand
    back":

    * this process's binding is `required` and carries no runtime because it
      was never bound (there is no such state reachable through
      `bind_database_runtime`, which always takes a real runtime — this is
      the reference assembly's own `dotmac_kernel.db` refusing to construct
      one after a strict binding claimed the reference-runtime slot first, or
      a test that made `dotmac_kernel.db` unimportable without ever calling
      `bind_database_runtime`);
    * nothing is bound, nothing declared strictness, AND the reference
      runtime's own import fails. A real deployment that never opts into
      strictness always has one or the other.
    """


class RuntimeBindingError(RuntimeError):
    """`bind_database_runtime` refused: a different runtime, a weaker policy,
    or the reference-runtime import already claimed the slot a strict bind
    needs."""


class _IsolationModeTooLateError(RuntimeError):
    """`_isolated_session` refused: the session it was handed had already
    begun a transaction, so applying a per-transaction isolation mode now
    would be too late to be trustworthy.

    Raised BY THE KERNEL, not by SQLAlchemy: SQLAlchemy 2.0.51 does not
    raise in this situation (measured — see `_EXECUTION_OPTIONS`'s comment
    and `_isolated_session`'s docstring for the file/line and the exact
    silently-ignored behaviour). Without this check, `readonly_session()`/
    `serializable_session()` could hand back a session that silently runs at
    the DEFAULT isolation while its name promises REPEATABLE READ + read-only
    or SERIALIZABLE.

    DELIBERATELY PRIVATE: not in `__all__`, not in `public_exports.json`.
    `_isolated_session` always constructs a fresh session, so no caller
    reachable through today's public surface (`readonly_session`,
    `serializable_session`) can trigger this branch — publishing the name
    now would add a permanent contract entry with no public consumer. It
    subclasses `RuntimeError`, so a future caller that reuses a session
    before reaching `_isolated_session` still fails loudly and is still
    catchable generically; that future caller's own PR is the place to
    promote this name, with itself as the named consumer.
    """


@dataclass(frozen=True)
class _Binding:
    runtime: DatabaseRuntime
    required: bool
    identity_provisioning: IdentityProvisioningAssemblyV1 | None = None


def bind_database_runtime(runtime: DatabaseRuntime, *, required: bool = False) -> None:
    """Seal THE process's one runtime binding.

    A product calls this from its own assembly composition (typically via
    `ProductAssemblySpec.database_runtime`/`require_database_runtime`, applied
    by `create_app`) with a `DatabaseRuntime` it built from its own DSNs,
    credentials and tenant lookup.

    THREE DIFFERENT transitions, named separately and DELIBERATELY not
    collapsed into one "idempotent" bucket — filing a change under
    idempotence is how a seal quietly loosens the next time someone adds a
    field and calls varying IT idempotent too:

    * First call, or a call whose `(runtime, required)` are BOTH IDENTICAL to
      what is already sealed: **IDEMPOTENCE**. Nothing changes; calling
      twice with the same arguments is exactly as safe as calling once (a
      second `create_app` composing the identical spec, for instance).
    * The SAME `runtime` object, `required` going `False` -> `True`:
      **MONOTONIC PROMOTION**. The binding DOES change — strictness only
      ever tightens, and this is the one permitted way it does.
    * ANYTHING ELSE — a DIFFERENT `runtime` object (regardless of policy), or
      the SAME `runtime` with `required` going `True` -> `False` (a
      downgrade): **REFUSAL**. There is no public way to switch runtimes or
      loosen strictness once sealed.
    * `required=True` additionally REFUSES if `dotmac_kernel.db` has already
      CLAIMED the reference-runtime slot (`_claim_reference_runtime_import`,
      called at its own import, before constructing any engine) —
      atomically, under the same lock that import uses, so there is no
      window between a boolean read and this call in which the two could
      disagree.
    """
    if not isinstance(runtime, DatabaseRuntime):
        raise TypeError(
            f"bind_database_runtime() requires a DatabaseRuntime, got "
            f"{type(runtime).__name__}"
        )
    global _binding
    with _claim_lock:
        if required and _reference_claimed:
            raise RuntimeBindingError(
                "REFUSAL: cannot bind a REQUIRED runtime — the "
                "reference-runtime import already claimed this process's "
                "slot, so the reference runtime it constructs may already "
                "exist. Bind before anything imports dotmac_kernel.db — "
                "commonly an eager module-scope import reached during "
                "manifest/feature discovery, before create_app ever ran."
            )
        if _binding is None:
            _binding = _Binding(runtime=runtime, required=required)
            return
        if runtime is not _binding.runtime:
            raise RuntimeBindingError(
                "REFUSAL: a different DatabaseRuntime is already bound in "
                "this process — one process supports one runtime binding; "
                "a second application or caller binding another instance "
                "is a configuration conflict, not a switch"
            )
        if _binding.required and not required:
            raise RuntimeBindingError(
                "REFUSAL: the bound runtime is already required (strict); "
                "a later call asking for required=False would DOWNGRADE "
                "it, which is refused — strictness is monotonic and has no "
                "public way back down"
            )
        if required and not _binding.required:
            # MONOTONIC PROMOTION: same runtime, optional -> required. The
            # binding changes; permitted because strictness only tightens.
            _binding = _Binding(
                runtime=runtime,
                required=True,
                identity_provisioning=_binding.identity_provisioning,
            )
        # else: IDEMPOTENCE — identical (runtime, required), nothing to do.


def _install_identity_provisioning(assembly: IdentityProvisioningAssemblyV1) -> None:
    """One trusted product composition, using the existing strict binding seal."""
    global _binding
    with _claim_lock:
        bound = _binding
        if bound is None or not bound.required or bound.runtime is not assembly.runtime:
            raise RuntimeBindingError(
                "identity provisioning requires the exact strictly bound runtime"
            )
        if bound.identity_provisioning is assembly:
            return
        if bound.identity_provisioning is not None:
            raise RuntimeBindingError(
                "identity provisioning assembly replacement refused"
            )
        _binding = _Binding(bound.runtime, bound.required, assembly)


def _identity_provisioning_assembly() -> IdentityProvisioningAssemblyV1:
    with _claim_lock:
        bound = _binding
        if bound is None or not bound.required or bound.identity_provisioning is None:
            raise RuntimeBindingError(
                "identity provisioning assembly is not strictly installed"
            )
        return bound.identity_provisioning


def _claim_reference_runtime_import() -> None:
    """The reference-runtime import's half of the atomic claim.

    `dotmac_kernel.db` calls this ONCE, at its own module scope, BEFORE
    constructing any engine. Under the SAME lock `bind_database_runtime`
    uses: refuses if a strict binding is already sealed (the mirror image of
    that function's own check), otherwise records that the reference-runtime
    slot is claimed, so a strict bind arriving AFTER this point refuses
    too — closing the TOCTOU window a pair of independent boolean reads left
    open (see the module comment above).
    """
    global _reference_claimed
    with _claim_lock:
        if _binding is not None and _binding.required:
            raise RuntimeError(
                "dotmac_kernel.db refuses to construct the reference "
                "runtime: this process already sealed a REQUIRED "
                "database-runtime binding (typically "
                "ProductAssemblySpec.require_database_runtime). Importing "
                "this module now would build an engine the deployment "
                "explicitly refused to fall back to -- find what imported "
                "dotmac_kernel.db (a module-scope import reached during "
                "manifest/feature discovery is the usual cause) and make it "
                "resolve through "
                "dotmac_kernel.session_runtime.resolve_database_runtime() "
                "instead."
            )
        _reference_claimed = True


def resolve_database_runtime() -> DatabaseRuntime:
    """The runtime every kernel-owned request, middleware, startup-check,
    CLI and worker path resolves through.

    A sealed binding always wins. Absent one, this is the reference
    assembly's `dotmac_kernel.db.runtime` — imported HERE, lazily, which is
    what keeps this module (and everything that calls this function at
    module scope) importable without a `DATABASE_URL`.
    """
    if _binding is not None:
        return _binding.runtime
    try:
        from dotmac_kernel.db import runtime as _reference_runtime
    except Exception as exc:  # pragma: no cover - exercised by the seam test
        raise NoDatabaseRuntimeError(
            "no runtime is bound in this process and the reference runtime "
            f"could not be imported: {type(exc).__name__}: {exc}"
        ) from exc
    return _reference_runtime


# Owner-private provenance for one actual non-request platform boundary. This is
# transaction custody, NOT database authorization. The consuming owner must check
# actual login/role/catalog/policy against the runtime's configured target/TLS.
# It cannot attest an indistinguishable configured clone or arbitrary Python/DBA
# interference. No Session.info flag, nonce, public token or fallback resolver.
_platform_scopes: dict[Session, _PlatformScope] = {}


class _PlatformScope:
    def __init__(self, runtime: DatabaseRuntime, db: Session) -> None:
        self.runtime = runtime
        self.db = db
        self.engine = runtime.platform_engine
        self.factory = runtime.platform_session_factory
        self.factory_configuration = dict(self.factory.kw)
        self.factory_class = self.factory.class_
        self.root: SessionTransaction | None = None
        self.connection: Connection | None = None
        self.connection_listening = False
        self.physical_root: RootTransaction | None = None
        self.dbapi: Any = None
        self.backend_xid: tuple[Any, ...] | None = None
        self.validators: list[Callable[[Connection], None]] = []
        self.retired = False
        self.protected = False
        self.failed = False
        self.runtime_commit = False
        self.opening_savepoint = False
        self.savepoint: SessionTransaction | None = None
        self.closing_savepoint: SessionTransaction | None = None
        self.rolling_back_savepoint: SessionTransaction | None = None
        self.physical_nested: NestedTransaction | None = None
        self.creating_physical_savepoint = False
        self.custody_damaged = False
        self.damaged_cleanup_done = False

    def listen(self) -> None:
        event.listen(self.db, "after_transaction_create", self.transaction_created)
        event.listen(self.db, "after_transaction_end", self.transaction_ended)
        event.listen(self.db, "before_commit", self.before_commit)

    def poison(self) -> None:
        self.failed = True
        self.validators.clear()

    def refuse(self, reason: str) -> NoReturn:
        self.poison()
        raise RuntimeBindingError(f"Protected platform boundary refuses: {reason}")

    def transaction_created(self, db: Session, transaction: SessionTransaction) -> None:
        if transaction.parent is None:
            if self.root is None and not self.retired:
                self.root = transaction
            elif self.protected:
                self.refuse("the issued root was already completed")
        elif transaction.nested and self.protected:
            if not self.opening_savepoint or self.savepoint is not None:
                self.refuse("only the operation-owned savepoint is permitted")
            self.savepoint = transaction

    def transaction_ended(self, db: Session, transaction: SessionTransaction) -> None:
        if transaction is self.root:
            self.retired = True
            if self.protected and not self.runtime_commit:
                self.poison()
            self.clear_root()

    def connection_guards(self) -> tuple[tuple[str, Callable[..., None]], ...]:
        return (
            ("commit", self.connection_commit),
            ("savepoint", self.connection_savepoint),
            ("release_savepoint", self.connection_release_savepoint),
            ("rollback_savepoint", self.connection_rollback_savepoint),
        )

    def clear_root(self) -> None:
        try:
            if self.connection is not None and self.connection_listening:
                # Attempt every removal even if a preceding one fails.
                first_failure: BaseException | None = None
                for name, listener in self.connection_guards():
                    try:
                        event.remove(self.connection, name, listener)
                    except BaseException as exc:
                        if first_failure is None:
                            first_failure = exc
                if first_failure is not None:
                    raise first_failure
        finally:
            self.connection_listening = False
            self.validators.clear()
            self.connection = None
            self.physical_root = None
            self.dbapi = None
            self.backend_xid = None
            self.root = None
            self.savepoint = None
            self.closing_savepoint = None
            self.rolling_back_savepoint = None
            self.physical_nested = None
            self.creating_physical_savepoint = False

    def check_configuration(self) -> None:
        with _claim_lock:
            bound = _binding
            if bound is None or not bound.required or bound.runtime is not self.runtime:
                self.refuse("the actual issuing runtime must be strictly bound")
        if self.failed or self.retired:
            self.refuse("this scope is poisoned or its one root has ended")
        if (
            self.runtime.platform_engine is not self.engine
            or self.runtime.platform_session_factory is not self.factory
            or self.factory.kw != self.factory_configuration
            or self.factory.class_ is not self.factory_class
            or self.db.get_bind() is not self.engine
        ):
            self.refuse("engine or session factory changed")

    def check(self, *, physical_commit: bool = False) -> Connection:
        self.check_configuration()
        # SQLAlchemy marks the Session root PREPARED before Connection.commit;
        # only that runtime-owned physical commit phase may inspect it inactive.
        prepared_commit = physical_commit and self.runtime_commit
        if (
            self.root is None
            or self.db.get_transaction() is not self.root
            or (not prepared_commit and not self.root.is_active)
        ):
            self.refuse("the issued Session root changed or became inactive")
        if self.db.get_nested_transaction() is not self.savepoint:
            self.refuse("a foreign nested transaction is active")
        connection = self.connection
        if (
            connection is None
            or connection.closed
            or connection.invalidated
            or connection.engine is not self.engine
            or (not prepared_commit and self.db.connection() is not connection)
            or self.physical_root is None
            or connection.get_transaction() is not self.physical_root
            or not self.physical_root.is_active
            or connection.connection.dbapi_connection is not self.dbapi
        ):
            self.custody_damaged = True
            self.refuse("physical connection or root identity changed")
        physical_nested = connection.get_nested_transaction()
        if self.savepoint is None:
            if physical_nested is not None or self.physical_nested is not None:
                self.custody_damaged = True
                self.refuse("a foreign physical savepoint is active")
        elif (
            self.physical_nested is None
            or physical_nested is not self.physical_nested
            or not self.physical_nested.is_active
        ):
            self.custody_damaged = True
            self.refuse("the operation's physical savepoint changed or ended")
        actual = tuple(
            connection.execute(
                text("SELECT pg_catalog.pg_backend_pid(), pg_catalog.txid_current()")
            ).one()
        )
        if actual != self.backend_xid:
            self.refuse("PostgreSQL backend or transaction identity changed")
        return connection

    def validate(self, *, physical_commit: bool = False) -> None:
        try:
            connection = self.check(physical_commit=physical_commit)
            for validator in self.validators:
                validator(connection)
            self.check(physical_commit=physical_commit)
        except BaseException:
            self.poison()
            raise

    def before_commit(self, db: Session) -> None:
        if self.failed:
            self.refuse("this scope is poisoned")
        if not self.protected:
            return
        if (
            self.closing_savepoint is not None
            and self.closing_savepoint is self.savepoint
            and db.get_nested_transaction() is self.closing_savepoint
        ):
            self.check()
            return
        if not self.runtime_commit or db.get_nested_transaction() is not None:
            self.refuse("only the runtime may commit the protected root")
        self.validate()

    def connection_savepoint(self, connection: Connection, name: str | None) -> None:
        if (
            self.failed
            or connection is not self.connection
            or not self.creating_physical_savepoint
            or self.savepoint is None
            or self.db.get_nested_transaction() is not self.savepoint
            or connection.get_nested_transaction() is not None
            or self.physical_nested is not None
        ):
            self.custody_damaged = True
            self.refuse("only the operation may create its physical savepoint")
        # One exact create event consumes the bounded materialization phase.
        self.creating_physical_savepoint = False

    def check_savepoint_completion(
        self, connection: Connection, name: str, phase: SessionTransaction | None
    ) -> None:
        physical = self.physical_nested
        if (
            self.failed
            and phase is not self.rolling_back_savepoint
            or connection is not self.connection
            or phase is None
            or phase is not self.savepoint
            or self.db.get_nested_transaction() is not phase
            or physical is None
            or connection.get_nested_transaction() is not physical
            or not physical.is_active
        ):
            self.custody_damaged = True
            self.refuse("only the operation may complete its physical savepoint")

    def connection_release_savepoint(
        self, connection: Connection, name: str, context: Any
    ) -> None:
        self.check_savepoint_completion(connection, name, self.closing_savepoint)

    def connection_rollback_savepoint(
        self, connection: Connection, name: str, context: Any
    ) -> None:
        self.check_savepoint_completion(connection, name, self.rolling_back_savepoint)

    def materialize_savepoint(self) -> None:
        self.creating_physical_savepoint = True
        try:
            self.db.connection()
        finally:
            self.creating_physical_savepoint = False
        if self.connection is None:
            self.refuse("the protected physical connection is absent")
        self.physical_nested = self.connection.get_nested_transaction()
        self.check()

    def connection_commit(self, connection: Connection) -> None:
        if not self.runtime_commit or connection is not self.connection:
            self.refuse("only the runtime may commit the physical root")
        self.validate(physical_commit=True)

    def commit(self) -> None:
        if self.failed:
            self.refuse("this scope is poisoned")
        if self.protected:
            self.validate()
        self.runtime_commit = True
        try:
            self.db.commit()
        except BaseException:
            self.poison()
            raise
        finally:
            self.runtime_commit = False

    def retire_damaged_root(self) -> None:
        """After an event unwinds, abandon damaged custody without SAVEPOINT repair.

        A rejected Core completion can deactivate its Python transaction before
        SQL ran. In that branch operation-only rollback is no longer guaranteed:
        roll back the actual physical root first, losing prior caller work too.
        Session.rollback alone may fail before it reaches that root. If physical
        rollback cannot be established, invalidate instead of reusing connection.
        Cleanup never replaces the original refusal/cancellation exception.
        """
        self.custody_damaged = True
        self.retired = True
        self.poison()
        if self.damaged_cleanup_done:
            return
        self.damaged_cleanup_done = True
        connection = self.connection
        physical_root = self.physical_root
        rolled_back = False
        if connection is not None:
            try:
                if (
                    not connection.closed
                    and physical_root is not None
                    and connection.get_transaction() is physical_root
                    and physical_root.is_active
                ):
                    connection.rollback()
                    rolled_back = (
                        not physical_root.is_active
                        and connection.get_transaction() is None
                    )
            except BaseException:
                self.poison()
            if not rolled_back:
                try:
                    connection.invalidate()
                except BaseException:
                    self.poison()
        try:
            self.db.rollback()
        except BaseException:
            self.poison()

    def dispose(self) -> None:
        first_failure: BaseException | None = None
        try:
            self.clear_root()
        except BaseException as exc:
            first_failure = exc
        for name, listener in (
            ("after_transaction_create", self.transaction_created),
            ("after_transaction_end", self.transaction_ended),
            ("before_commit", self.before_commit),
        ):
            try:
                event.remove(self.db, name, listener)
            except BaseException as exc:
                if first_failure is None:
                    first_failure = exc
        if first_failure is not None:
            raise first_failure


@contextmanager
def _protected_platform_operation(
    db: Session, validate: Callable[[Connection], None]
) -> Generator[Connection, None, None]:
    """Private owner seam; validator must perform actual authorization checks.

    The owner supplies a closure over its operation's real policy inputs, never
    a boolean verification flag. Validators survive successful operation savepoint
    completion and run again before the runtime's final root commit. Exceptions
    poison the lifetime even if a caller catches them. Only this operation's
    savepoint is rolled back here; previously flushed caller work stays in root
    for intact custody. Damaged physical custody or failed SAVEPOINT cleanup
    instead retires and rolls back the whole root (or invalidates the connection).
    """
    scope = _platform_scopes.get(db)
    if scope is None:
        raise RuntimeBindingError("Session was not issued by platform_session")
    # A protected attempt starts before capture/I/O, even if capture itself fails.
    scope.protected = True
    try:
        if scope.failed or scope.retired:
            scope.refuse("the issued scope has ended or failed")
        if db.in_nested_transaction() or scope.savepoint is not None:
            scope.refuse("protected operation cannot enter a nested transaction")
        if db.new or db.dirty or db.deleted:
            scope.refuse("flush caller changes before entering protected work")
        # No connection/autobegin is requested for ordinary unprotected sessions.
        # Entry can establish the first real root exactly once, then captures it.
        scope.check_configuration()
        if scope.connection is None:
            if db.get_bind() is not scope.engine:
                scope.refuse("Session is bound to a foreign engine")
            if scope.engine.dialect.name != "postgresql":
                scope.refuse("protected operations require PostgreSQL")
            connection = db.connection()
            scope.connection = connection
            scope.physical_root = connection.get_transaction()
            scope.dbapi = connection.connection.dbapi_connection
            # Install physical guards before any owner validator can run.
            for name, listener in scope.connection_guards():
                event.listen(connection, name, listener)
            scope.connection_listening = True
            if connection.get_nested_transaction() is not None:
                scope.refuse("a preexisting physical savepoint is active")
            scope.backend_xid = tuple(
                connection.execute(
                    text(
                        "SELECT pg_catalog.pg_backend_pid(), pg_catalog.txid_current()"
                    )
                ).one()
            )
        scope.protected = True
        scope.validators.append(validate)
        scope.validate()
        scope.opening_savepoint = True
        try:
            nested = db.begin_nested()
        finally:
            scope.opening_savepoint = False
        try:
            scope.materialize_savepoint()
            yield scope.check()
            scope.validate()
            scope.closing_savepoint = nested
            try:
                nested.commit()
            finally:
                scope.closing_savepoint = None
        except BaseException:
            scope.poison()
            connection = scope.connection
            physical = scope.physical_nested
            intact = (
                connection is not None
                and not connection.closed
                and physical is not None
                and connection.get_nested_transaction() is physical
                and physical.is_active
                and db.get_nested_transaction() is nested
                and nested.is_active
            )
            if scope.custody_damaged or not intact:
                scope.retire_damaged_root()
            else:
                try:
                    scope.rolling_back_savepoint = nested
                    nested.rollback()
                except BaseException:
                    scope.retire_damaged_root()
            raise
        finally:
            scope.savepoint = None
            scope.physical_nested = None
            scope.rolling_back_savepoint = None
        scope.validate()
    except BaseException:
        scope.poison()
        if scope.custody_damaged:
            scope.retire_damaged_root()
        raise
