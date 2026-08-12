"""Fact-level numbers must stay in sync, and must not outrun the detector.

Companion to `test_fleet_decomposition_matrix.py`, same division of labour: the
script measures, this file keeps the prose honest about what was measured. It
does not re-measure — ERP, CRM and Sub are absent from this repository's CI, and
scoring an absent repo as "no undeclared surface" would invert the finding.

The failure this file exists to prevent already happened once. An earlier
revision reported "711 tables have no declared owner", "97 duplicates correspond
to a declared fact" and "28 duplicate facts are undeclared". The detector proves
none of those: it counts `owns` strings without retaining their identities,
separately scans model imports, and never associates a fact with a table. The
overclaim survived review because nothing tested the *epistemic* status of the
numbers, only their arithmetic. These tests do both.
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORIES = PROJECT_ROOT / "docs" / "inventories"
DOC = INVENTORIES / "fleet-fact-level-decomposition.md"
REGISTRY = INVENTORIES / "fleet-fact-registry.json"

REPOS = ("dotmac_sub", "dotmac_erp", "dotmac_crm")

# The heuristic's limits, stated in the doc rather than assumed by the reader.
REQUIRED_CAVEATS = (
    "never associates a particular fact with a particular table",
    "neither direction is a reliable ownership bound",
    "a detection gap, not an ownership gap",
    "high-priority manual triage list",
)

# Claims the detector cannot support. Their exact earlier wording is banned.
WITHDRAWN_CLAIMS = (
    "tables across the fleet have no declared owner",
    "duplicate of a named fact",
    "duplicate of an unnamed fact",
    "unlinked is reliable",
    "declaration coverage",
)

# CRM's zero is intentional for one of three classes, not all three.
CRM_CLASSES = ("retirement_source", "extraction_source", "projection")

# The linkage that would make ownership provable, per Michael 2026-08-12.
TARGET_LINKAGE = (
    "fact_id",
    "owner_service_id",
    "role",
    "owned transitions",
)

PROVENANCE_FIELDS = (
    "measured_at",
    "revisions",
    "detector_schema_version",
    "normalization",
    "proves",
    "does_not_prove",
)


def _registry() -> dict:
    return json.loads(REGISTRY.read_text())


def _normalized_doc() -> str:
    """Lowercased, unwrapped, and stripped of markdown noise.

    Blockquote markers and emphasis are what broke the first version of these
    assertions: a caveat wrapped across a `>` continuation line normalizes to
    "... with a > particular table" and silently fails to match, which would have
    read as a missing caveat rather than a matching bug.
    """
    text = DOC.read_text().lower()
    text = re.sub(r"^\s*>\s?", " ", text, flags=re.MULTILINE)
    text = text.replace("*", "").replace("`", "")
    return " ".join(text.split())


def test_doc_and_registry_exist_and_are_linked() -> None:
    assert DOC.is_file()
    assert REGISTRY.is_file()
    assert (
        "fleet-fact-level-decomposition.md" in (INVENTORIES / "README.md").read_text()
    )


def test_declared_fact_counts_appear_in_the_doc() -> None:
    source = DOC.read_text()
    registry = _registry()
    for repo in REPOS:
        facts = registry["products"][repo]["declared_facts"]
        assert re.search(
            rf"\|\s*\*?\*?{facts:,}\*?\*?\s*\|", source
        ), f"{repo} declared_facts={facts:,} is not in the doc's table"


def test_detected_edge_counts_agree_with_the_registry() -> None:
    source = DOC.read_text()
    registry = _registry()
    for repo in REPOS:
        for key in (
            "tables_with_a_direct_import_edge",
            "tables_without_a_direct_import_edge",
        ):
            value = registry["products"][repo][key]
            assert re.search(
                rf"\|\s*{value}\s*\|", source
            ), f"{repo}.{key}={value} missing"


def test_triage_queue_count_and_membership_agree() -> None:
    source = DOC.read_text()
    duplicates = _registry()["duplicate_tables"]
    queue = duplicates["triage_queue"]

    assert len(queue) == duplicates["duplicates_without_any_direct_import_edge"]
    assert f"{len(queue)} have no detected direct import edge" in _normalized_doc()
    missing = [table for table in queue if f"`{table}`" not in source]
    assert missing == [], f"triage rows absent from the doc: {missing}"


def test_the_heuristic_caveats_survive() -> None:
    normalized = _normalized_doc()
    assert [phrase for phrase in REQUIRED_CAVEATS if phrase not in normalized] == []


def _states_without_withdrawing(text: str, claim: str, window: int = 200) -> bool:
    """Does `claim` appear anywhere that is not near a withdrawal marker?

    The first version of this check asked `claim in text and "withdrawn" not in
    text`. Since the doc always contains "withdrawn", the right-hand side was
    permanently false and the test could never fail — a vacuous guard, which is
    worse than none because it reads as coverage.
    """
    markers = ("withdrawn", "exceeded the detector", "earlier revision")
    for match in re.finditer(re.escape(claim), text):
        around = text[max(0, match.start() - window) : match.end() + window]
        if not any(marker in around for marker in markers):
            return True
    return False


def test_withdrawn_ownership_claims_do_not_come_back() -> None:
    """The doc may state a withdrawn claim only while withdrawing it."""
    normalized = _normalized_doc()
    assert "that claim exceeded the detector and is withdrawn" in normalized
    reintroduced = [
        claim
        for claim in WITHDRAWN_CLAIMS
        if _states_without_withdrawing(normalized, claim)
    ]
    assert reintroduced == []


def test_withdrawal_detector_is_sensitive() -> None:
    """A reintroduced claim far from any withdrawal must fail."""
    assert _states_without_withdrawing(
        "the fleet has 711 tables with no declared owner, so we can proceed",
        "tables with no declared owner",
    )
    assert not _states_without_withdrawing(
        'an earlier revision said "tables with no declared owner"; it is withdrawn',
        "tables with no declared owner",
    )


def test_registry_keys_do_not_assert_ownership() -> None:
    """Key names are the claim most likely to be quoted without the caveat."""
    keys = set(_registry()["products"]["dotmac_sub"]) | set(
        _registry()["duplicate_tables"]
    )
    banned = {
        "tables_with_a_named_owner",
        "tables_undeclared",
        "undeclared_tables",
        "duplicate_of_a_named_fact",
        "duplicate_of_an_unnamed_fact",
        "unowned_duplicates",
    }
    assert keys & banned == set()


def test_registry_carries_its_own_provenance() -> None:
    provenance = _registry()["provenance"]
    assert [field for field in PROVENANCE_FIELDS if field not in provenance] == []
    assert provenance["detector_schema_version"] >= 1
    assert sorted(provenance["revisions"]) == sorted(REPOS)
    assert all(provenance["revisions"][repo] for repo in REPOS)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", provenance["measured_at"])
    assert provenance["does_not_prove"], "the artifact must carry its own limits"


def test_crm_zero_is_classified_rather_than_blanket_intentional() -> None:
    registry = _registry()
    normalized = _normalized_doc()

    assert registry["products"]["dotmac_crm"]["declared_facts"] == 0
    assert [cls for cls in CRM_CLASSES if cls not in normalized] == []
    assert "not uniformly intentional" in normalized
    # Extraction sources need characterization; retirement sources do not.
    assert "ownership characterization is a prerequisite" in normalized


def test_doc_states_the_target_linkage_contract() -> None:
    normalized = _normalized_doc()
    assert [field for field in TARGET_LINKAGE if field not in normalized] == []
    assert "authoritative" in normalized and "observation" in normalized


def test_registry_covers_all_three_source_monoliths() -> None:
    assert sorted(_registry()["products_measured"]) == sorted(REPOS)
