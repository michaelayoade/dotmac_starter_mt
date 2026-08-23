"""Backend projection values keep domain meaning out of templates."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dotmac_kernel.ui_projection import (
    Action,
    Kpi,
    StateKind,
    StateValue,
    StatusIcon,
    StatusPresentation,
    StatusTone,
)


def test_status_presentation_is_transport_neutral_and_non_colour_only() -> None:
    projection = StatusPresentation(
        value="active",
        label="Active",
        tone=StatusTone.positive,
        icon=StatusIcon.check,
    )

    assert projection.model_dump(mode="json") == {
        "value": "active",
        "label": "Active",
        "tone": "positive",
        "icon": "check",
    }


def test_present_stale_and_absent_values_stay_distinct() -> None:
    observed_at = datetime(2026, 8, 19, tzinfo=UTC)
    present = StateValue.present(42, as_of=observed_at)
    stale = StateValue.stale(41, as_of=observed_at)
    absent = (
        StateValue.unknown(),
        StateValue.unavailable(),
        StateValue.not_applicable(),
    )

    assert present.kind is StateKind.present
    assert present.is_present is True
    assert present.is_stale is False
    assert stale.is_present is True
    assert stale.is_stale is True
    assert [value.placeholder for value in absent] == [
        "Unknown",
        "Unavailable",
        "—",
    ]
    assert all(value.value is None for value in absent)


def test_kpi_carries_the_exact_cohort_and_action_carries_owner_eligibility() -> None:
    kpi = Kpi(
        label="Overdue",
        value=StateValue.present(3),
        cohort_url="/billing?status=overdue",
        tone=StatusTone.warning,
        icon=StatusIcon.alert,
        unit="invoices",
    )
    blocked = Action(
        key="cancel",
        label="Cancel",
        allowed=False,
        reason="Already fulfilled",
        tone=StatusTone.negative,
        preview_url="/orders/1/cancel/preview",
        requires_confirmation=True,
    )

    assert kpi.cohort_url == "/billing?status=overdue"
    assert kpi.value.value == 3
    assert blocked.allowed is False
    assert blocked.reason == "Already fulfilled"
    assert blocked.requires_confirmation is True


def test_projection_contracts_reject_contradictory_or_unsafe_shapes() -> None:
    with pytest.raises(ValueError, match="cannot carry"):
        StateValue(StateKind.unknown, value=0)
    with pytest.raises(ValueError, match="requires a value"):
        StateValue.present(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        StateValue.present(1, as_of=datetime(2026, 8, 19))
    with pytest.raises(ValueError, match="cohort URL"):
        Kpi(label="Orphan", value=StateValue.present(1), cohort_url="")
    with pytest.raises(ValueError, match="Blocked action requires"):
        Action(key="cancel", label="Cancel", allowed=False)
    with pytest.raises(ValueError, match="declared together"):
        Action(
            key="refund",
            label="Refund",
            allowed=True,
            requires_confirmation=True,
        )
