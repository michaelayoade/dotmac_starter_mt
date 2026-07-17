"""Tenant-scoped auth service — register/login flows.

All `select()`/session-mutation calls for the auth domain live here —
`app/features/auth/router.py` only resolves dependencies, calls these
functions, and shapes the response.

Registration creates a `party_type == person` `Party` plus its `PartyPerson`
subtype row in the same transaction — `Party` replaced `Person` (Task 6); the
`/auth/register` request/response shape is unchanged, kept mechanically
person-shaped (Task 7 owns any schema redesign).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.models import (
    AuthSession,
    Party,
    PartyPerson,
    PartyRole,
    PartyType,
    Role,
    Tenant,
)
from app.core.security import (
    hash_password,
    hash_token,
    issue_access_token,
    verify_password,
)
from app.features.auth.models import UserCredential
from app.features.auth.schemas import LoginRequest, RegisterRequest


@dataclass(frozen=True)
class LoginResult:
    """`TokenResponse`-shaped result — router wraps this into the response schema."""

    access_token: str
    token_type: str = "bearer"


@dataclass(frozen=True)
class PersonView:
    """`CurrentUserResponse`-shaped combination of `Party` + its `PartyPerson`
    subtype row — the API keeps returning person-shaped payloads this task
    (Task 7 owns schema redesign), but the fields now come from two tables.
    """

    id: UUID
    email: str
    first_name: str
    last_name: str
    tenant_id: UUID


def register(db: Session, tenant: Tenant, payload: RegisterRequest) -> PersonView:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=f"{payload.first_name} {payload.last_name}",
        email=payload.email,
    )
    db.add(party)
    try:
        db.flush()
        party_person = PartyPerson(
            party_id=party.id,
            first_name=payload.first_name,
            last_name=payload.last_name,
        )
        db.add(party_person)
        credential = UserCredential(
            tenant_id=tenant.id,
            party_id=party.id,
            email=payload.email,
            password_hash=hash_password(payload.password),
        )
        db.add(credential)
        db.flush()
        _assign_first_user_admin(db, tenant, party)
        db.refresh(party)
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Email already registered") from exc
    return PersonView(
        id=party.id,
        email=payload.email,
        first_name=party_person.first_name,
        last_name=party_person.last_name,
        tenant_id=party.tenant_id,
    )


def login(db: Session, tenant: Tenant, payload: LoginRequest) -> LoginResult:
    credential = db.scalars(
        select(UserCredential)
        .where(UserCredential.tenant_id == tenant.id)
        .where(UserCredential.email == payload.email)
    ).first()
    if credential is None or not verify_password(
        payload.password, credential.password_hash
    ):
        raise UnauthorizedError("Invalid credentials")

    token, expires_at = issue_access_token(credential.party_id, tenant.id)
    db.add(
        AuthSession(
            tenant_id=tenant.id,
            party_id=credential.party_id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    db.flush()
    return LoginResult(access_token=token)


def get_current_user_view(db: Session, party: Party) -> PersonView:
    """Combine `party` with its `PartyPerson` subtype row for `/auth/me`."""
    party_person = db.get(PartyPerson, party.id)
    if party_person is None or party.email is None:
        raise UnauthorizedError("Invalid credentials")
    return PersonView(
        id=party.id,
        email=party.email,
        first_name=party_person.first_name,
        last_name=party_person.last_name,
        tenant_id=party.tenant_id,
    )


def _assign_first_user_admin(db: Session, tenant: Tenant, party: Party) -> None:
    existing_assignment = db.scalars(
        select(PartyRole).where(PartyRole.tenant_id == tenant.id).limit(1)
    ).first()
    if existing_assignment is not None:
        return

    role = db.scalars(
        select(Role).where(Role.tenant_id == tenant.id).where(Role.slug == "admin")
    ).first()
    if role is None:
        role = Role(tenant_id=tenant.id, slug="admin", name="Admin")
        db.add(role)
        db.flush()
    db.add(PartyRole(tenant_id=tenant.id, party_id=party.id, role_id=role.id))
    db.flush()


__all__ = ["LoginResult", "PersonView", "get_current_user_view", "login", "register"]
