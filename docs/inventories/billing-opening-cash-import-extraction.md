# Cash-only opening import and complete correction lifecycle

Source work starts at accepted Starter
`053d6dd13a98d0fe94119a29e9d2513e108a3821`, after accepted
`bi_0006_cash_source_claim`. This is source implementation, not production
import, activation, release acceptance or authority transfer.

## Qualified source inventory

| Source | Disposition |
|---|---|
| Accepted Billing immutable opening manifest/preflight and exact source claims | Reuse complete population evidence, integer controls, canonical provenance fingerprint and permanently unique live cash-source tuple. Preview attestation remains preview-only. |
| Accepted Billing `apply_available_credit`, `_post_group`, allocation and inverse helpers | Reuse account-first serialization, deterministic exact room draw, immutable source effects, position/exposure snapshots and Kernel-owned idempotent conflict savepoint. Preserve settlement-only V1–V3 contracts and retry bytes. |
| Sub `8ad84a677eb23b62c5d7b832df5c3954d8b89ac5`, `account_credit.py`, `payments.py`, `PaymentSettlement`, provider-event consequences | Scenario/algorithm sources for per-source room, exact historical consumption, refund and inverse provenance. Metadata identities, repair paths, ambiguous proportional attribution and status-only cash proof are excluded. |
| Canonical `billing-opening-cash-source-evidence-20261013` v2 | Trusted ingestion/source qualification must distinguish source gross/fee/net paths and bind actual source identity, confirmation and complete consumption. No new blanket bank-payout condition. |

The immutable import/lot/effect persistence, operation-qualified trusted
assembly attestation, distinct AccountingFactV4 and coupled mixed-source
inverse are explicit PORT-DELTAs. They close gaps absent from the product
source without claiming a nonexistent reusable importer. The maintained
Billing audit remains the product inventory owner.

## Bounded contract

Only a cash-only positive selected partition in a complete supported manifest
can enter a financially pristine account/currency. Verification must qualify
`billing.opening.import.v1` for the full command, actual installation/source
namespace, cash proof, migration authority and expected target fingerprint plus
posting watermark. Preview authority cannot grant import authority. Fresh
verification precedes account lock and Kernel idempotency; exact replay then
resolves before mutable pristine/room/watermark guards.

Imported residuals become distinct real opening lots, never synthetic
settlements, invoices, new revenue or unexplained balance plugs. Claims retain
one exact XOR owner: an actual settlement or an actual opening lot. Claims stay
occupied after full unwind. No partial principal amendment or automatic
reimport is supported. Exact integer conversion and aggregate bounds fit
Numeric(20,6), precision zero through six, independently of ambient Decimal
context; pure preview's zero-through-eighteen contract stays unchanged.

Ordinary cash draw can consume opening lots and settlements deterministically.
Its V4 fact retains actual allocation/effect identities. `cash_source_order`
covers every concrete source effect in every V4 operation, including recognition,
refund, correction and unwind, with exact one-to-one multiplicity. Only permanent
claim acquisition is sorted by natural tuple to avoid cross-account key-order
deadlocks; manifest, lot and financial source ordering is preserved. The
mandatory-amount `reverse_cash_credit_draw` atomically reverses both sources
against concrete originals and cumulative bounds. Generic reversal and legacy
half-draw deallocation/reallocation cannot bypass this owner.

Unused-lot refund requires independently qualified outcome evidence. The
bounded correction restores the full named refund only once and requires new
independent correction/outcome proof. Application inverses restore credit and
AR without claiming external cash. Import unwind requires fresh distinct
migration authority, all net dependent applications/refunds zero, and appends
linked receipt/effects/facts without releasing claims or restoring cash.

Mirrored SQL guards enforce exact scoped affinity, immutable evidence, natural
claims, source room, per-original inverse caps, live invoice room, exact posting
and V4 completeness. The additive migration replaces relevant bi0005 invoice
room/void definitions to include opening coverage without rewriting historical
rows or accepted migration bytes. Services remain caller-transaction flush-only.

Hosted canaries must prove both planes, fresh and genuine predecessor upgrade,
immutable/scoped SQL constraints, exact retry, stale target and authority
refusal, claim races, mixed use/inverse/refund/correction/unwind, and rollback
faults across claims/lots/postings/snapshots/facts/outbox. Static results are not
runtime or production proof.
