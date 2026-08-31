"""The catalogue's public READ contracts — typed values, never rows.

A browser surface for this module has to be buildable without a consumer ever
writing a query over `mod_rel`. That is what this file is: the closed, typed
vocabulary a screen renders, and the only shape in which the catalogue answers.

## Values, not ORM objects

Every type here is a frozen dataclass built from stdlib types. A surface handed
a live `ReleaseArtifact` holds a session-bound object that lazy-loads on
attribute access, mutates under it, and expires when the transaction closes —
so a template becomes a query planner and a detached instance becomes a runtime
error in a render. Handing out values instead is what makes "a UI never
receives live ORM objects" a property of the type rather than a review comment.

## What the catalogue may and may NOT claim

`EvidenceState` is **coverage over recorded claims, not cryptographic
verification**. This module never fetches an attestation URI — ADR-0009's rule
that a held reference is not a network call — so it cannot know whether a
signature validates, only that one was recorded. A surface must therefore render
"signature recorded", never "verified"; the word is reserved for whoever
actually checks the bytes.

## Linking out without joining out

An artifact is referenced by offers, licences and deployment plans owned by
OTHER modules. This one holds no foreign key to any of them and never will: the
correlation keys are the artifact's own `digest` and its `(product_code,
version)` pair, which those owners already store. A surface composes across
modules by asking each owner's own read contract, never by joining tables it
does not own — and `ArtifactView` carrying no `offer_id`/`licence_id`/`plan_id`
is what keeps that structural instead of aspirational.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from dotmac_release_catalog.vocabulary import ArtifactKind, AttestationKind

# ── Owner-derived verdicts ──────────────────────────────────────────────────


class EvidenceState(StrEnum):
    """How well-vouched-for an artifact is, as far as the CATALOGUE can tell.

    Derived here, from rows this module owns, so a surface never computes it
    from an attestation list it happened to be given. A row action that decided
    its own eligibility downstream would let two screens disagree about the same
    artifact.

    Deliberately three states and not a boolean: "nothing was recorded" and
    "things were recorded, none of them a signature" are different operational
    situations, and collapsing them hides the second behind the first.
    """

    #: No attestation of any kind has been recorded.
    UNATTESTED = "unattested"
    #: Attestations exist, but none of them is a `SIGNATURE`.
    UNSIGNED = "unsigned"
    #: A `SIGNATURE` attestation is recorded. NOT "the signature is valid" —
    #: see the module docstring.
    SIGNATURE_RECORDED = "signature_recorded"


class ArtifactAction(StrEnum):
    """What may still be done to a published artifact.

    Owner-derived and carried on the read, because eligibility is the
    catalogue's decision and not a button's. In particular there is no `EDIT`
    and no `WITHDRAW` member at all: the online `platform_api` role holds SELECT
    and INSERT and nothing else, so a surface offering either would be offering
    an action the database will refuse. An action a screen cannot be told about
    is an action a screen cannot render.
    """

    ATTEST = "attest"


class PublicationRefusal(StrEnum):
    """Why publishing a candidate artifact would be refused.

    The typed form of what `publish_artifact` raises, so an operator can be told
    BEFORE they submit rather than by an exception afterwards.
    """

    DIGEST_MALFORMED = "digest_malformed"
    REFERENCE_UNPINNED = "reference_unpinned"
    DIGEST_ALREADY_PUBLISHED = "digest_already_published"
    PRODUCT_VERSION_KIND_TAKEN = "product_version_kind_taken"


# ── Views ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AttestationView:
    """One recorded claim about an artifact."""

    id: UUID
    artifact_id: UUID
    attestation_kind: str
    #: Held, never dereferenced (ADR-0009). A surface renders it as text or as
    #: a link the OPERATOR follows; this module does not fetch it.
    uri: str
    #: The digest OF THE DOCUMENT, not of the artifact.
    digest: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class ArtifactView:
    """One published artifact, and what the catalogue derives about it."""

    id: UUID
    product_code: str
    version: str
    artifact_kind: str
    #: The identity. Also the correlation key an offer, a licence or a
    #: deployment plan holds — see the module docstring.
    digest: str
    artifact_ref: str
    size_bytes: int | None
    source_revision: str | None
    #: When the publisher released it.
    published_at: datetime | None
    #: When the catalogue LEARNED of it. Kept distinct from `published_at`
    #: because back-filling a catalogue makes them differ by months.
    recorded_at: datetime
    #: Which kinds of claim are on file. Derived, never supplied.
    attested_kinds: frozenset[str]
    evidence_state: EvidenceState


@dataclass(frozen=True, slots=True)
class ArtifactDetail:
    """One artifact with its full attestation history and permitted actions."""

    artifact: ArtifactView
    attestations: tuple[AttestationView, ...]
    #: Owner-derived. A surface renders these; it does not decide them.
    permitted_actions: tuple[ArtifactAction, ...]


# ── The read inputs ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ArtifactFilter:
    """What an operator may narrow an artifact list by.

    A closed set of typed fields. A surface that could pass a predicate — a SQL
    fragment, a sort column, a raw `where` — would make every future query the
    client's decision, and the catalogue could no longer say what its own read
    surface is.

    There is deliberately nowhere here to state an artifact's evidence state,
    its attested kinds or its permitted actions: those are derived by this
    module from rows it owns, and an input that could carry one would be an
    input a client could use to assert it. `attested_with` SELECTS on recorded
    claims; it does not assert one.

    `page_size` is bounded by the type rather than by the caller, because an
    unbounded list is how a catalogue screen becomes a full-table scan the day
    the fleet publishes its ten-thousandth artifact.
    """

    product_code: str | None = None
    version: str | None = None
    artifact_kind: ArtifactKind | None = None
    #: Only artifacts carrying a recorded claim of this kind.
    attested_with: AttestationKind | None = None
    page: int = 1
    page_size: int = 50

    MAX_PAGE_SIZE: ClassVar[int] = 200

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page is 1-based")
        if not 1 <= self.page_size <= self.MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be 1..{self.MAX_PAGE_SIZE}")


@dataclass(frozen=True, slots=True)
class ArtifactPage:
    """One page of artifacts, and enough to render a pager honestly.

    `total` counts what matches the FILTER, not the page, so a surface can say
    "showing 50 of 412" without a second query and without guessing.
    """

    artifacts: tuple[ArtifactView, ...]
    total: int
    page: int
    page_size: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


@dataclass(frozen=True, slots=True)
class PublicationPreview:
    """Whether publishing this candidate would be accepted, and if not, why.

    A READ. It runs the same identity validators `publish_artifact` runs and
    checks the same two uniques, so an operator sees the refusal before they
    commit to it rather than as an exception afterwards.

    `would_publish` is derived here and appears on no input type: a caller
    states the candidate's facts — the bytes' digest, the publisher's reference
    — and the catalogue states the verdict. A shape where the client could
    supply the verdict is a shape where someone eventually will.
    """

    would_publish: bool
    refusals: tuple[PublicationRefusal, ...]
    #: The validator's own message, when there was one. For the operator, not
    #: for branching: branch on `refusals`.
    detail: str | None = None
    #: The already-published artifact this candidate collides with, if any.
    conflicting_artifact_id: UUID | None = None


__all__ = [
    "ArtifactAction",
    "ArtifactDetail",
    "ArtifactFilter",
    "ArtifactPage",
    "ArtifactView",
    "AttestationView",
    "EvidenceState",
    "PublicationPreview",
    "PublicationRefusal",
]
