"""One persistence-free lifecycle shared by both subscription planes."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TypeVar

from dotmac_subscriptions.errors import SubscriptionStateError


class OfferState(str, Enum):
    draft = "draft"
    published = "published"
    withdrawn = "withdrawn"


class ContractVersionState(str, Enum):
    draft = "draft"
    effective = "effective"
    superseded = "superseded"
    ended = "ended"
    cancelled = "cancelled"


class OccurrenceState(str, Enum):
    scheduled = "scheduled"
    due = "due"
    emitted = "emitted"
    cancelled = "cancelled"


_OFFER_TRANSITIONS = {
    OfferState.draft: frozenset({OfferState.published}),
    OfferState.published: frozenset({OfferState.withdrawn}),
    OfferState.withdrawn: frozenset(),
}
_CONTRACT_TRANSITIONS = {
    ContractVersionState.draft: frozenset(
        {ContractVersionState.effective, ContractVersionState.cancelled}
    ),
    ContractVersionState.effective: frozenset(
        {ContractVersionState.superseded, ContractVersionState.ended}
    ),
    ContractVersionState.superseded: frozenset(),
    ContractVersionState.ended: frozenset(),
    ContractVersionState.cancelled: frozenset(),
}
_OCCURRENCE_TRANSITIONS = {
    OccurrenceState.scheduled: frozenset(
        {OccurrenceState.due, OccurrenceState.cancelled}
    ),
    OccurrenceState.due: frozenset(
        {OccurrenceState.emitted, OccurrenceState.cancelled}
    ),
    OccurrenceState.emitted: frozenset(),
    OccurrenceState.cancelled: frozenset(),
}

_StateT = TypeVar("_StateT", bound=Enum)


def require_offer_transition(current: OfferState, target: OfferState) -> None:
    _require_transition("offer", current, target, _OFFER_TRANSITIONS)


def require_contract_transition(
    current: ContractVersionState, target: ContractVersionState
) -> None:
    _require_transition("contract version", current, target, _CONTRACT_TRANSITIONS)


def require_occurrence_transition(
    current: OccurrenceState, target: OccurrenceState
) -> None:
    _require_transition("occurrence", current, target, _OCCURRENCE_TRANSITIONS)


def _require_transition(
    aggregate: str,
    current: _StateT,
    target: _StateT,
    allowed: Mapping[_StateT, frozenset[_StateT]],
) -> None:
    if target not in allowed[current]:
        raise SubscriptionStateError(
            "lifecycle.invalid_transition",
            f"{aggregate} cannot transition from {current.value!r} to {target.value!r}",
        )


__all__ = [
    "ContractVersionState",
    "OccurrenceState",
    "OfferState",
    "require_contract_transition",
    "require_occurrence_transition",
    "require_offer_transition",
]
