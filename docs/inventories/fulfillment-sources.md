# Fulfillment sources — the generic saga engine

**As of:** 2026-08-15
**Starter:** `e6ba2022f3d7` (branch `docs/adr-0030-cloud-commerce-composition`)
**Sub:** `27c76aaeebb7`
**ERP:** `0f4b1698ddbf` (revision-pinned reads; worktree had 67 local paths)
**Vendor CP:** `89848017d6b8`
**CRM:** `c64b5aa0f790`
**Integrator:** `d014116e63ad`
**Decision:** [ADR-0030](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
§1 (owner matrix), §5d (Fulfillment is greenfield on the kernel participant
contract; build-order step 12), §7 (Sub replaces its synchronous executor and
reaper, not a generic saga engine), §8.1 (a business saga never owns
connector-delivery retry state).

The 2026-08-15 amendment to ADR-0030 was written FROM this audit: the decision
originally said to port Sub's run/step/readiness patterns, and this dossier is
the evidence that refuted it.

This audit settles two things: which production code the saga engine may start
from, and where Sub's engine stops being an engine and starts being an ISP
installation. Read under this directory's standing cautions
([README](README.md)) — a row here is not permission to extract anything.

## Verdict

`dotmac-fulfillment` is **greenfield-after-inventory**.

**No saga engine exists anywhere in the fleet to port.** Sub's
`saga_executions` and `provisioning_step_executions` tables exist in migrations
`037` and `047` and have **zero references anywhere else in the tracked tree** —
`git grep -i saga 27c76aaeebb7` outside `alembic/` returns nothing at all. No
model, no service, no test, no caller. Worse, `saga_executions` carries foreign
keys to `ont_units` and `olt_devices`, so the one table that looks generic is
bound to ISP hardware at the schema level. What actually executes provisioning
in Sub is a synchronous `for` loop with no step rows, no attempts, no retries
and no compensation (`ProvisioningRuns.run`, below), recovered by a 30-minute
wall-clock reaper that marks runs failed without re-observing the participant.

The saga aggregate, ordered business steps, append-only step attempts, retry
classification, compensation decisions and partial-success derivation are
therefore **fresh design**. Nothing below is permission to copy an
implementation.

What the inventory DID find is three **reference constraints** — things the new
module must satisfy, extend or beat, not code it starts from:

- **The kernel participant contract is the mandatory foundation.**
  `dotmac_kernel.providers.provisioning` is a product-neutral
  plan/apply/observe/cancel contract with `PARTIAL` resumption, `operation_id`
  idempotency and a retryable/terminal error hierarchy, plus a shipped
  conformance kit. It is not a saga. It IS the participant seam, and ADR-0030
  §5d requires the module to extend it rather than invent a second one — with
  `participant_code`, explicit `TenantScope`/`PlatformScope`, typed
  asynchronous outcome envelopes, and compensation that may answer
  `not_supported` or `manual_required`.
- **Sub constrains the cutover, and proves the idempotency and correlation
  shapes are achievable.** `owner_commands.py`, `events/owner_outputs.py` and
  `provisioning_lifecycle.py` are real, heavily-used, production-proven
  idempotency, correlation, append-only-evidence and sole-writer code. They are
  a design reference and the definition of what Sub's cutover must preserve —
  not a saga to lift. Two of their behaviours are defects the module must NOT
  reproduce: `consume_owner_output` cannot replay an outcome and never compares
  its stored fingerprint, while `provisioning_lifecycle` correctly fails closed.
- **ERP states the ledger requirements and demonstrates the failure mode.**
  `platform.saga_execution` + `platform.saga_step` is the only durable step
  graph with compensation in the fleet, and it is dead: `use_saga` defaults
  `False`, no tracked file passes `True`, and `SagaRecoveryService` has zero
  callers. It is a requirements source and a warning, never a code source.

A greenfield verdict here is not a licence to design in the abstract. The
participant port, the ISP seam mapped below, and Sub's retirement inventory are
binding inputs; ADR-0006's extraction rule simply has nothing to act on because
there is no qualifying implementation to extract.

## Sub source

### What constrains the design (reference, not a code source)

**Paths that bind the cutover:**

- `app/services/owner_commands.py` (427 lines) — `CommandContext`
  (`command_id`, `correlation_id`, `actor`, `scope`, `reason`,
  `idempotency_key`, `causation_id`) and the command boundary.
- `app/services/events/owner_outputs.py` (334 lines) — `stage_owner_output` /
  `consume_owner_output` / `record_terminal_failure` over
  `OwnerOutputReceipt`.
- `app/models/owner_output.py` — `owner_output_receipts`, unique
  `(consumer, event_id)`, outcome ∈ `{succeeded, terminal_failure}`.
- `app/services/provisioning_lifecycle.py` (666 lines) — command-keyed replay,
  append-only decisions, normalized checks with provenance.
- `app/models/provisioning.py` (565 lines) —
  `ProvisioningReadinessDecision` / `ProvisioningReadinessCheck` only.
- `app/services/service_order_lifecycle.py` (330 lines) — the sole status
  writer and its transition table.
- `app/services/sales_lifecycle_reconciliation.py` (166 lines) — a report/apply
  drift reconciler.
- `tests/test_provisioning_lifecycle.py` (521 lines, 11 tests),
  `tests/test_provisioning_run_state.py` (217 lines, 5 tests),
  `tests/architecture/test_provisioning_lifecycle_sot.py` (5 tests),
  `tests/architecture/test_service_order_status_writers.py` (2 tests).

**How widely it is used.** `execute_owner_command` is called from **162 tracked
files** under `app/`; **252 `OwnerCommandDefinition` instances** are declared
across **130 files**. `consume_owner_output` is called from **17 files** across
billing, support, field work, topology, sales and access — genuinely
cross-domain, not a provisioning-local experiment; `stage_owner_output` from 9.
`service_order_lifecycle` is imported by 12 files besides itself,
`provisioning_lifecycle` by 4, `sales_fulfillment` by 5 (plus 3 architecture
tests). `ProvisioningRun` is referenced in 20 application files.

**What the code proves.**

`provisioning_lifecycle.evaluate_readiness` locks the aggregate
(`select(...).with_for_update()`), looks up any prior decision by
`context.command_id`, and — this is the part worth porting —
`_validate_replay_scope` **fails closed** when one command id is replayed
against a different service order or run:

```python
raise _error(
    "command_replay_conflict",
    "Command id was already used for a different provisioning scope.",
    command_id=str(decision.command_id),
)
```

It separates *activation requested* from *activated*: a ready decision emits a
request, the participant projections run, and only a second command records
confirmation. That two-phase request/confirm shape is exactly a saga's
command→outcome receipt, and it is production behaviour with tests
(`test_ready_order_requests_activation_then_requires_projection_confirmation`,
`test_confirmation_rejects_activation_without_readiness_request`).

Evidence immutability is enforced at the model:
`@event.listens_for(..., "before_update")` and `"before_delete"` raise
`ProvisioningReadinessEvidenceImmutableError` on both the decision and its
checks. `ProvisioningReadinessCheck` carries `source_type` + `source_id` — an
observation names where it came from — and `UniqueConstraint("decision_id",
"kind")` makes one decision hold at most one check per kind.

`owner_outputs` is the strongest single port. The `(consumer, event_id)`
receipt commits in the same transaction as the consumer's effect, so redelivery
is harmless and a raised failure leaves no receipt and stays retryable in the
outbox. `record_terminal_failure` refuses an empty `failure_reason` and refuses
to overwrite an existing receipt — a terminal outcome is reviewable evidence,
never a silent abandonment. This is the receipted-consumption discipline the
saga needs for every participant outcome.

`service_order_lifecycle` proves the sole-writer pattern with an explicit
`_ALLOWED` transition table, `_lock()` before every transition, and an
architecture test that AST-walks `app/services/**` asserting no other module
assigns `ServiceOrderStatus.active` or `.failed`.

### What does not qualify: there is no run/step engine

`ProvisioningRuns.run` (`app/services/provisioning_managers.py`, lines
822–1037) is what actually executes a provisioning run. It:

- writes one `ProvisioningRun` row, then loops steps **synchronously in
  process**, opening SSH / NETCONF / RouterOS sessions to network hardware
  inline;
- writes **no step rows at all** — every step result is appended to a Python
  list and dumped once into `run.output_payload = {"results": results}` at the
  end, so a crashed worker loses the entire step history;
- `break`s on the first failing step. There is **no retry, no backoff, no
  attempt record and no compensation** on this path;
- dispatches through an `if/elif` chain over the closed `ProvisioningStepType`
  enum, raising `HTTPException(400, "Unsupported step type")` from a service;
- calls `db.commit()` three times inside one logical run.

If the worker dies mid-loop the row stays `running` forever, and the mitigation
is a wall-clock reaper (`reap_stale_runs`, default 30 minutes) that marks such
runs `failed` and emits `provisioning_failed`. That is a timeout heuristic, not
convergence: a run whose provider effect actually succeeded is recorded as
failed, and nothing re-observes the participant to find out.

Compensation exists only as `app/models/compensation_failure.py` +
`app/services/network/compensation_retry.py` (420 lines). It is not generic:
`CompensationFailure` FKs to `ont_units` and `olt_devices`, stores
`undo_commands: list[str]` (OLT CLI strings) and `interface_path` ("0/2" for a
GPON interface), and the retry executes them over `olt_session` SSH. Only the
*operator repair queue shape* is portable — `pending/resolved/abandoned`,
`failure_count`, `last_attempted_at`, capped exponential backoff
(`retry_backoff_seconds`, 300 s base, 21600 s cap), `resolved_by` and
`resolution_notes`.

`app/services/web_network_provisioning_triage.py` is a read-only worst-first
attention queue over runs, orders and tasks, mapping runs to a
`ControlPlanePhase` convergence lens. It is a good requirements source for
operator repair UX and writes nothing.

### Where the engine ends and the ISP participants begin — the seam

The seam is **real but not clean**, and the cost of untangling is concentrated
in three places, not spread through the codebase.

**Clean seam (cheap).** `provisioning_lifecycle` is already transport-free — an
architecture test asserts `"fastapi"`, `"APIRouter"`, `"celery"` and
`"DeviceProvisioner"` do not appear in it. Its generic half (replay lookup,
scope validation, `_append_decision`, `_to_outcome`, the request/confirm split)
touches no ISP type. Its ISP half is one function, `_evaluate_facts`, ~150
lines, which queries `Project`, `ProjectTask`, `WorkOrder`,
`SubscriptionChangeRequest` and `IPAssignment`. Replacing `_evaluate_facts`
with a participant readiness port is a bounded, mechanical change.

**Entangled seam (expensive).** Three couplings are structural, not incidental:

1. **The check vocabulary is a database enum.**
   `ProvisioningReadinessCheckKind` is a PostgreSQL enum whose members are
   `provisioning_run`, `project_binding`, `activation_task`, `field_work`,
   `ip_assignment`. Four of five are ISP facts. A Cloud participant kind
   ("registrar contact verified") cannot exist without an ALTER TYPE. Cost:
   the column becomes an open registered string, and every reader stops
   comparing enum members.

2. **The step vocabulary is a database enum with provider brands beside it.**
   `ProvisioningStepType` has nine ISP members (`assign_ont`, `push_config`,
   `create_olt_service_port`, `ensure_nas_vlan`, `restore_olt_from_backup`, …)
   and `ProvisioningVendor` is literally `mikrotik | huawei | zte | nokia |
   genieacs | other` — provider brand names in a product schema and in a
   `workflows.vendor` column. Sub carries three migrations proving the release
   cost of this design: `a079511c71a3_add_provisioning_step_types_for_vlan_`,
   `046_add_restore_olt_from_backup_step_type`,
   `052_remove_legacy_tr069_provisioning_step_types`. Cost: total replacement.
   Nothing here ports.

3. **The sole status writer reaches into another owner.**
   `service_order_lifecycle.transition_service_order` calls
   `activate_subscription(db, str(subscription.id), ...)` when moving to
   `active`. The architecture test
   `test_raw_service_order_manager_cannot_activate_subscription` proves only
   that the *manager* does not do this — the status writer itself still does.
   Cost: the saga emits a completion fact and the service lifecycle owner
   decides; the writer loses the call.

**The ISP participants found, and the surface each occupies:**

| Participant | Where it enters | Excluded by ADR-0030 §5d |
|---|---|---|
| Field appointment | `InstallAppointment` model; `install_appointments` manager | yes |
| Installation project / project task | `Project`, `InstallationProject`, `ProjectTask`; `sales_fulfillment.ensure_implementation_scope` | yes |
| Field work order | `WorkOrder`, `WORK_ORDER_TERMINAL_VALUES` in `_evaluate_facts` | yes |
| IP assignment | `IPAssignment`; `_ensure_ip_assignments` inside `ProvisioningRuns.run` | yes |
| OLT service port / GEM / VLAN | `provisioning_step_executors.execute_create_olt_service_port`, `execute_ensure_nas_vlan` | yes |
| ONT assignment / TR-069 / GenieACS | `provisioning_adapters.Provisioner.assign_ont/push_config/confirm_up` | yes |
| RADIUS | `events/handlers/provisioning.py::_sync_radius_on_activation` | yes |
| NAS device push | `events/handlers/provisioning.py::_push_nas_provisioning`, `nas.DeviceProvisioner` | yes |
| ISP subscription activation | `service_order_lifecycle` → `account_lifecycle.activate_subscription` | yes |
| Subscriber identity | `ServiceOrder.subscriber_id` FK, `account_id` alias property | yes |
| Relocation change request | `SubscriptionChangeRequest.service_order_id` / `.work_order_id` | yes |

The participant *port* in Sub is `app/services/provisioning_adapters.py`: an
abstract `Provisioner` with exactly three methods — `assign_ont`,
`push_config`, `confirm_up` — subclassed per hardware vendor
(`MikrotikProvisioner`, `HuaweiProvisioner`, `ZteProvisioner`,
`GenieACSProvisioner`), each opening `paramiko` / `ncclient` / `routeros_api`
connections. The port is named after ONT hardware, keyed by provider brand, and
performs blocking network I/O inside the engine's transaction. It is the
clearest possible demonstration of why the participant port must be designed
fresh, and why ADR-0030 sequenced fulfillment last.

**Second orchestrator inside Sub.** `app/services/control_relationships.py`
(719 lines) owns event *execution policy* — `RelationshipMode` ∈ `exclusive |
precedence | chain | fanout | competing | incompatible`, `HandlerStage` ∈
`state(10) | communication(20) | external(30)`, and per-handler dependency
declarations. `docs/designs/EVENT_ORCHESTRATION_SOT.md` states it plainly:
"`app.services.control_relationships` owns event execution policy… The
dispatcher builds one executable plan from those sources before invoking any
handler." If `dotmac-fulfillment` also orders work, Sub acquires two orderings.
The distinction to hold: `control_relationships` orders *handlers of one event*;
the saga orders *participant commands of one intent*. Sub's cutover must state
that a saga step never becomes a dispatcher stage.

**Tenancy: Sub proves nothing.** Only **4 of 155** model files under
`app/models/` mention `tenant_id` (`auth`, `rbac`, `domain_settings`,
`domain_setting_history`). Not one provisioning, saga, receipt or event table
carries one. `git grep -i "row level security"` across the whole tracked tree
returns **2 files, both tests**, for auth/role migrations. Sub is a
single-operator deployment. Every tenant-isolation property of a tenant-plane
`dotmac-fulfillment` is unproven by this source.

## Starter kernel source

`packages/dotmac-kernel/src/dotmac_kernel/providers/provisioning.py` (355
lines) is the participant port shape, and it already exists. It is explicitly
"a CONTRACT, not a runner… no executable state machine ships here", which is
precisely the division of labour a saga needs: the kernel owns the shape of one
participant conversation, the saga owns the ledger.

It already owns:

- `plan(request) -> PlanResult` (read-only, stable `plan_hash`),
  `apply(request) -> ApplyResult`, `observe(operation_id) -> ObserveResult`,
  `cancel(operation_id) -> ObserveResult` (cooperative, never force-kill);
- `ProvisioningStatus` ∈ `pending | in_progress | partial | succeeded | failed
  | cancelled`, with `PARTIAL` load-bearing and resumable, and `StepStatus` per
  step;
- `operation_id` as the idempotency and resume key, with a stated derivation
  rule when the caller omits it: "a provider MUST derive a stable one from
  `(intent_id, plan_hash)`";
- `ApplyResult.outstanding_steps` / `ProvisioningStep.is_settled` — what a
  resume must reconcile;
- a retry classification that fails closed: `ProvisioningError.retryable =
  False` by default, `ProvisioningRetryableError` (`retryable = True`),
  `ProvisioningTerminalError`, `ProvisioningPlanError`,
  `ProvisioningApplyError`, `ProvisioningCancelled`;
- a conformance kit — `dotmac_kernel/testing/provisioning.py` (241 lines),
  `FakeProvisioningProvider` + `check_provisioning_provider_contract`, which
  asserts plan-hash determinism across fresh providers, re-apply idempotency on
  a terminal `operation_id`, partial→resume convergence to empty
  `outstanding_steps`, and `ProvisioningApplyError("x").retryable is False`.

It has one live consumer, Vendor CP's laboratory provider, which runs the
kernel suite verbatim
(`tests/unit/test_provisioning_contract.py::test_vendor_provider_factory_satisfies_kernel_contract`).
Source reach is 7 kernel files plus 2 tests and a floor probe; the other 17
grep hits are documentation.

**What it does not have, and the saga does need:** no compensation method
(`cancel` stops an in-flight operation; it does not undo a settled effect), no
participant identity (`intent_id` and `spec` are opaque — nothing says *which*
participant), no tenant in the request, and no inbound-outcome path for a
participant that answers days later rather than returning from `apply`.

## ERP source

ERP has the fleet's only durable step graph with compensation, and it is
dormant.

- `app/models/finance/platform/saga_execution.py` — `platform.saga_execution`
  (`status` ∈ `PENDING/EXECUTING/COMPLETED/COMPENSATING/COMPENSATED/FAILED`,
  `current_step`, `payload`, `context`, `result`, `correlation_id`,
  `UniqueConstraint("idempotency_key", name="uq_saga_idempotency_key")`) and
  `platform.saga_step` (`step_number`, `step_name`, `status`, `input_data`,
  `output_data`, **`compensation_data`**, `error_message`, `retry_count`);
- `app/services/finance/platform/saga_orchestrator.py` — abstract
  `SagaOrchestrator`, `SagaStepDefinition`, `StepResult`, `SagaResult`,
  reverse-order `_compensate_steps`, `_handle_existing_saga()` replay-or-resume;
- `app/services/finance/platform/saga_factory.py` — global `saga_factory`
  registry;
- `app/services/finance/platform/saga_recovery.py` — `SagaRecoveryService`,
  `STUCK_THRESHOLD_MINUTES = 30`, `MAX_COMPENSATION_RETRIES = 3`;
- `alembic/versions/add_saga_execution_tables.py` — in the live chain;
- subclasses `ap_posting_saga.py`, `ar_posting_saga.py`.

**It is not adopted.** `git grep -n use_saga 0f4b1698ddbf` returns 6 hits in 2
files, always as `use_saga: bool = False`; **no tracked file passes
`use_saga=True`**. `SagaRecoveryService` has zero callers outside its own file
— no scheduler, no task, no route. Real code reach is ~7 files, all
finance-internal. Steps are in-process Python callables against one `Session`;
there is no participant dispatch and no command correlation. Retry
classification is absent: any exception is `success=False` → immediate
compensation, and `retry_count` increments only on *compensation* failure.

The ruling: ERP is a **requirements and negative-test source**, not a code
source. Its step table is the right column set (especially `compensation_data`
alongside `output_data`); its schema binding (`platform.` finance namespace),
its dormancy, and its missing retry taxonomy are why it cannot be the base. It
is also a genuine parallel-authority risk if ERP ever adopts fulfillment —
ADR-0030 §7 says ERP adopts none of the seven, which keeps the conflict
theoretical, but the tables should be recorded as a retirement candidate rather
than left as a second saga in the fleet.

ERP additionally has `automation.workflow_execution` (a rule→action engine with
`retry_count` / `max_retries` but no step graph and no compensation) and
`platform.event_outbox` (which owns `error_class` and `terminal_reason` — ERP's
retry-classification owner). Neither is a saga; both mean a fulfillment run
table would be a third and fourth execution ledger in ERP.

## Vendor CP, CRM and Integrator

- **Vendor CP** — `src/vendor_cp/provisioning/` is five files and its own
  `__init__.py` calls it "a LABORATORY, not a fleet driver: simulation only…
  no fleet tables, no `DeploymentRunner`, no persistence beyond the provider's
  in-memory operation state." State is `self._operations: dict[str,
  ApplyResult]`, lost on restart; no model, no migration, no table. It defines
  no conformance checks of its own — it runs the kernel's. Not an authority,
  and confirming this closes ADR-0030's note that it is a "contract
  laboratory". No `saga` or orchestrator anywhere in the repo.
- **CRM** — no generic engine. `app/models/workflow.py` is allowed-transition
  config plus SLA clocks; `crm/inbox/orchestrator.py` is a re-export barrel with
  no state; `integration_runs` is a flat run row with no steps, attempts or
  compensation; `crm_outbox` is a message-delivery ledger. No conflict.
- **Integrator** — a 24-file thin assembly pinning `dotmac-integration
  0.1.0a1`. Its `worker.py` runs one periodic `release_expired_leases` sweep
  and says so: "The dispatch pump is deliberately NOT here." No orchestration.

## The `dotmac-integration` boundary

`dotmac-integration` (source at
`packages/dotmac-integration/src/dotmac_integration/`, schema `mod_intg`,
**platform plane only** — `TENANT_TABLES` is empty) already owns the entire
transport layer, and the saga must not restate any of it.

| Owned there | Where |
|---|---|
| Delivery attempts, backoff schedule, leasing, dead-lettering | `models.py::DeliveryAttempt` → `mod_intg.delivery_attempts`: `attempt_count`, `next_attempt_at`, `leased_until`, unique `(installation_id, idempotency_key)`, state check constraint |
| Retry classification | `retry.py`: `OutcomeStatus` ∈ `SUCCEEDED / RETRYABLE / RECONCILIATION_REQUIRED / TERMINAL`; `next_state()`; `retry_delay_seconds()` = `base·2^(attempt-1)` capped, provider `retry_after_seconds` wins |
| Retry/lease numbers | `policy.py::ExecutionPolicy` — `max_attempts=10`, `base_delay_seconds=60`, `max_backoff_seconds=8h`, `lease_seconds=300` |
| Inbound dedup | `models.py::InboxReceipt` → `mod_intg.inbox_receipts`, `provider_event_id` + `payload_digest` |
| Poll cursors | `models.py::PollingCheckpoint`, optimistic `version` |
| Health | `operations.py::health_report` — **derived at read time, never stored** |
| Repair | `operations.py::replay_delivery` / `replay_receipt` / `release_expired_leases`, audited, explicit, never automatic |
| Transaction discipline across provider I/O | `dispatch.py` three-phase `prepare → invoke → settle`: "No database transaction is held across provider I/O." |

**The line.** A *delivery attempt* answers "did the message reach the
connector?". A *step attempt* answers "did the participant accept the command
and finish it?". One step attempt may span many delivery attempts, and a
delivered message is not a completed step. The saga therefore records step
attempts and **never** carries `attempt_count`, `next_attempt_at` or
`leased_until` for an outbound connector call, never schedules transport
backoff, and never stores a health column. The two also cannot share a row:
`mod_intg` is platform-plane and `dotmac-fulfillment` is tenant-plane.

`dotmac-fulfillment` must **not** import `dotmac_integration` either — a
platform-plane foundation is not a dependency of a tenant-plane business
owner, and the participant abstraction the saga needs is the kernel's
`ProvisioningProvider`, not a connector. Reusing integration's four-outcome
vocabulary is a design borrowing to be re-declared locally in the saga's own
terms, not an import.

## Do not port

1. **`ProvisioningStepType`, `ProvisioningVendor`,
   `ProvisioningReadinessCheckKind`, `ProvisioningReadinessDecisionStatus`,
   `ServiceOrderType`, `ServiceState` as database enums.** Every participant,
   step kind, check kind and reason code is an open registered string under
   ADR-0008. Sub's three step-type migrations are the evidence for why.
2. **Provider brand names anywhere.** `mikrotik`, `huawei`, `zte`, `nokia`,
   `genieacs` are columns and enum members in Sub today. No provider name
   enters a contract, schema, column, registry code or setting.
3. **`Provisioner.assign_ont` / `push_config` / `confirm_up`** and every
   `provisioning_step_executors` function. The participant port is not named
   after hardware and does not open a socket.
4. **In-process provider I/O inside the engine's loop.** `ProvisioningRuns.run`
   holds a session across `paramiko` SSH. Non-transactional effects leave
   through the durable outbox; nothing blocks on a participant.
5. **`execute_owner_command` itself.** It is Sub's host framework: it commits,
   it refuses to run inside a caller transaction, and it validates against
   Sub's `sot_manifest` / `sot_relationships` registry. Hard rule 8 gives
   `dotmac_kernel.db` the transaction authority; a module never commits. Port
   `CommandContext`'s *field set*, not the executor.
6. **Service-level `commit=True` and `db.rollback()`.**
   `transition_service_order(..., commit=True)` is the default;
   `sales_lifecycle_reconciliation` calls `db.rollback()` inside its loop.
   Hard rules 8 and 9 forbid both — use `conflict_savepoint`.
7. **`HTTPException` from a service.** `provisioning_managers` raises
   `HTTPException(404/400)` throughout. Errors are typed domain classes.
8. **The wall-clock reaper as convergence.** `reap_stale_runs` declares a run
   failed because 30 minutes passed. Convergence re-observes the participant;
   it does not guess from a clock.
9. **`restore_recorded_status`.** It lets an operator reinstate any state,
   including from a terminal one, bypassing `_ALLOWED`. Operator repair may
   request a new attempt or record a reviewed terminal outcome; it may not
   rewrite the run's history.
10. **The silent no-op transition.** `if previous == target: return order` —
    a repeated transition succeeds with no evidence. A saga must distinguish
    "already done" (a replay, receipted) from "nothing happened".
11. **`ServiceOrder`'s host coupling.** `subscriber_id`, `subscription_id`,
    `project_id`, `installation_project_id`, `activation_project_task_id`,
    `sales_order_line_id` and the `account_id` back-compat alias. The saga
    correlates to an order line by an opaque reference the assembly supplies,
    with no FK to another owner's table.
12. **ERP's `platform.` finance schema binding, `SagaRecoveryService`'s
    hardcoded `STUCK_THRESHOLD_MINUTES`/`MAX_COMPENSATION_RETRIES`, and
    "any exception → compensate".** Compensation is a decision, not the
    default handler for an unclassified error.
13. **A second ordering authority.** Do not restate `control_relationships`'
    stage/chain/fanout model. Saga step ordering is not handler dispatch
    ordering.

## Known defects and deltas

1. **`consume_owner_output` cannot replay an outcome.** The module docstring
   promises "a replay returns the recorded outcome without re-running the
   effect", but the code returns `(None, receipt)` — `OwnerOutputReceipt` has
   no result column. For a saga this is disqualifying: an idempotent outcome
   receipt must return the original outcome, not merely suppress the effect.
   The kernel's `IdempotentOutcome` (with `result` and `replayed`) is the
   correct shape.
2. **`consume_owner_output` has no fingerprint conflict check.** It keys on
   `(consumer, event_id)` only; `effect_idempotency_key` is stored and never
   compared. A replay carrying a different request is silently accepted as a
   no-op. `provisioning_lifecycle._validate_replay_scope` does fail closed on
   scope reuse — the two idempotency paths in the same codebase disagree.
   `dotmac_kernel.idempotency` already resolves this correctly with
   `fingerprint_of` and `IdempotencyConflict`.
3. **Immutability is ORM-only.** `ProvisioningReadinessDecision` /
   `ProvisioningReadinessCheck` are protected by SQLAlchemy `before_update` /
   `before_delete` listeners. A bulk `UPDATE`, a raw `db.execute`, an Alembic
   step or any non-ORM writer bypasses them entirely. Append-only must be a
   database grant or trigger, not a Python event hook.
4. **`uq_provisioning_step_execution_per_attempt` provides no attempt
   granularity.** The constraint is `(saga_execution_id, step_name)` — exactly
   one row per step per run. Its name claims per-attempt uniqueness the columns
   cannot deliver. Since the table has no code at all this is currently
   harmless, and it is a precise warning about the schema a saga must not
   inherit.
5. **Two orphaned tables in Sub's live lineage.** `saga_executions` (mig. 037,
   FK to `ont_units` and `olt_devices`) and `provisioning_step_executions`
   (mig. 047) have no model, service, test or caller. The "generic" saga table
   is FK-bound to ISP hardware at the schema level. Sub's adoption must delete
   them, not reuse them.
6. **Every provisioning test runs on SQLite.** `tests/conftest.py` monkey-patches
   `Uuid` and `JSONB` for SQLite. The `with_for_update()` calls in
   `provisioning_lifecycle.py` (4), `sales_fulfillment.py` (3) and
   `service_order_lifecycle.py` (1) are therefore **never exercised as row
   locks** — SQLite ignores `FOR UPDATE`. There is no concurrency test, no
   rollback test and no isolation test on this path.
7. **The sole-writer proofs are substring assertions.**
   `test_provisioning_lifecycle_sot.py` reads source text and asserts
   `"activate_subscription" not in source`, `"celery" not in source`. They
   catch a regression in the file they name and prove nothing about runtime
   behaviour or about a new file.
8. **The status writer is a cross-owner writer.**
   `transition_service_order` calls `activate_subscription` when moving to
   `active`. The architecture test that appears to forbid this only checks the
   manager module.
9. **`ProvisioningRun` has no idempotency key.** `ServiceOrder` has
   `ix_service_orders_idempotency_key`; the run does not. `run_for_order` can be
   invoked repeatedly and will create duplicate runs against the same order.
10. **`ProvisioningRun` has no tenant column and no RLS**, like every other
    table on this path. Nothing in the source proves cross-tenant isolation of
    a run, a step, a receipt or an audit row.
11. **Partial success does not exist in Sub.** `ServiceOrder.sales_order_line_id`
    is `unique=True`, so the one-command-per-line structure is genuinely there,
    but "partial" in Sub is only `SalesOrderPaymentStatus.partial` — a payment
    state. No code derives aggregate fulfillment progress from per-line
    outcomes. This is fresh design.
12. **Retry classification does not exist on this path.** A tree-wide search for
    `retryable | transient | is_retryable | RetryClass` finds hits only in
    unrelated services (`ai/client.py`, `dotmac_erp/outbox.py`,
    `celery_scheduler.py`). No evidence found in fulfillment.
13. **`compensation_retry` mixes naive and aware datetimes.**
    `next_retry_at` defends with `if attempted_at.tzinfo is None:
    attempted_at.replace(tzinfo=UTC)` against a `DateTime(timezone=True)`
    column — the defence is evidence the column is not reliably aware.

## Shared contract

Version one **owns**:

- the **saga aggregate** — one run per commercial intent, tenant-scoped, with a
  declared idempotency identity and a locked, single-writer state;
- **participant definitions** as an open registered vocabulary (ADR-0008): a
  `participant_code` is a plain string column validated against a registry
  built from module manifests, exactly as `dotmac_kernel.setting_domains` /
  `audit_actions` do it. The engine must never contain `domain`, `hosting` or
  `radius` in an enum, a match statement, or an `if participant ==` branch;
- **step definitions** — the ordered, declared work for a run, addressed by a
  stable `step_id`, each bound to one participant code and one command;
- **step attempts** — an append-only attempt row per dispatch, carrying the
  attempt's own outcome, error class and reason code; the run's history
  survives a crashed worker;
- **outcome classification** at the participant layer: succeeded, retryable,
  reconciliation-required, terminal. The fourth is the one that is normally
  omitted and the one that matters — a participant effect may have half-landed,
  so retrying risks duplication and dead-lettering hides it;
- **command correlation** — `command_id`, `correlation_id`, `causation_id` and
  the business `idempotency_key` on every dispatched command, so a participant
  outcome can be matched back to exactly one step attempt;
- **idempotent outcome receipts** — one receipt per `(participant, command_id)`
  that stores the outcome and can **return it on replay**, with a fingerprint
  conflict when the same key arrives carrying a different request;
- **compensation requests** — a typed, receipted request that a participant undo
  a named settled effect. A request is not permission and not a state write
  (ADR-0026, and ADR-0030 §1's Collections rule applied to the saga): the
  participant revalidates its own facts and may refuse;
- **partial-success derivation** — each order line has its own command and
  outcome; the run derives aggregate progress from the per-line outcomes and
  never stores a hand-maintained aggregate status;
- **convergence and reconciliation** — re-observing a participant to settle an
  unknown or partial outcome, rebuilding derived run progress from attempts and
  receipts, and repairing a missed outcome delivery;
- **operator repair** — an attention queue derived at read time, plus explicit,
  authorised, audited actions: request another attempt, request compensation,
  record a reviewed terminal outcome. Repair never edits an attempt, a receipt
  or a recorded outcome.

Version one does **NOT** own:

- **what any participant does.** The saga names a participant and a command; it
  never knows whether that means an EPP transfer or a panel account.
- **provider I/O, credentials, endpoints, webhook verification, wire payloads,
  transport retry, backoff, leasing, dead-lettering, inbox dedup, polling
  cursors or connector health.** All of that is `dotmac-integration` and the
  connector plugins.
- **order state.** `dotmac-orders` owns the order and its immutable line
  snapshots. The saga holds an opaque line reference.
- **invoice, obligation, settlement, receivable or coverage state.**
  `dotmac-billing`.
- **service lifecycle decisions.** Whether a transition is permitted, and the
  transition itself, belong to `dotmac-domains` / `dotmac-hosting`. A saga
  outcome is evidence the lifecycle owner consumes; it is never a service
  status.
- **subscription cadence, recurrence or proration** (`dotmac-subscriptions`),
  **dunning policy** (`dotmac-collections`), or **GL and statutory accounting**
  (ERP).
- **scheduling.** When due work wakes up is `dotmac_kernel.durable_timers`
  (ADR-0030 §4), not a saga-local scheduler ledger or a wall-clock reaper.
- **handler dispatch ordering.** Saga step order is not event-handler stage
  order.

### The participant port, version one

This is the deliverable ADR-0030 §5d asks for: a shape Domains, Hosting and
Sub can each implement without the engine importing any of them. §5d fixes the
four extensions to the kernel contract — `participant_code`, explicit
`TenantScope`/`PlatformScope`, typed asynchronous outcome envelopes, and
compensation that may answer `not_supported` or `manual_required` — and §8.1
fixes what the saga may not carry.

Start from `dotmac_kernel.providers.provisioning.ProvisioningProvider`, which
already gives plan/apply/observe/cancel, `operation_id` idempotency, `PARTIAL`
resumption, `outstanding_steps`, the retryable/terminal error hierarchy and a
conformance kit. Extend it with exactly four things, each justified by a gap
proven above:

1. **A participant code on the request.** An open registered string identifying
   *which* owner is being addressed, so the saga can bind a step to a
   participant without a Python import and without an enum. `intent_id` and
   `spec` stay opaque.
2. **A compensation method.** `cancel` stops an in-flight operation; it does not
   undo a settled one. `compensate(operation_id, reason)` returns the same
   `ObserveResult` snapshot shape and may legitimately answer "refused" — the
   participant revalidates and decides. ERP's `saga_step.compensation_data` is
   the column-level requirement; Sub's `undo_commands: list[str]` of OLT CLI
   strings is the anti-pattern to avoid.
3. **An asynchronous outcome path.** A registrar transfer takes days. `apply`
   returns `IN_PROGRESS`/`PARTIAL` and the outcome arrives later, so the port
   must state that an outcome may be delivered inbound (translated by the
   assembly from an integration inbox receipt) and must carry the same
   `operation_id`. `observe` remains the pull half of the same fact.
4. **Tenant in the request.** The kernel contract is a process-level Protocol
   with no tenant; a tenant-plane saga must carry it explicitly.

Ship a provider-free fake and run `check_provisioning_provider_contract` plus
the saga's own additions against it. That is ADR-0030 §2.6, and the kernel
already supplies most of the kit.

## Kernel floor

Capabilities `dotmac-fulfillment` consumes, to be proven sufficient and
necessary at the completion gate:

- **`dotmac_kernel.db`** — the one transaction authority (hard rule 8). The
  module never commits and never calls `db.rollback()`; conflicts use
  `conflict_savepoint` with the mutation inside the `with` block (hard rule 9).
- **`dotmac_kernel.idempotency`** — `execute_once` (tenant), `fingerprint_of`,
  `IdempotentOutcome(result, replayed)`, `IdempotencyConflict`. ADR-0014 gives
  at-most-once exactly one owner; the saga's outcome receipt is built on it,
  not beside it. This is also the fix for defects 1 and 2 above.
- **`dotmac_kernel.messaging`** — `enqueue_event` / `outbox_events` for every
  non-transactional effect, `CommandEnvelope` (`command_id` as the idempotency
  key) for dispatched commands, `inbox.process_once` for inbound outcomes, and
  `relay` for delivery. The saga owns no queue.
- **`dotmac_kernel.providers.provisioning`** — the participant port base, and
  `dotmac_kernel.testing.check_provisioning_provider_contract` as the
  conformance floor.
- **`dotmac_kernel.audit`** — `write_audit_event` for every operator repair
  action, with declared audit actions on the module manifest (hard rule 12).
- **`dotmac_kernel.planes`** — `ModulePlane.TENANT`, declared, never inferred
  (ADR-0023, ADR-0028).
- **`dotmac_kernel.modules`** / **`features`** — the `ModuleManifest`, its
  `permissions` / `capabilities` / `audit_actions` / `feature_flags` /
  `setting_domains` declarations, and the declaration-registry pattern
  (`setting_domains.py`, `audit_actions.py`) that the participant-code registry
  must copy.
- **`dotmac_kernel.namespaces`** / **`prerequisites`** — one immutable
  `mod_<code>` schema, one lineage, effects declared via `requires`/`provides`
  and bound by the assembly (hard rule 14).
- **`dotmac-durable-timers`** — now released as the separately installable,
  selectable dual-plane timing owner (`0.1.0a1`, kernel floor a72). Fulfillment
  declares it as a module-code dependency but imports no sibling package; an
  adopter binds `ReobservationSchedule` to the timer service. The timer in turn
  reuses the kernel outbox relay for claim, lease, retry and dead-letter
  mechanics. The negative fact remains true: there is no
  `dotmac_kernel.durable_timers` implementation to call or duplicate.
- **Not consumed:** `dotmac_kernel.money`. The saga carries no amounts — money
  belongs to Billing, Orders and Subscriptions. Recording this now so the floor
  proof can show it is necessary as well as sufficient.

## Fresh proof required

1. **Tenant RLS isolation** — a run, its steps, attempts, receipts and
   compensation requests are invisible and unwritable across tenants, on live
   PostgreSQL with FORCEd RLS. Sub proves none of this (4/155 models have
   `tenant_id`; zero RLS).
2. **Concurrency on the run aggregate** — two workers advancing the same run
   cannot both dispatch the same step. Sub's `with_for_update()` has never run
   against PostgreSQL.
3. **Rollback with the consuming transaction** — a failed enclosing business
   transaction leaves no attempt, no receipt and no half-written run.
4. **Idempotent replay returns the original outcome**, and a replay of the same
   `(participant, command_id)` with a different request fingerprint fails
   closed. Both halves — defect 1 is the replay half, defect 2 the fingerprint
   half, and Sub gets each wrong in a different place.
5. **Out-of-order delivery** — an outcome for step 3 arriving before step 2's
   does not advance the run past step 2, and a stale outcome for a superseded
   attempt is rejected, not applied.
6. **Lost callback** — a participant that succeeded but whose outcome never
   arrived is settled by re-observation, and the run converges to the true
   state. Explicitly prove the case Sub's reaper gets wrong: a run must not be
   recorded failed because a clock expired.
7. **Duplicate outcome** — the same outcome delivered twice produces one
   receipt and one state advance.
8. **Partial success** — a three-line order where one line's participant
   succeeds, one is retryable and one is terminal derives the correct aggregate
   progress, and the two settled lines are not re-dispatched by a resume.
9. **Compensation is a request, not a write** — a participant that refuses
   compensation leaves its own state unchanged and the saga records the refusal
   as an outcome. The saga cannot write a participant's state under any path.
10. **Compensation is idempotent and ordered** — a repeated compensation
    request for the same settled effect is a receipted no-op; compensation runs
    in reverse settle order.
11. **Attempt and receipt immutability at the database**, not at the ORM —
    prove a raw `UPDATE`/`DELETE` as the tenant app role is refused (defect 3).
12. **Drift and reconciliation** — derived run progress is rebuilt from
    attempts and receipts alone and matches; a manufactured divergence is
    detected and repaired.
13. **Operator repair cannot rewrite evidence** — a repair action creates a new
    attempt or a new reviewed-terminal receipt and can never alter a recorded
    one, nor move a run out of a terminal state (defect 9).
14. **No participant code appears in engine control flow** — an architecture
    test proving the package contains no enum member, match arm or comparison
    naming a concrete participant, and that an unregistered participant code
    fails closed at declaration. This is the ADR-0008 guard, and it needs a
    sensitivity proof (ADR-0018): a deliberately planted `domain` branch must
    fail it.
15. **No transport state in the saga** — an architecture test that the package
    declares no `attempt_count`/`next_attempt_at`/`leased_until`/health column
    and does not import `dotmac_integration`.

Platform-plane revocation proofs do not apply: `dotmac-fulfillment` is
tenant-plane only, with an empty `platform_tables`, declared on the manifest.

## Adoption and retirement

**First adopter is Dotmac Cloud, not Sub**, and that inverts the usual order for
a good reason: ADR-0030 §7 has Sub replacing its synchronous executor and
reaper rather than adopting an engine it never had, while §5c requires Domains'
and Hosting's command surfaces to stabilise before the saga can depend on them. Cloud is where those
participants exist. Sub's cutover is second and is the harder one, because Sub
must retire live writers rather than adopt into empty tables.

Slicing the Cloud cutover:

1. Land the participant port extension and its fake/conformance kit, with no
   persistence. Prove Domains and Hosting can each implement it.
2. Land the run/step/attempt/receipt tables, the tenant plane, and the
   isolation canaries in the same change that allocates the namespace and
   lineage.
3. Cut over **one participant and one order line** — a single-line domain
   registration — end to end, shadowing the outcome before it drives anything.
4. Add the second participant, which is where partial success first becomes
   real, and prove proof 8 on live data.
5. Add compensation last. Until a participant has implemented refusal, a
   compensation request has nowhere honest to go.

Sub's cutover, and what retires:

- `ProvisioningRuns.run`'s inline step loop and `reap_stale_runs` retire
  together — the loop becomes participant dispatch, the reaper becomes
  re-observation. Neither may survive alongside the module.
- `provisioning_workflows` / `provisioning_steps` / `provisioning_runs` retire
  as the engine's tables and, if retained at all, only as ISP participant
  records owned by Sub.
- `ProvisioningStepType`, `ProvisioningVendor` and
  `ProvisioningReadinessCheckKind` retire as database enums.
- The orphaned `saga_executions` and `provisioning_step_executions` tables are
  dropped, not adopted (defect 5).
- `service_order_lifecycle` **stays in Sub** as an ISP service lifecycle owner
  and loses its `activate_subscription` call; the saga hands it an outcome and
  it decides.
- `provisioning_lifecycle._evaluate_facts` **stays in Sub** as its participant
  readiness implementation. Only the surrounding decision machinery moves.
- ERP's `platform.saga_execution` / `saga_step` are recorded as a fleet
  retirement candidate. They are dormant today, and ADR-0030 §7 gives ERP none
  of the seven modules, so this is cleanup rather than an authority migration —
  but leaving a second saga schema in the fleet after building the first is how
  a parallel authority reappears.

A green test suite is not a cutover. The package is complete when its contract
passes; it is adopted only when a real application runs the exact released
version, switches authority through a measured shadow, and the displaced local
writer is gone. Neither Cloud step 3 nor a reference-assembly migration test is
adoption.
