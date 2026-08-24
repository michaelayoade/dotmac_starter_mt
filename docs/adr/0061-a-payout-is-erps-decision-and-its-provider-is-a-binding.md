# ADR-0061: A payout is ERP's decision and its provider is a binding

> **Number allocation, 2026-08-24.** `0059` and `0060` are allocated on
> sibling branches that have not merged. This record takes `0061` rather than
> risk the ADR-0032 and ADR-0010/0011 collisions again; under the rule Michael
> applied there, the earlier record keeps the number, so a colliding new record
> would have had to move anyway.

- Status: Accepted
- Date: 2026-08-24
- Deciders: Michael
- Supersedes: none
- Extends: ADR-0024 § 4 (shared behavior contains no product or provider
  switch), § 6 (the Integrator is the sole external connector control plane),
  § 7 (the connector-plugin SPI), and its 2026-08-24 amendment
- Related: ADR-0042 § 3 (payment obligations are not payment instructions —
  this record names the Treasury/payment owner that ADR-0042 left unnamed),
  ADR-0014 (at-most-once execution has one owner), ADR-0016 (payment coverage
  is derived, not a status), ADR-0017 (adoption is the scarce resource),
  ADR-0062 (modules own metric definitions; assemblies own exporters),
  `docs/inventories/payment-connector-sources.md`,
  `docs/inventories/payment-connector-extraction-dossier.md`

## Context

Five outbound connector distributions now exist —
`dotmac-connector-paystack`, `-flutterwave`, `-remita`, `-whatsapp` and
`-meta-social` — and the payments ones move money. Money-moving outbound is the
first place where the difference between *a decision* and *a transport* stops
being an architectural nicety: a retry that a transport decided to make is a
second payment.

ADR-0042 § 3 already separated the two on the liabilities side ("Payables says
what is owed… A Treasury/payment owner performs disbursement") but deliberately
did not name the disbursement owner. ADR-0024 § 4 requires that owner to be
named in an accepted contract or ADR, because configuration may select how an
authorized adapter is reached and may never select who decides. That name is
missing today, and the gap is being filled implicitly by whichever connector
happens to carry a payout surface.

At the same time the strategic reason for the whole Integrator layering is
provider substitutability. It is only real if it can be stated as a test:
**can ERP move payout traffic from Paystack to Flutterwave without an ERP
release?** Today it cannot, for reasons § 4 records exactly.

## Decision

### 1. ERP owns the payout decision

Whether a payout happens, to whom, for how much, in which currency, on which
schedule, and whether an ambiguous attempt may be tried again are **ERP's**
decisions, made by ERP's Treasury/payment owner. This is the owner ADR-0042 § 3
refers to.

No connector, no `dotmac-integration` code path, no `dotmac_integrator`
configuration and no operator gesture inside the Integrator decides any of
them. Concretely, an Integrator artifact may not:

- originate a payout that ERP did not command;
- change a payout's amount, currency, destination or beneficiary;
- decide that an `AMBIGUOUS` / `RECONCILIATION_REQUIRED` attempt should be sent
  again — it may only present the evidence to ERP and carry ERP's next command
  (`dotmac_integration.outbound_repair.classify_repair` is deliberately a
  *classification*, and its refusal to replay an ambiguous money attempt is
  this rule, expressed in code); or
- suppress, batch, split or reorder payouts as a delivery optimisation.

The Integrator owns at-most-once delivery, retry curve, dead-letter,
quarantine, checkpoints and evidence. Those are transport properties of a
command that has already been decided.

### 2. `payments.payout.v1` is ONE provider-neutral contract

There is exactly one payout capability contract in the fleet. It is
`payments.payout.v1`. It is not a Paystack contract, and a second provider does
not get a variant of it.

Forbidden, explicitly:

- a provider-named capability id (`payments.payout.paystack.v1`);
- a provider-shaped sibling id that means the same business act under another
  provider's vocabulary (`payments.transfer.v1`, `payments.payout.batch.v1`) —
  a provider calling a payout a "transfer" is wire vocabulary, not a second
  capability;
- a per-provider command payload dialect behind the one id (see § 4); and
- a capability version bumped because one provider changed its API. A `vN`
  bump is a change to what ERP means, never to what a provider exposes.

A genuinely different business act — a *batch* payout that ERP decides as one
unit, with its own partial-failure semantics — may become its own contract, but
only once ERP owns that act. It is not created because a provider ships a batch
endpoint.

### 3. Paystack and Flutterwave v4 are interchangeable bindings behind it

This is the strategic endpoint, and it is stated as an obligation rather than
an aspiration:

> **ERP must be able to move payout traffic between Paystack and Flutterwave v4
> by changing a capability binding in the Integrator — with no ERP code
> release, no ERP configuration branch, and no provider name appearing anywhere
> in ERP.**

What that requires, and what each artifact owes:

| Artifact | Owes |
|---|---|
| ERP | one command shape for `payments.payout.v1`, provider-free, carrying the business facts of a payout and nothing about a wire |
| `dotmac-integration` | one active binding per `(installation, capability)`; a binding swap that is an operator gesture over data, never a deployment of new engine code |
| each payment connector | acceptance of that one command shape, translation to its own wire, and typed outcomes — including the outcomes that received no answer |
| `dotmac_integrator` | the installation, credentials and binding; the only place a provider is named |

A swap that requires ERP to change a field name, add a field, remove a field or
learn a new action verb is not a swap. It is a provider branch with extra
steps, and § 4 of ADR-0024's 2026-08-24 amendment forbids it.

`messaging.send.v1` is the shape to imitate at the id level — two connectors
(`meta_whatsapp`, `meta_social`) already declare the same outbound id rather
than minting one each — and the shape NOT to imitate at the payload level, for
the reason recorded in § 4.

### 4. Known divergence at acceptance — what the shipped code does instead

This decision does not describe today's code. Recorded precisely so the record
is actionable, and so nobody cites it as evidence of compliance.

**D1 — payout has exactly one binding, so it is not yet swappable.**
`payments.payout.v1` is declared only by `dotmac-connector-paystack`
(`delivery.PAYMENT_PAYOUT_CAPABILITY`, actions `resolve_bank_account`,
`create_transfer_recipient`, `initiate_transfer`).
`dotmac-connector-flutterwave` declares only `payments.intent.v1` and
`payments.refund.v1` (`outbound.py`; `plugin.handler_for` refuses anything
else) and withholds transfers deliberately. Interchangeability with one
implementation is a claim, not a property.

**D2 — the withheld Flutterwave capability was named per provider, not per
contract.** `packages/dotmac-connector-flutterwave/EXTRACTION.toml` withheld
`payments.transfer.v1` and `payments.payout.batch.v1`. Both are § 2 breaches
minted in a dossier: the act ERP would command is `payments.payout.v1`, and
Flutterwave's word for it is wire vocabulary. Corrected in that dossier in the
same change as this ADR, with the previous entry preserved in a comment.

**D3 — the two payment connectors take different command payloads behind
contracts that are supposed to be one.** This is the hard one, and neither side
is careless:

| | `dotmac-connector-paystack` | `dotmac-connector-flutterwave` / `-remita` |
|---|---|---|
| envelope | `{"action": <verb>, "params": {…}}`, verb checked against `ACTIONS_BY_CAPABILITY` | flat, typed per capability; no action verb |
| provider reference | **derived** — `operations.provider_reference` is `dmi` + SHA-256 of `DispatchRequest.idempotency_key`, and reading one from the payload is refused outright | **product-minted** — Flutterwave requires `intent_reference`; Remita requires the product's `orderId` verbatim and mints none |
| minor units | product sends an exact MAJOR-unit decimal string plus `currency`; the connector applies `PAYSTACK_WIRE_SCALE = 2` itself and accepts no `currency_minor_units` | product MUST send `currency_minor_units`; absence is `currency_minor_units_required` |
| provider idempotency | the derived value in the provider's own `reference` field | Flutterwave: engine key on `X-Idempotency-Key`; Remita: none available, `orderId` is the only natural key |

Both refusals are defensible on their own terms. Paystack's wire multiplies by
100 for **every** supported currency including zero-exponent XOF, so an exponent
supplied by the product would be a second, contradictory authority on that
provider's scale; and deriving the reference from the engine key is what makes
every attempt of one delivery present an identical, provider-refusable key.
Flutterwave and Remita cannot derive, because their natural key is the
product's own and their exponent is genuinely currency-dependent.

The conclusion is not that one connector is wrong. It is that
**`payments.payout.v1` has no declared command payload**, so each connector
settled the question locally and correctly for itself, and the capability id is
currently a name rather than a contract. § 5 says who closes that.

**D4 — the same gap already bit `messaging.send.v1`.** Both messaging
connectors declare the id and then diverge under it:
`meta_whatsapp` accepts `send_text | send_template | send_media` with a
`recipient` param; `meta_social` accepts `send_direct_message |
reply_to_comment` with `recipient_id` + `channel`. A product bound to one
cannot be re-bound to the other without changing its command. The payments
divergence is therefore a pattern, not an accident.

**D5 — the SPI has no place to put a command contract.**
`dotmac_integration.spi.CapabilityDeclaration` carries `capability_id`,
`config_schema` and `modes`; `DispatchRequest.payload` is
`dict[str, object]`, unvalidated. Configuration has a declared schema and
commands do not, which is exactly why the divergence was invisible to every
existing gate.

### 5. What must be true before payout interchangeability may be claimed

In order. Each is a reviewable diff, not a status change:

1. **The command contract exists.** `payments.payout.v1` gets a declared,
   versioned command payload schema — provider-neutral field names, exact
   decimal amount, explicit currency, product-minted stable reference,
   beneficiary as opaque references — owned by the declaring side and
   validated by the engine before dispatch, not by each connector after.
2. **Every payment connector accepts it.** Paystack keeps deriving its provider
   reference (that behaviour is correct and stays) but from the contract's
   stable reference plus the engine key rather than from an `action`/`params`
   dialect; a connector that cannot use `currency_minor_units` ignores it
   rather than refusing it, and states in its dossier why its wire scale is
   provider protocol.
3. **Flutterwave implements `payments.payout.v1`** — the same id, the same
   payload — at the point ERP asks for it, and not before (ADR-0062's
   companion rule in ADR-0024's 2026-08-24 amendment § 9).
4. **ERP is proven provider-free** for payouts: no provider name in a route,
   task, setting, column, enum or branch, ratcheted to zero by the
   external-connector ratchet in the same change as the cutover.
5. **A binding swap is exercised**, in shadow, with both connectors installed
   and one enabled, and the evidence recorded. Until step 5 exists, the honest
   claim is "one payout binding", not "interchangeable providers".

## Consequences

- ADR-0042 § 3's unnamed Treasury/payment owner is now named for payouts, and
  Payables' obligation projection is fed by ERP's disbursement decision through
  a typed settlement observation as that ADR already requires.
- `payments.payout.v1` is a fleet vocabulary item under ADR-0008 rules: one
  declaring owner, no per-provider minting, and a new id needs a reason that
  names a business act rather than an endpoint.
- The Integrator's refusal to replay an ambiguous money attempt is no longer an
  implementation choice inside `outbound_repair`; it is a consequence of § 1
  and may not be relaxed by configuration.
- Work is created, and it is named in § 5 rather than implied. The largest item
  — a declared command payload on the SPI — is a `dotmac-integration` minor
  version and a coordinated connector release wave, not a doc change.

## Alternatives rejected

**Let each connector define its own command shape behind a shared id.** This is
today's state (§ 4 D3/D4). It makes the capability id a label, moves the
provider branch into the product that has to construct the payload, and makes a
binding swap a product release — losing the entire benefit the Integrator
exists to buy.

**Give each provider its own payout capability id.** Honest about the
divergence and fatal to the strategy: ERP would bind, and therefore branch, per
provider. ADR-0024 § 4 forbids exactly that, and relocating a conditional tree
into capability ids is relocation, not neutrality.

**Let the Integrator decide payout retries, since it owns the retry curve.**
A retry of a money-moving command with an unknown outcome is a business
decision about whether to pay twice. Transport-level retry is admissible only
where the effect provably did not land, which is why the connectors classify
connect-phase failure as `RETRYABLE` and post-send silence as
`RECONCILIATION_REQUIRED`.

**Name ERP's payout owner "Treasury" in a module now.** Premature. This ADR
assigns the decision to ERP; whether that owner is later extracted as a shared
module is an ADR-0006/ADR-0017 question with its own product-first inventory.
