"""Keep ADR-0017's production-lineage exit evidence internally consistent.

P11 is not satisfied by a package pin, a rehearsal, or a prose-only status
change.  The accepted evidence is one immutable Vendor production deployment,
and the three adopted module dossiers are the checked-in witnesses that the
deployed assembly runs module-owned authority.  This guard makes the central
ADR and dashboard agree with those witnesses.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR = REPO_ROOT / "docs/adr/0017-adoption-is-the-scarce-resource.md"
STATUS = REPO_ROOT / "docs/inventories/p11-adoption-status.md"

VENDOR_REVISION = "f8f8c3fd636e663e4a17275c19e82fc1667aa52a"
DEPLOY_RUN = "32022599873"
IMAGE_DIGEST = "sha256:56ec553139c449dc7da46a8873b3c03e95a61e43c970cd1675e28a202b2991cc"

DOSSIERS = {
    name: REPO_ROOT / f"packages/{name}/EXTRACTION.toml"
    for name in (
        "dotmac-release-catalog",
        "dotmac-entitlement-allocation",
        "dotmac-approvals",
    )
}

COMMON_ADOPTION_EVIDENCE = {
    f"dotmac_vendor_control_plane:main@{VENDOR_REVISION}",
    f"dotmac_vendor_control_plane:production-deploy#{DEPLOY_RUN}",
    ("ghcr.io/michaelayoade/dotmac_vendor_control_plane@" f"{IMAGE_DIGEST}"),
}

ADR_CLAIMS = (
    "## Amendment, 2026-08-17: P11 is met by Vendor production",
    "**P11 is MET.**",
    VENDOR_REVISION,
    DEPLOY_RUN,
    IMAGE_DIGEST,
)

STATUS_CLAIMS = (
    "**P11 is MET.**",
    VENDOR_REVISION,
    DEPLOY_RUN,
    IMAGE_DIGEST,
    "`dotmac-kernel==0.1.0a61`",
    "`dotmac-release-catalog==0.1.0a4`",
    "`dotmac-entitlement-allocation==0.1.0a4`",
    "`dotmac-approvals==0.1.0a4`",
    "composed `heads`",
    "Sub tenant-plane / RLS proof remains a separate adoption track",
)


def _load_dossiers() -> dict[str, dict[str, Any]]:
    return {
        name: tomllib.loads(path.read_text(encoding="utf-8"))
        for name, path in DOSSIERS.items()
    }


def _evidence_errors(
    adr: str,
    status: str,
    dossiers: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    errors: list[str] = []

    for claim in ADR_CLAIMS:
        if claim not in adr:
            errors.append(f"ADR missing {claim}")
    for claim in STATUS_CLAIMS:
        if claim not in status:
            errors.append(f"status missing {claim}")

    for name, dossier in dossiers.items():
        if dossier.get("status") != "adopted":
            errors.append(f"{name} is not adopted")
        if dossier.get("contract_consumers") != ["dotmac_vendor_control_plane"]:
            errors.append(f"{name} does not name the Vendor production consumer")
        evidence = set(dossier.get("adoption_evidence", ()))
        missing = COMMON_ADOPTION_EVIDENCE - evidence
        if missing:
            errors.append(f"{name} is missing common evidence {sorted(missing)}")

    return tuple(errors)


def test_p11_is_backed_by_one_immutable_production_lineage_record() -> None:
    adr = ADR.read_text(encoding="utf-8")
    status = STATUS.read_text(encoding="utf-8")

    assert _evidence_errors(adr, status, _load_dossiers()) == ()


def test_p11_evidence_guard_is_sensitive_to_every_required_claim() -> None:
    """ADR-0018: prove the governance detector fails for planted drift."""
    adr = ADR.read_text(encoding="utf-8")
    status = STATUS.read_text(encoding="utf-8")
    dossiers = _load_dossiers()

    for claim in ADR_CLAIMS:
        violated = adr.replace(claim, "removed by sensitivity proof")
        assert _evidence_errors(violated, status, dossiers), claim

    for claim in STATUS_CLAIMS:
        violated = status.replace(claim, "removed by sensitivity proof")
        assert _evidence_errors(adr, violated, dossiers), claim

    for name in DOSSIERS:
        not_adopted = copy.deepcopy(dossiers)
        not_adopted[name]["status"] = "audit-complete"
        assert _evidence_errors(adr, status, not_adopted)

        no_consumer = copy.deepcopy(dossiers)
        no_consumer[name]["contract_consumers"] = []
        assert _evidence_errors(adr, status, no_consumer)

        missing_run = copy.deepcopy(dossiers)
        missing_run[name]["adoption_evidence"].remove(
            f"dotmac_vendor_control_plane:production-deploy#{DEPLOY_RUN}"
        )
        assert _evidence_errors(adr, status, missing_run)
