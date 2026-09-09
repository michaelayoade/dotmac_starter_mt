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
* **An authenticated request** — `POST /platform/auth/login` then
  `POST /platform/auth/logout` against `platform_auth_router` mounted with no
  other machinery: `login`/`require_platform_admin` resolve their session
  through `dotmac_kernel.deps.get_platform_db`, which resolves the runtime
  through `get_database_runtime()`.
* **A CLI-shaped entry point** — a function in the shape of
  `scripts/create_platform_admin.py` (build one, from a script that owns no
  request), but reached through `get_database_runtime().platform_session()`
  instead of building its own engine.
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
"""

from __future__ import annotations

import sys

# THE FIRST THING THIS PROCESS DOES, before any `dotmac_kernel` import: make
# `dotmac_kernel.db` unimportable. `sys.modules[name] = None` is Python's own
# mechanism for this (import machinery raises `ImportError: import of {name}
# halted; None in sys.modules` for any later `import dotmac_kernel.db` or
# `from dotmac_kernel.db import ...`, anywhere in the process).
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
    from dotmac_kernel.models import Base
    from dotmac_kernel.models_platform import PlatformAdmin
    from dotmac_kernel.security import hash_password
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

    seeded_email = "probe-admin@platform.example.test"
    seeded_password = "probe-password-not-a-secret"  # noqa: S105  # nosec B105 -- fixture
    with runtime.platform_session() as seed_db:
        seed_db.add(
            PlatformAdmin(
                email=seeded_email,
                password_hash=hash_password(seeded_password),
                is_active=True,
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
        login = platform_client.post(
            "/platform/auth/login",
            json={"email": seeded_email, "password": seeded_password},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]

        logout = platform_client.post(
            "/platform/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logout.status_code == 204, logout.text
    print(
        "PASS authenticated request: platform login+logout resolved "
        "deps.get_platform_db through the product runtime"
    )

    # ── A CLI-SHAPED ENTRY POINT ──────────────────────────────────────────────
    #
    # Same job as `scripts/create_platform_admin.py` (bootstrap/rotate a
    # platform admin from a non-request caller), reached through
    # `get_database_runtime()` instead of that script's own `create_engine`.
    def cli_upsert_platform_admin(email: str, password: str) -> None:
        with get_database_runtime().platform_session() as db:
            from sqlalchemy import func, select

            admin = db.scalars(
                select(PlatformAdmin).where(
                    func.lower(PlatformAdmin.email) == email.lower()
                )
            ).first()
            if admin is None:
                db.add(
                    PlatformAdmin(email=email, password_hash=hash_password(password))
                )
            else:
                admin.password_hash = hash_password(password)

    cli_upsert_platform_admin("probe-cli-admin@platform.example.test", "another-pw")
    with runtime.platform_session() as verify_db:
        from sqlalchemy import func, select

        found = verify_db.scalars(
            select(PlatformAdmin).where(
                func.lower(PlatformAdmin.email)
                == "probe-cli-admin@platform.example.test"
            )
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


if __name__ == "__main__":
    main()
