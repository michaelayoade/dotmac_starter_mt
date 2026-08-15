# Cloud commerce owner source audit

**As of:** 2026-08-15

| Repository | Revision inspected | Worktree note |
|---|---:|---|
| `dotmac_starter_mt` | `991213c5ccaf` | clean before this documentation change |
| `dotmac_sub` | `27c76aaeebb7` | clean |
| `dotmac_erp` | `0f4b1698ddbf` | 67 local paths present; source conclusions below use `git grep/show HEAD` |
| `dotmac_crm` | `c64b5aa0f790` | 3 local paths present; source conclusions below use `git grep/show HEAD` |
| `dotmac_vendor_control_plane` | `89848017d6b8` | clean |
| `dotmac_integrator` | `d014116e63ad` | clean |

**Decision:** [ADR-0030](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)

This is characterization, not implementation proof. It completes the required
portfolio pass before the new Cloud owners are opened. Each package still needs
its own `EXTRACTION.toml`, exact parity dispositions and sensitivity canaries in
the change that creates it.

## 1. Verdict by owner

| Target owner | Source ruling | Mandatory source / evidence | Excluded coupling |
|---|---|---|---|
| `dotmac-billing` | product-first; audit complete | existing `billing-sources.md`, `billing-extraction-dossier.md`, `billing-parity-tests.md`; Sub financial obligation/settlement/allocation/coverage behavior plus ERP structural deltas | subscription recurrence, dunning, provider transport, GL/statutory accounting, rendering and storage |
| `dotmac-subscriptions` | product-first; audit complete | existing `subscriptions-sources.md` and `subscriptions-extraction-dossier.md`; Sub cadence/contract/recurrence plus Vendor CP exact-money immutable publication deltas | ISP/RADIUS vocabulary, vendor agreement/approval/licensing, invoices/receivables, collections and providers |
| `dotmac-collections` | product-first; audit complete | existing `collections-sources.md` and `collections-extraction-dossier.md`; Sub live enforcement behavior plus its corrected shadow lifecycle | direct access/service writes, notification transport, PSP state and mutable/unversioned policy |
| `dotmac-orders` | product-first; dossier required | Sub is the qualifying product-neutral source; ERP is a physical-order requirement source; CRM is a retiring parallel fork | quote/CRM ownership, invoice/payment authority, subscriptions, installation projects, warehouse shipment and provider calls |
| `dotmac-fulfillment` | product-first; dossier required | Sub's service-order lifecycle, provisioning runs/readiness decisions, idempotent receipt and sole-writer tests | appointments, field projects, OLT/NAS/RADIUS, ISP subscription activation, provider clients and business-service lifecycle state |
| `dotmac-domains` | greenfield-after-inventory | no qualifying domain registration/transfer/renewal/DNS lifecycle implementation found in the inspected fleet | mobile push “registrar”, FreeRADIUS “domain”, provider-specific registrar payloads |
| `dotmac-hosting` | greenfield-after-inventory | no qualifying web-hosting account/package/suspend/restore lifecycle implementation found in the inspected fleet | infrastructure deployment provisioning, ISP provisioning, mailbox-only features and panel-specific payloads |

## 2. Orders: Sub is the mandatory starting point

### Sub

Sub has the current complete customer sales path:

- `app/models/sales.py::SalesOrder` and `SalesOrderLine` hold the order and line
  records, unique quote/order identities, money snapshots and lifecycle state;
- `app/services/sales_orders.py` owns quote acceptance, line copying, totals,
  funding transitions and idempotent creation behavior;
- `app/services/sales_order_funding.py` separates finite obligation evidence
  from mutable `amount_paid` projections; and
- `tests/test_quote_acceptance_workflow.py`,
  `tests/test_quote_financial_safety.py`, `tests/test_sales_lifecycle_chain.py`,
  `tests/test_sales_order_funding.py` and
  `tests/test_sales_to_service_lifecycle.py` cover replay, copied terms,
  financial safety and the order-to-service handoff.

`docs/PARTY_CUSTOMER_LIFECYCLE.md` states that Sub owns the complete customer
lifecycle and CRM has no runtime role. That makes Sub the mandatory code and
test source, subject to removing product coupling rather than copying the class
whole.

The generic port is intentionally smaller: order identity, customer reference,
currency, immutable accepted line snapshots, totals that are consequences of
those snapshots, cancellation rules, funding/coverage observation receipts and
published fulfillment request. Quote authoring, customer identity, invoice
links, product subscriptions, project creation and service activation stay with
their owners.

### ERP

ERP has a mature but physically oriented order aggregate:

- `app/models/finance/ar/sales_order.py`;
- `app/services/finance/ar/sales_order.py`; and
- `tests/ifrs/ar/test_sales_order_service.py`.

It covers approval/confirmation, quantities, partial shipment, shipment,
invoice conversion, tax and warehouse semantics. Those are valuable
requirements and negative tests, but its fulfillment vocabulary is not the
Cloud/customer-order lifecycle. Port exact line snapshots and legal transition
invariants where compatible; leave inventory, shipment and finance coupling in
ERP.

### CRM

CRM contains `app/models/sales_order.py`, `app/services/sales_orders.py`, API and
field-sales surfaces, with tests around commissions, plan metadata, projects and
payment synchronization. Its status vocabulary and base fields match the older
Sub shape, while its effects push the result to Sub. Because Sub now owns the
complete lifecycle, CRM is a retirement inventory and regression source, not a
second implementation to merge. The extraction must not preserve its direct
sync writers or `Person`/agent/commission coupling.

## 3. Fulfillment: port the engine shape, not ISP provisioning

Sub is the only qualifying production source for the reusable mechanics:

- `app/models/provisioning.py` has `ServiceOrder`, `ProvisioningWorkflow`,
  `ProvisioningStep`, `ProvisioningRun`, append-only readiness decisions/checks
  and explicit state transitions;
- `app/services/service_order_lifecycle.py` is the transport-neutral sole
  service-order status writer;
- `app/services/provisioning_lifecycle.py` evaluates facts, fails closed on
  idempotency-key scope reuse, records append-only readiness evidence and
  separates activation request from confirmation;
- `app/services/sales_fulfillment.py` demonstrates receipted consumption of
  committed owner outputs; and
- `tests/test_provisioning_lifecycle.py`,
  `tests/test_sales_to_service_lifecycle.py`,
  `tests/architecture/test_provisioning_lifecycle_sot.py` and
  `tests/architecture/test_service_order_status_writers.py` prove replay,
  evidence immutability and a single transition writer.

The source also contains exactly the coupling that must not port:
`InstallAppointment`, `Project`, `InstallationProject`, `Subscriber`, ISP
`Subscription`, OLT/NAS steps, VLAN/RADIUS behavior, and direct activation of
ISP subscriptions. `dotmac-fulfillment` owns only saga/run/step/attempt state,
participant command correlation, retry classification, compensation request,
aggregate outcome and convergence. Domains and Hosting own their service states.

Vendor CP's `src/vendor_cp/provisioning` is explicitly a side-effect-free
contract laboratory with an in-memory fake. Its conformance checks are useful
requirements for determinism and idempotency, but it is neither production
fulfillment nor a stateful source. Infrastructure deployment provisioning is a
different lifecycle and must not be renamed into customer-service fulfillment.

## 4. Domains and hosting: greenfield evidence

A revision-pinned `git grep` across the five sibling repositories searched for
DirectAdmin, cPanel, Plesk, EPP, nameserver, WHOIS, domain transfer/renewal,
registrar, web hosting and hosting account terminology.

- ERP, Vendor CP and Integrator produced no matching tracked files.
- Sub's matches were FreeRADIUS configuration and mobile push registration.
- CRM's matches were mobile push registration.

Those false positives do not implement domain or hosting service lifecycle.
There are no registrar credentials, availability/registration/transfer/renewal
services, DNS-zone owner, hosting account/package/suspension service, or parity
tests to port. The required ruling is therefore
`greenfield-after-inventory`, not “copy the first registrar SDK we choose.”

The first implementation source for each is its provider-neutral lifecycle and
failure contract:

- Domains: requested registration, registration observation, active, transfer
  in/out, renewal due/attempted/confirmed/failed, expiry/redemption observation,
  contact/nameserver desired state, drift and explicit refusal of release.
- Hosting: requested creation, active, change requested/applied, suspend
  requested/permitted/applied, restore, retention hold, termination approved and
  observed resource/usage drift.

Provider operations are connector capabilities in the Integrator. Sandboxes
validate a connector; they do not become the module's domain model.

## 5. Integrator and providers

`dotmac-integration` already owns installation, binding, secret references,
inbox/outbox, retries, checkpoints, health and repair. The separate
`dotmac_integrator` repository at `d014116e63ad` is the thin assembly that pins
and runs it. It is therefore an existing foundation, not another Cloud business
module.

No inspected repository supplies a production PSP, registrar or hosting-panel
plugin under the ADR-0024 SPI. Each connector needs its own provider-source
audit and conformance evidence after the relevant business input/output contract
is frozen. A Blesta plugin, if chosen for an interim rollout, is held to the
same capability contract as direct registrar/panel plugins and receives no
special branch in Cloud or a business module.

## 6. Required package dossiers

The existing Billing, Subscriptions and Collections dossiers remain the source
for those owners and must be copied into their package roots only when each
package is created, as their plans require.

Before code for the four Cloud-specific owners:

1. create `dotmac-orders/EXTRACTION.toml` with Sub parity dispositions and ERP/
   CRM exclusions;
2. create `dotmac-domains/EXTRACTION.toml` and
   `dotmac-hosting/EXTRACTION.toml` with
   `source_mode = "greenfield-after-inventory"` and this audit as evidence;
3. create `dotmac-fulfillment/EXTRACTION.toml` with the Sub lifecycle/readiness
   suites, every ISP coupling explicitly `not_ported`, and a Cloud consumer;
4. allocate namespace/prefix/lineage only in the same change as each stateful
   package; and
5. keep `contract_consumers` honest: candidate is not adopted, and a package is
   not reported reused until a second application cuts over and retires its
   local owner.
