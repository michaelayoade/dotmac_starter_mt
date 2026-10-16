# Sub billing cutover readiness

As of 2026-10-14, this candidate builds on accepted source
`f75c69e6586732b14a0eab6b55422c71e4ddefdf`, including the Billing a8 source
integrated through PR #826 and the Kernel correction in PR #829.
This is an implementation and
evidence checklist, not production activation authority. Michael selected Sub
first (M1; Vendor-first waived), prepaid pay-then-record (M6), and open items plus
opening lanes only (M8). Clean Starter modules run locally inside Sub; the assembly
wires contracts and owns the transaction. Reuse proven Sub/ERP logic and parity
scenarios. Metadata identity, bespoke repairs and drift-reconciliation code are
excluded from extraction. Historical source coordinates remain in the
[package dossier](../../packages/dotmac-billing/EXTRACTION.toml) and
[source variance](billing-source-variance.md).

## Current opening-control candidate — 2026-10-14

The candidate adds a permanent scope-wide window, actual restricted-principal
binding, immutable admission/seal evidence and database-enforced closure on
both planes. Exact authenticated retries preserve old receipts; new imports
after sealing refuse even for a new account. Full unwind remains a separately
verified linked inverse. V1–V5 accounting facts keep their distinct meaning.
Existing original receipts are not silently bootstrapped into a new window.

The pure rehearsal report consumes actual V2 preview contracts and typed Sub
observations. It checks identities, partitions, documentary/discharge amounts
and residuals separately, exposes blockers and requires three distinct complete
captures. Actual Sub V2 does not supply full original-documentary or all WHT/
non-cash historical evidence: nonempty eligible-invoice runs remain blocked.
No net-balance tolerance, manual hold or admissible preview can waive that gap.
The real source adapter and capture still need evidence. Source observations
qualify no cash.

Independent component static review and pinned checks passed. Fresh full
hosted CI, new source approval, verified publication and Sub adoption remain
required. No production principal, import, seal or authority switch has run.
Earlier dated slice-status sections below retain their historical evidence;
they do not replace this candidate's current head/base acceptance.

## Module, contract, retirement and gate matrix

“Open” means the Sub cutover evidence is absent here; an existing module is not
therefore absent or unimplemented. This first foundation slice does not establish
end-to-end adoption or retire any source writer.

| Owner / module | Existing boundary and extraction target | Sub local retirement target | Known gap / next evidence gate |
|---|---|---|---|
| `dotmac-billing` | `AcceptRatedObligationV1`, `AcceptSettlementV1`; immutable documents, allocations, corrections, three per-currency positions, accounting/document facts. Lift qualifying Sub money behavior and ERP coverage logic behind those contracts. | Invoice/credit-note, settlement/allocation/refund/reversal and direct-balance writers listed in the package dossier. | Candidate: homogeneous multiline documents behind a new typed command and additive `bi_0002_multiline`, with account-affinity line FKs, immutable authoring mode and exact line/tax/FX checks; hosted proofs pending. Unpublished a3 adds invoice-bound WHT acceptance/correction with immutable Tax evidence and distinct V2 accounting facts; [source and limits](billing-withholding-extraction.md). Heterogeneous subject/service/period invoices remain open. Open-item/opening-lane import commands, document-level correction/exception flow and full Sub composition remain unproved. The unreleased 0.1.0a2 foundation implements confirmed-cash-backed available-credit draw and reversed-funding guards. Both-plane PostgreSQL canaries are authored; hosted execution and Sub composition are pending. Non-cash grants and opening credit are not spendable through the cash-backed draw command. A separate source slice now implements ordinary non-cash grant/apply/partial application reversal/unused reduction, immutable classification, V3 accounting facts and a fourth independent V2 position balance; [source and hosted gates](billing-non-cash-credit-extraction.md). Sub UI permission/configuration binding, numbering/presentation, ERP V3 consumption and opening credit remain unproved. |
| Prepaid product flow / Subscriptions + Sub assembly | Pay-then-record: confirmed funding → final service period and rated obligation → billing document/allocation → product consequence. Wallet is Billing `available_credit`; renewal draws from it. Advance drafts become notices. | Advance financial drafts, settlement-time period rewrites, metadata-based renewal identity and auto-repair, prepaid reconciliation writers. | Open: complete funding-time orchestration and replay/concurrency/period proofs; cash-backed available-credit draw does not implement non-cash grants, notices, renewal policy or access consequences. Recurrence needs Subscriptions' adopted durable timers before effective occurrences. |
| `dotmac-payments` / Integrator intake | Payment intent, transfer proof and immutable confirmation correlation; assembly translates independently confirmed observations into `AcceptSettlementV1`. PSP credentials, verification, retries and checkpoints remain Integrator concerns. | Provider callbacks and intake paths that directly create money; retain source evidence, remove parallel business writers after accepted cutover. | Existing intent/confirmation API is present. Open: Sub confirmation-to-settlement binding, pending/unverified-proof refusal, duplicate/conflicting confirmation and ambiguous-outcome reconciliation evidence. A proof upload is not confirmed money. |
| `dotmac-tax` / Billing applied snapshots | Tax determination contract and frozen `AppliedTaxSnapshotV1`; reuse qualifying calculation logic, with assembly mapping between owners. ERP retains statutory recognition, books and returns. | Sub invoice-side tax calculation/snapshot decisions only. | Open: immutable Tax line/component mapping and exact parity, Tax a5 composable versioned rate/basis UI, and trusted production adoption. The settled configuration direction does not impose a blanket pending category/rate ruling. |
| `dotmac-numbering` | Assembly-supplied `NumberingProvider`; series state remains the numbering owner. | `next_invoice_number` and bypass callers. | Existing module/tag history; Open: released-package pin, tenant-lineage installation, settings/series seed, historical-number collision and counter reconciliation, all invoice/credit-note caller retirement, issuance/replay/concurrency and no-number-for-notice evidence. Historical absence of a source test is not proof the shared module is absent. |
| `dotmac-document-rendering` + `dotmac-files` | `InvoiceDocumentFactV1` → assembly reconciler → rendered/stored bytes → `RecordDocumentArtifactV1`; replacements require `RepairDocumentArtifactV1` and semantic-digest match. | Rendering/storage portions of `InvoicePdfExport` flow; official relation moves to Billing. No replacement job-state table. | Existing modules/tag history; Open: Sub reconciler, suppressed-event convergence, official artifact/retrieval and render/storage provenance proofs. Rendering release is no longer described as blocked merely by the August release-lane gap. |
| `dotmac-collections` | Assembly maps Billing exposure/position into narrow collections observations; policy/cases request consequences, service owner revalidates and acts. | Legacy dunning, prepaid/postpaid policy and sweep decisions for each accepted slice. | Open: Sub mapping, unknown due-date/stale/incomplete evidence fail-closed behavior, policy parity and consequence-owner proof. No billing-side access mutation. |
| ERP feed adapter | Immutable `AccountingFactV1`, stable source identity/version; ERP owns mapping, posting, replay and reconciliation. Existing ERP feed adapter can continue through cutover. | Retire any replaced Sub fact producer only after exact adapter parity; ERP retires no GL owner to Billing. | Open: mapping Billing facts to the continuing feed, dedupe/replay and end-to-end receipt/reconciliation proof. A new ERP inbox is not a prerequisite established by this slice. ADR-0020/0024 authority and transport boundaries remain binding. |
| Sub read/product projections | Separate exact `collectible_receivable`, `available_credit`, `prepaid_funding` per currency; product owner consumes financial outcomes for entitlement/service state. | Combined `current_balance`, float money displays, direct financial assignments and unsupported fallback reads. | Open: account/currency mapping, labelled archive navigation, exact display/entitlement parity and accepted intentional display changes. Projections never acquire financial authority. |

## Evidence and cutover gates

1. Complete the per-component lift / lift-behind-contract / leave-behind map
   with source revision, preserved behavioral tests and a named consumer.
   `greenfield-after-inventory` describes the clean shared core; it does not
   authorize a rewrite of qualifying working logic or imports of repair code.
2. Preserve the **no-mutations freeze**: this work does not execute historical
   repair scripts, corrections, backfills or financial writes. Wider Sub script,
   one-off and shadow-command freeze enforcement and Michael's billing review
   control remain tracked debt, not completed controls in this repository.
3. Define and reconcile the migration population: **open items plus opening
   lanes only**, exact per account/currency with source identities. Closed
   history stays labelled and read-only in Sub. Unmappable or provenance-deficient
   open records enter the exceptions queue; do not infer funding or repair them.
4. Execute isolated behavior shadow against immutable captured inputs, with the
   incumbent Sub writer as sole production authority and no dual write. Require
   exact documents, settlements, allocation edges, snapshots, positions and
   accounting-fact comparison, classified differences and three consecutive
   complete reconciliations with zero unclassified drift. Evidence is not yet
   supplied here. Local tests/dependency installation are prohibited; repository
   tests run on Git-hosted CI.
5. Separately establish activation readiness: accepted authority profile and
   coupled watermark, actual runtime role/RLS proofs, restorable backup and
   rehearsal, module/version artifact evidence, continuing ERP feed receipts,
   document convergence and per-slice writer-retirement ratchets. Activation,
   deployment, financial migration and legacy deletion remain separate authorized
   operations. No N2 production permission or authority transfer is inferred
   from implementation direction.

## Existing immutable publication coordinates

The checked-in [legacy publication register](module-release-legacy-unverified.json)
contains `dotmac-billing-v0.1.0a1`, tag object
`3dbb4e171903d954d58cbc90a28069d976e9df1e`, peeled commit
`92a1626b16d7e068f92536d8cfcb2ef9b6f270c2`; Collections a1, Payments a1,
Numbering a2 and Document Rendering a1 also have entries there. The August 24
Knowledge record `dotmac-four-commerce-owners-published-2026-08-24` reports the
four-owner composition on kernel a94; its rendering-blocked statement is
historical and superseded by the later rendering tag entry. The newer
[module verification register](module-release-verifications.json) contains no
Billing receipt at this baseline. These are existing historical coordinates,
not pinnable artifact evidence, a newly verified wheel receipt, current registry
lookup, successor release
identity or proof of Sub adoption. No release record is fabricated by this work.

## First implementation slice

Development successor `dotmac-billing 0.1.0a2` adds
`ApplyAvailableCreditCommand` / `apply_available_credit`. It extracts account
serialization, cash-source room limits and the deterministic minimum-allocation
loop from `dotmac_sub:app/services/billing/account_credit.py` at inspected local
source revision `c7707880232c9298ef89c24a1aea66d35bd3a1e6`. It uses immutable
posting effects and per-settlement allocation edges, rather than porting Sub's
mutable ledger, status checks or repair paths. Existing single-settlement
allocation shares the funding-reversal and target guards.

Independent source review found no remaining actionable issue after correcting
the reversal and concurrency canary setup. Pinned Ruff 0.6.9 lint/format and
syntax/manifest parsing passed; these are static evidence only. PostgreSQL
execution remains a Git-hosted CI gate. The successor is declared unpublished;
no artifact, adoption, financial migration or production activation is claimed.

## Multiline follow-on candidate

[Multiline extraction](billing-multiline-extraction.md) records the second
bounded slice and its file-by-file source disposition. The source rules come
from Sub invoice draft authoring and construction owners; metadata identities,
repair logic and fixed two-decimal arithmetic are excluded. The new command
requires homogeneous collection evidence and exact explicit prices. Storage
retains per-line obligation/tax provenance; V1 still carries a flat tax list and
one common FX value. Mixed service/period grouping and line-specific wire
attribution remain open.

Independent review required immutable authoring provenance and per-obligation
tax reconciliation. Extra document-owned tax is refused for new explicit
invoices, so it cannot bypass the line evidence. Fresh and predecessor migration
canaries are authored; their hosted execution remains required. No Sub source
writer, production schema or financial record has changed.

## Ordered continuation

The execution order below separates implementation evidence from activation.
Completed source slices do not retire Sub's incumbent financial writer.

| Slice | Required result before advancing |
|---|---|
| Cash-backed credit draw | PR #802 at `d6d3981b97db84bb54f31467f97014266bff0c02` passed all 36 hosted checks. Review, merge and successor publication remain separate steps. |
| Homogeneous multiline invoices | PR #803 includes the additive migration and exact evidence guards. PostgreSQL integration and security passed `a284f98c250c41129c12026126554b5ed0053f6a`; its precise SQLAlchemy alias-type fix at `31d3e7c944df07f99ecc9b89231fca5251fbdc8b` requires fresh complete CI acceptance. |
| Confirmed-payment safety | The Payments successor must reject conflicting confirmation evidence and serialize competing intent transitions. Prove exact retry, competing confirmations, cancellation/expiry and transfer-proof review on migrated PostgreSQL before an adopter turns a confirmation into Billing money. |
| Sub numbering composition | Pin released Numbering `0.1.0a2`, compose its tenant lineage and prove isolation, preview, retry and rollback against Sub's migrated schema. Its peeled publication coordinate is `4c282228b53405442effc4159f5db9d50446d335`; registry verification run `31908774569` does not replace adopter-database proof. |
| Numbering switch preparation | Derive exact settings-to-series mapping and business-date/command identities from incumbent callers. Produce read-only counter/collision evidence, seed and inverse plans, and complete writer retirement evidence before enabling allocation. |
| Intake and prepaid orchestration | Bind verified actual cash to Billing settlements, then exercise funding-time obligation, final period, invoice, allocation and service consequence with exact retry and concurrency proofs. No gross-payment substitution for WHT deductions. |
| Remaining consumers and migration | Complete Tax, rendering/artifact convergence, Collections, existing ERP-feed mapping and Sub read projections; rehearse open items/opening lanes plus exceptions, closed-history archive and the coupled ledger switch. |

WHT records distinguish claimed deductions, accepted receivable discharge and
tax-credit evidence. The a3 source implements the separate discharge/correction
owner; Tax determines exact amounts from versioned policy. Immutable mapping,
parity, composable rate/basis UI and trusted adopter finance authorization still
need their own evidence. A deduction never becomes spendable cash credit.
Non-cash grants, opening recognition and statutory accounting remain separate
product/adoption work.


## Current hosted predecessor evidence

PR #808 at `fd1f2feed85dbd5fb3fde2786410e6f8838f6ce0` passed all 41 hosted
checks against base `f483` (full coordinates retained by the coordinator's
release/readiness register). Accounting PR #809 at `523b` also passed all 41
checks. Sub Tax PR #3590 at `a5f` passed 31 checks with three mobile skips.
Earlier pending statements above are historical slice status. They do not make
this unpublished a3 successor green: its exact head/base hosted acceptance is
still required. No complete cutover, policy UI or production adoption is claimed.
