"""The binding lifecycle, its sources, and its reconciliation state.

Three closed vocabularies, each with exactly one job. All three are `StrEnum`
closed at the PYTHON layer and stored as text, for the reason ADR-0008 records
and `dotmac_ticketing.lifecycle` follows: a native database enum or a CHECK
constraint turns "add a state" into an `ALTER` on every deployment, and the
vocabulary is enforceable and testable in the service layer instead.

## No state means "authorized"

The most important thing in this module is a negative. `ACTIVE` means the
binding is usable — the tenant has this application, the descriptor is
believable, a launcher may render a tile for it. It does **not** mean any
particular person may enter, and no state here ever will (ADR-0021 §3).

That distinction is why the reachability predicate below is called
`is_launchable` rather than anything containing "allowed", "permitted" or
"granted". A predicate named for authorization is a predicate someone will
eventually use for authorization.
"""

from __future__ import annotations

import enum
from types import MappingProxyType
from typing import Final


class BindingLifecycleError(ValueError):
    """An illegal binding transition was attempted. Raised rather than returned
    because a caller that ignores an illegal transition leaves the row in a
    state its own history cannot explain."""


class BindingState(enum.StrEnum):
    """Where a binding is in its life.

    `INVITED`
        The Workspace knows the application should exist for this tenant, from
        a vendor allocation or an administrator's action. Nothing has been
        confirmed by the application itself.
    `PENDING_VERIFICATION`
        The application has been contacted and has not yet confirmed the
        binding — the local tenant reference is claimed, not proven.
    `ACTIVE`
        Confirmed and believable. The only launchable state.
    `SUSPENDED`
        Temporarily unusable, by the tenant's or the vendor's decision. The row
        and its history are kept; only launchability is withdrawn.
    `DETACHED`
        Terminal. The application is no longer connected to this tenant.
    """

    INVITED = "invited"
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DETACHED = "detached"


class BindingSource(enum.StrEnum):
    """How the binding came to exist — provenance, never authority.

    A `CUSTOMER_ATTACHED` binding is not weaker than a `VENDOR_ALLOCATION` one
    in any security sense; both are inventory. The distinction exists so the
    Workspace can explain to an administrator why a tile appeared, and so a
    reconciler knows whose statement to re-read when a binding drifts: a
    vendor-allocated binding is re-derived from the vendor plane, a
    customer-attached one is not.
    """

    VENDOR_ALLOCATION = "vendor_allocation"
    OEM_ALLOCATION = "oem_allocation"
    CUSTOMER_ATTACHED = "customer_attached"


class ReconciliationStatus(enum.StrEnum):
    """How much the Workspace currently trusts its copy of the descriptor.

    `UNKNOWN`
        Never successfully read. The initial state, and deliberately not
        `STALE` — "we have never looked" and "we looked and it has moved on"
        are different operational problems.
    `FRESH`
        Last read succeeded and matched the stored digest.
    `STALE`
        The last read completed but returned a descriptor the Workspace did
        NOT adopt — most often one whose version is behind the stored copy,
        which happens against a lagging replica. The stored copy is intact but
        may no longer reflect the application, and that is worth saying out
        loud rather than reporting as fresh.
    `FAILED`
        The last read did not complete, or returned something contradictory —
        the same `descriptor_version` carrying different content, which is an
        application defect or tampering and must never be adopted silently.
        `reconciliation_error` says which.
    """

    UNKNOWN = "unknown"
    FRESH = "fresh"
    STALE = "stale"
    FAILED = "failed"


#: The legal moves. Read as: from this state, to any of these.
#:
#: Two properties are deliberate. `DETACHED` is terminal and appears in no
#: value tuple — a detached application that returns is a NEW binding, because
#: reusing the row would silently carry forward a local tenant reference the
#: application may have reassigned in the meantime. And every non-terminal
#: state may go straight to `DETACHED`: disconnecting must never require first
#: repairing a binding that is already broken.
_TRANSITIONS: Final[MappingProxyType[BindingState, frozenset[BindingState]]] = (
    MappingProxyType(
        {
            BindingState.INVITED: frozenset(
                {
                    BindingState.PENDING_VERIFICATION,
                    # Straight to ACTIVE is legal: a customer attaching an
                    # application they already administer can present proof in
                    # the same action that creates the binding.
                    BindingState.ACTIVE,
                    BindingState.DETACHED,
                }
            ),
            BindingState.PENDING_VERIFICATION: frozenset(
                {
                    BindingState.ACTIVE,
                    # Back to INVITED when verification is abandoned rather than
                    # refused, so a retry does not need a new row.
                    BindingState.INVITED,
                    BindingState.DETACHED,
                }
            ),
            BindingState.ACTIVE: frozenset(
                {BindingState.SUSPENDED, BindingState.DETACHED}
            ),
            BindingState.SUSPENDED: frozenset(
                {BindingState.ACTIVE, BindingState.DETACHED}
            ),
            BindingState.DETACHED: frozenset(),
        }
    )
)


def allowed_transitions(state: BindingState) -> frozenset[BindingState]:
    """The states reachable from `state` in one move."""
    return _TRANSITIONS[state]


def can_transition(current: BindingState, target: BindingState) -> bool:
    """Whether `current -> target` is legal.

    A self-transition is False rather than True: re-asserting the current state
    is a no-op the caller should recognise as one, and silently permitting it
    hides the case where a caller believed it was making a change.
    """
    return target in _TRANSITIONS[current]


def require_transition(current: BindingState, target: BindingState) -> None:
    """Raise `BindingLifecycleError` unless `current -> target` is legal."""
    if not can_transition(current, target):
        allowed = ", ".join(sorted(_TRANSITIONS[current])) or "(terminal)"
        raise BindingLifecycleError(
            f"illegal binding transition {current} -> {target}; "
            f"allowed from {current}: {allowed}"
        )


def is_launchable(state: BindingState) -> bool:
    """Whether a launcher may render a tile for a binding in this state.

    **Launchable is not authorized.** This answers "does the tenant have this
    application, and is our record of it believable" — nothing about the person
    looking at the screen. The target application authenticates and authorizes
    whoever follows the link (ADR-0021 §3).
    """
    return state is BindingState.ACTIVE


__all__ = [
    "BindingLifecycleError",
    "BindingSource",
    "BindingState",
    "ReconciliationStatus",
    "allowed_transitions",
    "can_transition",
    "is_launchable",
    "require_transition",
]
