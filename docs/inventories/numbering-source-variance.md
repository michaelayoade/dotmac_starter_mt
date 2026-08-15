# Numbering source variance

**As of:** 2026-08-15
**Baseline document:** `docs/inventories/numbering-sources.md` as committed at
starter `5e11fb2` (PR #178). That file is not present in the starter working
tree at the time of writing; it was read via
`git show 5e11fb2:docs/inventories/numbering-sources.md` and byte-compared
against the two agent worktree copies, which are identical.

**Heads resolved for this revalidation:**

| Repository | Dossier pin | Current `origin/main` | Drift |
| --- | --- | --- | --- |
| `dotmac_starter_mt` | `991213c5ccaf` | `d4f43a4` on `main`; worktree on `fix/appdir-import-safety` at `fd3d68bd` | moved |
| `dotmac_erp` | `0f4b1698ddbf` | `7fc1111f73ca926eb8729a0dff589a3dfad896a8` | **30 commits** |
| `dotmac_sub` | `27c76aaeebb7` | `65f6cb06d6e2beba61266368f4073f9df55197ca` | **78 commits** |

The ERP checkout is on branch `feat/kernel-ui-contract-alignment` at the old
pin with a dirty worktree; the Sub checkout is detached at the old pin. Every
citation below is a revision-pinned read against `origin/main` in each
repository (`git show origin/main:<path>`), never the working tree.

This document is a revalidation of the accepted source verdict. It does not
create the package, allocate a namespace, or change any shared registration
file.

---

## 1. Headline verdict

**The dossier's verdict holds. Its evidence does not.**

The two-source product-first ruling — ERP for the date-aware configurable
series model and the parity suite, Sub for the conflict-safe first-use shape —
survives revalidation and should stand. No mandatory path was deleted, renamed
or rewritten; all seven mandatory files are **byte-identical** between the
dossier pins and the current heads despite 108 combined commits.

But the dossier is now inaccurate in three ways that matter to the build:

1. **It credits Sub with a capability Sub does not use.** `reconcile_next_value`
   — the "monotonic reconciliation" named in the verdict — is **dead code**. It
   has exactly one reference in the entire repository: its own definition.
   Sub's live reconciliation is duplicated by hand inside three consuming
   services, each of which locks the sequence row and writes `next_value`
   directly. Sub is not a clean source for reconciliation; it is a source for
   the *row-establishment* SQL only.
2. **It understates the ERP reference-date defect to the point of inverting
   it.** The dossier lists "explicit business `reference_date`" as a shared
   contract requirement and treats ERP's `date.today()` default as a detail.
   In fact **16 of ERP's caller sites route through
   `SequenceService.get_next_number`, whose signature has no `reference_date`
   parameter at all** — including AR invoice, GL journal and supplier invoice.
   Two callers in the entire repository pass a business date. The contract
   requirement is not a port delta; it is an API break at three quarters of the
   cutover surface.
3. **Its defect list is incomplete in a load-bearing way.** Five defects were
   recorded; five more of comparable severity are present at head, including a
   backdated-allocation counter rewind that can produce duplicate numbers under
   a legal configuration, and an entirely unregistered sixth ERP allocator.

**Do not trust `numbering-sources.md` past its date.** Treat it as the source
*ruling* and treat this document as the current defect and cutover evidence.

---

## 2. Revalidation of the mandatory paths

Every path the dossier names still exists, at the same path, with no content
change between the pin and the head.

### ERP — `origin/main` = `7fc1111f`

| Mandatory path | Present | Lines | `git diff 0f4b1698..origin/main` |
| --- | --- | --- | --- |
| `app/models/finance/core_config/numbering_sequence.py` | yes | 197 | empty |
| `app/services/finance/common/numbering.py` | yes | 551 | empty |
| `app/services/finance/common/sequence_utils.py` | yes | 59 | empty |
| `tests/ifrs/common/test_numbering_service.py` | yes | 713 | empty |

### Sub — `origin/main` = `65f6cb06d`

| Mandatory path | Present | Lines | `git diff 27c76aaeebb7..origin/main` |
| --- | --- | --- | --- |
| `app/models/sequence.py` | yes | 28 | empty |
| `app/services/numbering.py` | yes | 150 | empty |
| `tests/test_numbering_defaults.py` | yes | 121 | empty |

**Interpretation.** The sibling audit's failure mode (code moved past the
dossier) did **not** occur here. This dossier's failure mode is different and
harder to see: the code is unchanged, and the dossier's *description* of that
unchanged code was already wrong when written. Byte-identity is therefore not
reassurance — it means every inaccuracy below has been there the whole time.

One correction of fact: the dossier says "41 tracked application files". At
head the count is **43** (`git grep -l -E
"SyncNumberingService|SequenceService|NumberingService" origin/main -- app/`).
Two of those 43 are the numbering implementation and its `__init__`, so the
true caller count is 41 consumers plus 2 implementation files. The dossier's
number is right for the wrong reason; use 41 consumers.

---

## 3. The dossier's five ERP defects, at head

All five are **still present**. Three are **worse than described**.

### Defect 1 — first-use query-then-insert race → **STILL PRESENT, changed in character**

The dossier says ERP "performs query then insert". At head the read is a
*locking* read, which makes the code look safer than it is and is the more
dangerous shape:

- `app/services/finance/common/numbering.py:514-530` (`SyncNumberingService
  .generate_next_number`) — `select(...).with_for_update()` at line 520,
  `if not sequence:` at 523, `self.db.add(sequence)` at 529, `flush()` at 530.
- `app/services/finance/common/numbering.py:256-275`
  (`NumberingService._get_or_create_sequence_for_update`) — same shape,
  `.with_for_update()` at line 262, insert at 268-274.

`SELECT ... FOR UPDATE` on a predicate that matches no row acquires **no**
lock. Two concurrent first-time callers both return `None`, both `INSERT`, and
one hits `uq_sequence_type`
(`app/models/finance/core_config/numbering_sequence.py:77-81`). Because the
insert executes inside the *consuming* transaction, the unique violation
aborts the caller's whole business transaction on PostgreSQL, not just the
numbering step. There is no `ON CONFLICT` and no savepoint anywhere on this
path.

Two additional entry points do the same thing with **no lock at all**:
`get_or_create_sequence` async at lines 231-249 and sync at lines 474-492,
reached from `initialize_all_sequences` (444-453) and from
`SequenceService.configure_sequence`.

This is exactly the race Sub's `lock_sequence` docstring
(`dotmac_sub:app/services/numbering.py:27-29`) says it exists to fix. The
dossier's port-delta ruling is correct and is confirmed as mandatory.

### Defect 2 — every focused database test uses mocks → **STILL PRESENT, confirmed exhaustively**

`tests/ifrs/common/test_numbering_service.py` at head: 31 tests, a hand-written
`MockNumberingSequence` at line 24, `mock_db = MagicMock()` at line 68,
`mock_async_db = AsyncMock()` at line 73, and `patch(...select...)` wrapping
every query assertion. No session, no engine, no transaction.

Widening the search does not help. Of the 36 test files that mention numbering,
the only one that appeared to use a session —
`tests/ifrs/platform/test_sequence_service.py`, 48 `db_session` references —
is served by `tests/ifrs/platform/conftest.py`, whose own docstring (lines 1-6)
states: *"These tests use mock objects to avoid PostgreSQL-specific
dependencies while still testing the service logic."* Its `mock_db_session` is
a `MagicMock` with a hand-rolled `MockColumn` comparison shim.

**There is not one real-database numbering test anywhere in ERP.** No
concurrency, no rollback, no RLS, no unique-constraint exercise, no first-use
race. The dossier's "fresh proof required" list is not a nice-to-have; it is
the entire correctness evidence base for the module.

### Defect 3 — naive `datetime.now()` into a timezone-aware column → **STILL PRESENT**

- Column: `last_used_at: Mapped[datetime | None] = mapped_column(
  DateTime(timezone=True), nullable=True)` —
  `app/models/finance/core_config/numbering_sequence.py:136-139`.
- Writes: `sequence.last_used_at = datetime.now()` at
  `app/services/finance/common/numbering.py:320` (async) and `:539` (sync).

Naive local time written to `timestamptz`; PostgreSQL will interpret it in the
session `TimeZone`, so the recorded value silently depends on server
configuration. `created_at` (line 143) and `updated_at` (line 148) correctly use
`func.now()`; only the field the service writes by hand is wrong. Do not port
this field's write; the module should either use an explicit UTC-aware stamp or
drop the field, since it is not part of the allocation contract.

### Defect 4 — parallel formatting and preview logic → **STILL PRESENT, worse: there are THREE, and two disagree**

The dossier says "formatting and preview contain parallel logic". At head there
are three independent implementations of the same format:

1. `sequence_utils.format_number` — lines 32-59. The real allocation path.
   Uses `reference_date` and `sequence.current_number`, pads with
   `zfill(min_digits)`.
2. `NumberingService.preview_format` — `numbering.py:334-364`. Duplicates the
   logic against `date.today()` and a `sample_number`. Honours `min_digits`.
3. `NumberingSequence.preview` (a model property) —
   `numbering_sequence.py:151-197`. Duplicates it a third time, against
   `date.today()`.

Implementation 3 is not merely duplicated, it is **internally broken**. Lines
157-180 build a `parts` list — including a `seq_part` derived from `min_digits`
— and then **throw it away entirely**: the function returns from the `if/else`
at 183-197 without ever reading `parts`. That live branch hardcodes the
sequence segment as the literal `"0001"` at line 192, ignoring `min_digits`.

Consequence: for any sequence with `min_digits != 4` **and** a date component,
`NumberingSequence.preview` and `NumberingService.preview_format` **return
different strings for the same row**, and the model property disagrees with
what allocation will actually produce. Seven of the shipped defaults in
`DEFAULT_SEQUENCE_CONFIGS` are in that set — `TASK`, `LEAVE_APPLICATION`,
`SALARY_SLIP`, `EXPENSE`, `EXPENSE_INVOICE`, `MATERIAL_REQUEST` and
`BANK_STATEMENT`, each `min_digits = 5` with `include_year = True` — so the
model preview renders `…-0001` where allocation will issue `…-00001`. This reaches the admin UI through
`app/services/finance/settings_web.py:190` / `:240`
(`get_numbering_list_context`, `get_numbering_edit_context`).

Port ruling: the module must have **one** formatter, and preview must be that
formatter called with an explicit `reference_date` and a candidate value — not
a second code path. Do not port either preview implementation.

### Defect 5 — reset/update rewrites counters with no allocation evidence → **STILL PRESENT, broader than described**

- `reset_sequence_counter` — `numbering.py:421-442`. Sets
  `sequence.current_number = new_value` (line 437) to any operator-supplied
  integer, defaulting to `0`. There is no receipt, no audit row, no
  monotonicity check, and no record that the numbers between the old and new
  counter were ever issued. A committed invoice number can be reissued.
- `update_sequence` — `numbering.py:378-419`. Mutates `prefix`, `suffix`,
  `separator`, `min_digits`, `include_year`, `include_month`, `year_format`
  and `reset_frequency` in place. Numbers already issued under the previous
  shape have no stored record of the configuration that produced them.
- Not in the dossier: `reset_sequence_counter` also stamps
  `sequence.current_year = date.today().year` and
  `sequence.current_month = date.today().month` at lines 438-439 — a **third
  process-clock leak**, on the repair path, where the operator's intent is
  most likely to be about a period other than today.
- Also not in the dossier: `SequenceService.configure_sequence`
  (`app/services/finance/platform/sequence.py:71` onward) and
  `SequenceService.reset_sequence` provide a second, differently-shaped route
  to the same rewrite.

The dossier's constraint — repair may advance to proven evidence, never rewind
or delete committed history — is confirmed necessary and is confirmed *not*
satisfiable by porting any of this code. The repair operation must be written
fresh.

---

## 4. ERP defects the dossier does not record

These are newly identified at head and are of the same order of severity as
the recorded five.

### Defect 6 — the dominant caller path cannot supply a reference date at all

`SequenceService.get_next_number`
(`app/services/finance/platform/sequence.py:37-69`) has the signature
`(db, organization_id, sequence_type, fiscal_year_id=None)`. It delegates at
lines 67-69 as `SyncNumberingService(db).generate_next_number(
coerce_uuid(organization_id), sequence_type)` — **positionally omitting
`reference_date`**, so `generate_next_number` falls through to
`reference_date = date.today()` at `numbering.py:511-512`.

Sixteen production callsites use it:

```
app/services/finance/ap/goods_receipt.py:230
app/services/finance/ap/payment_batch.py:111
app/services/finance/ap/purchase_order.py:341
app/services/finance/ap/supplier_invoice.py:521
app/services/finance/ap/supplier_payment.py:411
app/services/finance/ar/customer_payment.py:322
app/services/finance/ar/invoice.py:390
app/services/finance/gl/journal.py:296
app/services/finance/gl/reversal.py:131
app/services/finance/import_export/assets.py:676
app/services/finance/import_export/items.py:558
app/services/finance/lease/lease_contract.py:169
app/services/finance/payments/batch_transfer_service.py:105
app/services/fixed_assets/asset.py:383
app/services/fixed_assets/reconciliation.py:523
app/services/procurement/ap_integration.py:81
```

Across all of `app/`, exactly **two** callers pass a business date:
`app/services/finance/banking/bank_statement.py:877`
(`reference_date=statement_date or period_start`) and
`app/services/dotmac_sub/sync/_base.py:286,294,302`, which merely forwards an
optional parameter that defaults to `None`.

This is the single most consequential finding for the build plan. The module's
`reference_date` is specified as a **required** business input with no default.
That means cutting over AR invoice, GL journal or supplier invoice is not a
call-site substitution — each one must first decide *which* business date it is
numbering against (invoice date, posting date, period start) and thread it
through. Sequence the cutover by "caller can name its business date", not by
"caller is high volume".

### Defect 7 — backdated or out-of-order allocation rewinds the counter

`should_reset` (`sequence_utils.py:12-29`) compares the supplied
`reference_date` against the row's `current_year`/`current_month` for
**inequality**, not for "is later than". Combined with the allocation path
(`numbering.py:313-316` / `:532-535`), an allocation whose `reference_date`
falls in an *earlier* period than the last one resets `current_number` to `0`
and rewrites `current_year`/`current_month` backwards. The next current-period
allocation resets again, back to `1`.

Where the period appears in the formatted output this yields a confusing but
non-colliding result. Where it does not, it yields **duplicate numbers**:
`include_year=False` with `reset_frequency=YEARLY` is an operator-reachable
configuration through `update_sequence` (lines 409-416 set `include_year` and
415-416 set `reset_frequency` independently, with no cross-validation), and it
guarantees the same string is issued every year. Nothing in the model, the
service or the suite validates reset policy against format composition.

Module ruling: validate the pair at configuration time and fail closed, and
make reset a forward-only period transition rather than a period-inequality
test.

### Defect 8 — a sixth ERP allocator, unregistered and unlocked

`app/services/careers/careers_service.py:318-342`,
`_generate_application_number`, is a fully independent implementation that does
not appear in the 41-caller inventory because it never mentions any numbering
class:

- `year = date.today().year` (line 324) — process clock, no business date.
- `select(func.max(JobApplicant.application_number)).where(... like prefix)`
  (lines 328-331) — a MAX-scan of the consuming table with **no row lock and no
  advisory lock**, so two concurrent applications read the same maximum.
- Its own docstring (lines 320-323) records that `application_number` carries a
  **table-wide** unique constraint, i.e. it is deliberately *not* organization
  scoped. Under the module's `TenantScope` this is a cross-tenant series, and
  needs an explicit decision before cutover.
- `except (ValueError, TypeError): last_seq = 0` (337-338) — silent fallback to
  zero on an unparseable stored number, the exact anti-pattern the module
  constraints forbid.

ADR-0017 line 536 states "ERP has five numbering implementations today". At
head the count is six by allocator and three by formatter:

| # | Implementation | Path | Status at head |
| --- | --- | --- | --- |
| 1 | `SyncNumberingService` | `app/services/finance/common/numbering.py:456-551` | canonical, 41 consumers |
| 2 | `NumberingService` (async) | `app/services/finance/common/numbering.py:211-453` | parallel async writer |
| 3 | `SequenceService` | `app/services/finance/platform/sequence.py:28+` | deprecated shim; still owns `configure_sequence`/`reset_sequence`; drops `reference_date` |
| 4 | `PayrollNumberingService` | `app/services/people/payroll/numbering.py:62+` | delegating shim; **`payroll_number_sequence` table still declared** at lines 30-59 |
| 5 | `NumberingSequence.preview` | `app/models/finance/core_config/numbering_sequence.py:151-197` | third formatter, internally broken |
| 6 | `_generate_application_number` | `app/services/careers/careers_service.py:318-342` | independent, unlocked, unregistered |

Note that `app/services/finance/platform/sequence.py` is a **naming trap**: the
directory is called `platform` but the service is `organization_id`-scoped and
raises `fastapi.HTTPException` (import at line 14). It is not a control-plane
service. Nothing in ERP at head implements a platform-plane sequence; the
module's platform plane is greenfield and has no product-first source.

### Defect 9 — `SequenceType` is a 27-member closed enum bound to a PostgreSQL type

`app/models/finance/core_config/numbering_sequence.py:27-54` declares 27
members; line 98 binds it as `Enum(SequenceType, name="sequence_type")`, i.e. a
native PostgreSQL enum type. The dossier correctly rules "do not port
`SequenceType` as a closed enum", but does not note the migration consequence:
ERP's cutover is not just a code change, it is a column-type change from a
native enum to the module's open `series_code` string, with a value mapping
that must be recorded and reversible. Budget an expand/contract migration on
the ERP side, not an in-place swap.

### Defect 10 — the default-configuration tables are large, product-specific and reachable on the allocation path

`DEFAULT_PREFIXES` (lines 25-52, 26 entries) and `DEFAULT_SEQUENCE_CONFIGS`
(lines 54-181, 14 entries) are consulted by `_default_sequence_kwargs`
(184-208) **on the allocation path**, via the auto-create branch. A caller that
allocates for a series nobody configured silently gets an invented convention —
`DEFAULT_PREFIXES.get(sequence_type, "DOC")` at line 199, falling back to a
monthly-resetting `DOC-YYYYMM-nnnn`. The dossier says not to port the prefix
dictionary; the stronger statement is that **auto-create on the allocation path
must not exist at all**. A missing series configuration is a fail-closed error
in the module, which means the ERP cutover needs an explicit seeding step for
every series a caller might touch, before the first allocation.

---

## 5. Sub revalidation

### Confirmed as described

| Dossier claim | Status | Evidence at `65f6cb06d` |
| --- | --- | --- |
| `INSERT ... ON CONFLICT DO NOTHING` then `SELECT FOR UPDATE` | **true** | `app/services/numbering.py:37-48` then `:57-59`; the docstring at `:27-29` names the query-then-insert race explicitly |
| Settings lookups inside the numbering owner | **true** | `_resolve_setting` at `:83-84`, called at `:109-114`, `:137-139` |
| Silent fallback from invalid settings to `1` / empty prefix / zero padding | **true** | `:95` (`max(int(start_value or 1), 1)`), `:115-118` and `:140-143` (`except (TypeError, ValueError): start_value_int = 1`), `:122-124` and `:146-148` (`prefix if isinstance(prefix, str) else None`, `padding if isinstance(padding, int) else None` → `_format_number` pad 0 → unpadded) |
| Globally scoped `key` with no tenant/control-plane declaration | **true, and stronger** | `app/models/sequence.py:13` — `UniqueConstraint("key", name="uq_document_sequences_key")`; the table has **no tenant column at all** (28 lines, no `tenant_id`) |
| Product keys in the module | **true** | literals `"project_number"`, `"support_ticket"`, `_SALES_ORDER_SEQUENCE_KEY` passed from callers; settings keys `invoice_number_prefix` etc. |
| No allocation receipt, unversioned configuration | **true** | `DocumentSequence` has `id`, `key`, `next_value`, `created_at`, `updated_at` and nothing else |
| No direct concurrency or rollback suite | **true, and worse — see below** | |

### Newly identified in Sub

**S1 — `reconcile_next_value` is dead code.** The function the dossier names in
its verdict as one of Sub's two contributions has **exactly one reference in
the whole repository**: its own definition at `app/services/numbering.py:65`.
`git grep -n "reconcile_next_value" origin/main` returns that line and nothing
else. Sub does not use it in production, in tests, or anywhere.

**S2 — the live reconciliation is a duplicated second writer, in three
places.** What Sub actually runs is hand-rolled inside each consuming service,
each of which acquires the sequence row lock and then **writes `next_value`
directly**:

- `app/services/projects.py:176-193` — `lock_sequence(db, "project_number",
  start_value)` at 176, then a full-table scan of `Project.number` at 178-188
  re-parsing the prefix by hand, then `sequence.next_value = minimum_next;
  db.flush()` at 191-193.
- `app/services/support.py:1029-1045` (in
  `Tickets.reconcile_ticket_number_sequence`) — the identical pattern against
  `Ticket.number`, with its own duplicate copy of the settings-fallback logic
  at 1015-1027.
- `app/services/sales_orders.py:289-293` — the most divergent:
  `lock_sequence(...)` at 289, then `value = max(sequence.next_value,
  _highest_existing_order_number(db) + 1)` at 290, `sequence.next_value =
  value + 1` at 291, and a formatted return at 293 with a **hardcoded prefix
  and `:06d` padding**, bypassing `generate_number`, the settings and
  `_format_number` entirely. This is a fourth allocator that happens to share
  a row lock.

The dossier's verdict sentence — "Sub supplies … monotonic reconciliation" —
should read: *Sub supplies the conflict-safe row-establishment SQL. Its
reconciliation is a caller-side duplicate and is itself part of what must be
retired.* The module must own reconciliation, and the evidence scan (walking a
consuming table to derive a proven minimum) must become a **caller-supplied
proven-minimum argument**, not a query the module performs — the module cannot
know about `Project.number` or `Ticket.number` without importing product
vocabulary.

**S3 — uniqueness is enforced by the consuming table, not the sequence.** Both
`app/services/projects.py:195-213` and `app/services/support.py:987-1004` wrap
allocation in `for _attempt in range(10_000)`, calling `generate_number`,
checking whether the candidate is already occupied in the consuming table, and
burning counter values until one is free — raising `RuntimeError` after ten
thousand attempts. Allocation is therefore advisory in Sub: the authority is
the consuming table's unique index. This confirms the module must issue an
**immutable allocation receipt** that is itself the authority, and it means the
Sub cutover retires these retry loops rather than porting them.

**S4 — the concurrency shape has no proof, on any backend.** Sub's
`tests/conftest.py` deliberately points `DATABASE_URL` at an unreachable
PostgreSQL (`127.0.0.1:9`, with the comment at line 46 "use TEST_DATABASE_URL
or explicit SQLite engines below") and installs an extensive SQLite
compatibility layer — UUID adapters (117-178), JSONB patching (190-205),
geometry passthrough (208-233). `tests/test_numbering_defaults.py` runs on
SQLite, where `with_for_update()` compiles to a **no-op**. Sub's safer SQL
shape is never exercised under an actual row lock in CI. The dossier says
"Sub's safer SQL shape is a code source, not proof" — correct, and the reason
is stronger than stated: it is not merely unproven, it is proven only on a
backend that cannot express the guarantee.

**S5 — a dialect switch inside the owner.** `lock_sequence` branches on
`bind.dialect.name` at lines 37/43/49, with three code paths and a
`RuntimeError` fallback at 60-61. Under ADR-0024 ("shared behavior has no
product/provider switches") this cannot be ported as-is. The module targets
PostgreSQL; the dialect fork is a Sub testing accommodation, not behaviour.

**S6 — `reconcile` creates rows.** `reconcile_next_value` calls
`lock_sequence(db, key, minimum_next_value)` at line 67, so a "reconcile" of an
absent series silently **creates** it seeded at the proven minimum. Repair must
not be a creation path in the module; a repair against an unconfigured series
is a fail-closed error.

---

## 6. Variance summary against `numbering-sources.md`

| Dossier statement | Verdict | Note |
| --- | --- | --- |
| Two mandatory sources, product-first, ERP primary | **still true** | Unchanged and confirmed. |
| All seven mandatory paths | **still true** | Present, byte-identical to the pins. |
| ERP "41 tracked application files" | **imprecise** | 43 files match; 41 are consumers, 2 are the implementation. |
| ERP focused suite covers the format contract | **still true** | 31 tests at `tests/ifrs/common/test_numbering_service.py`, covering defaults, three reset policies, 2/4-digit years, optional month/prefix/suffix, separators, padding, large values, increments, previews, sync and async. |
| ERP defect 1 (first-use race) | **still present** | Worse in character: the racing read is `FOR UPDATE`, which reads as safe. |
| ERP defect 2 (all tests mocked) | **still present** | Confirmed across all 36 numbering-touching test files; zero real-DB coverage. |
| ERP defect 3 (naive `datetime.now()`) | **still present** | `numbering.py:320`, `:539`. |
| ERP defect 4 (parallel format/preview) | **still present, understated** | Three implementations; the model property is internally broken and disagrees with allocation for `min_digits != 4`. |
| ERP defect 5 (counter rewrite) | **still present, understated** | Plus a process-clock write on the repair path (`:438-439`) and a second route via `SequenceService`. |
| Sub: conflict-safe establishment + `SELECT FOR UPDATE` | **still true** | `numbering.py:37-48`, `:57-59`. |
| Sub: "monotonic reconciliation" | **NEWLY WRONG** | `reconcile_next_value` is dead code; live reconciliation is a caller-side duplicate in three services. |
| Sub: output tests prove invoice/credit-note numbers | **still true, weaker than implied** | Real, but SQLite-only; they prove formatting, not allocation. |
| Sub "do not port" list | **still true** | Every item confirmed at head. |
| "Explicit business `reference_date`" as a shared-contract item | **still true, but the cost is unrecorded** | 16 of ERP's callsites go through a signature with no date parameter; only 2 callers in ERP pass one. |
| Shared contract (series/format/lock/receipt/replay/rollback/repair) | **still correct** | No source supplies receipt, replay, fingerprint conflict or platform plane. All greenfield. |
| Adoption plan (ERP first, Sub second, Vendor CP platform) | **holds, resequence within ERP** | Order ERP's cutover by "caller can name a business date", not by volume. |
| Eight fresh proofs required | **still correct, and now the whole evidence base** | Nothing in either product proves any of them. |

**Newly identified and not in the dossier at all:** ERP defects 6-10 (§4) and
Sub findings S1-S6 (§5).

---

## 7. Draft `EXTRACTION.toml`

Draft only — inline here per the read-only scope. Do **not** create this file;
it is written by the single write agent after PR #178 merges. Field names
follow `packages/dotmac-files/EXTRACTION.toml`. The `mod_num` short code and
the plane declaration are proposals, subject to the write agent's namespace
allocation.

```toml
schema_version = 1
package = "dotmac-numbering"
classification = "optional-module"
status = "audit-complete"
source_mode = "product-first"
owner = "Concurrency-safe allocation and formatting of explicitly configured document series on separate tenant and platform planes, with one immutable receipt per allocation"
contract = "Given a required TenantScope or PlatformScope, an open registered series_code, an explicit business reference_date and an idempotency key, reserve the next value of a configured series under a row lock, format it from validated configuration, and record one immutable allocation receipt. Replay of the same key with the same request fingerprint returns the original formatted number; a different fingerprint conflicts. Allocation participates in the caller's transaction and rolls back with it. Repair may advance a counter to caller-supplied proven evidence and may never rewind or delete committed allocation history. NOT what a number means, which documents require one, legal gaplessness policy, fiscal periods, invoice issuance, document rendering, or any product's series vocabulary."
source_repositories = [
  "dotmac_erp",
  "dotmac_sub",
]
source_revisions = [
  "dotmac_erp:7fc1111f73ca926eb8729a0dff589a3dfad896a8",
  "dotmac_sub:65f6cb06d6e2beba61266368f4073f9df55197ca",
]
source_paths = [
  "dotmac_erp:app/models/finance/core_config/numbering_sequence.py",
  "dotmac_erp:app/services/finance/common/numbering.py",
  "dotmac_erp:app/services/finance/common/sequence_utils.py",
  "dotmac_sub:app/models/sequence.py",
  "dotmac_sub:app/services/numbering.py",
]
preserved_tests = [
  "dotmac_erp:tests/ifrs/common/test_numbering_service.py",
  "dotmac_sub:tests/test_numbering_defaults.py",
]
contract_consumers = []
candidate_consumers = ["dotmac_erp", "dotmac_sub", "dotmac_vendor_control_plane"]
composition_boundary = "ADR-0024: each adopter installs its own num lineage and owns its own series configuration, counters and receipts. Applications share the package contract, never a mod_num schema, a counter row or a receipt table. ADR-0023: tables (tenant, tenant_id NOT NULL, FORCE RLS) and platform_tables (no tenant column, REVOKEd from the tenant app role) are declared, not inferred; no foreign key crosses the planes and there is no nullable tenant_id, sentinel tenant or polymorphic scope column. series_code is an open registered string, never a closed enum; no product vocabulary (invoice_number, project_number, SequenceType members) enters the module. The module performs no settings lookup and reads no clock: configuration is passed in and reference_date is a required business input."
inventory_evidence = [
  "docs/inventories/numbering-sources.md",
  "docs/inventories/numbering-source-variance.md",
  "docs/adr/0017-adoption-is-the-scarce-resource.md",
  "docs/adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md",
]
first_cutover = "dotmac_erp, tenant plane, one series family at a time, ordered by whether the caller can name its business date rather than by volume. Slice 1 is app/services/finance/banking/bank_statement.py:877, the only production caller that already passes a real reference_date (statement_date or period_start). Slice 2 is app/services/dotmac_sub/sync/_base.py:286,294,302, which already threads an optional date. Only then the sixteen SequenceService.get_next_number callsites, each of which must first choose and thread an explicit business date, because SequenceService.get_next_number (app/services/finance/platform/sequence.py:37-69) has no reference_date parameter and silently allocates against date.today(). dotmac_sub follows on the tenant plane for invoice, credit-note, sales-order, project and support-ticket series after ERP's tenant plane is proven; its cutover retires three caller-side reconcilers and two ten-thousand-iteration collision-retry loops. dotmac_vendor_control_plane is the first platform-plane adopter through Billing; the platform plane has no product-first source and is written fresh against the same behaviour suite."
shadow_and_drift = "Neither source has a real-database test, so the shadow phase is the first genuine evidence. For each ERP series family: seed the module series explicitly from the existing numbering_sequence row (there is no auto-create in the module and no default-prefix dictionary), then run the module allocator in shadow inside the same transaction as the legacy allocator and compare the formatted string, the counter delta and the reset decision for every allocation, recording divergence rather than failing. Shadow must be driven with the business date the caller will use in production, not date.today(), because that substitution is the change most likely to move a number across a reset boundary. Backfill one receipt per historically issued number where the number is recoverable from the consuming table, and reconcile the module counter forward to that proven maximum using the caller-supplied proven-minimum repair path; never rewind. Divergence classes to expect and adjudicate before cutover: (a) reference_date substitution moving an allocation across a yearly or monthly boundary; (b) the ERP backdating rewind (sequence_utils.should_reset compares period inequality, not ordering) producing a legacy value the module refuses; (c) preview divergence, since NumberingSequence.preview hardcodes a four-digit sequence segment and disagrees with allocation for every min_digits != 4 series; (d) auto-created legacy series that were never configured and carry an invented DOC- prefix. For Sub, shadow against the existing consuming-table uniqueness check: the module's receipt must be authoritative before the retry loops are removed, so run both and assert the retry loop never fires."
local_copy_retirement = "ERP retires, in order: SequenceService (app/services/finance/platform/sequence.py) including configure_sequence and reset_sequence; the async NumberingService; SyncNumberingService; NumberingSequence.preview and NumberingService.preview_format, both replaced by one module formatter called with an explicit date; PayrollNumberingService and the residual people.payroll_number_sequence table; and app/services/careers/careers_service.py:318-342, an unregistered sixth allocator that MAX-scans a table-wide-unique column with no lock and must first have its cross-organization series scope decided. sequence_utils.format_number and should_reset are the extraction source and are deleted with their callers. The core_config.numbering_sequence table's native sequence_type PostgreSQL enum becomes the module's open series_code string through an expand/contract migration with a recorded, reversible value mapping. Sub retires generate_number, generate_required_number, generate_number_with_config, the dead reconcile_next_value, the document_sequences table, the three caller-side reconcilers (projects.py:176-193, support.py:1029-1045, sales_orders.py:289-293) and the two collision-retry loops (projects.py:195-213, support.py:987-1004). A two-directional import/caller ratchet reaches zero in each repository before its local owner is deleted. No permanent mirrored counter or parallel allocator is allowed in either product."
next_action = "Land the eight PostgreSQL proofs in this document's test matrix as the module's first commit, before any caller is touched, because neither source contributes a single real-database test. Then seed and shadow ERP's bank-statement series as slice 1. Do not begin any SequenceService callsite until its business date is chosen and threaded."
```

---

## 8. PostgreSQL test matrix

Eight proofs, one per item in the dossier's "fresh proof required" list.
Everything here is new: neither source contributes a real-database numbering
test, so this matrix is the module's entire correctness evidence base and
should land before any caller is cut over.

All of these require a real migrated PostgreSQL (`make test-db-up`) and belong
at the top level of `tests/`, not under `tests/unit`, per the repository's
testing model. SQLite cannot express any of them: `with_for_update()` is a
no-op there, and `FORCE ROW LEVEL SECURITY` and role privileges do not exist —
which is precisely how Sub's identical SQL shape has gone unproven.

### Common harness requirements

- Two engines with **distinct roles**: `app_user` (tenant, RLS-forced) and
  `platform_api` (control plane, schema `USAGE` plus row DML on
  `platform_tables`, `REVOKE`d from tenant tables).
- Independent connections for concurrency — separate `Session` objects on
  separate DBAPI connections, never two sessions sharing one connection, or
  the row lock is trivially satisfied and every race test passes vacuously.
- A `threading.Barrier(2)` (or two-thread rendezvous) so both actors are
  demonstrably inside their transactions before either proceeds.
- A **sensitivity proof** for each race test (per ADR-0018): a companion test
  that removes the guard — patch the module to do query-then-insert, or to skip
  `FOR UPDATE` — and asserts the race test *fails*. A concurrency test that
  cannot be made to fail is not evidence.
- An explicit `reference_date` argument in every allocation call. No test may
  rely on a default; the module has none.

---

### Proof 1 — tenant-plane RLS and cross-tenant allocation isolation

**Setup.** Configure series `s` under tenant A and, with the same
`series_code`, under tenant B. Allocate several numbers in each.

**Mechanism.** Sequential, but through the `app_user` role with the tenant GUC
set per statement — RLS forced, no `BYPASSRLS`. Assert: reading tenant A's
context returns only A's counter row and only A's receipts; a direct
`SELECT`/`UPDATE`/`DELETE` against B's counter row id from A's context affects
zero rows; tenant A allocating does not advance tenant B's counter; and the
same `series_code` in both tenants yields the same formatted string
independently. Add the composite-unique canary: `(tenant_id, series_code)` is
unique and `series_code` alone is not.

**Failing run looks like.** A returns a non-zero row count for B's id; or B's
counter advances after A allocates; or the module's unique constraint is on
`series_code` alone and the second tenant's configuration insert raises
`UniqueViolation`. A false pass to guard against: the test connecting as the
migration owner or a superuser, which bypasses RLS — assert
`current_user = 'app_user'` and that `pg_class.relforcerowsecurity` is true for
each tenant table inside the test.

### Proof 2 — platform-plane revocation from `app_user`, reachability by `platform_api`

**Setup.** A configured platform series with at least one allocation and one
receipt.

**Mechanism.** As `app_user`, attempt `SELECT`, `INSERT`, `UPDATE`, `DELETE`
against every declared `platform_tables` entry, and additionally attempt a
column-level `SELECT` of each column — the ADR-0023 requirement is revocation
across every table *and column* privilege, and a column grant left behind will
not show up in a table-level check. Then, as `platform_api`, perform a full
allocation and read back its receipt. Enumerate the tables from the manifest's
`platform_tables` declaration rather than a hand-written list, so a table added
later is covered automatically.

**Failing run looks like.** Any `app_user` statement returns rows or reports a
row count instead of raising `InsufficientPrivilege`; or `platform_api` raises
on `USAGE` or on row DML. A false pass to guard against: `app_user` failing
with `UndefinedTable` because the schema is missing in the test database rather
than because privileges were revoked — assert the error is specifically
`InsufficientPrivilege`, and assert as `platform_api` that the table exists.

### Proof 3 — two concurrent first allocations

**Setup.** A configured series with **no counter row yet** — this is the
first-use path, the one ERP gets wrong at `numbering.py:514-530` and
`:256-275`, and the reason Sub's `ON CONFLICT DO NOTHING` shape is a mandatory
port delta.

**Mechanism.** Two threads on independent connections. Both open a
transaction, both meet at a `Barrier(2)`, both call allocate for the same
`(scope, series_code)` with the same `reference_date` and *different*
idempotency keys, both commit. Assert: both succeed, the two formatted numbers
are different and consecutive, exactly one counter row exists, two receipts
exist, and neither raises `UniqueViolation`. Run the same test with the counter
row pre-existing to cover the steady-state lock, and repeat both at N=8 threads
to catch a lock taken too late.

**Failing run looks like.** One thread raises `UniqueViolation` on the counter
row's unique index (the ERP defect exactly); or both threads return the same
formatted number (no lock, the `careers_service.py` MAX-scan shape); or one
thread hangs to statement timeout (lock acquired in an order that deadlocks).
**Sensitivity proof:** monkeypatch the establishment step to query-then-insert
and assert this test fails with `UniqueViolation`.

### Proof 4 — rollback with the consuming transaction

**Setup.** A configured series with a known counter value `v`.

**Mechanism.** In one transaction: allocate, observe the returned number, then
`rollback`. In a fresh transaction, read the counter and allocate again.
Assert the counter is still `v`, no receipt row survives, and the second
allocation returns the *same* number the rolled-back one did. Then the
interleaved variant, which is the one that catches an out-of-band autonomous
transaction: thread 1 allocates and holds its transaction open; thread 2
allocates for the same series and **blocks** on the row lock; thread 1 rolls
back; thread 2 unblocks and must receive the number thread 1 abandoned.
Finally, a savepoint variant — allocate inside a nested savepoint, roll the
savepoint back, and assert the outer transaction can still allocate and gets
the same value.

**Failing run looks like.** The counter advanced despite the rollback, or a
receipt survives — both mean the module opened its own connection or committed
internally, i.e. it became a second transaction authority in violation of the
`dotmac_kernel.db` rule. In the interleaved variant: thread 2 not blocking at
all (no lock) or receiving `v+2` (a value was consumed by a rolled-back
transaction, the "gap on rollback" the contract forbids).

### Proof 5 — same-key/same-fingerprint replay, and fingerprint conflict

**Setup.** A configured series. Define the request fingerprint over the inputs
that determine the output — at minimum `scope`, `series_code`,
`reference_date`, and the resolved configuration version.

**Mechanism.** Three cases, each in its own transaction. (a) Allocate with key
`k` and fingerprint `f`; allocate again with `(k, f)`; assert the identical
formatted number is returned, the counter did **not** advance, and exactly one
receipt exists. (b) Allocate with `(k, f)`, then with `(k, f')` where only
`reference_date` differs; assert a conflict is raised, the counter did not
advance, and the original receipt is unmodified. (c) The concurrent replay: two
threads on independent connections, barrier-synchronised, both calling with the
same `(k, f)`; assert exactly one receipt is created, both return the same
number, and the counter advanced by exactly one.

**Failing run looks like.** Case (a) returning a new number or advancing the
counter (idempotency not enforced). Case (b) silently returning the original
number for different inputs (fingerprint not checked — the failure that lets a
retried request with a corrected date reuse a number issued for the wrong
period) or, worse, overwriting the receipt. Case (c) producing two receipts or
raising a raw `UniqueViolation` instead of a typed conflict, which means the
fingerprint column is being relied on as an accidental arbiter rather than a
declared one. Note the ADR-0014 constraint: the fingerprint is its own column,
and nothing is reserved before the effect.

### Proof 6 — reset boundaries use the supplied date, never the worker clock

**Setup.** A yearly-reset series and a monthly-reset series, each with a known
counter and period.

**Mechanism.** Freeze nothing and patch nothing — instead pass reference dates
that are *unrelated* to today, which is the only way to prove the clock is not
consulted. Allocate with `date(2025, 12, 31)` then `date(2026, 1, 1)` on the
yearly series and assert the second resets to the start value and stamps the
new period; allocate twice within `2026-03` on the monthly series and assert no
reset. Then the discriminating case: run the whole test with the system clock
in a period matching *neither* reference date (e.g. dates in 2025 and 2026 with
`date.today()` in some third period) and assert the outputs are identical to a
run where they do coincide. Add a static guard alongside — an architecture test
asserting the module source contains no `date.today`, `datetime.now`,
`datetime.utcnow` or `time.time`, which is the cheap version of the same
proof and is what ERP fails at `numbering.py:306`, `:511`, `:320`, `:539`,
`:338`, `:438-439` and `numbering_sequence.py:156`.

**Failing run looks like.** The formatted number carrying the current year
rather than the reference year; or a reset firing on the clock's period
boundary; or the two runs above disagreeing. Also assert the ERP backdating
defect is *not* ported: allocate for `2026-03`, then for `2026-02`, and assert
the module refuses or handles the out-of-order date explicitly rather than
rewinding the counter to zero and re-issuing (`sequence_utils.should_reset`
lines 20-27 compare period inequality, not ordering).

### Proof 7 — configuration change cannot rewrite a receipt or rewind a counter

**Setup.** A configured series with several committed allocations and their
receipts.

**Mechanism.** Four cases. (a) Change `prefix`, `padding` and `reset_frequency`
through the module's configuration path; assert every existing receipt's stored
formatted number, series configuration snapshot and configuration version are
byte-unchanged, and that subsequent allocations use the new configuration under
a new version. (b) Attempt repair to a value **below** the current counter;
assert it is refused and the counter is unchanged. (c) Repair to a
caller-supplied proven minimum **above** the current counter; assert the
counter advances, a repair record exists with its evidence, and no receipt is
created or deleted. (d) Attempt to delete or update a receipt row directly as
`app_user`; assert it is refused — receipts are append-only by grant, not by
convention. Add a validation case: configure `reset_frequency=YEARLY` with no
year component in the format and assert the module **fails closed** at
configuration time (the ERP defect 7 configuration that guarantees annual
duplicates), and configure an invalid padding/start and assert it raises rather
than falling back to zero or one (the Sub defect at
`app/services/numbering.py:115-118`).

**Failing run looks like.** A historical receipt's formatted number changing
when configuration changes — the ERP `update_sequence` behaviour at
`numbering.py:378-419`, which leaves issued numbers with no record of the
shape that produced them. Or a repair accepting a lower value and rewinding —
the ERP `reset_sequence_counter` behaviour at `:421-442`. Or an invalid
configuration silently yielding `1` / empty prefix / zero padding rather than
raising.

### Proof 8 — tenant and platform parity

**Setup.** The same series configuration expressed on both planes.

**Mechanism.** Parameterise the module's entire behaviour suite over
`TenantScope` and `PlatformScope` from one shared test body — a fixture
returning `(engine, role, scope_factory)` — rather than writing two suites,
so a behaviour added later cannot be added to only one plane. Assert that for
otherwise identical inputs (`series_code`, `reference_date`, idempotency key,
configuration) both planes produce the **identical formatted number**, the same
reset decisions, the same replay and conflict behaviour, and the same repair
semantics. Add a structural assertion: the two planes' tables are distinct, no
foreign key crosses them, the tenant table has `tenant_id NOT NULL` with FORCEd
RLS, and the platform table has no tenant column and no nullable/sentinel
substitute.

**Failing run looks like.** A test in the shared body that only executes for
one plane (assert the parameterisation count equals twice the case count); the
two planes producing different strings for identical input; or the structural
assertion finding a nullable `tenant_id` or a cross-plane foreign key — which
is the shape ADR-0023's gate refuses and which no source product can guide,
since ERP's `app/services/finance/platform/` directory is not a platform plane
at all but an `organization_id`-scoped deprecated shim.

---

## 9. What this changes in the build plan

1. **Write the eight proofs first, before any extraction.** The dossier framed
   them as fresh proof to add; the revalidation shows they are the module's
   *only* correctness evidence. ERP has zero real-database numbering tests and
   Sub's run on SQLite where the lock is a no-op. There is nothing to inherit.
2. **Re-sequence the ERP cutover by business-date readiness, not by volume.**
   Sixteen of the highest-value callsites cannot express a reference date
   today. Slice 1 is `bank_statement.py:877`; slice 2 is the Sub sync base;
   everything else needs a date decision first.
3. **Budget the Sub cutover as three reconciler retirements plus two
   collision-retry-loop removals**, not as a call-site swap. Sub's callers are
   second writers to the sequence row.
4. **Do not carry the reconciliation *scan* into the module.** Repair takes a
   caller-supplied proven minimum. The evidence walk stays in the product,
   because it is inherently product vocabulary.
5. **Plan an expand/contract migration for ERP's native `sequence_type`
   enum** — 27 members, PostgreSQL enum type — to the module's open
   `series_code` string, with a recorded reversible mapping.
6. **Add `careers_service._generate_application_number` to the retirement
   ledger**, and decide its cross-organization series scope before cutover: it
   is table-wide unique by design and does not fit `TenantScope` as written.
7. **The platform plane is greenfield.** `app/services/finance/platform/` is a
   naming trap, not a control-plane source. Proof 8's parity requirement is the
   only thing keeping the two planes honest.

---

## Appendix — housekeeping observations

- `docs/inventories/numbering-sources.md` is **absent from the starter working
  tree** at the time of writing, though it is committed at `5e11fb2`. It exists
  in two agent worktrees under `.claude/worktrees/`. Whoever lands the module
  should confirm it is restored on `main` before citing it.
- An **empty `packages/dotmac-numbering/src/` directory shell exists on disk**
  (created 2026-08-15 06:47), untracked and containing no files. It was not
  created by this task and has not been touched. The single write agent should
  verify it is empty before scaffolding into it, so a partial earlier attempt
  is not silently inherited.
