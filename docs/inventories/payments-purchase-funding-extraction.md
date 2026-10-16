# Purchase funding intent prerequisite

Audit date: 2026-10-16. Starter baseline `49f35ca6`; qualifying Sub revision
`4e2e296f1be63c953cd9b4c7417bacc34e019d9c` (existing extraction baseline).
Explicit `project:dotmac_starter_mt` knowledge search and canonical
`dotmac-billing-cutover-orchestration-2026-10-14` v116 retrieval succeeded
without a scope denial. The canonical source records pay-then-record prepaid
purchase behavior: checkout precedes an invoice. This corrects the existing
Payments owner contract; it creates no new module or financial algorithm.

## Qualifying product inventory

| Existing source | Extracted requirement |
| --- | --- |
| Sub `app/services/topup_intents.py:GatewayTopupIntentFlow` | `prepaid_period_purchase` is an existing checkout flow, distinct from `invoice_payment`; it is not greenfield shared behavior. |
| Sub `app/services/topup_intents.py:_gateway_intent_metadata` and `stage_gateway_topup_intent` | A purchase checkout requires account/purchase identities before provider I/O and rejects reference replay with a changed purchase binding. Shared Payments retains only opaque payer and destination references. |
| Sub `app/services/gateway_topup_intents.py` purchase staging | Uses the reviewed purchase total and purchase ID to open checkout before issuing invoices. Quote review, product authorization, pricing, tax, purchase status and expiry policy remain Sub-owned. |
| Starter `OpenPaymentIntent`, `get_payment_intent_v1`, `record_confirmation`, `get_confirmed_cash_v1` | Existing exact replay and immutable-confirmation/query contracts already cover the lifecycle. Only the purpose vocabulary and its required destination were missing. |

## Shared contract and migration

`PaymentPurpose.PURCHASE_FUNDING` requires a nonblank opaque `target_reference`
for the purchase or obligation. Its exact bytes are retained and compared;
whitespace is not normalized into a different destination. Existing replay
checks retain payer, purpose, provider/channel, exact money/scale and original
opening/expiry. It is neither invoice settlement nor an account-credit deposit.
No product enum/type/import enters the shared owner.

The existing typed opening/read/confirmation/cash APIs are reused without new
command fields. Checkout purpose grants no cash qualification, spendable credit,
allocation, invoice issuance or ledger posting. Assembly retains structural
purchase/account binding and verified settlement admission.

Published `pm_0001` restricts purpose to three values; changing the Python enum
alone is insufficient. Successor `pm_0004_purchase_funding` extends that named
CHECK, requires a destination for the new purpose, and installs an AFTER UPDATE/DELETE
refusal for deleting or moving the binding when either tuple is purchase
funding. The original ID, tenant, natural reference, payer, destination, purpose,
requested money/code/scale, provider/channel and opening/expiry are immutable;
only the existing lifecycle fields remain writable. PostgreSQL rejects
whitespace-only destinations, including tabs/newlines.
Existing `pm_0002`/`pm_0003` guard confirmations, not intent bindings;
this is the narrow raw-DML hole being closed. Lifecycle and no-op destination
updates still work. No existing row is rewritten; RLS, policies, grants and
confirmation guards remain intact. DDL takes a bounded exclusive intent-table
lock. Downgrade checks all tenants with RLS disabled and refuses while any
purchase funding intent exists; it does not delete or reclassify evidence.
Payments is tenant-only. PlatformScope is refused; no platform plane is invented.

## Validation and release boundary

Authored hosted canaries cover genuine fresh migration and populated pm3
predecessor (no stamp/metadata substitute), exact legacy row and RLS/ACL/policy/
confirmation-trigger preservation, empty-funding downgrade/reupgrade, purchase
open/query/confirm/cash query/replay, cross-purpose and exact-target refusal,
owner/app-user raw-DML binding mutation refusal, pending/confirmed reference-move
and original-reference reopen refusal, later BEFORE-trigger final-tuple proof,
cross-tenant read/update/insert negatives and funding-present downgrade refusal. Unit canaries cover missing/
blank destination, exact bytes and PlatformScope refusal.

Local validation is stdlib AST parsing and pinned Ruff only; runtime is pending
hosted CI. No local tests, application imports, database, Docker or installation
are authorized. `0.1.0a2` is already published and its migrations/release receipt
remain unchanged. Metadata proposes successor `0.1.0a3` as unpublished source;
this does not allocate, approve, build, tag, publish or adopt that identity.
Release-catalog allocation/authorization remains the release owner's decision.
Sub remains the live writer until separately authorized adoption and cutover.

## Merchant context and cash identity gap (2026-10-16)

The purchase caller audit identified a second prerequisite in the same existing
Payments owner. Local confirmation IDs cannot identify the economic provider
transaction for both opening import and live acceptance. The following source
inventory precedes implementation; none of these new contracts is published or
adopted by Sub.

| Existing source | Retain or extract | Leave outside this module |
| --- | --- | --- |
| Sub `provider_payment_settlements.ModuleInvoiceCashSource` | Exact frozen intent, payer, provider, transaction and Money comparisons; generalize the target boundary to purchases | Product account/invoice/purchase eligibility and local model FKs |
| Sub `payment_gateway_finance.bind_integrator_installation` | Explicit reviewed configuration authority and exact provenance binding | Local installation/configuration FKs, authenticated Finance permission checks and UI |
| Sub `payment_provider_events._normalize` | Normalized original transaction facts and exact replay conflicts | Provider wire decoding and transport authentication |
| Sub `integrations.payment_capture` | Original connector/configuration provenance and separately observed capture facts | Caller-asserted merchant namespace as qualification; current configuration as original proof |
| Starter Payments intent/confirmation owners | Frozen intent correlation and immutable accepted external facts | Confirmation UUID or a constant version as economic cash identity |

The genuinely new portable rule is independently reviewed merchant-context
authority. An immutable accepted context binds provider/environment and physical
merchant identity to opaque original transport/configuration references and
review evidence. Sub supplies server-authenticated review authority; the shared
command validates its exact tenant, operation, request fingerprint and reviewer
binding. A request flag or reviewer string cannot authorize acceptance. This is
human-reviewed configuration authority, not automatic proof that an artifact or
provider response is authentic.

Append-only termination/supersession closes new admissions without rewriting
original review facts. A historical context query returns evidence only and
cannot itself authorize new cash. New mutations require READ WRITE READ
COMMITTED and serialized scoped admission; historical reads retain their
original facts. No new permission grant, provider credential or transport
implementation belongs in these shared records.

Sub remains the thin product adapter. Payments must ultimately own the shared
merchant-qualified namespace/transaction/economic-version codec and successful
cash qualification against the original frozen intent. Those qualification
contracts are a following source slice, not supplied by the context records
alone. Integration supplies actual retained authenticated transport provenance
through its versioned delivery boundary, never a foreign platform-table query.
Paystack capture time must come from explicit `paid_at`; unsupported Flutterwave
merchant/capture semantics remain unresolved. No current, received, retrieved or
creation timestamp substitutes for missing capture evidence.
