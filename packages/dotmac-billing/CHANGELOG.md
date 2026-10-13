# Changelog — dotmac-billing

All notable changes follow Semantic Versioning.

## 0.1.0a5 — UNRELEASED

- Add immutable complete-population opening-manifest contracts and a pure
  cash-only evidence preflight with exact minor-unit controls, ordered cash
  provenance, target bindings and counted refusals. Require an assembly-owned
  verifier for source/cash/migration evidence; candidate hashes establish no
  authority. No opening writer, storage, spendable funding or activation.

## 0.1.0a4 — UNRELEASED

- Refresh the locked draft header and line identities before issuance so a
  concurrent draft edit cannot leave emitted facts with stale totals. Preserve
  pending caller edits and exact replay without completing its transaction.
- Add append-only non-cash grants, applications, partial application reversals
  and unused-grant reductions, with separately linked replacement grants.
- Keep cash funding and prepaid credit distinct. Ordinary account credits carry
  zero tax; immutable versioned classification evidence records policy authority.
- Add bi_0005 grant/effect and V2 position evidence in both persistence planes,
  source-version and per-invoice capacity guards, and V3 accounting facts while
  preserving prior V1/V2 contracts. Older writers require a fenced upgrade.
- Source candidate only; publication, product UI/adoption and production writer
  acceptance remain separate gates.

## 0.1.0a3 — UNRELEASED

- Add invoice-bound withholding acceptance and linked reversal with immutable
  Tax determination evidence. Accepted withholding reduces receivables without
  creating cash or spendable credit; claims and certificate observations remain
  distinct from the accepted financial decision.
- Add append-only withholding effects in `bi_0004_withholding_discharge`, scoped
  account/document links and natural Tax-component duplicate prevention.
- Publish WHT-specific accounting facts as V2 while preserving existing V1
  events and fingerprints. Source presence is not release or Sub adoption.

## 0.1.0a2 — UNRELEASED

- Close line insertion, reparenting, edits and deletion for every issued
  document kind in additive `bi_0003_issued_line_freeze`, with parent-row
  locking and bounded migration table locks. Stage credit-note
  initial lines before issuance in the caller's transaction. Preserve both
  earlier migrations and historical monetary/fact evidence.
- Replay fully credited invoices' exact credit-note commands before mutable
  receivable admission, preserving original keys, fingerprints and effects.
- Add exact-version immutable invoice-fact queries with persisted identity and
  recording metadata, strict typed decoding, scope/plane isolation and raw-JSON
  presentation-digest verification. Reads preserve caller transactions and
  ignore dirty ORM state; malformed or missing evidence fails closed.

- Add typed homogeneous multiline drafts with structural account/obligation
  affinity, exact explicit prices and immutable authoring provenance in an
  additive migration. Keep the released root migration unchanged.
- Preserve all line tax evidence and common FX, revalidate at issuance and
  prevent adding lines to an issued invoice. Exclude unrelated document-owned
  tax/FX rows from credit-note facts.
- Add a typed, atomic cash-backed available-credit draw across confirmed
  settlement sources, preserving allocation provenance and existing financial
  events. Non-cash grants and opening-balance funding remain separate gaps.
- Refuse allocation from reversed settlement funding while preserving exact
  replay of a previously accepted command.
- Rebuild position attestations from persisted numeric values, independent of
  ORM cache scale, preserving the hash algorithm and strict drift refusal.
- Reconcile Sub-first, pay-then-record and open-items-only cutover preparation.

## 0.1.0a1 — historical publication

Publication tag: `dotmac-billing-v0.1.0a1`, peeled commit
`92a1626b16d7e068f92536d8cfcb2ef9b6f270c2`. The historical record is in
`docs/inventories/module-release-legacy-unverified.json`; it does not constitute
a modern wheel-verification receipt or evidence of product adoption.

- First complete Billing V1: frozen obligation, settlement, receivable,
  accounting and invoice-document contracts; one pure financial engine; two
  declared persistence planes; immutable source effects and rebuild hashes.
- Every published command/fact is a closed immutable type, including nested
  party/address/payment/line/discount/accounting-effect values; architecture
  canaries reject `Any`, `object` and unshaped dictionaries at the boundary.
- Make Billing the sole owner of the account/currency `ReceivablePositionV1`
  and invoice-grained `ReceivableExposureV1`, use kernel `Money` on every public
  amount, own financial state without a `reversed` steady state, and preserve
  each exposure's subject/service, service-period and due-date provenance.
- Record exact renderer/file provenance for the Billing-owned official
  artifact relation, with a structural record key, a separate current-row-bound
  repair key, strict semantic mismatch, append-only repair supersession and
  cancellation withdrawal. Artifact creation serializes with cancellation and
  refuses a new medium after a void while preserving historical replay.
- Keep `RatedObligationOutputV1` exclusively on Subscriptions' public surface;
  Billing owns and names only its distinct `AcceptRatedObligationV1` input.
- Emits complete typed accounting and receivable-position payloads through the
  transactional outbox, including applied tax/FX and immutable service-period
  and due-date-basis evidence.
- Ships the `bi_0001_billing` migration with exact tenant RLS and platform
  revokes/grants, including column-only account lock permission, plus live
  concurrency, immutability, isolation and rebuild/hash canaries.
- Declares the exact `dotmac-kernel>=0.1.0a89` and `alembic>=1.13` floors so a
  clean wheel install can import the published migration/linking surface.
- Includes no provider connector, rendering, byte storage, cadence, collections
  policy, product consequence, numbering engine, GL or ERP posting path.
