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
LEDGER = REPO_ROOT / "docs/inventories/commercial-retirement-ledger.md"

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


# The retirement ledger GATES fifty-three rows of work on P11. It is prose, it
# is guarded by nothing else, and on 2026-08-28 its banner was found still
# reading "UNMET — not one row below may begin" fourteen days after P11 was met.
# Work stayed frozen on a premise that had stopped being true.
#
# This is written as a BICONDITIONAL between the dashboard's verdict and the
# ledger's banner rather than as a fixed assertion that P11 is met, so it keeps
# biting if the verdict ever moves again instead of pinning today's state.
LEDGER_MET_CLAIM = "ADR-0017 P11 is **MET**"
LEDGER_STALE_GATE_PHRASES = (
    "ADR-0017 P11 is **UNMET**",
    "Not one row below may begin.",
)


def _squash(text: str) -> str:
    """Collapse whitespace so a reflowed paragraph is still the same claim."""

    return " ".join(text.split())


def _ledger_gate_errors(status: str, ledger: str) -> tuple[str, ...]:
    """The ledger's banner must agree with the P11 dashboard's verdict."""

    status_says_met = _squash("**P11 is MET.**") in _squash(status)
    ledger_squashed = _squash(ledger)
    errors: list[str] = []

    if status_says_met:
        if _squash(LEDGER_MET_CLAIM) not in ledger_squashed:
            errors.append(
                "p11-adoption-status.md says P11 is MET but the retirement "
                f"ledger banner does not state {LEDGER_MET_CLAIM!r}"
            )
        for phrase in LEDGER_STALE_GATE_PHRASES:
            if _squash(phrase) in ledger_squashed:
                errors.append(
                    "the retirement ledger still carries the superseded gate "
                    f"text {phrase!r} while P11 is MET"
                )
    elif _squash(LEDGER_MET_CLAIM) in ledger_squashed:
        errors.append(
            "the retirement ledger claims P11 is MET but "
            "p11-adoption-status.md does not"
        )

    return tuple(errors)


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


def test_the_retirement_ledger_gate_agrees_with_the_p11_dashboard() -> None:
    """A document that GATES work may not restate the verdict and drift."""

    status = STATUS.read_text(encoding="utf-8")
    ledger = LEDGER.read_text(encoding="utf-8")

    assert _ledger_gate_errors(status, ledger) == ()


def test_the_ledger_gate_guard_is_sensitive_in_both_directions() -> None:
    """ADR-0018: the guard must fail for each way the two can disagree."""

    status = STATUS.read_text(encoding="utf-8")
    ledger = LEDGER.read_text(encoding="utf-8")

    # 1. The ledger drops the MET claim while the dashboard still carries it.
    assert _ledger_gate_errors(
        status, ledger.replace(LEDGER_MET_CLAIM, "removed by sensitivity proof")
    )

    # 2. The ledger regains the superseded gate text. This is the exact 2026-08-28
    #    defect: the banner froze fifty-three rows on a premise already false.
    for phrase in LEDGER_STALE_GATE_PHRASES:
        assert _ledger_gate_errors(status, ledger + f"\n\n{phrase}\n"), phrase

    # 3. The other direction: the ledger claims MET when the dashboard does not.
    unmet_status = status.replace("**P11 is MET.**", "**P11 is UNMET.**")
    assert _ledger_gate_errors(unmet_status, ledger)

    # 4. Reflowing a paragraph must not defeat the guard — the claim is the
    #    words, not their line breaks.
    reflowed = ledger.replace(LEDGER_MET_CLAIM, LEDGER_MET_CLAIM.replace(" ", "\n", 1))
    assert _ledger_gate_errors(status, reflowed) == ()


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
