"""The DESIGN's own evidence, kept honest — not the contract's implementation.

`docs/superpowers/specs/2026-09-05-foundation-concern-contribution-contract.md`
is a proposal. It carries golden byte fixtures, and a golden fixture nobody
re-derives is a decoration — which is precisely the inert shape the design it
supports is written about. So the fixture is re-derived here from the SHIPPED
canonicalizer.

**This file guards the fixture. It does not implement the contract.** There is
no build tool here, no discovery, no module declaration and no entry-point
group. When item 7 lands, its tests are elsewhere and these stay.

The document itself is asserted only where a claim in it is checkable against
this repository. Prose is not tested — a detector that matched its own docstring
is a defect this package has already shipped once — but a NUMBER in a review
package that no longer holds is a false statement in the artifact a decision
gets made from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dotmac_deployment_foundation.application_profile import (
    APPLICATION_PROFILE_SCHEMA,
    FoundationConcern,
    canonical_profile_bytes,
    profile_digest,
)
from dotmac_deployment_foundation.errors import SpecError

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "docs" / "inventories" / "foundation-profile-golden.json"
DESIGN = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-09-05-foundation-concern-contribution-contract.md"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_golden_digest_is_re_derived_from_the_shipped_canonicalizer() -> None:
    """The fixture's whole value is that a second implementation must reproduce
    it byte for byte. If the canonicalizer moves and the fixture does not, the
    review package is quoting a digest nobody can produce."""
    fixture = _fixture()
    document = fixture["document"]
    assert profile_digest(document) == fixture["profile_digest"]
    assert len(canonical_profile_bytes(document)) == fixture["canonical_length"]


def test_the_design_document_quotes_the_digest_the_fixture_holds() -> None:
    """A number in a review package that no longer holds is a false statement in
    the artifact a decision gets made from."""
    text = DESIGN.read_text(encoding="utf-8")
    fixture = _fixture()
    assert fixture["profile_digest"] in text
    assert str(fixture["canonical_length"]) in text


def test_the_fixture_exercises_BOTH_slot_kinds() -> None:
    """Non-vacuity of the fixture itself.

    A golden profile of thirteen identical bindings would pin the canonical form
    and prove nothing about the slot value the design turns on. The
    absent-proven slot became representable on 2026-09-05 and is the reason this
    fixture exists rather than an older one.
    """
    concerns = _fixture()["document"]["concerns"]
    states = {slot["state"] for slot in concerns.values()}
    assert states == {"bound", "absent_proven"}, states
    assert concerns["integration"]["state"] == "absent_proven"
    # And the proof binds the WHEEL, not a self-referential image.
    assert "artifact_digest" in concerns["integration"]
    assert "image_digest" not in concerns["integration"]


def test_the_fixture_is_a_COMPLETE_profile() -> None:
    """Thirteen slots, every one filled. A fixture that omitted one would pin the
    canonical form of a document the profile type refuses to construct."""
    concerns = _fixture()["document"]["concerns"]
    assert set(concerns) == {concern.value for concern in FoundationConcern}
    assert len(concerns) == 13
    assert _fixture()["document"]["schema"] == APPLICATION_PROFILE_SCHEMA


def test_the_canonicalizer_still_refuses_a_wrapper() -> None:
    """The design cites this rule rather than restating it, so it is checked
    where it is cited. Hashing a wrapper that merely CONTAINS a profile is how
    two parties compute permanently unequal values while both look correct."""
    wrapped = {"profile": _fixture()["document"]}
    with pytest.raises(SpecError):
        canonical_profile_bytes(wrapped)


def test_the_derivation_guard_would_notice_a_drifted_fixture() -> None:
    """Sensitivity. The assertions above pass over a fixture that is already
    correct, which says nothing about their ability to fail — and a golden
    fixture is the single easiest thing in a repository to leave behind."""
    document = _fixture()["document"]
    drifted = json.loads(json.dumps(document))
    drifted["application"] = "someone-elses-app"
    assert profile_digest(drifted) != _fixture()["profile_digest"]
    # A near miss: re-serialising the SAME document through a different key
    # order must NOT change the digest, or the canonical form is not canonical.
    reordered = dict(reversed(list(document.items())))
    assert profile_digest(reordered) == _fixture()["profile_digest"]


def test_the_design_is_marked_PROPOSED_and_allocates_no_ADR_number() -> None:
    """It becomes authoritative by an accepted ADR, not by being merged.

    `docs/superpowers/specs/` is defined by this repository as non-authoritative
    intent; a proposal that read as a decision would be a decision nobody took.
    """
    text = DESIGN.read_text(encoding="utf-8")
    assert "CONTRACT DESIGN ONLY" in text
    assert "not implemented" in text
    # Michael's standing scope line: this is neither of the two things a reader
    # is most likely to mistake it for.
    assert (
        "not** profile\nadmission" in text
        or "not profile admission" in text.replace("**", "").replace("\n", " ")
    )
    assert not list(
        (ROOT / "docs" / "adr").glob("*concern-contribution*")
    ), "the design allocated an ADR number while still under review"


# ── the parity map is a RATCHET, in both directions ────────────────────────

PARITY = ROOT / "docs" / "inventories" / "foundation-admission-parity-map.json"


def _parity() -> dict:
    return json.loads(PARITY.read_text(encoding="utf-8"))


def test_the_parity_map_holds_its_counts_in_both_directions() -> None:
    """A one-directional map lets either half slip: a shrinking count reads as
    cleanup and a growing one as progress. Both totals are fixed, so a case
    cannot appear or vanish as a side effect of some other change."""
    parity = _parity()
    assert parity["legacy_total"] == 53
    assert sum(parity["legacy_breakdown"].values()) == parity["legacy_total"]
    assert len(parity["added"]) == parity["added_total"]


def test_the_added_cases_are_the_ones_the_dialect_could_not_STATE() -> None:
    """Reproducing 53 and adding none would mean the generic path had reproduced
    the dialect rather than replaced it. Each added case records WHY the dialect
    has no counterpart, so a row cannot be added as a round number."""
    added = _parity()["added"]
    cases = {row["case"] for row in added}
    assert cases == {
        "all_negative",
        "answers_everything",
        "foreign_inventory",
        "nonce_only",
        "retirement_round_trip",
        "unknown_key",
        "uninjected",
        "wrong_assembly",
        "wrong_site",
    }
    for row in added:
        assert row["requires"].strip()
        assert row["absent_from_dialect_because"].strip()


def test_the_map_does_not_RESTATE_platforms_rows() -> None:
    """A copy of another repository's inventory is a second authority that
    drifts. The map is accountable to the count and names who owns the rows."""
    parity = _parity()
    assert parity["legacy_rows_supplied_here"] is False
    assert "d7b8ca6a" in parity["legacy_rows_owned_by"]


def test_the_design_and_the_map_agree_on_the_added_cases() -> None:
    """A number in a review package that no longer holds is a false statement in
    the artifact a decision gets made from — the same rule as the golden digest,
    applied to the case list."""
    text = DESIGN.read_text(encoding="utf-8")
    for row in _parity()["added"]:
        assert row["case"] in text, row["case"]
    assert str(_parity()["legacy_total"]) in text
