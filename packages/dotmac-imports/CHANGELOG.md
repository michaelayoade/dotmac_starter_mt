# dotmac-imports changelog

## 0.2.0a2 — 2026-11-03

Fresh allocation (pyproject, `__version__` and manifest agree); released
`0.2.0a1` and `im_0001_import_runs` remain immutable. Kernel floor unchanged
(`>=0.1.0a56`). No migration, no model change.

- **Partition-manifest loader** (`dotmac_imports.partition_manifest`): a
  generated manifest file listing partition objects with SHA-256 digests becomes
  `PartitionDescriptor` values and is registered through the existing
  `register_partition_plan` (new `register_manifest_plan`). Leases, expiring
  claims, checkpoints, claimed-partition settlement and the `RowApplier` port
  are reused unchanged. No domain vocabulary.
- **Format `dotmac.imports.partition-manifest` version 1** (UTF-8 JSON, closed
  members, documented in the README and module docstring) carries the run's
  source identity, the ordered partitions (`ordinal`, `start_row`, `row_count`,
  `file_id`, `byte_size`, `checksum_sha256`) and a `manifest_sha256` over the
  canonical form of everything else.
- **Refused, never interpreted:** unknown format or version, unknown or missing
  members, repeated JSON keys, duplicate partition ordinal or file id, non-
  canonical UUIDs or digests, non-contiguous ranges, partitions out of ordinal
  order, a digest that does not match the document, a digest that does not match
  a caller-pinned `expected_sha256`, and a document over 16 MiB or 100,000
  partitions. A manifest for another source is a `SourceMismatch`.
- `verify_manifest_partitions` streams each stored object (no session) and
  proves size and SHA-256. `render_partition_manifest` emits the canonical form
  for generators. `load_partition_manifest` reads a bounded file without echoing
  its path.
- Registration is idempotent: identical redelivery returns the existing plan; a
  different plan for the run is refused as immutable (existing contract).
- Corrects the `0.2.0a1` heading below: it was released (tag
  `dotmac-imports-v0.2.0a1`) and had been mislabelled "unreleased".

## 0.2.0a1 — 2026-10-15

- Replace domain computation with a private batched Rust extension behind the existing Python API.
- Ship a platform-specific CPython wheel and a complete locked native source archive.
- Keep authorization, persistence, transactions and audit with their existing Python owners.


## 0.1.0a2 — 2026-08-13

Declares the database EFFECTS this lineage needs instead of naming a foreign
revision (ADR-0006 D1 amendment).

### Changed

- Adds the large-CSV execution lane: two-pass streaming partition creation,
  immutable partition descriptors and checksums, atomic expiring worker claims,
  bounded validation/apply entry points, and completion checkpoints settled
  with row outcomes. `dotmac-files` still owns every byte and product code still
  owns validation and mutation.
- Reads and verifies claimed partition bytes between the claim and settlement
  transactions, so storage I/O never holds a database lock. Settlement refuses
  an expired or replaced claim and rechecks its immutable source identity.
- Adds typed `RowSkipped` outcomes for valid rows that need no domain write and
  a tenant-scoped `get_run_outcomes` projection for row-for-row adopter parity;
  the projection exposes no imported values, domain result payloads or ORM rows.
- Exposes the installed Alembic lineage through the fleet-standard public
  `versions_dir()` locator. ERP is the named first adopter; release eligibility
  does not make it a proven contract consumer before its exact pin merges.
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
`decode` refuses it. No proven product consumer yet — the dossier remains
`audit-complete` until ERP's exact pin and shadow cutover merge.
