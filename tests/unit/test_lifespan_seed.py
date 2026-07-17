"""Task 4 review fix #1: flag-off coverage for the startup seed guard.

`app.main`'s lifespan calls `seed_platform_defaults()` iff
`settings.seed_on_startup` is True (see app/main.py's lifespan function).
These tests drive the REAL `lifespan` async context manager from `app.main`
— not a reimplementation of the guard — with `app.main.seed_platform_defaults`
monkeypatched to a recorder, so no DB is touched (importing `app.main` is
safe without a reachable DB; see the CI docker-build health check and
`python -c "import app.main"` smoke check, both of which rely on this).

`DATABASE_URL` is pinned to a hermetic, unroutable placeholder by the root
`tests/conftest.py` before any `app.*` import happens, so `import app.main`
here never attempts a real connection — only `seed_platform_defaults()`
would, and that's exactly what's stubbed out.
"""

from __future__ import annotations

import asyncio

import pytest

import app.main as main_module


@pytest.fixture(autouse=True)
def _restore_seed_on_startup():
    """`settings` is a module-level singleton; don't leak the flag across tests."""
    original = main_module.settings.seed_on_startup
    yield
    main_module.settings.seed_on_startup = original


def _drive_lifespan() -> None:
    async def _run() -> None:
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(_run())


def test_lifespan_seeds_when_flag_true(monkeypatch):
    calls: list[None] = []
    monkeypatch.setattr(
        main_module, "seed_platform_defaults", lambda: calls.append(None)
    )
    main_module.settings.seed_on_startup = True

    _drive_lifespan()

    assert calls == [None]


def test_lifespan_does_not_seed_when_flag_false(monkeypatch):
    calls: list[None] = []
    monkeypatch.setattr(
        main_module, "seed_platform_defaults", lambda: calls.append(None)
    )
    main_module.settings.seed_on_startup = False

    _drive_lifespan()

    assert calls == []
