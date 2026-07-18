"""Web-portal service logic: login/logout session mutation + dashboard counts.

All `select()`/session-mutation calls the web routes need live here —
`app/features/web/web.py` only resolves dependencies, parses the form, calls
these functions, and shapes the response (thin-wrapper rule, same as every
other feature's `router.py`/`web.py`).

Cross-feature import note (Task 3 architecture decision — see
`docs/superpowers/plans/2026-07-17-phase2b-admin-portal.md`'s Task 3
section: "the web shell feature imports core only"): this module imports
`app.features.auth.service.login` for ONE thing only — reusing the existing
credential-check + token-issuance flow UNCHANGED for the login POST route,
rather than duplicating password verification. That single edge is an
explicit, reviewed exception to the "features are independent" contract
(`pyproject.toml`'s `ignore_imports` entry, scoped to exactly this path) —
everything else in this module (logout/session-revocation, dashboard counts,
the current-user view) queries CORE models directly (`Party`, `Role`,
`PartyRole`, `AuthSession`, `PartyPerson` all live in `app.core.models`) and
touches no other feature package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import UnauthorizedError
from app.core.models import AuthSession, Party, PartyPerson, Role, Tenant
from app.core.security import hash_token
from app.features.auth import service as auth_service
from app.features.auth.schemas import LoginRequest


def safe_next_url(url: str | None, default: str = "/admin") -> str:
    """Port of `ST:app/web/auth.py::_safe_next_url` — open-redirect guard.

    Only a same-origin, absolute path is accepted: must start with a single
    `/` (rejects protocol-relative `//evil.example.com`) and must not
    contain `://` anywhere (rejects `/x?u=http://evil.example.com` style
    smuggling as well as a bare `http://evil.example.com` value). Anything
    else falls back to `default`.
    """
    if not url:
        return default
    if url.startswith("/") and not url.startswith("//") and "://" not in url:
        return url
    return default


def is_secure_request(request: Request) -> bool:
    """Port of `ST:app/web/auth.py::_is_secure_request`.

    True if the request arrived over HTTPS — either directly (`request.url.
    scheme`) or forwarded through a TLS-terminating proxy (`X-Forwarded-
    Proto: https`, the standard header nginx/most LBs set). Drives the
    `access_token` cookie's `Secure` flag: `Secure` in prod (behind the
    proxy), not required in local dev over plain HTTP.
    """
    proto = request.headers.get("x-forwarded-proto", "")
    return proto == "https" or request.url.scheme == "https"


def web_login(db: Session, tenant: Tenant, username: str, password: str) -> str | None:
    """Attempt login via the UNCHANGED auth-feature `login()` flow.

    Returns the access token on success, `None` on invalid credentials —
    never raises, so the web route can re-render the login form with a
    generic error instead of leaking which part of the credential pair was
    wrong.
    """
    try:
        result = auth_service.login(
            db, tenant, LoginRequest(email=username, password=password)
        )
    except UnauthorizedError:
        return None
    return result.access_token


def web_logout(db: Session, tenant: Tenant, token: str | None) -> None:
    """Revoke the `AuthSession` backing `token`, if any.

    No auth-service call needed — session revocation only touches
    `AuthSession`, a CORE model, so this is a plain, direct query here (same
    pattern the auth service's own `login()` uses to CREATE a session; this
    is the mirror-image REVOKE, added because no revocation path existed
    anywhere in the app before this task). Silently no-ops on a
    missing/already-revoked/foreign token — logout must always succeed from
    the caller's point of view (clearing a stale cookie is not an error).
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


@dataclass(frozen=True)
class DashboardCounts:
    parties: int
    roles: int
    active_sessions: int


def get_dashboard_counts(db: Session, tenant: Tenant) -> DashboardCounts:
    """Tenant-scoped counts for the dashboard's stat cards.

    All three are CORE models (`Party`, `Role`, `AuthSession`) — querying
    them here (not in `web.py`) satisfies the thin-wrapper rule.
    `active_sessions` (not-revoked, not-expired `AuthSession` rows) is this
    task's judgment call for the brief's third count (worded "active-
    definitions counts" in the task brief — no model in this repo is named
    "definitions" outside the feature-local `CustomFieldDefinition`, which
    `web` cannot query per feature independence; "active sessions" is the
    reading that (a) is a genuinely useful admin-portal metric and (b) is a
    CORE-model query, matching the brief's own "query CORE models" framing).
    """
    parties = (
        db.scalar(
            select(func.count()).select_from(Party).where(Party.tenant_id == tenant.id)
        )
        or 0
    )
    roles = (
        db.scalar(
            select(func.count()).select_from(Role).where(Role.tenant_id == tenant.id)
        )
        or 0
    )
    active_sessions = (
        db.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.tenant_id == tenant.id)
            .where(AuthSession.revoked_at.is_(None))
            .where(AuthSession.expires_at > datetime.now(UTC))
        )
        or 0
    )
    return DashboardCounts(
        parties=parties, roles=roles, active_sessions=active_sessions
    )


@dataclass(frozen=True)
class CurrentUserView:
    """`templates/components/topbar.html`-shaped view of the logged-in party.

    Built here (not by importing `app.features.auth.service.
    get_current_user_view`, which would be another cross-feature import)
    from `PartyPerson` — a CORE model — directly.
    """

    first_name: str
    last_name: str
    email: str


def get_current_user_view(db: Session, party: Party) -> CurrentUserView:
    person = db.get(PartyPerson, party.id)
    if person is not None:
        return CurrentUserView(
            first_name=person.first_name,
            last_name=person.last_name,
            email=party.email or "",
        )
    # Defensive fallback — every person-type Party created via
    # auth_service.register() also gets a PartyPerson row, but this guards
    # against a hypothetical future writer that doesn't.
    first, _, last = (party.display_name or "").partition(" ")
    return CurrentUserView(first_name=first, last_name=last, email=party.email or "")


__all__ = [
    "CurrentUserView",
    "DashboardCounts",
    "get_current_user_view",
    "get_dashboard_counts",
    "is_secure_request",
    "safe_next_url",
    "web_login",
    "web_logout",
]
