"""The logical-prerequisite contract: declare an effect, bind it, prove it.

These tests are about the property that makes the indirection safe rather than
merely indirect: **every unanswered question fails closed.** An unregistered
name, an unbound requirement, a malformed version — none of them resolve to
"probably fine", because the failure mode this replaces (a physical
`depends_on` naming a revision that does not exist in this assembly) was itself
silent in exactly one direction.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.modules import ModuleManifest, ModuleRegistryError
from dotmac_kernel.namespaces import MigrationOwner
from dotmac_kernel.prerequisites import (
    BINDINGS_ENV_VAR,
    KERNEL_PREREQUISITES,
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
    DuplicateBindingError,
    DuplicatePrerequisiteError,
    InvalidPrerequisiteNameError,
    InvalidRevisionReferenceError,
    PrerequisiteBinding,
    PrerequisiteError,
    PrerequisiteSpec,
    UnboundPrerequisiteError,
    UnknownPrerequisiteError,
    binding_for,
    binding_map,
    install_prerequisite_bindings,
    prerequisite,
    register_prerequisites,
    registered_prerequisites,
    resolve_depends_on,
    validate_prerequisites,
)

KERNEL_ROOT = "0001_initial_tenant_schema"


@pytest.fixture(autouse=True)
def _no_leaked_bindings():
    """Bindings are process state; a leaked set makes the next test lie."""
    install_prerequisite_bindings(())
    yield
    install_prerequisite_bindings(())


def _bind_kernel() -> None:
    install_prerequisite_bindings(
        [
            PrerequisiteBinding(spec.name, KERNEL_ROOT, "kernel")
            for spec in KERNEL_PREREQUISITES
        ]
    )


# ── Vocabulary ──────────────────────────────────────────────────────────────


def test_the_kernel_ships_exactly_the_two_effects_files_needs() -> None:
    """Not a style assertion: every extra shipped prerequisite is one more
    thing a blocked adopter must supply before it can install anything."""
    assert {spec.name for spec in KERNEL_PREREQUISITES} == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
    }


@pytest.mark.parametrize(
    "name",
    [
        "tenant_scope_catalog",  # no version at all
        "tenant_scope_catalog.v0",  # v0 is not a released contract
        "Tenant_Scope.v1",  # capitals
        "tenant scope.v1",  # space
        "ab.v1",  # too short to be meaningful
        "tenant_scope_catalog.v1.v2",
    ],
)
def test_a_name_without_one_explicit_version_is_refused(name: str) -> None:
    with pytest.raises(InvalidPrerequisiteNameError):
        PrerequisiteSpec(name=name, summary="x")


def test_a_spec_must_say_what_a_provider_has_to_supply() -> None:
    """The summary is what a reviewer reads when judging a proposed binding."""
    with pytest.raises(ValueError, match="observable database effects"):
        PrerequisiteSpec(name="thing_here.v1", summary="   ")


def test_an_unregistered_prerequisite_fails_closed_naming_the_fix() -> None:
    with pytest.raises(UnknownPrerequisiteError, match="not registered"):
        prerequisite("never_registered_effect.v1")


def test_registering_the_same_spec_twice_is_idempotent() -> None:
    """Import order must not decide whether a product's module loads."""
    spec = PrerequisiteSpec(name="product_effect.v1", summary="something real")
    register_prerequisites([spec])
    register_prerequisites([spec])
    assert prerequisite("product_effect.v1") is not None


def test_redefining_a_registered_contract_is_refused() -> None:
    """A changed contract is a new `.vN`. Silently re-pointing an accepted name
    would revalidate every existing binding against a contract nobody accepted."""
    register_prerequisites([PrerequisiteSpec("shifting_effect.v1", "original")])
    with pytest.raises(DuplicatePrerequisiteError, match="new `.vN`"):
        register_prerequisites([PrerequisiteSpec("shifting_effect.v1", "changed")])


def test_registry_is_open_to_products() -> None:
    """ADR-0008: a vocabulary is a declaration registry, never an enum."""
    before = len(registered_prerequisites())
    register_prerequisites([PrerequisiteSpec("erp_only_effect.v1", "ERP supplies it")])
    assert len(registered_prerequisites()) == before + 1


def test_declaring_one_prerequisite_twice_is_refused() -> None:
    """Not de-duplicated: whoever wrote it twice believed they differed."""
    with pytest.raises(DuplicatePrerequisiteError, match="twice"):
        validate_prerequisites(
            (TENANT_SCOPE_CATALOG_V1.name, TENANT_SCOPE_CATALOG_V1.name)
        )


# ── Binding ─────────────────────────────────────────────────────────────────


def test_an_unbound_requirement_fails_closed_pointing_at_the_assembly() -> None:
    """The reader hitting this is composing a module into an assembly that never
    said how it supplies the effect — the fix is assembly-side, and the message
    has to say so."""
    with pytest.raises(UnboundPrerequisiteError, match="install_prerequisite_bindings"):
        binding_for(TENANT_SCOPE_CATALOG_V1.name)


def test_resolve_depends_on_produces_a_real_physical_edge() -> None:
    """The whole point: Alembic still orders on a concrete revision id."""
    _bind_kernel()
    assert resolve_depends_on(
        (TENANT_SCOPE_CATALOG_V1.name, MODULE_DATABASE_ROLES_V1.name)
    ) == (KERNEL_ROOT, KERNEL_ROOT)


def test_resolution_fails_at_script_load_rather_than_ordering_wrongly() -> None:
    install_prerequisite_bindings(
        [PrerequisiteBinding(TENANT_SCOPE_CATALOG_V1.name, KERNEL_ROOT, "kernel")]
    )
    with pytest.raises(UnboundPrerequisiteError):
        resolve_depends_on(
            (TENANT_SCOPE_CATALOG_V1.name, MODULE_DATABASE_ROLES_V1.name)
        )


def test_one_effect_cannot_have_two_providers_in_one_assembly() -> None:
    scope = TENANT_SCOPE_CATALOG_V1.name
    with pytest.raises(DuplicateBindingError, match="exactly one provider"):
        install_prerequisite_bindings(
            [
                PrerequisiteBinding(scope, KERNEL_ROOT, "kernel"),
                PrerequisiteBinding(scope, "20260813_tenant_projection", "erp"),
            ]
        )


def test_binding_an_unregistered_effect_is_refused() -> None:
    with pytest.raises(UnknownPrerequisiteError):
        install_prerequisite_bindings(
            [PrerequisiteBinding("imaginary_effect.v1", KERNEL_ROOT, "kernel")]
        )


@pytest.mark.parametrize("revision", ["", "Not A Revision", "x" * 33, "-leading"])
def test_a_binding_must_name_a_usable_revision_id(revision: str) -> None:
    with pytest.raises(InvalidRevisionReferenceError):
        PrerequisiteBinding(TENANT_SCOPE_CATALOG_V1.name, revision, "kernel")


def test_a_binding_must_name_its_lineage_owner() -> None:
    """Carried so a review diff shows the binding crossing a lineage boundary
    instead of hiding it inside a revision id."""
    with pytest.raises(ValueError, match="lineage owner"):
        PrerequisiteBinding(TENANT_SCOPE_CATALOG_V1.name, KERNEL_ROOT, "  ")


def test_installing_replaces_rather_than_accumulates() -> None:
    _bind_kernel()
    install_prerequisite_bindings(
        [PrerequisiteBinding(TENANT_SCOPE_CATALOG_V1.name, KERNEL_ROOT, "kernel")]
    )
    assert set(binding_map()) == {TENANT_SCOPE_CATALOG_V1.name}


def test_a_different_assembly_binds_the_same_effect_to_its_own_revision() -> None:
    """This is the case the whole mechanism exists for: ERP hosts the tenant
    catalogue in its own lineage and can never run kernel 0001."""
    install_prerequisite_bindings(
        [
            PrerequisiteBinding(
                TENANT_SCOPE_CATALOG_V1.name, "20260813_tenant_projection", "erp"
            ),
            PrerequisiteBinding(
                MODULE_DATABASE_ROLES_V1.name, "20260814_database_roles", "erp"
            ),
        ]
    )
    assert resolve_depends_on((TENANT_SCOPE_CATALOG_V1.name,)) == (
        "20260813_tenant_projection",
    )


# ── Graph inspection must not explode ───────────────────────────────────────


def test_an_unbound_assembly_resolves_empty_rather_than_raising() -> None:
    """`alembic heads`, `history` and `show` build the revision map WITHOUT
    running `env.py`, so they have no bindings at all. An earlier draft raised
    here, which made merely INSPECTING the graph crash. Nothing installed means
    nobody has answered yet, which is the inspection case."""
    assert resolve_depends_on((TENANT_SCOPE_CATALOG_V1.name,)) == ()


def test_a_partially_bound_assembly_still_raises() -> None:
    """The distinction that keeps the tolerance above from hiding a real fault:
    SOME bindings installed means an assembly HAS answered, so a missing one is
    a misconfiguration rather than an uninitialised map."""
    install_prerequisite_bindings(
        [PrerequisiteBinding(TENANT_SCOPE_CATALOG_V1.name, KERNEL_ROOT, "kernel")]
    )
    with pytest.raises(UnboundPrerequisiteError):
        resolve_depends_on((MODULE_DATABASE_ROLES_V1.name,))


def test_bindings_autoload_from_the_environment(monkeypatch) -> None:
    """The channel that keeps the INSPECTED graph faithful too, since an env var
    reaches both entry points and `install_prerequisite_bindings` reaches one."""
    monkeypatch.setenv(
        BINDINGS_ENV_VAR, "app.migration_bindings:ASSEMBLY_PREREQUISITE_BINDINGS"
    )
    assert resolve_depends_on((TENANT_SCOPE_CATALOG_V1.name,)) == (KERNEL_ROOT,)


def test_a_malformed_autoload_pointer_is_refused(monkeypatch) -> None:
    monkeypatch.setenv(BINDINGS_ENV_VAR, "app.migration_bindings")
    with pytest.raises(PrerequisiteError, match="module.path:ATTRIBUTE"):
        resolve_depends_on((TENANT_SCOPE_CATALOG_V1.name,))


# ── Enforcement is open too ─────────────────────────────────────────────────


def test_a_product_prerequisite_without_a_verifier_fails_loudly() -> None:
    """The registry advertises itself as open. Registering a spec used to be
    enough to pass declaration and binding, then die on a `KeyError` mid-
    migration — an extension point that only worked for the kernel's own two
    effects. An effect that cannot be proven must not be silently assumed."""
    from dotmac_kernel.migrations.verify import (
        PrerequisiteVerifierMissingError,
        require_prerequisites,
    )

    register_prerequisites(
        [PrerequisiteSpec("unproven_effect.v1", "a product supplies this")]
    )
    install_prerequisite_bindings(
        [PrerequisiteBinding("unproven_effect.v1", "pr_0001_thing", "product")]
    )
    with pytest.raises(PrerequisiteVerifierMissingError, match="register_verifier"):
        # Raises before the bind is touched, so None never reaches a query.
        require_prerequisites(None, ("unproven_effect.v1",))  # type: ignore[arg-type]


def test_a_product_can_register_its_own_verifier() -> None:
    """Sensitivity proof for the check above, and the property that makes the
    registry genuinely open rather than open-looking."""
    from dotmac_kernel.migrations.verify import register_verifier, require_prerequisites

    seen: list[object] = []
    register_prerequisites(
        [PrerequisiteSpec("provable_effect.v1", "a product supplies this")]
    )
    register_verifier("provable_effect.v1", lambda bind: seen.append(bind))
    install_prerequisite_bindings(
        [PrerequisiteBinding("provable_effect.v1", "pr_0001_thing", "product")]
    )
    require_prerequisites(None, ("provable_effect.v1",))  # type: ignore[arg-type]
    assert seen == [None]


# ── Role posture: every attribute combination ───────────────────────────────

#: The one acceptable observation: `(rolbypassrls, rolsuper)` per role. Kernel
#: `0001` creates exactly this — `app_admin LOGIN BYPASSRLS`, the other two
#: plain `LOGIN`.
GOOD_ROLES = {
    "app_admin": (True, False),
    "app_user": (False, False),
    "platform_api": (False, False),
}


def test_the_kernels_own_role_creation_satisfies_the_contract() -> None:
    """Non-vacuity: a contract nothing can satisfy would fail every install."""
    from dotmac_kernel.migrations.verify import role_violations

    assert role_violations(GOOD_ROLES) == []


@pytest.mark.parametrize("role", ["app_admin", "app_user", "platform_api"])
@pytest.mark.parametrize("bypassrls", [True, False])
@pytest.mark.parametrize("superuser", [True, False])
def test_every_role_attribute_combination_is_decided(
    role: str, bypassrls: bool, superuser: bool
) -> None:
    """All 4 combinations across 3 roles, so no posture is merely unconsidered.

    `app_admin` is the one that changed: an earlier draft accepted BYPASSRLS OR
    SUPERUSER, which certified a cluster-wide identity to satisfy a requirement
    only ever about reading past RLS. The contract says `LOGIN BYPASSRLS`, so
    that is what is required — bypass true, superuser false.
    """
    from dotmac_kernel.migrations.verify import role_violations

    observed = {**GOOD_ROLES, role: (bypassrls, superuser)}
    problems = role_violations(observed)
    acceptable = GOOD_ROLES[role] == (bypassrls, superuser)
    assert (problems == []) is acceptable, (
        f"{role} with bypassrls={bypassrls} superuser={superuser} should "
        f"{'pass' if acceptable else 'fail'}; got {problems}"
    )


def test_a_superuser_app_admin_is_refused_naming_the_reason() -> None:
    """Called out separately because it is the finding, not just a row in a
    matrix: a superuser satisfies "can read past RLS" and brings DDL on every
    database, role creation and COPY PROGRAM with it."""
    from dotmac_kernel.migrations.verify import role_violations

    problems = role_violations({**GOOD_ROLES, "app_admin": (True, True)})
    assert any("rolsuper=False" in p and "cluster-wide" in p for p in problems)


def test_a_missing_role_is_reported_rather_than_skipped() -> None:
    from dotmac_kernel.migrations.verify import role_violations

    observed = {k: v for k, v in GOOD_ROLES.items() if k != "platform_api"}
    assert any("do not exist" in p for p in role_violations(observed))


# ── Declaration sites ───────────────────────────────────────────────────────


def test_the_kernel_lineage_declares_what_it_supplies() -> None:
    from dotmac_kernel.namespaces import KERNEL_MIGRATION_OWNER

    assert set(KERNEL_MIGRATION_OWNER.provides) == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
    }


def test_an_owner_cannot_claim_to_provide_an_unregistered_effect() -> None:
    with pytest.raises(UnknownPrerequisiteError):
        MigrationOwner(
            owner="x",
            prefix="x",
            branch_label="x",
            db_schema="mod_x",
            provides=("unregistered_effect.v1",),
        )


def test_a_manifest_cannot_require_an_unregistered_effect() -> None:
    with pytest.raises(UnknownPrerequisiteError):
        ModuleManifest(
            code="x",
            version="1.0.0",
            short_code="xx",
            migration_prefix="xx",
            tables=("t",),
            requires=("unregistered_effect.v1",),
        )


def test_a_stateless_module_cannot_require_a_migration_effect() -> None:
    """`requires` orders MIGRATIONS. A module with no lineage has none to order,
    so declaring one is a category error rather than a harmless extra."""
    with pytest.raises(ModuleRegistryError, match="owns no lineage"):
        ModuleManifest(
            code="stateless",
            version="1.0.0",
            requires=(TENANT_SCOPE_CATALOG_V1.name,),
        )


def test_files_requires_a_tenant_target_and_roles_and_nothing_else() -> None:
    """The regression this guards: re-coupling stored bytes to the kernel's
    identity, RBAC and audit estate because one FK target lives beside them."""
    from dotmac_files.manifest import module as files

    assert set(files.requires) == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
    }


def test_the_files_root_names_no_foreign_revision() -> None:
    """`depends_on` must be resolved from bindings, never hard-coded — the exact
    defect that made `dotmac-files` un-installable in ERP.

    Asserted on the AST rather than the text. Two earlier spellings of this test
    failed for the same reason: the migration's docstring QUOTES the old
    `depends_on = ("0001_initial_tenant_schema",)` in order to explain why it is
    gone, so any substring check also matches the explanation. What actually
    matters is the shape of the assignment, and only a parse can see that.
    """
    import ast
    from pathlib import Path

    import dotmac_files

    root = (
        Path(dotmac_files.__file__).parent
        / "migrations"
        / "versions"
        / "fi_0001_stored_files.py"
    )
    tree = ast.parse(root.read_text(encoding="utf-8"))
    assigned = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    depends_on = assigned["depends_on"]
    assert isinstance(
        depends_on, ast.Call
    ), "`depends_on` must be resolved from the assembly's bindings, not a literal"
    assert getattr(depends_on.func, "id", None) == "resolve_depends_on"
    assert ast.literal_eval(assigned["REQUIRES"]) == (
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
    )
