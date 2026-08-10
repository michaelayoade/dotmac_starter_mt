"""A deployment can declare that it serves exactly one tenant, and be held to it.

ADR-0003 makes "dedicated one-tenant deployment per ISP" the safe default, but
nothing enforced it. A deployment that acquired rows for a second tenant — a
restored backup, a migration rehearsal, a shared database someone meant to split
— would serve them to anyone who knew the host. There was no error state,
because from the resolver's point of view the host resolved perfectly well.

`dotmac_academy_app` had implemented this privately, which is the only reason it
was noticed. Per ADR-0013 it is a deployment fact, so the deployment declares it
and the kernel enforces it.

The default is empty, meaning multi-tenant. That keeps every existing assembly
on exactly its current behaviour.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.middleware import tenant as tenant_module


class _FakeTenant:
    def __init__(self, slug: str) -> None:
        self.slug = slug


def _middleware(
    monkeypatch: pytest.MonkeyPatch, *, locked_to: str
) -> tenant_module.TenantResolverMiddleware:
    monkeypatch.setattr(tenant_module.settings, "single_tenant_slug", locked_to)
    return tenant_module.TenantResolverMiddleware(app=lambda *a, **k: None)


def test_unset_means_multi_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default must not change anyone's behaviour."""
    mw = _middleware(monkeypatch, locked_to="")
    tenant = _FakeTenant("anything")
    assert mw._allow(tenant) is tenant


def test_the_declared_tenant_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    mw = _middleware(monkeypatch, locked_to="academy")
    tenant = _FakeTenant("academy")
    assert mw._allow(tenant) is tenant


def test_another_tenant_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: a host resolving to the wrong tenant serves nothing."""
    mw = _middleware(monkeypatch, locked_to="academy")
    assert mw._allow(_FakeTenant("some-other-isp")) is None


def test_refusal_is_not_substitution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returning None (=> 404) rather than the declared tenant is deliberate.

    Quietly serving the right tenant for a wrong host would hide the
    misconfiguration that produced the wrong host.
    """
    mw = _middleware(monkeypatch, locked_to="academy")
    result = mw._allow(_FakeTenant("some-other-isp"))
    assert result is None
    assert not isinstance(result, _FakeTenant)


def test_no_tenant_stays_no_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Platform paths resolve to None legitimately; the gate must not invent one."""
    mw = _middleware(monkeypatch, locked_to="academy")
    assert mw._allow(None) is None


@pytest.mark.parametrize(
    "declared,resolved", [("Academy", "academy"), ("academy", "ACADEMY")]
)
def test_comparison_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, declared: str, resolved: str
) -> None:
    """Hosts are case-insensitive, so a lockdown keyed off one must be too —
    otherwise the control fails open on a capitalised setting."""
    mw = _middleware(monkeypatch, locked_to=declared)
    tenant = _FakeTenant(resolved)
    assert mw._allow(tenant) is tenant
