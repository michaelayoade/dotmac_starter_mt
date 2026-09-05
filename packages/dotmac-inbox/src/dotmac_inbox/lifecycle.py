"""Conversation status and direction — a closed vocabulary with open reasons.

Sub declares four statuses; CRM declares five. The fifth is
``resolved_to_ticket``, and it is not a status: it is *why* a conversation was
resolved. Reading it as a status is what forces every membership set in the
codebase to know about ticket handoff before it can answer "is this
conversation still open".

That is the same finding ``ticket-sources.md`` reached about
``lastmile_rerun``, and it gets the same treatment — a **closed** status
vocabulary with an **open**, product-declared reason layer beside it.

## Why the status vocabulary is closed and the reason layer is not

The four statuses answer a question every product asks identically: is anyone
expected to act, and when. A product that needs a fifth answer to *that*
question has almost certainly found a reason, not a state — and the registry
below makes declaring the reason the easy path.

Conversely a reason is genuinely unbounded: ``resolved_to_ticket``,
``spam``, ``duplicate``, ``customer_no_longer_reachable``, ``merged``. Each is
product vocabulary, each is branched on by product code, and none of them
changes whether the conversation needs attention.

## Why a reason is not a tag

Identical test to ticketing's: if you cannot name the code that branches on the
term, it is a tag — free-form, operator-created, undeclared. A reason is
declared, owned, and validated against the status it may accompany, because
``resolved_to_ticket`` on an ``open`` conversation is nonsense that should fail
at the write rather than confuse a report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

__all__ = [
    "UNTIL_REPLY",
    "Direction",
    "InvalidTransitionError",
    "ReasonSpec",
    "SnoozeUntilReply",
    "Status",
    "UnknownReasonError",
    "is_open",
    "register_reasons",
    "registered_reasons",
    "reset_reason_registry_for_tests",
    "validate_reason",
    "validate_transition",
]

_REASON_CODE_MAX: Final[int] = 64


class Status(StrEnum):
    """The four standard statuses. Closed — extend the reason layer instead."""

    #: Awaiting the tenant's operators. The default on inbound.
    OPEN = "open"
    #: Awaiting the external party. The clock is theirs, not ours.
    PENDING = "pending"
    #: Deliberately out of sight until a time or an event. Distinct from
    #: PENDING: nobody is waiting on the customer, we have chosen to defer.
    SNOOZED = "snoozed"
    #: Terminal for this exchange. A new inbound message may reopen it — that is
    #: the product's policy, and the transition table below permits it.
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class SnoozeUntilReply:
    """Explicitly request an indefinite snooze that ends on inbound activity.

    ``None`` remains an omitted/invalid snooze deadline at command boundaries;
    callers must pass this value to ask for the persisted NULL deadline.
    """


SnoozeTarget = datetime | SnoozeUntilReply | None

# A named value keeps the command readable while the class remains available
# for callers that prefer constructing the typed value explicitly.
UNTIL_REPLY = SnoozeUntilReply()


def is_open(status: Status | str) -> bool:
    """Whether the conversation is still live (anything but RESOLVED).

    Deliberately NOT "needs attention" — a snoozed conversation is open but
    nobody should be looking at it. Callers wanting the narrower question should
    say so explicitly rather than reusing this one.
    """
    return Status(status) is not Status.RESOLVED


class Direction(StrEnum):
    """Who a message came from. Identical in both source products."""

    #: From the external party.
    INBOUND = "inbound"
    #: From the tenant's operators, to the external party.
    OUTBOUND = "outbound"
    #: Between operators. Never delivered anywhere, never counted as a reply.
    INTERNAL = "internal"


#: Legal status transitions. Every pair either appears here or is rejected.
#:
#: RESOLVED → OPEN is present on purpose: an inbound message on a resolved
#: conversation reopens it in both source products, and modelling that as
#: "create a new conversation" loses the thread the customer can plainly see.
_TRANSITIONS: Final[dict[Status, frozenset[Status]]] = {
    Status.OPEN: frozenset({Status.PENDING, Status.SNOOZED, Status.RESOLVED}),
    Status.PENDING: frozenset({Status.OPEN, Status.SNOOZED, Status.RESOLVED}),
    Status.SNOOZED: frozenset({Status.OPEN, Status.PENDING, Status.RESOLVED}),
    Status.RESOLVED: frozenset({Status.OPEN}),
}


class InvalidTransitionError(ValueError):
    """A status change the lifecycle does not permit."""


def validate_transition(current: Status | str, target: Status | str) -> Status:
    """Return ``target`` if the move is legal, else raise.

    A no-op move (``current == target``) is legal and returns unchanged: a
    caller re-asserting a status it already has is idempotent, not an error, and
    making it raise would push every caller into a read-compare-write dance.
    """
    src, dst = Status(current), Status(target)
    if src is dst:
        return dst
    if dst not in _TRANSITIONS[src]:
        allowed = ", ".join(sorted(s.value for s in _TRANSITIONS[src]))
        raise InvalidTransitionError(
            f"cannot move a conversation from {src.value!r} to {dst.value!r}; "
            f"legal targets are: {allowed}"
        )
    return dst


@dataclass(frozen=True, slots=True)
class ReasonSpec:
    """A declared reason, scoped to the statuses it may accompany."""

    code: str
    owner: str
    #: The statuses this reason is meaningful on. At least one — a reason valid
    #: everywhere is a tag with extra ceremony.
    statuses: frozenset[Status] = field(default_factory=frozenset)
    label: str = ""

    def __post_init__(self) -> None:
        code = self.code
        if not code or code != code.strip().lower():
            raise ValueError(f"reason code must be lowercase and unpadded: {code!r}")
        if len(code) > _REASON_CODE_MAX:
            raise ValueError(
                f"reason code {code!r} exceeds {_REASON_CODE_MAX} characters"
            )
        if not self.owner or not self.owner.strip():
            raise ValueError(f"reason {code!r} must declare an owning module")
        if not self.statuses:
            raise ValueError(
                f"reason {code!r} declares no statuses. A reason valid on every "
                "status is a tag — use the conversation's tags instead."
            )


class UnknownReasonError(ValueError):
    """A reason never declared, or not valid for the status it accompanies."""


_REASONS: dict[str, ReasonSpec] = {}


def register_reasons(specs: tuple[ReasonSpec, ...] | list[ReasonSpec]) -> None:
    """Declare status reasons at import time. Idempotent for identical specs."""
    for spec in specs:
        existing = _REASONS.get(spec.code)
        if existing is not None and existing != spec:
            raise ValueError(
                f"reason {spec.code!r} is already declared by {existing.owner!r} "
                "with a different scope"
            )
        _REASONS[spec.code] = spec


def registered_reasons() -> tuple[ReasonSpec, ...]:
    """Every declared reason, ordered by code."""
    return tuple(_REASONS[code] for code in sorted(_REASONS))


def validate_reason(reason: str | None, *, status: Status | str) -> str | None:
    """Check ``reason`` is declared and legal on ``status``; ``None`` passes.

    ``None`` is the common case — most conversations carry no reason — so it is
    permitted rather than requiring callers to branch before calling.
    """
    if reason is None:
        return None
    spec = _REASONS.get(reason)
    if spec is None:
        known = ", ".join(sorted(_REASONS)) or "(none declared)"
        raise UnknownReasonError(
            f"status reason {reason!r} is not declared. Declared: {known}. "
            "Declare it with dotmac_inbox.lifecycle.register_reasons(...), or "
            "use a free-form tag if no code branches on it."
        )
    resolved = Status(status)
    if resolved not in spec.statuses:
        allowed = ", ".join(sorted(s.value for s in spec.statuses))
        raise UnknownReasonError(
            f"status reason {reason!r} is not valid on status "
            f"{resolved.value!r}; it is declared for: {allowed}"
        )
    return reason


def reset_reason_registry_for_tests() -> None:
    """Empty the reason registry. Tests only."""
    _REASONS.clear()
