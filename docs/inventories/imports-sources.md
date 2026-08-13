# Bulk-import source inventory

**As of:** 2026-08-13  
**Starter:** `d7371314728db11dada3a8299a5f7b11663f3ad4`  
**ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`  
**Sub:** `4ca42f3c110e85c66d0d9900b7412ce234a1979a`  
**CRM:** `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`  
**Academy:** `5072e4a5fca9438d3838421989179b1779ef4bc7`  
**Vendor CP:** `89848017d6b87e82dd4d6ffd0b2c9eaed5f9fee8`

This is the ADR-0006 product-first evidence for `dotmac-imports`, and the
separate dossier ADR-0017 and ADR-0022 both demanded before any import work
could start. It is deliberately a different document from
[`files-sources.md`](files-sources.md): that audit ruled on stored bytes and
recorded parsing/mapping/apply as *not* file lifecycle. This one rules on what
that excluded half is, and who owns it.

The audit separated four capabilities that product code routinely fuses into one
service:

| Capability | Owner selected | Product source |
|---|---|---|
| Durable run/row ledger, dry-run→apply promotion, per-row outcomes, chunked progress | `dotmac-imports` optional module | Sub `import_runs`/`import_run_rows` and its orchestrator |
| Tabular decoding and column-mapping *mechanism* (CSV delimiter/encoding, XLSX/XLS rows, alias resolution, auto-map, preview) | `dotmac-imports` optional module | ERP `finance/import_export/base.py` |
| Field vocabulary, business validation, duplicate policy, the mutation itself, and any reversal of it | The importing domain | ERP's 23 entity importers; Sub's `_persist_row` and payment batch reversal |
| The uploaded bytes themselves | `dotmac-files` (ADR-0022) | Already decided; the run references an opaque file id |

## Mechanical census

Searched for import, CSV, XLSX, spreadsheet, ingest and bulk-load names in all
five repositories, then read the service, model, migration, caller, task and test
paths behind the hits.

- **ERP — the parsing/mapping source.** 50 tracked paths carry an
  `import_export` name and its import-named service code totals 13,439 lines;
  the finance import package alone is 8,346 lines across 13 modules, and
  `finance/import_export/base.py` is 2,383 of them. It supplies
  the fleet's only mature tabular front end: CSV/XLS/XLSX/XLSM decoding
  (`parse_xlsx_file`, `parse_xls_file`, `import_any_file`, `preview_any_file`),
  a 6-vendor source-format detector (Zoho, QuickBooks, Sage, Xero, Wave,
  FreshBooks) over a `COLUMN_ALIASES` table, `resolve_column_alias` /
  `auto_map_columns` / `PreviewResult`, a declarative `ValidationRule` set, typed
  coercions (`parse_date`, `parse_decimal`, `parse_boolean`, `parse_enum`),
  duplicate detection and batch commits. 23 concrete importers subclass it
  across finance, fixed assets, fleet, HR and PM. Tests:
  `tests/ifrs/import_export/` is 3,311 lines over 11 test modules, plus the
  architecture guard below. What ERP does **not** have is any durable record: a
  repository-wide search finds no `import_run`, `import_job` or `import_batch`
  table in any model or migration. An ERP import's outcome exists only as an
  in-memory `ImportResult` and whatever the operator was looking at.
- **Sub — the ledger source.** 11 import service/model/schema/API/task modules,
  2,640 lines. `app/models/imports.py` + migration `180_import_runs` supply the
  durable `import_runs` / `import_run_rows` pair; `app/services/import_runs.py`
  (328 lines) supplies the orchestration that ERP lacks: pending→running→
  `dry_run_ready`→completed states, `apply_from_dry_run` promotion under
  `SELECT … FOR UPDATE` with a unique `source_run_id` so one validated run can be
  applied exactly once, a per-row `SAVEPOINT` so one bad row cannot abort the
  batch, chunked commits every 200 rows so a crash keeps its progress, and
  idempotent reprocessing (a non-`pending` run is returned unchanged). Celery
  drives it from `app/tasks/imports.py`. Tests: `test_import_runs.py`,
  `test_system_import_wizard_service.py`, `test_import_rollback_money_safety.py`,
  `test_payment_import_batch_reversal.py`, `test_imports_services.py` — 1,593
  lines. Sub's own docstring records why the ledger exists: it replaced a
  settings-log JSON (`import_jobs_log`) that did not scale. Sub's *parsing* is
  the weaker half — a hand-rolled `_parse_xlsx_rows`, CSV, and per-entity
  Pydantic row models with no alias resolution or preview.
- **CRM — the duplication evidence.** `app/imports/loader.py` is byte-equivalent
  to Sub's `app/imports/loader.py` apart from line wrapping: same
  `load_csv`/`load_json`/`load_csv_content`, same `ImportError(index, detail)`
  dataclass, same `max_rows` guard. This is a copy-paste fork of the simplest
  tier of the capability, already living in two products, and it is the clearest
  single piece of evidence that the tier below the ledger is shared work rather
  than product work.
- **Academy — not an import source.** `app/services/content_import.py` (283
  lines) parses markdown chapters with YAML frontmatter into rendered HTML and
  upserts `Course`/`Chapter`, driven from `app/cli.py`. It is content authoring
  over a filesystem tree, not operator-supplied tabular data: no columns, no
  mapping, no dry run, no row outcomes, no uploaded file. Academy's CSV surface
  is **export**-only (`csv_reports.py`, `web/applications.py`). It contributes
  no import behaviour, and it is not a candidate consumer.
- **Vendor CP — nothing.** A repository-wide search for CSV, XLSX, spreadsheet,
  openpyxl, bulk or `import_run` over `src/` and `tests/` returns zero hits.
  The control plane has no import capability, no candidate slice, and must not
  be listed as one.

## Source choice

Neither source qualifies alone, and this is the audit's central finding: ERP and
Sub each built one half of the same capability and neither built the other.

- ERP has the parser and mapper with no durable record of what a run did.
- Sub has the durable record with a weak parser and no mapping mechanism.

So `dotmac-imports` is **product-first from both**: the run/row/promotion
contract is ported from Sub, the decoding/alias/preview mechanism from ERP.
Nothing here is greenfield except the joins between them.

## The three defects the module must not inherit

**1. A dry run that can reach a writer.** ERP's
`tests/architecture/test_importer_dry_run_writes_nothing.py` exists because a
statement importer called `ensure_bank_account` unconditionally: safe only by
accident, because a dry run skipped the final commit and the session close
discarded the INSERT. Once a batch-operation helper started committing to record
its own progress, the same unguarded write began landing. Sub has the identical
hazard shape — `process_import_run` commits every 200 rows, so any write reached
during a dry run would persist. Both products manage this with caller
discipline plus, in ERP's case, an AST guard.

The module removes the discipline: a dry run is given a `RowValidator` and never
holds the `RowApplier` at all. There is no code path from validation to
mutation to guard, and the guard test asserts the absence rather than the
conditionality.

**2. Domain identity on the generic table.** Sub's generic `import_run_rows`
carries `payment_id` (FK to `payments`) and `record_created`, and `ImportRun`
carries `created_payments` and `payment_batch_reversal` relationships. One
domain's money concerns are welded into the row ledger every other module would
share. The direction inverts in the module: the ledger row records only an
opaque `result` document, and the *domain* row carries the run/row reference —
which Sub already does correctly on the other side with `Payment.import_run_id`.
`PaymentImportBatchReversal` stays in Sub's billing domain; reversing money is
not an import concern.

**3. Input bytes stored inline.** `ImportRun.input_text` is a `Text` column
holding the whole uploaded payload, with Sub's own comment: *"Inline for now; an
object-store key can replace this later without touching the run/row
contract."* That later is now, and the replacement is not an object-store key
but a `dotmac-files` file id — the exact seam ADR-0022 left open. It also
strengthens apply-time verification: Sub proves an apply matches its dry run by
comparing `input_text` string equality, whereas a file reference makes it a
SHA-256 comparison the files module already computes.

## Preserved and rejected behaviour

Preserved in the initial module:

- the run lifecycle `pending → running → dry_run_ready → completed | failed`,
  and the row outcome set `ok | error | skipped`;
- one-shot promotion of a validated dry run into an apply run, locked and
  uniquely constrained so a run cannot be applied twice;
- per-row savepoint isolation, chunked durable progress, and idempotent
  reprocessing of a non-pending run;
- CSV decoding with configurable delimiter and encoding, and XLSX/XLS row
  reading behind an optional extra so the module installs without a spreadsheet
  dependency;
- alias-based column resolution, auto-mapping and a bounded preview, as a
  *mechanism* parameterised by a caller-supplied field declaration;
- typed scalar coercions and a declarative validation-rule set.

Rejected from the shared owner:

- ERP's `COLUMN_ALIASES`, `VALID_ACCOUNT_TYPES`, `VALID_CURRENCY_CODES` and the
  six accounting-vendor format detectors — that is finance vocabulary, and it
  belongs to the finance domain that supplies the field declaration;
- Sub's `ENTITY_CONFIG` module registry and its per-entity row models, for the
  same reason;
- every mutation, duplicate policy and business rule; the module never writes a
  domain row and never resolves a foreign entity;
- money reversal and any compensating transaction;
- service-layer `commit`/`rollback` ownership beyond the savepoint the row
  contract requires, and HTTP exceptions;
- the uploaded bytes, their validation and their lifecycle (`dotmac-files`);
- **export**. ERP's package is named `import_export` but contains no exporter;
  Academy owns the fleet's only formula-injection defence (`sanitize_cell` and
  the `= + - @ TAB CR` prefix set) and it is an export concern. Export is a
  separate capability with a separate source and no dossier yet. Recording it
  here is not a commitment to build it.

## Cross-cutting finding, outside this module's scope

Academy's `sanitize_cell` is the only CSV formula-injection defence found in the
audited scope. Sub and ERP both write CSV from more than ten modules each
(`web_system_export_tool.py`, `web_reports_extended.py`, `bulk_actions.py`,
`api/people/payroll.py`, …) with no equivalent guard, and both also generate
import *templates*. This is a real fleet exposure, it is not fixed by
`dotmac-imports`, and it needs its own owner. It is recorded here because the
audit found it, not because this module addresses it.

## Adoption

No product is blocked on this module today: ERP and Sub each have a working
implementation of their own half. The dossier therefore opens at
`audit-complete` with zero contract consumers, and the ratchet in
`tests/architecture/test_product_first_extraction.py` holds it there until a
real cutover happens.

Candidate consumers, in order:

1. **ERP** — cutover 1, because product-first extraction requires a qualifying
   source product to retire its local owner, and because ERP is the half with no
   durable record at all: adopting the module is a capability gain, not just a
   relocation. Gated behind the same E8 Organization-to-Tenant and composed-
   lineage decision that gates `dotmac-files`; the module's `tenant_id` cannot
   be satisfied before it.
2. **Sub** — cutover 2, retiring `import_runs.py` and the `import_runs` /
   `import_run_rows` tables into the module lineage while `web_system_import_
   wizard`'s entity configuration stays in Sub as the domain half. Sub's
   payment-provenance columns migrate to the domain side of the boundary.
3. **CRM** — cutover 3, the cheapest one: delete the forked `loader.py` and
   consume the module's typed row loader.

Neither Academy nor Vendor CP is a candidate. Manufacturing one would be exactly
the speculative extraction ADR-0006 § 5 and ADR-0017 forbid.
