"""Multi-period authoring preserves old admission and refuses ambiguous coverage."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import _require_homogeneous, _require_multi_period


def _row(start, end, **changes):
    values = {
        "billing_account_id": "a",
        "currency": "NGN",
        "minor_units": 2,
        "subject_ref": "customer:a",
        "service_ref": "subscription:1",
        "collection_timing": "arrears",
        "service_period_status": "verified",
        "period_start": start,
        "period_end": end,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _periods():
    jan = datetime(2027, 1, 2, tzinfo=UTC)
    feb = datetime(2027, 2, 2, tzinfo=UTC)
    mar = datetime(2027, 3, 2, tzinfo=UTC)
    return (_row(jan, feb), _row(feb, mar))


def test_existing_multiline_contract_still_refuses_distinct_periods():
    with pytest.raises(BillingRuleViolation, match="share account"):
        _require_homogeneous(_periods())


def test_multi_period_accepts_adjacent_anniversary_intervals_in_either_order():
    periods = _periods()
    _require_multi_period(periods)
    _require_multi_period(tuple(reversed(periods)))


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "USD"},
        {"billing_account_id": "b"},
        {"subject_ref": "customer:b"},
        {"collection_timing": "advance"},
        {"minor_units": 0},
    ],
)
def test_multi_period_refuses_changed_identity_or_financial_basis(changes):
    a, b = _periods()
    b = _row(b.period_start, b.period_end, **changes)
    with pytest.raises(BillingRuleViolation):
        _require_multi_period((a, b))


@pytest.mark.parametrize(
    "kind", ["overlap", "duplicate", "unknown", "naive", "reversed"]
)
def test_multi_period_refuses_ambiguous_or_invalid_intervals(kind):
    a, b = _periods()
    if kind == "overlap":
        b.period_start = datetime(2027, 1, 15, tzinfo=UTC)
    elif kind == "duplicate":
        b.period_start, b.period_end = a.period_start, a.period_end
    elif kind == "unknown":
        b.service_period_status = "unknown_unverified"
    elif kind == "naive":
        b.period_start = b.period_start.replace(tzinfo=None)
    else:
        b.period_end = b.period_start
    with pytest.raises(BillingRuleViolation):
        _require_multi_period((a, b))


def test_explicit_mixed_service_periods_do_not_collide_across_services():
    a, _ = _periods()
    b = _row(a.period_start, a.period_end, service_ref="subscription:2")
    _require_multi_period((a, b))


def test_non_periodic_charge_requires_explicit_not_applicable():
    a, _ = _periods()
    item = _row(None, None, service_ref=None, service_period_status="not_applicable")
    _require_multi_period((a, item))
    item.service_period_status = "unknown_unverified"
    with pytest.raises(BillingRuleViolation):
        _require_multi_period((a, item))


def test_service_charge_policy_uses_shared_anniversary_overlap_and_component_identity():
    from dotmac_billing.contracts import (
        ServiceChargeComponentV1,
        ServiceChargeDocumentFactV1,
        ServiceChargePeriodV1,
        ServiceClaimDocumentPhaseV1,
        ServiceCoverageIntervalV1,
    )
    from dotmac_billing.service import resolve_service_charge_conflict_v1

    jan = datetime(2030, 1, 2, tzinfo=UTC)
    feb = datetime(2030, 2, 2, tzinfo=UTC)
    mar = datetime(2030, 3, 2, tzinfo=UTC)
    first = ServiceChargePeriodV1(
        "service:1", ServiceChargeComponentV1.base, "base", jan, feb, True
    )
    adjacent = ServiceChargePeriodV1(
        "service:1", ServiceChargeComponentV1.base, "base", feb, mar, True
    )
    draft = ServiceChargeDocumentFactV1(
        "invoice:1125", "1125", ServiceClaimDocumentPhaseV1.draft, first
    )
    conflict = resolve_service_charge_conflict_v1(first, documents=(draft,))
    assert (
        conflict.reason == "period_reserved"
        and conflict.document_number == "1125"
        and conflict.draft
    )
    assert resolve_service_charge_conflict_v1(adjacent, documents=(draft,)) is None
    addon = ServiceChargePeriodV1(
        "service:1", ServiceChargeComponentV1.addon, "router", jan, feb, True
    )
    assert resolve_service_charge_conflict_v1(addon, documents=(draft,)) is None
    assert (
        resolve_service_charge_conflict_v1(
            first, documents=(), coverage=(ServiceCoverageIntervalV1(jan, feb),)
        ).reason
        == "period_covered"
    )


def test_capped_pending_identity_reserves_unresolved_request_but_allows_closed_cycle():
    from dotmac_billing.contracts import (
        ServiceChargeComponentV1,
        ServiceChargeDocumentFactV1,
        ServiceChargePeriodV1,
        ServiceClaimDocumentPhaseV1,
    )
    from dotmac_billing.service import resolve_service_charge_conflict_v1

    pending = ServiceChargePeriodV1(
        "service:1", ServiceChargeComponentV1.base, "base", None, None, True, True
    )
    closed = ServiceChargeDocumentFactV1(
        "old", "1125", ServiceClaimDocumentPhaseV1.closed, pending
    )
    open_request = ServiceChargeDocumentFactV1(
        "new", "1126", ServiceClaimDocumentPhaseV1.open, pending
    )
    assert resolve_service_charge_conflict_v1(pending, documents=(closed,)) is None
    assert (
        resolve_service_charge_conflict_v1(
            pending, documents=(open_request,)
        ).document_number
        == "1126"
    )
    assert (
        resolve_service_charge_conflict_v1(
            pending, documents=(), unresolved_purchase=True
        ).reason
        == "period_purchase_pending"
    )
