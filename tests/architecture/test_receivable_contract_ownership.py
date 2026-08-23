"""Canaries for the Billing -> assembly -> Collections receivable seam.

These checks freeze ownership, not transport.  Billing publishes the financial
fact; a consuming assembly translates it into Collections' peer-owned input
without either installable module importing the other.
"""

from __future__ import annotations

import ast
import dataclasses
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args, get_type_hints
from uuid import uuid4

import pytest
from dotmac_billing import contracts as billing_contracts
from dotmac_billing.contracts import (
    DueDateBasisV1,
    ReceivableExposureV1,
    ReceivablePositionV1,
    ServicePeriodEvidenceV1,
    ServicePeriodStatus,
)
from dotmac_collections.receivables import ReceivableObservationV1
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money, currency
from dotmac_subscriptions.contracts import RatedObligationOutputV1

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / "packages"


def _class_owners(class_name: str) -> list[Path]:
    owners: list[Path] = []
    for path in PACKAGES.glob("*/src/**/*.py"):
        tree = ast.parse(path.read_text())
        if any(
            isinstance(node, ast.ClassDef) and node.name == class_name
            for node in ast.walk(tree)
        ):
            owners.append(path.relative_to(ROOT))
    return owners


def _map_for_collections(
    exposure: ReceivableExposureV1, *, reason_code: str
) -> ReceivableObservationV1:
    """Reference assembly map: no business decision and no funding arithmetic."""

    return ReceivableObservationV1(
        scope=exposure.scope,
        source_owner=exposure.source_owner,
        exposure_ref=exposure.exposure_ref,
        source_version=exposure.source_version,
        state_fingerprint=exposure.state_fingerprint,
        subject_ref=exposure.subject_ref,
        service_ref=exposure.service_ref,
        collection_timing=exposure.collection_timing,
        reason_code=reason_code,
        collectible_receivable=exposure.collectible_receivable,
        service_period_status=exposure.service_period.status.value,
        service_period_starts_at=exposure.service_period.starts_at,
        service_period_ends_at=exposure.service_period.ends_at,
        due_at=exposure.due_at,
        due_date_status=exposure.due_date_basis.status.value,
        financial_state=exposure.financial_state,
        source_authority=exposure.source_authority,
        projection_mode=exposure.projection_mode,
        completeness=exposure.completeness,
        completeness_reason_code=exposure.completeness_reason_code,
        observed_at=exposure.observed_at,
    )


def _unknown_due_exposure(*, observed_at: datetime) -> ReceivableExposureV1:
    ngn = currency("NGN")
    return ReceivableExposureV1(
        scope=TenantScope(uuid4()),
        source_owner="dotmac-billing",
        exposure_ref="invoice:example",
        billing_account_id=uuid4(),
        subject_ref="customer:example",
        service_ref="service:example",
        collection_timing="arrears",
        source_version=1,
        posting_group_watermark=uuid4(),
        source_authority="internal",
        projection_mode="authoritative",
        derived_from="posting_groups",
        completeness="complete",
        completeness_reason_code=None,
        state_fingerprint="f" * 64,
        observed_at=observed_at,
        service_period=ServicePeriodEvidenceV1(
            status=ServicePeriodStatus.NOT_APPLICABLE
        ),
        due_at=None,
        due_date_basis=DueDateBasisV1.unknown_unverified(
            source_authority="dotmac-billing",
            evidence_ref="invoice:example:missing-terms",
        ),
        financial_state="open",
        collectible_receivable=Money.of("100.00", ngn),
    )


def test_billing_is_the_only_receivable_position_contract_owner() -> None:
    assert _class_owners("ReceivablePositionV1") == [
        Path("packages/dotmac-billing/src/dotmac_billing/contracts.py")
    ]
    assert _class_owners("MoneyV1") == []


def test_billing_is_the_only_receivable_exposure_contract_owner() -> None:
    assert _class_owners("ReceivableExposureV1") == [
        Path("packages/dotmac-billing/src/dotmac_billing/contracts.py")
    ]


def test_subscriptions_alone_owns_the_rated_obligation_output() -> None:
    assert _class_owners("RatedObligationOutputV1") == [
        Path("packages/dotmac-subscriptions/src/" "dotmac_subscriptions/contracts.py")
    ]
    assert RatedObligationOutputV1.__module__ == "dotmac_subscriptions.contracts"
    assert not hasattr(billing_contracts, "OBLIGATION_OUTPUT_CONTRACT")


def test_billing_position_uses_kernel_money_and_owns_financial_state() -> None:
    hints = get_type_hints(ReceivablePositionV1)
    money_fields = {
        "collectible_receivable",
        "available_credit",
        "prepaid_funding",
    }
    assert {field.name for field in dataclasses.fields(ReceivablePositionV1)} >= {
        "scope",
        "source_owner",
        "billing_account_id",
        "source_version",
        "source_authority",
        "projection_mode",
        "completeness",
        "financial_state",
        *money_fields,
    }
    assert {
        "exposure_ref",
        "subject_ref",
        "service_ref",
        "collection_timing",
        "service_period",
        "due_at",
        "due_date_basis",
    }.isdisjoint(field.name for field in dataclasses.fields(ReceivablePositionV1))
    assert {name for name in money_fields if hints[name] is Money} == money_fields
    assert set(get_args(hints["financial_state"])) == {
        "open",
        "partially_resolved",
        "resolved",
        "cancelled",
    }


def test_billing_exposure_preserves_one_collectible_identity_without_funding() -> None:
    hints = get_type_hints(ReceivableExposureV1)
    names = {field.name for field in dataclasses.fields(ReceivableExposureV1)}
    assert {
        "scope",
        "source_owner",
        "exposure_ref",
        "billing_account_id",
        "subject_ref",
        "service_ref",
        "collection_timing",
        "source_version",
        "service_period",
        "due_at",
        "due_date_basis",
        "financial_state",
        "collectible_receivable",
    } <= names
    assert {"available_credit", "prepaid_funding"}.isdisjoint(names)
    assert hints["collectible_receivable"] is Money


def test_collections_owns_a_narrow_peer_observation_not_a_second_position() -> None:
    names = {field.name for field in dataclasses.fields(ReceivableObservationV1)}
    assert {
        "scope",
        "source_owner",
        "exposure_ref",
        "source_version",
        "state_fingerprint",
        "subject_ref",
        "service_ref",
        "collectible_receivable",
        "financial_state",
        "projection_mode",
        "completeness",
        "due_date_status",
        "service_period_status",
    } <= names
    assert {
        "available_credit",
        "funding_available",
        "prepaid_funding",
        "resolution",
        "authority",
    }.isdisjoint(names)
    assert get_type_hints(ReceivableObservationV1)["collectible_receivable"] is Money


def test_unknown_due_date_cannot_be_laundered_into_collection_eligibility() -> None:
    observed_at = datetime(2026, 8, 23, tzinfo=UTC)
    exposure = _unknown_due_exposure(observed_at=observed_at)
    observation = _map_for_collections(
        exposure,
        reason_code="invoice_overdue",
    )

    assert observation.collectible_receivable == exposure.collectible_receivable
    assert not hasattr(observation, "available_credit")
    assert not hasattr(observation, "prepaid_funding")
    assert observation.automated_collection_blocker(as_of=observed_at) == (
        "due_date_unverified"
    )


def test_reversal_is_a_movement_that_can_reopen_not_a_steady_state() -> None:
    billing_states = set(
        get_args(get_type_hints(ReceivableExposureV1)["financial_state"])
    )
    collections_states = set(
        get_args(get_type_hints(ReceivableObservationV1)["financial_state"])
    )
    assert "reversed" not in billing_states | collections_states
    assert billing_states == collections_states

    with pytest.raises(ValueError, match="financial_state is unsupported"):
        replace(
            _unknown_due_exposure(observed_at=datetime(2026, 8, 23, tzinfo=UTC)),
            financial_state="reversed",  # type: ignore[arg-type]
        )
