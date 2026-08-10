# Idempotency implementation sources — ERP and Sub

**As of:** 2026-08-10
**Starter:** `8472b9ee` (working tree on `docs/product-first-extraction`)
**ERP:** `3c86b5a9` (`chore/governance-b1dfd82-schema-v3`)
**Sub:** `5d6f115b7` (`feat/hold-boot-secrets`)

Step 2 of the product-first extraction procedure: *inventory ERP, Sub, and every
other product in scope, including callers, migrations, tests, operational repair
paths, and ownership.* Written before the contract was named (step 1, since done
in ADR-0014). An inventory is not permission to extract — the two-consumer,
same-contract, named-owner and cutover gate still applies.

Scope note: the raw token `idempot` matches 105 files in ERP and 300+ in Sub,
mostly docstrings and comments. This inventory counts **mechanisms** — a store
plus the code that enforces it — not mentions.

## Headline finding

Sub's shared `idempotency_keys` table has **one untyped `ref_id` column carrying
three mutually incompatible meanings**, decided per calling service:

| Service | `ref_id` holds | "In progress" is | Conflict behaviour |
|---|---|---|---|
| `crm_subscriber_provisioning.py` | the request **fingerprint** (SHA256) | n/a — reservation is fingerprinted at insert | raises `idempotency_conflict` |
| `subscription_lifecycle_commands.py` | the request **fingerprint** | n/a | returns a `rejected` outcome, no raise |
| `vendor_as_built_review_proposals.py` (+4 sibling vendor files) | the **result id** (`review_event_id`) | `ref_id` empty/NULL | raises `confirmation_in_progress` |

A reader cannot know what `ref_id` means without knowing which writer produced
the row, and the same value is a fingerprint in one scope and a foreign key in
another. Scopes keep them from colliding today, so this is a latent hazard
rather than a live incident — but it is the strongest single piece of evidence
that the mechanism needs one owner, and it is the kind of defect a typed shared
contract makes unrepresentable.

## Kernel baseline — what already exists

This materially narrows the work. `dotmac_kernel.messaging.inbox.process_once`
is already a correct general idempotent-execution primitive:

- keyed on `(tenant_id, command_id)` with a unique constraint;
- runs the handler **at most once**, replays the recorded result otherwise;
- the dedup marker commits atomically with the effect (shares the caller's
  transaction);
- loses a concurrent race safely via `conflict_savepoint`, returning the
  winner's result;
- obeys the kernel transaction-authority rule — `add`/`flush` only, never
  `commit`/`rollback`.

`dotmac_kernel.providers.provisioning` separately uses `operation_id` as a
provider-side idempotency/resume key.

**It already gets right the two things ERP's implementation gets wrong** (below).
The gap is therefore *not* "build idempotency"; it is four missing pieces:

1. no HTTP entry point — nothing maps an `Idempotency-Key` header to a
   `command_id`, and no cached-response replay exists;
2. no request **fingerprint**, so a replay carrying a *different* body is
   silently treated as a duplicate rather than a conflict — both products
   detect this and the kernel does not;
3. no TTL or retention — `InboxRecord` grows without bound;
4. no worker/task-level entry point.

## ERP — three independent mechanisms

| # | Mechanism | Store | Enforcement | Consumers |
|---|---|---|---|---|
| 1 | **API response replay** | `platform.idempotency_record`, uq(`organization_id`, `endpoint`, `idempotency_key`), 24h TTL, `request_hash` SHA256, `response_status` + `response_body` JSONB | `app/services/finance/platform/idempotency.py` (375 lines) + `app/api/idempotency.py` (91 lines) | **3** — `api/expense.py`, `api/me.py`, `api/people/expense.py` |
| 2 | **Posting guard** | none of its own — reads `JournalEntry.source_module`/`source_document_type`/`source_document_id` | `app/services/finance/posting/idempotency.py` (104 lines) | **6** — AP/AR posting invoice+payment, `dotmac_sub/sync/_payments.py`, self |
| 3 | **Saga / outbox keys** | `saga_execution.idempotency_key` (unique), `event_outbox.idempotency_key` | `saga_orchestrator.py`, `outbox_publisher.py` | finance platform internals |

Plus 5 model files carrying a bare `idempotency_key` column with a local unique
constraint (`posting_batch`, `bank_statement` match lines, and the three above).

### Defects in mechanism 1 (do not carry forward)

- **Commits inside the service.** `check`, `store_response`, `update_response`
  and `cleanup_expired` all call `db.commit()`. Under kernel rule 8
  (`dotmac_kernel/db.py` is the one transaction authority) this is disqualifying
  as written.
- **`reserve` calls `db.rollback()`** on `IntegrityError` — kernel rule 9
  forbids exactly this; `conflict_savepoint` is the sanctioned shape, which
  `process_once` already uses.
- **Stuck-placeholder failure mode.** `reserve` writes a `202 {"detail":
  "Request in progress"}` record before side effects. If the request then dies,
  that placeholder is served to **every retry for 24 hours** — the operation can
  never be re-driven. There is no lease, no stale detection, no way out.
- **`update_response` fallback writes `request_hash=""`.** A later `check` with
  a real hash mismatches and raises 409, so the fallback path poisons the key.
- **`get_cached_response` never compares `request_hash`** — the conflict check
  exists on one read path and not the other.
- **Expired-record delete inside a read.** `check` deletes and commits when it
  finds an expired row, so a GET-shaped call mutates and commits.

Mechanism 2 is materially healthier — pure reads plus a `flush`, no commit, and
its "backfill the document reference" secondary guard is a genuinely good
recovery path for imports and retries. It is domain-coupled to `JournalEntry`,
so it is a *pattern* source, not a portable one.

## Sub — three mechanisms, ~26 hand-rolled call sites

| # | Mechanism | Store | Enforcement | Consumers |
|---|---|---|---|---|
| 1 | **Shared key reservation** | `idempotency_keys`, uq(`scope`, `key`), + `account_id`, `ref_id`; **no TTL, no expiry, no cleanup** | none — each service writes its own replay logic | **26** service modules |
| 2 | **Celery task dedupe** | `task_execution.idempotency_key` + status/result/error | `app/services/task_idempotency.py`, `@idempotent_task` decorator | **11** decorated tasks |
| 3 | **Per-domain columns** | 40 `idempotency_key`/`dedupe_key` columns across 27 model files (billing ×8, network, comms, collections, imports, prepaid, ERP export, subledger…) | ad hoc, per owning service | — |

Mechanism 1 has **no shared helper at all**. Each of the 26 services re-derives
lookup, locking, fingerprint comparison and replay. Roughly 20 define a
near-identical private `_locked_replay`/`_replay` pair.

### The best implementation in either product

`app/services/crm_subscriber_provisioning.py` is the strongest candidate source
for request/command-level idempotency:

- `pg_advisory_xact_lock(hashtextextended(key))` serialises concurrent replays
  by key, degrading cleanly on non-Postgres;
- reservation read is `SELECT … FOR UPDATE`;
- stable fingerprint over `model_dump(mode="json")` with sorted keys and compact
  separators;
- typed, coded errors (`missing_idempotency_key`, `idempotency_conflict`);
- the result carries an explicit `replayed: bool` — the caller can tell a replay
  from a first execution, which neither ERP mechanism exposes;
- key length is validated (≤120) rather than truncated.

`vendor_as_built_review_proposals.py` contributes a different and complementary
idea worth keeping: the key is the **`jti` of a signed proposal token**, plus a
`state_fingerprint` compared with `hmac.compare_digest`, so a stale preview is
rejected (`stale_proposal`) instead of replayed. That is a stronger
authorisation story than a client-chosen key, and it is the pattern behind Sub's
mandatory-`Idempotency-Key` command surfaces.

### Defects in Sub's mechanisms

- **`idempotency_keys` has no TTL, no retention, no cleanup path** — it grows
  forever. ERP's 24h TTL is the better half of that trade.
- **`ref_id` is untyped and overloaded** — the headline finding above.
- **`@idempotent_task` retry key is unbounded.** On a failed prior execution it
  builds `key + ":retry:" + datetime.now().isoformat()`, so every retry mints a
  *new* row and the retry itself is not idempotent — a duplicated retry
  delivery executes twice.
- **`@idempotent_task` calls `db.rollback()`** in `_get_or_create_execution` and
  `db.commit()` in the wrapper (same kernel rules 8/9 conflict as ERP).
- **Stale-running detection is a fixed 1-hour wall-clock timeout**, not a lease
  — a task legitimately running for 61 minutes is marked failed and a second
  copy is allowed to start.
- **No tenant dimension anywhere.** Sub has no `tenant_id`/RLS by design, so
  `uq(scope, key)` is globally unique. The kernel's `(tenant_id, command_id)`
  is the shape a multi-tenant contract needs; this is a real mapping question
  for Sub's cutover, not a defect in Sub.

## Behaviour proofs available to port

| Repo | File | Covers |
|---|---|---|
| ERP | `tests/ifrs/platform/test_idempotency_service.py` | the `IdempotencyService` surface directly |
| ERP | `tests/test_expense_idempotency.py` | 4 request-replay cases end to end |
| ERP | `tests/services/test_dotmac_sub_payment_idempotency.py` | cross-product payment sync replay |
| Sub | `tests/test_task_idempotency.py` | the Celery decorator |
| Sub | `tests/test_crm_subscriber_provisioning.py` | 3 cases on the strongest implementation |
| Sub | `tests/test_crm_invoice_idempotency.py`, `test_crm_payment_idempotency.py` | CRM-boundary replay |
| Sub | `tests/test_subscription_lifecycle_commands.py` | 7 replay/conflict cases |
| Sub | `tests/test_vendor_submission_proposals.py` | 3 signed-proposal replay cases |

## Reading for the contract-naming step

1. **There are at least three separable contracts here, not one.** Request/
   command replay (client-supplied key, fingerprint, conflict, cached result);
   scheduled-work dedupe (worker-derived key, lease, stale recovery); and
   natural-key posting guards (no key at all — derive from the source document).
   Bundling them into one facility would repeat the mistake both products made.
   The first is the one with two real consumers.
2. **The kernel is the healthiest implementation, and it is nobody's source.**
   `process_once` already satisfies the transaction and concurrency rules both
   products violate. The port direction is therefore *inward for semantics*
   (fingerprint, conflict, TTL, lease, `replayed` flag) and *outward for
   mechanics* — not a wholesale replacement of `process_once`.
3. **Sub is the source for semantics; ERP is the source for retention.** Sub's
   advisory-lock + fingerprint + typed-conflict + `replayed` design is better on
   every axis except that it never expires anything.
4. **Placement is unsettled.** `packages/*/EXTRACTION.toml` distinguishes
   `universal-facility` (kernel) from `optional-module`. Idempotency sits
   directly on `db.py` and `messaging`, which argues kernel — but that is a
   step-1 decision, not an assumption of this inventory.

## Not yet inventoried

- **Migrations and rollout shape** for `platform.idempotency_record` and
  `idempotency_keys` — neither product's migration history was read.
- **Operational repair paths** — what an operator does today with a stuck ERP
  `202` placeholder or a wedged Sub reservation. No runbook was located.
- **`dotmac_academy_app` and `dotmac_vendor_control_plane`** — out of scope
  here; Academy is a discovery target admitted only to Phase 0/1.
- **The 40 per-domain Sub columns** were counted, not characterised. Some are
  genuine natural keys that should *stay* domain-local; deciding which is part
  of the cutover slice, not this inventory.
