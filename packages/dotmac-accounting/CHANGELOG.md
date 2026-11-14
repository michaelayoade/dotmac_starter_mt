# Changelog — dotmac-accounting

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
  `create_dimension_value`) to use the kernel's `_transactions.conflict_savepoint`,
  as the idempotency module does, instead of importing `dotmac_kernel.db`, whose
  reference runtime fails inside a composed app ("Could not parse SQLAlchemy
  URL"). Kernel floor unchanged (`_transactions` exists at a85).
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
