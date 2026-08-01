"""Receiver-owned durable licence state — the WS8 `AppliedLicence` record.

The kernel verifier deliberately owns NO storage: the RECEIVER persists what it
last applied per licence lineage and passes it back into `verify_licence` as
the replay/rollback guard (kernel design brief, 0.1.0a7). One row per
`(tenant_id, licence_id)`; each successful application upserts it with the new
`licence_version`/`digest`/`validity`.

Tenant-scoped + RLS (assembly migration `a002`): a licence applies to exactly
one tenant here — in this assembly's model the licensed customer IS the tenant
(the deployment operator), and the projected WS2 grants are tenant-scoped too.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column


class TenantAppliedLicence(Base, TimestampMixin):
    """The last licence version this tenant applied, per lineage."""

    __tablename__ = "tenant_applied_licences"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "licence_id", name="uq_applied_licence_tenant_lineage"
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    licence_id: Mapped[str] = mapped_column(String(200), nullable=False)
    licence_version: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(128), nullable=False)
    # "valid" | "in_grace" — the verifier's explicit validity state at apply time.
    validity: Mapped[str] = mapped_column(String(20), nullable=False)
