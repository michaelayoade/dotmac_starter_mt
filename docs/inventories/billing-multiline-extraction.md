# Multiline Billing extraction candidate

This slice extends the unpublished `dotmac-billing 0.1.0a2` foundation. It is
source preparation for the Sub-first cutover, not adoption or activation.
Sub retains its production writers. The inspected source is `dotmac_sub`
`c7707880232c9298ef89c24a1aea66d35bd3a1e6`; the clean Billing core is retained.

## Source disposition

| Source file / behavior | Disposition in this slice |
|---|---|
| `app/services/invoice_draft_authoring.py`: complete line validation, account affinity, account lock, exact replay and atomic aggregate staging | Lift rules behind `CreateMultiLineDraftDocument`; use structural Billing obligation IDs and the existing kernel transaction/idempotency owner. |
| `app/services/billing/invoices.py`: complete draft lines, exact source-content conflicts and frozen tax evidence | Lift guards and scenarios; retain immutable accepted obligations as line evidence. Product models and metadata identities stay behind. |
| `app/services/billing/_common.py`: quantity × price validation | Lift exact-product rule using kernel Money and explicit unit prices; exclude the fixed two-decimal rounding implementation. |
| `tests/test_invoice_draft_authoring.py` and `tests/services/billing/test_invoice_construction_owner.py` | Preserve replay, account linkage, atomic rollback and no financial posting while drafting as new shared-module canaries. |
| `tests/test_billing_services.py`: quantity × unit-price example | Preserve the scenario with exact rated totals and explicit quantity/unit-price inputs. |
| Metadata identity, mutable total reconciliation, bespoke historical repair and Sub tax calculation | Leave behind; no imports or duplicate owners. |

## Command and evidence boundary

`DraftDocumentLine` supplies an obligation ID, description, quantity, unit code
and explicit Money unit price. `CreateMultiLineDraftDocument` supplies an
account, idempotency key, ordered tuple of at least two lines and the existing
document presentation snapshots. Services mutate and flush the caller's session.

The account lock serializes overlapping commands. Exact stored replay precedes
mutable eligibility checks. Each obligation is accepted at most once into a
document line, and already documented or superseded obligations are refused.
Prices and quantities must reproduce the rated pre-tax amounts exactly, with no
guessed division or rounding. Header amounts are sums of authoritative lines.

Grouping requires one account, currency/precision, subject, service, collection
timing and identical service-period status/start/end. The first line is the
header anchor; its exposure describes the entire document truthfully because
every line has the same collection evidence. Heterogeneous grouping is an open
gap, requiring an explicit exposure/allocation design rather than choosing an
arbitrary line's service period.

Per-obligation tax snapshots must have matching Money identity and exact amounts
whose tax sum equals the rated tax. Billing does not determine tax bases or
recalculate tax policy. FX must be absent on every obligation or field-identical
on every obligation. Facts include all source tax snapshots and one common FX
snapshot. The existing V1 wire format remains unchanged: line-specific tax/FX
association is structural in storage, not transmitted by V1. A consumer needing
that association needs a successor contract.

Issuance revalidates the line graph, totals, tax and FX. Immutable document
authoring mode distinguishes new explicit-price drafts from historical
single-line behavior; deleting a line cannot select a legacy validation path.
The existing single-line command, key and fingerprint remain unchanged.

## Additive schema and recovery

`bi_0002_multiline` succeeds the unchanged released `bi_0001_billing` lineage.
Each line gains its parent document's billing account, with composite foreign
keys to both document and obligation. Existing obligation uniqueness remains.
The document gains immutable authoring provenance. Historical monetary fields,
anchors, documents, postings and facts are not corrected or rehashed.

The migration locks affected tables and removes only the existing line-freeze
trigger while filling the new structural account column, then restores that
trigger. Selected tenant/platform planes retain their isolation and grants.
The original invoice-only insertion guard is broadened by the additive
`bi_0003_issued_line_freeze`: every issued kind, including credit notes and
receipts, refuses new lines. Both guards retain their parent `FOR UPDATE` lock,
serializing line insertion against issuance. The existing UPDATE/DELETE guards
also gain parent locks; reparenting checks both old and new parents in ID order
so draft edits, deletion or reparenting cannot change an issued line set. The
migration drains predecessor writers under parent-then-child table locks with
a five-second acquisition timeout before replacing any function.
Credit-note issuance creates a
draft parent, flushes its initial line, then sets the number, issued lifecycle
and issuance time before posting and freezing the document fact. All stages
remain in the caller's transaction. Neither earlier migration changes.
Exact credit-note replay precedes the mutable outstanding-receivable admission
check, so a fully credited invoice returns its original credit without posting
again. The existing key, fingerprint and immutable command validation remain.

Downgrade is refused after explicit multiline authoring. Compatible readers and
a forward fix are required after that point. Migration execution, deployment,
publication and production authority transfer remain separately governed.

## Required evidence

Hosted CI must execute the new both-plane PostgreSQL canaries for exact totals,
all-line snapshots, deterministic facts, replay after issuance, conflicts,
wrong account/tenant/currency/precision, invalid quantities, changed grouping,
tax/FX mismatch, immutable mode, concurrent overlapping obligations and issued
line insertion refusal. Fresh migration and real `bi_0001` predecessor-to-head
rehearsals prove different properties; both are required. Existing cash-credit,
credit-note, allocation, cancellation and exposure canaries remain required.
The issued-line canaries also exercise the real `bi_0002` predecessor-to-`bi_0003`
upgrade without changing monetary/document/fact rows, a safe migration timeout
behind an in-flight predecessor writer, both lock-race orders for insert,
update, delete and reparenting,
owner-role raw SQL refusal, malformed issued parents with no initial lines,
full-credit exact replay and Billing-only caller rollback. The rollback canary
uses a stateless Numbering test adapter and claims no Numbering counter or
cross-module rollback proof. These run on Git-hosted CI only.

Tests are authored in this candidate. Static checks and read-only review do not
prove runtime acceptance; no production or adoption claim follows from them.

## Remaining cutover gaps

Mixed-service/period invoices and line-specific wire attribution remain open.
Prepaid funding-time orchestration, open-item/opening-lane migration and Sub
composition remain separate work. Withholding is a statutory non-cash deduction
in Sub's source flows, not cash-backed available credit. Its applicability,
determination, recognition and evidence treatment must be approved before a
successor settlement contract can move those consequences into the module.
