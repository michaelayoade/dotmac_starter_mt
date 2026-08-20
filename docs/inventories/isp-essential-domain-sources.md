# ISP essential-domain source inventory

As of 2026-08-20. This is read-only extraction evidence, not a release or
cutover claim.

## Immutable source pins

| Repository | Audited ref | Revision | Disposition |
|---|---|---|---|
| Starter | `origin/main` | `828bc0968bb68bd8401c7227ae1334366cdc4b41` | Target package/runtime contract |
| Sub | `origin/main` | `552d0fdfce7ede36d430fe52ac1eaa4f06ee10d1` | Qualifying source for the first seven owners and the staffed-inbox/service-team evidence |
| CRM | `origin/main` | `60daaa2dd305696636632f48505ab784110a55d2` | Inbox workqueue/routing/presence evidence; workforce projection/retirement evidence |
| ERP | `origin/main` | `4aab56812d6fb243c814ada15c13fabea6234da8` | Qualifying workforce skills/shift/scheduling and FX-policy source |

Dirty local checkouts were not treated as current evidence. Candidate Starter
worktrees were inspected read-only; none contained any of the ten exact package
names. Existing candidates such as `dotmac-party`, `dotmac-inbox`, Positioning,
Network Access, Subscriptions, Billing, Collections, Fulfillment and Work Orders
remain separate owners and are boundary inputs, not code to absorb.

## 1. Customers — Sub first

Sub's `Subscriber` is the production customer-account aggregate, but it mixes
identity/contact, billing, network and reseller facts. The reusable slice is the
account identity/profile plus explicit Party-role references.

- Source: `app/models/subscriber.py`, `app/models/customer_identity.py`,
  `app/services/customer_context.py`, `app/services/subscriber.py`,
  `app/services/customer_portal_contacts.py`.
- Preserve: `tests/test_customer_context.py`,
  `tests/test_subscriber_party_binding.py`,
  `tests/test_subscriber_profile_cleanup.py`.
- Exclude: Party identity/reachability, credentials, reseller policy, billing,
  tax, portal session state, network configuration and subscriptions.

## 2. Service Catalog — Sub first

Sub proves plan-family publication and technical access/service attributes but
stores them beside commercial offers and subscriptions.

- Source: `app/models/plan_family_catalogue.py`, `app/models/catalog.py`,
  `app/services/catalog/plan_family_catalogues.py`,
  `app/services/catalog/validation.py`.
- Preserve: `tests/test_plan_family_catalogues.py`,
  `tests/architecture/test_plan_family_catalogue_boundary.py`,
  `tests/test_catalog_contracts.py`.
- Exclude: `CatalogOffer`, offer prices/versions, contract terms, cadence,
  recurring charges, Radius profiles and provisioning templates.

## 3. Qualification — Sub first

Sub has an explicit qualification model/service and GIS import proofs. Network
and positioning code supplies observations; it does not own the decision.

- Source: `app/models/qualification.py`, `app/services/qualification.py`.
- Preserve: `tests/test_qualification_services.py`,
  `tests/test_gis_qualification_imports.py`.
- Exclude: addresses/coordinates as master data, fiber topology, capacity
  observations, sales eligibility and fulfillment execution.

## 4. Services — Sub first

Sub's subscription lifecycle commands/evidence prove activation, suspension,
restoration and termination semantics, but `Subscription` also holds the
commercial contract and network projection.

- Source: `app/models/subscription_engine.py`,
  `app/models/subscription_change.py`,
  `app/services/subscription_lifecycle.py`,
  `app/services/subscription_lifecycle_commands.py`,
  `app/services/subscription_lifecycle_evidence.py`.
- Preserve: `tests/test_subscription_lifecycle_contract.py`,
  `tests/test_subscription_lifecycle_commands.py`,
  `tests/architecture/test_generic_subscription_lifecycle_boundary.py`.
- Exclude: offer/price/cadence, fulfillment steps, Radius/NAS/ONT state and the
  desired access-policy decision.

## 5. Usage — Sub first

Sub normalizes metering into `app/models/usage.py` and exposes usage services
and summaries. Raw Radius accounting/session rows remain Network Access facts.

- Source: `app/models/usage.py`, `app/services/usage.py`,
  `app/services/usage_summary.py`.
- Preserve: `tests/test_usage_services.py`, `tests/test_usage_metering.py`,
  `tests/test_usage_end_to_end.py`, `tests/test_usage_summary.py`.
- Exclude: raw AAA rows, access sessions, pricing and bill creation.

## 6. Usage Rating — Sub first

Sub's `app/services/billing/rating.py` proves quantity-band selection and exact
charge calculation, while recurring subscription rating lives elsewhere.

- Source: `app/services/billing/rating.py`, `app/models/usage.py`.
- Preserve: `tests/test_billing_rating.py`, `tests/test_usage_rollover.py`.
- Exclude: fixed-recurring charges, invoices, tax, FX, payment coverage and
  receivables. Output is a pre-tax obligation contract only.

## 7. Service Access Policy — Sub first

Sub has strong but split FUP, prepaid and collections paths. The shared product
decision is the desired per-service access state; Radius/network code is a
projection/enforcement adapter.

- Source: `app/models/fup.py`, `app/models/fup_state.py`,
  `app/models/prepaid_coverage.py`,
  `app/services/subscriber_access_policy.py`, `app/services/fup.py`,
  `app/services/prepaid_service_coverage.py`, `app/services/enforcement.py`.
- Preserve: `tests/architecture/test_subscription_service_access_boundary.py`,
  `tests/architecture/test_fup_rule_engine_boundary.py`,
  `tests/test_fup_period_aware_evaluation.py`,
  `tests/test_prepaid_service_coverage.py`,
  `tests/test_fup_enforcement_scope_and_evidence.py`.
- Exclude: Radius credentials/profiles, session disconnect, collections case
  lifecycle, funding ledger and account-wide shortcuts for per-service facts.

## 8. Inbox Operations — Sub/CRM adjudication

Sub is the qualifying staffed-inbox source and the first retirement target. CRM
adds the clearest queue, presence and provider-neutral workqueue contracts but is
already a consolidation copy; neither supplies the reusable conversation owner,
which is the separate `dotmac-inbox` candidate.

- Sub source: `app/models/team_inbox.py`,
  `app/services/team_inbox_assignment.py`,
  `app/services/team_inbox_routing.py`,
  `app/services/team_inbox_operations.py`.
- CRM evidence: `app/models/crm/queue.py`, `app/models/crm/presence.py`,
  `app/models/workqueue.py`, `app/services/crm/inbox/queue.py`,
  `app/services/crm/inbox/routing.py`, `app/services/crm/presence.py`,
  `app/services/workqueue/aggregator.py`.
- Preserve: Sub `tests/test_team_inbox_assignment.py`,
  `tests/test_team_inbox_routing.py`, `tests/test_team_inbox_complete_ops.py`;
  CRM `tests/test_crm_assignment_presence_guards.py`,
  `tests/test_two_queue_dispatch.py`,
  `tests/services/test_workqueue_aggregator.py`.
- Exclude: conversations/messages/read cursors, ticket/work-order lifecycle,
  transport/connectors, notifications, AI and Workforce shift/availability.

## 9. Workforce — Sub/CRM/ERP adjudication

The capability is mixed. Sub proves field service-team lifecycle, capacity and
dispatch. ERP is the qualifying skills/shift/schedule source. CRM synchronizes
ERP teams/shifts/technicians and is retirement evidence, not a second owner.

- Sub source: `app/models/service_team.py`, `app/models/dispatch.py`,
  `app/services/service_team_lifecycle.py`,
  `app/services/service_team_composition.py`,
  `app/services/capacity_planning.py`, `app/services/dispatch.py`.
- ERP source: `app/models/people/attendance/shift_type.py`,
  `app/models/people/attendance/shift_assignment.py`,
  `app/models/people/hr/skill_requirement.py`,
  `app/models/people/scheduling/shift_pattern.py`,
  `app/models/people/scheduling/shift_schedule.py`,
  `app/services/people/hr/skills_matrix_service.py`,
  `app/services/people/scheduling/schedule_generator.py`.
- CRM evidence: `app/models/workforce.py`, `app/services/workforce.py`,
  `app/services/dotmac_erp/shift_sync.py`,
  `app/services/dotmac_erp/team_sync.py`,
  `app/services/dotmac_erp/technician_sync.py`.
- Preserve: Sub `tests/test_service_team_lifecycle.py`,
  `tests/test_service_team_composition.py`, `tests/test_capacity_planning.py`,
  `tests/test_dispatch_services.py`; ERP
  `tests/services/test_schedule_generator_rotating_work_days.py` and
  `tests/services/test_attendance_service.py`.
- Exclude: employee identity/employment (People), payroll/leave/attendance
  consequences, Inbox agent presence, Work Order lifecycle and route execution.

## 10. FX Policy — ERP first

ERP is the sole mature owner of rate types, effective rate rows, task-sourced
observations, lookup and conversion selection. Its GL revaluation is a
downstream accounting consequence and does not port.

- Source: `app/models/finance/core_fx/exchange_rate.py`,
  `app/models/finance/core_fx/exchange_rate_type.py`,
  `app/services/finance/platform/fx.py`, `app/tasks/exchange_rates.py`.
- Preserve: `tests/ifrs/platform/test_fx_service.py`,
  `tests/api/test_fx_api.py`, `tests/finance/test_money_boundary.py`.
- Exclude: Currency/Money/ExchangeRate value types (kernel), GL revaluation,
  invoice snapshots, tax and provider credentials/network retrieval.

## Adoption and retirement gate

All ten first ship as `audit-complete` candidates with zero contract consumers.
Their dossiers must pin the revisions above, name a first cutover, specify full
shadow/drift fingerprints and identify the exact local writers to retire.
No package is allowlisted, published, composed or authoritative merely because
the Starter implementation and tests exist.
