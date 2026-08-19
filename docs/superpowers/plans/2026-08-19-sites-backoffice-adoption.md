# `dotmac-sites` implementation and Backoffice adoption plan

**Status:** Gate 1 controlled RED recorded at exact commit
`9f735b4ba5d7fd6c529c9d1d289aa0e245af2541`; Gate 2 may start.
**First adopter:** Backoffice. **Second candidate:** Sub, independently.  
**Release/adoption:** separately gated.

## Outcome

Build one tenant-only site owner that can produce the same immutable local
release value for a local renderer and a later remote publishing adapter. Do
not build a hosting client, a second file/form owner, or a product-specific CMS.

## Gate 1 — prove the contract fails first

1. Land only the source audit, dossier, ADR, `EXTRACTION.toml`, plan and
   architecture/lifecycle/contract canaries.
2. In a fresh writable Observer checkout pinned to that commit, install only
   from the committed Poetry 2.4.1 lock and run the focused tests.
3. Require controlled missing-package failures; a pass or skip is a defect.
4. Record the exact commit, command and counts here and in the dossier.

**Recorded 2026-08-19:** A fresh detached writable Observer checkout at
`9f735b4ba5d7fd6c529c9d1d289aa0e245af2541`, installed from the committed lock
with Poetry 2.4.1 and the bundle's complete tag set, ran:

```text
poetry run pytest -q tests/architecture/test_marketing_suite_source_audit.py \
  tests/architecture/test_sites_module.py \
  tests/unit/test_sites_lifecycle.py tests/unit/test_sites_contracts.py
```

Thirteen source/evidence cases passed and all 40 sites implementation canaries
failed with their intended missing-`dotmac_sites` messages. Ruff lint passed;
Ruff identified two formatting-only changes, which are recorded immediately
after the RED commit and do not alter a canary. No runtime package existed in
the tested tree.

## Gate 2 — implement the local owner

1. Add the service and disposable-PostgreSQL isolation canaries first.
2. Allocate kernel a77 owner/short code/branch `sites`, prefix `si`, schema
   `mod_sites`, in the same change as the manifest and root migration.
3. Add `dotmac-sites 0.1.0a1` with kernel floor `>=0.1.0a77`.
4. Implement exactly the five tenant tables, composite tenant/site foreign
   keys, immutable triggers, one-ready-revision constraint, forced RLS and
   exact grants.
5. Implement typed path/navigation/redirect/SEO validation, deterministic
   snapshots, append-only page/site revisions and flush-only services.
6. Update package/root metadata, module catalogs and as-built architecture;
   regenerate the lock only with Poetry 2.4.1 on Observer.
7. Run focused, `make check`, full unit/architecture, full disposable
   PostgreSQL and dedicated live-catalog/RLS suites on exact commits.

**Exit:** a validated local candidate that is still unpublished, unallowlisted,
uncomposed and unadopted.

## Gate 3 — remote release seam

1. Define a generic typed publication artifact if the current publishing
   contract cannot carry `SiteReleaseV1` without overloading title/body or
   variant fields.
2. Put the sites-to-publishing translation in Backoffice assembly code; neither
   module imports the other.
3. Publishing persists the request/outcomes and writes the outbox; Integrator
   selects and executes the hosting connector.
4. Reconcile release digest, target, attempt and receipt to the local snapshot;
   remote state never replaces the local artifact.

## Gate 4 — Backoffice adoption

1. Obtain separate authorization and pin released distributions, never paths.
2. Repeat the empty-owner census and fail if a competing Backoffice writer has
   appeared.
3. Compose/migrate Backoffice's tenant plane and add guarded thin adapters.
4. Prove the local renderer serves exactly the ready digest and that drafts do
   not alter the live site.
5. Only after remote Gate 3, prove end-to-end deployment/reconciliation.

Sub may later run the same released lineage in its own database. No application
shares site rows, sessions, filesystems or writers.
