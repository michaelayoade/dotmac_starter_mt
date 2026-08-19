"""Pure editorial lifecycle decisions."""

from __future__ import annotations

import enum
from datetime import date


class TransitionError(ValueError):
    """An editorial state transition is inadmissible."""


class ContentPlanStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ContentItemState(enum.StrEnum):
    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


_PLAN_TRANSITIONS: dict[ContentPlanStatus, frozenset[ContentPlanStatus]] = {
    ContentPlanStatus.DRAFT: frozenset(
        {ContentPlanStatus.ACTIVE, ContentPlanStatus.ARCHIVED}
    ),
    ContentPlanStatus.ACTIVE: frozenset(
        {
            ContentPlanStatus.PAUSED,
            ContentPlanStatus.COMPLETED,
            ContentPlanStatus.ARCHIVED,
        }
    ),
    ContentPlanStatus.PAUSED: frozenset(
        {
            ContentPlanStatus.ACTIVE,
            ContentPlanStatus.COMPLETED,
            ContentPlanStatus.ARCHIVED,
        }
    ),
    ContentPlanStatus.COMPLETED: frozenset({ContentPlanStatus.ARCHIVED}),
    ContentPlanStatus.ARCHIVED: frozenset(),
}

_ITEM_TRANSITIONS: dict[ContentItemState, frozenset[ContentItemState]] = {
    ContentItemState.DRAFT: frozenset(
        {ContentItemState.READY, ContentItemState.ARCHIVED}
    ),
    ContentItemState.READY: frozenset(
        {ContentItemState.DRAFT, ContentItemState.ARCHIVED}
    ),
    ContentItemState.ARCHIVED: frozenset(),
}


def check_plan_transition(
    current: ContentPlanStatus, desired: ContentPlanStatus
) -> None:
    if current == desired:
        return
    if desired not in _PLAN_TRANSITIONS[current]:
        allowed = ", ".join(
            status.value for status in sorted(_PLAN_TRANSITIONS[current])
        )
        detail = allowed or "no later state"
        raise TransitionError(
            f"content plan in {current.value!r} cannot move to "
            f"{desired.value!r}; allowed: {detail}"
        )


def check_item_transition(current: ContentItemState, desired: ContentItemState) -> None:
    if current == desired:
        return
    if desired not in _ITEM_TRANSITIONS[current]:
        allowed = ", ".join(state.value for state in sorted(_ITEM_TRANSITIONS[current]))
        detail = allowed or "no later state"
        raise TransitionError(
            f"content item in {current.value!r} cannot move to "
            f"{desired.value!r}; allowed: {detail}"
        )


def validate_plan_date_range(starts_on: date | None, ends_on: date | None) -> None:
    if starts_on is not None and ends_on is not None and ends_on < starts_on:
        raise ValueError("ends_on cannot precede starts_on")


__all__ = [
    "ContentItemState",
    "ContentPlanStatus",
    "TransitionError",
    "check_item_transition",
    "check_plan_transition",
    "validate_plan_date_range",
]
