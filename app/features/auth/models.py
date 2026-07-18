"""Auth feature-local models.

`AuthSession` moved to `app.core.models` (needed by `app.core.deps.require_user_auth`
— core cannot import features). `UserCredential` stays here: nothing outside
`app.features.auth.service` touches it, so it remains feature-local. It references
`parties` and `tenants` via string-form `ForeignKey`/`ForeignKeyConstraint` only —
no import of `app.core.models.Party`/`Tenant` classes needed.

No `email` column (Phase 2b.1 Task 3, finding F2): a credential-local copy of
the login email used to drift from `Party.email` — a portal edit changed the
one, `login()` read the other, so a person's login identity and their
visible profile email could silently disagree. `Party.email` (core,
`app/core/models.py`) is now the SINGLE email authority; `login()` resolves
the party by email first, then this table by `party_id` only — see
`app/features/auth/service.py::login`'s docstring and
`docs/ARCHITECTURE.md`'s "Auth credentials" ownership row. Migration
`alembic/versions/20260718_0005_single_email_authority.py` dropped the
column + its `uq_user_credentials_tenant_email` unique constraint.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, uuid_pk


class UserCredential(Base, TimestampMixin):
    __tablename__ = "user_credentials"
    __table_args__ = (
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
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
