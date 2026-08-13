# ADR-0025: Imports own the run, never what a row means

**Status:** Accepted
**Date:** 2026-08-13
**Decision owner:** Michael
**Scope:** FLEET-WIDE for the module boundary; the module itself is optional.
**Relates to:** ADR-0006 (product-first extraction), ADR-0017 (adoption is the
scarce resource — this is the separate import dossier and decision it demanded),
ADR-0022 (`dotmac-files` owns stored bytes), ADR-0023 (dual-plane modules),
ADR-0024 (apps compose by synchronizing data), hard rule 11 (tenant-scoped
tables), hard rule 24 (product-first extraction).

**Acceptance note, 2026-08-13.** Michael explicitly directed implementation of
`dotmac-imports` as a named owner exception to ADR-0017. The audit did not find
an independently blocked product, so this acceptance does not reinterpret a
dossier, duplicated code, or two candidate consumers as satisfying ADR-0017's
demand-pulled exception. It is the same narrow owner-directed instrument used
for `dotmac-files` and `dotmac-ticketing`; it creates no general exception for
another unadopted facility.

## Context

ADR-0022 drew a line and stopped at it. `dotmac-files` owns bytes; PDF and
spreadsheet signature checks there are *file admission*, and semantic parsing,
column mapping, dry-run/apply and domain mutation were pushed out to "a future
`dotmac-imports` owner". ADR-0017 kept import/export under the adoption
moratorium and required a separate dossier and decision before anything could be
built. [`docs/inventories/imports-sources.md`](../inventories/imports-sources.md)
is that dossier. This is that decision.

The audit's central finding is that two products each built one half of the same
capability and neither built the other:

- **ERP** has the tabular front end — CSV/XLS/XLSX/XLSM decoding, alias
  resolution, auto-mapping, preview, typed coercions, declarative validation
  rules, 23 concrete importers over one 2,383-line base, 3,311 lines of tests —
  and **no durable record of any run**. An import's outcome lives in an
  in-memory result object and in whatever the operator was looking at.
- **Sub** has the durable record — `import_runs` / `import_run_rows`, the
  `pending → running → dry_run_ready → completed` lifecycle, one-shot promotion
  of a validated run into an apply run, per-row savepoints, chunked commits,
  idempotent reprocessing, 1,593 lines of tests — and the weaker parser, with no
  alias resolution and no mapping mechanism at all.
- **CRM** carries a byte-equivalent fork of Sub's typed row loader. The same
  file, in two products, differing only in line wrapping.

Neither product is *blocked* today; each has a working half. What the fleet has
is one capability implemented twice, incompletely, in opposite directions.

Three defects in the sources are load-bearing for this decision, because a
straight port would inherit all three.

**A dry run can reach a writer.** ERP's
`tests/architecture/test_importer_dry_run_writes_nothing.py` exists because a
statement importer called `ensure_bank_account` unconditionally. It was safe by
accident — a dry run skipped the commit and the session close discarded the
INSERT — until a helper that commits to record its own progress made the same
unguarded write land. Sub has the identical hazard: its orchestrator commits
every 200 rows. Both products manage this by discipline, and ERP additionally by
an AST guard that checks the write is *conditional*, deliberately not that it is
*correct* (it declines to analyse polarity, and says so).

**Domain identity sits on the generic table.** Sub's shared `import_run_rows`
carries `payment_id` and `record_created`; `ImportRun` carries `created_payments`
and `payment_batch_reversal`. One domain's money concerns are welded into the
ledger every other domain would share.

**The uploaded bytes are stored inline.** `ImportRun.input_text` is a `Text`
column holding the whole payload, carrying Sub's own comment that an object-store
key could replace it later. ADR-0022 already built the owner for that.

## Decision

### 1. The module owns the run; the domain owns the meaning

`dotmac-imports` owns the durable record of *that an import happened and what
became of each row*: the run, its lifecycle, its rows, their outcomes, the
promotion of a validated dry run into an apply run, and the progress that
survives a crash.

It never owns what a row means. The importing domain supplies the field
declaration, the validation, and the mutation — and remains the only writer of
its own tables. The module cannot name an entity, resolve a foreign key, or
construct a domain object, because it has no vocabulary in which to do so.

Concretely, the domain implements two ports and the module holds them
separately:

- a **validator** — given a mapped row, return bounded, typed, persistence-safe
  errors or nothing;
- an **applier** — given a validated row, perform the domain mutation and return
  an opaque result document.

The run is advanced by one caller-transaction-owned chunk at a time. Each call
locks and reloads the run, verifies the source bytes, resumes after the durable
row checkpoint, and commits each domain effect with its row outcome. A completed
re-delivery is a no-op. No generator spans commits, and no `running` run is
stranded merely because its previous worker disappeared.

### 2. A dry run does not hold the applier

The dry-run/apply split is structural, not conditional. A dry run is given the
validator only; the applier is never passed to it and does not exist on that
path. There is no `if dry_run:` to get backwards, no unguarded call site to
detect, and the architecture test asserts the *absence* of a reachable mutation
rather than the conditionality of one.

This is the one place the module deliberately improves on both sources rather
than porting them, and the improvement is what their own guard tests and
incident comments asked for.

### 3. The ledger carries no domain column

An import row records `ok | error | skipped`, a SHA-256 fingerprint of the
canonically mapped row, an optional bounded safe error code/message, and an
opaque JSON result. It does not copy the mapped row into the ledger: the source
bytes remain in `dotmac-files`, avoiding a second retention surface for customer,
employee or bank data. Raw exception text is never persisted. A domain converts
an expected refusal into the typed `RowRejected`; every unexpected exception
escapes and rolls back the attempted chunk.

It never carries a foreign key into a domain table.

The reference runs the other way: a domain row that an import created carries
the run and row id, as Sub's `Payment.import_run_id` already does correctly. Any
compensating action over what an import created — reversal, void, credit — is a
domain decision with a domain owner, and stays there. `PaymentImportBatchReversal`
is billing's, not imports'.

### 4. The input is a file reference, not a column

A run references an opaque `dotmac-files` file id. The module never validates,
stores, streams or deletes bytes, and never holds a provider key or a path.

This also strengthens the promotion check. Sub proves an apply run matches its
validated dry run by comparing the stored text for equality; with a file
reference the same guarantee is a SHA-256 comparison. Promotion verifies the
file id and recorded digest, and both validation and apply independently hash
the raw bytes they decode at their own entry points. Apply therefore has no
free-form `rows` parameter through which a caller could supply content unrelated
to the validated file.

The dependency is one-directional and optional at the contract level: imports
depends on the *file id*, not on the files module's Python. An assembly that
composes both gets the checksum check; the module never imports `dotmac_files`.

### 5. Mechanism is shared; vocabulary is not

The module ships the column-mapping *mechanism* — alias resolution, auto-map,
bounded preview — parameterised by a caller-supplied field declaration. It does
not ship ERP's `COLUMN_ALIASES`, `VALID_ACCOUNT_TYPES`, `VALID_CURRENCY_CODES`
or its six accounting-vendor format detectors, and it does not ship Sub's
`ENTITY_CONFIG`. Those are finance and ISP vocabulary that happen to live inside
a generic file; a shared module that shipped them would be making product
decisions for every future adopter.

Spreadsheet decoding beyond CSV is an optional install extra. A module that
required a spreadsheet library to be installed in order to import a CSV would
tax every adopter for a format they may never accept.

### 6. Tenant plane only

`import_runs` and `import_run_rows` are tenant-scoped: `tenant_id NOT NULL`,
composite uniques, forced RLS in the same migration (hard rule 11). Neither
source has a tenant column at all, so this is added, not ported.

No platform plane is declared. ADR-0023 requires both planes to be *declared*
when both exist; the audit found zero control-plane import capability in the
vendor control plane, and declaring a plane no product uses would be the
speculative extraction ADR-0006 § 5 forbids. If a control-plane import is ever
demanded by a real adopter, adding `platform_tables` is an additive manifest
change.

### 7. Export is not in scope

ERP's package is named `import_export` and contains no exporter. Academy owns
the fleet's only CSV formula-injection defence, and it guards an *export*. Export
is a distinct capability with a distinct source and no dossier; naming it here
is not a commitment to build it.

## Consequences

- The module opens at `audit-complete` with zero contract consumers and three
  named candidates — ERP, then Sub, then CRM. No product is blocked on it today,
  and the dossier says so; the two-directional ratchet in
  `tests/architecture/test_product_first_extraction.py` keeps it from claiming
  otherwise until a cutover lands.
- ERP's adoption is a capability *gain*, not a relocation: it acquires the
  durable ledger it never had. It is gated behind the same E8
  Organization-to-Tenant and composed-lineage decision that gates
  `dotmac-files`, because `tenant_id NOT NULL` cannot be satisfied before it.
- Sub's adoption retires `app/services/import_runs.py` and moves its two tables
  into the `im` lineage. Its payment provenance columns move to the domain side
  of the boundary in the same change; the ledger does not carry them across.
- CRM's adoption deletes a forked file.
- The fleet's CSV formula-injection exposure on the export side is now recorded
  and unowned. This ADR does not close it.

## Alternatives rejected

**Port ERP's `BaseImporter` as the module.** It is the better parser, but it
fuses parsing, validation, duplicate policy, entity construction and commit
behaviour into one abstract class that a subclass must inherit to use. Adopting
it would make every future importer a subclass of a shared base — the widest
possible coupling, and the opposite of two typed ports.

**Port Sub's orchestrator as the module.** It has the right lifecycle but reaches
into `web_system_import_wizard` for parsing and into billing for payment
provenance, in both directions. The lifecycle is worth porting; the reach is what
the boundary exists to remove.

**Wait for a blocked product.** This remains the default required by ADR-0017,
and neither duplication nor an `audit-complete` dossier satisfies its
demand-pulled exception. Michael instead approved `dotmac-imports` explicitly as
a named owner exception after reviewing the product-first evidence. That
direction is intentionally non-precedential: another facility still waits for
an independently blocked product or its own explicit owner decision.

**Build the export half at the same time.** Symmetry is not evidence. The audit
found no export owner worth porting and one real export defect that a shared
module would not fix.
