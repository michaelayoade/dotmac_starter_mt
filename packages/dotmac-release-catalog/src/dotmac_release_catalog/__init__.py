"""DotMac Release Catalogue — what was published, and what vouches for it.

The vendor-side answer to one question: *which exact bytes are we entitled to
deploy, and what proves they are what we think they are?*

## The rule the whole module exists to hold

**An artifact is its content digest, never a tag.** `docs/design/domain-foundation.md`
requires that the exact artifact a deployment runs "must not become a mutable
tag such as `:latest`", and this is where that is enforced rather than assumed:
`identity.Digest` refuses anything that is not `<algorithm>:<hex>` with a known
algorithm and exact width, and `identity.pinned_reference` refuses a reference
that is not digest-pinned — optionally proving it pins *this* artifact's digest,
so the two adjacent columns cannot drift into addressing different bytes.

A tag is a pointer the publisher can move after the plan naming it was approved.
Approving `app:1.4.2` and deploying `app:1.4.2` are the same words about two
different sets of bytes if someone re-pushed in between, and nothing in the
audit trail records that they diverged. That is what a plan hash is supposed to
prevent, and it can only prevent it if identity is content.

## Deliberately not here yet

**Channels and pins are deferred until update authority exists.** Ruling C3 says
a channel pin is desired state *only* under vendor-automatic authority and is
otherwise an offer. Shipping the pin table before the authority that gates it
would let that distinction be discovered later, in production, by a deployment
that moved when nobody approved it.

Also absent, and not merely unfinished: artifact *selection* (a pure function
over a pin and a compatibility range — it belongs to the fleet part that reads
this catalogue), the artifact bytes themselves, and any `is_current` flag, which
is a mutable tag with a different spelling.

## Writing

`publish_artifact`, `attest_artifact` and the typed attestation functions are
the seam, and all route through their validators unconditionally. There is no
`update_artifact`: the online
`platform_api` role holds SELECT and INSERT only, so a published artifact cannot
be rewritten from the request path at all. Correcting one is an offline
`app_admin` migration under review — a different act, with a different trail.

## Where it may be installed

Vendor and OEM control-plane assemblies only. Its tables are platform catalog
tables with `app_user` explicitly revoked: a product data plane learns which
artifact to run from a signed licence or a deployment plan, never by reading the
vendor's catalogue.

## Public surface

Everything importable from this top-level namespace is stable. Submodules are
not: import from here.
"""

from __future__ import annotations

from dotmac_release_catalog.identity import (
    SHA256,
    ArtifactIdentityError,
    Digest,
    DigestError,
    UnpinnedReferenceError,
    pinned_reference,
)
from dotmac_release_catalog.manifest import module
from dotmac_release_catalog.migrations import versions_dir
from dotmac_release_catalog.models import (
    SCHEMA,
    ArtifactAttestation,
    ReleaseArtifact,
)
from dotmac_release_catalog.service import (
    DuplicateSingularAttestationError,
    ModuleDatabaseCatalogMismatchError,
    ProductDatabaseCatalogMismatchError,
    ProductManifestMismatchError,
    TypedAttestationRequiredError,
    UnknownArtifactError,
    attest_artifact,
    attest_module_database_catalog,
    attest_product_database_catalog,
    attest_product_manifest,
    publish_artifact,
)
from dotmac_release_catalog.vocabulary import (
    ARTIFACT_KINDS,
    ATTESTATION_KINDS,
    ArtifactKind,
    AttestationKind,
)

__version__ = "0.1.0a4"

__all__ = [
    "ARTIFACT_KINDS",
    "ATTESTATION_KINDS",
    "SCHEMA",
    "SHA256",
    "ArtifactAttestation",
    "ArtifactIdentityError",
    "ArtifactKind",
    "AttestationKind",
    "Digest",
    "DigestError",
    "DuplicateSingularAttestationError",
    "ModuleDatabaseCatalogMismatchError",
    "ProductDatabaseCatalogMismatchError",
    "ProductManifestMismatchError",
    "ReleaseArtifact",
    "TypedAttestationRequiredError",
    "UnknownArtifactError",
    "UnpinnedReferenceError",
    "__version__",
    "attest_artifact",
    "attest_module_database_catalog",
    "attest_product_database_catalog",
    "attest_product_manifest",
    "module",
    "pinned_reference",
    "publish_artifact",
    "versions_dir",
]
