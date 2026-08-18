"""Canaries for the product-neutral editorial lifecycle.

These tests intentionally precede the ``dotmac-content`` implementation. The
first Observer run must be RED because the package does not exist; making these
tests green is Gate 2 of the adoption plan.
"""

from __future__ import annotations

from datetime import date
from importlib import import_module
from types import ModuleType

import pytest


def _lifecycle() -> ModuleType:
    """Load late so a missing package is a clear test failure, not collection noise."""
    try:
        return import_module("dotmac_content.lifecycle")
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("dotmac_content"):
            raise
        pytest.fail(
            "dotmac-content is intentionally absent: this is the Gate 1 RED "
            "canary; implement it only after the RED run is recorded on Observer"
        )


def test_plan_status_vocabulary_is_exactly_the_selected_source_terms() -> None:
    lifecycle = _lifecycle()
    assert [status.value for status in lifecycle.ContentPlanStatus] == [
        "draft",
        "active",
        "paused",
        "completed",
        "archived",
    ]


def test_item_state_is_editorial_and_contains_no_publication_outcome() -> None:
    lifecycle = _lifecycle()
    assert [state.value for state in lifecycle.ContentItemState] == [
        "draft",
        "ready",
        "archived",
    ]
    assert "published" not in {state.value for state in lifecycle.ContentItemState}


def test_normal_plan_progression_and_pause_are_permitted() -> None:
    lifecycle = _lifecycle()
    lifecycle.check_plan_transition(
        lifecycle.ContentPlanStatus.DRAFT, lifecycle.ContentPlanStatus.ACTIVE
    )
    lifecycle.check_plan_transition(
        lifecycle.ContentPlanStatus.ACTIVE, lifecycle.ContentPlanStatus.PAUSED
    )
    lifecycle.check_plan_transition(
        lifecycle.ContentPlanStatus.PAUSED, lifecycle.ContentPlanStatus.ACTIVE
    )
    lifecycle.check_plan_transition(
        lifecycle.ContentPlanStatus.ACTIVE, lifecycle.ContentPlanStatus.COMPLETED
    )
    lifecycle.check_plan_transition(
        lifecycle.ContentPlanStatus.COMPLETED, lifecycle.ContentPlanStatus.ARCHIVED
    )


def test_archived_plan_is_terminal_but_reassertion_is_idempotent() -> None:
    lifecycle = _lifecycle()
    lifecycle.check_plan_transition(
        lifecycle.ContentPlanStatus.ARCHIVED, lifecycle.ContentPlanStatus.ARCHIVED
    )
    with pytest.raises(lifecycle.TransitionError, match="archived"):
        lifecycle.check_plan_transition(
            lifecycle.ContentPlanStatus.ARCHIVED, lifecycle.ContentPlanStatus.ACTIVE
        )


def test_plan_cannot_skip_from_draft_to_completed() -> None:
    lifecycle = _lifecycle()
    with pytest.raises(lifecycle.TransitionError, match="active"):
        lifecycle.check_plan_transition(
            lifecycle.ContentPlanStatus.DRAFT, lifecycle.ContentPlanStatus.COMPLETED
        )


def test_item_can_return_to_draft_before_a_release_snapshot_is_requested() -> None:
    lifecycle = _lifecycle()
    lifecycle.check_item_transition(
        lifecycle.ContentItemState.DRAFT, lifecycle.ContentItemState.READY
    )
    lifecycle.check_item_transition(
        lifecycle.ContentItemState.READY, lifecycle.ContentItemState.DRAFT
    )


def test_archived_item_is_terminal() -> None:
    lifecycle = _lifecycle()
    lifecycle.check_item_transition(
        lifecycle.ContentItemState.READY, lifecycle.ContentItemState.ARCHIVED
    )
    with pytest.raises(lifecycle.TransitionError, match="archived"):
        lifecycle.check_item_transition(
            lifecycle.ContentItemState.ARCHIVED, lifecycle.ContentItemState.DRAFT
        )


def test_plan_date_range_refuses_an_end_before_the_start() -> None:
    lifecycle = _lifecycle()
    lifecycle.validate_plan_date_range(date(2026, 8, 1), date(2026, 8, 31))
    lifecycle.validate_plan_date_range(None, date(2026, 8, 31))
    lifecycle.validate_plan_date_range(date(2026, 8, 1), None)
    with pytest.raises(ValueError, match="ends_on"):
        lifecycle.validate_plan_date_range(date(2026, 9, 1), date(2026, 8, 31))
