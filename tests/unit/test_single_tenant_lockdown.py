"""A deployment can declare it serves one tenant, and be held to it.

ADR-0003 makes "dedicated one-tenant deployment per ISP" the safe default, but
nothing enforced it. A deployment that acquired rows for a second tenant — a
restored backup, a migration rehearsal, a shared database someone meant to split
— would serve them to anyone who knew the host, with no error state, because
from the resolver's point of view the host resolved perfectly well.

Two properties here are the design, not incidental:

* The deployment declares the **mode**, never which tenant. The identity is
  discovered from the database at startup, so configuration cannot drift from
  it and a typo cannot take the deployment down.
* The **primary** control is the startup assertion (`_tenancy_errors`), covered
  in `test_tenancy_startup_check.py`. The per-request gate below is the second
  half only: it covers a tenant created *after* startup, which no assertion
  would see until the next restart.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.middleware import tenant as tenant_module
from dotmac_kernel.tenancy import bind_single_tenant, clear_single_tenant_binding


class _FakeTenant:
    def __init__(self, slug: str) -> None:
        self.slug = slug


@pytest.fixture(autouse=True)
def _unbound() -> None:
    """Every test starts unbound, and leaves nothing behind for the next one."""
    clear_single_tenant_binding()
    yield
    clear_single_tenant_binding()


def _middleware() -> tenant_module.TenantResolverMiddleware:
    return tenant_module.TenantResolverMiddleware(app=lambda *a, **k: None)


def test_unbound_means_multi_tenant() -> None:
    """The default must not change anyone's behaviour."""
    tenant = _FakeTenant("anything")
    assert _middleware()._allow(tenant) is tenant


def test_the_bound_tenant_is_allowed() -> None:
    bind_single_tenant("academy")
    tenant = _FakeTenant("academy")
    assert _middleware()._allow(tenant) is tenant


def test_another_tenant_is_refused() -> None:
    """The point: a host resolving to a tenant created after startup serves nothing."""
    bind_single_tenant("academy")
    assert _middleware()._allow(_FakeTenant("some-other-isp")) is None


def test_refusal_is_not_substitution() -> None:
    """Returning None (=> 404) rather than the bound tenant is deliberate.

    Quietly serving the right tenant for a wrong host would hide the
    misconfiguration that produced the wrong host.
    """
    bind_single_tenant("academy")
    assert _middleware()._allow(_FakeTenant("some-other-isp")) is None


def test_no_tenant_stays_no_tenant() -> None:
    """Platform paths resolve to None legitimately; the gate must not invent one."""
    bind_single_tenant("academy")
    assert _middleware()._allow(None) is None


@pytest.mark.parametrize(
    "bound,resolved", [("Academy", "academy"), ("academy", "ACADEMY")]
)
def test_comparison_is_case_insensitive(bound: str, resolved: str) -> None:
    """Hosts are case-insensitive, so a binding compared against one must be too,
    or the control fails open on a capitalised slug."""
    bind_single_tenant(bound)
    tenant = _FakeTenant(resolved)
    assert _middleware()._allow(tenant) is tenant


def test_binding_is_not_configured_anywhere() -> None:
    """The mode is declared; the identity is discovered.

    Pinned because the obvious design — naming the slug in config — creates a
    second source of truth for something the database already holds.
    """
    from dotmac_kernel.config import Settings

    assert not hasattr(Settings(), "single_tenant_slug")
    assert Settings().tenancy == "multi"
