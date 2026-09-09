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
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

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
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        platform_engine = create_engine(
            platform_database_url or database_url,
            pool_pre_ping=True,
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
            f"set_config('{name}', :tenant_id, :is_local)"
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
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

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


@dataclass(frozen=True)
class _Binding:
    runtime: DatabaseRuntime
    required: bool


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
            _binding = _Binding(runtime=runtime, required=True)
        # else: IDEMPOTENCE — identical (runtime, required), nothing to do.


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
