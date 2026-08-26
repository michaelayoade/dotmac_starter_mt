# ADR-0032: Integrator executes exact approved provisioning commands

- **Status:** Accepted
- **Date:** 2026-08-17
- **Amends:** ADR-0024

## Context

ADR-0024 makes the independently deployed Integrator the sole external
connector control plane and gives the `dotmac-integration` module ownership of
registry, bindings, retries, checkpoints and evidence. The shipped SPI can
deliver, receive and poll observations. It cannot truthfully represent fleet
provisioning: there is no engine mode with `plan/apply/observe/cancel`, no
operation ledger bound to an approved plan hash, and no structured signed
receipt.

Vendor CP now owns provider-neutral desired state and exact deployment plans.
Letting Vendor call provider SDKs would violate ADR-0024; encoding apply as a
magic delivery event would hide approval and lifecycle semantics inside an
unstructured payload.

## Decision

### 1. Provisioning is a first-class connector mode

SPI 1.2 adds `ConnectorMode.PROVISION` and a `ProvisionPlugin` protocol. Its
handler implements four distinct operations:

- `plan` validates and returns a typed provider-neutral preview;
- `apply` executes only an exact approved saved plan;
- `observe` reports current external facts without changing desired state; and
- `cancel` performs only the connector's declared bounded compensation.

The mode is part of the engine's closed mode-to-protocol registry. Discovery,
startup and activation refuse a connector that declares the mode without its
handler, or exposes the handler without declaring the mode.

#### Every declared operation schema is executable

The product owner supplies the exact input and output schema for all four
engine operations; no pair is documentary metadata. PLAN validates every
proposed step input before recording its command, validates the connector's
typed evidence against the held plan output, and binds the evidence digest into
the immutable PLAN receipt without persisting its values.

APPLY persists the original approved step input. OBSERVE and CANCEL derive a
provider-neutral immutable target from that durable input by copying only the
top-level properties declared by their held input schema, then validate the
target before claim or provider I/O. They validate successful connector
evidence against the held output schema (`CANCELLED` and `NOT_FOUND` are
successful cancel outcomes). The operation receipt stores the canonical digest
of the complete evidence and only its schema-derived public, non-secret
projection. Installation configuration and held secret references are supplied
separately and never become operation input or target fields.

### 2. The module owns the provisioning operation ledger

`dotmac-integration` owns immutable command identity, collision detection,
operation state, step attempts, observations, receipt evidence and retry/
indeterminate classification. An operation key replayed with identical content
returns the recorded result; the same key with a different command digest,
deployment assignment, desired revision or plan hash is a conflict.

The engine claims an operation transactionally, releases the database session
before connector I/O, and settles from the typed result in a new transaction.
`indeterminate` pauses for `observe`; it is never blindly replayed. The module
does not decide a Vendor deployment transition.

### 3. Apply binds every authority and artifact input

The accepted command envelope binds at least:

- command and operation ids, deployment id and desired-state revision;
- saved plan id and exact `plan_hash`;
- approval request/grant ids, grant digest and expiry;
- managed-profile and command schema versions;
- capability id/schema version and exact installation/binding ids;
- connector key/version/manifest/artifact/configuration digests;
- component artifact and configuration digests;
- issued-at, expires-at, nonce and intended Integrator audience; and
- authenticated account/deployment assignment.

The module refuses stale, ambiguous, expired or mismatched values before
calling a plugin. Secrets are held by the Integrator assembly and materialized
only for the selected installation.

#### Cross-binding dependencies bind static intent and later evidence separately

A global plan may order work carried by different capability bindings. The
approved document contains a canonical command template per binding and its
sorted symbolic prerequisite binding ids. Its template digest covers the
deployment, capability and binding, artifact/configuration digests, exact local
steps and those symbolic edges.

The terminal receipt proving an upstream apply succeeded cannot exist when the
plan is approved. It is therefore never predicted or smuggled into the plan
hash. At dispatch, Vendor supplies a signed, command-fingerprinted receipt pin
for every approved prerequisite binding. The module locks upstream operations
in canonical UUID order and requires the exact deployment, plan, binding,
`succeeded` state, and latest immutable receipt sequence/hash before creating
the downstream operation. Missing, additional, stale or cross-plan pins fail
before connector I/O. Dynamic receipt bytes are execution evidence; the static
dependency graph remains the approved decision.

### 4. Receipts are canonical and signed

A receipt binds the command, operation, deployment, plan and approval digests;
connector and target-agent identity; artifact/configuration/schema
fingerprints; typed step outcome; opaque resource references; evidence-object
digests; timestamps; and signature key id. Connector exception text, secrets
and customer payloads never enter the receipt.

The module writes an immutable hash-chain receipt from its locked operation and
step rows; it accepts no private signing port and never loads a key or network
secret. The assembly projects that module evidence into a canonical transport
receipt and signs the projection with receipt-signing material held at boot.
Target-agent signed evidence is preserved as a nested digest-bound evidence
reference rather than replaced with prose.

### 5. Command intake is a distinct authenticated surface

The thin `dotmac_integrator` assembly exposes a machine command surface
separate from `/operations`, provider `/ingress` and probes. It authenticates an
audience/scope-bound service identity, corroborates deployment assignment from
that identity, validates the complete envelope, and passes the typed command to
the module-owned command ledger. Each authenticated APPLY dispatch claims and
drives at most one durable step outside the database transaction; replaying the
same signed command advances the next claimable step without duplicating prior
provider I/O. PLAN, OBSERVE and CANCEL use their corresponding signed command
routes and module-owned prepare/invoke/settle phases.

Vendor CP never supplies shell text. A deployment-host connector may invoke
only versioned allowlisted bundle operations on a constrained host agent.

## Ownership

- Vendor CP owns desired state, plan DAG, exact-hash approval, lifecycle
  transition and compensation decision.
- `dotmac-integration` owns command/operation/step/receipt persistence,
  collision detection, claims, retry, checkpoints and repair evidence.
- `dotmac_integrator` owns authentication, held secrets/signing material,
  command routes and plugin composition; it remains a thin assembly and owns
  no second command or receipt ledger.
- connector distributions own provider wire mappings and external I/O only.
- products own their users, data, authorization and local sessions.

## Consequences

- No provisioning provider or product name enters the shared engine.
- Existing delivery/ingress/poll connectors remain compatible with SPI 1.2
  when their declared range admits it.
- A real connector cannot be admitted until its conformance suite proves
  idempotent apply, separate observe, typed failure classification, redaction
  and evidence semantics.
- Control-plane failure stops new changes; it does not enter an application's
  request/session path.

## Acceptance

1. The mode registry has a sensitivity proof for a mode with no executable
   contract.
2. A mismatched/expired plan, approval, assignment, connector or artifact is
   refused before plugin I/O.
3. Same-key/same-content replay returns one operation; same-key/different-content
   is a conflict.
4. Crash after connector return can be resumed without a second blind apply.
5. `indeterminate` requires observe and cannot be auto-retried.
6. Signed receipt verification detects any changed bound field.
7. A fake connector proves plan/apply/observe/cancel and partial-failure resume.
8. Assembly architecture tests prove no provider branch and no generic
   SSH/arbitrary-shell surface.
