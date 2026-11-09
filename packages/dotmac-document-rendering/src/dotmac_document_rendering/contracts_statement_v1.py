"""Account statement inputs: a frozen, by-value statement of one customer account.

Billing (or whichever owner produces the statement) supplies every amount,
including each running balance and the closing balance. Rendering formats those
values and derives none of them: no sum, difference or reconciliation happens
here. The brand logo is the same frozen ``EmbeddedLogoAssetV3`` snapshot used by
V3 invoices; the bytes travel beside the fact, never inside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

from dotmac_document_rendering.contracts import Section, TemplateDecision
from dotmac_document_rendering.contracts_v2 import (
    PartySnapshotV2,
    PaymentInstructionsV2,
)
from dotmac_document_rendering.contracts_v3 import EmbeddedLogoAssetV3


@dataclass(frozen=True, slots=True)
class StatementEntryV1:
    entry_number: int
    occurred_at: datetime
    entry_kind: str
    reference: str
    description: str
    debit: Money
    credit: Money
    balance_after: Money
    source_fact_id: UUID
    source_fact_version: int


@dataclass(frozen=True, slots=True)
class AccountStatementFactV1:
    contract_version: int
    scope: Scope
    statement_id: UUID
    emitter: str
    fact_version: int
    recorded_at: datetime
    original_presentation_model_digest: str
    statement_number: str
    statement_state: str
    statement_profile_code: str
    statement_profile_version: str
    currency: str
    minor_units: int
    period_start: datetime
    period_end: datetime
    opening_balance: Money
    total_debits: Money
    total_credits: Money
    closing_balance: Money
    entries: tuple[StatementEntryV1, ...]
    customer_account_reference: str | None
    seller_snapshot: PartySnapshotV2
    customer_snapshot: PartySnapshotV2
    payment_instructions: PaymentInstructionsV2 | None
    brand_logo: EmbeddedLogoAssetV3 | None
    locale: str
    timezone: str
    issued_at: datetime
    frozen_at: datetime
    source_authority: str
    correlation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))


@dataclass(frozen=True, slots=True)
class StatementProfileBindingV1:
    statement_profile_code: str
    statement_profile_version: str
    media_type: str
    template_code: str
    template_version: str


@dataclass(frozen=True, slots=True)
class AccountStatementProjectionV1:
    projection_contract_version: int
    statement_id: UUID
    fact_version: int
    source_fact_fingerprint: str
    statement_profile_code: str
    statement_profile_version: str
    template_code: str
    template_version: str
    renderer_code: str
    renderer_version: str
    media_type: str
    template_decisions: tuple[TemplateDecision, ...]
    sections: tuple[Section, ...]
    brand_logo: EmbeddedLogoAssetV3 | None
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_decisions", tuple(self.template_decisions))
        object.__setattr__(self, "sections", tuple(self.sections))


__all__ = [
    "AccountStatementFactV1",
    "AccountStatementProjectionV1",
    "StatementEntryV1",
    "StatementProfileBindingV1",
]
