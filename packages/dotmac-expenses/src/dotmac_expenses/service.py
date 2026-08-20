"""One transaction-participating service owner for Expenses decisions."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Party
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from dotmac_expenses.contracts import (
    AddPolicyRule,
    ApplyDecision,
    AttachReceipt,
    ClaimLineDraft,
    ClaimStatus,
    Conflict,
    CreateCategory,
    CreateClaim,
    CreateClaimFromRequest,
    CreatePolicyRevision,
    CreateRequest,
    Decision,
    EvaluationFinding,
    EvaluationResult,
    InvalidCommand,
    InvalidLifecycle,
    LimitAction,
    LimitPeriod,
    NotFound,
    PolicyContext,
    PolicyStatus,
    PolicyTarget,
    ReceiptVerificationStatus,
    ReimbursementEligibility,
    RequestStatus,
    ReviseClaim,
    ReviseRequest,
    SubmissionOutcome,
)
from dotmac_expenses.models import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimLine,
    ExpenseLifecycleEvent,
    ExpensePolicy,
    ExpensePolicyEvaluation,
    ExpensePolicyRule,
    ExpenseReceipt,
    ExpenseRequest,
    ExpenseRequestLine,
)

_MONEY_QUANTUM = Decimal("0.01")
_CODE_RE = re.compile(r"[^A-Z0-9]+")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class _EvaluationLine:
    id: UUID
    category_id: UUID
    amount: Decimal
    occurred_on: date
    has_usable_receipt: bool


def _conflict_scope(db: Session) -> AbstractContextManager[None]:
    """Load the database boundary lazily so package imports need no DB config."""

    from dotmac_kernel.db import conflict_savepoint

    return conflict_savepoint(db)


def _money(value: Decimal, *, field: str, allow_zero: bool = False) -> Decimal:
    try:
        amount = Decimal(value).quantize(_MONEY_QUANTUM)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidCommand(f"{field} must be a finite decimal amount") from exc
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "greater than zero"
        raise InvalidCommand(f"{field} must be {qualifier}")
    return amount


def _optional_money(
    value: Decimal | None, *, field: str, allow_zero: bool = False
) -> Decimal | None:
    if value is None:
        return None
    return _money(value, field=field, allow_zero=allow_zero)


def _code(value: str, *, field: str) -> str:
    normalized = _CODE_RE.sub("-", value.strip().upper()).strip("-")
    if not normalized:
        raise InvalidCommand(f"{field} is required")
    if len(normalized) > 80:
        raise InvalidCommand(f"{field} is too long")
    return normalized


def _text(value: str, *, field: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise InvalidCommand(f"{field} is required")
    if len(normalized) > maximum:
        raise InvalidCommand(f"{field} is too long")
    return normalized


def _optional_text(value: str | None, *, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise InvalidCommand("text value is too long")
    return normalized


def _currency(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
        raise InvalidCommand("currency_code must be a three-letter ASCII code")
    return normalized


def _applicability_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if len(normalized) > 200:
        raise InvalidCommand("applicability_key is too long")
    return normalized


def _party_exists(db: Session, *, tenant_id: UUID, party_id: UUID) -> bool:
    return (
        db.scalar(
            select(Party.id).where(
                Party.tenant_id == tenant_id,
                Party.id == party_id,
            )
        )
        is not None
    )


def _category(
    db: Session, *, tenant_id: UUID, category_id: UUID, active: bool = False
) -> ExpenseCategory:
    query = select(ExpenseCategory).where(
        ExpenseCategory.tenant_id == tenant_id,
        ExpenseCategory.id == category_id,
    )
    if active:
        query = query.where(ExpenseCategory.is_active.is_(True))
    row = db.scalar(query)
    if row is None:
        raise NotFound(f"expense category {category_id} was not found")
    return row


def create_category(
    db: Session, *, scope: TenantScope, command: CreateCategory
) -> ExpenseCategory:
    code = _code(command.code, field="category code")
    if db.scalar(
        select(ExpenseCategory.id).where(
            ExpenseCategory.tenant_id == scope.tenant_id,
            ExpenseCategory.code == code,
        )
    ):
        raise Conflict(f"expense category code {code!r} already exists")
    receipt_threshold = _optional_money(
        command.receipt_threshold,
        field="receipt_threshold",
        allow_zero=True,
    )
    max_line = _optional_money(command.max_amount_per_line, field="max_amount_per_line")
    max_claim = _optional_money(
        command.max_amount_per_claim, field="max_amount_per_claim"
    )
    row = ExpenseCategory(
        tenant_id=scope.tenant_id,
        code=code,
        name=_text(command.name, field="category name", maximum=160),
        description=_optional_text(command.description, maximum=4000),
        requires_receipt=command.requires_receipt,
        receipt_threshold=receipt_threshold,
        max_amount_per_line=max_line,
        max_amount_per_claim=max_claim,
        is_active=command.is_active,
    )
    try:
        with _conflict_scope(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(f"expense category code {code!r} already exists") from exc
    return row


def create_policy_revision(
    db: Session, *, scope: TenantScope, command: CreatePolicyRevision
) -> ExpensePolicy:
    code = _code(command.code, field="policy code")
    if command.version <= 0:
        raise InvalidCommand("policy version must be positive")
    if (
        command.effective_to is not None
        and command.effective_to < command.effective_from
    ):
        raise InvalidCommand("effective_to cannot be before effective_from")
    row = ExpensePolicy(
        tenant_id=scope.tenant_id,
        code=code,
        name=_text(command.name, field="policy name", maximum=200),
        version=command.version,
        currency_code=_currency(command.currency_code),
        effective_from=command.effective_from,
        effective_to=command.effective_to,
        description=_optional_text(command.description, maximum=4000),
        status=PolicyStatus.DRAFT,
    )
    try:
        with _conflict_scope(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(
            f"policy {code!r} version {command.version} already exists"
        ) from exc
    return row


def _policy(
    db: Session, *, tenant_id: UUID, policy_id: UUID, lock: bool = False
) -> ExpensePolicy:
    query = (
        select(ExpensePolicy)
        .where(
            ExpensePolicy.tenant_id == tenant_id,
            ExpensePolicy.id == policy_id,
        )
        .options(selectinload(ExpensePolicy.rules))
    )
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise NotFound(f"expense policy {policy_id} was not found")
    return row


def add_policy_rule(
    db: Session, *, scope: TenantScope, command: AddPolicyRule
) -> ExpensePolicyRule:
    policy = _policy(
        db, tenant_id=scope.tenant_id, policy_id=command.policy_id, lock=True
    )
    if policy.status != PolicyStatus.DRAFT:
        raise InvalidLifecycle("a published or retired policy revision is immutable")
    category_id = command.category_id
    if category_id is not None:
        _category(db, tenant_id=scope.tenant_id, category_id=category_id)
    code = _code(command.code, field="rule code")
    if command.priority < 0:
        raise InvalidCommand("priority must be non-negative")
    row = ExpensePolicyRule(
        tenant_id=scope.tenant_id,
        policy_id=policy.id,
        category_id=category_id,
        code=code,
        name=_text(command.name, field="rule name", maximum=200),
        description=_optional_text(command.description, maximum=4000),
        target=command.target,
        period=command.period,
        action=command.action,
        limit_amount=_money(command.limit_amount, field="limit_amount"),
        applicability_key=_applicability_key(command.applicability_key),
        priority=command.priority,
    )
    try:
        with _conflict_scope(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(f"policy rule code {code!r} already exists") from exc
    return row


def publish_policy(
    db: Session,
    *,
    scope: TenantScope,
    policy_id: UUID,
    published_at: datetime,
) -> ExpensePolicy:
    policy = _policy(db, tenant_id=scope.tenant_id, policy_id=policy_id, lock=True)
    if policy.status == PolicyStatus.PUBLISHED:
        return policy
    if policy.status != PolicyStatus.DRAFT:
        raise InvalidLifecycle("only a draft policy revision can be published")
    if not policy.rules:
        raise InvalidLifecycle("a policy needs at least one rule before publication")
    end = policy.effective_to or date.max
    overlap = db.scalar(
        select(ExpensePolicy.id).where(
            ExpensePolicy.tenant_id == scope.tenant_id,
            ExpensePolicy.id != policy.id,
            ExpensePolicy.code == policy.code,
            ExpensePolicy.currency_code == policy.currency_code,
            ExpensePolicy.status == PolicyStatus.PUBLISHED,
            ExpensePolicy.effective_from <= end,
            or_(
                ExpensePolicy.effective_to.is_(None),
                ExpensePolicy.effective_to >= policy.effective_from,
            ),
        )
    )
    if overlap is not None:
        raise Conflict(
            f"published policy {policy.code!r} has an overlapping effective revision"
        )
    policy.status = PolicyStatus.PUBLISHED
    policy.published_at = published_at
    db.flush()
    return policy


def retire_policy(
    db: Session,
    *,
    scope: TenantScope,
    policy_id: UUID,
    retired_at: datetime,
) -> ExpensePolicy:
    policy = _policy(db, tenant_id=scope.tenant_id, policy_id=policy_id, lock=True)
    if policy.status == PolicyStatus.RETIRED:
        return policy
    if policy.status != PolicyStatus.PUBLISHED:
        raise InvalidLifecycle("only a published policy revision can be retired")
    policy.status = PolicyStatus.RETIRED
    policy.retired_at = retired_at
    db.flush()
    return policy


def _event(
    db: Session,
    *,
    tenant_id: UUID,
    request_id: UUID | None,
    claim_id: UUID | None,
    from_status: str | None,
    to_status: str,
    actor_reference: str,
    occurred_at: datetime,
    decision_reference: str | None = None,
    reason: str | None = None,
) -> ExpenseLifecycleEvent:
    row = ExpenseLifecycleEvent(
        tenant_id=tenant_id,
        request_id=request_id,
        claim_id=claim_id,
        from_status=from_status,
        to_status=to_status,
        actor_reference=_text(actor_reference, field="actor_reference", maximum=255),
        decision_reference=_optional_text(decision_reference, maximum=255),
        reason=_optional_text(reason, maximum=4000),
        occurred_at=occurred_at,
    )
    db.add(row)
    return row


def create_request(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateRequest,
    actor_reference: str,
    recorded_at: datetime,
) -> ExpenseRequest:
    if not command.lines:
        raise InvalidCommand("an expense request needs at least one line")
    if not _party_exists(
        db, tenant_id=scope.tenant_id, party_id=command.requester_party_id
    ):
        raise NotFound(f"requester party {command.requester_party_id} was not found")
    reference = _text(command.reference, field="request reference", maximum=80)
    lines: list[tuple[UUID, str, Decimal, date, str | None, str | None]] = []
    for draft in command.lines:
        category = _category(
            db,
            tenant_id=scope.tenant_id,
            category_id=draft.category_id,
            active=True,
        )
        lines.append(
            (
                category.id,
                _text(draft.description, field="line description", maximum=500),
                _money(draft.amount, field="line amount"),
                draft.expected_on,
                _optional_text(draft.vendor_name, maximum=200),
                _optional_text(draft.notes, maximum=4000),
            )
        )
    row = ExpenseRequest(
        tenant_id=scope.tenant_id,
        reference=reference,
        requester_party_id=command.requester_party_id,
        purpose=_text(command.purpose, field="request purpose", maximum=500),
        currency_code=_currency(command.currency_code),
        needed_by=command.needed_by,
        notes=_optional_text(command.notes, maximum=4000),
        total_requested_amount=sum((item[2] for item in lines), Decimal("0")),
        status=RequestStatus.DRAFT,
    )
    try:
        with _conflict_scope(db):
            db.add(row)
            db.flush()
            for sequence, line in enumerate(lines):
                db.add(
                    ExpenseRequestLine(
                        tenant_id=scope.tenant_id,
                        request_id=row.id,
                        category_id=line[0],
                        sequence=sequence,
                        description=line[1],
                        amount=line[2],
                        expected_on=line[3],
                        vendor_name=line[4],
                        notes=line[5],
                    )
                )
            _event(
                db,
                tenant_id=scope.tenant_id,
                request_id=row.id,
                claim_id=None,
                from_status=None,
                to_status=RequestStatus.DRAFT.value,
                actor_reference=actor_reference,
                occurred_at=recorded_at,
            )
            db.flush()
    except IntegrityError as exc:
        raise Conflict(
            f"expense request reference {reference!r} already exists"
        ) from exc
    db.refresh(row, attribute_names=["lines"])
    return row


def _request(
    db: Session, *, tenant_id: UUID, request_id: UUID, lock: bool = False
) -> ExpenseRequest:
    query = (
        select(ExpenseRequest)
        .where(
            ExpenseRequest.tenant_id == tenant_id,
            ExpenseRequest.id == request_id,
        )
        .options(selectinload(ExpenseRequest.lines))
    )
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise NotFound(f"expense request {request_id} was not found")
    return row


def revise_request(
    db: Session,
    *,
    scope: TenantScope,
    request_id: UUID,
    command: ReviseRequest,
) -> ExpenseRequest:
    request = _request(db, tenant_id=scope.tenant_id, request_id=request_id, lock=True)
    if request.status != RequestStatus.DRAFT:
        raise InvalidLifecycle("only a draft expense request can be revised")
    if not command.lines:
        raise InvalidCommand("an expense request needs at least one line")
    lines: list[tuple[UUID, str, Decimal, date, str | None, str | None]] = []
    for draft in command.lines:
        category = _category(
            db,
            tenant_id=scope.tenant_id,
            category_id=draft.category_id,
            active=True,
        )
        lines.append(
            (
                category.id,
                _text(draft.description, field="line description", maximum=500),
                _money(draft.amount, field="line amount"),
                draft.expected_on,
                _optional_text(draft.vendor_name, maximum=200),
                _optional_text(draft.notes, maximum=4000),
            )
        )

    # Delete before inserting the replacement sequence so the tenant/request/
    # sequence unique cannot see an old and new line zero in the same flush.
    request.lines.clear()
    db.flush()
    request.purpose = _text(command.purpose, field="request purpose", maximum=500)
    request.needed_by = command.needed_by
    request.notes = _optional_text(command.notes, maximum=4000)
    request.total_requested_amount = sum((item[2] for item in lines), Decimal("0"))
    for sequence, line in enumerate(lines):
        request.lines.append(
            ExpenseRequestLine(
                tenant_id=scope.tenant_id,
                category_id=line[0],
                sequence=sequence,
                description=line[1],
                amount=line[2],
                expected_on=line[3],
                vendor_name=line[4],
                notes=line[5],
            )
        )
    db.flush()
    return request


def create_claim(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateClaim,
    actor_reference: str,
    recorded_at: datetime,
) -> ExpenseClaim:
    if not command.lines:
        raise InvalidCommand("an expense claim needs at least one line")
    if not _party_exists(
        db, tenant_id=scope.tenant_id, party_id=command.claimant_party_id
    ):
        raise NotFound(f"claimant party {command.claimant_party_id} was not found")
    if (
        command.expense_period_start is not None
        and command.expense_period_end is not None
        and command.expense_period_end < command.expense_period_start
    ):
        raise InvalidCommand("expense period end cannot be before its start")
    if command.request_id is not None:
        request = _request(
            db, tenant_id=scope.tenant_id, request_id=command.request_id, lock=True
        )
        if request.status != RequestStatus.APPROVED:
            raise InvalidLifecycle("only an approved request can seed a claim")
        if db.scalar(
            select(ExpenseClaim.id).where(
                ExpenseClaim.tenant_id == scope.tenant_id,
                ExpenseClaim.request_id == request.id,
            )
        ):
            raise Conflict("approved request already has a claim")
    reference = _text(command.reference, field="claim reference", maximum=80)
    lines: list[tuple[UUID, str, Decimal, date, str | None, str | None]] = []
    for draft in command.lines:
        category = _category(
            db,
            tenant_id=scope.tenant_id,
            category_id=draft.category_id,
            active=True,
        )
        lines.append(
            (
                category.id,
                _text(draft.description, field="line description", maximum=500),
                _money(draft.claimed_amount, field="claimed_amount"),
                draft.expense_date,
                _optional_text(draft.vendor_name, maximum=200),
                _optional_text(draft.notes, maximum=4000),
            )
        )
    row = ExpenseClaim(
        tenant_id=scope.tenant_id,
        reference=reference,
        claimant_party_id=command.claimant_party_id,
        request_id=command.request_id,
        purpose=_text(command.purpose, field="claim purpose", maximum=500),
        claim_date=command.claim_date,
        expense_period_start=command.expense_period_start,
        expense_period_end=command.expense_period_end,
        currency_code=_currency(command.currency_code),
        notes=_optional_text(command.notes, maximum=4000),
        total_claimed_amount=sum((item[2] for item in lines), Decimal("0")),
        status=ClaimStatus.DRAFT,
    )
    try:
        with _conflict_scope(db):
            db.add(row)
            db.flush()
            for sequence, line in enumerate(lines):
                db.add(
                    ExpenseClaimLine(
                        tenant_id=scope.tenant_id,
                        claim_id=row.id,
                        category_id=line[0],
                        sequence=sequence,
                        description=line[1],
                        claimed_amount=line[2],
                        expense_date=line[3],
                        vendor_name=line[4],
                        notes=line[5],
                    )
                )
            _event(
                db,
                tenant_id=scope.tenant_id,
                request_id=None,
                claim_id=row.id,
                from_status=None,
                to_status=ClaimStatus.DRAFT.value,
                actor_reference=actor_reference,
                occurred_at=recorded_at,
            )
            db.flush()
    except IntegrityError as exc:
        raise Conflict(f"expense claim reference {reference!r} already exists") from exc
    db.refresh(row, attribute_names=["lines"])
    return row


def create_claim_from_request(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateClaimFromRequest,
    actor_reference: str,
    recorded_at: datetime,
) -> ExpenseClaim:
    request = _request(
        db, tenant_id=scope.tenant_id, request_id=command.request_id, lock=True
    )
    if db.scalar(
        select(ExpenseClaim.id).where(
            ExpenseClaim.tenant_id == scope.tenant_id,
            ExpenseClaim.request_id == request.id,
        )
    ):
        raise Conflict("approved request already has a claim")
    if request.status == RequestStatus.CONVERTED:
        raise Conflict("approved request already has a claim")
    if request.status != RequestStatus.APPROVED:
        raise InvalidLifecycle("only an approved request can seed a claim")
    claim = create_claim(
        db,
        scope=scope,
        command=CreateClaim(
            reference=command.reference,
            claimant_party_id=request.requester_party_id,
            purpose=request.purpose,
            claim_date=command.claim_date,
            currency_code=request.currency_code,
            request_id=request.id,
            notes=request.notes,
            lines=tuple(
                ClaimLineDraft(
                    category_id=line.category_id,
                    description=line.description,
                    claimed_amount=line.amount,
                    expense_date=line.expected_on,
                    vendor_name=line.vendor_name,
                    notes=line.notes,
                )
                for line in request.lines
            ),
        ),
        actor_reference=actor_reference,
        recorded_at=recorded_at,
    )
    old = request.status
    request.status = RequestStatus.CONVERTED
    request.converted_at = recorded_at
    _event(
        db,
        tenant_id=scope.tenant_id,
        request_id=request.id,
        claim_id=None,
        from_status=old.value,
        to_status=request.status.value,
        actor_reference=actor_reference,
        occurred_at=recorded_at,
        reason=f"converted to claim {claim.id}",
    )
    db.flush()
    return claim


def _claim(
    db: Session, *, tenant_id: UUID, claim_id: UUID, lock: bool = False
) -> ExpenseClaim:
    query = (
        select(ExpenseClaim)
        .where(
            ExpenseClaim.tenant_id == tenant_id,
            ExpenseClaim.id == claim_id,
        )
        .options(
            selectinload(ExpenseClaim.lines).selectinload(ExpenseClaimLine.receipts)
        )
    )
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise NotFound(f"expense claim {claim_id} was not found")
    return row


def revise_claim(
    db: Session,
    *,
    scope: TenantScope,
    claim_id: UUID,
    command: ReviseClaim,
) -> ExpenseClaim:
    claim = _claim(db, tenant_id=scope.tenant_id, claim_id=claim_id, lock=True)
    if claim.status != ClaimStatus.DRAFT:
        raise InvalidLifecycle("only a draft expense claim can be revised")
    if not command.lines:
        raise InvalidCommand("an expense claim needs at least one line")
    if (
        command.expense_period_start is not None
        and command.expense_period_end is not None
        and command.expense_period_end < command.expense_period_start
    ):
        raise InvalidCommand("expense period end cannot be before its start")
    lines: list[tuple[UUID, str, Decimal, date, str | None, str | None]] = []
    for draft in command.lines:
        category = _category(
            db,
            tenant_id=scope.tenant_id,
            category_id=draft.category_id,
            active=True,
        )
        lines.append(
            (
                category.id,
                _text(draft.description, field="line description", maximum=500),
                _money(draft.claimed_amount, field="claimed_amount"),
                draft.expense_date,
                _optional_text(draft.vendor_name, maximum=200),
                _optional_text(draft.notes, maximum=4000),
            )
        )

    # Receipts are claim-line evidence, so replacing a draft line removes its
    # draft-only receipt metadata with it. Submitted lines never reach here.
    claim.lines.clear()
    db.flush()
    claim.purpose = _text(command.purpose, field="claim purpose", maximum=500)
    claim.claim_date = command.claim_date
    claim.expense_period_start = command.expense_period_start
    claim.expense_period_end = command.expense_period_end
    claim.notes = _optional_text(command.notes, maximum=4000)
    claim.total_claimed_amount = sum((item[2] for item in lines), Decimal("0"))
    claim.total_approved_amount = None
    claim.evaluation_batch_id = None
    for sequence, line in enumerate(lines):
        claim.lines.append(
            ExpenseClaimLine(
                tenant_id=scope.tenant_id,
                category_id=line[0],
                sequence=sequence,
                description=line[1],
                claimed_amount=line[2],
                expense_date=line[3],
                vendor_name=line[4],
                notes=line[5],
            )
        )
    db.flush()
    return claim


def attach_receipt(
    db: Session,
    *,
    scope: TenantScope,
    command: AttachReceipt,
    actor_reference: str,
    recorded_at: datetime,
) -> ExpenseReceipt:
    line = db.scalar(
        select(ExpenseClaimLine)
        .where(
            ExpenseClaimLine.tenant_id == scope.tenant_id,
            ExpenseClaimLine.id == command.claim_line_id,
        )
        .with_for_update()
    )
    if line is None:
        raise NotFound(f"expense claim line {command.claim_line_id} was not found")
    claim = _claim(db, tenant_id=scope.tenant_id, claim_id=line.claim_id, lock=True)
    if claim.status != ClaimStatus.DRAFT:
        raise InvalidLifecycle("receipts can only be attached to a draft claim")
    sha256 = command.sha256.strip().lower()
    if _SHA256_RE.fullmatch(sha256) is None:
        raise InvalidCommand("sha256 must be a 64-character lowercase hex digest")
    if command.size_bytes <= 0:
        raise InvalidCommand("size_bytes must be positive")
    currency = _currency(command.currency_code) if command.currency_code else None
    amount = _optional_money(
        command.gross_amount, field="gross_amount", allow_zero=True
    )
    row = ExpenseReceipt(
        tenant_id=scope.tenant_id,
        claim_line_id=line.id,
        file_id=command.file_id,
        original_filename=_text(
            command.original_filename, field="original_filename", maximum=255
        ),
        media_type=_text(command.media_type, field="media_type", maximum=120),
        size_bytes=command.size_bytes,
        sha256=sha256,
        receipt_number=_optional_text(command.receipt_number, maximum=100),
        merchant_name=_optional_text(command.merchant_name, maximum=200),
        issued_on=command.issued_on,
        gross_amount=amount,
        currency_code=currency,
        verification_status=ReceiptVerificationStatus.PENDING,
    )
    try:
        with _conflict_scope(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(
            "that stored file is already attached to this claim line"
        ) from exc
    # The actor and time are accepted deliberately: the lifecycle event trail is
    # about status transitions, while receipt creation has its own immutable row.
    _text(actor_reference, field="actor_reference", maximum=255)
    if recorded_at.tzinfo is None:
        raise InvalidCommand("recorded_at must be timezone-aware")
    return row


def record_receipt_verification(
    db: Session,
    *,
    scope: TenantScope,
    receipt_id: UUID,
    status: ReceiptVerificationStatus,
    verification_reference: str,
    verified_at: datetime,
) -> ExpenseReceipt:
    row = db.scalar(
        select(ExpenseReceipt)
        .where(
            ExpenseReceipt.tenant_id == scope.tenant_id,
            ExpenseReceipt.id == receipt_id,
        )
        .with_for_update()
    )
    if row is None:
        raise NotFound(f"expense receipt {receipt_id} was not found")
    if row.verification_status != ReceiptVerificationStatus.PENDING:
        if row.verification_status == status and row.verification_reference == (
            verification_reference.strip()
        ):
            return row
        raise Conflict("receipt verification has already been recorded")
    if status == ReceiptVerificationStatus.PENDING:
        raise InvalidCommand("verification outcome must be verified or rejected")
    row.verification_status = status
    row.verification_reference = _text(
        verification_reference, field="verification_reference", maximum=255
    )
    row.verified_at = verified_at
    db.flush()
    return row


def _period_bounds(period: LimitPeriod, reference: date) -> tuple[date, date]:
    if period == LimitPeriod.DAY:
        return reference, reference
    if period == LimitPeriod.WEEK:
        start = reference - timedelta(days=reference.weekday())
        return start, start + timedelta(days=6)
    if period == LimitPeriod.MONTH:
        start = reference.replace(day=1)
        next_month = (
            date(reference.year + 1, 1, 1)
            if reference.month == 12
            else date(reference.year, reference.month + 1, 1)
        )
        return start, next_month - timedelta(days=1)
    if period == LimitPeriod.QUARTER:
        first_month = ((reference.month - 1) // 3) * 3 + 1
        start = date(reference.year, first_month, 1)
        if first_month == 10:
            next_quarter = date(reference.year + 1, 1, 1)
        else:
            next_quarter = date(reference.year, first_month + 3, 1)
        return start, next_quarter - timedelta(days=1)
    if period == LimitPeriod.YEAR:
        return date(reference.year, 1, 1), date(reference.year, 12, 31)
    return reference, reference


def _evaluation_result(action: LimitAction, exceeded: bool) -> EvaluationResult:
    if not exceeded:
        return EvaluationResult.PASSED
    return {
        LimitAction.WARN: EvaluationResult.WARNING,
        LimitAction.REQUIRE_APPROVAL: EvaluationResult.APPROVAL_REQUIRED,
        LimitAction.BLOCK: EvaluationResult.BLOCKED,
    }[action]


def _fingerprint(parts: dict[str, object]) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evaluation(
    db: Session,
    *,
    tenant_id: UUID,
    batch_id: UUID,
    policy_id: UUID | None,
    rule_id: UUID | None,
    request_id: UUID | None,
    request_line_id: UUID | None,
    claim_id: UUID | None,
    claim_line_id: UUID | None,
    result: EvaluationResult,
    reason_code: str,
    actual_amount: Decimal,
    limit_amount: Decimal | None,
    period_start: date | None,
    period_end: date | None,
    evaluated_at: datetime,
) -> ExpensePolicyEvaluation:
    fingerprint = _fingerprint(
        {
            "batch": batch_id,
            "policy": policy_id,
            "rule": rule_id,
            "request": request_id,
            "request_line": request_line_id,
            "claim": claim_id,
            "claim_line": claim_line_id,
            "result": result.value,
            "reason": reason_code,
            "actual": str(actual_amount),
            "limit": str(limit_amount) if limit_amount is not None else None,
            "start": period_start,
            "end": period_end,
        }
    )
    row = ExpensePolicyEvaluation(
        tenant_id=tenant_id,
        batch_id=batch_id,
        policy_id=policy_id,
        rule_id=rule_id,
        request_id=request_id,
        request_line_id=request_line_id,
        claim_id=claim_id,
        claim_line_id=claim_line_id,
        result=result,
        reason_code=reason_code,
        actual_amount=actual_amount,
        limit_amount=limit_amount,
        period_start=period_start,
        period_end=period_end,
        fingerprint=fingerprint,
        evaluated_at=evaluated_at,
    )
    db.add(row)
    return row


def _active_rules(
    db: Session,
    *,
    tenant_id: UUID,
    currency_code: str,
    on_date: date,
    target: PolicyTarget,
    context: PolicyContext,
) -> list[tuple[ExpensePolicy, ExpensePolicyRule]]:
    normalized_keys = {
        _applicability_key(value) for value in context.applicability_keys
    }
    normalized_keys.discard(None)
    rules = list(
        db.execute(
            select(ExpensePolicy, ExpensePolicyRule)
            .join(
                ExpensePolicyRule,
                and_(
                    ExpensePolicyRule.tenant_id == ExpensePolicy.tenant_id,
                    ExpensePolicyRule.policy_id == ExpensePolicy.id,
                ),
            )
            .where(
                ExpensePolicy.tenant_id == tenant_id,
                ExpensePolicy.status == PolicyStatus.PUBLISHED,
                ExpensePolicy.currency_code == currency_code,
                ExpensePolicy.effective_from <= on_date,
                or_(
                    ExpensePolicy.effective_to.is_(None),
                    ExpensePolicy.effective_to >= on_date,
                ),
                ExpensePolicyRule.target == target,
            )
            .order_by(ExpensePolicyRule.priority, ExpensePolicyRule.code)
        )
    )
    return [
        (policy, rule)
        for policy, rule in rules
        if rule.applicability_key is None or rule.applicability_key in normalized_keys
    ]


def _historical_usage(
    db: Session,
    *,
    tenant_id: UUID,
    target: PolicyTarget,
    party_id: UUID,
    category_id: UUID | None,
    start: date,
    end: date,
    exclude_id: UUID,
) -> Decimal:
    if target == PolicyTarget.CLAIM:
        statuses = (ClaimStatus.SUBMITTED, ClaimStatus.APPROVED)
        if category_id is None:
            value = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)
                ).where(
                    ExpenseClaim.tenant_id == tenant_id,
                    ExpenseClaim.claimant_party_id == party_id,
                    ExpenseClaim.id != exclude_id,
                    ExpenseClaim.status.in_(statuses),
                    ExpenseClaim.claim_date >= start,
                    ExpenseClaim.claim_date <= end,
                )
            )
        else:
            value = db.scalar(
                select(func.coalesce(func.sum(ExpenseClaimLine.claimed_amount), 0))
                .join(
                    ExpenseClaim,
                    and_(
                        ExpenseClaim.tenant_id == ExpenseClaimLine.tenant_id,
                        ExpenseClaim.id == ExpenseClaimLine.claim_id,
                    ),
                )
                .where(
                    ExpenseClaim.tenant_id == tenant_id,
                    ExpenseClaim.claimant_party_id == party_id,
                    ExpenseClaim.id != exclude_id,
                    ExpenseClaim.status.in_(statuses),
                    ExpenseClaim.claim_date >= start,
                    ExpenseClaim.claim_date <= end,
                    ExpenseClaimLine.category_id == category_id,
                )
            )
    else:
        request_statuses = (
            RequestStatus.SUBMITTED,
            RequestStatus.APPROVED,
            RequestStatus.CONVERTED,
        )
        if category_id is None:
            value = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseRequest.total_requested_amount), 0)
                ).where(
                    ExpenseRequest.tenant_id == tenant_id,
                    ExpenseRequest.requester_party_id == party_id,
                    ExpenseRequest.id != exclude_id,
                    ExpenseRequest.status.in_(request_statuses),
                    ExpenseRequest.needed_by >= start,
                    ExpenseRequest.needed_by <= end,
                )
            )
        else:
            value = db.scalar(
                select(func.coalesce(func.sum(ExpenseRequestLine.amount), 0))
                .join(
                    ExpenseRequest,
                    and_(
                        ExpenseRequest.tenant_id == ExpenseRequestLine.tenant_id,
                        ExpenseRequest.id == ExpenseRequestLine.request_id,
                    ),
                )
                .where(
                    ExpenseRequest.tenant_id == tenant_id,
                    ExpenseRequest.requester_party_id == party_id,
                    ExpenseRequest.id != exclude_id,
                    ExpenseRequest.status.in_(request_statuses),
                    ExpenseRequest.needed_by >= start,
                    ExpenseRequest.needed_by <= end,
                    ExpenseRequestLine.category_id == category_id,
                )
            )
    return Decimal(value or 0).quantize(_MONEY_QUANTUM)


def _evaluate_subject(
    db: Session,
    *,
    tenant_id: UUID,
    target: PolicyTarget,
    subject: ExpenseRequest | ExpenseClaim,
    context: PolicyContext,
    batch_id: UUID,
    evaluated_at: datetime,
) -> list[ExpensePolicyEvaluation]:
    subject_id = subject.id
    currency_code = subject.currency_code
    if isinstance(subject, ExpenseClaim):
        if target != PolicyTarget.CLAIM:
            raise InvalidCommand("claim evaluation requires the claim policy target")
        reference_date = subject.claim_date
        party_id = subject.claimant_party_id
        total = subject.total_claimed_amount
        request_id = None
        claim_id = subject.id
        lines = tuple(
            _EvaluationLine(
                id=line.id,
                category_id=line.category_id,
                amount=line.claimed_amount,
                occurred_on=line.expense_date,
                has_usable_receipt=any(
                    receipt.verification_status != ReceiptVerificationStatus.REJECTED
                    for receipt in line.receipts
                ),
            )
            for line in subject.lines
        )
    else:
        if target != PolicyTarget.REQUEST:
            raise InvalidCommand(
                "request evaluation requires the request policy target"
            )
        reference_date = subject.needed_by
        party_id = subject.requester_party_id
        total = subject.total_requested_amount
        request_id = subject.id
        claim_id = None
        lines = tuple(
            _EvaluationLine(
                id=line.id,
                category_id=line.category_id,
                amount=line.amount,
                occurred_on=line.expected_on,
                has_usable_receipt=False,
            )
            for line in subject.lines
        )
    evaluations: list[ExpensePolicyEvaluation] = []

    for policy, rule in _active_rules(
        db,
        tenant_id=tenant_id,
        currency_code=currency_code,
        on_date=reference_date,
        target=target,
        context=context,
    ):
        matching = [
            line
            for line in lines
            if rule.category_id is None or line.category_id == rule.category_id
        ]
        if rule.category_id is not None and not matching:
            continue
        if rule.period == LimitPeriod.TRANSACTION and rule.category_id is not None:
            observations: list[tuple[Decimal, UUID | None]] = [
                (line.amount, line.id) for line in matching
            ]
            start = end = reference_date
        else:
            current = (
                total
                if rule.category_id is None
                else sum(
                    (line.amount for line in matching),
                    Decimal("0"),
                )
            )
            start, end = _period_bounds(rule.period, reference_date)
            if rule.period != LimitPeriod.TRANSACTION:
                current += _historical_usage(
                    db,
                    tenant_id=tenant_id,
                    target=target,
                    party_id=party_id,
                    category_id=rule.category_id,
                    start=start,
                    end=end,
                    exclude_id=subject_id,
                )
            observations = [(current, None)]
        for actual, line_id in observations:
            result = _evaluation_result(rule.action, actual > rule.limit_amount)
            evaluations.append(
                _evaluation(
                    db,
                    tenant_id=tenant_id,
                    batch_id=batch_id,
                    policy_id=policy.id,
                    rule_id=rule.id,
                    request_id=request_id,
                    request_line_id=line_id if request_id is not None else None,
                    claim_id=claim_id,
                    claim_line_id=line_id if claim_id is not None else None,
                    result=result,
                    reason_code=f"policy.{rule.code.lower()}",
                    actual_amount=actual,
                    limit_amount=rule.limit_amount,
                    period_start=start,
                    period_end=end,
                    evaluated_at=evaluated_at,
                )
            )

    categories = {
        row.id: row
        for row in db.scalars(
            select(ExpenseCategory).where(
                ExpenseCategory.tenant_id == tenant_id,
                ExpenseCategory.id.in_({line.category_id for line in lines}),
            )
        )
    }
    for line in lines:
        category = categories[line.category_id]
        amount = line.amount
        if category.max_amount_per_line is not None:
            result = (
                EvaluationResult.BLOCKED
                if amount > category.max_amount_per_line
                else EvaluationResult.PASSED
            )
            evaluations.append(
                _evaluation(
                    db,
                    tenant_id=tenant_id,
                    batch_id=batch_id,
                    policy_id=None,
                    rule_id=None,
                    request_id=request_id,
                    request_line_id=line.id if request_id is not None else None,
                    claim_id=claim_id,
                    claim_line_id=line.id if claim_id is not None else None,
                    result=result,
                    reason_code="category.line_limit",
                    actual_amount=amount,
                    limit_amount=category.max_amount_per_line,
                    period_start=reference_date,
                    period_end=reference_date,
                    evaluated_at=evaluated_at,
                )
            )
    for category_id, category in categories.items():
        if category.max_amount_per_claim is None:
            continue
        amount = sum(
            (line.amount for line in lines if line.category_id == category_id),
            Decimal("0"),
        )
        result = (
            EvaluationResult.BLOCKED
            if amount > category.max_amount_per_claim
            else EvaluationResult.PASSED
        )
        evaluations.append(
            _evaluation(
                db,
                tenant_id=tenant_id,
                batch_id=batch_id,
                policy_id=None,
                rule_id=None,
                request_id=request_id,
                request_line_id=None,
                claim_id=claim_id,
                claim_line_id=None,
                result=result,
                reason_code="category.claim_limit",
                actual_amount=amount,
                limit_amount=category.max_amount_per_claim,
                period_start=reference_date,
                period_end=reference_date,
                evaluated_at=evaluated_at,
            )
        )
    if claim_id is not None:
        for line in lines:
            category = categories[line.category_id]
            requires = category.requires_receipt and (
                category.receipt_threshold is None
                or line.amount >= category.receipt_threshold
            )
            if requires and not line.has_usable_receipt:
                evaluations.append(
                    _evaluation(
                        db,
                        tenant_id=tenant_id,
                        batch_id=batch_id,
                        policy_id=None,
                        rule_id=None,
                        request_id=None,
                        request_line_id=None,
                        claim_id=claim_id,
                        claim_line_id=line.id,
                        result=EvaluationResult.BLOCKED,
                        reason_code="receipt.required",
                        actual_amount=line.amount,
                        limit_amount=category.receipt_threshold,
                        period_start=line.occurred_on,
                        period_end=line.occurred_on,
                        evaluated_at=evaluated_at,
                    )
                )
    db.flush()
    return evaluations


def _finding(row: ExpensePolicyEvaluation) -> EvaluationFinding:
    return EvaluationFinding(
        evaluation_id=row.id,
        result=row.result,
        reason_code=row.reason_code,
        actual_amount=row.actual_amount,
        limit_amount=row.limit_amount,
        rule_id=row.rule_id,
        line_id=row.claim_line_id or row.request_line_id,
    )


def submit_request(
    db: Session,
    *,
    scope: TenantScope,
    request_id: UUID,
    context: PolicyContext,
    actor_reference: str,
    submitted_at: datetime,
) -> SubmissionOutcome:
    request = _request(db, tenant_id=scope.tenant_id, request_id=request_id, lock=True)
    if request.status != RequestStatus.DRAFT:
        raise InvalidLifecycle("only a draft expense request can be submitted")
    batch_id = uuid.uuid4()
    evaluations = _evaluate_subject(
        db,
        tenant_id=scope.tenant_id,
        target=PolicyTarget.REQUEST,
        subject=request,
        context=context,
        batch_id=batch_id,
        evaluated_at=submitted_at,
    )
    request.evaluation_batch_id = batch_id
    blocked = any(row.result == EvaluationResult.BLOCKED for row in evaluations)
    approval_required = any(
        row.result == EvaluationResult.APPROVAL_REQUIRED for row in evaluations
    )
    if not blocked:
        old = request.status
        request.status = RequestStatus.SUBMITTED
        request.submitted_at = submitted_at
        _event(
            db,
            tenant_id=scope.tenant_id,
            request_id=request.id,
            claim_id=None,
            from_status=old.value,
            to_status=request.status.value,
            actor_reference=actor_reference,
            occurred_at=submitted_at,
        )
    db.flush()
    return SubmissionOutcome(
        subject_id=request.id,
        status=request.status.value,
        evaluation_batch_id=batch_id,
        blocked=blocked,
        approval_required=approval_required,
        evaluations=tuple(_finding(row) for row in evaluations),
    )


def submit_claim(
    db: Session,
    *,
    scope: TenantScope,
    claim_id: UUID,
    context: PolicyContext,
    actor_reference: str,
    submitted_at: datetime,
) -> SubmissionOutcome:
    claim = _claim(db, tenant_id=scope.tenant_id, claim_id=claim_id, lock=True)
    if claim.status != ClaimStatus.DRAFT:
        raise InvalidLifecycle("only a draft expense claim can be submitted")
    batch_id = uuid.uuid4()
    evaluations = _evaluate_subject(
        db,
        tenant_id=scope.tenant_id,
        target=PolicyTarget.CLAIM,
        subject=claim,
        context=context,
        batch_id=batch_id,
        evaluated_at=submitted_at,
    )
    claim.evaluation_batch_id = batch_id
    blocked = any(row.result == EvaluationResult.BLOCKED for row in evaluations)
    approval_required = any(
        row.result == EvaluationResult.APPROVAL_REQUIRED for row in evaluations
    )
    if not blocked:
        old = claim.status
        claim.status = ClaimStatus.SUBMITTED
        claim.submitted_at = submitted_at
        _event(
            db,
            tenant_id=scope.tenant_id,
            request_id=None,
            claim_id=claim.id,
            from_status=old.value,
            to_status=claim.status.value,
            actor_reference=actor_reference,
            occurred_at=submitted_at,
        )
    db.flush()
    return SubmissionOutcome(
        subject_id=claim.id,
        status=claim.status.value,
        evaluation_batch_id=batch_id,
        blocked=blocked,
        approval_required=approval_required,
        evaluations=tuple(_finding(row) for row in evaluations),
    )


def apply_request_decision(
    db: Session,
    *,
    scope: TenantScope,
    request_id: UUID,
    command: ApplyDecision,
    actor_reference: str,
    decided_at: datetime,
) -> ExpenseRequest:
    request = _request(db, tenant_id=scope.tenant_id, request_id=request_id, lock=True)
    target = (
        RequestStatus.APPROVED
        if command.decision == Decision.APPROVED
        else RequestStatus.REJECTED
    )
    decision_reference = _text(
        command.decision_reference, field="decision_reference", maximum=255
    )
    if request.status == target and request.decision_reference == decision_reference:
        return request
    if request.status != RequestStatus.SUBMITTED:
        raise InvalidLifecycle("only a submitted expense request can be decided")
    if command.decision == Decision.REJECTED and not _optional_text(
        command.reason, maximum=4000
    ):
        raise InvalidCommand("a rejected request needs a reason")
    old = request.status
    request.status = target
    request.decision_reference = decision_reference
    request.decision_reason = _optional_text(command.reason, maximum=4000)
    request.decided_at = decided_at
    _event(
        db,
        tenant_id=scope.tenant_id,
        request_id=request.id,
        claim_id=None,
        from_status=old.value,
        to_status=target.value,
        actor_reference=actor_reference,
        occurred_at=decided_at,
        decision_reference=decision_reference,
        reason=command.reason,
    )
    db.flush()
    return request


def apply_claim_decision(
    db: Session,
    *,
    scope: TenantScope,
    claim_id: UUID,
    command: ApplyDecision,
    actor_reference: str,
    decided_at: datetime,
) -> ExpenseClaim:
    claim = _claim(db, tenant_id=scope.tenant_id, claim_id=claim_id, lock=True)
    target = (
        ClaimStatus.APPROVED
        if command.decision == Decision.APPROVED
        else ClaimStatus.REJECTED
    )
    decision_reference = _text(
        command.decision_reference, field="decision_reference", maximum=255
    )
    if claim.status == target and claim.decision_reference == decision_reference:
        return claim
    if claim.status != ClaimStatus.SUBMITTED:
        raise InvalidLifecycle("only a submitted expense claim can be decided")
    if command.decision == Decision.REJECTED and not _optional_text(
        command.reason, maximum=4000
    ):
        raise InvalidCommand("a rejected claim needs a reason")
    if command.decision == Decision.APPROVED:
        supplied = {item.line_id: item.amount for item in command.approved_lines}
        if len(supplied) != len(command.approved_lines):
            raise InvalidCommand("approved line ids must be unique")
        line_ids = {line.id for line in claim.lines}
        unknown = supplied.keys() - line_ids
        if unknown:
            raise NotFound(f"approved line {next(iter(unknown))} was not found")
        total = Decimal("0")
        for line in claim.lines:
            approved = _money(
                supplied.get(line.id, line.claimed_amount),
                field="approved amount",
                allow_zero=True,
            )
            if approved > line.claimed_amount:
                raise InvalidCommand("approved amount cannot exceed claimed amount")
            line.approved_amount = approved
            total += approved
        claim.total_approved_amount = total
        # The database allows this one projection while the parent is still
        # submitted. Flush it before advancing the guarded parent transition.
        db.flush()
    else:
        for line in claim.lines:
            line.approved_amount = None
        claim.total_approved_amount = None
    old = claim.status
    claim.status = target
    claim.decision_reference = decision_reference
    claim.decision_reason = _optional_text(command.reason, maximum=4000)
    claim.decided_at = decided_at
    _event(
        db,
        tenant_id=scope.tenant_id,
        request_id=None,
        claim_id=claim.id,
        from_status=old.value,
        to_status=target.value,
        actor_reference=actor_reference,
        occurred_at=decided_at,
        decision_reference=decision_reference,
        reason=command.reason,
    )
    db.flush()
    return claim


def resubmit_claim(
    db: Session,
    *,
    scope: TenantScope,
    claim_id: UUID,
    actor_reference: str,
    recorded_at: datetime,
) -> ExpenseClaim:
    claim = _claim(db, tenant_id=scope.tenant_id, claim_id=claim_id, lock=True)
    if claim.status != ClaimStatus.REJECTED:
        raise InvalidLifecycle("only a rejected claim can return to draft")
    old = claim.status
    claim.status = ClaimStatus.DRAFT
    claim.decision_reference = None
    claim.decision_reason = None
    claim.decided_at = None
    claim.total_approved_amount = None
    claim.evaluation_batch_id = None
    for line in claim.lines:
        line.approved_amount = None
    _event(
        db,
        tenant_id=scope.tenant_id,
        request_id=None,
        claim_id=claim.id,
        from_status=old.value,
        to_status=claim.status.value,
        actor_reference=actor_reference,
        occurred_at=recorded_at,
        reason="claim reopened for revision",
    )
    db.flush()
    return claim


def cancel_request(
    db: Session,
    *,
    scope: TenantScope,
    request_id: UUID,
    actor_reference: str,
    cancelled_at: datetime,
    reason: str | None = None,
) -> ExpenseRequest:
    request = _request(db, tenant_id=scope.tenant_id, request_id=request_id, lock=True)
    if request.status == RequestStatus.CANCELLED:
        return request
    if request.status not in {RequestStatus.DRAFT, RequestStatus.SUBMITTED}:
        raise InvalidLifecycle("only a draft or submitted request can be cancelled")
    if request.claims:
        raise InvalidLifecycle("a request with a claim cannot be cancelled")
    old = request.status
    request.status = RequestStatus.CANCELLED
    request.cancelled_at = cancelled_at
    _event(
        db,
        tenant_id=scope.tenant_id,
        request_id=request.id,
        claim_id=None,
        from_status=old.value,
        to_status=request.status.value,
        actor_reference=actor_reference,
        occurred_at=cancelled_at,
        reason=reason,
    )
    db.flush()
    return request


def cancel_claim(
    db: Session,
    *,
    scope: TenantScope,
    claim_id: UUID,
    actor_reference: str,
    cancelled_at: datetime,
    reason: str | None = None,
) -> ExpenseClaim:
    claim = _claim(db, tenant_id=scope.tenant_id, claim_id=claim_id, lock=True)
    if claim.status == ClaimStatus.CANCELLED:
        return claim
    if claim.status not in {ClaimStatus.DRAFT, ClaimStatus.SUBMITTED}:
        raise InvalidLifecycle("only a draft or submitted claim can be cancelled")
    old = claim.status
    claim.status = ClaimStatus.CANCELLED
    claim.cancelled_at = cancelled_at
    _event(
        db,
        tenant_id=scope.tenant_id,
        request_id=None,
        claim_id=claim.id,
        from_status=old.value,
        to_status=claim.status.value,
        actor_reference=actor_reference,
        occurred_at=cancelled_at,
        reason=reason,
    )
    db.flush()
    return claim


def withdraw_claim_approval(
    db: Session,
    *,
    scope: TenantScope,
    claim_id: UUID,
    actor_reference: str,
    decision_reference: str,
    withdrawn_at: datetime,
    reason: str,
) -> ExpenseClaim:
    claim = _claim(db, tenant_id=scope.tenant_id, claim_id=claim_id, lock=True)
    if claim.status == ClaimStatus.APPROVAL_WITHDRAWN:
        return claim
    if claim.status != ClaimStatus.APPROVED:
        raise InvalidLifecycle("only an approved claim can have approval withdrawn")
    old = claim.status
    claim.status = ClaimStatus.APPROVAL_WITHDRAWN
    claim.decision_reference = _text(
        decision_reference, field="decision_reference", maximum=255
    )
    claim.decision_reason = _text(reason, field="withdrawal reason", maximum=4000)
    claim.decided_at = withdrawn_at
    _event(
        db,
        tenant_id=scope.tenant_id,
        request_id=None,
        claim_id=claim.id,
        from_status=old.value,
        to_status=claim.status.value,
        actor_reference=actor_reference,
        occurred_at=withdrawn_at,
        decision_reference=decision_reference,
        reason=reason,
    )
    db.flush()
    return claim


def reimbursement_eligibility(
    db: Session, *, scope: TenantScope, claim_id: UUID
) -> ReimbursementEligibility:
    claim = _claim(db, tenant_id=scope.tenant_id, claim_id=claim_id)
    reasons: list[str] = []
    if claim.status != ClaimStatus.APPROVED:
        reasons.append("claim.not_approved")
    approved = claim.total_approved_amount or Decimal("0")
    if approved <= 0:
        reasons.append("claim.no_approved_amount")
    categories = {
        row.id: row
        for row in db.scalars(
            select(ExpenseCategory).where(
                ExpenseCategory.tenant_id == scope.tenant_id,
                ExpenseCategory.id.in_({line.category_id for line in claim.lines}),
            )
        )
    }
    for line in claim.lines:
        category = categories[line.category_id]
        requires = category.requires_receipt and (
            category.receipt_threshold is None
            or line.claimed_amount >= category.receipt_threshold
        )
        if requires and not any(
            receipt.verification_status != ReceiptVerificationStatus.REJECTED
            for receipt in line.receipts
        ):
            reasons.append(f"receipt.missing:{line.id}")
    if claim.evaluation_batch_id is None:
        reasons.append("policy.not_evaluated")
    else:
        blocked = db.scalar(
            select(func.count())
            .select_from(ExpensePolicyEvaluation)
            .where(
                ExpensePolicyEvaluation.tenant_id == scope.tenant_id,
                ExpensePolicyEvaluation.batch_id == claim.evaluation_batch_id,
                ExpensePolicyEvaluation.result == EvaluationResult.BLOCKED,
            )
        )
        if blocked:
            reasons.append("policy.blocked")
    return ReimbursementEligibility(
        claim_id=claim.id,
        eligible=not reasons,
        reasons=tuple(reasons),
        approved_amount=approved,
        currency_code=claim.currency_code,
        decision_reference=claim.decision_reference,
        evaluation_batch_id=claim.evaluation_batch_id,
    )


__all__ = [
    "add_policy_rule",
    "apply_claim_decision",
    "apply_request_decision",
    "attach_receipt",
    "cancel_claim",
    "cancel_request",
    "create_category",
    "create_claim",
    "create_claim_from_request",
    "create_policy_revision",
    "create_request",
    "publish_policy",
    "record_receipt_verification",
    "reimbursement_eligibility",
    "revise_claim",
    "revise_request",
    "resubmit_claim",
    "retire_policy",
    "submit_claim",
    "submit_request",
    "withdraw_claim_approval",
]
