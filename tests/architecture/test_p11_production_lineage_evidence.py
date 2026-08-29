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

#: The three witnesses, as AdoptionEvidenceV1 rows rather than as free-text
#: strings.  Each is `(kind, coordinate)`, and the coordinate is what a later
#: verifier resolves: the deploy run's id, the image's content address, and — on
#: the `pinned_at` row — nothing beyond the commit itself, because the pin's
#: value differs per module and is checked in each module's own suite.
#:
#: Every row must ALSO carry `commit == VENDOR_REVISION`.  That is the field the
#: free-text strings could not express: a bare `production-deploy#<id>` says
#: which run happened and not which tree it ran, so it stayed silently true
#: while the tree beneath it moved.
COMMON_ADOPTION_EVIDENCE = {
    ("pinned_at", None),
    ("deploy_run", DEPLOY_RUN),
    ("image_digest", IMAGE_DIGEST),
}


def _witnesses(dossier: dict[str, Any]) -> set[tuple[str, str | None]]:
    """The witness set a dossier actually carries, at VENDOR_REVISION only."""
    found: set[tuple[str, str | None]] = set()
    for row in dossier.get("adoption_evidence", ()):
        if not isinstance(row, dict):
            continue
        if row.get("commit") != VENDOR_REVISION:
            continue
        if row.get("repository") != "dotmac_vendor_control_plane":
            continue
        kind = row.get("kind")
        if kind == "pinned_at":
            found.add(("pinned_at", None))
        elif kind == "deploy_run":
            found.add(("deploy_run", row.get("run_id")))
        elif kind == "image_digest":
            found.add(("image_digest", row.get("digest")))
    return found


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
        missing = COMMON_ADOPTION_EVIDENCE - _witnesses(dossier)
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
        missing_run[name]["adoption_evidence"] = [
            row
            for row in missing_run[name]["adoption_evidence"]
            if row.get("kind") != "deploy_run"
        ]
        assert _evidence_errors(adr, status, missing_run)

        # The defect the free-text shape could not express at all: the deploy
        # run kept, but re-pointed at a DIFFERENT tree. Under the old string set
        # this was invisible, because the run id carried no commit.
        moved_tree = copy.deepcopy(dossiers)
        for row in moved_tree[name]["adoption_evidence"]:
            if row.get("kind") == "deploy_run":
                row["commit"] = "0" * 40
        assert _evidence_errors(adr, status, moved_tree)

        wrong_digest = copy.deepcopy(dossiers)
        for row in wrong_digest[name]["adoption_evidence"]:
            if row.get("kind") == "image_digest":
                row["digest"] = "sha256:" + "0" * 64
        assert _evidence_errors(adr, status, wrong_digest)
