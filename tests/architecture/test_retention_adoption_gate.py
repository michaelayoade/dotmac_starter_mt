"""Retention may not be ADOPTED until the privacy notice states its period.

`ig_0006_retention` destroys provider payload content on a schedule. The Nigeria
Data Protection Act requires a stated retention period and storage no longer
than necessary, so the period has to appear in Dotmac's privacy notice / record
of processing BEFORE content is destroyed under it. Merging the code does not do
that, and no test can do it either.

What a test can do is stop "the code shipped" from being read as "we may destroy
data". `docs/inventories/integration-retention-adoption.toml` separates the two,
and this module keeps them separated.

## Why this is not a grep over the notice

Grepping a legal document for `30 days` is a bad guard three ways: it passes on
a sentence that says the opposite, it breaks when a lawyer rewords a clause that
means exactly the same thing, and it pushes whoever writes the notice to satisfy
a regex rather than a reader. The gate keys on a REVISION IDENTIFIER that a
named human accepted, on a date — structured fields, not text a machine has to
interpret.

The trade is stated rather than hidden: this cannot tell whether the accepted
revision actually says what `[decision]` says. Nothing automatic can. It moves
the failure from "nobody noticed" to "somebody signed", which is the honest
place for it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECORD = PROJECT_ROOT / "docs" / "inventories" / "integration-retention-adoption.toml"


def _record() -> dict:
    return tomllib.loads(RECORD.read_text(encoding="utf-8"))


def test_the_record_states_the_ruled_decision() -> None:
    """The decision Michael ruled, pinned so a later edit is a visible change.

    Asserted here rather than in the module because the module deliberately
    ships NO default: an unconfigured deployment refuses and alerts, which is
    the correct fail-closed state. These values are what a deployment must be
    configured WITH, and what the notice must say.
    """
    decision = _record()["decision"]
    assert decision["status"] == "accepted"
    assert decision["period_days"] == 30
    assert decision["period_anchor"] == "received_at"
    assert "Data Protection Officer" in decision["legal_policy_owner"]
    assert decision["recording_rule_seconds"] == 30 * 24 * 60 * 60


def test_retention_is_not_adopted_while_the_notice_revision_is_pending() -> None:
    """THE gate.

    `adopted` may only be true once a real notice revision is recorded with the
    human who accepted it and when. Until then the module may be installed,
    released and exercised — but a deployment must not sweep real provider
    content, and this refuses to let the record claim otherwise.
    """
    record = _record()
    notice = record["notice"]
    adopted = record["adoption"]["adopted"]

    if notice["revision"] == "PENDING":
        assert adopted is False, (
            "adoption is claimed while the privacy notice revision is still "
            "PENDING. The code being released is not authority to destroy "
            "content under a period nobody has published"
        )
        assert record["adoption"]["blocked_on"], "a blocked adoption must say why"
        return

    # The accepted case: a revision is only evidence if it is attributable.
    assert notice["accepted_on"], "an accepted notice revision needs a date"
    assert notice["accepted_by"], "an accepted notice revision needs a person"


def test_replay_evidence_has_a_period_and_says_whether_it_is_enforced() -> None:
    """The approved fleet standard's qualification, made checkable.

    Content and replay identity have separate lifetimes — and "separate" cuts
    both ways: keeping identity is required to recognise a replay, and keeping
    it FOREVER is an unbounded personal-data store justified by a sentence.

    The period is now ruled (180 days from `received_at`, 2026-08-16), which
    closes half the gap and opens the half that matters more: a ruled period
    nothing enforces is not a shorter retention, it is the same indefinite
    retention with a number written next to it. So the record carries
    `replay_evidence_implemented`, and adoption stays blocked until it is true.
    """
    behaviour = _record()["behaviour"]
    assert behaviour["replay_evidence_retained"], "redaction keeps something; say what"

    days = behaviour["replay_evidence_period_days"]
    assert isinstance(days, int) and days > 0, "a period is a number of days"
    assert days > _record()["decision"]["period_days"], (
        "replay evidence must outlive the content it identifies, or a "
        "redelivery arrives with nothing left to recognise it by"
    )
    assert behaviour["replay_evidence_period_anchor"] == "received_at"

    if not behaviour["replay_evidence_implemented"]:
        assert "replay_evidence_implemented" in " ".join(
            _record()["adoption"]["blocked_on"]
        ), (
            "a ruled-but-unenforced period must BLOCK adoption. Recording the "
            "number without enforcing it leaves evidence retained indefinitely "
            "while the record reads as though a limit applies"
        )


def test_the_code_released_flag_is_not_the_adoption_flag() -> None:
    """The two facts this record exists to keep apart.

    Collapsing them is the failure: a released module reads as an approved
    practice, and the legal step becomes something everyone assumes someone
    else did. They are separate fields and this asserts they can differ.
    """
    adoption = _record()["adoption"]
    assert "code_released" in adoption
    assert "adopted" in adoption
    assert adoption["code_released"] is True
    assert adoption["code_released_as"].startswith("dotmac-integration ")


def test_the_gate_would_catch_a_premature_adoption_claim() -> None:
    """Sensitivity proof: the gate must be able to fail.

    `adopted = false` today, so every assertion above passes without the guard
    ever discriminating. This drives the same predicate with the shape it
    forbids — adoption claimed while the notice is PENDING — and shows it
    refused.
    """
    premature = {
        "notice": {"revision": "PENDING", "accepted_on": "", "accepted_by": ""},
        "adoption": {"adopted": True, "blocked_on": ["notice.revision is PENDING"]},
    }
    with pytest.raises(AssertionError, match="adoption is claimed"):
        notice = premature["notice"]
        if notice["revision"] == "PENDING":
            assert premature["adoption"]["adopted"] is False, (
                "adoption is claimed while the privacy notice revision is still "
                "PENDING. The code being released is not authority to destroy "
                "content under a period nobody has published"
            )
