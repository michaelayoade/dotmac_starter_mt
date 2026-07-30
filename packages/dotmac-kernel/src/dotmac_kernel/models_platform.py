"""Platform-actor identity models (control-plane security Task 1).

`PlatformAdmin`/`PlatformSession` are the platform control plane's OWN
identity tables — deliberately NOT tenant `Party`/`AuthSession` rows, which
are tenant-bound by design (composite `(tenant_id, ...)` FKs, tenant-claim
JWT). A platform actor exists with no tenant context at all, so reusing the
tenant identity model would either fabricate a fake "platform tenant" row or
punch holes in the tenant-claim checks; see ADR-0004.

These are PLATFORM catalog tables, like `tenants`/`tenant_domains`:
- NO `tenant_id` column and NO RLS (there is no tenant to scope by).
- GRANTed to `platform_api` + `app_admin` ONLY — explicitly REVOKEd from
  `app_user` in the same migration that creates them, so a tenant-scoped
  application session can never even SELECT a platform credential row. The
  RLS catalog test (control-plane security Task 3) encodes this as the
  platform-table allowlist with grant assertions.

They live in `dotmac_kernel/` (not a feature) for the same reason the guard does:
`dotmac_kernel.platform_auth.require_platform_admin` queries them, and the
platform surface must exist even with every feature disabled.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk


class PlatformAdmin(Base, TimestampMixin):
    """A platform-control-plane actor. Bootstrapped ONLY via
    `scripts/create_platform_admin.py` (same trust boundary as migrations) —
    there is deliberately no HTTP self-registration path."""

    __tablename__ = "platform_admins"
    __table_args__ = (
        # Case-insensitive uniqueness, mirroring `parties`' email semantics.
        Index(
            "uq_platform_admins_lower_email",
            sa.text("lower(email)"),
            unique=True,
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PlatformSession(Base, TimestampMixin):
    """Server-side session row backing a platform bearer token — same
    revocable-session shape as the tenant `AuthSession` (`token_hash` is the
    HMAC from `dotmac_kernel.security.hash_token`; the raw token is never stored).
    """

    __tablename__ = "platform_sessions"
    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="uq_platform_sessions_token_hash"),
    )

    id: Mapped[UUID] = uuid_pk()
    admin_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("platform_admins.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
