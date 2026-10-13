# Changelog — dotmac-accounting

## 0.1.0a2 — unreleased candidate

- Add `ac_0002_posted_content` without changing the published root migration.
- Freeze both OLD and NEW journal ancestry for source lines and dimension
  assignments, including reparenting; serialize content writes with posting.
- Preserve draft editing and FK cascades. Missing or hidden ancestry refuses
  ordinary writes; nested cascade deletion relies on the parent's draft guard.
- Bound migration lock acquisition, drain predecessor writers, and refuse
  downgrade while non-draft journals or appended ledger evidence exist.
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
