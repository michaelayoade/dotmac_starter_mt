"""Access administration over the kernel identity tables.

Every mutation here runs in ONE conflict SAVEPOINT of the caller's transaction,
takes the kernel's Party-first lock order, uses the kernel identity-storage
primitives for grants and sessions, and writes its audit events before the
SAVEPOINT is released. So a failure anywhere (including the audit write) rolls
the whole operation back, and the request's transaction never commits half a
leaver revoke.

Order inside a savepoint matters: the identity-storage primitives refuse pending
ORM state (`db.new`/`dirty`/`deleted`), so ORM attribute changes and audit rows
come AFTER the storage calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import UUID, uuid4

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from dotmac_kernel.identity import normalize_email, person_display_name
from dotmac_kernel.identity_storage import (
    NewPersonValues,
    PartyFact,
    ensure_grant,
    find_party_normalized_email,
    lock_grants,
    lock_parties,
    remove_grants,
    revoke_party_sessions,
)
from dotmac_kernel.identity_storage import create_person as _create_person
from dotmac_kernel.models import (
    AuthSession,
    Party,
    PartyPerson,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
)
from dotmac_kernel.query import apply_pagination, escape_like
from dotmac_kernel.transactions import conflict_savepoint
from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_access_admin import provisioning
from dotmac_access_admin.roles import (
    CodeRole,
    SodWarning,
    code_role_slugs,
    code_roles,
    sod_warnings,
    validate_composition,
)

PROVISION_REASON = "admin_provisioned"


class UserRow(NamedTuple):
    id: UUID
    email: str | None
    display_name: str
    first_name: str
    last_name: str
    is_active: bool
    roles: tuple[str, ...]


class RoleRow(NamedTuple):
    slug: str
    name: str
    id: UUID | None
    kind: str  # "code" | "tenant"
    read_only: bool
    permission_codes: tuple[str, ...]
    holders: int


class CreatedUser(NamedTuple):
    user: UserRow
    recovery_intent_ref: str


class GrantOutcome(NamedTuple):
    party_id: UUID
    role_id: UUID
    role_slug: str
    warnings: tuple[SodWarning, ...]


class RevokeOutcome(NamedTuple):
    party_id: UUID
    grants_revoked: int
    sessions_revoked: int
    deactivated: bool


def _audit(
    db: Session,
    tenant: Tenant,
    actor: Party,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, object],
) -> None:
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        actor_type="user",
        actor_id=str(actor.id),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )


# ── Reads ───────────────────────────────────────────────────────────────────


def _role_slugs_by_party(
    db: Session, tenant: Tenant, party_ids: Sequence[UUID]
) -> dict[UUID, list[str]]:
    if not party_ids:
        return {}
    rows = db.execute(
        select(PartyRoleGrant.party_id, Role.slug)
        .join(
            Role,
            (Role.id == PartyRoleGrant.role_id)
            & (Role.tenant_id == PartyRoleGrant.tenant_id),
        )
        .where(PartyRoleGrant.tenant_id == tenant.id)
        .where(PartyRoleGrant.party_id.in_(party_ids))
        .order_by(Role.slug)
    ).all()
    out: dict[UUID, list[str]] = {}
    for party_id, slug in rows:
        out.setdefault(party_id, []).append(slug)
    return out


def _search(tenant: Tenant, q: str | None) -> Select[tuple[Party, PartyPerson]]:
    stmt = (
        select(Party, PartyPerson)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .where(Party.tenant_id == tenant.id, Party.party_type == PartyType.person)
    )
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(
            or_(
                Party.display_name.ilike(like, escape="\\"),
                Party.email.ilike(like, escape="\\"),
            )
        )
    return stmt


def list_users(
    db: Session, tenant: Tenant, *, q: str | None, limit: int, offset: int
) -> list[UserRow]:
    stmt = apply_pagination(
        _search(tenant, q).order_by(Party.display_name.asc(), Party.id.asc()),
        limit=limit,
        offset=offset,
    )
    rows = db.execute(stmt).all()
    roles = _role_slugs_by_party(db, tenant, [party.id for party, _ in rows])
    return [
        UserRow(
            party.id,
            party.email,
            party.display_name,
            person.first_name,
            person.last_name,
            party.is_active,
            tuple(roles.get(party.id, ())),
        )
        for party, person in rows
    ]


def count_users(db: Session, tenant: Tenant, *, q: str | None) -> int:
    sub = _search(tenant, q).subquery()
    return db.scalar(select(func.count()).select_from(sub)) or 0


def get_user(db: Session, tenant: Tenant, party_id: UUID) -> UserRow:
    row = db.execute(_search(tenant, None).where(Party.id == party_id)).first()
    if row is None:
        raise NotFoundError("User not found")
    party, person = row
    roles = _role_slugs_by_party(db, tenant, [party.id])
    return UserRow(
        party.id,
        party.email,
        party.display_name,
        person.first_name,
        person.last_name,
        party.is_active,
        tuple(roles.get(party.id, ())),
    )


def list_roles(db: Session, tenant: Tenant) -> list[RoleRow]:
    """Tenant roles plus assembly-declared code roles (read-only).

    A code role that already has a tenant row is reported once, as `code`, with
    its row id and holder count.
    """
    declared = {role.slug: role for role in code_roles()}
    holders = dict(
        db.execute(
            select(PartyRoleGrant.role_id, func.count())
            .where(PartyRoleGrant.tenant_id == tenant.id)
            .group_by(PartyRoleGrant.role_id)
        ).all()
    )
    stored = list(
        db.scalars(
            select(Role).where(Role.tenant_id == tenant.id).order_by(Role.slug)
        ).all()
    )
    rows: list[RoleRow] = []
    seen: set[str] = set()
    for role in stored:
        code: CodeRole | None = declared.get(role.slug)
        seen.add(role.slug)
        rows.append(
            RoleRow(
                role.slug,
                role.name,
                role.id,
                "code" if code else "tenant",
                code is not None,
                code.permission_codes if code else (),
                int(holders.get(role.id, 0)),
            )
        )
    for slug, code in sorted(declared.items()):
        if slug not in seen:
            rows.append(
                RoleRow(slug, slug, None, "code", True, code.permission_codes, 0)
            )
    return rows


def composition_check(permission_codes: Sequence[str]) -> tuple[str, ...]:
    """Refuse any undeclared permission. Validation only: see `roles.py`."""
    return validate_composition(permission_codes)


# ── Roles ───────────────────────────────────────────────────────────────────


def create_role(
    db: Session, tenant: Tenant, actor: Party, *, slug: str, name: str
) -> Role:
    if slug in code_role_slugs():
        raise ConflictError(
            "That slug belongs to an assembly-declared role and cannot be created here"
        )
    role = Role(tenant_id=tenant.id, slug=slug, name=name)
    try:
        with conflict_savepoint(db):
            db.add(role)
            db.flush()
            _audit(
                db,
                tenant,
                actor,
                "access_admin.role.create",
                "role",
                str(role.id),
                {"slug": slug},
            )
    except IntegrityError as exc:
        raise ConflictError("Role already exists") from exc
    return role


def rename_role(
    db: Session, tenant: Tenant, actor: Party, *, role_id: UUID, name: str
) -> Role:
    with conflict_savepoint(db):
        role = db.scalars(
            select(Role)
            .where(Role.tenant_id == tenant.id, Role.id == role_id)
            .with_for_update()
        ).first()
        if role is None:
            raise NotFoundError("Role not found")
        if role.slug in code_role_slugs():
            raise ConflictError("An assembly-declared role is read-only")
        previous = role.name
        role.name = name
        db.flush()
        _audit(
            db,
            tenant,
            actor,
            "access_admin.role.rename",
            "role",
            str(role.id),
            {"slug": role.slug, "previous_name": previous},
        )
    return role


# ── Users ───────────────────────────────────────────────────────────────────


def create_user(
    db: Session,
    tenant: Tenant,
    actor: Party,
    *,
    email: str,
    first_name: str,
    last_name: str,
) -> CreatedUser:
    """Create a person and provision a credential nobody is told.

    Goes through `CredentialLifecycle.provision`: no password parameter exists,
    and the receipt carries only the recovery intent reference.
    """
    normalized = normalize_email(email.strip())
    if not normalized:
        raise BadRequestError("Email is required")
    now = datetime.now(UTC)
    party_id = uuid4()
    credential_id = uuid4()
    try:
        with conflict_savepoint(db):
            if (
                find_party_normalized_email(
                    db, tenant_id=tenant.id, normalized_email=normalized
                )
                is not None
            ):
                raise ConflictError("Email already registered")
            _create_person(
                db,
                values=NewPersonValues(
                    id=party_id,
                    tenant_id=tenant.id,
                    email=normalized,
                    display_name=person_display_name(first_name, last_name),
                    first_name=first_name,
                    last_name=last_name,
                    is_active=True,
                    custom_fields={},
                    created_at=now,
                    updated_at=now,
                ),
            )
            lifecycle = provisioning.build_lifecycle(db, tenant.id, actor.id)
            subject = provisioning.CredentialSubjectRef(
                application=provisioning.APPLICATION,
                principal_kind=provisioning.PRINCIPAL_KIND,
                principal_ref=str(party_id),
                credential_ref=str(credential_id),
            )
            receipt = lifecycle.provision(
                subject,
                reason_code=PROVISION_REASON,
                correlation_ref=str(uuid4()),
            )
            _audit(
                db,
                tenant,
                actor,
                "access_admin.user.create",
                "party",
                str(party_id),
                {"recovery_intent_ref": receipt.recovery_intent_ref},
            )
    except IntegrityError as exc:
        raise ConflictError("Email already registered") from exc
    return CreatedUser(get_user(db, tenant, party_id), receipt.recovery_intent_ref)


def _locked_person(db: Session, tenant: Tenant, party_id: UUID) -> PartyFact:
    found = lock_parties(db, tenant_id=tenant.id, party_ids=(party_id,))
    if len(found) != 1 or found[0].party_type != PartyType.person:
        raise NotFoundError("User not found")
    return found[0]


def _refuse_self(actor: Party, party_id: UUID, what: str) -> None:
    if actor.id == party_id:
        raise ConflictError(f"You cannot {what} your own account")


def deactivate_user(
    db: Session, tenant: Tenant, actor: Party, *, party_id: UUID
) -> UserRow:
    """Mark a person inactive and end every session.

    Refused while the person still holds grants: the kernel's login and session
    checks do not consult `Party.is_active`, so a flag alone would cut nothing.
    Remove access first with `leaver_revoke`.
    """
    _refuse_self(actor, party_id, "deactivate")
    with conflict_savepoint(db):
        fact = _locked_person(db, tenant, party_id)
        if lock_grants(db, tenant_id=tenant.id, party_ids=(party_id,)):
            raise ConflictError(
                "User still holds grants; revoke their access first (leaver revoke)"
            )
        if not fact.is_active:
            raise ConflictError("User is already inactive")
        revoked = revoke_party_sessions(
            db,
            tenant_id=tenant.id,
            party_id=party_id,
            session_ids=None,
            revoked_at=datetime.now(UTC),
        )
        party = db.get(Party, party_id)
        assert party is not None
        party.is_active = False
        db.flush()
        _audit(
            db,
            tenant,
            actor,
            "access_admin.user.deactivate",
            "party",
            str(party_id),
            {"sessions_revoked": len(revoked)},
        )
    return get_user(db, tenant, party_id)


def reactivate_user(
    db: Session, tenant: Tenant, actor: Party, *, party_id: UUID
) -> UserRow:
    """Flag only. Grants are never restored; re-grant explicitly (audited)."""
    with conflict_savepoint(db):
        fact = _locked_person(db, tenant, party_id)
        if fact.is_active:
            raise ConflictError("User is already active")
        party = db.get(Party, party_id)
        assert party is not None
        party.is_active = True
        db.flush()
        _audit(
            db,
            tenant,
            actor,
            "access_admin.user.reactivate",
            "party",
            str(party_id),
            {},
        )
    return get_user(db, tenant, party_id)


def leaver_revoke(
    db: Session,
    tenant: Tenant,
    actor: Party,
    *,
    party_id: UUID,
    deactivate: bool = True,
) -> RevokeOutcome:
    """Revoke ALL grants and ALL sessions of one person in ONE transaction.

    One conflict SAVEPOINT: the party is locked first, then its grants and
    sessions; the grants are removed and the sessions revoked through the kernel
    identity-storage primitives; then the party flag and the audit rows (one per
    grant, plus a summary) are written. Any failure, including the audit write,
    rolls the whole operation back, so a leaver is never left half-revoked.
    The next request carrying one of that person's tokens fails authentication.
    """
    _refuse_self(actor, party_id, "revoke")
    with conflict_savepoint(db):
        _locked_person(db, tenant, party_id)
        grants = lock_grants(db, tenant_id=tenant.id, party_ids=(party_id,))
        # Lock the complete session cohort before the revoke, as the kernel's
        # own session owners do.
        db.scalars(
            select(AuthSession.id)
            .where(
                AuthSession.tenant_id == tenant.id,
                AuthSession.party_id == party_id,
            )
            .order_by(AuthSession.id)
            .with_for_update()
        ).all()
        role_slugs = dict(
            db.execute(
                select(Role.id, Role.slug).where(Role.tenant_id == tenant.id)
            ).all()  # type: ignore[arg-type]
        )
        removed = remove_grants(
            db, tenant_id=tenant.id, party_id=party_id, expected=grants
        )
        revoked = revoke_party_sessions(
            db,
            tenant_id=tenant.id,
            party_id=party_id,
            session_ids=None,
            revoked_at=datetime.now(UTC),
        )
        if deactivate:
            party = db.get(Party, party_id)
            assert party is not None
            party.is_active = False
            db.flush()
        for grant in removed:
            _audit(
                db,
                tenant,
                actor,
                "access_admin.grant.revoke",
                "party_role_grant",
                str(party_id),
                {
                    "role_id": str(grant.role_id),
                    "role_slug": role_slugs.get(grant.role_id, ""),
                    "via": "leaver_revoke",
                },
            )
        _audit(
            db,
            tenant,
            actor,
            "access_admin.user.leaver_revoke",
            "party",
            str(party_id),
            {
                "grants_revoked": len(removed),
                "sessions_revoked": len(revoked),
                "deactivated": deactivate,
            },
        )
    return RevokeOutcome(party_id, len(removed), len(revoked), deactivate)


# ── Grants ──────────────────────────────────────────────────────────────────


def grant_role(
    db: Session, tenant: Tenant, actor: Party, *, party_id: UUID, role_id: UUID
) -> GrantOutcome:
    """Grant a role (audited). SoD pairs produce WARNINGS, never a refusal."""
    with conflict_savepoint(db):
        fact = _locked_person(db, tenant, party_id)
        if not fact.is_active:
            raise ConflictError("Cannot grant a role to an inactive user")
        role = db.scalars(
            select(Role).where(Role.tenant_id == tenant.id, Role.id == role_id)
        ).first()
        if role is None:
            raise NotFoundError("Party or role not found")
        existing = lock_grants(db, tenant_id=tenant.id, party_ids=(party_id,))
        if any(grant.role_id == role_id for grant in existing):
            raise ConflictError("Role already assigned")
        held = [
            slug
            for rid, slug in db.execute(
                select(Role.id, Role.slug).where(Role.tenant_id == tenant.id)
            ).all()
            if rid in {grant.role_id for grant in existing}
        ]
        warnings = sod_warnings([*held, role.slug])
        ensure_grant(
            db,
            tenant_id=tenant.id,
            party_id=party_id,
            role_id=role_id,
            new_grant_id=None,
            created_at=None,
            updated_at=None,
        )
        _audit(
            db,
            tenant,
            actor,
            "access_admin.grant.add",
            "party_role_grant",
            str(party_id),
            {
                "role_id": str(role_id),
                "role_slug": role.slug,
                "sod_warnings": [list(w.roles) for w in warnings],
            },
        )
    return GrantOutcome(party_id, role_id, role.slug, warnings)


def revoke_grant(
    db: Session, tenant: Tenant, actor: Party, *, party_id: UUID, role_id: UUID
) -> None:
    _refuse_self(actor, party_id, "revoke a role from")
    with conflict_savepoint(db):
        _locked_person(db, tenant, party_id)
        grants = lock_grants(db, tenant_id=tenant.id, party_ids=(party_id,))
        match = tuple(grant for grant in grants if grant.role_id == role_id)
        if not match:
            raise NotFoundError("Grant not found")
        slug = db.scalar(
            select(Role.slug).where(Role.tenant_id == tenant.id, Role.id == role_id)
        )
        remove_grants(db, tenant_id=tenant.id, party_id=party_id, expected=match)
        _audit(
            db,
            tenant,
            actor,
            "access_admin.grant.revoke",
            "party_role_grant",
            str(party_id),
            {"role_id": str(role_id), "role_slug": slug or "", "via": "revoke"},
        )


__all__ = [
    "CreatedUser",
    "GrantOutcome",
    "RevokeOutcome",
    "RoleRow",
    "UserRow",
    "composition_check",
    "count_users",
    "create_role",
    "create_user",
    "deactivate_user",
    "get_user",
    "grant_role",
    "leaver_revoke",
    "list_roles",
    "list_users",
    "reactivate_user",
    "rename_role",
    "revoke_grant",
]
