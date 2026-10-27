# Durable PROVISION runtime and network-target SPI for dotmac-integration

**Status:** Design for implementation (non-authoritative until shipped)
**Branch:** `feat/integration-provision-runtime-20261027`
**Base:** `origin/main` at `c4d0fe3c`
**Architecture owners:** ADR-0024, ADR-0038 §6, ADR-0074 (draft), ADR-0076 §§7–8
**Started:** 2026-10-27

## Goal

Give SPI 1.5's `PROVISION` mode the durable engine the other modes already
have, so a FreeRADIUS connector can run product-authored plans
(plan/apply/observe/cancel) survivable across worker death, and add the
installation-scoped network-target declaration ADR-0074 requires for
fleet-addressed transports (CoA now; SNMP/RouterOS/OLT later). This is slice 2
of the programme that lets Sub adopt `dotmac-network-access` in the
network-suite-v1 cutover. Slices 3–6 (Sub capability contracts, the connector
distribution, assembly wiring, shadow and cutover) consume this runtime; they
are not designed here beyond the seams named below.

## Principles carried over from Sub's current implementation

Three properties of the incumbent Sub system are deliberately preserved; they
constrain every slice, not only this runtime:

1. **FreeRADIUS is the thin front door; policy sits behind a narrow
   boundary.** FreeRADIUS does SQL authorize/accounting and nothing else.
   Desired state, decisions and drift live behind the network-access
   contract and the capability payload contracts, so the connector behind
   that boundary is replaceable — including a future language change —
   without touching any caller. No slice may move policy into FreeRADIUS
   configuration (unlang/SQL) or into the connector.
2. **Session effects have one executor, driven by lifecycle transitions.**
   Throttle-projection changes, CoA and Disconnect (PoD) are executed by the
   one Integrator daemon as governed commands: credential and access-policy
   lifecycle transitions produce projection operations, and disconnects are
   `dotmac-network-control` commands with recorded dispatch and execution
   evidence. FUP *evaluation* stays an input to `dotmac-service-access-policy`
   — the "one owner" is the executor of effects, not a merger of FUP policy
   into network-access, whose contract excludes commercial eligibility. The
   ordering invariants of ADR-0076 §8 (withdraw → converge → disconnect; no
   disconnect before convergence) are the state machine these commands obey.
3. **Workers are separate processes; a slow poll blocks nothing.**
   Projection apply, accounting polling and health/monitoring run as
   separate Integrator worker loops, with per-installation concurrency
   ceilings and no provider I/O inside a database transaction. The auth path
   (FreeRADIUS→SQL) must never depend on Sub's web app being up, and a stuck
   `radacct` poll must not starve projection applies — slice 5 keeps their
   pools separate for exactly this reason.

## What exists today

- SPI 1.5 types and a persistence-free invocation boundary
  (`provision_contract.plan/apply/observe/cancel_provisioning`): plan-hash
  integrity, typed results, no table, no lease, no retry, no idempotency
  claim. Released in `0.1.0a18`; `CURRENT_SPI_VERSION` is 1.6.
- Durable patterns to mirror, never fork: DELIVERY's
  `dispatch.prepare/invoke/settle` over `delivery_attempts` (lease,
  `next_attempt_at`, per-installation idempotency key, `payload_digest`
  conflict detection) and POLL's `polling.prepare_poll/invoke_poll` over
  `polling_checkpoints` + `poll_schedule` failure history. Execution
  at-most-once is owned by `dotmac_kernel.idempotency.execute_once_platform`
  via `execution.py` (ADR-0014); backoff by `retry.retry_delay_seconds`;
  operational numbers by `policy.ExecutionPolicy`; admission by `admission.py`.
- Platform-plane only: tables land in `mod_intg`, no `tenant_id`, no RLS,
  `app_user` revoked; each owning area declares its `*_PLATFORM_TABLES` and
  `manifest.py` composes them. Latest migration is `ig_0016`.
- `0.1.0a19` is allocated and unreleased, scoped to held-key product-query
  authentication with an explicit "no connector SPI change" claim. This work
  changes the SPI, so it does not ride a19.

## Sequencing inside this slice

0. **Allocate first.** Allocation-only change for the next integration version
   (a20) following the serialized allocation process, merged before source, so
   parallel trains cannot collide on the version or on `ig_0017`.
1. SPI 1.7 (network-target declarations) — types, validation, conformance.
2. Models + migration `ig_0017` (provision tables, config-revision targets).
3. Engine (`provisioning.py`): submit/prepare/invoke/settle/observe/cancel.
4. Assembly-facing surfaces stay out of this package: the Integrator assembly
   adds its worker loop and product routes in slice 5. This package ships the
   callable engine and due-job selectors only, exactly as POLL does.
5. COMPATIBILITY.md + CHANGELOG for SPI 1.7 and the runtime; release via the
   protected release path. Nothing may exact-pin the new version until tagged.

## SPI 1.7: network targets

- New manifest field pair, additive and digest-covered, following the SPI 1.3
  precedent (secret bindings + egress arrive together or not at all):
  `fleet_targets: FleetTargetDeclaration | None` on `ConnectorManifest`,
  naming the protocols and port ranges the connector may ever use against
  fleet endpoints (the ceiling). A connector with `fleet_targets=None` keeps
  today's meaning: manifest egress hosts only. Pre-1.7 manifests stay
  readable; a connector whose minimum SPI is 1.7 may not omit the field.
- Installations narrow the ceiling: each `ConnectorConfigRevision` may carry
  `network_targets` — CIDRs with protocol and port — validated against the
  ceiling before revision write, binding and activation, and covered by the
  revision digest. Absence is deny-all for fleet traffic. IPs in *config* are
  the point; IPs in the *manifest* remain forbidden.
- Validation locus, stated honestly: the engine cannot interpret arbitrary
  step input to find "the address". The engine therefore (a) validates the
  declaration itself, (b) delivers the bound targets to the handler inside
  `config` at invoke time, and (c) ships
  `spi.require_network_target(config, *, address, port, protocol)` which the
  connector must call before any fleet I/O. Conformance plants an
  out-of-declaration target behind a counting fake transport and requires a
  refusal with zero I/O. Where a capability contract names target fields in
  its command schema (the disconnect capability will), the engine-side
  submit path additionally validates before enqueue.
- The assembly's runtime-policy report lists declared network targets per
  connector beside egress hosts, derived from installed manifests and bound
  revisions — no second list in the assembly (slice 5 wiring, named here so
  the projection is designed once in `runtime_policy.py`).

## Durable model

One new owning module, `provisioning.py`, declaring
`PROVISION_PLATFORM_TABLES`, composed in `manifest.py` like
`poll_schedule`'s tables. Two tables:

### `provision_operations` — one product-issued plan

- `id`, `installation_id` FK, `capability_binding_id` FK, `capability_id`.
- `command_id` (product command identity) with
  `UniqueConstraint(installation_id, command_id)`: resubmission of the same
  command with the same `plan_hash` is a replay; with a different `plan_hash`
  it is a conflict and refused — the `payload_digest` rule from receipts and
  deliveries, applied to plans.
- `plan_hash` (`^[0-9a-f]{64}$` CHECK) and `plan_json` (the ordered steps:
  step_key, endpoint_code, depends_on, finite-JSON input). By ADR-0076 §7
  this payload can never contain a secret; the submit path also refuses
  step input keys the capability contract marks secret-bearing (schema-level,
  slice 3 defines the marks; the runtime only enforces the mechanism).
- `state` (closed CHECK): `planned → applying → (succeeded | failed |
  cancelled | reconciliation_required)`. Plan-level state is a projection of
  step states, recomputed at settle time inside the same transaction — never
  written by a second path.
- `cancel_requested_at`, `correlation_id`, `created_at`, `finished_at`.

### `provision_step_attempts` — one row per step, the claim unit

Mirrors `delivery_attempts`, because a step is a delivery-shaped unit of work:

- `operation_id` FK, `step_key`, `depends_on` (JSON list of step keys),
  `idempotency_key` unique per `(operation_id, step_key)` — engine-generated
  deterministically as `command_id:step_key:plan_hash` and handed to the
  connector as the SPI request's `idempotency_key`, so connector-side
  dedupe and engine-side dedupe name the same effect.
- `state` (closed CHECK, engine vocabulary): `pending, in_flight, succeeded,
  observing, retryable, terminal, ambiguous, cancelled,
  reconciliation_required`. `settle` maps `ProvisionResultStatus` onto it:
  SUCCEEDED→succeeded; ACCEPTED/PENDING→observing; RETRYABLE→retryable;
  TERMINAL→terminal; AMBIGUOUS→ambiguous; CANCELLED→cancelled;
  NOT_FOUND→terminal for apply, and per a18's stated semantics it proves
  nothing for cancel, so cancel+NOT_FOUND→reconciliation_required.
- `attempt_count`, `leased_until`, `next_attempt_at` (due index
  `(state, next_attempt_at)` like deliveries), `provider_operation_ref`,
  `evidence_json` (typed, bounded; raw provider bodies stay out, as with
  `result_json`), `error_code` (machine vocabulary only — the a3/a4 lesson:
  no connector exception text is ever persisted).

Migration `ig_0017` creates both tables with the platform-plane ACL pattern
(no RLS, no tenant_id, revoked from `app_user`), adds
`connector_config_revisions.network_targets_json` (nullable; NULL = not
declared, distinct from an explicitly empty deny-all list), and registers in
`test_released_migrations.py`'s `UNRELEASED` until tagged.

## Engine phases

Same three-phase shape as DELIVERY/POLL — no DB transaction spans provider I/O,
no handler ever sees a session:

- `submit_provision_operation(db, …)` — admission (`admit_installation`,
  runtime quarantine, capability mode PROVISION, contract-digest claim),
  command-schema validation of every step input via the capability registry,
  then `plan_provisioning` (the existing persistence-free validator) is **not**
  called here: plan validation needs connector code. Submit persists the
  operation + step rows and returns; `plan` runs as the first claimed unit of
  work and its refusal fails the operation before any `apply`.
- `prepare_provision(db, policy)` — claims due work with the delivery claim
  predicate (state pending/retryable/observing, `next_attempt_at` due, lease
  expired, per-installation concurrency ceiling, `dispatch_enabled` kill
  switch). A step is claimable only when every `depends_on` step of its
  operation is `succeeded`. Returns an immutable prepared value carrying
  resolved config (including bound network targets), manifest digest and
  config digest snapshots — settlement re-checks them, as receipts do.
- `invoke_provision(prepared, plugin, secrets)` — assembly materializes secret
  bindings after prepare closes (exactly the DELIVERY boundary) and the engine
  calls the existing `provision_contract` wrappers unchanged. Handler
  exceptions are normalized to a retryable outcome with a machine error code;
  exception text is withheld.
- `settle_provision(db, prepared, result)` — under the kernel platform
  idempotency executor: lease-checked conditional update of the step row,
  attempt/failure bookkeeping via `retry.retry_delay_seconds` (the one curve;
  failure never bumps any cursor-style version — there is none here),
  operation-state projection recomputed, and for `observing` steps the next
  observe floor. A lost lease is `LostClaim`, never a double-settle.
- `due_provision_operations` — the sole selector, keyset-bounded, claims
  nothing (the `due_polling_jobs` rule). The assembly worker owns cadence;
  this package owns only the floor. A provisioning job, like a poll job, is
  never dead-lettered automatically: `terminal`/`ambiguous`/
  `reconciliation_required` are operator-visible end states, and retryable
  work rides the saturating backoff until a lifecycle gesture stops it.
- `request_provision_cancel(db, …)` — records the product's cancel decision
  (only the owning product issues it); the next claim invokes `cancel` for
  each non-terminal step carrying a `provider_operation_ref`, in reverse
  dependency order, each under its own idempotency key.

## The secret-fetch seam (ADR-0076 §§7–8)

The runtime deliberately adds **no** table for projection requests: the open
projection request is network-access's record in the product, and the
credential owner honours one fetch against it. What this package guarantees
is the engine half of that contract:

- A step is invoked at most once per claim, and a settled step is never
  re-invoked. The at-most-once claim is what makes "fetch at most once per
  request" enforceable without the engine holding secret state.
- A step whose connector reports the fetch refused (closed error code
  `secret_fetch_refused` in evidence) settles to `reconciliation_required`,
  never `retryable`: retrying would either re-fetch (forbidden) or spin.
  Recovery is a new product-side projection request and a new operation.
- `AMBIGUOUS` apply results settle to `ambiguous` and are only ever followed
  by `observe`, never by a blind re-apply — so a lost response cannot cause a
  second fetch or a duplicate effect.
- Conformance obligation for the connector (slice 4, named now): persisted
  Integrator-side payloads contain no secret on every command path
  (ADR-0076 §7's test obligation), asserted against this runtime's tables.

The fetch transport itself (mutual authentication between the Integrator and
the product's fetch endpoint) is assembly + connector work in slices 4–5; the
SPI shapes already carry everything the connector needs (`operation_ref`,
step input identifiers, config, secret bindings for its own client
credential).

## Failure vocabulary

Persisted failure codes are a closed engine-owned set derived from this
package's exception types, as with `POLL_FAILURE_CODES`: a connector's
`error_code` is stored only after the SPI's machine-code validation and is
never branched on. The one bounded exception (sanitized exception type name)
follows the poll precedent.

## Tests and canaries (each with a planted-defect sensitivity proof)

- SPI 1.7: manifest digest changes when `fleet_targets` changes; pre-1.7
  manifest digests unchanged; config revision exceeding the ceiling refused
  at write, binding and activation; `require_network_target` refusal matrix
  (CIDR edges, port bounds, protocol mismatch, deny-all absence).
- Submission: same `command_id` + same plan replays; same id + different
  `plan_hash` conflicts; step input failing the capability command schema
  refused before any row exists; dependency cycles/unknown keys refused by
  the existing plan validation.
- Engine: two workers claim the same step — one wins, the other sees
  `LostClaim`; a worker dying mid-apply leaves a lease that expires into
  `ambiguous` → observe-only recovery (planted: re-apply must be refused);
  `depends_on` gating; kill switch refuses claims without touching rows;
  failure backoff uses the shared curve; terminal cancel+NOT_FOUND lands in
  `reconciliation_required`.
- Migration: schema-exists canary against the manifest table list, ACL
  canary (`app_user` revoked), downgrade refuses while operations exist.
- Conformance kit: a hand-written SPI 1.5 provisioning connector still
  discovers, conforms and is invoked under SPI 1.7 (the "SPI 1.0 still works"
  rule applied to 1.5).

## Explicitly out of scope here

- Connector implementation and FreeRADIUS specifics (slice 4); capability
  payload schemas and descriptor-v3 publication (slice 3); Integrator
  assembly routes/workers and Sub composition (slice 5).
- Retention/tombstoning of `plan_json` and `evidence_json`: the retention
  module grows a provision section when the first connector's evidence shape
  is known; until then rows are retained and the omission is recorded here
  rather than silently inherited.
- REQUEST-mode durability: SPI 1.6 stays persistence-free by design.

## Open questions carried into implementation

1. Does `submit` run connector `plan` synchronously as part of prepare, or as
   a distinct first claim with its own lease? (Draft answer above: first
   claim. Confirm against assembly worker topology in slice 5.)
2. Operation-level `correlation_id` provenance: copy destination-revision
   provenance onto the operation like receipts do, or join at read?
   (Precedent says copy; decide when writing `submit`.)
3. Should `observing` reuse the step row's `next_attempt_at`, or does observe
   need its own failure history table like `poll_schedule`? (Start with the
   row; add history only if the first connector's evidence needs it.)

