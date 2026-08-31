"""The only supported way to write the catalogue.

Every field that has an invariant is validated here, and the models are
deliberately not usable as a construction API for anything that matters:
`publish_artifact`, `attest_artifact` and the typed attestation functions are
the seam. Artifact identity always goes through `identity.Digest` and
`identity.pinned_reference`; typed attestations additionally derive their
digest from the kernel contract's canonical bytes.

## Why a factory and not model `__init__` validation

A SQLAlchemy model is constructed in a dozen ways the class does not see —
`session.merge`, a bulk insert, a row loaded and mutated, Alembic data
migrations. Validation attached to `__init__` looks total and is not. Putting it
in one function that is the documented entry point makes the gap visible rather
than hidden, and the database constraints behind it catch what bypasses it:
`ck_release_artifacts_ref_pins_digest`, the unique constraints and indexes, and
`platform_api` holding no UPDATE privilege at all.

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

from dotmac_kernel.product_database_catalog import (
    ModuleDatabaseCatalogSnapshot,
    ProductDatabaseCatalogSnapshot,
)
from dotmac_kernel.product_manifest import ProductManifestSnapshot
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_release_catalog.identity import Digest, pinned_reference
from dotmac_release_catalog.models import ArtifactAttestation, ReleaseArtifact
from dotmac_release_catalog.vocabulary import ArtifactKind, AttestationKind


class UnknownArtifactError(LookupError):
    """No artifact with that id, so there is nothing to attest."""


class TypedAttestationRequiredError(ValueError):
    """A typed claim was sent through the opaque digest attestation seam."""


class ProductDatabaseCatalogMismatchError(ValueError):
    """A database-catalog snapshot identifies a different product release."""


class ModuleDatabaseCatalogMismatchError(ValueError):
    """A module catalogue identifies a different distribution artifact."""


class ProductManifestMismatchError(ValueError):
    """A product-manifest snapshot identifies a different product release."""


class DuplicateSingularAttestationError(ValueError):
    """An artifact already has the one allowed attestation of this kind."""


_SINGULAR_ATTESTATION_KINDS = frozenset(
    {
        AttestationKind.PRODUCT_MANIFEST,
        AttestationKind.MODULE_DATABASE_CATALOG,
        AttestationKind.PRODUCT_DATABASE_CATALOG,
    }
)


def _require_open_singular_slot(
    db: Session,
    *,
    artifact_id: UUID,
    kind: AttestationKind,
) -> None:
    """Give a named error before the database closes the concurrent/raw path."""
    if kind not in _SINGULAR_ATTESTATION_KINDS:
        return
    existing_id = db.scalar(
        select(ArtifactAttestation.id).where(
            ArtifactAttestation.artifact_id == artifact_id,
            ArtifactAttestation.attestation_kind == kind.value,
        )
    )
    if existing_id is not None:
        raise DuplicateSingularAttestationError(
            f"artifact {artifact_id} already has its singular {kind.value} "
            "attestation; immutable declaration claims cannot be replaced or "
            "accumulated"
        )


def publish_artifact(
    db: Session,
    *,
    product_code: str,
    version: str,
    artifact_kind: ArtifactKind,
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

    Product manifests and database catalogues are deliberately refused here.
    Their kinds prove more than possession of a digest: the service must inspect
    typed content, bind its product identity to the artifact, and derive the
    digest itself. Use the corresponding typed attestation function.
    """
    kind = AttestationKind(attestation_kind)
    if kind in _SINGULAR_ATTESTATION_KINDS:
        writer = {
            AttestationKind.PRODUCT_MANIFEST: "attest_product_manifest",
            AttestationKind.MODULE_DATABASE_CATALOG: "attest_module_database_catalog",
            AttestationKind.PRODUCT_DATABASE_CATALOG: (
                "attest_product_database_catalog"
            ),
        }[kind]
        raise TypedAttestationRequiredError(
            f"{kind.value} requires {writer}(snapshot=...); the generic "
            "digest seam cannot attest a typed declaration"
        )

    if db.get(ReleaseArtifact, artifact_id) is None:
        raise UnknownArtifactError(
            f"no artifact {artifact_id}; attest only a published artifact"
        )

    _require_open_singular_slot(db, artifact_id=artifact_id, kind=kind)
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


def attest_product_manifest(
    db: Session,
    *,
    artifact_id: UUID,
    uri: str,
    snapshot: ProductManifestSnapshot,
) -> ArtifactAttestation:
    """Attest the canonical product/capability manifest for one artifact."""

    artifact = db.get(ReleaseArtifact, artifact_id)
    if artifact is None:
        raise UnknownArtifactError(
            f"no artifact {artifact_id}; attest only a published artifact"
        )
    if type(snapshot) is not ProductManifestSnapshot:
        raise TypeError(
            "snapshot must be a ProductManifestSnapshot; opaque maps and "
            "digest-only claims are not accepted"
        )
    mismatches: list[str] = []
    if snapshot.product_code != artifact.product_code:
        mismatches.append(
            f"product_code {snapshot.product_code!r} != {artifact.product_code!r}"
        )
    if snapshot.product_version != artifact.version:
        mismatches.append(
            f"product_version {snapshot.product_version!r} != {artifact.version!r}"
        )
    if mismatches:
        raise ProductManifestMismatchError(
            "product manifest does not describe the artifact: " + "; ".join(mismatches)
        )

    _require_open_singular_slot(
        db,
        artifact_id=artifact_id,
        kind=AttestationKind.PRODUCT_MANIFEST,
    )
    parsed = Digest.parse(snapshot.digest)
    attestation = ArtifactAttestation(
        artifact_id=artifact_id,
        attestation_kind=AttestationKind.PRODUCT_MANIFEST.value,
        uri=uri,
        digest=str(parsed),
    )
    db.add(attestation)
    db.flush()
    return attestation


def attest_product_database_catalog(
    db: Session,
    *,
    artifact_id: UUID,
    uri: str,
    snapshot: ProductDatabaseCatalogSnapshot,
) -> ArtifactAttestation:
    """Attest one artifact's typed, canonical product database catalogue.

    The snapshot is content, not a digest-shaped assertion. Its product code
    and version must identify the artifact row exactly, and the stored digest
    is computed from `snapshot.to_json_bytes()` here. There is intentionally no
    caller-supplied digest parameter that could disagree with those bytes.

    Flush-only: the caller remains the transaction authority.
    """
    artifact = db.get(ReleaseArtifact, artifact_id)
    if artifact is None:
        raise UnknownArtifactError(
            f"no artifact {artifact_id}; attest only a published artifact"
        )
    if type(snapshot) is not ProductDatabaseCatalogSnapshot:
        raise TypeError(
            "snapshot must be a ProductDatabaseCatalogSnapshot; opaque maps "
            "and digest-only claims are not accepted"
        )

    mismatches: list[str] = []
    if snapshot.product_code != artifact.product_code:
        mismatches.append(
            f"product_code {snapshot.product_code!r} != {artifact.product_code!r}"
        )
    if snapshot.product_version != artifact.version:
        mismatches.append(
            f"product_version {snapshot.product_version!r} != {artifact.version!r}"
        )
    if mismatches:
        raise ProductDatabaseCatalogMismatchError(
            "database catalogue does not describe the artifact: "
            + "; ".join(mismatches)
        )

    _require_open_singular_slot(
        db,
        artifact_id=artifact_id,
        kind=AttestationKind.PRODUCT_DATABASE_CATALOG,
    )
    parsed = Digest.parse(snapshot.digest)
    attestation = ArtifactAttestation(
        artifact_id=artifact_id,
        attestation_kind=AttestationKind.PRODUCT_DATABASE_CATALOG.value,
        uri=uri,
        digest=str(parsed),
    )
    db.add(attestation)
    db.flush()
    return attestation


def attest_module_database_catalog(
    db: Session,
    *,
    artifact_id: UUID,
    uri: str,
    snapshot: ModuleDatabaseCatalogSnapshot,
) -> ArtifactAttestation:
    """Attest one module distribution's canonical tables/columns catalogue."""

    artifact = db.get(ReleaseArtifact, artifact_id)
    if artifact is None:
        raise UnknownArtifactError(
            f"no artifact {artifact_id}; attest only a published artifact"
        )
    if type(snapshot) is not ModuleDatabaseCatalogSnapshot:
        raise TypeError(
            "snapshot must be a ModuleDatabaseCatalogSnapshot; opaque maps "
            "and digest-only claims are not accepted"
        )
    mismatches: list[str] = []
    if snapshot.distribution_name != artifact.product_code:
        mismatches.append(
            f"distribution_name {snapshot.distribution_name!r} != "
            f"{artifact.product_code!r}"
        )
    if snapshot.distribution_version != artifact.version:
        mismatches.append(
            f"distribution_version {snapshot.distribution_version!r} != "
            f"{artifact.version!r}"
        )
    if mismatches:
        raise ModuleDatabaseCatalogMismatchError(
            "module database catalogue does not describe the artifact: "
            + "; ".join(mismatches)
        )
    _require_open_singular_slot(
        db,
        artifact_id=artifact_id,
        kind=AttestationKind.MODULE_DATABASE_CATALOG,
    )
    attestation = ArtifactAttestation(
        artifact_id=artifact_id,
        attestation_kind=AttestationKind.MODULE_DATABASE_CATALOG.value,
        uri=uri,
        digest=str(Digest.parse(snapshot.digest)),
    )
    db.add(attestation)
    db.flush()
    return attestation


__all__ = [
    "DuplicateSingularAttestationError",
    "ModuleDatabaseCatalogMismatchError",
    "ProductDatabaseCatalogMismatchError",
    "ProductManifestMismatchError",
    "TypedAttestationRequiredError",
    "UnknownArtifactError",
    "attest_artifact",
    "attest_module_database_catalog",
    "attest_product_database_catalog",
    "attest_product_manifest",
    "publish_artifact",
]
