# The payment connector plugin and the settlement contracts

> **Review status: PROPOSED — not reviewed, not frozen.** This is historical
> intent, not proof of current behavior. Package-root `EXTRACTION.toml` files
> and accepted ADR amendments govern implemented connector slices.
>
> **Status:** specification of intent, `docs/superpowers/specs/` —
> non-authoritative. The accepted decisions are ADR-0024 §§ 6–7 and ADR-0020's
> 2026-08-14 amendment A3.
> **Date:** 2026-08-14
> **Owner boundary in one line:** the Integrator owns provider **transport**;
> it never owns payment **meaning**.
> **Governed by:** ADR-0024 (apps compose by synchronizing data; the Integrator
> is the sole external connector control plane; the connector-plugin SPI),
> ADR-0020 + A3 (billing owns the money decisions), ADR-0023 (dual-plane
> persistence), ADR-0014 (at-most-once has one owner), ADR-0009 (a secret is
> held, never dereferenced), ADR-0018 (an exemption states an enforceable
> premise), ADR-0008 (declaration registries), ADR-0010 (adapters are thin),
> ADR-0025 (a run's owner is not the row's owner).
> **Evidence base:** `docs/inventories/payment-connector-sources.md`,
> `docs/inventories/payment-connector-extraction-dossier.md`.
> **Counterparty spec:** `docs/superpowers/specs/2026-08-14-billing-authority-profile-contract.md`
> (Team 2 — **also PROPOSED**). § 2.2 there is the consumer of § 2 here. Where
> the two disagree, § 9 records it; neither side may treat the other as settled.

## 2026-08-20 implementation note

This specification remains historical intent, but its two original blocking
gaps no longer describe the as-built platform. `dotmac-integration` 0.1.0a10
ships SPI 1.3 ingress handlers, verification evidence, acknowledgement
ownership, declared secret bindings and egress policy. `dotmac_integrator` at
`d886e3c9956192fe1d5f085d352a516812c253c8` supplies the secret resolver and the
provider-neutral ingress/product-delivery adapters. Michael's 2026-08-20
ADR-0017 amendment authorizes separate Paystack and Flutterwave connector
distributions; it does not authorize product adoption or financial authority
movement.

The implemented first slice is Paystack ingress only. The Integrator's delivery
envelope owns destination, scope, source, receipt and idempotency context, which
resolves D1 and D2 without putting tenant or source-system fields in the
provider observation. The connector uses Paystack's documented ×100 wire scale
for every supported currency, including XOF. That is provider protocol, not the
hardcoded currency fallback this specification rejects: the emitted currency
is still mandatory and copied from the authenticated event, never defaulted.
Generic future payment transports must not infer their scale from Paystack.

## 2026-08-21 Flutterwave v4 implementation note

The Flutterwave package targets API v4 only. It verifies the current
`flutterwave-signature` as Base64 HMAC-SHA256 over the exact request bytes and
accepts only the v4 `type`/`webhook_id` event envelope. It has no v3
`verif-hash`, `event` or `tx_ref` fallback. Sub's v3 implementation remains the
product-first normalization and cutover source, but its authentication scheme
is deliberately not ported.

The v4 charge webhook carries amount, currency, reference and provider status
but no `app_fee`. The connector therefore omits `provider_fee`; zero would be a
fabricated financial fact. Fee evidence is deferred to
`payments.reconcile.v1`. The package's `EXTRACTION.toml` is authoritative for
this released slice; the historical v3 findings remain evidence about the
legacy product surface that cutover must retire.

---

## 0. What this document is, and the two gaps that block it

ADR-0020 A3 split the payment path in two. Billing owns payment intent as a
domain fact, acceptance of a typed settlement observation, allocation,
deallocation, reversal, refund, coverage and every financial consequence. A
payment connector plugin inside the Integrator owns the PSP client, the
credentials, the webhook signature verification, the raw ingress and its receipt
evidence, the dedupe, the retries and the checkpoints.

This document specifies the connector side of that line, and exactly one message
across it in each direction.

### 0.1 Blocking gap 1 — SPI 1.0 declares `INGRESS` but cannot run it

Already recorded, for a different connector, in
`docs/inventories/whatsapp-connector-sources.md`. It is restated here because it
blocks payments **harder**: a payment connector's primary mode is ingress, and
`payments.webhook.v1` in the source carries no executable actions at all
(`dotmac_sub:app/services/integrations/connectors/payment_gateway.py:33`,
`PAYMENT_WEBHOOK_CAPABILITY: set()`).

`packages/dotmac-integration/src/dotmac_integration/spi.py` ships
`ConnectorMode.INGRESS`, but the `ConnectorPlugin` protocol's only executable
member is `handler_for`, returning a `CapabilityHandler` over a
`DispatchRequest` that carries `capability_id`, `event_type`, `payload`,
`config`, `secrets`, `idempotency_key` — **no raw body and no headers**. Every
PSP signs the raw bytes (Paystack: HMAC-SHA512 over the body, presented as
`X-Paystack-Signature`; Flutterwave: a shared hash presented as `verif-hash` —
verified at `dotmac_sub:app/services/integrations/payment_capability.py:359-374`
and `dotmac_sub:app/api/billing.py:1393-1432`). A parsed `payload` cannot
reproduce the bytes, so verification has nowhere to happen.

This spec assumes the ingress hook sketched in `whatsapp-connector-sources.md`
lands first, and therefore declares `spi_range = ">=1.1,<2.0"` throughout. A
payment connector built against SPI 1.0 would have to verify signatures in the
assembly, which would make the deployment name a provider — the exact failure
ADR-0024 § 7 forbids and `dotmac_integrator`'s
`tests/architecture/test_the_assembly_stays_thin.py` already guards.

### 0.2 Blocking gap 2 — payments are explicitly not the first cutover

`packages/dotmac-integration/EXTRACTION.toml`'s `first_cutover` field names an
ingress-only Meta/WhatsApp messaging capability and says so in terms:
*"deliberately NOT payments… Payments would put a money decision behind an
unproven delivery ledger on its first run."*

ADR-0017's 2026-08-12 amendment is stronger still: *"An inbound receiver for
payment-provider events therefore remains under the same moratorium unless a
live adopter is blocked on it today."*

So this document is a **contract specification written ahead of its gate**, in
the same posture as Team 2's billing spec. It exists so the boundary is agreed
before anyone writes a plugin, not to start one.

### 0.3 Three more things that do not exist yet

Verified by reading `/Users/michaelayoade/Downloads/management/dotmac_integrator`
at `d014116` (733 LOC of Python across eight modules):

| Missing | Consequence for this spec |
|---|---|
| No ingress route at all — `assembly.py` mounts two probes, a composition report, a connector listing, a health report and three operations endpoints | § 5 specifies one provider-agnostic route; the assembly does not have it |
| No dispatch pump — `worker.py` runs only a lease sweep, and says the pump *"cannot be written honestly until a real connector exists"* | § 4's outbound direction has no runtime today |
| No secret resolver — `grep -n "resolve_secrets\|secret"` over the whole assembly returns nothing | § 6's materialization seam is declared by `dispatch.invoke`'s injected `SecretResolver` and **supplied by nobody** |

None of these is a defect in the module. They are the honest state of a runtime
that has never had a connector.

---

## 1. The payment connector plugin manifest

### 1.1 One distribution per provider — a correction, not a preference

The source implements two PSPs in **one** class:
`PaymentGatewayRunner(provider)` refuses anything outside
`{"paystack", "flutterwave"}` at `payment_gateway.py:50-51`, then branches on
`self.provider` at least fourteen times across `validate`, `_provider_data`,
`_execute_action`, `_initialize` and `_refund`.

That is the conditional tree ADR-0024 § 7 refuses, and porting it as one
plugin would relocate it rather than remove it. The extraction is therefore a
**split**: `dotmac-connector-paystack` and `dotmac-connector-flutterwave` are
separate distributions, separately released, separately installable, sharing
nothing but the SPI. Adding Remita later adds a third; it releases no product,
no module, no kernel and no assembly.

The test that this happened is not "the word `flutterwave` does not appear in
the Paystack package" — it is that each distribution declares exactly one
`connector_key`, and `test_no_plugin_branches_on_a_sibling_provider` scans each
package for any comparison against a provider name other than its own.

### 1.2 The declared manifest

Against `dotmac_integration.spi.ConnectorManifest` as shipped — the fields are
its fields, not new ones.

| Field | Value / rule |
|---|---|
| `connector_key` | Stable, lowercase, `^[a-z][a-z0-9_]{1,118}$` (`spi._KEY_RE`). One per distribution. Two installed distributions claiming one key is refused at discovery. |
| `version` | The distribution version. Non-empty; participates in `ConnectorManifest.digest`. |
| `spi_range` | `">=1.1,<2.0"`. Both bounds required — `SpiRange` rejects open-ended ranges. Stored on `connector_installations.spi_range`, not merely compared, so a later module upgrade can refuse a previously activated binding. |
| `capabilities` | The four in § 1.3. A duplicate capability id in one manifest is refused by `ConnectorManifest.__post_init__`. |

`modes` on the `ConnectorPlugin` (not the manifest) is
`frozenset({ConnectorMode.INGRESS, ConnectorMode.POLL, ConnectorMode.DELIVERY})`
for a full PSP connector. A connector may declare fewer — an ingress-only
observer that never initiates a charge is a legitimate and much safer first
slice, and § 10 recommends exactly that.

Discovery is `importlib.metadata` entry points in the group
`dotmac_integration.connectors` (`discovery.ENTRY_POINT_GROUP`). There is no
import list anywhere in module core or in the assembly.

### 1.3 The four capability contracts

Capability ids follow `domain.noun.vN` (`spi._CAPABILITY_RE`), where the version
is part of the identity rather than a separate column.

| Capability | Mode | What the plugin does | What it must never do |
|---|---|---|---|
| `payments.settlement.observation.v1` | INGRESS, POLL | Verify the provider's signature over raw bytes; normalise one or more provider events into `SettlementObservationV1` (§ 2) | Decide what the money means; touch a product database |
| `payments.intent.v1` | DELIVERY | Carry a `PaymentIntentCommandV1` to the provider and return `PaymentIntentAcknowledgementV1` (§ 4) | Decide whether the intent should exist, or what it settles |
| `payments.refund.v1` | DELIVERY | Carry a refund instruction to the provider and return its acknowledgement | Decide whether a refund is owed |
| `payments.reconcile.v1` | POLL, DELIVERY | Ask the provider the state of one reference, or page a window of transactions, and emit observations | Decide that a discrepancy is a shortfall |

**`payments.webhook.v1` is deliberately renamed.** The source calls it that
(`payment_gateway.py:23`), but "webhook" names a *transport mechanism*, not a
contract: the same settlement fact can arrive by webhook, by a reconciliation
poll, or by an operator-triggered verify, and all three must produce the same
message under the same identity. Naming the capability after HTTP would force a
second capability for the polled path and let the two drift. The source itself
proves the point — `dotmac_sub:app/services/payment_reconciliation.py` already
produces settlements from `verify`, i.e. from `payments.reconcile.v1`, not from
a webhook.

The other three ids are carried through unchanged from the source, which is
what product-first extraction means when the source already got it right.

### 1.4 The configuration contract

Each `CapabilityDeclaration.config_schema` is a JSON-schema fragment. Three
rules, and the third is enforced by code that already exists.

1. **Provider-shaped, not product-shaped.** `base_url`, `timeout_seconds`,
   `environment`, page sizes, retry hints. A key naming a product concept
   (`invoice_id`, `billing_account`, `tenant`) is a boundary violation.
2. **No money defaults.** The source has
   `config.get("default_currency") or "NGN"` at `payment_gateway.py:333`. A
   currency default in a connector is a business decision wearing a config key.
   The replacement: `currency` is **required** on every amount-bearing message
   and a missing currency is a refusal, never a fallback.
3. **Secret REFERENCES only.**
   `dotmac_integration.secret_refs.validate_config_revision` walks the whole
   nested config and refuses a literal under any key whose leaf name matches
   `secret|password|passwd|token|api_key|apikey|private_key|credential|client_secret`.
   `validate_secret_refs` requires every `secret_refs` value to be
   `<scheme>://<opaque>` with a scheme in
   `{bao, env, file, aws-sm, gcp-sm}`.

A payment connector's `secret_refs` keys are, minimally:

| Ref name | Used for |
|---|---|
| `api_secret_key` | Server-to-server authorization (`Authorization: Bearer …`) |
| `api_public_key` | The browser-side key the plugin returns from `get_public_key`. Held as a reference even though it is public, because config revisions are immutable and a "public" key that later turns out not to be is unrecoverable. |
| `webhook_signing_secret` | Ingress signature verification |
| `webhook_signing_secret_previous` | The rotation window. The source has this shape as `_signature_fallback_secret`; carry the behaviour as a second explicit ref, never as a database lookup. |

### 1.5 Factory entry points

`ConnectorPlugin.handler_for(capability_id)` returns a handler **only** for a
declared capability. `ConnectorManifest.require_declares` is the refusal, and it
already fires at the write in `lifecycle.add_binding`. The proposed SPI 1.1
addition is `ingress_handler_for(capability_id)`, symmetrical and subject to the
same refusal.

`validate_connection(config, secrets)` gates enablement on a **live** check, as
`lifecycle.enable` requires. The source's version is a cheap authenticated read
(`GET /bank` for one provider, `GET /banks/NG` for the other,
`payment_gateway.py:108`) — port the shape, not the path, and never a call that
moves money.

---

## 2. `SettlementObservationV1` — the Integrator's output, Billing's input

The message says **what the provider reported**. It never says what that means
for a receivable. It carries no allocation, no coverage, no invoice status, no
balance, no account state and no lifecycle field.

### 2.1 The eight facets

**1. Identity and version.** Contract name `SettlementObservationV1`, carried on
capability `payments.settlement.observation.v1`. Business identity is
`(source_system, source_settlement_key)`:

- `source_system` is the **Integrator deployment identity** — a stable,
  provider-neutral string configured once per Integrator runtime. It is **not**
  the PSP and **not** the connector key. See § 9, D1: this is a substantive
  disagreement with Team 2's spec.
- `source_settlement_key` is an **Integrator-minted opaque key**, derived as
  `sha256(installation_id || provider_event_id)` truncated to a fixed width. It
  is not the PSP's reference string.

The source mints the near-opposite: `f"{provider.value}-{identity}"`
(`payment_webhook_commands.py:157`), which prefixes the provider name onto every
event identity and therefore into every downstream idempotency key. That is the
provider-name leak this contract exists to stop, and it is a
`payments.settlement.observation.v1` port delta, not a preserved behaviour.

**2. Idempotency key and request fingerprint.** Per ADR-0014, whose one owner is
`dotmac_kernel.idempotency`; the Integrator adapts it through
`dotmac_integration.idempotency.run_effect_once` and owns no second ledger.

- **Ingress side:** dedupe is `(capability_binding_id, provider_event_id)`, a
  database UNIQUE constraint on `inbox_receipts`, not a service check —
  `models.InboxReceipt` documents this as the normal case rather than an edge
  case. `payload_digest` is the fingerprint: the same event id arriving with
  different content raises `ProviderEventIdentityCollision` rather than being
  treated as a redelivery (`execution.receive_verified:133-140`).
- **Delivery side:** `delivery_attempts.idempotency_key` is unique per
  installation and deduplicates **enqueue**. Whether the effect *ran* at most
  once is a separate question answered by the kernel ledger under scope
  `integration.delivery`.
- **On the wire:** `idempotency_key = source_settlement_key`;
  `request_fingerprint = sha256` over the canonicalised
  `{amount, currency, occurred_at, observation_kind, provider_status}`. A reused
  key with a differing fingerprint is a **conflict**, never a replay.

**3. Scope.** `SettlementObservationV1` carries **no tenant identifier and no
scope discriminator.** This is the strongest statement in the contract and it
follows from ADR-0023 plus the module's own shape: every table in `mod_intg` is
platform-plane with an explicitly empty tenant `tables` tuple
(`models.TENANT_TABLES = ()`), and the Integrator has no product tenancy to
speak of.

Scope is bound by **configuration on the installation**, not derived from the
payload. One installation delivers to exactly one product destination at exactly
one scope; the receiving product's inbound adapter knows its own scope because
it is that product. Deriving a tenant from provider metadata would let an
attacker who can influence a checkout's `metadata` block choose the tenant a
settlement lands in. The source has exactly this shape today
(`_metadata_uuid(settlement.metadata, field="invoice_id", …)`,
`payment_webhook_commands.py:361-365`) — provider-supplied metadata resolving a
product row. It is safe there only because Sub runs one operator tenant. It does
not survive the boundary.

**4. Currency and exact amount.** `{amount: decimal-string, currency: ISO-4217}`,
per `dotmac_kernel.money.Money`'s wire form. Never a float, never a bare number,
never an implicit currency.

Three source deltas the extraction must not carry:

- `float(Decimal(str(params["amount"])))` at `payment_gateway.py:331` and
  `:371` — a float amount on the wire to the provider. Wire encoding is the
  provider's business, but the plugin holds `Decimal` internally and converts
  once, at the last line before the request, with an explicit rounding rule.
- Minor-unit conversion (`amount_to_kobo` / `kobo_to_naira`,
  `payment_capability.py:140-145`) is currency-named. The replacement is
  `Currency.minor_units`, which `dotmac_kernel.money` already carries.
- `_money(..., divisor=Decimal(100))` at `payment_webhook_commands.py:201`
  hardcodes the divisor per provider branch rather than reading it from the
  currency.

`provider_fee` travels as its own exact `Money`, never netted into `amount`. The
source is right about this (`_SettlementObservation.provider_fee`,
`payment_webhook_commands.py:118`) and wrong about the next step: it computes
`net_amount = amount - provider_fee` at `payment_webhook_commands.py:374`.
Netting is arithmetic on money to produce a figure a ledger will use — a
billing decision. The observation reports both numbers and stops.

**5. Source authority and provenance.**

| Field | Meaning |
|---|---|
| `source_system` | the Integrator deployment (facet 1) |
| `source_settlement_key` | the opaque Integrator key (facet 1) |
| `capability_id` | `payments.settlement.observation.v1` |
| `contract_version` | the manifest digest the installation is pinned to |
| `observation_kind` | declared registry code — see facet 6 |
| `provider_status` | the provider's **own** status token, verbatim, unmapped |
| `occurred_at` | when the provider says it happened |
| `observed_at` | when the Integrator verified and recorded it |
| `arrival_mode` | `ingress` \| `poll` \| `operator_verify` |
| `confirmation_evidence` | a declared code naming *how* it was confirmed |
| `receipt_id` | the `inbox_receipts` row, so evidence is retrievable |
| `correlation_ref` | the opaque reference a product may store on its own record |

`provider_status` is carried **verbatim and unmapped** on purpose. The moment a
connector maps `"successful"` to a fleet-wide `succeeded`, it has made the first
financial judgement, and every later disagreement about what the provider meant
becomes unanswerable because the original token is gone. The source maps it
(`payment_webhook_commands.py:213-231`, `status == "successful"` →
`PaymentStatus.succeeded`); the port keeps the raw token **and** supplies a
declared `observation_kind`, so billing can act on the typed field while an
investigator can still read what the provider said.

`confirmation_evidence` is Team 2's field and Team 2's registry. The Integrator
emits exactly one value — `connector_verified`, meaning "the signature over the
raw bytes checked out against the installation's configured signing secret, or
the provider answered a direct authenticated query for this reference." It never
emits `finance_reviewed` or `bank_statement_match`, because those are things
people do, not things a connector observes. **Only an independently confirmed
settlement creates money** is Team 2's rule, and this is the Integrator's honest
contribution to it.

**6. Correction, supersession and reversal.** Never by editing. An observation
is immutable once emitted; a later fact is a **new** observation carrying
`relates_to_settlement_key` and a declared `observation_kind` (ADR-0008 open
registry, seeded):

| `observation_kind` | Reported |
|---|---|
| `capture` | the provider says funds were taken |
| `capture_failed` | the provider says the attempt failed |
| `refund` | the provider says funds were returned |
| `chargeback` | the provider says the payer disputed and funds were pulled |
| `chargeback_reversed` | the provider says the dispute resolved in the merchant's favour |
| `fee_adjustment` | the provider restated its own fee |
| `provider_correction` | the provider restated a prior fact, with `relates_to_settlement_key` naming it |

The connector does **not** decide that a `chargeback` reverses a receivable, or
that a `provider_correction` invalidates an allocation. It reports the kind.
Billing's reversal, deallocation and write-off behaviour is ADR-0020 § 1 and
stays there entirely.

**7. Accepted errors and retry classification.** This is where a payment
connector differs most from a messaging one, and getting it wrong loses money in
both directions.

The engine already refuses to guess: `dispatch.invoke` turns a raising plugin
into `RECONCILIATION_REQUIRED`, **not** retryable, because *"a throw tells us
nothing about whether the effect LANDED."* That reasoning is correct in general
and load-bearing for payments. Extended into a rule:

| Situation | Classification | Why |
|---|---|---|
| Signature verification fails | reject at ingress; **no receipt row**, HTTP 400 | An unverified body is not evidence. § 5.1. |
| Provider 5xx on a **read** (`verify`, `list_transactions`) | `RETRYABLE` | Idempotent; retrying costs nothing |
| Provider 5xx on a **write** (`initialize`, `charge_authorization`, `refund`) | `RECONCILIATION_REQUIRED` | The charge may have landed. The source classifies `>= 500` as retryable at `payment_gateway.py:141-147` **for every action including refunds** — a mandatory port delta, and the highest-severity finding in this spec. |
| Network timeout on a write | `RECONCILIATION_REQUIRED` | Same reasoning, more so |
| Provider 4xx that names a business refusal | `TERMINAL` | Retrying a declined card is the provider's decision to repeat, not the engine's |
| Handler raised, or returned a non-`Outcome` | `RECONCILIATION_REQUIRED` | Already the engine's behaviour |

`reconciliation_required` is a real state on both `inbox_receipts` and
`delivery_attempts` with no `next_attempt_at`, so nothing picks it up
automatically. It is resolved by `payments.reconcile.v1` asking the provider what
actually happened — which is the whole reason that capability exists and is not
an optional extra.

The connector's error codes are **stored, never branched on**
(`models.InboxReceipt.error_code`: *"A CONNECTOR's vocabulary. Stored, never
branched on"*). The source violated the equivalent rule for a different product
and the module records the fix at `execution.claim_receipt`; the same discipline
applies here.

**8. Compatibility.** Additive-optional within `V1`: a new optional field, a new
member of an **open** declared registry (`observation_kind`), a new
`arrival_mode`. A new version is required for: removing or renaming a field;
narrowing a type; changing the meaning of a field; **changing the composition of
`source_settlement_key`**; changing the amount representation; or adding a member
to a closed vocabulary. Consumers reject an unknown version rather than
best-effort parsing it. Version negotiation is an assembly binding, exactly as
`dotmac-integration` binds a capability version.

### 2.2 What the message must not contain — the detector list

Every row is scanned over the connector distribution and over the emitted
message schema, each with a planted-violation sensitivity proof (§ 8).

| Forbidden | Why |
|---|---|
| `invoice_id`, `billing_account_id`, `subscription_id`, `customer_id`, `account_id` | Product identity. The connector may carry an opaque provider-supplied `merchant_reference` string; it may not name a product row. |
| `allocation`, `allocations`, `balance`, `balance_due`, `coverage`, `receivable`, `outstanding`, `net_amount`, `write_off` | Financial meaning. `net_amount` is on this list specifically because the source computes it (`payment_webhook_commands.py:371-375`). |
| Any lifecycle or status field naming a product state (`paid`, `partially_paid`, `settled`, `overdue`) | ADR-0016; the source's `PaymentStatus.succeeded` mapping is the port delta |
| A tenant identifier or a `scope_kind` discriminator | Facet 3 |
| A float, or a `Decimal` without an accompanying currency | Facet 4 |

---

## 3. The payment-intent direction — carried, never owned

Billing owns payment intent as a domain fact (ADR-0020 A3, and Sub already has
`app/services/payment_intent_management.py`). The Integrator carries it to a
provider and reports back. Two messages, both transport.

### 3.1 `PaymentIntentCommandV1` — accepted by the Integrator

Delivered onto `payments.intent.v1`, enqueued through `execution.enqueue_delivery`
and dispatched through the three-phase `prepare → invoke → settle` seam.

| Facet | Specification |
|---|---|
| **Identity + version** | `PaymentIntentCommandV1`. Identity is the caller's `intent_reference` — an opaque string billing mints and owns. The Integrator never mints it, never interprets it, and never parses meaning out of it. |
| **Idempotency** | `delivery_attempts.idempotency_key = intent_reference`, unique per installation. A second enqueue of one reference is one row. Whether the provider call *ran* is `integration.delivery` in the kernel ledger. Deliberately **not** the provider's reference: the source derives its reference as `f"DMAC-{invoice_number}-{suffix}"` (`payment_capability.py:135-137`), embedding a product document number in a provider-visible string. |
| **Scope** | none on the wire (facet 3 of § 2.1). The installation binding carries it. |
| **Money** | `amount` as exact `Money` with a **required** currency. Minor-unit conversion happens inside the plugin, from `Currency.minor_units`. |
| **Payload** | `intent_reference`, `amount`, `currency`, `payer_contact` (an opaque string the product supplies — usually an email), `return_url`, `merchant_reference` (opaque, echoed back on the observation), `mandate_ref` (opaque, for a saved-instrument charge). Nothing else. |
| **Correction** | none. An intent is not amended; a superseding intent is a new `intent_reference`, and the old one is cancelled by the product, at the product. |
| **Errors** | § 2.1 facet 7's table. A write that may have landed is `RECONCILIATION_REQUIRED`, never retried. |
| **Compatibility** | § 2.1 facet 8. |

**`mandate_ref` is where autopay splits.** `dotmac_sub:app/services/autopay.py`
(467 LOC) fuses a mandate lifecycle (one mandate per account, failure counting,
suspension after N failures, customer notification) with a Paystack
`charge_authorization` call and a provider-specific reference-burning trick
(`autopay.py:203-204, 245, 379`). The mandate, the failure policy, the
suspension and the invoice selection are **billing/collections meaning** and
stay in the product. The connector receives one `PaymentIntentCommandV1` per
attempt, carrying an opaque `mandate_ref`, and knows nothing about attempts,
failures or mandates.

### 3.2 `PaymentIntentAcknowledgementV1` — produced by the Integrator

What the provider said about the attempt, and nothing more.

`intent_reference` (echoed), `provider_status` (verbatim), `provider_reference`
(opaque), `redirect_url` if the provider issued one, `public_key_ref` if the
product must complete the flow in a browser, `acknowledged_at`, `receipt_id`.

**It is not a settlement.** An acknowledgement that a charge was *submitted* is
not evidence that money moved; that evidence is a `SettlementObservationV1` with
`observation_kind = capture`. Conflating the two is how a product marks an
invoice paid on a pending checkout, and Team 2's rule — *"a pending checkout, an
uploaded proof, an unverified provider acknowledgement and a UI click each carry
an evidence code that the acceptance policy refuses"* — is exactly right and is
adopted here without amendment.

---

## 4. Delivery, retries and checkpoints — already owned, not re-specified

Everything in this section already exists in `dotmac-integration` and is listed
so nobody builds it again in a plugin.

| Concern | Owner | Where |
|---|---|---|
| Outbound queue and enqueue dedupe | module | `execution.enqueue_delivery`, `delivery_attempts` |
| Worker claim / lease | module | `execution.claim_delivery`, `leased_until` |
| Three-phase dispatch with **no transaction across provider I/O** | module | `dispatch.prepare` / `invoke` / `settle` |
| Conditional settle guarded by the claim | module | `dispatch.settle` — one `UPDATE … WHERE state='in_flight' AND attempt_count=…` |
| Backoff, attempt caps, terminal states | module | `retry.next_state`, `retry.retry_delay_seconds`, `policy.ExecutionPolicy` |
| At-most-once execution | **kernel** | `dotmac_kernel.idempotency.execute_once_platform`, adapted by `idempotency.run_effect_once` |
| Polling cursors with an optimistic lock | module | `execution.advance_checkpoint`, `polling_checkpoints.version` |
| Replay, lease release, health | module | `operations.replay_delivery` / `replay_receipt` / `release_expired_leases` / `health_report` |

ADR-0024 § 7 is explicit that plugins *"do not persist a second delivery ledger
or implement their own retry/checkpoint engine."* The detector is § 8's scan for
`backoff`, `tenacity`, `celery`, `apscheduler`, `while True` retry loops, and any
table, cursor or watermark declared by a connector distribution.

**Reconciliation polling gets a real checkpoint.** Sub's sweep
(`app/services/payment_reconciliation.py`, 811 LOC) selects candidates from its
own `TopupIntent` rows and asks the provider about each — a product-driven sweep
with no provider cursor at all, which is why it cannot detect a settlement for a
payment the product never knew about. The connector's
`payments.reconcile.v1` polling job advances a `polling_checkpoints` row keyed
`(capability_binding_id, job_key)` over the provider's own transaction listing,
so an unmatched settlement is *observed* and handed to billing as an
unattributed fact. Deciding what to do with an unattributed settlement is
billing's, not the connector's.

---

## 5. Ingress

### 5.1 Signature verification

The connector verifies; the module records. `execution.receive_verified`'s
docstring is the contract: *"'Verified' is the caller's assertion that
authenticity was already checked — this function records, it does not
authenticate. Signature verification belongs to the connector that knows the
provider's scheme."*

Order, and it is not negotiable:

1. Resolve `connector_key` + `capability_id` from the route path (§ 5.5).
2. Resolve the enabled installation and binding, and its pinned config revision.
3. Materialize `secret_refs` (§ 6).
4. Call the plugin's ingress `verify(raw_body, headers, config, secrets)`.
5. **Only then** parse JSON.
6. Call `normalize(raw_body, headers) -> tuple[InboundEvent, ...]`.
7. `receive_verified` per event.

Parsing before verifying hands an unauthenticated attacker your JSON parser. The
source gets this right — `await request.body()` before any parse, with an
inline comment explaining why the handler is `async` at all
(`dotmac_sub:app/api/billing.py:1397-1401`) — and the behaviour ports as written.

Comparison is constant-time (`hmac.compare_digest`), as the source does at
`payment_capability.py:373`. A missing or empty configured secret must **fail
closed**: the source's `expected = "" if not secret` combined with
`bool(expected and signature and …)` is correct today, but only by accident of
the truthiness guard, and the port makes it an explicit refusal.

`normalize` returns a **tuple**. One provider POST may carry several events, and
a one-event signature silently drops all but the first.

### 5.2 Raw-payload retention

`inbox_receipts` stores `payload_json`, `headers_json` and `payload_digest`. Two
consequences and one open question:

- The digest is what makes "the same event id with different content" detectable
  rather than assumed away.
- `consequence_json` records what the receipt **caused**, so a replay is
  comparable rather than guessed at.
- **Open:** raw payment payloads carry payer contact details and provider
  metadata. Retention is a product policy under ADR-0014 § 6, and the Integrator
  has no retention job today. § 9, Q3.

The **raw bytes** are not retained beyond verification. Storing the signed body
indefinitely means storing the material an attacker would need to replay against
a future signature-verification bug.

### 5.3 An event that arrives twice

`(capability_binding_id, provider_event_id)` is a database UNIQUE constraint.

| Case | Behaviour |
|---|---|
| Same id, same `payload_digest` | `receive_verified` returns `(existing, False)`. The route returns the recorded `consequence_json` with its recorded HTTP status. No second consequence. |
| Same id, **different** digest | `ProviderEventIdentityCollision` — HTTP 409. The engine refuses to discard the new content on the assumption the provider is well-behaved. Escalation, not retry. |
| Same id, different **binding** | Two receipts, deliberately. The binding determines which capability handles an event, so two bindings observing one upstream event are two consequences; deduplicating at the installation would silently drop one. |

Note what this means with `source_settlement_key = sha256(installation_id ||
provider_event_id)`: a redelivery produces the same key, so billing's own
idempotency sees a replay, not a new settlement. That is the intended chain and
it is why the key is derived rather than random.

### 5.4 An event that arrives out of order

**Nothing reorders it, and nothing buffers it.** Providers do not guarantee
order, a refund webhook can precede its capture webhook, and a connector that
tried to impose order would need a product-shaped notion of what "before" means.

Each observation is an immutable fact with its own identity, its own
`occurred_at` and its own `observation_kind`. Billing accepts facts in any order
and applies its own policy — which is precisely why the append-only,
correction-by-new-fact shape in § 2.1 facet 6 is not stylistic.

The connector may carry an optional opaque `provider_sequence` when the provider
supplies one. It is provenance for an investigator, never an ordering
instruction.

### 5.5 An event for an unknown installation

One provider-agnostic route,
`POST /ingress/{connector_key}/{capability_id}` and its `GET` twin for providers
that use a subscription handshake. The path names a connector key, which is a
**plugin declaration**, not a provider catalogue in the assembly — the assembly
resolves it through discovery and knows nothing about who it is.

| Case | Response | Evidence |
|---|---|---|
| Unknown `connector_key` | 404 | A counter. **No row.** |
| Known key, no enabled installation | 404 | A counter. **No row.** |
| Known installation, capability not bound or binding disabled | 404 | A counter. **No row.** |
| Signature invalid | 400 | A counter, and the failing header name — **never the body, never the signature** |
| Verified | 200/202 | An `inbox_receipts` row |

**No receipt row is written before verification succeeds**, and the schema makes
that structural rather than disciplined: `inbox_receipts.installation_id` and
`capability_binding_id` are both `NOT NULL`, and the module's own note says an
inbound event not routed to a capability *"has nothing that could process it."*
An unverified body from an unknown source is attacker-controlled storage on a
public endpoint; recording it is a denial-of-service primitive, not evidence.

404 rather than 403 for every unknown-installation case is deliberate: a
distinguishable 403 tells an unauthenticated caller which connector keys and
installations exist.

### 5.6 Replay

`operations.replay_receipt` is the only authorized path, and `claim_receipt`
refuses a bare claim on a dead-lettered receipt: *"a dead-letter receipt requires
an authorized replay, not a claim."* Replay writes the declared audit action
`integration.receipt.replayed`, and the recorded `consequence_json` is what the
replayed outcome is compared against.

A replayed settlement observation re-emits with the **same**
`source_settlement_key`, so billing sees a replay rather than a second
settlement. If a replay would produce a different `request_fingerprint`, that is
a conflict for a human, not an automatic supersession.

---

## 6. Secret materialization

ADR-0009's rule is that a secret is **held**, never dereferenced on a resolution
path, and hard rule 20 makes it testable. The Integrator's shape is consistent
with it and is worth stating precisely, because a payment connector is the case
where getting it wrong is most expensive.

1. **Configuration holds references, never values.**
   `connector_config_revisions.secret_refs` is validated at the write by
   `secret_refs.validate_secret_refs`, and `validate_config_revision`
   additionally walks `config_json` and refuses a literal under any
   secret-shaped key name. A revision is immutable and ends up in every backup,
   which is why the check is at the write and not at read.
2. **Materialization happens at exactly one seam.** `dispatch.invoke` calls the
   injected `resolve_secrets(prepared.secret_refs)` and passes the values into
   `DispatchRequest.secrets` for one call. `invoke` takes no `db` **by
   signature** — the boundary is enforced by what a caller cannot pass.
3. **Nothing persists a value.** `DispatchRequest.secrets` is materialized per
   call and discarded. No connector may write one to its own state, and a
   connector has no state to write it to.
4. **The resolver is injected, not implemented.** The module holds pointers; the
   deployment decides how to dereference them. Recognised schemes are
   `bao`, `env`, `file`, `aws-sm`, `gcp-sm`, and adding one is a reviewed diff:
   an unrecognised scheme is indistinguishable from a password containing `://`.
5. **Names are logged; values never.** The ingress and dispatch paths may log
   which reference names were materialized and may not log a value, a prefix, a
   length, or a hash of one.

**Two gaps this spec must not paper over.**

- The `dotmac_integrator` assembly supplies **no** resolver today. Verified: no
  occurrence of `secret` or `resolve_secrets` anywhere in its 733 lines. A
  payment connector cannot be enabled — `lifecycle.enable` demands a live
  `validate_connection(config, secrets)` — until one exists.
- Ingress needs secrets **before** any dispatch exists, and `dispatch.invoke` is
  the only materialization seam the module ships. SPI 1.1's ingress route must
  carry its own materialization call with the same properties: per request, in
  memory, never persisted, never logged.

Sub's existing `app/services/secrets.py` (462 LOC) already resolves
`bao://mount/path#field` with `env://` as an alternative, and Sub already stores
`secret_refs` on its config revision (`app/models/integration_platform.py:165`).
That is the product-first source for the deployment-side resolver. Its
process-lifetime TTL cache (`OPENBAO_CACHE_TTL_SECONDS`) is a port question, not
a port default: ADR-0009's held-not-fetched posture argues for load-once plus an
explicit `refresh`, in the shape `dotmac_kernel.secret_sources` already uses.

---

## 7. Dual-plane (ADR-0023) — what it means here

`dotmac-integration` declares `platform_tables` with an explicitly **empty**
tenant `tables` tuple, the first module in the fleet to do so. On that plane the
`REVOKE ALL` from the tenant app role *is* the isolation, and the online platform
role needs schema `USAGE` plus at least one row DML privilege to be reachable.

Consequences for payments specifically:

- **A connector installation is a platform fact.** It is a statement about the
  fleet's integrations, not about anyone's tenant. Two Sub tenants sharing a
  PSP merchant account share one installation; two tenants with separate
  merchant accounts get two installations with two names
  (`uq_connector_installations_key_name` is on `(connector_key, name)`).
- **An ingress receipt is a platform row, always.** Even when the settlement it
  reports will land in a tenant-plane receivable. The Integrator holds the
  transport evidence; the product holds the money.
- **The vendor control plane is platform-only and takes payments for its own
  deployments.** ADR-0020 A6 gives Vendor CP platform-plane billing with **no
  fake tenant**. That works here without a special case precisely because the
  observation carries no tenant field: the same connector, the same message
  shape, a different destination binding. Sub's installation delivers to a
  tenant-plane billing installation; Vendor CP's delivers to a platform-plane
  one. Neither the plugin nor the module knows the difference, and neither
  should.
- **No FK crosses a plane**, and none could: the Integrator holds no product FK
  at all. `correlation_ref` is an opaque string on the product's own record.

---

## 8. Conformance tests, with sensitivity proofs

ADR-0018: a guard that cannot fail is not a guard, and a check over an empty set
passes for the wrong reason. Every scan below ships a planted-violation proof.
The shape to copy is
`tests/architecture/test_web_conventions.py::test_the_safe_filter_guard_still_bites`.

### 8.1 The SPI and discovery suite

| Test | Plants | Asserts |
|---|---|---|
| `test_a_temporary_connector_distribution_is_discovered` | a `_StaticEntryPoint` in group `dotmac_integration.connectors` (the shipped `conformance.fake_registry` shape) | the plugin is discovered without importing it by name |
| `test_a_duplicate_connector_key_is_refused` | two distributions claiming one key | discovery refuses |
| `test_an_undeclared_capability_cannot_be_bound` | `add_binding` for a capability the manifest omits | `InvalidManifestError` naming the declared set |
| `test_a_duplicate_capability_in_one_manifest_is_refused` | one capability twice | `InvalidManifestError` |
| `test_an_incompatible_spi_range_refuses_at_all_three_points` | a range excluding `CURRENT_SPI_VERSION` | refusal at discovery, at startup **and** at activation |
| `test_a_module_upgrade_refuses_a_previously_activated_binding` | raise `CURRENT_SPI_VERSION` past a stored `installation.spi_range` | activation refuses — the case that actually bites |
| **`test_the_core_contains_no_provider_catalogue`** | — | a token scan over `dotmac_integration/**` and `dotmac_integrator/**` for `paystack`, `flutterwave`, `remita`, `stripe`, `monnify`, `interswitch`, `paypal` finds nothing outside a comment |
| **`test_the_provider_catalogue_scan_bites`** | one provider token in a temporary module inside the scanned tree | the scan **fails** |
| `test_the_scan_covers_every_file` | — | scanned file count equals package file count minus a named, commented exclusion set |

### 8.2 The payment connector suite (per distribution)

| Test | Plants | Asserts |
|---|---|---|
| `test_the_manifest_declares_one_connector_key` | — | exactly one, matching the distribution name |
| `test_no_plugin_branches_on_a_sibling_provider` | a comparison against another provider's name | the scan fails |
| `test_a_tampered_body_fails_verification` | a valid body with one byte changed | `verify` returns `False`; no receipt row exists |
| `test_verification_uses_constant_time_comparison` | — | `hmac.compare_digest` on the comparison path; a plain `==` fails the scan |
| `test_a_missing_signing_secret_fails_closed` | empty `webhook_signing_secret` | refusal, not acceptance |
| `test_the_previous_signing_secret_is_accepted_during_rotation` | a body signed with `webhook_signing_secret_previous` | accepted |
| `test_a_batched_webhook_normalises_to_several_events` | a multi-event body | `normalize` returns a tuple of length > 1 |
| `test_the_observation_carries_no_product_identifier` | — | the emitted schema's field names intersect § 2.2's forbidden list at zero |
| **`test_the_forbidden_field_scan_bites`** | `invoice_id` into the observation schema | the scan **fails** |
| `test_a_provider_5xx_on_a_write_is_reconciliation_required` | a 500 from `refund` | `RECONCILIATION_REQUIRED`, `next_attempt_at is None` |
| `test_a_provider_5xx_on_a_read_is_retryable` | a 500 from `verify` | `RETRYABLE` with a scheduled next attempt |
| `test_a_write_timeout_is_never_auto_retried` | a socket timeout mid-`initialize` | `RECONCILIATION_REQUIRED` |
| `test_every_amount_is_exact` | — | AST scan: no `float(` on a money path, no `Decimal` money without a currency |
| `test_no_currency_default_exists` | a `"NGN"` default in config handling | the scan fails |
| `test_no_second_retry_engine` | — | no `backoff`/`tenacity`/`celery`/`apscheduler` import, no `while True` retry loop, no declared table/cursor/watermark |
| `test_the_plugin_opens_no_product_database` | — | no import of any product package; no SQLAlchemy import |

### 8.3 Replay, idempotency and redaction canaries

| Test | Asserts |
|---|---|
| `test_a_redelivered_event_produces_one_consequence` | second delivery returns the recorded `consequence_json`; one receipt row |
| `test_a_same_id_different_payload_is_a_collision` | `ProviderEventIdentityCollision`, HTTP 409, original content preserved |
| `test_a_replayed_observation_keeps_its_settlement_key` | the re-emitted `source_settlement_key` is byte-identical |
| `test_a_replay_that_changes_the_fingerprint_escalates` | a conflict, not a silent supersession |
| `test_an_unknown_installation_writes_no_row` | 404, receipt count unchanged, no payload persisted |
| `test_an_invalid_signature_writes_no_row` | 400, receipt count unchanged |
| **`test_no_secret_value_reaches_a_log`** | a caplog fixture across the whole ingress and dispatch path; the planted secret string appears nowhere, and neither does a prefix or a length of it |
| **`test_the_redaction_canary_bites`** | a deliberate `logger.info(secrets["api_secret_key"])`; the canary **fails** |
| `test_secrets_are_not_persisted` | after a full dispatch, no materialized value appears in any `mod_intg` row |
| `test_the_plugin_receives_no_session` | `dispatch.invoke`'s signature accepts no `db`, proven by introspection |
| `test_at_most_once_uses_the_kernel_ledger` | the `integration.delivery` scope row exists in the kernel ledger and no second ledger table exists in `mod_intg` |

### 8.4 The product-side ratchets

Two-directional, per ADR-0018 and hard rule 25: the ratchet fails when a count
rises **or** falls without the baseline being lowered in the same change. These
extend `docs/inventories/external-connector-baseline.json` rather than starting a
new baseline, and their targets and current values are in
`docs/inventories/payment-connector-extraction-dossier.md` § "Retirement
inventory".

---

## 9. Where this contract depends on something unresolved

**D1 — `source_system` must not identify the PSP.** Team 2's § 2.2 sets the
billing idempotency key to `f"{source_system}:{source_settlement_key}"` and
simultaneously requires that *"billing never learns which PSP produced it."*
Those hold together only if `source_system` is the Integrator deployment rather
than the provider or the connector key. This spec makes that explicit and mints
`source_settlement_key` opaquely; the source product does the opposite
(`f"{provider.value}-{identity}"`). **Needs Team 2's agreement**, because it
changes what their key composition means.

**D2 — where `tenant_id` comes from.** Team 2's `AcceptSettlementV1` requires
`tenant_id` on the tenant plane. `SettlementObservationV1` carries none (§ 2.1
facet 3). The assembly supplies it from the installation→destination binding,
never from provider metadata. **Needs Team 2's agreement**, and the assembly-side
mapping needs an owner nobody has named.

**D3 — `confirmation_evidence` membership.** The Integrator emits exactly one
code, `connector_verified`. Team 2's registry also has finance-reviewed and
bank-statement-match codes, which no connector can produce. Agreed in substance;
recorded so neither side later assumes the connector can emit them.

**D4 — `provider_status` verbatim.** This spec insists the provider's raw status
token travels alongside the typed `observation_kind`. Team 2's contract does not
mention it. It is additive-optional, so it does not conflict — but if billing
ever branches on it, the boundary has moved and this document is wrong.

**Q1 — SPI 1.1's ingress hook.** Not designed, not released, blocking (§ 0.1).
Its shape is a `dotmac-integration` decision, not a payments one, and the
WhatsApp connector needs it first.

**Q2 — the assembly's secret resolver.** Nobody owns it (§ 6). A payment
connector cannot be enabled without it.

**Q3 — receipt retention.** Raw payment payloads carry payer contact details.
ADR-0014 § 6 makes retention a product policy; the Integrator ships no retention
job and no policy. Open.

**Q4 — the moratorium.** ADR-0017's 2026-08-12 amendment keeps an inbound
payment-provider receiver under the moratorium absent a live blocked adopter,
and `dotmac-integration`'s own dossier names payments as explicitly not the
first cutover (§ 0.2). This document does not claim that gate is met.

**Gates owned elsewhere that this spec does not assume resolved:** Team 2/Team
4's official-artifact relation; Team 1's A2 verdict; ADR-0017 P11. None of them
blocks this contract's *shape*, and all of them sit upstream of anyone building
it.

---

## 10. Recommendation

If and when the gates open, the first payment connector should be
**ingress-only, one provider, observation-only** — `payments.settlement.observation.v1`
alone, with `modes = frozenset({ConnectorMode.INGRESS})`, running in shadow
beside Sub's existing receiver, comparing `provider_event_id` coverage and
normalised field equality until there is zero unexplained drift.

Not because it is easier, but because the outbound direction is where a mistake
takes money from a customer twice, and the ingress direction is where a mistake
merely means a fact arrives late. The engine's own reasoning already says this:
a raising plugin is `RECONCILIATION_REQUIRED` rather than retryable because *"a
throw tells us nothing about whether the effect LANDED."* An observation-only
first connector has no effect to land.
