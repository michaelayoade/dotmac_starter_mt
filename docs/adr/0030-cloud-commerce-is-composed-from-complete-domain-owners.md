# ADR-0030: Cloud commerce is composed from complete domain owners

**Status:** Accepted
**Date:** 2026-08-15
**Decision owner:** Michael
**Scope:** Dotmac Cloud and the reusable commerce/service modules it composes;
the single-owner and independent-module rules apply FLEET-WIDE.
**Amends:** [ADR-0017](0017-adoption-is-the-scarce-resource.md) by granting a
named owner-directed implementation exception for the seven unbuilt business
modules and three enabling owners named in this decision. It does not declare
P11 met and does not authorize any other gap-list candidate.
**Extends:** [ADR-0020](0020-billing-owns-operational-receivables.md) and
[ADR-0024](0024-apps-compose-by-synchronizing-data.md).
**Amended:** 2026-08-23 — production bill of materials, provider selections
and the Domains/Hosting allocation (see the amendment below).
**Amended:** 2026-08-28 — §G's "first cutover" label is reassigned to
`dotmac_sub` for **Billing and Payments only**, by applying §G's own definition
of the term. Subscriptions is expressly NOT reassigned: Vendor CP holds a real
`offer_versions` writer to retire. See the amendment below.
**Evidence:**
[`cloud-commerce-owner-sources.md`](../inventories/cloud-commerce-owner-sources.md),
[`numbering-sources.md`](../inventories/numbering-sources.md),
[`orders-sources.md`](../inventories/orders-sources.md),
[`subscriptions-sources.md`](../inventories/subscriptions-sources.md),
[`collections-sources.md`](../inventories/collections-sources.md),
[`domains-sources.md`](../inventories/domains-sources.md),
[`hosting-sources.md`](../inventories/hosting-sources.md),
[`fulfillment-sources.md`](../inventories/fulfillment-sources.md),
[`provider-capability-sources.md`](../inventories/provider-capability-sources.md)

## Amendment 2026-08-15 — corrected by the source dossiers

This decision was accepted before six of its nine evidence dossiers existed. The
completed audits confirmed the ownership matrix in §1 and both greenfield
verdicts, and refuted four subsidiary claims. Michael directed the corrections
below on 2026-08-15, before any implementation PR opens. They are folded into
the sections that follow; this note records what changed and why.

1. **Fulfillment was reclassified from product-first to greenfield-after-inventory.**
   The original §5d said to port Sub's run/step/readiness patterns. Sub has no
   saga engine to port — see §5d.
2. **Orders' source characterization was corrected.** No source in the fleet has
   immutable accepted lines, so that behavior is a mandatory greenfield delta
   rather than a port — see §5b.
3. **Subscriptions' immutability source was corrected.** Vendor CP's
   `offer_versions` are not structurally immutable — see §5a.
4. **Connector distributions were confirmed unauthorized.** §5 sequences them;
   sequencing never authorized them, and §6 remains the controlling text —
   see §6.

The build order in §5 was reordered as a consequence: Orders now precedes
Subscriptions, and Fulfillment moves after Collections.

## Amendment 2026-08-15 (second) — the step 6/7 revalidations

The two revalidation reports commissioned after `dotmac-numbering` reached its
completion gate moved two rulings. Both were reached through code that is
BYTE-IDENTICAL to its pin, so a diff-based recheck would have confirmed the
old text; what changed is what the unchanged code turns out to do. Michael
ruled on both on 2026-08-15.

5. **Durable timers become a selectable dual-plane MODULE, not kernel code.**
   `dotmac_kernel.durable_timers` is replaced everywhere in this decision by
   `dotmac-durable-timers`. The module owns timer identity, generation,
   supersession, cancellation and staleness verification, and REUSES the kernel
   outbox/relay for claim, lease, retry and dead-letter. A second claim loop is
   forbidden — see §4a.
6. **Billing's sourcing is reclassified from product-first to
   greenfield-after-inventory**, on the same evidence standard that reclassified
   Fulfillment. Its contract contradictions must be resolved before any
   behaviour code — see §5e.

## Amendment 2026-08-23 — the production bill of materials

Michael directed a production Dotmac Cloud V1 on 2026-08-22 and answered eight
composition questions on 2026-08-23. This amendment freezes the bill of
materials before any behaviour change, and reconciles this decision against
owners that landed on `main` AFTER it was accepted. Repository facts below were
measured at `origin/main` `90916442`, `dotmac-kernel 0.1.0a91`,
`dotmac-integration 0.1.0a12`.

### A. Owners that landed after this ADR was accepted

Seven distributions now exist on `main` that §1 never considered. Two of them
own capabilities Cloud V1 requires, so §1's matrix is incomplete as written.

| Distribution | On `main` | Published | Disposition for Cloud V1 |
|---|---|---|---|
| `dotmac-payments` | yes, `0.1.0a1` | no | **ADOPT — new §1 row.** Owns the payment-intent lifecycle and the append-only intent↔settlement correlation. |
| `dotmac-tax` | yes, `0.1.0a1` | yes | **ADOPT — new §1 row.** Owns effective-dated tax determination. |
| `dotmac-party` | yes, `0.1.0a1` | yes | **ADOPT.** Customer identity beyond the kernel Party. |
| `dotmac-brand-profiles` | yes, `0.1.0a1` | yes | ADOPT for storefront presentation. Holds no CSS, bytes or keys. |
| `dotmac-fx-policy` | yes, `0.1.0a1` | no | **EXCLUDE from V1** — see §D, NGN-only. |
| `dotmac-service-orders` | yes, `0.1.0a1` | no | **EXCLUDE from V1.** Service-delivery orders and activation readiness are ISP-shaped; Cloud's activation path is Fulfillment → Domains/Hosting. |
| `dotmac-template-studio` | yes, `0.2.0a3` | no | Defer to the notification slice; not launch-blocking. |

**`dotmac-payments` is the material correction.** §1 assigns "PSP credentials,
provider webhook verification, wire mapping and delivery transport" to a
connector plugin, and settlement acceptance/allocation to Billing. Between those
two rows sat an unowned fact: *the intent to be paid, and its correlation to an
external settlement*. Billing must not grow it — that would make Billing both
the payment-intent owner and the receivable owner, and the journey's step 4
("a PSP creates and confirms payment") would have no owner at all. §1 gains:

| Fact or decision | Sole owner |
|---|---|
| Payment intent lifecycle, transfer proof, and the append-only intent↔settlement correlation | `dotmac-payments` |
| Effective-dated tax determination from typed source facts | `dotmac-tax` |

Billing consumes a determination and FREEZES it into the immutable invoice
snapshot; it never recomputes a rate. ERP retains statutory returns and GL.
Tax determining and Billing freezing are two decisions, not one — a rate that
changes after issuance must not silently restate an issued invoice.

### B. The frozen production bill of materials

Pinned exactly by the Cloud assembly. "Published" is a claim about an
authoritative external oracle (rule 30), not about presence on `main`.

**Published today — pinnable now:** `dotmac-kernel` (`0.1.0a91`),
`dotmac-ui`, `dotmac-auth-oidc`, `dotmac-numbering`, `dotmac-durable-timers`,
`dotmac-files`, `dotmac-integration` (`0.1.0a12`), `dotmac-party`, `dotmac-tax`,
`dotmac-brand-profiles`, `dotmac-connector-paystack` (`0.1.0a2`),
`dotmac-connector-flutterwave` (`0.1.0a2`).

**On `main`, complete-but-unpublished — must be released before Cloud pins:**
`dotmac-fulfillment`, `dotmac-document-rendering`, `dotmac-payments`.

**Not on `main` — candidates on branches only:** `dotmac-billing`,
`dotmac-orders`, `dotmac-subscriptions`, `dotmac-collections`.

**Not committed anywhere — preserved only on a salvage branch:**
`dotmac-domains` and `dotmac-hosting`. Both existed solely as uncommitted
working-tree files until 2026-08-23; they are now on pushed
`salvage/cloud-domains-hosting`. Neither has ever been reviewed, tested on
PostgreSQL, allocated, merged or released.

**To be built:** `dotmac-storefront` (§F), the NiRA EPP connector, the
Openprovider connector, the PowerDNS connector, at least one panel connector
(cPanel and/or DirectAdmin — §E.2), an outbound-delivery connector, and PSP
checkout-initialization egress (§C).

`dotmac-integration` is deployed only through Dotmac Integrator; Cloud never
composes it.

### C. PSP — both providers are selectable options, and neither can yet charge

Michael's ruling: **Paystack and Flutterwave are both offered to the customer**,
as alternatives at checkout — not primary-and-failover.

This is an installation binding plus a per-checkout selection recorded as data.
It is NOT a branch. Concretely, and enforced by architecture test:

- `dotmac-payments` carries an OPAQUE provider-binding reference on the intent.
  No provider enum, no `if provider ==`, no `paystack_*`/`flutterwave_*` column
  in any module — the recorded fleet anti-pattern is
  `dotmac_crm:app/models/subscriber.py:210`.
- Which providers a tenant may offer is Integrator binding configuration.
- Both connectors certify against the SAME conformance kit. A provider that
  cannot pass it is not offered.

**The gap this exposes.** Both published connectors implement
`payments.settlement.observation.v1` in INGRESS and POLL modes only. Neither can
*initiate* a charge. Journey step 4 therefore has no transport today. V1
requires a new egress capability — provisionally
`payments.checkout.initialization.v1` — implemented by both distributions, with
the hosted-checkout redirect/reference returned as opaque evidence. This is new
connector work on two already-published distributions and is launch-blocking.

Renewal (journey step 10) additionally needs a stored-instrument charge.
Paystack exposes a first-class Subscriptions/Plans and card-authorization API;
Flutterwave offers tokenization whose tokens expire after one year. That
asymmetry is a CONNECTOR concern: the capability contract must express
"charge a stored instrument" and let each connector meet it, including
re-tokenization, without Cloud learning either provider's model.

### D. Money: NGN-only V1, multi-currency-shaped rows

Michael's ruling: ship NGN-only, but design money so multi-currency needs no
migration.

- Every monetary column carries an EXPLICIT currency alongside an exact decimal
  amount. Never a float, never an implied currency.
- `dotmac-fx-policy` is EXCLUDED from the V1 profile. No rate sourcing,
  selection policy or determination evidence ships in V1.
- Registrar and panel costs are USD. That is procurement and margin, not a
  customer-facing FX decision: NGN sell prices are set as immutable published
  price versions by `dotmac-subscriptions`.
- Adding a second sell currency later adds `dotmac-fx-policy` plus immutable FX
  snapshots on order lines and invoices. It must not require altering a money
  column.

### E. Provider selections

| Slot | Selected | Notes |
|---|---|---|
| PSP | **Paystack AND Flutterwave**, both customer-selectable | §C |
| Registrar — `.ng`/`.com.ng` | **NiRA, direct EPP** | Accreditation already held, so `.ng` is sold at registrar margin rather than through a reseller. RFC 5730/5731/5732, plus 5910 if DNSSEC is offered. Open sub-question: whether NiRA operations run over EPP today or the web portal — that sizes the connector. |
| Registrar — gTLDs | **Openprovider** | ~1,900–2,000 TLDs on one REST API with a full OT&E sandbox. Flat annual membership + cost-price beats volume tiers at launch volume, and it accepts wire transfer and multiple currencies, which matters paying from Nigeria. CentralNic Reseller was the runner-up on API rigour (OpenAPI 3.0) but is priced for larger resellers. |
| Authoritative DNS | **Self-hosted PowerDNS Authoritative** | Hidden primary + branded public secondaries, AXFR/IXFR with TSIG, DNSSEC live-signing on the primary, across at least two ASNs (on-prem AS328160 for in-country latency, Contabo for off-net diversity). Reached through an Integrator connector, never imported. |
| Hosting panel | **No single panel is selected, by decision** | Ruled 2026-08-23: cPanel and DirectAdmin are both admissible, concurrently, through the Integrator panel capability. DirectAdmin's flat per-server licence and cPanel's per-account pricing are a PROCUREMENT comparison per deployment, not an architecture choice — see §E.2. |
| Outbound email | **No provider is selected, by decision** | Ruled 2026-08-23: outbound email reaches its provider ONLY through an Integrator connector capability. Cloud names no email provider anywhere — see §E.1. |

Every name above is an installation binding. None may reach a schema column, a
lifecycle enum, or a business decision.

#### E.2 The provider-slot rule — every slot admits more than one provider

Michael's rulings on the PSP (2026-08-23), on outbound email, and on the
hosting panel are the same rule three times. It is recorded once, here, and it
governs EVERY provider slot in this decision — present and future.

**A provider slot is a capability, never a vendor.** For each slot:

1. The consuming Dotmac owner declares a versioned, provider-neutral
   **capability contract** plus a fake and a conformance kit.
2. One **or more** independently released Integrator connector distributions
   implement that capability. Several may be installed and offered at once.
3. Which connectors a deployment installs is **installation binding**. Which one
   a given subject uses is **data** — an opaque binding reference on the row.
4. No module, and not Cloud, may name a vendor in a schema column, an enum, a
   setting key, a class name, a conditional, or a typed identifier. A
   provider-issued identifier is opaque transport evidence.
5. **The test that the boundary is real:** adding, removing or swapping a
   provider must change no Cloud or module code — only bindings and data.

Applied to the slots in the table above:

- **PSP** — Paystack and Flutterwave are offered side by side today, and a third
  is additive. `dotmac-payments` holds an opaque provider-binding reference.
- **Outbound email** — no vendor is named at all (§E.1).
- **Hosting panel** — cPanel and DirectAdmin are both admissible, and a
  deployment may run both: acquiring a book of cPanel accounts must not require
  a code change or a migration. `dotmac-hosting` records an OPAQUE panel binding
  reference per account and expresses desired and observed state in
  provider-neutral terms. Where panels genuinely differ in what they can do,
  that is a **declared capability feature on the binding** which the owner reads
  as data — never `if panel == "cpanel"`. A panel that cannot pass the
  conformance kit is not offered.
- **Registrar** — already two connectors for one capability: NiRA EPP for `.ng`
  and Openprovider for gTLDs. Which one serves a given TLD is routing DATA, not
  a branch, and a third registrar is additive.
- **Authoritative DNS** — PowerDNS is self-hosted, which changes who operates it
  and changes nothing about the boundary: it is reached through a connector and
  is replaceable.

Panel and PSP economics (DirectAdmin's flat per-server licence versus cPanel's
per-account pricing; each PSP's fees) are real and they matter — but they are a
per-deployment PROCUREMENT comparison, re-made whenever prices move. Encoding
today's answer in code converts a reversible commercial decision into an
irreversible technical one. That is the mistake this rule exists to prevent.

#### E.1 Outbound email is a capability, not a vendor

Michael ruled on 2026-08-23 that the email provider connection goes over the
Integrator and that nothing hardcodes to a provider. This is stronger than
"pick a good provider later" — it removes the question from the bill of
materials.

- Cloud and every business owner emit **delivery-intent facts**. They never
  call an email API, hold an email credential, or name a vendor.
- The Integrator owns the outbound delivery capability
  (provisionally `messaging.email.delivery.v1`), and one connector
  distribution implements it. Which distribution is installed is an
  installation binding, exactly like the PSP, registrar, DNS and panel slots.
- Therefore **no `postmark_*`, `ses_*`, `sendgrid_*` or `mailgun_*` field,
  enum, setting key or conditional may appear in Cloud, in any module, or in
  any dossier.** A message ID returned by a provider is opaque transport
  evidence, never a typed provider identifier.
- Template content stays with `dotmac-template-studio`; consent, channel
  selection, suppression and receipts stay with the kernel messaging owners;
  retry, checkpoints and delivery evidence stay with `dotmac-integration`.
  The connector translates one wire format and nothing else.
- Deliverability posture (dedicated sending IP, SPF/DKIM/DMARC alignment,
  PTR, and separating transactional from marketing streams) is a DEPLOYMENT
  and DNS concern, given the fleet's recorded SPF/DMARC/PTR risk. It is
  satisfied by configuration and by the authoritative-DNS build, not by a
  vendor name in code.

Selecting the exact distribution remains a Phase 4 authorization under §6,
and it changes no Cloud or module code when it happens. That is the test that
this boundary is real.

### F. Storefront is a shared stateless module

Michael's ruling: build `dotmac-storefront` as a shared, stateless module rather
than Cloud-local presentation code.

Consequence, and it is a gate not a formality: hard rule 22 (ADR-0006
product-first extraction) applies. A product-first inventory across Sub, ERP,
CRM and Vendor CP, plus a checked-in ownership decision, MUST land before any
storefront behaviour code — with Cloud as first named adopter. Similarity
between existing checkout screens is explicitly NOT grounds for extraction.

Boundary: Storefront owns buyer-facing discovery, cart/checkout interaction,
purchase-progress presentation and provider-neutral ports. It owns NO offer,
price, order, invoice, receivable, collections, fulfillment or service fact, and
imports no sibling module. Prefer stateless: a persistent cart stays assembly
state unless the inventory proves a separate cart lifecycle and owner.

### G. Adoption order is unchanged — and does not block Cloud

Michael's ruling: KEEP the recorded product-first cutover order. Vendor CP
remains first cutover for Billing and Subscriptions; Sub remains first cutover
for Orders and Collections.

This does not delay Cloud, because the two claims are different. "First cutover"
names the application that migrates authority away from an EXISTING local writer
and retires it. Cloud is greenfield and displaces no writer, so installing a
published module is an adoption, never a cutover. §7 already lists both
dispositions; §2 already separates "complete package" from "adopted owner".

The residual risk is real and is accepted explicitly: a contract can move during
its first genuine cutover in Sub or Vendor CP, invalidating a Cloud pin. The
mitigation is that Cloud pins EXACT immutable versions and re-pins deliberately;
it never tracks a range.

### H. Domains and Hosting need fresh allocations

`dotmac-domains`'s provisional prefix `do` is INVALID: `do` is permanently
allocated to `documents` in `MIGRATION_OWNER_LEDGER`. An allocation is never
reused or repointed.

Allocated here, from prefixes free at `0.1.0a91`:

| Owner | short_code | schema | prefix | branch_label |
|---|---|---|---|---|
| `domains` | `domains` | `mod_domains` | `dn` | `domains` |
| `hosting` | `hosting` | `mod_hosting` | `hs` | `hosting` |

Billing (`bi`), Orders (`or`), Subscriptions (`su`) and Collections (`cl`) are
ALREADY allocated at `0.1.0a91`; they need no new ledger row.

Per the allocation rule, the ledger rows for Domains and Hosting merge against
current `main` BEFORE any Domains or Hosting source code is ported, as their own
kernel change.

### I. Every a75/a76/a77 kernel floor is obsolete

The commerce and Cloud candidates were written against `a74`–`a77`. The kernel
is now `0.1.0a91`, and Domains/Hosting cannot float on any floor that predates
their own allocation. Every provisional floor is reassigned to the first
published kernel that actually contains the required allocation and
capabilities — for Domains and Hosting that is the kernel release carrying §H,
which does not exist yet. No module may declare a floor naming an unpublished
kernel.

### J. Customer secrets and registrar-contact PII

**Domain transfer-in is OUT of V1 scope.** An EPP auth code is a per-operation
customer secret. `dotmac-integration`'s `SecretResolver` materializes references
held on a connector configuration revision — correct for a provider credential
that is stable per installation, wrong for a one-off code supplied by a customer
for a single transfer. V1 sells registration, renewal and DNS only. Transfer-in
returns when Integrator offers a per-operation secret channel, and it must be
approved separately.

Until then, and permanently thereafter: an auth code is **never** persisted as a
literal in Cloud, in Domains, in Fulfillment step or attempt evidence, in
Collections, in outbox payloads, or in delivery-attempt evidence. It is a
reference, or material loaded explicitly for one operation and never written
down. A saga that retries must re-resolve it, never replay a stored copy.

**Registrar-contact PII — proposed, requires Michael's approval before any
registrant payload is sent:**

- `dotmac-domains` stores the registry-required registrant/admin/tech/billing
  contact fields and the registrar's opaque contact handle. It is the only owner
  that holds them.
- Fulfillment carries an opaque contact reference in step payloads, never
  contact values. The saga is a coordinator, not a PII store.
- Integration payload retention redacts CONTENT while preserving IDENTITY, so a
  delivery can still be proven, deduplicated and repaired after redaction.
- A registrar-mandated proxy/privacy service, where the TLD permits one, is a
  per-order option and an immutable line-snapshot fact — not a mutable profile
  toggle that silently changes what a past order bought.
- Retention runs to the registry-mandated minimum plus a named margin, and a
  legal hold suspends redaction. Both values are settings, not constants.

This section is a PROPOSAL. It is not approved, and no registrant payload may
leave Cloud until it is.

## Amendment 2026-08-23 — one receivable owner and a distinct Collections input

Michael accepted the implementation ruling that resolves §5e gaps G1/G2 and
the duplicate money defect. It does not declare either module released or
adopted, and it does not resolve the separate obligation-output or artifact-key
questions.

1. **Billing is the sole owner of `ReceivablePositionV1`.** Its identity carries
   the explicit `Scope`, source owner/exposure/version, Billing account, opaque
   subject and optional service. It publishes the already-derived
   `collectible_receivable`, while retaining `available_credit` and
   `prepaid_funding` as separate facts. It also carries service-period and
   due-date provenance, completeness, source authority and projection mode.
2. **Every public amount is `dotmac_kernel.money.Money`.** The candidate
   `dotmac_billing.contracts.MoneyV1` is deleted. Persistence may retain exact
   amount/currency/minor-unit columns, but those columns do not create another
   value contract.
3. **Billing owns financial state:** `open | partially_resolved | resolved |
   cancelled`. `reversed` is not a steady state. Reversal, refund and chargeback
   are immutable causal movements; their new version may reopen the position.
   Collections owns its case lifecycle and closure reason, not this state.
4. **Collections owns `ReceivableObservationV1`, not another position.** The
   consuming assembly translates the Billing fact into this narrow peer input.
   It contains the already-funded collectible amount and decision provenance,
   never `available_credit`, `prepaid_funding` or the old
   `funding_available` synonym. Neither module imports the other.
5. **Authority dimensions stay separate.** `internal | provider_owned |
   external_finance` says who owns the financial fact;
   `authoritative | shadow` says whether this projection may drive a decision.
   A field is never translated into the other.
6. **Unknown or unverified due-date evidence fails closed.** The assembly must
   preserve that status, and Collections cannot open/advance an automated case
   from it. The same applies to incomplete or shadow observations and to an
   advance exposure whose verified service period has not started.

The producer→assembly→consumer conformance canary must prove this mapping while
the import-independence gate proves that the mapping did not move into either
module. ADR-0020 A2's separate requirement that Collections ship a platform
plane is not decided by this amendment; a tenant-only package remains an
explicit implementation conflict, not a reason to weaken this contract.

**Implementation follow-up — 2026-08-23.** The unreleased
`dotmac-collections` candidate now resolves that conflict: its first lineage
contains explicit tenant and `platform_*` table sets, supports tenant-only,
platform-only and combined selections, and dispatches the same typed service
behavior from `TenantScope` or `PlatformScope` to the matching persistence and
kernel idempotency plane. Tenant rows retain FORCE RLS; platform rows have no
tenant column or RLS, are reachable by `platform_api`, are revoked from
`app_user`, and have no cross-plane foreign keys. This completes the module
shape only; it does not claim Sub, Vendor CP, Cloud or any production cutover.

**Contract-completion follow-up — 2026-08-23.** Implementation exposed three
ambiguities that the earlier amendment could not safely leave implicit. Michael's
direction to complete the production Cloud track settles them as follows; this
note amends the operative contract without rewriting the historical proposals.

1. `ReceivablePositionV1` keeps the identity already specified by Billing:
   `(scope, billing_account_id, currency)` at a posting-group source version. It
   is the account aggregate and carries the three exact financial lanes. It
   does **not** borrow `exposure_ref`, subject, service period or due-date
   evidence from whichever invoice happened to sort first.
2. Billing separately publishes `ReceivableExposureV1`, one immutable version
   per affected issued invoice. It carries the stable document exposure,
   subject/service, collection timing, service period, due-date evidence,
   Billing-owned financial state and only the already-derived collectible
   amount. The Cloud assembly translates this fact to Collections'
   `ReceivableObservationV1`; the account position is not a Collections input.
   Credit notes remain explicitly related to the invoice they credit, and
   allocation, deallocation, credit, void and reversal paths republish every
   affected exposure.
3. The Subscriptions producer contract is named
   `RatedObligationOutputV1`. `RecurringObligationDueV1` and
   `subscriptions.recurring_obligation_due.v1` retire; they are not aliases.
   Subscriptions owns that output, Billing owns `AcceptRatedObligationV1`, and
   only an assembly may translate between them while supplying product-owned
   account/subject/service links and frozen tax evidence.
4. Official artifact creation and physical repair have different identities.
   `RecordDocumentArtifactV1` is the one-time structural record for
   `(scope, fact_id, fact_version, media_type)`; an identical payload replays
   and a different payload conflicts. `RepairDocumentArtifactV1` must name the
   exact current artifact, a replacement opaque file id and byte evidence, the
   unchanged presentation-model digest and a declared reason. Repair appends a
   new current relation and supersedes the former row in the same transaction.
   A stale, withdrawn or semantically different repair is refused. This
   replaces both contradictory draft key compositions: checksum-in-the-record
   key could not repair identical bytes at a new file id, while an in-place
   pointer update would erase the physical history.

These rulings complete the three contract gates recorded in §5e. They do not
release either package and do not establish a product adoption or deployment.

## Amendment 2026-08-28 — Sub is first cutover for Billing and Payments

This amendment reassigns the term "first cutover" **for Billing and Payments
only** from the vendor control plane to `dotmac_sub`. **It changes the outcome
of §G in part, and §G is five days old and is Michael's own recorded ruling**,
so it states the reason rather than quietly superseding it.

**Subscriptions is expressly NOT reassigned.** An earlier draft of this
amendment extended the same reasoning to Subscriptions; that was wrong, and the
error is recorded here because the reasoning that produced it is seductive. See
"Where this reasoning stops" below.

### The two statements that cannot both hold

§G rules: *"KEEP the recorded product-first cutover order. Vendor CP remains
first cutover for Billing and Subscriptions; Sub remains first cutover for
Orders and Collections."*

§G also **defines** the term, in the sentence immediately following:

> *"First cutover" names the application that migrates authority away from an
> EXISTING local writer and retires it. Cloud is greenfield and displaces no
> writer, so installing a published module is an adoption, never a cutover.*

[ADR-0020](0020-billing-owns-operational-receivables.md) §6 says of the vendor
control plane: it *"has a live invoicing need and **no invoice rows or writer to
migrate**."*

Apply §G's own definition to ADR-0020's own description and the conclusion is
forced **for billing**: the vendor control plane cannot be first *cutover* for
billing, because it has nothing to cut over. Its billing work is an adoption, by
the same reasoning §G already applied to Cloud. Both documents are internally
correct; only the label was attached to the wrong application.

`dotmac_sub` holds the only billing writer in the fleet that can be retired —
`billing/payments.py` (6 814 L), `billing/invoices.py` (3 262 L),
`billing_automation.py` (2 629 L) and the rest of the writers in
[`commercial-retirement-ledger.md`](../inventories/commercial-retirement-ledger.md).

### Where this reasoning stops — Subscriptions

The premise is "Vendor CP has no writer to retire". That premise is TRUE for
billing and **FALSE for subscriptions**, and the canonical dossier says so:
`packages/dotmac-subscriptions/EXTRACTION.toml` `first_cutover` reads

> *"dotmac_vendor_control_plane is cutover 1 on the platform plane; **it retires
> its local offer-version writer and capability-code column.** dotmac_sub is
> cutover 2 on the tenant plane…"*

and its `local_copy_retirement` names the artifacts: *"Vendor removes
`vendor_cp.offers` writers/models/routes and the local `offer_versions` table
after zero drift."* That is a genuine local writer and a genuine retirement.
Vendor CP is therefore a real first **cutover** for Subscriptions under §G's
definition, and this amendment leaves that assignment untouched.

The generalisable lesson is the check, not the outcome: **the question is always
"does the named first cutover hold a writer to retire?", asked per module.** The
answer differed between two modules in the same programme. A reassignment that
travels from one module to its neighbours by momentum is not applying the
definition; it is assuming the answer.

If Sub should also go first for Subscriptions, that is a deliberate **sequencing
reversal** and must be recorded as one — with its own rationale about risk,
cohort size and evidence — never as a consequence of Vendor CP lacking an
invoice writer.

### Decision

1. **`dotmac_sub` is first cutover for Billing and Payments**, and remains first
   cutover for Collections and Orders (unchanged from §G). It already composes
   these as digest-pinned wheels with their lineages in `alembic.ini`, and has
   moved no authority.
2. **The vendor control plane remains first cutover for Subscriptions**, on the
   platform plane, retiring `vendor_cp.offers` and its local `offer_versions`
   table. `dotmac_sub` is Subscriptions cutover 2 on the tenant plane, exactly
   as `packages/dotmac-subscriptions/EXTRACTION.toml` already records.
3. **The vendor control plane's Billing work is an ADOPTION**, not a cutover,
   sequenced independently. It does not gate Sub, and Sub does not gate it —
   the plane separation ADR-0023 already describes.
4. **§G's residual-risk mitigation is unchanged and now also applies to Sub**: a
   contract can move during its first genuine cutover, so every adopter pins
   EXACT immutable versions and re-pins deliberately. It never tracks a range.

### What this amendment does not do

- It does **not** claim a module cutover has happened. Two collections writers
  are retired (`COL-R5`, `COL-R7` — see the ledger § 3.1), and neither moves a
  table's owner: one re-routes an in-process writer to its owning service, the
  other deletes dead code. **No authority has moved to a composed module**, and
  the ledger's module-cutover count remains zero.
- It does **not** release, re-release or re-version any package.
- It does **not** relieve Sub of ADR-0031. Vendor CP's approvals cutover was
  greenfield — the legacy estate was empty, so there was nothing to seal. Sub
  has live money data, so every Sub money slice is a cutover **with** data and
  ADR-0031 applies in full: observation, verification and switch in one
  transaction, under `LOCK TABLE … IN SHARE MODE`.
- It does **not** unblock the vendor control plane's billing cutover, which
  remains blocked by gap G4 (`InvoiceArtifactReconciler` has no module owner).
  That blocker is now simply off Sub's critical path.

## Context

Dotmac Cloud must not become a Blesta-shaped application with Dotmac names. It
must compose business owners that can also be installed by Sub, Vendor CP, or a
future Dotmac application. Blesta, a PSP, a registrar and a hosting panel are
replaceable transports; none is allowed into a product schema, lifecycle enum,
or business decision.

The earlier Cloud solution separated the right concerns but described them as a
Cloud application's internal feature list. That is not enough for build-once
reuse. A feature package that is only complete when imported beside six sibling
packages is a distributed monolith, not a composable unit.

Michael directed a stronger rule on 2026-08-15: each named owner is built and
verified individually. This decision defines what “complete” means, fixes the
ownership matrix, and sequences the work without creating parallel owners.

## Decision

### 1. The business owner matrix

| Fact or decision | Sole owner |
|---|---|
| Stable offer and immutable offer/price versions | `dotmac-subscriptions` |
| Subscription contract, cadence, proration and recurring charge occurrence | `dotmac-subscriptions` |
| Customer order and immutable line snapshots | `dotmac-orders` |
| Rated-obligation acceptance, invoice, operational receivable, settlement acceptance and allocation | `dotmac-billing` |
| Payment intent lifecycle, transfer proof, and the append-only intent-to-settlement correlation | `dotmac-payments` (added 2026-08-23) |
| PSP credentials, provider webhook verification, wire mapping and delivery transport | a PSP connector plugin run by the Integrator |
| Effective-dated tax determination from typed source facts | `dotmac-tax` (added 2026-08-23; Billing freezes the determination into the immutable invoice snapshot) |
| Dunning case, versioned grace/escalation policy and delinquency-driven consequence request | `dotmac-collections` |
| Whether a requested service transition is permitted, and the actual transition | the service lifecycle owner — initially `dotmac-domains` or `dotmac-hosting` |
| Cross-owner fulfillment saga, step attempts, compensation and convergence | `dotmac-fulfillment` |
| Dotmac domain-service lifecycle and interpretation of registrar observations | `dotmac-domains` |
| Dotmac hosting-service lifecycle and interpretation of panel observations | `dotmac-hosting` |
| General ledger, journals, fiscal periods, statutory accounting and tax returns | Dotmac ERP |

The Collections row is deliberately narrow. Customer cancellation, abuse,
security and operator action have their own initiating owners. Collections can
request only a consequence justified by its own delinquency case. A request is
not permission and not a state write: Domains or Hosting locks and revalidates
its own facts, applies or refuses the transition, and returns a receipted
outcome.

Registrar and panel facts remain external observations. `dotmac-domains` and
`dotmac-hosting` own Dotmac's desired state, policy decisions and customer-visible
lifecycle; connector plugins own provider I/O. A collector records a typed,
deduplicated observation, a local resolver derives drift, and the lifecycle
owner decides any consequence. A provider callback never assigns a Dotmac
service status directly.

### 2. Complete means independently releasable for a declared contract

“Fully built” does not mean every future feature. It means the module's declared
version-one contract is complete and can be installed, migrated, tested and
operated without importing another business module.

Every owner must ship, as applicable:

1. a precise positive contract and an equally explicit `NOT` boundary;
2. one lifecycle/decision engine and one canonical writer per owned state;
3. typed commands, facts, observations, outcomes and stable error classes;
4. idempotency identities and conflict rules, with non-transactional effects
   leaving through the durable outbox;
5. its own models, namespace and migration lineage where stateful, with the
   declared persistence plane and live PostgreSQL isolation canaries;
6. a provider-free fake/conformance kit for every port it publishes or consumes;
7. reconciliation that can rebuild derived state and repair missed delivery;
8. source parity tests plus fresh invariant, failure, replay, concurrency and
   drift tests;
9. package/wheel, manifest, migration, import-independence and public-surface
   verification; and
10. an `EXTRACTION.toml` dossier naming source code, preserved tests, consumer,
    first cutover, shadow proof and local-writer retirement gate.

“Complete package” and “adopted owner” are separate claims. A package becomes
complete when the contract above passes. It becomes adopted only when a real
application runs the exact version, switches authority through a measured
shadow/cutover, and retires the displaced local writer. A green test suite may
not be reported as a cutover.

### 3. Modules are peers; the assembly composes them

No module imports a sibling or reads its tables. The consuming application
translates one owner's published output into another owner's input and records
the receipt. The minimum Cloud flow is:

```text
subscriptions --immutable offer/price fact-----> Cloud assembly
orders --------order + line snapshots----------> Cloud assembly
billing -------coverage/receivable facts--------> Cloud assembly
domains -------domain command outcomes----------> Cloud assembly
hosting -------hosting command outcomes---------> Cloud assembly
collections ---delinquency consequence request--> Cloud assembly
fulfillment <--order/coverage/outcomes----------> Cloud assembly
                         |
                         v
                 Integrator capabilities
              PSP / registrar / panel plugins
```

The arrows are versioned commands/events through assembly adapters, never Python
dependencies between business modules. Provider names, endpoints, credentials,
webhook signatures, retry checkpoints and wire payloads stay in Integrator
connector distributions. In particular, no `blesta_client_id`, Blesta enum, or
Blesta status belongs in any module named above. A Blesta connector may exist,
but choosing it is an installation binding, not an architecture branch.

### 4. The matrix needs three enabling owners

The business matrix is correct but is not an executable dependency graph by
itself. Three already-audited cross-cutting capabilities must be delivered
without being absorbed into a business owner:

| Enabling capability | Owner | Why it is separate |
|---|---|---|
| Generation-safe due work and wake-up | `dotmac-durable-timers` (selectable dual-plane module) | subscriptions and collections must not each invent a scheduler ledger — and neither may the kernel, which is why this is a module: see §4a |
| Concurrency-safe document series | `dotmac-numbering` | billing owns what an invoice number means, not the reusable allocation engine |
| Deterministic issued-document bytes | `dotmac-document-rendering` | billing emits immutable facts; rendering produces bytes; `dotmac-files` stores bytes |

ADR-0017 already names the first two owners, and the rendering dossier names the
third. This ADR records them as prerequisites of the directed Cloud commerce
programme. It does not move invoice meaning out of Billing or timers into
Collections.

### 4a. Durable timers is a module, and it reuses the claiming engine

Ruled 2026-08-15 on `docs/inventories/durable-timers-sources.md`.

**Why not the kernel.** ADR-0028 plane selection applies to modules, not to the
kernel: the kernel has one unconditional lineage, so a capability placed there
adds its tables, policies, indexes, grants and possibly a third database role to
EVERY composed database — including adopters that never schedule anything — and
raises the kernel floor for all of them. That floor is already the binding
constraint on two cutovers (Vendor CP at `a45`, Sub at `a50`). Two tables and a
floor is the wrong price for a capability two owners use.

**What it owns.** Timer identity, generation, supersession, cancellation, and
staleness verification — the half the audit found genuinely product-first in
Sub (`app/models/durable_timer.py`,
`app/services/runtime_durable_timers.py`).

**What it must NOT build.** A claim loop. The kernel already owns claiming:
`claim_outbox_batch` / `settle_outbox_event` (`SECURITY DEFINER`, `FOR UPDATE
SKIP LOCKED`, stale-lease reclaim), `RelayPolicy` backoff and dead-letter, on
both planes, behind least-privilege roles, proven on real PostgreSQL in
`tests/test_outbox_relay.py`. `available_at` IS a due time. A module that ships
its own claim loop puts a second scheduler ledger inside one deployment, which
is the precise failure this ADR names owners to prevent.

**Gates before the first behaviour commit.**

1. This amendment and the matching ADR-0017 amendment are merged.
2. The kernel publishes `outbox_relay.v1` — a `PrerequisiteSpec` with a
   STRUCTURAL verifier, so a module declaring reuse is checked against the live
   catalogue rather than trusted. This is the same defect class kernel
   `0.1.0a66` closed for `idempotency_ledger.v1`: a facility consumed at
   runtime needs a name a module can declare.
3. The ten PostgreSQL proofs in the report are written FIRST. Sub's suite runs
   on SQLite behind a single-connection fixture, so every claim, lease and
   generation guarantee it appears to prove is unproven.

**Known source defects that must not be ported:** the 200-timer fire batch as
one transaction (one poison emission rolls back all 200, reselected first
forever, no attempts and no dead-letter); `ORDER BY generation DESC LIMIT 1 FOR
UPDATE`, which takes no lock on an empty predicate; a native enum; an
unvalidated event-type string; and no `tenant_id`, RLS or retention anywhere.
Sub's own SOT registry declares the facility `SHADOWING`, and
`collections.case_action_due` is scheduled with no consumer — so ADR-0017's
"complete and tested" characterisation of this source is wrong on both
adjectives.

### 5. Build order

Work completes one owner before opening the next package, except that adopter
integration may run after a package reaches its independent completion gate.

Two steps are product defect repairs rather than module work. They are listed
because the evidence found active authority bypasses that must not wait for a
cutover, and because a module inherits a defect it was never told about.

1. **Commit the source dossiers as evidence.** Nine audits, no implementation.
2. **Amend this decision** before any implementation PR opens. An accepted ADR
   must not contradict its own attached evidence.
3. **Fix Sub's manufactured-funding path immediately** — see §5b. This is an
   active authority bypass in a live product, not a migration task.
4. **Harden Vendor CP's offer-version immutability if it is live** — see §5a.
   **Status 2026-08-15: CONDITIONALLY SKIPPED — no operational runtime
   exists.** Skipped by condition, not done and not waived. Vendor CP has
   never deployed: the production-deploy workflow has zero runs, the
   deployments API holds zero records, and there are no releases or tags. A
   production environment, a named host and provisioned secrets now exist, and
   the human-approval blocker recorded on 2026-08-14 has cleared — but
   configuration is not execution. The defect is unchanged and still real on
   `origin/main`: `v002_offer_versions.py` grants `UPDATE, DELETE` on
   `offer_versions` to `platform_api`, with no later `REVOKE` and no trigger
   anywhere in the v001–v011 lineage. Hardening becomes due the moment any one
   of these appears: a successful production-deploy run, a non-empty
   deployments API, or host-side evidence of a running Vendor instance. Until
   then the fix lands with the extraction, because there is no live data to
   protect and no cutover to sequence.
5. **Build `dotmac-numbering`.**
6. **Build `dotmac-durable-timers`** — a selectable dual-plane module, on the
   gates in §4a, which must all be met before its first behaviour commit.
7. **Build `dotmac-billing`** — greenfield-after-inventory, see §5e. It is the
   commercial spine and has the deepest source audit, extraction dossier,
   parity ledger and first-adopter plan, but its two flagship capabilities are
   shadow or dead code in their own repositories, so the audit specifies the
   behaviour rather than supplying it. Resolve the contract contradictions in
   §5e before any behaviour code: `AcceptSettlementV1` and
   `InvoiceDocumentFactV1` freeze now, `ReceivablePositionV1` and the
   obligation output must first be reconciled, and `allocation`/`coverage` are
   not published at this stage.
   **`dotmac-document-rendering` is unblocked independently** once
   `InvoiceDocumentFactV1` is frozen — it does not wait for the rest of
   Billing.
8. **Build `dotmac-orders`, including structurally immutable accepted lines**
   — see §5b. Orders now precedes Subscriptions because the funding-authority
   defect it closes is live in Sub today.
9. **Build `dotmac-subscriptions` with structural publication immutability**
   — see §5a. It emits a recurring charge occurrence; the Cloud assembly
   translates that output into Billing's accepted-obligation input.
10. **Build `dotmac-domains`, then `dotmac-hosting`.** Both are
    greenfield-after-inventory — verdicts confirmed by measured negative
    inventory across eleven repositories — and therefore start from lifecycle
    contracts and failure/reconciliation canaries, not a copied provider API.
11. **Build `dotmac-collections`.** Port Sub's live and target dunning evidence,
    but emit only typed delinquency consequence requests. No service status or
    provider call exists in this package.
12. **Build greenfield `dotmac-fulfillment` on the kernel participant
    contract** — see §5d.
13. **Complete the Integrator secret resolver** — see §6. Until an installation
    can pass connection validation with materialized secrets, no connector can
    be called operationally complete.
14. **Select the actual initial providers and amend §6** with the exact
    connector distributions authorized. No wildcard authorization.
15. **Build and certify those named connectors** against the stable owner ports.
16. **Compose Dotmac Cloud and prove the journey:** offer → order → obligation →
    independently confirmed settlement → coverage → per-line fulfillment →
    active service → renewal → delinquency request → permitted/refused service
    consequence → restoration. Test partial fulfillment and provider-success /
    callback-loss before external customers.

This order does not mean one giant release. Each numbered owner ends at its own
completion gate and can be reviewed, versioned and adopted independently.

#### 5a Subscriptions — immutability is built, not ported

Vendor CP remains useful evidence for exact money, platform-plane operation,
`(offer_code, version)` uniqueness and declared capability membership. It is
**not** a source of structural immutability:
`alembic/versions/v002_offer_versions.py` grants `UPDATE, DELETE` on
`offer_versions` to `platform_api` — the online API's own role — with no
trigger and no revoke, version numbers are caller-asserted, and there is no
digest or previous-version link, so a publication delta cannot be
reconstructed.

The reusable module must therefore ADD, as new invariants:

- database refusal of `UPDATE` and `DELETE` for published versions;
- an immutable-row trigger or equivalent structural guard;
- module-assigned version numbers taken under a lock;
- `previous_version_id`;
- a canonical content digest;
- same-key/same-fingerprint replay, and conflict on changed publication input;
- append-only publication history; and
- no meaningful `updated_at` on an immutable version.

If Vendor CP is operational today, harden it in place rather than waiting for
extraction.

#### 5b Orders — port the aggregate, build the snapshot

Orders remains product-first for the order aggregate, acceptance workflow and
handoff behavior, sourced from Sub. Immutable accepted lines are a **mandatory
greenfield delta**: no source in the fleet provides them. Sub's line update
`setattr`s any field with no status guard and carries `onupdate=`; ERP mutates
shipped/invoiced counters on the line; none of the three references an
immutable price version, and Sub's own guard stops at the quote.

The corrected instruction is: port Sub's order identity, acceptance and handoff
behavior, then add structurally immutable accepted line snapshots and exact
price-version provenance as a mandatory new invariant.

A valid accepted line carries BOTH halves:

- the copied commercial values — description, quantity, unit price, discounts,
  tax inputs and total; and
- the immutable offer/price/specification **version identities** those values
  came from.

Copying `unit_price` while retaining only a reference to a mutable offer is not
a snapshot.

**The Sub funding defect is a separate, immediate product fix.** An operator can
manufacture funding: `payment_status` is accepted by a generic order update
command, promoted to paid, and emits `funding_satisfied`, creating subscriptions
and provisioning. That must be repaired in Sub now, not at cutover:

- remove `payment_status`, `amount_paid` and `paid_at` from generic order update
  commands;
- enforce that inside the owning service, not by hiding form fields;
- derive funded/paid state only from accepted settlement or exact funding-gate
  evidence;
- give cash/manual settlement its own Billing command, permission, audit
  evidence and idempotency identity;
- give waiver or deliberately extended credit a distinct command and state —
  never represent it as payment; and
- add a canary proving an operator with ordinary sales-order write permission
  cannot produce `funding_satisfied`.

Orders then becomes the first adopter of Billing's coverage/funding fact once
the shared modules exist.

#### 5c Domains and Hosting — verdicts stand, with two consequences

Both greenfield rulings are confirmed by measured negative inventory. Two
consequences follow:

- The existing `TenantDomain` catalogue is a **different owner on a different
  plane** — a platform-plane tenant-hostname routing catalogue whose write
  privileges are granted to `platform_api` only. `dotmac-domains` is
  tenant-plane and must neither import nor write it; the database grant already
  makes the separation physical rather than conventional.
- **Remove any claim that Blesta is the quickest existing implementation.**
  There is no Blesta code anywhere in the fleet — zero tracked matches across
  all repositories. Blesta must now compete with direct registrar and panel
  integration on measured API quality, commercial terms and implementation
  effort, with no incumbency advantage.

DNS remains a **separate connector capability family** from the registrar,
because registrar and authoritative-DNS providers are commonly replaced
independently.

#### 5d Fulfillment — greenfield on the kernel participant contract

`dotmac-fulfillment` is **greenfield-after-inventory**. Sub supplies legacy
cutover requirements and negative evidence, not a reusable saga implementation.
The mandatory existing foundation is the kernel provisioning participant
contract and its conformance kit
(`packages/dotmac-kernel/src/dotmac_kernel/providers/provisioning.py`).

The evidence: `saga_executions` and `provisioning_step_executions` exist in
Sub's migrations and nowhere else — no model, service, test or caller — and
`saga_executions` carries foreign keys to `ont_units` and `olt_devices`, so the
"generic" table is bound to ISP hardware at the schema level. What actually
executes provisioning is a synchronous in-process loop that writes no step
rows, breaks on first failure, and is recovered by a 30-minute wall-clock reaper
that marks runs failed without re-observing the participant.

The new module owns: saga executions; ordered business steps; append-only step
attempts; participant command correlation; asynchronous outcome receipts;
compensation decisions and receipts; partial completion and convergence; and
reconciliation and operator repair.

Do **not** port `saga_executions`, `provisioning_step_executions`, the
synchronous loop, or the reaper.

The kernel participant contract is extended by exactly four things:

1. `participant_code`, an open registered vocabulary;
2. explicit scope via `TenantScope`/`PlatformScope` — never a nullable
   `tenant_id`;
3. typed asynchronous outcome envelopes. The local Fulfillment importer
   deduplicates them and calls the owner; **the Integrator never writes
   Fulfillment tables**; and
4. compensation as an explicit capability. Some operations — domain
   registration above all — cannot safely be reversed, so a participant must be
   able to return `not_supported` or `manual_required`. Compensation must never
   mean "guess the inverse operation."

### 5e. Billing is greenfield-after-inventory, and its contracts block it

Ruled 2026-08-15 on `docs/inventories/billing-source-variance.md`. The ownership
ruling in §1 is unchanged; only the SOURCING classification moves, from
product-first to **greenfield-after-inventory** — the same standard applied to
Fulfillment in the first amendment.

Both capabilities the port plan rested on turn out not to be the live path in
their own repository:

- Sub's ADR-0007 obligation stack is `SHADOWING` **by its own declaration**, and
  every row carries `BillingRecordAuthority.shadow` — "nothing may read it as
  money". The live invoice path contains zero occurrences of `obligation`.
  Obligation acceptance in Sub has never raised an invoice.
- ERP's `coverage.py` — called "the single highest-value port in the programme"
  by the parity ledger — has zero references under `app/`. The only production
  import is a constant, taken by two modules that then re-implement the rule.

Scenarios port; owners largely do not. The parity suite remains the acceptance
target, but as a specification of required behaviour, not as evidence that
behaviour exists.

**Contract contradictions block behaviour code.** Freezing the six contracts in
§5 was already the stated gate; the revalidation shows two are freezable now and
three cannot be:

- freezable: `AcceptSettlementV1`, and `InvoiceDocumentFactV1` after a
  `document_profile_code`/`template_profile_code` rename. That is enough to
  unblock `dotmac-document-rendering` independently of the rest of Billing.
- contradictory: `ReceivablePositionV1` is specified twice incompatibly and
  omits the service-period field `prepaid_policy.py:57` consumes; the obligation
  output carries three competing names; the artifact relation's key composition
  is contested (`commercial-composition-and-conformance.md:551` — "the key
  compositions cannot both ship").
- not to be published at all yet: `allocation` and `coverage` have no agreed
  shape, and §5's sentence listing them among the contracts to freeze is
amended accordingly.

The historical blocker is resolved by the two 2026-08-23 amendments above: one
account/currency Billing position, one exposure-grained Collections input,
kernel Money, preserved service/due evidence, Billing-owned financial state,
one Subscriptions output name, and separate artifact-record and repair keys.

`InvoiceArtifactReconciler` still has no module owner, and `cadence.py` must be
struck from Billing's `source_paths` — it is recurrence, which §1 assigns to
Subscriptions, and it is the only candidate path that breaches a not-owned
category.

Money is a data project, not a type choice: Sub carries six precisions and none
is `NUMERIC(20,6)`; `Invoice` has no `amount_paid` column at all, so the
coverage operand must be reconstructed from settlement history; and there are
125 float-on-money casts across 33 files where the dossier recorded one.

### 6. Implementation authorization and gates

Michael's direction to start the composable Cloud modules is the named
owner-directed exception required by ADR-0017 for the three prerequisites:

- `dotmac-numbering`;
- `dotmac-durable-timers`; and
- `dotmac-document-rendering`;

and the seven business owners:

- `dotmac-billing`;
- `dotmac-subscriptions`;
- `dotmac-orders`;
- `dotmac-domains`;
- `dotmac-hosting`;
- `dotmac-collections`; and
- `dotmac-fulfillment`.

The exception removes the moratorium for those ten names only. It does not pretend
P11 is production-proven, relax live PostgreSQL migration/plane gates, create a
consumer by assertion, or allow a package before its product inventory and
`EXTRACTION.toml` are complete. Namespace allocation still occurs in the same
change that creates the stateful package.

The enabling owners' existing source rulings remain authoritative;
implementation must follow their own dossiers and named source code rather than
this ADR inventing a second contract. Where a dossier is incomplete, the
exception permits completing the audit; it does not turn missing evidence into
permission to greenfield.

#### Connector distributions are NOT authorized

§5 sequences connector work; sequencing is not authorization, and this section
is the controlling text. No connector distribution appears in the ten names
above, so none may be implemented yet.

What IS permitted now: connector dossiers, capability contracts and conformance
specifications. What is blocked: real connector implementation.

The gate opens in this order — complete the Integrator secret resolver, select
the actual initial providers, then amend this section with the **exact** named
connector distributions. There is no wildcard authorization for arbitrary
future plugins.

#### Amendment 2026-08-17 — first exact connector authorized

Michael subsequently directed the external-integration programme through
completion without further decision prompts. That direction opens the gate for
exactly one distribution:

- distribution `dotmac-connector-whatsapp`;
- import package `dotmac_connector_whatsapp`;
- connector key `meta_whatsapp`;
- capability `messaging.receive.v1`;
- INGRESS mode only;
- SPI `>=1.2,<2.0`, with published `dotmac-integration 0.1.0a5` as the floor.

This is not wildcard authorization. Any other provider or a send-side WhatsApp
capability still requires its own product-first dossier and named release entry.
Publication also does not authorize product consequences: Sub remains the
message/conversation owner, and adoption requires a shadow cutover followed by
retirement of its direct provider surface.

The prerequisites the original text named are now satisfied by checked-in
mechanisms: Integrator materializes secret references through its installed
resolver; SPI 1.2 carries exact request bytes, provider acknowledgements and
verification evidence; and the release lane refuses an unpublished Integration
floor. The package, fixture conformance, allowlist row and installed-wheel proof
land together so authorization cannot outrun evidence.

#### Amendment 2026-08-19 — the authorized connector declares its runtime boundary

The exact WhatsApp authorization above advances from its historical a1
contract to `dotmac-connector-whatsapp 0.1.0a2`, targeting SPI
`>=1.3,<2.0` with published `dotmac-integration 0.1.0a10` as its floor. This is
not a second provider or a wider capability authorization. It makes the already
authorized ingress edge enforceable by declaring three exact logical secret
bindings—primary signing, optional previous signing during rotation, and
subscription verification—and an explicit empty provider-egress set.

Operator-chosen secret aliases are not a current contract. If a plugin reads a
name absent from its manifest, an assembly cannot derive a least-privilege
OpenBao policy from the installed package and the declaration is only prose.
The published a1 manifest and digest remain historical inside the a2
distribution for bounded adoption; current a2 configuration uses only the
manifest-owned names. The connector still performs no provider call, and Sub
still owns every messaging consequence.

#### The Integrator secret resolver is completion work

The audit found that the Integrator assembly has no secret resolver, so no
installation can reach `enabled` and therefore no connector can be dispatched
at all. This is completion work for the existing `dotmac-integration`
architecture, not a new business module. It must:

- materialize references through an installed `SecretSource`;
- load at startup and on explicit refresh — never perform network retrieval per
  dispatch (ADR-0009);
- retain the current working set if a refresh fails;
- fail enablement when required material is unavailable, rather than starting
  degraded; and
- never log, serialize or expose a secret value.

Until an installation passes connection validation with materialized secrets, a
connector is not operationally complete regardless of test coverage.

### 7. Application adoption matrix

“Shared module” means one versioned distribution installed locally by each
adopter. It does not mean one shared service or database. Every application runs
its own pinned copy, migrations, authorization, transactions and rows; cross-app
views synchronize typed data under ADR-0024.

| Application | Modules it adopts | Disposition |
|---|---|---|
| **Dotmac Sub** | `dotmac-billing`, `dotmac-subscriptions`, `dotmac-collections`, `dotmac-orders`, and `dotmac-fulfillment` | Sub is the product-first source for the first four and must cut over by shadowing the module and retiring each displaced local writer. **For fulfillment Sub is not replacing a generic saga engine — it has none. It is replacing its synchronous executor and 30-minute reaper** with the new module, implementing its ISP work as participants. ISP catalog, RADIUS, installation, field work and network activation stay in Sub as product participants/links. |
| **Dotmac Cloud** | all seven business modules in §6 | Cloud owns its local order, receivable, subscription, service and saga rows. It is not a façade over Sub and never reads Sub's database. |
| **Vendor CP** | platform planes of Billing, Subscriptions and Collections only | Existing ADR-0020 composition; it retains vendor agreements, approvals, allocation/licensing and consequence execution. |
| **Dotmac ERP** | none of the seven | Receives immutable accounting facts and retains GL/statutory authority. ERP's physical sales-order implementation remains ERP-owned. |
| **Dotmac CRM** | none | Its parallel sales-order and commercial writers retire; customer-experience projections arrive through versioned synchronization. |
| **Integrator** | none of the business modules | Runs `dotmac-integration` and independently released PSP/registrar/panel connector plugins; holds transport evidence only. |

`dotmac-domains` and `dotmac-hosting` are Cloud-only at first because no other
application has that lifecycle. Sub may display a customer's Cloud portfolio
through a rebuildable Cloud projection or link to the Cloud portal; it does not
install those modules merely to render navigation. If Sub later becomes a real
domain/hosting lifecycle owner, that is a deliberate new local adoption, not a
shortcut through Cloud's tables.

### 8. Four boundary decisions, resolved

These were raised as candidates by the source audits and decided by Michael on
2026-08-15.

#### 8.1 A business saga never owns connector-delivery retry state

FLEET-WIDE. `dotmac-fulfillment` may record immutable business attempts and
decide that a participant should be redriven. It may NOT carry a mutable
delivery `attempt_count`, delivery backoff, `next_attempt_at`, leases, delivery
dead-letter state, or connector health state. Those belong to
`dotmac-integration`.

Fulfillment may record `attempt_id`, participant outcome, error class, reason
and timestamps. Any count is DERIVED from append-only attempts. A business
redrive is scheduled through `dotmac-durable-timers`, never a connector
lease column.

#### 8.2 Capability-ID ownership is split

- The **business domain owner** owns each capability ID and its typed semantic
  contract. Domains owns the meaning of a domain-registration capability.
- **`dotmac-integration`** owns registry mechanics, installed-plugin
  declarations, binding validation and collision refusal.
- **Governance CI** enforces fleet-wide uniqueness and declaration/consumer
  completeness.
- **Connector plugins** implement declared capabilities. A registrar plugin
  declares that it implements the accepted version; it never mints the
  authoritative meaning.

#### 8.3 Capabilities are not split per lifecycle verb

Per-verb splitting is explicitly NOT a fleet convention. The default is one
capability per independently bindable lifecycle boundary — not one per method.
Split only where there is a real reason to select different providers,
credentials, release cycles or failure domains.

Initial families: PSP settlement/payment lifecycle; domain registrar lifecycle;
DNS zone/record lifecycle; hosting account lifecycle.

A capability declares its supported operations internally, so a panel that
cannot terminate reports that operation unsupported. Making create, suspend,
restore and terminate separately bindable would permit an incoherent
installation in which different providers claim different verbs for one hosting
account.

#### 8.4 Cloud v1 does not get a Customer Directory

No new enterprise customer-master module is built for Cloud v1. Instead:

- the IdP is the login-subject authority;
- Sub owns ISP customer/account facts;
- Cloud owns Cloud customer/account facts;
- an explicit **opaque correlation identifier** supports cross-sell;
- projection is **one-way and source-labelled** when an ISP customer onboards
  to Cloud;
- there is no email-based automatic linking; and
- there is no bidirectional "shared contact" promise.

If Dotmac later requires one editable organisation/contact profile across
applications, that is an independent Customer Directory with its own migration
and displaced-writer retirement plan. Installing the same Party model in two
applications would create two local row sets, not a shared authority, and must
never be described as one.

## Consequences

- Dotmac Cloud is a composition profile, not the owner of reusable commercial
  or service lifecycles.
- Blesta has NO incumbency advantage. The audit found zero Blesta code in the
  fleet, so "adopt what we already have" was never available. It competes with
  direct registrar and panel integration on measured API quality, commercial
  terms and implementation effort, and if chosen is a replaceable connector —
  never the billing or lifecycle authority in the internal-authority profile.
- Orders and fulfillment are separate. An order records what the customer
  bought; fulfillment records attempts to make it true.
- Partial fulfillment is natural because each order line has its own service
  command and outcome while the saga derives aggregate progress.
- ERP receives immutable accounting facts and remains the only GL/statutory
  accounting owner; operational invoices are not recreated in ERP.
- The first coding change after this decision is the prerequisite/source dossier
  for the first buildable owner, not seven empty package directories.

## Alternatives rejected

**One `dotmac-cloud` service owning every row.** Faster for the first demo, but
it makes the promised modules implementation folders rather than reusable
owners and makes every later extraction an authority migration.

**Blesta as the hidden lifecycle owner.** This makes provider status the real
truth and Dotmac's state a projection. Replacing Blesta would then be a data and
policy migration rather than a connector swap.

**Build all package skeletons first.** Empty manifests and interfaces create
parallel WIP, speculative contracts and no independently verified owner. The
programme completes one coherent owner at a time.

**Put fulfillment inside Orders.** The order becomes coupled to every service
type and retry policy. It also cannot express an immutable purchase snapshot
alongside a long-running, repairable saga without acquiring two authorities.

**Let Collections suspend directly.** A financial policy engine would become a
second writer of domain/hosting state and could bypass non-financial holds,
retention policy, transfer locks and destructive-action approval.
