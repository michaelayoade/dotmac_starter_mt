"""An assembly selects module planes explicitly; bindings only name providers.

These are the load-bearing ADR-0028 canaries.  Vendor CP physically composes the
kernel tenant catalogue while semantically installing only platform approvals,
so provider availability must never be used as the plane selector.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.modules import ModuleManifest, ModuleRegistryError
from dotmac_kernel.namespaces import APPROVALS_MIGRATION_OWNER, NamespaceRegistry
from dotmac_kernel.planes import (
    MODULE_PLANES_ENV_VAR,
    ModulePlane,
    ModulePlaneSelection,
    ModulePlaneSelectionError,
    install_module_plane_selections,
    selected_module_planes,
)
from dotmac_kernel.prerequisites import (
    PrerequisiteBinding,
    UnboundPrerequisiteError,
    install_prerequisite_bindings,
    resolve_depends_on,
)

ROLES = "module_database_roles.v1"
TENANT = "tenant_scope_catalog.v1"
MODULE = "approvals"
SUPPORTED = (
    (ModulePlane.TENANT,),
    (ModulePlane.PLATFORM,),
    (ModulePlane.TENANT, ModulePlane.PLATFORM),
)
GRAPH_SELECTIONS = (
    ModulePlaneSelection(module=MODULE, planes=(ModulePlane.PLATFORM,)),
)


@pytest.fixture(autouse=True)
def _no_leaked_composition() -> Iterator[None]:
    install_prerequisite_bindings(())
    install_module_plane_selections(())
    yield
    install_prerequisite_bindings(())
    install_module_plane_selections(())


def _binding(name: str, revision: str) -> PrerequisiteBinding:
    return PrerequisiteBinding(
        prerequisite=name,
        provider_revision=revision,
        provider_owner="kernel",
    )


def _selection(*planes: ModulePlane, module: str = MODULE) -> ModulePlaneSelection:
    return ModulePlaneSelection(module=module, planes=planes)


def _manifest(**overrides: object) -> ModuleManifest:
    defaults: dict[str, object] = {
        "code": MODULE,
        "version": "0.1.0a3",
        "core": False,
        "short_code": MODULE,
        "migration_prefix": "ap",
        "migration_branch": MODULE,
        "tables": ("approval_requests",),
        "platform_tables": ("platform_approval_requests",),
        "requires": (ROLES,),
        "tenant_requires": (TENANT,),
        "supported_plane_sets": SUPPORTED,
    }
    defaults.update(overrides)
    return ModuleManifest(**defaults)  # type: ignore[arg-type]


def test_provider_availability_does_not_select_the_tenant_plane() -> None:
    """The Vendor case: kernel 0001 supplies BOTH effects, but the assembly's
    explicit platform-only selection must omit the tenant edge."""
    install_prerequisite_bindings(
        [_binding(ROLES, "0001_initial"), _binding(TENANT, "0001_initial")]
    )
    install_module_plane_selections([_selection(ModulePlane.PLATFORM)])

    assert resolve_depends_on((ROLES,), module=MODULE, tenant=(TENANT,)) == (
        "0001_initial",
    )


def test_graph_commands_can_autoload_the_explicit_plane_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph inspection skips ``env.py`` but still sees installation intent.

    The environment carries a module/attribute pointer, never a copied or
    stringly encoded selection, so graph and upgrade entrypoints read the same
    typed assembly declaration.
    """
    monkeypatch.setenv(
        MODULE_PLANES_ENV_VAR,
        f"{__name__}:GRAPH_SELECTIONS",
    )
    assert selected_module_planes(MODULE) == {ModulePlane.PLATFORM}


def test_a_selected_plane_still_fails_closed_on_an_unbound_requirement() -> None:
    install_prerequisite_bindings([_binding(ROLES, "0001_initial")])
    install_module_plane_selections([_selection(ModulePlane.TENANT)])

    with pytest.raises(UnboundPrerequisiteError, match=TENANT):
        resolve_depends_on((ROLES,), module=MODULE, tenant=(TENANT,))


def test_a_composed_selectable_module_cannot_omit_its_selection() -> None:
    with pytest.raises(
        ModulePlaneSelectionError, match="approvals.*no plane selection"
    ):
        ProductAssemblySpec(name="vendor", modules=[_manifest()])


def test_an_explicit_supported_selection_is_accepted() -> None:
    selection = _selection(ModulePlane.PLATFORM)
    spec = ProductAssemblySpec(
        name="vendor", modules=[_manifest()], module_planes=[selection]
    )
    assert spec.module_planes == (selection,)


def test_an_unknown_module_selection_is_refused() -> None:
    with pytest.raises(ModulePlaneSelectionError, match="unknown module"):
        ProductAssemblySpec(
            name="vendor",
            modules=[_manifest()],
            module_planes=[_selection(ModulePlane.PLATFORM, module="ghost")],
        )


def test_an_unsupported_selection_is_refused() -> None:
    manifest = _manifest(
        supported_plane_sets=(
            (ModulePlane.PLATFORM,),
            (ModulePlane.TENANT, ModulePlane.PLATFORM),
        )
    )
    with pytest.raises(ModulePlaneSelectionError, match="does not support"):
        ProductAssemblySpec(
            name="vendor",
            modules=[manifest],
            module_planes=[_selection(ModulePlane.TENANT)],
        )


def test_an_atomic_dual_plane_module_needs_no_selector() -> None:
    manifest = _manifest(supported_plane_sets=())
    spec = ProductAssemblySpec(name="starter", modules=[manifest])
    assert spec.module_planes == ()


def test_plane_specific_requirements_need_the_matching_declared_plane() -> None:
    with pytest.raises(ModuleRegistryError, match="tenant_requires.*tenant tables"):
        _manifest(tables=())
    with pytest.raises(ModuleRegistryError, match="platform_requires.*platform tables"):
        _manifest(platform_tables=(), tenant_requires=(), platform_requires=(TENANT,))


def test_requirements_cannot_appear_in_two_contract_lists() -> None:
    with pytest.raises(ModuleRegistryError, match="more than one prerequisite list"):
        _manifest(requires=(ROLES, TENANT), tenant_requires=(TENANT,))


def test_expected_tables_follow_selection_not_available_bindings() -> None:
    """A live gate must expect only the selected plane even when the database
    contains a truthful provider for the unselected plane."""
    selection = _selection(ModulePlane.PLATFORM)
    install_prerequisite_bindings(
        [_binding(ROLES, "0001_initial"), _binding(TENANT, "0001_initial")]
    )
    registry = NamespaceRegistry.from_manifests(
        [_manifest()], module_planes=[selection]
    )
    schema = APPROVALS_MIGRATION_OWNER.db_schema
    assert schema is not None

    assert registry.expected_tables(schema) == {"platform_approval_requests"}
    assert registry.declared_tables(schema) == {
        "approval_requests",
        "platform_approval_requests",
    }


def test_atomic_modules_still_expect_every_declared_table() -> None:
    registry = NamespaceRegistry.from_manifests(
        [_manifest(supported_plane_sets=())], module_planes=[]
    )
    schema = APPROVALS_MIGRATION_OWNER.db_schema
    assert schema is not None
    assert registry.expected_tables(schema) == registry.declared_tables(schema)
