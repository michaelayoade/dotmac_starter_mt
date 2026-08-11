"""The closed lifecycle: five classes, nine statuses, and nothing a product may add.

This module is the reason a shared ticket module is possible at all, so the
constraint is worth stating before the code.

## Three layers, and only one of them is extensible

``LifecycleClass``  five values, FIXED. Machine semantics — does the SLA clock
                    run, does this count as open. Nothing outside this module
                    may add one, because everything else keys off it.
``Status``          nine standard helpdesk terms, CLOSED. What a UI calls the
                    ticket and what transitions are guarded.
*reason*            product-declared, open. See :mod:`dotmac_ticketing.vocabulary`.

**A product extends the reason layer and never the status layer.** That is not
a style preference; it is what stops one product redefining "open" for every
other product on the same package.

## Why the status vocabulary is closed, in evidence

The obvious alternative — let each product declare its own statuses — was
measured against ``dotmac_sub`` and failed. Sub has two ISP statuses,
``lastmile_rerun`` and ``site_under_construction``, and all sixteen references
to them put them in a SET alongside the standard statuses:

    SLA_APPLICABLE_STATUSES = {new, open, pending, lastmile_rerun,
                               waiting_on_customer, on_hold,
                               site_under_construction}
    _OPEN_STATUSES          = {...the same shape...}

Every one of those sets is asking a *class* question — does the clock run, is
this open. **Not one site branches on ``lastmile_rerun`` to do something no
other waiting status does.** As statuses they carry no behaviour their class
does not already carry; their real job is to say *why* the ticket is waiting,
and to be filtered on. That is a reason.

Two things follow. Products lose nothing by not declaring statuses, because
their domain terms were never behaving like statuses. And those six hand-written
membership sets collapse into ``status.lifecycle_class in {OPEN, WAITING}`` —
which removes a live bug shape: add a tenth status, forget one of six lists, and
the SLA clock silently stops for it. No error, no failing test.

## Where the nine came from

They are the intersection of mainstream helpdesk systems (Zendesk, Jira Service
Management, Freshdesk, osTicket) and the ITIL incident lifecycle — not a merge
of ERP's and Sub's enums. Sourcing them from an existing product would have let
whichever product was ported first define "standard" for the fleet, and it would
have carried Sub's two genuine defects with it: Sub has no ``resolved`` state at
all (it goes ``pending_confirmation`` → ``closed``), and it declares both
``medium`` and ``normal`` as priorities, two names for one rung.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

__all__ = [
    "STANDARD_STATUSES",
    "TERMINAL_CLASSES",
    "Channel",
    "LifecycleClass",
    "Priority",
    "Status",
    "TransitionError",
    "check_transition",
    "is_open",
    "sla_clock_runs",
]


class LifecycleClass(Enum):
    """What a status MEANS to code that has never heard of it.

    Fixed at five. A product declaring a reason attaches it to a status, and the
    status already has a class — so every consumer downstream (SLA clocks,
    workqueue projections, "is this open" predicates) keeps working without
    knowing the product's vocabulary at all.
    """

    OPEN = "open"
    """Actively workable. The SLA clock runs."""

    WAITING = "waiting"
    """Blocked on someone. The SLA clock is PAUSED — see `sla_clock_runs`."""

    RESOLVED = "resolved"
    """Work is done, awaiting confirmation or auto-close. Clock stopped."""

    CLOSED = "closed"
    """Terminal. Reopening is a new transition, not an edit."""

    CANCELLED = "cancelled"
    """Terminal, and it was never legitimate work. Excluded from SLA reporting."""


#: The classes from which no further work is expected.
TERMINAL_CLASSES: Final[frozenset[LifecycleClass]] = frozenset(
    {LifecycleClass.CLOSED, LifecycleClass.CANCELLED}
)


class Status(Enum):
    """The nine standard helpdesk statuses. CLOSED — products declare reasons.

    The value is the wire/storage form. The class is attached in
    `_STATUS_CLASSES` rather than on the member so this stays a plain
    `str`-valued enum a database column can hold.
    """

    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    WAITING_ON_CUSTOMER = "waiting_on_customer"
    ON_HOLD = "on_hold"
    RESOLVED = "resolved"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    MERGED = "merged"

    @property
    def lifecycle_class(self) -> LifecycleClass:
        return _STATUS_CLASSES[self]

    @property
    def is_terminal(self) -> bool:
        return self.lifecycle_class in TERMINAL_CLASSES


_STATUS_CLASSES: Final[dict[Status, LifecycleClass]] = {
    Status.NEW: LifecycleClass.OPEN,
    Status.OPEN: LifecycleClass.OPEN,
    # `pending` is blocked with the cause unstated — which is exactly the status
    # a product's reason is most often attached to.
    Status.PENDING: LifecycleClass.WAITING,
    Status.WAITING_ON_CUSTOMER: LifecycleClass.WAITING,
    Status.ON_HOLD: LifecycleClass.WAITING,
    Status.RESOLVED: LifecycleClass.RESOLVED,
    Status.CLOSED: LifecycleClass.CLOSED,
    Status.CANCELLED: LifecycleClass.CANCELLED,
    # A merged ticket is closed, not cancelled: the work was legitimate and
    # continues on the target ticket. Excluding it from SLA reporting the way
    # `cancelled` is excluded would hide real work.
    Status.MERGED: LifecycleClass.CLOSED,
}

#: Every standard status, in lifecycle order. Exported for seeding and for the
#: architecture test that pins the vocabulary against silent widening.
STANDARD_STATUSES: Final[tuple[Status, ...]] = tuple(Status)


class Priority(Enum):
    """Four rungs. Deliberately not Sub's six.

    Sub declares `lower` and `normal` in addition to these, which gives it two
    names for one rung (`medium`/`normal`) that no consumer can order. Extra
    rungs are a product concern; if a product genuinely needs five levels it
    declares a reason or a custom field, not a fifth global priority.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Channel(Enum):
    """How the request arrived. Shared because every product has these five."""

    WEB = "web"
    EMAIL = "email"
    PHONE = "phone"
    CHAT = "chat"
    API = "api"


class TransitionError(ValueError):
    """A rejected status transition. Stable type so callers can catch it."""


# Which statuses may follow which. Expressed over STATUSES rather than classes
# because "closed → open" (a reopen) and "closed → merged" differ in a way the
# class layer deliberately cannot see: both targets are class CLOSED.
#
# The table is permissive about forward motion and strict about leaving a
# terminal state: a closed ticket may be reopened or merged, and nothing else.
_ALLOWED: Final[dict[Status, frozenset[Status]]] = {
    Status.NEW: frozenset(
        {
            Status.OPEN,
            Status.PENDING,
            Status.WAITING_ON_CUSTOMER,
            Status.ON_HOLD,
            Status.RESOLVED,
            Status.CANCELLED,
            Status.MERGED,
        }
    ),
    Status.OPEN: frozenset(
        {
            Status.PENDING,
            Status.WAITING_ON_CUSTOMER,
            Status.ON_HOLD,
            Status.RESOLVED,
            Status.CANCELLED,
            Status.MERGED,
        }
    ),
    Status.PENDING: frozenset(
        {
            Status.OPEN,
            Status.WAITING_ON_CUSTOMER,
            Status.ON_HOLD,
            Status.RESOLVED,
            Status.CANCELLED,
            Status.MERGED,
        }
    ),
    Status.WAITING_ON_CUSTOMER: frozenset(
        {
            Status.OPEN,
            Status.PENDING,
            Status.ON_HOLD,
            Status.RESOLVED,
            Status.CANCELLED,
            Status.MERGED,
        }
    ),
    Status.ON_HOLD: frozenset(
        {
            Status.OPEN,
            Status.PENDING,
            Status.WAITING_ON_CUSTOMER,
            Status.RESOLVED,
            Status.CANCELLED,
            Status.MERGED,
        }
    ),
    # Resolved may go back to open (the fix did not hold) or close.
    Status.RESOLVED: frozenset({Status.OPEN, Status.CLOSED, Status.CANCELLED}),
    # Terminal states are not dead ends, but leaving one is a deliberate,
    # enumerable act rather than a general edit.
    Status.CLOSED: frozenset({Status.OPEN, Status.MERGED}),
    Status.CANCELLED: frozenset({Status.OPEN}),
    # A merged ticket's life continues on its target. Reopening it would create
    # two live tickets for one piece of work, which is what merging fixed.
    Status.MERGED: frozenset(),
}


def check_transition(current: Status, target: Status) -> None:
    """Raise `TransitionError` unless `current → target` is permitted.

    A no-op transition (`current is target`) is allowed and does nothing: a
    caller re-asserting the status it already has — a retried request, an
    idempotent import — is not an error, and making it one pushes every caller
    into a read-then-compare it can lose a race on.
    """
    if current is target:
        return
    allowed = _ALLOWED[current]
    if target not in allowed:
        permitted = ", ".join(sorted(status.value for status in allowed)) or "nothing"
        raise TransitionError(
            f"cannot move a ticket from {current.value!r} to {target.value!r}; "
            f"from {current.value!r} the permitted targets are {permitted}"
        )


def is_open(status: Status) -> bool:
    """Is this ticket still someone's problem?

    The predicate every product currently hand-writes as a set literal, which
    is where they drift. `OPEN` and `WAITING` both count: a ticket blocked on a
    customer is not finished, it is waiting.
    """
    return status.lifecycle_class in {LifecycleClass.OPEN, LifecycleClass.WAITING}


def sla_clock_runs(status: Status) -> bool:
    """Does elapsed time in this status count against the SLA?

    Only class `OPEN`. Waiting on a customer or a third party pauses it.

    This is a deliberate divergence from `dotmac_sub`, verified in its code
    rather than assumed: `SLA_APPLICABLE_STATUSES` contains
    `waiting_on_customer` and `on_hold`, and `sla_assignment.py`'s transition
    handler treats membership as *resume* —

        elif new_status in SLA_APPLICABLE_STATUSES:
            clock.completed_at = None
            clock.paused_at = None
            if clock.status == SlaClockStatus.paused.value:
                clock.status = SlaClockStatus.running.value

    so moving a Sub ticket to `waiting_on_customer` actively un-pauses a paused
    clock. There is no path that pauses on those statuses. A Sub ticket blocked
    on a customer for a week therefore burns a week of SLA its operator could
    not have prevented. That set reads as "not closed", not as "clock running".

    A product that genuinely wants time-in-waiting to count keeps its own policy
    over `is_open`; it does not get to redefine this one for everyone.
    """
    return status.lifecycle_class is LifecycleClass.OPEN
