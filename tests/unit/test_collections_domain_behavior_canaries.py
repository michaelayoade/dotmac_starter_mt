"""RED-first pure behavior contract for the Collections extraction.

These tests port the product-proven semantics that do not require a database:
published policy replay, arbitrary ladders, exact arrangement membership,
explicit grace anchors, and owner-receipt replay/conflict handling.  The
package remains deliberately absent until the active Billing allocation
session releases the shared stateful-module surfaces.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from dotmac_collections.actions import (
    ActionApplied,
    ActionReceiptConflict,
    ActionRefused,
    FakeActionReceiptRecorder,
)
from dotmac_collections.arrangements import (
    ArrangementExposureV1,
    InstallmentDraftV1,
    PaymentArrangementDraftV1,
    arrangement_protects_exposure,
)
from dotmac_collections.grace import (
    GraceActive,
    GraceExpired,
    GraceGrantV1,
    evaluate_grace,
)
from dotmac_collections.policies import (
    AnchorUnavailable,
    LadderComplete,
    PolicyAnchorSetV1,
    PolicyPublicationV1,
    PolicyStepDraftV1,
    PolicyVersionDraftV1,
    StepDue,
    evaluate_policy_version,
    publish_policy_version,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money, currency

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
POLICY_VERSION_ID = UUID("50000000-0000-0000-0000-000000000001")
CASE_ID = UUID("40000000-0000-0000-0000-000000000001")
ARRANGEMENT_ID = UUID("70000000-0000-0000-0000-000000000001")
GRANT_ID = UUID("80000000-0000-0000-0000-000000000001")
REQUEST_ID = UUID("60000000-0000-0000-0000-000000000001")
AT = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
SCOPE = TenantScope(TENANT_ID)
NGN = currency("NGN")


def _step(ordinal: int, *, anchor: str = "exposure_at") -> PolicyStepDraftV1:
    return PolicyStepDraftV1(
        code=f"step_{ordinal}",
        ordinal=ordinal,
        offset=timedelta(days=ordinal - 1),
        offset_anchor=anchor,
        request_kind="notice" if ordinal == 1 else "action",
        action_code=None if ordinal == 1 else f"restrict_{ordinal}",
        purpose_code=f"notice_{ordinal}" if ordinal == 1 else None,
        effect_scope=None if ordinal == 1 else "service",
        receipt_required=True,
        retry_offsets=(),
    )


def _draft(step_count: int = 4) -> PolicyVersionDraftV1:
    return PolicyVersionDraftV1(
        policy_code="standard_arrears",
        reason_code="invoice_overdue",
        collection_timing="arrears",
        grace=None,
        steps=tuple(_step(ordinal) for ordinal in range(1, step_count + 1)),
    )


def _publication(
    *,
    version_id: UUID = POLICY_VERSION_ID,
    version: int = 1,
) -> PolicyPublicationV1:
    return PolicyPublicationV1(
        policy_version_id=version_id,
        version=version,
        effective_from=AT,
        actor_ref="staff:user-1",
        reason="initial production policy",
        published_at=AT,
    )


def _anchors(**overrides: object) -> PolicyAnchorSetV1:
    values: dict[str, object] = {
        "exposure_at": AT,
        "request_at": None,
        "accepted_notice_receipt_at": None,
    }
    values.update(overrides)
    return PolicyAnchorSetV1(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("step_count", [1, 4, 7])
def test_one_engine_evaluates_arbitrary_published_ladders(step_count: int) -> None:
    published = publish_policy_version(_draft(step_count), _publication())
    completed: tuple[str, ...] = ()

    for ordinal in range(1, step_count + 1):
        decision = evaluate_policy_version(
            published,
            anchors=_anchors(),
            completed_step_codes=completed,
            as_of=AT + timedelta(days=30),
        )
        assert decision == StepDue(
            step_code=f"step_{ordinal}",
            due_at=AT + timedelta(days=ordinal - 1),
        )
        completed += (decision.step_code,)

    assert evaluate_policy_version(
        published,
        anchors=_anchors(),
        completed_step_codes=completed,
        as_of=AT + timedelta(days=30),
    ) == LadderComplete(completed_step_codes=completed)


def test_publication_is_deterministic_immutable_and_does_not_rewrite_v1() -> None:
    draft = _draft()
    first = publish_policy_version(draft, _publication())
    replay = publish_policy_version(draft, _publication())
    successor_id = UUID("50000000-0000-0000-0000-000000000002")
    changed_draft = replace(
        draft,
        steps=(*draft.steps, _step(5)),
    )
    successor = publish_policy_version(
        changed_draft,
        _publication(version_id=successor_id, version=2),
    )

    assert first.version_fingerprint == replay.version_fingerprint
    assert successor.version_fingerprint != first.version_fingerprint
    assert first.policy_version_id == POLICY_VERSION_ID
    assert tuple(step.code for step in first.steps) == (
        "step_1",
        "step_2",
        "step_3",
        "step_4",
    )
    with pytest.raises(FrozenInstanceError):
        first.steps[0].code = "mutated"  # type: ignore[misc]
    with pytest.raises(ValueError, match="version"):
        publish_policy_version(draft, _publication(version=0))


def test_missing_declared_anchor_is_typed_non_actionable_evidence() -> None:
    policy = publish_policy_version(
        replace(_draft(1), steps=(_step(1, anchor="request_at"),)),
        _publication(),
    )

    assert evaluate_policy_version(
        policy,
        anchors=_anchors(),
        completed_step_codes=(),
        as_of=AT,
    ) == AnchorUnavailable(anchor_kind="request_at", step_code="step_1")


def test_policy_steps_own_notice_purpose_action_scope_and_retry_timing() -> None:
    notice = _step(1)
    action = _step(2)
    assert notice.purpose_code == "notice_1"
    assert notice.effect_scope is None
    assert action.purpose_code is None
    assert action.effect_scope == "service"

    with pytest.raises(ValueError, match="purpose_code"):
        replace(notice, purpose_code=None)
    with pytest.raises(ValueError, match="effect_scope"):
        replace(action, effect_scope=None)
    with pytest.raises(ValueError, match="retry offsets"):
        replace(action, retry_offsets=(timedelta(seconds=-1),))


def _exposure(
    exposure_ref: str,
    amount: str,
    *,
    source_version: int = 1,
) -> ArrangementExposureV1:
    return ArrangementExposureV1(
        source_owner="billing.receivables",
        exposure_ref=exposure_ref,
        source_version=source_version,
        position_fingerprint=f"sha256:{exposure_ref}:v{source_version}",
        subject_ref="subscriber:sub-1",
        service_ref="service:svc-1",
        admitted_amount=Money.of(Decimal(amount), NGN),
    )


def _installment(ordinal: int, amount: str) -> InstallmentDraftV1:
    return InstallmentDraftV1(
        ordinal=ordinal,
        amount=Money.of(Decimal(amount), NGN),
        due_at=AT + timedelta(days=ordinal * 7),
    )


def _arrangement(**overrides: object) -> PaymentArrangementDraftV1:
    values: dict[str, object] = {
        "arrangement_id": ARRANGEMENT_ID,
        "scope": SCOPE,
        "subject_ref": "subscriber:sub-1",
        "proposed_at": AT,
        "exposures": (
            _exposure("invoice:inv-1", "100.00", source_version=7),
            _exposure("invoice:inv-2", "50.00", source_version=3),
        ),
        "installments": (
            _installment(1, "75.00"),
            _installment(2, "75.00"),
        ),
    }
    values.update(overrides)
    return PaymentArrangementDraftV1(**values)  # type: ignore[arg-type]


def test_arrangement_protects_only_its_exact_exposure_membership() -> None:
    arrangement = _arrangement()

    assert arrangement_protects_exposure(
        arrangement,
        source_owner="billing.receivables",
        exposure_ref="invoice:inv-1",
    )
    assert not arrangement_protects_exposure(
        arrangement,
        source_owner="billing.receivables",
        exposure_ref="invoice:new-for-same-subject",
    )
    assert not arrangement_protects_exposure(
        arrangement,
        source_owner="other.receivables",
        exposure_ref="invoice:inv-1",
    )
    assert {field.name for field in fields(PaymentArrangementDraftV1)}.isdisjoint(
        {"account_id", "frequency", "installments_paid", "invoice_id"}
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"installments": (_installment(1, "149.99"),)},
        {"installments": (_installment(2, "150.00"),)},
        {
            "exposures": (
                _exposure("invoice:inv-1", "100.00"),
                _exposure("invoice:inv-1", "50.00"),
            )
        },
    ],
)
def test_arrangement_rejects_inexact_totals_implicit_order_and_duplicates(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _arrangement(**overrides)


def test_installment_rejects_a_naive_due_at_at_its_contract_boundary() -> None:
    with pytest.raises(ValueError, match="due_at must be timezone-aware"):
        replace(_installment(1, "75.00"), due_at=AT.replace(tzinfo=None))


def _grace(**overrides: object) -> GraceGrantV1:
    values: dict[str, object] = {
        "grant_id": GRANT_ID,
        "scope": SCOPE,
        "case_id": CASE_ID,
        "anchor_kind": "exposure_at",
        "anchor_at": AT,
        "duration": timedelta(days=2),
        "actor_ref": "policy:standard_arrears:v1",
        "reason_code": "published_policy_grace",
        "granted_at": AT,
    }
    values.update(overrides)
    return GraceGrantV1(**values)  # type: ignore[arg-type]


def test_grace_has_an_explicit_anchor_and_never_reads_a_clock() -> None:
    grant = _grace()
    assert evaluate_grace(grant, as_of=AT + timedelta(days=1)) == GraceActive(
        ends_at=AT + timedelta(days=2)
    )
    assert evaluate_grace(grant, as_of=AT + timedelta(days=2)) == GraceExpired(
        ended_at=AT + timedelta(days=2)
    )
    assert evaluate_grace(_grace(duration=timedelta(0)), as_of=AT) == GraceExpired(
        ended_at=AT
    )

    with pytest.raises(ValueError, match="anchor"):
        _grace(anchor_kind="")
    with pytest.raises((TypeError, ValueError), match="anchor"):
        _grace(anchor_at=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_grace(grant, as_of=AT.replace(tzinfo=None))


def _applied_receipt() -> ActionApplied:
    return ActionApplied(
        request_id=REQUEST_ID,
        owner_code="subscriptions.access",
        owner_receipt_id="receipt:applied:1",
        action_ref="restriction:1",
        applied_at=AT,
        owner_state_fingerprint="sha256:restricted",
    )


def test_owner_receipt_replay_is_idempotent_but_conflicting_evidence_fails() -> None:
    recorder = FakeActionReceiptRecorder()
    receipt = _applied_receipt()

    first = recorder.record(receipt)
    replay = recorder.record(receipt)
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.receipt_fingerprint == first.receipt_fingerprint
    assert recorder.receipts == (receipt,)

    with pytest.raises(ActionReceiptConflict):
        recorder.record(
            ActionRefused(
                request_id=REQUEST_ID,
                owner_code="subscriptions.access",
                owner_receipt_id="receipt:refused:2",
                reason_code="owner_state_changed",
                observed_at=AT,
                owner_state_fingerprint="sha256:different",
            )
        )
