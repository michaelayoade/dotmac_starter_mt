"""Feature-seed dispatch on startup.

Composition moved into the kernel in Task 3A: `create_app` builds the lifespan
and the seed dispatch now lives in `dotmac_kernel.app_factory._run_enabled_seeds`
(iterate the spec's manifests, call each ENABLED manifest's optional `seed`
hook, gated by `settings.seed_on_startup`). These tests target that function
directly for the dispatch logic, and a `create_app`-built app for the
end-to-end gating / non-fatal-failure behavior.

`DATABASE_URL` is pinned to a hermetic, unroutable placeholder by the root
`tests/conftest.py` before any `dotmac_kernel`/`app` import, so importing here
never attempts a real connection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

import pytest
from dotmac_kernel import create_app
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.app_factory import _run_enabled_seeds
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.config import settings
from dotmac_kernel.features import FeatureManifest
from fastapi.testclient import TestClient

#: Test assemblies declare the development policy explicitly: the kernel
#: refuses to build without one, and a fallback would be the inherited
#: exposure `api_documentation` exists to end.
_DOCS_POLICY = api_documentation_policy("development")


@pytest.fixture(autouse=True)
def _restore_settings():
    """`settings` is a module-level singleton; don't leak flags across tests."""
    original_seed_on_startup = settings.seed_on_startup
    original_disabled_features = settings.disabled_features
    yield
    settings.seed_on_startup = original_seed_on_startup
    settings.disabled_features = original_disabled_features


def _fake_manifest(name: str, calls: list[str]) -> FeatureManifest:
    return FeatureManifest(name=name, core=False, seed=lambda: calls.append(name))


def test_dispatch_seeds_enabled_feature():
    calls: list[str] = []
    asyncio.run(
        _run_enabled_seeds([_fake_manifest("fake_feature", calls)], frozenset())
    )
    assert calls == ["fake_feature"]


def test_dispatch_skips_manifest_with_no_seed_hook():
    """Most features (parties, rbac, custom_fields, auth, tenants) have no
    `seed` — the loop must skip them, not error on `None()`."""
    asyncio.run(
        _run_enabled_seeds([FeatureManifest(name="no_seed", core=False)], frozenset())
    )  # must not raise


def test_dispatch_skips_disabled_feature():
    """A feature named in the disabled set is skipped even though it has a
    seed hook — disabling `settings` must not run its seed."""
    calls: list[str] = []
    asyncio.run(
        _run_enabled_seeds([_fake_manifest("settings", calls)], frozenset({"settings"}))
    )
    assert calls == []


def _drive_lifespan(app) -> None:
    async def _run() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(_run())


def _app_with(manifest: FeatureManifest):
    return create_app(
        ProductAssemblySpec(
            api_documentation=_DOCS_POLICY,
            name="seed_test",
            modules=[manifest],
            web_enabled=True,
        )
    )


def test_lifespan_seeds_when_flag_true():
    calls: list[str] = []
    settings.seed_on_startup = True
    settings.disabled_features = ""
    _drive_lifespan(_app_with(_fake_manifest("fake_feature", calls)))
    assert calls == ["fake_feature"]


def test_lifespan_does_not_seed_when_flag_false():
    calls: list[str] = []
    settings.seed_on_startup = False
    settings.disabled_features = ""
    _drive_lifespan(_app_with(_fake_manifest("fake_feature", calls)))
    assert calls == []


def test_settings_feature_manifest_carries_the_real_seed_hook():
    from app.features.settings.feature import feature as settings_feature
    from app.features.settings.seed import seed_platform_defaults

    assert settings_feature.seed is seed_platform_defaults


def test_import_app_main_works_with_settings_feature_disabled():
    """Disabling the settings feature via DISABLED_FEATURES: `import app.main`
    must succeed and the disabled feature's seed must never run — exercised in
    a real subprocess (mirrors the final-gate `python -c "import app.main"`)."""
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+psycopg://x:x@127.0.0.1:59999/x",
        "DISABLED_FEATURES": "settings",
        "SEED_ON_STARTUP": "false",
    }
    result = subprocess.run(  # noqa: S603 # nosec B603 — fixed argv, no shell
        [sys.executable, "-c", "import app.main; print('ok')"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_lifespan_seed_failure_is_deferred_non_fatal_and_health_still_serves(caplog):
    """A seed that raises (e.g. the real settings seed against an unreachable
    DATABASE_URL) must be deferred and non-fatal: logged and skipped so
    `/health` still serves. Uses the REAL reference app (`app.main.app`, built
    by `create_app`) against the hermetic unroutable DATABASE_URL."""
    import app.main as main_module

    settings.seed_on_startup = True
    settings.disabled_features = ""

    with caplog.at_level(logging.WARNING):
        with TestClient(main_module.app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert any("seed skipped" in record.getMessage() for record in caplog.records)
