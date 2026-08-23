"""Stateful extraction, allocation, and selectable-lineage canaries."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path

import dotmac_collections
import pytest
from dotmac_collections import models
from dotmac_collections.manifest import module
from dotmac_kernel.migrations.gate import run_gate
from dotmac_kernel.namespaces import (
    COLLECTIONS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
)
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection

from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "packages/dotmac-collections"
MIGRATION = (
    PACKAGE_ROOT / "src/dotmac_collections/migrations/versions/cl_0001_collections.py"
)
KERNEL_VERSIONS = (
    PROJECT_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
SUB_PIN = "d1a1a913e287ffadaf21b7da7be448f2c28b5483"


def test_public_surface_exports_adopter_contracts_but_not_persistence() -> None:
    public = set(dotmac_collections.__all__)
    assert {
        "AssessCollectionExposureV1",
        "CollectionActionRequestedV1",
        "CollectionActionService",
        "CollectionCaseService",
        "CollectionNoticeRequestedV1",
        "CollectionNoticeService",
        "CollectionPolicyService",
        "CollectionsTimer",
        "PaymentArrangementService",
        "ProcessCollectionStepDueV1",
        "ReceivableObservationV1",
        "ReceivablesReader",
        "TimerRequestV1",
        "module",
        "versions_dir",
    } <= public
    assert "models" not in public
    assert not any(
        name.startswith(("CollectionPolicyRow", "Platform")) for name in public
    )


def _dossier_problems(dossier: Mapping[str, object]) -> tuple[str, ...]:
    problems: list[str] = []
    if dossier.get("classification") != "optional-module":
        problems.append("classification")
    if dossier.get("status") != "audit-complete":
        problems.append("audit-status")
    if dossier.get("source_mode") != "product-first":
        problems.append("source-mode")
    if f"dotmac_sub:{SUB_PIN}" not in dossier.get("source_revisions", []):
        problems.append("sub-pin")
    if dossier.get("contract_consumers") != []:
        problems.append("unproven-consumer")
    return tuple(problems)


def test_dossier_detector_proves_its_sensitivity() -> None:
    assert _dossier_problems({}) == (
        "classification",
        "audit-status",
        "source-mode",
        "sub-pin",
        "unproven-consumer",
    )


def test_product_first_dossier_is_exact_and_claims_no_cutover_early() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text("utf-8"))
    assert not _dossier_problems(dossier)
    assert "dotmac_sub" in dossier["source_repositories"]
    assert "dotmac_sub" in dossier["candidate_consumers"]
    retirement = str(dossier["local_copy_retirement"])
    for required in ("dunning_runner", "prepaid_balance_sweep", "sensitivity"):
        assert required in retirement


def test_namespace_lineage_and_release_identity_are_allocated_together() -> None:
    assert COLLECTIONS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == COLLECTIONS_MIGRATION_OWNER.owner == "collections"
    assert module.short_code == "coll"
    assert module.migration_prefix == COLLECTIONS_MIGRATION_OWNER.prefix == "cl"
    assert (
        module.migration_branch
        == COLLECTIONS_MIGRATION_OWNER.branch_label
        == "collections"
    )
    assert models.SCHEMA == COLLECTIONS_MIGRATION_OWNER.db_schema == "mod_coll"
    assert MIGRATION.is_file()


@pytest.mark.parametrize(
    "planes",
    [
        (ModulePlane.TENANT,),
        (ModulePlane.PLATFORM,),
        (ModulePlane.TENANT, ModulePlane.PLATFORM),
    ],
)
def test_every_supported_plane_selection_passes_the_composed_gate(
    planes: tuple[ModulePlane, ...],
) -> None:
    report = run_gate(
        [module],
        [KERNEL_VERSIONS, MIGRATION.parent],
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
        module_planes=(ModulePlaneSelection(module="collections", planes=planes),),
    )
    assert report.ok, report.violations


def test_package_has_no_sibling_module_dependency() -> None:
    package = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text("utf-8"))
    dependencies = package["tool"]["poetry"]["dependencies"]
    assert "dotmac-kernel" in dependencies
    assert "sqlalchemy" in dependencies
    assert not {
        name
        for name in dependencies
        if name.startswith("dotmac-") and name != "dotmac-kernel"
    }
