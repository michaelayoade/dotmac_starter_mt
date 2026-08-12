"""The directory's domain service — the one writer of a binding row.

Every mutation goes through here. The Workspace's routes and web handlers are
thin adapters over these functions (ADR-0010, fleet-wide), and nothing outside
this module writes `application_bindings`.

## Transactions

This service never commits and never rolls back. `dotmac_kernel.db` is the one
transaction authority (hard rule 8), and a service that rolls back destroys work
its caller did not know about (hard rule 9). Where a conflict is expected —
attaching a binding that already exists — it uses `conflict_savepoint`, with the
mutation INSIDE the `with` block so the savepoint actually covers the flush.

## Clocks are parameters

Every function that records a time takes `now` explicitly. Reconciliation state
is exactly the kind of thing whose tests need to move time around, and a service
that reads the wall clock internally cannot be tested for staleness without
sleeping.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from dotmac_kernel.db import conflict_savepoint
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_application_directory.descriptor import ApplicationDescriptor
from dotmac_application_directory.lifecycle import (
    BindingSource,
    BindingState,
    ReconciliationStatus,
    is_launchable,
    require_transition,
)
from dotmac_application_directory.models import ApplicationBinding


class DirectoryError(Exception):
    """Base for the directory's refusals."""


class BindingAlreadyExists(DirectoryError):
    """This tenant already holds a binding for that application instance."""


class BindingNotFound(DirectoryError):
    """No such binding for this tenant."""


class ReconcileOutcome(enum.StrEnum):
    """What a reconciliation pass concluded.

    Returned rather than inferred from the row afterwards, so a caller can log
    or audit the decision without re-deriving it — and so `CONFLICT` cannot be
    mistaken for `UNCHANGED`, which is what a boolean return would have done.
    """

    #: Observed descriptor is identical to the stored copy.
    UNCHANGED = "unchanged"
    #: Observed descriptor is newer; the stored copy has been replaced.
    UPDATED = "updated"
    #: Observed descriptor is BEHIND the stored copy — not adopted.
    REGRESSED = "regressed"
    #: Same version, different content — not adopted. A defect or tampering.
    CONFLICT = "conflict"


def attach_application(
    db: Session,
    *,
    tenant_id: UUID,
    descriptor: ApplicationDescriptor,
    source: BindingSource,
    state: BindingState = BindingState.INVITED,
    now: datetime | None = None,
) -> ApplicationBinding:
    """Create the binding for one application instance.

    `state` defaults to `INVITED` — the honest starting point, because at
    creation nothing has been confirmed by the application itself. A caller
    that genuinely has proof (a customer attaching an application they already
    administer) may pass `ACTIVE`; the lifecycle permits that edge directly.

    `now` is recorded as `descriptor_refreshed_at` ONLY when the descriptor was
    actually read from the application. Passing it means "I read this"; omitting
    it leaves the copy marked `UNKNOWN`, which is what an invitation created
    from a vendor allocation should say.
    """
    binding = ApplicationBinding(
        tenant_id=tenant_id,
        application_code=descriptor.application_code,
        instance_ref=descriptor.instance_ref,
        local_tenant_ref=descriptor.local_tenant_ref,
        admin_url=descriptor.admin_url,
        api_audience=descriptor.api_audience,
        descriptor_version=descriptor.descriptor_version,
        descriptor_digest=descriptor.digest,
        role_catalogue_digest=descriptor.role_catalogue_digest,
        state=str(state),
        source=str(source),
        descriptor_refreshed_at=now,
        reconciliation_status=str(
            ReconciliationStatus.FRESH if now else ReconciliationStatus.UNKNOWN
        ),
        reconciliation_error=None,
    )
    try:
        with conflict_savepoint(db):
            db.add(binding)
            db.flush()
    except IntegrityError as exc:
        raise BindingAlreadyExists(
            f"tenant already holds a binding for "
            f"{descriptor.application_code!r}/{descriptor.instance_ref!r}"
        ) from exc
    return binding


def reconcile_descriptor(
    db: Session,
    binding: ApplicationBinding,
    observed: ApplicationDescriptor,
    *,
    now: datetime,
) -> ReconcileOutcome:
    """Compare a freshly read descriptor with the stored copy and act on it.

    The four outcomes are not symmetric, and the asymmetry is the point:

    * **UNCHANGED / UPDATED** — the read is believable. Adopt (or keep) the copy
      and mark it `FRESH`.
    * **REGRESSED** — the application reported a version behind the stored one.
      Do not adopt. This is usually a lagging replica rather than a fault, so it
      is `STALE` rather than `FAILED`, and the stored copy is left alone.
    * **CONFLICT** — the same `descriptor_version` carrying different content.
      Do not adopt, and mark `FAILED`. A version is a promise that content did
      not change beneath it; adopting silently would make every later digest
      comparison meaningless, and the case is indistinguishable from tampering.

    Refuses a descriptor for a different application outright — a caller that
    has fetched the wrong instance must not be able to overwrite this row with
    it.
    """
    if (
        observed.application_code != binding.application_code
        or observed.instance_ref != binding.instance_ref
    ):
        raise DirectoryError(
            f"descriptor {observed.application_code!r}/{observed.instance_ref!r} "
            f"does not describe binding "
            f"{binding.application_code!r}/{binding.instance_ref!r}"
        )

    observed_digest = observed.digest
    if observed.descriptor_version < binding.descriptor_version:
        binding.reconciliation_status = str(ReconciliationStatus.STALE)
        binding.reconciliation_error = (
            f"application reported descriptor_version "
            f"{observed.descriptor_version}, behind the stored "
            f"{binding.descriptor_version}; stored copy kept"
        )
        return ReconcileOutcome.REGRESSED

    if (
        observed.descriptor_version == binding.descriptor_version
        and observed_digest != binding.descriptor_digest
    ):
        binding.reconciliation_status = str(ReconciliationStatus.FAILED)
        binding.reconciliation_error = (
            f"descriptor_version {observed.descriptor_version} carries content "
            f"differing from the stored copy ({observed_digest} != "
            f"{binding.descriptor_digest}); not adopted"
        )
        return ReconcileOutcome.CONFLICT

    unchanged = observed_digest == binding.descriptor_digest
    binding.local_tenant_ref = observed.local_tenant_ref
    binding.admin_url = observed.admin_url
    binding.api_audience = observed.api_audience
    binding.descriptor_version = observed.descriptor_version
    binding.descriptor_digest = observed_digest
    binding.role_catalogue_digest = observed.role_catalogue_digest
    binding.descriptor_refreshed_at = now
    binding.reconciliation_status = str(ReconciliationStatus.FRESH)
    # Cleared on every success, so a stale explanation cannot outlive the
    # failure it described.
    binding.reconciliation_error = None
    db.flush()
    return ReconcileOutcome.UNCHANGED if unchanged else ReconcileOutcome.UPDATED


def mark_reconciliation_failed(
    db: Session, binding: ApplicationBinding, *, error: str
) -> None:
    """Record that the descriptor could not be read at all.

    Deliberately does NOT touch `descriptor_refreshed_at`: the copy is as old as
    it was, and moving the timestamp on a failed read would make an unreachable
    application look freshly checked.
    """
    binding.reconciliation_status = str(ReconciliationStatus.FAILED)
    binding.reconciliation_error = error
    db.flush()


def transition(
    db: Session, binding: ApplicationBinding, target: BindingState
) -> ApplicationBinding:
    """Move a binding to `target`, or raise `BindingLifecycleError`."""
    require_transition(BindingState(binding.state), target)
    binding.state = str(target)
    db.flush()
    return binding


def get_binding(
    db: Session, *, tenant_id: UUID, binding_id: UUID
) -> ApplicationBinding:
    """One binding, or `BindingNotFound`.

    Takes `tenant_id` explicitly and filters on it even though RLS already
    scopes the query. Defence in depth is cheap here, and it keeps the function
    correct when called from a session whose scope was primed wrongly.
    """
    binding = db.scalars(
        select(ApplicationBinding).where(
            ApplicationBinding.tenant_id == tenant_id,
            ApplicationBinding.id == binding_id,
        )
    ).one_or_none()
    if binding is None:
        raise BindingNotFound(f"no binding {binding_id} for tenant {tenant_id}")
    return binding


def list_bindings(
    db: Session, *, tenant_id: UUID, states: tuple[BindingState, ...] | None = None
) -> list[ApplicationBinding]:
    """The tenant's portfolio, newest application code first for stable display.

    `states` filters; `None` means every state INCLUDING detached, because an
    administrator auditing what was disconnected needs to see it.
    """
    query = select(ApplicationBinding).where(ApplicationBinding.tenant_id == tenant_id)
    if states is not None:
        query = query.where(ApplicationBinding.state.in_([str(s) for s in states]))
    query = query.order_by(
        ApplicationBinding.application_code, ApplicationBinding.instance_ref
    )
    return list(db.scalars(query))


def launchable_bindings(db: Session, *, tenant_id: UUID) -> list[ApplicationBinding]:
    """The bindings a launcher may render a tile for.

    **Launchable is not authorized.** This is the portfolio the tenant has, not
    the set the viewing member may enter; the target application authenticates
    and authorizes whoever follows the link (ADR-0021 §3). The filter is derived
    from `lifecycle.is_launchable` rather than hard-coding `ACTIVE`, so the two
    cannot drift apart.
    """
    states = tuple(state for state in BindingState if is_launchable(state))
    return list_bindings(db, tenant_id=tenant_id, states=states)


__all__ = [
    "BindingAlreadyExists",
    "BindingNotFound",
    "DirectoryError",
    "ReconcileOutcome",
    "attach_application",
    "get_binding",
    "launchable_bindings",
    "list_bindings",
    "mark_reconciliation_failed",
    "reconcile_descriptor",
    "transition",
]
