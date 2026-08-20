"""The single writer for reseller hierarchy, authority and lifecycle."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging import enqueue_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_reseller_management.contracts import (
    BindCustomerAccount,
    BindMember,
    ChangeStatus,
    ContractError,
    CreateResellerAccount,
    PublishAuthority,
    SetParent,
)
from dotmac_reseller_management.models import (
    ResellerAccount,
    ResellerAuthorityRevision,
    ResellerCustomerAccountBinding,
    ResellerMemberBinding,
)

STATUS_CHANGED_EVENT = "reseller.account.status-changed.v1"


class ResellerManagementError(ValueError):
    """A reseller command cannot be admitted."""


class NotFound(ResellerManagementError):
    """A tenant-local reseller subject does not exist."""


class Conflict(ResellerManagementError):
    """A stable identity or hierarchy invariant would be violated."""


class InvalidTransition(ResellerManagementError):
    """A reseller lifecycle transition is not allowed."""


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{name} must be timezone-aware")


def _account(db: Session, scope: TenantScope, account_id: UUID) -> ResellerAccount:
    row = db.scalar(
        select(ResellerAccount).where(
            ResellerAccount.tenant_id == scope.tenant_id,
            ResellerAccount.id == account_id,
        )
    )
    if row is None:
        raise NotFound("reseller account was not found in the tenant")
    return row


def _eligible_account(
    db: Session, scope: TenantScope, account_id: UUID
) -> ResellerAccount:
    row = _account(db, scope, account_id)
    if row.status != "active":
        raise InvalidTransition("only an active reseller account accepts bindings")
    return row


def create_account(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateResellerAccount,
    recorded_at: datetime,
) -> ResellerAccount:
    _aware("recorded_at", recorded_at)
    existing = db.scalar(
        select(ResellerAccount).where(
            ResellerAccount.tenant_id == scope.tenant_id,
            (ResellerAccount.code == command.code)
            | (ResellerAccount.party_role_ref == command.party_role_ref),
        )
    )
    if existing is not None:
        raise Conflict("reseller code or Party-role reference is already bound")
    if command.parent_account_id is not None:
        try:
            parent = _account(db, scope, command.parent_account_id)
        except NotFound as exc:
            raise Conflict("parent reseller must exist in the same tenant") from exc
        if parent.status != "active":
            raise Conflict("parent reseller must be active")
    row = ResellerAccount(
        tenant_id=scope.tenant_id,
        code=command.code,
        name=command.name,
        party_role_ref=command.party_role_ref,
        parent_account_id=command.parent_account_id,
        status="active",
        created_at=recorded_at,
        updated_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def set_parent(
    db: Session,
    *,
    scope: TenantScope,
    command: SetParent,
    recorded_at: datetime,
) -> ResellerAccount:
    _aware("recorded_at", recorded_at)
    account = _account(db, scope, command.account_id)
    if account.status == "retired":
        raise InvalidTransition("a retired reseller has a terminal hierarchy")
    parent_id = command.parent_account_id
    if parent_id is not None:
        try:
            parent = _account(db, scope, parent_id)
        except NotFound as exc:
            raise Conflict("parent reseller must exist in the same tenant") from exc
        if parent.status == "retired":
            raise Conflict("a retired reseller cannot become a parent")
        cursor: ResellerAccount | None = parent
        visited: set[UUID] = set()
        while cursor is not None:
            if cursor.id == account.id or cursor.id in visited:
                raise Conflict("reseller hierarchy would contain a cycle")
            visited.add(cursor.id)
            if cursor.parent_account_id is None:
                cursor = None
            else:
                cursor = _account(db, scope, cursor.parent_account_id)
    account.parent_account_id = parent_id
    account.updated_at = recorded_at
    db.flush()
    return account


def publish_authority(
    db: Session,
    *,
    scope: TenantScope,
    command: PublishAuthority,
    recorded_at: datetime,
) -> ResellerAuthorityRevision:
    _aware("recorded_at", recorded_at)
    account = _eligible_account(db, scope, command.account_id)
    replay = db.scalar(
        select(ResellerAuthorityRevision).where(
            ResellerAuthorityRevision.tenant_id == scope.tenant_id,
            ResellerAuthorityRevision.account_id == account.id,
            ResellerAuthorityRevision.evidence_ref == command.evidence_ref,
        )
    )
    codes = list(command.authority_codes)
    if replay is not None:
        if replay.authority_codes != codes:
            raise Conflict("authority evidence was replayed with different codes")
        return replay
    if account.parent_account_id is not None:
        parent = _account(db, scope, account.parent_account_id)
        parent_revision = (
            _authority_revision(db, scope, parent.current_authority_revision_id)
            if parent.current_authority_revision_id is not None
            else None
        )
        allowed = set(parent_revision.authority_codes if parent_revision else ())
        if not set(codes).issubset(allowed):
            raise Conflict("child authority must be a subset of parent authority")
    current_number = db.scalar(
        select(func.max(ResellerAuthorityRevision.version_number)).where(
            ResellerAuthorityRevision.tenant_id == scope.tenant_id,
            ResellerAuthorityRevision.account_id == account.id,
        )
    )
    row = ResellerAuthorityRevision(
        tenant_id=scope.tenant_id,
        account_id=account.id,
        version_number=(current_number or 0) + 1,
        authority_codes=codes,
        evidence_ref=command.evidence_ref,
        frozen_at=recorded_at,
    )
    db.add(row)
    db.flush()
    account.current_authority_revision_id = row.id
    account.updated_at = recorded_at
    db.flush()
    return row


def _authority_revision(
    db: Session, scope: TenantScope, revision_id: UUID
) -> ResellerAuthorityRevision:
    row = db.scalar(
        select(ResellerAuthorityRevision).where(
            ResellerAuthorityRevision.tenant_id == scope.tenant_id,
            ResellerAuthorityRevision.id == revision_id,
        )
    )
    if row is None:
        raise Conflict("current reseller authority revision is missing")
    return row


def bind_member(
    db: Session,
    *,
    scope: TenantScope,
    command: BindMember,
    recorded_at: datetime,
) -> ResellerMemberBinding:
    _aware("recorded_at", recorded_at)
    account = _eligible_account(db, scope, command.account_id)
    existing = db.scalar(
        select(ResellerMemberBinding).where(
            ResellerMemberBinding.tenant_id == scope.tenant_id,
            ResellerMemberBinding.member_ref == command.member_ref,
        )
    )
    if existing is not None:
        if existing.account_id != account.id:
            raise Conflict("member reference is already bound to another reseller")
        return existing
    row = ResellerMemberBinding(
        tenant_id=scope.tenant_id,
        account_id=account.id,
        member_ref=command.member_ref,
        evidence_ref=command.evidence_ref,
        bound_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def bind_customer_account(
    db: Session,
    *,
    scope: TenantScope,
    command: BindCustomerAccount,
    recorded_at: datetime,
) -> ResellerCustomerAccountBinding:
    _aware("recorded_at", recorded_at)
    account = _eligible_account(db, scope, command.account_id)
    existing = db.scalar(
        select(ResellerCustomerAccountBinding).where(
            ResellerCustomerAccountBinding.tenant_id == scope.tenant_id,
            ResellerCustomerAccountBinding.customer_account_ref
            == command.customer_account_ref,
        )
    )
    if existing is not None:
        if existing.account_id != account.id:
            raise Conflict("customer account is already bound to another reseller")
        return existing
    row = ResellerCustomerAccountBinding(
        tenant_id=scope.tenant_id,
        account_id=account.id,
        customer_account_ref=command.customer_account_ref,
        evidence_ref=command.evidence_ref,
        bound_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def transition_account(
    db: Session,
    *,
    scope: TenantScope,
    command: ChangeStatus,
    recorded_at: datetime,
) -> ResellerAccount:
    _aware("recorded_at", recorded_at)
    account = _account(db, scope, command.account_id)
    previous = account.status
    if previous == command.target_status:
        return account
    allowed = {
        "active": {"suspended", "retired"},
        "suspended": {"active", "retired"},
        "retired": set(),
    }
    if command.target_status not in allowed[previous]:
        suffix = "; retired is terminal" if previous == "retired" else ""
        raise InvalidTransition(
            f"cannot transition reseller from {previous} to "
            f"{command.target_status}{suffix}"
        )
    account.status = command.target_status
    account.updated_at = recorded_at
    db.flush()
    enqueue_event(
        db,
        tenant_id=scope.tenant_id,
        event_type=STATUS_CHANGED_EVENT,
        correlation_id=str(account.id),
        payload={
            "contract": STATUS_CHANGED_EVENT,
            "reseller_account_id": str(account.id),
            "party_role_ref": account.party_role_ref,
            "previous_status": previous,
            "current_status": command.target_status,
            "evidence_ref": command.evidence_ref,
        },
    )
    db.flush()
    return account


__all__ = [
    "Conflict",
    "InvalidTransition",
    "NotFound",
    "ResellerManagementError",
    "STATUS_CHANGED_EVENT",
    "bind_customer_account",
    "bind_member",
    "create_account",
    "publish_authority",
    "set_parent",
    "transition_account",
]
