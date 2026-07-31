"""Consumer tests for the WS1 capability catalogue (`dotmac_kernel.capabilities`).

The catalogue DESCRIBES which capability codes exist (declared by manifests); it
never grants entitlement. These pin the public contract: stable string codes,
one owning module per code, fail-closed on duplicates, and rejection of
undeclared references.
"""

from __future__ import annotations

import pytest
from dotmac_kernel import (
    CapabilityCatalogue,
    DuplicateCapabilityError,
    FeatureManifest,
    UndeclaredCapabilityError,
)


def _m(name: str, *capabilities: str) -> FeatureManifest:
    return FeatureManifest(name=name, capabilities=capabilities)


def test_manifest_capabilities_defaults_to_empty() -> None:
    assert FeatureManifest(name="x").capabilities == ()


def test_catalogue_declares_and_owns_codes() -> None:
    cat = CapabilityCatalogue.from_manifests(
        [_m("inventory", "inventory.use"), _m("billing", "billing.use")]
    )
    assert cat.is_declared("inventory.use")
    assert cat.owner("inventory.use") == "inventory"
    assert cat.codes() == {"inventory.use", "billing.use"}
    assert not cat.is_declared("nope.use")
    assert cat.owner("nope.use") is None


def test_require_rejects_an_undeclared_code() -> None:
    cat = CapabilityCatalogue.from_manifests([_m("inventory", "inventory.use")])
    cat.require("inventory.use")  # declared → no raise
    with pytest.raises(UndeclaredCapabilityError):
        cat.require("inventory.export")  # a reference that invents a new code


def test_duplicate_code_across_modules_fails_closed() -> None:
    with pytest.raises(DuplicateCapabilityError):
        CapabilityCatalogue.from_manifests(
            [_m("a", "shared.use"), _m("b", "shared.use")]
        )


def test_same_module_repeating_its_own_code_is_ok() -> None:
    # A manifest listing a code twice is a harmless duplicate within one owner.
    cat = CapabilityCatalogue.from_manifests([_m("a", "a.use", "a.use")])
    assert cat.codes() == {"a.use"}
    assert cat.owner("a.use") == "a"
