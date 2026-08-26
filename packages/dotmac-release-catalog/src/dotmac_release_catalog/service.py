"""The only supported way to write the catalogue.

Every field that has an invariant is validated here, and the models are
deliberately not usable as a construction API for anything that matters:
`publish_artifact` and `attest_artifact` are the seam, and both go through
`identity.Digest` and `identity.pinned_reference` unconditionally.

## Why a factory and not model `__init__` validation

A SQLAlchemy model is constructed in a dozen ways the class does not see —
`session.merge`, a bulk insert, a row loaded and mutated, Alembic data
migrations. Validation attached to `__init__` looks total and is not. Putting it
in one function that is the documented entry point makes the gap visible rather
than hidden, and the database constraints behind it catch what bypasses it:
`ck_release_artifacts_ref_pins_digest`, the two uniques, and `platform_api`
holding no UPDATE privilege at all.

That layering is the point. This function makes the common path correct and
gives a good error; the grants and the CHECK make the uncommon path *impossible*
rather than merely discouraged.

## Immutability

There is no `update_artifact`. Not "there is one and it raises" — there is no
such function, because the online role has no UPDATE privilege to call it with.
A published artifact that was recorded wrongly is corrected by an offline
`app_admin` migration under review, which is a different act with a different
audit trail.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_release_catalog.identity import Digest, pinned_reference
from dotmac_release_catalog.models import ArtifactAttestation, ReleaseArtifact
from dotmac_release_catalog.vocabulary import (
    ArtifactKind,
    ArtifactOrigin,
    AttestationKind,
)


class UnknownArtifactError(LookupError):
    """No artifact with that id, so there is nothing to attest."""


class AttestationOriginError(ValueError):
    """An attestation kind contradicts the artifact's immutable origin."""


def publish_artifact(
    db: Session,
    *,
    product_code: str,
    version: str,
    artifact_kind: ArtifactKind,
    origin: ArtifactOrigin,
    digest: str | Digest,
    artifact_ref: str,
    size_bytes: int | None = None,
    source_revision: str | None = None,
    published_at: datetime | None = None,
) -> ReleaseArtifact:
    """Record a published artifact. Flush-only — the caller owns the transaction.

    Raises `DigestError` if the digest is not one this catalogue accepts, and
    `UnpinnedReferenceError` if the reference is not digest-pinned or pins
    different bytes than `digest`. Both share `ArtifactIdentityError`.

    `artifact_kind` is typed as the enum rather than `str` so a caller that
    invents a kind fails at the type checker, not at a text column that would
    have accepted anything.
    """
    parsed = digest if isinstance(digest, Digest) else Digest.parse(digest)
    # `expected=` is the half that matters: it proves the reference and the
    # digest address the same bytes, which is the failure that passes every
    # other check and still deploys the wrong artifact.
    reference = pinned_reference(artifact_ref, expected=parsed)

    artifact = ReleaseArtifact(
        product_code=product_code,
        version=version,
        artifact_kind=ArtifactKind(artifact_kind).value,
        origin_class=ArtifactOrigin(origin).value,
        digest=str(parsed),
        artifact_ref=reference,
        size_bytes=size_bytes,
        source_revision=source_revision,
        published_at=published_at,
    )
    db.add(artifact)
    # Flush, never commit: `dotmac_kernel.db` is the one transaction authority
    # (hard rule 8), and a module that committed would take a decision belonging
    # to the assembly's request or job boundary.
    db.flush()
    return artifact


def attest_artifact(
    db: Session,
    *,
    artifact_id: UUID,
    attestation_kind: AttestationKind,
    uri: str,
    digest: str | Digest,
) -> ArtifactAttestation:
    """Record a claim about an artifact.

    `digest` is the digest OF THE ATTESTATION DOCUMENT, not of the artifact —
    without it, "the SBOM at this URI" is a mutable pointer by another route.
    `uri` is stored and never fetched: ADR-0009's rule that a held reference is
    not a network call applies here too.
    """
    artifact = db.get(ReleaseArtifact, artifact_id)
    if artifact is None:
        raise UnknownArtifactError(
            f"no artifact {artifact_id}; attest only a published artifact"
        )

    kind = AttestationKind(attestation_kind)
    origin = ArtifactOrigin(artifact.origin_class)
    upstream_only = {
        AttestationKind.VULNERABILITY_POLICY_RESULT,
        AttestationKind.COMPATIBILITY_RESULT,
    }
    dotmac_only = {
        AttestationKind.PRODUCT_MANIFEST,
        AttestationKind.CAPABILITY_CONTRACT,
        AttestationKind.CAPABILITY_SCHEMA,
        AttestationKind.CAPABILITY_COMPOSITION,
    }
    if kind in dotmac_only and origin is not ArtifactOrigin.DOTMAC_PRODUCT:
        raise AttestationOriginError(
            "an upstream artifact cannot carry a Dotmac product-owned attestation"
        )
    if kind in upstream_only and origin is not ArtifactOrigin.UPSTREAM_THIRD_PARTY:
        raise AttestationOriginError(
            "a Dotmac artifact cannot carry upstream admission results"
        )

    parsed = digest if isinstance(digest, Digest) else Digest.parse(digest)
    attestation = ArtifactAttestation(
        artifact_id=artifact_id,
        attestation_kind=kind.value,
        uri=uri,
        digest=str(parsed),
    )
    db.add(attestation)
    db.flush()
    return attestation


__all__ = [
    "AttestationOriginError",
    "UnknownArtifactError",
    "attest_artifact",
    "publish_artifact",
]
