"""Manifest-owned outbox routing vocabulary."""

from __future__ import annotations

import pytest
from dotmac_kernel import ProductAssemblySpec, create_app
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.outbox_event_types import (
    DuplicateOutboxEventTypeError,
    OutboxEventTypeRegistry,
    OutboxEventTypesNotInstalledError,
    UndeclaredOutboxEventTypeError,
    active_outbox_event_types,
)

#: Test assemblies declare the development policy explicitly: the kernel
#: refuses to build without one, and a fallback would be the inherited
#: exposure `api_documentation` exists to end.
_DOCS_POLICY = api_documentation_policy("development")


def _manifest(code: str, *event_types: str) -> ModuleManifest:
    return ModuleManifest(
        code=code,
        version="1.0.0",
        outbox_event_types=event_types,
    )


def test_registry_names_one_owner_and_requires_declared_routing_codes() -> None:
    registry = OutboxEventTypeRegistry.from_manifests(
        [_manifest("billing", "billing.invoice_due")]
    )

    registry.require("billing.invoice_due")
    assert registry.owner("billing.invoice_due") == "billing"
    assert registry.event_types() == frozenset({"billing.invoice_due"})

    with pytest.raises(UndeclaredOutboxEventTypeError, match="not declared"):
        registry.require("billing.invoice_du")


def test_duplicate_routing_code_is_not_a_shared_declaration() -> None:
    with pytest.raises(DuplicateOutboxEventTypeError, match="both 'billing' and 'crm'"):
        OutboxEventTypeRegistry.from_manifests(
            [
                _manifest("billing", "billing.invoice_due"),
                _manifest("crm", "billing.invoice_due"),
            ]
        )


def test_legacy_feature_manifest_without_the_new_field_contributes_nothing() -> None:
    class LegacyManifest:
        name = "legacy"

    registry = OutboxEventTypeRegistry.from_manifests([LegacyManifest()])  # type: ignore[list-item]
    assert registry.event_types() == frozenset()


def test_create_app_installs_the_registry_from_the_installed_module_set() -> None:
    create_app(
        ProductAssemblySpec(
            api_documentation=_DOCS_POLICY,
            name="event-types",
            modules=[_manifest("billing", "billing.invoice_due")],
        )
    )
    assert active_outbox_event_types().owner("billing.invoice_due") == "billing"


def test_not_installed_is_distinct_from_installed_and_empty(monkeypatch) -> None:
    import dotmac_kernel.outbox_event_types as module

    monkeypatch.setattr(module, "_active_registry", None)
    with pytest.raises(OutboxEventTypesNotInstalledError):
        module.active_outbox_event_types()

    monkeypatch.setattr(module, "_active_registry", OutboxEventTypeRegistry(()))
    with pytest.raises(UndeclaredOutboxEventTypeError):
        module.active_outbox_event_types().require("billing.invoice_due")
