# Fixed-asset accounting sources

**As of:** 2026-08-18  
**Starter:** `300ebd7` (`origin/main`)  
**ERP:** `b969a889e8aba7255e32aa466960c22347c02fd8` (`origin/main`)  
**Sub:** `430a09e847715832c1dd8e67a16f07d77c8a0fcc` (`origin/dev`)  
**CRM:** `60daaa2dd305696636632f48505ab784110a55d2` (`origin/main`)  
**Vendor CP:** `f8f8c3fd636e663e4a17275c19e82fc1667aa52a` (`origin/main`)

## Verdict

ERP is the only qualifying product source for fixed-asset accounting. It has 13
fixed-asset model files, 12 service files, 14 related test files and 106 internal
caller hits across API/web adapters, AP invoice posting and scheduled finance
work. Sub, CRM and Vendor CP contain no competing depreciation, impairment,
revaluation or carrying-value owner at their pinned heads.

The reusable unit is narrower than ERP's package. `dotmac-finance` owns the
fixed-asset **book** and its accounting decisions. A physical Assets owner owns
identity, condition, custody, maintenance and disposal workflow. Chart of
accounts, fiscal periods and the general ledger remain separate owners. Those
boundaries meet through opaque references and source-versioned consequences,
not imports, shared tables or cross-module foreign keys.

## Mandatory source surface

ERP's model surface is under `app/models/fixed_assets/`, chiefly `asset.py`,
`asset_component.py`, `depreciation_run.py`, `depreciation_schedule.py`,
`asset_impairment.py`, `asset_revaluation.py`, `asset_disposal.py` and
`gl_reconciliation.py`. The behavior source is
`app/services/fixed_assets/{asset,capitalization,depreciation,disposal,fa_posting_adapter,reconciliation,revaluation}.py`.
The AP helper that creates asset records and the scheduled finance task are
mandatory caller inputs, not alternative owners.

The parity suite contributes real business expectations even though most tests
mock persistence: straight-line residual value, declining/double-declining final
true-up, catch-up periods, refusal of stale schedules, creator/poster separation,
revaluation reserve versus profit-or-loss allocation, disposal gain/loss as net
proceeds less carrying value, reconciliation intent and scheduled execution.

## Source defects not ported

- Physical and accounting state are fused: posting depreciation can set a
  physical asset status to `FULLY_DEPRECIATED`. A zero depreciable balance is
  not a condition, custody or lifecycle transition.
- Several feature services call `commit()` and error paths call `rollback()`.
  That violates Starter's single transaction authority and can erase tenant
  `SET LOCAL` context.
- `asset_component`, `asset_disposal`, `asset_impairment`,
  `asset_revaluation`, `depreciation_schedule` and GL reconciliation child
  tables lack their own forced RLS policy. Parent scoping is not direct tenant
  isolation.
- The impairment web response parses a form and redirects success without
  writing an impairment. The model has no operational writer.
- The disposal posting adapter temporarily debits the gain/loss account for
  proceeds instead of cash/receivable/clearing, then posts gain/loss again.
  Mocked tests prove invocation, not line semantics.
- Revaluation derives available reserve/loss by summing all historical rows
  without consuming earlier reversals, so repeated changes can over-allocate.
- `UNITS_OF_PRODUCTION` is declared without a usage measurement contract and
  unknown methods silently fall through to straight-line. The extracted closed
  vocabulary refuses unsupported methods.
- A `REVERSED` depreciation status exists without a reversal writer. AP
  capitalization logs and continues after fixed-asset creation fails, allowing
  the accounting subledger to be silently incomplete.
- The source suite is predominantly mock-based and supplies no PostgreSQL
  tenant-isolation or concurrency proof.

## Corrected extraction contract

Book values are functional-currency `Decimal` amounts with explicit minor-unit
rounding. Depreciation begins from the supplied available-for-use date and is
prospective over remaining life. A calculated run is an immutable snapshot;
posting locks each book, refuses stale versions, enforces creator/poster
separation and writes book changes plus a balanced consequence in the caller's
transaction.

Impairment uses the higher of fair value less costs of disposal and value in use,
allocates a loss against available revaluation reserve before profit or loss,
and caps reversal at the carrying amount that would exist without impairment.
Revaluation and impairment keep running consumed balances rather than summing
history. Derecognition clears gross cost and accumulated balances, uses an
explicit clearing-account reference for net proceeds and records gain/loss.

This matches the relevant recognition/depreciation/revaluation/derecognition
shape in [IAS 16](https://www.ifrs.org/issued-standards/list-of-standards/ias-16-property-plant-and-equipment/)
and recoverable-amount/reversal shape in
[IAS 36](https://www.ifrs.org/issued-standards/list-of-standards/ias-36-impairment-of-assets/).
It is not a claim of complete statutory or tax compliance: tax books,
componentization, useful-life/residual review workflows and general-ledger
period controls require separately owned follow-up slices.

## Cutover gate

ERP remains authoritative today. The first cutover must backfill and reconcile
every book, schedule, valuation balance and consequence; shadow equivalent
operations at declared currency precision; seal the authority switch under
source-book locks; and retire the legacy decision writers with a bidirectional
ratchet. Backoffice is the second independent candidate consumer. Until ERP's
cutover is complete, the package is audit-complete, uncomposed and unpublished.
