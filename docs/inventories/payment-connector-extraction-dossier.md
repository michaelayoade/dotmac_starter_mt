# Payment connector plugins — extraction dossier content

**As of:** 2026-08-14
**starter:** working tree on `docs/whatsapp-connector-extraction-dossier`
(`b55c9a5`), carrying the uncommitted ADR-0020 2026-08-14 amendment
**Sub:** `/Users/michaelayoade/Downloads/management/dotmac_sub`, HEAD `27c76aae`
**ERP:** `/Users/michaelayoade/Downloads/management/dotmac_erp`, HEAD at audit
**Vendor CP / CRM:** HEAD at audit
**Integrator assembly:** `/Users/michaelayoade/Downloads/management/dotmac_integrator`, HEAD `d014116`
**Decision:** ADR-0024 §§ 6–7, ADR-0020 amendment A3, under ADR-0023, ADR-0014,
ADR-0009, ADR-0018, ADR-0017
**Evidence base:** `docs/inventories/payment-connector-sources.md`
**Contract spec:** `docs/superpowers/specs/2026-08-14-payment-connector-and-settlement-contracts.md`

## Execution amendment — 2026-08-20

The historical audit below is retained as measured. Its three construction
gates have since changed state:

1. `dotmac-integration` 0.1.0a10 publishes SPI 1.3, including immutable raw
   ingress bytes, headers, exact-byte connector verification, normalization,
   acknowledgement ownership, declared secret bindings and deny-all-capable
   egress declarations. The SPI gap is resolved.
2. `dotmac_integrator` at
   `d886e3c9956192fe1d5f085d352a516812c253c8` owns the assembly secret resolver
   and loads declared material for the connector call without adding a
   provider branch. The missing-owner claim in G2 is resolved.
3. Michael's dated amendment to ADR-0017 authorizes Paystack and Flutterwave
   connector construction. The authority is scoped to separate
   provider-specific adapters and does not claim adoption or cutover.

The first executable package is therefore
`packages/dotmac-connector-paystack/`, whose own `EXTRACTION.toml` supersedes
the illustrative TOML in § 3. It is deliberately INGRESS-only and deny-all on
egress. Publication is supply-chain evidence; Sub remains authoritative until
the recorded shadow and callback cutover retires its direct transport.

Two contract ambiguities are also closed by the as-built assembly boundary.
The Integrator delivery envelope supplies destination, scope, contract,
receipt and idempotency context; the connector neither accepts nor derives a
tenant or source-system identity from provider metadata. Paystack's documented
×100 amount representation for every supported currency, including XOF, is a
provider wire rule. Using that rule in the Paystack adapter is not the product
currency default rejected below: currency remains mandatory on every emitted
money value, and no connector chooses a missing currency.

## Execution amendment — 2026-08-21

`packages/dotmac-connector-flutterwave/` now supersedes the illustrative
Flutterwave TOML in § 4. It targets **Flutterwave API v4 only**. The provider's
current v4 contract authenticates the exact body using HMAC-SHA256 in
`flutterwave-signature` and emits `type`, `webhook_id`, `reference`,
`created_datetime` and `status = succeeded` fields. The plugin has no v3
`verif-hash` or v3 envelope fallback.

Sub remains the product-first source for payment-event normalization and the
first cutover, but its v3 shared-header authentication is a documented
**not-ported security delta**, not a second runtime mode. The old receiver stays
authoritative until a v4 callback is shadowed and cut over. Flutterwave's v4
webhook does not report `app_fee`, so the connector omits `provider_fee`
instead of manufacturing zero; the later reconciliation capability must supply
fee evidence before a product makes a fee-dependent decision.

Every path in the TOML blocks below was confirmed to exist by `ls` / `wc -l`
during this audit.

---

## 1. Why this is a markdown document, and why there is more than one dossier

Repository convention locates a dossier at its package root.
**Historical measurement:** at audit time neither connector package existed.
The dated execution amendments above supersede that construction status; the
three original gates below are retained as review history rather than current
blockers.

1. **ADR-0017's 2026-08-12 amendment** keeps an inbound receiver for
   payment-provider events under the moratorium *"unless a live adopter is
   blocked on it today."*
2. **`packages/dotmac-integration/EXTRACTION.toml`'s `first_cutover`** names an
   ingress-only Meta/WhatsApp capability and says *"deliberately NOT payments…
   Payments would put a money decision behind an unproven delivery ledger on its
   first run."*
3. **SPI 1.0 cannot run an ingress connector at all** — the gap recorded in
   `docs/inventories/whatsapp-connector-sources.md` and restated in the contract
   spec § 0.1. A payment connector's primary mode is ingress.

**And there is no single "payments" package**, because ADR-0024 § 7 makes a
connector distribution the unit of independent release. Sub implements two PSPs
in one class with fourteen `if self.provider ==` branches
(`payment_gateway.py`, verified in `payment-connector-sources.md` § 2.1);
porting that whole would relocate the conditional tree into the Integrator,
which the ADR names as a rejected alternative in terms. So the extraction is a
**split**, and each distribution gets its own dossier. § 3 is the full one; § 4
is the delta.

---

## 2. What is being extracted into what

Three destinations, and the middle column is the one that stops work being done
twice.

### 2.1 Already owned by `dotmac-integration` — claim none of it

Verified against `packages/dotmac-integration/src/dotmac_integration/` at
`b55c9a5`. A plugin that reimplements any of these has violated ADR-0024 § 7's
*"do not persist a second delivery ledger or implement their own
retry/checkpoint engine."*

| Concern | Module owner |
|---|---|
| Plugin discovery from package metadata | `discovery.ENTRY_POINT_GROUP = "dotmac_integration.connectors"` |
| Manifest, SPI range, capability declarations, the three refusals | `spi.py` |
| Installations, immutable config revisions, capability bindings | `models.py`, `lifecycle.py` |
| Secret **references** and the refusal that keeps them references | `secret_refs.py` |
| Binding selection (enabled ≠ selected; fail closed) | `selection.py`, `activation.py` |
| Inbound receipts, dedupe, identity-collision refusal | `execution.receive_verified`, `inbox_receipts` |
| Outbound queue, enqueue dedupe, worker lease, conditional settle | `execution.*`, `dispatch.prepare/invoke/settle`, `delivery_attempts` |
| Backoff, attempt caps, terminal states | `retry.py`, `policy.py` |
| At-most-once execution | **kernel** `dotmac_kernel.idempotency`, adapted by `idempotency.run_effect_once` |
| Polling cursors with an optimistic lock | `execution.advance_checkpoint`, `polling_checkpoints` |
| Replay, lease release, health, audit | `operations.py` |
| Fake-plugin conformance kit | `conformance.py` |

Sub's own generic engine — `installations.py`, `manifest.py`, `registry.py`,
`inbox.py`, `delivery.py`, `runtime.py`, `runtime_execution.py`,
`runner_protocol.py`, `egress_gateway.py`, `egress_policy.py`,
`external_runner.py`, and the seven `integration_*` tables — is already covered
by `packages/dotmac-integration/EXTRACTION.toml` and
`docs/inventories/integration-platform-sources.md`. **This dossier claims none
of it.**

### 2.2 Becomes the connector plugin

| From | Becomes |
|---|---|
| `payment_gateway.PaymentGatewayRunner`'s **Paystack** branches | `dotmac-connector-paystack`'s I/O and wire mapping |
| the same class's **Flutterwave** branches | `dotmac-connector-flutterwave`'s |
| `payment_capability.verify_webhook_signature:359-374` | each plugin's `verify()` under the SPI 1.1 ingress hook |
| `payment_webhook_commands.identify_verified_payment_webhook:137-159` | each plugin's `normalize()` — **minus** the `f"{provider.value}-…"` prefix (contract spec § 2.1 facet 1) |
| `payment_webhook_commands._settlement_observation:189-232`, wire-mapping half only | each plugin's normalisation into `SettlementObservationV1` |
| `payment_gateway_adapter.py`'s verify/refund shaping | each plugin's `payments.reconcile.v1` / `payments.refund.v1` output |
| `registry.py`'s Paystack (`:155-225`) and Flutterwave (`:664-715`) manifests, including secret bindings and the egress hosts | each plugin's `ConnectorManifest` and JSON-schema config contract, published as **package metadata** |
| ERP `paystack_client.py`'s transfers/batch surface | `dotmac-connector-paystack`'s `payments.intent.v1` egress leg |
| ERP `paystack_sync.py`'s paged transaction listing | `dotmac-connector-paystack`'s `payments.reconcile.v1` polling job over a `polling_checkpoints` cursor |

> **Corrected 2026-08-24 (ADR-0061 Amendments A5/A7, ADR-0063).** Three things
> about the transfers/batch row:
> 1. The destination for a transfer is `payments.payout.v1`, not
>    `payments.intent.v1` — a payout and an intent are different business acts
>    (ADR-0061 § 2), and the row above predates that id existing.
> 2. **`batch_transfer_service.py` is not a port source.** It is dead code with
>    a live, ungated, SoD-free path to `PaystackClient.initiate_transfer`, and
>    it is a DELETION (ADR-0061 A7). Its measured disposition — the condition
>    attached to deleting it — is
>    `docs/inventories/treasury-payment-execution-sources.md` § 1.3. The
>    provider-batch shape it embodies is explicitly rejected as an owner:
>    provider calls are not atomic, so a batch-level response is not an answer
>    about any individual transfer (ADR-0063 § 3).
> 3. Every claim in this dossier about ERP payout reads **"Implemented and
>    tested; production enablement unconfirmed."** Neither retirement nor
>    adoption may be asserted from it (A7; `AGENTS.md` rule 30).
>
> Separately, `payments.customer.v1` — which `sync_customers_to_paystack`
> below feeds — is REMOVED from the public capability manifest by ADR-0061 A5:
> it has no independent Dotmac business lifecycle, and a provider-side customer
> is a connector internal or a normalized result of `payments.intent.v1`.

### 2.3 Stays product code, and retires

Everything that decides what money **means**. Named here so nobody ports it by
reflex:

| Stays / retires | Where |
|---|---|
| Allocation, coverage, refund, reversal, posting groups | Sub `payment_provider_events._stage_financial_consequences:599-973`; ERP `payment_service.process_successful_payment:401` and `:495-520` |
| Money admissibility, net-amount arithmetic, invoice targeting | Sub `payment_webhook_commands:343-375` |
| Top-up intent, account-credit deposit, quote-deposit lifecycles | Sub `topup_intents.py`, `account_credit_deposits.py`, `quote_deposits.py` |
| Autopay mandate, failure counting, suspension, invoice selection | Sub `autopay.py:320-443` — only the deterministic reference and the prior-attempt recovery are transport |
| Gateway presentment priority and health-for-display | Sub `payment_routing.py` — the health *probe* is the connector's `validate_connection`; deciding which gateway a customer is shown is product policy |
| RRR source-handler dispatch to AP/payroll/expense | ERP `remita/source_handler.py` |
| GL posting, expense-claim reversal, fee accounts | ERP `payment_service.py:1396,1666,1733`, `expense_posting_adapter.py:1672` |
| Bank-statement auto-reconciliation | ERP `transaction_patterns.py`, `reconciliation_policy_service.py`, `auto_reconciliation_parts/*` — see § 5 |

---

## 3. Dossier content — `dotmac-connector-paystack`

```toml
schema_version = 1
package = "dotmac-connector-paystack"
# The vocabulary has no member for a connector plugin distribution. See § 6, G1
# — this value is the closest ADMISSIBLE one, not an accurate one.
classification = "optional-module"
status = "audit-complete"
source_mode = "product-first"
owner = "Paystack wire transport only: authenticated provider I/O, webhook signature verification over raw bytes, and translation between Paystack's wire format and the provider-neutral payments.* capability contracts"
contract = "Verify a Paystack webhook signature over the exact request bytes; normalise one HTTP body into one or more SettlementObservationV1 facts carrying the provider's own status token verbatim, exact amounts with an explicit currency, and a declared observation_kind; carry a PaymentIntentCommandV1 or a refund instruction to the provider and return its acknowledgement; and page the provider's transaction listing from an engine-owned checkpoint. NOT what a settlement MEANS to a receivable: no allocation, no coverage, no balance, no invoice targeting, no net-amount arithmetic, no product identifier, no tenant, no lifecycle state, no second delivery ledger, no retry or checkpoint engine, and no product database."

# Stateless. A connector plugin owns NO table, NO schema and NO migration
# lineage — every durable fact about a dispatch or a receipt belongs to
# dotmac-integration's mod_intg platform plane (ADR-0023, ADR-0024 §7). Declared
# rather than omitted, so "this distribution persists nothing" is a statement a
# reviewer can see rather than an absence they must infer.
planes = "none"

source_repositories = ["dotmac_sub", "dotmac_erp"]
source_paths = [
  # --- Sub: the ingress half, and the qualifying source for it ---
  # The runner. Only the Paystack branches; the Flutterwave ones go to the
  # sibling distribution (§4). One class, fourteen `if self.provider ==`
  # branches, and porting it whole is the rejected alternative in ADR-0024 §7.
  "dotmac_sub:app/services/integrations/connectors/payment_gateway.py",
  # HMAC-SHA512 over the raw body, compare_digest. The only correct-by-
  # construction payment signature verifier in the fleet; ports as written.
  # NOTE this file is currently inside the `*_capability.py` not_ported glob in
  # packages/dotmac-integration/EXTRACTION.toml — see §6, G3.
  "dotmac_sub:app/services/integrations/payment_capability.py",
  # Verify/refund payload shaping into typed results.
  "dotmac_sub:app/services/payment_gateway_adapter.py",
  # The wire->neutral half only: identify_verified_payment_webhook (:137-159)
  # and _settlement_observation (:189-232). Everything from :263 down is money
  # meaning and stays with the product (§2.3).
  "dotmac_sub:app/services/payment_webhook_commands.py",
  # The route pair and the verify-before-parse ordering, which is correct here
  # and must survive: `await request.body()` precedes any JSON parse.
  "dotmac_sub:app/api/billing.py",
  # Manifest, capability ids, secret binding names and the egress host allowlist
  # -> becomes package metadata. The Paystack entry is :155-225.
  "dotmac_sub:app/services/integrations/registry.py",
  # bao:// / env:// reference resolution -> the DEPLOYMENT-side resolver the
  # dotmac_integrator assembly is missing entirely (§6, G2). Not plugin code.
  "dotmac_sub:app/services/secrets.py",

  # --- ERP: the egress and polling half, which Sub does not have ---
  # 1164 LOC, Paystack-only, with transfers and account resolution Sub lacks.
  # The second independent Paystack client in the fleet.
  "dotmac_erp:app/services/finance/payments/paystack_client.py",
  # HMAC-SHA512 verification with an explicit missing-secret raise (:399-407) —
  # the fail-closed behaviour Sub only achieves through a truthiness guard.
  "dotmac_erp:app/services/finance/payments/webhook_service.py",
  # Paged collections/transfers/settlements listing -> payments.reconcile.v1
  # polling. Sub has no provider cursor at all.
  "dotmac_erp:app/services/finance/payments/paystack_sync.py",
  # Batch transfer shaping.
  "dotmac_erp:app/services/finance/payments/batch_transfer_service.py",
  # The webhook route, and get_paystack_config at :241-276 — which is ALSO where
  # the two-secret ambiguity lives (§6, G4).
  "dotmac_erp:app/api/finance/payments.py",
  # The receipt ledger and its two-layer dedupe (pre-check + IntegrityError).
  # Requirement input: the module already owns this shape.
  "dotmac_erp:app/models/finance/payments/payment_webhook.py",
]
preserved_tests = [
  # Sub — the ingress proofs.
  "dotmac_sub:tests/test_payment_webhook_settlement.py",
  "dotmac_sub:tests/test_api_billing_webhooks.py",
  "dotmac_sub:tests/test_integration_payment_capability.py",
  "dotmac_sub:tests/test_payment_gateway_refunds.py",
  "dotmac_sub:tests/test_payment_webhook_public.py",
  "dotmac_sub:tests/test_paystack_cutover_reconcile.py",
  # Sub — the boundary proofs. test_payment_webhook_ownership.py:99-119 is the
  # exact shape the connector guard needs, applied to the other side.
  "dotmac_sub:tests/architecture/test_payment_webhook_ownership.py",
  "dotmac_sub:tests/architecture/test_payment_gateway_control_plane.py",
  # ERP — the egress and reconcile proofs Sub cannot supply.
  "dotmac_erp:tests/finance/test_paystack_payments.py",
  "dotmac_erp:tests/services/test_paystack_customer_sync.py",
  # ERP — secret handling. Both are requirement inputs rather than ports: the
  # new contract stores references, so the behaviour they prove about encrypted
  # VALUES cannot occur. Carried for the exposure assertions.
  "dotmac_erp:tests/test_settings_secret_exposure.py",
]

# Empty. `status = "audit-complete"` is the exact evidence level for zero
# contract consumers, and claiming otherwise would fail the gate's own arithmetic.
contract_consumers = []
# The thin assembly INSTALLS the plugin; Sub and ERP are the SOURCES that must
# retire their clients. Neither composes this distribution.
candidate_consumers = ["dotmac_integrator", "dotmac_sub", "dotmac_erp"]
inventory_evidence = [
  "docs/inventories/payment-connector-sources.md",
  "docs/inventories/external-connector-sources.md",
  "docs/inventories/integration-platform-sources.md",
  "docs/adr/0024-apps-compose-by-synchronizing-data.md",
  "docs/adr/0020-billing-owns-operational-receivables.md",
]

# Named so nobody ports them by reflex.
#
#   _stage_financial_consequences   allocation, coverage, refund, reversal,
#                                   posting groups. dotmac-billing's (ADR-0020).
#   _resolve_topup_intent           product row resolution from provider
#   _stage_*                        metadata. Stays with the product.
#   autopay mandate/failure/suspend billing + collections meaning.
#   payment_routing presentment     product policy about what a customer sees.
#   remita/*                        a different provider AND a different
#                                   capability shape (§5).
#   mono_*                          open banking, not payments (§5).
#   transaction_patterns.py         ERP's own bank-statement reconciliation.
not_ported = [
  "payment_provider_events.py",
  "payment_reconciliation.py",
  "topup_intents.py",
  "account_credit_deposits.py",
  "quote_deposits.py",
  "payment_routing.py",
  "remita/*",
  "mono_*",
  "transaction_patterns.py",
]

composition_boundary = "ADR-0024 §7: an independently released distribution discovered through the `dotmac_integration.connectors` entry-point group. It is NOT part of dotmac-integration core, and core gains no knowledge of it — no provider enum, no import list, no `if provider == ...` branch in the module or in the thin assembly, enforced by a token scan with a planted-violation sensitivity proof. The plugin declares a stable connector_key, an SPI range, its capability contract versions, a JSON-schema config contract carrying secret REFERENCES only, its supported modes, and factory entry points for exactly its declared capabilities. It imports no product package, opens no product database, persists nothing, and receives no Session — `dispatch.invoke` takes no `db` BY SIGNATURE. One installation binds one capability once; duplicate or SPI-incompatible bindings fail closed at discovery, at startup and at activation."

first_cutover = "NOT YET PERMITTED, and this dossier does not claim otherwise. Three gates stand in front of it: ADR-0017's 2026-08-12 amendment keeps an inbound payment-provider receiver under the moratorium absent a live blocked adopter; packages/dotmac-integration/EXTRACTION.toml names an ingress-only Meta/WhatsApp capability as the first cutover and payments as explicitly NOT it; and SPI 1.0 declares ConnectorMode.INGRESS but offers no seam to verify a signature over raw bytes. WHEN those clear, the first cutover is deliberately the narrowest possible slice: ingress-only, Paystack only, `payments.settlement.observation.v1` alone, modes = frozenset({INGRESS}), against Sub — not ERP, because Sub's receiver is already a thin adapter with an architecture test holding it to transport-only, so the shadow comparison has a clean control. The egress capabilities (payments.intent.v1, payments.refund.v1) and ERP come strictly after, because the outbound direction is where a mistake takes money from a customer twice while the ingress direction only makes a fact arrive late."

shadow_and_drift = "Paystack delivers to BOTH the Integrator and Sub's existing /api/v1/payment-events/paystack route. The Integrator verifies, records an inbox_receipts row and emits SettlementObservationV1 to a comparison sink ONLY; Sub's own path remains the sole producer of every financial consequence, unchanged. Compared, per event: provider_event_id coverage (every event Sub saw, the Integrator saw), signature verdict equality, payload_digest equality, and field-level equality of the normalised observation against Sub's _SettlementObservation (status token, exact amount, exact provider_fee, currency, reference) — deliberately NOT against Sub's net_amount, which is meaning and must not exist on the Integrator side. Zero unexplained drift over a full billing cycle including at least one refund and one redelivery is the bar. Only then is one binding activated; only then is Sub's route, verifier, credential reference, connector task and gateway client removed — and the external-connector ratchet baseline is lowered in the SAME change, so the retirement is reviewable as a diff rather than asserted. A drift class that must NOT be waived: a settlement the Integrator observed and Sub did not. Sub's reconciliation sweep selects candidates from its own TopupIntent rows and structurally cannot see a settlement for a payment it never knew about, so this class is expected to be non-empty and is the capability gain, not a defect."

local_copy_retirement = "Sub retires app/services/integrations/connectors/payment_gateway.py (the Paystack half), payment_capability.py's verification and provider allow-list, the POST /api/v1/payment-events/paystack route and its handler, the Paystack manifest in registry.py, the Paystack secret binding names, and autopay's direct charge_authorization call — keeping its mandate, failure-count and suspension policy, which were never the connector's. It KEEPS payment_provider_events.py, payment_reconciliation.py and every *_intent service: what a settlement MEANS to Sub is product business policy. ERP retires paystack_client.py, webhook_service.py's verification and dispatch, paystack_sync.py, the POST /payments/webhook/paystack route, get_paystack_config's credential read, and both Celery tasks (sync_paystack_transactions, sync_customers_to_paystack) — keeping payment_service.py's allocation, GL posting and expense-claim lifecycle. Each retirement lowers a two-directional ratchet in the same change (§7). Sub's paystack_reference-shaped columns are neutral already; ERP's are not, and its three payment_intent columns, two payment_webhook columns and one transfer_batch column carry the provider name into the schema — renaming them is a separate expand/contract migration that must NOT be bundled into the cutover."

next_action = "No implementation, no package, no entry point. The immediate next actions are three decisions and one design, none of them payments work: (1) resolve the SPI ingress gap in dotmac-integration and release the next alpha, which the Meta/WhatsApp connector needs first and which this dossier is downstream of; (2) give the dotmac_integrator assembly a secret resolver — it has none, verified across all 733 lines, so lifecycle.enable's live validate_connection(config, secrets) cannot run for any connector, let alone a payment one; (3) Michael to rule on whether a payment connector may proceed at all under ADR-0017's 2026-08-12 amendment, given dotmac-integration's own first_cutover explicitly excludes payments. Independently and in parallel: Team 2's SettlementObservationV1 consumer contract is PROPOSED, and its source_system/tenant_id composition disagrees with this dossier's on two points recorded as D1 and D2 in the contract spec. When and only when those clear, Stage E creates packages/dotmac-connector-paystack/ with this content as its EXTRACTION.toml, in the same change as the entry point and the conformance suite — never reserved ahead of it."
```

---

## 4. Historical dossier delta — `dotmac-connector-flutterwave`

A separate distribution with a separate dossier. This pre-v4 illustration is
superseded by the package-root `EXTRACTION.toml` and the 2026-08-21 amendment;
it remains here to preserve what the source audit actually found.

```toml
package = "dotmac-connector-flutterwave"
owner = "Flutterwave wire transport only: authenticated provider I/O, verif-hash ingress authentication, and translation between Flutterwave's wire format and the provider-neutral payments.* capability contracts"

# Sub is the ONLY source. ERP has 11 Flutterwave hits and all of them are
# chart-of-accounts and bank-name strings — no client, no route, no credential.
source_repositories = ["dotmac_sub"]
source_paths = [
  "dotmac_sub:app/services/integrations/connectors/payment_gateway.py",
  "dotmac_sub:app/services/integrations/payment_capability.py",
  "dotmac_sub:app/services/payment_gateway_adapter.py",
  "dotmac_sub:app/services/payment_webhook_commands.py",
  "dotmac_sub:app/api/billing.py",
  "dotmac_sub:app/services/integrations/registry.py",
]
preserved_tests = [
  "dotmac_sub:tests/test_payment_webhook_settlement.py",
  "dotmac_sub:tests/test_api_billing_webhooks.py",
  "dotmac_sub:tests/test_payment_webhook_public.py",
  "dotmac_sub:tests/test_web_integrations_payment_gateways.py",
  "dotmac_sub:tests/architecture/test_payment_webhook_ownership.py",
]
candidate_consumers = ["dotmac_integrator", "dotmac_sub"]

first_cutover = "AFTER Paystack, not beside it. Same three gates. The reason for the ordering is not effort but evidence: Flutterwave's ingress carries no payload integrity (see the declared property below), so its shadow comparison cannot distinguish a replayed body from a fresh one, and it should run against an engine whose dedupe and reconciliation behaviour is already proven by the Paystack cutover."
```

### 4.1 A declared property, not a defect

Flutterwave authenticates ingress with a **static shared secret** in the
`verif-hash` header — `payment_capability.py:370-371` — compared with
`hmac.compare_digest`. That is Flutterwave's own scheme, not a Sub defect. Its
consequence for the connector contract is concrete and must be **declared on the
manifest, not discovered**: the header is a bearer secret with no binding to the
payload, so a captured value replays against any body.

For such a provider, integrity is carried by two mechanisms the engine already
owns:

- `inbox_receipts`' `(capability_binding_id, provider_event_id)` unique
  constraint plus the `payload_digest` collision refusal — the same id with
  different content raises rather than deduplicating;
- an independent `payments.reconcile.v1` re-verification of any observation that
  will create money.

Neither is optional for this connector. That is the difference between a
declared property and an assumption.

### 4.2 Two port deltas specific to this connector

Both verified in `payment_gateway.py`:

- `float(Decimal(str(params["amount"])))` at `:331` and `:371` — a float amount
  on the wire. The plugin holds `Decimal` internally and converts once, at the
  last line before the request, with an explicit rounding rule.
- `config.get("default_currency") or "NGN"` at `:333` — a hardcoded currency
  fallback three layers into a default chain. Replaced by a **required**
  `currency` and a refusal when it is absent.

---

## 5. Explicitly out of scope, and why

**Remita is not in this dossier.** ERP runs it (10 files, `client.py` 370 LOC,
`rrr_service.py` 457), and it is a genuine Integrator connector under ADR-0024
§ 6 — but it is a **different capability shape**. It generates a government
Remita Retrieval Reference and has **no webhook at all**: status arrives by
`check_status` polling (`rrr_service.py:181`) or by a human clicking
`POST /finance/remita/{rrr_id}/mark-paid` (`web/finance/remita.py:289`). Its
first-class contract is `payments.reconcile.v1` with no ingress, and its
`RRRStatus` lifecycle is an ERP domain vocabulary, not a settlement observation.
Folding it into a PSP dossier would blur exactly the boundary this document
exists to draw. It needs its own dossier, after Paystack, and its
process-global credentials (`app/config.py:184-188`, § 6 G5) are a prerequisite
to fix rather than to port.

**Mono is not in this dossier and is not a payment connector.** ERP's
`mono_client.py` (660 LOC) + `mono_sync.py` (1,648 LOC) ingest **bank
statements** — an open-banking account-aggregation capability that feeds
reconciliation. It moves no money and issues no settlement. It belongs in the
Integrator under a `banking.*` capability family with its own dossier.
Recorded here only so it is not swept into "payments" by the word "webhook".

**CRM is deleted, not ported** — the standing rule in
`packages/dotmac-integration/EXTRACTION.toml`. It has no PSP transport to port
anyway; its one money decision trusts a client-supplied `deposit_reference`
(`payment-connector-sources.md` § 6B) and is a requirement input for the
`confirmation_evidence` field, not a source.

**Vendor CP has nothing to retire** — zero in every category. It is a future
consumer, and the cleanest possible adopter.

---

## 6. Gate mismatches this dossier cannot fix itself

**G1 — the classification vocabulary has no member for a connector plugin.**
`tests/architecture/test_product_first_extraction.py:53-57` admits exactly
`universal-facility`, `presentation-foundation`, `optional-module`. A connector
plugin distribution is none of them: it is stateless, owns no schema, no
migration lineage and no tables, and is discovered rather than composed. The
TOML above uses `optional-module` because it is the only admissible value, and
that is a lie the gate currently forces. A `connector-plugin` member is needed,
owned by whoever owns that test. **Reported, not edited.**

**G2 — the `dotmac_integrator` assembly has no secret resolver.** Verified: no
occurrence of `secret` or `resolve_secrets` anywhere in its 733 lines.
`dispatch.invoke` requires an injected `SecretResolver`, and `lifecycle.enable`
gates enablement on a live `validate_connection(config, secrets)`. No connector
can be enabled until this exists. Sub's `app/services/secrets.py` (462 LOC) is
the product-first source; whether its TTL cache survives ADR-0009's
held-not-fetched posture is an open design question, not a defect.

**G3 — `packages/dotmac-integration/EXTRACTION.toml`'s `not_ported` glob
`*_capability.py` would strand signature verification in Sub forever.** Its
rationale is right for what it describes; `payment_capability.py` is not that
file. Its 374 lines are a transport facade holding Sub's only payment webhook
verifier (`:359-374`), the provider allow-list (`:53-57`) and an
`if provider_type == "paystack"` branch (`:363`). This dossier records it as
**split** — transport half ports, binding-resolution half is superseded by the
module's own `dispatch`/`selection`. That file belongs to another owner;
**reported, not edited.**

**G4 — ERP's Paystack webhook secret is ambiguous.**
`app/api/finance/payments.py:276` sets `webhook_secret=str(secret_key)` — the
API secret key — while a separate `paystack_webhook_secret` setting exists
(`settings_spec.py:526`) and *is* used by the Celery poll path
(`tasks/expense.py:876`). Two paths can authenticate against different secrets.
The extraction inherits this the moment it reads ERP credentials, and it must be
resolved in ERP before, not during, a cutover.

**G5 — Remita's credentials are process-global env vars in a multi-tenant ERP**
(`app/config.py:184-188`), where every Paystack and Mono key is per-organization.
One organization's Remita merchant identity is every organization's. Recorded
here because the Remita dossier cannot start until it is decided.

**G6 — ERP's `retry_failed_webhook` re-executes money handlers from a stored
payload with no signature re-verification** (`webhook_service.py:454-500`).
Contrast `dotmac_integration.operations.replay_receipt`, which is authorized,
audited (`integration.receipt.replayed`) and operates on an already-verified
receipt, while `claim_receipt` refuses a bare claim on a dead-lettered row. This
is an ERP defect, and the module's shape is the fix — but it must not be ported.

**G7 — Team 2's consumer contract is PROPOSED and disagrees on two points.**
`source_system` must not identify the PSP, and `SettlementObservationV1` carries
no `tenant_id`. Both are argued in the contract spec § 9 (D1, D2). Neither team
may treat the other as settled.

**Gates owned elsewhere, not assumed resolved:** Team 2/Team 4's
official-artifact relation; Team 1's A2 verdict; ADR-0017 P11.

---

## 7. Retirement inventory — the two-directional ratchets

Per ADR-0018 and hard rule 25: a ratchet fails when the count **rises** or
**falls without the baseline being lowered in the same change**, is kept distinct
from any per-line "reviewed and correct" marker, and carries a sensitivity proof
that the detector still fires. A retirement that does not lower the baseline in
its own diff is an assertion, not a retirement.

### 7.1 What is being ratcheted, and to what

| # | Category | Target | Retires when |
|---|---|---|---|
| R1 | **Direct PSP client files** | 0 | the connector's capability is bound and the shadow shows zero drift |
| R2 | **Provider-named webhook routes** | 0 | ingress moves to `POST /ingress/{connector_key}/{capability_id}` |
| R3 | **Payment signature verifiers in a product runtime** | 0 | verification moves into the plugin's `verify()` |
| R4 | **PSP credential references in a product runtime** | 0 | the reference moves to `connector_config_revisions.secret_refs` |
| R5 | **Provider-named Celery tasks** | 0 | polling moves to an engine-owned `polling_checkpoints` job |
| R6 | **Provider-named persisted identifiers** (enum members, columns, table names) | 0 | a separate expand/contract migration, **never bundled into a cutover** |
| R7 | **Provider names in product business logic** (literals, regexes, membership tests) | 0 | the capability that needed them is bound |

### 7.2 The seed measurement

**These are raw greps, not a detector.** They are recorded so the executable
baseline has a starting point and so nobody claims a reduction that was really a
`.gitignore` change. Case-insensitive, running code only (`app/`), including
comments and docstrings:

| Repo | `.py` files under `app/` naming a real PSP | Breakdown |
|---|---|---|
| `dotmac_sub` | **33** | `paystack` \| `flutterwave` |
| `dotmac_erp` | **70** | 52 `paystack`, 13 `remita`, plus the `mono_*` family |
| `dotmac_crm` | **1** | one docstring; separately, 3 files name `stripe` as an enum/label only |
| `dotmac_vendor_control_plane` | **0** | — |

Outside `app/`: Sub has 54 test files and 6 scripts naming a PSP; ERP has 15+
one-off tools and archived scripts. **One-off scripts are a distinct retirement
class** and must not be counted in the same ratchet as running code — a script
that ran once during a 2026 cutover is history, not debt.

### 7.3 The enumerated targets

Verified individually in `payment-connector-sources.md` §§ 6.5 and 8.

**R1 — client files.** Sub: `connectors/payment_gateway.py`,
`payment_gateway_adapter.py`. ERP: `paystack_client.py`,
`batch_transfer_service.py`, `paystack_sync.py`, `paystack_customer_sync.py`,
plus `remita/client.py` and `mono_client.py` under their own dossiers.

**R2 — routes.** Sub: `app/api/billing.py:1394`, `:1415`; the constant at
`operational_checks.py:44`. ERP: `api/finance/payments.py:734`,
`api/finance/banking.py:945`, four `*-mono` account routes at
`api/finance/banking.py:841,868,893,916`, `web/admin.py:1665,1680`, and the
`/finance/remita/*` family.

**R3 — verifiers.** Sub: `payment_capability.py:359-374`. ERP:
`paystack_client.py:385-421`, `mono_client.py:637-660`.

**R4 — credentials.** Sub: two secret binding names per provider in
`registry.py:217-220` and `:705-709`, plus
`web_integrations_payment_gateways.py:79-83`. ERP: nine `paystack_*` and four
`mono_*` specs in `settings_spec.py:504-603`, and three `REMITA_*` env vars in
`config.py:184-188`.

**R5 — tasks.** Sub is already **clean** — `app.tasks.autopay.charge_due_invoices`
and `app.tasks.payment_reconciliation.reconcile_topups` carry no provider name.
ERP is not: `app.tasks.finance.sync_paystack_transactions`
(`tasks/finance.py:1305`, every 1800s per `settings_seed.py:590-591`) and
`sync_customers_to_paystack` (`tasks/payments_sync.py:28`).

**R6 — persisted identifiers, the expensive ones.**

| Repo | Identifier | Location |
|---|---|---|
| Sub | `PaymentProviderType.{stripe,paypal,paystack,flutterwave}` — a DB `Enum` column with 34 references across 12 modules | `models/billing.py:148-151`, column `:2372` |
| Sub | `ConnectorType.stripe` | `models/connector.py:28` |
| Sub | `PaymentWebhookProvider.{PAYSTACK,FLUTTERWAVE}` | `payment_webhook_commands.py:60-61` |
| ERP | `payment_intent.paystack_{reference,access_code,transaction_id}` | `models/finance/payments/payment_intent.py:64,70,174` |
| ERP | `payment_webhook.paystack_{event_id,reference}` (both indexed, one unique) | `models/finance/payments/payment_webhook.py:58,64` |
| ERP | `transfer_batch.paystack_batch_reference` | `models/finance/payments/transfer_batch.py:177` |
| ERP | `bank_account.mono_*` — eight columns | `models/finance/banking/bank_account.py:164-191` |
| ERP | table `payments.remita_rrr`, enum `RRRStatus`, three constraints/indexes | `models/finance/remita/rrr.py:34,53,55-57` |
| CRM | `ConnectorType.stripe`, persisted as a PG enum value | `models/connector.py:18`; DDL `af8fbbefa221_initial_schema.py:63` |

**R6 is the one that must not be bundled.** Every other ratchet is a code
deletion in the cutover diff. R6 is an expand/contract migration with a data
backfill, and doing it inside a money-write cutover window would mean two
irreversible things happening at once.

**R7 — business-logic literals.** Seventeen Sub files, each individually
verified to exist, are enumerated in `payment-connector-sources.md` §§ 8.1–8.4
(the balance of the raw 33 is transport code already covered by R1–R4, plus
comments). The two worst are
`sales/quote_delivery.py:348,367`, which writes `("quote_payment_mode",
"paystack")` into durable quote metadata, and `sales/quote_documents.py:634-635,
666`, which emits a `.paystack-copy` CSS class into a **generated PDF**. ERP's
worst is `transaction_patterns.py` (37 LOC, entirely provider regexes) feeding
`reconciliation_policy_service.py:43` and `auto_reconciliation_parts/base.py:93,95`.

### 7.4 The detector, and its sensitivity proof

None of the above is measurable yet. The executable baseline needs a script in
the shape of `scripts/external_connector_sweep.py` plus a frozen JSON baseline
in the shape of `docs/inventories/external-connector-baseline.json`, and the
detector must state what it deliberately does **not** see — the discipline
`external-connector-sources.md` § "What each detector sees" already applies.

Two things it must get right, both learned from the existing sweep:

- **It must not inherit `external-connector-baseline.json`.** That baseline's
  `webhook_surface` detector counts a path containing
  `webhook`/`callback`/`/hooks`/`ipn`. Sub's payment routes are at
  `/payment-events/…` and ERP's Mono account routes at `/accounts/{id}/link-mono`
  — neither matches. The payment ratchet needs its own baseline.
- **The sensitivity proof plants a provider name and asserts the count rises.**
  A scan whose path glob or file-extension filter had drifted would report a
  falling count and read as progress. That is the exact failure ADR-0018's
  two-directional rule exists to catch, and a payment ratchet is the worst place
  to discover it.
