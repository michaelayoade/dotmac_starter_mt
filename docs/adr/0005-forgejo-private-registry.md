# ADR-0005: Forgejo private artifact registry (supersedes the public-PyPI R0 decision)

- **Status:** Accepted (2026-07-30). Supersedes the registry choice in the R0
  release plan (`docs/superpowers/plans/2026-07-30-kernel-release-preparation.md`).
- **Deciders:** Michael.
- **Scope:** Cross-Dotmac artifact distribution (Python wheels now; OCI, npm, and
  other formats later).

## Context

R0 ratified publishing `dotmac-kernel` to **public PyPI** via OIDC trusted
publishing. Before the first publish, the strategy was revised: Dotmac's kernel,
vendor control plane, and product packages are proprietary and should not be
world-visible, and Dotmac wants one self-hosted control plane for all artifact
formats (Python, OCI, npm) with scoped access and its own retention/backup.

## Decision

**Forgejo becomes Dotmac's authoritative private artifact registry.** GitHub
remains authoritative for source, pull requests, and CI during phase 1. **No
repository mirroring and no parallel CI authority.**

Authority split (phase 1):

| Authority | System |
|---|---|
| Source, PRs, rulesets, CI | GitHub |
| Private Python / OCI / npm / future packages | Forgejo |
| Credentials & signing material | OpenBao |
| Vendor/product lifecycle | Vendor control plane |

- **Deployment:** Forgejo runs on **dotmac-s3 (194.163.130.216)** at
  `https://registry.dotmac.io`, co-located with the existing (idle) MinIO — a
  dedicated registry host, deliberately NOT the production database tier. Package
  **blobs** live in a MinIO bucket (`dotmac-packages`); Forgejo **metadata** in a
  dedicated Postgres. Config + runbook: `deploy/forgejo/`. Forgejo compute is
  intended to migrate to a dedicated VM later (a clean repoint, since blobs are in
  MinIO and metadata in Postgres).
- **Ownership:** business owner Dotmac; initial Forgejo admin is a Dotmac-controlled
  account; a second named maintainer is added after the first release.
- **Publishing:** GitHub Actions publishes with a **narrowly-scoped
  `write:package` credential** whose canonical source is OpenBao
  (`secret/dotmac/forgejo/ci-publisher-token`). Phase 1 mirrors it into the
  protected `registry-release` GitHub environment (Michael-gated, main-only)
  because GitHub-hosted runners cannot reach the internal OpenBao; the hardening
  target is a self-hosted runner using GitHub OIDC → OpenBao JWT auth so no token
  is stored in GitHub. **PyPI trusted publishing is not used.**
- **Release gates preserved** from R0: protected `workflow_dispatch`, exact
  current-`main`-SHA gate, explicit `version` input matched to metadata,
  build-once → inspect → publish-the-same-bytes → install-and-verify from the
  registry → tag `dotmac-kernel-v<version>` only after verification. Every
  publishing-workflow action is pinned to a full commit SHA; container images are
  pinned to digests.

## Consequences / controls

- **Immutability** (compensating for Forgejo admins being able to delete/republish
  a version): the MinIO artifact bucket has **versioning + object-lock (WORM
  retention)**, so prior bytes + digests are retained tamper-evidently beneath
  Forgejo. Restrict Forgejo admin accounts; treat delete/republish as audited.
  Keep off-host/versioned backups (MinIO replication + the Forgejo-metadata
  Postgres dump).
- **Dependency confusion:** consumers (Vendor CP, Sub) resolve `dotmac-kernel`
  explicitly from the Forgejo index and must avoid an uncontrolled public
  extra-index fallback for Dotmac names (pin with hashes and/or a controlled index
  priority). Public transitive deps still come from PyPI.
- **Forgejo package-permission granularity** is currently org-oriented (finer team
  permissions are still upstream-planned); compensate with restricted
  administration + the scoped publisher token + the MinIO immutability controls.
- **Availability:** the registry is now on the critical path for every Dotmac CI
  install; monitoring, backup, and a restore rehearsal are standup prerequisites.

## Follow-ups

1. Stand up per `deploy/forgejo/RUNBOOK.md`; publish + verify `dotmac-kernel
   0.1.0a1`.
2. Pin Vendor CP and Sub to `dotmac-kernel==0.1.0a1` from the Forgejo index.
3. Decide the self-hosted-runner + OIDC→OpenBao hardening (removes the GitHub
   environment secret).
4. A future ADR governs any move of Git/CI authority off GitHub — out of scope here.
