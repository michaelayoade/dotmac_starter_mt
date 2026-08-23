"""Brand profile data, per-field precedence, and resolution that reports itself.

Extracted product-first from `dotmac_sub`'s `BrandProfile` (897 LOC across seven
modules, in production) under ADR-0057 § 2.

## Every resolved field reports its source

ADR-0006 § 3's first safety rule, and the reason `ResolvedBrand` carries a
`sources` map rather than just values. An operator looking at a portal that says
the wrong company name needs to know WHICH layer said it — and a resolver that
returns only the merged result makes that a debugging session instead of a
lookup.

Sub's implementation carries `source_scope`/`source_scope_id` for the whole
record. This generalises it PER FIELD, because per-field precedence is what
ADR-0006 § 3 actually specifies and a whole-record source cannot express "the
name came from the reseller and the legal identity from the platform".

## A lock beats precedence

ADR-0006 § 3's second safety rule. A higher-precedence layer may pin a field, and
a pinned field is not overridable by anything below it. Without locks, precedence
is only a default and a reseller can rebrand the operator's legal identity;
`IDENTITY_FIELDS` exists so a caller can pin exactly that set in one call.

## The three-way boundary (Michael, 2026-08-19)

`dotmac-ui` owns the token vocabulary, the projection logic and contrast
validation. **This module owns the scoped values, their provenance, the
precedence between them and the locks over them** — constrained runtime
brand/accent values are permitted and intended here. **The assembly** maps those
values into `dotmac_ui.BrandOverride`.

So there is deliberately no `brand_override()` in this module: returning a
ready-made override would take the assembly's job back. What the module does
supply is `BRAND_OVERRIDE_INPUTS` — the allowlist, checked against
`BrandOverride`'s own fields — so the assembly's one-line mapping cannot drift.

Colour VALIDATION is still `dotmac-ui`'s, called on write through
`validate_brand_values`. This module owns no colour parser; a second one would
eventually accept something the first refuses.

Importing `dotmac_ui` is the ONE cross-package import this module makes, and it
is the permitted direction: `assembly → module → dotmac-ui → dotmac-kernel`
(ADR-0006 U1). It is not a sibling module.

## Transaction authority (hard rule 8)

Receives a `Session`; only `add` and `flush`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from dotmac_kernel.audit import write_platform_audit_event
from dotmac_kernel.idempotency import execute_once, execute_once_platform
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_brand_profiles.brand_values import validate_brand_values
from dotmac_brand_profiles.models import (
    LOCKABLE_FIELDS,
    BrandProfile,
    PlatformBrandHostBinding,
    PlatformBrandProfile,
    ProfileStatus,
)
from dotmac_brand_profiles.ports import (
    BrandProfileError,
    HostBindingRefusedError,
    ProfileFields,
    ProfileRefusedError,
    UnknownLockedFieldError,
)

#: ONE audit action, and only on the platform plane.
#:
#: The tenant plane deliberately writes none. `dotmac_kernel.audit
#: .write_audit_event` is a FROZEN facility (`tests/architecture/
#: kernel_facilities.py`): it touches storage, has no prerequisite spec to
#: declare, and its caller set is ratcheted so the debt cannot grow unnoticed.
#: A new caller is refused, and refusing it is correct rather than merely
#: enforced — a module that receives a `Session` does not know WHO acted, and
#: the tenant trail's actor derivation belongs to the adapter that does.
#:
#: So a tenant-plane brand change is audited by the assembly route that made it,
#: exactly as every other tenant-plane change is. The module contributes
#: `record_version`, which is what makes such an audit reconstructable.
#:
#: The platform peer is different in a way that matters: it is MAPPED, with
#: `platform_audit_log.v1` as its declared prerequisite, and its actor is a
#: `PlatformAdmin` the caller already holds.
AUDIT_ACTION_PLATFORM: str = "platform_brand_profile.changed"

SCOPE_UPSERT_TENANT = "brand_profile.upsert"
SCOPE_UPSERT_PLATFORM = "platform_brand_profile.upsert"
SCOPE_BIND_HOST = "platform_brand_profile.bind_host"

_ENTITY_PLATFORM = "platform_brand_profile"


# ── Resolution result ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ResolvedBrand:
    """The merged brand, plus where every field came from.

    `sources` maps each resolved field name to the `scope_type` that supplied
    it, and `locked` names the fields that were pinned. Both are part of the
    contract, not diagnostics: ADR-0006 § 3 requires every field to report its
    source, and a caller rendering an admin screen shows the operator which
    layer they need to edit.
    """

    values: Mapping[str, Any]
    sources: Mapping[str, str]
    locked: frozenset[str]

    def get(self, field: str, default: Any = None) -> Any:
        return self.values.get(field, default)

    def source_of(self, field: str) -> str | None:
        return self.sources.get(field)


# ── Field mechanics ─────────────────────────────────────────────────────────


def _values_of(row: BrandProfile | PlatformBrandProfile) -> dict[str, Any]:
    """Only the fields that participate in precedence.

    Restricted to `LOCKABLE_FIELDS` on purpose: `id`, `status` and
    `record_version` are row mechanics, and merging them across layers would
    produce a resolved brand carrying one layer's primary key.
    """
    return {
        field: getattr(row, field)
        for field in sorted(LOCKABLE_FIELDS)
        if getattr(row, field, None) is not None
    }


def _require_lockable(fields: Sequence[str]) -> tuple[str, ...]:
    unknown = tuple(sorted(set(fields) - LOCKABLE_FIELDS))
    if unknown:
        raise UnknownLockedFieldError(unknown)
    return tuple(sorted(set(fields)))


def resolve(
    layers: Sequence[tuple[str, BrandProfile | PlatformBrandProfile]],
) -> ResolvedBrand:
    """Merge an ordered chain of `(scope_type, profile)`, highest precedence FIRST.

    The caller supplies the order, because the scope vocabulary belongs to the
    product (ADR-0008) — Sub's is `organization, reseller, platform` and another
    product's will differ. This module owns the MECHANICS: first non-null wins,
    a locked field cannot be overridden by anything later in the chain, and every
    resolved field records which layer supplied it.

    "First wins" rather than "last wins" because the caller reads the chain in
    precedence order, and a merge that silently reversed it would be correct
    exactly half the time and wrong invisibly the other half.
    """
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    locked: set[str] = set()

    for scope_type, profile in layers:
        profile_locks = set(profile.locked_fields or ())
        for field, value in _values_of(profile).items():
            if field in values:
                # Already supplied by a higher-precedence layer.
                continue
            values[field] = value
            sources[field] = scope_type
        # A lock declared by THIS layer binds every layer below it. Recorded
        # after the merge above so a layer can both supply and pin a field.
        locked |= profile_locks & LOCKABLE_FIELDS

    return ResolvedBrand(values=values, sources=sources, locked=frozenset(locked))


def resolvable_by(resolved: ResolvedBrand, *, scope_type: str) -> frozenset[str]:
    """Which fields a layer at `scope_type` may still change.

    Everything not locked by a higher-precedence layer. Exposed because an admin
    UI that offers an operator a field they cannot actually change is worse than
    one that hides it — the operator edits, saves, and sees no effect.
    """
    return frozenset(LOCKABLE_FIELDS - resolved.locked)


# ── Tenant plane ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UpsertTenantProfileCommand:
    command_id: str
    tenant_id: UUID
    scope_type: str
    profile_code: str
    fields: ProfileFields
    scope_id: UUID | None = None
    lock: Sequence[str] = ()


def upsert_tenant_profile(db: Session, command: UpsertTenantProfileCommand) -> UUID:
    """Create or update a tenant-plane profile, as `DRAFT` on first write.

    Draft first, always: a profile becomes resolvable only by an explicit
    activation. Creating straight to active would put a half-entered brand in
    front of customers the moment someone opened the form.
    """
    locks = _require_lockable(tuple(command.lock))
    payload = command.fields.as_dict()
    # Fail where the value was entered, not when a page renders. Uses
    # dotmac-ui's own parser through the published surface.
    validate_brand_values(payload)

    def handler(session: Session) -> Mapping[str, object]:
        row = session.execute(
            select(BrandProfile).where(
                BrandProfile.tenant_id == command.tenant_id,
                BrandProfile.profile_code == command.profile_code,
            )
        ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = BrandProfile(
                tenant_id=command.tenant_id,
                scope_type=command.scope_type,
                scope_id=command.scope_id,
                profile_code=command.profile_code,
                display_name=str(payload.get("display_name") or command.profile_code),
                status=ProfileStatus.DRAFT.value,
                record_version=1,
            )
            session.add(row)
        else:
            row.record_version += 1
        for field, value in payload.items():
            setattr(row, field, value)
        row.locked_fields = list(locks)
        session.flush()
        # No audit write here — see the note on AUDIT_ACTION_PLATFORM above.
        # `created` is returned so the caller's own audit can say which it was
        # without a second query.
        return {"id": str(row.id), "created": created}

    outcome = execute_once(
        db,
        tenant_id=command.tenant_id,
        scope=SCOPE_UPSERT_TENANT,
        key=command.command_id,
        operation=handler,
    )
    return UUID(str(outcome.result["id"]))


def activate_tenant_profile(
    db: Session, *, command_id: str, tenant_id: UUID, profile_id: UUID
) -> None:
    """`draft | retired → active`. The only way a profile becomes resolvable."""

    def handler(session: Session) -> Mapping[str, object]:
        row = session.get(BrandProfile, profile_id)
        if row is None:
            raise ProfileRefusedError(f"brand profile {profile_id} not found")
        if not row.display_name:
            raise ProfileRefusedError(
                f"brand profile {row.profile_code!r} has no display name; a brand "
                "with nothing to show is not activatable"
            )
        row.status = ProfileStatus.ACTIVE.value
        row.record_version += 1
        session.flush()
        return {"id": str(row.id)}

    execute_once(
        db,
        tenant_id=tenant_id,
        scope=SCOPE_UPSERT_TENANT,
        key=command_id,
        operation=handler,
    )


def resolve_for_tenant(
    db: Session, *, tenant_id: UUID, chain: Sequence[tuple[str, UUID | None]]
) -> ResolvedBrand:
    """Resolve a tenant's brand over an ordered scope chain, highest first.

    `chain` is `[(scope_type, scope_id), ...]` — the caller's own hierarchy. Only
    ACTIVE profiles participate: a draft is not yet a brand and a retired one is
    history.
    """
    layers: list[tuple[str, BrandProfile | PlatformBrandProfile]] = []
    for scope_type, scope_id in chain:
        statement = select(BrandProfile).where(
            BrandProfile.tenant_id == tenant_id,
            BrandProfile.scope_type == scope_type,
            BrandProfile.status == ProfileStatus.ACTIVE.value,
        )
        statement = statement.where(
            BrandProfile.scope_id == scope_id
            if scope_id is not None
            else BrandProfile.scope_id.is_(None)
        )
        for row in db.execute(statement).scalars():
            layers.append((scope_type, row))
    return resolve(layers)


# ── Platform plane ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UpsertPlatformProfileCommand:
    command_id: str
    profile_code: str
    fields: ProfileFields
    lock: Sequence[str] = ()
    actor_admin_id: UUID | None = None


def upsert_platform_profile(db: Session, command: UpsertPlatformProfileCommand) -> UUID:
    """Create or update a platform-plane (OEM / white-label) profile."""
    locks = _require_lockable(tuple(command.lock))
    payload = command.fields.as_dict()
    # Fail where the value was entered, not when a page renders. Uses
    # dotmac-ui's own parser through the published surface.
    validate_brand_values(payload)

    def handler(session: Session) -> Mapping[str, object]:
        row = session.execute(
            select(PlatformBrandProfile).where(
                PlatformBrandProfile.profile_code == command.profile_code
            )
        ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = PlatformBrandProfile(
                profile_code=command.profile_code,
                display_name=str(payload.get("display_name") or command.profile_code),
                status=ProfileStatus.DRAFT.value,
                record_version=1,
            )
            session.add(row)
        else:
            row.record_version += 1
        for field, value in payload.items():
            setattr(row, field, value)
        row.locked_fields = list(locks)
        session.flush()
        write_platform_audit_event(
            session,
            actor_admin_id=command.actor_admin_id,
            action=AUDIT_ACTION_PLATFORM,
            entity_type=_ENTITY_PLATFORM,
            entity_id=str(row.id),
            details={
                "profile_code": row.profile_code,
                "created": created,
                "locked_fields": list(locks),
                "record_version": row.record_version,
            },
        )
        return {"id": str(row.id)}

    outcome = execute_once_platform(
        db,
        scope=SCOPE_UPSERT_PLATFORM,
        key=command.command_id,
        operation=handler,
    )
    return UUID(str(outcome.result["id"]))


def activate_platform_profile(
    db: Session,
    *,
    command_id: str,
    profile_id: UUID,
    actor_admin_id: UUID | None = None,
) -> None:
    """`draft | retired → active`."""

    def handler(session: Session) -> Mapping[str, object]:
        row = session.get(PlatformBrandProfile, profile_id)
        if row is None:
            raise ProfileRefusedError(f"platform brand profile {profile_id} not found")
        row.status = ProfileStatus.ACTIVE.value
        row.record_version += 1
        session.flush()
        write_platform_audit_event(
            session,
            actor_admin_id=actor_admin_id,
            action=AUDIT_ACTION_PLATFORM,
            entity_type=_ENTITY_PLATFORM,
            entity_id=str(row.id),
            details={"profile_code": row.profile_code, "status": row.status},
        )
        return {"id": str(row.id)}

    execute_once_platform(
        db, scope=SCOPE_UPSERT_PLATFORM, key=command_id, operation=handler
    )


def bind_host(
    db: Session,
    *,
    command_id: str,
    host: str,
    profile_id: UUID,
    is_canonical: bool = False,
    actor_admin_id: UUID | None = None,
) -> UUID:
    """Bind an already-NORMALISED host to a platform profile.

    Normalisation is the caller's: lowercased, punycode, no trailing dot.
    Normalising here would make this module a second authority on what a host is,
    and two normalisers eventually disagree — at which point one of them binds a
    host the other cannot find.

    This is host → BRAND, never host → TENANT. The kernel's `TenantDomain` owns
    the second question.
    """
    if host != host.strip().lower() or not host:
        raise HostBindingRefusedError(
            f"host {host!r} is not normalised; this module binds hosts and does "
            "not normalise them, because two normalisers eventually disagree"
        )

    def handler(session: Session) -> Mapping[str, object]:
        profile = session.get(PlatformBrandProfile, profile_id)
        if profile is None:
            raise HostBindingRefusedError(
                f"platform brand profile {profile_id} not found"
            )
        existing = session.execute(
            select(PlatformBrandHostBinding).where(
                PlatformBrandHostBinding.host == host
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.profile_id = profile_id
            existing.is_canonical = is_canonical
            session.flush()
            return {"id": str(existing.id)}
        row = PlatformBrandHostBinding(
            host=host, profile_id=profile_id, is_canonical=is_canonical
        )
        session.add(row)
        session.flush()
        write_platform_audit_event(
            session,
            actor_admin_id=actor_admin_id,
            action=AUDIT_ACTION_PLATFORM,
            entity_type=_ENTITY_PLATFORM,
            entity_id=str(profile_id),
            details={"host": host, "is_canonical": is_canonical},
        )
        return {"id": str(row.id)}

    outcome = execute_once_platform(
        db, scope=SCOPE_BIND_HOST, key=command_id, operation=handler
    )
    return UUID(str(outcome.result["id"]))


def resolve_by_host(db: Session, host: str) -> ResolvedBrand | None:
    """The OEM path: which brand does this host present?

    Answerable BEFORE any tenant is resolved, which is the whole reason a brand
    profile cannot be a tenant setting. Returns `None` for an unbound host —
    fail-open would mean serving whichever brand happened to be first.
    """
    binding = db.execute(
        select(PlatformBrandHostBinding).where(PlatformBrandHostBinding.host == host)
    ).scalar_one_or_none()
    if binding is None:
        return None
    profile = db.get(PlatformBrandProfile, binding.profile_id)
    if profile is None or profile.status != ProfileStatus.ACTIVE.value:
        return None
    return resolve([("platform", profile)])


def get_platform_profile(db: Session, profile_code: str) -> PlatformBrandProfile | None:
    return db.execute(
        select(PlatformBrandProfile).where(
            PlatformBrandProfile.profile_code == profile_code
        )
    ).scalar_one_or_none()


__all__ = [
    "AUDIT_ACTION_PLATFORM",
    "SCOPE_BIND_HOST",
    "SCOPE_UPSERT_PLATFORM",
    "SCOPE_UPSERT_TENANT",
    "BrandProfileError",
    "ResolvedBrand",
    "UpsertPlatformProfileCommand",
    "UpsertTenantProfileCommand",
    "activate_platform_profile",
    "activate_tenant_profile",
    "bind_host",
    "get_platform_profile",
    "resolvable_by",
    "resolve",
    "resolve_by_host",
    "resolve_for_tenant",
    "upsert_platform_profile",
    "upsert_tenant_profile",
]
