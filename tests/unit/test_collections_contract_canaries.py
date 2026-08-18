"""Canary-first contract for the product-first Collections extraction.

These tests intentionally precede ``packages/dotmac-collections``.  Their
initial ModuleNotFoundError is the RED evidence; implementation starts only
after the active Billing worktree releases the shared namespace and package
metadata surfaces.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from dotmac_collections.actions import (
    ActionApplied,
    ActionDeferred,
    ActionFailed,
    ActionRefused,
    CollectionActionRequestedV1,
)
from dotmac_collections.contracts import (
    AssessCollectionExposureV1,
    TriggerProvenanceV1,
)
from dotmac_collections.notices import (
    CollectionNoticeRequestedV1,
    NoticeAccepted,
    NoticeFailed,
    NoticeSuppressed,
    NoticeUnavailable,
)
from dotmac_collections.policies import (
    GraceRuleV1,
    PolicyStepDraftV1,
    PolicyVersionDraftV1,
)
from dotmac_collections.receivables import (
    FakeReceivablesReader,
    PositionAuthorityMismatch,
    PositionReadOk,
    PositionUnavailable,
    PositionUnknown,
    ReceivablePositionV1,
    ReceivablesReadCallV1,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money, MoneyError, currency

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
COMMAND_ID = UUID("20000000-0000-0000-0000-000000000001")
CORRELATION_ID = UUID("30000000-0000-0000-0000-000000000001")
CASE_ID = UUID("40000000-0000-0000-0000-000000000001")
POLICY_VERSION_ID = UUID("50000000-0000-0000-0000-000000000001")
REQUEST_ID = UUID("60000000-0000-0000-0000-000000000001")
AT = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
SCOPE = TenantScope(TENANT_ID)
NGN = currency("NGN")
USD = currency("USD")


def _trigger(**overrides: object) -> TriggerProvenanceV1:
    values: dict[str, object] = {
        "kind": "source_event",
        "trigger_id": "billing.receivable.changed:evt-1",
        "triggered_at": AT,
    }
    values.update(overrides)
    return TriggerProvenanceV1(**values)  # type: ignore[arg-type]


def _assessment(**overrides: object) -> AssessCollectionExposureV1:
    values: dict[str, object] = {
        "command_id": COMMAND_ID,
        "idempotency_key": "assess:invoice:inv-1:v7",
        "correlation_id": CORRELATION_ID,
        "causal_event_id": "evt-1",
        "scope": SCOPE,
        "source_owner": "billing.receivables",
        "exposure_ref": "invoice:inv-1",
        "subject_ref": "subscriber:sub-1",
        "service_ref": "service:svc-1",
        "collection_timing": "arrears",
        "reason_code": "invoice_overdue",
        "trigger": _trigger(),
    }
    values.update(overrides)
    return AssessCollectionExposureV1(**values)  # type: ignore[arg-type]


def _position(**overrides: object) -> ReceivablePositionV1:
    values: dict[str, object] = {
        "scope": SCOPE,
        "source_owner": "billing.receivables",
        "exposure_ref": "invoice:inv-1",
        "source_version": 7,
        "state_fingerprint": "sha256:position-v7",
        "subject_ref": "subscriber:sub-1",
        "service_ref": "service:svc-1",
        "collection_timing": "arrears",
        "reason_code": "invoice_overdue",
        "collectible_receivable": Money.of(Decimal("1200.00"), NGN),
        "available_credit": Money.zero(NGN),
        "funding_available": Money.zero(NGN),
        "due_at": AT - timedelta(days=7),
        "coverage_start_at": None,
        "resolution": "open",
        "authority": "authoritative",
        "completeness": "complete",
        "observed_at": AT,
    }
    values.update(overrides)
    return ReceivablePositionV1(**values)  # type: ignore[arg-type]


def _step(ordinal: int, *, offset_days: int | None = None) -> PolicyStepDraftV1:
    delay = ordinal - 1 if offset_days is None else offset_days
    return PolicyStepDraftV1(
        code=f"step_{ordinal}",
        ordinal=ordinal,
        offset=timedelta(days=delay),
        offset_anchor="exposure_at",
        request_kind="notice" if ordinal == 1 else "action",
        action_code=None if ordinal == 1 else f"restrict_{ordinal}",
        receipt_required=True,
    )


def _policy(*steps: PolicyStepDraftV1) -> PolicyVersionDraftV1:
    return PolicyVersionDraftV1(
        policy_code="standard_arrears",
        reason_code="invoice_overdue",
        collection_timing="arrears",
        grace=None,
        steps=steps,
    )


def _action_request(**overrides: object) -> CollectionActionRequestedV1:
    values: dict[str, object] = {
        "request_id": REQUEST_ID,
        "idempotency_key": "case:case-1:step:restrict:attempt:1",
        "case_id": CASE_ID,
        "policy_version_id": POLICY_VERSION_ID,
        "policy_step_code": "restrict",
        "step_attempt_ordinal": 1,
        "source_owner": "billing.receivables",
        "exposure_ref": "invoice:inv-1",
        "source_version": 7,
        "position_fingerprint": "sha256:position-v7",
        "subject_ref": "subscriber:sub-1",
        "service_ref": "service:svc-1",
        "action_code": "restrict_service_for_delinquency",
        "effect_scope": "service",
        "decision_evidence": _position(),
        "requested_at": AT,
    }
    values.update(overrides)
    return CollectionActionRequestedV1(**values)  # type: ignore[arg-type]


def _notice_request(**overrides: object) -> CollectionNoticeRequestedV1:
    values: dict[str, object] = {
        "request_id": REQUEST_ID,
        "idempotency_key": "case:case-1:step:notice:attempt:1",
        "case_id": CASE_ID,
        "policy_version_id": POLICY_VERSION_ID,
        "policy_step_code": "initial_notice",
        "step_attempt_ordinal": 1,
        "source_owner": "billing.receivables",
        "exposure_ref": "invoice:inv-1",
        "source_version": 7,
        "position_fingerprint": "sha256:position-v7",
        "subject_ref": "subscriber:sub-1",
        "service_ref": "service:svc-1",
        "purpose_code": "collections.payment_required",
        "decision_evidence": _position(),
        "requested_at": AT,
    }
    values.update(overrides)
    return CollectionNoticeRequestedV1(**values)  # type: ignore[arg-type]


def test_assessment_contract_is_identity_only_frozen_and_closed() -> None:
    assert tuple(field.name for field in fields(AssessCollectionExposureV1)) == (
        "command_id",
        "idempotency_key",
        "correlation_id",
        "causal_event_id",
        "scope",
        "source_owner",
        "exposure_ref",
        "subject_ref",
        "service_ref",
        "collection_timing",
        "reason_code",
        "trigger",
    )
    forbidden = {
        "amount",
        "balance",
        "currency",
        "available_credit",
        "funding_available",
        "due_at",
        "coverage_start_at",
        "position_version",
        "position_fingerprint",
        "policy_code",
        "resolved_state",
    }
    assert forbidden.isdisjoint(
        field.name for field in fields(AssessCollectionExposureV1)
    )

    command = _assessment()
    assert not hasattr(command, "__dict__")
    with pytest.raises(FrozenInstanceError):
        command.reason_code = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        _assessment(amount=Decimal("1.00"))


@pytest.mark.parametrize(
    ("factory", "overrides"),
    [
        (_trigger, {"triggered_at": AT.replace(tzinfo=None)}),
        (_position, {"observed_at": AT.replace(tzinfo=None)}),
        (_position, {"due_at": AT.replace(tzinfo=None)}),
    ],
)
def test_contract_instants_reject_naive_datetimes(
    factory: object, overrides: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        factory(**overrides)  # type: ignore[operator]


def test_current_position_requires_exact_non_negative_single_currency_money() -> None:
    with pytest.raises((MoneyError, TypeError, ValueError)):
        Money.of(0.1, NGN)  # type: ignore[arg-type]

    for field_name in (
        "collectible_receivable",
        "available_credit",
        "funding_available",
    ):
        with pytest.raises(ValueError, match="non-negative"):
            _position(**{field_name: Money.of(Decimal("-0.01"), NGN)})

    with pytest.raises(ValueError, match="currency"):
        _position(available_credit=Money.zero(USD))
    with pytest.raises((TypeError, ValueError), match="Money"):
        _position(collectible_receivable=1200.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_version": 0},
        {"collection_timing": "arrears", "due_at": None},
        {"collection_timing": "advance", "coverage_start_at": None, "due_at": AT},
    ],
)
def test_position_rejects_invalid_version_or_timing_anchor(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _position(**overrides)


def test_reader_outcomes_are_typed_and_unavailable_is_not_zero() -> None:
    position = _position()
    outcomes = (
        PositionReadOk(position=position),
        PositionUnavailable(reason_code="billing_transition", retry_after=AT),
        PositionUnknown(
            source_owner="billing.receivables", exposure_ref="invoice:missing"
        ),
        PositionAuthorityMismatch(
            expected_owner="billing.receivables",
            observed_owner="legacy.collections",
        ),
    )
    assert [type(outcome).__name__ for outcome in outcomes] == [
        "PositionReadOk",
        "PositionUnavailable",
        "PositionUnknown",
        "PositionAuthorityMismatch",
    ]
    assert not hasattr(outcomes[1], "position")
    with pytest.raises(FrozenInstanceError):
        outcomes[1].reason_code = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="timezone-aware"):
        PositionUnavailable(
            reason_code="billing_transition",
            retry_after=AT.replace(tzinfo=None),
        )


def test_fake_reader_rereads_and_records_supplied_time() -> None:
    reader = FakeReceivablesReader()
    with pytest.raises(AssertionError, match="unconfigured"):
        reader.read(
            scope=SCOPE,
            source_owner="billing.receivables",
            exposure_ref="invoice:inv-1",
            as_of=AT,
        )

    first = PositionReadOk(position=_position())
    reader.set_result(
        scope=SCOPE,
        source_owner="billing.receivables",
        exposure_ref="invoice:inv-1",
        result=first,
    )
    assert (
        reader.read(
            scope=SCOPE,
            source_owner="billing.receivables",
            exposure_ref="invoice:inv-1",
            as_of=AT,
        )
        is first
    )

    second_at = AT + timedelta(hours=1)
    second = PositionReadOk(
        position=_position(
            source_version=8,
            state_fingerprint="sha256:position-v8",
            collectible_receivable=Money.of(Decimal("200.00"), NGN),
            observed_at=second_at,
        )
    )
    reader.set_result(
        scope=SCOPE,
        source_owner="billing.receivables",
        exposure_ref="invoice:inv-1",
        result=second,
    )
    assert (
        reader.read(
            scope=SCOPE,
            source_owner="billing.receivables",
            exposure_ref="invoice:inv-1",
            as_of=second_at,
        )
        is second
    )
    assert reader.calls == (
        ReceivablesReadCallV1(
            scope=SCOPE,
            source_owner="billing.receivables",
            exposure_ref="invoice:inv-1",
            as_of=AT,
        ),
        ReceivablesReadCallV1(
            scope=SCOPE,
            source_owner="billing.receivables",
            exposure_ref="invoice:inv-1",
            as_of=second_at,
        ),
    )


@pytest.mark.parametrize("step_count", [1, 4, 7])
def test_policy_draft_accepts_arbitrary_ladder_lengths(step_count: int) -> None:
    policy = _policy(*(_step(ordinal) for ordinal in range(1, step_count + 1)))
    assert len(policy.steps) == step_count
    assert tuple(step.ordinal for step in policy.steps) == tuple(
        range(1, step_count + 1)
    )


@pytest.mark.parametrize(
    "steps",
    [
        (_step(1), _step(1)),
        (_step(1), _step(3)),
        (_step(1, offset_days=2), _step(2, offset_days=1)),
    ],
)
def test_policy_draft_rejects_duplicate_or_non_contiguous_ladders(
    steps: tuple[PolicyStepDraftV1, ...],
) -> None:
    with pytest.raises(ValueError):
        _policy(*steps)


def test_grace_requires_an_explicit_anchor_and_allows_explicit_zero() -> None:
    grace = GraceRuleV1(duration=timedelta(0), anchor="exposure_at")
    assert grace.duration == timedelta(0)
    assert grace.anchor == "exposure_at"
    with pytest.raises(ValueError, match="anchor"):
        GraceRuleV1(duration=timedelta(days=1), anchor="")
    with pytest.raises(ValueError, match="anchor"):
        GraceRuleV1(duration=timedelta(days=1), anchor="implicit_default")
    with pytest.raises(ValueError, match="duration"):
        GraceRuleV1(duration=timedelta(microseconds=-1), anchor="exposure_at")


def test_notice_request_is_owner_neutral_and_carries_no_delivery_decision() -> None:
    assert tuple(field.name for field in fields(CollectionNoticeRequestedV1)) == (
        "request_id",
        "idempotency_key",
        "case_id",
        "policy_version_id",
        "policy_step_code",
        "step_attempt_ordinal",
        "source_owner",
        "exposure_ref",
        "source_version",
        "position_fingerprint",
        "subject_ref",
        "service_ref",
        "purpose_code",
        "decision_evidence",
        "requested_at",
    )
    forbidden = {
        "address",
        "channel",
        "consent",
        "locale",
        "provider",
        "rendered_body",
        "template_id",
    }
    assert forbidden.isdisjoint(
        field.name for field in fields(CollectionNoticeRequestedV1)
    )
    request = _notice_request()
    assert request.decision_evidence.source_version == request.source_version
    assert request.decision_evidence.state_fingerprint == request.position_fingerprint
    with pytest.raises(ValueError, match="timezone-aware"):
        _notice_request(requested_at=AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="evidence"):
        _notice_request(position_fingerprint="sha256:stale")


def test_notice_outcomes_keep_suppression_unavailability_and_failure_distinct() -> None:
    outcomes = (
        NoticeAccepted(
            request_id=REQUEST_ID,
            owner_code="communications.delivery",
            owner_receipt_id="notice:accepted:1",
            accepted_at=AT,
        ),
        NoticeSuppressed(
            request_id=REQUEST_ID,
            owner_code="communications.delivery",
            owner_receipt_id="notice:suppressed:1",
            reason_code="consent_absent",
            observed_at=AT,
        ),
        NoticeUnavailable(
            request_id=REQUEST_ID,
            owner_code="communications.delivery",
            owner_receipt_id="notice:unavailable:1",
            reason_code="delivery_owner_unavailable",
            observed_at=AT,
            retry_at=AT + timedelta(minutes=5),
        ),
        NoticeFailed(
            request_id=REQUEST_ID,
            owner_code="communications.delivery",
            owner_receipt_id="notice:failed:1",
            reason_code="provider_rejected",
            observed_at=AT,
            retryable=False,
        ),
    )
    assert tuple(type(outcome).__name__ for outcome in outcomes) == (
        "NoticeAccepted",
        "NoticeSuppressed",
        "NoticeUnavailable",
        "NoticeFailed",
    )
    assert len({outcome.owner_receipt_id for outcome in outcomes}) == 4
    assert not hasattr(outcomes[0], "__dict__")
    with pytest.raises(FrozenInstanceError):
        outcomes[0].owner_code = "changed"  # type: ignore[misc]


def test_action_request_is_frozen_and_pins_the_current_position_evidence() -> None:
    assert tuple(field.name for field in fields(CollectionActionRequestedV1)) == (
        "request_id",
        "idempotency_key",
        "case_id",
        "policy_version_id",
        "policy_step_code",
        "step_attempt_ordinal",
        "source_owner",
        "exposure_ref",
        "source_version",
        "position_fingerprint",
        "subject_ref",
        "service_ref",
        "action_code",
        "effect_scope",
        "decision_evidence",
        "requested_at",
    )
    request = _action_request()
    assert request.decision_evidence.source_version == request.source_version
    assert request.decision_evidence.state_fingerprint == request.position_fingerprint
    assert not hasattr(request, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request.action_code = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="timezone-aware"):
        _action_request(requested_at=AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="evidence"):
        _action_request(source_version=8)
    with pytest.raises(ValueError):
        _action_request(step_attempt_ordinal=0)


def test_action_receipt_variants_are_distinct_frozen_owner_evidence() -> None:
    receipts = (
        ActionApplied(
            request_id=REQUEST_ID,
            owner_code="subscriptions.access",
            owner_receipt_id="receipt:applied:1",
            action_ref="access-lock:lock-1",
            applied_at=AT,
            owner_state_fingerprint="sha256:access-applied",
        ),
        ActionRefused(
            request_id=REQUEST_ID,
            owner_code="subscriptions.access",
            owner_receipt_id="receipt:refused:1",
            reason_code="service_already_terminated",
            observed_at=AT,
            owner_state_fingerprint="sha256:access-terminated",
        ),
        ActionDeferred(
            request_id=REQUEST_ID,
            owner_code="subscriptions.access",
            owner_receipt_id="receipt:deferred:1",
            reason_code="owner_transition_in_progress",
            observed_at=AT,
            retry_at=AT + timedelta(minutes=5),
        ),
        ActionFailed(
            request_id=REQUEST_ID,
            owner_code="subscriptions.access",
            owner_receipt_id="receipt:failed:1",
            reason_code="owner_unavailable",
            observed_at=AT,
            retryable=True,
        ),
    )
    assert tuple(type(receipt).__name__ for receipt in receipts) == (
        "ActionApplied",
        "ActionRefused",
        "ActionDeferred",
        "ActionFailed",
    )
    assert all(receipt.request_id == REQUEST_ID for receipt in receipts)
    assert len({receipt.owner_receipt_id for receipt in receipts}) == 4
    with pytest.raises(FrozenInstanceError):
        receipts[0].owner_code = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="timezone-aware"):
        ActionApplied(
            request_id=REQUEST_ID,
            owner_code="subscriptions.access",
            owner_receipt_id="receipt:naive",
            action_ref="access-lock:lock-1",
            applied_at=AT.replace(tzinfo=None),
            owner_state_fingerprint="sha256:access-applied",
        )
