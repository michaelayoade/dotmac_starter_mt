# ADR-0061: A payout is ERP's decision and its provider is a binding

> **Number allocation, 2026-08-24.** `0059` and `0060` are allocated on
> sibling branches that have not merged. This record takes `0061` rather than
> risk the ADR-0032 and ADR-0010/0011 collisions again; under the rule Michael
> applied there, the earlier record keeps the number, so a colliding new record
> would have had to move anyway.

- Status: Accepted. Amended 2026-08-24 — see "Amendment — 2026-08-24"
  at the end of this record. The amendment names the interim payout owner
  exactly, rules that `dotmac-treasury` is not created yet, moves the
  command schema onto the domain-owned `CapabilityContract`, and replaces
  § 4 D3's "both refusals are locally correct" conclusion. No earlier text
  is rewritten.
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

> **Corrected 2026-08-24 — see Amendment A1.** "ERP's Treasury/payment owner"
> is a ROLE, not an owner, and a role cannot be held to a boundary. A1 names
> the exact services that hold the decision today, path-qualified, and records
> that no `dotmac-treasury` package or namespace may be allocated before a
> product-first dossier establishes its lifecycle.

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

> **Superseded 2026-08-24 — see Amendment A3.** The table's observations stand;
> the conclusion drawn from them does not. "Both settled it locally and
> correctly" describes a stand-off with no resolution path, and it is not the
> ruling: the payload question is answered ONCE, by the domain contract, and
> each connector adapts to it INTERNALLY. A3 states the resulting
> `payments.payout.v1` surface and what stops being product-visible.

**D4 — the same gap already bit `messaging.send.v1`.** Both messaging
connectors declare the id and then diverge under it:
`meta_whatsapp` accepts `send_text | send_template | send_media` with a
`recipient` param; `meta_social` accepts `send_direct_message |
reply_to_comment` with `recipient_id` + `channel`. A product bound to one
cannot be re-bound to the other without changing its command. The payments
divergence is therefore a pattern, not an accident.

> **Corrected 2026-08-24 — see Amendment A4 and ADR-0024 § 11.** The implied
> repair — converge the two dialects under the published `messaging.send.v1` —
> would redefine a published contract version, which is now forbidden. The
> repair is SUCCESSION: `messaging.direct.send.v2`, `social.comment.reply.v1`
> and `social.profile.read.v1`.

**D5 — the SPI has no place to put a command contract.**
`dotmac_integration.spi.CapabilityDeclaration` carries `capability_id`,
`config_schema` and `modes`; `DispatchRequest.payload` is
`dict[str, object]`, unvalidated. Configuration has a declared schema and
commands do not, which is exactly why the divergence was invisible to every
existing gate.

> **Corrected 2026-08-24 — see Amendment A2 and ADR-0024 § 10.** The missing
> declaration surface is NOT `CapabilityDeclaration`. A schema published by
> each connector makes drift machine-readable instead of preventing it; the
> canonical schema belongs to the business-owned `CapabilityContract`, and a
> connector declaration may only CLAIM that contract's digest.

### 5. What must be true before payout interchangeability may be claimed

In order. Each is a reviewable diff, not a status change:

1. **The command contract exists.** `payments.payout.v1` gets a declared,
   versioned command payload schema — provider-neutral field names, exact
   decimal amount, explicit currency, product-minted stable reference,
   beneficiary as opaque references — owned by the declaring side and
   validated by the engine before dispatch, not by each connector after.
   *(Amended 2026-08-24, A2: "the declaring side" is now named as the
   domain-owned `CapabilityContract`, the schema set is command + result +
   observation under one canonical digest, and "validated by the engine" is the
   five-point gate in ADR-0024 § 10.4 rather than a single pre-dispatch check.)*
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
*(Reaffirmed and made concrete 2026-08-24 by Amendment A1: the interim owner is
two named ERP services, and the product-first dossier that would have to
precede any `dotmac-treasury` allocation has a stated scope.)*


## Amendment — 2026-08-24 (accepted corrections)

Four corrections, accepted the same day this record was. Each says which
section it corrects and why. Nothing above is rewritten; the original framing
stays readable so the correction reads as one.

### A1. The interim payout owner is two named ERP services — and `dotmac-treasury` is not created yet

§ 1 assigned the payout decision to "ERP's Treasury/payment owner". That is a
role. A role cannot be pointed at in a review, cannot be diffed, and cannot be
held to the boundary § 1 draws. The owner is named:

| Owner | Path | Owns |
|---|---|---|
| **`PaymentService`** | `dotmac_erp:app/services/finance/payments/payment_service.py` | the payout DECISION and the transfer lifecycle: `initiate_expense_transfer`, `_recover_transfer_initiation`, `process_successful_transfer`, `mark_transfer_failed`, `poll_transfer_status`, `process_transfer_reversal` |
| **`BatchTransferService`** | `dotmac_erp:app/services/finance/payments/batch_transfer_service.py` | composition of BATCHES of expense-reimbursement transfers over `PaymentService` — it composes, it does not decide separately |

Everything § 1 forbids an Integrator artifact from doing is forbidden because
these two services do it. In particular `_recover_transfer_initiation` and
`poll_transfer_status` are where an ambiguous attempt is resolved, which is the
concrete form of "whether an ambiguous attempt may be tried again is ERP's
call".

**`dotmac-treasury` is NOT to be created yet.** No package, no distribution, no
namespace allocation, no `mod_*` short code. The `Alternatives rejected` entry
above already called it premature; this makes the precondition explicit rather
than leaving it to judgement. A mandatory product-first dossier (ADR-0006's
extraction amendment, `AGENTS.md` rule 22) must first cover, as one inventory:

- ERP's `PaymentService` and `BatchTransferService` above;
- `dotmac-payments`;
- `dotmac-banking`;
- `dotmac-accounting`; and
- the approvals / payment-authorization paths that gate a disbursement.

The existing modules explicitly exclude payment EXECUTION, so a reusable
Treasury owner may well be justified — but the audit has to establish what
lifecycle that owner would hold before anything is allocated to it. This
amendment does not pre-empt that conclusion, and a separate record produces the
dossier.

*Corrects: § 1, and the "Name ERP's payout owner Treasury in a module now"
alternative. Consequence: `docs/ARCHITECTURE.md`'s ownership register row and
`AGENTS.md` rule 28 clause (d) carry the named services.*

### A2. The command schema belongs to the DOMAIN contract, not the connector declaration

§ 4 D5 recorded that `spi.CapabilityDeclaration` carries a config schema and no
command schema, and § 5 step 1 asked for a command payload schema "owned by the
declaring side". Read together they point at the connector's declaration, and
that placement is wrong: **if every connector publishes its own schema, drift
merely becomes machine-readable rather than prevented.** Two connectors serving
one id would publish two individually valid schemas and the engine would have
no ground to prefer either.

The canonical schema belongs to `CapabilityContract` — the business-owned
declaration in `dotmac_integration.capability_registry`, the artifact that owns
the capability's MEANING and of which the registry already permits exactly one
per id. It is extended with `command_schema`, `result_schema`,
`observation_schema`, a canonical contract digest, and deprecation/replacement
metadata.

`CapabilityDeclaration` keeps what is genuinely per-connector — configuration
and modes — and gains only the ability to CLAIM the domain contract's digest.
It never publishes a competing schema.

The required gate, stated as an obligation and not as an existing control:
command validation before enqueue; connector digest agreement at composition
AND at binding; result validation before settlement; observation validation
before inbox recording; and a schema change taking a new `.vN` capability id,
because a published contract version is never redefined. Sensitivity tests are
planted for digest mismatch, missing schema, invalid payload and invalid
result. ADR-0024 §§ 10.4–10.5 hold the full statement and name the exact
seams.

*Corrects: § 4 D5 and § 5 step 1. The gap they record is unchanged; only its
closure moves.*

### A3. `payments.payout.v1` exposes product meaning, not provider workflow — and the divergence is closed, not tolerated

§ 4 D3 concluded that both connectors "settled the question locally and
correctly". The observations in its table are accurate and stay. The conclusion
is superseded, because it describes a stand-off with no resolution and licenses
the divergence to persist. With A2 the payload question is answered ONCE, by
the domain contract, and each connector adapts to it internally.

`payments.payout.v1` exposes exactly this, and nothing else:

- `submit_payout` — one command for the business act;
- a PRODUCT payout reference — stable, minted by ERP, opaque to every provider;
- exact money — an exact decimal amount with an explicit currency, never a
  float;
- a provider-neutral destination — opaque beneficiary references, not a
  provider's account object;
- idempotency and correlation identifiers.

**Paystack's recipient creation and Flutterwave's direct-transfer details are
internal connector steps.** ERP must never orchestrate "create a Paystack
recipient" versus "send a Flutterwave transfer": if it does, switching a
binding still requires an ERP code release, which defeats the whole of § 3.
Concretely, Paystack's `resolve_bank_account` and `create_transfer_recipient`
stop being product-visible actions and become steps the connector performs
behind `submit_payout`; Paystack keeps deriving its provider reference and
keeps applying its own wire scale, from the contract's stable reference and
exact money, because those are provider protocol executed inside the connector;
and it IGNORES a field it cannot use rather than refusing the command that
carries it. Flutterwave and Remita derive their required fields the same way.

**The same principle governs `payments.intent.v1` and `payments.refund.v1`.**
Provider customer creation, provider recipient codes and provider transfer
references are normalized RESULTS — returned under the contract's
`result_schema` — or connector internals. They are not separate product-visible
provider actions unless a genuinely independent lifecycle requires them, and
that has to be ARGUED in an accepted record rather than assumed because a
provider publishes an endpoint (§ 9.2 of ADR-0024's amendment, applied to
command surface).

This puts `dotmac-connector-paystack`'s `payments.customer.v1` — today
`create_customer | update_customer | read_customer` — on notice: it is a
capability id minted around a provider's customer object, and it either earns
an argued independent lifecycle or it collapses into the intent contract's
normalized result. Recorded as an open item, not decided here.

*Supersedes: § 4 D3's conclusion, and § 5 step 2's framing of the closure as
each connector meeting the other halfway.*

### A4. `messaging.send.v1` is repaired by SUCCESSION, not redefinition

§ 4 D4 recorded the two messaging dialects and implied they should converge
under the published id. A published contract version is never redefined. The
canonical successors, ruled in ADR-0024 § 11.2:

| Successor | Replaces |
|---|---|
| `messaging.direct.send.v2` | provider-neutral direct delivery with a DISCRIMINATED text/template/media content shape — this is what replaces the disjoint `send_text` / `send_template` / `send_media` and `send_direct_message` vocabularies at once |
| `social.comment.reply.v1` | public Facebook/Instagram comment consequences, a different business act that was never a direct message |
| `social.profile.read.v1` | caller-initiated profile observation through REQUEST mode |

Sub migrates to the successors. `messaging.send.v1` is retained only for a
bounded compatibility window and is then RETIRED; the migration and the
retirement are both recorded obligations carried by the old contract's
deprecation metadata (A2), not intentions.

Known gap, stated rather than implied: `spi.ConnectorMode` is a closed union of
`INGRESS | POLL | DELIVERY` and has no REQUEST member, so
`social.profile.read.v1` cannot be served until the SPI grows one —
`dotmac-connector-meta-social`'s dossier already records the same fact from the
connector side.

*Corrects: § 4 D4.*
