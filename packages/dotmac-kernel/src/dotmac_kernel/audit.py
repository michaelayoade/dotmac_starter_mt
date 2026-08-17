"""Audit event model + write-side helpers.

Cross-cutting: the audit trail is written from every domain (rbac, auth, ...),
so the model and the write helper live in dotmac_kernel. The audit *read* endpoint
stays in app.features.rbac.

**`action` is a declared code, not free text** (module control-plane directive
step 3). `write_audit_event` validates it against the process-active
`dotmac_kernel.audit_actions.AuditActionRegistry` — built from the installed
manifests' `audit_actions` — before it writes anything, so a typo cannot quietly
create a second near-identical action nobody queries for. The platform writer
enforces the same registry: its callers are installable control-plane modules,
and their `ModuleManifest.audit_actions` declarations own that vocabulary.

**The actor is `(actor_type, actor_id)`, not a party.** `actor_party_id` is
optional accountability *enrichment*, never the primary identity, and
`actor_label` is a write-time display snapshot that carries no authority. That
is not a preference — it is what the data is. Across ERP and Sub production,
**93-98% of audit rows have a non-party actor** (a job, a service principal, an
API key), so a party-primary model would be NULL for almost every row. ERP
independently arrived at the same polymorphic pair, so this is a two-product
contract rather than one product's accommodation.

No actor column in the converging tenant `audit_events` contract gains a foreign
key. An audit row must remain readable after its actor is deleted, which is also
why `actor_label` is stored rather than resolved on read. The kernel's separate
platform audit trail predates this contract and is not changed by this slice.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, Session, mapped_column

from dotmac_kernel.audit_actions import active_audit_actions
from dotmac_kernel.models import Base, uuid_pk
from dotmac_kernel.models_platform import PlatformAuditEvent

#: The four actor kinds the contract defines. Deliberately a Python constant
#: over a `String` column, NOT a PostgreSQL enum. Actor kinds are kernel-owned
#: semantic categories, so ADR-0008's module-declared-vocabulary trigger does
#: not apply. Plain string storage instead keeps a future, versioned kernel
#: contract addition from requiring enum surgery in every product database.
#:
#: Closed rather than an open registry, because each member carries a defined
#: resolution rule (below). A fifth kind is a contract change with an answer for
#: what its `actor_id` means and whether it can carry a party — not a string a
#: product invents at a call site.
ACTOR_TYPES: frozenset[str] = frozenset({"system", "user", "api_key", "service"})

#: What `actor_id` holds per kind, and whether a party may accompany it:
#:
#:   system    optional component/job identifier        no party
#:   service   service-principal identifier             no party
#:   api_key   the key ID — NEVER the token             optional owning party
#:   user      principal identifier                     party when available


class MissingAuditActorError(ValueError):
    """The canonical actor pair cannot be resolved from the supplied fields.

    Deliberately fatal rather than defaulting a missing actor to `system` or
    accepting a principal kind with no identifier. Either would manufacture
    false or incomplete forensic data in the one table meant to be trustworthy.
    """


class UnknownAuditActorTypeError(ValueError):
    """An actor type outside the contract's four kinds."""


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The canonical forensic actor. Nullable only because rows written before
    # 0023 never recorded a kind — see that migration for why they are not
    # backfilled.
    actor_type: Mapped[str | None] = mapped_column(String(32), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(120), index=True)
    #: Display snapshot resolved at write time. Never authority, never joined —
    #: it survives deletion of the actor it names, which is the point.
    actor_label: Mapped[str | None] = mapped_column(String(160), index=True)
    #: Optional accountability enrichment. NOT a foreign key: an audit row must
    #: outlive the party it references.
    actor_party_id: Mapped[UUID | None] = mapped_column(Uuid(), index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(120))
    # Request forensics. All nullable: a scheduled job, a reconciler or an
    # internal transition has no HTTP interaction at all, and `status_code`
    # NOT NULL would force every such event to invent a meaningless 200 —
    # destroying the field as a filter, since "succeeded with 200" would stop
    # being distinguishable from "not an HTTP request".
    request_id: Mapped[str | None] = mapped_column(String(120), index=True)
    status_code: Mapped[int | None] = mapped_column(sa.Integer())
    is_success: Mapped[bool | None] = mapped_column(sa.Boolean())
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict[str, object]] = mapped_column(
        sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    #: Domain event time — when the thing happened. Caller-supplyable, and NOT
    #: an alias for `created_at`: a reconstruction or a scheduled effect carries
    #: a time that is not its insertion time.
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
        server_default=func.now(),
    )
    #: Persistence time — when the row was written. Server-assigned, never
    #: supplied by a caller.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def resolve_audit_actor(
    *,
    actor_type: str | None,
    actor_id: str | None,
    actor_party_id: UUID | None,
) -> tuple[str, str | None]:
    """Return the `(actor_type, actor_id)` to record, or raise.

    Carries one temporary compatibility rule. `dotmac_workspace` still has two
    live callers that pass only `actor_party_id`; removing the fallback in this
    repository would break that independently released consumer. When a party
    is supplied with no kind, this derives `user` and uses the party id as the
    principal identifier, which is exactly what those call sites mean.

    The rule retires only after both Workspace callers pass the explicit pair.
    Starter's nine Template Studio callers already do so and may not regress.
    What is NOT temporary: supplying neither raises. Defaulting a missing actor
    to `system` would be indistinguishable, forever, from a genuine system
    action.
    """
    resolved_id = actor_id if actor_id is not None and actor_id.strip() else None

    if actor_type is None:
        if actor_party_id is None:
            raise MissingAuditActorError(
                "an audit event needs an actor: pass actor_type (one of "
                f"{sorted(ACTOR_TYPES)}) or actor_party_id. Refusing to default "
                "to 'system' — that would record a caller defect as a real actor."
            )
        return "user", resolved_id or str(actor_party_id)

    if actor_type not in ACTOR_TYPES:
        raise UnknownAuditActorTypeError(
            f"unknown audit actor type {actor_type!r}; "
            f"the contract defines {sorted(ACTOR_TYPES)}"
        )
    if actor_type == "system":
        return actor_type, resolved_id

    if actor_type == "user" and resolved_id is None and actor_party_id is not None:
        resolved_id = str(actor_party_id)

    if resolved_id is None:
        raise MissingAuditActorError(
            f"audit actor type {actor_type!r} needs a non-empty actor_id"
        )

    return actor_type, resolved_id


def write_audit_event(
    db: Session,
    *,
    tenant_id: UUID,
    actor_party_id: UUID | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    actor_label: str | None = None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    request_id: str | None = None,
    status_code: int | None = None,
    is_success: bool | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    occurred_at: datetime | None = None,
    details: dict[str, object] | None = None,
) -> AuditEvent:
    """Record a tenant-scoped audit event.

    `action` MUST be declared by an installed module's manifest
    (`audit_actions`) — raises `UndeclaredAuditActionError` otherwise, BEFORE
    anything is added to the session, so a rejected write leaves no partial
    state. See this module's docstring for why the trail's vocabulary is a
    declaration rather than free text.

    The actor is resolved by `resolve_audit_actor`: pass `actor_type` (with
    `actor_id` where the kind has one), or pass `actor_party_id` alone and get
    the temporary `user` derivation. Passing neither raises
    `MissingAuditActorError` — validated with `action`, before anything is
    added to the session, for the same reason.

    `occurred_at` is the DOMAIN time and is optional; leave it unset for an
    ordinary current event and the database supplies `now()`. Supply it for a
    reconstruction or delayed observation whose domain time differs from its
    persistence time (`created_at`, always server-assigned). Do not pass
    `created_at` — it is not a parameter, because a caller cannot know when the
    row will be written.
    """
    active_audit_actions().require(action)
    resolved_type, resolved_id = resolve_audit_actor(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_party_id=actor_party_id,
    )
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_type=resolved_type,
        actor_id=resolved_id,
        actor_label=actor_label,
        actor_party_id=actor_party_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=request_id,
        status_code=status_code,
        is_success=is_success,
        ip_address=ip_address,
        user_agent=user_agent,
        occurred_at=occurred_at,
        details=details or {},
    )
    db.add(event)
    db.flush()
    return event


def write_platform_audit_event(
    db: Session,
    *,
    actor_admin_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    details: dict[str, object] | None = None,
) -> PlatformAuditEvent:
    """Record a PLATFORM-level audit event (no tenant context). Same add/flush
    contract as `write_audit_event`; the actor is a `PlatformAdmin` and the
    action must be declared by an installed manifest."""
    active_audit_actions().require(action)
    event = PlatformAuditEvent(
        actor_admin_id=actor_admin_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
    )
    db.add(event)
    db.flush()
    return event


__all__ = [
    "ACTOR_TYPES",
    "AuditEvent",
    "MissingAuditActorError",
    "PlatformAuditEvent",
    "UnknownAuditActorTypeError",
    "resolve_audit_actor",
    "write_audit_event",
    "write_platform_audit_event",
]
