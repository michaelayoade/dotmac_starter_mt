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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_release_catalog import facts
from dotmac_release_catalog.identity import (
    ArtifactIdentityError,
    Digest,
    DigestError,
    pinned_reference,
)
from dotmac_release_catalog.models import ArtifactAttestation, ReleaseArtifact
from dotmac_release_catalog.vocabulary import ArtifactKind, AttestationKind


class UnknownArtifactError(LookupError):
    """No artifact with that id, so there is nothing to attest."""


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
    """
    if db.get(ReleaseArtifact, artifact_id) is None:
        raise UnknownArtifactError(
            f"no artifact {artifact_id}; attest only a published artifact"
        )

    parsed = digest if isinstance(digest, Digest) else Digest.parse(digest)
    attestation = ArtifactAttestation(
        artifact_id=artifact_id,
        attestation_kind=AttestationKind(attestation_kind).value,
        uri=uri,
        digest=str(parsed),
    )
    db.add(attestation)
    db.flush()
    return attestation


# ── Reads ───────────────────────────────────────────────────────────────────
#
# The catalogue's read surface, and the reason it lives HERE rather than in a
# consuming assembly: query construction over `mod_rel` is this module's alone.
# The moment a consumer writes its own `select(ReleaseArtifact)` it has taken a
# second read authority over a schema it does not own, and every future column
# rename becomes a cross-repository break. The vendor control plane owns the
# operator workflow; this module owns its tables.


def _evidence_state(attested_kinds: frozenset[str]) -> facts.EvidenceState:
    """Derived here, from rows, so no caller can assert it.

    Note what it does NOT say: a recorded signature is a recorded signature.
    This module never fetches the attestation document, so it can never report
    that one verified.
    """
    if not attested_kinds:
        return facts.EvidenceState.UNATTESTED
    if AttestationKind.SIGNATURE.value in attested_kinds:
        return facts.EvidenceState.SIGNATURE_RECORDED
    return facts.EvidenceState.UNSIGNED


def _artifact_view(
    row: ReleaseArtifact, attested_kinds: frozenset[str]
) -> facts.ArtifactView:
    return facts.ArtifactView(
        id=row.id,
        product_code=row.product_code,
        version=row.version,
        artifact_kind=row.artifact_kind,
        digest=row.digest,
        artifact_ref=row.artifact_ref,
        size_bytes=row.size_bytes,
        source_revision=row.source_revision,
        published_at=row.published_at,
        recorded_at=row.created_at,
        attested_kinds=attested_kinds,
        evidence_state=_evidence_state(attested_kinds),
    )


def _attestation_view(row: ArtifactAttestation) -> facts.AttestationView:
    return facts.AttestationView(
        id=row.id,
        artifact_id=row.artifact_id,
        attestation_kind=row.attestation_kind,
        uri=row.uri,
        digest=row.digest,
        recorded_at=row.created_at,
    )


def _attested_kinds(
    db: Session, artifact_ids: list[UUID]
) -> dict[UUID, frozenset[str]]:
    """Which claim kinds are on file, for a whole page in one query.

    One query for the page rather than one per row: a list screen that lazily
    walked `artifact.attestations` would issue N+1 queries and would only be
    noticed once the catalogue was large, which is exactly when it hurts.
    """
    if not artifact_ids:
        return {}
    rows = db.execute(
        select(
            ArtifactAttestation.artifact_id, ArtifactAttestation.attestation_kind
        ).where(ArtifactAttestation.artifact_id.in_(artifact_ids))
    ).all()
    collected: dict[UUID, set[str]] = {}
    for artifact_id, kind in rows:
        collected.setdefault(artifact_id, set()).add(kind)
    return {artifact_id: frozenset(kinds) for artifact_id, kinds in collected.items()}


def list_artifacts(
    db: Session, filter: facts.ArtifactFilter | None = None
) -> facts.ArtifactPage:
    """One page of published artifacts matching a typed, closed filter.

    Ordering is by `(product_code, version, artifact_kind)` — the triple the
    `uq_release_artifacts_product_version_kind` unique already guarantees is
    total. Not `published_at`, which is nullable and not unique: a pager over an
    unstable order shows one row twice and skips another as the catalogue grows
    under it, which reads as data loss rather than as a sorting bug.
    """
    criteria = filter if filter is not None else facts.ArtifactFilter()
    conditions = []
    if criteria.product_code is not None:
        conditions.append(ReleaseArtifact.product_code == criteria.product_code)
    if criteria.version is not None:
        conditions.append(ReleaseArtifact.version == criteria.version)
    if criteria.artifact_kind is not None:
        conditions.append(
            ReleaseArtifact.artifact_kind == ArtifactKind(criteria.artifact_kind).value
        )
    if criteria.attested_with is not None:
        conditions.append(
            select(ArtifactAttestation.id)
            .where(
                ArtifactAttestation.artifact_id == ReleaseArtifact.id,
                ArtifactAttestation.attestation_kind
                == AttestationKind(criteria.attested_with).value,
            )
            .exists()
        )

    total = db.execute(
        select(func.count()).select_from(ReleaseArtifact).where(*conditions)
    ).scalar_one()
    rows = (
        db.execute(
            select(ReleaseArtifact)
            .where(*conditions)
            .order_by(
                ReleaseArtifact.product_code,
                ReleaseArtifact.version,
                ReleaseArtifact.artifact_kind,
            )
            .offset((criteria.page - 1) * criteria.page_size)
            .limit(criteria.page_size)
        )
        .scalars()
        .all()
    )
    kinds = _attested_kinds(db, [row.id for row in rows])
    return facts.ArtifactPage(
        artifacts=tuple(
            _artifact_view(row, kinds.get(row.id, frozenset())) for row in rows
        ),
        total=int(total),
        page=criteria.page,
        page_size=criteria.page_size,
    )


def artifact_attestations(
    db: Session, artifact_id: UUID
) -> tuple[facts.AttestationView, ...]:
    """The attestation history for one artifact, oldest first.

    Ordered by `(created_at, id)` rather than by `created_at` alone: two claims
    recorded in the same transaction share a timestamp, and a history that
    reorders itself between two renders of the same page is not a history.
    """
    rows = (
        db.execute(
            select(ArtifactAttestation)
            .where(ArtifactAttestation.artifact_id == artifact_id)
            .order_by(ArtifactAttestation.created_at, ArtifactAttestation.id)
        )
        .scalars()
        .all()
    )
    return tuple(_attestation_view(row) for row in rows)


def get_artifact(db: Session, artifact_id: UUID) -> facts.ArtifactDetail | None:
    """One artifact, its attestation history, and what may still be done to it.

    `permitted_actions` is derived HERE. A row action that decided its own
    eligibility downstream would let two screens disagree about one artifact —
    and there is no `EDIT` action to derive at all, because the online
    `platform_api` role holds no UPDATE privilege to serve one with.
    """
    row = db.get(ReleaseArtifact, artifact_id)
    if row is None:
        return None
    attestations = artifact_attestations(db, artifact_id)
    kinds = frozenset(view.attestation_kind for view in attestations)
    return facts.ArtifactDetail(
        artifact=_artifact_view(row, kinds),
        attestations=attestations,
        permitted_actions=(facts.ArtifactAction.ATTEST,),
    )


def preview_publication(
    db: Session,
    *,
    product_code: str,
    version: str,
    artifact_kind: ArtifactKind,
    digest: str | Digest,
    artifact_ref: str,
) -> facts.PublicationPreview:
    """Whether publishing this candidate would be accepted — as a READ.

    It runs the same validators `publish_artifact` runs and checks the same two
    uniques, so an operator learns of a refusal before submitting rather than
    from a traceback afterwards.

    The verdict is derived here and there is nowhere to pass one in. A caller
    states the candidate's facts — the digest of bytes it holds, the reference
    the publisher produced — and this module states whether they are admissible.
    """
    refusals: list[facts.PublicationRefusal] = []
    detail: str | None = None
    parsed: Digest | None = None

    try:
        parsed = digest if isinstance(digest, Digest) else Digest.parse(digest)
    except DigestError as exc:
        refusals.append(facts.PublicationRefusal.DIGEST_MALFORMED)
        detail = str(exc)

    if parsed is not None:
        try:
            # `expected=` is the half that matters, here as in `publish_artifact`:
            # a reference that pins DIFFERENT bytes passes every other check.
            # A malformed digest inside the reference is reported against the
            # reference, because that is the value the operator has to fix.
            pinned_reference(artifact_ref, expected=parsed)
        except ArtifactIdentityError as exc:
            refusals.append(facts.PublicationRefusal.REFERENCE_UNPINNED)
            detail = detail if detail is not None else str(exc)

    conflicting: UUID | None = None
    if parsed is not None:
        published = db.execute(
            select(ReleaseArtifact).where(ReleaseArtifact.digest == str(parsed))
        ).scalar_one_or_none()
        if published is not None:
            refusals.append(facts.PublicationRefusal.DIGEST_ALREADY_PUBLISHED)
            conflicting = published.id

    taken = db.execute(
        select(ReleaseArtifact).where(
            ReleaseArtifact.product_code == product_code,
            ReleaseArtifact.version == version,
            ReleaseArtifact.artifact_kind == ArtifactKind(artifact_kind).value,
        )
    ).scalar_one_or_none()
    if taken is not None:
        refusals.append(facts.PublicationRefusal.PRODUCT_VERSION_KIND_TAKEN)
        conflicting = conflicting if conflicting is not None else taken.id

    return facts.PublicationPreview(
        would_publish=not refusals,
        refusals=tuple(refusals),
        detail=detail,
        conflicting_artifact_id=conflicting,
    )


__all__ = [
    "UnknownArtifactError",
    "artifact_attestations",
    "attest_artifact",
    "get_artifact",
    "list_artifacts",
    "preview_publication",
    "publish_artifact",
]
