# Changelog — dotmac-release-catalog

All notable changes to the `dotmac-release-catalog` distribution. Pre-1.0 the
surface is still settling; the top-level `dotmac_release_catalog` namespace is
the stable one.

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
