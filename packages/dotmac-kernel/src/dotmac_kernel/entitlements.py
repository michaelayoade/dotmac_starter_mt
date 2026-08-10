"""Tenant entitlements (WS2) — the data-plane's grant store + explainable evaluator.

The division of labour (ADR-0003, deployment plan): **allocation is not
evaluation.** A vendor/commercial authority decides *what a tenant is entitled to*
and projects those decisions here as grants; this module is the *evaluator* — a
request-time, purely local, explainable check of *whether a capability is allowed*
for a tenant. The evaluator NEVER calls a payment/licence provider and NEVER
depends on network validation; it reads the grant store the projection populated.

Grants may only reference **declared capability codes** (WS1 — a code comes from a
`FeatureManifest.capabilities` declaration, never invented by a row): `grant`
validates the code against a `CapabilityCatalogue`. This module is the ONE
entitlement authority — there is no parallel `tenant_module_entitlements` table.

`TenantEntitlementGrant` is a tenant-scoped, RLS-protected table (kernel migration
`0010`). Import-safe: touches only `Base.metadata`, never the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, Session, mapped_column

from dotmac_kernel.capabilities import CapabilityCatalogue
from dotmac_kernel.models import Base, TimestampMixin, uuid_pk

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class TenantEntitlementGrant(Base, TimestampMixin):
    """A tenant's grant of a declared capability code. Tenant-scoped + RLS: a
    grant belongs to exactly one tenant and is invisible across tenants. Written
    by projecting a commercial allocation (source records where from); read by the
    evaluator. `granted=False` is an explicit denial (revocation) kept for
    provenance rather than a hard delete."""

    __tablename__ = "tenant_entitlement_grants"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "capability_code", name="uq_entitlement_tenant_code"
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability_code: Mapped[str] = mapped_column(String(200), nullable=False)
    granted: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.text("true")
    )
    # Optional explainable limits/quota attached to the grant (e.g. seat count).
    limits: Mapped[dict] = mapped_column(
        _JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    # Provenance: which allocation/authority produced this grant.
    source: Mapped[str | None] = mapped_column(String(120))


@dataclass(frozen=True, slots=True)
class EntitlementDecision:
    """The explainable outcome of an entitlement check. `reason` is a stable,
    language-neutral code — never a payment/licence lookup result."""

    capability_code: str
    allowed: bool
    reason: str  # "granted" | "not_granted" | "revoked"
    limits: dict[str, object]


def grant_entitlement(
    db: Session,
    *,
    tenant_id: UUID,
    capability_code: str,
    catalogue: CapabilityCatalogue,
    granted: bool = True,
    limits: dict[str, object] | None = None,
    source: str | None = None,
) -> TenantEntitlementGrant:
    """Create or update a tenant's grant of `capability_code`.

    The code MUST be declared by an installed module (WS1) — an entitlement may
    never invent a capability code. Raises `UndeclaredCapabilityError` otherwise.
    Upserts on `(tenant_id, capability_code)`. Transaction-authority contract:
    receives a `Session`, only add/flush.
    """
    catalogue.require(capability_code)
    existing = db.execute(
        sa.select(TenantEntitlementGrant).where(
            TenantEntitlementGrant.tenant_id == tenant_id,
            TenantEntitlementGrant.capability_code == capability_code,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.granted = granted
        existing.limits = limits or {}
        existing.source = source
        db.flush()
        return existing
    row = TenantEntitlementGrant(
        tenant_id=tenant_id,
        capability_code=capability_code,
        granted=granted,
        limits=limits or {},
        source=source,
    )
    db.add(row)
    db.flush()
    return row


def is_entitled(
    db: Session, *, tenant_id: UUID, capability_code: str
) -> EntitlementDecision:
    """Explainable, local check of whether `capability_code` is allowed for the
    tenant. A pure read of the grant store — no external call. An undeclared or
    ungranted code is simply not allowed (the read side does not validate against
    the catalogue; only writes do)."""
    row = db.execute(
        sa.select(TenantEntitlementGrant).where(
            TenantEntitlementGrant.tenant_id == tenant_id,
            TenantEntitlementGrant.capability_code == capability_code,
        )
    ).scalar_one_or_none()
    if row is None:
        return EntitlementDecision(capability_code, False, "not_granted", {})
    if not row.granted:
        return EntitlementDecision(capability_code, False, "revoked", dict(row.limits))
    return EntitlementDecision(capability_code, True, "granted", dict(row.limits))


__all__ = [
    "EntitlementDecision",
    "TenantEntitlementGrant",
    "grant_entitlement",
    "is_entitled",
]
