# ADR-0014: At-most-once execution has one owner

**Status:** Accepted
**Date:** 2026-08-10
**Supersedes in part:** the WS3 framing in which `inbox_records` was an
"inbox" table belonging to the messaging subsystem.

## Context

`docs/inventories/idempotency-sources.md` (2026-08-10) inventoried every
idempotency mechanism in the fleet. It found **six** — three in ERP, three in
Sub — plus the kernel's own. The evidence that forced this decision:

- Sub's shared `idempotency_keys.ref_id` column carries **three mutually
  incompatible meanings** depending on which of its 26 calling services wrote
  the row: a request fingerprint in two, a result id in five others, where an
  empty value additionally encodes "in progress". Scopes keep them from
  colliding today. Nothing but convention prevents it tomorrow.
- ERP's `platform.idempotency_record` writes a `202 "Request in progress"`
  placeholder **before** the effect runs. If the request then dies, that
  placeholder is replayed to every retry for 24 hours and the operation can
  never be re-driven. There is no lease and no recovery path.
- Sub's `@idempotent_task` builds its retry key as
  `key + ":retry:" + now().isoformat()`, so **the retry itself is not
  idempotent** — a duplicated retry delivery executes twice.
- Neither product's mechanism obeys kernel rules 8 and 9: both commit and
  roll back inside service code.

Meanwhile the kernel already had a correct primitive —
`messaging.inbox.process_once` — which no product uses, and which was framed
narrowly as transport-delivery dedupe rather than as the general facility it
actually is.

This is the build-once case, not the harvest-what-looks-alike case ADR-0006 § 5
forbids: not two things that merely resemble each other, but **one thing with
one correct encoding**, implemented six times.

## Decision

### 1. The contract

> **At-most-once execution with result replay.** Given a `scope` (the operation
> family), a `key` (supplied by the caller — a client, a transport, or a
> scheduler), and optionally a `fingerprint` of the request that produced it,
> run an effect **at most once** and return the recorded result for every later
> attempt carrying the same `(tenant_id, scope, key)`. An attempt whose
> fingerprint **differs** from the recorded one is a **conflict**, not a replay.

### 2. `dotmac_kernel.idempotency` is the one owner

It owns the store (`idempotency_records`, `platform_idempotency_records`), the
engine (`execute_once`, `execute_once_platform`), the fingerprint function, the
conflict error, and the retention purge. `dotmac_kernel.messaging.inbox` and
`.platform` are **re-expressed as thin callers** of it and keep their published
signatures; they no longer own a store. `inbox_records` is renamed, not
duplicated — there is exactly one table answering "has this been done".

### 3. Key identity is `(tenant_id, scope, key)`

Deliberately neither product's shape:

- **Not ERP's `(organization_id, endpoint, key)`.** `endpoint` is an HTTP
  artifact leaking into the store; the same logical operation reached through a
  second route would get a second ledger. `scope` is a caller-declared
  operation family, opaque to the kernel.
- **Not Sub's `(scope, key)`.** It carries no tenant because Sub has no
  tenancy. A shared facility cannot inherit that; tenant scoping with RLS is
  non-negotiable under hard rule 11. Sub's cutover maps its single deployment
  to one tenant, per ADR-0003.

### 4. The fingerprint is explicit and typed — never overloaded

A separate nullable `fingerprint` column, never a reused id column. `None`
means *the caller asserts the key alone identifies the request* — the correct
reading for a transport-generated `command_id`. A non-`None` fingerprint is
compared on replay, and a mismatch raises `IdempotencyConflict`. This gives
both products' behaviour without either's encoding: Sub's overloaded `ref_id`
and ERP's `request_hash=""` sentinel are both unrepresentable.

### 5. No upfront reservation — this is the answer to ERP's stuck placeholder

The effect and the ledger row commit **in the same transaction**. Nothing is
written before the effect runs. Therefore:

- a crash mid-effect leaves **no** row, so the retry re-drives cleanly;
- there is no "in progress" state to get stuck in, no lease to expire, and no
  recovery runbook to write, because the failure mode does not exist;
- a concurrent duplicate loses the unique-constraint race, its work rolls back
  to a `SAVEPOINT` (`conflict_savepoint`, hard rule 9), and it replays the
  winner's result.

**Rejected alternative — reserve-then-execute** (ERP's design, and the shape
Sub's vendor proposals approximate). It buys the ability to tell a concurrent
caller "in progress" instead of making it wait, and it is the only way to guard
a **non-transactional** effect. It costs a stuck state that needs a lease, a
stale detector, and an operator recovery path — and ERP shipped it without any
of the three. We decline the trade. See § 7 for non-transactional effects.

### 6. Retention is a product policy, not a kernel constant

`expires_at` is **nullable** and the kernel sets no default TTL. ERP hardcodes
24 hours; Sub never expires anything and the table grows forever. Neither is a
default the kernel may impose — a payment replay window and a provisioning
replay window are not the same duration. The product sets `expires_at` per call
or schedules `purge_expired(...)`. This is the repo's "everything by config"
rule applied to retention.

### 7. Non-transactional effects are explicitly out of scope

If the effect is an external call that cannot join the database transaction,
this facility is the wrong tool and reserve-then-execute is a trap. The kernel
already owns the right one: enqueue through the **outbox** in the same
transaction, and let the relay own delivery, retry and dead-lettering. Stated
here so nobody rebuilds ERP's reservation on top of this contract.

### 8. No worker decorator ships in the kernel

Sub's `@idempotent_task` is Celery-coupled. The kernel has no Celery dependency
and takes none. A worker calls `execute_once` with a key it derives — the
decorator is a product adapter. Shipping one would hardcode a scheduler choice
into a universal facility.

## Consequences

- **Breaking, alpha-window.** `InboxRecord` → `IdempotencyRecord`,
  `PlatformInboxRecord` → `PlatformIdempotencyRecord`, tables renamed, `scope`
  added. `process_once` / `process_once_platform` / `ProcessOutcome` keep their
  signatures and behaviour, so `dotmac_vendor_control_plane`'s service code —
  the only external consumer — is unchanged. Its `tests/unit/test_accounts.py`
  imports `PlatformInboxRecord` directly and needs a one-line rename to
  `PlatformIdempotencyRecord` when it takes the bump.
- Migration `0018` renames both tables, adds `scope`/`fingerprint`/
  `expires_at`, backfills `scope='inbox'` for existing rows, and re-applies RLS
  and grants. No data is dropped.
- The three separable contracts the inventory identified are **not** merged.
  This ADR governs only the first (request/command replay). Natural-key posting
  guards (ERP's `PostingIdempotencyService`) derive their key from the source
  document and need no ledger — they stay product-owned. Scheduled-work dedupe
  is § 8.
- ERP and Sub adopt on a released pin, each retiring its local owner. Until
  both do, the extraction is **not** complete: the product-first procedure
  counts an extraction as done only once a second independent consumer
  exercises the same contract and the source product's local owner is gone.

## Compliance

Hard rule 8 (one transaction authority): the engine only `add`/`flush`.
Hard rule 9 (no service `rollback`): the race is handled by
`conflict_savepoint`. Hard rule 11 (tenant tables): `tenant_id NOT NULL`,
composite unique, RLS in the same migration. Hard rule 12: `scope` is a
caller-declared string, not an enum — ADR-0008's registry principle.

**Compliance amendment — 2026-08-19.** `execute_once` and
`execute_once_platform` operate exclusively on the application-owned `Session`
they receive. Their savepoint comes from the kernel-private, engine-free
transaction mechanic; they do not import the eager `dotmac_kernel.db` runtime.
That module remains the one public transaction authority and re-exports the
same `conflict_savepoint` API (ADR-0024 caller-owned-runtime amendment).

## Amendment — 2026-08-25: exact replay precedes mutable-state validation

"Replay first" does not mean "trust first" or "skip validation". The required
order for an idempotent writer is:

1. authenticate and authorize the caller and tenant/platform scope;
2. validate and canonicalize the command shape, idempotency key and exact
   request fingerprint;
3. look up the idempotency record and compare its stored fingerprint;
4. return the stored result for an exact replay, or refuse the same key carrying
   a different fingerprint; then
5. only for a new request, run mutable-state business preconditions and perform
   the effect plus ledger write in the same transaction.

The lookup must precede overlap, uniqueness, "already open", availability and
similar preflight decisions whose answer can change because the first attempt
succeeded. Otherwise a retry fails against the state it created and the writer
is not idempotent in practice.

Authentication, authorization, scope isolation, parsing, canonicalization and
fingerprint-conflict checks are never bypassed by replay. They establish that
the caller may receive the stored result and that the request is the same one.

The test contract follows the service contract: build one command once and
submit that same command/key twice. A helper that previews and rebuilds the
command on its second call tests preflight again, not replay. A fingerprint
conflict test must use inputs that pass earlier preconditions and assert the
specific conflict reason.

The reference is
`dotmac_subscriptions.treatments.approve_billing_arrangement`: it resolves the
stored arrangement and compares the preview fingerprint before rerunning the
mutable overlap preview. Its unit canary submits one command twice. The kernel
owner itself already follows the same sequence: `_validate` →
lookup/replay-or-conflict → `operation`.

Enforcement:
`tests/architecture/test_semantic_identity_and_replay.py` proves both reference
orders structurally and includes a planted operation-before-lookup sensitivity
case; `tests/unit/test_subscriptions_treatments.py` proves exact replay behavior.
