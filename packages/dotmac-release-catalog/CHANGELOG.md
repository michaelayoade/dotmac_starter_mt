# Changelog — dotmac-release-catalog

All notable changes to the `dotmac-release-catalog` distribution. Pre-1.0 the
surface is still settling; the top-level `dotmac_release_catalog` namespace is
the stable one.

## 0.1.0a5 — unreleased

Adds two distinct upstream-admission claims:
`vulnerability_policy_result` and `compatibility_result`. A managed deployment
may now bind an upstream artifact to the exact immutable result documents that
admitted it, instead of accepting request-carried references or pretending an
upstream image has a Dotmac `product_manifest`.

Also adds the Dotmac-product-only `capability_contract` attestation. A product
manifest answers which capability codes exact product bytes declare; each
capability-contract document separately binds the typed operations, schemas,
configuration, endpoints and evidence gates for one of those codes. An exact
artifact may carry several such digest-pinned documents. Upstream artifacts
cannot carry them, because that would let third-party bytes mint Dotmac-owned
contract semantics.

`capability_schema` separately binds every canonical schema document referenced
by those contracts. Keeping schema bytes as their own attestations lets a
consumer prove exact reference, `$id` and digest coverage without making the
contract document an unversioned schema bundle or trusting request-carried
bytes. Upstream artifacts cannot carry these product-owned schema claims.

`capability_composition` binds a suite/product owner's canonical, value-free
mapping from one exact APPLY output schema path to another exact APPLY input
path. It never contains a runtime value, installation or connector binding;
those are resolved later under the approved deployment plan. Like the contract
and schema kinds, it is a Dotmac-product-only attestation.

The kinds stay separate from `provenance`: how bytes were built does not answer
whether a versioned vulnerability policy accepted them, and neither answers
whether they are compatible with a managed profile.

Adds `rl_0002_artifact_origin`, which records the catalogue-owned origin class
on each artifact and enforces the evidence regime in Postgres as well as the
service: an upstream artifact cannot carry a Dotmac product manifest,
capability contract, capability schema or capability composition, and a Dotmac artifact cannot carry
upstream admission results. Existing catalogue
rows are backfilled as `dotmac_product`; new raw-SQL writes must state the
origin explicitly.

## 0.1.0a4 — 2026-08-15

**The persistence plane is now declared correctly.** This module always built
control-plane tables — no `tenant_id`, no RLS, grants to `platform_api` and
`app_admin`, `REVOKE ALL` from `app_user` — but the manifest declared them under
`tables=`, the TENANT slot. No DDL changed; the declaration did.

The mismatch mattered: ADR-0023 § 2 makes the plane declared and never inferred,
and the live-catalog gate holds each plane to its own contract, so these tables
were being audited against the tenant contract they can never satisfy.

The module is ATOMIC and says so by saying nothing — `supported_plane_sets` is
omitted rather than written as an explicit `()`. That keeps the kernel floor at
`0.1.0a56`, the earliest published kernel with `platform_tables`; writing the
keyword would raise the floor to `a61` where that constructor field first
appears, for a value the default already supplies.

**Kernel floor raised to `>=0.1.0a56`**, which is the honest minimum for a
module that declares `platform_tables` at all.

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
