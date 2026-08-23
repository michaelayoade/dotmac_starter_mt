"""Product-neutral contracts for sales through accepted Quote handoff."""

from __future__ import annotations

import enum
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID


class LeadStatus(enum.StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


class QuoteStatus(enum.StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class DiscountType(enum.StrEnum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class DiscountAction(enum.StrEnum):
    CREATED = "created"
    CHANGED = "changed"
    REMOVED = "removed"


class SalesError(Exception):
    """Base for typed refusals made by the sales owner."""

    code = "sales.error"


class SalesNotFound(SalesError):
    code = "sales.not_found"


class InvalidSalesTransition(SalesError):
    code = "sales.invalid_transition"


class AcceptedQuoteImmutable(SalesError):
    code = "sales.accepted_quote_immutable"


class SalesConflict(SalesError):
    code = "sales.conflict"


@dataclass(frozen=True, slots=True)
class SalesActorRef:
    kind: str
    opaque_id: str


@dataclass(frozen=True, slots=True)
class SalesActorSnapshot:
    ref: SalesActorRef
    label: str


@dataclass(frozen=True, slots=True)
class SalesSubjectRef:
    kind: str
    opaque_id: str
    version: str | None = None


@dataclass(frozen=True, slots=True)
class SalesSubjectSnapshot:
    ref: SalesSubjectRef
    label: str


class ActorPort(Protocol):
    def require_actor(
        self, *, tenant_id: UUID, actor: SalesActorRef
    ) -> SalesActorSnapshot: ...


class SubjectPort(Protocol):
    def require_subject(
        self, *, tenant_id: UUID, subject: SalesSubjectRef
    ) -> SalesSubjectSnapshot: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class OwnerOutputPort(Protocol):
    """Stage an owner-output event in the caller's transaction."""

    def stage(
        self,
        db: object,
        *,
        tenant_id: UUID,
        event_type: str,
        event_id: UUID,
        payload: Mapping[str, object],
        correlation_id: str | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class CreatePipelineCommand:
    tenant_id: UUID
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CreateStageCommand:
    tenant_id: UUID
    pipeline_id: UUID
    name: str
    order_index: int
    default_probability: int = 0


@dataclass(frozen=True, slots=True)
class CreateLeadCommand:
    tenant_id: UUID
    subject: SalesSubjectRef
    title: str
    pipeline_id: UUID
    stage_id: UUID
    currency: str
    estimated_value: Decimal | None = None
    probability: int | None = None
    expected_close_date: datetime | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureLeadOriginCommand:
    tenant_id: UUID
    lead_id: UUID
    capture_method: str
    source_kind: str
    source_ref: str
    source_interaction_id: str | None
    captured_at: datetime
    evidence: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class QuoteTermValueV1:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class QuoteTermsSnapshotV1:
    version_ref: str
    values: tuple[QuoteTermValueV1, ...]


@dataclass(frozen=True, slots=True)
class QuoteTaxRateV1:
    tax_code: str
    source_version: str
    rate: Decimal


@dataclass(frozen=True, slots=True)
class QuoteLineDraft:
    description: str
    quantity: Decimal
    unit_price: Decimal
    price_version_ref: str
    terms_ref: str
    terms_snapshot: QuoteTermsSnapshotV1
    specification_ref: str
    taxes: tuple[QuoteTaxRateV1, ...]
    catalogue_ref: str | None = None


@dataclass(frozen=True, slots=True)
class DiscountInput:
    discount_type: DiscountType
    value: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class AuthorQuoteCommand:
    tenant_id: UUID
    command_id: UUID
    quote_id: UUID
    actor: SalesActorRef
    lead_id: UUID
    status: QuoteStatus
    currency: str
    currency_minor_units: int
    lines: tuple[QuoteLineDraft, ...]
    fulfillment_eligibility_requirement_refs: tuple[str, ...]
    discount: DiscountInput | None = None
    expires_at: datetime | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeQuoteDiscountCommand:
    tenant_id: UUID
    command_id: UUID
    quote_id: UUID
    actor: SalesActorRef
    expected_revision: int
    discount: DiscountInput | None


@dataclass(frozen=True, slots=True)
class AcceptQuoteCommand:
    tenant_id: UUID
    command_id: UUID
    quote_id: UUID
    actor: SalesActorRef
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeQuoteDiscountOutcome:
    quote_id: UUID
    revision: int
    discount_amount: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class AcceptedQuoteLineV1:
    line_id: UUID
    position: int
    description: str
    quantity: str
    unit_price: str
    gross_amount: str
    discount_amount: str
    tax_amount: str
    amount: str
    catalogue_ref: str | None
    price_version_ref: str
    terms_ref: str
    terms_snapshot: QuoteTermsSnapshotV1
    specification_ref: str
    taxes: tuple[AcceptedQuoteTaxComponentV1, ...]


@dataclass(frozen=True, slots=True)
class AcceptedQuoteTaxComponentV1:
    tax_code: str
    source_version: str
    taxable_basis: str
    rate: str | None
    amount: str


@dataclass(frozen=True, slots=True)
class AcceptedQuoteHandoffV1:
    schema_version: int
    event_id: UUID
    tenant_id: UUID
    quote_id: UUID
    lead_id: UUID
    accepted_at: datetime
    accepted_by: Mapping[str, str]
    sales_subject: Mapping[str, str | None]
    sales_subject_label: str
    currency: str
    currency_minor_units: int
    subtotal: str
    discount_amount: str
    tax_total: str
    total: str
    lines: tuple[AcceptedQuoteLineV1, ...]
    fulfillment_eligibility_requirement_refs: tuple[str, ...]
    accepted_snapshot_sha256: str

    def as_payload(self) -> dict[str, object]:
        value = _json_value(asdict(self))
        if not isinstance(value, dict):  # pragma: no cover - asdict is a mapping
            raise TypeError("AcceptedQuoteHandoffV1 did not encode as an object")
        return value


@dataclass(frozen=True, slots=True)
class QuoteAcceptanceOutcome:
    quote_id: UUID
    event_id: UUID
    accepted_at: datetime
    accepted_snapshot_sha256: str
    replayed: bool


def canonical_digest(value: object, *, domain: str) -> str:
    encoded = json.dumps(
        _json_value(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(domain.encode() + b"\x00" + encoded).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, UUID | Decimal | datetime):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value


__all__ = [
    "AcceptQuoteCommand",
    "AcceptedQuoteHandoffV1",
    "AcceptedQuoteImmutable",
    "AcceptedQuoteLineV1",
    "AcceptedQuoteTaxComponentV1",
    "ActorPort",
    "AuthorQuoteCommand",
    "CaptureLeadOriginCommand",
    "ChangeQuoteDiscountCommand",
    "ChangeQuoteDiscountOutcome",
    "Clock",
    "CreateLeadCommand",
    "CreatePipelineCommand",
    "CreateStageCommand",
    "DiscountAction",
    "DiscountInput",
    "DiscountType",
    "InvalidSalesTransition",
    "LeadStatus",
    "OwnerOutputPort",
    "QuoteAcceptanceOutcome",
    "QuoteLineDraft",
    "QuoteTaxRateV1",
    "QuoteTermsSnapshotV1",
    "QuoteTermValueV1",
    "QuoteStatus",
    "SalesActorRef",
    "SalesActorSnapshot",
    "SalesConflict",
    "SalesError",
    "SalesNotFound",
    "SalesSubjectRef",
    "SalesSubjectSnapshot",
    "SubjectPort",
    "canonical_digest",
]
