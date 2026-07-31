"""Consumer tests for the WS1 deployment-profile registry (`dotmac_kernel.profiles`).

A profile DESCRIBES a versioned desired composition + constraints; it is not a
fleet deployment. These pin the public contract: stable `(code, version)` id,
unique codes, and a deterministic fail-closed `validate(...)`.
"""

from __future__ import annotations

import pytest
from dotmac_kernel import (
    DeploymentProfileRegistry,
    DeploymentProfileSpec,
    DuplicateProfileError,
    UnknownProfileError,
)

_PROVIDERS = {
    "signed_license",
    "local_bootstrap",
    "local",
    "disabled",
    "offline_bundle",
    "nginx_static",
    "manual_txt",
    "customer_pki",
}


def _profile(
    code: str = "on_prem", version: str = "1.0.0", **over: object
) -> DeploymentProfileSpec:
    base: dict[str, object] = {
        "code": code,
        "version": version,
        "required_modules": frozenset({"auth", "parties"}),
        "forbidden_modules": frozenset({"billing"}),
        "commercial_provider": "signed_license",
        "provisioning_provider": "local_bootstrap",
        "identity_provider": "local",
        "telemetry_provider": "disabled",
        "update_provider": "offline_bundle",
        "ingress_provider": "nginx_static",
        "dns_verification_provider": "manual_txt",
        "tls_provider": "customer_pki",
        "default_locale": "en",
        "supported_locales": frozenset({"en"}),
        "allowed_currencies": frozenset({"USD"}),
        "legal_authority": "customer",
        "data_residency": "customer_site",
    }
    base.update(over)
    return DeploymentProfileSpec(**base)  # type: ignore[arg-type]


def _registry() -> DeploymentProfileRegistry:
    return DeploymentProfileRegistry([_profile()])


def test_stable_identifier_and_provider_selections() -> None:
    p = _profile()
    assert (p.code, p.version) == ("on_prem", "1.0.0")
    assert p.provider_selections()["provisioning"] == "local_bootstrap"
    assert set(p.provider_selections()) == {
        "commercial",
        "provisioning",
        "identity",
        "telemetry",
        "update",
        "ingress",
        "dns_verification",
        "tls",
    }


def test_unique_code_enforced() -> None:
    with pytest.raises(DuplicateProfileError):
        DeploymentProfileRegistry([_profile(), _profile(version="2.0.0")])


def test_is_valid_code_and_unknown_lookup() -> None:
    reg = _registry()
    assert reg.is_valid_code("on_prem")
    assert not reg.is_valid_code("saas")
    with pytest.raises(UnknownProfileError):
        reg.get("saas")


def test_validate_ok_when_composition_satisfied() -> None:
    report = _registry().validate(
        "on_prem",
        installed_modules={"auth", "parties", "rbac"},
        enabled_modules={"auth", "parties", "rbac"},
        available_providers=_PROVIDERS,
    )
    assert report.ok
    assert report.errors == ()
    assert report.profile_version == "1.0.0"
    assert "on_prem" in report.render() and "v1.0.0" in report.render()


def test_validate_fails_closed_and_is_deterministic() -> None:
    report = _registry().validate(
        "on_prem",
        installed_modules={"parties", "billing"},  # billing is forbidden & present
        enabled_modules={"parties"},  # auth required but not enabled
        available_providers=_PROVIDERS - {"local_bootstrap"},  # provisioning gone
    )
    assert not report.ok
    # All three failure classes reported, order deterministic (sorted).
    assert report.errors == (
        "forbidden module 'billing' is installed",
        "provider for 'provisioning' ('local_bootstrap') is not available",
        "required module 'auth' is not enabled",
    )
    # Re-running yields identical output (determinism).
    again = _registry().validate(
        "on_prem",
        installed_modules={"parties", "billing"},
        enabled_modules={"parties"},
        available_providers=_PROVIDERS - {"local_bootstrap"},
    )
    assert again.errors == report.errors
