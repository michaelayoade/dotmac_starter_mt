"""Status reasons — the one layer a product extends, and how it is kept honest.

A *reason* answers "why is this ticket in this status", and it is the home for
every term the standard nine statuses cannot express: `lastmile_rerun`,
`site_under_construction`, `awaiting_parts`, `vendor_escalation`,
`awaiting_customer_confirmation`.

## Why a registry and not an enum

ADR-0008, applied to a lifecycle instead of a settings domain. A vocabulary that
must hold Sub's ISP operations *and* ERP's helpdesk states *and* stay open for
the next product is a declaration registry, never an enum and never a CHECK
constraint. The same rule already governs `SettingDomain`, and the same failures
motivate it: a native Postgres enum needs an `ALTER TYPE` migration to grow, and
a merged enum means every product carries every other product's terms.

## Why a reason is not just a tag

Both are filterable, so the distinction has to be behavioural:

* **reason** — code branches on it, or a rule/notification/routing condition
  selects on it. It is declared, has an owning module, and CI can require a
  consumer. `lastmile_rerun` qualifies: Sub's assignment selectors and
  notification-template conditions both read it.
* **tag** — only humans search it. Free-form, operator-created, no declaration.

If you cannot name the code that branches on a term, it is a tag. Declaring it
as a reason instead buys nothing and adds a registry row that will rot.

## Scoping

A reason is declared against the statuses it may accompany. `awaiting_parts`
belongs to `on_hold`; attaching it to `closed` is meaningless, and the registry
rejects it at write time rather than letting a nonsense pair reach a report. A
reason declared for no status at all is a tag with extra ceremony, so at least
one is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from dotmac_ticketing.lifecycle import Status

__all__ = [
    "ReasonSpec",
    "UnknownReasonError",
    "register_reasons",
    "registered_reasons",
    "reset_registry_for_tests",
    "validate_reason",
]

_REASON_CODE_MAX = 64


class UnknownReasonError(ValueError):
    """A reason that was never declared, or is not valid for this status."""


@dataclass(frozen=True, slots=True)
class ReasonSpec:
    """One product-declared reason.

    `owner` is the declaring module's manifest `code`. It is required for the
    same purpose as every other declaration registry in the fleet: a code with
    no named owner cannot be reviewed, deprecated, or blamed.
    """

    code: str
    description: str
    owner: str
    statuses: frozenset[Status] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.code or len(self.code) > _REASON_CODE_MAX:
            raise ValueError(
                f"reason code {self.code!r} must be 1..{_REASON_CODE_MAX} characters"
            )
        if self.code != self.code.strip().lower():
            # Canonical form, for the same reason consent canonicalises a
            # channel: two spellings of one reason silently split every report
            # and every routing rule that filters on it.
            raise ValueError(
                f"reason code {self.code!r} must be lowercase and unstripped of "
                "surrounding whitespace"
            )
        if not self.description.strip():
            raise ValueError(f"reason {self.code!r} needs a description")
        if not self.owner.strip():
            raise ValueError(f"reason {self.code!r} needs an owning module code")
        if not self.statuses:
            raise ValueError(
                f"reason {self.code!r} must declare at least one status it is "
                "valid for — a reason valid everywhere is a tag, not a reason"
            )


_REGISTRY: dict[str, ReasonSpec] = {}


def register_reasons(specs: tuple[ReasonSpec, ...] | list[ReasonSpec]) -> None:
    """Declare reasons at import time, from the owning module.

    Re-registering an identical spec is a no-op, so a module imported twice does
    not fail. Re-registering a DIFFERENT spec under the same code raises: two
    products silently disagreeing about what `awaiting_parts` means is precisely
    the vocabulary fork this registry exists to prevent.
    """
    for spec in specs:
        existing = _REGISTRY.get(spec.code)
        if existing is None:
            _REGISTRY[spec.code] = spec
            continue
        if existing != spec:
            raise ValueError(
                f"reason {spec.code!r} is already declared by {existing.owner!r} "
                f"with a different specification; {spec.owner!r} cannot redeclare "
                "it. Pick a distinct code, or agree one specification and share it."
            )


def registered_reasons() -> tuple[ReasonSpec, ...]:
    """Every declared reason, ordered by code. Used by admin UIs and by CI."""
    return tuple(_REGISTRY[code] for code in sorted(_REGISTRY))


def validate_reason(reason: str | None, status: Status) -> str | None:
    """Return the canonical reason for `status`, or raise.

    `None` is always valid: most tickets in most statuses have no reason, and
    requiring one would push every caller into inventing a filler code.
    """
    if reason is None:
        return None
    code = reason.strip().lower()
    if not code:
        return None
    spec = _REGISTRY.get(code)
    if spec is None:
        known = ", ".join(sorted(_REGISTRY)) or "none are declared"
        raise UnknownReasonError(
            f"reason {code!r} is not declared. Declare it on the owning module's "
            f"manifest via register_reasons(...). Declared reasons: {known}"
        )
    if status not in spec.statuses:
        valid = ", ".join(sorted(s.value for s in spec.statuses))
        raise UnknownReasonError(
            f"reason {code!r} is declared by {spec.owner!r} for status(es) "
            f"{valid}, and cannot be attached to {status.value!r}"
        )
    return code


def reset_registry_for_tests() -> dict[str, ReasonSpec]:
    """Clear the registry and return what was there, for test isolation.

    Named for what it is. The registry is process-global by design — it is
    populated at import time from manifests — so a test that registers a reason
    must be able to undo it without reaching into module internals.
    """
    previous = dict(_REGISTRY)
    _REGISTRY.clear()
    return previous


#: A minimal set the module itself declares, because two of the nine standard
#: statuses are close to useless without one. `pending` means "blocked, cause
#: unstated", and `cancelled` without a reason loses the only fact anyone will
#: later want. Products may declare more; they may not redeclare these.
CORE_REASONS: Final[tuple[ReasonSpec, ...]] = (
    ReasonSpec(
        code="awaiting_customer_confirmation",
        description="Resolved, waiting for the requester to confirm the fix.",
        owner="ticketing",
        statuses=frozenset({Status.RESOLVED}),
    ),
    ReasonSpec(
        code="awaiting_third_party",
        description="Blocked on a supplier, vendor or other external party.",
        owner="ticketing",
        statuses=frozenset({Status.ON_HOLD, Status.PENDING}),
    ),
    ReasonSpec(
        code="duplicate",
        description="Closed or cancelled because another ticket covers this work.",
        owner="ticketing",
        statuses=frozenset({Status.CANCELLED, Status.CLOSED, Status.MERGED}),
    ),
    ReasonSpec(
        code="withdrawn_by_requester",
        description="The requester withdrew the request before it was worked.",
        owner="ticketing",
        statuses=frozenset({Status.CANCELLED}),
    ),
)
