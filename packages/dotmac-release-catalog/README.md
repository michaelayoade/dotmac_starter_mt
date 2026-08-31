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
publisher calls `attest_product_manifest`; the typed seam checks product code
and version against the artifact and derives the digest from the canonical
snapshot bytes. The generic digest-only seam refuses this declaration kind.
The module never fetches the URI. A consumer fetches through its own transport,
verifies the digest, and parses through the kernel contract.

Do not record this as `PROVENANCE`. Provenance says how an artifact was built;
the product manifest says which product and capability vocabulary that build
declares. Conflating them makes presence of one appear to prove the other.

## Product database-catalog attestations

`AttestationKind.PRODUCT_DATABASE_CATALOG` associates a canonical, typed
`dotmac_kernel.ProductDatabaseCatalogSnapshot` with exact artifact bytes. It is
not accepted by the generic `attest_artifact` digest seam. A publisher must call
`attest_product_database_catalog`, which verifies that the snapshot's product
code and version match the artifact and computes the attestation digest from
the snapshot's canonical bytes:

```python
from dotmac_release_catalog import attest_product_database_catalog

attest_product_database_catalog(
    db,
    artifact_id=artifact.id,
    uri="https://example.com/product-database-catalog.json",
    snapshot=database_catalog_snapshot,
)
```

Release Catalog stores the URI and derived digest; it does not fetch the URI or
infer structure from a running database. The digest is not a replacement for
typed content: a consumer fetches the exact bytes, verifies the digest, and
parses them through the kernel contract before it reasons about namespaces,
tables or columns.

`MODULE_DATABASE_CATALOG` is a distinct singular claim for one reusable module
distribution. `attest_module_database_catalog` binds `distribution_name` and
`distribution_version` to the artifact. The module release and manifest
contract versions remain separate coordinates; a module claim never implies
that a whole product database is complete.

A product manifest, module database catalogue and product database catalogue
are each singular per artifact. Migration `rl_0002_singular_attestations`
enforces that cardinality with a partial unique index while continuing to allow
multiple signatures. If existing rows contradict the rule, the migration
refuses to build the index; it never chooses one declaration as authoritative.
