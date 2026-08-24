# ADR-0061: A payout is ERP's decision and its provider is a binding

> **Number allocation, 2026-08-24.** `0059` and `0060` are allocated on
> sibling branches that have not merged. This record takes `0061` rather than
> risk the ADR-0032 and ADR-0010/0011 collisions again; under the rule Michael
> applied there, the earlier record keeps the number, so a colliding new record
> would have had to move anyway.

- Status: Accepted. Amended 2026-08-24 (A1–A4), again 2026-08-24 (A5–A7), and
  again 2026-08-24 (A8–A9) — see "Amendment — 2026-08-24", "Second amendment
  — 2026-08-24" and "Third amendment — 2026-08-24" at the end
  of this record. A1–A4 name the interim payout owner exactly, rule that
  `dotmac-treasury` is not created yet, move the command schema onto the
  domain-owned `CapabilityContract`, and replace § 4 D3's "both refusals are
  locally correct" conclusion. A5–A7 remove `payments.customer.v1` from the
  public capability manifest, record ADR-0042 as the controlling record for
  disbursement ownership, and fix the evidentiary wording for every ERP payout
  claim. A8 KEEPS `payments.refund.v1` — closing A3's remaining open item the
  OPPOSITE way to A5, because a refund passes the same test
  `payments.customer.v1` failed — and A9 records that Treasury is not
  automatically the refund owner. No earlier text is rewritten.
- Date: 2026-08-24
- Deciders: Michael
- Supersedes: none
- Extends: ADR-0024 § 4 (shared behavior contains no product or provider
  switch), § 6 (the Integrator is the sole external connector control plane),
  § 7 (the connector-plugin SPI), and its 2026-08-24 amendment
- Related: ADR-0042 § 3 (payment obligations are not payment instructions —
  this record names the Treasury/payment owner that ADR-0042 left unnamed, and
  ADR-0042 is the CONTROLLING record on disbursement ownership, see A6),
  ADR-0047 + its Amendment A1 (Expenses own eligibility, not payment — A1 holds
  the authoritative six-owner disbursement split), ADR-0063 (Treasury owns the
  payment instruction, not the provider batch — the gated scope A1 deferred),
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
| ~~`BatchTransferService`~~ | `dotmac_erp:app/services/finance/payments/batch_transfer_service.py` | **NOT an owner — dead code.** Zero callers across `app/`, `tests/`, `scripts/` and `tools/`; not exported from its package `__init__.py`; zero tests. It neither composes nor decides. Named here in the first draft of this ADR from a method-name reading, corrected by the product-first dossier before this record was ever merged. It nonetheless holds a live `initiate_transfer` with no separation of duties and no permission check on `approve_batch` — a latent hazard, recorded in `docs/inventories/treasury-payment-execution-sources.md`, not an authority |

`PaymentService` is therefore the SOLE interim owner. Everything § 1 forbids an
Integrator artifact from doing is forbidden because that one service does it.
ERP's only live batch writer is `PaymentService._update_batch_item_status`,
which can fire only for items no live code creates. In particular `_recover_transfer_initiation` and
`poll_transfer_status` are where an ambiguous attempt is resolved, which is the
concrete form of "whether an ambiguous attempt may be tried again is ERP's
call".

**`dotmac-treasury` is NOT to be created yet.** No package, no distribution, no
namespace allocation, no `mod_*` short code.

> **Superseded in its PRECONDITION 2026-08-24 — see ADR-0063.** The dossier
> this paragraph demands now exists
> (`docs/inventories/treasury-payment-execution-sources.md`, on the sibling
> branch `docs/treasury-product-first-dossier`), and ADR-0063 answers it — its
> § 12.3 **G5** asks in terms for exactly such a record. A narrow Treasury owner
> of `PaymentInstruction` is authorized in
> SCOPE, and CONSTRUCTION is gated on ERP's `PaymentIntent.status` three-writer
> defect being fixed first. The prohibition below stands in full today —
> nothing may be allocated — but its release condition is now ADR-0063 § 7
> rather than "a dossier exists".

The `Alternatives rejected` entry
above already called it premature; this makes the precondition explicit rather
than leaving it to judgement. A mandatory product-first dossier (ADR-0006's
extraction amendment, `AGENTS.md` rule 22) must first cover, as one inventory:

- ERP's `PaymentService` above, and `BatchTransferService` as a disposal
  question rather than an ownership one;
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

> **Applied to refund 2026-08-24 — see Amendment A8.** The test in this
> paragraph is run against `payments.refund.v1` and it PASSES, which is the
> opposite result to A5's. A refund has the independent business lifecycle and
> financial consequence a provider-side customer record does not, so the
> capability is KEPT — while its provider-shaped OPERATIONS are removed under
> this same paragraph's rule. A8 states the distinguishing test so the two
> outcomes do not read as an inconsistency.

This puts `dotmac-connector-paystack`'s `payments.customer.v1` — today
`create_customer | update_customer | read_customer` — on notice: it is a
capability id minted around a provider's customer object, and it either earns
an argued independent lifecycle or it collapses into the intent contract's
normalized result. Recorded as an open item, not decided here.

> **Decided 2026-08-24 — see Amendment A5.** The open item is closed AGAINST
> `payments.customer.v1`: it has no independent Dotmac business lifecycle, so
> it is REMOVED from the public capability manifest rather than left on notice.

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


## Second amendment — 2026-08-24 (accepted corrections A5–A7)

Three further corrections accepted the same day. Same convention as A1–A4: each
names the section it corrects, nothing above is rewritten, and the superseded
spot carries a short pointer.

### A5. `payments.customer.v1` is REMOVED from the public capability manifest

A3 left this on notice — *"it either earns an argued independent lifecycle or
it collapses into the intent contract's normalized result"*. It does not earn
it. **`payments.customer.v1` has no independent Dotmac business lifecycle. It
is Paystack workflow exposed as a capability**, and it is removed.

The test A3 set is the right one and it fails cleanly. A capability id must name
a business act that a Dotmac product DECIDES. Nothing in the fleet decides
"create a customer at a payment provider" as an act of its own: the decision is
always *take this payment* or *pay this beneficiary*, and the provider-side
customer record is a by-product of that. `create_customer | update_customer |
read_customer` is a mirror of `POST/PUT/GET /customer` — the provider's REST
surface with a Dotmac id painted on it. A5 of ADR-0024's 2026-08-24 amendment
§ 9.2 says the same thing from the other side: a provider publishing an endpoint
is not an argument that a capability exists.

What replaces it — five statements, all of which are the existing architecture
rather than new machinery:

1. **The product Customer owner keeps customer identity.** Who the customer is,
   what they are called, how to reach them, and every consequence of that is
   owned where it already is. A payment provider never held it and must not
   start.
2. **`payments.intent.v1` carries the required customer EVIDENCE.** The contract
   already carries what a provider needs to attribute a charge; the evidence
   travels with the act that needs it, in the command that was decided.
3. **The connector creates or resolves any provider-side customer INTERNALLY**,
   behind the intent command — exactly as A3 already ruled for Paystack's
   recipient creation behind `submit_payout`. Paystack's `/transaction/initialize`
   resolves or creates its own customer from the evidence on the command; that
   is provider protocol performed inside the connector.
4. **The result returns an OPAQUE Integrator correlation** where one is needed
   at all — under the contract's `result_schema` (A2), normalized, not a
   provider's customer object.
5. **Saved-instrument charging consumes an opaque PAYMENT-METHOD correlation,
   never a Paystack customer code.** This is already true in the shipped
   connector and the amendment fixes it in place: `charge_authorization` takes
   an `authorization_code`, which is an instrument handle. A future
   provider-neutral saved-instrument surface normalizes THAT, and a customer
   code is not an acceptable substitute for it — a customer code identifies a
   person at a provider, an instrument handle identifies a thing that can be
   charged, and conflating them is how one customer's stored card gets charged
   for another's invoice.

**Customer read, create and update disappear from the public manifest.** Not
deprecated with a compatibility window: they have no consumer to migrate (no
product binds this capability today), so there is nothing to succeed and A4's
succession rule is not engaged.

**A future need is welcome, but it arrives fully formed.** If a product later
requires genuine, independent synchronization of provider-side customer records,
it comes with its own OWNER, its own CONSUMER, its own LIFECYCLE and its own
SCHEMA, argued in an accepted record. **Today's Paystack synchronization is not
preserved merely because it exists** — "we already wrote it" is the argument
ADR-0017 exists to refuse, and preserving an unbound capability id costs a
permanent contract obligation to buy nothing.

**The code removal happens LATER**, as part of the payout refactor, gated behind
the capability-schema seam (A2) and the result seam (A3). This amendment is the
ruling; the diff follows it. Recorded exactly so nobody has to re-derive it:

| Artifact | What must change |
|---|---|
| `packages/dotmac-connector-paystack/src/dotmac_connector_paystack/delivery.py` | delete `PAYMENT_CUSTOMER_CAPABILITY` (`:59`) and its `ACTIONS_BY_CAPABILITY` entry (`:72–74`). `OUTBOUND_CAPABILITY_IDS` is derived from that mapping and shrinks on its own |
| `…/operations.py` — `OPERATIONS` | delete the three `_Operation` rows and their comment block: `create_customer` (`:390`, AMBIGUOUS), `update_customer` (`:393`), `read_customer` (`:394`) |
| `…/operations.py` — request builders | delete the `create_customer` (`:689`), `update_customer` (`:698`) and `read_customer` (`:710`) branches of `_request` |
| `…/operations.py` — `_REPLY_EVIDENCE` | delete the three `customer_code` entries (`:933–935`) |
| `…/operations.py` — `_classify` | delete the `status == 404 and sent.operation.name == "read_customer"` branch (`:1034`) |
| `…/operations.py` — `_correlation` | drop `"customer"` from `("transaction", "customer", "recipient")` (`:1180`). After the removal NO remaining Paystack command takes a `customer` param — `refund` takes `transaction`, `initiate_transfer` takes `recipient` — so the key is dead, and leaving it would let a stray payload key become stored evidence |
| **ordering constraint** | `delivery._MISALLOCATED` is an IMPORT-TIME guard requiring exact bijection between `OPERATIONS` and `ACTIONS_BY_CAPABILITY`. The two deletions above must land in the SAME change or the module refuses to import — which is the guard working, not a problem to route around |
| `packages/dotmac-connector-paystack/COMPATIBILITY.md` (`:16`), `README.md` (`:28`), `CHANGELOG.md` (`:9–15`) | remove the capability row / command row, and record the removal as a change rather than silently dropping it |
| `packages/dotmac-connector-paystack/EXTRACTION.toml` | the `contract` string (`:9`) names `payments.customer.v1` — narrow it; and the A3 OPEN-ITEM block (`:86–92`) becomes the recorded decision, not a question |
| `tests/unit/test_paystack_outbound_operations.py` | the `CUSTOMER` constant (`:56`), the action-allocation assertions (`:163–165`, `:668–688`) and the two `read_customer` dispatch cases (`:867`, `:884`) go with it. The totality and misallocation proofs STAY and must still bite on the smaller table |

*Corrects: A3's closing "recorded as an open item, not decided here". Related:
ADR-0024 § 9.2, ADR-0017.*

### A6. Disbursement ownership — ADR-0042 controls, and the split is six owners

ADR-0047 § Context (accepted 2026-08-18) says *"Finance/Payables owns
obligations, journals, disbursement and settlement."* ADR-0042 (accepted
2026-08-19, one day later, and devoted specifically to separating liabilities
from payment execution) says a **Treasury/payment owner performs disbursement**.

**ADR-0042 controls.** ADR-0047's statement is too broad, it was written to say
"not Expenses" rather than to locate disbursement, and it is narrowed by
ADR-0047's own dated Amendment A1 in the same change as this one. Two records
now cross-link to it: this one, and ADR-0063.

The authoritative split — canonical text in ADR-0047 Amendment A1, restated here
because this is the record a reader arrives at from the connector side:

| Owner | Decision |
|---|---|
| Expenses | whether a claim is eligible for reimbursement |
| Payables | what is owed, to whom, in what currency and when |
| **Treasury** | the authorized payment instruction, rail submission and resolution |
| **Integrator** | provider authentication, transport and evidence |
| Banking | statement/cash observations and reconciliation evidence |
| Accounting | journal and ledger consequences |

The two bold rows are this record's subject matter, and § 1's whole point is the
line between them: Treasury decides, the Integrator carries. Everything § 1
forbids an Integrator artifact from doing is a restatement of that row boundary.

Treasury's scope as a shared owner is ADR-0063 — `PaymentInstruction` and
`PaymentRun`, two rails, gated on ERP's `PaymentIntent.status` three-writer fix.

*Corrects: nothing in this record; it records which of two accepted records
controls, and cross-links both. Related: ADR-0041, ADR-0044, ADR-0047 A1,
ADR-0063.*

### A7. Every ERP payout claim reads "Implemented and tested; production enablement unconfirmed"

A1 and § 4 rest on a measured reading of ERP's payout code. That reading
supports exactly one evidentiary claim, and it is narrower than the words used
around it.

**The required wording, verbatim, wherever an ERP payout claim is made:**

> **Implemented and tested; production enablement unconfirmed.**

Why it is unconfirmed rather than merely unstated: ERP's payout path is gated by
`paystack_transfers_enabled`, which is a **row** in `domain_settings` —
per-organization overridable, declared with `default=False`
(`app/services/settings_spec.py:557-563`), seeded ONCE from
`PAYSTACK_TRANSFERS_ENABLED` (`app/services/settings_seed.py:500-505`) and never
read from the environment again. It is **runtime data**, not a repository fact.
ERP's `.env.example` carries no `PAYSTACK*` key at all, and the name appears
zero times in `docker-compose.yml`, `deploy/`, `config/`, `docs/`, `reports/` or
`proposals/`. The product-first dossier
(`docs/inventories/treasury-payment-execution-sources.md` § 5, § 12.4, § 14 Q1)
records the same measurement and the same conclusion — *"today the claim is
'implemented and tested', not 'production-used'"*.

Confirming its value therefore requires an explicitly named deployment target
and, under `AGENTS.md` rule 30, a `deployment_run` oracle carrying immutable
coordinates. **Nobody has named a target.** Reading a default out of a settings
spec answers what the code does when no row exists; it does not answer what any
deployment does, and a default is not an oracle. The dossier's § 12.4 states
what each answer would buy: a true value gives the extraction its production-use
leg, and a false-everywhere value WEAKENS it to `greenfield-after-inventory` —
so the absence cuts in both directions and neither may be assumed.

What the wording does and does not block:

- It does **NOT** block building the gated Treasury module (ADR-0063) or the
  connectors. Implementation quality is a repository-local fact and the code is
  there to be read.
- It **DOES** block any claim of production PARITY, ADOPTION or RETIREMENT.
  A retirement decision that assumes a path is live in production, or that
  assumes it is not, is unfounded in both directions — which is the honest
  position, and rule 30's point about an ABSENCE being a moment rather than a
  permanent fact.

Applied in the same change as this amendment wherever a dossier or an ADR
implied more — `docs/inventories/payment-connector-sources.md` § 1, whose "live"
column is a repository-local reading rather than a deployment observation, and
`docs/inventories/payment-connector-extraction-dossier.md` § 2.2.

**And one disposal, now that the dossier preserves its disposition:
`BatchTransferService` is to be DELETED.** A1 recorded it as dead code holding a
live `initiate_transfer` with no separation of duties and no permission check on
`approve_batch`. The dossier tightens that to what is actually there (§ 1.3):
there is NO method of that name on the class — what it holds is a live CALL PATH
to `client.initiate_transfer` (`batch_transfer_service.py:409`), reached from
`process_batch` → `_process_batch_item`, behind an `approve_batch` with neither
separation of duties nor a permission gate. It is welded to one document type at
the schema level, and it reads a settings value without passing
`organization_id=`, unlike the equivalent call in `PaymentService`. A1's
conclusion is unaffected and was, if anything, understated. Dead code that can move money is security-sensitive dead code:
it is a working money path one import away from being reachable, and it is
exempt from review attention precisely because nothing calls it. The only reason
to keep it was that it was the sole written record of how batch payment was
imagined in ERP — and that record now exists, measured, in the dossier. The
disposition is preserved; the class is not. ADR-0063 § 3 also rules it out as a
port source, so nothing downstream needs it either.

*Corrects: the evidentiary framing of A1 and § 4, and the retention of
`BatchTransferService` implied by "recorded as a disposal question". Related:
`AGENTS.md` rule 30, ADR-0063 §§ 3, 7.*


## Third amendment — 2026-08-24 (accepted corrections A8–A9)

Two further corrections accepted the same day. Same convention as A1–A7: each
names the section it corrects, nothing above is rewritten, and the superseded
spot carries a short pointer.

### A8. `payments.refund.v1` is KEPT — it passes the test `payments.customer.v1` failed

A3 closed with an open item on `payments.customer.v1` and left the SAME test
standing over `payments.refund.v1` (`packages/dotmac-connector-paystack/
EXTRACTION.toml`: *"the same A3 test still applies to `payments.refund.v1`'s
surface, and remains an open item there"*). A5 ran the test on customer and
removed the capability. Running it on refund returns the OPPOSITE answer, and
that is not an inconsistency — it is the test discriminating, which is what a
test is for.

**The distinguishing test, stated so a reader arriving at the two outcomes
together can see why they differ.** A capability id must name a business act a
Dotmac product DECIDES, with a lifecycle and a consequence of its own. Ask the
question that separates the two cases:

> **If the provider vanished tomorrow, would the thing still exist and still
> need an owner?**

| | `payments.customer.v1` — REMOVED (A5) | `payments.refund.v1` — KEPT (A8) |
|---|---|---|
| Is there a Dotmac decision? | No. Nothing in the fleet decides "create a customer at a payment provider". The decision is always *take this payment* | **Yes.** Someone with authority decides money goes BACK — whether the customer is entitled to it, how much, and against which original payment |
| Independent lifecycle? | No. The provider record is a by-product of a charge, with no state a Dotmac owner transitions | **Yes.** Requested → submitted → ambiguous → settled / failed, with its own evidence and its own reconciliation |
| Financial consequence? | None | **Yes.** A refund reverses a receivable, changes revenue and coverage, and is a reportable movement in its own right |
| If the provider vanished? | Nothing survives — there was never a Dotmac object, only a mirror of `POST/PUT/GET /customer` | **The refund obligation survives.** It would be paid another way. The provider was the rail, never the reason |
| Verdict | provider WORKFLOW wearing a Dotmac id | a **business act** that happens to be executed at a provider |

One line: **a provider-side customer record is a by-product of an act; a refund
IS the act.**

**The split — five rows, and the boundary lines are the same ones § 1 draws for
a payout:**

| Layer | Owns |
|---|---|
| **Billing, or the named refund owner** | whether the customer is ENTITLED to a refund, for how much, against which original payment, and every receivable, revenue and coverage consequence of that decision |
| **`payments.refund.v1`** | the AUTHORIZED, provider-neutral refund request — what was decided, carried without a provider's vocabulary anywhere in it |
| **the Integrator** | provider transport: authentication, at-most-once dispatch, retry curve, dead-letter, evidence |
| **the connector** | provider references and provider-side reconciliation — the Paystack transaction handle, the Flutterwave charge id, the derived key, the refund-list read |
| **nobody** | a decision to refund again. See below |

The refund owner is deliberately NOT named in this record. Today's live refund
decisions are Billing/AR ones (`dotmac_sub`'s `_stage_financial_consequences`
stages a refund consequence; ERP's `payment_service` posts one), but naming a
shared owner needs the same product-first evidence A1 demanded before naming a
payout owner, and nobody has produced it. **Until it is named, "Billing or the
named refund owner" is the honest phrase, and this record uses it rather than
inventing a role — which is precisely the error A1 corrected in § 1.**

**An ambiguous refund is NEVER blindly retried.** This is § 1's rule about
ambiguous payouts, and the reason it bites harder for a Paystack refund is
worth writing down because the code makes it easy to misread:

- Paystack's `/refund` **accepts no client reference**. There is no field in
  which the engine's idempotency key can ride, and so **no provider-enforced
  duplicate protection exists on this endpoint at all.**
- The connector's response is correct and stays: it stamps the derived key into
  `merchant_note` (`operations.py:626-630`) and classifies an unanswered send
  as `AMBIGUOUS` (`operations.py:368`), so an ambiguous refund is resolved by
  READING the provider's refund list for that transaction and matching on the
  note (`operations.py:68-73`) — Sub's production mechanism, ported verbatim.
- **That makes an ambiguous refund DECIDABLE. It does NOT make a second refund
  REFUSED.** The distinction is the whole point: a duplicated payout is
  refusable at Paystack because the derived reference rides the provider's own
  `reference` field; a duplicated refund is not, because there is no such
  field. Nothing at the provider stands between a re-request and a second
  refund except the read.
- Therefore: the reconciliation read is MANDATORY before any re-request, the
  transport may never make one on its own, and a re-request after a conclusive
  "no refund exists" is a **new authorized decision by the refund owner** —
  the same shape as ADR-0063 § 4's rule that rerouting is an authorization
  event and not a retry.

**The surface: one command, no provider operations.** Applying A3's rule to
refund, `payments.refund.v1` exposes exactly one command — `request_refund` —
carrying:

- **exact money** — an exact decimal amount with an explicit currency, never a
  float, and **full-versus-partial declared explicitly**, never signalled by an
  absent field. Today `dotmac-connector-flutterwave`'s `_refund_body` treats a
  missing `amount` as a FULL refund at the provider
  (`outbound.py:333-340`); absence-as-meaning is how a dropped key becomes
  "refund everything", and the contract settles it with a discriminator;
- the **original-payment correlation** — the product's own handle on the
  payment being reversed, provider-neutral;
- the **authorization reference** — which decision by the refund owner
  authorized this, so the Integrator carries an authorized request rather than
  a request; and
- **idempotency identity** — the engine key, from which each connector derives
  whatever its own wire needs.

Everything provider-shaped moves INSIDE the connector, exactly as A3 moved
recipient creation behind `submit_payout`. Named so the diff is not
re-derived — the code removal is LATER, gated behind A2's schema seam and A3's
result seam, like A5's:

| Artifact | What must change |
|---|---|
| `…/dotmac_connector_paystack/delivery.py` | `ACTIONS_BY_CAPABILITY[PAYMENT_REFUND_CAPABILITY]` is `frozenset({"refund"})` (`:68`) — `refund` is Paystack's verb inside an `{"action", "params"}` envelope. Under the contract the product sends `request_refund`'s declared payload and the connector maps it. `delivery._MISALLOCATED`'s import-time bijection forces this and the `OPERATIONS` change into ONE commit, same constraint A5 records |
| `…/operations.py` — the `refund` `_Operation` (`:368`) | **STAYS as it is.** `AMBIGUOUS` on an unanswered send and `needs_key=True` are correct and are the reason the capability is safe to keep. Do not "simplify" either while renaming the command |
| `…/operations.py` — `_request`'s `refund` branch (`:613-630`) | it reads `params["transaction"]` — a Paystack transaction reference supplied BY THE PRODUCT. That is the provider-shaped field A3 forbids: the connector resolves its own provider handle from the contract's original-payment correlation. The `merchant_note` stamping at `:626-630` stays — it is provider protocol executed inside the connector |
| `…/operations.py` — `_correlation` (`:1177-1184`) | after A5 drops `"customer"` the tuple is `("transaction", "recipient")`; the refund's handle then comes from the contract's correlation field, not from a provider-named payload key. The docstring's *"without it an ambiguous refund has no handle at all"* states the requirement the contract must keep satisfying |
| `…/operations.py` — `_refund_result` (`:895-925`) | returns `{"refund_id": …}`, a provider-shaped reply. It becomes a normalized value under the contract's `result_schema` (A2) |
| `…/dotmac_connector_flutterwave/outbound.py` — `_refund_body` (`:326-345`) | requires `provider_transaction_id` as a PRODUCT-supplied field (`:327-331`) — the same defect from the other side. It derives the charge id, and the `currency_minor_units` exponent it needs, from the contract's correlation and exact money, INSIDE the connector, and refuses no command for carrying a field another provider needs |
| both connectors' `EXTRACTION.toml`, `README.md`, `COMPATIBILITY.md` | the capability ROW keeps its place — this is a retention, not a removal — and the command NAME becomes the contract's rather than the provider's |
| refund STATUS reads | `GET /refund?transaction=…` and Flutterwave's refund-status read are reconciliation internals, never product-visible actions. A3's rule, unchanged |

**Nothing here weakens A5.** The two amendments apply one test and report what
it returned. A future capability is argued on the same ground: an independent
lifecycle and a consequence a Dotmac owner is accountable for, not the fact
that a provider publishes an endpoint.

*Corrects: A3's closing scope — its open item over `payments.refund.v1`, carried
in `packages/dotmac-connector-paystack/EXTRACTION.toml`, is now DECIDED and
decided in favour of retention. Nothing in A5 changes. Related: ADR-0024 § 9.2,
ADR-0063 §§ 4, 6, `AGENTS.md` rule 28 clauses (h), (i), (l).*

### A9. Treasury is NOT automatically the refund owner

Stated explicitly because the inference is easy and wrong: A8 keeps a
money-moving capability, ADR-0063 authorizes a money-moving owner, and a reader
one step ahead of the evidence will connect them.

**They are not connected, and broadening ADR-0063 to cover refunds requires
separate evidence.** Three reasons, in order of how hard they are to argue
around:

1. **ADR-0063 § 6 is a CLOSED surface of twelve items and a refund is not one
   of them.** That section says in terms that *"anything not on this list is
   another owner's, and adding to the list is an ADR, not an implementation
   detail."* This is that rule being applied to its first candidate.
2. **A refund is not a disbursement.** The six-owner split (A6) gives Treasury
   the authorized payment instruction for what Payables says is OWED. A refund
   reverses a receivable — money going back along the leg it came in — and its
   consequence lands in Billing/AR and Accounting, not in Payables. Same
   direction of cash, different obligation, different owner, different
   controls.
3. **"It moves money out, so Treasury does it" is department-shaped
   reasoning** — the exact error A6 and ADR-0047 Amendment A1 corrected when
   they broke *"Finance/Payables owns obligations, journals, disbursement and
   settlement"* into six named owners. A rail is not an owner.

What would be required to extend Treasury over refunds: a product-first
inventory of the live refund decisions (`dotmac_sub`'s staged refund
consequence, ERP's refund posting, and whatever Billing holds), a named refund
owner, the lifecycle that owner would hold, and an ADR amending ADR-0063 § 6's
closed list. Until all four exist, `payments.refund.v1` is carried by the
Integrator on behalf of an owner ADR-0063 does not name and does not claim.

*Corrects: nothing. It forecloses an inference A8 and ADR-0063 make available
together. Related: ADR-0063 § 6, ADR-0047 Amendment A1, ADR-0042 § 3.*
