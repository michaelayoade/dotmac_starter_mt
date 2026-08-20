"""One transaction-owned service for procurement and purchasing decisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeVar, cast
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money, currency
from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_procurement.contracts import (
    ApprovalFact,
    ApprovedPurchaseFact,
    AwardFact,
    BidScoreInput,
    BidStatus,
    BudgetAuthorizationFact,
    CompleteEvaluation,
    Conflict,
    ContractError,
    CreatePurchaseOrder,
    CreateRequisition,
    CreateSourcingEvent,
    EvaluationCriterion,
    EvaluationStatus,
    InvalidTransition,
    NotFound,
    ObservationConflict,
    PurchaseOrderStatus,
    ReceiptObservation,
    RejectionFact,
    RequisitionStatus,
    SnapshotImmutable,
    SourcingMethod,
    SourcingStatus,
    SourcingWindow,
    SubmitBid,
    digest_document,
    purchase_totals,
    weighted_score,
)
from dotmac_procurement.models import (
    BidEvaluation,
    BidLine,
    BidSubmission,
    ProcurementEvidence,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    ReceiptObservationRecord,
    SourcingEvent,
    SourcingEventLine,
    SourcingInvitation,
)

_Model = TypeVar("_Model")


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-procurement requires an explicit TenantScope")
    return scope.tenant_id


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{name} must be timezone-aware")


def _as_utc(value: datetime) -> datetime:
    """Restore SQLite's dropped offset at persistence boundaries."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _action(name: str, value: str, *, limit: int = 255) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ContractError(f"{name} is required and must be at most {limit} chars")
    return normalized


def _canonical(document: object) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)


def _one(db: Session, statement: Select[tuple[_Model]], *, detail: str) -> _Model:
    record = db.scalar(statement)
    if record is None:
        raise NotFound(detail)
    return record


def _record_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    aggregate_kind: str,
    aggregate_id: UUID,
    event_type: str,
    occurred_at: datetime,
    details: dict[str, object],
    actor_ref: str | None = None,
    source_ref: str | None = None,
) -> ProcurementEvidence:
    highest = db.scalar(
        select(func.max(ProcurementEvidence.sequence)).where(
            ProcurementEvidence.tenant_id == tenant_id,
            ProcurementEvidence.aggregate_kind == aggregate_kind,
            ProcurementEvidence.aggregate_id == aggregate_id,
        )
    )
    evidence = ProcurementEvidence(
        tenant_id=tenant_id,
        aggregate_kind=aggregate_kind,
        aggregate_id=aggregate_id,
        sequence=int(highest or 0) + 1,
        event_type=event_type,
        actor_ref=actor_ref,
        source_ref=source_ref,
        payload_sha256=digest_document(details),
        details_json=_canonical(details),
        occurred_at=occurred_at,
    )
    db.add(evidence)
    db.flush()
    return evidence


def _requisition(
    db: Session, tenant_id: UUID, requisition_id: UUID, *, lock: bool = False
) -> PurchaseRequisition:
    statement = select(PurchaseRequisition).where(
        PurchaseRequisition.tenant_id == tenant_id,
        PurchaseRequisition.id == requisition_id,
    )
    if lock:
        statement = statement.with_for_update()
    return _one(db, statement, detail="purchase requisition not found")


def _requisition_lines(
    db: Session, tenant_id: UUID, requisition_id: UUID
) -> tuple[PurchaseRequisitionLine, ...]:
    return tuple(
        db.scalars(
            select(PurchaseRequisitionLine)
            .where(
                PurchaseRequisitionLine.tenant_id == tenant_id,
                PurchaseRequisitionLine.requisition_id == requisition_id,
            )
            .order_by(PurchaseRequisitionLine.line_number)
        ).all()
    )


def _requisition_document(
    db: Session, requisition: PurchaseRequisition
) -> dict[str, object]:
    return {
        "id": requisition.id,
        "number": requisition.requisition_number,
        "requested_on": requisition.requested_on,
        "requester_ref": requisition.requester_ref,
        "urgency": requisition.urgency,
        "justification": requisition.justification,
        "currency_code": requisition.currency_code,
        "lines": [
            {
                "id": line.id,
                "position": line.line_number,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "estimated_unit_cost": str(line.estimated_unit_cost),
                "estimated_total": str(line.estimated_total),
                "item_ref": line.item_ref,
                "expense_ref": line.expense_ref,
                "cost_center_ref": line.cost_center_ref,
                "subject_ref": line.subject_ref,
                "requested_delivery_date": line.requested_delivery_date,
            }
            for line in _requisition_lines(db, requisition.tenant_id, requisition.id)
        ],
    }


def _add_requisition_lines(
    db: Session,
    *,
    tenant_id: UUID,
    requisition_id: UUID,
    command: CreateRequisition,
) -> None:
    for position, line in enumerate(command.lines, start=1):
        db.add(
            PurchaseRequisitionLine(
                tenant_id=tenant_id,
                requisition_id=requisition_id,
                line_number=position,
                description=line.description,
                quantity=line.quantity,
                unit=line.unit,
                estimated_unit_cost=line.estimated_unit_cost.amount,
                estimated_total=line.estimated_total.amount,
                item_ref=line.item_ref,
                expense_ref=line.expense_ref,
                cost_center_ref=line.cost_center_ref,
                subject_ref=line.subject_ref,
                requested_delivery_date=line.requested_delivery_date,
            )
        )


def create_requisition(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateRequisition,
    recorded_at: datetime,
) -> PurchaseRequisition:
    _aware("recorded_at", recorded_at)
    tenant_id = _tenant(scope)
    requisition = PurchaseRequisition(
        tenant_id=tenant_id,
        requisition_number=command.requisition_number,
        requested_on=command.requested_on,
        requester_ref=command.requester_ref,
        created_by_ref=command.created_by_ref,
        urgency=command.urgency,
        justification=command.justification,
        currency_code=command.currency_code,
        total_estimated_amount=command.estimated_total.amount,
        source_owner=command.source_owner,
        source_event_id=command.source_event_id,
        created_at=recorded_at,
        updated_at=recorded_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(requisition)
            db.flush()
            _add_requisition_lines(
                db,
                tenant_id=tenant_id,
                requisition_id=requisition.id,
                command=command,
            )
            db.flush()
            _record_evidence(
                db,
                tenant_id=tenant_id,
                aggregate_kind="requisition",
                aggregate_id=requisition.id,
                event_type="procurement.requisition_created.v1",
                occurred_at=recorded_at,
                actor_ref=command.created_by_ref,
                source_ref=(
                    f"{command.source_owner}:{command.source_event_id}"
                    if command.source_owner
                    else None
                ),
                details={
                    "requisition_number": requisition.requisition_number,
                    "total": str(requisition.total_estimated_amount),
                    "currency_code": requisition.currency_code,
                },
            )
    except IntegrityError as exc:
        raise Conflict("requisition number or source identity already exists") from exc
    return requisition


def revise_requisition(
    db: Session,
    *,
    scope: TenantScope,
    requisition_id: UUID,
    command: CreateRequisition,
    revised_at: datetime,
) -> PurchaseRequisition:
    _aware("revised_at", revised_at)
    tenant_id = _tenant(scope)
    requisition = _requisition(db, tenant_id, requisition_id, lock=True)
    if requisition.status != RequisitionStatus.DRAFT:
        raise SnapshotImmutable("only a draft requisition can be revised")
    requisition.requisition_number = command.requisition_number
    requisition.requested_on = command.requested_on
    requisition.requester_ref = command.requester_ref
    requisition.urgency = command.urgency
    requisition.justification = command.justification
    requisition.currency_code = command.currency_code
    requisition.total_estimated_amount = command.estimated_total.amount
    db.execute(
        delete(PurchaseRequisitionLine).where(
            PurchaseRequisitionLine.tenant_id == tenant_id,
            PurchaseRequisitionLine.requisition_id == requisition_id,
        )
    )
    _add_requisition_lines(
        db,
        tenant_id=tenant_id,
        requisition_id=requisition_id,
        command=command,
    )
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="requisition",
        aggregate_id=requisition.id,
        event_type="procurement.requisition_revised.v1",
        occurred_at=revised_at,
        actor_ref=command.created_by_ref,
        details={"requisition_number": requisition.requisition_number},
    )
    return requisition


def submit_requisition(
    db: Session,
    *,
    scope: TenantScope,
    requisition_id: UUID,
    submitted_at: datetime,
    submitted_by_ref: str,
) -> PurchaseRequisition:
    _aware("submitted_at", submitted_at)
    submitted_by_ref = _action("submitted_by_ref", submitted_by_ref)
    tenant_id = _tenant(scope)
    requisition = _requisition(db, tenant_id, requisition_id, lock=True)
    if requisition.status != RequisitionStatus.DRAFT:
        raise InvalidTransition("only a draft requisition can be submitted")
    content_sha256 = digest_document(_requisition_document(db, requisition))
    requisition.status = RequisitionStatus.SUBMITTED
    requisition.content_sha256 = content_sha256
    requisition.submitted_at = submitted_at
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="requisition",
        aggregate_id=requisition.id,
        event_type="procurement.requisition_submitted.v1",
        occurred_at=submitted_at,
        actor_ref=submitted_by_ref,
        details={"content_sha256": content_sha256},
    )
    return requisition


def record_budget_authorization(
    db: Session,
    *,
    scope: TenantScope,
    requisition_id: UUID,
    fact: BudgetAuthorizationFact,
) -> PurchaseRequisition:
    tenant_id = _tenant(scope)
    requisition = _requisition(db, tenant_id, requisition_id, lock=True)
    if requisition.status != RequisitionStatus.SUBMITTED:
        raise InvalidTransition("only a submitted requisition can bind budget")
    if requisition.content_sha256 is None:
        raise InvalidTransition("submitted requisition has no content digest")
    fact.require_matches(
        subject_id=requisition.id,
        content_sha256=requisition.content_sha256,
        required_amount=Money.of(
            requisition.total_estimated_amount, currency(requisition.currency_code)
        ),
    )
    requisition.status = RequisitionStatus.BUDGET_VERIFIED
    requisition.budget_authorization_ref = fact.authorization_ref
    requisition.budget_authorized_at = fact.authorized_at
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="requisition",
        aggregate_id=requisition.id,
        event_type="procurement.requisition_budget_authorized.v1",
        occurred_at=fact.authorized_at,
        source_ref=fact.authorization_ref,
        details={"content_sha256": fact.content_sha256},
    )
    return requisition


def apply_requisition_approval(
    db: Session,
    *,
    scope: TenantScope,
    requisition_id: UUID,
    fact: ApprovalFact,
) -> PurchaseRequisition:
    tenant_id = _tenant(scope)
    requisition = _requisition(db, tenant_id, requisition_id, lock=True)
    if requisition.status not in {
        RequisitionStatus.SUBMITTED,
        RequisitionStatus.BUDGET_VERIFIED,
    }:
        raise InvalidTransition("requisition is not awaiting approval")
    if requisition.content_sha256 is None:
        raise InvalidTransition("submitted requisition has no content digest")
    fact.require_matches(
        subject_type="procurement.requisition",
        subject_id=requisition.id,
        content_sha256=requisition.content_sha256,
    )
    requisition.status = RequisitionStatus.APPROVED
    requisition.approval_decision_ref = fact.decision_ref
    requisition.approved_at = fact.approved_at
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="requisition",
        aggregate_id=requisition.id,
        event_type="procurement.requisition_approved.v1",
        occurred_at=fact.approved_at,
        source_ref=fact.decision_ref,
        details={"content_sha256": fact.content_sha256},
    )
    return requisition


def apply_requisition_rejection(
    db: Session,
    *,
    scope: TenantScope,
    requisition_id: UUID,
    fact: RejectionFact,
) -> PurchaseRequisition:
    tenant_id = _tenant(scope)
    requisition = _requisition(db, tenant_id, requisition_id, lock=True)
    if requisition.status not in {
        RequisitionStatus.SUBMITTED,
        RequisitionStatus.BUDGET_VERIFIED,
    }:
        raise InvalidTransition("requisition is not awaiting a decision")
    if requisition.content_sha256 is None:
        raise InvalidTransition("submitted requisition has no content digest")
    fact.require_matches(
        subject_type="procurement.requisition",
        subject_id=requisition.id,
        content_sha256=requisition.content_sha256,
    )
    requisition.status = RequisitionStatus.REJECTED
    requisition.approval_decision_ref = fact.decision_ref
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="requisition",
        aggregate_id=requisition.id,
        event_type="procurement.requisition_rejected.v1",
        occurred_at=fact.rejected_at,
        source_ref=fact.decision_ref,
        details={"content_sha256": fact.content_sha256, "reason": fact.reason},
    )
    return requisition


def cancel_requisition(
    db: Session,
    *,
    scope: TenantScope,
    requisition_id: UUID,
    cancelled_at: datetime,
    actor_ref: str,
    reason: str,
) -> PurchaseRequisition:
    _aware("cancelled_at", cancelled_at)
    actor_ref = _action("actor_ref", actor_ref)
    reason = _action("reason", reason, limit=4000)
    tenant_id = _tenant(scope)
    requisition = _requisition(db, tenant_id, requisition_id, lock=True)
    if requisition.status not in {
        RequisitionStatus.DRAFT,
        RequisitionStatus.SUBMITTED,
        RequisitionStatus.BUDGET_VERIFIED,
        RequisitionStatus.APPROVED,
    }:
        raise InvalidTransition("requisition cannot be cancelled from this state")
    if (
        db.scalar(
            select(SourcingEvent.id).where(
                SourcingEvent.tenant_id == tenant_id,
                SourcingEvent.source_requisition_id == requisition.id,
                SourcingEvent.status != SourcingStatus.CANCELLED,
            )
        )
        is not None
    ):
        raise InvalidTransition("requisition has an active sourcing event")
    if (
        db.scalar(
            select(PurchaseOrder.id).where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.source_requisition_id == requisition.id,
                PurchaseOrder.status != PurchaseOrderStatus.CANCELLED,
            )
        )
        is not None
    ):
        raise InvalidTransition("requisition has an active purchase order")
    requisition.status = RequisitionStatus.CANCELLED
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="requisition",
        aggregate_id=requisition.id,
        event_type="procurement.requisition_cancelled.v1",
        occurred_at=cancelled_at,
        actor_ref=actor_ref,
        details={"reason": reason},
    )
    return requisition


def _sourcing_event(
    db: Session, tenant_id: UUID, event_id: UUID, *, lock: bool = False
) -> SourcingEvent:
    statement = select(SourcingEvent).where(
        SourcingEvent.tenant_id == tenant_id, SourcingEvent.id == event_id
    )
    if lock:
        statement = statement.with_for_update()
    return _one(db, statement, detail="sourcing event not found")


def _sourcing_lines(
    db: Session, tenant_id: UUID, event_id: UUID
) -> tuple[SourcingEventLine, ...]:
    return tuple(
        db.scalars(
            select(SourcingEventLine)
            .where(
                SourcingEventLine.tenant_id == tenant_id,
                SourcingEventLine.event_id == event_id,
            )
            .order_by(SourcingEventLine.line_number)
        ).all()
    )


def _criteria(event: SourcingEvent) -> tuple[EvaluationCriterion, ...]:
    documents = cast(list[dict[str, str]], json.loads(event.criteria_json))
    return tuple(
        EvaluationCriterion(
            code=document["code"],
            name=document["name"],
            weight=Decimal(document["weight"]),
        )
        for document in documents
    )


def _event_document(db: Session, event: SourcingEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "number": event.event_number,
        "title": event.title,
        "method": event.method.value,
        "opens_at": event.opens_at,
        "closes_at": event.closes_at,
        "currency_code": event.currency_code,
        "criteria": [criterion.document() for criterion in _criteria(event)],
        "terms": event.terms,
        "source_requisition_id": event.source_requisition_id,
        "lines": [
            {
                "id": line.id,
                "position": line.line_number,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "source_requisition_line_id": line.source_requisition_line_id,
                "item_ref": line.item_ref,
                "target_unit_cost": (
                    str(line.target_unit_cost)
                    if line.target_unit_cost is not None
                    else None
                ),
                "requested_delivery_date": line.requested_delivery_date,
            }
            for line in _sourcing_lines(db, event.tenant_id, event.id)
        ],
    }


def _add_sourcing_lines(
    db: Session,
    *,
    tenant_id: UUID,
    event_id: UUID,
    command: CreateSourcingEvent,
) -> None:
    for position, line in enumerate(command.lines, start=1):
        db.add(
            SourcingEventLine(
                tenant_id=tenant_id,
                event_id=event_id,
                line_number=position,
                description=line.description,
                quantity=line.quantity,
                unit=line.unit,
                source_requisition_line_id=line.source_requisition_line_id,
                item_ref=line.item_ref,
                target_unit_cost=(
                    line.target_unit_cost.amount if line.target_unit_cost else None
                ),
                requested_delivery_date=line.requested_delivery_date,
            )
        )


def _event_estimate(command: CreateSourcingEvent) -> Decimal:
    return sum(
        (
            line.target_unit_cost.multiply(line.quantity).amount
            if line.target_unit_cost
            else Decimal("0")
            for line in command.lines
        ),
        Decimal("0"),
    )


def _validate_sourcing_source(
    db: Session,
    *,
    tenant_id: UUID,
    command: CreateSourcingEvent,
    current_event_id: UUID | None = None,
) -> None:
    source_ids = [line.source_requisition_line_id for line in command.lines]
    if command.source_requisition_id is None:
        if any(source_id is not None for source_id in source_ids):
            raise ContractError(
                "sourcing lines require their owning source requisition"
            )
        return
    requisition = _requisition(db, tenant_id, command.source_requisition_id, lock=True)
    if requisition.status != RequisitionStatus.APPROVED:
        raise InvalidTransition("sourcing requires an approved requisition")
    if requisition.currency_code != command.currency_code:
        raise ContractError("sourcing currency differs from the requisition")
    source_lines = {
        line.id: line for line in _requisition_lines(db, tenant_id, requisition.id)
    }
    if (
        any(source_id is None for source_id in source_ids)
        or len(source_ids) != len(set(source_ids))
        or set(source_ids) != set(source_lines)
    ):
        raise ContractError("sourcing lines must cover requisition lines exactly once")
    for sourcing_line in command.lines:
        source_id = sourcing_line.source_requisition_line_id
        if source_id is None:
            raise ContractError("sourcing line has no requisition line")
        requisition_line = source_lines[source_id]
        if sourcing_line.quantity != requisition_line.quantity:
            raise ContractError("sourcing quantity differs from the requisition")
        if sourcing_line.unit != requisition_line.unit:
            raise ContractError("sourcing unit differs from the requisition")
        if sourcing_line.item_ref != requisition_line.item_ref:
            raise ContractError("sourcing item differs from the requisition")
    active_event = select(SourcingEvent.id).where(
        SourcingEvent.tenant_id == tenant_id,
        SourcingEvent.source_requisition_id == requisition.id,
        SourcingEvent.status != SourcingStatus.CANCELLED,
    )
    if current_event_id is not None:
        active_event = active_event.where(SourcingEvent.id != current_event_id)
    if db.scalar(active_event) is not None:
        raise Conflict("requisition already has an active sourcing event")


def create_sourcing_event(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateSourcingEvent,
    recorded_at: datetime,
) -> SourcingEvent:
    _aware("recorded_at", recorded_at)
    tenant_id = _tenant(scope)
    _validate_sourcing_source(db, tenant_id=tenant_id, command=command)
    event = SourcingEvent(
        tenant_id=tenant_id,
        event_number=command.event_number,
        title=command.title,
        method=command.method,
        opens_at=command.window.opens_at,
        closes_at=command.window.closes_at,
        currency_code=command.currency_code,
        criteria_json=_canonical(
            [criterion.document() for criterion in command.criteria]
        ),
        terms=command.terms,
        estimated_amount=_event_estimate(command),
        source_requisition_id=command.source_requisition_id,
        created_by_ref=command.created_by_ref,
        created_at=recorded_at,
        updated_at=recorded_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(event)
            db.flush()
            _add_sourcing_lines(
                db, tenant_id=tenant_id, event_id=event.id, command=command
            )
            db.flush()
            _record_evidence(
                db,
                tenant_id=tenant_id,
                aggregate_kind="sourcing_event",
                aggregate_id=event.id,
                event_type="procurement.sourcing_event_created.v1",
                occurred_at=recorded_at,
                actor_ref=command.created_by_ref,
                details={
                    "event_number": event.event_number,
                    "method": event.method.value,
                },
            )
    except IntegrityError as exc:
        raise Conflict("sourcing event number already exists") from exc
    return event


def revise_sourcing_event(
    db: Session,
    *,
    scope: TenantScope,
    event_id: UUID,
    command: CreateSourcingEvent,
    revised_at: datetime,
) -> SourcingEvent:
    _aware("revised_at", revised_at)
    tenant_id = _tenant(scope)
    event = _sourcing_event(db, tenant_id, event_id, lock=True)
    if event.status != SourcingStatus.DRAFT:
        raise SnapshotImmutable("published sourcing content is immutable")
    if command.source_requisition_id != event.source_requisition_id:
        raise ContractError("a sourcing revision cannot change its requisition source")
    _validate_sourcing_source(
        db,
        tenant_id=tenant_id,
        command=command,
        current_event_id=event.id,
    )
    event.event_number = command.event_number
    event.title = command.title
    event.method = command.method
    event.opens_at = command.window.opens_at
    event.closes_at = command.window.closes_at
    event.currency_code = command.currency_code
    event.criteria_json = _canonical(
        [criterion.document() for criterion in command.criteria]
    )
    event.terms = command.terms
    event.estimated_amount = _event_estimate(command)
    db.execute(
        delete(SourcingEventLine).where(
            SourcingEventLine.tenant_id == tenant_id,
            SourcingEventLine.event_id == event_id,
        )
    )
    _add_sourcing_lines(db, tenant_id=tenant_id, event_id=event_id, command=command)
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="sourcing_event",
        aggregate_id=event.id,
        event_type="procurement.sourcing_event_revised.v1",
        occurred_at=revised_at,
        actor_ref=command.created_by_ref,
        details={"event_number": event.event_number},
    )
    return event


def invite_supplier(
    db: Session,
    *,
    scope: TenantScope,
    event_id: UUID,
    supplier_ref: str,
    invited_at: datetime,
    invited_by_ref: str,
) -> SourcingInvitation:
    _aware("invited_at", invited_at)
    supplier_ref = _action("supplier_ref", supplier_ref)
    invited_by_ref = _action("invited_by_ref", invited_by_ref)
    tenant_id = _tenant(scope)
    event = _sourcing_event(db, tenant_id, event_id, lock=True)
    if event.status != SourcingStatus.DRAFT:
        raise SnapshotImmutable("supplier invitations freeze at publication")
    invitation = SourcingInvitation(
        tenant_id=tenant_id,
        event_id=event_id,
        supplier_ref=supplier_ref,
        invited_at=invited_at,
        invited_by_ref=invited_by_ref,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(invitation)
            db.flush()
            _record_evidence(
                db,
                tenant_id=tenant_id,
                aggregate_kind="sourcing_event",
                aggregate_id=event.id,
                event_type="procurement.supplier_invited.v1",
                occurred_at=invited_at,
                actor_ref=invited_by_ref,
                details={"supplier_ref": invitation.supplier_ref},
            )
    except IntegrityError as exc:
        raise Conflict("supplier is already invited") from exc
    return invitation


def publish_sourcing_event(
    db: Session,
    *,
    scope: TenantScope,
    event_id: UUID,
    published_at: datetime,
    published_by_ref: str,
) -> SourcingEvent:
    _aware("published_at", published_at)
    published_by_ref = _action("published_by_ref", published_by_ref)
    tenant_id = _tenant(scope)
    event = _sourcing_event(db, tenant_id, event_id, lock=True)
    if event.status != SourcingStatus.DRAFT:
        raise InvalidTransition("only a draft sourcing event can be published")
    invitation_count = db.scalar(
        select(func.count())
        .select_from(SourcingInvitation)
        .where(
            SourcingInvitation.tenant_id == tenant_id,
            SourcingInvitation.event_id == event.id,
        )
    )
    if event.method in {SourcingMethod.DIRECT, SourcingMethod.SELECTIVE} and not int(
        invitation_count or 0
    ):
        raise ContractError("direct and selective sourcing require invitations")
    content_sha256 = digest_document(_event_document(db, event))
    event.status = SourcingStatus.PUBLISHED
    event.content_sha256 = content_sha256
    event.published_at = published_at
    if event.source_requisition_id is not None:
        requisition = _requisition(
            db, tenant_id, event.source_requisition_id, lock=True
        )
        if requisition.status != RequisitionStatus.APPROVED:
            raise InvalidTransition("source requisition is no longer approved")
        requisition.status = RequisitionStatus.SOURCED
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="sourcing_event",
        aggregate_id=event.id,
        event_type="procurement.sourcing_event_published.v1",
        occurred_at=published_at,
        actor_ref=published_by_ref,
        details={"content_sha256": content_sha256},
    )
    return event


def receive_bid(
    db: Session,
    *,
    scope: TenantScope,
    event_id: UUID,
    command: SubmitBid,
) -> BidSubmission:
    tenant_id = _tenant(scope)
    event = _sourcing_event(db, tenant_id, event_id, lock=True)
    if event.status != SourcingStatus.PUBLISHED:
        raise InvalidTransition("bids are accepted only for a published event")
    SourcingWindow(_as_utc(event.opens_at), _as_utc(event.closes_at)).require_open(
        command.received_at
    )
    if command.currency_code != event.currency_code:
        raise ContractError("bid currency differs from the sourcing event")
    if event.method in {SourcingMethod.DIRECT, SourcingMethod.SELECTIVE}:
        invited = db.scalar(
            select(SourcingInvitation.id).where(
                SourcingInvitation.tenant_id == tenant_id,
                SourcingInvitation.event_id == event.id,
                SourcingInvitation.supplier_ref == command.supplier_ref,
            )
        )
        if invited is None:
            raise ContractError("supplier was not invited to this sourcing event")
    event_lines = {line.id: line for line in _sourcing_lines(db, tenant_id, event.id)}
    bid_line_ids = [line.sourcing_line_id for line in command.lines]
    if len(bid_line_ids) != len(set(bid_line_ids)) or set(bid_line_ids) != set(
        event_lines
    ):
        raise ContractError("bid lines must cover sourcing lines exactly once")
    for line in command.lines:
        if line.quantity != event_lines[line.sourcing_line_id].quantity:
            raise ContractError("bid quantity must match its sourcing line")
    content_document = {
        "event_id": event.id,
        "response_number": command.response_number,
        "supplier_ref": command.supplier_ref,
        "received_at": command.received_at,
        "currency_code": command.currency_code,
        "validity_days": command.validity_days,
        "delivery_period_days": command.delivery_period_days,
        "technical_proposal": command.technical_proposal,
        "terms": command.terms,
        "lines": [
            line.document(position) for position, line in enumerate(command.lines, 1)
        ],
    }
    bid = BidSubmission(
        tenant_id=tenant_id,
        event_id=event.id,
        response_number=command.response_number,
        supplier_ref=command.supplier_ref,
        status=BidStatus.DRAFT,
        received_at=command.received_at,
        currency_code=command.currency_code,
        total_amount=command.total.amount,
        validity_days=command.validity_days,
        delivery_period_days=command.delivery_period_days,
        technical_proposal=command.technical_proposal,
        terms=command.terms,
        source_owner=command.source_owner,
        source_event_id=command.source_event_id,
        content_sha256=digest_document(content_document),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(bid)
            db.flush()
            for position, line in enumerate(command.lines, start=1):
                db.add(
                    BidLine(
                        tenant_id=tenant_id,
                        event_id=event.id,
                        bid_id=bid.id,
                        sourcing_line_id=line.sourcing_line_id,
                        line_number=position,
                        description=line.description,
                        quantity=line.quantity,
                        unit_price=line.unit_price.amount,
                        line_total=line.line_total.amount,
                        promised_delivery_date=line.promised_delivery_date,
                    )
                )
            db.flush()
            bid.status = BidStatus.SUBMITTED
            bid.submitted_at = command.received_at
            db.flush()
            _record_evidence(
                db,
                tenant_id=tenant_id,
                aggregate_kind="sourcing_event",
                aggregate_id=event.id,
                event_type="procurement.bid_received.v1",
                occurred_at=command.received_at,
                source_ref=f"{command.source_owner}:{command.source_event_id}",
                details={"bid_id": bid.id, "content_sha256": bid.content_sha256},
            )
    except IntegrityError as exc:
        raise Conflict("supplier bid or source event already exists") from exc
    return bid


def close_sourcing_event(
    db: Session,
    *,
    scope: TenantScope,
    event_id: UUID,
    closed_at: datetime,
    closed_by_ref: str,
) -> SourcingEvent:
    _aware("closed_at", closed_at)
    closed_by_ref = _action("closed_by_ref", closed_by_ref)
    tenant_id = _tenant(scope)
    event = _sourcing_event(db, tenant_id, event_id, lock=True)
    if event.status != SourcingStatus.PUBLISHED:
        raise InvalidTransition("only a published sourcing event can close")
    if closed_at < _as_utc(event.closes_at):
        raise InvalidTransition("sourcing cannot close before its declared deadline")
    bid_count = db.scalar(
        select(func.count())
        .select_from(BidSubmission)
        .where(
            BidSubmission.tenant_id == tenant_id,
            BidSubmission.event_id == event.id,
            BidSubmission.status == BidStatus.SUBMITTED,
        )
    )
    if not int(bid_count or 0):
        raise InvalidTransition("a sourcing event with no bids cannot be evaluated")
    event.status = SourcingStatus.CLOSED
    event.closed_at = closed_at
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="sourcing_event",
        aggregate_id=event.id,
        event_type="procurement.sourcing_event_closed.v1",
        occurred_at=closed_at,
        actor_ref=closed_by_ref,
        details={"bid_count": int(bid_count or 0)},
    )
    return event


def cancel_sourcing_event(
    db: Session,
    *,
    scope: TenantScope,
    event_id: UUID,
    cancelled_at: datetime,
    cancelled_by_ref: str,
    reason: str,
) -> SourcingEvent:
    _aware("cancelled_at", cancelled_at)
    cancelled_by_ref = _action("cancelled_by_ref", cancelled_by_ref)
    reason = _action("reason", reason, limit=4000)
    tenant_id = _tenant(scope)
    event = _sourcing_event(db, tenant_id, event_id, lock=True)
    if event.status not in {SourcingStatus.DRAFT, SourcingStatus.PUBLISHED}:
        raise InvalidTransition("sourcing cannot be cancelled from this state")
    previous_status = event.status
    if previous_status == SourcingStatus.PUBLISHED:
        bid_count = db.scalar(
            select(func.count())
            .select_from(BidSubmission)
            .where(
                BidSubmission.tenant_id == tenant_id,
                BidSubmission.event_id == event.id,
            )
        )
        if int(bid_count or 0):
            raise InvalidTransition("sourcing with received bids cannot be cancelled")
        if event.source_requisition_id is not None:
            requisition = _requisition(
                db, tenant_id, event.source_requisition_id, lock=True
            )
            if requisition.status != RequisitionStatus.SOURCED:
                raise InvalidTransition("source requisition is no longer sourced")
            requisition.status = RequisitionStatus.APPROVED
    event.status = SourcingStatus.CANCELLED
    event.closed_at = cancelled_at
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="sourcing_event",
        aggregate_id=event.id,
        event_type="procurement.sourcing_event_cancelled.v1",
        occurred_at=cancelled_at,
        actor_ref=cancelled_by_ref,
        details={"previous_status": previous_status.value, "reason": reason},
    )
    return event


def _score_document(
    criteria: tuple[EvaluationCriterion, ...], entry: BidScoreInput
) -> dict[str, object]:
    return {
        "bid_id": entry.bid_id,
        "scores": {code: str(value) for code, value in sorted(entry.scores.items())},
        "weighted_total": str(weighted_score(criteria, entry.scores)),
        "comments": entry.comments,
    }


def complete_evaluation(
    db: Session,
    *,
    scope: TenantScope,
    event_id: UUID,
    command: CompleteEvaluation,
) -> BidEvaluation:
    tenant_id = _tenant(scope)
    event = _sourcing_event(db, tenant_id, event_id, lock=True)
    if event.status != SourcingStatus.CLOSED:
        raise InvalidTransition("only a closed sourcing event can be evaluated")
    if db.scalar(
        select(BidEvaluation.id).where(
            BidEvaluation.tenant_id == tenant_id,
            BidEvaluation.event_id == event.id,
        )
    ):
        raise Conflict("the sourcing event already has an evaluation")
    bids = tuple(
        db.scalars(
            select(BidSubmission)
            .where(
                BidSubmission.tenant_id == tenant_id,
                BidSubmission.event_id == event.id,
                BidSubmission.status == BidStatus.SUBMITTED,
            )
            .with_for_update()
        ).all()
    )
    scores_by_bid = {entry.bid_id: entry for entry in command.bid_scores}
    if set(scores_by_bid) != {bid.id for bid in bids}:
        raise ContractError("evaluation must score every submitted bid exactly once")
    criteria = _criteria(event)
    documents = [
        _score_document(criteria, scores_by_bid[bid.id])
        for bid in sorted(bids, key=lambda row: str(row.id))
    ]
    selected_document = next(
        document
        for document in documents
        if document["bid_id"] == command.selected_bid_id
    )
    content_document = {
        "event_id": event.id,
        "selected_bid_id": command.selected_bid_id,
        "scores": documents,
        "report": command.report,
        "evaluated_by_ref": command.evaluated_by_ref,
        "evaluated_at": command.evaluated_at,
    }
    evaluation = BidEvaluation(
        tenant_id=tenant_id,
        event_id=event.id,
        selected_bid_id=command.selected_bid_id,
        status=EvaluationStatus.COMPLETED,
        scores_json=_canonical(documents),
        selected_total_score=Decimal(cast(str, selected_document["weighted_total"])),
        report=command.report,
        evaluated_by_ref=command.evaluated_by_ref,
        evaluated_at=command.evaluated_at,
        content_sha256=digest_document(content_document),
    )
    db.add(evaluation)
    for bid in bids:
        bid.status = BidStatus.UNDER_EVALUATION
    event.status = SourcingStatus.EVALUATED
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="sourcing_event",
        aggregate_id=event.id,
        event_type="procurement.bid_evaluation_completed.v1",
        occurred_at=command.evaluated_at,
        actor_ref=command.evaluated_by_ref,
        details={
            "evaluation_id": evaluation.id,
            "selected_bid_id": evaluation.selected_bid_id,
            "content_sha256": evaluation.content_sha256,
        },
    )
    return evaluation


def apply_award_approval(
    db: Session,
    *,
    scope: TenantScope,
    evaluation_id: UUID,
    fact: ApprovalFact,
) -> AwardFact:
    tenant_id = _tenant(scope)
    evaluation = _one(
        db,
        select(BidEvaluation)
        .where(
            BidEvaluation.tenant_id == tenant_id,
            BidEvaluation.id == evaluation_id,
        )
        .with_for_update(),
        detail="bid evaluation not found",
    )
    if evaluation.status != EvaluationStatus.COMPLETED:
        raise InvalidTransition("evaluation is not awaiting award approval")
    fact.require_matches(
        subject_type="procurement.sourcing_award",
        subject_id=evaluation.id,
        content_sha256=evaluation.content_sha256,
    )
    event = _sourcing_event(db, tenant_id, evaluation.event_id, lock=True)
    selected = _one(
        db,
        select(BidSubmission)
        .where(
            BidSubmission.tenant_id == tenant_id,
            BidSubmission.id == evaluation.selected_bid_id,
        )
        .with_for_update(),
        detail="selected bid not found",
    )
    bids = tuple(
        db.scalars(
            select(BidSubmission)
            .where(
                BidSubmission.tenant_id == tenant_id,
                BidSubmission.event_id == event.id,
            )
            .with_for_update()
        ).all()
    )
    evaluation.status = EvaluationStatus.APPROVED
    evaluation.approval_decision_ref = fact.decision_ref
    evaluation.approved_at = fact.approved_at
    event.status = SourcingStatus.AWARDED
    event.awarded_at = fact.approved_at
    for bid in bids:
        bid.status = BidStatus.SELECTED if bid.id == selected.id else BidStatus.REJECTED
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="sourcing_event",
        aggregate_id=event.id,
        event_type="procurement.sourcing_awarded.v1",
        occurred_at=fact.approved_at,
        source_ref=fact.decision_ref,
        details={"evaluation_id": evaluation.id, "selected_bid_id": selected.id},
    )
    return AwardFact(
        sourcing_event_id=event.id,
        evaluation_id=evaluation.id,
        bid_id=selected.id,
        supplier_ref=selected.supplier_ref,
        total=Money.of(selected.total_amount, currency(selected.currency_code)),
        approved_at=fact.approved_at,
    )


def _purchase_order(
    db: Session, tenant_id: UUID, order_id: UUID, *, lock: bool = False
) -> PurchaseOrder:
    statement = select(PurchaseOrder).where(
        PurchaseOrder.tenant_id == tenant_id, PurchaseOrder.id == order_id
    )
    if lock:
        statement = statement.with_for_update()
    return _one(db, statement, detail="purchase order not found")


def _purchase_lines(
    db: Session, tenant_id: UUID, order_id: UUID, *, lock: bool = False
) -> tuple[PurchaseOrderLine, ...]:
    statement = (
        select(PurchaseOrderLine)
        .where(
            PurchaseOrderLine.tenant_id == tenant_id,
            PurchaseOrderLine.order_id == order_id,
        )
        .order_by(PurchaseOrderLine.line_number)
    )
    if lock:
        statement = statement.with_for_update()
    return tuple(db.scalars(statement).all())


def _validate_purchase_source(
    db: Session, tenant_id: UUID, command: CreatePurchaseOrder
) -> None:
    totals = purchase_totals(command.lines)
    requisition: PurchaseRequisition | None = None
    if command.source_requisition_id is not None:
        requisition = _requisition(
            db, tenant_id, command.source_requisition_id, lock=True
        )
        required_status = (
            RequisitionStatus.SOURCED
            if command.source_evaluation_id is not None
            else RequisitionStatus.APPROVED
        )
        if requisition.status != required_status:
            raise InvalidTransition("purchase source requisition is not approved")
        if requisition.currency_code != command.currency_code:
            raise ContractError("purchase currency differs from the requisition")
        if totals.subtotal.amount > requisition.total_estimated_amount:
            raise ContractError("purchase subtotal exceeds the requisition estimate")
        requisition_lines = _requisition_lines(db, tenant_id, requisition.id)
        if len(command.lines) != len(requisition_lines):
            raise ContractError("purchase lines differ from the requisition")
        for purchase_line, requisition_line in zip(
            command.lines, requisition_lines, strict=True
        ):
            if purchase_line.quantity != requisition_line.quantity:
                raise ContractError("purchase quantity differs from the requisition")
            if purchase_line.unit != requisition_line.unit:
                raise ContractError("purchase unit differs from the requisition")
            if purchase_line.item_ref != requisition_line.item_ref:
                raise ContractError("purchase item differs from the requisition")
            for field in ("expense_ref", "cost_center_ref", "subject_ref"):
                if getattr(purchase_line, field) != getattr(requisition_line, field):
                    raise ContractError(
                        f"purchase {field} differs from the requisition"
                    )
    if command.source_evaluation_id is not None:
        evaluation = _one(
            db,
            select(BidEvaluation)
            .where(
                BidEvaluation.tenant_id == tenant_id,
                BidEvaluation.id == command.source_evaluation_id,
            )
            .with_for_update(),
            detail="source bid evaluation not found",
        )
        if evaluation.status != EvaluationStatus.APPROVED:
            raise InvalidTransition("purchase source award is not approved")
        event = _sourcing_event(db, tenant_id, evaluation.event_id)
        if event.source_requisition_id != command.source_requisition_id:
            raise ContractError("purchase sources do not belong to the same decision")
        selected = _one(
            db,
            select(BidSubmission)
            .where(
                BidSubmission.tenant_id == tenant_id,
                BidSubmission.id == evaluation.selected_bid_id,
                BidSubmission.event_id == evaluation.event_id,
            )
            .with_for_update(),
            detail="selected source bid not found",
        )
        if selected.status != BidStatus.SELECTED:
            raise InvalidTransition("awarded bid is not selected")
        if selected.supplier_ref != command.supplier_ref:
            raise ContractError("purchase supplier differs from the award")
        if selected.currency_code != command.currency_code:
            raise ContractError("purchase currency differs from the award")
        if totals.subtotal.amount != selected.total_amount:
            raise ContractError("purchase subtotal differs from the awarded bid")
        awarded_lines = tuple(
            db.scalars(
                select(BidLine)
                .where(
                    BidLine.tenant_id == tenant_id,
                    BidLine.bid_id == selected.id,
                )
                .order_by(BidLine.line_number)
            ).all()
        )
        if len(command.lines) != len(awarded_lines):
            raise ContractError("purchase lines differ from the awarded bid")
        sourcing_lines = {
            line.id: line for line in _sourcing_lines(db, tenant_id, event.id)
        }
        for purchase_line, awarded_line in zip(
            command.lines, awarded_lines, strict=True
        ):
            sourcing_line = sourcing_lines.get(awarded_line.sourcing_line_id)
            if sourcing_line is None:
                raise ContractError("awarded bid line has no sourcing line")
            if purchase_line.quantity != awarded_line.quantity:
                raise ContractError("purchase quantity differs from the awarded bid")
            if purchase_line.unit != sourcing_line.unit:
                raise ContractError(
                    "purchase unit differs from the awarded sourcing line"
                )
            if purchase_line.unit_price.amount != awarded_line.unit_price:
                raise ContractError("purchase unit price differs from the awarded bid")
            if purchase_line.item_ref != sourcing_line.item_ref:
                raise ContractError(
                    "purchase item differs from the awarded sourcing line"
                )


def _add_purchase_lines(
    db: Session,
    *,
    tenant_id: UUID,
    order_id: UUID,
    command: CreatePurchaseOrder,
) -> None:
    for position, line in enumerate(command.lines, start=1):
        db.add(
            PurchaseOrderLine(
                tenant_id=tenant_id,
                order_id=order_id,
                line_number=position,
                description=line.description,
                quantity_ordered=line.quantity,
                quantity_received=Decimal("0"),
                unit=line.unit,
                unit_price=line.unit_price.amount,
                line_amount=line.line_total.amount,
                tax_amount=line.tax.amount,
                item_ref=line.item_ref,
                expense_ref=line.expense_ref,
                asset_ref=line.asset_ref,
                cost_center_ref=line.cost_center_ref,
                subject_ref=line.subject_ref,
                expected_delivery_date=line.expected_delivery_date,
            )
        )


def create_purchase_order(
    db: Session,
    *,
    scope: TenantScope,
    command: CreatePurchaseOrder,
    recorded_at: datetime,
) -> PurchaseOrder:
    _aware("recorded_at", recorded_at)
    tenant_id = _tenant(scope)
    _validate_purchase_source(db, tenant_id, command)
    totals = purchase_totals(command.lines)
    order = PurchaseOrder(
        tenant_id=tenant_id,
        order_number=command.order_number,
        supplier_ref=command.supplier_ref,
        ordered_on=command.ordered_on,
        expected_delivery_date=command.expected_delivery_date,
        currency_code=command.currency_code,
        subtotal=totals.subtotal.amount,
        tax_amount=totals.tax.amount,
        total_amount=totals.total.amount,
        source_requisition_id=command.source_requisition_id,
        source_evaluation_id=command.source_evaluation_id,
        created_by_ref=command.created_by_ref,
        ship_to_ref=command.ship_to_ref,
        terms=command.terms,
        created_at=recorded_at,
        updated_at=recorded_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(order)
            db.flush()
            _add_purchase_lines(
                db, tenant_id=tenant_id, order_id=order.id, command=command
            )
            db.flush()
            _record_evidence(
                db,
                tenant_id=tenant_id,
                aggregate_kind="purchase_order",
                aggregate_id=order.id,
                event_type="procurement.purchase_order_created.v1",
                occurred_at=recorded_at,
                actor_ref=command.created_by_ref,
                details={
                    "order_number": order.order_number,
                    "total": str(order.total_amount),
                    "currency_code": order.currency_code,
                },
            )
    except IntegrityError as exc:
        raise Conflict("purchase order number or source already committed") from exc
    return order


def revise_purchase_order(
    db: Session,
    *,
    scope: TenantScope,
    order_id: UUID,
    command: CreatePurchaseOrder,
    revised_at: datetime,
) -> PurchaseOrder:
    _aware("revised_at", revised_at)
    tenant_id = _tenant(scope)
    order = _purchase_order(db, tenant_id, order_id, lock=True)
    if order.status != PurchaseOrderStatus.DRAFT:
        raise SnapshotImmutable("only a draft purchase order can be revised")
    _validate_purchase_source(db, tenant_id, command)
    totals = purchase_totals(command.lines)
    order.order_number = command.order_number
    order.supplier_ref = command.supplier_ref
    order.ordered_on = command.ordered_on
    order.expected_delivery_date = command.expected_delivery_date
    order.currency_code = command.currency_code
    order.subtotal = totals.subtotal.amount
    order.tax_amount = totals.tax.amount
    order.total_amount = totals.total.amount
    order.source_requisition_id = command.source_requisition_id
    order.source_evaluation_id = command.source_evaluation_id
    order.ship_to_ref = command.ship_to_ref
    order.terms = command.terms
    db.execute(
        delete(PurchaseOrderLine).where(
            PurchaseOrderLine.tenant_id == tenant_id,
            PurchaseOrderLine.order_id == order_id,
        )
    )
    _add_purchase_lines(db, tenant_id=tenant_id, order_id=order_id, command=command)
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="purchase_order",
        aggregate_id=order.id,
        event_type="procurement.purchase_order_revised.v1",
        occurred_at=revised_at,
        actor_ref=command.created_by_ref,
        details={"order_number": order.order_number},
    )
    return order


def _purchase_document(db: Session, order: PurchaseOrder) -> dict[str, object]:
    return {
        "id": order.id,
        "number": order.order_number,
        "supplier_ref": order.supplier_ref,
        "ordered_on": order.ordered_on,
        "expected_delivery_date": order.expected_delivery_date,
        "currency_code": order.currency_code,
        "source_requisition_id": order.source_requisition_id,
        "source_evaluation_id": order.source_evaluation_id,
        "ship_to_ref": order.ship_to_ref,
        "terms": order.terms,
        "lines": [
            {
                "id": line.id,
                "position": line.line_number,
                "description": line.description,
                "quantity": str(line.quantity_ordered),
                "unit": line.unit,
                "unit_price": str(line.unit_price),
                "line_amount": str(line.line_amount),
                "tax": str(line.tax_amount),
                "item_ref": line.item_ref,
                "expense_ref": line.expense_ref,
                "asset_ref": line.asset_ref,
                "cost_center_ref": line.cost_center_ref,
                "subject_ref": line.subject_ref,
                "expected_delivery_date": line.expected_delivery_date,
            }
            for line in _purchase_lines(db, order.tenant_id, order.id)
        ],
    }


def submit_purchase_order(
    db: Session,
    *,
    scope: TenantScope,
    order_id: UUID,
    submitted_at: datetime,
    submitted_by_ref: str,
) -> PurchaseOrder:
    _aware("submitted_at", submitted_at)
    submitted_by_ref = _action("submitted_by_ref", submitted_by_ref)
    tenant_id = _tenant(scope)
    order = _purchase_order(db, tenant_id, order_id, lock=True)
    if order.status != PurchaseOrderStatus.DRAFT:
        raise InvalidTransition("only a draft purchase order can be submitted")
    content_sha256 = digest_document(_purchase_document(db, order))
    order.status = PurchaseOrderStatus.PENDING_APPROVAL
    order.content_sha256 = content_sha256
    order.submitted_at = submitted_at
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="purchase_order",
        aggregate_id=order.id,
        event_type="procurement.purchase_order_submitted.v1",
        occurred_at=submitted_at,
        actor_ref=submitted_by_ref,
        details={"content_sha256": content_sha256},
    )
    return order


def apply_purchase_order_approval(
    db: Session,
    *,
    scope: TenantScope,
    order_id: UUID,
    fact: ApprovalFact,
) -> ApprovedPurchaseFact:
    tenant_id = _tenant(scope)
    order = _purchase_order(db, tenant_id, order_id, lock=True)
    if order.status != PurchaseOrderStatus.PENDING_APPROVAL:
        raise InvalidTransition("purchase order is not awaiting approval")
    if order.content_sha256 is None:
        raise InvalidTransition("submitted purchase order has no content digest")
    fact.require_matches(
        subject_type="procurement.purchase_order",
        subject_id=order.id,
        content_sha256=order.content_sha256,
    )
    order.status = PurchaseOrderStatus.APPROVED
    order.approval_decision_ref = fact.decision_ref
    order.approved_at = fact.approved_at
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="purchase_order",
        aggregate_id=order.id,
        event_type="procurement.purchase_order_approved.v1",
        occurred_at=fact.approved_at,
        source_ref=fact.decision_ref,
        details={"content_sha256": fact.content_sha256},
    )
    return ApprovedPurchaseFact(
        purchase_order_id=order.id,
        order_number=order.order_number,
        supplier_ref=order.supplier_ref,
        total=Money.of(order.total_amount, currency(order.currency_code)),
        content_sha256=order.content_sha256,
        approved_at=fact.approved_at,
    )


def record_receipt_observation(
    db: Session,
    *,
    scope: TenantScope,
    order_id: UUID,
    observation: ReceiptObservation,
) -> PurchaseOrder:
    tenant_id = _tenant(scope)
    order = _purchase_order(db, tenant_id, order_id, lock=True)
    document = observation.document()
    payload_sha256 = digest_document(document)
    existing = db.scalar(
        select(ReceiptObservationRecord).where(
            ReceiptObservationRecord.tenant_id == tenant_id,
            ReceiptObservationRecord.source_owner == observation.source_owner,
            ReceiptObservationRecord.source_event_id == observation.source_event_id,
        )
    )
    if existing is not None:
        if existing.order_id != order.id or existing.payload_sha256 != payload_sha256:
            raise ObservationConflict(
                "receipt observation identity was reused with different content"
            )
        return order
    if order.status not in {
        PurchaseOrderStatus.APPROVED,
        PurchaseOrderStatus.PARTIALLY_RECEIVED,
    }:
        raise InvalidTransition("purchase order is not accepting receipt observations")
    lines = {
        line.id: line for line in _purchase_lines(db, tenant_id, order.id, lock=True)
    }
    if not {line.order_line_id for line in observation.lines} <= set(lines):
        raise ContractError("receipt observation names another purchase order's line")
    next_quantities: dict[UUID, Decimal] = {}
    for observed in observation.lines:
        line = lines[observed.order_line_id]
        next_quantity = line.quantity_received + observed.quantity_received
        if next_quantity > line.quantity_ordered:
            raise ContractError("receipt observation exceeds ordered quantity")
        next_quantities[line.id] = next_quantity
    for line_id, next_quantity in next_quantities.items():
        lines[line_id].quantity_received = next_quantity
    db.add(
        ReceiptObservationRecord(
            tenant_id=tenant_id,
            order_id=order.id,
            source_owner=observation.source_owner,
            source_event_id=observation.source_event_id,
            observed_at=observation.observed_at,
            payload_sha256=payload_sha256,
            lines_json=_canonical(document["lines"]),
        )
    )
    received = [line.quantity_received for line in lines.values()]
    ordered = [line.quantity_ordered for line in lines.values()]
    order.status = (
        PurchaseOrderStatus.RECEIVED
        if received == ordered
        else PurchaseOrderStatus.PARTIALLY_RECEIVED
    )
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="purchase_order",
        aggregate_id=order.id,
        event_type="procurement.purchase_receipt_observed.v1",
        occurred_at=observation.observed_at,
        source_ref=f"{observation.source_owner}:{observation.source_event_id}",
        details={"payload_sha256": payload_sha256, "status": order.status.value},
    )
    return order


def cancel_purchase_order(
    db: Session,
    *,
    scope: TenantScope,
    order_id: UUID,
    cancelled_at: datetime,
    actor_ref: str,
    reason: str,
) -> PurchaseOrder:
    _aware("cancelled_at", cancelled_at)
    actor_ref = _action("actor_ref", actor_ref)
    reason = _action("reason", reason, limit=4000)
    tenant_id = _tenant(scope)
    order = _purchase_order(db, tenant_id, order_id, lock=True)
    if order.status not in {
        PurchaseOrderStatus.DRAFT,
        PurchaseOrderStatus.PENDING_APPROVAL,
        PurchaseOrderStatus.APPROVED,
    }:
        raise InvalidTransition("purchase order cannot be cancelled from this state")
    if any(
        line.quantity_received > 0 for line in _purchase_lines(db, tenant_id, order.id)
    ):
        raise InvalidTransition("a purchase order with receipts cannot be cancelled")
    order.status = PurchaseOrderStatus.CANCELLED
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="purchase_order",
        aggregate_id=order.id,
        event_type="procurement.purchase_order_cancelled.v1",
        occurred_at=cancelled_at,
        actor_ref=actor_ref,
        details={"reason": reason},
    )
    return order


def close_purchase_order(
    db: Session,
    *,
    scope: TenantScope,
    order_id: UUID,
    closed_at: datetime,
    actor_ref: str,
) -> PurchaseOrder:
    _aware("closed_at", closed_at)
    actor_ref = _action("actor_ref", actor_ref)
    tenant_id = _tenant(scope)
    order = _purchase_order(db, tenant_id, order_id, lock=True)
    if order.status != PurchaseOrderStatus.RECEIVED:
        raise InvalidTransition("only a fully received purchase order can close")
    order.status = PurchaseOrderStatus.CLOSED
    order.closed_at = closed_at
    db.flush()
    _record_evidence(
        db,
        tenant_id=tenant_id,
        aggregate_kind="purchase_order",
        aggregate_id=order.id,
        event_type="procurement.purchase_order_closed.v1",
        occurred_at=closed_at,
        actor_ref=actor_ref,
        details={"status": order.status.value},
    )
    return order


__all__ = [
    "apply_award_approval",
    "apply_purchase_order_approval",
    "apply_requisition_approval",
    "apply_requisition_rejection",
    "cancel_purchase_order",
    "cancel_requisition",
    "cancel_sourcing_event",
    "close_purchase_order",
    "close_sourcing_event",
    "complete_evaluation",
    "create_purchase_order",
    "create_requisition",
    "create_sourcing_event",
    "invite_supplier",
    "publish_sourcing_event",
    "receive_bid",
    "record_budget_authorization",
    "record_receipt_observation",
    "revise_purchase_order",
    "revise_requisition",
    "revise_sourcing_event",
    "submit_purchase_order",
    "submit_requisition",
]
