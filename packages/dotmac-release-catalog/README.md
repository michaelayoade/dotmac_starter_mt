# dotmac-release-catalog

**What was published, and what vouches for it.**

The vendor-side answer to one question: *which exact bytes are we entitled to
deploy, and what proves they are what we think they are?*

## The rule the module exists to hold

**An artifact is its content digest, never a tag.**

A tag is a pointer the publisher can move after the plan naming it was approved.
Approving `app:1.4.2` and deploying `app:1.4.2` are the same words about two
different sets of bytes if someone re-pushed in between — and nothing in the
audit trail records that they diverged. That is what a plan hash is supposed to
prevent, and it can only prevent it if identity is content.

So `Digest` refuses anything that is not `<algorithm>:<hex>` with a known
algorithm and exact width, and `pinned_reference` refuses a reference that is
not digest-pinned:

```python
from dotmac_release_catalog import Digest, publish_artifact, ArtifactKind

publish_artifact(
    db,
    product_code="dotmac-sub",
    version="7.100.7",
    artifact_kind=ArtifactKind.CONTAINER_IMAGE,
    digest="sha256:" + "a" * 64,
    artifact_ref="registry.example.com/dotmac/sub@sha256:" + "a" * 64,
)
```

Pass `app:latest` as the reference and it raises. Pass a reference that pins a
*different* digest than the `digest` argument and it raises — that is the
failure which passes every syntactic check and still deploys the wrong bytes.

## Origin selects the evidence contract

The catalogue records whether exact bytes are a Dotmac product or an admitted
upstream artifact. That is publication evidence, not a label a deployment
request may choose:

- `dotmac_product` may carry a canonical `product_manifest` and one or more
  canonical `capability_contract`, `capability_schema` and
  `capability_composition` attestations.
- `upstream_third_party` may carry distinct
  `vulnerability_policy_result` and `compatibility_result` attestations.

SBOM, provenance and signature claims are admissible for either origin. The
two upstream results stay separate because "this vulnerability policy accepted
these bytes" and "this managed profile is compatible with these bytes" are
different claims with different versioned documents.

`rl_0002_artifact_origin` enforces those combinations in Postgres, including
raw SQL and later offline repairs. It backfills the previously published rows
as Dotmac products and then removes the column default so every new publisher
must classify the artifact explicitly. Vendor CP selects immutable catalogue
row and attestation identities; it never accepts request-carried admission
claims or invents a product manifest for upstream software.

## Immutability is enforced, not promised

Three layers, only the first of which is code you could forget to call:

1. `publish_artifact` validates and is the documented entry point.
2. `platform_api` — the online request-path role — holds **SELECT and INSERT
   only**. There is no `update_artifact`, because the role has no privilege to
   call one with.
3. `ck_release_artifacts_ref_pins_digest` proves `artifact_ref` ends in `@` plus
   the row's own `digest`, closing the raw-SQL path.

`app_admin`, the offline migration role, keeps UPDATE/DELETE: a mis-recorded
artifact has to be correctable by someone, and confining that to the role that
already runs reviewed migrations makes it a deliberate repair rather than an
accident during a request.

## Not here, on purpose

- **Channels and pins.** Deferred until update authority exists. A channel pin
  is desired state *only* under vendor-automatic authority and is otherwise an
  offer; shipping the table first makes that distinction discoverable only in
  production.
- **Artifact selection.** A pure function over a pin and a compatibility range.
  It belongs to the fleet part that reads this catalogue.
- **The bytes.** A registry stores those. A catalogue that also served them
  would be two availability problems wearing one name.
- **Any `is_current` flag.** A mutable tag with a different spelling.

## Where it may be installed

Vendor and OEM control-plane assemblies only. Its tables are platform catalog
tables with `app_user` — the product data-plane role — explicitly REVOKEd: a
data plane learns which artifact to run from a signed licence or a deployment
plan, never by reading the vendor's catalogue.

## Contracts

`dotmac_kernel` is the only runtime dependency (ADR-0006 § 2:
`assembly → module → dotmac-ui → dotmac-kernel`). The floor is the kernel
release that allocates `mod_rel` in `MIGRATION_OWNER_LEDGER`; an earlier kernel
cannot register this module at all.

Everything importable from the top-level `dotmac_release_catalog` namespace is
stable. Submodules are not — import from the top level.

A consuming control-plane assembly adds `versions_dir()` to its Alembic
`version_locations`; the locator resolves the installed wheel's path, so the
consumer never hard-codes a source checkout or reaches into package internals.

## Product-manifest attestations

`AttestationKind.PRODUCT_MANIFEST` associates a canonical
`dotmac_kernel.ProductManifestSnapshot` with exact artifact bytes. The
attestation row stores the document URI and the digest of its exact bytes; this
module neither fetches nor interprets it. A consumer fetches through its own
transport, verifies the digest, and parses through the kernel contract.

Do not record this as `PROVENANCE`. Provenance says how an artifact was built;
the product manifest says which product and capability vocabulary that build
declares. Conflating them makes presence of one appear to prove the other.

## Capability-contract attestations

`AttestationKind.CAPABILITY_CONTRACT` associates one canonical
`dotmac_kernel.CapabilityContractSnapshot` with exact Dotmac product bytes.
The owning product may publish several contracts, so the attestation kind is
not unique per artifact; the row identity and document digest select the exact
one. The product manifest proves the capability code is declared, while the
capability contract proves its typed operations and evidence grammar. Neither
claim substitutes for the other.

`AttestationKind.CAPABILITY_SCHEMA` binds the exact canonical schema bytes
named by a contract operation, configuration field, endpoint or activation
check. The contract records a schema reference and digest; the separate schema
attestation proves which held bytes satisfy that digest. A consumer must verify
exact coverage, including each schema document's canonical `$id`, rather than
trusting a URI or request-carried document.

These attestations are refused for `upstream_third_party` artifacts. Mailcow,
Nextcloud or Keycloak bytes are admitted by vulnerability and compatibility
results; the Dotmac managed-service contract and schemas they implement are
released as their own Dotmac-owned contract artifact rather than relabelling
upstream software.

`AttestationKind.CAPABILITY_COMPOSITION` binds one canonical
`dotmac_kernel.CapabilityCompositionSnapshot`. A composition-only suite
artifact may declare no capabilities of its own: its Product Manifest owns the
composition, while exact-pinned dependency contract artifacts own every source
and target capability/schema. The document approves only an abstract,
public/non-secret APPLY-output to APPLY-input path; it carries no deployment
binding or runtime evidence value.
