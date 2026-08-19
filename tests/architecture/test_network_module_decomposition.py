"""Govern the approved Sub-first network module decomposition.

The checked-in TOML ledger is deliberately mechanical: this test makes the
module set, sequence, source product, first cutover, and critical authority
boundaries reviewable without treating an ADR's prose as as-built evidence.
"""

from __future__ import annotations

import copy
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "docs/inventories/network-module-dispositions.toml"
CONTRACT_LEDGER_PATH = ROOT / "docs/inventories/network-module-contracts.toml"
AUDIT_PATH = ROOT / "docs/inventories/network-module-sources.md"
ADR_PATH = ROOT / "docs/adr/0043-network-capabilities-decompose-sub-first.md"
PACKAGES_DIR = ROOT / "packages"

EXPECTED_MODULES = {
    "dotmac-ipam",
    "dotmac-network-inventory",
    "dotmac-network-observability",
    "dotmac-network-topology",
    "dotmac-network-assurance",
    "dotmac-network-control",
    "dotmac-fiber-plant",
    "dotmac-network-access",
    "dotmac-pon-access",
}
SHARED_CLOUD_CANDIDATES = {
    "dotmac-ipam",
    "dotmac-network-inventory",
    "dotmac-network-observability",
    "dotmac-network-topology",
    "dotmac-network-assurance",
    "dotmac-network-control",
}
ISP_ONLY_MODULES = {
    "dotmac-fiber-plant",
    "dotmac-network-access",
    "dotmac-pon-access",
}
REUSED_FOUNDATIONS = {"dotmac-inventory", "dotmac-assets"}


class NetworkDecompositionError(AssertionError):
    """The network module disposition ledger violates the accepted boundary."""


def _load_ledger() -> dict[str, Any]:
    with LEDGER_PATH.open("rb") as stream:
        return tomllib.load(stream)


def _load_contract_ledger() -> dict[str, Any]:
    with CONTRACT_LEDGER_PATH.open("rb") as stream:
        return tomllib.load(stream)


def _module_map(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    modules = ledger.get("modules", [])
    return {module["name"]: module for module in modules}


def _require(condition: bool, message: str, problems: list[str]) -> None:
    if not condition:
        problems.append(message)


def _validate_ledger(ledger: dict[str, Any]) -> None:
    problems: list[str] = []
    _require(ledger.get("schema_version") == 1, "schema_version must be 1", problems)
    _require(
        ledger.get("decision_status") == "accepted",
        "the disposition must remain an accepted decision",
        problems,
    )
    _require(
        ledger.get("source_product") == "dotmac_sub",
        "dotmac_sub must remain the product-first source",
        problems,
    )
    _require(
        ledger.get("build_strategy") == "single-starter-integration-branch",
        "the nine modules must be built on one Starter integration branch",
        problems,
    )
    _require(
        ledger.get("integration_branch") == "integration/network-module-suite",
        "the network-suite integration branch changed",
        problems,
    )
    _require(
        ledger.get("adoption_strategy") == "single-coordinated-sub-cutover",
        "Sub must adopt the complete suite through one coordinated cutover",
        problems,
    )
    _require(
        ledger.get("source_normalization_policy")
        == "code-owner-before-build-production-authority-before-adoption",
        "normalized source code gates the build; live authority gates adoption",
        problems,
    )
    _require(
        ledger.get("cloud_profile_state") == "profile-only-no-repository",
        "Cloud must not be represented as an audited repository or current adopter",
        problems,
    )

    revisions = ledger.get("revisions", {})
    for repository in ("starter", "sub", "crm", "erp", "workspace", "vendor_cp"):
        revision = revisions.get(repository, "")
        _require(
            re.fullmatch(r"[0-9a-f]{40}", revision) is not None,
            f"{repository} needs a pinned 40-character audit revision",
            problems,
        )

    conflicts = ledger.get("authority_conflicts", [])
    crm_conflicts = [
        conflict
        for conflict in conflicts
        if conflict.get("source") == "dotmac_crm:CLAUDE.md"
    ]
    _require(
        len(crm_conflicts) == 1
        and crm_conflicts[0].get("resolved_by")
        == "docs/adr/0024-applications-are-independent.md",
        "the stale CRM ownership claim needs an explicit ADR-0024 resolution",
        problems,
    )

    modules = ledger.get("modules", [])
    names = [module.get("name") for module in modules]
    _require(len(names) == len(set(names)), "module names must be unique", problems)
    _require(
        set(names) == EXPECTED_MODULES, "the approved nine-module set changed", problems
    )
    module_map = _module_map(ledger)
    foundations = ledger.get("reused_foundations", [])
    foundation_names = {foundation.get("name") for foundation in foundations}
    _require(
        foundation_names == REUSED_FOUNDATIONS,
        "the approved Inventory/Assets foundation set changed",
        problems,
    )
    for foundation in foundations:
        name = foundation.get("name", "<unnamed>")
        _require(
            foundation.get("status") == "audit-complete",
            f"{name} must not be reported adopted before its product cutover",
            problems,
        )
        _require(
            foundation.get("runtime_dependency") is False,
            f"{name} is an opaque composition handoff, not a sibling import",
            problems,
        )
        _require(
            (PACKAGES_DIR / name / "EXTRACTION.toml").is_file(),
            f"{name} must be present on the integration branch",
            problems,
        )

    orders = [module.get("implementation_order") for module in modules]
    _require(
        sorted(order for order in orders if isinstance(order, int))
        == list(range(1, len(EXPECTED_MODULES) + 1)),
        "implementation_order must be the unique sequence 1 through 9",
        problems,
    )

    for module in modules:
        name = module.get("name", "<unnamed>")
        _require(module.get("stateful") is True, f"{name} must be stateful", problems)
        _require(
            module.get("persistence_plane") == "tenant",
            f"{name} must remain tenant-plane only",
            problems,
        )
        _require(
            module.get("source_repository") == "dotmac_sub",
            f"{name} must be extracted from Sub",
            problems,
        )
        _require(
            module.get("source_mode") == "product-first",
            f"{name} must use product-first extraction",
            problems,
        )
        _require(
            module.get("first_cutover") == "dotmac_sub",
            f"{name} must cut over in Sub before another product",
            problems,
        )
        _require(
            module.get("adoption_cohort") == "network-suite-v1",
            f"{name} must remain in the single network-suite adoption cohort",
            problems,
        )
        _require(
            module.get("runtime_module_dependencies") == [],
            f"{name} must not import a sibling module",
            problems,
        )
        for field in (
            "owns",
            "excludes",
            "current_table_families",
            "current_service_families",
            "source_paths",
            "parity_tests",
            "entry_gate",
            "retirement_gate",
        ):
            _require(
                bool(module.get(field)), f"{name}.{field} must not be empty", problems
            )
        _require(
            all(
                path.startswith("dotmac_sub:")
                for path in module.get("source_paths", [])
            ),
            f"{name} source paths must point to the qualifying Sub implementation",
            problems,
        )
        _require(
            all(
                path.startswith("dotmac_sub:")
                for path in module.get("parity_tests", [])
            ),
            f"{name} parity tests must point to Sub tests",
            problems,
        )

        delivery_after = module.get("delivery_after", [])
        _require(
            set(delivery_after) <= EXPECTED_MODULES,
            f"{name}.delivery_after may name only approved modules",
            problems,
        )
        for predecessor in delivery_after:
            if predecessor in module_map:
                _require(
                    module_map[predecessor].get("implementation_order", 99)
                    < module.get("implementation_order", -1),
                    f"{name} must follow {predecessor}",
                    problems,
                )

    cloud_candidates = {
        module["name"] for module in modules if module.get("cloud_candidate") is True
    }
    _require(
        cloud_candidates == SHARED_CLOUD_CANDIDATES,
        "the Cloud-candidate module set changed",
        problems,
    )
    _require(
        all(
            module_map[name].get("cloud_candidate") is False
            for name in ISP_ONLY_MODULES
        ),
        "fiber and access modules must remain ISP-only",
        problems,
    )

    ipam = module_map.get("dotmac-ipam", {})
    _require(
        ipam.get("allows_product_foreign_keys") is False,
        "IPAM must not retain Sub subscriber/subscription/service FKs",
        problems,
    )
    _require(
        ipam.get("build_input_gate") == "sub-ip-one-writer-code-normalized"
        and ipam.get("adoption_gate") == "sub-ip-production-cutover-evidence-complete",
        "IPAM needs normalized source code before build and live cutover "
        "evidence before adoption",
        problems,
    )

    observability = module_map.get("dotmac-network-observability", {})
    _require(
        observability.get("external_io_owner") == "dotmac-integration",
        "external collectors must be Integrator connector plugins",
        problems,
    )
    _require(
        observability.get("production_metrics_owner") == "Dotmac Observability",
        "the module must not replace the production metrics/logs/traces stack",
        problems,
    )

    assurance = module_map.get("dotmac-network-assurance", {})
    _require(
        assurance.get("may_close_tickets") is False
        and assurance.get("may_close_work_orders") is False,
        "assurance may emit evidence but never close tickets or work orders",
        problems,
    )

    control = module_map.get("dotmac-network-control", {})
    _require(
        control.get("execution_owner") == "dotmac-kernel"
        and control.get("external_io_owner") == "dotmac-integration",
        "network control must separate command workflow from provider I/O",
        problems,
    )

    fiber = module_map.get("dotmac-fiber-plant", {})
    _require(
        fiber.get("build_input_gate") == "sub-primary-source-and-crm-conflict-audited"
        and fiber.get("adoption_gate") == "crm-outside-plant-consolidated-into-sub",
        "Fiber Plant may build from audited Sub code but cannot be adopted "
        "before CRM consolidation",
        problems,
    )

    network_inventory = module_map.get("dotmac-network-inventory", {})
    _require(
        set(network_inventory.get("reuses_foundations", [])) == REUSED_FOUNDATIONS,
        "Network Inventory must reuse Inventory and Assets without importing them",
        problems,
    )

    if problems:
        raise NetworkDecompositionError("\n".join(problems))


def test_network_module_disposition_ledger_is_complete() -> None:
    _validate_ledger(_load_ledger())


def test_network_module_disposition_is_linked_from_decision_and_audit() -> None:
    ledger_ref = "docs/inventories/network-module-dispositions.toml"
    for path in (ADR_PATH, AUDIT_PATH):
        text = path.read_text()
        assert ledger_ref in text, f"{path.relative_to(ROOT)} must link the ledger"
        for module in EXPECTED_MODULES:
            assert module in text, f"{path.relative_to(ROOT)} must name {module}"


def test_complete_suite_contract_ledger_is_typed_and_orm_free() -> None:
    contract_ledger = _load_contract_ledger()
    assert contract_ledger["schema_version"] == 1
    assert contract_ledger["adoption_cohort"] == "network-suite-v1"
    contracts = {
        module["name"]: module for module in contract_ledger.get("modules", [])
    }
    assert set(contracts) == EXPECTED_MODULES
    for package, contract in contracts.items():
        assert contract["package"] == package
        assert contract["contract_module"].endswith(".contracts")
        assert contract["returns_orm_instances"] is False
        assert contract["uses_opaque_cross_owner_refs"] is True
        for field in ("commands", "queries", "snapshots", "events"):
            members = contract.get(field, [])
            assert members, f"{package}.{field} must declare its public surface"
            assert len(members) == len(set(members)), f"duplicate {package}.{field}"


def test_detector_rejects_a_missing_approved_module() -> None:
    ledger = copy.deepcopy(_load_ledger())
    ledger["modules"] = [
        module for module in ledger["modules"] if module["name"] != "dotmac-ipam"
    ]
    with pytest.raises(NetworkDecompositionError):
        _validate_ledger(ledger)


def test_detector_rejects_cloud_scope_creep_into_isp_only_modules() -> None:
    ledger = copy.deepcopy(_load_ledger())
    _module_map(ledger)["dotmac-fiber-plant"]["cloud_candidate"] = True
    with pytest.raises(NetworkDecompositionError):
        _validate_ledger(ledger)


def test_detector_rejects_assurance_becoming_a_ticket_decision_system() -> None:
    ledger = copy.deepcopy(_load_ledger())
    _module_map(ledger)["dotmac-network-assurance"]["may_close_tickets"] = True
    with pytest.raises(NetworkDecompositionError):
        _validate_ledger(ledger)


def test_detector_rejects_cloud_first_extraction() -> None:
    ledger = copy.deepcopy(_load_ledger())
    _module_map(ledger)["dotmac-ipam"]["first_cutover"] = "dotmac_cloud"
    with pytest.raises(NetworkDecompositionError):
        _validate_ledger(ledger)


def test_detector_rejects_incremental_sub_adoption() -> None:
    ledger = copy.deepcopy(_load_ledger())
    _module_map(ledger)["dotmac-ipam"]["adoption_cohort"] = "ipam-early"
    with pytest.raises(NetworkDecompositionError):
        _validate_ledger(ledger)


def test_detector_rejects_a_second_stock_or_asset_owner() -> None:
    ledger = copy.deepcopy(_load_ledger())
    network_inventory = _module_map(ledger)["dotmac-network-inventory"]
    network_inventory["reuses_foundations"] = ["dotmac-assets"]
    with pytest.raises(NetworkDecompositionError):
        _validate_ledger(ledger)
