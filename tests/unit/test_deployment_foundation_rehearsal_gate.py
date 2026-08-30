"""The Lane 3 gate — and the two substitutions it must refuse.

On 2026-08-29 the exposure rehearsal's status document opened with
*"RUN. 14 of 16 items CLOSED"* while the table three lines below recorded four
items **partial** and one **n/a**. Fourteen was reached by counting those as
closed, and nothing could catch it: the summary and the evidence were the same
hand-maintained file.

Two changes close that, and this file holds both to account.

**The document is GENERATED from the receipt.** `render_status_document`
computes its counts from the rows it prints, so a header cannot contradict its
own table.

**Publication is gated on the receipt, not the document.** Only
`executed_passed` satisfies it — never `partial`, `not_applicable`,
`hand_measured`, `vacuous`, `incomplete`, or a missing row.

The two sensitivity tests at the bottom are the ones that matter most: a gate
nobody has watched REFUSE is a gate nobody should trust.
"""

from __future__ import annotations

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.rehearsal import (
    REQUIRED_ITEMS,
    RehearsalReceiptV1,
    RequirementResult,
    RequirementStatus,
    build_receipt,
    render_status_document,
    verify_publication,
)

REVISION = "c" * 40
DIGEST = "sha256:" + "a" * 64
ARTIFACT = "sha256:" + "b" * 64
FIXTURE = "sha256:" + "d" * 64


def _results(**overrides: RequirementStatus) -> list[RequirementResult]:
    rows = []
    for item in REQUIRED_ITEMS:
        status = overrides.get(item.code, RequirementStatus.EXECUTED_PASSED)
        rows.append(
            RequirementResult(
                code=item.code,
                status=status,
                detail=f"{item.title} — {status.value}",
                evidence=(f"evidence:{item.code}",),
            )
        )
    return rows


def _receipt(**overrides: RequirementStatus) -> RehearsalReceiptV1:
    return build_receipt(
        foundation_revision=REVISION,
        foundation_artifact_digest=ARTIFACT,
        authorization_run_id="pcp-run-9182",
        authorization_document_digest=DIGEST,
        descriptor_digest=DIGEST,
        execution_report_digest=DIGEST,
        fixture_digest=FIXTURE,
        controller_identity="SHA256:abc",
        target="203.0.113.10",
        lease_id="pcp-run-9182",
        probe_identity="198.51.100.7",
        started_at="2026-08-30T10:00:00+00:00",
        finished_at="2026-08-30T10:40:00+00:00",
        results=_results(**overrides),
    )


# ── the accepting control ───────────────────────────────────────────────────


def test_sixteen_executed_passed_satisfies_publication() -> None:
    """Without this, every refusal below could be a gate that refuses
    everything."""
    verify_publication(_receipt(), revision=REVISION)


def test_the_receipt_carries_all_sixteen_and_is_digest_bearing() -> None:
    receipt = _receipt()
    assert len(receipt.results) == len(REQUIRED_ITEMS) == 16
    assert receipt.sha256_digest().startswith("sha256:")
    assert receipt.lane == 3


# ── only executed_passed counts ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    [
        RequirementStatus.EXECUTED_FAILED,
        RequirementStatus.NOT_EXECUTED,
        RequirementStatus.HAND_MEASURED,
        RequirementStatus.BLOCKED,
        RequirementStatus.VACUOUS,
    ],
)
def test_no_status_other_than_executed_passed_can_publish(
    status: RequirementStatus,
) -> None:
    """`hand_measured` and `vacuous` are the two the old count folded into
    'closed'. A hand-driven step proves the OPERATOR can do it; a vacuous check
    observed nothing."""
    with pytest.raises(SpecError) as excinfo:
        verify_publication(_receipt(digest_equality=status), revision=REVISION)
    assert "digest_equality=" + status.value in str(excinfo.value)


def test_every_unsatisfied_item_is_named_not_merely_counted() -> None:
    receipt = _receipt(
        inert_v6_chain=RequirementStatus.BLOCKED,
        private_from_source=RequirementStatus.BLOCKED,
    )
    with pytest.raises(SpecError) as excinfo:
        verify_publication(receipt, revision=REVISION)
    message = str(excinfo.value)
    assert "2 of 16" in message
    assert "inert_v6_chain" in message
    assert "private_from_source" in message


def test_a_receipt_missing_an_item_is_refused_at_construction() -> None:
    """An absent item is not an implicit pass, and silence is exactly how the
    previous count went wrong."""
    partial = [r for r in _results() if r.code != "provoked_rollback"]
    with pytest.raises(SpecError, match="omits"):
        build_receipt(
            foundation_revision=REVISION,
            foundation_artifact_digest=ARTIFACT,
            authorization_run_id="pcp-run-9182",
            authorization_document_digest=DIGEST,
            descriptor_digest=DIGEST,
            execution_report_digest=DIGEST,
            fixture_digest=FIXTURE,
            controller_identity="SHA256:abc",
            target="203.0.113.10",
            lease_id="pcp-run-9182",
            probe_identity="198.51.100.7",
            started_at="2026-08-30T10:00:00+00:00",
            finished_at="2026-08-30T10:40:00+00:00",
            results=partial,
        )


def test_an_unknown_item_code_is_refused() -> None:
    """A gate that accepts unknown codes can be satisfied by renaming a
    failure."""
    with pytest.raises(SpecError, match="not one of the sixteen"):
        RequirementResult(
            code="definitely_fine",
            status=RequirementStatus.EXECUTED_PASSED,
            detail="x",
        )


# ── gate item 9, enforced at construction ───────────────────────────────────


def test_a_receipt_whose_three_terms_disagree_cannot_be_built() -> None:
    with pytest.raises(SpecError, match="do not agree"):
        build_receipt(
            foundation_revision=REVISION,
            foundation_artifact_digest=ARTIFACT,
            authorization_run_id="pcp-run-9182",
            authorization_document_digest="sha256:" + "e" * 64,
            descriptor_digest=DIGEST,
            execution_report_digest=DIGEST,
            fixture_digest=FIXTURE,
            controller_identity="SHA256:abc",
            target="203.0.113.10",
            lease_id="pcp-run-9182",
            probe_identity="198.51.100.7",
            started_at="2026-08-30T10:00:00+00:00",
            finished_at="2026-08-30T10:40:00+00:00",
            results=_results(),
        )


def test_a_receipt_for_another_revision_is_refused() -> None:
    with pytest.raises(SpecError, match=r"says\s+nothing about this one"):
        verify_publication(_receipt(), revision="d" * 40)


# ── the sensitivity proofs (ADR-0018) ───────────────────────────────────────


def test_removing_lane_3_evidence_makes_publication_fail() -> None:
    """Sensitivity proof: with no Lane 3 receipt there is nothing to verify, and
    the gate must refuse rather than treat absence as consent."""
    with pytest.raises(SpecError):
        RehearsalReceiptV1.from_json("{}")
    with pytest.raises(SpecError):
        RehearsalReceiptV1.from_json('{"schema": "RehearsalReceipt.v0"}')


def test_substituting_a_lane_2_receipt_is_refused_by_lane_number() -> None:
    """The second sensitivity proof, and the substitution most likely to be
    attempted: Lane 2 is green far more often, and it proves a different thing.

    Refused on the LANE, before any item is inspected — so a Lane 2 receipt
    whose rows all happen to say `executed_passed` still cannot publish.
    """
    lane2 = _receipt()
    content = dict(lane2.content)
    content["lane"] = 2
    forged = RehearsalReceiptV1(content=content)
    assert all(r.status.satisfies_publication for r in forged.results)
    with pytest.raises(SpecError) as excinfo:
        verify_publication(forged, revision=REVISION)
    assert "Lane 2" in str(excinfo.value)
    assert "requires Lane 3" in str(excinfo.value)


# ── the generated document cannot contradict itself ─────────────────────────


def test_the_status_document_counts_agree_with_its_own_rows() -> None:
    """The direct fix for the '14 of 16' header. The heading, the table and the
    tally are all derived from one list."""
    receipt = _receipt(
        inert_v6_chain=RequirementStatus.BLOCKED,
        firewall_reobservation=RequirementStatus.VACUOUS,
        apply_under_lock=RequirementStatus.HAND_MEASURED,
    )
    document = render_status_document(receipt)
    assert "13 of 16 executed and passed" in document
    assert document.count("**PASS**") == 13
    assert "`blocked`: 1" in document
    assert "`vacuous`: 1" in document
    assert "`hand_measured`: 1" in document
    assert "does not: 3 item(s) short" in document
    assert "GENERATED" in document


def test_a_fully_passing_document_says_so() -> None:
    document = render_status_document(_receipt())
    assert "16 of 16 executed and passed" in document
    assert "This receipt satisfies it." in document
