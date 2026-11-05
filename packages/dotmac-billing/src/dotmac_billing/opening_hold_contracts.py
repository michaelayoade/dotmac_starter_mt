"""Post-seal admission of sealed-hold partitions: closed contracts and ports.

Recorded widening (cutover decision D-5). The opening seal stays permanent for
every partition that was admitted before it. Only the partitions the seal itself
names as held can later enter, one partition per command, each with a reviewed
disposition digest and an Approvals maker-checker approval. Billing owns no
approval state: the Approvals decision arrives through ``OpeningHoldApprovalPort``,
whose adapter maps the Approvals module's public hold contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of

from dotmac_billing._opening_values import OpeningCashValue
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_contracts import OpeningEvidenceError
from dotmac_billing.opening_receivable_contracts import ImportOpeningAccountV2
from dotmac_billing.opening_receivable_validation import (
    opening_account_command_digest_v2,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Approvals ``subject_type`` for a held-partition admission (opaque to Approvals).
HOLD_APPROVAL_SUBJECT_TYPE = "billing.opening.hold_admission.v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_MAX_REF = 255


@dataclass(frozen=True, slots=True)
class OpeningHoldDispositionV1(OpeningCashValue):
    """The reviewed disposition: a digest of a document held outside Billing."""

    disposition_ref: str
    disposition_digest: str

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        if _DIGEST.fullmatch(self.disposition_digest) is None:
            raise OpeningEvidenceError("disposition digest must be sha256:<64 hex>")
        if len(self.disposition_ref) > _MAX_REF:
            raise OpeningEvidenceError("disposition reference exceeds storage")


@dataclass(frozen=True, slots=True)
class AdmitHeldOpeningAccountV2(OpeningCashValue):
    """One held partition. The embedded import command is the ordinary V2 import.

    ``import_command`` is validated by the same validators, verifier and writer as
    ``import_opening_account_v2``; nothing here relaxes them.
    """

    scope: Scope
    window_id: UUID
    import_command: ImportOpeningAccountV2
    disposition: OpeningHoldDispositionV1
    maker_ref: str
    approval_request_id: UUID

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        if len(self.maker_ref) > _MAX_REF:
            raise OpeningEvidenceError("maker reference exceeds storage")
        if self.import_command.scope != self.scope:
            raise OpeningEvidenceError("hold admission scope differs from import")


@dataclass(frozen=True, slots=True)
class HeldOpeningHoldApprovalV1(OpeningCashValue):
    """What Approvals vouched for, observed under the request's SHARE lock."""

    request_id: UUID
    subject_type: str
    subject_id: str
    content_digest: str
    requester_ref: str
    approver_refs: tuple[str, ...]


class OpeningHoldApprovalPort(Protocol):
    """Trusted dependency over the Approvals public contract; never caller data.

    The adapter must hold the approval request FOR SHARE until the caller's
    transaction ends (Approvals ``hold_platform_approval`` semantics) so a
    concurrent withdrawal cannot slip between this check and the admission
    commit. Return ``None`` or raise ``OpeningVerificationUnavailable`` when the
    request does not stand as approved for exactly this subject and digest.
    """

    def hold_approval(
        self,
        db: Session,
        *,
        scope: Scope,
        request_id: UUID,
        subject_type: str,
        subject_id: str,
        content_digest: str,
    ) -> HeldOpeningHoldApprovalV1 | None: ...


@dataclass(frozen=True, slots=True)
class OpeningHoldAdmissionV1(OpeningCashValue):
    """Immutable audit record of one admitted held partition."""

    scope: Scope
    admission_id: UUID
    window_id: UUID
    billing_account_id: UUID
    receipt_id: UUID
    import_command_digest: str
    content_digest: str
    disposition: OpeningHoldDispositionV1
    maker_ref: str
    approval: HeldOpeningHoldApprovalV1
    recorded_at: datetime


def opening_hold_subject_id(command: AdmitHeldOpeningAccountV2) -> str:
    """Approvals subject: one window, one partition."""
    return f"{command.window_id}:{command.import_command.partition.account_id}"


def opening_hold_content_digest(command: AdmitHeldOpeningAccountV2) -> str:
    """Digest the approver approves: the exact partition command plus disposition."""
    scope = command.scope
    digest = fingerprint_of(
        {
            "domain": "billing.opening.hold.admission.v1",
            "scope_kind": "tenant" if isinstance(scope, TenantScope) else "platform",
            "scope_id": str(scope.tenant_id)
            if isinstance(scope, TenantScope)
            else "platform",
            "window_id": str(command.window_id),
            "import_command_digest": opening_account_command_digest_v2(
                command.import_command
            ),
            "disposition_ref": command.disposition.disposition_ref,
            "disposition_digest": command.disposition.disposition_digest,
            "maker_ref": command.maker_ref,
        }
    )
    return f"sha256:{digest}"


def require_hold_maker_checker(
    command: AdmitHeldOpeningAccountV2,
    approval: HeldOpeningHoldApprovalV1 | None,
) -> HeldOpeningHoldApprovalV1:
    """Refuse unless the approval is for exactly this command and maker != checker."""
    if approval is None:
        raise BillingRuleViolation(
            "opening_hold_approval_missing",
            "hold admission requires a standing Approvals decision",
        )
    if (
        type(approval) is not HeldOpeningHoldApprovalV1
        or approval.request_id != command.approval_request_id
        or approval.subject_type != HOLD_APPROVAL_SUBJECT_TYPE
        or approval.subject_id != opening_hold_subject_id(command)
        or approval.content_digest != opening_hold_content_digest(command)
    ):
        raise BillingRuleViolation(
            "opening_hold_approval_binding",
            "approval does not bind this exact partition, disposition and maker",
        )
    checkers = approval.approver_refs
    if (
        not checkers
        or len(set(checkers)) != len(checkers)
        or any(not ref.strip() or len(ref) > _MAX_REF for ref in checkers)
    ):
        raise BillingRuleViolation(
            "opening_hold_maker_checker", "approval carries no distinct checker"
        )
    if approval.requester_ref != command.maker_ref or command.maker_ref in checkers:
        raise BillingRuleViolation(
            "opening_hold_maker_checker",
            "maker must be the requester and may not be a checker",
        )
    return approval


__all__ = [
    "HOLD_APPROVAL_SUBJECT_TYPE",
    "AdmitHeldOpeningAccountV2",
    "HeldOpeningHoldApprovalV1",
    "OpeningHoldAdmissionV1",
    "OpeningHoldApprovalPort",
    "OpeningHoldDispositionV1",
    "opening_hold_content_digest",
    "opening_hold_subject_id",
    "require_hold_maker_checker",
]
