"""Q-F: staff release of frozen expected withholding (tenant plane, Billing alone).

At issue Billing freezes each elected expected-withholding component (bi_0016).
When the customer in fact does not deduct, the component can never be
discharged and its undischarged residual blocks the invoice's cash requirement
for good. ``release_withholding_component`` appends one ``withholding_releases``
row for the residual (a "Tax correction" by a permission holder); the cash
requirement becomes frozen minus discharged minus released, and a later
discharge of that component is refused by the service and by the database cap.
``reverse_withholding_release`` appends the exact inverse while the invoice
still has the room.

A release only raises the cash the customer owes: it reduces no debt and writes
nothing off. Nothing was booked for the expectation, so there is no posting
group, AccountingFact, exposure or position row; the one outbox event
``billing.withholding.released.v1`` carries ids only. Commands run in the
caller's transaction and never commit. No Tax write and no Tax seal read: Tax's
determination stays true, only Billing's own expectation is released.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_billing import models, service
from dotmac_billing.commands import (
    WITHHOLDING_RELEASE_PERMISSION,
    ActorPermissionReader,
    ReleaseWithholdingComponent,
    ReverseWithholdingRelease,
)
from dotmac_billing.errors import BillingConflict, BillingRuleViolation

RELEASED_EVENT_TYPE = "billing.withholding.released.v1"
_RELEASE_SCOPE = "billing.withholding.release"
_REVERSE_SCOPE = "billing.withholding.release.reverse"


@dataclass(frozen=True, slots=True)
class WithholdingReleaseV1:
    """A stored release (positive) or its undo (negative, ``reverses_release_id``)."""

    release_id: UUID
    billing_account_id: UUID
    document_id: UUID
    component_id: UUID
    amount_delta: Money
    reverses_release_id: UUID | None
    command_id: str
    actor_ref: str
    reason: str
    evidence_ref: str | None
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class WithholdingComponentPositionV1:
    """One frozen component's draw: ``residual = frozen - discharged - released``."""

    component_id: UUID
    frozen: Money
    discharged: Money
    released: Money
    residual: Money


def _tenant_only(scope: Scope) -> TenantScope:
    if not isinstance(scope, TenantScope):
        raise BillingRuleViolation(
            "withholding_release_platform_unsupported",
            "withholding releases are tenant-plane only; the platform plane "
            "stays fail-closed",
        )
    return scope


def _authorize(
    *,
    scope: Scope,
    declared: Scope,
    permissions: ActorPermissionReader | None,
    actor_ref: str,
    texts: tuple[tuple[str, int], ...],
    evidence_ref: str | None = None,
) -> TenantScope:
    tenant = _tenant_only(scope)
    if permissions is None:
        raise BillingRuleViolation(
            "withholding_release_permission_unavailable",
            "no permission reader is wired; the release cannot be authorized",
        )
    service._require_scope_matches(routed=scope, declared=declared)
    for text, maximum in texts:
        if not isinstance(text, str) or not text.strip() or len(text) > maximum:
            raise BillingRuleViolation(
                "withholding_release_evidence_required",
                "a release requires a bounded command id, actor and reason",
            )
    if evidence_ref is not None and (
        not isinstance(evidence_ref, str)
        or not evidence_ref.strip()
        or len(evidence_ref) > 255
    ):
        raise BillingRuleViolation(
            "withholding_release_evidence_required",
            "an evidence reference, when given, must be non-blank and bounded",
        )
    if not permissions.holds(
        scope=scope, actor_ref=actor_ref, code=WITHHOLDING_RELEASE_PERMISSION
    ):
        raise BillingRuleViolation(
            "withholding_release_permission_denied",
            "the actor does not hold the withholding release permission",
        )
    return tenant


def _sum(db: Session, column: Any, *where: Any) -> Decimal:
    return Decimal(
        db.execute(
            select(func.coalesce(func.sum(column), 0)).where(*where)
        ).scalar_one()
    )


def _discharged(
    db: Session, *, scope: TenantScope, component: models.DocumentWithholdingComponent
) -> Decimal:
    w = models.WithholdingDischarge
    return _sum(
        db,
        w.amount_delta,
        w.tenant_id == scope.tenant_id,
        w.document_id == component.document_id,
        w.tax_source_owner == component.tax_source_owner,
        w.determination_ref == component.determination_ref,
        w.component_ref == component.component_ref,
    )


def _released(db: Session, *, scope: TenantScope, component_id: UUID) -> Decimal:
    r = models.WithholdingRelease
    return _sum(
        db,
        r.amount_delta,
        r.tenant_id == scope.tenant_id,
        r.component_id == component_id,
    )


def _money(value: Decimal, currency: str, minor_units: int) -> Money:
    return service._money(value, currency, minor_units)


def _view(row: models.WithholdingRelease) -> WithholdingReleaseV1:
    return WithholdingReleaseV1(
        release_id=row.id,
        billing_account_id=row.billing_account_id,
        document_id=row.document_id,
        component_id=row.component_id,
        amount_delta=_money(Decimal(row.amount_delta), row.currency, row.minor_units),
        reverses_release_id=row.reverses_release_id,
        command_id=row.command_id,
        actor_ref=row.actor_ref,
        reason=row.reason,
        evidence_ref=row.evidence_ref,
        recorded_at=row.recorded_at,
    )


def _refuse_reused_command(
    session: Session, *, scope: TenantScope, command_id: str
) -> None:
    r = models.WithholdingRelease
    if (
        session.execute(
            select(r.id).where(
                r.tenant_id == scope.tenant_id, r.command_id == command_id
            )
        ).first()
        is not None
    ):
        raise BillingConflict(
            "withholding_release_command_reused",
            "this command id already recorded another release or undo",
        )


def release_withholding_component(
    db: Session,
    *,
    scope: Scope,
    command: ReleaseWithholdingComponent,
    permissions: ActorPermissionReader | None = None,
) -> WithholdingReleaseV1:
    """Release the residual of one frozen component (D1 to D5 of the Q-F design).

    Refusals, in order: platform scope; no reader wired or permission denied;
    unbounded or blank text; invoice not live; component not on this document
    and account; nothing outstanding; residual zero; ``expected_amount`` is not
    the residual. A partly discharged component releases only its residual.
    Idempotent on ``command_id``: the same body returns the stored release, a
    different body is a conflict. Emits one ids-only event.
    """

    tenant = _authorize(
        scope=scope,
        declared=command.scope,
        permissions=permissions,
        actor_ref=command.actor_ref,
        texts=(
            (command.command_id, 255),
            (command.actor_ref, 255),
            (command.reason, 2000),
        ),
        evidence_ref=command.evidence_ref,
    )
    plane = service._models(scope)
    account = service._one(
        db,
        scope=scope,
        model=plane.account,
        row_id=command.billing_account_id,
        lock=True,
    )

    def operation(session: Session) -> dict[str, object]:
        document = service._one(
            session,
            scope=scope,
            model=plane.document,
            row_id=command.document_id,
            lock=True,
        )
        service._require_credit_target(
            session, scope=scope, document=document, account_id=account.id
        )
        service._require_live_invoice_issuance(session, scope=scope, document=document)
        component = session.execute(
            select(models.DocumentWithholdingComponent).where(
                models.DocumentWithholdingComponent.tenant_id == tenant.tenant_id,
                models.DocumentWithholdingComponent.id == command.component_id,
                models.DocumentWithholdingComponent.document_id == document.id,
                models.DocumentWithholdingComponent.billing_account_id == account.id,
            )
        ).scalar_one_or_none()
        if component is None:
            raise BillingRuleViolation(
                "withholding_release_component_not_found",
                "the component is not frozen on this invoice and account",
            )
        if service._document_outstanding(session, scope=scope, document=document) <= 0:
            raise BillingRuleViolation(
                "withholding_release_nothing_outstanding",
                "the invoice has nothing outstanding",
            )
        residual = (
            Decimal(component.tax_amount)
            - _discharged(session, scope=tenant, component=component)
            - _released(session, scope=tenant, component_id=component.id)
        )
        if residual <= 0:
            raise BillingRuleViolation(
                "withholding_release_nothing_to_release",
                "the component has no residual to release",
            )
        offered = command.expected_amount
        if (offered.currency.code, offered.currency.minor_units) != (
            component.currency,
            component.minor_units,
        ):
            raise BillingRuleViolation(
                "money_identity_mismatch",
                "the expected amount must be in the component's currency",
            )
        if offered.amount != residual:
            raise BillingRuleViolation(
                "withholding_release_amount_stale",
                "the expected amount is not the component's residual",
            )
        _refuse_reused_command(session, scope=tenant, command_id=command.command_id)
        row = models.WithholdingRelease(
            id=uuid4(),
            tenant_id=tenant.tenant_id,
            billing_account_id=account.id,
            document_id=document.id,
            component_id=component.id,
            command_id=command.command_id,
            actor_ref=command.actor_ref,
            reason=command.reason,
            evidence_ref=command.evidence_ref,
            amount_delta=residual,
            currency=component.currency,
            minor_units=component.minor_units,
        )
        session.add(row)
        session.flush()
        service._emit(
            session,
            scope=scope,
            event_type="billing.withholding.released.v1",
            payload={
                "release_id": str(row.id),
                "billing_account_id": str(account.id),
                "document_id": str(document.id),
                "component_id": str(component.id),
            },
        )
        return {"release_id": str(row.id)}

    outcome = service._idempotent(
        db,
        scope=scope,
        operation_scope=_RELEASE_SCOPE,
        key=command.command_id,
        fingerprint=fingerprint_of(asdict(command)),
        operation=operation,
    )
    return get_withholding_release(
        db, scope=scope, release_id=UUID(str(outcome.result["release_id"]))
    )


def reverse_withholding_release(
    db: Session,
    *,
    scope: Scope,
    command: ReverseWithholdingRelease,
    permissions: ActorPermissionReader | None = None,
) -> WithholdingReleaseV1:
    """Undo a release by appending its exact inverse (D3).

    Refused when the release is already reversed, when the target is itself an
    undo (terminal), or when the invoice's outstanding is below the released
    amount (the cash already used the room: a certificate arriving after that is
    a customer-credit item for the manual exceptions review). A new release is
    allowed afterwards. Emits no event: an undo makes nothing payable.
    """

    tenant = _authorize(
        scope=scope,
        declared=command.scope,
        permissions=permissions,
        actor_ref=command.actor_ref,
        texts=(
            (command.command_id, 255),
            (command.actor_ref, 255),
            (command.reason, 2000),
        ),
    )
    plane = service._models(scope)
    original = service._one(
        db, scope=scope, model=models.WithholdingRelease, row_id=command.release_id
    )
    account = service._one(
        db,
        scope=scope,
        model=plane.account,
        row_id=original.billing_account_id,
        lock=True,
    )

    def operation(session: Session) -> dict[str, object]:
        document = service._one(
            session,
            scope=scope,
            model=plane.document,
            row_id=original.document_id,
            lock=True,
        )
        if original.reverses_release_id is not None:
            raise BillingRuleViolation(
                "withholding_release_undo_terminal",
                "an undo cannot itself be reversed",
            )
        r = models.WithholdingRelease
        if (
            session.execute(
                select(r.id).where(
                    r.tenant_id == tenant.tenant_id,
                    r.reverses_release_id == original.id,
                )
            ).first()
            is not None
        ):
            raise BillingRuleViolation(
                "withholding_release_already_reversed",
                "the release already has its undo",
            )
        service._require_live_invoice_issuance(session, scope=scope, document=document)
        if service._document_outstanding(
            session, scope=scope, document=document
        ) < Decimal(original.amount_delta):
            raise BillingRuleViolation(
                "withholding_release_undo_room_used",
                "the invoice no longer has the room: send it to the manual "
                "exceptions review",
            )
        _refuse_reused_command(session, scope=tenant, command_id=command.command_id)
        row = models.WithholdingRelease(
            id=uuid4(),
            tenant_id=tenant.tenant_id,
            billing_account_id=account.id,
            document_id=original.document_id,
            component_id=original.component_id,
            command_id=command.command_id,
            actor_ref=command.actor_ref,
            reason=command.reason,
            amount_delta=-Decimal(original.amount_delta),
            currency=original.currency,
            minor_units=original.minor_units,
            reverses_release_id=original.id,
        )
        session.add(row)
        session.flush()
        return {"release_id": str(row.id)}

    outcome = service._idempotent(
        db,
        scope=scope,
        operation_scope=_REVERSE_SCOPE,
        key=command.command_id,
        fingerprint=fingerprint_of(asdict(command)),
        operation=operation,
    )
    return get_withholding_release(
        db, scope=scope, release_id=UUID(str(outcome.result["release_id"]))
    )


def get_withholding_release(
    db: Session, *, scope: Scope, release_id: UUID
) -> WithholdingReleaseV1:
    """Read one stored release or undo (its ``recorded_at`` is the database's)."""

    tenant = _tenant_only(scope)
    row = db.execute(
        select(models.WithholdingRelease).where(
            models.WithholdingRelease.tenant_id == tenant.tenant_id,
            models.WithholdingRelease.id == release_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise BillingRuleViolation(
            "not_found", "WithholdingRelease was not found", row_id=str(release_id)
        )
    db.refresh(row)
    return _view(row)


def get_withholding_component_position(
    db: Session, *, scope: Scope, document_id: UUID
) -> tuple[WithholdingComponentPositionV1, ...]:
    """Per frozen component: frozen, discharged, released and residual."""

    tenant = _tenant_only(scope)
    components = db.execute(
        select(models.DocumentWithholdingComponent)
        .where(
            models.DocumentWithholdingComponent.tenant_id == tenant.tenant_id,
            models.DocumentWithholdingComponent.document_id == document_id,
        )
        .order_by(
            models.DocumentWithholdingComponent.component_ref,
            models.DocumentWithholdingComponent.id,
        )
    ).scalars()
    result: list[WithholdingComponentPositionV1] = []
    for component in components:
        frozen = Decimal(component.tax_amount)
        discharged = _discharged(db, scope=tenant, component=component)
        released = _released(db, scope=tenant, component_id=component.id)

        def money(
            value: Decimal, c: models.DocumentWithholdingComponent = component
        ) -> Money:
            return _money(value, c.currency, c.minor_units)

        result.append(
            WithholdingComponentPositionV1(
                component_id=component.id,
                frozen=money(frozen),
                discharged=money(discharged),
                released=money(released),
                residual=money(max(frozen - discharged - released, Decimal(0))),
            )
        )
    return tuple(cast("list[WithholdingComponentPositionV1]", result))


__all__ = [
    "RELEASED_EVENT_TYPE",
    "WithholdingComponentPositionV1",
    "WithholdingReleaseV1",
    "get_withholding_component_position",
    "get_withholding_release",
    "release_withholding_component",
    "reverse_withholding_release",
]
