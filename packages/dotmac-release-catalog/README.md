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
