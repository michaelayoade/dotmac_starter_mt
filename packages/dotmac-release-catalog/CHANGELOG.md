# Changelog — dotmac-release-catalog

All notable changes to the `dotmac-release-catalog` distribution. Pre-1.0 the
surface is still settling; the top-level `dotmac_release_catalog` namespace is
the stable one.

## 0.1.0a4 — 2026-08-14

**The manifest now declares the plane the migration always built.** Both tables
move from `tables` to `platform_tables` (ADR-0023). Nothing about the database
changes: `rl_0001` has created them without row-level security and REVOKEd them
from `app_user` since `0.1.0a1`, which is the PLATFORM contract, and the kernel
ledger comment beside `RELEASE_CATALOG_MIGRATION_OWNER` has said so all along.
Only the declaration disagreed.

Why it mattered. `audit_snapshot` requires RLS ENABLEd AND FORCEd plus a policy
for every table NOT declared platform, so the composed live-catalog gate would
have reported this correct migration as a broken one. It never did, because this
repository composes the module in no assembly and `mod_rel` was never walked —
so the first failure would have landed in a vendor control plane's deployment,
where the migration is right and the manifest is wrong.

`tests/test_release_catalog_platform_plane.py` closes that: it applies the
lineage to a scratch PostgreSQL database and runs the real gate over it, plus
direct canaries that no RLS exists, that `app_user` effectively holds none of
the seven table privileges, and that `platform_api` and `app_admin` can still
work. A sensitivity test re-audits the same live schema with the tables declared
TENANT and asserts the gate DOES flag them, so the green run above cannot be
green vacuously.

`tables=()` is written out rather than omitted, as `dotmac-integration` writes
it. No `supported_plane_sets`: one plane is supported, so the contract stays
atomic and an assembly makes no selection (ADR-0028).

**Kernel floor raised to `>= 0.1.0a53`**, the release that added
`ModuleManifest.platform_tables`. An earlier kernel raises `TypeError` importing
this manifest, before the `mod_rel` allocation check is reached — so, like
`dotmac-ticketing`, this module's floor is now set by a capability it consumes
rather than by its own ledger row. Not a61: the module is not selectable and
consumes no part of the ADR-0028 plane-selection mechanism.

No schema change, no migration, no model change.

## 0.1.0a3 — 2026-08-13

Adds `AttestationKind.PRODUCT_MANIFEST`, the explicit claim that an exact
product release declares the product code and capability vocabulary in a
canonical `dotmac_kernel.ProductManifestSnapshot` document.

This is not provenance: provenance explains how bytes were built, while the
product manifest explains what product/capabilities those bytes declare. The
database already stores attestation kinds as unconstrained text, so this is a
closed Python vocabulary release with no migration or kernel-floor change.

## 0.1.0a2 — 2026-08-13

Adds the public `versions_dir()` locator required by a separately installed
consumer to compose the Release Catalog Alembic lineage without guessing the
package's internal path. No schema, model, or kernel-floor change.

## 0.1.0a1 — 2026-08-12

First release. Immutable, digest-addressed published artifacts and the
attestations that vouch for them.

**Identity.** `Digest` accepts only `sha256` at exactly 64 lowercase hex
characters, refusing rather than normalising — two spellings of one digest must
not both be storable against a UNIQUE column. `pinned_reference` refuses any
reference a publisher could move, and with `expected=` proves the reference and
the digest address the same bytes. Both raise under `ArtifactIdentityError`.

**Immutability, in three layers.** `publish_artifact` validates; `platform_api`
holds SELECT and INSERT only, so no request-path role can rewrite a published
artifact; `ck_release_artifacts_ref_pins_digest` closes the raw-SQL path.
`app_admin` retains UPDATE/DELETE as the offline repair role.

**Tables.** `release_artifacts` and `artifact_attestations` in `mod_rel`,
lineage root `rl_0001_release_artifacts`. Platform catalog tables: no
`tenant_id`, no RLS, `app_user` REVOKEd.

**Vocabularies.** `ArtifactKind` and `AttestationKind` are closed in Python and
stored as plain text with no CHECK — growth should cost a module release, not a
migration on every deployment.

**Deliberately absent:** channels, pins, artifact selection, the bytes, and any
`is_current` flag. Channels and pins wait for update authority, because a pin is
desired state only under vendor-automatic authority and is otherwise an offer.

Requires `dotmac-kernel >= 0.1.0a44`, the release that allocates `mod_rel`.
