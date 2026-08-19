"""Consumer tests for `ModuleManifest` / `ModuleRegistry` (module control-plane
directive step 2).

The registry is the ONE place that answers "is the installed module set
coherent?". Every check it owns gets a RED-sensitive test here: a duplicate
code, an unsupported contract version, a missing dependency, and a cycle must
each raise its own named error — a test that only asserted "a valid set
validates" would still pass if every check were deleted.

Also pinned: the deterministic startup order (and specifically that it is
dependency-order with DECLARATION order as the tiebreak, not alphabetical), the
`FeatureManifest` compatibility adaptation, and the inventory payload shape that
health/diagnostics consumers read.
"""

from __future__ import annotations

import pytest
from dotmac_kernel import (
    DuplicateModuleError,
    FeatureManifest,
    MissingModuleDependencyError,
    ModuleContractVersionError,
    ModuleDependencyCycleError,
    ModuleInventoryEntry,
    ModuleManifest,
    ModuleRegistry,
    ModuleRegistryError,
    NavItem,
    UnknownModuleError,
)
from dotmac_kernel.modules import (
    KERNEL_MODULE_CONTRACT_VERSION,
    UNVERSIONED,
)
from fastapi import APIRouter


def _m(
    code: str,
    *,
    version: str = "1.0.0",
    deps: tuple[str, ...] = (),
    **kwargs: object,
) -> ModuleManifest:
    return ModuleManifest(code=code, version=version, dependencies=deps, **kwargs)  # type: ignore[arg-type]


def _codes(registry: ModuleRegistry) -> list[str]:
    return [m.code for m in registry.startup_order()]


# ── Manifest shape ──────────────────────────────────────────────────────────


def test_manifest_normalizes_sequences_to_tuples() -> None:
    """Declared with lists, stored as tuples — a manifest is a frozen
    declaration, so a caller cannot mutate the registry's view after the fact."""
    manifest = ModuleManifest(
        code="inventory",
        version="1.4.0",
        dependencies=["parties"],
        api_routers=[APIRouter()],
        nav=[NavItem("Inventory", "/admin/inventory")],
        capabilities=["inventory.use"],
    )
    assert manifest.dependencies == ("parties",)
    assert isinstance(manifest.api_routers, tuple)
    assert isinstance(manifest.nav, tuple)
    assert manifest.capabilities == ("inventory.use",)
    assert manifest.contract_version == KERNEL_MODULE_CONTRACT_VERSION


def test_manifest_requires_code_and_version() -> None:
    with pytest.raises(ModuleRegistryError):
        ModuleManifest(code="", version="1.0.0")
    with pytest.raises(ModuleRegistryError):
        ModuleManifest(code="inventory", version="")


def test_compat_aliases_expose_feature_manifest_names() -> None:
    """`name`/`routers` are read-only views of `code`/`api_routers` — this is
    what lets `mount_features` and friends take a ModuleManifest unchanged."""
    api = APIRouter()
    manifest = _m("inventory", api_routers=[api])
    assert manifest.name == "inventory"
    assert list(manifest.routers) == [api]


# ── FeatureManifest compatibility adaptation ────────────────────────────────


def test_from_feature_carries_every_field_across() -> None:
    api, web = APIRouter(), APIRouter()
    seed_calls: list[int] = []
    feature = FeatureManifest(
        name="legacy",
        routers=[api],
        web_routers=[web],
        nav=[NavItem("Legacy", "/admin/legacy")],
        core=False,
        enabled_by_default=False,
        capabilities=["legacy.use"],
        charge_models=["recurring_access"],
        obligation_sources=["accepted_order_line"],
        outbox_event_types=["legacy.wake"],
        seed=lambda: seed_calls.append(1),
    )
    adapted = ModuleManifest.from_feature(feature)

    assert adapted.code == "legacy"
    assert list(adapted.api_routers) == [api]
    assert list(adapted.web_routers) == [web]
    assert adapted.nav[0].label == "Legacy"
    assert adapted.core is False
    assert adapted.enabled_by_default is False
    assert adapted.capabilities == ("legacy.use",)
    assert adapted.charge_models == ("recurring_access",)
    assert adapted.obligation_sources == ("accepted_order_line",)
    assert adapted.outbox_event_types == ("legacy.wake",)
    assert adapted.seed is not None
    adapted.seed()
    assert seed_calls == [1]
    # No version/dependencies declared by a FeatureManifest — the adapter must
    # NOT invent either.
    assert adapted.version == UNVERSIONED
    assert adapted.dependencies == ()
    assert adapted.contract_version == KERNEL_MODULE_CONTRACT_VERSION


def test_from_feature_accepts_enrichment_without_touching_the_package() -> None:
    """An assembly can pin a version and declare edges for a feature it has not
    migrated yet — the migration path that keeps this step non-breaking."""
    adapted = ModuleManifest.from_feature(
        FeatureManifest(name="custom_fields"),
        version="2.1.0",
        dependencies=("parties",),
    )
    assert (adapted.version, adapted.dependencies) == ("2.1.0", ("parties",))


def test_registry_accepts_feature_and_module_manifests_mixed() -> None:
    registry = ModuleRegistry(
        [FeatureManifest(name="parties"), _m("inventory", deps=("parties",))]
    )
    assert _codes(registry) == ["parties", "inventory"]
    assert registry.get("parties").version == UNVERSIONED
    assert registry.get("inventory").version == "1.0.0"


# ── RED: duplicate codes ────────────────────────────────────────────────────


def test_duplicate_module_code_fails_closed() -> None:
    with pytest.raises(DuplicateModuleError) as exc:
        ModuleRegistry([_m("inventory"), _m("inventory", version="2.0.0")])
    assert "inventory" in str(exc.value)


def test_duplicate_across_a_feature_and_a_module_manifest_is_still_a_duplicate() -> (
    None
):
    """The adaptation must not create a loophole where the same code declared
    once as a FeatureManifest and once as a ModuleManifest slips through."""
    with pytest.raises(DuplicateModuleError):
        ModuleRegistry([FeatureManifest(name="inventory"), _m("inventory")])


def test_duplicate_error_lists_every_offending_code() -> None:
    with pytest.raises(DuplicateModuleError) as exc:
        ModuleRegistry([_m("a"), _m("b"), _m("a"), _m("b")])
    message = str(exc.value)
    assert "'a'" in message and "'b'" in message


# ── RED: contract-version mismatch ──────────────────────────────────────────


def test_unsupported_contract_version_fails_closed() -> None:
    with pytest.raises(ModuleContractVersionError) as exc:
        ModuleRegistry([_m("inventory", contract_version=99)])
    message = str(exc.value)
    assert "inventory" in message
    assert "99" in message
    # The message must say what IS supported, or an operator cannot act on it.
    assert str(KERNEL_MODULE_CONTRACT_VERSION) in message


def test_contract_version_below_the_supported_floor_also_fails() -> None:
    """Not just "too new" — a module built for a RETIRED generation must fail
    too, rather than load half-understood."""
    with pytest.raises(ModuleContractVersionError):
        ModuleRegistry([_m("inventory", contract_version=0)])


def test_supported_contract_versions_is_overridable_for_forward_compat() -> None:
    """A kernel that supports two generations loads both — the parameter is the
    seam that makes a contract bump a rollout rather than a flag day."""
    registry = ModuleRegistry(
        [_m("old", contract_version=1), _m("new", contract_version=2)],
        supported_contract_versions=frozenset({1, 2}),
    )
    assert registry.codes() == {"old", "new"}


# ── RED: missing dependencies ───────────────────────────────────────────────


def test_missing_dependency_fails_closed() -> None:
    with pytest.raises(MissingModuleDependencyError) as exc:
        ModuleRegistry([_m("inventory", deps=("parties",))])
    message = str(exc.value)
    assert "inventory" in message and "parties" in message


def test_missing_dependency_error_lists_every_unsatisfied_edge() -> None:
    with pytest.raises(MissingModuleDependencyError) as exc:
        ModuleRegistry([_m("a", deps=("ghost", "phantom"))])
    message = str(exc.value)
    assert "ghost" in message and "phantom" in message


def test_dependency_disabled_in_this_deployment_fails_closed() -> None:
    """Installed is not enough: `enabled_order` proves the dependency is
    actually RUNNING. Disabling a module something else needs is a deployment
    misconfiguration and must surface at startup."""
    registry = ModuleRegistry([_m("parties"), _m("inventory", deps=("parties",))])
    registry.enabled_order()  # nothing disabled → fine
    with pytest.raises(MissingModuleDependencyError) as exc:
        registry.enabled_order({"parties"})
    assert "not enabled" in str(exc.value)


def test_enabled_by_default_false_counts_as_not_enabled_for_dependents() -> None:
    """The opt-out flag and DISABLED_FEATURES must reach the same verdict —
    otherwise one of the two switches is a silent hole in dependency checking."""
    registry = ModuleRegistry(
        [_m("parties", enabled_by_default=False), _m("inventory", deps=("parties",))]
    )
    with pytest.raises(MissingModuleDependencyError):
        registry.enabled_order()


def test_disabling_a_module_nothing_depends_on_is_fine() -> None:
    registry = ModuleRegistry([_m("parties"), _m("inventory", deps=("parties",))])
    assert [m.code for m in registry.enabled_order({"inventory"})] == ["parties"]


# ── RED: dependency cycles ──────────────────────────────────────────────────


def test_dependency_cycle_fails_closed_and_names_the_cycle() -> None:
    with pytest.raises(ModuleDependencyCycleError) as exc:
        ModuleRegistry(
            [_m("a", deps=("b",)), _m("b", deps=("c",)), _m("c", deps=("a",))]
        )
    message = str(exc.value)
    # A diagnosable message: the actual path, not just "there is a cycle".
    assert "->" in message
    for code in ("a", "b", "c"):
        assert code in message


def test_self_dependency_is_a_cycle() -> None:
    with pytest.raises(ModuleDependencyCycleError) as exc:
        ModuleRegistry([_m("a", deps=("a",))])
    assert "a -> a" in str(exc.value)


def test_a_cycle_is_reported_even_when_acyclic_modules_are_present() -> None:
    """The acyclic modules drain first; the stuck remainder must still be
    detected rather than silently truncating the startup order."""
    with pytest.raises(ModuleDependencyCycleError):
        ModuleRegistry([_m("standalone"), _m("x", deps=("y",)), _m("y", deps=("x",))])


def test_a_diamond_is_not_a_cycle() -> None:
    """Sensitivity check on cycle detection: a shared dependency reached by two
    paths is ordinary, and must not be misreported as a cycle."""
    registry = ModuleRegistry(
        [
            _m("base"),
            _m("left", deps=("base",)),
            _m("right", deps=("base",)),
            _m("top", deps=("left", "right")),
        ]
    )
    assert _codes(registry) == ["base", "left", "right", "top"]


# ── Deterministic startup order ─────────────────────────────────────────────


def test_dependencies_come_before_dependents() -> None:
    registry = ModuleRegistry(
        [_m("app", deps=("db",)), _m("db", deps=("config",)), _m("config")]
    )
    assert _codes(registry) == ["config", "db", "app"]


def test_declaration_order_is_the_tiebreak_not_alphabetical() -> None:
    """Load-bearing: an assembly's module list is a deliberate mount order
    (route matching is first-match-wins), so introducing the registry must not
    reorder modules that declare no dependencies. Alphabetical would put 'auth'
    first here — declaration order must win."""
    declared = ["tenants", "auth", "parties", "rbac"]
    registry = ModuleRegistry([_m(code) for code in declared])
    assert _codes(registry) == declared


def test_order_is_a_pure_function_of_declaration_order_and_edges() -> None:
    """Same manifests, same order, every boot — and a DIFFERENT declaration
    order legitimately yields a different (still dependency-valid) order."""
    manifests = [_m("a"), _m("b", deps=("c",)), _m("c")]
    first = _codes(ModuleRegistry(manifests))
    assert first == _codes(ModuleRegistry(manifests)) == ["a", "c", "b"]
    assert _codes(ModuleRegistry(list(reversed(manifests)))) == ["c", "b", "a"]


def test_enabled_order_preserves_startup_order() -> None:
    registry = ModuleRegistry([_m("a"), _m("b"), _m("c")])
    assert [m.code for m in registry.enabled_order({"b"})] == ["a", "c"]


# ── Lookup ──────────────────────────────────────────────────────────────────


def test_get_and_is_installed() -> None:
    registry = ModuleRegistry([_m("inventory", version="1.4.0")])
    assert registry.get("inventory").version == "1.4.0"
    assert registry.is_installed("inventory")
    assert not registry.is_installed("ghost")
    with pytest.raises(UnknownModuleError):
        registry.get("ghost")


def test_enabled_codes_is_the_single_definition_of_enabled() -> None:
    registry = ModuleRegistry([_m("a"), _m("b"), _m("c", enabled_by_default=False)])
    assert registry.codes() == {"a", "b", "c"}
    assert registry.enabled_codes({"b"}) == {"a"}


# ── Inventory (health / diagnostics) ────────────────────────────────────────


def test_inventory_reports_installed_versions_sorted_by_code() -> None:
    registry = ModuleRegistry(
        [_m("zeta", version="2.0.0"), _m("alpha", version="1.0.0", core=False)]
    )
    inventory = registry.inventory()
    assert [e.code for e in inventory] == ["alpha", "zeta"]
    assert inventory[0] == ModuleInventoryEntry(
        code="alpha",
        version="1.0.0",
        contract_version=KERNEL_MODULE_CONTRACT_VERSION,
        dependencies=(),
        core=False,
        enabled=True,
    )


def test_inventory_marks_disabled_modules_as_installed_but_not_enabled() -> None:
    """Installed and enabled are different facts — an operator diagnosing "why
    is this route missing?" needs to see the code IS present and IS off."""
    registry = ModuleRegistry([_m("web", core=False), _m("auth")])
    by_code = {e.code: e for e in registry.inventory({"web"})}
    assert by_code["web"].enabled is False
    assert by_code["auth"].enabled is True


def test_inventory_payload_is_json_safe_and_complete() -> None:
    import json

    registry = ModuleRegistry([_m("base"), _m("top", version="3.1.0", deps=("base",))])
    payload = registry.inventory_payload()
    json.dumps(payload)  # must not raise — diagnostics serialize this

    assert payload["kernel_contract_version"] == KERNEL_MODULE_CONTRACT_VERSION
    assert payload["startup_order"] == ["base", "top"]
    modules = payload["modules"]
    assert isinstance(modules, list)
    assert modules[1] == {
        "code": "top",
        "version": "3.1.0",
        "contract_version": KERNEL_MODULE_CONTRACT_VERSION,
        "dependencies": ["base"],
        "core": True,
        "enabled": True,
        # D1: stateless modules declare no namespace and own no lineage.
        "db_schema": None,
        "migration_branch": None,
    }
    # D1 item 5: the attribution that explains every `alembic_version` row.
    assert payload["migration_owners"] == [
        {
            "owner": "assembly",
            "prefix": "a",
            "branch_label": "assembly",
            "db_schema": None,
        },
        {
            "owner": "kernel",
            "prefix": "k",
            "branch_label": "kernel",
            "db_schema": None,
        },
    ]


# ── Every registry error is one catchable family ────────────────────────────


@pytest.mark.parametrize(
    "manifests",
    [
        pytest.param([_m("a"), _m("a")], id="duplicate"),
        pytest.param([_m("a", contract_version=99)], id="contract-version"),
        pytest.param([_m("a", deps=("ghost",))], id="missing-dependency"),
        pytest.param([_m("a", deps=("a",))], id="cycle"),
    ],
)
def test_every_validation_failure_is_a_module_registry_error(
    manifests: list[ModuleManifest],
) -> None:
    """One base class a caller can catch — and every one is a ValueError, so an
    assembly's existing startup error handling already covers them."""
    with pytest.raises(ModuleRegistryError):
        ModuleRegistry(manifests)
    with pytest.raises(ValueError):
        ModuleRegistry(manifests)
