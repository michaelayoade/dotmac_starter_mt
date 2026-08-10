"""`TENANCY=single` refuses to boot unless exactly one tenant exists.

This is the primary single-tenancy control. Refusing a wrong *host* only fires
if somebody tries; refusing to *start* with two tenant rows catches the hazard
itself — restored backup, migration rehearsal, a shared database someone meant
to split — at deploy time, whether or not anyone probes for it.

It also produces the binding the per-request gate uses, which is why the
identity is never configured: it is whatever the database actually held.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from dotmac_kernel import app_factory
from dotmac_kernel.tenancy import clear_single_tenant_binding, single_tenant_binding


class _FakeTenant:
    def __init__(self, slug: str) -> None:
        self.slug = slug


def _fake_db(slugs: list[str]):
    class _Query:
        def order_by(self, *_a: object) -> _Query:
            return self

        def all(self) -> list[_FakeTenant]:
            return [_FakeTenant(s) for s in slugs]

    class _Session:
        def query(self, *_a: object) -> _Query:
            return _Query()

    @contextmanager
    def _resolver():
        yield _Session()

    return _resolver


@pytest.fixture(autouse=True)
def _unbound() -> None:
    clear_single_tenant_binding()
    yield
    clear_single_tenant_binding()


def _run(
    monkeypatch: pytest.MonkeyPatch, *, tenancy: str, slugs: list[str]
) -> list[str]:
    monkeypatch.setattr(app_factory.settings, "tenancy", tenancy, raising=False)
    import dotmac_kernel.db as db_module

    monkeypatch.setattr(db_module, "resolver_session", _fake_db(slugs))
    return app_factory._tenancy_errors()


def test_multi_tenant_does_not_check_or_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default costs nothing and binds nothing."""
    assert _run(monkeypatch, tenancy="multi", slugs=["a", "b", "c"]) == []
    assert single_tenant_binding() is None


def test_exactly_one_tenant_passes_and_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, tenancy="single", slugs=["academy"]) == []
    assert single_tenant_binding() == "academy"


def test_two_tenants_is_a_startup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The case the whole control exists for."""
    errors = _run(monkeypatch, tenancy="single", slugs=["academy", "someone-else"])
    assert len(errors) == 1
    assert "2 tenants" in errors[0]
    assert "someone-else" in errors[0], "the message must name what it found"
    assert single_tenant_binding() is None, "a failed check must not bind"


def test_no_tenants_is_a_startup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declared single but empty: the deployment has nothing to serve."""
    errors = _run(monkeypatch, tenancy="single", slugs=[])
    assert len(errors) == 1
    assert "no tenant" in errors[0]
    assert single_tenant_binding() is None


def test_an_unreachable_store_is_not_a_tenancy_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead database says nothing about how many tenants exist.

    Reporting a tenancy failure for a connection error would take production
    down for the wrong reason — the same argument `_required_setting_errors`
    makes for its own store.
    """
    monkeypatch.setattr(app_factory.settings, "tenancy", "single", raising=False)
    import dotmac_kernel.db as db_module

    @contextmanager
    def _boom():
        raise RuntimeError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr(db_module, "resolver_session", _boom)
    assert app_factory._tenancy_errors() == []
    assert single_tenant_binding() is None
