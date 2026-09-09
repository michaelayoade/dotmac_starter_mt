#!/usr/bin/env python3
"""Prove kernel-owned paths run through a PRODUCT-supplied `DatabaseRuntime`
with the reference runtime (`dotmac_kernel.db`) genuinely unavailable.

A direct-import count reaching zero is not the property that matters — a
product that imports `dotmac_kernel.db` nowhere can still have the kernel
itself reach it transitively at boot or on every authenticated request. This
probe makes that impossible to fake: `dotmac_kernel.db` is blocked from ever
being imported (``sys.modules["dotmac_kernel.db"] = None``, so any
``import dotmac_kernel.db`` anywhere raises ``ImportError``) BEFORE a single
kernel module loads, and only then does the probe drive boot, an authenticated
request, and a CLI-shaped entry point through a `DatabaseRuntime` the probe
itself constructs and installs via `ProductAssemblySpec.database_runtime`.

Run in an isolated subprocess (see `tests/architecture
/test_kernel_runtime_composition_seam.py`), the same isolation style as
`check_kernel_app_factory_import.py`'s import-boundary probe, with
``DATABASE_URL``/``PLATFORM_DATABASE_URL``/``PYTHONPATH`` removed by the
caller.

What is proved LIVE, against a real (SQLite) engine the runtime executes SQL
against:

* **Boot** — `create_app(spec)` with `spec.database_runtime` set, entered as an
  ASGI lifespan (`with TestClient(app) as client`), runs
  `_required_setting_errors()` (a startup check) through
  `resolve_database_runtime().platform_session()` and serves `/health`.
* **An authenticated request** — `POST /platform/auth/logout` against
  `platform_auth_router` mounted with no other machinery, driven by a
  PRE-SEEDED `PlatformSession` row rather than a minted password: this probe
  proves the runtime seam, not the credential lifecycle, so it never calls
  `login()` and never hashes a password (hard rule 42 — password hashing has
  one owner, `dotmac_kernel.credential_lifecycle`, and a probe with no real
  credential to verify has no business calling it either). `require_platform_admin`
  resolves its session through `dotmac_kernel.deps.get_platform_db`, which
  resolves the runtime through `resolve_database_runtime()`.
* **A CLI-shaped entry point** — a tenant-provisioning command (no credential
  material involved at all) reached through
  `resolve_database_runtime().platform_session()` instead of building its own
  engine.
* **A worker path** — `dotmac_kernel.messaging.worker.run_once` is handed
  `resolve_database_runtime().platform_session_factory`/`.session_factory`
  directly (its documented contract: it "NEVER constructs an engine or a
  sessionmaker — it RECEIVES session factories"). It reaches a real `execute()`
  against the product engine and fails ONLY on `claim_outbox_batch` — a
  Postgres `SECURITY DEFINER` function this SQLite engine does not have. That
  failure mode (a SQL-dialect gap, not an import/config error) is the proof:
  the session plumbing is the product runtime's, `dotmac_kernel.db` was never
  reached to produce it, and only the delivery SQL itself needs Postgres —
  which is this repository's existing, documented split between SQLite unit
  coverage and the top-level Postgres canaries.

At every stage, `sys.modules["dotmac_kernel.db"]` stays `None`: it is asserted
one final time before the probe exits successfully.

## The sensitivity control the authenticated-request stage needs

Sidestepping `login()` (see that stage's own comment) avoids hashing a
password, but it also means that stage no longer proves anything by itself —
a stubbed-away authentication check would pass whether or not the runtime
seam works, and would keep passing if the seam were deleted. `argv[1] ==
"fallback"` runs `main_fallback_sensitivity()` instead of `main()`: it seeds
the IDENTICAL admin/session into the PRODUCT's own runtime, but never installs
it (`ProductAssemblySpec.database_runtime` absent, no strict mode,
`dotmac_kernel.db` genuinely importable this time — the fallback is
deliberately live). `resolve_database_runtime()` then falls back to the
reference runtime, a DIFFERENT, empty database the seeded session was never
written to, and the same `POST /platform/auth/logout` call must therefore
REFUSE (401) rather than succeed (204). If it still returned 204, the
authenticated-request stage would be demonstrating nothing about which
runtime is installed.
"""

from __future__ import annotations

import sys

# THE FIRST THING THIS PROCESS DOES, before any `dotmac_kernel` import: make
# `dotmac_kernel.db` unimportable -- UNLESS this run needs it GENUINELY
# reachable to prove something about reaching it:
#
# * the fallback sensitivity control (`argv[1] == "fallback"`), proving the
#   authenticated-request stage fails without a bound runtime;
# * the paired plant proving a reference IMPORT arriving AFTER a strict bind
#   refuses (`argv[1] == "after-strict-bind"`) — it needs to actually ATTEMPT
#   the import to prove the refusal, not have it pre-empted by this block;
# * real-assembly composition (`argv[1] == "real-assembly"`). This one is
#   easy to get backwards, and an earlier version of this probe did: with
#   the block ACTIVE, `sys.modules["dotmac_kernel.db"]` already contains the
#   key (mapped to `None`) before `import app.assembly` ever runs, so a
#   check written as `"dotmac_kernel.db" not in sys.modules` is FALSE
#   immediately — not because anything imported it, but because the block's
#   OWN sentinel put the key there. Worse, in the PLANTED (eager-import)
#   case it meant the planted import crashed via `ModuleNotFoundError`
#   *inside* `import app.assembly` — never reaching
#   `bind_database_runtime`'s own atomic-claim refusal at all, so the plant
#   "passed" by hitting the wrong mechanism entirely. Leaving
#   `dotmac_kernel.db` genuinely reachable here is what lets the CLEAN case
#   prove "nothing tried" (checked via `sys.modules.get(...) is None`, which
#   reads true precisely because nothing tried) and the PLANTED case prove
#   the REAL refusal — the eager import genuinely claims the reference slot,
#   and `bind_database_runtime` then genuinely refuses against that claim.
#
# `sys.modules[name] = None` is Python's own mechanism for the block (import
# machinery raises `ImportError: import of {name} halted; None in
# sys.modules` for any later `import dotmac_kernel.db` or
# `from dotmac_kernel.db import ...`, anywhere in the process).
_NEEDS_REFERENCE_REACHABLE = {"fallback", "after-strict-bind", "real-assembly"}
if not (len(sys.argv) > 1 and sys.argv[1] in _NEEDS_REFERENCE_REACHABLE):
    sys.modules["dotmac_kernel.db"] = None  # type: ignore[assignment]


def main() -> None:
    # dotmac_kernel.models_platform / .settings_models register their tables
    # on the shared `Base.metadata` this import brings in — needed before
    # `create_all` so the probe's engine has somewhere to write.
    from dotmac_kernel import (
        models_platform,  # noqa: F401
        settings_models,  # noqa: F401
    )
    from dotmac_kernel.api_documentation import (
        ApiDocumentationPolicy,
        DocumentationExposure,
    )
    from dotmac_kernel.app_factory import create_app
    from dotmac_kernel.assembly import ProductAssemblySpec
    from dotmac_kernel.models import Base, Tenant
    from dotmac_kernel.models_platform import PlatformAdmin, PlatformSession
    from dotmac_kernel.platform_auth import issue_platform_token
    from dotmac_kernel.security import hash_token
    from dotmac_kernel.session_runtime import DatabaseRuntime, resolve_database_runtime
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # ── the product's own runtime: one SQLite engine, one shared connection ──
    #
    # `check_same_thread=False` + `StaticPool` is this PROBE's own engine
    # configuration choice (the same seam a product exercises with its own
    # DSN/pool policy) — FastAPI's sync dependencies run in a worker thread,
    # and an unshared `:memory:` connection per thread would silently see an
    # empty database.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    runtime = DatabaseRuntime(engine=engine)

    # A pre-seeded admin + session, not a minted password: this probe proves
    # the RUNTIME seam, not the credential lifecycle (hard rule 42 — password
    # hashing has one owner, `dotmac_kernel.credential_lifecycle`, and `login()`
    # is never called here, so `password_hash` holds an inert placeholder no
    # code ever hashes or verifies). `issue_platform_token`/`hash_token` are
    # the kernel's own SESSION-token machinery, not password hashing.
    with runtime.platform_session() as seed_db:
        admin = PlatformAdmin(
            email="probe-admin@platform.example.test",
            password_hash="unused-not-a-real-credential-hash",  # noqa: S106  # nosec B106 -- inert, never hashed or verified
            is_active=True,
        )
        seed_db.add(admin)
        seed_db.flush()
        seeded_token, expires_at = issue_platform_token(admin.id)
        seed_db.add(
            PlatformSession(
                admin_id=admin.id,
                token_hash=hash_token(seeded_token),
                expires_at=expires_at,
            )
        )

    # ── attachment point: ProductAssemblySpec.database_runtime ──────────────
    spec = ProductAssemblySpec(
        name="runtime-composition-seam-probe",
        database_runtime=runtime,
        api_documentation=ApiDocumentationPolicy(
            environment="development",
            interactive=DocumentationExposure.DISABLED,
            document=DocumentationExposure.DISABLED,
            rationale="probe: no documentation surface needed",
        ),
        web_enabled=False,
    )

    # ── BOOT ─────────────────────────────────────────────────────────────────
    app = create_app(spec)
    # `resolve_database_runtime()` must already resolve to the installed runtime
    # the instant `create_app` returns — before any request, before lifespan.
    assert (
        resolve_database_runtime() is runtime
    ), "create_app(spec) did not install spec.database_runtime"
    with TestClient(app) as client:
        # Entering the context manager runs the ASGI lifespan, which is where
        # `_required_setting_errors()` calls
        # `resolve_database_runtime().platform_session()` — a REAL query against
        # the product engine, executed while `dotmac_kernel.db` is blocked.
        health = client.get("/health")
        assert health.status_code == 200, health.text
    print("PASS boot: create_app + startup validation ran on the product runtime")

    # ── AN AUTHENTICATED REQUEST ─────────────────────────────────────────────
    #
    # A standalone app carrying ONLY the platform-auth router — deliberately
    # without `TenantResolverMiddleware` (which this repository's own testing
    # model reserves for the Postgres canaries: it primes/resets a real
    # Postgres GUC, so a SQLite probe exercises the guard and the dependency
    # directly, the same way `tests/unit/test_platform_auth.py` does).
    from dotmac_kernel.platform_auth import platform_auth_router
    from fastapi import FastAPI

    platform_app = FastAPI()
    platform_app.include_router(platform_auth_router)
    with TestClient(platform_app, base_url="http://localhost") as platform_client:
        # No `login()` call: the session was pre-seeded above. This is exactly
        # `require_platform_admin` (the authenticated-request guard) resolving
        # `deps.get_platform_db` -> `resolve_database_runtime()` against the
        # product runtime, on a real bearer token this probe never hashed a
        # password to obtain.
        logout = platform_client.post(
            "/platform/auth/logout",
            headers={"Authorization": f"Bearer {seeded_token}"},
        )
        assert logout.status_code == 204, logout.text
    print(
        "PASS authenticated request: platform logout resolved "
        "deps.get_platform_db through the product runtime"
    )

    # ── A CLI-SHAPED ENTRY POINT ──────────────────────────────────────────────
    #
    # A tenant-provisioning command — no credential material involved —
    # reached through `resolve_database_runtime()` instead of building its own
    # engine, the way a real CLI script would.
    def cli_create_tenant(slug: str, name: str) -> None:
        with resolve_database_runtime().platform_session() as db:
            db.add(Tenant(slug=slug, name=name))

    cli_create_tenant("probe-cli-tenant", "Probe CLI Tenant")
    with runtime.platform_session() as verify_db:
        from sqlalchemy import select

        found = verify_db.scalars(
            select(Tenant).where(Tenant.slug == "probe-cli-tenant")
        ).first()
        assert found is not None, "the CLI-shaped entry point did not persist"
    print("PASS CLI entry point: upsert ran through resolve_database_runtime()")

    # ── A WORKER PATH ─────────────────────────────────────────────────────────
    #
    # `messaging.worker.run_once` never imports `dotmac_kernel.db` — it
    # RECEIVES session factories. Handed the product runtime's own factories,
    # it reaches a real `execute()` on the product engine; the only failure is
    # the Postgres-only `claim_outbox_batch` function, which is a SQL-dialect
    # gap, not an import or configuration one.
    from dotmac_kernel.messaging.worker import LoggingTransport, run_once
    from sqlalchemy.exc import OperationalError

    dispatcher_db = resolve_database_runtime().platform_session_factory()
    try:
        run_once(
            dispatcher_db=dispatcher_db,
            tenant_session_factory=resolve_database_runtime().session_factory,
            transport=LoggingTransport(),
            worker_id="probe-worker",
        )
    except OperationalError as exc:
        # The exact SQLite error text for an unsupported table-valued-function
        # FROM clause varies by SQLite version ("no such function", "near
        # \"(\": syntax error", ...). What matters is the EXCEPTION CLASS:
        # `OperationalError` only ever comes from a statement that actually
        # reached a real DBAPI connection — the product engine's — which is
        # the property this stage exists to prove. A wiring defect (missing
        # import, wrong callable shape, no engine at all) would raise
        # `ImportError`/`TypeError`/`AttributeError` instead, well before any
        # SQL is sent.
        print(
            "PASS worker path: run_once reached the product runtime's session "
            f"(failure is the Postgres-only claim_outbox_batch function, not "
            f"an import or configuration gap): {type(exc.orig).__name__}"
        )
    else:
        raise AssertionError(
            "run_once succeeded against SQLite — claim_outbox_batch is "
            "Postgres-only, so either the probe's assumption is stale or the "
            "call never reached the product engine at all"
        )
    finally:
        dispatcher_db.close()

    # ── the reference runtime was never reached, anywhere above ─────────────
    assert (
        sys.modules.get("dotmac_kernel.db") is None
    ), "dotmac_kernel.db was imported during the probe"
    print("PASS dotmac_kernel.db stayed unimported for the whole probe")


def main_strict_refusal() -> None:
    """The other half of the seam: nothing forces a product to USE it.

    `resolve_database_runtime()`'s fallback is correct for the reference
    assembly, but it means a product that simply forgets to set
    `ProductAssemblySpec.database_runtime` silently gets the eager reference
    runtime — the same defect shape this whole seam exists to close, one
    level up. `require_database_runtime=True` is the declared opt-out of that
    fallback: `create_app` must refuse to build, and must do so WITHOUT ever
    importing `dotmac_kernel.db` — a refusal that itself imported the module
    it is refusing to fall back to would prove nothing.
    """
    from dotmac_kernel.api_documentation import (
        ApiDocumentationPolicy,
        DocumentationExposure,
    )
    from dotmac_kernel.app_factory import create_app
    from dotmac_kernel.assembly import ProductAssemblySpec

    spec = ProductAssemblySpec(
        name="strict-mode-probe",
        database_runtime=None,
        require_database_runtime=True,
        api_documentation=ApiDocumentationPolicy(
            environment="development",
            interactive=DocumentationExposure.DISABLED,
            document=DocumentationExposure.DISABLED,
            rationale="probe: no documentation surface needed",
        ),
        web_enabled=False,
    )

    try:
        create_app(spec)
    except RuntimeError as exc:
        assert "require_database_runtime" in str(
            exc
        ), f"create_app refused for the wrong reason: {exc}"
    else:
        raise AssertionError(
            "create_app built successfully with require_database_runtime=True "
            "and no database_runtime — the missed-binding refusal did not fire"
        )

    assert sys.modules.get("dotmac_kernel.db") is None, (
        "the strict refusal itself imported dotmac_kernel.db — a refusal "
        "that reaches the thing it is refusing to fall back to proves nothing"
    )
    print(
        "PASS strict mode: create_app refused a missed database_runtime "
        "binding without ever importing dotmac_kernel.db"
    )


def main_fallback_sensitivity() -> None:
    """Sensitivity control for the authenticated-request stage in `main()`.

    That stage sidesteps `login()` to avoid hashing a password (hard rule 42),
    driving `POST /platform/auth/logout` from a PRE-SEEDED session instead.
    That is only a proof of the runtime seam if the stage genuinely depends on
    which runtime is installed — otherwise it is a stub that would pass
    whether or not the seam works. This run makes the seam ABSENT on purpose
    (no `ProductAssemblySpec.database_runtime`, no strict mode,
    `dotmac_kernel.db` genuinely importable) and shows the IDENTICAL stage
    then FAILS: the seeded session was written to the product's own runtime,
    never installed, so `resolve_database_runtime()` falls back to the reference
    runtime — a different, empty database — and the logout call must be
    refused rather than accepted.
    """
    import os

    # `dotmac_kernel.db` is reachable in THIS run (see the module-level
    # guard), and its eager engine needs a parseable DATABASE_URL the moment
    # it is imported — a fresh, empty SQLite file, so the reference runtime
    # is real but genuinely has none of the rows seeded below.
    os.environ.setdefault("DATABASE_URL", "sqlite:///./fallback-sensitivity.sqlite3")
    os.environ.setdefault(
        "PLATFORM_DATABASE_URL", "sqlite:///./fallback-sensitivity.sqlite3"
    )

    from dotmac_kernel import (
        models_platform,  # noqa: F401
        settings_models,  # noqa: F401
    )
    from dotmac_kernel.models import Base
    from dotmac_kernel.models_platform import PlatformAdmin, PlatformSession
    from dotmac_kernel.platform_auth import issue_platform_token, platform_auth_router
    from dotmac_kernel.security import hash_token
    from dotmac_kernel.session_runtime import DatabaseRuntime, resolve_database_runtime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # The PRODUCT's own runtime — built and seeded exactly like `main()` —
    # but deliberately NEVER bound via `bind_database_runtime` or
    # `ProductAssemblySpec.database_runtime`.
    product_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(product_engine)
    product_runtime = DatabaseRuntime(engine=product_engine)
    with product_runtime.platform_session() as seed_db:
        admin = PlatformAdmin(
            email="probe-admin@platform.example.test",
            password_hash="unused-not-a-real-credential-hash",  # noqa: S106  # nosec B106 -- inert, never hashed or verified
            is_active=True,
        )
        seed_db.add(admin)
        seed_db.flush()
        seeded_token, expires_at = issue_platform_token(admin.id)
        seed_db.add(
            PlatformSession(
                admin_id=admin.id,
                token_hash=hash_token(seeded_token),
                expires_at=expires_at,
            )
        )

    # Prove the premise: nothing installed the product runtime, so resolution
    # falls back to the reference assembly's own instance.
    fallback = resolve_database_runtime()
    assert fallback is not product_runtime, (
        "the product runtime is still installed from an earlier stage — "
        "this control requires a genuinely empty fallback"
    )
    from dotmac_kernel.db import engine as reference_engine  # the fallback itself

    # The reference engine needs the SAME tables (an empty platform_sessions
    # table, not a missing one) so the guard's query answers "no session
    # found" rather than raising on a schema that was never created.
    Base.metadata.create_all(reference_engine)

    platform_app = FastAPI()
    platform_app.include_router(platform_auth_router)
    with TestClient(platform_app, base_url="http://localhost") as client:
        logout = client.post(
            "/platform/auth/logout",
            headers={"Authorization": f"Bearer {seeded_token}"},
        )
        assert logout.status_code != 204, (
            "the authenticated-request stage accepted a session that was "
            "only ever written to the PRODUCT runtime while nothing "
            "installed it — the stage does not actually exercise "
            "resolve_database_runtime() and proves nothing about the seam"
        )
    print(
        "PASS fallback sensitivity: the authenticated-request stage genuinely "
        "depends on the installed runtime — with none installed, the "
        f"identical session is correctly refused ({logout.status_code})"
    )


#: Kept SEPARATE on purpose (`session_runtime.bind_database_runtime`'s own
#: docstring explains why): filing MONOTONIC PROMOTION under "idempotent" is
#: exactly how a future field addition could smuggle in a real change under
#: cover of an established "idempotent rebinding is fine" exemption. Four
#: transitions, four probe entry points, four independent processes — no
#: shared state between them to accidentally blur the distinction.


def main_rebind_idempotent() -> None:
    """IDEMPOTENCE: the IDENTICAL runtime, at the IDENTICAL policy, changes
    nothing. Calling `bind_database_runtime` twice with the same arguments
    is exactly as safe as calling it once."""
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        bind_database_runtime,
        resolve_database_runtime,
    )
    from sqlalchemy import create_engine

    runtime = DatabaseRuntime(engine=create_engine("sqlite://"))
    bind_database_runtime(runtime, required=True)
    assert resolve_database_runtime() is runtime

    bind_database_runtime(runtime, required=True)  # identical: idempotent
    assert resolve_database_runtime() is runtime
    print("PASS idempotence: reinstalling the identical (runtime, policy) succeeded")


def main_rebind_monotonic_promotion() -> None:
    """MONOTONIC PROMOTION: the SAME runtime, `required` going False -> True.
    The binding DOES change here — that is what makes it a promotion and not
    idempotence — and it is permitted only because strictness tightens."""
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        bind_database_runtime,
        resolve_database_runtime,
    )
    from sqlalchemy import create_engine

    runtime = DatabaseRuntime(engine=create_engine("sqlite://"))
    bind_database_runtime(runtime, required=False)
    assert resolve_database_runtime() is runtime

    bind_database_runtime(runtime, required=True)  # same runtime, stricter
    assert resolve_database_runtime() is runtime
    print(
        "PASS monotonic promotion: the same runtime's policy tightened from "
        "optional to required"
    )


def main_rebind_refuses_a_different_runtime() -> None:
    """REFUSAL (cause 1 of 2): a DIFFERENT `DatabaseRuntime` object, at any
    policy, is never accepted — one process supports one binding, and a
    second application or caller supplying a different instance is a
    configuration conflict, never a silent clobber of the first."""
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        RuntimeBindingError,
        bind_database_runtime,
        resolve_database_runtime,
    )
    from sqlalchemy import create_engine

    runtime_one = DatabaseRuntime(engine=create_engine("sqlite://"))
    runtime_two = DatabaseRuntime(engine=create_engine("sqlite://"))

    bind_database_runtime(runtime_one, required=True)
    try:
        bind_database_runtime(runtime_two, required=True)
    except RuntimeBindingError:
        pass
    else:
        raise AssertionError(
            "bind_database_runtime accepted a second, DIFFERENT runtime — "
            "the first application's binding was silently clobbered"
        )
    assert resolve_database_runtime() is runtime_one, (
        "the refused second bind changed what resolve_database_runtime() "
        "answers — a refusal must leave the sealed binding untouched"
    )
    print(
        "PASS refusal (different runtime): a second, different runtime was "
        "refused and the original binding is still authoritative"
    )


def main_rebind_refuses_a_downgrade() -> None:
    """REFUSAL (cause 2 of 2): the SAME runtime, `required` going True ->
    False (a downgrade). There is no public way to loosen strictness once
    sealed — this is the same REFUSAL outcome as a different runtime, but a
    genuinely different cause, and the probe/test names say so separately."""
    from dotmac_kernel.session_runtime import (
        DatabaseRuntime,
        RuntimeBindingError,
        bind_database_runtime,
    )
    from sqlalchemy import create_engine

    runtime = DatabaseRuntime(engine=create_engine("sqlite://"))
    bind_database_runtime(runtime, required=True)
    try:
        bind_database_runtime(runtime, required=False)
    except RuntimeBindingError:
        pass
    else:
        raise AssertionError(
            "bind_database_runtime accepted downgrading an already-required "
            "binding to required=False — strictness is supposed to be "
            "monotonic with no public way back down"
        )
    print(
        "PASS refusal (downgrade): weakening an already-required binding "
        "to optional was refused"
    )


def main_real_assembly_strict() -> None:
    """Acceptance against the REAL Starter assembly, not a zero-feature
    surrogate.

    A bespoke, feature-free `ProductAssemblySpec` (as `main()` above builds)
    cannot see the transitive-reach hazard this seam exists to close: the
    real `app.assembly` composes `FEATURE_MODULES` via `load_manifests` at
    IMPORT TIME (`app/assembly.py`'s module-level `ProductAssemblySpec(...)`
    call), well before `create_app` ever runs, and several feature services
    used to import `dotmac_kernel.db` at module scope to reach
    `conflict_savepoint` (fixed on `main` by the
    `refactor/conflict-savepoint-engine-free-owner` predecessor, which moved
    those onto the already-public, engine-free
    `dotmac_kernel.transactions.conflict_savepoint`).

    MEASURED, not asserted from a cause: this runs in its OWN fresh
    interpreter (the isolated-subprocess harness), so `sys.modules` cannot
    carry an entry left behind by some unrelated earlier import the way it
    could inside a shared pytest process — but a bare `"dotmac_kernel.db" in
    sys.modules` check still only answers "is it present", never "who put it
    there". `sys.addaudithook`'s `"import"` event is the instrument that
    answers the second question: it fires for every import ATTEMPT, with the
    importing frame's stack captured at the moment of the attempt, so a
    failure here reports the OBSERVED CHAIN — not a hypothesis about which
    branch or PR is or is not present.
    """
    import os

    os.environ.setdefault("DATABASE_URL", "sqlite:///./real-assembly-strict.sqlite3")
    os.environ.setdefault(
        "PLATFORM_DATABASE_URL", "sqlite:///./real-assembly-strict.sqlite3"
    )

    import dataclasses
    import traceback

    from dotmac_kernel import create_app
    from dotmac_kernel.models import Base
    from dotmac_kernel.session_runtime import DatabaseRuntime
    from dotmac_kernel.settings_models import DomainSetting, DomainSettingHistory
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.pool import StaticPool

    # Assert ABSENCE before the import under test — this is the baseline the
    # audit hook's later findings are compared against, and it is itself
    # meaningful in a fresh interpreter: nothing has requested
    # dotmac_kernel.db yet.
    assert "dotmac_kernel.db" not in sys.modules, (
        "dotmac_kernel.db was already present before this probe imported "
        "anything of its own — a fresh-interpreter precondition failure, "
        "not a claim about app.assembly"
    )

    # The instrument: records the first `import dotmac_kernel.db` REQUEST
    # (not merely its eventual presence in sys.modules) and the stack at the
    # moment it happened. Audit hooks cannot be removed once added, which is
    # fine here — this process exists for exactly one measurement.
    _reached: list[str] = []

    def _record_dotmac_kernel_db_import(event: str, args: object) -> None:
        if event != "import":
            return
        module_name = args[0] if isinstance(args, tuple) and args else None
        if module_name == "dotmac_kernel.db":
            _reached.append("".join(traceback.format_stack()))

    sys.addaudithook(_record_dotmac_kernel_db_import)

    def _report_if_reached(phase: str) -> None:
        if not _reached:
            return
        raise AssertionError(
            f"dotmac_kernel.db was requested during {phase} — OBSERVED "
            f"import chain (first request's stack):\n{_reached[0]}"
        )

    import app.assembly

    _report_if_reached("`import app.assembly`")
    assert "dotmac_kernel.db" not in sys.modules, (
        "dotmac_kernel.db is present in sys.modules after `import "
        "app.assembly`, but the audit hook recorded no import event for "
        "it — report this discrepancy rather than guessing at it"
    )
    print(
        "PASS real assembly: import app.assembly requested no import of "
        "dotmac_kernel.db (measured via sys.addaudithook, not inferred "
        "from sys.modules membership alone)"
    )

    # NARROWED, not the full composed schema (option 2 of the three the
    # fixture failure named, in preference order — see below for why option
    # 1, this repo's own `dotmac_kernel.testing.create_test_engine`, does
    # not fit this specific caller).
    #
    # The real assembly composes `dotmac_ticketing`/`dotmac_template_studio`,
    # whose tables are declared in module schemas (`mod_tkt`/`mod_tstudio`)
    # that plain SQLite has no concept of; a bare `Base.metadata.create_all`
    # against every table registered on the shared `Base.metadata` (which
    # `import app.assembly` populates in full) fails with `unknown database
    # mod_tkt` before this function's own assertions ever run — a FIXTURE
    # defect, not a finding about the seam. The property under test is
    # about IMPORTS AND BINDING, and the only runtime consumer this function
    # actually drives is the settings feature's seed hook — so the fix is to
    # create only what THAT hook actually writes, which also means no module
    # schema is ever selected and there is nothing to ATTACH.
    #
    # Read `ensure_by_key` (the function `seed_platform_defaults` calls) end
    # to end rather than growing this list by trial-and-error against CI:
    # it writes the `DomainSetting` row itself, then unconditionally calls
    # `_record_history`, which writes one `DomainSettingHistory` row and
    # flushes — the second and LAST table this path touches. It also calls
    # `_emit_change`, which is a no-op unless `SETTINGS_CHANGE_EVENTS` is
    # set (unset here) and, even when active, swallows its own exceptions
    # rather than propagating them — so it cannot be a third table this
    # narrowed set needs. `.__table__` off each MAPPED CLASS, not a
    # restated table-name string, so the set stays derived from the models
    # `ensure_by_key` actually uses.
    #
    # `create_test_engine()` (the repo's existing fixture,
    # `tests/unit/conftest.py` and `dotmac_kernel.testing.harness` use it
    # fleet-wide) was tried first and DOES solve the schema problem via its
    # own `tables=` parameter — but it hardcodes its pool to SQLAlchemy's
    # default for `sqlite:///:memory:` (`SingletonThreadPool`, confirmed by
    # inspection: one connection PER THREAD, so each thread sees its own
    # independent in-memory database) with no override. This function's
    # seed hook runs off the event loop via `asyncio.to_thread` — genuinely
    # a different thread than the one that builds `product_engine` and later
    # verifies the seeded row — so a `SingletonThreadPool` engine would make
    # the write and the read land on two different, unconnected databases
    # regardless of the schema fix. `StaticPool` (one shared connection for
    # the whole engine, used elsewhere in this same probe) is what makes the
    # write visible across threads; `create_test_engine()` does not expose
    # that knob, so this narrowed case builds its own engine rather than
    # reusing a fixture that cannot satisfy both constraints at once.
    product_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        product_engine,
        tables=[DomainSetting.__table__, DomainSettingHistory.__table__],
    )
    product_runtime = DatabaseRuntime(engine=product_engine)

    spec = dataclasses.replace(
        app.assembly.assembly,
        database_runtime=product_runtime,
        require_database_runtime=True,
    )
    app = create_app(spec)

    _report_if_reached("create_app(spec) under a strict binding")
    assert "dotmac_kernel.db" not in sys.modules
    print(
        "PASS real assembly, strict: composing app.assembly's real feature "
        "manifests under a strict binding requested no import of "
        "dotmac_kernel.db"
    )

    # ── DIAGNOSTIC ISOLATION, before the lifespan-driven check below ────────
    #
    # If the lifespan-driven check finds no seeded row, that is ONE symptom
    # with (at least) three different causes, only one of which is a seam
    # defect: (1) seed_platform_defaults() genuinely does not resolve the
    # bound runtime; (2) it resolves and writes, but the write is not
    # visible to the later read (a fixture/pooling problem); (3) it
    # resolves, writes, and something rolls it back. These checks isolate
    # (1) from (2)/(3) BEFORE the lifespan is ever entered, by calling the
    # exact same production code directly, synchronously, on THIS thread —
    # bypassing `_run_enabled_seeds`' `asyncio.to_thread` dispatch and its
    # own exception-swallowing (`except Exception: logger.warning(...)`,
    # which would otherwise turn a real defect into silence).
    from dotmac_kernel.session_runtime import resolve_database_runtime
    from dotmac_kernel.settings_admin import all_specs

    from app.features.settings.seed import seed_platform_defaults

    # (a) the spec registry itself — a seed hook with nothing registered to
    # seed would also write no row, with NO runtime-binding defect at all.
    registered = all_specs()
    assert registered, (
        "the setting-spec registry is EMPTY at this point -- "
        "seed_platform_defaults() would have nothing to write regardless of "
        "which runtime it resolves; this is a spec-registration gap (check "
        "app/features/settings/__init__.py's `from app.features.settings "
        "import spec` import), not evidence about resolve_database_runtime()"
    )

    # (b) resolve_database_runtime(), called directly, right after
    # create_app sealed the binding — confirms the binding itself is
    # correct before blaming the seed hook for a binding problem it does
    # not have.
    assert resolve_database_runtime() is product_runtime, (
        "resolve_database_runtime() does not answer the PRODUCT runtime "
        "immediately after create_app(spec) sealed it -- a defect in the "
        "binding itself, upstream of seed_platform_defaults() entirely"
    )

    # (c) THE ISOLATING CALL: seed_platform_defaults() invoked directly,
    # synchronously, on this thread — the same resolve_database_runtime()
    # global state (a)/(b) just confirmed, with NO asyncio.to_thread
    # dispatch and NO exception-swallowing layer between this call and its
    # result.
    seed_platform_defaults()
    with product_runtime.platform_session() as direct_check:
        direct_seeded = direct_check.scalars(select(DomainSetting)).first()
    assert direct_seeded is not None, (
        "CATEGORY 1 -- a genuine defect: seed_platform_defaults() called "
        "DIRECTLY (same thread, same process, the SAME resolve_database_"
        "runtime() binding just confirmed above) still wrote nothing. This "
        "rules out the lifespan's async/threaded dispatch and its "
        "exception-swallowing as the cause -- the defect is in "
        "seed_platform_defaults()/resolve_database_runtime() itself, not in "
        "how the lifespan reaches it."
    )
    print(
        "PASS real assembly, strict, direct seed call: "
        "seed_platform_defaults() called directly (bypassing the lifespan's "
        "asyncio.to_thread dispatch) wrote through resolve_database_runtime()"
    )

    # (d) The lifespan's OWN gate: `if settings.seed_on_startup:` around the
    # `_run_enabled_seeds` call. If this is False (e.g. an inherited
    # SEED_ON_STARTUP=false from the environment), the lifespan never calls
    # the seed hook AT ALL — a distinct, cheaply-ruled-out sub-cause of
    # "resolved and wrote, but the later read sees nothing" that is neither
    # a threading/pooling gap nor a rollback.
    from dotmac_kernel.config import settings as _kernel_settings

    assert _kernel_settings.seed_on_startup, (
        "settings.seed_on_startup is False in this process, so the "
        "lifespan's `if settings.seed_on_startup:` gate would skip "
        "_run_enabled_seeds entirely -- the seed hook is never called "
        "through the lifespan at all, independent of any runtime-binding "
        "or threading question"
    )

    with TestClient(app) as client:
        # Entering the lifespan runs _run_enabled_seeds -> the settings
        # feature's seed_platform_defaults() AGAIN (idempotent — see that
        # function's own docstring) -- a REAL write reached through the
        # actual asyncio.to_thread dispatch this time, while
        # dotmac_kernel.db stays unrequested.
        health = client.get("/health")
        assert health.status_code == 200, health.text

    _report_if_reached(
        "the real assembly's lifespan (including the settings feature's seed hook)"
    )
    assert "dotmac_kernel.db" not in sys.modules
    with product_runtime.platform_session() as verify_db:
        seeded = verify_db.scalars(select(DomainSetting)).first()
        assert seeded is not None, (
            "CATEGORY 2 or 3 -- a fixture problem, not a seam defect: the "
            "direct call above (bypassing the lifespan) already proved "
            "seed_platform_defaults() resolves the bound runtime and writes "
            "successfully, so this row's absence after going through the "
            "REAL lifespan's asyncio.to_thread dispatch points at a "
            "threading/pooling visibility gap or a rollback specific to "
            "that dispatch path, not at resolve_database_runtime() itself"
        )
    print(
        "PASS real assembly, strict, through seeding: the settings feature's "
        "startup seed wrote into the PRODUCT runtime, and dotmac_kernel.db "
        "was never requested through the whole lifespan"
    )


def main_reference_import_after_strict_bind() -> None:
    """Paired plant, ORDERING 2 of 2: a reference-runtime IMPORT arriving
    AFTER a strict bind must refuse — before constructing any engine.

    Ordering 1 of 2 — an eager reference import arriving BEFORE a strict
    bind, so the bind itself refuses against the recorded claim — is proved
    by `main_real_assembly_strict`'s plant counterpart in the test file
    (`test_one_eager_feature_import_defeats_a_strict_binding`, which
    reintroduces one eager `dotmac_kernel.db` import into a copy of
    `app.assembly` and shows `bind_database_runtime` refuse). The atomic
    claim (`session_runtime._claim_lock`) exists precisely to make the
    outcome independent of which side runs first — proving only one
    ordering would leave the other genuinely untested, an asymmetric proof
    of a claim advertised as mutual.

    This run needs `dotmac_kernel.db` GENUINELY importable (see the
    module-level guard above, which exempts `argv[1] == "after-strict-bind"`)
    so the import can actually be attempted and observed to refuse, rather
    than being pre-empted by the unrelated `sys.modules[...] = None` block
    every other stage in this probe relies on.
    """
    import os

    os.environ.setdefault("DATABASE_URL", "sqlite:///./after-strict-bind.sqlite3")
    os.environ.setdefault(
        "PLATFORM_DATABASE_URL", "sqlite:///./after-strict-bind.sqlite3"
    )

    from dotmac_kernel.session_runtime import DatabaseRuntime, bind_database_runtime
    from sqlalchemy import create_engine

    product_runtime = DatabaseRuntime(engine=create_engine("sqlite://"))
    bind_database_runtime(product_runtime, required=True)

    try:
        import dotmac_kernel.db  # noqa: F401
    except RuntimeError as exc:
        assert "already sealed a REQUIRED" in str(
            exc
        ), f"the import refused, but for the wrong reason: {exc}"
    else:
        raise AssertionError(
            "dotmac_kernel.db imported successfully AFTER a strict binding "
            "was already sealed — the atomic claim did not refuse the "
            "losing order"
        )
    assert "dotmac_kernel.db" not in sys.modules, (
        "dotmac_kernel.db's refusal still left a (presumably half-built) "
        "module object registered in sys.modules"
    )
    print(
        "PASS paired plant (reference-after-bind): dotmac_kernel.db refused "
        "to import after a strict binding was already sealed, before "
        "constructing any engine"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "strict":
        main_strict_refusal()
    elif len(sys.argv) > 1 and sys.argv[1] == "fallback":
        main_fallback_sensitivity()
    elif len(sys.argv) > 1 and sys.argv[1] == "rebind-idempotent":
        main_rebind_idempotent()
    elif len(sys.argv) > 1 and sys.argv[1] == "rebind-promotion":
        main_rebind_monotonic_promotion()
    elif len(sys.argv) > 1 and sys.argv[1] == "rebind-refuses-different-runtime":
        main_rebind_refuses_a_different_runtime()
    elif len(sys.argv) > 1 and sys.argv[1] == "rebind-refuses-downgrade":
        main_rebind_refuses_a_downgrade()
    elif len(sys.argv) > 1 and sys.argv[1] == "real-assembly":
        main_real_assembly_strict()
    elif len(sys.argv) > 1 and sys.argv[1] == "after-strict-bind":
        main_reference_import_after_strict_bind()
    else:
        main()
