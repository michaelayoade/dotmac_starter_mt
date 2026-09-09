"""Tenant customer-account decisions; callers own the transaction."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_customers.contracts import (
    AccountStatus,
    Conflict,
    CreateCustomerAccount,
    LinkPartyReference,
    NotFound,
    SetCustomerProfile,
)
from dotmac_customers.models import (
    CustomerAccount,
    CustomerPartyReference,
    CustomerProfile,
)

_TRANSITIONS = {
    AccountStatus.PROSPECT: frozenset({AccountStatus.ACTIVE, AccountStatus.CLOSED}),
    AccountStatus.ACTIVE: frozenset({AccountStatus.SUSPENDED, AccountStatus.CLOSED}),
    AccountStatus.SUSPENDED: frozenset({AccountStatus.ACTIVE, AccountStatus.CLOSED}),
    AccountStatus.CLOSED: frozenset(),
}


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-customers requires an explicit TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def get_account(
    db: Session,
    *,
    scope: TenantScope,
    account_id: UUID | None = None,
    account_number: str | None = None,
    required: bool = True,
) -> CustomerAccount | None:
    if (account_id is None) == (account_number is None):
        raise ValueError("provide exactly one account identifier")
    statement = select(CustomerAccount).where(
        CustomerAccount.tenant_id == _tenant(scope)
    )
    if account_id is not None:
        statement = statement.where(CustomerAccount.id == account_id)
    else:
        statement = statement.where(
            CustomerAccount.account_number
            == _required(account_number or "", "account number").upper()
        )
    account = db.scalar(statement)
    if account is None and required:
        raise NotFound("customer account was not found in the tenant")
    return account


def create_account(
    db: Session, *, scope: TenantScope, command: CreateCustomerAccount
) -> CustomerAccount:
    from dotmac_kernel.db import conflict_savepoint

    tenant_id = _tenant(scope)
    number = _required(command.account_number, "account number").upper()
    if db.scalar(
        select(CustomerAccount.id).where(
            CustomerAccount.tenant_id == tenant_id,
            CustomerAccount.account_number == number,
        )
    ):
        raise Conflict(f"account number {number!r} already exists")
    account = CustomerAccount(
        tenant_id=tenant_id,
        account_number=number,
        display_name=_required(command.display_name, "display name"),
        status=command.status,
    )
    try:
        with conflict_savepoint(db):
            db.add(account)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(f"account number {number!r} conflicts") from exc
    return account


def set_profile(
    db: Session, *, scope: TenantScope, command: SetCustomerProfile
) -> CustomerProfile:
    tenant_id = _tenant(scope)
    get_account(db, scope=scope, account_id=command.account_id)
    profile = db.scalar(
        select(CustomerProfile).where(
            CustomerProfile.tenant_id == tenant_id,
            CustomerProfile.account_id == command.account_id,
        )
    )
    if profile is None:
        profile = CustomerProfile(tenant_id=tenant_id, account_id=command.account_id)
        db.add(profile)
    profile.segment = command.segment.strip().upper() if command.segment else None
    profile.notes = command.notes.strip() if command.notes else None
    db.flush()
    return profile


def link_party_reference(
    db: Session, *, scope: TenantScope, command: LinkPartyReference
) -> CustomerPartyReference:
    from dotmac_kernel.db import conflict_savepoint

    tenant_id = _tenant(scope)
    get_account(db, scope=scope, account_id=command.account_id)
    reference = CustomerPartyReference(
        tenant_id=tenant_id,
        account_id=command.account_id,
        party_system=_required(command.party_system, "party system").lower(),
        party_reference=_required(command.party_reference, "party reference"),
        role=command.role,
    )
    try:
        with conflict_savepoint(db):
            db.add(reference)
            db.flush()
    except IntegrityError as exc:
        raise Conflict("party reference already exists for the account role") from exc
    return reference


def transition_account(
    db: Session, *, scope: TenantScope, account_id: UUID, target: AccountStatus
) -> CustomerAccount:
    account = cast(CustomerAccount, get_account(db, scope=scope, account_id=account_id))
    if target == account.status:
        return account
    if target not in _TRANSITIONS[account.status]:
        detail = (
            "terminal" if account.status == AccountStatus.CLOSED else "inadmissible"
        )
        raise Conflict(f"{detail} customer lifecycle transition")
    account.status = target
    db.flush()
    return account


__all__ = [
    "create_account",
    "get_account",
    "link_party_reference",
    "set_profile",
    "transition_account",
]
