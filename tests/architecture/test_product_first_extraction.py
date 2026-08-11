"""Shared packages must prove product-first extraction before implementation.

ADR-0006's 2026-08-08 amendment turns ERP/Sub from inspiration into mandatory
source evidence.  This gate makes a missing dossier, missing product audit, or
new unresolved package fail in the fast architecture suite.  The existing debt
map is exact and may only shrink.

A dossier's `status` is one of exactly three things:

``approved``
    Two independent products are on the contract.  Requires an audited
    `source_mode` and two `contract_consumers`, and the package must not
    appear in ``PRE_RULE_DEBT``.
``audit-complete``
    The inventory was done and the unit was drawn deliberately, but nothing has
    adopted it yet.  ADR-0017 makes this gap unavoidable — the first cutover is
    what earns approval, and it cannot precede the first release.  It expires:
    once two contract consumers exist, the dossier must move to ``approved``.
``PRE_RULE_DEBT[package]``
    Grandfathered.  The map is exact and only shrinks, and this is deliberately
    NOT the same claim as ``audit-complete`` (ADR-0018: "grandfathered" must
    stay distinguishable from "reviewed and correct").
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
# The two modes that assert the ERP/Sub inventory actually happened.  Any status
# that claims the audit was done has to be backed by one of them.
AUDITED_SOURCE_MODES = {"product-first", "greenfield-after-inventory"}

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
    consumers = dossier.get("contract_consumers")
    consumer_count = len(set(consumers)) if isinstance(consumers, list) else 0
    if status == "approved":
        if source_mode not in AUDITED_SOURCE_MODES:
            problems.append(
                "an approved package must be product-first or "
                "greenfield-after-inventory"
            )
        if consumer_count < 2:
            problems.append(
                "an approved package needs two independent contract consumers"
            )
        if expected_debt is not None:
            problems.append(
                "remove this package from PRE_RULE_DEBT when its dossier "
                "becomes approved"
            )
    elif status == "audit-complete" and expected_debt is None:
        # The state between "inventoried and correctly drawn" and "two products
        # have proven the contract".  ADR-0017 makes that gap unavoidable: the
        # first cutover is what earns approval, and it cannot precede the first
        # release.  Without this status a new package must either claim
        # `approved` on zero evidence or be filed as pre-rule debt it is not —
        # and ADR-0018 is explicit that "grandfathered" and "reviewed, awaiting
        # proof" must stay distinguishable.
        if source_mode not in AUDITED_SOURCE_MODES:
            problems.append(
                "audit-complete claims the inventory was done; source_mode must "
                "be product-first or greenfield-after-inventory to back that"
            )
        if consumer_count >= 2:
            # The ratchet: once the consumers exist the claim is provable, so
            # this status stops being available.  Otherwise a package parks here
            # permanently and the gate silently stops meaning anything.
            problems.append(
                "audit-complete is a pre-adoption status; with two contract "
                "consumers the dossier must move to approved"
            )
        # `candidate_consumers` — who it is being built for — is already
        # required to name two independent products for every dossier above.
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


def test_audit_complete_expires_once_the_consumers_exist() -> None:
    """The ratchet on the pre-adoption status.

    `audit-complete` is honest while nothing has adopted the module.  The moment
    two products are on the contract the claim is provable, and a package that
    stays parked here would be carrying an unearned exemption — exactly the
    shape ADR-0018 rejects.
    """
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier["contract_consumers"] = ["dotmac_vendor_control_plane", "dotmac_sub"]

    with pytest.raises(ExtractionDossierError, match="must move to approved"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-ticketing",
            distribution_name="dotmac-ticketing",
        )


def test_audit_complete_cannot_be_claimed_without_the_inventory() -> None:
    """Specificity: the status asserts the ERP/Sub audit happened.

    Without an audited `source_mode` it would just be `unresolved` wearing a
    better name — a new package's route around the gate.
    """
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier["source_mode"] = "unresolved"

    with pytest.raises(ExtractionDossierError, match="inventory was done"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-ticketing",
            distribution_name="dotmac-ticketing",
        )


def test_a_module_must_name_two_candidate_consumers() -> None:
    """A module with one candidate consumer is a product feature, not a module.

    ADR-0006 §5 forbids extracting on resemblance; naming two independent
    products is the cheapest available proof that the unit was drawn for more
    than the repository it happens to live in.
    """
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier["candidate_consumers"] = ["dotmac_sub"]

    with pytest.raises(ExtractionDossierError, match="candidate_consumers"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-ticketing",
            distribution_name="dotmac-ticketing",
        )
