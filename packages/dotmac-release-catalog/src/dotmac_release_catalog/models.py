"""The catalogue's tables, bound to the `mod_rel` schema (ADR-0006 D1).

Every model carries `schema_table_args(SCHEMA)`, so the ORM emits fully
qualified `mod_rel.<table>` rather than resolving through `search_path` —
connection state a pooler or another module can change.

## Platform catalog tables, not tenant-scoped

Neither table has a `tenant_id`, and neither has RLS. A published artifact is a
**vendor-wide fact**: the same bytes, the same digest, the same SBOM, whoever is
looking. Giving it a tenant column would assert that two tenants can disagree
about what `sha256:abc…` contains, which is false, and would then have to be
maintained as a lie in every join.

This is hard rule 11's documented platform-catalog case, and it is the shape the
vendor control plane's own `offer_versions` already uses: GRANT to `platform_api`
and `app_admin`, REVOKE from `app_user`. A product data plane must not read this
table at all — it learns which artifact it should run from a signed licence or a
deployment plan, never by querying the vendor's catalogue.

## What is here, and what deliberately is not

`release_artifacts` is a thing that was published; `artifact_attestations` is
what is claimed about it. That is the whole first slice. Notably absent:

* **No channels, no pins.** Deferred until update authority exists. A channel
  that can repoint desired state without an authority to gate it is ruling C3's
  exact failure — a pin is only desired state under vendor-automatic authority,
  and is otherwise an *offer*. Shipping the table first would let that
  distinction be discovered later, in production, by a deployment.
* **No selection.** `ArtifactSelection` is a pure function of a pin, a
  compatibility range and a current digest. It needs channels, and it belongs to
  the fleet part that reads this catalogue rather than to the catalogue itself.
* **No bytes, and no upload path.** The registry stores the artifact; this
  stores the fact that it exists and what vouches for it. A catalogue that also
  served the bytes would be two availability problems wearing one name.
* **No `is_current`, no `latest` flag.** Both are a mutable tag with a different
  spelling. What is current is a property of a *deployment*, not of an artifact.

## Immutability is enforced by PRIVILEGE, not by convention

Rows are never updated after insert, and that is not a rule a service is trusted
to keep: `platform_api` — the online request-path role — is granted SELECT and
INSERT and nothing else, so no request handler, no future router and no ad-hoc
query on that connection *can* rewrite a published artifact. `app_admin`, the
offline migration role, retains UPDATE/DELETE, because a mis-recorded artifact
has to be correctable by someone and confining that to reviewed migrations makes
it a deliberate act rather than an accident during a request.

`ck_release_artifacts_ref_pins_digest` closes the remaining gap in the other
direction: raw SQL that never calls `identity.pinned_reference` still cannot
store a reference addressing different bytes than the `digest` column beside it.

`updated_at` therefore exists and should always equal `created_at` on rows the
online role wrote. The live proofs are in
`tests/test_release_catalog_immutability.py`, which drives the real roles rather
than asserting this paragraph.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    BigInteger,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

#: Derived from the module's allocated short code — never a literal here. The
#: migration uses a literal on purpose (a frozen historical artifact); runtime
#: models resolve through the ledger so a drift between the two is a boot
#: failure rather than a silent split-brain.
SCHEMA: str = module_schema("rel")

_ARTIFACTS = "release_artifacts"
_ATTESTATIONS = "artifact_attestations"


class ReleaseArtifact(Base, TimestampMixin):
    """One immutable, digest-addressed artifact a vendor has published."""

    __tablename__ = _ARTIFACTS
    __table_args__ = (
        # The content address is the identity. Unique fleet-wide and not
        # scoped to a product: the same bytes published under two product
        # codes are one artifact, and recording them twice would let two
        # rows disagree about what vouches for identical content.
        UniqueConstraint("digest", name="uq_release_artifacts_digest"),
        # A product may publish exactly one artifact of a given kind per
        # version. This is what makes "version 1.4.2's container image"
        # answerable without a tie-break rule, and it is the constraint that
        # turns a re-publish into a loud failure instead of a second row.
        UniqueConstraint(
            "product_code",
            "version",
            "artifact_kind",
            name="uq_release_artifacts_product_version_kind",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()

    #: Which software this is a release of. A plain string, not an FK: the
    #: catalogue does not own the product registry, and inventing a `products`
    #: table here would make this module the authority on what products exist —
    #: a decision that belongs to whoever composes the fleet.
    product_code: Mapped[str] = mapped_column(String(120), nullable=False)

    #: The publisher's version string, recorded verbatim. Deliberately NOT
    #: parsed into a comparable ordering: version-ordering rules differ per
    #: ecosystem, and a catalogue that half-understands them would answer
    #: "is this newer?" wrongly and confidently. Ordering is the fleet part's
    #: problem, against a compatibility range it declares.
    version: Mapped[str] = mapped_column(String(120), nullable=False)

    artifact_kind: Mapped[str] = mapped_column(String(40), nullable=False)

    #: Admission class is immutable catalogue evidence. A deployment request
    #: reads it; it cannot relabel an artifact to choose weaker evidence.
    origin_class: Mapped[str] = mapped_column(String(40), nullable=False)

    #: `<algorithm>:<hex>`, validated by `identity.Digest` on the way in.
    #:
    #: 160 is sized for the vocabulary this module says it does NOT close, not
    #: for the one algorithm it accepts today: `sha512:` + 128 hex is 135
    #: characters. An 80-char column would have made the "a second algorithm is
    #: a module release, not a migration" claim false — the release would still
    #: need an ALTER TABLE on every deployment. Caught by
    #: `test_the_constraint_does_not_close_the_algorithm_vocabulary`.
    digest: Mapped[str] = mapped_column(String(160), nullable=False)

    #: The digest-pinned pull reference. Validated by `identity.pinned_reference`
    #: against the `digest` column above, so the two cannot address different
    #: bytes.
    artifact_ref: Mapped[str] = mapped_column(Text, nullable=False)

    size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    #: The exact source revision built, when the publisher knows it. Evidence,
    #: not authority — it is what the build claimed, and `PROVENANCE`
    #: attestations are what proves it.
    source_revision: Mapped[str | None] = mapped_column(String(120))

    #: When the publisher released it, which is not when this row was written.
    #: Both are kept: `created_at` is when the catalogue learned, `published_at`
    #: is when the world could have it, and back-filling a catalogue makes them
    #: differ by months.
    published_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
    )

    attestations: Mapped[list[ArtifactAttestation]] = relationship(
        back_populates="artifact",
        cascade="all, delete-orphan",
    )


class ArtifactAttestation(Base, TimestampMixin):
    """A typed pointer to something that vouches for an artifact.

    The claim lives wherever attestations live — a registry, an object store, a
    transparency log. This row records that it exists, what kind it is, and its
    own digest, so a consumer can fetch it and prove it did not change since the
    catalogue saw it.
    """

    __tablename__ = _ATTESTATIONS
    __table_args__ = (
        # One artifact may carry several signatures, so the kind alone is not
        # unique — but recording the SAME claim twice is a bug, and this makes
        # it one. Including the digest is what allows both to be true.
        UniqueConstraint(
            "artifact_id",
            "attestation_kind",
            "digest",
            name="uq_artifact_attestations_artifact_kind_digest",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()

    artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.{_ARTIFACTS}.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    attestation_kind: Mapped[str] = mapped_column(String(40), nullable=False)

    #: Where the claim can be fetched. Not dereferenced by this module —
    #: ADR-0009's rule that a held reference is not a network call applies here
    #: too: recording a pointer must never mean fetching it.
    uri: Mapped[str] = mapped_column(Text, nullable=False)

    #: The digest OF THE ATTESTATION DOCUMENT, not of the artifact. Without it,
    #: "the SBOM at this URI" is a mutable tag by another route. Same 160-char
    #: sizing, for the same reason.
    digest: Mapped[str] = mapped_column(String(160), nullable=False)

    artifact: Mapped[ReleaseArtifact] = relationship(back_populates="attestations")


__all__ = ["SCHEMA", "ArtifactAttestation", "ReleaseArtifact"]
