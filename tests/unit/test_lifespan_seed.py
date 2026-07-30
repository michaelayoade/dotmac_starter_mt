"""Task 4 review fix #1 (flag-off seed coverage), extended by final-review
Group 3: `app.main`'s lifespan no longer hard-imports
`app.features.settings.seed.seed_platform_defaults` — it iterates
`dotmac_kernel.features.load_manifests(FEATURE_MODULES)` and calls each ENABLED
manifest's optional `seed` hook (still gated by `settings.seed_on_startup`).
`app.features.settings.feature.feature.seed` is the settings package's own
hook now; deleting or disabling that feature must not crash import or run
its seed.

These tests drive the REAL `lifespan` async context manager from `app.main`
against a monkeypatched `app.main._manifests` list of FAKE manifests (never
the real settings manifest), so no DB is touched — `seed_platform_defaults`
itself is never called here; only the generic dispatch loop is exercised.

`DATABASE_URL` is pinned to a hermetic, unroutable placeholder by the root
`tests/conftest.py` before any `app.*` import happens, so `import app.main`
here never attempts a real connection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

import pytest
from dotmac_kernel.features import FeatureManifest
from fastapi.testclient import TestClient

import app.main as main_module


@pytest.fixture(autouse=True)
def _restore_settings():
    """`settings` is a module-level singleton; don't leak flags across tests."""
    original_seed_on_startup = main_module.settings.seed_on_startup
    original_disabled_features = main_module.settings.disabled_features
    original_manifests = main_module._manifests
    yield
    main_module.settings.seed_on_startup = original_seed_on_startup
    main_module.settings.disabled_features = original_disabled_features
    main_module._manifests = original_manifests


def _drive_lifespan() -> None:
    async def _run() -> None:
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(_run())


def _fake_manifest(name: str, calls: list[str]) -> FeatureManifest:
    return FeatureManifest(name=name, core=False, seed=lambda: calls.append(name))


def test_lifespan_seeds_enabled_feature_when_flag_true():
    calls: list[str] = []
    main_module._manifests = [_fake_manifest("fake_feature", calls)]
    main_module.settings.seed_on_startup = True
    main_module.settings.disabled_features = ""

    _drive_lifespan()

    assert calls == ["fake_feature"]


def test_lifespan_does_not_seed_when_flag_false():
    calls: list[str] = []
    main_module._manifests = [_fake_manifest("fake_feature", calls)]
    main_module.settings.seed_on_startup = False
    main_module.settings.disabled_features = ""

    _drive_lifespan()

    assert calls == []


def test_lifespan_skips_manifests_with_no_seed_hook():
    """Most features (parties, rbac, custom_fields, auth, tenants) have no
    `seed` — the dispatch loop must simply skip them, not error on `None()`."""
    main_module._manifests = [FeatureManifest(name="no_seed_feature", core=False)]
    main_module.settings.seed_on_startup = True
    main_module.settings.disabled_features = ""

    _drive_lifespan()  # must not raise


def test_lifespan_does_not_seed_disabled_feature():
    """Group 3: a feature named in DISABLED_FEATURES is skipped even though
    seed_on_startup is True — disabling `settings` must not run its seed."""
    calls: list[str] = []
    main_module._manifests = [_fake_manifest("settings", calls)]
    main_module.settings.seed_on_startup = True
    main_module.settings.disabled_features = "settings"

    _drive_lifespan()

    assert calls == []


def test_settings_feature_manifest_carries_the_real_seed_hook():
    from app.features.settings.feature import feature as settings_feature
    from app.features.settings.seed import seed_platform_defaults

    assert settings_feature.seed is seed_platform_defaults


def test_import_app_main_works_with_settings_feature_disabled():
    """Deleting the settings package can't be tested in-tree (see
    tests/architecture/test_feature_manifests.py's grep-based assertion that
    app/main.py never hard-imports a feature module directly) — disabling it
    via DISABLED_FEATURES is the in-tree proxy: import must succeed and the
    disabled feature's seed must never run, exercised end-to-end in a real
    subprocess (mirrors the final-gate `python -c "import app.main"` check)."""
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+psycopg://x:x@127.0.0.1:59999/x",
        "DISABLED_FEATURES": "settings",
        "SEED_ON_STARTUP": "false",
    }
    result = subprocess.run(  # noqa: S603 # nosec B603 — fixed argv, no shell, no untrusted input
        [sys.executable, "-c", "import app.main; print('ok')"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_lifespan_seed_failure_is_deferred_non_fatal_and_health_still_serves(caplog):
    """Regression for the CI docker-build health-gate failure (run
    29592275704): with an unreachable DATABASE_URL, the real settings
    manifest's `seed_platform_defaults` raises when it tries to connect —
    previously this propagated out of `lifespan` and killed startup entirely
    ("Application startup failed"), so `/health` never got a chance to
    serve. Seeds are idempotent (`ensure_by_key` never overwrites), so a
    failed seed must be deferred and non-fatal: log a warning and keep
    serving; the next boot retries.

    Uses the REAL manifests (this test does not monkeypatch `_manifests`)
    so it exercises `seed_platform_defaults` against the hermetic,
    unroutable `DATABASE_URL` pinned by `tests/conftest.py` — the exact
    scenario from the failing CI job.
    """
    main_module.settings.seed_on_startup = True
    main_module.settings.disabled_features = ""

    with caplog.at_level(logging.WARNING, logger="app.main"):
        with TestClient(main_module.app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert any("seed skipped" in record.getMessage() for record in caplog.records)
