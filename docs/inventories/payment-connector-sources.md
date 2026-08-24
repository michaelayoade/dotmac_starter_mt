# Payment connector sources — where transport ends and meaning begins

## Correction recorded 2026-08-20

The inventory's finding that Sub hardcodes `Decimal(100)` is accurate, but its
generic conclusion was too broad for Paystack. Paystack's published wire
contract represents every supported currency as an integer multiplied by 100,
including XOF. The Paystack adapter therefore owns a fixed ×100 conversion as
provider protocol; a generic payment engine must not. This does not permit the
separate `default_currency` fallback found below: every emitted money value
still requires an authenticated-event currency and refuses a missing value.

The product-first Paystack package and its current exact source revisions are
recorded in `packages/dotmac-connector-paystack/EXTRACTION.toml`; the historical
measurements below remain unchanged.

**As of:** 2026-08-14
**starter:** working tree on `docs/whatsapp-connector-extraction-dossier`
(`b55c9a5`), carrying the uncommitted ADR-0020 2026-08-14 amendment
**Sub:** `/Users/michaelayoade/Downloads/management/dotmac_sub`, HEAD `27c76aae`
**ERP / vendor CP / CRM:** HEAD at time of audit
**Decision:** ADR-0024 §§ 6–7, ADR-0020 amendment A3
**Companion documents:**
`docs/inventories/payment-connector-extraction-dossier.md`,
`docs/superpowers/specs/2026-08-14-payment-connector-and-settlement-contracts.md`
**Adjacent evidence:** `docs/inventories/external-connector-sources.md`,
`docs/inventories/integration-platform-sources.md`,
`docs/inventories/whatsapp-connector-sources.md`,
`docs/inventories/billing-sources.md`

Read under the two standing cautions in [`README.md`](README.md): facts go
stale, and **a row here is not permission to extract anything**. Every path and
LOC below was verified with `ls` / `wc -l` during this audit. Where an existing
inventory cites a path that turned out to be wrong, § 8 records the correction.

---

## 0. The one question this document answers

ADR-0020 A3 split payments in two: billing owns the money decisions, an
Integrator connector plugin owns the transport. That line does not exist in the
fleet today. This document finds where it **would** fall in the code that
exists, because the places the line cuts through the middle of a function are
the places extraction is expensive and dangerous.

**The short answer.** Sub's payment path is four layers deep and the boundary is
drawn twice, neither cleanly:

```text
app/api/billing.py:1393                    route          TRANSPORT  (clean)
  api_billing_webhooks.py                  adapter        TRANSPORT  (clean, one exception)
    payment_capability.verify_webhook_signature           TRANSPORT  (clean)
    integration_inbox.receive_and_claim_verified          TRANSPORT  (clean)
    payment_webhook_commands.py            normalise      ── FIRST CUT ──
      _settlement_observation      :189    provider string -> PaymentStatus
      _prepare_payment_webhook     :343    money admissibility
      _prepare_payment_webhook     :361    invoice targeting from PSP metadata
      _prepare_payment_webhook     :371    net_amount = amount - provider_fee
    payment_provider_events.py             consequences   ── SECOND CUT ──
      _stage_financial_consequences :599-973   allocation, coverage, refund,
                                               reversal, posting groups
```

Everything above the first cut is transport and ports to a connector plugin
almost unchanged. Everything below the second cut is money meaning and is
`dotmac-billing`'s under ADR-0020 § 1. The 400 lines between the two cuts are
the extraction's real work.

**ERP draws the same line at a different depth and reaches the same
arithmetic.** `webhook_service.process_webhook` (`:45`) is transport;
`payment_service.process_successful_payment` (`:401`) is meaning; and at `:520`
it computes `amount=min(intent.amount, invoice.balance_due)` — which is, to the
token, what Sub computes at `payment_provider_events.py:748`. **Two products,
two independent Paystack integrations, one identical coverage decision, written
twice.** That is the strongest single piece of evidence that this capability has
one owner and it is not either product.

---

## 1. Which providers are actually in production use

> **Wording corrected 2026-08-24 (ADR-0061 Amendment A7).** This section's
> heading and its "live" column say more than the evidence supports for **ERP
> payout**. "Live" here means *the code exists, is wired to a route and has
> tests* — it is a repository-local reading, not a deployment observation.
> ERP's transfer path is gated by `paystack_transfers_enabled`, a
> `domain_settings` ROW with `default=False` seeded once from the environment,
> and this repository holds no evidence of its value anywhere
> (`docs/inventories/treasury-payment-execution-sources.md` §§ 5, 12.4, 14).
> The required
> claim for ERP payout, verbatim, is **"Implemented and tested; production
> enablement unconfirmed."** Under `AGENTS.md` rule 30, confirming it needs an
> explicitly named deployment target and a `deployment_run` oracle, and no
> target has been named — so the absence is an as-of-2026-08-24 observation in
> both directions, not proof that the path is dark. Nothing here blocks
> building the gated Treasury module (ADR-0063) or the connectors; it blocks
> claims of production parity, adoption and retirement.

| Provider | Sub | ERP | Vendor CP | CRM |
|---|---|---|---|---|
| **Paystack** | **live** — client, webhook route, saved-card charging, autopay, reconciliation sweep, three one-off cutover scripts | **live** — a second, independent client (1,164 LOC), its own webhook route, transfers, sync poller | — | — |
| **Flutterwave** | **live** — client, webhook route, hosted checkout; no saved-card, no transaction listing | name strings only (chart-of-accounts / bank names), no client | — | — |
| **Remita** | 8 files, docs/data only, no code | **live** — RRR government-payment client (370 LOC) + 9 more files. **Pull-only: no webhook.** | — | — |
| **Mono** (open banking, not a PSP) | — | **live** — bank-statement ingestion client (660 LOC) + webhook | — | — |
| Stripe | **dead enum only** | 8 files, all the CSS/visual sense of the word | — | enum + label strings only |
| PayPal | **dead enum only** | — | — | allowed-value string only |
| Monnify / Squad / OPay / Kuda / Providus / Wema / Moniepoint | absent | bank-directory rows only | — | absent |
| Interswitch | absent | 1 line, statement parsing | — | absent |

**Two independent Paystack clients exist in the fleet.** Sub's
(`app/services/integrations/connectors/payment_gateway.py`, 403 LOC, shared with
Flutterwave) and ERP's
(`app/services/finance/payments/paystack_client.py`, 1,164 LOC, Paystack-only,
with its own transfers and batch-transfer surface). They share no code, no
credential store, no webhook receiver and no idempotency ledger. This is the
duplication ADR-0024 § 6 names when it says products *"do not each compose"* the
Integrator: two clients means two rate-limit budgets against one provider
account, two backoff policies and two answers to "did this charge run?"

`direct_bank_transfer` (Sub, `app/services/topup_intents.py:52`) and USSD
mentions are **not** PSP transports — the first is a manual proof-of-payment
flow, the second is two string mentions with no code.

**Finding: two dead persisted enum members in Sub.** `stripe` and `paypal` are
`PaymentProviderType` values stored in a database `Enum` column
(`payment_providers.provider_type`, `app/models/billing.py:2372`) for providers
that have no client. They are a persisted-value surface with no implementation,
and retiring them is a migration rather than a code deletion. CRM's
`ConnectorType.stripe` (`app/models/connector.py:18`) is the same shape, also
persisted as a PostgreSQL enum value.

---

## 2. Sub — provider clients and the connector runtime

Sub is the qualifying product-first source, and it is already **structured as a
connector platform**: installations, immutable configuration revisions,
capability bindings, secret references, an inbox, a delivery ledger and
checkpoints all exist. `docs/inventories/integration-platform-sources.md`
already covers that engine; this section covers only the payment surface on top
of it.

| File (all under `dotmac_sub:`) | LOC | What it decides |
|---|---|---|
| `app/services/integrations/connectors/payment_gateway.py` | **403** | The only PSP HTTP client. `PaymentGatewayRunner(provider)` refuses anything outside `{"paystack","flutterwave"}` at `:50-51`, then branches on `self.provider` throughout. Declares the four capability ids at `:22-25` and the per-capability action allow-list at `:27-41`. |
| `app/services/integrations/payment_capability.py` | **374** | Capability facade: binding resolution, secret materialisation, and **webhook signature verification** (`:359-374`). Also the provider allow-list `_connector()` at `:53-57`. |
| `app/services/payment_gateway_adapter.py` | **354** | Normalises verify/refund provider payloads into `PaymentGatewayTransaction` / `PaymentGatewayRefund`. Registered in `adapter_registry` at `:353-354`. |
| `app/services/integrations/runtime_execution.py` | **310** | Registers `PaymentGatewayRunner("paystack")` (`:84`) and `("flutterwave")` (`:85`); materialises `secret_refs` at `:216-221`. |
| `app/services/integrations/registry.py` | **895** | Connector manifests. Paystack `:155-225` (egress host `api.paystack.co` at `:225`); Flutterwave `:664-715` (`api.flutterwave.com` at `:715`). Secret bindings at `:217-220` and `:705-709`. |
| `app/services/integrations/egress_gateway.py` | **333** | Outbound egress confinement — every provider call passes through it. |
| `app/services/integrations/egress_policy.py` | **53** | Manifest-derived host allowlist, default-deny. |

### 2.1 One class, two providers, fourteen branches

`PaymentGatewayRunner` is the shape ADR-0024 § 7 forbids, in the source:

| Line | Branch |
|---|---|
| `:50-51` | constructor allow-list `{"paystack","flutterwave"}` |
| `:108` | health probe path `/bank` vs `/banks/NG` |
| `:174-185` | success test `status is True` vs `status == "success"` |
| `:199-200` | `charge_authorization` — Paystack only, else `raise ValueError` |
| `:220-237` | `verify` — path vs query-parameter form |
| `:240-245`, `:253-265` | refund read paths `/refund` vs `/refunds` |
| `:280-282` | `list_transactions` — Paystack only |
| `:320-342` | initialise — **kobo int** vs **major-unit float**; `metadata` vs `meta` |
| `:361-376` | refund — `amount*100` + `merchant_note` vs major units + `comments` |

Extraction cannot port this class. It is a **split** into two independently
released distributions; porting it whole would relocate the conditional tree
into the Integrator, which the ADR names as a rejected alternative in terms.

### 2.2 Two money defects in the client

- `float(Decimal(str(params["amount"])))` at `:331` and `float(Decimal(str(amount)))`
  at `:371` — a float amount on the wire.
- `str(params.get("currency") or config.get("default_currency") or "NGN")` at
  `:333` — a hardcoded currency fallback, three layers deep in a default chain.

Both are port deltas, not preserved behaviours.

---

## 3. Sub — webhook ingress and signature verification

### 3.1 The routes

| Line | Route | Handler | Signature header | Guard |
|---|---|---|---|---|
| `app/api/billing.py:1393-1411` | `POST /api/v1/payment-events/paystack` | `paystack_webhook` (`async`) | `X-Paystack-Signature` (`:1406`) | **none** |
| `app/api/billing.py:1414-1432` | `POST /api/v1/payment-events/flutterwave` | `flutterwave_webhook` (`async`) | `verif-hash` (`:1427`) | **none** |

`webhook_router` is declared at `app/api/billing.py:174` with an inline comment
explaining that these are mounted without `require_user_auth` because *"external
providers authenticate by HMAC signature, not a session."* Mounted in
`app/main.py:76` as `("app.api.billing", "webhook_router", "api", "none")` with
prefix `/api/v1`, and deliberately **not** deferred — the comment at
`app/main.py:73-75` says deferring would drop payment confirmations during
startup.

Both handlers read `await request.body()` **before** any JSON parse, with an
inline note that this is the reason the handler is `async` at all. That ordering
is correct and ports as written.

Both wrap in `webhook_observation(provider=…, event="payment")`
(`app/api/webhook_observation.py`, 50 LOC), which emits `observe_webhook_event`
from `app/metrics.py:1142`.

**Path-prefix finding.** `app/main.py`'s audit-skip and API-sync-pressure
exemption lists key on the prefix `/api/v1/webhooks/` (`:62`, `:1238`). The
payment routes are at `/api/v1/payment-events/…` and are therefore **not** in
those sets. Whether that is intended is a Sub question, not an extraction one,
but it is exactly the kind of prefix drift ADR-0018 § "guards enumerate
entry-point families" exists to catch.

### 3.2 Signature verification — one function, two very different schemes

`app/services/integrations/payment_capability.py:359-374`:

```python
if provider_type == "paystack":
    secret = str(material.get("gateway_credentials") or "")
    expected = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest() if secret else ""
else:
    expected = str(material.get("webhook_signing_secret") or "")
return binding, bool(expected and signature and hmac.compare_digest(expected, signature))
```

- **Paystack:** HMAC-SHA512 over the raw body, constant-time compared. Correct
  construction; ports as written.
- **Flutterwave:** a static shared-secret string comparison. This is
  **Flutterwave's own `verif-hash` scheme**, not a Sub defect — but it means the
  header is a bearer secret with **no binding to the payload**, so a captured
  value replays against any body. The consequence for the connector contract is
  concrete: for a provider whose ingress cannot prove payload integrity, inbox
  dedupe and an independent `payments.reconcile.v1` re-verification carry the
  weight that a signature carries elsewhere. That must be a declared property of
  the connector, not an assumption.
- **Paystack's API bearer token *is* its webhook HMAC key** — the same
  `gateway_credentials` reference is used at `:62-63` (as
  `Authorization: Bearer`) and at `:364` (as the HMAC key). One compromise, two
  surfaces. The connector contract splits them into `api_secret_key` and
  `webhook_signing_secret` even where the provider currently lets them be one
  value.
- **Fail-closed is accidental.** A missing configured secret yields
  `expected = ""`, and only the `bool(expected and …)` guard prevents an empty
  signature matching. That works; it should be an explicit refusal.

The `_connector()` allow-list at `:53-57` and the `if provider_type == "paystack"`
at `:363` are two more instances of the forbidden conditional — and they sit in
a file the `dotmac-integration` dossier currently marks **not ported** (§ 8, C1).

---

## 4. Sub — where transport ends and meaning begins

This is the section the extraction turns on.

### 4.1 The adapter is clean — with one exception

`app/services/api_billing_webhooks.py` (**244** LOC) does no ORM writes, touches
no `PaymentStatus`, and handles no money. Its architecture test
(`tests/architecture/test_payment_webhook_ownership.py:99-119`, 119 LOC) asserts
exactly that: no `add`/`delete`/`flush`/`commit`/`rollback`/`begin_nested`, no
`app.models` import, no `PaymentStatus` reference.

The exception is `_map_payment_webhook_error` at `:44-81`. It decides the
retry-versus-dead-letter policy for a **rejected financial event**:
`deposit_rejected` and `provider_event_rejected` get `max_attempts=1` (`:62-68`),
everything else gets `max_attempts=10` (`:76-81`). An HTTP adapter permanently
dead-letters a settlement. In the target shape, retry classification is the
engine's (`dotmac_integration.retry.next_state` + `policy.ExecutionPolicy`) and
terminality is the connector's typed `Outcome`; neither is an HTTP concern.

### 4.2 First cut — `payment_webhook_commands.py` (**654** LOC)

| Site | Line | Transport or meaning | What it decides |
|---|---|---|---|
| `identify_verified_payment_webhook` | `:137-159` | **transport** | Derives the receipt identity. Ports to the connector — but see the leak at `:157`. |
| `_settlement_observation` | `:189-232` | **transport → meaning, in one function** | Provider event strings become `PaymentStatus`: `charge.success` → `succeeded` (`:196-200`); Flutterwave `status == "successful"` → `succeeded` (`:214-215`), `"failed"` → `failed` (`:223-224`). The wire→neutral mapping is the connector's; the *typed money status* is billing's. |
| `_settlement_observation` | `:201-203` | **transport** | `divisor=Decimal(100)` per provider branch — minor-unit conversion hardcoded rather than read from the currency. |
| `_prepare_payment_webhook` | `:343-355` | **meaning** | Money admissibility: amount must be positive; fee must be `0 ≤ fee ≤ amount`. |
| `_prepare_payment_webhook` | `:361-365` | **meaning, and a security boundary** | `invoice_id = _metadata_uuid(settlement.metadata, field="invoice_id", …)` — the invoice a payment will be allocated to is read out of **PSP-controlled metadata**. |
| `_prepare_payment_webhook` | `:371-375` | **meaning** | `net_amount = requested_amount if topup_intent else round_money(amount - provider_fee)`. The number that gets allocated is computed here. |
| `_resolve_topup_intent` | `:263-302` | **meaning** | Resolves a Sub-owned `TopupIntent` and refuses reference/provider mismatches. |
| `_stage_deposit_settlement` | `:391-452` | **meaning** | Stages an account-credit deposit settlement. |
| `_stage_provider_event` | `:455-479` | **meaning** | Hands the observation to the provider-event owner. |
| `_stage_topup_consequences` | `:482-521` | **meaning** | Projects the payment onto the top-up intent. |
| `_process_claimed_payment_webhook` | `:618-627` | **meaning** | The "money must be linked" invariant: a succeeded settlement with no `payment_id` raises `settlement_unlinked`. |

**The identity leak at `:157`.** `provider_event_id = f"{provider.value}-{identity}"`
prefixes the provider name onto every event identity, and that identity becomes
the `idempotency_key` (`:324`) and the `CommandContext.idempotency_key`
(`:179`). The provider name therefore reaches every downstream idempotency
record. This is the single most consequential provider-name leak in the fleet,
because it is not a string in a file — it is a value in durable rows.

### 4.3 Second cut — `payment_provider_events.py` (**1335** LOC)

`_stage_financial_consequences` at `:599-973` is a **375-line function** with
five mutually exclusive settlement paths, chosen by provider-derived fields
(`observation.external_id`, `observation.provider_reference`). Everything it
does is billing's under ADR-0020 § 1. The decisions, verified:

| # | Line | Decision |
|---|---|---|
| 1 | `:235-246` | `_STATUS_BY_EVENT_TYPE` — ten provider event strings mapped to `PaymentStatus` |
| 2 | `:248-253` | `_FINANCIAL_EFFECT_BY_EVENT_TYPE` — `*.refunded` → `refund_confirmed`, `*.reversed` → `reversal_confirmed` |
| 3 | `:311-320` | money admissibility (fee ≤ gross, net ≤ gross) |
| 4 | `:353-366` | refunded/reversed status **requires** a matching declared financial effect |
| 5 | `:367-374` | **refund and reversal evidence requires a signature-verified webhook** — `untrusted_financial_effect` |
| 6 | `:375-382` | administrative events can never change payment state |
| 7 | `:522-527` | replay conflict: same identity, different money → hard reject |
| 8 | `:676-686` | mutates the provider fee on an existing payment |
| 9 | `:719-736` | **creates a payment and settles it against an invoice** |
| 10 | `:743-750` | **coverage decision** — `amount=min(observation.amount, balance_due)` |
| 11 | `:752-773` | consolidated billing-account settlement |
| 12 | `:774-792` | succeeded payment creation with allocations |
| 13 | `:793-809` | pending payment creation for non-success observations |
| 14 | `:810-839` | late-binds an existing payment to an invoice |
| 15 | `:841-875` | **allocation amount** `min(payment.amount, balance_due)` plus an idempotent allocation confirm keyed on a SHA-256 of provider fields |
| 16 | `:876-885` | records a non-settling allocation intent |
| 17 | `:887-891` | stamps `event.invoice_id` — the invoice the event is deemed to belong to |
| 18 | `:896-909` | **refund decision** and its posting |
| 19 | `:910-923` | **reversal decision** and its posting |
| 20 | `:924-956` | consolidated re-settlement carrying existing allocations |
| 21 | `:957-963` | generic payment status transition |
| 22 | `:966-970` | orphan money: succeeded event with no payment → `error_code = "payment_not_found"` |
| 23 | `:1248-1279` | customer sub-ledger posting group for a refund (`credit_refunded`) |
| 24 | `:1309-1335` | negates the original posting group; **silently returns** when none exists (`:1321-1322`) |
| 25 | `:1227-1231` | with no owner command active, the money transition proceeds **unposted** by design |

Rows 5 and 7 are worth keeping: "refund evidence requires a signature-verified
webhook" and "same identity with different money is a hard reject" are exactly
the invariants the connector contract needs to preserve on the other side of the
boundary, and they came from this product.

Rows 22, 24 and 25 are money-at-risk paths recorded here as inputs to whoever
owns billing, not as connector work.

---

## 5. Sub — routing, autopay, retries, checkpoints and the delivery ledger

| File | LOC | Role |
|---|---|---|
| `app/services/payment_routing.py` | **311** | Gateway presentment. `SUPPORTED_PROVIDER_TYPES = (paystack, flutterwave)` at `:27-30`; eight-member `GatewayHealthState` at `:42-52`; `provider_health` at `:147-257` requires an enabled installation, a pinned manifest and all four capabilities bound; `_presentment_priority` reads `binding.policy_json["presentment_priority"]` at `:135-144`; `gateway_options` sorts by `(-priority, provider_type.value)` at `:260-281` — **an alphabetical tiebreak, so Flutterwave wins over Paystack at equal priority**. |
| `app/services/autopay.py` | **467** | A per-provider retry engine. See below. |
| `app/tasks/autopay.py` | **25** | Celery task `app.tasks.autopay.charge_due_invoices` (`:17`) |
| `app/services/payment_reconciliation.py` | **811** | Gateway-verify sweep for stranded top-ups |
| `app/tasks/payment_reconciliation.py` | **32** | Celery task `app.tasks.payment_reconciliation.reconcile_topups` (`:17`) |
| `app/services/integrations/inbox.py` | **288** | Inbound receipt ledger and retry. `mark_failed` at `:201-214` dead-letters at `attempt_count >= max_attempts`. |
| `app/services/integrations/delivery.py` | **394** | Outbound delivery ledger with exponential backoff `min(8h, 60 * 2**(n-1))` at `:366-369`, dead-letter cap at `:362`. **Not used by the PSP inbound path.** |
| `app/models/integration_platform.py:238-281` | — | `IntegrationCheckpoint` / `integration_checkpoints` — cursor storage. **Not used by the payment reconcile path.** |

### 5.1 Autopay is the densest transport/meaning fusion in the fleet

`app/services/autopay.py` charges saved cards for due invoices, and it is
**Paystack-only in a file that names no provider in its title**:

| Line | What |
|---|---|
| `:199-212` | `_autopay_reference` — deterministic `AUTOPAY-{invoice_id}-{amount_kobo}[-A{attempt}]`, with a docstring reasoning explicitly about Paystack's burn-on-decline behaviour |
| `:239-249` | `_recover_charge` calls `verify_transaction(db, provider_type="paystack", …)` — **a hardcoded provider literal** |
| `:320-321` | refuses to charge an account with no collectible service |
| `:323-325` | attempt cap from the `autopay_max_consecutive_failures` setting |
| `:347-350` | `amount = round_money(to_decimal(invoice.balance_due))` — **charges the invoice balance** |
| `:358-361` | double-charge guard |
| `:379-386` | the charge itself |
| `:403-419` | **allocates the payment to the invoice directly**, bypassing `payment_provider_events` entirely |
| `:420-433` | charged-but-unrecorded → `logger.error("AUTOPAY RECONCILE: …")` and no reversal |
| `:440-443` | failure-counter mutation advances the attempt suffix |

The split is clean in principle and awkward in practice: mandate lifecycle,
failure counting, suspension, invoice selection and allocation are **billing and
collections meaning**; the deterministic reference, the burn-on-decline
workaround and the prior-attempt recovery are **provider transport**. They are
interleaved line by line in one function.

### 5.2 There is no payment polling checkpoint

`app/services/payment_reconciliation.py` selects candidates from Sub's own
`TopupIntent` rows and asks the provider about each. It is a **product-driven
sweep**, not a provider cursor — so it structurally cannot discover a settlement
for a payment the product never knew about. The generic `IntegrationCheckpoint`
machinery exists and is unused by this path.

---

## 6. ERP — a second, entirely separate payment transport

`docs/inventories/billing-sources.md:45` records ERP's PSP integration as
**Remita**. That is true and incomplete: ERP runs **three** external financial
transports, and Remita is the smallest of them.

### 6.1 The three transports

| Transport | Purpose | Ingress | Client (LOC) |
|---|---|---|---|
| **Paystack** | collections and outbound transfers | webhook, HMAC-SHA512 | `app/services/finance/payments/paystack_client.py` — **1,164** |
| **Remita** | RRR government payment references | **none — poll-only** | `app/services/remita/client.py` — **370** |
| **Mono** | open-banking bank-statement ingestion | webhook, **shared-secret header** | `app/services/finance/banking/mono_client.py` — **660** |

Base URLs are hardcoded: `paystack_client.py:21`
`PAYSTACK_BASE_URL = "https://api.paystack.co"`; `remita/client.py:22-23`
`REMITA_DEMO_URL` / `REMITA_LIVE_URL`.

Supporting Paystack surface (all `dotmac_erp:`): `payment_service.py` **1,821**,
`paystack_sync.py` **718**, `webhook_service.py` **516**,
`batch_transfer_service.py` **476**, `payments/web.py` **411**,
`paystack_customer_sync.py` **182**, `api/finance/payments.py` **825**,
`web/finance/payments.py` **229**, models `payment_intent.py` **237**,
`transfer_batch.py` **360**, `payment_webhook.py` **114**,
`tasks/payments_sync.py` **67**, migration `add_paystack_payment_tables.py`
**152**.

Remita surface: `rrr_service.py` **457**, `source_handler.py` **230**,
`services/remita/web/remita_web.py` **590**, `web/finance/remita.py` **451**,
model `models/finance/remita/rrr.py` **217**, migration
`20260201_add_remita_rrr_table.py` **186**.

Mono surface: `mono_sync.py` **1,648**, `api/finance/banking.py` **967**.

### 6.2 Ingress and verification

| Route | File:line | Guard | Scheme |
|---|---|---|---|
| `POST /payments/webhook/paystack` | `app/api/finance/payments.py:734` | **none** — separate `webhook_router` (`:42`), un-primed `get_db()` (`:45`) | HMAC-SHA512 over the raw body, `paystack_client.py:385-421`, `hmac.compare_digest` at `:420`, raises when the secret is missing (`:399-407`) |
| `POST /banking/webhook/mono` | `app/api/finance/banking.py:945` | **none** (`mono_webhook_router` at `:942`) | **Not HMAC** — a plain `compare_digest` of the `mono-webhook-secret` header against the configured secret (`mono_client.py:637-660`), with an explicit fail-closed guard for empty values at `:657-658` |
| `POST /dotmac-sub/webhook` | `app/api/dotmac_sub.py:212` | none | HMAC-SHA256; the signature also carries the organization identity (`:105-140`). Carries `payment.received`, `payment.refunded`, `invoice.paid`. |

Remita has **no webhook at all** — status is pulled by `check_status`
(`rrr_service.py:181`) or asserted by a human through
`POST /finance/remita/{rrr_id}/mark-paid` (`web/finance/remita.py:289`), behind
finance-role auth only. That is the whole reason `payments.reconcile.v1` is a
first-class capability in the connector contract and not an optional extra: a
real production provider in this fleet has no push channel.

### 6.3 Where ERP's transport makes a financial decision

Same pattern as Sub, drawn at a different depth.

| Site | Line | Decision |
|---|---|---|
| `webhook_service.process_webhook` | `:136` | sets `webhook.organization_id` from the **resolved intent** — the tenant comes from provider-correlated data, not from auth |
| `webhook_service._validate_amount_and_currency` | `:230-232` | direction-aware money tolerance: `max_tolerance_kobo = 5 if outbound else 1` |
| `payment_service.process_successful_payment` | `:495-520` | **the coverage decision** — `PaymentAllocationInput(invoice_id=…, amount=min(intent.amount, invoice.balance_due))`, byte-for-byte the same shape as Sub's `payment_provider_events.py:748` |
| same | `:527`, `:535` | creates the customer payment and auto-posts to the GL |
| same | `:549-558` | **GL posting failure is swallowed** ("Log but don't fail") |
| `payment_service.process_successful_transfer` | `:1210`, `:1396`, `:1483` | transfer settlement, PSP fee posting, batch-item state |
| `payment_service.process_transfer_reversal` | `:1666`, `:1710-1717`, `:1733` | reverses an expense claim from `PAID` back to `APPROVED` and posts reversing journals |
| `remita/rrr_service.check_status` | `:212-231` | provider status code `"00"` → `RRRStatus.paid` → `_handle_paid` |
| `remita/source_handler._handle_ap_payment_paid` | `:137-140` | `SENT` → `CLEARED` on a supplier payment |

Two findings that belong in this document because they are transport failures,
not billing ones:

- **`retry_failed_webhook` (`webhook_service.py:454`) re-executes the money
  handlers from the stored payload with no signature re-verification.** Verified
  by reading `:454-500`: it resets status, re-looks-up the intent by
  `paystack_reference`, and dispatches straight back into
  `_handle_charge_success` / `_handle_transfer_success` /
  `_handle_transfer_reversed`. Compare the Integrator's shape, where
  `operations.replay_receipt` is an authorized, audited operation
  (`integration.receipt.replayed`) over an already-verified receipt, and
  `claim_receipt` refuses a bare claim on a dead-lettered row.
- **`source_handler.py:134-135` writes `payment.remita_payment_reference` behind
  a `hasattr(...)` guard, and the attribute does not exist on `SupplierPayment`
  — so the provider reference is silently dropped.** A correlation reference
  that is written conditionally is a correlation reference that is absent when
  it is needed.

### 6.4 ERP credentials

Per-organization, DB-backed, encrypted where `is_secret=True`, in
`app/services/settings_spec.py`: `paystack_enabled` `:504`,
`paystack_public_key` `:511`, `paystack_secret_key` `:518`,
`paystack_webhook_secret` `:526`, `paystack_callback_base_url` `:534`, four
account/enable knobs `:543`–`:567`; `mono_enabled` `:575`, `mono_public_key`
`:584`, `mono_secret_key` `:593`, `mono_webhook_secret` `:603`. Encrypted at
rest by migration `20260712_encrypt_secret_settings.py`.

**Remita is the exception, and it is the wrong exception.** Its credentials are
**process-global environment variables** — `REMITA_MERCHANT_ID`,
`REMITA_API_KEY`, `REMITA_IS_LIVE` (`app/config.py:184-188`) — in an otherwise
multi-tenant ERP where every Paystack and Mono key is per-organization. One
organization's Remita merchant identity is every organization's.

**A second finding worth escalating:** `app/api/finance/payments.py:276` sets
`webhook_secret=str(secret_key)` — the Paystack **API secret key** is used as
the webhook verification secret — while a separate `paystack_webhook_secret`
setting exists (`settings_spec.py:526`) and *is* used by the Celery poll path
(`app/tasks/expense.py:876`). Two code paths can authenticate against different
secrets. Sub has the same conflation (§ 3.2); ERP has it *and* a second
configured value that disagrees.

### 6.5 ERP provider-name leakage — worse than Sub, because it is in columns

Sub's leakage is mostly literals. ERP's is in the **schema**:

| Column / table | Location |
|---|---|
| `payment_intent.paystack_reference` (unique) | `models/finance/payments/payment_intent.py:64`; DDL `add_paystack_payment_tables.py:59`, `:87` |
| `payment_intent.paystack_access_code` | `payment_intent.py:70` |
| `payment_intent.paystack_transaction_id` | `payment_intent.py:174` |
| `payment_webhook.paystack_event_id` (unique) | `payment_webhook.py:58`; DDL `:100`, `:110` |
| `payment_webhook.paystack_reference` | `payment_webhook.py:64` |
| `transfer_batch.paystack_batch_reference` | `transfer_batch.py:177` |
| `bank_account.mono_*` — eight columns | `models/finance/banking/bank_account.py:164-191` |
| **table** `payments.remita_rrr` + `RRRStatus` enum + three constraints/indexes | `models/finance/remita/rrr.py:34,53,55-57` |

Plus provider names in reconciliation *logic*:
`transaction_patterns.py` (37 LOC) is entirely provider regexes —
`PAYSTACK_REF_RE` `:16`, `BANK_FEE_RE = re.compile(r"Paystack Fee:")` `:19`,
`PAYSTACK_DEPOSIT_RE = re.compile(r"paystack|PSST10")` `:26`,
`PAYSTACK_OPEX_RE` `:30` — consumed by
`reconciliation_policy_service.py:43` and
`auto_reconciliation_parts/base.py:93,95`. A bank-name map in
`services/dotmac_sub/sync/_constants.py:15-18` hardcodes `"paystack"`,
`"pay stack"`, `"flutterwave"`, `"flutter wave"`.

Route segments: `/payments/webhook/paystack`, `/banking/webhook/mono`,
`/accounts/{id}/link-mono|unlink-mono|sync-mono|refresh-mono`
(`api/finance/banking.py:841,868,893,916`),
`/admin/settings/payments/paystack` (`web/admin.py:1665,1680`),
`/finance/remita/*`.

Celery task names — **unlike Sub, these do carry provider names**:
`app.tasks.finance.sync_paystack_transactions` (`tasks/finance.py:1305`,
scheduled every 1800s at `services/settings_seed.py:590-591`),
`sync_customers_to_paystack` (`tasks/payments_sync.py:28`).

### 6.6 ERP transport evidence and retries

- **Receipt ledger:** `payments.payment_webhook` (`payment_webhook.py`, 114 LOC)
  — `WebhookStatus` `:19` (RECEIVED/PROCESSING/PROCESSED/FAILED/DUPLICATE),
  `retry_count` `:103`, raw `payload` `:72`, `signature` `:77`.
- **Dedupe key:** `_build_event_id` (`webhook_service.py:172`) =
  `event_type + reference`, deliberately excluding the provider transaction id.
  Enforced by a unique constraint on `paystack_event_id`, with both a pre-check
  (`:80-87`) and an `IntegrityError` catch (`:106-121`). This is the same
  two-layer shape `dotmac_integration.execution.receive_verified` uses.
- **Poller as a webhook backstop:** `poll_stuck_expense_transfers`
  (`tasks/expense.py:761`), `MAX_POLL_ATTEMPTS = 10` (`:786`), every 2 minutes.
- **Sync backstop:** `PaystackSyncService.sync_transactions`
  (`paystack_sync.py:112`) over collections, transfers and settlements, every
  30 minutes.
- Generic `event_handler_checkpoint.py` (88) and `outbox_relay.py` (617) exist
  and are **not wired into the Paystack path**.

### 6.7 ERP tests

`tests/finance/test_paystack_payments.py` **153**,
`tests/finance/test_expense_transfer_lifecycle.py` **1,463**,
`tests/services/test_paystack_customer_sync.py` **199**,
`tests/services/test_mono_sync.py` **2,473**,
`tests/unit/test_payment_coverage.py` **141**,
`tests/architecture/test_webhook_org_attribution.py` **71**,
`tests/api/test_dotmac_sub_webhook_org.py` **268**,
`tests/api/test_dotmac_sub_webhook_retry.py` **130**,
`tests/services/test_dotmac_sub_payment_{allocations,idempotency,reversal}.py`
**94 / 110 / 256**, `tests/test_settings_encryption_at_rest.py` **278**,
`tests/test_settings_secret_exposure.py` **137**.

Two gaps: `tests/test_webhook_routes_mounted.py` (28 LOC) — written precisely to
catch an unmounted webhook receiver — asserts only `/dotmac-sub/webhook`,
`/dotmac-academy/webhook` and `/crm/webhook` at `:20-23`, and **omits both
money-bearing routes**. And there is no dedicated Remita test file anywhere;
`grep -rln remita tests` returns only `test_dependency_health.py` (129).

---

## 6A. Vendor control plane — no payment surface, and that is the finding

Verified by exhaustive grep over `src`, `tests`, `alembic` and `docs`:
`remita|paystack|flutterwave|stripe|monnify|interswitch|opay|palmpay|squad|kuda|providus|wema|moniepoint`
→ **zero files, zero lines**. `psp`, `gateway`, `charge`, `checkout`, `mandate`
→ zero. No outbound HTTP client of any kind — `httpx|requests\.|aiohttp|urllib`
over `src/vendor_cp/**/*.py` → zero hits. This matches the frozen baseline in
`docs/inventories/external-connector-baseline.json`, which measures Vendor CP at
zero in all six categories.

The four `payment` mentions are all **prose, and all negative** —
`docs/ARCHITECTURE.md:44` ("A request-time access check never calls a
payment/cloud provider"), `docs/design/domain-foundation.md:109` (an
anti-pattern: allocation lifecycle must not be "a side effect of a payment
webhook"), `docs/design/contract-service.md:24`, and
`domain-foundation.md:416`.

**Does Vendor CP take payments for its own deployments today? No.** It carries
**prices as frozen snapshots** and never money: `offers/models.py:38-39`
(`amount` as a decimal string plus `currency_code`),
`contracts/models.py:53,101-102` (`unit_amount` / `unit_currency_code`,
*"Frozen unit price snapshot — NULL until submit, then immutable"*). Contract
activation and suspension are approval-driven, not payment-driven
(`contracts/router.py:94-143`). Real providers are hard-blocked at startup —
`providers.py` raises `RealProviderNotPermittedError` unless
`VENDOR_PROVIDER_MODE == "fake"`.

Platform scope is explicit and documented in the models themselves:
`accounts/models.py:4-5` — *"NO `tenant_id` and NO RLS, because a vendor account
has no tenant context"*; the same note in `contracts/models.py:4,11`,
`offers/models.py:3`, `allocations/models.py:10`, `licensing/models.py:20`,
`licensing/delivery_models.py:19`, `approvals/models.py:3`.

ADR-0020 A6 nevertheless gives Vendor CP **platform-plane billing** and names the
Integrator as the host of *"the PSP/payment connector plugins and their transport
evidence."* So Vendor CP is a future payment consumer with zero transport to
retire — the ideal adoption shape, and the reason § 7's dual-plane note matters.

One structure worth naming: Vendor CP already runs a mature append-only delivery
ledger with generation-scoped retry budgets for **licences** —
`licensing/delivery_models.py` (283 LOC): `licence_delivery_attempts` `:220`,
`attempt_no` `:239`, `uq_licence_delivery_attempt_no` `:225`, retry budget
`:138-139`, `AttemptOutcome` `:146`, `DeliveryState` `:33`; driver
`licensing/transport.py` (616 LOC). It is the same shape a payment transport
needs, built by the same fleet, for a different payload. It is **not** an
extraction source — `dotmac_integration.execution` already owns that mechanism —
but it is evidence the shape is understood here.

---

## 6B. CRM — no PSP transport, and one money decision that trusts its caller

CRM has **zero** PSP clients, zero PSP webhooks and zero PSP credentials.
`paystack` appears once, in a docstring (`app/schemas/crm/portal.py:200`);
`remita`, `flutterwave`, `monnify`, `interswitch` and `mono` are absent; the 12
`stripe` hits are enum members, allowed-value strings and the CSS sense of the
word.

The admin "payment providers" registry
(`app/web/admin/integrations.py:133,152,1008-1130`) persists a JSON blob into
`DomainSetting.key == "payment_providers"` and stores secrets **by reference
only** (`webhook_secret_ref`, with a `vault://payments/stripe/webhook-secret`
placeholder in the template). **Nothing in CRM resolves that reference** — it is
configuration for a transport that does not exist. Notable in one respect: CRM
independently arrived at reference-not-value storage, the same conclusion
`dotmac_integration.secret_refs` enforces.

**The one financial decision, and it has no provider behind it.**
`POST /quotes/{quote_id}/accept` (`app/api/crm/portal.py:358`, guarded by
`require_portal_auth` + `quotes:write`) accepts a client-supplied
`deposit_reference` — its schema field description literally reads *"Verified
deposit payment reference"* (`app/schemas/crm/portal.py:198`) — plus a
client-supplied `deposit_amount`. `portal_quotes.accept_with_deposit:365` records
`"paid": True` from the request body and calls
`_record_deposit_on_sales_order:404`, which sets coverage and status directly:

```python
sales_order.deposit_paid = True
sales_order.amount_paid = paid
sales_order.balance_due = _money(max(Decimal("0.00"), total - paid))
sales_order.payment_status = (SalesOrderPaymentStatus.paid
    if paid >= total and total > 0 else SalesOrderPaymentStatus.partial)
```

The result is then pushed into Sub as an allocating payment
(`services/events/handlers/selfcare_customer.py:403` →
`services/selfcare.py:432`, idempotent on
`external_ref=f"sales_order:{sales_order_id}:payment"`).

`grep -rn "paystack\|deposit_reference" tests` in CRM returns **zero hits** — no
test asserts anything about this trust boundary.

This is recorded here because it is the clearest illustration of what the
connector contract's `confirmation_evidence` field is for. *"Verified"* is a word
in a docstring; `connector_verified` is a claim a signature check made. CRM is
being decommissioned and its connectors are **deleted, not ported** per the
standing rule in `packages/dotmac-integration/EXTRACTION.toml`, so this is a
requirement input, not an extraction source.

---

## 7. Credentials — how PSP secrets are held today

Three products, three different answers, and only one of them is the target
shape.

| Repo | Storage | Form | Scope |
|---|---|---|---|
| **Sub** | `integration_config_revisions.secret_refs` (JSON, `NOT NULL`) | **references** — `bao://`, `openbao://`, `vault://`, `env://` | per installation |
| **ERP** | `SettingDomain.payments` / `.banking`, encrypted at rest where `is_secret=True` | **values** | per organization — **except Remita, which is process-global env** |
| **CRM** | `DomainSetting["payment_providers"]` JSON blob | **references** (`webhook_secret_ref`) that nothing resolves | n/a — no transport exists |
| **Vendor CP** | — | — | no credentials at all |

Sub's is the product-first source. ERP's is encrypted-value storage, which the
connector contract supersedes rather than ports — the same reason the
`dotmac-integration` dossier marks Sub's own value-encryption suites
`exclude-superseded`.

### 7.1 Sub — the source shape

**Sub holds no PSP secret in the environment and none in code.** Verified: zero
occurrences of `PAYSTACK_*`, `FLUTTERWAVE_*` or `STRIPE_*` in any `.env*`,
`*.yml`, `*.toml`, `*.cfg` or `*.example` in the main tree.

| Layer | Where |
|---|---|
| Storage | `integration_config_revisions.secret_refs` — JSON, `NOT NULL`, `app/models/integration_platform.py:129` (table) and `:165` (column). **References, never values.** |
| Declared names | Paystack `registry.py:217-220` — `gateway_credentials` (required), `public_key` (conditional). Flutterwave `registry.py:705-709` — `gateway_credentials` (required), `public_key` (optional), `webhook_signing_secret` (required). Capability-level overrides at `app/services/web_integrations_payment_gateways.py:79-83`. |
| Resolution | `app/services/integrations/runtime_execution.py:216-221` iterates `revision.secret_refs` and calls the injected `secret_resolver`. |
| Resolver | `app/services/secrets.py` (**462** LOC), `resolve_secret` at `:222-237`. |
| Schemes | `bao://`, `openbao://`, `vault://`, `env://` (`secrets.py:56-60`) |
| Store config (names only) | `OPENBAO_ADDR`/`VAULT_ADDR` `:72`; `OPENBAO_TOKEN`/`VAULT_TOKEN` `:43`; `OPENBAO_TOKEN_FILE`/`VAULT_TOKEN_FILE` `:34`; `OPENBAO_NAMESPACE`/`VAULT_NAMESPACE` `:74`; `OPENBAO_KV_VERSION` `:75`; `OPENBAO_CACHE_TTL_SECONDS` `:47` |
| Log hygiene | `RuntimeExecutionContext.secret_material` is `field(repr=False)` (`runtime_execution.py:73`) |

**This is the strongest thing in the source.** `bao`, `env` and `file` are three
of the five schemes `dotmac_integration.secret_refs.SECRET_REFERENCE_SCHEMES`
already recognises, and Sub already stores references on an immutable config
revision. The module's `validate_config_revision` refusal — which walks nested
config and rejects a literal under any secret-shaped key name — is the one thing
Sub does **not** have, and it is the reason the module's dossier marks Sub's
`test_connector_auth_config_encryption.py` and `test_connector_header_masking.py`
`exclude-superseded`: they cover storing and masking secret **values**, which the
new contract forbids outright.

One open port question: `secrets.py`'s TTL cache
(`OPENBAO_CACHE_TTL_SECONDS`) resolves on demand. ADR-0009's held-not-fetched
posture argues for load-once plus an explicit `refresh`, in the shape
`dotmac_kernel.secret_sources` already uses. That is a decision, not a defect.

---

## 8. Provider-named identifiers leaking into Sub's product domain code

This is the ratchet target ADR-0024 § "Enforcement and evidence" requires driven
to zero: *"Product architecture tests ratchet direct provider clients,
provider-named routes/tasks/configuration and provider credentials to zero as
Integrator capabilities adopt them."*

ERP's leakage is inventoried separately in § 6.5 and is **structurally worse** —
Sub leaks mostly literals, ERP leaks database columns, a table name, an enum
type and two Celery task names.

### 8.1 Persisted enum members — the expensive ones

| Path:line | Member |
|---|---|
| `app/models/billing.py:148` | `PaymentProviderType.stripe` — **dead** |
| `app/models/billing.py:149` | `PaymentProviderType.paypal` — **dead** |
| `app/models/billing.py:150` | `PaymentProviderType.paystack` |
| `app/models/billing.py:151` | `PaymentProviderType.flutterwave` |
| `app/models/connector.py:28` | `ConnectorType.stripe` — **dead** |
| `app/services/payment_webhook_commands.py:60-61` | `PaymentWebhookProvider.PAYSTACK` / `.FLUTTERWAVE` |

`PaymentProviderType` is a database `Enum` column
(`payment_providers.provider_type`, `app/models/billing.py:2372`) with **34
references across 12 modules**. Retiring it is a migration plus a data cutover,
not a rename.

`app/models/connector.py`'s `ConnectorType` is separately named in
`docs/inventories/external-connector-sources.md:53-58` as *"exactly what must
NOT be ported"* — the same finding, reached independently.

### 8.2 Route path segments

| Path:line | Leak |
|---|---|
| `app/api/billing.py:1394` | `"/payment-events/paystack"` |
| `app/api/billing.py:1415` | `"/payment-events/flutterwave"` |
| `app/services/operational_checks.py:44` | `PAYSTACK_WEBHOOK_PATH = "/api/v1/payment-events/paystack"` |

### 8.3 Module-level constants

| Path:line | Leak |
|---|---|
| `app/services/operational_checks.py:42` | `_PAYSTACK_CONNECTOR_KEY = "paystack"` |
| `app/services/payment_routing.py:28-29` | `SUPPORTED_PROVIDER_TYPES` |
| `app/services/billing_enforcement_guards.py:53-54` | `ONLINE_PAYMENT_PROVIDER_TYPES` |
| `app/services/web_integrations_payment_gateways.py:80-82` | `_CAPABILITY_REQUIRED_SECRETS` keyed on provider names |
| `app/services/customer_portal_flow_payments.py:85-86` | UI label map |
| `app/services/billing/reporting.py:785-787`, `:804` | payment-method label map and membership test |

### 8.4 Provider literals inside product **business** code — the worst ones

| Path:line | Leak |
|---|---|
| `app/services/billing_enforcement_guards.py:266` | `TopupIntent.provider_type.in_(["paystack","flutterwave"])` in an enforcement health query |
| `app/services/quote_deposits.py:293`, `:296-297`, and 7 more sites | `"paystack"` throughout the quote-deposit domain, including the error code `"paystack_unavailable"` |
| `app/services/customer_portal_flow_payment_methods.py:103`, `:109`, `:263`, `:269` | saved-card capture gated on `provider_type == "paystack"` |
| `app/services/reseller_portal_billing.py:147`, `:158-159` | `raise ValueError("Saved cards can only be used with Paystack")` |
| `app/services/customer_portal_flow_payments.py` (13 sites) | provider literals through the checkout flow |
| `app/services/autopay.py:245` | `provider_type="paystack"` |
| `app/services/sales/quote_delivery.py:348`, `:367` | **writes `("quote_payment_mode", "paystack")` into quote metadata** — a provider name in a durable business record |
| `app/services/sales/quote_documents.py:634-635`, `:666` | `class='payment-card paystack'` and a `.paystack-copy` CSS class **in a generated PDF** |
| `app/services/web_integrations.py:323`, `:351` | `entry.key in {"paystack","flutterwave"}` |
| `app/services/operational_checks.py:419` | `PaymentProvider.provider_type == PaymentProviderType.paystack` |

### 8.5 What is already clean

- **Celery task names** carry no provider name:
  `app.tasks.autopay.charge_due_invoices`,
  `app.tasks.payment_reconciliation.reconcile_topups`.
- **Settings keys** carry no provider name — nine payment-related specs in
  `app/services/settings_spec.py` (`autopay_charge_only_due` `:1934`,
  `autopay_max_consecutive_failures` `:1942`,
  `gateway_topup_intent_ttl_minutes` `:1980`,
  `topup_reconciliation_*` `:2148`–`:2178`, `autopay_interval_seconds` `:4929`,
  `topup_reconciliation_interval_seconds` `:4950`), all neutral.
- **Model columns and table names** carry no provider name: `provider_id`,
  `provider_type`, `provider_fee`, `provider_reference`, `external_id`,
  `payment_provider_events`. One provider-shaped comment at
  `app/models/billing.py:1296-1297`.

Three one-off scripts carry provider names in their **filenames**
(`scripts/one_off/paystack_cutover_reconcile_export.py` 445,
`paystack_cutover_post_credits.py` 244,
`reverse_duplicate_paystack_recoveries.py` 185). One-off scripts are a distinct
retirement class and should not be counted in the same ratchet as running code.

---

## 9. Tests available to port, and the one that is missing

### Behavioural (all `dotmac_sub:tests/`)

| File | LOC |
|---|---|
| `test_payment_webhook_settlement.py` | **954** |
| `test_autopay.py` | **597** |
| `test_provider_payment_settlements.py` | **462** |
| `test_paystack_cutover_reconcile.py` | **440** |
| `test_gateway_topup_intents.py` | **366** |
| `test_payment_provider_events.py` | **296** |
| `test_web_integrations_payment_gateways.py` | **233** |
| `test_integration_payment_capability.py` | **194** |
| `test_api_billing_webhooks.py` | **167** |
| `test_payment_routing.py` | **159** |
| `test_payment_gateway_refunds.py` | **134** |
| `test_payment_intent_management.py` | **104** |
| `test_payment_webhook_public.py` | **45** |

### Architecture — the transport/meaning contract, already encoded

| File | LOC |
|---|---|
| `tests/architecture/test_payment_settlement_participants.py` | **194** |
| `tests/architecture/test_payment_reconciliation_ownership.py` | **139** |
| `tests/architecture/test_payment_webhook_ownership.py` | **119** |
| `tests/architecture/test_payment_provider_event_ownership.py` | **110** |
| `tests/architecture/test_payment_gateway_control_plane.py` | **110** |

**The asymmetry that matters.** `test_payment_webhook_ownership.py:99-119` holds
`api_billing_webhooks.py` to a strict transport-only rule. There is **no
equivalent guard on `payment_webhook_commands.py`** — so the normalisation step
that turns a provider string into `PaymentStatus`, gates money admissibility,
targets an invoice from PSP metadata and computes the net amount (§ 4.2) is
entirely unguarded. That is why the boundary drifted into that file, and it is
the shape of the guard the extraction has to add on the connector side.

Also present: `tests/integration/test_payment_provider_event_concurrency.py`
(89) and the shared `tests/payment_provider_event_helpers.py` (64).

---

## 10. Corrections to existing inventories, and what is still unresolved

### Corrections

**C1 — `docs/inventories/billing-sources.md:46` cites the wrong directory.** It
lists `payment_provider_events.py` (1,335), `payment_webhook_commands.py` (654)
and `api_billing_webhooks.py` (244). The **LOC figures are exact**, but all three
live at `dotmac_sub:app/services/`, not under `app/services/billing/`. Verified
by `find`.

**C2 — `packages/dotmac-integration/EXTRACTION.toml`'s `not_ported` glob
`*_capability.py` is too broad for payments.** The dossier's rationale is sound
for what it describes — *"Sub business policy: what a message or payment MEANS to
Sub"* — but `app/services/integrations/payment_capability.py` is **not** that
file. Its 374 lines are a transport facade: binding resolution, secret
materialisation, and Sub's **only** payment webhook signature verifier
(`:359-374`), plus the provider allow-list (`:53-57`) and an
`if provider_type == "paystack"` branch (`:363`). Leaving it under a
`not_ported` glob would strand signature verification and a provider
conditional in the product forever — the precise opposite of ADR-0024 § 6.

The fix is not to widen the glob but to name the exception: the payment
connector dossier records `payment_capability.py` as **split** — the transport
half ports, the `_execute`/binding-resolution half is superseded by the module's
own `dispatch`/`selection`. Recorded here rather than edited there; the
integration dossier belongs to another owner.

**C3 — `docs/inventories/external-connector-sources.md`'s `webhook_surface`
detector may miss Sub's payment routes.** The documented rule counts *"a route
whose path contains `webhook`/`callback`/`/hooks`/`ipn`, or a function named
`verify_signature`-ish."* Sub's payment routes are at `/payment-events/…` and
its verifier is `verify_webhook_signature` — the function name matches, the path
does not. The baseline's `dotmac_sub: webhook_surface = 4` should be checked
against the six payment-relevant files before the payment ratchet is derived
from it. **Not a defect claim** — the detector's own "deliberately does NOT see"
column already admits *"a provider callback mounted at a domain-shaped path."*
It is a note that the payment ratchet needs its own baseline rather than
inheriting this one.

**C4 — `docs/inventories/billing-sources.md:45` understates ERP.** It records
ERP's PSP integration as "Remita". ERP additionally runs a full **Paystack**
collections-and-transfers stack (1,164-LOC client, its own webhook, its own
dedupe ledger, two Celery pollers) and a **Mono** open-banking transport. Remita
is the only one of the three with no ingress at all.

### Unresolved

1. **Which Sub deployment actually runs Flutterwave, and which ERP organizations
   have Paystack enabled.** The code is complete and tested in both; whether a
   production installation is enabled is a live-catalog question this document
   cannot answer from source.
2. **Receipt retention.** Raw payment payloads persist in Sub's
   `integration_inbox` and ERP's `payments.payment_webhook`, both with payer
   contact details. ADR-0014 § 6 makes retention a product policy; neither
   product has a retention job for these tables.
3. **Whether `stripe`/`paypal` rows exist in production `payment_providers`
   (Sub) or `connectortype` (CRM).** If they do, retiring the enum members is a
   data migration.
4. **ERP's two-secret Paystack ambiguity** (§ 6.4). Whether
   `paystack_webhook_secret` and `paystack_secret_key` currently hold the same
   value in production decides whether this is a latent defect or an active one.
   It is an ERP question, raised here because the extraction inherits it.
5. **Remita's process-global credentials** (§ 6.4). Whether more than one ERP
   organization uses Remita decides whether this is a cross-tenant credential
   leak today or only a design one.
