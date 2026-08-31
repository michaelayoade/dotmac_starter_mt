"""The two closed vocabularies: what kind of thing an artifact is, and what
kind of claim an attestation makes.

## Closed in Python, unconstrained in the database

Both are `str` enums whose values are stored as plain text columns, with no
`CHECK` constraint — the same split `dotmac_ticketing` uses for ticket status,
and for the same reason. A `CHECK` is an `ALTER TABLE` on every deployment the
day a fourth kind is genuinely justified; a closed Python union is a module
version. Growth should cost a release, not a fleet migration.

## Why these are closed unions and not ADR-0008 registries

ADR-0008's open declaration registry is for vocabularies whose *members belong
to modules* and which a product must extend without a kernel change. Neither of
these qualifies, but they fail the test in different ways, and the difference is
worth stating because "make it a registry" is the wrong default in both:

* `AttestationKind` is a **safety** vocabulary. Code branches on it to decide
  what a claim proves — a signature is not an SBOM, and treating an unknown
  kind as either is how an artifact ends up looking vouched-for when nothing
  vouched for it. An unrecognised member here must fail closed, which is
  precisely what an open registry is designed not to do.
* `ArtifactKind` is a **descriptive** vocabulary and will grow — a Helm chart
  or a Debian package is a plausible fourth. It is closed anyway, for now,
  because it has no declaring module to be open against: this release ships no
  registry, and inventing one for three members would be the speculative
  generality ADR-0006 warns about. When a real fourth kind arrives with an
  owner, opening it is a deliberate decision with evidence, not a default.

The distinction matters beyond this module: an unknown value that fails closed
is a safety property, an unknown value that a deployer simply cannot act on is
a taxonomy gap. Only the second is a candidate for an open registry.
"""

from __future__ import annotations

from enum import StrEnum


class ArtifactKind(StrEnum):
    """What kind of thing the published bytes are.

    Descriptive, not behavioural: this catalogue never branches on kind. A
    deployer does, and an unrecognised kind there fails closed on its own — it
    has no apply path — rather than needing this module to refuse it.
    """

    CONTAINER_IMAGE = "container_image"
    PYTHON_WHEEL = "python_wheel"
    #: A self-contained bundle for an air-gapped or offline-authority
    #: deployment. Named here because ruling C3's `offline` update authority
    #: produces one, and the catalogue must be able to record what it produced.
    OFFLINE_BUNDLE = "offline_bundle"


class AttestationKind(StrEnum):
    """What a recorded claim about an artifact actually proves.

    Six distinct questions, deliberately not merged into "provenance":

    * `SBOM` — what is *inside* the artifact.
    * `PROVENANCE` — how and from what the artifact was *built*.
    * `SIGNATURE` — who *vouches* for it.
    * `PRODUCT_MANIFEST` — which product and capabilities the exact product
      assembly declares. Its document schema is owned by
      `dotmac_kernel.product_manifest`; this enum classifies the attestation.
    * `MODULE_DATABASE_CATALOG` — which schema, tables and columns one exact
      reusable module distribution declares. Its document schema is owned by
      `dotmac_kernel.product_database_catalog`; the claim is complete for that
      module only and says nothing about a whole product database.
    * `PRODUCT_DATABASE_CATALOG` — which namespaces, tables and columns that
      exact product assembly declares. Its document schema is owned by
      `dotmac_kernel.product_database_catalog`; Release Catalog may classify
      only a typed canonical snapshot, never an opaque caller-supplied digest.

    An artifact can have any subset. Having none is a legal state and an
    informative one; it is the reason attestations are rows rather than columns
    with defaults, so "no SBOM was recorded" is distinguishable from "an SBOM
    was recorded as empty".
    """

    SBOM = "sbom"
    PROVENANCE = "provenance"
    SIGNATURE = "signature"
    PRODUCT_MANIFEST = "product_manifest"
    MODULE_DATABASE_CATALOG = "module_database_catalog"
    PRODUCT_DATABASE_CATALOG = "product_database_catalog"


#: Every member, for exhaustiveness checks in consumers and in tests.
ARTIFACT_KINDS: frozenset[ArtifactKind] = frozenset(ArtifactKind)
ATTESTATION_KINDS: frozenset[AttestationKind] = frozenset(AttestationKind)


__all__ = [
    "ARTIFACT_KINDS",
    "ATTESTATION_KINDS",
    "ArtifactKind",
    "AttestationKind",
]
