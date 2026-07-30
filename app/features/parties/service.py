"""Tenant-scoped Parties service.

`Party` (`party_type` person|organization) is the identity source of truth
(Task 6); this feature owns its own API shape (Task 7) — `create_person_party`
and `create_organization_party` create a `Party` + its subtype row
(`PartyPerson`/`PartyOrganization`) atomically via a single flush each
(`get_db` owns the commit — see `dotmac_kernel.db.get_db`), `list_parties` filters
by `party_type` and paginates, and `Parties` (a `CRUDManager[Party]`) handles
plain get/delete. All `select()`/session-mutation calls for the party domain
live here — `app/features/parties/router.py` only resolves dependencies,
calls these functions, and shapes the response.

`update_person_party`/`update_organization_party` (Task 5) close the
`display_name` dual-writer SOT gap: both the create and update paths now
recompute `display_name` via the shared `dotmac_kernel.identity` helpers
(`normalize_email`/`person_display_name`), so this module — together with
`app.features.auth.service.register` — is the single write-owner of the
projection. `party_type` is immutable on update (enforced by raising
`NotFoundError` on a type mismatch, same convention `delete_party` already
uses via `Parties.get`).
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.crud import CRUDManager
from dotmac_kernel.db import conflict_savepoint
from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from dotmac_kernel.identity import normalize_email, person_display_name
from dotmac_kernel.models import (
    Party,
    PartyOrganization,
    PartyPerson,
    PartyType,
    Tenant,
)
from dotmac_kernel.query import apply_pagination, escape_like
from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.features.parties.schemas import (
    OrganizationPartyCreate,
    OrganizationPartyUpdate,
    PersonPartyCreate,
    PersonPartyUpdate,
)

# SoT: `PartyPerson.first_name`/`last_name` and `PartyOrganization.legal_name`
# are `nullable=False` columns (app/core/models.py). The corresponding
# `*Update` schema fields are typed `str | None = None` purely so
# `model_dump(exclude_unset=True)` can distinguish "not sent" from "sent" —
# not because the column accepts NULL. Same convention (and same reason) as
# `custom_fields/router.py::NOT_NULLABLE_FIELDS`; kept here instead of a
# router because this feature has no JSON PATCH route yet (web-only this
# task — see docs/superpowers/phase2-backlog.md).
_NOT_NULLABLE_PERSON_FIELDS = frozenset({"first_name", "last_name"})
_NOT_NULLABLE_ORGANIZATION_FIELDS = frozenset({"legal_name"})


class Parties(CRUDManager[Party]):
    model = Party
    not_found_detail = "Party not found"


def create_person_party(
    db: Session, tenant: Tenant, payload: PersonPartyCreate
) -> Party:
    email = normalize_email(payload.email)
    party = Party(
        tenant_id=tenant.id,  # never from payload — always from request state
        party_type=PartyType.person,
        # `dotmac_kernel.identity.person_display_name` is the single-owner
        # projection recompute — `update_person_party` below calls the same
        # helper, and so does `app.features.auth.service.register` (Task 5
        # closes the display_name dual-writer gap; see docs/ARCHITECTURE.md).
        display_name=person_display_name(payload.first_name, payload.last_name),
        email=email,
    )
    # Assigning via the relationship (rather than setting party_id by hand)
    # lets SQLAlchemy's cascade add both rows and resolve the FK in a single
    # flush, keeping the in-memory object graph populated for the caller
    # without a second round trip.
    party.person_profile = PartyPerson(
        first_name=payload.first_name, last_name=payload.last_name
    )
    # `db.add` happens INSIDE the savepoint, not before it: `Session.
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
    except IntegrityError as exc:
        raise ConflictError("Email already registered") from exc
    return party


def create_organization_party(
    db: Session, tenant: Tenant, payload: OrganizationPartyCreate
) -> Party:
    email = normalize_email(payload.email) if payload.email else None
    party = Party(
        tenant_id=tenant.id,  # never from payload — always from request state
        party_type=PartyType.organization,
        # Organizations have no derivation helper to share (unlike persons)
        # — `legal_name` IS the display name already; `update_organization_
        # party` below reassigns the same way on every write.
        display_name=payload.legal_name,
        email=email,
    )
    party.organization_profile = PartyOrganization(legal_name=payload.legal_name)
    # See create_person_party's comment: `db.add` must happen INSIDE the
    # savepoint, not before it.
    try:
        with conflict_savepoint(db):
            db.add(party)
            db.flush()
    except IntegrityError as exc:
        raise ConflictError("Email already registered") from exc
    return party


def update_person_party(
    db: Session, party_id: UUID, payload: PersonPartyUpdate
) -> Party:
    """Update subtype fields + email on a `party_type == person` `Party`,
    recomputing `display_name` from the (possibly just-updated) subtype
    fields via the shared `dotmac_kernel.identity.person_display_name` helper —
    this function is now the single write-owner of the projection for the
    parties-service side (`app.features.auth.service.register` is the other
    writer, for the initial create-at-signup case only; see
    docs/ARCHITECTURE.md's "Known dual-writer: Parties" section).

    `party_type` is immutable: a person party can never become an
    organization, so calling this on an organization's `party_id` raises
    `NotFoundError` — same "wrong type looks like missing" convention
    `delete_party` already relies on via `Parties.get`/`_get_or_404`.
    """
    party = Parties.get(db, str(party_id))
    if party.party_type is not PartyType.person or party.person_profile is None:
        raise NotFoundError(Parties.not_found_detail)

    updates = payload.model_dump(exclude_unset=True)
    for key in _NOT_NULLABLE_PERSON_FIELDS:
        if key in updates and updates[key] is None:
            raise BadRequestError(f"{key} cannot be null")

    # The whole mutation section runs INSIDE the savepoint, not just the
    # final flush: `Session.begin_nested()` auto-flushes any already-dirty
    # objects as part of establishing the SAVEPOINT (`_take_snapshot`), so
    # mutating `party`/`profile` BEFORE entering `conflict_savepoint` would
    # let that pre-flush emit the conflicting UPDATE with no savepoint yet
    # in place to protect the outer transaction's `SET LOCAL` if it fails.
    # See `.superpowers/sdd/task-2-report.md`'s harness-interplay notes.
    try:
        with conflict_savepoint(db):
            profile = party.person_profile
            if "first_name" in updates:
                profile.first_name = updates["first_name"]
            if "last_name" in updates:
                profile.last_name = updates["last_name"]
            if "email" in updates:
                raw_email = updates["email"]
                party.email = normalize_email(raw_email) if raw_email else None

            party.display_name = person_display_name(
                profile.first_name, profile.last_name
            )
            db.flush()
    except IntegrityError as exc:
        raise ConflictError("Email already registered") from exc
    return party


def update_organization_party(
    db: Session, party_id: UUID, payload: OrganizationPartyUpdate
) -> Party:
    """Update subtype fields + email on a `party_type == organization`
    `Party`. Organizations have no shared display_name helper to call —
    `legal_name` IS the display name — so this function reassigns
    `party.display_name = profile.legal_name` directly on every write; it is
    still the single write-owner of the projection for this party_type (see
    `update_person_party`'s docstring for the person-side equivalent and the
    dual-writer note in docs/ARCHITECTURE.md).

    `party_type` is immutable — calling this on a person's `party_id` raises
    `NotFoundError`, same convention as `update_person_party`/`delete_party`.
    """
    party = Parties.get(db, str(party_id))
    if (
        party.party_type is not PartyType.organization
        or party.organization_profile is None
    ):
        raise NotFoundError(Parties.not_found_detail)

    updates = payload.model_dump(exclude_unset=True)
    for key in _NOT_NULLABLE_ORGANIZATION_FIELDS:
        if key in updates and updates[key] is None:
            raise BadRequestError(f"{key} cannot be null")

    # See update_person_party's comment: the whole mutation section runs
    # INSIDE the savepoint, not just the final flush.
    try:
        with conflict_savepoint(db):
            profile = party.organization_profile
            if "legal_name" in updates:
                profile.legal_name = updates["legal_name"]
            if "email" in updates:
                raw_email = updates["email"]
                party.email = normalize_email(raw_email) if raw_email else None

            party.display_name = profile.legal_name
            db.flush()
    except IntegrityError as exc:
        raise ConflictError("Email already registered") from exc
    return party


def list_parties(
    db: Session, *, party_type: PartyType | None, limit: int, offset: int
) -> list[Party]:
    # No explicit tenant filter — RLS does it. If RLS were misconfigured this
    # would leak; the cross-tenant test catches that.
    stmt = select(Party).options(
        joinedload(Party.person_profile), joinedload(Party.organization_profile)
    )
    if party_type is not None:
        stmt = stmt.where(Party.party_type == party_type)
    stmt = stmt.order_by(Party.created_at.desc())
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    return list(db.scalars(stmt).unique().all())


def _search_filter(
    stmt: Select, *, q: str | None, party_type: PartyType | None
) -> Select:
    """Shared WHERE-clause builder for `search_parties`/`count_parties` (Task
    4) — one place for the filter shape so the count and the page of rows it
    paginates can never drift apart.

    LIKE-escaping is `dotmac_kernel.query.escape_like` (moved there in Task 6 so
    `rbac.service.list_grantable_parties` can share it — see that helper's
    docstring).
    """
    if party_type is not None:
        stmt = stmt.where(Party.party_type == party_type)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(
            or_(
                Party.display_name.ilike(like, escape="\\"),
                Party.email.ilike(like, escape="\\"),
            )
        )
    return stmt


def search_parties(
    db: Session,
    *,
    q: str | None,
    party_type: PartyType | None,
    limit: int,
    offset: int,
) -> list[Party]:
    """Free-text (display_name/email) + party_type filtered listing for the
    admin parties screen (Task 4's index/search/filter/pagination).

    Same RLS-only tenant-scoping convention as `list_parties` above — no
    explicit `tenant_id` filter; RLS enforces it, and
    `tests/test_party_isolation.py` is the canary that would catch drift.
    """
    stmt = _search_filter(select(Party), q=q, party_type=party_type).options(
        joinedload(Party.person_profile), joinedload(Party.organization_profile)
    )
    stmt = stmt.order_by(Party.created_at.desc())
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    return list(db.scalars(stmt).unique().all())


def count_parties(db: Session, *, q: str | None, party_type: PartyType | None) -> int:
    """Total row count for `search_parties`' filters — powers the index
    page's pagination (page X of Y), computed with the SAME `_search_filter`
    so the count and the page it describes can never disagree.
    """
    stmt = _search_filter(
        select(func.count()).select_from(Party), q=q, party_type=party_type
    )
    return db.scalar(stmt) or 0


def get_party(db: Session, party_id: UUID) -> Party:
    party = Parties.get(db, str(party_id))
    # Touch the subtype relationships now, while the session is open, so the
    # router's read-model mapping (app.features.parties.router) never issues
    # a lazy-loading query itself.
    _ = party.person_profile
    _ = party.organization_profile
    return party


def delete_party(db: Session, party_id: UUID) -> None:
    Parties.delete(db, str(party_id), commit=False)


__all__ = [
    "Parties",
    "count_parties",
    "create_organization_party",
    "create_person_party",
    "delete_party",
    "get_party",
    "list_parties",
    "search_parties",
    "update_organization_party",
    "update_person_party",
]
