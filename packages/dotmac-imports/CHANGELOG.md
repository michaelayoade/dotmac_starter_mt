# dotmac-imports changelog

## 0.1.0a2 — 2026-08-13

Declares the database EFFECTS this lineage needs instead of naming a foreign
revision (ADR-0006 D1 amendment).

### Changed

- Exposes the installed Alembic lineage through the fleet-standard public
  `versions_dir()` locator. This completes composition readiness without
  publishing or selecting an adopter; a2 remains intentionally unreleased
  until ERP is ready for the coordinated cohort cutover.
- `im_0001_import_runs` previously read
  `depends_on = ("0001_initial_tenant_schema",)`. That edge is true only in an
  assembly that runs the kernel lineage: ERP hosts `public.tenants` in its own
  lineage and can never run kernel `0001`, so the module was un-installable
  there for want of a foreign-key target. The manifest now declares
  `requires=("tenant_scope_catalog.v1", "module_database_roles.v1")`, the root
  resolves its `depends_on` from the assembly's bindings, and `upgrade()` proves
  both effects against the live catalog before any DDL.
- Kernel floor raised to `>=0.1.0a56`, the release that added the prerequisite
  contract. A kernel below it cannot import this manifest.


## 0.1.0a1 — unreleased

First cut of the import-run ledger (ADR-0025). Extracted product-first from two
sources, because neither had the whole capability: the run/row/promotion
contract from `dotmac_sub`'s `import_runs` service and tables, the decoding,
alias resolution, auto-mapping and preview from `dotmac_erp`'s
`finance/import_export/base.py`.

- `contracts` — run and row status, `FieldSpec`/`FieldSet` (the domain's
  vocabulary, never the module's), `ColumnMapping`, `SourceDocument`,
  `RunProgress`, and the two ports `RowValidator` / `RowApplier`.
- `tabular` — CSV decoding with BOM tolerance, duplicate-heading refusal and a
  row ceiling; alias-based `auto_map`; `preview` that reports what is unmapped
  and what is missing rather than refusing.
- `models` — `import_runs` and `import_run_rows` in `mod_imports`, both
  tenant-scoped with FORCEd RLS; row values are represented by a SHA-256
  fingerprint rather than copied into a second retention surface.
- `service` — `create_dry_run`, `validate_next_chunk` (no applier parameter),
  `promote` (checksum-verified, uniquely constrained), `apply_next_chunk`, and
  typed `mark_failed`. Each processing call locks and advances one durable
  chunk from independently verified raw bytes; running runs resume and
  completed re-delivery is a no-op.
- expected domain refusals use bounded `ImportIssue` / `RowRejected` detail;
  unexpected exceptions escape and roll back the attempted chunk rather than
  leaking raw exception text into the ledger.
- CSV rows wider than their header are rejected instead of silently truncated.

Three source defects deliberately not inherited: a dry run that could reach a
writer, domain foreign keys on the shared row table, and the uploaded payload
stored inline in a `Text` column.

XLSX/XLS decoding is not in this release; `SourceLayout.XLSX` is declarable and
`decode` refuses it. No product consumer yet — the dossier is `audit-complete`.
