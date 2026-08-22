"""The machine credential row. Tenant-scoped, and deliberately write-free at read.

Separate module rather than another entry in `models.py` for the reason the
placement rule gives: `models.py` holds what core deps and middleware query, and
this is queried by exactly one dependency in `machine_auth`. Keeping it beside
its verifier means a reader who finds one finds the other.

## What is absent, and why each absence is deliberate

**No `last_used_at`.** Sub has one and commits it during a GET; ERP has one and
refreshes it on the same path. A column that records its own reads makes every
authenticated request a write, inside the caller's transaction. There is no
column here, so there is nothing to tempt the write back in. Usage observation
belongs to the audit trail, which has an owner, a retention policy and a plane.

**No `person_id` / `subscriber_id`.** ERP refuses a credential without a human
and then loads the `Person`; Sub falls back to the credential's own id when the
subscriber is absent, so the field means two different things depending on how
the row was made. A machine is not a person, and a nullable link that is
sometimes the machine and sometimes a human is worse than no link.

**No `roles`.** Authority is the enumerated `scopes` and nothing else.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy import Uuid as SAUuid
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk


class MachineCredential(Base, TimestampMixin):
    """One `X-Api-Key` a machine may present, scoped to one tenant.

    `scopes` is NOT NULL with no default. A credential must SAY what it may do,
    and the row cannot exist without an answer — which is the schema half of
    refusing ERP's "empty means everything". The application half is
    `MachinePrincipal.has_scope`, and an empty list there authorizes nothing, so
    the two halves agree instead of one rescuing the other.
    """

    __tablename__ = "machine_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_machine_credentials_tenant",
        ),
        # The hash is globally unique: a raw key must resolve to at most one
        # credential regardless of tenant, or the same secret could be minted
        # twice and RLS would decide which one answered.
        UniqueConstraint("key_hash", name="uq_machine_credentials_key_hash"),
        UniqueConstraint(
            "tenant_id", "label", name="uq_machine_credentials_tenant_label"
        ),
        CheckConstraint(
            "length(trim(label)) > 0", name="ck_machine_credentials_label_nonempty"
        ),
        CheckConstraint(
            "key_hash LIKE 'hmac-sha256:%'",
            name="ck_machine_credentials_key_hash_scheme",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(SAUuid(), nullable=False)

    #: An operator-facing name. Appears in the forbidden-scope message, so it
    #: must never carry key material — it is a label, not a hint.
    label: Mapped[str] = mapped_column(String(120), nullable=False)

    #: `hmac-sha256:<hex>` and nothing else. The scheme prefix is CHECKed so a
    #: row written by an older or weaker hasher cannot sit here unnoticed.
    key_hash: Mapped[str] = mapped_column(String(120), nullable=False)

    #: Exactly what this credential may do. Empty authorizes nothing.
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["MachineCredential"]
