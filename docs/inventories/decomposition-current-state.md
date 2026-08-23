# Decomposition current state — Sub, ERP, CRM and UI

**Review date:** 2026-08-19  
**Starter base:** `300920fbb8be86b80934cdee624a814c90e44d85` plus the
uncommitted Orders/Sales handoff slice in
`feat/orders-eligibility-sales-handoff`  
**Sub:** `91c1ec477b3af37931424bced856a16bbc2c6d3f`  
**ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`  
**CRM:** `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`

This is a current-state checkpoint, not a replacement for the source audits,
ADRs or extraction dossiers. It answers three questions:

1. Which reusable owner boundaries already exist?
2. Which coherent capability areas in Sub, ERP or CRM still have no settled
   module boundary?
3. Does `dotmac-ui` need another package split, or only more contract slices?

The word **decomposed** is reserved for the full state: a released package is
consumed on an exact pin, authority has switched, and the displaced local
writer is retired. `audit-complete` package construction is valuable progress,
but is not reported as decomposition by itself.

## Evidence method and limits

ERP and CRM had unrelated working-tree changes, so every source measurement in
this review used committed Git objects. Clean archives of the exact revisions
above were scanned with `scripts/fleet_decomposition_sweep.py` and
`scripts/fleet_fact_registry.py`. The latter remains a direct-import
reachability heuristic: it measures declarations and detected edges, not
fact-to-table ownership.

Concurrent Starter worktrees were inspected only to identify active ownership
work. Their uncommitted files are **WIP evidence**, not as-built truth, release
evidence or adoption credit. In particular:

- Party is active in `feat/dotmac-party`;
- Domains and Hosting are active together in the stacked Domains worktree;
- Inventory, Assets and the nine network candidates are active in
  `integration/network-module-suite`;
- Accounting, Payables, Banking, Tax, Payroll, Expenses, Procurement and
  fixed-asset Finance are active in separate ERP-source worktrees; and
- Document Rendering is assigned to another active session. The audited tree
  still contains no package directory for it, so this review does not report it
  complete or duplicate that work.

## Fresh fleet measurement

The countable duplication shape has not changed: **149 duplicated names**
(126 exact and 23 aliased), including **133 CRM↔Sub names**. The source sizes
have changed:

| Product | Frozen matrix | 2026-08-19 | Change |
|---|---:|---:|---:|
| ERP | 397 | 397 | 0 |
| CRM | 218 | 218 | 0 |
| Sub | 576 | 590 | +14 |

Sub's ownership registry grew from 30 domains / 426 services / 1,392 declared
facts to **31 / 455 / 1,464**. Detected direct-import edges now reach 448 of
590 tables, up from 428 of 576. These are useful declaration and reachability
counts; they do not prove that any particular table has an authoritative
writer.

The frozen ratchet was deliberately not rewritten. A check against the current
revisions fails because Sub added rows in seven classified families and one new
`other` family, while Vendor CP legitimately retired its local Approvals and
fleet-deployment rows. Updating the baseline before classifying those changes
would turn new monolith state into an accepted starting point.

### The fourteen new Sub tables

| Rows | Current reading | Required disposition |
|---|---|---|
| `ai_intake_policies`, `ai_intake_policy_versions`, `ai_intake_sessions`, `ai_intake_generation_attempts` | A coherent AI-intake/advisory lifecycle not owned by an existing Starter module | Run a source audit before naming a module; do not hide it inside Analytics or Inbox. |
| `authentication_bindings` | Local identity-binding state | Reconcile with the kernel external-identity owner; do not create another identity module. |
| `ncc_weekly_report_runs` | Regulatory-report execution evidence | Audit a regulatory-reporting owner; Document Rendering may render an artifact but must not own the report decision or run. |
| `network_map_asset_change_proposals`, `ont_service_configuration_heads`, `ont_service_configuration_revisions` | Network/topology/PON owner state | Map into the active Network Topology / PON Access suite and its Sub retirement ledger. |
| `customer_quote_lead_links` | Product-specific Party/customer ↔ Sales correlation | Keep as an assembly-owned typed link unless the Sales audit proves it is part of the accepted commercial aggregate. It is not a reason for another customer module. |
| `sales_order_waivers` | Product policy evidence affecting order eligibility | Map mechanically into the Sales→Orders eligibility contract or retain it as an explicitly named Sub policy-owner receipt; it may not remain an untyped bypass. |
| `support_ticket_comment_mentions` | Ticket/comment-to-notification correlation | Decide whether the fact is Ticketing evidence or a product link during Ticketing adoption; delivery remains outside Ticketing. |
| `inbox_team_round_robin_cursors`, `inbox_queue_notifications` | Contact-centre routing/workforce state explicitly excluded from `dotmac-inbox` | Audit one routing/assignment/capacity owner instead of widening Inbox. |

## Reusable boundary coverage

### Present in the current Starter branch

The current branch already carries reusable candidates for Billing,
Collections, Orders, Subscriptions, Sales, Inbox, Surveys, Campaigns, Projects,
Work Orders, Positioning, Ticketing, Analytics, Web Analytics, People,
Approvals, Integration, Files, Imports, Numbering and Durable Timers. Party,
Domains, Hosting, Inventory, Assets, the network suite and the ERP finance
cohort are active concurrent work, as listed above.

The Orders/Sales boundary is now complete at source level:

- Sales owns the finite fulfillment-eligibility requirement membership and
  freezes it with accepted price, terms, specification, tax and minor-unit
  evidence in `AcceptedQuoteHandoffV1`.
- Orders owns the later reasoned eligibility decision over that finite set and
  explicitly addressed owner receipts.
- Billing retains receivable, settlement, allocation and financial coverage
  meaning; neither Orders nor an assembly infers a verdict from balances.

The remaining gate is Observe validation followed by the Sub mapping, shadow,
sealed writer switch and local retirement. No consumer or adoption is claimed
by this source change.

### Package construction is ahead of adoption

With the exception of the packages whose dossiers name real consumers, most
domain candidates above still report no contract consumer. Therefore the
largest remaining decomposition tranche is not another round of package
creation. It is product adoption:

1. exact released pins and composed lineages;
2. backfill and read-only shadow comparison;
3. reconciliation to an accepted zero-drift gate;
4. one sealed authority switch; and
5. deletion or hard gating of every displaced local writer and fallback.

CRM↔Sub consolidation is especially important. Most CRM network, subscriber,
ticket, work-order, project and operational copies are retirement inputs into
the accepted Sub owners, not sources for new parallel modules.

## Owner areas still not decomposed

The rows below are the remaining coherent capability areas after removing work
already active in another session. A row marked **audit needed** is a candidate,
not an approved module name or boundary.

### Sub / ISP source

| Capability | Current evidence | Recommendation |
|---|---|---|
| ISP customer/service intent and lifecycle | `service_intent_control_plane`, subscriber lifecycle/change/extension/request models and product-owned service consequences remain outside Party, Subscriptions and the network suite | **Audit needed.** Party owns identity/capacity; Subscriptions owns recurring commercial terms; network modules own provider-neutral network/access facts. A narrow ISP service-intent owner must decide activation/suspension/termination and consume those peers without absorbing them. |
| Serviceability and qualification | `coverage_areas`, `geo_layers`, `service_qualifications`, offer availability and location/GIS decisions span Sub and CRM | **Audit needed.** Positioning owns observations, not coverage or saleability. Name one serviceability/qualification owner and retire CRM's copy into it through Sub. |
| Contact-centre routing and workforce policy | Teams, queues, assignments, capacity, presence and round-robin state are deliberately excluded from Inbox; Work Orders owns physical execution, not conversation assignment | **Audit needed.** Extract only after routing/assignment decisions, observations and workforce consequences are separated. |
| Regulatory reporting | NCC report runs and regulatory projections are a named Sub SOT domain with no Starter owner | **Audit needed.** Keep report meaning/run evidence separate from generic rendering. |
| AI intake and advisory | Four new AI-intake lifecycle tables plus the `ai_advisory` SOT domain | **Audit needed.** Treat model/provider I/O as Integrator or an AI-provider seam; keep the product decision and evidence owner local until the contract is named. |
| Generic fulfillment saga | A complete fleet audit exists, but no `dotmac-fulfillment` package exists | **Build still outstanding.** The active Domains/Hosting commands and implemented Durable Timers candidate reduce the old prerequisite gap, but the greenfield run/step/attempt/receipt owner and its first Cloud proof still need implementation. |
| Referrals, reseller commercial policy and commissions | Sub and CRM retain referral/reseller rows; Sales intentionally owns Lead→accepted Quote only | **Audit needed.** Do not widen Sales until referral eligibility, attribution and commission consequence are named separately. |

The active nine-package network suite covers IPAM, generic network inventory,
network observations, topology, assurance, control, fiber plant, network access
and PON access. It does not make the ISP customer/service-intent row disappear,
and it does not move provider clients into those modules; Integrator owns those
transports.

### ERP / back-office source

| Capability | Current evidence | Recommendation |
|---|---|---|
| Treasury and payment execution | Payables explicitly stops at obligations; Banking stops at observations/matching; no active candidate owns bank/rail selection, payment batches or disbursement execution | **Audit needed; highest ERP residual.** Keep it separate from Payables, Banking and Integrator transport. |
| Budgeting, consolidation, FX sourcing and statutory financial reporting | Accounting explicitly excludes all four; the active fixed-asset Finance slice excludes the general ledger and these concerns | **Audit separately.** Do not revive a broad `finance-ledger` bucket that gives several decisions one owner. |
| Workforce domains beyond the directory | People excludes attendance, leave, scheduling, discipline, performance, training and compensation. Payroll WIP covers calculation/run/liability, not those owners. | **Audit as separate coherent slices**, beginning with attendance/scheduling and leave because they are widely referenced inputs. Recruitment, performance/discipline, learning/training and compensation policy follow independently. |
| Forms/data capture | Seven ERP tables and an accepted disposition exist, but no package or active implementation worktree was found | **Audit needed.** Keep form definition/submission evidence separate from workflow automation. |
| Workflow automation | ERP rules, versions and execution state remain outside Approvals and Durable Timers | **Audit needed.** Approvals decides approval; Timers wake work; automation owns neither vocabulary. |
| Help/knowledge content | ERP's help/content state has no current Starter owner | **Audit later.** Zero duplication sequences this behind higher-risk owners; it does not exempt a coherent domain from modularization. Keep it distinct from document rendering and notification templates. |

Inventory, Assets, Procurement, Expenses, Accounting, Payables, Banking, Tax,
Payroll and fixed-asset Finance are not listed as missing because active
uncommitted candidates exist. They remain incomplete until integrated,
validated, released, adopted and their ERP writers retired.

### CRM / engagement source

CRM's first obligation is retirement, not a second implementation wave. Sales,
Inbox, Campaigns, Surveys, Projects, Work Orders, Ticketing, Positioning and
Party already cover most legitimate reusable boundaries or have active
candidates. The credible residual audits are:

- contact-centre routing/assignment/capacity/presence, shared with Sub;
- referrals, reseller attribution and commission policy;
- customer-retention/customer-success case lifecycle, which Sales and
  Campaigns both explicitly exclude; and
- AI intake/advisory evidence, shared with Sub's newly expanded source.

CRM still declares **zero** facts in a product SOT registry. That is acceptable
for rows being retired, but not for legitimate extraction sources. Before one
of the four residuals becomes a module source, CRM must classify its state as
authoritative, observation, projection or retirement input and name the writer
and transitions. Its checked-in guidance also contains stale claims that CRM
owns operational projects, work orders, quotes and customer-support tickets;
the accepted cross-application boundary assigns those operational owners to
Sub. Correcting that source document belongs in the CRM retirement change.

## UI decomposition recommendation

**Do not split `dotmac-ui` into more packages.** Its published surface is
already dependency-free and contract-sliced. The correct next step is targeted
adoption and additional named slices inside the same package, with the one
non-presentation contract placed in the kernel.

Current `dotmac-ui` slices are:

| Slice | State | Current consumers / next gate |
|---|---|---|
| semantic tokens | reuse-proven | Sub and Academy; ERP's live local CSS remains retirement debt |
| empty-state component | reuse-proven | ERP and Sub |
| map frame | audit-complete | No adopter; replace only the outer frame/sizing in a product proof |
| catalog grid | audit-complete | No adopter; Workspace and Academy still own local renderers |

Finish the two pending adoptions before adding a broad component library. Then
use this priority order:

1. **List contract and list rendering.** Sub has one mature
   `ListDefinition` / `ListQuery` / `PageMeta` contract used by 30 declarations
   across 27 importing production files; ERP and CRM have no equivalent typed
   contract. Move the transport-neutral query/state/KPI/action values into
   `dotmac-kernel`, then add token-native table/filter/sort/pagination rendering
   to `dotmac-ui`. First reconcile Sub's six live `data_grid` callers so the
   macro cannot remain a second pagination/query owner. ERP is the candidate
   independent proof after its list semantics are characterized.
2. **Recent activity panel.** Sub and CRM carry the same blob and reference it
   from 11 and 10 templates respectively. It is a credible small inert
   component once its raw palette is replaced with role tokens and its input is
   a typed display-only activity item. The product remains the official
   timeline and URL owner.
3. **Generic form behaviours, only after splitting product logic out.** Sub and
   CRM have byte-identical, live `form-validation.js`,
   `repeatable-fields.js` and `unsaved-changes.js`. The current repeatable file
   also embeds invoice tax/money arithmetic and customer-contact roles; those
   are domain decisions and cannot enter `dotmac-ui`. Extract only generic
   repeat/add/remove/reorder, dirty-state and accessibility enhancement under
   behaviour/conformance tests. Server validation and all money decisions stay
   authoritative.

Two negative rulings prevent scope creep:

- Sub and ERP's identical live `csv-parser.js` belongs with the tabular import
  front end and `dotmac-imports`, not the design system.
- CRM's copied alert and modal templates have no production caller; Sub is the
  only live user. Delete the dead CRM copies during retirement and wait for a
  real second contract before extracting them. Charts, Kanban, Gantt, network
  topology and product dashboards remain product-owned composites.

## Recommended programme order

1. Validate and finish the current Orders/Sales handoff record, then prepare
   the Sub shadow/cutover rather than expanding either module.
2. Integrate and validate the already-active Party, network, Domains/Hosting
   and ERP finance candidates without counting WIP as adoption.
3. Implement Fulfillment after its active participants are stable.
4. Start the uncovered source audits in this order: ISP service intent,
   serviceability, Treasury/payment execution, contact-centre routing, then the
   remaining workforce domains.
5. For UI, adopt Map Frame/Catalog Grid, then extract the kernel/list-rendering
   pair. Do not create another UI distribution.
