"""Tenant-scoped auth service — register/login flows.

All `select()`/session-mutation calls for the auth domain live here —
`app/api/auth.py` only resolves dependencies, calls these functions, and
shapes the response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.core.security import (
    hash_password,
    hash_token,
    issue_access_token,
    verify_password,
)
from app.models.auth import AuthSession, UserCredential
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.models.tenant import Tenant


@dataclass(frozen=True)
class LoginResult:
    """`TokenResponse`-shaped result — router wraps this into the response schema."""

    access_token: str
    token_type: str = "bearer"


def register(db: Session, tenant: Tenant, payload: Any) -> Person:
    person = Person(
        tenant_id=tenant.id,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )
    db.add(person)
    try:
        db.flush()
        credential = UserCredential(
            tenant_id=tenant.id,
            person_id=person.id,
            email=payload.email,
            password_hash=hash_password(payload.password),
        )
        db.add(credential)
        db.flush()
        _assign_first_user_admin(db, tenant, person)
        db.refresh(person)
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Email already registered") from exc
    return person


def login(db: Session, tenant: Tenant, payload: Any) -> LoginResult:
    credential = db.scalars(
        select(UserCredential)
        .where(UserCredential.tenant_id == tenant.id)
        .where(UserCredential.email == payload.email)
    ).first()
    if credential is None or not verify_password(
        payload.password, credential.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    token, expires_at = issue_access_token(credential.person_id, tenant.id)
    db.add(
        AuthSession(
            tenant_id=tenant.id,
            person_id=credential.person_id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    db.flush()
    return LoginResult(access_token=token)


def _assign_first_user_admin(db: Session, tenant: Tenant, person: Person) -> None:
    existing_assignment = db.scalars(
        select(PersonRole).where(PersonRole.tenant_id == tenant.id).limit(1)
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
    db.add(PersonRole(tenant_id=tenant.id, person_id=person.id, role_id=role.id))
    db.flush()


__all__ = ["LoginResult", "login", "register"]
