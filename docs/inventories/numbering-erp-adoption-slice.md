# Numbering — ERP bank-statement adoption slice (cutover slice 1)

**As of:** 2026-08-15
**Authorizing decision:** ADR-0030 §5 — "adopter integration may run after a
package reaches its independent completion gate". `dotmac-numbering` reached it
at starter `0b8d47a` (PR #193). This document is the PLAN for that integration.
It is read-only analysis: no file in `dotmac_erp` was modified, no package,
migration, namespace or version was created, and no test was run.

**Heads resolved for this analysis** (every ERP citation is a revision-pinned
`git show origin/main:<path>`, never the working tree):

| Repository | Revision read | Notes |
| --- | --- | --- |
| `dotmac_starter_mt` | `1e9c4332dc1dbc2cc81fd18ba3401a079c81d839` (`origin/main`) | worktree is on `ed9dcc0`, 1 behind / 1 ahead; `packages/dotmac-numbering` is byte-identical to `origin/main` |
| `dotmac-numbering` | landed at `0b8d47a`, version `0.1.0a1`, kernel floor `0.1.0a65` | **unreleased** — see gate G0 |
| `dotmac_erp` | `9d67c3990e01e20409ab118badb5dfdf7ce045a7` (`origin/main`) | checkout is on `feat/kernel-ui-contract-alignment` at the old pin with a dirty worktree; **7 commits** past the variance report's `7fc1111f` |

The 7 new ERP commits do **not** touch any numbering path.
`git diff 7fc1111f..origin/main` over `numbering.py`, `sequence_utils.py`,
`numbering_sequence.py`, `platform/sequence.py`, `bank_statement.py` and
`tests/ifrs/common/test_numbering_service.py` is **empty**. Per the standing
lesson, an empty diff is not a revalidation — so every claim below was re-read
and its callers re-counted at `9d67c399`.

---

## 1. Headline verdict

**The slice-1 callsite claim survives verification, and its stated reason is the
weaker of its two reasons.** `bank_statement.py:874-878` is real, it passes
`statement_date or period_start`, and — the fact the dossier does not record —
`SequenceType.BANK_STATEMENT` has **exactly one allocation site in the whole of
`app/`**. That makes it the only series in ERP that can be cut over without
creating two writers for one counter. Slice 1 is correct.

**But the slice as specified cannot execute, for three reasons that are all
new here.** In descending order of cost:

1. **The module and ERP do not share a format grammar, so "compare the
   formatted string" is an unsatisfiable shadow assertion.** ERP concatenates
   prefix+year+month with *no* separator and then places exactly one separator
   before the sequence segment (`sequence_utils.format_number:48-54`); the
   module joins *every* segment with the separator
   (`service.py:288-301`). ERP issues `STMT2026-00001`; **no
   `SeriesConfiguration` in the module's current surface can produce that
   string.** See §2 and §4/D1 — this is the finding that changes the slice.
2. **`allocate` has an undeclared database prerequisite that ERP does not
   satisfy.** `service.py:457-499` delegates at-most-once to
   `dotmac_kernel.idempotency.execute_once`, which writes
   `public.idempotency_records`. That table is created by kernel migration
   `20260810_0018_idempotency_one_owner`, and ERP deliberately does not run the
   kernel lineage (`alembic.ini`: `dotmac_kernel.migrations:versions` is
   "deliberately ABSENT and must stay so"). No `PrerequisiteSpec` covers it —
   `dotmac_kernel.prerequisites` declares only `tenant_scope_catalog.v1` and
   `module_database_roles.v1`, and `manifest.py:47-48` requires exactly those
   two. `allocate` in ERP fails with `UndefinedTable` at the first call. See
   §4/D2.
3. **Slice 2 as written creates two writers.** `_base.py:286,294,302` allocate
   `INVOICE`, `PAYMENT` and `CREDIT_NOTE` — series with **5, 4 and 1** mention
   sites respectively in `app/`. Cutting `_base.py` over alone would leave
   `ar/invoice.py:391`, `ar/quote.py:462`, `ar/sales_order.py:612` and
   `automation/recurring.py:459` writing `numbering_sequence.current_number`
   for the same series the module is now allocating. The slice unit is a
   **series**, not a callsite. See §7.

Two corrections to `numbering-source-variance.md` also fall out, both of the
"grep for callers, not definitions" class:

- **Variance Defect 4 is wrong about the admin UI.** It says
  `NumberingSequence.preview` (the internally broken third formatter) "reaches
  the admin UI through `app/services/finance/settings_web.py:190` / `:240`".
  At head those lines are `get_numbering_list_context` /
  `get_numbering_edit_context`, and they call
  `numbering_service.preview_format(...)` at `:226` and `:264` — the *second*
  implementation, which is correct for BANK_STATEMENT. `git grep` for the model
  property returns **no caller anywhere in `app/` or `tests/`**. It is dead
  code, exactly like Sub's `reconcile_next_value`.
- **ERP has FIVE formatters, not three.** Two more, unrecorded anywhere:
  `app/services/fixed_assets/web.py:1304` `_sequence_preview` (which drops
  year and month entirely — it renders `prefix + digits + suffix` and is simply
  wrong for any dated series) and `app/services/inventory/web.py:433`
  `_sequence_preview` (which re-implements `should_reset` *and*
  `format_number` inline). Neither is slice 1, both belong on the retirement
  ledger.

---

## 2. The callsite, verified

### What it calls

`app/services/finance/banking/bank_statement.py`, method `import_statement`
(signature begins at `:815`), at `9d67c399`:

```python
        # Auto-generate statement number if not provided
        if not statement_number:
            from app.models.finance.core_config.numbering_sequence import SequenceType
            from app.services.finance.common.numbering import SyncNumberingService

            numbering = SyncNumberingService(db)
            statement_number = numbering.generate_next_number(
                organization_id,
                SequenceType.BANK_STATEMENT,
                reference_date=statement_date or period_start,
            )
```
— `bank_statement.py:868-878`. Line 877 is `reference_date=...` exactly as the
dossier claims.

**The date.** `statement_date: date | None` (`:821`) and `period_start: date`
(`:822`, non-optional). `statement_date or period_start` is therefore **always
a real `date`**, never `None`, so `generate_next_number` never reaches its
`reference_date = date.today()` fallback at `numbering.py:511-512`. This is the
claim's substance and it holds.

**The branch.** Allocation happens **only** when the caller supplied no number
(`:869`). Two callers reach `import_statement`: `app/api/finance/banking.py:362`
and `app/services/finance/banking/web_parts/statements.py:1528`, both forwarding
`payload.statement_number`. So the series governs auto-generated statements
only, and the consuming column holds a mixture — see §5.

**The series.** `SequenceType.BANK_STATEMENT`
(`numbering_sequence.py:53`), scoped by `organization_id`, resolved through the
unique `uq_sequence_type (organization_id, sequence_type)`
(`numbering_sequence.py:76-81`).

### Sole-allocator proof

`git grep -n "SequenceType\.BANK_STATEMENT" origin/main -- app/` returns **one**
line outside the numbering implementation itself: `bank_statement.py:876`. The
only other `BANK_STATEMENT` token in `app/` is an unrelated enum member in
`app/models/finance/common/attachment.py:40`. This is the strongest reason for
slice 1 and it is not in the dossier.

For contrast, a mention census across `app/` (excluding `common/numbering.py`,
`settings_web.py` and the model) — treat as an ordering signal, not an exact
allocator count:

| 1 site | INVOICE 5 · PAYMENT 4 · JOURNAL 4 · TASK 4 · SUPPLIER_INVOICE 3 · ASSET 3 · EXPENSE 3 |
| --- | --- |
| CREDIT_NOTE, RECEIPT, PURCHASE_ORDER, LEASE, GOODS_RECEIPT, QUOTE, SHIPMENT, SUPPORT_TICKET, PROJECT, CUSTOMER, EMPLOYEE, LEAVE_APPLICATION, EXPENSE_INVOICE, CONTRACT, **BANK_STATEMENT** | ITEM 2 · SALES_ORDER 2 · MATERIAL_REQUEST 2 · SALARY_SLIP 2 · PAYROLL_ENTRY 2 |

### What that series currently produces

`sequence_utils.format_number` (`:32-59`) — the one allocation formatter:

```python
    date_str = "".join(parts)                                   # :48
    seq_str = str(sequence.current_number).zfill(sequence.min_digits)  # :49

    if date_str:
        result = f"{date_str}{sequence.separator}{seq_str}"      # :52
    else:
        result = seq_str
    if sequence.suffix:
        result += sequence.suffix                                # :57
```

With the shipped BANK_STATEMENT defaults (`numbering.py:163-171`: prefix
`"STMT"`, separator `"-"`, `min_digits` 5, `include_year` True, `include_month`
False, `year_format` 4, `reset_frequency` YEARLY):

**`STMT2026-00001`** — prefix and year fused, one separator, five digits.

Note also that `generate_next_number` increments **before** formatting
(`numbering.py:538` then `:542`), so `current_number` is the **last issued**
value, not the next one. That is the arithmetic the seeding depends on (§3).

`DEFAULT_SEQUENCE_CONFIGS[BANK_STATEMENT]` has been unchanged since it was
introduced in a single commit (`fe741d07`), so an auto-created row from any era
carries the same shape. That is the one piece of luck in this slice.

---

## 3. Seeding

### Which row governs

`core_config.numbering_sequence` WHERE `organization_id = <org>` AND
`sequence_type = 'BANK_STATEMENT'` — one row per organization, guaranteed unique
by `uq_sequence_type`.

**The row may not exist, and there are three routes that create it.** Two are
already documented (`SyncNumberingService.generate_next_number:523-530` and
`get_or_create_sequence:480-492` / `initialize_all_sequences:444-453`). A third
is not recorded anywhere and matters most: `settings_web
.get_numbering_list_context` creates **every** `SequenceType` and **commits**:

```python
        if len(existing_types) < len(SequenceType):
            for seq_type in SequenceType:
                if seq_type not in existing_types:
                    await numbering_service.get_or_create_sequence(
                        organization_id, seq_type
                    )
            await db.commit()
```
— `settings_web.py`, `get_numbering_list_context`. So any organization whose
admin has ever opened the numbering settings page already has a
defaults-shaped BANK_STATEMENT row, whether or not it ever issued a number.

Seeding therefore reads the live row per organization. It must never read
`DEFAULT_SEQUENCE_CONFIGS`: the row is what governs, the dict is only what the
row was born with, and `update_sequence` (`numbering.py:378-419`) and
`SequenceService.configure_sequence` can both have changed it since.

### The configuration that reproduces current output — and why it cannot

Field-by-field the mapping is clean:

| ERP column | Module field | Default row value | Module accepts? |
| --- | --- | --- | --- |
| `prefix` `String(20)` | `prefix` `String(32)` | `"STMT"` | yes |
| `suffix` `String(10)` | `suffix` `String(32)` | `""` | yes |
| `separator` `String(5)` | `separator` `String(8)` | `"-"` | yes |
| `min_digits` | `min_digits` (1..18) | `5` | yes |
| `include_year` bool | `include_year` int | `True` | yes |
| `year_format` | `year_digits` (2 or 4) | `4` | yes |
| `include_month` bool | `include_month` int | `False` | yes |
| `reset_frequency` YEARLY | `reset_policy` `"yearly"` | YEARLY | yes — and coherent, because `include_year` is True |
| `current_number` (last issued) | *not configuration* | — | becomes a counter repair, §5 |
| — | `start_value` | — | `1` (ERP resets to 0 then increments) |

So the nominal seeding call is:

```python
SeriesConfiguration(
    series_code="BANK_STATEMENT",   # ERP's enum member name, verbatim
    prefix="STMT",
    suffix="",
    separator="-",
    min_digits=5,
    include_year=True,
    year_digits=4,
    include_month=False,
    reset_policy="yearly",
    start_value=1,
)
```

**And it renders `STMT-2026-00001`, not `STMT2026-00001`.** The module's
formatter joins uniformly:

```python
    segments.append(str(value).zfill(series.min_digits))
    if series.suffix:
        segments.append(series.suffix)
    return series.separator.join(segments)
```
— `service.py:298-301`. Confirmed by the module's own suite:
`tests/test_numbering_isolation.py:606` asserts
`"INV-2026-000001"`, and `:519` asserts `"INV-000001"`.

Exhaust the alternatives, because this is the decision the slice turns on:

- `separator=""` → `STMT202600001` (loses the separator ERP prints).
- `separator="-"` → `STMT-2026-00001` (gains one ERP does not print).
- Folding the year into `prefix` is impossible: `prefix` is static
  configuration and `include_year` is the only date source.
- ERP's suffix rule differs too — ERP appends it with no separator
  (`sequence_utils.py:57`), the module joins it with one (`service.py:299-300`).
  Immaterial for BANK_STATEMENT (`suffix=""`), material for six other series.

**There is no exact-reproduction configuration. Choose one of two, and record
the choice as an ADR before shadowing:**

- **(A) Accept the shape change** — `STMT2026-00001` → `STMT-2026-00001` at
  cutover. Cheap, honest, and safe: the two grammars are disjoint strings, so a
  new-shape number can never collide with a historical one, and the duplicate
  check at `bank_statement.py:843-857` is per
  `(organization, bank_account, statement_number)` and is unaffected. Cost is
  cosmetic discontinuity in a customer-visible identifier.
  **Recommended.**
- **(B) Teach the module ERP's grammar.** This is a new identity-shaping field
  on `number_series`, which means a second migration, a new column in
  `_IDENTITY_COLUMNS` and the freeze trigger, a new check constraint, a version
  bump and a new kernel floor — to a package that just reached its completion
  gate and has not been published. Out of slice-1 scope, and it re-opens a
  closed package to serve one adopter's legacy cosmetics.

If (A) is chosen, **the shadow comparison in `EXTRACTION.toml`'s
`shadow_and_drift` is wrong as written** ("compare the formatted string"). The
invariant that actually holds is:

> `legacy.replace(separator, "") == module.replace(separator, "")`
> **and** `legacy_counter_delta == module_counter_delta == 1`
> **and** `legacy_period == module_period`.

That is a normalized-string comparison plus the two structural comparisons —
which is the real content of the check, since a separator difference is
expected and a digit, period or value difference is not.

### Divergence gate before any seed

Two configurations the module refuses outright, both operator-reachable in ERP
through `update_sequence` (`numbering.py:409-416` sets `include_year` and
`reset_frequency` independently with no cross-validation). Run these queries
per organization and adjudicate before seeding — a refusal at seed time is
fine, a refusal discovered at cutover is an outage:

- `reset_frequency='YEARLY' AND include_year = false` → refused by
  `SeriesConfiguration.validate` (`service.py:132-137`) *and* by check
  constraint `ck_number_series_yearly_prints_year`.
- `reset_frequency='MONTHLY' AND NOT (include_year AND include_month)` →
  refused by `service.py:138-145` / `ck_number_series_monthly_prints_month`.
- `include_month AND NOT include_year` → refused by `service.py:146-150`.
- `year_format NOT IN (2,4)` → ERP silently renders 4 digits via the `else`
  branch at `sequence_utils.py:42-43`; the module refuses
  (`service.py:116-121`).
- `min_digits < 1 OR min_digits > 18` → refused (`service.py:110-115`).

---

## 4. Prerequisites and defects, at ERP's current head

Numbered, each marked. These gate the slice; they are not stylistic.

### D1 — format-grammar divergence · NEWLY FOUND, blocking

`sequence_utils.format_number:48-54` vs `dotmac_numbering.service.format_number
:288-301`. Detailed in §2/§3. No configuration bridges it. Requires decision (A)
or (B) before shadow can define its comparison.

### D2 — `allocate` needs `public.idempotency_records`, which ERP does not have · NEWLY FOUND, blocking

`service.py:457-461` imports `execute_once` / `execute_once_platform` /
`fingerprint_of`; `service.py:480-499` calls one of them for every allocation.
`execute_once` reads and writes `IdempotencyRecord`
(`dotmac_kernel/idempotency_models.py:78-108`), table `idempotency_records` in
`public`, FK to `public.tenants`, created by kernel
`20260810_0018_idempotency_one_owner`.

ERP's `alembic.ini` states plainly that the kernel lineage is
"deliberately ABSENT and must stay so", and
`git grep -l "idempotency_records" origin/main -- alembic/` returns nothing.
ERP's own `app/models/finance/platform/idempotency_record.py` is a different
table with a different key `(organization, endpoint, key)` and is not a
substitute.

Nothing declares this dependency: `manifest.py:47-48` requires
`module_database_roles.v1` and `tenant_scope_catalog.v1` only, and
`dotmac_kernel.prerequisites` has no third spec. So the module's
`require_prerequisites` gate passes, the migration succeeds, and the failure
surfaces at the first runtime allocation.

**This is a defect in the module, not only an ERP gap** — a module whose public
entry point writes a table it does not declare is trusting an assembly it
cannot see. The durable fix is a `kernel_idempotency_ledger.v1` prerequisite
spec that ERP binds to a revision hosting the table, exactly as
`tenant_scope_catalog.v1` is bound to `20260813_tenant_projection` in
`app/migration_bindings.py:50-54`. The slice-1 workaround — an ERP-lineage
migration creating the two ledger tables to the kernel's shape — is the same
work without the declaration, and should not be preferred.

### D3 — ERP runs as a superuser, so FORCE RLS is not yet load-bearing · NEWLY FOUND, gate-relevant

`app/db/session_context.py` (`cross_org_session` warning) records that
`core_org.organization` "returns every row under today's `postgres` superuser
runtime and *zero* rows under `app_user`". A superuser bypasses RLS, FORCEd or
not. `mod_numbering`'s tenant isolation
(`nu_0001_numbering.py:404-414`) is therefore inert until
`scripts/cutover_database_ownership.py` has moved the runtime to `app_user`.

Correctness does not collapse in the meantime — `service.py:341-344`
`_tenant_filter` scopes every query in Python — but the database guarantee the
module advertises is not the one ERP would be getting. State this in the
cutover gate rather than discovering it in a post-incident review.

### D4 — the backdating rewind · STILL PRESENT, unchanged at head

`should_reset` (`sequence_utils.py:12-29`) compares period **inequality**:

```python
    if sequence.reset_frequency == ResetFrequency.YEARLY:
        return bool(reference_date.year != sequence.current_year)
```

Combined with `numbering.py:532-535`, an allocation whose `reference_date` is in
an *earlier* year than `current_year` resets `current_number` to 0 and rewrites
`current_year` backwards; the next current-year allocation resets again to 1.
For BANK_STATEMENT the year **is** printed, so this produces a *reissue* of an
already-issued 2026 number rather than a silent duplicate across years — still a
unique-key collision waiting in the module, and a live risk here because
historical statement imports are exactly the backdating case.

The module has no equivalent: one counter per `(scope, series, period)`
(`models.py:7-31`) means a 2026 allocation always reads the 2026 counter.

### D5 — `NumberingSequence.preview` is dead code · VARIANCE REPORT WRONG

Covered in §1. `git grep "\.preview"` over `app/` and `tests/` at head finds no
reader of the model property. `templates/finance/settings/numbering.html:45`
renders `item.preview`, which `settings_web.py:226` populates from
`numbering_service.preview_format(seq)`.

Consequence for the brief's divergence class (c): **the hardcoded four-digit
segment is not the preview divergence that affects slice 1.** `preview_format`
honours `min_digits`, so it already renders `STMT2026-00001` correctly. The two
preview divergences that *do* affect slice 1 are (i) `preview_format` reads
`date.today()` (`numbering.py:338`) where the module's `preview` takes an
explicit `reference_date` (`service.py:304-318`), and (ii) after decision (A)
the ERP preview screen shows the *old* grammar for a series the module now
governs. See §6 for what slice 1 owes that screen.

### D6 — two further formatters · NEWLY FOUND

`app/services/fixed_assets/web.py:1304` `_sequence_preview` renders
`f"{prefix}{zfill(current_number+1)}{suffix}"` — it ignores `include_year`,
`include_month` and `separator` entirely, so it is wrong for any dated series.
`app/services/inventory/web.py:433` `_sequence_preview` re-implements both
`should_reset` and `format_number` inline. Neither is slice 1; both join the
retirement ledger, bringing ERP's formatter count to five.

### D7 — a third auto-create route that commits · NEWLY FOUND

`settings_web.get_numbering_list_context` (quoted in §3) creates all 27 sequence
types and commits, on a GET of an admin page. Harmless for slice 1 (the
BANK_STATEMENT defaults are coherent and stable), but it means the legacy
configuration row for a module-governed series will keep being re-created after
cutover — see §6.

Divergence class (d) from the brief — an auto-created series carrying the
invented `DOC-` prefix (`numbering.py:199`) — **does not apply to slice 1**:
`BANK_STATEMENT` is present in `DEFAULT_SEQUENCE_CONFIGS`, so the
`DEFAULT_PREFIXES.get(sequence_type, "DOC")` fallback is unreachable for it. The
detection query is still worth running once across all 27 types before any later
slice, because a `DOC`-prefixed row is evidence of a series nobody ever
configured.

### What ERP already has right — verified, not assumed

Both of the module's declared prerequisites are satisfied, because
`dotmac-files` needed the same two:

- **`tenant_scope_catalog.v1`** → bound to `20260813_tenant_projection`
  (`app/migration_bindings.py:50-54`), which hosts `public.tenants`,
  `public.tenant_domains` and `public.app_current_tenant_id()` in ERP's own
  lineage.
- **`module_database_roles.v1`** → bound to `20260814_database_roles`
  (`:55-59`), which adopts and verifies `app_admin`, `app_user`, `platform_api`.

- **Tenant identity is settled.** `app/tenancy.py:24-33`:
  "the Organization UUID *is* the Tenant UUID". So
  `TenantScope(tenant_id=OrganizationTenantContext.for_organization(organization_id).tenant_id)`
  — no mapping table, no second identifier.
- **The RLS GUC name matches.** ERP sets `app.current_tenant`
  (`app/rls.py:46-50`); the kernel's `app_current_tenant_id()` reads exactly
  that (`20260504_0001:392`).
- **A `tenants` row exists for the FK**, provisioned by
  `reconcile_organization_tenant` from `scripts/create_org.py:48,67`,
  `scripts/bootstrap_instance.py:466`, `scripts/seed_e2e_data.py:151` and the
  admin settings paths. Verify coverage before seeding — an organization with
  no projected tenant row fails `fk_number_series_tenant` at seed time, which is
  the right place to fail.
- **A per-organization iterator already exists** for the seed/backfill script:
  `app/db/session_context.py::for_each_organization`, which primes both the ORM
  layer and both GUCs.
- **The kernel version must move.** ERP pins `dotmac-kernel = "0.1.0a56"` as an
  exact pin, with `tests/architecture/test_kernel_compatibility.py` rejecting
  any range drift. `dotmac-numbering` floors at `0.1.0a65`.

---

## 5. The shadow plan

### Where it hooks in

One place: `bank_statement.py:868-878`, immediately after the legacy call. The
legacy allocator stays authoritative for the whole shadow phase; the module runs
beside it and records.

Sketch of the shape, not the code:

```
if not statement_number:
    reference_date = statement_date or period_start
    statement_id = uuid4()                       # minted BEFORE the number
    statement_number = numbering.generate_next_number(   # legacy, authoritative
        organization_id, SequenceType.BANK_STATEMENT, reference_date=reference_date
    )
    shadow_allocate_statement_number(            # new, ERP-owned, records only
        db,
        organization_id=organization_id,
        reference_date=reference_date,
        document_id=statement_id,
        legacy_number=statement_number,
        legacy_sequence=<the numbering_sequence row, re-read>,
    )
...
statement = BankStatement(statement_id=statement_id, ...)   # was defaulted
```

`statement_id` is currently defaulted rather than passed at `:881-899`.
Minting it first is not incidental: the allocation's identity is the document
the number will be printed on, so `idempotency_key = f"bank_statement:{statement_id}"`
is the only stable, retry-safe key available at that point. This change is
needed at cutover anyway, so make it in the shadow commit.

### Same transaction, own savepoint, committed

`allocate` joins the caller's transaction and never commits (`service.py:26`),
which is what we want — but a shadow must never be able to abort a real import.
Wrap the module call in `db.begin_nested()` and catch, recording the exception
as a divergence. This is mandatory, not defensive: a caught `IntegrityError`
leaves the session unusable unless a savepoint is rolled back, and
`execute_once`'s internal savepoint does not cover an exception escaping
`execute_once` itself.

Successful shadow allocations therefore **commit with the business
transaction**. That is deliberate. It means:

- the module counter tracks the legacy one in real time, so at cutover the
  counter is already correct and only pre-shadow history needs repair;
- the counter delta is observable (a rolled-back shadow would return the same
  value forever and prove nothing);
- a rolled-back import rolls back both, which is itself proof 4 running in
  production.

### What is compared, per allocation

| Comparison | Assert | Expected divergence |
| --- | --- | --- |
| Formatted string, **separator-normalized** | equal | none |
| Formatted string, raw | recorded | **always differs** under decision (A) — count it, do not alarm on it |
| Counter delta | legacy `current_number` +1 and module `next_value` +1 | none |
| Reset decision | `should_reset(seq, ref)` vs whether the module's `(series, period)` counter was newly created | differs on backdating — see class (b) |
| Period | legacy `(current_year, current_month)` after the call vs module `Allocation.period_key` | differs on backdating |
| Value | legacy parsed sequence segment vs `Allocation.value` | differs after the first backdating event, permanently |

### Where divergence is recorded

An ERP-owned table (`finance` or a small `core_config` addition), not a log
line. A cutover gate needs a queryable count with a class label, a
`statement_id`, both numbers, both periods and the reference date. It is created
and dropped by the shadow slice and is not a module table. Structured logging
alone cannot answer "how many class-(b) divergences in the last 30 days", which
is precisely the gate question.

### Divergence classes to adjudicate before cutover

**(a) `reference_date` substitution moving an allocation across a reset
boundary — DOES NOT APPLY to slice 1.** This is the whole point of choosing this
callsite: the legacy path already receives `statement_date or period_start`, so
shadow passes the identical value and there is no substitution. *Detect anyway*
with a one-line assertion that the value handed to the module is object-identical
to the one handed to the legacy allocator — a cheap regression guard for a later
refactor that reintroduces a default. This class is the dominant risk for every
*later* slice, where the date must be chosen and threaded.

**(b) ERP's backdating rewind produces a value the module refuses — the real
risk here.** Detect by evaluating, before the legacy call:

```
rewound := should_reset(seq, ref) AND seq.current_year IS NOT NULL
           AND ref.year < seq.current_year
```

When `rewound` is true, ERP restarts at 1 for `ref.year` while the module
continues that year's own counter. Two consequences, both recordable:

1. The legacy number is very likely one already issued for that year. Confirm
   directly: `SELECT 1 FROM banking.bank_statement WHERE organization_id = ? AND
   statement_number = ?` before insert. If it hits, ERP is about to violate its
   own duplicate check — that is a *pre-existing ERP defect surfaced by the
   shadow*, worth reporting on its own.
2. The module's receipt for that period may collide on
   `uq_allocation_receipts_value` if backfill (§6) already recorded that value.
   The savepoint catches it and it is recorded as class (b).

Gate: **zero unadjudicated class-(b) events** in the shadow window, or an
explicit written finding that every one was an ERP reissue and the module was
right to differ.

**(c) Preview divergence.** Not the hardcoded-four-digits story (D5). Two real
ones: `preview_format` reads `date.today()` while the module's `preview` takes
an explicit date, and after decision (A) the settings screen renders the retired
grammar. Detect by rendering both for BANK_STATEMENT on every settings-page load
during shadow and recording inequality. Gate: the screen is corrected in the
same commit that flips the allocation (§6).

**(d) Auto-created legacy series with an invented `DOC-` prefix.** Unreachable
for BANK_STATEMENT (D7). Run the detection query once —
`SELECT organization_id, sequence_type FROM core_config.numbering_sequence WHERE
prefix = 'DOC'` — and record the result as baseline evidence for later slices.

### Shadow exit criteria

- ≥ N allocations observed across ≥ 2 organizations, including at least one
  import whose `reference_date` falls in a prior year (force one in a
  non-production organization if production does not supply one);
- zero divergences in any column except raw formatted string;
- every class-(b) event adjudicated in writing;
- at least one rolled-back import observed leaving no receipt and no counter
  movement.

---

## 6. Backfill, the cutover gate, and what actually retires

### Backfill

Per `(organization, period)`, in one transaction, in this order:

1. **`configure_series`** with the §3 configuration read from the live
   `numbering_sequence` row. Do this before anything else: nothing else in the
   module works without it, and it must happen while the series has no history
   (`_assert_safe_transition`, `service.py:221-248`) — after the first
   allocation the identity fields freeze, in the service *and* in the
   `number_series_identity_freeze` trigger.

2. **Recover the issued numbers** from the consuming table:

   ```sql
   SELECT statement_number, statement_date, period_start
     FROM banking.bank_statement
    WHERE organization_id = :org
      AND statement_number LIKE 'STMT%'
   ```

   Then parse `STMT<YYYY>-<NNNNN>`: `period_key` is the four-digit year taken
   **from the number itself**, and `allocated_value` is the integer after the
   last separator. Do not derive the period from `statement_date` — that column
   is mutable and the number is not.

   **Exclusions, all verified present at head:** `mono_sync.py:1503` writes
   `MONO-YYYYMM` and `paystack_sync.py:571` writes `PSK-YYYYMMDD-YYYYMMDD`,
   neither through any sequence; and `banking.py:366` /
   `web_parts/statements.py:1532` pass user-supplied numbers. None of these are
   allocations and none may become receipts. A prefix filter alone is not
   sufficient if a user-supplied number happens to start with `STMT` — require
   the full `^STMT\d{4}-\d{5}$` shape and route anything else to a manual list.

3. **Insert one receipt per recovered number.** `allocation_receipts` grants
   `SELECT, INSERT` to `app_user`/`platform_api` and the append-only trigger
   fires on `UPDATE OR DELETE` only (`nu_0001_numbering.py:431-435`), so a
   backfill INSERT is permitted and is permanent — get it right the first time.
   `reference_date` = `statement_date or period_start`, but **only where its
   year matches the number's year**; where they disagree, skip the receipt and
   record the statement on a manual list rather than inventing a date into an
   immutable row. `idempotency_key` = `f"backfill:bank_statement:{statement_id}"`.
   `allocated_by` = the backfill actor.

   Under decision (A) these receipts hold the *legacy* grammar while later
   receipts hold the new one. That is honest evidence, and it is safe:
   `uq_allocation_receipts_rendered` is per `(tenant, series_code,
   formatted_number)` and the two grammars are disjoint strings.

4. **Repair the counter forward**, one call per period:

   ```python
   advance_to_at_least(
       db, scope=TenantScope(tenant_id=organization_id),
       series_code="BANK_STATEMENT",
       period_key="2026",
       proven_minimum=<see below>,
       reason="ERP slice-1 cutover: counter reconciled to issued bank-statement numbers",
       repaired_by="<operator or job identity>",
   )
   ```

   `advance_to_at_least` sets `next_value = proven_minimum + 1` and only when
   that exceeds the current value (`service.py:616-620`), so it can never
   rewind. It creates the counter at `start_value` if absent
   (`_locked_counter`, `service.py:383-408`) and writes one immutable
   `series_repairs` row per attempt — which is the audit trail for the whole
   backfill.

   **`proven_minimum` is the max of two independent sources**, and taking both
   is what makes it drift-resistant:
   - the maximum parsed value for that period from step 2; and
   - for the period equal to `numbering_sequence.current_year`, the row's
     `current_number` — which is the last issued value, since ERP increments
     before formatting (§2).

   D4 is exactly why both are needed: a rewound `current_number` understates
   the truth, and the parsed maximum catches it.

   Note the ordering property this buys. If step 4 were skipped after step 3,
   the next live allocation would take value 1 and violate
   `uq_allocation_receipts_value` — so **the backfill makes a missing repair
   fail loudly instead of silently reissuing.** That is a reason to do the
   receipt backfill even where it is only partially recoverable.

5. **Verify before commit**: module `next_value` for each period equals
   `max(recovered) + 1`; receipt count equals recovered count minus the manual
   list; `series_repairs` holds exactly one row per repaired period.

Never rewind, never `UPDATE` a receipt, never `DELETE` one. All three are
refused by grant and by trigger; the point of saying it is that the *script*
must not try.

### The cutover gate

Flip only when all of these are true. G0-G3 are hard blockers.

- **G0 — the artifacts exist.** `dotmac-numbering 0.1.0a1` is unreleased
  (no `dotmac-numbering-v*` tag) and its kernel floor `0.1.0a65` is unpublished
  (highest tag is `dotmac-kernel-v0.1.0a64`). Both must be published and
  installable from the Forgejo index. This is the gate ADR-0030 §5 leaves open.
- **G1 — D1 decided.** Decision (A) or (B), written as an ADR, with the shadow
  comparison defined accordingly.
- **G2 — D2 closed.** `public.idempotency_records` and
  `public.platform_idempotency_records` exist in ERP to the kernel's shape,
  preferably through a declared `kernel_idempotency_ledger.v1` prerequisite
  bound in `app/migration_bindings.py`.
- **G3 — composition landed.** `dotmac-kernel` moved `0.1.0a56 → ≥0.1.0a65`
  (exact pin) and `dotmac-numbering` added to `pyproject.toml`;
  `dotmac_numbering.migrations:versions` appended to `alembic.ini`'s
  `version_locations`; a composition test mirroring
  `tests/architecture/test_files_composition.py`; `mod_numbering` migrated by
  `scripts/deploy.sh` as `app_admin`.
- **G4** — every organization seeded, backfilled, repaired and verified (§6),
  with the divergence-gate queries in §3 returning zero unadjudicated rows.
- **G5** — the shadow exit criteria in §5 met, with the class-(b) adjudication
  written down.
- **G6 — D3 acknowledged.** Either the runtime has moved to `app_user`, or the
  gate records in writing that `mod_numbering`'s tenant isolation is currently
  enforced by `_tenant_filter` in Python and not by PostgreSQL.
- **G7** — a rollback plan that is a config flag, not a revert: the legacy
  allocator stays in place and callable for one release after the flip.

**A green suite is not a cutover.** Nothing in ERP's suite touches a real
database for numbering (variance report Defect 2, unchanged at head: 31 mocked
tests, `MagicMock` at `:68`, `AsyncMock` at `:73`, and `tests/ifrs/platform/
conftest.py`'s own docstring admitting the mocking). The evidence that matters
here is the shadow window's divergence table, not CI.

### What retires at the flip — and what emphatically does not

**Retires:** exactly the auto-generate branch at `bank_statement.py:868-878`.
That is one call to `SyncNumberingService.generate_next_number` for one
`SequenceType`. Nothing else.

**Does not retire** — every one of these still has live callers at
`9d67c399`:

| Named in `EXTRACTION.toml`'s `local_copy_retirement` | Why it survives slice 1 |
| --- | --- |
| `SequenceService` (`platform/sequence.py`) | 16 callsites, none of them bank statements |
| async `NumberingService` | still the async surface, incl. `initialize_all_sequences` and `settings_web` |
| `SyncNumberingService` | still allocates INVOICE, PAYMENT, CREDIT_NOTE and everything the 16 route to |
| `NumberingService.preview_format` | still renders all 27 types on the settings screen |
| `NumberingSequence.preview` | already dead (D5) — deletable at any time, on its own merits, not as part of this slice |
| `PayrollNumberingService` + `people.payroll_number_sequence` | untouched |
| `careers_service.py:318-342` | untouched, and still needs its cross-organization scope decided |
| `sequence_utils.format_number` / `should_reset` | still the allocation path for 26 series |
| the `sequence_type` PostgreSQL enum | all 27 members still live; the expand/contract migration belongs to the last slice, not the first |

**One obligation the dossier does not record.** After the flip, the legacy
BANK_STATEMENT row still exists, is still shown on
`/…/settings/numbering`, is still editable, and is still re-created by
`get_numbering_list_context` (D7) if deleted. An operator editing it would
change nothing while believing they had changed the numbering of every future
statement. **Slice 1 must therefore also mark BANK_STATEMENT read-only on that
screen** — a "governed by `dotmac-numbering`" marker rendering the module's
`preview(db, scope=…, series_code="BANK_STATEMENT", reference_date=today)`
instead of `preview_format`. Pointing the whole screen at the module is the
right end state and the wrong slice-1 scope; a per-type lock is minimal and
reversible.

---

## 7. What slice 2 actually is

**The `EXTRACTION.toml` answer — `app/services/dotmac_sub/sync/_base.py:286,294,302`
— is wrong as a slice boundary, and the reason is not the date.**

The date part is fine. All three methods thread a real business date from their
callers: `_invoices.py:285` passes `invoice_date`, `_payments.py:286` passes
`payment_date`, `_credit_notes.py:283` passes `cn_date`. None relies on the
`None` default. So date-readiness is genuine.

The problem is **series co-ownership**. Those three methods allocate
`SequenceType.INVOICE`, `PAYMENT` and `CREDIT_NOTE`, and INVOICE and PAYMENT are
allocated elsewhere too:

```
INVOICE   app/services/dotmac_sub/sync/_base.py:291     (SyncNumberingService, dated)
          app/services/finance/ar/invoice.py:391        (SequenceService, NO date)
          app/services/finance/ar/quote.py:462          (SyncNumberingService, NO date)
          app/services/finance/ar/sales_order.py:612    (SyncNumberingService, NO date)
          app/services/finance/automation/recurring.py:459 (SyncNumberingService, NO date)
PAYMENT   app/services/dotmac_sub/sync/_base.py:299     (SyncNumberingService, dated)
          app/services/finance/ap/payment_batch.py:111  (SequenceService, NO date)
          app/services/finance/ap/supplier_payment.py:412 (SequenceService, NO date)
          app/services/finance/payments/batch_transfer_service.py:108 (SequenceService, NO date)
```

Note `quote.py:462` and `sales_order.py:612`: they call `SyncNumberingService`
with keyword arguments and simply **omit** `reference_date`, so they fall through
to `date.today()` at `numbering.py:511-512` — a third date-less shape the
16-callsite inventory does not capture.

Cutting `_base.py` over alone would leave the module's `BANK_STATEMENT`-style
per-period counter and ERP's `current_number` **both allocating INVOICE**. Two
writers for one counter is precisely what the source-of-truth standard forbids,
and here it produces duplicate invoice numbers on the first day.

**So the slice unit is a series, and the slice-1 property that matters is
"exactly one allocator", not "already passes a date".** The revised order:

- **Slice 1** — `BANK_STATEMENT`. One allocator, already dated. Verified.
- **Slice 2** — the next series with exactly one allocator *and* a nameable
  business date. From the census, the single-mention candidates are
  `CREDIT_NOTE` (dated already, via `_base.py:307` — genuinely atomic, and the
  correct slice 2), then `RECEIPT`, `PURCHASE_ORDER`, `LEASE`, `GOODS_RECEIPT`,
  `QUOTE`, `SHIPMENT`, `SUPPORT_TICKET`, `PROJECT`, `CUSTOMER`, `EMPLOYEE`,
  `LEAVE_APPLICATION`, `EXPENSE_INVOICE`, `CONTRACT` — each of which needs its
  date chosen first. Confirm each candidate's allocator count directly before
  committing to it; the census counts mentions, not allocations.
- **Slice N (multi-allocator series)** — `INVOICE`, `PAYMENT`, `JOURNAL`,
  `TASK`, `SUPPLIER_INVOICE`, `ASSET`, `EXPENSE`. Each is one slice covering
  *all* of its allocators at once, and each requires the business date to be
  chosen and threaded through every one of them first. That threading is the
  real cost: `SequenceService.get_next_number`
  (`platform/sequence.py:37-69`) has the signature
  `(db, organization_id, sequence_type, fiscal_year_id=None)` — no
  `reference_date` parameter at all — and it delegates positionally at
  `:67-69`, so the parameter does not exist to be passed. Sixteen callsites
  need a date decision (invoice date? posting date? period start?), a signature
  change, and in several cases a schema and API change to carry the date in
  from the request.
- **Last** — the `sequence_type` enum expand/contract, once no allocator reads
  it.

---

## 8. Summary of what would change the cutover order

1. **D1 (format grammar) is the decision the slice waits on**, and it is not
   mentioned in any prior document. Until it is made, the shadow comparison has
   no definition.
2. **D2 (idempotency ledger) is a module-level declaration gap**, not an ERP
   chore. Fixing it as a declared prerequisite benefits every future adopter;
   fixing it as an ERP-local migration hides it until the next one hits it.
3. **Order by allocator count, not by date-readiness.** Date-readiness is
   necessary and not sufficient; a series with two allocators cannot be cut over
   in one slice at all. This demotes `_base.py` from slice 2 to a partial
   participant in three later series slices, and promotes `CREDIT_NOTE` — the
   one `_base.py` series with a single allocator — to slice 2.
4. **Slice 1 owes the settings screen a read-only marker**, or it ships a
   control that silently does nothing.
5. **D3**: `mod_numbering`'s RLS is inert under ERP's current superuser runtime.
   Either move the runtime or state the limitation at the gate.

---

## Appendix — draft `EXTRACTION.toml` corrections

Inline only. **Do not create or edit any file from this appendix.** These are
the two fields in `packages/dotmac-numbering/EXTRACTION.toml` this analysis
shows to be inaccurate, drafted as they would read after slice 1 is planned
correctly. `contract_consumers` stays `[]` until the flip.

```toml
first_cutover = "dotmac_erp, tenant plane, one SERIES at a time, ordered by whether the series has exactly ONE allocator and can name its business date — in that order of precedence, because a series with two allocators cannot be cut over in one slice at all. Slice 1 is SequenceType.BANK_STATEMENT at app/services/finance/banking/bank_statement.py:874-878: verified at dotmac_erp 9d67c399 as the only allocation site for that series in app/, and it already passes reference_date=statement_date or period_start. Slice 2 is SequenceType.CREDIT_NOTE at app/services/dotmac_sub/sync/_base.py:302-307, the only _base.py series with a single allocator (its caller _credit_notes.py:283 passes cn_date). INVOICE and PAYMENT are NOT slice 2: _base.py:291 and :299 share those series with ar/invoice.py:391, ar/quote.py:462, ar/sales_order.py:612, automation/recurring.py:459, ap/payment_batch.py:111, ap/supplier_payment.py:412 and payments/batch_transfer_service.py:108, so cutting _base.py alone would leave two writers on one counter. Each multi-allocator series is one slice covering all of its allocators at once, and each first needs a business date chosen and threaded, because SequenceService.get_next_number (app/services/finance/platform/sequence.py:37-69) has no reference_date parameter and two SyncNumberingService callers (quote.py:462, sales_order.py:612) simply omit it. Three blockers precede slice 1: dotmac-numbering 0.1.0a1 and kernel 0.1.0a65 are both unpublished; ERP has no public.idempotency_records, which allocate requires and no prerequisite declares; and the module's formatter joins every segment with the separator while ERP fuses prefix+year+month, so no SeriesConfiguration reproduces ERP's STMT2026-00001 and the format change must be decided before shadowing. dotmac_sub follows on the tenant plane after ERP's is proven; dotmac_vendor_control_plane is the first platform-plane adopter through Billing."
shadow_and_drift = "Neither source has a real-database test, so the shadow phase is the first genuine evidence. Per ERP series: seed explicitly from the LIVE core_config.numbering_sequence row per organization (never from DEFAULT_SEQUENCE_CONFIGS — update_sequence and SequenceService.configure_sequence can have changed it), refusing to seed any row the module's coherence checks reject (yearly without include_year, monthly without year+month, month without year, year_format not in (2,4), min_digits outside 1..18). Then run the module allocator beside the legacy one inside the caller's transaction, in its OWN savepoint so a shadow failure can never abort a real business transaction, and COMMIT successful shadow allocations so the counter tracks the legacy one in real time. The formatted-string comparison must be SEPARATOR-NORMALIZED: the module joins every segment with the separator (service.py:288-301) while ERP fuses prefix+year+month and separates only the sequence (sequence_utils.py:48-54), so a raw-string comparison fails on every allocation by construction. Compare instead: normalized string equality, counter delta of exactly one on both sides, and the same period. Record divergence in an adopter-owned table with a class label, not a log line — a cutover gate needs a queryable count. Divergence classes: (a) reference_date substitution across a reset boundary — DOES NOT APPLY to slice 1, whose callsite already passes the production date, but assert object identity anyway as a regression guard, and it is the dominant risk for every later slice; (b) the ERP backdating rewind, detected as should_reset(seq, ref) AND ref.year < seq.current_year, which makes ERP restart the earlier year at 1 and reissue a number it already issued — check the consuming table for that number before insert, because a hit is a pre-existing ERP defect the shadow surfaced; (c) preview divergence, which for slice 1 is NOT the hardcoded four-digit segment (NumberingSequence.preview has zero callers at ERP head; settings_web.py:226/:264 call the correct preview_format) but preview_format reading date.today() and rendering the retired grammar after cutover; (d) auto-created series carrying the invented DOC- prefix, unreachable for BANK_STATEMENT because it has a DEFAULT_SEQUENCE_CONFIGS entry, but worth one baseline query across all 27 types. Backfill per (organization, period): configure_series first while the series still has no history and its identity fields are still unfrozen; recover issued numbers from the consuming table by full-shape regex, excluding the writers that never allocated (mono_sync.py:1503 MONO-, paystack_sync.py:571 PSK-, and user-supplied numbers); derive period_key from the NUMBER, not from a mutable date column; insert one immutable receipt per recovered number, skipping any whose date-year disagrees with its number-year rather than inventing a date into an append-only row; then advance_to_at_least once per period with proven_minimum = max(parsed maximum, numbering_sequence.current_number for the current period), taking both because the backdating rewind can have understated current_number. Never rewind. The receipt backfill also makes a forgotten repair fail loudly on uq_allocation_receipts_value instead of silently reissuing."
```
