"""The licence tables, bound to the `mod_licensing` schema (ADR-0006 D1).

Platform catalog tables: no `tenant_id`, no RLS, `app_user` REVOKEd. Issuance is
a control-plane act; a tenant data plane RECEIVES a signed envelope and verifies
it offline through `dotmac_kernel.licensing` — it never reads the issuer's tables
to learn what it may do. That asymmetry is the whole point of a signed licence,
and ADR-0057 § 7 declares the plane from it.

## There is no private-key column, and that is structural

`SigningKey` holds the PUBLIC half and a rotation status. Custody lives in the
product behind the `LicenceSigner` port (ADR-0009, hard rule 20), so a database
dump, a backup, a replica and a `SELECT *` in a support session are all
incapable of leaking signing material — because the column does not exist, not
because a policy says not to select it.

This is ported verbatim in intent from the source implementation, whose own
docstring makes the same point: *"a database dump can never leak signing
material — structurally, not by convention."*

## Six tables, and why each is separate

- `signing_keys` — public material + rotation status. The registry the
  distributed verification keyring is built from.
- `licences` — a LINEAGE, one per `(subject_ref, product_code, generation)`. Its
  `id` is the `licence_id` inside every document of the lineage, and the
  receiver's replay/rollback guard keys on it.
- `licence_issuances` — one immutable issued version: the exact signed payload's
  digest, the signing `key_id`, the envelope verbatim, and the lifecycle status
  of that version. A change is a NEW version, never an edit.
- `licence_acknowledgements` — append-only reports of what a deployment
  installed. Separate from the issuance because an issuance is what the ISSUER
  did and an acknowledgement is what a REMOTE party claims; conflating them puts
  a remote claim in a column that reads as issuer fact.
- `revocations` — append-only revoked lineages, unique per licence so revoking
  twice is idempotent rather than duplicating the fact.
- `revocation_lists` — immutable published snapshots with a monotonic
  `list_version`, which connected and air-gapped deployments import as-is.

## `generation` exists for one reason, and it is not tidiness

Revocation is by `licence_id` and is PERMANENT. Once a lineage is revoked, the
contracted recovery path — re-issuing for the same subject and product — needs a
genuinely new lineage to issue into. Without the discriminator the resolver
returns the revoked lineage and every "recovery" document is dead on arrival at
every deployment. Generations start at 1 and advance only when the current one
has been revoked.

## No foreign key leaves this schema

`subject_ref`, `agreement_ref`, `allocation_ref` and `deployment_ref` are bare
strings. A licence is enforceable authority that must stay verifiable after the
agreement row is archived and the allocation's retention has passed, and ADR-0006
D1 forbids the cross-lineage foreign key that would splice three module lineages
into one release unit.

## Status is text with no CHECK

ADR-0008's reason: adding a lifecycle member should cost a module release, not
an `ALTER TYPE` on every deployment. Transition legality is proven by the
service's guard and by the append-only acknowledgement record, both of which are
testable; a CHECK would restate the value set without restating a single rule.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

#: JSONB in production, portable `JSON` everywhere else — the module's logic
#: tests run on in-memory SQLite, and a bare `JSONB` column makes the whole
#: model layer unimportable there. The MIGRATION names `JSONB` unconditionally:
#: the production dialect is not in question, only the test dialect is.
_JSON_DOC = JSON().with_variant(JSONB(), "postgresql")

#: Derived from the allocated short code — never a literal here. The migration
#: uses a literal on purpose (a frozen historical artifact); runtime models
#: resolve through the ledger so drift between the two is a boot failure.
SCHEMA: str = module_schema("licensing")

_KEYS = "signing_keys"
_LICENCES = "licences"
_ISSUANCES = "licence_issuances"
_ACKS = "licence_acknowledgements"
_REVOCATIONS = "revocations"
_REVOCATION_LISTS = "revocation_lists"


class SigningKeyStatus(StrEnum):
    """Mirrors the kernel verifier's `KeyStatus`, and must keep mirroring it.

    `active` signs new documents and verifies. `retired` verifies ONLY — the
    rotation overlap that lets an installed base keep working while the fleet
    updates at its own pace. `revoked` verifies nothing, even a
    cryptographically valid signature, and is for compromise only; every lineage
    signed by a revoked key must be re-issued at a higher version.

    Three values, and the middle one is the one that makes rotation
    non-breaking. An implementation with only active/revoked forces every
    deployment to update in lockstep with the issuer.
    """

    ACTIVE = "active"
    RETIRED = "retired"
    REVOKED = "revoked"


class IssuanceStatus(StrEnum):
    """The lifecycle of ONE issued licence version.

    `ISSUED` — signed and frozen; the receiver has not reported yet.
    `ACTIVE` — a deployment acknowledged applying exactly this version+digest.
    `SUSPENDED` — authority withheld without destroying the record; the
    contracted response to a payment hold, and deliberately reversible.
    `REVOKED` — permanent, by lineage. Recovery is a new generation.
    `EXPIRED` — the validity window closed. Clock-driven and guarded.
    `REPLACED` — superseded by a higher version of the same lineage.

    `REPLACED` is separate from `EXPIRED` because they answer different
    questions: a replaced version was fine and is simply no longer current; an
    expired one ran out. A model that collapsed them could not tell an operator
    whether re-issuing would help.
    """

    ISSUED = "issued"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"
    REPLACED = "replaced"


#: Statuses from which no further transition is legal. Named once so the guard
#: and the tests cannot disagree about which they are.
TERMINAL_ISSUANCE_STATUSES: frozenset[str] = frozenset(
    {
        IssuanceStatus.REVOKED.value,
        IssuanceStatus.EXPIRED.value,
        IssuanceStatus.REPLACED.value,
    }
)


class AcknowledgementOutcome(StrEnum):
    """What the receiver reported. The kernel's shared cross-plane vocabulary,
    not a local variant — neither plane invents its own spelling."""

    APPLIED = "applied"
    REJECTED = "rejected"


class SigningKey(Base, TimestampMixin):
    """A signing key's PUBLIC material and rotation status. No private key."""

    __tablename__ = _KEYS
    __table_args__ = (
        UniqueConstraint("key_id", name="uq_signing_keys_key_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Base64 of the PUBLIC key. There is deliberately no sibling column for the
    #: private half; see the module docstring.
    public_key_b64: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SigningKeyStatus.ACTIVE.value
    )


class Licence(Base, TimestampMixin):
    """A licence lineage. `id` is the `licence_id` carried in every document."""

    __tablename__ = _LICENCES
    __table_args__ = (
        UniqueConstraint(
            "subject_ref",
            "product_code",
            "generation",
            name="uq_licences_subject_product_generation",
        ),
        CheckConstraint("generation >= 1", name="ck_licences_generation"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    #: Opaque. The counterparty as the assembly identifies it — never resolved,
    #: dereferenced or joined here (ADR-0019 § 1, ruling A3).
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(String(120), nullable=False)
    #: See the module docstring: this is the revoked-lineage recovery path, not
    #: a version number.
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    issuances: Mapped[list[LicenceIssuance]] = relationship(
        lambda: LicenceIssuance,
        back_populates="licence",
        order_by=lambda: LicenceIssuance.version,
    )


class LicenceIssuance(Base, TimestampMixin):
    """One immutable signed version of a lineage, and its lifecycle."""

    __tablename__ = _ISSUANCES
    __table_args__ = (
        UniqueConstraint("licence_id", "version", name="uq_issuance_version"),
        #: One issued version per allocation. Two licences for one allocation
        #: would mean the same entitlement authorised twice, which is exactly
        #: what an idempotent issuer must make impossible.
        UniqueConstraint("allocation_ref", name="uq_issuance_allocation"),
        UniqueConstraint("digest", name="uq_issuance_digest"),
        CheckConstraint("version >= 1", name="ck_issuance_version"),
        CheckConstraint("record_version >= 1", name="ck_issuance_record_version"),
        CheckConstraint("grace_days >= 0", name="ck_issuance_grace_days"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    licence_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.{_LICENCES}.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: Strictly monotonic within the lineage. Exactly what the receiver's
    #: replay/rollback guard keys on.
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Provenance. Opaque, unconstrained — see the module docstring.
    agreement_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    allocation_ref: Mapped[str] = mapped_column(String(200), nullable=False)

    #: `sha256:<hex>` of the exact signed payload bytes — the identity an
    #: acknowledgement is matched against.
    digest: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The PRIMARY signer's key id. Overlap signatures are inside the envelope;
    #: this column answers "which key does this issuance belong to" for the
    #: re-issue sweep after a key is revoked.
    key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The signed envelope, verbatim — what an assembly hands to the Integrator
    #: for delivery. This module performs no delivery (ADR-0024).
    envelope: Mapped[dict[str, Any]] = mapped_column(_JSON_DOC, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=IssuanceStatus.ISSUED.value, index=True
    )
    #: Optimistic concurrency, for the same reason the agreement header carries
    #: one: two operators suspending and reinstating from two screens is the
    #: ordinary case, and last-writer-wins silently discards a decision.
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: NULL is PERPETUAL, which is a different contractual choice from expired
    #: and must stay expressible.
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Bound licence when set; absent means deliberately PORTABLE.
    deployment_ref: Mapped[str | None] = mapped_column(String(200))

    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    replaced_by_version: Mapped[int | None] = mapped_column(Integer)

    licence: Mapped[Licence] = relationship(lambda: Licence, back_populates="issuances")


class LicenceAcknowledgement(Base, TimestampMixin):
    """One append-only report of what a deployment did with a licence.

    Append-only because it is EVIDENCE that a remote party applied or rejected a
    specific document. A row that could be edited would let the issuer's own
    record of a remote claim be rewritten, which is the one thing that makes an
    acknowledgement worth storing.
    """

    __tablename__ = _ACKS
    __table_args__ = (
        #: One report per (issuance, outcome, reporting deployment). A target
        #: retrying its report is idempotent; two different targets reporting on
        #: one issuance are two facts and both are kept.
        UniqueConstraint(
            "issuance_id",
            "outcome",
            "reported_deployment_ref",
            name="uq_ack_issuance_outcome_deployment",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    issuance_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.{_ISSUANCES}.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: Copied from the report and checked against the issuance before storing.
    #: Kept denormalised so the acknowledgement stays readable as a standalone
    #: record of what was claimed.
    licence_version: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(128), nullable=False)

    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The stable rejection code — a kernel `LicenceError` subclass name.
    reason: Mapped[str | None] = mapped_column(String(120))
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: What the report CLAIMS about itself.
    reported_deployment_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    #: What the TRANSPORT authenticated, when it did. Deliberately a separate
    #: column: a self-declared identity inside a payload is not authentication,
    #: and one column holding both would make "did we verify this?"
    #: unanswerable after the fact.
    authenticated_deployment_ref: Mapped[str | None] = mapped_column(String(200))


class Revocation(Base, TimestampMixin):
    """One revoked lineage. Append-only, unique per licence.

    There is deliberately no "unrevoke" row type and no `revoked` boolean to
    clear. Revocation is permanent by contract; recovery is re-issuance under a
    new generation.
    """

    __tablename__ = _REVOCATIONS
    __table_args__ = (
        UniqueConstraint("licence_id", name="uq_revocations_licence"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    licence_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.{_LICENCES}.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_ref: Mapped[str | None] = mapped_column(String(200))


class RevocationList(Base, TimestampMixin):
    """An immutable published snapshot of the full revoked set."""

    __tablename__ = _REVOCATION_LISTS
    __table_args__ = (
        UniqueConstraint("list_version", name="uq_revocation_list_version"),
        CheckConstraint("list_version >= 1", name="ck_revocation_list_version"),
        CheckConstraint("entry_count >= 0", name="ck_revocation_list_entry_count"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    list_version: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(128), nullable=False)
    key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The signed envelope, verbatim — what deployments import.
    envelope: Mapped[dict[str, Any]] = mapped_column(_JSON_DOC, nullable=False)


__all__ = [
    "SCHEMA",
    "TERMINAL_ISSUANCE_STATUSES",
    "AcknowledgementOutcome",
    "IssuanceStatus",
    "Licence",
    "LicenceAcknowledgement",
    "LicenceIssuance",
    "Revocation",
    "RevocationList",
    "SigningKey",
    "SigningKeyStatus",
]
