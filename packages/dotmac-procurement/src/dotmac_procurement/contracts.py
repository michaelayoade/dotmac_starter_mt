"""Typed procurement commands, values and owner facts.

No product or sibling-module type crosses this surface.  Assemblies translate
their Party, requester, supplier, budget, approval, item, project and receipt
facts into bounded opaque references before calling the owner.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from dotmac_kernel.money import CurrencyMismatchError, Money, currency


class ProcurementError(Exception):
    """Base for every typed procurement refusal."""


class ContractError(ProcurementError, ValueError):
    """A public command or fact is malformed or does not bind its subject."""


class NotFound(ProcurementError):
    """The requested tenant-owned record does not exist."""


class Conflict(ProcurementError):
    """A stable number, source identity or observation conflicts."""


class InvalidTransition(ProcurementError):
    """The requested transition is not legal from the current state."""


class SnapshotImmutable(ProcurementError):
    """A submitted or published purchasing snapshot cannot be edited."""


class ObservationConflict(ProcurementError):
    """An observation identity was replayed with different content."""


class RequisitionStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    BUDGET_VERIFIED = "budget_verified"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    SOURCED = "sourced"


class SourcingMethod(StrEnum):
    DIRECT = "direct"
    SELECTIVE = "selective"
    OPEN_COMPETITIVE = "open_competitive"


class SourcingStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CLOSED = "closed"
    EVALUATED = "evaluated"
    AWARDED = "awarded"
    CANCELLED = "cancelled"


class BidStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_EVALUATION = "under_evaluation"
    SELECTED = "selected"
    REJECTED = "rejected"


class EvaluationStatus(StrEnum):
    COMPLETED = "completed"
    APPROVED = "approved"


class PurchaseOrderStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CANCELLED = "cancelled"
    CLOSED = "closed"


def _required(name: str, value: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ContractError(f"{name} is required and must be at most {limit} chars")
    return normalized


def _optional(name: str, value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return _required(name, value, limit)


def _positive(name: str, value: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ContractError(f"{name} must be a finite positive decimal")
    return value


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{name} must be timezone-aware")


def _currency_code(value: str) -> str:
    try:
        return currency(value).code
    except Exception as exc:
        raise ContractError("currency_code must be an ISO-4217 code") from exc


def _digest(value: str, *, field: str = "content_sha256") -> str:
    normalized = value.removeprefix("sha256:").lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ContractError(f"{field} must be a SHA-256 hex digest")
    return normalized


def digest_document(document: object) -> str:
    """Canonical SHA-256 over a JSON-compatible owner document."""
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalFact:
    """An approval-owner result bound to one exact procurement snapshot."""

    decision_ref: str
    subject_type: str
    subject_id: UUID
    content_sha256: str
    approved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_ref", _required("decision_ref", self.decision_ref, 255)
        )
        object.__setattr__(
            self, "subject_type", _required("subject_type", self.subject_type, 80)
        )
        object.__setattr__(self, "content_sha256", _digest(self.content_sha256))
        _aware("approved_at", self.approved_at)

    def require_matches(
        self, *, subject_type: str, subject_id: UUID, content_sha256: str
    ) -> None:
        if self.subject_type != subject_type or self.subject_id != subject_id:
            raise ContractError("approval fact names a different subject")
        if self.content_sha256 != _digest(content_sha256):
            raise ContractError("approval fact binds different content")


@dataclass(frozen=True, slots=True)
class RejectionFact:
    """A rejection-owner result bound to one exact procurement snapshot."""

    decision_ref: str
    subject_type: str
    subject_id: UUID
    content_sha256: str
    rejected_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_ref", _required("decision_ref", self.decision_ref, 255)
        )
        object.__setattr__(
            self, "subject_type", _required("subject_type", self.subject_type, 80)
        )
        object.__setattr__(self, "content_sha256", _digest(self.content_sha256))
        object.__setattr__(self, "reason", _optional("reason", self.reason, 4000))
        _aware("rejected_at", self.rejected_at)

    def require_matches(
        self, *, subject_type: str, subject_id: UUID, content_sha256: str
    ) -> None:
        if self.subject_type != subject_type or self.subject_id != subject_id:
            raise ContractError("rejection fact names a different subject")
        if self.content_sha256 != _digest(content_sha256):
            raise ContractError("rejection fact binds different content")


@dataclass(frozen=True, slots=True)
class BudgetAuthorizationFact:
    """A budget-owner authorization; Procurement never recomputes availability."""

    authorization_ref: str
    subject_id: UUID
    content_sha256: str
    authorized_amount: Money
    authorized_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authorization_ref",
            _required("authorization_ref", self.authorization_ref, 255),
        )
        object.__setattr__(self, "content_sha256", _digest(self.content_sha256))
        if self.authorized_amount.is_negative:
            raise ContractError("authorized_amount cannot be negative")
        _aware("authorized_at", self.authorized_at)

    def require_matches(
        self, *, subject_id: UUID, content_sha256: str, required_amount: Money
    ) -> None:
        if self.subject_id != subject_id:
            raise ContractError("budget fact names a different requisition")
        if self.content_sha256 != _digest(content_sha256):
            raise ContractError("budget fact binds different content")
        try:
            enough = self.authorized_amount >= required_amount
        except CurrencyMismatchError as exc:
            raise ContractError("budget fact uses a different currency") from exc
        if not enough:
            raise ContractError("budget fact does not authorize the requisition total")


@dataclass(frozen=True, slots=True)
class SourcingWindow:
    opens_at: datetime
    closes_at: datetime

    def __post_init__(self) -> None:
        _aware("opens_at", self.opens_at)
        _aware("closes_at", self.closes_at)
        if self.closes_at <= self.opens_at:
            raise ContractError("closes_at must be later than opens_at")

    def require_open(self, at: datetime) -> None:
        _aware("receipt time", at)
        if not self.opens_at <= at <= self.closes_at:
            raise InvalidTransition("sourcing event is not open at that time")


@dataclass(frozen=True, slots=True)
class EvaluationCriterion:
    code: str
    name: str
    weight: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required("criterion code", self.code, 80))
        object.__setattr__(self, "name", _required("criterion name", self.name, 160))
        if not self.weight.is_finite() or not Decimal("0") < self.weight <= Decimal(
            "100"
        ):
            raise ContractError(
                "criterion weight must be greater than 0 and at most 100"
            )

    def document(self) -> dict[str, str]:
        return {"code": self.code, "name": self.name, "weight": str(self.weight)}


def weighted_score(
    criteria: tuple[EvaluationCriterion, ...], scores: Mapping[str, Decimal]
) -> Decimal:
    if not criteria:
        raise ContractError("an evaluation requires at least one criterion")
    codes = [criterion.code for criterion in criteria]
    if len(codes) != len(set(codes)):
        raise ContractError("evaluation criterion codes must be unique")
    if sum((criterion.weight for criterion in criteria), Decimal("0")) != Decimal(
        "100"
    ):
        raise ContractError("evaluation criterion weights must total 100")
    if set(scores) != set(codes):
        raise ContractError("scores must cover the evaluation criteria exactly")
    total = Decimal("0")
    for criterion in criteria:
        score = scores[criterion.code]
        if not score.is_finite() or not Decimal("0") <= score <= Decimal("100"):
            raise ContractError("criterion scores must be between 0 and 100")
        total += criterion.weight * score / Decimal("100")
    return total.quantize(Decimal("0.0001"))


@dataclass(frozen=True, slots=True)
class RequisitionLineInput:
    description: str
    quantity: Decimal
    unit: str
    estimated_unit_cost: Money
    item_ref: str | None = None
    expense_ref: str | None = None
    cost_center_ref: str | None = None
    subject_ref: str | None = None
    requested_delivery_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "description", _required("description", self.description, 2000)
        )
        object.__setattr__(self, "quantity", _positive("quantity", self.quantity))
        object.__setattr__(self, "unit", _required("unit", self.unit, 40))
        if self.estimated_unit_cost.is_negative:
            raise ContractError("estimated unit cost cannot be negative")
        for field in ("item_ref", "expense_ref", "cost_center_ref", "subject_ref"):
            object.__setattr__(self, field, _optional(field, getattr(self, field), 255))

    @property
    def estimated_total(self) -> Money:
        return self.estimated_unit_cost.multiply(self.quantity)

    def document(self, position: int) -> dict[str, object]:
        return {
            "position": position,
            "description": self.description,
            "quantity": str(self.quantity),
            "unit": self.unit,
            "estimated_unit_cost": str(self.estimated_unit_cost.amount),
            "currency_code": self.estimated_unit_cost.currency.code,
            "item_ref": self.item_ref,
            "expense_ref": self.expense_ref,
            "cost_center_ref": self.cost_center_ref,
            "subject_ref": self.subject_ref,
            "requested_delivery_date": self.requested_delivery_date,
        }


@dataclass(frozen=True, slots=True)
class CreateRequisition:
    requisition_number: str
    requested_on: date
    requester_ref: str
    created_by_ref: str
    currency_code: str
    lines: tuple[RequisitionLineInput, ...]
    urgency: str = "normal"
    justification: str | None = None
    source_owner: str | None = None
    source_event_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requisition_number",
            _required("requisition_number", self.requisition_number, 80),
        )
        object.__setattr__(
            self, "requester_ref", _required("requester_ref", self.requester_ref, 255)
        )
        object.__setattr__(
            self,
            "created_by_ref",
            _required("created_by_ref", self.created_by_ref, 255),
        )
        object.__setattr__(self, "urgency", _required("urgency", self.urgency, 40))
        object.__setattr__(
            self, "justification", _optional("justification", self.justification, 4000)
        )
        object.__setattr__(self, "currency_code", _currency_code(self.currency_code))
        if not self.lines:
            raise ContractError("a requisition requires at least one line")
        if any(
            line.estimated_unit_cost.currency.code != self.currency_code
            for line in self.lines
        ):
            raise ContractError("every requisition line must use its currency")
        if (self.source_owner is None) != (self.source_event_id is None):
            raise ContractError("source_owner and source_event_id are paired")
        object.__setattr__(
            self, "source_owner", _optional("source_owner", self.source_owner, 120)
        )
        object.__setattr__(
            self,
            "source_event_id",
            _optional("source_event_id", self.source_event_id, 255),
        )

    @property
    def estimated_total(self) -> Money:
        total = Money.zero(currency(self.currency_code))
        for line in self.lines:
            total = total + line.estimated_total
        return total


@dataclass(frozen=True, slots=True)
class SourcingLineInput:
    description: str
    quantity: Decimal
    unit: str
    source_requisition_line_id: UUID | None = None
    item_ref: str | None = None
    target_unit_cost: Money | None = None
    requested_delivery_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "description", _required("description", self.description, 2000)
        )
        object.__setattr__(self, "quantity", _positive("quantity", self.quantity))
        object.__setattr__(self, "unit", _required("unit", self.unit, 40))
        object.__setattr__(self, "item_ref", _optional("item_ref", self.item_ref, 255))
        if self.target_unit_cost is not None and self.target_unit_cost.is_negative:
            raise ContractError("target unit cost cannot be negative")

    def document(self, position: int) -> dict[str, object]:
        return {
            "position": position,
            "description": self.description,
            "quantity": str(self.quantity),
            "unit": self.unit,
            "source_requisition_line_id": self.source_requisition_line_id,
            "item_ref": self.item_ref,
            "target_unit_cost": (
                str(self.target_unit_cost.amount) if self.target_unit_cost else None
            ),
            "currency_code": (
                self.target_unit_cost.currency.code if self.target_unit_cost else None
            ),
            "requested_delivery_date": self.requested_delivery_date,
        }


@dataclass(frozen=True, slots=True)
class CreateSourcingEvent:
    event_number: str
    title: str
    method: SourcingMethod
    window: SourcingWindow
    currency_code: str
    criteria: tuple[EvaluationCriterion, ...]
    lines: tuple[SourcingLineInput, ...]
    created_by_ref: str
    source_requisition_id: UUID | None = None
    terms: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "event_number", _required("event_number", self.event_number, 80)
        )
        object.__setattr__(self, "title", _required("title", self.title, 240))
        object.__setattr__(
            self,
            "created_by_ref",
            _required("created_by_ref", self.created_by_ref, 255),
        )
        object.__setattr__(self, "terms", _optional("terms", self.terms, 10_000))
        object.__setattr__(self, "currency_code", _currency_code(self.currency_code))
        if not self.lines:
            raise ContractError("a sourcing event requires at least one line")
        if not self.criteria:
            raise ContractError("a sourcing event requires evaluation criteria")
        weighted_score(
            self.criteria, {criterion.code: Decimal("0") for criterion in self.criteria}
        )
        if any(
            line.target_unit_cost is not None
            and line.target_unit_cost.currency.code != self.currency_code
            for line in self.lines
        ):
            raise ContractError("target costs must use the sourcing currency")


@dataclass(frozen=True, slots=True)
class BidLineInput:
    sourcing_line_id: UUID
    description: str
    quantity: Decimal
    unit_price: Money
    promised_delivery_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "description", _required("description", self.description, 2000)
        )
        object.__setattr__(self, "quantity", _positive("quantity", self.quantity))
        if self.unit_price.is_negative:
            raise ContractError("unit price cannot be negative")

    @property
    def line_total(self) -> Money:
        return self.unit_price.multiply(self.quantity)

    def document(self, position: int) -> dict[str, object]:
        return {
            "position": position,
            "sourcing_line_id": self.sourcing_line_id,
            "description": self.description,
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price.amount),
            "currency_code": self.unit_price.currency.code,
            "promised_delivery_date": self.promised_delivery_date,
        }


@dataclass(frozen=True, slots=True)
class SubmitBid:
    response_number: str
    supplier_ref: str
    received_at: datetime
    currency_code: str
    lines: tuple[BidLineInput, ...]
    source_owner: str
    source_event_id: str
    validity_days: int | None = None
    delivery_period_days: int | None = None
    technical_proposal: str | None = None
    terms: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "response_number",
            _required("response_number", self.response_number, 80),
        )
        object.__setattr__(
            self, "supplier_ref", _required("supplier_ref", self.supplier_ref, 255)
        )
        object.__setattr__(
            self, "source_owner", _required("source_owner", self.source_owner, 120)
        )
        object.__setattr__(
            self,
            "source_event_id",
            _required("source_event_id", self.source_event_id, 255),
        )
        object.__setattr__(self, "currency_code", _currency_code(self.currency_code))
        object.__setattr__(
            self,
            "technical_proposal",
            _optional("technical_proposal", self.technical_proposal, 20_000),
        )
        object.__setattr__(self, "terms", _optional("terms", self.terms, 10_000))
        _aware("received_at", self.received_at)
        if not self.lines:
            raise ContractError("a bid requires at least one line")
        if any(
            line.unit_price.currency.code != self.currency_code for line in self.lines
        ):
            raise ContractError("every bid line must use the bid currency")
        for name, value in (
            ("validity_days", self.validity_days),
            ("delivery_period_days", self.delivery_period_days),
        ):
            if value is not None and value < 0:
                raise ContractError(f"{name} cannot be negative")

    @property
    def total(self) -> Money:
        total = Money.zero(currency(self.currency_code))
        for line in self.lines:
            total = total + line.line_total
        return total


@dataclass(frozen=True, slots=True)
class BidScoreInput:
    bid_id: UUID
    scores: Mapping[str, Decimal]
    comments: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "comments", _optional("comments", self.comments, 4000))


@dataclass(frozen=True, slots=True)
class CompleteEvaluation:
    selected_bid_id: UUID
    bid_scores: tuple[BidScoreInput, ...]
    evaluated_by_ref: str
    evaluated_at: datetime
    report: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evaluated_by_ref",
            _required("evaluated_by_ref", self.evaluated_by_ref, 255),
        )
        object.__setattr__(self, "report", _optional("report", self.report, 10_000))
        _aware("evaluated_at", self.evaluated_at)
        if not self.bid_scores:
            raise ContractError("an evaluation requires at least one scored bid")
        bid_ids = [entry.bid_id for entry in self.bid_scores]
        if len(bid_ids) != len(set(bid_ids)):
            raise ContractError("each bid may be scored once")
        if self.selected_bid_id not in set(bid_ids):
            raise ContractError("selected bid must be one of the scored bids")


@dataclass(frozen=True, slots=True)
class PurchaseLineInput:
    description: str
    quantity: Decimal
    unit: str
    unit_price: Money
    tax: Money
    item_ref: str | None = None
    expense_ref: str | None = None
    asset_ref: str | None = None
    cost_center_ref: str | None = None
    subject_ref: str | None = None
    expected_delivery_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "description", _required("description", self.description, 2000)
        )
        object.__setattr__(self, "quantity", _positive("quantity", self.quantity))
        object.__setattr__(self, "unit", _required("unit", self.unit, 40))
        if self.unit_price.is_negative:
            raise ContractError("unit price cannot be negative")
        if self.tax.is_negative:
            raise ContractError("tax cannot be negative")
        if self.unit_price.currency != self.tax.currency:
            raise ContractError("unit price and tax must use the same currency")
        for field in (
            "item_ref",
            "expense_ref",
            "asset_ref",
            "cost_center_ref",
            "subject_ref",
        ):
            object.__setattr__(self, field, _optional(field, getattr(self, field), 255))

    @property
    def line_total(self) -> Money:
        return self.unit_price.multiply(self.quantity)

    def document(self, position: int) -> dict[str, object]:
        return {
            "position": position,
            "description": self.description,
            "quantity": str(self.quantity),
            "unit": self.unit,
            "unit_price": str(self.unit_price.amount),
            "tax": str(self.tax.amount),
            "currency_code": self.unit_price.currency.code,
            "item_ref": self.item_ref,
            "expense_ref": self.expense_ref,
            "asset_ref": self.asset_ref,
            "cost_center_ref": self.cost_center_ref,
            "subject_ref": self.subject_ref,
            "expected_delivery_date": self.expected_delivery_date,
        }


@dataclass(frozen=True, slots=True)
class PurchaseTotals:
    subtotal: Money
    tax: Money
    total: Money


def purchase_totals(lines: tuple[PurchaseLineInput, ...]) -> PurchaseTotals:
    if not lines:
        raise ContractError("a purchase order requires at least one line")
    purchase_currency = lines[0].unit_price.currency
    subtotal = Money.zero(purchase_currency)
    tax = Money.zero(purchase_currency)
    try:
        for line in lines:
            subtotal = subtotal + line.line_total
            tax = tax + line.tax
    except CurrencyMismatchError as exc:
        raise ContractError("every purchase line must use the same currency") from exc
    return PurchaseTotals(subtotal=subtotal, tax=tax, total=subtotal + tax)


@dataclass(frozen=True, slots=True)
class CreatePurchaseOrder:
    order_number: str
    supplier_ref: str
    ordered_on: date
    currency_code: str
    lines: tuple[PurchaseLineInput, ...]
    created_by_ref: str
    source_requisition_id: UUID | None = None
    source_evaluation_id: UUID | None = None
    expected_delivery_date: date | None = None
    ship_to_ref: str | None = None
    terms: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "order_number", _required("order_number", self.order_number, 80)
        )
        object.__setattr__(
            self, "supplier_ref", _required("supplier_ref", self.supplier_ref, 255)
        )
        object.__setattr__(
            self,
            "created_by_ref",
            _required("created_by_ref", self.created_by_ref, 255),
        )
        object.__setattr__(
            self, "ship_to_ref", _optional("ship_to_ref", self.ship_to_ref, 255)
        )
        object.__setattr__(self, "terms", _optional("terms", self.terms, 10_000))
        object.__setattr__(self, "currency_code", _currency_code(self.currency_code))
        totals = purchase_totals(self.lines)
        if totals.total.currency.code != self.currency_code:
            raise ContractError("purchase lines must use the order currency")
        if self.source_requisition_id is None and self.source_evaluation_id is None:
            raise ContractError(
                "a purchase order requires a requisition or approved evaluation source"
            )


@dataclass(frozen=True, slots=True)
class ReceiptLineObservation:
    order_line_id: UUID
    quantity_received: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "quantity_received",
            _positive("quantity_received", self.quantity_received),
        )


@dataclass(frozen=True, slots=True)
class ReceiptObservation:
    source_owner: str
    source_event_id: str
    observed_at: datetime
    lines: tuple[ReceiptLineObservation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_owner", _required("source_owner", self.source_owner, 120)
        )
        object.__setattr__(
            self,
            "source_event_id",
            _required("source_event_id", self.source_event_id, 255),
        )
        _aware("observed_at", self.observed_at)
        if not self.lines:
            raise ContractError("a receipt observation requires at least one line")
        line_ids = [line.order_line_id for line in self.lines]
        if len(line_ids) != len(set(line_ids)):
            raise ContractError("a receipt observation names each line once")

    def document(self) -> dict[str, object]:
        return {
            "source_owner": self.source_owner,
            "source_event_id": self.source_event_id,
            "observed_at": self.observed_at,
            "lines": [
                {
                    "order_line_id": line.order_line_id,
                    "quantity_received": str(line.quantity_received),
                }
                for line in self.lines
            ],
        }


@dataclass(frozen=True, slots=True)
class AwardFact:
    sourcing_event_id: UUID
    evaluation_id: UUID
    bid_id: UUID
    supplier_ref: str
    total: Money
    approved_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovedPurchaseFact:
    purchase_order_id: UUID
    order_number: str
    supplier_ref: str
    total: Money
    content_sha256: str
    approved_at: datetime


__all__ = [
    "ApprovalFact",
    "ApprovedPurchaseFact",
    "AwardFact",
    "BidLineInput",
    "BidScoreInput",
    "BidStatus",
    "BudgetAuthorizationFact",
    "CompleteEvaluation",
    "Conflict",
    "ContractError",
    "CreatePurchaseOrder",
    "CreateRequisition",
    "CreateSourcingEvent",
    "EvaluationCriterion",
    "EvaluationStatus",
    "InvalidTransition",
    "NotFound",
    "ObservationConflict",
    "ProcurementError",
    "PurchaseLineInput",
    "PurchaseOrderStatus",
    "PurchaseTotals",
    "ReceiptLineObservation",
    "ReceiptObservation",
    "RejectionFact",
    "RequisitionLineInput",
    "RequisitionStatus",
    "SnapshotImmutable",
    "SourcingLineInput",
    "SourcingMethod",
    "SourcingStatus",
    "SourcingWindow",
    "SubmitBid",
    "digest_document",
    "purchase_totals",
    "weighted_score",
]
