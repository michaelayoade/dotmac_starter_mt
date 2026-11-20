# Changelog — dotmac-accounting

## 0.1.0a3 — unreleased candidate

- Add `ac_0004_account_maintenance`: the append-only `account_changes` history
  and a database guard on `accounts`. The account `id` and `tenant_id` never
  change; `code`, `kind`, `posting_allowed` and `is_active` change only together
  with a matching history row written in the same transaction (the row is
  stamped with the database transaction id).
- Add governed, idempotent (`execute_once`) commands `recode_account`,
  `update_account_flags`, `retire_account` and `activate_account`
  (`RecodeAccount`, `UpdateAccountFlags`, `RetireAccount`, `ActivateAccount`),
  each recording reason, actor and the full before/after snapshot.
- `recode_account`: new code unique per tenant and, when the caller supplies the
  tenant chart-policy pattern, matching it completely. Posted ledger lines keep
  the `account_code` they were posted under (history); the account is
  identified by `account_id`, which never changes.
- `retire_account` refuses a non-zero posted balance, any draft journal line,
  an active child account, and a positive caller-attested count of open
  subledger items (accounting cannot see sibling modules); it sets the account
  inactive and non-posting. `activate_account` reverses it, posting stays off
  unless requested for a posting account.
- The guard also refuses any change to `category_id`, `normal_balance`,
  `parent_id` and `currency_code`; `update_account_flags` refuses moving a
  posted-to account away from POSTING. Callers must always pass the pinned
  numeric `code_pattern` to `recode_account`.
- Downgrade of ac_0004 refuses once any history row exists.

## 0.1.0a2 — unreleased candidate

- Add `ac_0002_posted_content` without changing the published root migration.
- Freeze both OLD and NEW journal ancestry for source lines and dimension
  assignments, including reparenting; serialize content writes with posting.
- Preserve draft editing and FK cascades. Missing or hidden ancestry refuses
  ordinary writes; nested cascade deletion relies on the parent's draft guard.
- Bound migration lock acquisition, drain predecessor writers, and refuse
  downgrade while non-draft journals or appended ledger evidence exist.
- Add `ac_0003_draft_void` and `void_draft_journal`/`VoidDraftJournal`: a
  governed, idempotent retirement of a never-posted DRAFT journal (reason,
  requester, a distinct approver, approval reference) that appends an immutable
  `JOURNAL_VOID` period event in the same transaction. The database refuses
  DRAFT->VOID without that event and refuses any posting evidence; posted or
  reversed journals can never be voided (reverse only). VOID content stays
  frozen. Downgrade refuses once a voided journal or void event exists (the
  pa_0003/pa_0004 policy). No new tables.
- Fix the creator helpers (`create_account_category`, `create_account`,
  `create_fiscal_year`, `create_fiscal_period`, `create_dimension`,
  `create_dimension_value`) to use the kernel's public `dotmac_kernel.transactions.conflict_savepoint`
  instead of importing `dotmac_kernel.db`, whose
  reference runtime fails inside a composed app ("Could not parse SQLAlchemy
  URL"). Raise the kernel floor from `>=0.1.0a85` to `>=0.1.0a98`, the first kernel
  carrying the public `dotmac_kernel.transactions` (ERP pins a107).
- A void is allowed in any period status (it has no ledger effect), including
  a LOCKED period, so Finance can retire legacy drafts without reopening.
- Add real PostgreSQL canaries for fresh and exact released-predecessor schemas,
  role/RLS enforcement, reversal parity, observed lock races and safe retry.
  Hosted CI evidence is required before acceptance.

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32477996764`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Product-first tenant accounting owner extracted from ERP behavior.
- Chart, fiscal-period, dimension, journal, balanced posting, reversal and
  immutable ledger/period evidence contracts.
- Independent `ac` migration lineage in `mod_accounting` with forced RLS.
