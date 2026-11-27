"""`ModuleManifest.runtime_requires`: a stateless module's call-time dependency.

`requires` orders migrations and stays refused on a module with no lineage.
`runtime_requires` is the other obligation: the module CALLS a kernel facility,
so composition must refuse an assembly that cannot supply it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.app_factory import create_app
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.modules import ModuleManifest, ModuleRegistryError
from dotmac_kernel.prerequisites import (
    IDENTITY_ADMIN_V1,
    TENANT_AUDIT_LOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    DuplicatePrerequisiteError,
    PrerequisiteBinding,
    UnknownPrerequisiteError,
    install_prerequisite_bindings,
)

NAME = IDENTITY_ADMIN_V1.name


@pytest.fixture(autouse=True)
def _no_leaked_bindings() -> Iterator[None]:
    install_prerequisite_bindings(())
    yield
    install_prerequisite_bindings(())


BINDING = PrerequisiteBinding(NAME, "0025_session_provenance", "kernel")


def _spec(
    manifest: ModuleManifest, bindings: tuple[PrerequisiteBinding, ...] = ()
) -> ProductAssemblySpec:
    return ProductAssemblySpec(
        name="runtime-requires",
        modules=(manifest,),
        api_documentation=api_documentation_policy("development"),
        prerequisite_bindings=bindings,
    )


def test_a_stateless_module_may_declare_runtime_requires() -> None:
    manifest = ModuleManifest(
        code="stateless", version="1.0.0", runtime_requires=(NAME,)
    )
    assert manifest.runtime_requires == (NAME,)


def test_runtime_requires_defaults_empty() -> None:
    assert ModuleManifest(code="plain", version="1.0.0").runtime_requires == ()


@pytest.mark.parametrize(
    "stateful",
    (
        {"short_code": "sf", "migration_prefix": "sf", "tables": ("t",)},
        {"short_code": "sf", "migration_prefix": "sf", "platform_tables": ("t",)},
    ),
)
def test_a_stateful_module_may_not_declare_runtime_requires(stateful) -> None:
    with pytest.raises(ModuleRegistryError, match="legal only on a stateless"):
        ModuleManifest(
            code="stateful", version="1.0.0", runtime_requires=(NAME,), **stateful
        )


def test_a_stateless_module_still_cannot_require_a_migration_effect() -> None:
    """The existing refusal is unchanged by the new field."""
    with pytest.raises(ModuleRegistryError, match="owns no lineage"):
        ModuleManifest(
            code="stateless",
            version="1.0.0",
            requires=(TENANT_SCOPE_CATALOG_V1.name,),
            runtime_requires=(NAME,),
        )


def test_runtime_requires_names_a_registered_effect_once() -> None:
    with pytest.raises(UnknownPrerequisiteError):
        ModuleManifest(code="x", version="1.0.0", runtime_requires=("nope.v1",))
    with pytest.raises(DuplicatePrerequisiteError, match="declared twice"):
        ModuleManifest(code="x", version="1.0.0", runtime_requires=(NAME, NAME))


def test_the_overlap_check_covers_runtime_requires() -> None:
    with pytest.raises(ModuleRegistryError, match="more than one prerequisite list"):
        ModuleManifest(
            code="x",
            version="1.0.0",
            short_code=None,
            tenant_requires=(NAME,),
            runtime_requires=(NAME,),
        )


def test_create_app_refuses_an_assembly_that_cannot_supply_the_effect() -> None:
    manifest = ModuleManifest(
        code="needs-identity", version="1.0.0", runtime_requires=(NAME,)
    )
    with pytest.raises(ModuleRegistryError, match="needs-identity"):
        create_app(_spec(manifest))


def test_create_app_refuses_when_only_other_effects_are_bound() -> None:
    other = PrerequisiteBinding(
        TENANT_AUDIT_LOG_V1.name, "0029_audit_history_shape", "kernel"
    )
    manifest = ModuleManifest(
        code="needs-identity", version="1.0.0", runtime_requires=(NAME,)
    )
    with pytest.raises(ModuleRegistryError, match=r"identity_admin\.v1"):
        create_app(_spec(manifest, (other,)))


def test_create_app_boots_when_the_spec_binds_the_effect() -> None:
    """Sensitivity: the refusal is about the binding, not the module. No
    process-wide bindings and no environment variable are involved."""
    manifest = ModuleManifest(
        code="needs-identity", version="1.0.0", runtime_requires=(NAME,)
    )
    assert create_app(_spec(manifest, (BINDING,))) is not None


def test_the_environment_variable_is_not_an_app_runtime_channel(monkeypatch) -> None:
    monkeypatch.setenv(
        "DOTMAC_MIGRATION_BINDINGS",
        "app.migration_bindings:ASSEMBLY_PREREQUISITE_BINDINGS",
    )
    manifest = ModuleManifest(
        code="needs-identity", version="1.0.0", runtime_requires=(NAME,)
    )
    with pytest.raises(ModuleRegistryError, match="needs-identity"):
        create_app(_spec(manifest))


def test_process_wide_bindings_that_differ_from_the_spec_refuse_boot() -> None:
    manifest = ModuleManifest(
        code="needs-identity", version="1.0.0", runtime_requires=(NAME,)
    )
    install_prerequisite_bindings(
        [PrerequisiteBinding(NAME, "0022_party_role_grants", "kernel")]
    )
    with pytest.raises(ModuleRegistryError, match="differ"):
        create_app(_spec(manifest, (BINDING,)))
    install_prerequisite_bindings([BINDING])
    assert create_app(_spec(manifest, (BINDING,))) is not None


def test_a_disabled_module_is_not_asked_to_be_supplied() -> None:
    manifest = ModuleManifest(
        code="needs-identity", version="1.0.0", runtime_requires=(NAME,)
    )
    spec = ProductAssemblySpec(
        name="runtime-requires",
        modules=(manifest,),
        disabled_modules=("needs-identity",),
        api_documentation=api_documentation_policy("development"),
    )
    assert create_app(spec) is not None
