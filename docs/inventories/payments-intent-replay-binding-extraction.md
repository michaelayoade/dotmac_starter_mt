# Payment intent replay binding prerequisite

Audit date: 2026-10-15. Shared source baseline:
`008ccaeaebda6855ba34b8e70740e3859954a37b`; qualifying Sub source:
`4e2e296f1be63c953cd9b4c7417bacc34e019d9c`.

This bounded correction belongs to the existing Payments intent owner and its
unreleased `0.1.0a2` source. It adds no owner, transport, schema, dependency,
release identity, publication or product writer switch. The canonical cutover
record is `sub-first-billing-credit-draw-foundation-20261013`, retrieved after
an explicit `project:dotmac_starter_mt` knowledge search with no scope denial.

## Product inventory before implementation

| Existing source | Contract and evidence retained |
| --- | --- |
| Sub `app/services/customer_portal_flow_payments.py:create_invoice_payment_intent` | Authorizes access to the invoice, obtains its server-owned amount, selects provider, and creates a typed gateway command before provider initialization. `tests/test_customer_portal_topup_flow.py:test_create_invoice_payment_intent_saved_card_idempotent_replay` asserts one charge and the original reference on retry. |
| Sub `app/services/topup_intents.py:stage_gateway_topup_intent` | Locks the account and rejects reused gateway reference with changed account, billing account, invoice, purchase, provider identity, capability binding, currency, amount or metadata. Product policy and IDs stay in the caller; shared Payments accepts opaque payer/target references. |
| Sub `app/services/gateway_topup_intents.py` | Requires enabled checkout capability and validates invoice/purchase bindings. Existing-purchase replay checks account/provider/capability/payment method and preview fingerprint. `tests/test_gateway_topup_intents.py` covers deposit replay, reviewed-preview refusal, typed purchase linking and missing checkout binding refusal. |
| Sub `app/services/account_credit_deposits.py:stage_intent` | Rejects changed amount, currency, provider or capability binding on deposit replay. Allocation policy, reviewed preview and deposit eligibility remain product-owned. |
| Starter `packages/dotmac-payments/src/dotmac_payments/contracts.py:OpenPaymentIntent`, `models.py:PaymentIntent`, `service.py:open_payment_intent` | The typed opening request persists destination, purpose, normalized provider/channel, exact money, opening and optional expiry. Previously replay compared only money, payer and explicit opening time. Changing destination could silently return an intent bound to another receivable. Confirmation provider derives from the stored intent; provider/channel have no mutation command. |

The existing `packages/dotmac-payments/EXTRACTION.toml` inventories ERP's
payment-intent model/service and Sub's original qualifying source. This change
ports the current Sub reference-binding refusal through the established typed
Payments seam; it does not extract receivables, gateway capability selection,
invoice eligibility, saved-card charging, allocation or provider metadata.

## Bound replay request

Within one `TenantScope`, the stripped reference identifies the intent. Replay
must retain stripped payer, exact opaque target (including absence), purpose,
stripped uppercase provider/channel, amount, currency code and currency scale,
and optional expiry. Different tenant scopes retain independent reference
namespaces. Payments remains tenant-only and rejects `PlatformScope`.

An omitted `opened_at` retains the stored opening instant, as already promised
by the shared contract. A supplied opening instant must match. Timestamps compare
as UTC instants, including naive UTC returned by the existing SQLite unit
adapter; equal instants in different offsets are equal. Replay does not change
terminal status or revalidate expiry against the current wall clock.

Expiry absence means no expiry in `OpenPaymentIntent`; changing `None` to an
instant, an instant to `None`, or the instant itself conflicts. This is a
deliberate strengthening at the shared typed boundary: Sub's gateway staging
helper currently derives `now + configured TTL` on every call and excludes
expiry/channel from its replay comparison. A migrating caller must freeze its
requested expiry with the original request (or consistently request no expiry),
not recalculate TTL on retries. This correction does not claim Sub's omitted
expiry behavior as parity. Only `opened_at` has the documented default-now replay
exception; no provider/capability re-selection is inferred by Payments.

## Validation and cutover boundary

Canaries are authored first in existing `tests/unit/test_payments_service.py`
and `tests/test_payments_isolation.py`: exact/normalized replay, every material
binding mismatch, absent destination/expiry transitions, persisted timestamp
reload, terminal replay, tenant-separated references, platform refusal and
caller-owned rollback. PostgreSQL uses the existing migrated RLS fixture.

Only AST parsing and pinned Ruff 0.6.9 static checks are permitted locally.
These are not runtime evidence. Hosted CI must execute the focused unit and
PostgreSQL canaries and repository-required checks before acceptance. Sub stays
the live writer pending verified package adoption, historical evidence/shadow
and separately authorized cutover; this prerequisite authorizes none of them.
