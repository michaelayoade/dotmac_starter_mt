"""Governed identity-administration commands (prerequisite `identity_admin.v1`).

A STATELESS module that administers people and role grants (the access-admin
screens) must not call the frozen `dotmac_kernel.identity_storage` primitives:
those leave the Party-first lock order, the active-party rule and the
tenant-scoping of every call to each caller, and the facility gate freezes their
module-caller sets at empty precisely so that stays true. This module is the
governed entry point instead. The commands carry the same names as the storage
primitives they wrap, take no more authority than those primitives, and add the
rules every caller would otherwise re-implement:

- **Party first, in ONE call.** `lock_parties` is the only way a party becomes
  lockable here, and it is called ONCE per transaction scope with every party the
  operation needs (a second call may only re-name parties already held). Two
  fresh calls in opposite orders by two operators could deadlock (PostgreSQL
  would abort one side), so a fresh second call is refused. `lock_grants`,
  `ensure_grant`, `remove_grants`, `set_party_active` and
  `revoke_party_sessions` refuse a party this scope has not locked (or created),
  and `lock_parties` also refuses a NEW party once a subordinate lock exists.
  A party locked inside a SAVEPOINT is released with it, so a lock is only
  honoured while the scope that took it (or one of its ancestors) is live.
- **Drift is refusal.** `lock_parties` refuses when any requested id is absent
  or belongs to another tenant, instead of returning a shorter tuple.
- **Normalised email only.** `find_party_normalized_email` and `create_person`
  refuse an email that is not already trimmed and lower-cased.
- **Deactivation has one owner.** `set_party_active` is the only governed writer
  of `Party.is_active` and requires the held party lock, so the lock that makes a
  racing login re-read the flag is not left to each caller to remember.
- **Tenant roles only.** `create_tenant_role` and `rename_tenant_role` refuse a
  slug that any declared permission binds through `default_roles` (a code role
  must not be created or renamed by a tenant: it would silently inherit the code
  role's permissions).
- **No grant to an inactive party**, and no grant of a role that is not in the
  tenant. The party's `is_active` is read under the party lock, so a concurrent
  deactivation is observed, never raced.
- **Sessions are locked before they are revoked.** `revoke_party_sessions` takes
  the ordered `FOR UPDATE` cohort lock itself, so a racing login cannot slip a
  session in between selection and revocation.

What this module deliberately does NOT do: decide who may call it (the caller's
route guards do), write audit rows (the caller writes them in the same
transaction), commit, or touch credentials (`credential_lifecycle` owns those).
`remove_grants` and `revoke_party_sessions` still require the caller's public
conflict SAVEPOINT (`dotmac_kernel.transactions.conflict_savepoint`), exactly as
the storage primitives do; that guard is not weakened here.

A module that calls these declares `runtime_requires=("identity_admin.v1",)` on
its manifest; the assembly binds that prerequisite to the revision that supplies
the storage, and `create_app` refuses an assembly that has not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, SessionTransaction

from dotmac_kernel import identity_storage as _storage
from dotmac_kernel.identity_storage import (
    GrantFact,
    NewPersonValues,
    PartyFact,
    RoleFact,
)
from dotmac_kernel.models import AuthSession, Party, Role
from dotmac_kernel.permissions import active_permissions

__all__ = (
    "GrantFact",
    "IdentityAdminRefusal",
    "NewPersonValues",
    "PartyFact",
    "RoleFact",
    "create_person",
    "create_tenant_role",
    "ensure_grant",
    "find_party_normalized_email",
    "lock_grants",
    "lock_parties",
    "remove_grants",
    "rename_tenant_role",
    "revoke_party_sessions",
    "set_party_active",
)


class IdentityAdminRefusal(RuntimeError):
    """A governed identity command refused. Nothing was changed by the refusal
    itself; the caller rolls back its operation SAVEPOINT."""


_STATE_KEY = "dotmac_kernel.identity_admin.held"


@dataclass
class _Held:
    """Which scopes hold which locks, keyed by transaction scope identity."""

    parties: dict[tuple[UUID, UUID], SessionTransaction] = field(default_factory=dict)
    grants: dict[tuple[UUID, UUID], SessionTransaction] = field(default_factory=dict)
    children: list[SessionTransaction] = field(default_factory=list)


def _scope(db: Session) -> SessionTransaction:
    root = db.get_transaction()
    if root is None:
        db.connection()  # autobegin: the locks below need a live transaction
        root = db.get_transaction()
    if root is None or not root.is_active:
        raise IdentityAdminRefusal(
            "identity administration needs an active transaction"
        )
    return db.get_nested_transaction() or root


def _live(db: Session, recorded: SessionTransaction) -> bool:
    """Is the scope that took a lock still live and an ancestor-or-self of now?"""
    node: SessionTransaction | None = _scope(db)
    while node is not None:
        if node is recorded:
            return bool(recorded.is_active)
        node = node.parent
    return False


def _state(db: Session) -> _Held:
    state = db.info.get(_STATE_KEY)
    if state is None:
        state = _Held()
        db.info[_STATE_KEY] = state
    return state


def _prune(db: Session, state: _Held) -> None:
    for table in (state.parties, state.grants):
        for key, recorded in tuple(table.items()):
            if not _live(db, recorded):
                table.pop(key)
    state.children[:] = [c for c in state.children if _live(db, c)]


def _uuid(value: object, what: str) -> UUID:
    if not isinstance(value, UUID):
        raise IdentityAdminRefusal(f"{what} must be a UUID")
    return value


def _uuids(values: object, what: str) -> tuple[UUID, ...]:
    if not isinstance(values, tuple) or not values:
        raise IdentityAdminRefusal(f"{what} must be a non-empty tuple of UUIDs")
    for value in values:
        _uuid(value, what)
    if len(set(values)) != len(values):
        raise IdentityAdminRefusal(f"{what} contains a duplicate")
    return values


def _normalized(email: str) -> bool:
    return email != "" and email == email.strip().lower()


def _require_held(db: Session, tenant_id: UUID, party_ids: tuple[UUID, ...]) -> _Held:
    state = _state(db)
    _prune(db, state)
    missing = [p for p in party_ids if (tenant_id, p) not in state.parties]
    if missing:
        raise IdentityAdminRefusal(
            "party lock required first: lock_parties must run in this "
            "transaction scope before any subordinate command"
        )
    return state


def find_party_normalized_email(
    db: Session, *, tenant_id: UUID, normalized_email: str
) -> PartyFact | None:
    """Read-only lookup by an already trimmed, lower-cased email."""
    _uuid(tenant_id, "tenant_id")
    if not isinstance(normalized_email, str) or not _normalized(normalized_email):
        raise IdentityAdminRefusal("email must be non-empty, trimmed and lower-cased")
    return _storage.find_party_normalized_email(
        db, tenant_id=tenant_id, normalized_email=normalized_email
    )


def lock_parties(
    db: Session, *, tenant_id: UUID, party_ids: tuple[UUID, ...]
) -> tuple[PartyFact, ...]:
    """Lock the named parties in UUID order; refuse if any is absent.

    Must precede every subordinate command. A party not already locked in this
    scope cannot be added once a grant or session lock has been taken.
    """
    _uuid(tenant_id, "tenant_id")
    ids = _uuids(party_ids, "party_ids")
    state = _state(db)
    _prune(db, state)
    fresh = [p for p in ids if (tenant_id, p) not in state.parties]
    if fresh and state.children:
        raise IdentityAdminRefusal(
            "party locks must precede subordinate locks: lock every party "
            "first, in one call"
        )
    if fresh and state.parties:
        raise IdentityAdminRefusal(
            "lock_parties is called once per scope: name every party in one "
            "call (two fresh calls in opposite orders can deadlock)"
        )
    facts = _storage.lock_parties(db, tenant_id=tenant_id, party_ids=ids)
    if {f.id for f in facts} != set(ids) or any(
        f.tenant_id != tenant_id for f in facts
    ):
        raise IdentityAdminRefusal("a requested party is absent from this tenant")
    scope = _scope(db)
    for party_id in ids:
        state.parties.setdefault((tenant_id, party_id), scope)
    return facts


def create_person(db: Session, *, values: NewPersonValues) -> PartyFact:
    """Create the canonical Party and person subtype; the new party is held.

    Refuses an email that is not already normalised. Collision with an existing
    email surfaces as the database's unique-violation inside the caller's
    SAVEPOINT; the caller maps it.
    """
    if not isinstance(values, NewPersonValues):
        raise IdentityAdminRefusal("values must be NewPersonValues")
    _uuid(values.tenant_id, "tenant_id")
    _uuid(values.id, "party id")
    if values.email is not None and not _normalized(values.email):
        raise IdentityAdminRefusal("email must be non-empty, trimmed and lower-cased")
    state = _state(db)
    _prune(db, state)
    scope = _scope(db)
    party = _storage.create_person(db, values=values)
    state.parties[(values.tenant_id, values.id)] = scope
    return PartyFact(
        party.id,
        party.tenant_id,
        party.party_type,
        party.email,
        party.display_name,
        party.is_active,
    )


def lock_grants(
    db: Session, *, tenant_id: UUID, party_ids: tuple[UUID, ...]
) -> tuple[GrantFact, ...]:
    """Lock every grant of already-locked parties, in a deterministic order."""
    _uuid(tenant_id, "tenant_id")
    ids = _uuids(party_ids, "party_ids")
    state = _require_held(db, tenant_id, ids)
    facts = _storage.lock_grants(db, tenant_id=tenant_id, party_ids=ids)
    scope = _scope(db)
    state.children.append(scope)
    for party_id in ids:
        state.grants[(tenant_id, party_id)] = scope
    return facts


def ensure_grant(
    db: Session, *, tenant_id: UUID, party_id: UUID, role_id: UUID
) -> GrantFact:
    """Grant a role to an ACTIVE, locked party; idempotent.

    Refuses an inactive party and a role that is not in the tenant. The active
    flag is read under the party lock this scope already holds.
    """
    _uuid(tenant_id, "tenant_id")
    _uuid(party_id, "party_id")
    _uuid(role_id, "role_id")
    state = _require_held(db, tenant_id, (party_id,))
    active = db.scalar(
        select(Party.is_active).where(
            Party.tenant_id == tenant_id, Party.id == party_id
        )
    )
    if active is not True:
        raise IdentityAdminRefusal("a role cannot be granted to an inactive party")
    role = db.scalar(
        select(Role.id).where(Role.tenant_id == tenant_id, Role.id == role_id)
    )
    if role is None:
        raise IdentityAdminRefusal("the role is not in this tenant")
    state.children.append(_scope(db))
    return _storage.ensure_grant(
        db,
        tenant_id=tenant_id,
        party_id=party_id,
        role_id=role_id,
        new_grant_id=None,
        created_at=None,
        updated_at=None,
    )


def remove_grants(
    db: Session,
    *,
    tenant_id: UUID,
    party_id: UUID,
    expected: tuple[GrantFact, ...],
) -> tuple[GrantFact, ...]:
    """Remove exactly the grants `lock_grants` just inventoried for this party.

    Requires the party lock AND a `lock_grants` for this party in this scope,
    so the `expected` facts are fresh under lock; still refuses any difference
    between the facts and the rows. Runs in the caller's conflict SAVEPOINT.
    """
    _uuid(tenant_id, "tenant_id")
    _uuid(party_id, "party_id")
    state = _require_held(db, tenant_id, (party_id,))
    if (tenant_id, party_id) not in state.grants:
        raise IdentityAdminRefusal(
            "grant lock required first: lock_grants must run for this party "
            "in this transaction scope"
        )
    db.connection()  # materialise the caller's physical SAVEPOINT, as storage requires
    return _storage.remove_grants(
        db, tenant_id=tenant_id, party_id=party_id, expected=expected
    )


def revoke_party_sessions(
    db: Session,
    *,
    tenant_id: UUID,
    party_id: UUID,
    session_ids: tuple[UUID, ...] | None,
    revoked_at: datetime,
) -> tuple[UUID, ...]:
    """End a locked party's sessions (None = all), locking the cohort first.

    The ordered `FOR UPDATE` cohort lock is taken here, after the party lock, so
    a login racing the revocation either committed its session before the
    cohort was read (and is revoked) or waits on the party lock and is refused
    by the active-party rule. Runs in the caller's conflict SAVEPOINT.
    """
    _uuid(tenant_id, "tenant_id")
    _uuid(party_id, "party_id")
    if session_ids is not None:
        if not isinstance(session_ids, tuple):
            raise IdentityAdminRefusal("session_ids must be a tuple or None")
        for session_id in session_ids:
            _uuid(session_id, "session_ids")
    state = _require_held(db, tenant_id, (party_id,))
    cohort = select(AuthSession.id).where(
        AuthSession.tenant_id == tenant_id, AuthSession.party_id == party_id
    )
    if session_ids is not None:
        cohort = cohort.where(AuthSession.id.in_(session_ids))
    db.execute(cohort.order_by(AuthSession.id).with_for_update()).all()
    state.children.append(_scope(db))
    return _storage.revoke_party_sessions(
        db,
        tenant_id=tenant_id,
        party_id=party_id,
        session_ids=session_ids,
        revoked_at=revoked_at,
    )


def set_party_active(
    db: Session, *, tenant_id: UUID, party_id: UUID, active: bool
) -> tuple[PartyFact, PartyFact]:
    """Set `Party.is_active` for a LOCKED party; returns (before, after).

    The only governed writer of the flag. It requires the party lock this scope
    already holds, so a login racing the change either committed first (and its
    session is part of the leaver's cohort) or waits on the lock and re-reads the
    new value. Grants are not touched: whether deactivation requires an empty
    grant set is the caller's policy.
    """
    _uuid(tenant_id, "tenant_id")
    _uuid(party_id, "party_id")
    if not isinstance(active, bool):
        raise IdentityAdminRefusal("active must be a bool")
    _require_held(db, tenant_id, (party_id,))
    party = db.scalars(
        select(Party)
        .where(Party.tenant_id == tenant_id, Party.id == party_id)
        .execution_options(populate_existing=True)
    ).first()
    if party is None:
        raise IdentityAdminRefusal("the party is absent from this tenant")

    def fact() -> PartyFact:
        return PartyFact(
            party.id,
            party.tenant_id,
            party.party_type,
            party.email,
            party.display_name,
            party.is_active,
        )

    before = fact()
    if party.is_active is not active:
        party.is_active = active
        db.flush()
    return before, fact()


def _code_role_slugs() -> frozenset[str]:
    catalogue = active_permissions()
    slugs: set[str] = set()
    for code in catalogue.codes():
        spec = catalogue.spec(code)
        if spec is not None:
            slugs.update(spec.default_roles)
    return frozenset(slugs)


def _role_fact(row: Role) -> RoleFact:
    return RoleFact(row.id, row.tenant_id, row.slug, row.name)


def _role_name(name: object) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise IdentityAdminRefusal("role name must be 1 to 120 characters")
    return name


def create_tenant_role(
    db: Session, *, tenant_id: UUID, slug: str, name: str
) -> RoleFact:
    """Create a TENANT role. Refuses a slug a declared permission binds."""
    _uuid(tenant_id, "tenant_id")
    if (
        not isinstance(slug, str)
        or not slug
        or len(slug) > 63
        or slug != slug.strip().lower()
    ):
        raise IdentityAdminRefusal("role slug must be 1 to 63 trimmed lower-case chars")
    if slug in _code_role_slugs():
        raise IdentityAdminRefusal(
            "the slug names a code role (a declared permission binds it)"
        )
    row = Role(tenant_id=tenant_id, slug=slug, name=_role_name(name))
    db.add(row)
    db.flush()
    return _role_fact(row)


def rename_tenant_role(
    db: Session, *, tenant_id: UUID, role_id: UUID, name: str
) -> RoleFact:
    """Rename a TENANT role (the slug never changes). Refuses a code role."""
    _uuid(tenant_id, "tenant_id")
    _uuid(role_id, "role_id")
    new_name = _role_name(name)
    row = db.scalars(
        select(Role)
        .where(Role.tenant_id == tenant_id, Role.id == role_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        raise IdentityAdminRefusal("the role is not in this tenant")
    if row.slug in _code_role_slugs():
        raise IdentityAdminRefusal(
            "a code role cannot be renamed (a declared permission binds it)"
        )
    if row.name != new_name:
        row.name = new_name
        db.flush()
    return _role_fact(row)
