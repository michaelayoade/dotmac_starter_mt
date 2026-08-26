"""The dispatch seam — resolve exactly one enabled binding, or fail closed.

This module exists because *enabled* and *selected* are different questions, and
conflating them is what makes people reach for a global uniqueness constraint
that ADR-0024 § 7 never asked for.

============ =============================================== ================
concept      meaning                                         multiplicity
============ =============================================== ================
**enabled**  this installation is capable and permitted to   **many** may be
             implement the capability                        enabled at once
**selected** the binding chosen for ONE concrete dispatch    **exactly one**
============ =============================================== ================

So the schema constrains
`(installation_id, capability_id, capability_instance_ref)`, and every
remaining ambiguity is resolved *here*, per dispatch, where the caller's
intent is available.

## Explicit binding is the generic seam

A caller that knows which binding it wants passes `capability_binding_id`. That
is the primary path and it is unambiguous by construction — no policy, no
defaulting, no ordering. Everything below is the fallback for callers that only
know a capability.

## Fail closed, in all three directions

* **absent** — no enabled binding: refuse. Never "do nothing quietly", which
  reads as success to a caller that just handed over a payload.
* **stale** — the named binding exists but is disabled, or its installation is
  not enabled: refuse. A binding is only usable if the whole chain is.
* **ambiguous** — several enabled and no single `policy_json.default`: refuse,
  and say which installations collide so the operator can fix it in one step.

Ported from `dotmac_sub`'s `require_enabled_capability_binding`, which already
had this shape. `scope_json` is not consulted — in the source it is displayed,
never routed on — and adding it here would be inventing routing semantics the
evidence does not support.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from dotmac_integration.capability_instances import require_capability_instance_ref
from dotmac_integration.models import CapabilityBinding, ConnectorInstallation

__all__ = [
    "AmbiguousBindingError",
    "NoEnabledBindingError",
    "SelectionError",
    "resolve_binding",
]


class SelectionError(RuntimeError):
    """Dispatch could not be resolved to exactly one usable binding."""


class NoEnabledBindingError(SelectionError):
    """Nothing enabled can serve this capability — absent or stale."""


class AmbiguousBindingError(SelectionError):
    """Several bindings are enabled and none is the declared default."""


def _usable(binding: CapabilityBinding, installation: ConnectorInstallation) -> bool:
    return binding.state == "enabled" and installation.state == "enabled"


def resolve_binding(
    db: Any,
    *,
    capability_id: str,
    capability_instance_ref: str | None = None,
    capability_binding_id: UUID | None = None,
    connector_key: str | None = None,
) -> CapabilityBinding:
    """Return the ONE binding this dispatch must use.

    :param capability_binding_id: the explicit seam. When given, it is the
        answer — subject only to the usability check, because a caller naming a
        disabled binding has a stale reference and must be told, not silently
        rerouted to a different connector.
    :param connector_key: narrows the candidate set for callers that know which
        connector they want but not which installation.
    """
    from sqlalchemy import select

    query = (
        select(CapabilityBinding, ConnectorInstallation)
        .join(
            ConnectorInstallation,
            ConnectorInstallation.id == CapabilityBinding.installation_id,
        )
        .where(CapabilityBinding.capability_id == capability_id)
    )
    if capability_instance_ref is not None:
        query = query.where(
            CapabilityBinding.capability_instance_ref
            == require_capability_instance_ref(capability_instance_ref)
        )
    if capability_binding_id is not None:
        query = query.where(CapabilityBinding.id == capability_binding_id)
    if connector_key is not None:
        query = query.where(
            ConnectorInstallation.connector_key == connector_key.strip().lower()
        )

    # Typed once, here: `db.execute(...).all()` yields untyped Rows, which made
    # every `return` below an implicit Any escaping a declared return type.
    rows: list[tuple[CapabilityBinding, ConnectorInstallation]] = list(
        db.execute(query).all()
    )

    if capability_binding_id is not None:
        if not rows:
            raise NoEnabledBindingError(
                f"binding {capability_binding_id} does not serve capability "
                f"{capability_id!r} — the reference is stale or wrong"
            )
        binding, installation = rows[0]
        if not _usable(binding, installation):
            raise NoEnabledBindingError(
                f"binding {capability_binding_id} is not usable "
                f"(binding={binding.state}, installation={installation.state}). "
                "A named binding is never silently rerouted to another "
                "connector"
            )
        return binding

    usable = [
        (binding, installation)
        for binding, installation in rows
        if _usable(binding, installation)
    ]
    if not usable:
        raise NoEnabledBindingError(
            f"no enabled binding for capability {capability_id!r}"
            + (f" on connector {connector_key!r}" if connector_key else "")
        )
    if len(usable) == 1:
        return usable[0][0]

    defaults = [
        binding
        for binding, _ in usable
        if (binding.policy_json or {}).get("default") is True
    ]
    if len(defaults) != 1:
        colliding = sorted(
            f"{installation.connector_key}/{installation.name}"
            for _, installation in usable
        )
        raise AmbiguousBindingError(
            f"{len(usable)} enabled bindings serve {capability_id!r} "
            f"({', '.join(colliding)}) and "
            f"{len(defaults)} declare policy_json.default — exactly one must. "
            "Name a capability_binding_id, or mark one default"
        )
    return defaults[0]
