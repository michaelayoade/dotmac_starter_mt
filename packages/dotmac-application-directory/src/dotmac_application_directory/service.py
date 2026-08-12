"""The directory's domain service — the one writer of a binding row.

Every mutation goes through here. The Workspace's routes and web handlers are
thin adapters over these functions (ADR-0010, fleet-wide), and nothing outside
this module writes `application_bindings`.

## Every mutation takes ids and locks the row

Mutations take `(tenant_id, binding_id)` rather than an already-loaded object,
and re-read the row `FOR UPDATE` inside the operation. That is not ceremony:

* **Reconciliation is not commutative.** Two reconcilers reading v1 concurrently,
  one observing v2 and the other v3, will both pass the version checks against
  the stale copy they hold — and whichever commits last wins. Without the lock a
  binding can end up storing v2 after v3, and `descriptor_refreshed_at` then
  attests to a descriptor the application has already replaced.
* **A terminal state is not terminal without one.** `DETACHED` is checked
  against the in-memory `binding.state`. Two callers both holding an `ACTIVE`
  object — one detaching, one suspending — both pass `require_transition`, and
  the suspend can land after the detach, resurrecting a binding the tenant
  disconnected.

Taking a caller-supplied object made both races invisible at the call site,
which is why the object-taking signatures were removed rather than supplemented.

On SQLite `FOR UPDATE` is silently omitted by SQLAlchemy, so the unit lane still
runs; the serialisation itself is proven by the PostgreSQL canaries in
`tests/test_application_directory_isolation.py`.

## Transactions

This service never commits and never rolls back. `dotmac_kernel.db` is the one
transaction authority (hard rule 8), and a service that rolls back destroys work
its caller did not know about (hard rule 9). Where a conflict is expected —
attaching a binding that already exists — it uses `conflict_savepoint`, with the
mutation INSIDE the `with` block so the savepoint actually covers the flush.

The row lock is held for the caller's transaction, which is the caller's to
commit. A caller that holds one open across a network call will block every
other writer of that binding; reconcilers must READ the application first and
call in with what they observed, which is why `observed` is a parameter rather
than something this module fetches.

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


class ActivationRefused(DirectoryError):
    """The binding cannot be activated from the descriptor supplied.

    Raised rather than returned, because a caller that ignores a failed
    activation would leave a binding the tenant believes is live in a state that
    cannot serve it.
    """


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


#: The outcomes that mean "the application answered, and we believe it".
_ADOPTING_OUTCOMES = frozenset({ReconcileOutcome.UNCHANGED, ReconcileOutcome.UPDATED})


# ── Reads ────────────────────────────────────────────────────────────────────


def get_binding(
    db: Session, *, tenant_id: UUID, binding_id: UUID
) -> ApplicationBinding:
    """One binding, or `BindingNotFound`.

    Takes `tenant_id` explicitly and filters on it even though RLS already
    scopes the query. Defence in depth is cheap here, and it keeps the function
    correct when called from a session whose scope was primed wrongly.
    """
    return _load(db, tenant_id=tenant_id, binding_id=binding_id, lock=False)


def list_bindings(
    db: Session, *, tenant_id: UUID, states: tuple[BindingState, ...] | None = None
) -> list[ApplicationBinding]:
    """The tenant's portfolio, ordered for stable display.

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


def _load(
    db: Session, *, tenant_id: UUID, binding_id: UUID, lock: bool
) -> ApplicationBinding:
    query = select(ApplicationBinding).where(
        ApplicationBinding.tenant_id == tenant_id,
        ApplicationBinding.id == binding_id,
    )
    if lock:
        # Serialises concurrent mutations of this binding. Silently omitted on
        # SQLite, which is why the guarantee is proven on PostgreSQL.
        query = query.with_for_update()
    binding = db.scalars(query).one_or_none()
    if binding is None:
        raise BindingNotFound(f"no binding {binding_id} for tenant {tenant_id}")
    return binding


# ── Mutations ────────────────────────────────────────────────────────────────


def attach_application(
    db: Session,
    *,
    tenant_id: UUID,
    descriptor: ApplicationDescriptor,
    source: BindingSource,
) -> ApplicationBinding:
    """Create the binding for one application instance, as `INVITED`.

    **There is no `state` parameter, and that is the point.** A caller could
    previously pass `state=ACTIVE` with no descriptor read behind it, producing a
    binding that was launchable and had never been verified — `ACTIVE` would then
    mean "someone asserted this", which is not a claim a launcher can rest on.

    Every binding therefore starts at `INVITED` with `reconciliation_status`
    `UNKNOWN` — the honest statement that nothing has been confirmed by the
    application itself. `ACTIVE` is reachable only through `activate_binding`,
    which requires a descriptor actually read from the application.
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
        state=str(BindingState.INVITED),
        source=str(source),
        descriptor_refreshed_at=None,
        reconciliation_status=str(ReconciliationStatus.UNKNOWN),
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


def activate_binding(
    db: Session,
    *,
    tenant_id: UUID,
    binding_id: UUID,
    observed: ApplicationDescriptor,
    now: datetime,
) -> ApplicationBinding:
    """Activate a binding against a descriptor read from the application.

    The ONLY route to `ACTIVE`. `observed` is the proof: it is what the
    application answered when asked, and activation succeeds only if
    reconciling it ADOPTS — a regression or a same-version content conflict
    refuses, because neither is a descriptor to make a binding launchable on.

    Also requires the application to agree about which of its tenants this
    binding is for. A descriptor whose `local_tenant_ref` differs from the
    stored one is not proof of this binding; it is proof of a different one.
    """
    binding = _load(db, tenant_id=tenant_id, binding_id=binding_id, lock=True)
    require_transition(BindingState(binding.state), BindingState.ACTIVE)

    # Captured BEFORE reconciling, which ADOPTS `local_tenant_ref` from the
    # observed descriptor — comparing afterwards would compare the value to
    # itself and pass unconditionally.
    claimed_local_tenant = binding.local_tenant_ref

    outcome = _reconcile_locked(db, binding, observed, now=now)
    if outcome not in _ADOPTING_OUTCOMES:
        raise ActivationRefused(
            f"cannot activate {binding_id}: the descriptor read from the "
            f"application was not adopted ({outcome}). "
            f"{binding.reconciliation_error}"
        )
    if observed.local_tenant_ref != claimed_local_tenant:
        raise ActivationRefused(
            f"cannot activate {binding_id}: the application reports local tenant "
            f"{observed.local_tenant_ref!r}, not the {claimed_local_tenant!r} "
            f"this binding was created for"
        )

    binding.state = str(BindingState.ACTIVE)
    db.flush()
    return binding


def reconcile_descriptor(
    db: Session,
    *,
    tenant_id: UUID,
    binding_id: UUID,
    observed: ApplicationDescriptor,
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

    The row is locked for the caller's transaction, so two reconcilers cannot
    both pass their version checks against the same stale copy and commit out of
    order.
    """
    binding = _load(db, tenant_id=tenant_id, binding_id=binding_id, lock=True)
    return _reconcile_locked(db, binding, observed, now=now)


def _reconcile_locked(
    db: Session,
    binding: ApplicationBinding,
    observed: ApplicationDescriptor,
    *,
    now: datetime,
) -> ReconcileOutcome:
    """The reconciliation decision itself, on an already-locked row.

    Private because calling it without the lock is the race the public entry
    points exist to close.
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
        db.flush()
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
        db.flush()
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
    db: Session, *, tenant_id: UUID, binding_id: UUID, error: str
) -> ApplicationBinding:
    """Record that the descriptor could not be read at all.

    Deliberately does NOT touch `descriptor_refreshed_at`: the copy is as old as
    it was, and moving the timestamp on a failed read would make an unreachable
    application look freshly checked.
    """
    binding = _load(db, tenant_id=tenant_id, binding_id=binding_id, lock=True)
    binding.reconciliation_status = str(ReconciliationStatus.FAILED)
    binding.reconciliation_error = error
    db.flush()
    return binding


def transition(
    db: Session, *, tenant_id: UUID, binding_id: UUID, target: BindingState
) -> ApplicationBinding:
    """Suspend, resume-toward-verification, or detach a binding.

    Refuses `ACTIVE` outright. Activation requires proof that the application
    answered, and this operation has none to offer — routing it here would put
    back exactly the unverified-but-launchable binding `activate_binding`
    exists to prevent. The refusal names the alternative so the caller is not
    left guessing.

    The row is locked, so a transition cannot be decided against a state another
    transaction has already left — the race that let a suspend land after a
    detach and resurrect a disconnected binding.
    """
    if target is BindingState.ACTIVE:
        raise DirectoryError(
            "a binding is activated by `activate_binding`, which requires a "
            "descriptor read from the application — `transition` cannot make a "
            "binding launchable on an assertion alone"
        )
    binding = _load(db, tenant_id=tenant_id, binding_id=binding_id, lock=True)
    require_transition(BindingState(binding.state), target)
    binding.state = str(target)
    db.flush()
    return binding


__all__ = [
    "ActivationRefused",
    "BindingAlreadyExists",
    "BindingNotFound",
    "DirectoryError",
    "ReconcileOutcome",
    "activate_binding",
    "attach_application",
    "get_binding",
    "launchable_bindings",
    "list_bindings",
    "mark_reconciliation_failed",
    "reconcile_descriptor",
    "transition",
]
