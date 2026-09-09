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
  `get_database_runtime().platform_session()` and serves `/health`.
* **An authenticated request** — `POST /platform/auth/logout` against
  `platform_auth_router` mounted with no other machinery, driven by a
  PRE-SEEDED `PlatformSession` row rather than a minted password: this probe
  proves the runtime seam, not the credential lifecycle, so it never calls
  `login()` and never hashes a password (hard rule 42 — password hashing has
  one owner, `dotmac_kernel.credential_lifecycle`, and a probe with no real
  credential to verify has no business calling it either). `require_platform_admin`
  resolves its session through `dotmac_kernel.deps.get_platform_db`, which
  resolves the runtime through `get_database_runtime()`.
* **A CLI-shaped entry point** — a tenant-provisioning command (no credential
  material involved at all) reached through
  `get_database_runtime().platform_session()` instead of building its own
  engine.
* **A worker path** — `dotmac_kernel.messaging.worker.run_once` is handed
  `get_database_runtime().platform_session_factory`/`.session_factory`
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
deliberately live). `get_database_runtime()` then falls back to the
reference runtime, a DIFFERENT, empty database the seeded session was never
written to, and the same `POST /platform/auth/logout` call must therefore
REFUSE (401) rather than succeed (204). If it still returned 204, the
authenticated-request stage would be demonstrating nothing about which
runtime is installed.
"""

from __future__ import annotations

import sys

# THE FIRST THING THIS PROCESS DOES, before any `dotmac_kernel` import: make
# `dotmac_kernel.db` unimportable -- UNLESS this run is the fallback
# sensitivity control (`argv[1] == "fallback"`), which needs the reference
# runtime genuinely reachable in order to prove the authenticated-request
# stage fails without an installed product runtime. `sys.modules[name] = None`
# is Python's own mechanism for the block (import machinery raises
# `ImportError: import of {name} halted; None in sys.modules` for any later
# `import dotmac_kernel.db` or `from dotmac_kernel.db import ...`, anywhere in
# the process).
if not (len(sys.argv) > 1 and sys.argv[1] == "fallback"):
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
    from dotmac_kernel.session_runtime import DatabaseRuntime, get_database_runtime
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
    # `get_database_runtime()` must already resolve to the installed runtime
    # the instant `create_app` returns — before any request, before lifespan.
    assert (
        get_database_runtime() is runtime
    ), "create_app(spec) did not install spec.database_runtime"
    with TestClient(app) as client:
        # Entering the context manager runs the ASGI lifespan, which is where
        # `_required_setting_errors()` calls
        # `get_database_runtime().platform_session()` — a REAL query against
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
        # `deps.get_platform_db` -> `get_database_runtime()` against the
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
    # reached through `get_database_runtime()` instead of building its own
    # engine, the way a real CLI script would.
    def cli_create_tenant(slug: str, name: str) -> None:
        with get_database_runtime().platform_session() as db:
            db.add(Tenant(slug=slug, name=name))

    cli_create_tenant("probe-cli-tenant", "Probe CLI Tenant")
    with runtime.platform_session() as verify_db:
        from sqlalchemy import select

        found = verify_db.scalars(
            select(Tenant).where(Tenant.slug == "probe-cli-tenant")
        ).first()
        assert found is not None, "the CLI-shaped entry point did not persist"
    print("PASS CLI entry point: upsert ran through get_database_runtime()")

    # ── A WORKER PATH ─────────────────────────────────────────────────────────
    #
    # `messaging.worker.run_once` never imports `dotmac_kernel.db` — it
    # RECEIVES session factories. Handed the product runtime's own factories,
    # it reaches a real `execute()` on the product engine; the only failure is
    # the Postgres-only `claim_outbox_batch` function, which is a SQL-dialect
    # gap, not an import or configuration one.
    from dotmac_kernel.messaging.worker import LoggingTransport, run_once
    from sqlalchemy.exc import OperationalError

    dispatcher_db = get_database_runtime().platform_session_factory()
    try:
        run_once(
            dispatcher_db=dispatcher_db,
            tenant_session_factory=get_database_runtime().session_factory,
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

    `get_database_runtime()`'s fallback is correct for the reference
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
    never installed, so `get_database_runtime()` falls back to the reference
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
    from dotmac_kernel.session_runtime import DatabaseRuntime, get_database_runtime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # The PRODUCT's own runtime — built and seeded exactly like `main()` —
    # but deliberately NEVER installed via `install_database_runtime` or
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
    fallback = get_database_runtime()
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
            "get_database_runtime() and proves nothing about the seam"
        )
    print(
        "PASS fallback sensitivity: the authenticated-request stage genuinely "
        "depends on the installed runtime — with none installed, the "
        f"identical session is correctly refused ({logout.status_code})"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "strict":
        main_strict_refusal()
    elif len(sys.argv) > 1 and sys.argv[1] == "fallback":
        main_fallback_sensitivity()
    else:
        main()
