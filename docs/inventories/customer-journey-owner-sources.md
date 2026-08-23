# Customer-journey owner source inventory

As of 2026-08-22. This is read-only extraction evidence, not a release or
cutover claim. Read it under the same two cautions as every file in this
directory ([README](README.md)): facts go stale, and a row here is not
permission to extract anything.

## Immutable source pins

| Repository | Audited ref | Revision | Disposition |
|---|---|---|---|
| Starter | `origin/main` | `f7a37385` | Target package/runtime contract |
| Sub | PR #2624 | `883a0ff1` | Qualifying source for all four owners |
| ERP | `origin/main` | `0e40d799` | Inventoried; qualifying source for none — see below |

The audit that produced this file walked Sub's customer journey end to end and
asked one question per step: **is there durable state here that no Starter
ledger row owns?** Four steps answered yes. Every other gap it found resolved to
an existing owner, to the Sub assembly, or to a boundary still needing
adjudication — those are recorded in the sequence note at the end rather than
turned into packages.

## 1. Service Orders — Sub first

Sub's `ServiceOrder` is the production delivery aggregate, and
`ProvisioningReadinessDecision`/`ProvisioningReadinessCheck` are already an
append-only decision with normalized evidence and a one-check-per-kind
constraint. The reusable slice is that decision, not the provisioning transport
it observes.

- Source: `app/models/provisioning.py` (`ServiceOrder`,
  `ProvisioningReadinessDecision`, `ProvisioningReadinessCheck`),
  `app/services/provisioning_lifecycle.py`,
  `app/services/service_order_lifecycle.py`.
- Preserve: `tests/test_provisioning_lifecycle.py`,
  `tests/test_sales_to_service_lifecycle.py`.
- Exclude: install appointments, provisioning workflows/steps/runs, vendor
  adapters, service state machine, project and task graph, work-order
  execution, IP assignment.
- Boundary change on port: Sub's `_evaluate_facts` READS Projects, Project
  Tasks, Work Orders and IP Assignments to build its checks. Those are other
  owners' facts, so the module takes normalized checks as input and keeps only
  the decision rule.

## 2. Payments — Sub first

Sub's `topup_intents` is the payment-intent record; `payments` carries the
confirmed fact; `payment_proofs` carries the bank-transfer evidence and its
review. The reusable slice is intent plus the correlation between it and an
external settlement fact.

- Source: `app/models/billing.py` (`topup_intents`, `payments`),
  `app/models/payment_proof.py`.
- Preserve: the source's top-up intent and proof-review suites.
- Exclude: invoices, credit notes, allocations, settlements, reconciliation
  evidence, refunds, reversals, withholding tax, ledger entries, collection
  accounts and provider credentials.
- Defect NOT ported: `uq_payments_active_external_id` is partial on
  `provider_id IS NOT NULL`, so CRM-origin payments fall outside it and needed
  a second partial index (`uq_payments_active_crm_external_id`) to stop a
  concurrent push double-recording cash. The module makes external-reference
  uniqueness unconditional per (tenant, provider type, reference).
- Shape change on port: opening and cancellation times are SUPPLIED by the
  caller, not read from the module's wall clock. `opened_at` is an
  authoritative business fact — when the payer was asked to pay — and the
  backfill carries Sub's real value; expiry is validated against it rather
  than against the moment the import runs, so an intent whose entire timeline
  has already passed is admitted on ordering alone. Without this the shadow
  below cannot be run at all: every migrated row would carry an import
  timestamp and report settlement-time drift against its own source.

## 3. Service Changes — Sub first

Sub's `SubscriptionChangeRequest` is the durable customer request, and its
`execution_state` is already the cross-domain checkpoint chain. The reusable
slice is the request, its decision, its evidence and the ordering.

- Source: `app/models/subscription_change.py`,
  `app/services/subscription_change_execution.py`,
  `app/services/events/handlers/subscription_change_execution.py`.
- Preserve: the source's change-execution suite.
- Exclude: catalogue offers, addresses, qualification decisions, fee pricing,
  invoices, credit notes, account adjustments, payments, service orders, work
  orders, Radius profiles and users.
- Shape change on port: Sub carries each crossed owner as a nullable FK column
  on the request. That cannot record WHEN a domain was reached, cannot hold two
  observations for one domain, and grows a column per collaborator. Typed
  append-only checkpoint rows carry the same facts without a schema change per
  owner.
- Defect NOT ported: `execution_state` is written by several handlers with no
  single guard, so a request can reach `fulfillment_released` with no
  settlement recorded. The module advances one declared step at a time.

## 4. Operational Escalations — Sub first

Sub's `OperationalEscalationPolicy`/`OperationalEscalationEvent` are the
production escalation decision. The reusable slice is policy terms and the
raised instance — not delivery.

- Source: `app/models/operational_escalation.py`.
- Preserve: the source's operational-escalation suite.
- Exclude: owners and watchers, room links and providers, notification
  channels' transport, `OperationalEscalationDelivery` and every delivery
  status.
- Defect NOT ported: the policy row is MUTABLE. Editing it silently rewrites
  the terms every already-open escalation was raised under, and nothing can
  read back what the policy said at raise time. The module makes versions
  immutable, allows exactly one ACTIVE version per policy (enforced by a
  partial unique index, not only by the writer), and binds each instance to the
  exact version.

## ERP inventory — inventoried, and why it is the source of none

The product-first rule (ADR-0006 amendment, `AGENTS.md` rule 22) is inventory
BOTH products before extracting, so this section records what ERP holds for
each of the four owners. It was added after
`test_every_shared_distribution_has_a_valid_extraction_dossier` correctly
refused four dossiers that named only Sub: an audit that never opened ERP
cannot claim Sub is the qualifying source, it can only claim nobody looked.

**Payments — ERP has a competing implementation, and it does not qualify.**
`app/models/finance/payments/` carries `PaymentIntent` (`payments.payment_intent`),
`PaymentWebhook` and `TransferBatch`, with `PaymentIntentStatus`, a
`PaymentDirection` INBOUND/OUTBOUND split and bank-account linkage for
settlement. This is a real second payment-intent record, not an incidental
name collision, and it is the one finding that changes what this file may
claim.

It is nonetheless not the extraction source, because the provider is welded
into the schema rather than carried as data: `paystack_reference` (unique),
`paystack_access_code` and `authorization_url` are first-class columns, and
`PaymentWebhook`'s identity is `paystack_event_id`. Porting it would make one
provider a structural property of every adopter's tables — the exact coupling
the module exists to remove. Sub's record is provider-neutral, so the module
keeps a generic `provider_type` and makes correlation unique per (tenant,
provider type, external reference), which is the invariant ERP's Paystack
columns express for a single provider and cannot express for two.

ERP's copy is therefore an adoption and retirement candidate for a later
cutover, not an authority and not a second writer. Its INBOUND/OUTBOUND
direction and settlement bank-account linkage are recorded here as capability
ERP has and the module does not; neither is ported now, because outbound
payouts are a different decision from customer settlement and belong with the
owner that adjudicates them.

**Service Orders, Service Changes, Operational Escalations — nothing in ERP.**
No service-order, provisioning-readiness, subscription-change or
operational-escalation model exists at the pinned ref. The near-matches were
checked and rejected by domain, not by name: `MaintenanceWorkOrder`
(`app/models/fixed_assets/`) is fixed-asset maintenance, not service delivery;
escalation in ERP appears only as approval routing inside expense limit rules
(`AUTO_ESCALATE`, `escalate_to_employee_id`) and HR grievance, which is
approval policy rather than an operational escalation instance; and
`RELOCATION` appears only as a project type and an HR allowance, not a
customer change request. For these three, Sub is the sole implementation, and
"Sub first" is a fact about the fleet rather than a preference.

## What the audit found and did NOT turn into a package

Recorded here so the same ground is not re-walked:

- `customer.experience_lifecycle` is a read composer; it belongs in the Sub
  assembly.
- Account lifecycle, close/restore/recovery: `dotmac-customers` already claims
  account lifecycle and `dotmac-records` already owns retention, legal holds
  and disposition. Sub PR #2624's
  `docs/designs/SUBSCRIBER_ACCOUNT_LIFECYCLE_SOURCES.md` records "No owner" and
  proposes a new Starter module; that claim is wrong and needs correcting
  before it becomes accepted evidence.
- Field expenses: `dotmac-expenses` already inventories Sub's implementation,
  so the governance rationale of "no ISP writer" does not hold.
- Migration mechanics stay in `dotmac-imports` — chunking, checkpoints,
  dry-run/apply, resumability. Each target module owns its own mappings.
- Addresses and Reporting are already-known build gaps, not new findings.

## Cohort-6 capability updates

Three existing owners were named in the same audit. Only one of them is a
Starter change:

- **Inbox Operations — BUILT (`0.1.0a3`, migration `io_0003`).** Sub's durable
  FIFO admission and round-robin rotation remain the product-first base. The
  a3 safety slice makes rules executable with durable decision evidence,
  accepts Workforce/product eligibility only as opaque references, locks queue
  position and presence capacity decisions, chooses the promotion winner
  inside the owner, and attempts one item per queue so a saturated queue cannot
  hide an assignable peer. Active-only uniqueness retains released assignments
  and settled entries while permitting later reassign/requeue cycles.
- **Surveys — no Starter change needed.** Its `EXTRACTION.toml` already names
  `dotmac_sub` as cutover 1 with the composition and retirement posture
  spelled out. What the audit found missing is a COHORT ASSIGNMENT in the
  governance programme, which lives in `dotmac_governance` (pinned by exact
  revision in `.dotmac/standards-profile.json`) and cannot be made from this
  repository.
- **Campaigns — no Starter change needed, same reason.** The package is
  audit-complete with Sub as cutover 1; the missing adoption cohort is a
  governance-repo row.

Both governance rows remain outstanding after this change. Recorded here rather
than left implicit, because "the package is fine" and "the programme knows when
it lands" are different claims and only the first is true today.

## Boundaries still needing adjudication

Not built, and deliberately: customer service agreements (generalize Commercial
Agreements with a tenant plane, or a narrow `dotmac-service-agreements`);
contractor delivery (split Procurement, Fiber Plant, Inventory/Payables and the
residual lifecycle first); work materials (extend Work Orders or extract
material requirement/consumption — not Inventory); appointments (Workforce
capability versus a dedicated owner); KYC/customer verification (establish the
owner and a provider-neutral contract first, with provider I/O in Integrator);
notification read state (typed storage, but no omnibus notifications module
until Kernel, Inbox and Template responsibilities are reconciled).

## Adoption and retirement gate

All four are `audit-complete` and uncomposed. Sub remains the writer for every
one of them until, per package, a history backfill, a read-only shadow and a
zero-drift comparison complete and a separately authorized writer switch
occurs. Service Changes additionally requires cohorts 2-5 to be composable,
because a change request that cannot record a Qualification, Billing, Payment
or Service Order checkpoint is not exercising the boundary it exists for.
