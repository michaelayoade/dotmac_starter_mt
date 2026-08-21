# Large-import execution inventory — 2026-08-21

## Decision

Extend `dotmac-imports`; do not create a second bulk-migration module. The
existing module already owns source verification, mapping, dry-run promotion,
per-row outcomes, resumable checkpoints and tenant isolation. A second owner
would split the run ledger and make repair ambiguous.

The added lane keeps the same domain ports. It changes only how a large CSV is
read and scheduled:

- verify the original `dotmac-files` SHA-256 before deriving anything;
- stream it into bounded CSV artifacts, each stored immutably by
  `dotmac-files` and represented by an opaque file id, row range, byte size and
  SHA-256 in `mod_imports.import_partitions`;
- atomically lease one pending or expired partition with `SKIP LOCKED` and an
  opaque claim token;
- verify the complete partition before invoking the domain validator/applier;
- commit a bounded partition's domain effects, row outcomes and completion
  checkpoint together; and
- clone the exact verified partition plan when a dry run is promoted, so apply
  cannot repartition different bytes.

## Product evidence

The fleet was inspected at these exact revisions:

| Product | Revision | Qualifying import surface | Large-run finding |
|---|---|---|---|
| ERP | `c656bb9070b7f35659f1968e44823e4727b309b9` | `app/services/finance/import_export/base.py`, `import_service.py`; `app/services/imports/formats.py` | Rich domain validators and CSV/XLS(X) decoders, but no durable import partition/shard ledger, immutable partition checksum, or import-worker claim. |
| Sub | `4ff82824646d0eb61c4f560034e3d51252507302` | `app/services/import_runs.py`, `app/imports/loader.py` | The qualifying durable run source, including 200-row commits; the complete input still lives/decodes as one payload and has no partition worker ledger. |
| CRM | `60daaa2dd305696636632f48505ab784110a55d2` | `app/imports/loader.py` | A local loader only; no qualifying large-run mechanism. It is inventory evidence, not a revived consumer or owner. |
| Academy | `a5e25e4e829350e503e66a03d73739529ba7da7f` | content import search | No qualifying tabular large-migration implementation. |
| Starter | `aac7a105333101817b629aad10f9d3e1f555f490` | `packages/dotmac-imports` 0.1.0a2 | Existing implementation is the mandatory base: CSV dry-run/mapping/validation, stored-file SHA-256, durable row outcomes, 200-row chunks, savepoints, replay and RLS; it loads the full CSV on every chunk and caps runs at 50,000 rows. |

Searches for import partitioning, streaming CSV ingestion, PostgreSQL COPY and
worker checkpoints found reusable concurrency patterns (`SKIP LOCKED`) but no
qualifying import-specific implementation. Reusing the pattern inside the
existing owner is therefore product-first generalisation, not a greenfield
parallel owner.

## Boundaries and deferrals

`dotmac-files` owns original and partition bytes. `dotmac-imports` owns only
the run, immutable partition descriptors, leases and outcomes. The consuming
application still owns field declarations, validation, mutation and every
domain consequence.

This slice deliberately does not add XLSX, a database extraction connector,
parallelism policy or PostgreSQL COPY. Those are format/transport or product
adoption slices. The mechanism permits bounded workers; the deployment decides
how many to run.

The package remains unreleased and unallowlisted until ERP provides the first
real receiver/cutover proof. CRM remains retired from the Integration programme
and is not made a candidate by appearing in this inventory.
