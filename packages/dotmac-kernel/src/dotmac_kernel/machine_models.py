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

## What was ADDED, and why the row grew

**`source_application`** — which fleet peer this credential belongs to. Neither
source has it: Sub identifies its CRM caller by the presence of an
`integration:crm` scope, which means the identity of the caller is inferred from
one of its permissions. Grant that scope to a second key and two applications
are indistinguishable in the trail forever. Attribution has to be its own field
because it answers a different question from authorization, and answering both
with one column means changing either one silently changes the other.

Nullable at the schema for exactly one release (see migration
`0028_machine_attribution`), and `authenticate_machine` REFUSES a credential
whose attribution is NULL. Nullable at rest is never permissive at runtime here:
the column is open only so an existing deployment can attribute its rows before
the NOT NULL lands, and an un-attributed row cannot authenticate meanwhile.

**`next_key_hash` / `rotation_started_at` / `rotated_at`** — the rotation
window. Sub's `rotate_api_key` overwrites `key_hash` in place, and its own
docstring says "the old secret stops working immediately": every caller holding
the previous key fails until somebody redeploys it. That is a defect for an
UNATTENDED machine caller, which is the only kind this table serves. Two
digests, one row: both keys authenticate to the same principal with the same
scopes, so a caller migrates at its own pace and the trail does not fork.
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
from dotmac_kernel.source_applications import SOURCE_APPLICATION_MAX_LENGTH


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
        # Composite with `tenant_id`, and the earlier global-unique version of
        # this constraint was WRONG. The argument for global was "one raw key
        # must resolve to at most one credential, or RLS silently decides which
        # answered". That premise is false: the tenant is established BEFORE
        # this lookup, so RLS leaves exactly one visible candidate and the
        # resolution is unambiguous either way.
        #
        # What global uniqueness DID do is leak. Two tenants issuing the same
        # raw key — operator copy-paste, not a hash collision — would give the
        # second an inexplicable constraint violation about a row it cannot
        # see, which is both a denial and a disclosure that a key exists
        # elsewhere. `tests/test_rls_catalog.py` names exactly that.
        #
        # Composite is also the stronger isolation: a key minted for one tenant
        # cannot authenticate in another, because the row is invisible there.
        UniqueConstraint(
            "tenant_id", "key_hash", name="uq_machine_credentials_tenant_key_hash"
        ),
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
        # The rotation half of the row. Composite with `tenant_id` for the same
        # reason `key_hash` is, and NULLs do not collide in either dialect, so
        # every credential that is NOT rotating sits outside this constraint.
        UniqueConstraint(
            "tenant_id",
            "next_key_hash",
            name="uq_machine_credentials_tenant_next_key_hash",
        ),
        CheckConstraint(
            "next_key_hash IS NULL OR next_key_hash LIKE 'hmac-sha256:%'",
            name="ck_machine_credentials_next_key_hash_scheme",
        ),
        # Rotating to the secret you already hold is not a rotation, and the
        # row would then accept one digest through two columns — which makes
        # `complete_rotation` a no-op that looks like it worked.
        CheckConstraint(
            "next_key_hash IS NULL OR next_key_hash <> key_hash",
            name="ck_machine_credentials_next_key_hash_differs",
        ),
        # The pair moves together or the window is unreadable: a `next_key_hash`
        # with no start time is a second live secret nobody can date, and a
        # start time with no next hash is a rotation somebody believes is open.
        CheckConstraint(
            "(next_key_hash IS NULL) = (rotation_started_at IS NULL)",
            name="ck_machine_credentials_rotation_pair",
        ),
        CheckConstraint(
            "source_application IS NULL OR ("
            "length(source_application) > 1 "
            "AND trim(source_application) = source_application)",
            name="ck_machine_credentials_source_application_shape",
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

    #: WHICH APPLICATION this credential belongs to. Nullable for one release
    #: only — an existing deployment attributes its rows before the NOT NULL
    #: lands — and `authenticate_machine` refuses a NULL, so the open column is
    #: never an open door. There is no "unknown" or "system" value: an
    #: attribution nobody can name is a credential nobody should be holding.
    source_application: Mapped[str | None] = mapped_column(
        String(SOURCE_APPLICATION_MAX_LENGTH), index=True
    )

    #: The INCOMING secret during an explicit rotation window. When set, BOTH
    #: it and `key_hash` authenticate, to the same principal with the same
    #: scopes — that is what lets a caller migrate without a failed request.
    #: There is no expiry on it and nothing that clears it on a timer: the old
    #: key stops working when an operator says so (`complete_rotation`), never
    #: because a clock ran out while a caller was still holding it.
    next_key_hash: Mapped[str | None] = mapped_column(String(120))

    #: When the window opened. Recorded so "how long has this been half-rotated"
    #: is answerable by query rather than by memory — an operational report, and
    #: deliberately NOT an input to any authentication decision.
    rotation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    #: When a window last CLOSED. Distinct from `updated_at`, which any edit
    #: moves; this one only moves when a secret was actually replaced.
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["MachineCredential"]
