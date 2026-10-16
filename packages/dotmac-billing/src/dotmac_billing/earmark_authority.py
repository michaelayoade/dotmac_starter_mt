"""Trusted assembly verification port for operation-qualified earmark changes.

Only trusted dependency injection installs a verifier. Request data and matching
fingerprints cannot authorize corrections; every call, including retry, verifies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

from dotmac_billing.commands import (
    DeallocationCommand,
    ReallocationCommand,
    RefundCommand,
    ReversePostingGroupCommand,
)
from dotmac_billing.earmark_contracts import (
    SettlementEarmarkV1,
    _identity,
    _instant,
    _invalid,
    _quantity,
    _scope,
)
from dotmac_billing.opening_commands import ReverseCashCreditDrawV1


@dataclass(frozen=True, slots=True)
class ReleaseEarmarkV1:
    scope: Scope
    earmark_id: UUID
    amount: Money
    source_ref: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _scope(self.scope, self.earmark_id)
        _quantity(self.amount)
        _identity(self.source_ref)
        _instant(self.occurred_at)


@dataclass(frozen=True, slots=True)
class ReverseEarmarkReleaseV1:
    scope: Scope
    release_effect_id: UUID
    amount: Money
    source_ref: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _scope(self.scope, self.release_effect_id)
        _quantity(self.amount)
        _identity(self.source_ref)
        _instant(self.occurred_at)


EarmarkChange = (
    DeallocationCommand
    | ReallocationCommand
    | RefundCommand
    | ReversePostingGroupCommand
    | ReverseCashCreditDrawV1
    | ReleaseEarmarkV1
    | ReverseEarmarkReleaseV1
)


@dataclass(frozen=True, slots=True)
class CorrectSettlementEarmarkV1:
    """Exact financial correction with separately injected verification.

    A qualified refund consumes still-held receipt capacity first. Authorized
    foreign reallocation releases moved held capacity while retaining original
    targets. Neither operation edits the original whole-receipt claim.
    """

    scope: Scope
    earmark_ids: tuple[UUID, ...]
    change: EarmarkChange

    def __post_init__(self) -> None:
        _scope(self.scope)
        if (
            type(self.earmark_ids) is not tuple
            or not self.earmark_ids
            or any(type(item) is not UUID for item in self.earmark_ids)
            or len(set(self.earmark_ids)) != len(self.earmark_ids)
        ):
            _invalid()
        if type(self.change) not in (
            DeallocationCommand,
            ReallocationCommand,
            RefundCommand,
            ReversePostingGroupCommand,
            ReverseCashCreditDrawV1,
            ReleaseEarmarkV1,
            ReverseEarmarkReleaseV1,
        ):
            _invalid()
        if isinstance(
            self.change,
            ReleaseEarmarkV1 | ReverseEarmarkReleaseV1 | ReverseCashCreditDrawV1,
        ):
            self.change.__post_init__()
            if self.change.scope != self.scope:
                _invalid()
        if isinstance(
            self.change, DeallocationCommand | ReallocationCommand | RefundCommand
        ):
            _quantity(self.change.amount)
        _instant(self.change.occurred_at)
        if not isinstance(self.change, ReverseCashCreditDrawV1):
            _identity(self.change.source_ref)
        identity_fields = {
            DeallocationCommand: ("allocation_id",),
            ReallocationCommand: (
                "settlement_id",
                "from_document_id",
                "to_document_id",
            ),
            RefundCommand: ("settlement_id",),
            ReversePostingGroupCommand: ("posting_group_id",),
        }
        for field in identity_fields.get(type(self.change), ()):
            _scope(self.scope, getattr(self.change, field))


@dataclass(frozen=True, slots=True)
class EarmarkCorrectionVerificationRequestV1:
    operation: str
    command: CorrectSettlementEarmarkV1
    command_digest: str
    claims: tuple[SettlementEarmarkV1, ...]


@dataclass(frozen=True, slots=True)
class EarmarkCorrectionVerificationV1:
    operation: str
    command_digest: str
    claim_fingerprints: tuple[tuple[UUID, str], ...]
    authorization_proof_ref: str
    outcome_proof_ref: str


class EarmarkCorrectionVerifier(Protocol):
    def verify_correction(
        self, request: EarmarkCorrectionVerificationRequestV1
    ) -> EarmarkCorrectionVerificationV1 | None: ...
