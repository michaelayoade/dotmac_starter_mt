# Sub vNext parity capability source adjudication

**As of:** 2026-08-20
**Decision:** [ADR-0034](../adr/0034-sub-vnext-parity-capabilities-have-narrow-independent-owners.md)

This is the mandatory product-first inventory for the Sub vNext parity cohort.
Only immutable Git references were inspected; dirty working copies contributed
no source and no completion credit.

## Exact revisions inspected

| Repository | Reference | Revision | Result |
|---|---|---|---|
| `dotmac_starter_mt` | fetched `origin/main` | `fead57bc93d6551450f5e6ae1c9de1296e27b0ae` | Existing module owners, ADRs, namespace ledger and tests inspected. |
| `dotmac_sub` | fetched `origin/main` | `552d0fdfce7ede36d430fe52ac1eaa4f06ee10d1` | Qualifying Referral, Reseller, AI and NCC implementations; partial remote/support/health references. |
| `dotmac_crm` | fetched `origin/main` | `60daaa2dd305696636632f48505ab784110a55d2` | Duplicate/legacy Referral, Reseller, AI and NCC implementations; retirement targets. |
| `dotmac_erp` | fetched `origin/main` | `4aab56812d6fb243c814ada15c13fabea6234da8` | Qualifying Workflow and Forms sources; finance compliance scripts stay finance-owned. |
| `dotmac_integrator` | fetched `origin/main` | `d886e3c9956192fe1d5f085d352a516812c253c8` | Generic connector/runtime and observation-port boundary; no domain module source. |
| `dotmac_vendor_control_plane` | fetched `origin/main` | `2c4d88ab877aeae1c8d5aef0637bc013edf07aa9` | Existing Deployment Control consumer target and health/support design references; no implemented support/health owner. |
| `dotmac_workspace` | fetched `origin/main` | `a158846c97e39b007724584244cb1ed4f9e6e58f` | No competing capability owner. |
| `dotmac_academy_app` | fetched `origin/main` | `a5e25e4e829350e503e66a03d73739529ba7da7f` | No competing capability owner. |
| `dotmac_backoffice` | local `main` (repository has no `origin`) | `a66faff36a19ca8127838235f7b51dab07ba4371` | Supplemental negative scan only; not cited as remote authority. |

The Backoffice fetch failure is evidence, not hidden: `git fetch origin main`
failed because that checkout has no `origin`. None of the decisions below
depends on it.

"Production source" below means a mounted/migrated product implementation on a
default remote branch with non-test callers and parity tests. This inventory
does not invent a production row count or deployment assertion from source
shape alone.

## Adjudication matrix

| Capability | Production-path evidence | Compared overlaps | Retain/retire decision |
|---|---|---|---|
| Referrals | Sub `app/models/referral_native.py`, `app/services/referrals.py`, customer/admin adapters, `tests/test_referrals_native.py`, `tests/architecture/test_referrals_program_boundary.py` | CRM `app/models/crm/referral.py` + `app/services/crm/referrals.py` is the earlier owner and still has direct writers | **Retain** `dotmac-referrals` from Sub. CRM writer/tables retire only after module cutover; Sub's local tables then retire after its own module cutover. |
| Reseller Management | Sub `Reseller`/`ResellerUser`, `reseller_onboarding.py`, reseller portal/admin adapters and boundary/soft-delete tests | CRM dropped its dedicated reseller tables in `h2b3c4d5e6f7` but still models reseller Organizations, hierarchy, portal authority and commissions | **Retain** `dotmac-reseller-management` from Sub. CRM hierarchy/portal duplicates are retirement evidence; commission/payout calculation is not absorbed. |
| AI Operations | Sub `ai_intake.py` (config, immutable policy versions, sessions, generation attempts), `ai_insight.py`, `ai_conversation_intake.py`, `ai_operations.py`, mounted API/task paths and AI boundary tests | CRM carries near-parallel intake, insights, provider gateway, personas and transcription code; Integrator owns the generic connector/SPI runtime but no AI domain state | **Retain** `dotmac-ai-operations` from Sub, removing provider/API/I/O branches. CRM/Sub provider clients retire into Integrator plugins during adoption. |
| Remote Access | Sub network desired state records enabled/expiry/source CIDRs and closes expired access through ONT reconciliation (`ont_features.py`, `ont_action_remote_access.py`, `network/reconcile/*`, tests) | No repository has a generic access request, approval reference, grant or revocation ledger. CRM has VPN configuration/cache, not this lifecycle | **Retain**, **greenfield after inventory**, using Sub expiry/fail-closed tests as the initial behavioral reference. Network Control remains the command executor. |
| Compliance Reporting | Sub `ncc_regulatory_pack.py` and `test_ncc_regulatory_pack.py` assemble native complaint/subscriber evidence plus typed back-office inputs and refuse fabricated fallback data | CRM holds the predecessor copy; ERP VAT/WHT scripts and tax-return state are finance-specific source facts, not the generic filing owner | **Retain** from Sub with a filing-lifecycle port delta. CRM pack retires; Ticketing complaint lifecycle and ERP tax decisions remain untouched. |
| Workflow Runtime | ERP `automation.workflow_rule`, immutable rule versions, `workflow_execution`, retry behavior, `WorkflowService`, API/UI and `tests/services/test_workflow_engine.py` | Sub has many domain workflows and tasks but no user-authored reusable resumable runtime; Fulfillment and Durable Timers already own orchestration/timing mechanics | **Retain** runtime-only from ERP. Product entity enums, effect dispatch, schedules, webhooks, email and domain mutations do not port. |
| Support Access | Sub `issue_impersonation_access_token`, read-only impersonation guard and reseller/admin impersonation tests prove an enforcement seam | Fleet-wide search found no request/consent/grant/expiry/revocation ledger. Vendor `domain-foundation.md` is design-only; Application Access owns standing cross-app grants | **Retain**, **greenfield after inventory**, for temporary support workflow only. Kernel enforces; Approvals decides. |
| Platform Health | Sub Redis task/job heartbeats and bounded operational snapshots; Integrator closed-label metrics/health report; Vendor licence-pipeline rollups | None is an authenticated, general application/runtime-health projection. Raw Prometheus/OTel/network monitoring remain observability concerns | **Retain**, **greenfield after inventory**, taking freshness/closed-label/no-consequence behavior from the references. |
| Fleet Control | Starter `packages/dotmac-deployment-control` 0.1.0a1, ADR-0033, rollout/observation unit tests and platform-isolation canary | No valid competing owner; Sub UISP reconciliation is device control, not application deployment control | **Adopt existing owner.** Never create `dotmac-fleet-control`; never duplicate kernel update authority. |
| Reusable Forms | ERP `app/models/forms/form.py` (seven tables), `FormEngineService`, recruitment callers and web submission tests | Starter/Kernel form macros are presentation only; Sub `form_contracts.py` is not an authored form/submission store; Workflow Runtime has no right to absorb it | **Retain separately** as `dotmac-forms`, product-first from ERP. |

## Required cutover evidence

Every retained product-first owner follows the same ordered gate:

1. Map every source row to accepted, typed quarantine or deliberate retirement;
   zero unclassified rows.
2. Install the exact released module lineage and backfill idempotently.
3. Compare complete aggregate fingerprints and refusal outcomes at a sealed
   source watermark, then replay the captured delta.
4. Switch one writer, revoke/disable the old mutation paths and prove the seal.
5. Keep a rollback window; then remove old routes, jobs, tables, settings and
   provider credentials. Lower a bidirectional retirement ratchet in the same
   change.

Capability-specific retirement impacts:

- Referrals preserve code identity, attribution, Party/customer/lead bindings,
  qualification/reward evidence and unfulfilled reward references. The module
  emits conversion/reward requests; it never writes Customer, Lead or Billing.
- Reseller migration preserves hierarchy, status, party/account references,
  delegated-authority revisions and member bindings. Customer accounts,
  agreements, entitlements and commission ledger rows remain with their owners.
- AI migration preserves immutable policy/input/execution/insight evidence but
  maps provider/model/request IDs into opaque execution observations. Secrets,
  endpoints and provider payloads never enter the module.
- Remote/Support Access have no legacy workflow rows to backfill. Adoption must
  disable direct issuance/toggle paths unless they carry an admitted module
  grant, and expiry/revocation must fail closed.
- Compliance migration snapshots evidence hashes and filing state; it does not
  copy source-domain facts into a second authority.
- Workflow migration imports only executions that can name an immutable
  definition version and a deterministic checkpoint. Product-specific actions
  remain adapter intents; unknown executions are quarantined, not guessed.
- Forms migration freezes published versions and answer snapshots; recruitment
  or other subject meaning stays an opaque reference owned by the adopter.
- Platform Health is rebuildable from retained observations. Deleting the
  projection and replaying observations must reproduce it; no health state is
  allowed to call Deployment Control.

## Negative inventory proof

Path and content searches covered models, services, routes, tasks, migrations
and tests at every revision above for referral/reseller/AI/transcription,
remote access/VPN, regulatory/compliance packs, workflow execution/checkpoints,
forms, impersonation/break-glass/support access, heartbeat/health/freshness and
deployment/rollout vocabulary. The negative findings used for greenfield mode
are narrow:

- no generic Remote Access request/grant/revocation owner;
- no temporary Support Access workflow owner;
- no general authenticated Platform Health projection owner.

They do not claim the neighbouring implementations do not exist; those are
explicitly recorded as references above.
