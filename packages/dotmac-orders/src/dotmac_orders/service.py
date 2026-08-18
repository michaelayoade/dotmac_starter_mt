"""Canonical writers for Orders; caller transactions own commit and rollback."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from dotmac_kernel.audit import (
    MissingAuditActorError,
    UnknownAuditActorTypeError,
    resolve_audit_actor,
    write_audit_event,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging.models import OutboxEvent, OutboxStatus
from dotmac_kernel.messaging.outbox import enqueue_event
from dotmac_kernel.money import Currency, Money, MoneyError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_orders.contracts import (
    AcceptOrderCommand,
    AcknowledgeFulfillmentCommand,
    ActorRef,
    CancelOrderCommand,
    CoverageResolutionV1,
    CoverageSnapshotV1,
    FulfillmentRequestV1,
    FxSnapshotV1,
    OrderCommandResult,
    OrderEventV1,
    OrderLineSnapshotV1,
    OrderSnapshotV1,
    OrderTotals,
    ReconciliationReport,
    RecordCoverageResolutionCommand,
    SubmitOrderCommand,
    TaxSnapshotV1,
    TermValueV1,
    TermsSnapshotV1,
)
from dotmac_orders.engine import (
    OrderPhase,
    OrderStateRegistry,
    calculate_line_snapshot,
    calculate_order_totals,
    default_order_state_registry,
    fits_numeric,
)
from dotmac_orders.errors import OrderConflict, OrderError, OrderNotFound
from dotmac_orders.models import (
    CoverageGate,
    CoverageObligation,
    CoverageResolutionReceipt,
    FulfillmentRequest,
    Order,
    OrderEvent,
    OrderLineSnapshot,
)

SUBMIT_SCOPE = "orders.submit"
ACCEPT_SCOPE = "orders.accept"
CANCEL_SCOPE = "orders.cancel"
COVERAGE_SCOPE = "orders.coverage.resolve"
FULFILLMENT_ACK_SCOPE = "orders.fulfillment.acknowledge"

ORDER_SUBMITTED_EVENT = "orders.order_submitted.v1"
ORDER_ACCEPTED_EVENT = "orders.order_accepted.v1"
ORDER_CANCELLED_EVENT = "orders.order_cancelled.v1"
FULFILLMENT_REQUESTED_EVENT = "orders.fulfillment_requested.v1"


def _fingerprint(payload: object) -> str:
    # Importing the kernel idempotency engine reaches ``dotmac_kernel.db``.
    # Keep it behind a command/reconciliation call so a manifest or wheel can
    # be inspected without configuring a database.
    from dotmac_kernel.idempotency import fingerprint_of

    return fingerprint_of(payload)


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if isinstance(value, str) else None


def _terms_payload(snapshot: TermsSnapshotV1) -> dict[str, object]:
    return {
        "version_ref": snapshot.version_ref,
        "values": [
            {"name": term.name, "value": term.value} for term in snapshot.values
        ],
    }


def _taxes_payload(taxes: tuple[TaxSnapshotV1, ...]) -> list[dict[str, object]]:
    return [
        {
            "tax_code": tax.tax_code,
            "source_version": tax.source_version,
            "taxable_basis": str(tax.taxable_basis.amount),
            "rate": str(tax.rate) if tax.rate is not None else None,
            "amount": str(tax.amount.amount),
        }
        for tax in taxes
    ]


def _taxes_contract(
    payload: object, *, currency: Currency
) -> tuple[TaxSnapshotV1, ...]:
    if not isinstance(payload, list):
        raise OrderConflict(
            "invalid_stored_tax_snapshot",
            "The persisted tax snapshot does not match the V1 contract.",
        )
    output: list[TaxSnapshotV1] = []
    for item in payload:
        if not isinstance(item, dict):
            raise OrderConflict(
                "invalid_stored_tax_snapshot",
                "The persisted tax snapshot does not match the V1 contract.",
            )
        tax_code = item.get("tax_code")
        source_version = item.get("source_version")
        taxable_basis = item.get("taxable_basis")
        rate = item.get("rate")
        amount = item.get("amount")
        if (
            not isinstance(tax_code, str)
            or not isinstance(source_version, str)
            or not isinstance(taxable_basis, str)
            or (rate is not None and not isinstance(rate, str))
            or not isinstance(amount, str)
        ):
            raise OrderConflict(
                "invalid_stored_tax_snapshot",
                "The persisted tax snapshot does not match the V1 contract.",
            )
        try:
            basis_money = Money.of(taxable_basis, currency)
            amount_money = Money.of(amount, currency)
            rate_value = Decimal(rate) if rate is not None else None
            if (
                not basis_money.amount.is_finite()
                or not amount_money.amount.is_finite()
                or (rate_value is not None and not rate_value.is_finite())
            ):
                raise InvalidOperation
            output.append(
                TaxSnapshotV1(
                    tax_code=tax_code,
                    source_version=source_version,
                    taxable_basis=basis_money,
                    rate=rate_value,
                    amount=amount_money,
                )
            )
        except (InvalidOperation, MoneyError) as exc:
            raise OrderConflict(
                "invalid_stored_tax_snapshot",
                "The persisted tax snapshot does not match the V1 contract.",
            ) from exc
    return tuple(output)


def _terms_contract(payload: Mapping[str, object]) -> TermsSnapshotV1:
    version_ref = payload.get("version_ref")
    raw_values = payload.get("values")
    if not isinstance(version_ref, str) or not isinstance(raw_values, list):
        raise OrderConflict(
            "invalid_stored_terms_snapshot",
            "The persisted terms snapshot does not match the V1 contract.",
        )
    values: list[TermValueV1] = []
    for item in raw_values:
        if not isinstance(item, dict):
            raise OrderConflict(
                "invalid_stored_terms_snapshot",
                "The persisted terms snapshot does not match the V1 contract.",
            )
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise OrderConflict(
                "invalid_stored_terms_snapshot",
                "The persisted terms snapshot does not match the V1 contract.",
            )
        values.append(TermValueV1(name=name, value=value))
    return TermsSnapshotV1(version_ref=version_ref, values=tuple(values))


def _audit(
    db: Session,
    *,
    scope: TenantScope,
    actor: ActorRef,
    action: str,
    order_id: UUID,
    occurred_at: datetime,
    is_success: bool,
    details: Mapping[str, object] | None = None,
) -> None:
    actor_type, actor_id, actor_label = _actor_values(actor)
    write_audit_event(
        db,
        tenant_id=scope.tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        entity_type="order",
        entity_id=str(order_id),
        is_success=is_success,
        occurred_at=_aware(occurred_at, field="audit_occurred_at"),
        details=dict(details or {}),
    )


def _aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OrderError(
            "naive_instant",
            f"{field} must carry an explicit timezone.",
            details={"field": field},
        )
    return value.astimezone(UTC)


def _required(value: str, *, field: str) -> str:
    if not value or not value.strip():
        raise OrderError("missing_value", f"{field} is required.")
    return value


def _actor_values(actor: ActorRef) -> tuple[str, str | None, str | None]:
    try:
        actor_type, actor_id = resolve_audit_actor(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            actor_party_id=None,
        )
    except (MissingAuditActorError, UnknownAuditActorTypeError) as exc:
        raise OrderError(
            "invalid_actor",
            "The lifecycle actor must satisfy the kernel audit actor contract.",
        ) from exc
    return actor_type, actor_id, actor.actor_label


def _actor_payload(
    values: tuple[str, str | None, str | None],
) -> Mapping[str, object]:
    actor_type, actor_id, actor_label = values
    return {
        "actor_type": actor_type,
        "actor_id": actor_id,
        "actor_label": actor_label,
    }


def _fx_payload(snapshot: FxSnapshotV1 | None) -> Mapping[str, object] | None:
    if snapshot is None:
        return None
    return {
        "base_currency_code": snapshot.base_currency_code.upper(),
        "quote_currency_code": snapshot.quote_currency_code.upper(),
        "rate": format(snapshot.rate.normalize(), "f"),
        "rate_ref": snapshot.rate_ref,
        "source": snapshot.source,
        "as_of": _aware(snapshot.as_of, field="fx_as_of").isoformat(),
    }


def _commercial_snapshot_payload(
    command: SubmitOrderCommand,
    *,
    currency: Currency,
    line_fingerprints: tuple[str, ...],
    obligations: tuple[str, ...],
) -> dict[str, object]:
    """Canonical stored commercial effects, independent of caller ordering."""

    return {
        "order_reference": command.order_reference,
        "customer_ref": command.customer_ref,
        "currency_code": currency.code,
        "currency_minor_units": currency.minor_units,
        "line_fingerprints": line_fingerprints,
        "coverage_obligation_refs": obligations,
        "source_ref": command.source_ref,
        "source_version": command.source_version,
        "fx_snapshot": _fx_payload(command.fx_snapshot),
    }


def _submission_payload(
    commercial_snapshot: Mapping[str, object],
    *,
    initial_state: str,
    submitted_by: tuple[str, str | None, str | None],
    submitted_at: datetime,
) -> dict[str, object]:
    return {
        **commercial_snapshot,
        "initial_state": initial_state,
        "submitted_by": _actor_payload(submitted_by),
        "submitted_at": submitted_at.isoformat(),
    }


def _event(
    db: Session,
    *,
    scope: TenantScope,
    order_id: UUID,
    event_ref: str,
    event_type: str,
    actor: ActorRef,
    occurred_at: datetime,
    from_state: str | None,
    to_state: str | None,
    details: Mapping[str, object] | None = None,
) -> OrderEvent:
    actor_type, actor_id, actor_label = _actor_values(actor)
    next_sequence = int(
        db.scalar(
            select(func.coalesce(func.max(OrderEvent.event_sequence), 0)).where(
                OrderEvent.tenant_id == scope.tenant_id,
                OrderEvent.order_id == order_id,
            )
        )
        or 0
    ) + 1
    row = OrderEvent(
        id=uuid4(),
        tenant_id=scope.tenant_id,
        order_id=order_id,
        event_sequence=next_sequence,
        event_ref=event_ref,
        event_type=event_type,
        from_state=from_state,
        to_state=to_state,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        occurred_at=_aware(occurred_at, field="occurred_at"),
        details=dict(details or {}),
    )
    db.add(row)
    return row


def _load_order(
    db: Session, *, scope: TenantScope, order_id: UUID, lock: bool = False
) -> Order:
    stmt = select(Order).where(
        Order.tenant_id == scope.tenant_id,
        Order.id == order_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    order = db.execute(stmt).scalar_one_or_none()
    if order is None:
        raise OrderNotFound(
            "order_not_found",
            "No order exists in the required tenant scope.",
            details={"order_id": str(order_id)},
        )
    return order


def _load_lines(
    db: Session, *, scope: TenantScope, order_id: UUID
) -> tuple[OrderLineSnapshot, ...]:
    return tuple(
        db.execute(
            select(OrderLineSnapshot)
            .where(
                OrderLineSnapshot.tenant_id == scope.tenant_id,
                OrderLineSnapshot.order_id == order_id,
            )
            .order_by(OrderLineSnapshot.line_key)
        )
        .scalars()
        .all()
    )


def _line_contract(row: OrderLineSnapshot) -> OrderLineSnapshotV1:
    unit = Currency(row.currency_code, row.currency_minor_units)
    return OrderLineSnapshotV1(
        line_id=row.id,
        line_key=row.line_key,
        description=row.description,
        quantity=row.quantity,
        unit_price=Money.of(row.unit_price, unit),
        extended_price=Money.of(row.extended_price, unit),
        discount=Money.of(row.discount_amount, unit),
        tax=Money.of(row.tax_amount, unit),
        taxes=_taxes_contract(row.tax_snapshot, currency=unit),
        total=Money.of(row.line_total, unit),
        price_version_ref=row.price_version_ref,
        terms_ref=row.terms_ref,
        terms_snapshot=_terms_contract(row.terms_snapshot),
        specification_ref=row.specification_ref,
        snapshot_fingerprint=row.snapshot_fingerprint,
        source_ref=row.source_ref,
        source_version=row.source_version,
    )


def _optional_actor(
    actor_type: str | None,
    actor_id: str | None,
    actor_label: str | None,
) -> ActorRef | None:
    if actor_type is None:
        if actor_id is not None or actor_label is not None:
            raise OrderConflict(
                "invalid_stored_actor",
                "The persisted lifecycle actor snapshot is incomplete.",
            )
        return None
    actor = ActorRef(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
    )
    try:
        resolved_type, resolved_id, resolved_label = _actor_values(actor)
    except OrderError as exc:
        raise OrderConflict(
            "invalid_stored_actor",
            "The persisted lifecycle actor snapshot is invalid.",
        ) from exc
    return ActorRef(
        actor_type=resolved_type,
        actor_id=resolved_id,
        actor_label=resolved_label,
    )


def get_order_snapshot(
    db: Session, *, scope: TenantScope, order_id: UUID
) -> OrderSnapshotV1:
    order = _load_order(db, scope=scope, order_id=order_id)
    unit = Currency(order.currency_code, order.currency_minor_units)
    lines = tuple(
        _line_contract(row)
        for row in _load_lines(db, scope=scope, order_id=order.id)
    )
    fx_snapshot = None
    if order.fx_rate is not None:
        base_code = order.fx_base_currency_code
        rate_ref = order.fx_rate_ref
        source = order.fx_source
        as_of = order.fx_as_of
        if (
            base_code is None
            or rate_ref is None
            or source is None
            or as_of is None
        ):
            raise OrderConflict(
                "invalid_stored_fx_snapshot",
                "The persisted FX snapshot is incomplete.",
            )
        fx_snapshot = FxSnapshotV1(
            base_currency_code=base_code,
            quote_currency_code=order.currency_code,
            rate=order.fx_rate,
            rate_ref=rate_ref,
            source=source,
            as_of=as_of,
        )
    gate = db.execute(
        select(CoverageGate).where(
            CoverageGate.tenant_id == scope.tenant_id,
            CoverageGate.order_id == order.id,
        )
    ).scalar_one_or_none()
    if gate is None:
        raise OrderConflict(
            "missing_stored_coverage_gate",
            "The persisted order has no finite coverage gate.",
        )
    obligations = tuple(
        db.scalars(
            select(CoverageObligation.obligation_ref)
            .where(
                CoverageObligation.tenant_id == scope.tenant_id,
                CoverageObligation.gate_id == gate.id,
            )
            .order_by(CoverageObligation.obligation_ref)
        ).all()
    )
    resolutions = tuple(
        _coverage_contract(row, order_id=order.id)
        for row in db.scalars(
            select(CoverageResolutionReceipt)
            .where(
                CoverageResolutionReceipt.tenant_id == scope.tenant_id,
                CoverageResolutionReceipt.gate_id == gate.id,
            )
            .order_by(CoverageResolutionReceipt.obligation_ref)
        ).all()
    )
    submitted_by = _optional_actor(
        order.submitted_actor_type,
        order.submitted_actor_id,
        order.submitted_actor_label,
    )
    if submitted_by is None:
        raise OrderConflict(
            "missing_stored_submission_actor",
            "The persisted order has no submission actor.",
        )
    return OrderSnapshotV1(
        order_id=order.id,
        order_reference=order.order_reference,
        customer_ref=order.customer_ref,
        state=order.state,
        totals=OrderTotals(
            subtotal=Money.of(order.subtotal_amount, unit),
            discount=Money.of(order.discount_amount, unit),
            tax=Money.of(order.tax_amount, unit),
            total=Money.of(order.total_amount, unit),
        ),
        lines=lines,
        snapshot_fingerprint=order.snapshot_fingerprint,
        source_ref=order.source_ref,
        source_version=order.source_version,
        submitted_by=submitted_by,
        submitted_at=order.submitted_at,
        accepted_by=_optional_actor(
            order.accepted_actor_type,
            order.accepted_actor_id,
            order.accepted_actor_label,
        ),
        accepted_at=order.accepted_at,
        covered_at=order.covered_at,
        cancelled_by=_optional_actor(
            order.cancelled_actor_type,
            order.cancelled_actor_id,
            order.cancelled_actor_label,
        ),
        cancelled_at=order.cancelled_at,
        cancellation_reason=order.cancellation_reason,
        coverage=CoverageSnapshotV1(
            state=gate.state,
            obligation_refs=obligations,
            resolutions=resolutions,
            satisfied_at=gate.satisfied_at,
        ),
        fx_snapshot=fx_snapshot,
    )


def _request_contract(
    order: Order, line: OrderLineSnapshot, request: FulfillmentRequest
) -> FulfillmentRequestV1:
    return FulfillmentRequestV1(
        request_id=request.id,
        order_id=order.id,
        order_reference=order.order_reference,
        customer_ref=order.customer_ref,
        line=_line_contract(line),
        request_fingerprint=request.request_fingerprint,
        state=request.state,
        publication_count=request.publication_count,
        acceptance_ref=request.acceptance_ref,
        accepted_at=request.accepted_at,
    )


def get_order_timeline(
    db: Session, *, scope: TenantScope, order_id: UUID
) -> tuple[OrderEventV1, ...]:
    """Read the owner-recorded lifecycle without exposing persistence rows."""

    _load_order(db, scope=scope, order_id=order_id)
    rows = db.scalars(
        select(OrderEvent)
        .where(
            OrderEvent.tenant_id == scope.tenant_id,
            OrderEvent.order_id == order_id,
        )
        .order_by(OrderEvent.event_sequence)
    ).all()
    output: list[OrderEventV1] = []
    for row in rows:
        actor = _optional_actor(row.actor_type, row.actor_id, row.actor_label)
        if actor is None:
            raise OrderConflict(
                "missing_stored_event_actor",
                "The persisted order event has no actor.",
            )
        output.append(
            OrderEventV1(
                event_id=row.id,
                sequence=row.event_sequence,
                event_ref=row.event_ref,
                event_type=row.event_type,
                from_state=row.from_state,
                to_state=row.to_state,
                actor=actor,
                occurred_at=row.occurred_at,
                recorded_at=row.recorded_at,
            )
        )
    return tuple(output)


def _coverage_contract(
    row: CoverageResolutionReceipt, *, order_id: UUID
) -> CoverageResolutionV1:
    return CoverageResolutionV1(
        order_id=order_id,
        obligation_ref=row.obligation_ref,
        resolution_ref=row.resolution_ref,
        resolution_kind=row.resolution_kind,
        source_ref=row.source_ref,
        source_version=row.source_version,
        resolved_at=row.resolved_at,
    )


def _request_payload(request: FulfillmentRequestV1) -> Mapping[str, object]:
    line = request.line
    return {
        "contract_version": 1,
        "request_id": str(request.request_id),
        "order_id": str(request.order_id),
        "order_reference": request.order_reference,
        "customer_ref": request.customer_ref,
        "request_fingerprint": request.request_fingerprint,
        "line": {
            "line_id": str(line.line_id),
            "line_key": line.line_key,
            "description": line.description,
            "quantity": str(line.quantity),
            "unit_price": str(line.unit_price.amount),
            "currency_code": line.unit_price.currency.code,
            "currency_minor_units": line.unit_price.currency.minor_units,
            "discount": str(line.discount.amount),
            "tax": str(line.tax.amount),
            "taxes": _taxes_payload(line.taxes),
            "total": str(line.total.amount),
            "price_version_ref": line.price_version_ref,
            "terms_ref": line.terms_ref,
            "terms_snapshot": _terms_payload(line.terms_snapshot),
            "specification_ref": line.specification_ref,
            "snapshot_fingerprint": line.snapshot_fingerprint,
            "source_ref": line.source_ref,
            "source_version": line.source_version,
        },
    }


def _publish_request(
    db: Session,
    *,
    scope: TenantScope,
    request: FulfillmentRequest,
    contract: FulfillmentRequestV1,
    occurred_at: datetime,
    correlation_id: str | None,
) -> None:
    event = enqueue_event(
        db,
        tenant_id=scope.tenant_id,
        event_type=FULFILLMENT_REQUESTED_EVENT,
        payload=_request_payload(contract),
        correlation_id=correlation_id,
    )
    request.last_outbox_event_id = event.id
    request.last_published_at = _aware(occurred_at, field="published_at")
    request.publication_count += 1


def _stage_fulfillment_requests(
    db: Session,
    *,
    scope: TenantScope,
    order: Order,
    occurred_at: datetime,
    correlation_id: str | None,
    states: OrderStateRegistry,
) -> tuple[FulfillmentRequestV1, ...]:
    phase = states.require(order.state).phase
    if phase is OrderPhase.ACCEPTED:
        previous = order.state
        order.state = states.transition(previous, "covered")
        order.covered_at = _aware(occurred_at, field="covered_at")
        _event(
            db,
            scope=scope,
            order_id=order.id,
            event_ref=f"coverage-satisfied:{order.id}",
            event_type="orders.order_covered.v1",
            actor=ActorRef(actor_type="system", actor_id="orders-coverage-gate"),
            occurred_at=occurred_at,
            from_state=previous,
            to_state=order.state,
        )
        phase = states.require(order.state).phase
    if phase is OrderPhase.COVERED:
        previous = order.state
        order.state = states.transition(previous, "fulfillment_requested")
        _event(
            db,
            scope=scope,
            order_id=order.id,
            event_ref=f"fulfillment-requested:{order.id}",
            event_type=FULFILLMENT_REQUESTED_EVENT,
            actor=ActorRef(actor_type="system", actor_id="orders-fulfillment"),
            occurred_at=occurred_at,
            from_state=previous,
            to_state=order.state,
        )
        phase = states.require(order.state).phase
    if phase is not OrderPhase.FULFILLMENT:
        raise OrderError(
            "invalid_fulfillment_phase",
            "Fulfillment requests require an accepted-to-fulfillment path.",
        )

    lines = _load_lines(db, scope=scope, order_id=order.id)
    existing = {
        row.line_snapshot_id: row
        for row in db.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.tenant_id == scope.tenant_id,
                FulfillmentRequest.order_id == order.id,
            )
        )
        .scalars()
        .all()
    }
    output: list[FulfillmentRequestV1] = []
    for line in lines:
        request = existing.get(line.id)
        if request is None:
            request_id = uuid5(
                NAMESPACE_URL,
                f"dotmac-orders:{scope.tenant_id}:{order.id}:{line.id}:fulfillment:v1",
            )
            request_payload = {
                "request_id": str(request_id),
                "order_id": str(order.id),
                "line_snapshot_fingerprint": line.snapshot_fingerprint,
            }
            request = FulfillmentRequest(
                id=request_id,
                tenant_id=scope.tenant_id,
                order_id=order.id,
                line_snapshot_id=line.id,
                state="pending",
                request_fingerprint=_fingerprint(request_payload),
                publication_count=0,
                last_published_at=_aware(occurred_at, field="published_at"),
            )
            db.add(request)
            contract = _request_contract(order, line, request)
            _publish_request(
                db,
                scope=scope,
                request=request,
                contract=contract,
                occurred_at=occurred_at,
                correlation_id=correlation_id,
            )
        output.append(_request_contract(order, line, request))
    return tuple(output)


def submit_order(
    db: Session,
    *,
    scope: TenantScope,
    command: SubmitOrderCommand,
    states: OrderStateRegistry | None = None,
) -> OrderCommandResult:
    states = states or default_order_state_registry()
    initial = states.require(command.initial_state)
    if initial.phase is not OrderPhase.SUBMITTED:
        raise OrderError(
            "invalid_initial_state",
            "A checkout snapshot must enter a submitted-phase state.",
        )
    _required(command.order_reference, field="order_reference")
    _required(command.customer_ref, field="customer_ref")
    if (command.source_ref is None) != (command.source_version is None) or any(
        value is not None and not value.strip()
        for value in (command.source_ref, command.source_version)
    ):
        raise OrderError(
            "invalid_source_provenance",
            "Order source_ref and source_version must be supplied together.",
        )
    submitted_at = _aware(command.submitted_at, field="submitted_at")
    submitted_actor = _actor_values(command.submitted_by)
    if len(command.currency_code) != 3 or not command.currency_code.isalpha():
        raise OrderError(
            "invalid_currency",
            "currency_code must be a three-letter code.",
        )
    if not 0 <= command.currency_minor_units <= 6:
        raise OrderError(
            "invalid_currency_precision",
            "currency_minor_units must be between zero and six.",
        )
    order_currency = Currency(command.currency_code, command.currency_minor_units)
    snapshots = tuple(
        sorted(
            (calculate_line_snapshot(line) for line in command.lines),
            key=lambda snapshot: snapshot.line_key,
        )
    )
    for line in snapshots:
        if line.unit_price.currency != order_currency:
            raise OrderError(
                "order_currency_mismatch",
                "Every line must use the order currency and minor-unit contract.",
            )
    totals = calculate_order_totals(snapshots)
    obligations = tuple(sorted(command.coverage_obligation_refs))
    if not obligations or any(not item.strip() for item in obligations):
        raise OrderError(
            "empty_coverage_set",
            "An order must bind a non-empty finite coverage-obligation set.",
        )
    if len(set(obligations)) != len(obligations):
        raise OrderError(
            "duplicate_coverage_obligation",
            "Coverage-obligation references must be unique within an order.",
        )
    fx = command.fx_snapshot
    if fx is not None:
        if (
            isinstance(fx.rate, float)
            or not isinstance(fx.rate, Decimal)
            or not fx.rate.is_finite()
            or fx.rate <= 0
        ):
            raise OrderError(
                "invalid_fx_rate",
                "A captured FX rate must be a finite positive Decimal.",
            )
        if not fits_numeric(fx.rate, precision=38, scale=18):
            raise OrderError(
                "fx_rate_out_of_range",
                "The captured FX rate must fit NUMERIC(38,18) exactly.",
            )
        for field, value in (
            ("fx_base_currency_code", fx.base_currency_code),
            ("fx_quote_currency_code", fx.quote_currency_code),
            ("fx_rate_ref", fx.rate_ref),
            ("fx_source", fx.source),
        ):
            _required(value, field=field)
        if fx.quote_currency_code.upper() != order_currency.code:
            raise OrderError(
                "fx_quote_currency_mismatch",
                "The FX quote currency must be the order currency.",
            )
        if fx.base_currency_code.upper() == order_currency.code:
            raise OrderError(
                "invalid_fx_pair",
                "A cross-currency snapshot must name two different currencies.",
            )
        _aware(fx.as_of, field="fx_as_of")

    commercial_snapshot = _commercial_snapshot_payload(
        command,
        currency=order_currency,
        line_fingerprints=tuple(snapshot.fingerprint for snapshot in snapshots),
        obligations=obligations,
    )
    order_fingerprint = _fingerprint(commercial_snapshot)
    payload = _submission_payload(
        commercial_snapshot,
        initial_state=command.initial_state,
        submitted_by=submitted_actor,
        submitted_at=submitted_at,
    )

    def operation(session: Session) -> Mapping[str, object]:
        actor_type, actor_id, actor_label = submitted_actor
        order_id = uuid4()
        order = Order(
            id=order_id,
            tenant_id=scope.tenant_id,
            order_reference=command.order_reference,
            customer_ref=command.customer_ref,
            state=command.initial_state,
            currency_code=order_currency.code,
            currency_minor_units=order_currency.minor_units,
            subtotal_amount=totals.subtotal.amount,
            discount_amount=totals.discount.amount,
            tax_amount=totals.tax.amount,
            total_amount=totals.total.amount,
            snapshot_fingerprint=order_fingerprint,
            snapshot_frozen_at=None,
            source_ref=command.source_ref,
            source_version=command.source_version,
            fx_rate=fx.rate if fx is not None else None,
            fx_base_currency_code=(
                fx.base_currency_code.upper() if fx is not None else None
            ),
            fx_rate_ref=fx.rate_ref if fx is not None else None,
            fx_source=fx.source if fx is not None else None,
            fx_as_of=_aware(fx.as_of, field="fx_as_of") if fx is not None else None,
            submitted_actor_type=actor_type,
            submitted_actor_id=actor_id,
            submitted_actor_label=actor_label,
            submitted_at=submitted_at,
        )
        session.add(order)
        # Persist an unfrozen header, insert the entire accepted snapshot set,
        # then freeze it in this same transaction. The migration refuses every
        # later line insert, including a direct ORM or SQL write.
        session.flush()
        gate = CoverageGate(
            id=uuid4(),
            tenant_id=scope.tenant_id,
            order_id=order_id,
            state="binding",
            obligation_count=len(obligations),
            resolved_count=0,
        )
        session.add(gate)
        for snapshot in snapshots:
            session.add(
                OrderLineSnapshot(
                    id=uuid4(),
                    tenant_id=scope.tenant_id,
                    order_id=order_id,
                    line_key=snapshot.line_key,
                    description=snapshot.description,
                    quantity=snapshot.quantity,
                    currency_code=snapshot.unit_price.currency.code,
                    currency_minor_units=snapshot.unit_price.currency.minor_units,
                    unit_price=snapshot.unit_price.amount,
                    extended_price=snapshot.extended_price.amount,
                    discount_amount=snapshot.discount.amount,
                    tax_amount=snapshot.tax.amount,
                    tax_snapshot=_taxes_payload(snapshot.taxes),
                    line_total=snapshot.total.amount,
                    price_version_ref=snapshot.price_version_ref,
                    terms_ref=snapshot.terms_ref,
                    terms_snapshot=_terms_payload(snapshot.terms_snapshot),
                    specification_ref=snapshot.specification_ref,
                    source_ref=snapshot.source_ref,
                    source_version=snapshot.source_version,
                    snapshot_fingerprint=snapshot.fingerprint,
                )
            )
        for obligation_ref in obligations:
            session.add(
                CoverageObligation(
                    id=uuid4(),
                    tenant_id=scope.tenant_id,
                    gate_id=gate.id,
                    obligation_ref=obligation_ref,
                )
            )
        session.flush()
        order.snapshot_frozen_at = submitted_at
        gate.state = "open"
        _event(
            session,
            scope=scope,
            order_id=order_id,
            event_ref=f"submit:{command.idempotency_key}",
            event_type=ORDER_SUBMITTED_EVENT,
            actor=command.submitted_by,
            occurred_at=submitted_at,
            from_state=None,
            to_state=command.initial_state,
            details={"snapshot_fingerprint": order_fingerprint},
        )
        enqueue_event(
            session,
            tenant_id=scope.tenant_id,
            event_type=ORDER_SUBMITTED_EVENT,
            payload={
                "contract_version": 1,
                "order_id": str(order_id),
                "order_reference": command.order_reference,
                "snapshot_fingerprint": order_fingerprint,
            },
            correlation_id=command.correlation_id,
        )
        _audit(
            session,
            scope=scope,
            actor=command.submitted_by,
            action="orders.submitted",
            order_id=order_id,
            occurred_at=submitted_at,
            is_success=True,
            details={"snapshot_fingerprint": order_fingerprint},
        )
        session.flush()
        return {"order_id": str(order_id)}

    from dotmac_kernel.idempotency import execute_once

    try:
        outcome = execute_once(
            db,
            tenant_id=scope.tenant_id,
            scope=SUBMIT_SCOPE,
            key=command.idempotency_key,
            fingerprint=_fingerprint(payload),
            operation=operation,
            correlation_id=command.correlation_id,
        )
    except IntegrityError as exc:
        if _constraint_name(exc) != "uq_orders_tenant_reference":
            raise
        raise OrderConflict(
            "order_identity_conflict",
            "The tenant already has an order with this order reference.",
        ) from exc
    order_id = UUID(str(outcome.result["order_id"]))
    return OrderCommandResult(
        order=get_order_snapshot(db, scope=scope, order_id=order_id),
        replayed=outcome.replayed,
    )


def accept_order(
    db: Session,
    *,
    scope: TenantScope,
    command: AcceptOrderCommand,
    states: OrderStateRegistry | None = None,
) -> OrderCommandResult:
    states = states or default_order_state_registry()
    target = states.require(command.target_state)
    if target.phase is not OrderPhase.ACCEPTED:
        raise OrderError(
            "invalid_acceptance_target",
            "Accepting an order requires an accepted-phase target state.",
        )
    accepted_at = _aware(command.accepted_at, field="accepted_at")
    accepted_actor = _actor_values(command.accepted_by)
    payload = {
        "order_id": str(command.order_id),
        "target_state": command.target_state,
        "accepted_at": accepted_at.isoformat(),
        "accepted_by": _actor_payload(accepted_actor),
    }

    def operation(session: Session) -> Mapping[str, object]:
        order = _load_order(session, scope=scope, order_id=command.order_id, lock=True)
        previous = order.state
        order.state = states.transition(previous, command.target_state)
        actor_type, actor_id, actor_label = accepted_actor
        order.accepted_actor_type = actor_type
        order.accepted_actor_id = actor_id
        order.accepted_actor_label = actor_label
        order.accepted_at = accepted_at
        _event(
            session,
            scope=scope,
            order_id=order.id,
            event_ref=f"accept:{command.idempotency_key}",
            event_type=ORDER_ACCEPTED_EVENT,
            actor=command.accepted_by,
            occurred_at=accepted_at,
            from_state=previous,
            to_state=order.state,
        )
        enqueue_event(
            session,
            tenant_id=scope.tenant_id,
            event_type=ORDER_ACCEPTED_EVENT,
            payload={
                "contract_version": 1,
                "order_id": str(order.id),
                "order_reference": order.order_reference,
                "snapshot_fingerprint": order.snapshot_fingerprint,
            },
            correlation_id=command.correlation_id,
        )
        _audit(
            session,
            scope=scope,
            actor=command.accepted_by,
            action="orders.accepted",
            order_id=order.id,
            occurred_at=accepted_at,
            is_success=True,
        )
        gate = session.execute(
            select(CoverageGate)
            .where(
                CoverageGate.tenant_id == scope.tenant_id,
                CoverageGate.order_id == order.id,
            )
            .with_for_update()
        ).scalar_one()
        if gate.satisfied_at is not None:
            _stage_fulfillment_requests(
                session,
                scope=scope,
                order=order,
                occurred_at=accepted_at,
                correlation_id=command.correlation_id,
                states=states,
            )
        session.flush()
        return {"order_id": str(order.id)}

    from dotmac_kernel.idempotency import execute_once

    outcome = execute_once(
        db,
        tenant_id=scope.tenant_id,
        scope=ACCEPT_SCOPE,
        key=command.idempotency_key,
        fingerprint=_fingerprint(payload),
        operation=operation,
        correlation_id=command.correlation_id,
    )
    order = get_order_snapshot(db, scope=scope, order_id=command.order_id)
    requests = _fulfillment_contracts(db, scope=scope, order_id=command.order_id)
    return OrderCommandResult(
        order=order,
        replayed=outcome.replayed,
        fulfillment_requests=requests,
    )


def _fulfillment_contracts(
    db: Session, *, scope: TenantScope, order_id: UUID
) -> tuple[FulfillmentRequestV1, ...]:
    order = _load_order(db, scope=scope, order_id=order_id)
    lines = {row.id: row for row in _load_lines(db, scope=scope, order_id=order_id)}
    rows = db.execute(
        select(FulfillmentRequest)
        .where(
            FulfillmentRequest.tenant_id == scope.tenant_id,
            FulfillmentRequest.order_id == order_id,
        )
        .order_by(FulfillmentRequest.id)
    ).scalars()
    return tuple(
        _request_contract(order, lines[row.line_snapshot_id], row)
        for row in rows
    )


def record_coverage_resolution(
    db: Session,
    *,
    scope: TenantScope,
    command: RecordCoverageResolutionCommand,
    states: OrderStateRegistry | None = None,
) -> OrderCommandResult:
    states = states or default_order_state_registry()
    obligation_ref = _required(command.obligation_ref, field="obligation_ref")
    resolution_ref = _required(command.resolution_ref, field="resolution_ref")
    resolution_kind = _required(command.resolution_kind, field="resolution_kind")
    source_ref = _required(command.source_ref, field="source_ref")
    source_version = _required(command.source_version, field="source_version")
    resolved_at = _aware(command.resolved_at, field="resolved_at")
    payload = {
        "order_id": str(command.order_id),
        "obligation_ref": obligation_ref,
        "resolution_ref": resolution_ref,
        "resolution_kind": resolution_kind,
        "source_ref": source_ref,
        "source_version": source_version,
        "resolved_at": resolved_at.isoformat(),
    }
    receipt_fingerprint = _fingerprint(payload)

    def operation(session: Session) -> Mapping[str, object]:
        order = _load_order(session, scope=scope, order_id=command.order_id, lock=True)
        gate = session.execute(
            select(CoverageGate)
            .where(
                CoverageGate.tenant_id == scope.tenant_id,
                CoverageGate.order_id == order.id,
            )
            .with_for_update()
        ).scalar_one()
        obligation = session.execute(
            select(CoverageObligation).where(
                CoverageObligation.tenant_id == scope.tenant_id,
                CoverageObligation.gate_id == gate.id,
                CoverageObligation.obligation_ref == obligation_ref,
            )
        ).scalar_one_or_none()
        if obligation is None:
            raise OrderError(
                "unregistered_coverage_obligation",
                "Coverage can only resolve a member of the bound finite set.",
            )
        existing = session.execute(
            select(CoverageResolutionReceipt).where(
                CoverageResolutionReceipt.tenant_id == scope.tenant_id,
                CoverageResolutionReceipt.gate_id == gate.id,
                CoverageResolutionReceipt.obligation_ref == obligation_ref,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.receipt_fingerprint != receipt_fingerprint:
                raise OrderConflict(
                    "coverage_resolution_conflict",
                    "An obligation already has a different satisfying receipt.",
                )
            return {"order_id": str(order.id), "receipt_id": str(existing.id)}

        receipt = CoverageResolutionReceipt(
            id=uuid4(),
            tenant_id=scope.tenant_id,
            gate_id=gate.id,
            obligation_ref=obligation_ref,
            resolution_ref=resolution_ref,
            resolution_kind=resolution_kind,
            source_ref=source_ref,
            source_version=source_version,
            receipt_fingerprint=receipt_fingerprint,
            resolved_at=resolved_at,
        )
        session.add(receipt)
        session.flush()
        gate.resolved_count = int(
            session.scalar(
                select(func.count(CoverageResolutionReceipt.id)).where(
                    CoverageResolutionReceipt.tenant_id == scope.tenant_id,
                    CoverageResolutionReceipt.gate_id == gate.id,
                )
            )
            or 0
        )
        became_satisfied = (
            gate.resolved_count == gate.obligation_count
            and gate.satisfied_at is None
        )
        if became_satisfied:
            gate.state = "satisfied"
            gate.satisfied_at = resolved_at
        _event(
            session,
            scope=scope,
            order_id=order.id,
            event_ref=f"coverage:{command.idempotency_key}",
            event_type="orders.coverage_resolved.v1",
            actor=ActorRef(actor_type="system", actor_id="coverage-observer"),
            occurred_at=resolved_at,
            from_state=None,
            to_state=gate.state,
            details={
                "obligation_ref": obligation_ref,
                "resolution_ref": resolution_ref,
            },
        )
        if (
            became_satisfied
            and states.require(order.state).phase is OrderPhase.ACCEPTED
        ):
            _stage_fulfillment_requests(
                session,
                scope=scope,
                order=order,
                occurred_at=resolved_at,
                correlation_id=command.correlation_id,
                states=states,
            )
        _audit(
            session,
            scope=scope,
            actor=ActorRef(actor_type="system", actor_id="coverage-observer"),
            action="orders.coverage_observed",
            order_id=order.id,
            occurred_at=resolved_at,
            is_success=True,
            details={
                "obligation_ref": obligation_ref,
                "resolution_ref": resolution_ref,
                "source_ref": source_ref,
            },
        )
        session.flush()
        return {"order_id": str(order.id), "receipt_id": str(receipt.id)}

    from dotmac_kernel.idempotency import execute_once

    try:
        outcome = execute_once(
            db,
            tenant_id=scope.tenant_id,
            scope=COVERAGE_SCOPE,
            key=command.idempotency_key,
            fingerprint=receipt_fingerprint,
            operation=operation,
            correlation_id=command.correlation_id,
        )
    except IntegrityError as exc:
        if (
            _constraint_name(exc)
            != "uq_coverage_resolution_receipts_tenant_resolution"
        ):
            raise
        raise OrderConflict(
            "coverage_resolution_identity_conflict",
            "The coverage resolution reference already satisfies another "
            "obligation.",
        ) from exc
    receipt_id = UUID(str(outcome.result["receipt_id"]))
    receipt = db.execute(
        select(CoverageResolutionReceipt).where(
            CoverageResolutionReceipt.tenant_id == scope.tenant_id,
            CoverageResolutionReceipt.id == receipt_id,
        )
    ).scalar_one()
    return OrderCommandResult(
        order=get_order_snapshot(db, scope=scope, order_id=command.order_id),
        replayed=outcome.replayed,
        fulfillment_requests=_fulfillment_contracts(
            db, scope=scope, order_id=command.order_id
        ),
        coverage_resolution=_coverage_contract(
            receipt,
            order_id=command.order_id,
        ),
    )


def acknowledge_fulfillment(
    db: Session,
    *,
    scope: TenantScope,
    command: AcknowledgeFulfillmentCommand,
) -> OrderCommandResult:
    accepted_at = _aware(command.accepted_at, field="accepted_at")
    acceptance_ref = _required(command.acceptance_ref, field="acceptance_ref")
    payload = {
        "request_id": str(command.request_id),
        "acceptance_ref": acceptance_ref,
        "accepted_at": accepted_at.isoformat(),
    }

    def operation(session: Session) -> Mapping[str, object]:
        order_id = session.scalar(
            select(FulfillmentRequest.order_id).where(
                FulfillmentRequest.tenant_id == scope.tenant_id,
                FulfillmentRequest.id == command.request_id,
            )
        )
        if order_id is None:
            raise OrderNotFound(
                "fulfillment_request_not_found",
                "No fulfillment request exists in the required tenant scope.",
            )
        # Every Orders writer locks aggregate first, then a child. Besides
        # serializing the official event sequence, this keeps acknowledgement
        # and cancellation out of an order↔request deadlock.
        _load_order(session, scope=scope, order_id=order_id, lock=True)
        request = session.execute(
            select(FulfillmentRequest)
            .where(
                FulfillmentRequest.tenant_id == scope.tenant_id,
                FulfillmentRequest.id == command.request_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if request is None:
            raise OrderNotFound(
                "fulfillment_request_not_found",
                "No fulfillment request exists in the required tenant scope.",
            )
        if request.state == "cancelled":
            raise OrderConflict(
                "fulfillment_request_cancelled",
                "A cancelled request cannot be accepted.",
            )
        if request.acceptance_ref is not None:
            if (
                request.acceptance_ref != acceptance_ref
                or request.accepted_at is None
                or request.accepted_at.astimezone(UTC) != accepted_at
            ):
                raise OrderConflict(
                    "fulfillment_acceptance_conflict",
                    "The request already has different acceptance evidence.",
                )
            return {"order_id": str(request.order_id)}
        request.state = "accepted"
        request.acceptance_ref = acceptance_ref
        request.accepted_at = accepted_at
        _event(
            session,
            scope=scope,
            order_id=request.order_id,
            event_ref=f"fulfillment-ack:{command.idempotency_key}",
            event_type="orders.fulfillment_accepted.v1",
            actor=ActorRef(actor_type="system", actor_id="fulfillment-observer"),
            occurred_at=accepted_at,
            from_state="pending",
            to_state="accepted",
            details={
                "request_id": str(request.id),
                "acceptance_ref": acceptance_ref,
            },
        )
        _audit(
            session,
            scope=scope,
            actor=ActorRef(actor_type="system", actor_id="fulfillment-observer"),
            action="orders.fulfillment_acknowledged",
            order_id=request.order_id,
            occurred_at=accepted_at,
            is_success=True,
            details={
                "request_id": str(request.id),
                "acceptance_ref": acceptance_ref,
            },
        )
        session.flush()
        return {"order_id": str(request.order_id)}

    from dotmac_kernel.idempotency import execute_once

    outcome = execute_once(
        db,
        tenant_id=scope.tenant_id,
        scope=FULFILLMENT_ACK_SCOPE,
        key=command.idempotency_key,
        fingerprint=_fingerprint(payload),
        operation=operation,
        correlation_id=command.correlation_id,
    )
    order_id = UUID(str(outcome.result["order_id"]))
    return OrderCommandResult(
        order=get_order_snapshot(db, scope=scope, order_id=order_id),
        replayed=outcome.replayed,
        fulfillment_requests=_fulfillment_contracts(
            db, scope=scope, order_id=order_id
        ),
    )


def cancel_order(
    db: Session,
    *,
    scope: TenantScope,
    command: CancelOrderCommand,
    states: OrderStateRegistry | None = None,
) -> OrderCommandResult:
    states = states or default_order_state_registry()
    target = states.require(command.target_state)
    if target.phase is not OrderPhase.TERMINAL:
        raise OrderError(
            "invalid_cancellation_target",
            "Cancelling an order requires a terminal-phase target state.",
        )
    cancelled_at = _aware(command.cancelled_at, field="cancelled_at")
    reason = _required(command.reason, field="reason")
    cancelled_actor = _actor_values(command.cancelled_by)
    payload = {
        "order_id": str(command.order_id),
        "target_state": command.target_state,
        "reason": reason,
        "cancelled_at": cancelled_at.isoformat(),
        "cancelled_by": _actor_payload(cancelled_actor),
    }

    def operation(session: Session) -> Mapping[str, object]:
        order = _load_order(session, scope=scope, order_id=command.order_id, lock=True)
        accepted_request = session.execute(
            select(FulfillmentRequest.id).where(
                FulfillmentRequest.tenant_id == scope.tenant_id,
                FulfillmentRequest.order_id == order.id,
                FulfillmentRequest.accepted_at.is_not(None),
            )
        ).first()
        if accepted_request is not None:
            refusal_code = "cancellation_refused_after_fulfillment_acceptance"
            _event(
                session,
                scope=scope,
                order_id=order.id,
                event_ref=f"cancel-refused:{command.idempotency_key}",
                event_type="orders.cancellation_refused.v1",
                actor=command.cancelled_by,
                occurred_at=cancelled_at,
                from_state=order.state,
                to_state=order.state,
                details={"reason": reason, "refusal_code": refusal_code},
            )
            _audit(
                session,
                scope=scope,
                actor=command.cancelled_by,
                action="orders.cancellation_refused",
                order_id=order.id,
                occurred_at=cancelled_at,
                is_success=False,
                details={"reason": reason, "refusal_code": refusal_code},
            )
            session.flush()
            return {
                "order_id": str(order.id),
                "refused": True,
                "refusal_code": refusal_code,
            }
        previous = order.state
        order.state = states.transition(previous, command.target_state)
        actor_type, actor_id, actor_label = cancelled_actor
        order.cancelled_actor_type = actor_type
        order.cancelled_actor_id = actor_id
        order.cancelled_actor_label = actor_label
        order.cancelled_at = cancelled_at
        order.cancellation_reason = reason
        for request in session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.tenant_id == scope.tenant_id,
                FulfillmentRequest.order_id == order.id,
            )
        ).scalars():
            request.state = "cancelled"
        _event(
            session,
            scope=scope,
            order_id=order.id,
            event_ref=f"cancel:{command.idempotency_key}",
            event_type=ORDER_CANCELLED_EVENT,
            actor=command.cancelled_by,
            occurred_at=cancelled_at,
            from_state=previous,
            to_state=order.state,
            details={"reason": reason},
        )
        enqueue_event(
            session,
            tenant_id=scope.tenant_id,
            event_type=ORDER_CANCELLED_EVENT,
            payload={
                "contract_version": 1,
                "order_id": str(order.id),
                "order_reference": order.order_reference,
                "reason": reason,
            },
            correlation_id=command.correlation_id,
        )
        _audit(
            session,
            scope=scope,
            actor=command.cancelled_by,
            action="orders.cancelled",
            order_id=order.id,
            occurred_at=cancelled_at,
            is_success=True,
            details={"reason": reason},
        )
        session.flush()
        return {"order_id": str(order.id), "refused": False}

    from dotmac_kernel.idempotency import execute_once

    outcome = execute_once(
        db,
        tenant_id=scope.tenant_id,
        scope=CANCEL_SCOPE,
        key=command.idempotency_key,
        fingerprint=_fingerprint(payload),
        operation=operation,
        correlation_id=command.correlation_id,
    )
    refused = bool(outcome.result.get("refused", False))
    refusal_code_value = outcome.result.get("refusal_code")
    refusal_code = (
        str(refusal_code_value) if refusal_code_value is not None else None
    )
    return OrderCommandResult(
        order=get_order_snapshot(db, scope=scope, order_id=command.order_id),
        replayed=outcome.replayed,
        fulfillment_requests=_fulfillment_contracts(
            db, scope=scope, order_id=command.order_id
        ),
        refused=refused,
        refusal_code=refusal_code,
    )


def reconcile_fulfillment_publications(
    db: Session,
    *,
    scope: TenantScope,
    order_id: UUID,
    observed_at: datetime,
    correlation_id: str | None = None,
    states: OrderStateRegistry | None = None,
) -> ReconciliationReport:
    """Repair missing/dead publication from authoritative frozen snapshots."""

    states = states or default_order_state_registry()
    observed_at = _aware(observed_at, field="observed_at")
    order = _load_order(db, scope=scope, order_id=order_id, lock=True)
    gate = db.execute(
        select(CoverageGate)
        .where(
            CoverageGate.tenant_id == scope.tenant_id,
            CoverageGate.order_id == order.id,
        )
        .with_for_update()
    ).scalar_one()
    before = set(
        db.scalars(
            select(FulfillmentRequest.id).where(
                FulfillmentRequest.tenant_id == scope.tenant_id,
                FulfillmentRequest.order_id == order.id,
            )
        ).all()
    )
    if gate.satisfied_at is not None and states.require(order.state).phase in {
        OrderPhase.ACCEPTED,
        OrderPhase.COVERED,
        OrderPhase.FULFILLMENT,
    }:
        _stage_fulfillment_requests(
            db,
            scope=scope,
            order=order,
            occurred_at=observed_at,
            correlation_id=correlation_id,
            states=states,
        )
    rows = tuple(
        db.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.tenant_id == scope.tenant_id,
                FulfillmentRequest.order_id == order.id,
            )
        ).scalars()
    )
    lines = {line.id: line for line in _load_lines(db, scope=scope, order_id=order.id)}
    restaged: list[UUID] = []
    for request in rows:
        if request.state != "pending":
            continue
        event = None
        if request.last_outbox_event_id is not None:
            event = db.get(OutboxEvent, request.last_outbox_event_id)
        terminal_statuses = {
            OutboxStatus.DEAD.value,
            OutboxStatus.FAILED.value,
        }
        if event is None or event.status in terminal_statuses:
            contract = _request_contract(
                order,
                lines[request.line_snapshot_id],
                request,
            )
            _publish_request(
                db,
                scope=scope,
                request=request,
                contract=contract,
                occurred_at=observed_at,
                correlation_id=correlation_id,
            )
            restaged.append(request.id)
    after = {request.id for request in rows}
    created = tuple(sorted(after - before, key=str))
    restaged_ids = tuple(sorted(restaged, key=str))
    if created or restaged_ids:
        _event(
            db,
            scope=scope,
            order_id=order.id,
            event_ref=f"reconcile:{uuid4()}",
            event_type="orders.fulfillment_reconciled.v1",
            actor=ActorRef(actor_type="system", actor_id="orders-reconciler"),
            occurred_at=observed_at,
            from_state=order.state,
            to_state=order.state,
            details={
                "created_request_ids": [str(value) for value in created],
                "restaged_request_ids": [str(value) for value in restaged_ids],
            },
        )
        _audit(
            db,
            scope=scope,
            actor=ActorRef(actor_type="system", actor_id="orders-reconciler"),
            action="orders.fulfillment_reconciled",
            order_id=order.id,
            occurred_at=observed_at,
            is_success=True,
            details={
                "created_request_ids": [str(value) for value in created],
                "restaged_request_ids": [str(value) for value in restaged_ids],
            },
        )
    db.flush()
    return ReconciliationReport(
        order_id=order.id,
        created_request_ids=created,
        restaged_request_ids=restaged_ids,
    )


__all__ = [
    "ACCEPT_SCOPE",
    "CANCEL_SCOPE",
    "COVERAGE_SCOPE",
    "FULFILLMENT_ACK_SCOPE",
    "FULFILLMENT_REQUESTED_EVENT",
    "SUBMIT_SCOPE",
    "accept_order",
    "acknowledge_fulfillment",
    "cancel_order",
    "get_order_snapshot",
    "get_order_timeline",
    "reconcile_fulfillment_publications",
    "record_coverage_resolution",
    "submit_order",
]
