# Ordinary non-cash credit successor

This source slice adds Billing-owned commercial grants, applications, partial
application reversals and unused grant reductions on both declared planes. It
is stacked on WHT source `93a6a320`. No runtime tests were run locally; hosted
acceptance, package publication, adopter composition and production cutover
remain separate gates.

## Product-first extraction

Qualifying source was inspected at Sub
`3ff8ab17ee64954c973568e930d8cfbf5c97df5f` in
`dotmac_sub-billing-erp-facts`.

| Source | Disposition |
|---|---|
| `app/services/billing/credit_notes.py:481`, `_decide_issue_application` | Port paid/unlinked retention and optional application capped by live invoice AR. An absent target never searches for another invoice. |
| `app/services/billing/credit_notes.py:383`, `_build_application_preview` | Port account/currency/invoice restriction, omitted amount choosing minimum grant availability and outstanding, and explicit excess refusal. |
| `app/services/billing/credit_notes.py:842`, `_funded_credit_available` | Port source-specific instrument room independent of unrelated account-credit drift. The new lane never contributes to cash-backed available-credit draw. |
| `app/services/billing/credit_notes.py:2496`, `reverse_application_with_evidence` | Port immutable application provenance, account-first locking, exact retry and linked correction. PORT-DELTA: partial reversals and unused-only reductions append typed effects. |
| `tests/test_credit_note_apply_on_issue.py`, `tests/test_credit_notes.py` | Scenario sources for retention, capped application, provenance/replay, isolation and reversal. |
| `tests/test_credit_notes.py:616`, inclusive-tax ordinary credit fixture | EXCLUDED: ordinary credits obey subtotal=total and tax_total=0. Statutory tax adjustment is a separate owner consequence. |

## Stored meaning and correction

`GrantNonCashCredit` accepts exact Money, immutable category and configuration
owner/reference/version/digest plus accepted typed policy parameters. Actor and
authorization references retain audit evidence; they do not prove a caller's
finance permission. The adopting assembly must bind actual authorization and
its accepted configuration owner. No numeric rate default or Tax determination
is required for an ordinary commercial grant.

A grant is the immutable instrument rather than an `IssueCreditNote` document:
the existing credit-note document path immediately reduces invoice AR by its
whole total and cannot also represent a partially consumed grant. The grant
contract exposes subtotal=total=original amount and zero tax. Numbering and
presentation remain subsequent composition work.

Grant availability is the sum of its own append-only effects. Grant adds value
without changing AR; application decreases availability and AR equally;
application reversal restores both by the correction amount; unused reduction
decreases only availability. Reversals cannot exceed their specific original
application's remaining amount. Fully cancelling a consumed grant requires
reversing its applications and then reducing its unused value. Corrections
cannot themselves be reversed. A mistaken unused reduction can receive a new
separately authorized replacement grant linked to that exact reduction and its
original grant, preserving account/currency/precision/invoice restriction;
cumulative replacements cannot exceed the reduction.

Each command has a stable natural source owner/reference independent of retry
key and source version. An exact immutable replay resolves before mutable room
or lifecycle admission. Reusing the identity with changed evidence conflicts.
Account, grant and application/invoice locks serialize consumption and
correction. Services flush only; the caller owns commit and full rollback.

## Independent balances and evidence

Additive `bi_0005_non_cash_credit` introduces tenant/platform grant and effect
pairs and separate V2 position snapshots. Noncash never creates a Payment,
Settlement or AllocationEffect and never enters the existing available-credit
or prepaid-funding lane. V1 engine, historical rows, contracts, fingerprints and
payload algorithms remain unchanged. Noncash groups publish `AccountingFactV3`;
WHT retains `AccountingFactV2`, and ordinary existing groups retain V1.

Every new group, including later cash and WHT, publishes a fresh
`ReceivablePositionV2` with the original three balances and separate
`available_non_cash_credit`. V2 hashes bind the original V1 fingerprint and
ordered immutable noncash effect identities/groups/deltas. There is no historical
backfill. Original grant/effect queries read scalar persisted JSON without ORM
identity-map substitution or autoflush and verify the original digest before
typed decoding and envelope validation. Every grant/effect consumed by room
admission is validated. Exact V2 queries verify the stored fingerprint and its
V1 source envelope.

Invoice outstanding and invoice exposure include active applications and WHT.
Generic group reversal cannot correct noncash groups, and invoice issuance
reversal/void refuses active applications. A reversed issuance cannot receive a
new grant application. The inherited WHT live-issuance structural relation is
shared with this admission; metadata labels alone do not prove issuance.

Hosted canary source covers both planes, retained paid/unlinked value,
50-grant/30-AR capped application, source replay after consumption, source
conflicts, partial correction and replacement caps, application/correction races,
tenant isolation, caller rollback, dirty ORM queries, fresh V2 after cash, and
generic owner bypasses. These are authored checks, not local execution evidence.
The SQL canaries separately exercise append-only, scoped relations, exact money,
cumulative caps, old-writer snapshot refusal and downgrade behavior.

The source test allowing a valid funded credit despite an unrelated **negative**
legacy shared pool is a migration exception rather than a clean-ledger parity
claim. This slice preserves the existing V1 contract's nonnegative original
balances, so a historical negative cash/prepaid/AR lane still refuses new posting
at V1 snapshot construction. It never clamps that value or skips V1 emission.
Canaries prove source-specific room with zero cash and an unrelated exhausted
grant. M8 import must quarantine provenance-deficient or invalid negative opening
lanes before activation; importing such a legacy pool is not implemented here.
