"""X1: Billing's receivable exposure, mapped to Collections' reader port.

Stateless. No table, migration, event, lock or clock. One read of Billing's
sealed document exposure becomes a ``ReceivableObservationV1``; Billing keeps the
money and the due date, Collections keeps the decision. The reader adds exactly
one thing of its own: ``access_hold_from``, the instant service goes off, which is
the due date plus the account's ``postpaid_grace`` term read at the due date.

Rules (fail closed):

* Terms cannot be back-dated, so an item issued or due before the flip has no terms at
  those instants. The reader takes the flip as ``terms_floor_at`` (a typed input
  entered at ship time, never hard-coded), reads terms at the later of the instant and
  the floor, and if none exist even then returns
  ``PositionUnavailable("account_terms_missing")``: Collections blocks the exposure,
  no poison message and no queue.

* A prepaid account's exposure is refused (``not_postpaid``): Collections never
  chases prepaid, so such an exposure is an anomaly for the manual exceptions
  review, not something to script away. The contract type is read at the invoice's
  issue instant, the same instant the GAP-3 balance pass uses.
* An unverified due date carries no ``due_at`` and no ``access_hold_from``.
* ``access_hold_from`` reads the grace at the due date. Billing refuses back-dated
  terms, so once the due date has passed the value cannot move.
* A document Billing does not know is ``PositionUnknown``; a source owner other than
  Billing is an authority mismatch. Any other Billing refusal raises: the caller rolls
  back and its event delivery retries, so a fault heals when the cause is fixed. No
  attention row, no queue.

The subject is the billing account, ``billing-account:{uuid}``, for native and
imported exposures alike; it is also the key of every collection hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from dotmac_billing import (
    BillingRuleViolation,
    DueDateBasisStatus,
    ReceivableExposureV1,
    ReceivableExposureV2,
    get_document_exposure,
    get_effective_account_terms,
)
from dotmac_collections import (
    PositionAuthorityMismatch,
    PositionReadOk,
    PositionUnavailable,
    PositionUnknown,
    ReceivableObservationV1,
    ReceivablesReadResult,
)
from dotmac_kernel.cache import Scope, TenantScope
from sqlalchemy.orm import Session

SOURCE_OWNER = "dotmac-billing"
SUBJECT_PREFIX = "billing-account:"
DOCUMENT_PREFIX = "document:"
_OPEN_STATES = ("open", "partially_resolved")
_POSTPAID = "postpaid"
_TERMS_MISSING = "billing.terms_default_missing"
_NOT_FOUND = "billing.not_found"  # Billing prefixes its stable error codes


def subject_ref_for(billing_account_id: UUID) -> str:
    """The one subject convention shared with every collection hold."""

    return f"{SUBJECT_PREFIX}{billing_account_id}"


def _document_id(exposure_ref: str) -> UUID | None:
    prefix, separator, identity = exposure_ref.partition(":")
    if prefix != DOCUMENT_PREFIX.rstrip(":") or not separator:
        return None
    try:
        return UUID(identity)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class BillingReceivablesReader:
    """Implements Collections' ``ReceivablesReader`` over one caller session."""

    db: Session
    reason_code: str = "invoice_overdue"
    # The first instant Billing holds account terms (the flip), entered at ship time.
    # Terms cannot be back-dated, so an item issued or due before it is read at the
    # floor instead of failing for ever.
    terms_floor_at: datetime | None = None

    def read(
        self,
        *,
        scope: Scope,
        source_owner: str,
        exposure_ref: str,
        as_of: datetime,
    ) -> ReceivablesReadResult:
        del as_of  # Collections applies the instant; Billing's fact is as sealed.
        if not self.reason_code.strip():
            raise ValueError("reason_code is required")
        floor = self.terms_floor_at
        if floor is not None and (floor.tzinfo is None or floor.utcoffset() is None):
            # A deploy-time input error, refused before any Billing read, not data.
            raise ValueError("terms_floor_at must be timezone-aware")
        if not isinstance(scope, TenantScope):
            raise TypeError("money flows requires a TenantScope")
        if source_owner != SOURCE_OWNER:
            return PositionAuthorityMismatch(SOURCE_OWNER, source_owner)
        document_id = _document_id(exposure_ref)
        if document_id is None:
            return PositionUnknown(source_owner, exposure_ref)
        try:
            exposure = get_document_exposure(
                self.db, scope=scope, document_id=document_id
            )
        except BillingRuleViolation as exc:
            if exc.code == _NOT_FOUND:
                return PositionUnknown(source_owner, exposure_ref)
            raise
        return self._observe(scope, exposure)

    def _terms(self, scope: TenantScope, account_id: UUID, at: datetime) -> Any | None:
        """The account terms at ``at`` (not before the floor); None if none exist."""

        if self.terms_floor_at is not None:
            at = max(at, self.terms_floor_at)
        try:
            return get_effective_account_terms(
                self.db, scope=scope, account_id=account_id, at=at
            )
        except BillingRuleViolation as exc:
            if exc.code == _TERMS_MISSING:
                return None
            raise

    def _observe(
        self, scope: TenantScope, exposure: ReceivableExposureV1 | ReceivableExposureV2
    ) -> PositionReadOk | PositionUnavailable:
        if isinstance(exposure, ReceivableExposureV2):
            evidence = exposure.collection
            verified = evidence.status == "verified"
            basis = evidence.due_date_basis
            service_ref = evidence.service_ref
            timing = evidence.collection_timing or "arrears"
            period = evidence.service_period
            due_at = evidence.due_at if verified else None
            status = basis.status.value if verified else "unknown_unverified"
        else:
            basis = exposure.due_date_basis
            service_ref = exposure.service_ref
            timing = exposure.collection_timing
            period = exposure.service_period
            due_at = exposure.due_at
            status = basis.status.value
        account_id = exposure.billing_account_id
        hold_from = None
        live = (
            exposure.financial_state in _OPEN_STATES
            and exposure.collectible_receivable.is_positive
        )
        if live and status == DueDateBasisStatus.VERIFIED.value and due_at is not None:
            if basis.issued_at is None:
                return PositionUnavailable("issue_instant_unknown", None)
            at_issue = self._terms(scope, account_id, basis.issued_at)
            if at_issue is None:
                return PositionUnavailable("account_terms_missing", None)
            if at_issue.contract_type.value != _POSTPAID:
                return PositionUnavailable("not_postpaid", None)
            at_due = self._terms(scope, account_id, due_at)
            if at_due is None:
                return PositionUnavailable("account_terms_missing", None)
            hold_from = due_at + at_due.postpaid_grace.value
        return PositionReadOk(
            ReceivableObservationV1(
                scope=scope,
                source_owner=exposure.source_owner,
                exposure_ref=exposure.exposure_ref,
                source_version=exposure.source_version,
                state_fingerprint=exposure.state_fingerprint,
                subject_ref=subject_ref_for(account_id),
                service_ref=service_ref,
                collection_timing=timing,
                reason_code=self.reason_code,
                collectible_receivable=exposure.collectible_receivable,
                service_period_status=period.status.value,
                service_period_starts_at=period.starts_at,
                service_period_ends_at=period.ends_at,
                due_at=due_at,
                due_date_status=status,  # type: ignore[arg-type]
                financial_state=exposure.financial_state,
                source_authority=exposure.source_authority,
                projection_mode=exposure.projection_mode,
                completeness=exposure.completeness,
                completeness_reason_code=exposure.completeness_reason_code,
                observed_at=exposure.observed_at,
                access_hold_from=hold_from,
            )
        )


__all__ = [
    "BillingReceivablesReader",
    "DOCUMENT_PREFIX",
    "SOURCE_OWNER",
    "SUBJECT_PREFIX",
    "subject_ref_for",
]
