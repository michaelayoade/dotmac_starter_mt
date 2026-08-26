"""The closed vocabularies: artifact kind, origin, and attestation claim.

## Closed in Python; only the security boundary is closed in the database

Artifact and attestation kinds remain plain text columns: extending their
taxonomies costs a module release, not a fleet migration. ``origin_class`` is
different. It selects which evidence regime is admissible, so ``rl_0002``
constrains its two values and trigger-checks each origin/attestation pair even
for raw SQL. The database constraint is deliberate because bypassing the
Python enum there would weaken artifact admission rather than merely introduce
an unknown descriptive kind.

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
* `ArtifactOrigin` is a **security** vocabulary as well: it chooses whether
  Dotmac product evidence or upstream admission results are accepted. It is
  therefore closed in Python and at the database write boundary.

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


class ArtifactOrigin(StrEnum):
    """Which admission contract made an artifact deployable.

    This is catalogue-owned evidence, never a label a deployment request may
    reinterpret. Dotmac products prove their assembly contract with a product
    manifest; upstream software proves policy and compatibility admission with
    two different result documents.
    """

    DOTMAC_PRODUCT = "dotmac_product"
    UPSTREAM_THIRD_PARTY = "upstream_third_party"


class AttestationKind(StrEnum):
    """What a recorded claim about an artifact actually proves.

    Nine distinct questions, deliberately not merged into "provenance":

    * `SBOM` — what is *inside* the artifact.
    * `PROVENANCE` — how and from what the artifact was *built*.
    * `SIGNATURE` — who *vouches* for it.
    * `PRODUCT_MANIFEST` — which product and capabilities the exact product
      assembly declares. Its document schema is owned by
      `dotmac_kernel.product_manifest`; this enum classifies the attestation.
    * `CAPABILITY_CONTRACT` — the typed operations, inputs, outputs,
      configuration, endpoints and evidence gates for one product-owned
      capability. Its document schema is owned by
      `dotmac_kernel.capability_contract`; one product artifact may publish
      several independently digest-addressed contracts.
    * `CAPABILITY_SCHEMA` — canonical schema bytes referenced by one of those
      contracts. The document's `$id` names the `schema:...@vN` identity and
      this row binds the exact bytes by digest so a consumer need not trust a
      request-carried schema or an unverified URI.
    * `CAPABILITY_COMPOSITION` — a product-owned, value-free mapping from an
      exact public/non-secret capability output schema path to an exact
      downstream input path. Runtime values never appear in this attestation.
    * `VULNERABILITY_POLICY_RESULT` — which versioned vulnerability policy
      evaluated the artifact, and the exact result document it produced.
    * `COMPATIBILITY_RESULT` — which versioned managed-profile compatibility
      contract evaluated the artifact, and the exact result document.

    An artifact can have any subset. Having none is a legal state and an
    informative one; it is the reason attestations are rows rather than columns
    with defaults, so "no SBOM was recorded" is distinguishable from "an SBOM
    was recorded as empty".
    """

    SBOM = "sbom"
    PROVENANCE = "provenance"
    SIGNATURE = "signature"
    PRODUCT_MANIFEST = "product_manifest"
    CAPABILITY_CONTRACT = "capability_contract"
    CAPABILITY_SCHEMA = "capability_schema"
    CAPABILITY_COMPOSITION = "capability_composition"
    VULNERABILITY_POLICY_RESULT = "vulnerability_policy_result"
    COMPATIBILITY_RESULT = "compatibility_result"


#: Every member, for exhaustiveness checks in consumers and in tests.
ARTIFACT_KINDS: frozenset[ArtifactKind] = frozenset(ArtifactKind)
ARTIFACT_ORIGINS: frozenset[ArtifactOrigin] = frozenset(ArtifactOrigin)
ATTESTATION_KINDS: frozenset[AttestationKind] = frozenset(AttestationKind)


__all__ = [
    "ARTIFACT_KINDS",
    "ARTIFACT_ORIGINS",
    "ATTESTATION_KINDS",
    "ArtifactKind",
    "ArtifactOrigin",
    "AttestationKind",
]
