# Sub billing cutover readiness

As of 2026-10-13, source baseline `e066dd07`. This is an implementation and
evidence checklist, not production activation authority. Michael selected Sub
first (M1; Vendor-first waived), prepaid pay-then-record (M6), and open items plus
opening lanes only (M8). Clean Starter modules run locally inside Sub; the assembly
wires contracts and owns the transaction. Reuse proven Sub/ERP logic and parity
scenarios. Metadata identity, bespoke repairs and drift-reconciliation code are
excluded from extraction. Historical source coordinates remain in the
[package dossier](../../packages/dotmac-billing/EXTRACTION.toml) and
[source variance](billing-source-variance.md).

## Module, contract, retirement and gate matrix

“Open” means the Sub cutover evidence is absent here; an existing module is not
therefore absent or unimplemented. This first foundation slice does not establish
end-to-end adoption or retire any source writer.

| Owner / module | Existing boundary and extraction target | Sub local retirement target | Known gap / next evidence gate |
|---|---|---|---|
| `dotmac-billing` | `AcceptRatedObligationV1`, `AcceptSettlementV1`; immutable documents, allocations, corrections, three per-currency positions, accounting/document facts. Lift qualifying Sub money behavior and ERP coverage logic behind those contracts. | Invoice/credit-note, settlement/allocation/refund/reversal and direct-balance writers listed in the package dossier. | Open: withholding-tax command/snapshot treatment; multi-obligation document support; current document-line `(document_id, obligation_id)` FK binds lines to the document's single obligation (`models.py` tenant and platform tables), requiring an additive migration. Open-item/opening-lane import commands, document-level correction/exception flow and full Sub composition remain unproved. The unreleased 0.1.0a2 foundation implements confirmed-cash-backed available-credit draw and reversed-funding guards. Both-plane PostgreSQL canaries are authored; hosted execution and Sub composition are pending. Non-cash grants and opening credit are not spendable through this command. |
| Prepaid product flow / Subscriptions + Sub assembly | Pay-then-record: confirmed funding → final service period and rated obligation → billing document/allocation → product consequence. Wallet is Billing `available_credit`; renewal draws from it. Advance drafts become notices. | Advance financial drafts, settlement-time period rewrites, metadata-based renewal identity and auto-repair, prepaid reconciliation writers. | Open: complete funding-time orchestration and replay/concurrency/period proofs; cash-backed available-credit draw does not implement non-cash grants, notices, renewal policy or access consequences. Recurrence needs Subscriptions' adopted durable timers before effective occurrences. |
| `dotmac-payments` / Integrator intake | Payment intent, transfer proof and immutable confirmation correlation; assembly translates independently confirmed observations into `AcceptSettlementV1`. PSP credentials, verification, retries and checkpoints remain Integrator concerns. | Provider callbacks and intake paths that directly create money; retain source evidence, remove parallel business writers after accepted cutover. | Existing intent/confirmation API is present. Open: Sub confirmation-to-settlement binding, pending/unverified-proof refusal, duplicate/conflicting confirmation and ambiguous-outcome reconciliation evidence. A proof upload is not confirmed money. |
| `dotmac-tax` / Billing applied snapshots | Tax determination contract and frozen `AppliedTaxSnapshotV1`; reuse qualifying calculation logic, with assembly mapping between owners. ERP retains statutory recognition, books and returns. | Sub invoice-side tax calculation/snapshot decisions only. | Open: accountant VAT ruling M7 for prepaid funding; final line/component snapshot mapping and exact tax parity. Module presence does not resolve policy. |
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
