"""Web-portal service logic: dashboard counts + current-user view.

All `select()`/session-mutation calls the web routes need live here —
`app/features/web/web.py` only resolves dependencies, calls these functions,
and shapes the response (thin-wrapper rule, same as every other feature's
`router.py`/`web.py`).

Login/logout (`web_login`/`web_logout`) and their `safe_next_url`/
`is_secure_request` helpers moved out of this module per Task 3 review's
required fix (see `.superpowers/sdd/task-3-report.md`'s fix note):
`web_login`/`web_logout` now live in `app.features.auth.service` (same
module as `login()` — the cross-feature import into `app.features.auth`
this module used to carry, and the `pyproject.toml` `ignore_imports`
exception it required, are both gone); `safe_next_url`/`is_secure_request`
now live in `dotmac_kernel.web_deps` as generic HTTP utilities. Everything
remaining here queries CORE models directly (`Party`, `Role`, `AuthSession`,
`PartyPerson`) and touches no other feature package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from dotmac_kernel.models import AuthSession, Party, PartyPerson, Role, Tenant
from sqlalchemy import func, select
from sqlalchemy.orm import Session


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
]
