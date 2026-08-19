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
| PSP credentials, provider webhook verification, wire mapping and delivery transport | a PSP connector plugin run by the Integrator |
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
