# Numbering sources

**As of:** 2026-08-15
**Starter:** `991213c5ccaf`
**ERP:** `0f4b1698ddbf` (revision-pinned reads; worktree had local changes)
**Sub:** `27c76aaeebb7`
**Decision:** ADR-0017 names `dotmac-numbering`; ADR-0030 authorizes it as the
first enabling owner in the Cloud commerce build order.

This audit resolves which implementation must be the starting point before the
package is created. It does not claim either source is safe to copy unchanged.

## Verdict

`dotmac-numbering` is **product-first with two mandatory sources**:

- ERP supplies the configurable, date-aware series model, format/reset rules,
  sync/async service behavior, broad production usage and the parity suite.
- Sub supplies the stronger first-use/concurrency shape: conflict-safe row
  establishment, `SELECT FOR UPDATE`, monotonic reconciliation and real
  invoice/credit-note output tests.

ERP remains the primary initial code source named by ADR-0017. Sub's creation
and reconciliation behavior is a mandatory port delta, not an optional
enhancement. Neither product supplies the complete shared contract.

> **Corrected 2026-08-15 — Sub's "monotonic reconciliation" credit is wrong.**
> The 2026-08-15 revalidation
> ([`numbering-source-variance.md`](numbering-source-variance.md)) found
> `reconcile_next_value` is dead code: `git grep` at Sub's current head returns
> exactly one line, its own definition. What Sub actually runs is hand-
> duplicated in three consuming services that lock the sequence row and write
> `next_value` directly, with uniqueness enforced by the consuming table's index
> behind collision-retry loops. **Sub is a source for the
> `INSERT ... ON CONFLICT DO NOTHING` + `SELECT FOR UPDATE` establishment SQL
> only.** Monotonic repair is greenfield.
>
> Note the failure mode, because it is not the usual one. Every mandatory path
> below is byte-identical to its pin — the diff is empty. The dossier's
> *description* of that unchanged code was wrong when written, so an empty diff
> reads as reassurance while the error survives. Where this file and the
> variance report disagree, **the variance report wins**.

## ERP source

Mandatory paths:

- `app/models/finance/core_config/numbering_sequence.py`;
- `app/services/finance/common/numbering.py`;
- `app/services/finance/common/sequence_utils.py`; and
- `tests/ifrs/common/test_numbering_service.py`.

The source has 41 tracked application files calling
`SyncNumberingService`/`SequenceService.get_next_number`. The focused suite
covers defaults, never/yearly/monthly resets, two/four-digit years, optional
month/prefix/suffix, custom separators, padding, large values, increments,
previews and the synchronous/asynchronous surfaces. This is production-used,
tested behavior and therefore the mandatory product-first base.

Do not port:

- `SequenceType` as a closed enum. Series are declared data owned by consuming
  products; the module must not require a release for a new document kind.
- ERP's default-prefix dictionary. A missing series/configuration fails closed;
  the module does not invent an invoice, journal or payroll convention.
- `organization_id`, finance schemas, fiscal-year models, FastAPI errors, or
  service commits.
- duplicate compatibility services. The shared module has one synchronous
  transaction-bound service; async transport can call it through the host's
  normal transaction boundary rather than becoming a second writer.

Known defects/deltas:

1. ERP's first-use path performs query then insert. Two callers can both see no
   row and race on the unique constraint before either owns a row lock.
2. Every focused database test uses mocks; none proves PostgreSQL concurrency,
   rollback, RLS or the first-use race.
3. `last_used_at = datetime.now()` is naive despite a timezone-aware column.
4. formatting and preview contain parallel logic.
5. reset/update permits an operator to rewrite counters without immutable
   allocation evidence, so a committed number can be reused.

## Sub source

Mandatory paths:

- `app/models/sequence.py`;
- `app/services/numbering.py`; and
- `tests/test_numbering_defaults.py`.

Sub's service establishes a sequence with `INSERT ... ON CONFLICT DO NOTHING`,
then takes `SELECT FOR UPDATE`. ~~It can advance a sequence to a proven minimum
without rewinding it.~~ **Corrected 2026-08-15:** the advance-to-minimum
function (`reconcile_next_value`) has no caller anywhere in Sub — it is dead
code, and the behaviour that actually ships is hand-duplicated in three
consuming services. The output tests prove configured start values and
formatting reach real invoice and credit-note records.

Do not port:

- settings lookups from inside the numbering owner;
- globally scoped `key` with no tenant/control-plane declaration;
- product keys such as `invoice_number` or `project_number`;
- silent fallback from invalid settings to `1`, empty prefix or zero padding;
  and
- unversioned configuration with no allocation receipt.

Sub has no direct concurrency or rollback suite. Its safer SQL shape is a code
source, not proof; fresh PostgreSQL tests remain mandatory.

## Shared contract

The first release owns:

- explicitly configured series identified by an open string `series_code`;
- validated prefix/suffix/separator, digit width, start value, year/month
  inclusion and never/yearly/monthly reset policy;
- concurrency-safe allocation under a row lock;
- one immutable allocation receipt per `(scope, series_code, idempotency_key)`;
- conflict when that key is replayed with a different request fingerprint;
- exact replay of the original formatted number;
- an explicit business `reference_date`, never process date/time or environment;
- rollback with the consuming business transaction, so a failed invoice does
  not consume a committed allocation; and
- a monotonic repair operation that may advance to proven external evidence and
  never rewinds or deletes committed allocation history.

It does **not** own what a number means, which documents require one, legal
gaplessness policy, fiscal periods, invoice issuance, rendering, or a product's
series vocabulary. Billing and other domain owners choose the series and pass
the business date. Numbering allocates and formats only.

## Fresh proof required

1. tenant-plane RLS and cross-tenant allocation isolation;
2. platform-plane revocation from `app_user` and reachability by `platform_api`;
3. two concurrent first allocations cannot duplicate a value or fail the
   series-creation race;
4. a rolled-back consuming transaction does not leave a number consumed;
5. same-key/same-fingerprint replay returns the same result, while a changed
   fingerprint fails;
6. reset boundaries use the supplied date and never the worker clock;
7. configuration change cannot rewrite an allocation receipt or rewind a
   counter; and
8. tenant and platform paths run the same behavior suite and produce the same
   number for otherwise identical input.

## Adoption and retirement

ERP is the first intended tenant-plane cutover because it has 41 caller files
and the richest format contract. Cut over one series family at a time, shadow
the generated value, then remove the corresponding local writer. Sub follows
for invoice/credit-note/order/project series after the module's tenant plane is
proven. Vendor CP is the first platform-plane adopter through Billing. Dotmac
Cloud consumes the tenant plane through Billing and Orders.

The package is not adopted until at least one caller uses the exact released
version and its old writer is retired. A reference-assembly migration test is
not adoption.
