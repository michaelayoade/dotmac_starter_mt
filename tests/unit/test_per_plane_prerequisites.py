"""A plane declares its own prerequisites (ADR-0027).

The property under test is narrow and load-bearing: a dual-plane module must be
installable in an assembly that can operate only ONE of its planes, without
weakening anything about the plane that does get built.

Every test here is in-memory. The live half — that a platform-only install
actually produces a schema with only platform tables, and that the gate accepts
it — is `tests/test_approvals_isolation.py`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dotmac_kernel.modules import ModuleManifest, ModuleRegistryError
from dotmac_kernel.namespaces import APPROVALS_MIGRATION_OWNER, NamespaceRegistry
from dotmac_kernel.prerequisites import (
    DuplicatePrerequisiteError,
    PrerequisiteBinding,
    UnknownPrerequisiteError,
    all_bound,
    install_prerequisite_bindings,
    is_bound,
    resolve_depends_on,
)

ROLES = "module_database_roles.v1"
TENANT = "tenant_scope_catalog.v1"


@pytest.fixture(autouse=True)
def _no_leaked_bindings() -> Iterator[None]:
    """Bindings are process-global; a leak would make these tests order-dependent."""
    install_prerequisite_bindings(())
    yield
    install_prerequisite_bindings(())


def _binding(name: str, revision: str, owner: str) -> PrerequisiteBinding:
    return PrerequisiteBinding(
        prerequisite=name, provider_revision=revision, provider_owner=owner
    )


def _manifest(**overrides: object) -> ModuleManifest:
    defaults: dict[str, object] = {
        "code": "approvals",
        "version": "0.1.0a2",
        "core": False,
        "short_code": "approvals",
        "migration_prefix": "ap",
        "migration_branch": "approvals",
        "tables": ("approval_requests",),
        "platform_tables": ("platform_approval_requests",),
        "requires": (ROLES,),
        "tenant_requires": (TENANT,),
    }
    defaults.update(overrides)
    return ModuleManifest(**defaults)  # type: ignore[arg-type]


# ── The binding is the switch ───────────────────────────────────────────────


def test_an_unbound_prerequisite_answers_false_rather_than_raising() -> None:
    """That is the answer, not an error — it is how a platform-only assembly
    says "I have no tenant catalogue"."""
    install_prerequisite_bindings([_binding(ROLES, "0001_initial", "kernel")])
    assert is_bound(ROLES)
    assert not is_bound(TENANT)


def test_an_unregistered_name_still_raises() -> None:
    """Returning False for a typo would silently skip a plane."""
    install_prerequisite_bindings([_binding(ROLES, "0001_initial", "kernel")])
    with pytest.raises(UnknownPrerequisiteError):
        is_bound("tenant_scope_catalogue.v1")  # British spelling: a real typo


def test_all_bound_is_true_for_an_empty_list() -> None:
    """A plane with no prerequisites of its own is always installable."""
    assert all_bound(())


# ── Ordering survives optionality ───────────────────────────────────────────


def test_a_bound_optional_prerequisite_still_orders() -> None:
    """Where the tenant plane IS built it must run after the catalogue."""
    install_prerequisite_bindings(
        [
            _binding(ROLES, "0001_initial", "kernel"),
            _binding(TENANT, "0002_tenants", "kernel"),
        ]
    )
    assert resolve_depends_on((ROLES,), optional=(TENANT,)) == (
        "0001_initial",
        "0002_tenants",
    )


def test_an_unbound_optional_prerequisite_contributes_no_edge() -> None:
    install_prerequisite_bindings([_binding(ROLES, "0001_initial", "kernel")])
    assert resolve_depends_on((ROLES,), optional=(TENANT,)) == ("0001_initial",)


def test_a_required_prerequisite_still_fails_closed_when_unbound() -> None:
    """`optional` must not soften anything in `names`."""
    install_prerequisite_bindings([_binding(TENANT, "0002_tenants", "kernel")])
    with pytest.raises(Exception, match="binds no provider"):
        resolve_depends_on((ROLES,), optional=(TENANT,))


def test_a_name_cannot_be_both_required_and_optional() -> None:
    """It would read as mandatory and behave as optional."""
    install_prerequisite_bindings([_binding(ROLES, "0001_initial", "kernel")])
    with pytest.raises(DuplicatePrerequisiteError, match="one or the other"):
        resolve_depends_on((ROLES,), optional=(ROLES,))


# ── The manifest refuses incoherent declarations ────────────────────────────


def test_a_manifest_may_not_declare_one_name_in_both_lists() -> None:
    with pytest.raises(ModuleRegistryError, match="one or the other"):
        _manifest(requires=(ROLES, TENANT), tenant_requires=(TENANT,))


def test_tenant_requires_needs_a_tenant_plane_to_condition() -> None:
    with pytest.raises(ModuleRegistryError, match="owns no tenant tables"):
        _manifest(tables=(), tenant_requires=(TENANT,))


def test_an_ordinary_tenant_only_module_declares_no_tenant_requires() -> None:
    """The new field is for dual-plane modules. A plane that is always built is
    not conditional, and its needs are simply `requires`."""
    manifest = _manifest(
        platform_tables=(), requires=(ROLES, TENANT), tenant_requires=()
    )
    assert manifest.tenant_requires == ()


# ── What the live gate will expect ──────────────────────────────────────────


def test_the_tenant_plane_is_expected_only_where_its_prerequisite_is_bound() -> None:
    """`expected_tables` is what a platform-only install is audited against."""
    registry = NamespaceRegistry.from_manifests([_manifest()])
    schema = APPROVALS_MIGRATION_OWNER.db_schema
    assert schema is not None

    install_prerequisite_bindings([_binding(ROLES, "0001_initial", "kernel")])
    assert not registry.tenant_plane_installed(schema)
    assert registry.expected_tables(schema) == {"platform_approval_requests"}

    install_prerequisite_bindings(
        [
            _binding(ROLES, "0001_initial", "kernel"),
            _binding(TENANT, "0002_tenants", "kernel"),
        ]
    )
    assert registry.tenant_plane_installed(schema)
    assert registry.expected_tables(schema) == {
        "approval_requests",
        "platform_approval_requests",
    }


def test_declared_tables_never_varies_by_assembly() -> None:
    """Ownership is not per-assembly; only what got BUILT is.

    Keeping the two readers separate is what stops "expected" eroding into
    "whatever we found" — the ownership claim the static gate checks has to stay
    the full one.
    """
    registry = NamespaceRegistry.from_manifests([_manifest()])
    schema = APPROVALS_MIGRATION_OWNER.db_schema
    assert schema is not None

    install_prerequisite_bindings([_binding(ROLES, "0001_initial", "kernel")])
    declared_platform_only = registry.declared_tables(schema)
    install_prerequisite_bindings(
        [
            _binding(ROLES, "0001_initial", "kernel"),
            _binding(TENANT, "0002_tenants", "kernel"),
        ]
    )
    assert registry.declared_tables(schema) == declared_platform_only
    assert declared_platform_only == {
        "approval_requests",
        "platform_approval_requests",
    }


def test_a_module_with_no_tenant_requires_expects_everything_it_declares() -> None:
    """The default has to be "everything declared exists", or a genuinely
    missing table would stop being reported the moment this mechanism existed."""
    registry = NamespaceRegistry.from_manifests([_manifest(tenant_requires=())])
    schema = APPROVALS_MIGRATION_OWNER.db_schema
    assert schema is not None
    install_prerequisite_bindings(())
    assert registry.tenant_plane_installed(schema)
    assert registry.expected_tables(schema) == registry.declared_tables(schema)
