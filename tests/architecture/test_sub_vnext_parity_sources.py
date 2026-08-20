"""The Sub vNext parity inventory is a closed ownership decision.

The inventory exists to prevent a package-name checklist from becoming nine
new duplicate owners.  The checker is pure over a parsed TOML document so the
last test can prove that a renamed owner or a second Fleet Control package is
detected (ADR-0018).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DISPOSITIONS = (
    REPO_ROOT / "docs/inventories/sub-vnext-parity-dispositions.toml"
)

EXPECTED = {
    "ai_operations": ("dotmac-ai-operations", "retain"),
    "compliance_reporting": ("dotmac-compliance-reporting", "retain"),
    "fleet_control": ("dotmac-deployment-control", "adopt-existing"),
    "forms": ("dotmac-forms", "retain"),
    "platform_health": ("dotmac-platform-health", "retain"),
    "referrals": ("dotmac-referrals", "retain"),
    "remote_access": ("dotmac-remote-access", "retain"),
    "reseller_management": ("dotmac-reseller-management", "retain"),
    "support_access": ("dotmac-support-access", "retain"),
    "workflow_runtime": ("dotmac-workflow-runtime", "retain"),
}

EXPECTED_SOURCE_MODES = {
    "dotmac-ai-operations": "product-first",
    "dotmac-compliance-reporting": "product-first",
    "dotmac-forms": "product-first",
    "dotmac-platform-health": "greenfield-after-inventory",
    "dotmac-referrals": "product-first",
    "dotmac-remote-access": "greenfield-after-inventory",
    "dotmac-reseller-management": "product-first",
    "dotmac-support-access": "greenfield-after-inventory",
    "dotmac-workflow-runtime": "product-first",
}


def disposition_problems(document: dict[str, Any]) -> list[str]:
    """Return every closed-cohort ownership violation in *document*."""
    problems: list[str] = []
    rows = document.get("capability", [])
    if not isinstance(rows, list):
        return ["capability must be an array of tables"]

    actual: dict[str, tuple[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            problems.append("every capability row must be a table")
            continue
        code = row.get("code")
        distribution = row.get("distribution")
        disposition = row.get("disposition")
        values = (code, distribution, disposition)
        if not all(isinstance(value, str) for value in values):
            problems.append("capability code/distribution/disposition must be strings")
            continue
        if code in actual:
            problems.append(f"duplicate capability row: {code}")
            continue
        actual[code] = (distribution, disposition)

    if actual != EXPECTED:
        problems.append(f"closed disposition map changed: {actual!r}")
    return problems


def test_sub_vnext_capabilities_have_exactly_one_adjudicated_owner() -> None:
    document = tomllib.loads(DISPOSITIONS.read_text(encoding="utf-8"))
    assert disposition_problems(document) == []


def test_retained_capabilities_have_audit_complete_dossiers() -> None:
    for distribution, expected_mode in EXPECTED_SOURCE_MODES.items():
        dossier_path = REPO_ROOT / "packages" / distribution / "EXTRACTION.toml"
        assert dossier_path.is_file(), f"{distribution} has no extraction dossier"
        dossier = tomllib.loads(dossier_path.read_text(encoding="utf-8"))
        assert dossier["package"] == distribution
        assert dossier["classification"] == "optional-module"
        assert dossier["status"] == "audit-complete"
        assert dossier["source_mode"] == expected_mode
        assert dossier["contract_consumers"] == []
        assert dossier["candidate_consumers"]
        assert dossier["inventory_evidence"] == [
            "docs/inventories/sub-vnext-parity-sources.md",
            "docs/inventories/sub-vnext-parity-dispositions.toml",
            "docs/adr/0034-sub-vnext-parity-capabilities-have-"
            "narrow-independent-owners.md",
        ]


def test_fleet_control_reuses_deployment_control() -> None:
    assert (REPO_ROOT / "packages/dotmac-deployment-control/pyproject.toml").is_file()
    assert not (REPO_ROOT / "packages/dotmac-fleet-control").exists()


def test_disposition_checker_detects_a_second_fleet_owner() -> None:
    document = tomllib.loads(DISPOSITIONS.read_text(encoding="utf-8"))
    rows = document["capability"]
    for row in rows:
        if row["code"] == "fleet_control":
            row["distribution"] = "dotmac-fleet-control"
            break
    assert disposition_problems(document) == [
        "closed disposition map changed: "
        + repr(
            {
                row["code"]: (row["distribution"], row["disposition"])
                for row in rows
            }
        )
    ]
