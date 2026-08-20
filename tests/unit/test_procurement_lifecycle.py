"""Source-parity canaries for procurement and purchase-order decisions.

ERP supplies the broad procurement implementation.  These tests preserve its
positive-line, draft-only editing, guarded transition and purchase-order total
properties while replacing product foreign keys and HTTP exceptions with typed
module contracts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Money, currency
from dotmac_procurement.contracts import (
    ApprovalFact,
    BidLineInput,
    BidScoreInput,
    CompleteEvaluation,
    Conflict,
    ContractError,
    CreatePurchaseOrder,
    CreateRequisition,
    CreateSourcingEvent,
    EvaluationCriterion,
    InvalidTransition,
    ObservationConflict,
    PurchaseLineInput,
    PurchaseOrderStatus,
    ReceiptLineObservation,
    ReceiptObservation,
    RequisitionLineInput,
    RequisitionStatus,
    SnapshotImmutable,
    SourcingLineInput,
    SourcingMethod,
    SourcingWindow,
    SubmitBid,
    digest_document,
    purchase_totals,
    weighted_score,
)
from dotmac_procurement.models import (
    ALL_MODELS,
    BidSubmission,
    ProcurementEvidence,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    SourcingEventLine,
)
from dotmac_procurement.service import (
    apply_award_approval,
    apply_purchase_order_approval,
    apply_requisition_approval,
    cancel_sourcing_event,
    close_purchase_order,
    close_sourcing_event,
    complete_evaluation,
    create_purchase_order,
    create_requisition,
    create_sourcing_event,
    invite_supplier,
    publish_sourcing_event,
    receive_bid,
    record_receipt_observation,
    revise_requisition,
    submit_purchase_order,
    submit_requisition,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

NGN = currency("NGN")
NOW = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)


def test_requisition_line_requires_positive_quantity_and_non_negative_cost() -> None:
    with pytest.raises(ContractError, match="quantity"):
        RequisitionLineInput(
            description="Cable",
            quantity=Decimal("0"),
            unit="m",
            estimated_unit_cost=Money.of("100", NGN),
        )
    with pytest.raises(ContractError, match="cost"):
        RequisitionLineInput(
            description="Cable",
            quantity=Decimal("1"),
            unit="m",
            estimated_unit_cost=Money.of("-1", NGN),
        )


def test_sourcing_window_is_aware_ordered_and_open_at_receipt_time() -> None:
    window = SourcingWindow(opens_at=NOW, closes_at=NOW + timedelta(days=7))
    window.require_open(NOW + timedelta(days=1))
    with pytest.raises(InvalidTransition, match="not open"):
        window.require_open(NOW + timedelta(days=8))
    with pytest.raises(ContractError, match="timezone-aware"):
        SourcingWindow(
            opens_at=datetime(2026, 8, 18),
            closes_at=datetime(2026, 8, 19),
        )


def test_evaluation_criteria_are_dense_and_total_one_hundred_percent() -> None:
    criteria = (
        EvaluationCriterion(code="technical", name="Technical", weight=Decimal("60")),
        EvaluationCriterion(code="price", name="Price", weight=Decimal("40")),
    )
    assert weighted_score(
        criteria,
        {"technical": Decimal("80"), "price": Decimal("100")},
    ) == Decimal("88.0000")
    with pytest.raises(ContractError, match="100"):
        weighted_score(criteria[:1], {"technical": Decimal("80")})


def test_purchase_order_totals_are_derived_from_exact_lines() -> None:
    lines = (
        PurchaseLineInput(
            description="Cable",
            quantity=Decimal("10"),
            unit="m",
            unit_price=Money.of("125.25", NGN),
            tax=Money.of("93.94", NGN),
        ),
        PurchaseLineInput(
            description="Closure",
            quantity=Decimal("2"),
            unit="each",
            unit_price=Money.of("500", NGN),
            tax=Money.zero(NGN),
        ),
    )
    totals = purchase_totals(lines)
    assert totals.subtotal == Money.of("2252.50", NGN)
    assert totals.tax == Money.of("93.94", NGN)
    assert totals.total == Money.of("2346.44", NGN)


def test_receipt_batch_is_validated_before_any_line_is_mutated() -> None:
    tenant_id = uuid4()
    engine = create_engine(
        "sqlite://",
        execution_options={
            "schema_translate_map": {"public": None, "mod_procurement": None}
        },
    )
    Base.metadata.create_all(
        engine,
        tables=[Tenant.__table__, *(model.__table__ for model in ALL_MODELS)],
    )
    with Session(engine) as db:
        db.add(Tenant(id=tenant_id, slug="receipt-atomic", name="Receipt Atomic"))
        requisition = PurchaseRequisition(
            tenant_id=tenant_id,
            requisition_number="REQ-ATOMIC",
            requested_on=date(2026, 8, 19),
            requester_ref="party:requester",
            created_by_ref="party:buyer",
            urgency="normal",
            currency_code="NGN",
            total_estimated_amount=Decimal("30"),
            status=RequisitionStatus.APPROVED,
        )
        db.add(requisition)
        db.flush()
        order = PurchaseOrder(
            tenant_id=tenant_id,
            order_number="PO-ATOMIC",
            supplier_ref="supplier:alpha",
            ordered_on=date(2026, 8, 19),
            currency_code="NGN",
            subtotal=Decimal("30"),
            tax_amount=Decimal("0"),
            total_amount=Decimal("30"),
            status=PurchaseOrderStatus.APPROVED,
            source_requisition_id=requisition.id,
            created_by_ref="party:buyer",
        )
        db.add(order)
        db.flush()
        first = PurchaseOrderLine(
            tenant_id=tenant_id,
            order_id=order.id,
            line_number=1,
            description="Cable",
            quantity_ordered=Decimal("2"),
            quantity_received=Decimal("0"),
            unit="m",
            unit_price=Decimal("10"),
            line_amount=Decimal("20"),
            tax_amount=Decimal("0"),
        )
        second = PurchaseOrderLine(
            tenant_id=tenant_id,
            order_id=order.id,
            line_number=2,
            description="Closure",
            quantity_ordered=Decimal("1"),
            quantity_received=Decimal("0"),
            unit="each",
            unit_price=Decimal("10"),
            line_amount=Decimal("10"),
            tax_amount=Decimal("0"),
        )
        db.add_all((first, second))
        db.flush()

        with pytest.raises(ContractError, match="exceeds"):
            record_receipt_observation(
                db,
                scope=TenantScope(tenant_id),
                order_id=order.id,
                observation=ReceiptObservation(
                    source_owner="inventory",
                    source_event_id="receipt:atomic",
                    observed_at=NOW,
                    lines=(
                        ReceiptLineObservation(
                            order_line_id=first.id,
                            quantity_received=Decimal("1"),
                        ),
                        ReceiptLineObservation(
                            order_line_id=second.id,
                            quantity_received=Decimal("2"),
                        ),
                    ),
                ),
            )
        assert first.quantity_received == Decimal("0")
        assert second.quantity_received == Decimal("0")
    engine.dispose()


def test_mixed_currency_lines_are_refused() -> None:
    with pytest.raises(ContractError, match="currency"):
        purchase_totals(
            (
                PurchaseLineInput(
                    description="One",
                    quantity=Decimal("1"),
                    unit="each",
                    unit_price=Money.of("1", NGN),
                    tax=Money.zero(NGN),
                ),
                PurchaseLineInput(
                    description="Two",
                    quantity=Decimal("1"),
                    unit="each",
                    unit_price=Money.of("1", currency("USD")),
                    tax=Money.zero(currency("USD")),
                ),
            )
        )


def test_approval_fact_binds_the_exact_subject_and_content() -> None:
    subject_id = uuid4()
    content = digest_document({"subject": str(subject_id), "version": 2})
    fact = ApprovalFact(
        decision_ref="approval:123",
        subject_type="purchase_order",
        subject_id=subject_id,
        content_sha256=content,
        approved_at=NOW,
    )
    fact.require_matches(
        subject_type="purchase_order", subject_id=subject_id, content_sha256=content
    )
    with pytest.raises(ContractError, match="content"):
        fact.require_matches(
            subject_type="purchase_order",
            subject_id=subject_id,
            content_sha256="f" * 64,
        )


def test_bid_line_requires_a_sourcing_line_and_exact_positive_values() -> None:
    line = BidLineInput(
        sourcing_line_id=uuid4(),
        description="Cable",
        quantity=Decimal("10"),
        unit_price=Money.of("125.25", NGN),
        promised_delivery_date=date(2026, 9, 1),
    )
    assert line.line_total == Money.of("1252.50", NGN)
    assert SourcingMethod.OPEN_COMPETITIVE.value == "open_competitive"


def test_full_buyer_decision_lifecycle_preserves_owner_boundaries() -> None:
    tenant_id = uuid4()
    engine = create_engine(
        "sqlite://",
        execution_options={
            "schema_translate_map": {"public": None, "mod_procurement": None}
        },
    )
    Base.metadata.create_all(
        engine,
        tables=[Tenant.__table__, *(model.__table__ for model in ALL_MODELS)],
    )
    scope = TenantScope(tenant_id)
    requisition_command = CreateRequisition(
        requisition_number="REQ-100",
        requested_on=date(2026, 8, 19),
        requester_ref="party:requester",
        created_by_ref="party:buyer",
        currency_code="NGN",
        lines=(
            RequisitionLineInput(
                description="Fibre cable",
                quantity=Decimal("10"),
                unit="m",
                estimated_unit_cost=Money.of("100", NGN),
                item_ref="catalogue:fibre",
            ),
        ),
    )

    with Session(engine) as db:
        db.add(Tenant(id=tenant_id, slug="procurement", name="Procurement"))
        db.flush()
        requisition = create_requisition(
            db,
            scope=scope,
            command=requisition_command,
            recorded_at=NOW,
        )
        requisition_line = db.scalar(
            select(PurchaseRequisitionLine).where(
                PurchaseRequisitionLine.requisition_id == requisition.id
            )
        )
        assert requisition_line is not None
        submit_requisition(
            db,
            scope=scope,
            requisition_id=requisition.id,
            submitted_at=NOW + timedelta(minutes=1),
            submitted_by_ref="party:buyer",
        )
        with pytest.raises(SnapshotImmutable):
            revise_requisition(
                db,
                scope=scope,
                requisition_id=requisition.id,
                command=requisition_command,
                revised_at=NOW + timedelta(minutes=2),
            )
        assert requisition.content_sha256 is not None
        apply_requisition_approval(
            db,
            scope=scope,
            requisition_id=requisition.id,
            fact=ApprovalFact(
                decision_ref="approval:requisition:100",
                subject_type="procurement.requisition",
                subject_id=requisition.id,
                content_sha256=requisition.content_sha256,
                approved_at=NOW + timedelta(minutes=3),
            ),
        )

        with pytest.raises(ContractError, match="unit differs"):
            create_sourcing_event(
                db,
                scope=scope,
                command=CreateSourcingEvent(
                    event_number="RFQ-RESHAPED",
                    title="Changed requisition sourcing",
                    method=SourcingMethod.SELECTIVE,
                    window=SourcingWindow(
                        opens_at=NOW + timedelta(minutes=10),
                        closes_at=NOW + timedelta(minutes=30),
                    ),
                    currency_code="NGN",
                    criteria=(
                        EvaluationCriterion(
                            code="commercial",
                            name="Commercial",
                            weight=Decimal("100"),
                        ),
                    ),
                    lines=(
                        SourcingLineInput(
                            description="Fibre cable",
                            quantity=Decimal("10"),
                            unit="each",
                            source_requisition_line_id=requisition_line.id,
                            item_ref="catalogue:fibre",
                            target_unit_cost=Money.of("100", NGN),
                        ),
                    ),
                    created_by_ref="party:buyer",
                    source_requisition_id=requisition.id,
                ),
                recorded_at=NOW + timedelta(minutes=4),
            )

        cancelled_sourcing = create_sourcing_event(
            db,
            scope=scope,
            command=CreateSourcingEvent(
                event_number="RFQ-CANCELLED",
                title="Abandoned fibre cable sourcing",
                method=SourcingMethod.SELECTIVE,
                window=SourcingWindow(
                    opens_at=NOW + timedelta(minutes=10),
                    closes_at=NOW + timedelta(minutes=30),
                ),
                currency_code="NGN",
                criteria=(
                    EvaluationCriterion(
                        code="commercial",
                        name="Commercial",
                        weight=Decimal("100"),
                    ),
                ),
                lines=(
                    SourcingLineInput(
                        description="Fibre cable",
                        quantity=Decimal("10"),
                        unit="m",
                        source_requisition_line_id=requisition_line.id,
                        item_ref="catalogue:fibre",
                        target_unit_cost=Money.of("100", NGN),
                    ),
                ),
                created_by_ref="party:buyer",
                source_requisition_id=requisition.id,
            ),
            recorded_at=NOW + timedelta(minutes=4),
        )
        invite_supplier(
            db,
            scope=scope,
            event_id=cancelled_sourcing.id,
            supplier_ref="supplier:alpha",
            invited_at=NOW + timedelta(minutes=5),
            invited_by_ref="party:buyer",
        )
        publish_sourcing_event(
            db,
            scope=scope,
            event_id=cancelled_sourcing.id,
            published_at=NOW + timedelta(minutes=6),
            published_by_ref="party:buyer",
        )
        cancel_sourcing_event(
            db,
            scope=scope,
            event_id=cancelled_sourcing.id,
            cancelled_at=NOW + timedelta(minutes=7),
            cancelled_by_ref="party:buyer",
            reason="Scope changed before any bid was received",
        )
        assert requisition.status == RequisitionStatus.APPROVED

        sourcing = create_sourcing_event(
            db,
            scope=scope,
            command=CreateSourcingEvent(
                event_number="RFQ-100",
                title="Fibre cable sourcing",
                method=SourcingMethod.SELECTIVE,
                window=SourcingWindow(
                    opens_at=NOW + timedelta(hours=1),
                    closes_at=NOW + timedelta(days=1),
                ),
                currency_code="NGN",
                criteria=(
                    EvaluationCriterion(
                        code="commercial",
                        name="Commercial",
                        weight=Decimal("100"),
                    ),
                ),
                lines=(
                    SourcingLineInput(
                        description="Fibre cable",
                        quantity=Decimal("10"),
                        unit="m",
                        source_requisition_line_id=requisition_line.id,
                        item_ref="catalogue:fibre",
                        target_unit_cost=Money.of("100", NGN),
                    ),
                ),
                created_by_ref="party:buyer",
                source_requisition_id=requisition.id,
            ),
            recorded_at=NOW + timedelta(minutes=4),
        )
        invite_supplier(
            db,
            scope=scope,
            event_id=sourcing.id,
            supplier_ref="supplier:alpha",
            invited_at=NOW + timedelta(minutes=5),
            invited_by_ref="party:buyer",
        )
        publish_sourcing_event(
            db,
            scope=scope,
            event_id=sourcing.id,
            published_at=NOW + timedelta(minutes=6),
            published_by_ref="party:buyer",
        )
        sourcing_line = db.scalar(
            select(SourcingEventLine).where(SourcingEventLine.event_id == sourcing.id)
        )
        assert sourcing_line is not None
        bid = receive_bid(
            db,
            scope=scope,
            event_id=sourcing.id,
            command=SubmitBid(
                response_number="BID-100",
                supplier_ref="supplier:alpha",
                received_at=NOW + timedelta(hours=2),
                currency_code="NGN",
                lines=(
                    BidLineInput(
                        sourcing_line_id=sourcing_line.id,
                        description="Fibre cable",
                        quantity=Decimal("10"),
                        unit_price=Money.of("100", NGN),
                    ),
                ),
                source_owner="supplier_portal",
                source_event_id="submission:100",
            ),
        )
        close_sourcing_event(
            db,
            scope=scope,
            event_id=sourcing.id,
            closed_at=NOW + timedelta(days=2),
            closed_by_ref="party:buyer",
        )
        evaluation = complete_evaluation(
            db,
            scope=scope,
            event_id=sourcing.id,
            command=CompleteEvaluation(
                selected_bid_id=bid.id,
                bid_scores=(
                    BidScoreInput(
                        bid_id=bid.id,
                        scores={"commercial": Decimal("95")},
                    ),
                ),
                evaluated_by_ref="party:evaluator",
                evaluated_at=NOW + timedelta(days=2, minutes=1),
            ),
        )
        award = apply_award_approval(
            db,
            scope=scope,
            evaluation_id=evaluation.id,
            fact=ApprovalFact(
                decision_ref="approval:award:100",
                subject_type="procurement.sourcing_award",
                subject_id=evaluation.id,
                content_sha256=evaluation.content_sha256,
                approved_at=NOW + timedelta(days=2, minutes=2),
            ),
        )
        assert award.bid_id == bid.id
        assert award.supplier_ref == "supplier:alpha"

        with pytest.raises(ContractError, match="unit differs"):
            create_purchase_order(
                db,
                scope=scope,
                command=CreatePurchaseOrder(
                    order_number="PO-RESHAPED",
                    supplier_ref=award.supplier_ref,
                    ordered_on=date(2026, 8, 21),
                    currency_code="NGN",
                    lines=(
                        PurchaseLineInput(
                            description="Fibre cable",
                            quantity=Decimal("10"),
                            unit="each",
                            unit_price=Money.of("100", NGN),
                            tax=Money.zero(NGN),
                            item_ref="catalogue:fibre",
                        ),
                    ),
                    created_by_ref="party:buyer",
                    source_requisition_id=requisition.id,
                    source_evaluation_id=evaluation.id,
                ),
                recorded_at=NOW + timedelta(days=2, minutes=3),
            )

        purchase_order = create_purchase_order(
            db,
            scope=scope,
            command=CreatePurchaseOrder(
                order_number="PO-100",
                supplier_ref=award.supplier_ref,
                ordered_on=date(2026, 8, 21),
                currency_code="NGN",
                lines=(
                    PurchaseLineInput(
                        description="Fibre cable",
                        quantity=Decimal("10"),
                        unit="m",
                        unit_price=Money.of("100", NGN),
                        tax=Money.zero(NGN),
                        item_ref="catalogue:fibre",
                    ),
                ),
                created_by_ref="party:buyer",
                source_requisition_id=requisition.id,
                source_evaluation_id=evaluation.id,
            ),
            recorded_at=NOW + timedelta(days=2, minutes=3),
        )
        with pytest.raises(Conflict, match="source"):
            create_purchase_order(
                db,
                scope=scope,
                command=CreatePurchaseOrder(
                    order_number="PO-DUPLICATE-AWARD",
                    supplier_ref=award.supplier_ref,
                    ordered_on=date(2026, 8, 21),
                    currency_code="NGN",
                    lines=(
                        PurchaseLineInput(
                            description="Fibre cable",
                            quantity=Decimal("10"),
                            unit="m",
                            unit_price=Money.of("100", NGN),
                            tax=Money.zero(NGN),
                            item_ref="catalogue:fibre",
                        ),
                    ),
                    created_by_ref="party:buyer",
                    source_requisition_id=requisition.id,
                    source_evaluation_id=evaluation.id,
                ),
                recorded_at=NOW + timedelta(days=2, minutes=4),
            )
        submit_purchase_order(
            db,
            scope=scope,
            order_id=purchase_order.id,
            submitted_at=NOW + timedelta(days=2, minutes=4),
            submitted_by_ref="party:buyer",
        )
        assert purchase_order.content_sha256 is not None
        approved_purchase = apply_purchase_order_approval(
            db,
            scope=scope,
            order_id=purchase_order.id,
            fact=ApprovalFact(
                decision_ref="approval:purchase:100",
                subject_type="procurement.purchase_order",
                subject_id=purchase_order.id,
                content_sha256=purchase_order.content_sha256,
                approved_at=NOW + timedelta(days=2, minutes=5),
            ),
        )
        assert approved_purchase.total == Money.of("1000", NGN)
        order_line = db.scalar(
            select(PurchaseOrderLine).where(
                PurchaseOrderLine.order_id == purchase_order.id
            )
        )
        assert order_line is not None
        partial = ReceiptObservation(
            source_owner="inventory",
            source_event_id="receipt:1",
            observed_at=NOW + timedelta(days=3),
            lines=(
                ReceiptLineObservation(
                    order_line_id=order_line.id,
                    quantity_received=Decimal("4"),
                ),
            ),
        )
        record_receipt_observation(
            db, scope=scope, order_id=purchase_order.id, observation=partial
        )
        assert purchase_order.status == PurchaseOrderStatus.PARTIALLY_RECEIVED
        with pytest.raises(ContractError, match="exceeds"):
            record_receipt_observation(
                db,
                scope=scope,
                order_id=purchase_order.id,
                observation=ReceiptObservation(
                    source_owner="inventory",
                    source_event_id="receipt:too-much",
                    observed_at=NOW + timedelta(days=3, minutes=1),
                    lines=(
                        ReceiptLineObservation(
                            order_line_id=order_line.id,
                            quantity_received=Decimal("7"),
                        ),
                    ),
                ),
            )
        assert order_line.quantity_received == Decimal("4")
        final = ReceiptObservation(
            source_owner="inventory",
            source_event_id="receipt:2",
            observed_at=NOW + timedelta(days=3, minutes=2),
            lines=(
                ReceiptLineObservation(
                    order_line_id=order_line.id,
                    quantity_received=Decimal("6"),
                ),
            ),
        )
        record_receipt_observation(
            db, scope=scope, order_id=purchase_order.id, observation=final
        )
        assert purchase_order.status == PurchaseOrderStatus.RECEIVED
        close_purchase_order(
            db,
            scope=scope,
            order_id=purchase_order.id,
            closed_at=NOW + timedelta(days=4),
            actor_ref="party:buyer",
        )
        replay = record_receipt_observation(
            db, scope=scope, order_id=purchase_order.id, observation=final
        )
        assert replay.status == PurchaseOrderStatus.CLOSED
        with pytest.raises(ObservationConflict):
            record_receipt_observation(
                db,
                scope=scope,
                order_id=purchase_order.id,
                observation=ReceiptObservation(
                    source_owner="inventory",
                    source_event_id="receipt:2",
                    observed_at=final.observed_at,
                    lines=(
                        ReceiptLineObservation(
                            order_line_id=order_line.id,
                            quantity_received=Decimal("5"),
                        ),
                    ),
                ),
            )
        assert db.scalar(select(func.count()).select_from(BidSubmission)) == 1
        assert db.scalar(select(func.count()).select_from(ProcurementEvidence)) >= 12

    engine.dispose()
