"""Shared packages must prove product-first extraction before implementation.

ADR-0006's 2026-08-08 amendment turns ERP/Sub from inspiration into mandatory
source evidence.  This gate makes a missing dossier, missing product audit, or
new unresolved package fail in the fast architecture suite.  The existing debt
map is exact and may only shrink.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = PROJECT_ROOT / "packages"

VALID_CLASSIFICATIONS = {
    "universal-facility",
    "presentation-foundation",
    "optional-module",
}
VALID_SOURCE_MODES = {
    "product-first",
    "greenfield-after-inventory",
    "historical-mixed",
    "unresolved",
}

# These packages predate the product-first dossier gate.  Keeping the status
# map exact prevents "temporary" audit debt from becoming the default for the
# next package.  A row is deleted when that package reaches approved status.
PRE_RULE_DEBT = {
    "dotmac-kernel": "historical-pre-rule",
    "dotmac-ui": "historical-pre-rule",
    "dotmac-template-studio": "audit-required",
}

REQUIRED_TEXT_FIELDS = {
    "package",
    "classification",
    "status",
    "source_mode",
    "owner",
    "contract",
    "first_cutover",
    "shadow_and_drift",
    "local_copy_retirement",
    "next_action",
}
REQUIRED_LIST_FIELDS = {
    "source_repositories",
    "source_paths",
    "preserved_tests",
    "candidate_consumers",
    "inventory_evidence",
}


class ExtractionDossierError(AssertionError):
    """The package cannot pass the product-first extraction gate."""


def _shared_package_dirs() -> list[Path]:
    return sorted(
        path
        for path in PACKAGES_DIR.iterdir()
        if path.is_dir() and (path / "pyproject.toml").is_file()
    )


def _load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _validate_dossier(
    dossier: dict[str, Any], *, directory_name: str, distribution_name: str
) -> None:
    problems: list[str] = []

    if dossier.get("schema_version") != 1:
        problems.append("schema_version must be 1")

    for field in sorted(REQUIRED_TEXT_FIELDS):
        value = dossier.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{field} must be a non-empty string")

    for field in sorted(REQUIRED_LIST_FIELDS):
        value = dossier.get(field)
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            problems.append(f"{field} must be a non-empty string list")

    for field in ("source_paths", "preserved_tests"):
        references = dossier.get(field)
        if not isinstance(references, list):
            continue
        for reference in references:
            if not isinstance(reference, str) or ":" not in reference:
                problems.append(f"{field} entries must use repository:path references")
                continue
            repository, relative_path = reference.split(":", 1)
            if (
                repository == "dotmac_starter_mt"
                and not (PROJECT_ROOT / relative_path).exists()
            ):
                problems.append(f"{field} local reference does not exist: {reference}")

    inventory_references = dossier.get("inventory_evidence")
    if isinstance(inventory_references, list):
        for reference in inventory_references:
            if isinstance(reference, str) and not (PROJECT_ROOT / reference).is_file():
                problems.append(f"inventory evidence does not exist: {reference}")

    contract_consumers = dossier.get("contract_consumers")
    if not isinstance(contract_consumers, list) or not all(
        isinstance(consumer, str) and consumer.strip()
        for consumer in contract_consumers
    ):
        problems.append("contract_consumers must be a string list")

    package = dossier.get("package")
    if package != directory_name or package != distribution_name:
        problems.append(
            "package must match both the package directory and pyproject distribution"
        )

    classification = dossier.get("classification")
    if classification not in VALID_CLASSIFICATIONS:
        problems.append(
            f"classification must be one of {sorted(VALID_CLASSIFICATIONS)}"
        )

    source_mode = dossier.get("source_mode")
    if source_mode not in VALID_SOURCE_MODES:
        problems.append(f"source_mode must be one of {sorted(VALID_SOURCE_MODES)}")

    repositories = dossier.get("source_repositories")
    if isinstance(repositories, list) and not {"dotmac_erp", "dotmac_sub"}.issubset(
        repositories
    ):
        problems.append(
            "source_repositories must show both ERP and Sub were inventoried"
        )

    candidate_consumers = dossier.get("candidate_consumers")
    if isinstance(candidate_consumers, list) and len(set(candidate_consumers)) < 2:
        problems.append("candidate_consumers must name two independent products")

    status = dossier.get("status")
    expected_debt = PRE_RULE_DEBT.get(directory_name)
    if status == "approved":
        if source_mode not in {"product-first", "greenfield-after-inventory"}:
            problems.append(
                "an approved package must be product-first or "
                "greenfield-after-inventory"
            )
        consumers = dossier.get("contract_consumers")
        if not isinstance(consumers, list) or len(set(consumers)) < 2:
            problems.append(
                "an approved package needs two independent contract consumers"
            )
        if expected_debt is not None:
            problems.append(
                "remove this package from PRE_RULE_DEBT when its dossier "
                "becomes approved"
            )
    elif status != expected_debt:
        problems.append(
            "only the exact PRE_RULE_DEBT map may carry an unresolved or "
            "historical status"
        )

    if problems:
        raise ExtractionDossierError("; ".join(problems))


def test_every_shared_distribution_has_a_valid_extraction_dossier() -> None:
    package_dirs = _shared_package_dirs()
    assert package_dirs, "no shared package distributions found"

    for package_dir in package_dirs:
        dossier_path = package_dir / "EXTRACTION.toml"
        assert dossier_path.is_file(), f"{package_dir.name} has no EXTRACTION.toml"
        pyproject = _load_toml(package_dir / "pyproject.toml")
        distribution_name = pyproject["tool"]["poetry"]["name"]
        _validate_dossier(
            _load_toml(dossier_path),
            directory_name=package_dir.name,
            distribution_name=distribution_name,
        )


def test_missing_product_test_proof_is_rejected() -> None:
    """Sensitivity proof: a plausible-looking dossier still fails without tests."""
    dossier = _load_toml(PACKAGES_DIR / "dotmac-template-studio/EXTRACTION.toml")
    dossier["preserved_tests"] = []

    with pytest.raises(ExtractionDossierError, match="preserved_tests"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-template-studio",
            distribution_name="dotmac-template-studio",
        )


def test_a_new_package_cannot_hide_behind_audit_required() -> None:
    """Sensitivity proof: unresolved status is closed debt, not an entry mode."""
    dossier = _load_toml(PACKAGES_DIR / "dotmac-template-studio/EXTRACTION.toml")
    dossier["package"] = "dotmac-new-module"

    with pytest.raises(ExtractionDossierError, match="PRE_RULE_DEBT"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-new-module",
            distribution_name="dotmac-new-module",
        )


def test_an_approved_package_needs_two_contract_consumers() -> None:
    """Sensitivity proof for the original ADR-0006 F0 extraction gate."""
    dossier = _load_toml(PACKAGES_DIR / "dotmac-template-studio/EXTRACTION.toml")
    dossier.update(
        {
            "package": "dotmac-new-module",
            "status": "approved",
            "source_mode": "product-first",
            "contract_consumers": ["dotmac_erp"],
        }
    )

    with pytest.raises(ExtractionDossierError, match="two independent"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-new-module",
            distribution_name="dotmac-new-module",
        )
