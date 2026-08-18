"""Typed commands and immutable cross-owner facts for Orders version one."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.money import Money


@dataclass(frozen=True, slots=True)
class ActorRef:
    actor_type: str
    actor_id: str | None = None
    actor_label: str | None = None


@dataclass(frozen=True, slots=True)
class TermValueV1:
    """One canonical string value in a captured commercial-terms snapshot."""

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class TermsSnapshotV1:
    """Closed, provider-neutral terms content bound to an immutable version."""

    version_ref: str
    values: tuple[TermValueV1, ...]


@dataclass(frozen=True, slots=True)
class TaxSnapshotV1:
    """One captured tax input and its exact resulting amount."""

    tax_code: str
    source_version: str
    taxable_basis: Money
    rate: Decimal | None
    amount: Money


@dataclass(frozen=True, slots=True)
class LineInput:
    line_key: str
    description: str
    quantity: Decimal
    unit_price: Money
    discount: Money
    taxes: tuple[TaxSnapshotV1, ...]
    price_version_ref: str
    terms_ref: str
    terms_snapshot: TermsSnapshotV1
    specification_ref: str
    source_ref: str | None = None
    source_version: str | None = None


@dataclass(frozen=True, slots=True)
class LineSnapshot:
    line_key: str
    description: str
    quantity: Decimal
    unit_price: Money
    extended_price: Money
    discount: Money
    tax: Money
    taxes: tuple[TaxSnapshotV1, ...]
    total: Money
    price_version_ref: str
    terms_ref: str
    terms_snapshot: TermsSnapshotV1
    specification_ref: str
    source_ref: str | None
    source_version: str | None
    fingerprint: str


@dataclass(frozen=True, slots=True)
class OrderTotals:
    subtotal: Money
    discount: Money
    tax: Money
    total: Money


@dataclass(frozen=True, slots=True)
class FxSnapshotV1:
    """Exact, sourced FX evidence captured with a cross-currency order."""

    base_currency_code: str
    quote_currency_code: str
    rate: Decimal
    rate_ref: str
    source: str
    as_of: datetime


@dataclass(frozen=True, slots=True)
class SubmitOrderCommand:
    idempotency_key: str
    order_reference: str
    customer_ref: str
    currency_code: str
    currency_minor_units: int
    lines: tuple[LineInput, ...]
    coverage_obligation_refs: tuple[str, ...]
    submitted_by: ActorRef
    submitted_at: datetime
    source_ref: str | None = None
    source_version: str | None = None
    fx_snapshot: FxSnapshotV1 | None = None
    initial_state: str = "submitted"
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptOrderCommand:
    idempotency_key: str
    order_id: UUID
    accepted_by: ActorRef
    accepted_at: datetime
    target_state: str = "accepted"
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class CancelOrderCommand:
    idempotency_key: str
    order_id: UUID
    cancelled_by: ActorRef
    cancelled_at: datetime
    reason: str
    target_state: str = "cancelled"
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecordCoverageResolutionCommand:
    idempotency_key: str
    order_id: UUID
    obligation_ref: str
    resolution_ref: str
    resolution_kind: str
    resolved_at: datetime
    source_ref: str
    source_version: str
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AcknowledgeFulfillmentCommand:
    idempotency_key: str
    request_id: UUID
    acceptance_ref: str
    accepted_at: datetime
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class OrderLineSnapshotV1:
    line_id: UUID
    line_key: str
    description: str
    quantity: Decimal
    unit_price: Money
    extended_price: Money
    discount: Money
    tax: Money
    taxes: tuple[TaxSnapshotV1, ...]
    total: Money
    price_version_ref: str
    terms_ref: str
    terms_snapshot: TermsSnapshotV1
    specification_ref: str
    snapshot_fingerprint: str
    source_ref: str | None = None
    source_version: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageResolutionV1:
    order_id: UUID
    obligation_ref: str
    resolution_ref: str
    resolution_kind: str
    source_ref: str
    source_version: str
    resolved_at: datetime


@dataclass(frozen=True, slots=True)
class CoverageSnapshotV1:
    """Finite coverage membership and the immutable observations received."""

    state: str
    obligation_refs: tuple[str, ...]
    resolutions: tuple[CoverageResolutionV1, ...]
    satisfied_at: datetime | None


@dataclass(frozen=True, slots=True)
class OrderSnapshotV1:
    order_id: UUID
    order_reference: str
    customer_ref: str
    state: str
    totals: OrderTotals
    lines: tuple[OrderLineSnapshotV1, ...]
    snapshot_fingerprint: str
    source_ref: str | None
    source_version: str | None
    submitted_by: ActorRef
    submitted_at: datetime
    accepted_by: ActorRef | None
    accepted_at: datetime | None
    covered_at: datetime | None
    cancelled_by: ActorRef | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    coverage: CoverageSnapshotV1
    fx_snapshot: FxSnapshotV1 | None = None


@dataclass(frozen=True, slots=True)
class OrderEventV1:
    event_id: UUID
    sequence: int
    event_ref: str
    event_type: str
    from_state: str | None
    to_state: str | None
    actor: ActorRef
    occurred_at: datetime
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class FulfillmentRequestV1:
    request_id: UUID
    order_id: UUID
    order_reference: str
    customer_ref: str
    line: OrderLineSnapshotV1
    request_fingerprint: str
    state: str
    publication_count: int
    acceptance_ref: str | None
    accepted_at: datetime | None


@dataclass(frozen=True, slots=True)
class OrderCommandResult:
    order: OrderSnapshotV1
    replayed: bool
    fulfillment_requests: tuple[FulfillmentRequestV1, ...] = field(
        default_factory=tuple
    )
    coverage_resolution: CoverageResolutionV1 | None = None
    refused: bool = False
    refusal_code: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    order_id: UUID
    created_request_ids: tuple[UUID, ...]
    restaged_request_ids: tuple[UUID, ...]


__all__ = [
    "AcceptOrderCommand",
    "AcknowledgeFulfillmentCommand",
    "ActorRef",
    "CancelOrderCommand",
    "CoverageResolutionV1",
    "CoverageSnapshotV1",
    "FulfillmentRequestV1",
    "FxSnapshotV1",
    "LineInput",
    "LineSnapshot",
    "OrderCommandResult",
    "OrderEventV1",
    "OrderLineSnapshotV1",
    "OrderSnapshotV1",
    "OrderTotals",
    "ReconciliationReport",
    "RecordCoverageResolutionCommand",
    "SubmitOrderCommand",
    "TaxSnapshotV1",
    "TermValueV1",
    "TermsSnapshotV1",
]
