"""Operation-qualified trusted assembly ports, separate from preview authority.

Only trusted dependency injection may supply these ports. Caller-created verdict
values, matching digests and actor permissions are not verification. Expected
unavailability raises OpeningVerificationUnavailable; programmer failures escape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.fingerprints import fingerprint_of

from dotmac_billing._opening_values import OpeningCashValue
from dotmac_billing.opening_commands import (
    CorrectOpeningCashRefundV1,
    ImportOpeningAccountV1,
    RecordOpeningCashRefundV1,
    ReverseCashCreditDrawV1,
    ReverseOpeningAccountImportV1,
)
from dotmac_billing.opening_contracts import OpeningPartitionV1, OpeningTargetV1
from dotmac_billing.opening_validation import _payload


def opening_cash_command_digest(
    command: ImportOpeningAccountV1
    | RecordOpeningCashRefundV1
    | CorrectOpeningCashRefundV1
    | ReverseOpeningAccountImportV1
    | ReverseCashCreditDrawV1,
) -> str:
    return fingerprint_of(
        {"domain": "billing.opening.cash.command.v1", "command": _payload(command)}
    )


@dataclass(frozen=True, slots=True)
class OpeningImportVerificationRequestV1(OpeningCashValue):
    command: ImportOpeningAccountV1
    command_digest: str
    manifest_fingerprint: str


@dataclass(frozen=True, slots=True)
class OpeningImportVerificationV1(OpeningCashValue):
    operation: Literal["billing.opening.import.v1"]
    scope: Scope
    command_digest: str
    manifest_fingerprint: str
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    installation_id: str
    migration_authority_ref: str
    source_authority_ref: str
    source_binding_proof_ref: str
    cash_lifecycle_proof_ref: str
    migration_authority_proof_ref: str


@dataclass(frozen=True, slots=True)
class OpeningCashRefundSubjectV1(OpeningCashValue):
    scope: Scope
    billing_account_id: UUID
    currency: str
    precision: int
    lot_id: UUID
    lot_evidence_digest: str
    source_system: str
    source_settlement_key: str


@dataclass(frozen=True, slots=True)
class OpeningCashRefundVerificationRequestV1(OpeningCashValue):
    command: RecordOpeningCashRefundV1
    command_digest: str
    subject: OpeningCashRefundSubjectV1


@dataclass(frozen=True, slots=True)
class OpeningCashRefundVerificationV1(OpeningCashValue):
    operation: Literal["billing.opening.cash.refund.v1"]
    command_digest: str
    subject: OpeningCashRefundSubjectV1
    outcome_proof_ref: str
    authorization_proof_ref: str


@dataclass(frozen=True, slots=True)
class OpeningCashRefundCorrectionVerificationRequestV1(OpeningCashValue):
    command: CorrectOpeningCashRefundV1
    command_digest: str
    subject: OpeningCashRefundSubjectV1
    refund_effect_id: UUID
    refund_evidence_digest: str


@dataclass(frozen=True, slots=True)
class OpeningCashRefundCorrectionVerificationV1(OpeningCashValue):
    operation: Literal["billing.opening.cash.refund.correct.v1"]
    command_digest: str
    subject: OpeningCashRefundSubjectV1
    refund_effect_id: UUID
    refund_evidence_digest: str
    correction_outcome_proof_ref: str
    authorization_proof_ref: str


@dataclass(frozen=True, slots=True)
class OpeningImportReversalVerificationRequestV1(OpeningCashValue):
    command: ReverseOpeningAccountImportV1
    command_digest: str
    receipt_evidence_digest: str
    original_posting_group_id: UUID
    billing_account_id: UUID
    currency: str
    precision: int


@dataclass(frozen=True, slots=True)
class OpeningImportReversalVerificationV1(OpeningCashValue):
    operation: Literal["billing.opening.import.reverse.v1"]
    scope: Scope
    command_digest: str
    receipt_id: UUID
    receipt_evidence_digest: str
    original_posting_group_id: UUID
    billing_account_id: UUID
    currency: str
    precision: int
    migration_authority_ref: str
    migration_authority_proof_ref: str


class OpeningImportVerifier(Protocol):
    def verify_import(
        self, request: OpeningImportVerificationRequestV1
    ) -> OpeningImportVerificationV1 | None: ...

    def verify_import_reversal(
        self, request: OpeningImportReversalVerificationRequestV1
    ) -> OpeningImportReversalVerificationV1 | None: ...


class OpeningCashRefundVerifier(Protocol):
    def verify_refund(
        self, request: OpeningCashRefundVerificationRequestV1
    ) -> OpeningCashRefundVerificationV1 | None: ...

    def verify_refund_correction(
        self, request: OpeningCashRefundCorrectionVerificationRequestV1
    ) -> OpeningCashRefundCorrectionVerificationV1 | None: ...
