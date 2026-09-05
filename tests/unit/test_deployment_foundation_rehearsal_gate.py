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
    require_rehearsed_artifact,
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


# ── the receipt is about BYTES, not only about a revision ──────────────────
#
# `verify_publication` compares the LANE 3 RUNNER revision with the RELEASE
# revision and every item's status. Until this commit nothing compared the
# receipt with the ARTIFACT, so a rehearsal of one candidate satisfied a
# publication of another whenever both ran at the same commit — and
# `candidate_version` is a dispatch input on `exposure-rehearsal.yml`
# specifically so that two candidates CAN be rehearsed from one SHA.


def test_a_receipt_for_these_bytes_satisfies_the_artifact_binding() -> None:
    """The accepting control, without which every refusal below could belong to
    a check that refuses everything.

    Fails before the change: `require_rehearsed_artifact` did not exist, so the
    import at the top of this module raises `ImportError`.
    """
    require_rehearsed_artifact(_receipt(), artifact_digest=ARTIFACT)


def test_a_receipt_for_OTHER_bytes_does_not_satisfy_publication() -> None:
    """THE substitution. Same revision, same sixteen passes, different wheel.

    Note what does NOT catch it: `verify_publication` accepts this receipt
    completely, because the revision and the statuses are all correct. The two
    checks are about different questions, which is why the second one had to
    exist rather than be folded into the first.
    """
    other = "sha256:" + "e" * 64
    verify_publication(_receipt(), revision=REVISION)
    with pytest.raises(SpecError) as exc:
        require_rehearsed_artifact(_receipt(), artifact_digest=other)
    assert ARTIFACT in str(exc.value) and other in str(exc.value)


def test_the_artifact_binding_reads_the_digest_and_not_a_prefix() -> None:
    """The near-miss: a digest that AGREES on a prefix and names other bytes.

    A comparison written as `startswith` or over a truncated value would credit
    this, and a truncation is exactly what a hand-copied digest produces.
    """
    truncated = ARTIFACT[:-1] + "c"
    with pytest.raises(SpecError):
        require_rehearsed_artifact(_receipt(), artifact_digest=truncated)


def test_the_artifact_binding_refuses_a_value_that_is_not_a_DIGEST() -> None:
    """An unparseable comparand refuses rather than comparing unequal.

    "Not a digest" and "a digest for other bytes" are different facts, and a
    check that reported the second for the first would send an operator looking
    for a wheel mix-up when the actual defect is a workflow expression that
    resolved to an empty string.
    """
    for value in ("", "not-a-digest", "sha256:" + "b" * 63):
        with pytest.raises(SpecError):
            require_rehearsed_artifact(_receipt(), artifact_digest=value)


def test_the_same_digest_spelled_without_its_algorithm_is_the_same_digest() -> None:
    """The near-miss in the OTHER direction, and it would bite in practice.

    `Digest.parse` normalises a bare 64-character hex value to `sha256:<hex>`,
    so an unprefixed spelling names the same bytes. A comparison written over
    raw strings would refuse this and report "a rehearsal of other bytes" about
    a receipt that is exactly right — the most confusing possible failure, since
    every digit matches.
    """
    require_rehearsed_artifact(_receipt(), artifact_digest="b" * 64)


def test_the_receipt_exposes_the_artifact_it_rehearsed() -> None:
    """`build_receipt` has always WRITTEN this field and nothing could ask for
    it, which is how publication came to compare the revision and never the
    bytes. The accessor reads a field every v1 receipt already carries, so no
    document changes shape."""
    assert _receipt().foundation_artifact_digest == ARTIFACT


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
