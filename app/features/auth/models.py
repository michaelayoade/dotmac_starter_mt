"""Auth feature-local models.

`AuthSession` moved to `app.core.models` (needed by `app.core.deps.require_user_auth`
— core cannot import features). `UserCredential` stays here: nothing outside
`app.features.auth.service` touches it, so it remains feature-local. It references
`parties` and `tenants` via string-form `ForeignKey`/`ForeignKeyConstraint` only —
no import of `app.core.models.Party`/`Tenant` classes needed.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, uuid_pk


class UserCredential(Base, TimestampMixin):
    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_user_credentials_tenant_email"),
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_user_credentials_tenant_party",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    party_id: Mapped[UUID] = mapped_column(
        Uuid(),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
