# Changelog — dotmac-release-catalog

All notable changes to the `dotmac-release-catalog` distribution. Pre-1.0 the
surface is still settling; the top-level `dotmac_release_catalog` namespace is
the stable one.

## Unreleased — `0.1.0a4+dev`

**Public typed READ contracts** (`dotmac_release_catalog.facts`): `list_artifacts`
over a closed, page-bounded `ArtifactFilter`; `get_artifact` returning an
`ArtifactDetail` with its attestation history and owner-derived permitted
actions; `artifact_attestations`; and `preview_publication`, which runs the same
identity validators `publish_artifact` runs and answers as a READ.

Every read answers in frozen dataclasses, never in ORM rows: a surface handed a
session-bound `ReleaseArtifact` holds an object that lazy-loads on attribute
access and expires with the transaction, which turns a template into a query
planner.

What the module DERIVES — `evidence_state`, `attested_kinds`,
`permitted_actions`, a publication verdict — appears on no input type, so a
caller has nowhere to assert it. `EvidenceState` deliberately reports
`signature_recorded` and never "verified": this module does not fetch an
attestation URI (ADR-0009), so it cannot know that one validated.

**The declared version now carries a PEP 440 local development marker.**
`0.1.0a4` is published and tagged; `src/` has moved since, and one version may
not name two sets of importable bytes. The marker allocates nothing and cannot
be published — the next release allocates a real version.

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
