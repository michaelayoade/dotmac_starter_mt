"""Pure publication and delivery lifecycle decisions."""

from __future__ import annotations

import enum
from collections.abc import Iterable


class TransitionError(ValueError):
    """A publication delivery transition is inadmissible."""


class PublicationState(enum.StrEnum):
    SCHEDULED = "scheduled"
    DISPATCHING = "dispatching"
    PARTIAL = "partial"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeliveryState(enum.StrEnum):
    PENDING = "pending"
    INTENT_PUBLISHED = "intent_published"
    ACCEPTED = "accepted"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


_DELIVERY_TRANSITIONS: dict[DeliveryState, frozenset[DeliveryState]] = {
    DeliveryState.PENDING: frozenset(
        {DeliveryState.INTENT_PUBLISHED, DeliveryState.CANCELLED}
    ),
    DeliveryState.INTENT_PUBLISHED: frozenset(
        {
            DeliveryState.ACCEPTED,
            DeliveryState.PUBLISHED,
            DeliveryState.FAILED,
            DeliveryState.CANCELLED,
        }
    ),
    DeliveryState.ACCEPTED: frozenset(
        {DeliveryState.PUBLISHED, DeliveryState.FAILED, DeliveryState.CANCELLED}
    ),
    DeliveryState.PUBLISHED: frozenset(),
    DeliveryState.FAILED: frozenset(
        {DeliveryState.INTENT_PUBLISHED, DeliveryState.CANCELLED}
    ),
    DeliveryState.CANCELLED: frozenset(),
}


def check_delivery_transition(current: DeliveryState, desired: DeliveryState) -> None:
    if current == desired:
        return
    if desired not in _DELIVERY_TRANSITIONS[current]:
        if current in {DeliveryState.PUBLISHED, DeliveryState.CANCELLED}:
            raise TransitionError(f"delivery state {current.value!r} is terminal")
        allowed = ", ".join(
            state.value for state in sorted(_DELIVERY_TRANSITIONS[current])
        )
        raise TransitionError(
            f"delivery in {current.value!r} cannot move to {desired.value!r}; "
            f"allowed: {allowed}"
        )


def derive_publication_state(states: Iterable[DeliveryState]) -> PublicationState:
    values = tuple(states)
    if not values:
        raise ValueError("publication requires at least one delivery")
    if all(state == DeliveryState.PENDING for state in values):
        return PublicationState.SCHEDULED

    terminal = {
        DeliveryState.PUBLISHED,
        DeliveryState.FAILED,
        DeliveryState.CANCELLED,
    }
    if not all(state in terminal for state in values):
        return PublicationState.DISPATCHING
    if all(state == DeliveryState.PUBLISHED for state in values):
        return PublicationState.PUBLISHED
    if all(state == DeliveryState.CANCELLED for state in values):
        return PublicationState.CANCELLED
    if any(state == DeliveryState.PUBLISHED for state in values):
        return PublicationState.PARTIAL
    return PublicationState.FAILED


__all__ = [
    "DeliveryState",
    "PublicationState",
    "TransitionError",
    "check_delivery_transition",
    "derive_publication_state",
]
