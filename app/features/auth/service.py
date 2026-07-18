"""Tenant-scoped auth service — register/login flows + the web (cookie)
login/logout surface.

All `select()`/session-mutation calls for the auth domain live here —
`app/features/auth/router.py` and `app/features/auth/web.py` only resolve
dependencies, call these functions, and shape the response.

Registration creates a `party_type == person` `Party` plus its `PartyPerson`
subtype row in the same transaction — `Party` replaced `Person` (Task 6); the
`/auth/register` request/response shape is unchanged, kept mechanically
person-shaped (Task 7 owns any schema redesign).

`web_login`/`web_logout` (Task 3, relocated here per Task 3 review — see
`.superpowers/sdd/task-3-report.md`'s fix note) are the admin-portal's
cookie-based counterparts to `login()`: `web_login` calls `login()` directly,
in the SAME module, so there is no cross-feature import at all (the prior
`app.features.web.service -> app.features.auth.service` edge, and its
`pyproject.toml` `ignore_imports` carve-out, are both gone). `web_logout`
revokes an `AuthSession` — a CORE model — same pattern `login()` itself uses
to create one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.identity import normalize_email, person_display_name
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
    # Normalize to lowercase at this boundary so credential lookup at login
    # matches the parties CI-unique semantics (the `parties` table's email
    # uniqueness index is `lower(email)`-based; `UserCredential.email` must
    # agree with it or a mixed-case register + lowercase login would fail).
    # `normalize_email`/`person_display_name` are the single-owner
    # implementations of these invariants (app.core.identity) — the parties
    # service's create/update paths call the same two functions, so the two
    # writers can never independently drift (see docs/ARCHITECTURE.md's
    # "Known dual-writer: Parties" section).
    email = normalize_email(payload.email)
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=person_display_name(payload.first_name, payload.last_name),
        email=email,
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
            email=email,
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
        email=email,
        first_name=party_person.first_name,
        last_name=party_person.last_name,
        tenant_id=party.tenant_id,
    )


def login(db: Session, tenant: Tenant, payload: LoginRequest) -> LoginResult:
    email = payload.email.lower()
    credential = db.scalars(
        select(UserCredential)
        .where(UserCredential.tenant_id == tenant.id)
        .where(UserCredential.email == email)
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


def web_login(db: Session, tenant: Tenant, username: str, password: str) -> str | None:
    """Attempt login via `login()` UNCHANGED — same module, no cross-feature
    import needed (this used to be `app.features.web.service.web_login`
    calling `auth_service.login`; Task 3 review's required fix moved it here
    so the call is same-module instead of cross-feature).

    Returns the access token on success, `None` on invalid credentials —
    never raises, so the web route can re-render the login form with a
    generic error instead of leaking which part of the credential pair was
    wrong.
    """
    try:
        result = login(db, tenant, LoginRequest(email=username, password=password))
    except UnauthorizedError:
        return None
    return result.access_token


def web_logout(db: Session, tenant: Tenant, token: str | None) -> None:
    """Revoke the `AuthSession` backing `token`, if any.

    Plain, direct query against `AuthSession` — a CORE model — same pattern
    `login()` above uses to CREATE a session; this is the mirror-image
    REVOKE. Silently no-ops on a missing/already-revoked/foreign token —
    logout must always succeed from the caller's point of view (clearing a
    stale cookie is not an error).
    """
    if not token:
        return
    session = db.scalars(
        select(AuthSession)
        .where(AuthSession.tenant_id == tenant.id)
        .where(AuthSession.token_hash == hash_token(token))
        .where(AuthSession.revoked_at.is_(None))
    ).first()
    if session is None:
        return
    session.revoked_at = datetime.now(UTC)
    db.flush()


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


__all__ = [
    "LoginResult",
    "PersonView",
    "get_current_user_view",
    "login",
    "register",
    "web_login",
    "web_logout",
]
