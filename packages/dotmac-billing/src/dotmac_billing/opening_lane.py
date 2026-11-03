"""Opening-lane import-specific assembly authority port.

Billing's contract names ``import-specific assembly authority``: the trusted
external qualification an assembly supplies for opening writes. This module is
that port as ONE injected object, plus thin entry points a composition package
(the flows opening lane) or an imports ``RowApplier`` calls. Nothing here adds
trust: every entry point still demands the operation-qualified verifier it
names, and a caller-built value, matching digest or actor permission is never
authority. Billing keeps the transaction; the caller commits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from dotmac_kernel.cache import Scope

from dotmac_billing import service
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_hold_contracts import (
    AdmitHeldOpeningAccountV2,
    OpeningHoldApprovalPort,
)
from dotmac_billing.opening_receivable_contracts import (
    ImportOpeningAccountV2,
    OpeningAccountImportVerifierV2,
    OpeningImportReceiptV2,
)
from dotmac_billing.opening_window_contracts import (
    OpeningAuthoritySwitchParticipant,
    OpeningWindowSealV1,
    OpeningWindowSealVerifier,
    SealOpeningWindowV1,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@runtime_checkable
class OpeningLaneAssemblyAuthorityV1(Protocol):
    """Trusted dependency injection only; assembly-owned, never request data."""

    def import_verifier(self) -> OpeningAccountImportVerifierV2: ...

    def seal_verifier(self) -> OpeningWindowSealVerifier: ...

    def hold_approvals(self) -> OpeningHoldApprovalPort: ...


@dataclass(frozen=True, slots=True)
class OpeningLanePartitionResultV1:
    """Identity-only outcome; a ``RowApplier`` may return ``as_result()`` verbatim."""

    receipt_id: UUID
    billing_account_id: UUID
    posting_group_id: UUID

    def as_result(self) -> Mapping[str, object]:
        return {
            "receipt_id": str(self.receipt_id),
            "billing_account_id": str(self.billing_account_id),
            "posting_group_id": str(self.posting_group_id),
        }


def _authority(value: object) -> OpeningLaneAssemblyAuthorityV1:
    if not isinstance(value, OpeningLaneAssemblyAuthorityV1):
        raise BillingRuleViolation(
            "opening_verifier_unavailable",
            "opening lane requires the trusted assembly authority port",
        )
    return value


def _result(receipt: OpeningImportReceiptV2) -> OpeningLanePartitionResultV1:
    return OpeningLanePartitionResultV1(
        receipt.receipt_id, receipt.billing_account_id, receipt.posting_group_id
    )


def import_opening_lane_partition(
    db: Session,
    *,
    scope: Scope,
    command: ImportOpeningAccountV2,
    authority: OpeningLaneAssemblyAuthorityV1,
) -> OpeningLanePartitionResultV1:
    """One partition per call, inside the open window (idempotent on its key)."""
    trusted = _authority(authority)
    return _result(
        service.import_opening_account_v2(
            db, scope=scope, command=command, verifier=trusted.import_verifier()
        )
    )


def admit_held_opening_lane_partition(
    db: Session,
    *,
    scope: Scope,
    command: AdmitHeldOpeningAccountV2,
    authority: OpeningLaneAssemblyAuthorityV1,
) -> OpeningLanePartitionResultV1:
    """One sealed-hold partition after the seal (D-5 widening)."""
    trusted = _authority(authority)
    return _result(
        service.admit_held_opening_account_v2(
            db,
            scope=scope,
            command=command,
            verifier=trusted.import_verifier(),
            approvals=trusted.hold_approvals(),
        )
    )


def seal_opening_lane(
    db: Session,
    *,
    scope: Scope,
    command: SealOpeningWindowV1,
    authority: OpeningLaneAssemblyAuthorityV1,
    authority_switch: OpeningAuthoritySwitchParticipant | None = None,
) -> OpeningWindowSealV1:
    """Seal (and optionally switch authority) in the caller's one transaction."""
    trusted = _authority(authority)
    return service.seal_opening_window(
        db,
        scope=scope,
        command=command,
        verifier=trusted.seal_verifier(),
        authority_switch=authority_switch,
    )


__all__ = [
    "OpeningLaneAssemblyAuthorityV1",
    "OpeningLanePartitionResultV1",
    "admit_held_opening_lane_partition",
    "import_opening_lane_partition",
    "seal_opening_lane",
]
