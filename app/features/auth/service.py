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

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import conflict_savepoint
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
    # Normalize to lowercase at this boundary so a later login lookup
    # matches the parties CI-unique semantics (the `parties` table's email
    # uniqueness index is `lower(email)`-based — `Party.email` is the ONLY
    # place an email is stored now, F2/Task 3: the credential row carries no
    # email of its own, so there is nothing left to keep in sync).
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
    # `db.add(party)` happens INSIDE the savepoint, not before it: `Session.
    # begin_nested()` auto-flushes any already-pending changes as part of
    # establishing the SAVEPOINT (`_take_snapshot`) — adding `party` before
    # entering `conflict_savepoint` would let that pre-flush emit the
    # conflicting INSERT with no savepoint yet in place to protect the
    # outer transaction's `SET LOCAL` if it fails. See
    # `.superpowers/sdd/task-2-report.md`'s harness-interplay notes.
    try:
        with conflict_savepoint(db):
            db.add(party)
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
                password_hash=hash_password(payload.password),
            )
            db.add(credential)
            db.flush()
            _assign_first_user_admin(db, tenant, party)
            db.refresh(party)
    except IntegrityError as exc:
        raise ConflictError("Email already registered") from exc
    return PersonView(
        id=party.id,
        email=email,
        first_name=party_person.first_name,
        last_name=party_person.last_name,
        tenant_id=party.tenant_id,
    )


def login(db: Session, tenant: Tenant, payload: LoginRequest) -> LoginResult:
    """Resolve the login identity via `Party.email` (F2/Task 3 — the single
    email authority), then find the credential row by `party_id`.

    Two-step resolution, both tenant-scoped: (1) `Party` by `(tenant_id,
    normalize_email(email), party_type == person)` — organization parties
    have no login of their own; (2) `UserCredential` by `(tenant_id,
    party_id)`. Either miss raises the SAME `UnauthorizedError("Invalid
    credentials")` as a wrong password — no user-enumeration widening
    relative to the pre-Task-3 behavior being replaced: that code ALSO
    short-circuited on a credential-row miss with no dummy password-hash
    comparison (`verify_password` runs a real PBKDF2 hash-and-compare, so a
    "no such credential" reply was already measurably faster than a "wrong
    password" reply for an existing account — that timing gap is inherited
    unchanged, not introduced here; a fully timing-safe login is a separate,
    not-yet-scoped hardening item). Splitting the single old query into two
    adds at most one extra indexed point-lookup on the miss path — noise
    next to the pre-existing PBKDF2-shaped gap, so it does not create a NEW
    or meaningfully bigger oracle.

    Intended consequence (documented, not a bug): NULLing a person party's
    `email` (`app.features.parties.service.update_person_party`) disables
    login for that party outright — the query is `func.lower(Party.email) ==
    normalize_email(email)`, and a NULL column matches no string. There is
    no separate identity to fall back to once the single email column is
    cleared; see `docs/ARCHITECTURE.md`'s "Auth credentials" ownership row
    and `docs/superpowers/phase2-backlog.md`'s (resolved) F2 entry.
    """
    email = normalize_email(payload.email)
    party = db.scalars(
        select(Party)
        .where(Party.tenant_id == tenant.id)
        .where(Party.party_type == PartyType.person)
        # `email` is already lowercased by `normalize_email` above, so this
        # predicate is defense-in-depth, not the normalization itself — but
        # it must be `func.lower(Party.email) == email`, not a bare
        # `Party.email == email`, because that's *exactly* the expression
        # the partial functional index `uq_parties_tenant_lower_email`
        # (`(tenant_id, lower(email)) WHERE email IS NOT NULL`, see
        # `app/core/models.py::Party.__table_args__`) is built on. Postgres
        # only matches a functional index when the query's WHERE clause is
        # syntactically the same expression the index was created with — a
        # bare `Party.email == email` would silently fall back to a seq
        # scan on this column.
        .where(func.lower(Party.email) == email)
    ).first()
    credential = (
        db.scalars(
            select(UserCredential)
            .where(UserCredential.tenant_id == tenant.id)
            .where(UserCredential.party_id == party.id)
        ).first()
        if party is not None
        else None
    )
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
