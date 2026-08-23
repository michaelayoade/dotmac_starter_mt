# Integrator adoption ledger

**As of:** 2026-08-19  
**Starter:** `459569a` (`origin/main`)  
**Sub catalogue source:** `0d27ab91`,
`app/services/integrations/registry.py`  
**Runtime evidence:** read-only Seabone staging snapshots for Sub, ERP and CRM,
plus ERP's staging-derived clone migration rehearsal. The uncommitted ERP
candidate is bound by base revision, patch digest and exact image digest; it is
staging evidence, not a release or production claim.

The machine-readable record is
[`integrator-adoption-ledger.json`](integrator-adoption-ledger.json). The exact
Sub catalogue projection is
[`integrator-sub-connector-catalogue.json`](integrator-sub-connector-catalogue.json).
The committed-source maps for applications that do not yet publish owned
capability catalogues are
[`Academy`](integrator-academy-source-catalogue.json),
[`ERP`](integrator-erp-source-catalogue.json), and
[`CRM`](integrator-crm-source-catalogue.json). They classify code; they do not
invent capability vocabulary or prove production use.
The parallel build lanes and sequential cutover order are in the non-authoritative
[`Integrator fleet adoption work packets`](../superpowers/plans/2026-08-18-integrator-fleet-adoption.md).
The ledger is also bound to the existing
[`external-connector-baseline.json`](external-connector-baseline.json), so every
measured application needs its own inventory disposition. Category counts are
discovery evidence, not capability inventory.
`scripts/integrator_adoption_ledger.py` validates the records and refuses
a shared-plane retirement claim while any blocker remains.

## What this ledger prevents

A WhatsApp callback cutover can retire WhatsApp **receive** without moving
WhatsApp send, payments, ERP, collaboration or any other integration. Deleting
Sub's shared control plane after that first cutover would break those survivors.
The ledger therefore treats two events separately:

1. **Capability cutover:** one binding, its provider machinery and its rollback
   boundary.
2. **Platform retirement:** every live binding has migrated or retired and the
   seven shared tables can be removed.

`retain-temporarily` is explicit debt, not a completed disposition. It lets
Paystack and Flutterwave remain under their existing financial owners while
billing work is deferred, but it blocks platform retirement.

## Evidence has three layers

| Layer | Answers | Can authorize retirement? |
|---|---|---|
| Fleet surface baseline | Which applications contain direct connector machinery, by category | No; a file count is not a capability |
| Declared catalogue | What the current Sub code can configure | No; capability is not usage |
| Staging snapshot | What a named staging database currently holds and which safety controls were observed | No; staging absence is not production absence |
| Production snapshot | Which bindings and legacy surfaces are live across the complete measured fleet | Yes, only with all seven coverage categories |

The seven required production coverage categories are the six ADR-0024
responsibilities measured by the external-connector ratchet—provider clients,
credentials, webhook verification, scheduling, checkpoints and delivery
retries—plus the integration tables themselves. An environment that measures
only one of them is partial evidence and stays `unmeasured` for retirement.

Coverage booleans are necessary but not sufficient. For ERP, CRM and Academy,
the deployed revision must match the committed source-map revision and every
mapped surface needs an `active`, `configured`, `historical` or `absent`
observation. For Sub, every declared connector capability needs a row, including
an explicit zero/absent row when it is not installed. Every production capture
also carries a positive database read-only proof. This prevents a capture
adapter that stopped querying half the product—or one that could mutate the
database it measures—from producing apparently authoritative evidence. A
staging rehearsal likewise selects the snapshot for the target cohort's
application; another application's database cannot satisfy its gate because
its file appeared first.

Snapshots may contain deployment identity, connector/capability identity,
states, counts and timestamps. They may never contain configuration, payload,
headers, consequences, secret references or secret values.

## Fleet inventory state

| Application | Current evidence | Disposition | What remains |
|---|---|---|---|
| Sub | Capability catalogue plus category counts | mixed | Production-derived usage reconciliation |
| ERP | Complete 21-surface staging census plus migration/survivor proof | unclassified | Land the census/upgrade repairs, then collect the same complete inventory from a named production target |
| CRM | Committed-source map plus category counts | retire | Prove each capability moves or has zero traffic before deleting the app |
| Academy | Committed-source map plus category counts | unclassified | Bind three ERP seams and SMTP to owned contracts; retain the lab proxy locally |
| Backoffice | Measured zero | not applicable | Stay at zero while typed domain ports replace ERP-local transport |
| Vendor CP | Measured zero | not applicable | Confirm production-derived zero and keep the no-growth ratchet green |

The application rows prevent a subtle false completion: Sub's catalogue cannot
stand in for applications that do not yet publish one. ERP, CRM and Academy now
have exact source maps at pinned revisions, but remain explicitly incomplete
until those surfaces are reconciled to named capabilities and production usage.

## Current catalogue dispositions

These are programme dispositions for the code-declared catalogue. Live status
still comes from a production snapshot.

| Cohort | Disposition | Wave | Why |
|---|---|---:|---|
| WhatsApp receive | migrate | 1 | First approved vertical; receive only |
| Fiber inquiry, lead capture, generic webhook | migrate | 2 | Continuing non-financial ingress/delivery |
| WhatsApp send/templates, Meta Social, Nextcloud Talk | migrate | 2 | Continuing communications transport |
| CRM capabilities | retire | 2 | CRM is being decommissioned; do not rebuild its connector |
| ERP capabilities | migrate | 3 | ERP remains an independent application with typed product ports |
| Paystack and Flutterwave | retain-temporarily | 4 | Financial adoption is separately gated and currently unauthorized |
| `dotmac.integrator.http` product-port binding dependency | migrate | 5 | The desired port stays; its dependency on Sub's shared tables does not |
| Catalogue-only 3CX and FreePBX | retire | — | No runtime or capability exists to migrate |

Every `migrate` row carries eight independently reviewable steps:

1. connector distribution;
2. typed product port;
3. product descriptor;
4. named reconciler;
5. secret mapping;
6. mirror evidence;
7. rollback plan; and
8. local retirement gate.

Only WhatsApp receive is programme-required before production inventory lands.
Seven of its eight steps are recorded; real mirror evidence is deliberately
`missing`. Other declared rows become mandatory when production evidence shows
they are live. A new live capability absent from the ledger is itself a blocker.

## Seabone staging observation

The read-only capture at 2026-08-18T06:39:35Z found one enabled sandbox binding:

| Connector | Capability | Installation | Binding |
|---|---|---|---|
| `nextcloud.talk@1.0.0` | `collaboration.message.send.v1` | enabled | enabled |

Row counts were installations 1, config revisions 1, capability bindings 1,
and zero checkpoints, subscriptions, deliveries and inbox receipts. The
preserved July databases checked on the same host predate these tables and
cannot supply the missing production population.

This observation also found `dotmac_sub_celery_worker_billing` running while
`celery-beat` was absent. That does not prove a billing task executed, but it
does prove that “no beat” is not a billing-containment gate. The staging
rehearsal must additionally prove the billing worker/queue is inert and compare
the named finance-state fingerprint before and after.

The complete ERP census now accounts for all 21 committed-source surfaces,
including explicit absence. It found one active CRM configuration and 10,759
CRM mapping rows; four configured Paystack settings, one active intent and 820
intent/webhook history rows; 150 external exchange-rate observations; four
SMTP configuration/profile rows; and 97,774 legacy identity-marker rows across
73 table/column identities. The database proof is one read-only,
repeatable-read discovery session plus one RLS-primed read-only organization
session. Financial cutover remains unauthorized.

The same source population was copied through a serializable-deferrable,
read-only logical snapshot into an isolated PostgreSQL container. Applying the
exact candidate there exposed three real upgrade blockers: `support.ticket`,
`pm.task` and `hr.employee_certification` existed without the primary keys
required by pending foreign keys. Each repair now refuses null or duplicate
identities before adding its key. The clone reached all five current heads,
including the later `20260818_dotmac_sub_customer_metrics` head.
Before/after financial counts, seven non-financial survivor counts and all 73
legacy-marker count rows match exactly; the marker evidence has the same
SHA-256 on both sides. The shared staging database, ERP app, worker, payment
paths and billing paths were not modified. This proves staging migration and
survivor preservation for the uncommitted candidate; it is not production
inventory and does not authorize a financial cutover.

CRM is unequivocally not a zero-use retirement candidate on staging: five
active Meta connector/OAuth rows, active Zabbix and email transports, 17,588
webhook endpoint/delivery rows, and 819,688 integration runs are present. That
turns “retire CRM” into a per-capability replacement/zero-traffic programme,
not permission to shut the application down after the first WhatsApp cutover.

## ERP product capture adapter

ERP now has an uncommitted candidate owner, `integration.adoption_inventory`,
on base revision `4aab5681`. It emits every source-mapped surface exactly once,
including absence, and aggregates global plus per-organization evidence through
one read-only tenant-catalog discovery session and one separately RLS-primed
read-only session per organization. It never emits tenant identifiers,
configuration, provider payloads, headers, credential references or values.
The production-style image invocation is
`python -m scripts.integrations.capture_adoption_inventory`; the earlier direct
script-path form depended on an editable development environment and is not an
accepted operator command.

The current exact Seabone image passed 36 focused
unit/architecture/migration tests, the live PostgreSQL write-refusal canary,
the staging-clone upgrade and a second complete census. Its 21 surface rows,
aggregate counts and financial fingerprint are unchanged from the first
candidate. The candidate state is bound by image digest and patch digest
because repository policy forbids committing without explicit authorization.
Production capture remains unmeasured until a target is explicitly named.

## Sub product capture adapter

Sub now has an uncommitted candidate owner,
`integration.adoption_inventory`, at source revision `0d27ab91`. It projects
the current `connector_definitions()` catalogue, emits an explicit absent row
for every capability without a persisted binding, counts all seven shared
integration tables, and refuses its CLI unless PostgreSQL proves the enclosing
transaction is read-only. It selects only aggregate identities and counts;
configuration, payloads, headers, consequences, credential references and
credential values are outside the contract.

The candidate passed focused unit and architecture tests in a clean Sub
worktree and against a fully migrated ephemeral PostgreSQL database on Seabone.
The migrated test database reached Sub head `541_staff_session_party_ratchet`;
the live canary then observed `transaction_read_only=on` under repeatable-read
and passed. The exact test container and network were removed afterward. This
proves the capture mechanism on staging infrastructure; it does **not** create
production evidence, because the owner is not yet merged/deployed and no
production target has been named.

## Current blockers

- production inventory is unmeasured for every required application;
- WhatsApp receive lacks real mirror evidence;
- every other continuing connector packet awaits production-driven scope;
- CRM lacks zero-traffic and deletion evidence;
- Paystack and Flutterwave are deliberately retained behind the closed
  financial gate; and
- the seven shared Sub tables still exist and serve survivors.

The normal command reports these blockers and exits successfully because the
record is honest:

```console
python scripts/integrator_adoption_ledger.py
```

The destructive-boundary command fails until the full goal is true:

```console
python scripts/integrator_adoption_ledger.py --require-retirement-ready
```

That distinction lets CI enforce truth today without turning a long programme
into a permanently ignored red build.
