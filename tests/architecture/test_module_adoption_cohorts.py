"""A product cutover cohort is a closed programme set, not a loose wish list.

Starter owns which shared distributions belong to the deferred ERP authority
cutover.  The ERP assembly will later own exact pins, migration bindings and
production evidence.  Keeping those two facts separate lets modules mature
independently without letting one module quietly become a piecemeal authority
switch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = PROJECT_ROOT / "docs" / "module-adoption-cohorts.toml"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import module_catalog  # noqa: E402


def _erp_cohort() -> module_catalog.AdoptionCohort:
    cohorts = module_catalog.discover_adoption_cohorts(PROJECT_ROOT)
    matches = [cohort for cohort in cohorts if cohort.product == "dotmac_erp"]
    assert len(matches) == 1, "ERP must have exactly one active cutover cohort"
    return matches[0]


def test_erp_cohort_closes_over_every_stateful_optional_candidate() -> None:
    """A new ERP candidate cannot remain outside the coordinated cutover."""
    records = module_catalog.discover_modules(PROJECT_ROOT)
    expected = {
        record.distribution
        for record in records
        if record.classification == "optional-module"
        and record.persistence_plane not in {"stateless", "n/a"}
        and "dotmac_erp" in record.candidate_consumers
        and "dotmac_erp" not in record.contract_consumers
    }
    cohort = _erp_cohort()

    assert {member.distribution for member in cohort.members} == expected
    assert expected == {
        "dotmac-approvals",
        "dotmac-files",
        "dotmac-imports",
        "dotmac-numbering",
        "dotmac-template-studio",
        "dotmac-ticketing",
    }


def test_erp_cohort_requires_all_members_before_one_promotion() -> None:
    """The readiness threshold cannot legalise a partial authority switch."""
    cohort = _erp_cohort()

    assert cohort.status == "accumulating"
    assert cohort.cutover_policy == "single-production-promotion"
    assert cohort.partial_activation is False
    assert cohort.activation_threshold == len(cohort.members)


def test_every_erp_cohort_member_selects_the_tenant_plane() -> None:
    """ERP is a tenant-plane adopter; a platform selection is a wrong product."""
    cohort = _erp_cohort()

    assert cohort.members
    assert {member.plane for member in cohort.members} == {"tenant"}


def test_adjacent_contracts_are_explicitly_outside_the_erp_cohort() -> None:
    """Similar words must not pull a foreign authority into the ERP database."""
    cohort = _erp_cohort()

    assert {item.distribution for item in cohort.exclusions} == {
        "dotmac-application-directory",
        "dotmac-entitlement-allocation",
        "dotmac-release-catalog",
    }
    assert {item.distribution for item in cohort.retirements} == {"dotmac-auth-oidc"}


def test_cohort_guard_detects_a_missing_candidate(tmp_path: Path) -> None:
    """Sensitivity: deleting one plausible member must make discovery refuse."""
    text = REGISTRY.read_text(encoding="utf-8")
    stale = tmp_path / REGISTRY.name
    without_member = text.replace(
        '  { package = "dotmac-approvals", plane = "tenant" },\n',
        "",
        1,
    )
    stale.write_text(
        without_member.replace(
            "activation_threshold = 6", "activation_threshold = 5", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(module_catalog.CatalogError, match="candidate membership"):
        module_catalog.discover_adoption_cohorts(
            PROJECT_ROOT,
            registry_path=stale,
        )


def test_cohort_guard_detects_a_partial_threshold(tmp_path: Path) -> None:
    """Sensitivity: five-ready-of-six may not be called the batch cutover."""
    text = REGISTRY.read_text(encoding="utf-8")
    stale = tmp_path / REGISTRY.name
    stale.write_text(
        text.replace("activation_threshold = 6", "activation_threshold = 5", 1),
        encoding="utf-8",
    )

    with pytest.raises(module_catalog.CatalogError, match="activation_threshold"):
        module_catalog.discover_adoption_cohorts(
            PROJECT_ROOT,
            registry_path=stale,
        )


def test_cohort_guard_refuses_a_cutover_claim_without_product_evidence(
    tmp_path: Path,
) -> None:
    """Sensitivity: the registry cannot call the deferred cohort completed."""
    text = REGISTRY.read_text(encoding="utf-8")
    stale = tmp_path / REGISTRY.name
    stale.write_text(
        text.replace('status = "accumulating"', 'status = "completed"', 1),
        encoding="utf-8",
    )

    with pytest.raises(module_catalog.CatalogError, match="consumer evidence"):
        module_catalog.discover_adoption_cohorts(
            PROJECT_ROOT,
            registry_path=stale,
        )
