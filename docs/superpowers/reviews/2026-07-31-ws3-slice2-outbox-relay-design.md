# WS3 slice 2 — outbox relay / dispatcher: security design

> **Status:** Design only (2026-07-31). No implementation is authorized by this
> document. It is the focused design the relay's privileged cross-tenant drain
> requires before any code lands, per Michael's directive: the drain "must not
> become a generic RLS bypass or reuse platform/app-admin authority."

## Problem

WS3 slice 1 shipped the transactional **outbox** (`outbox_events`, tenant-scoped,
RLS-protected) and **inbox** (idempotent `process_once`). Slice 2 is the
**relay** (dispatcher): a background worker that drains `pending` outbox rows and
delivers each event to its transport, then marks it `sent` (or retries / dead-
letters). Delivery is out-of-band from the request that enqueued the event.

The relay is **inherently cross-tenant**: it must see and claim pending rows for
*every* tenant. That is a privilege no request-time role has, and getting it
wrong turns the relay into a hole in row-level security. This design bounds that
privilege so the relay can drain across tenants **without** becoming a general
RLS bypass and **without** reusing `platform_api` (platform routes) or
`app_admin` (migrations, `BYPASSRLS`) authority.

## Security boundary — a dedicated, narrowly-scoped dispatcher role

Introduce a new Postgres role **`outbox_dispatcher`**, distinct from the three
existing roles and used by nothing else:

| Role | Today | May the relay use it? |
|---|---|---|
| `app_user` | request role, RLS-enforced, one tenant per request | **No** — cannot see other tenants' rows; that is the point of RLS. |
| `platform_api` | platform routes, no RLS bypass | **No** — reusing it would smear the relay's cross-tenant reach onto the platform surface. |
| `app_admin` | migrations, `BYPASSRLS`, schema owner | **No** — `BYPASSRLS` on the *whole database* is exactly the generic bypass to avoid. |
| **`outbox_dispatcher`** (new) | the relay only | **Yes**, and ONLY as scoped below. |

`outbox_dispatcher` is **not** `BYPASSRLS`. Its cross-tenant reach is confined to
the outbox table alone by one of two mechanisms (decision at implementation, the
first is preferred):

1. **Scoped RLS policy (preferred).** `outbox_events` keeps RLS `ENABLE`d +
   `FORCE`d. Add a policy that grants `outbox_dispatcher` (only) `USING (true)` on
   `outbox_events` — i.e. the dispatcher sees all tenants' rows *for this one
   table*. It has **no** privilege (no `GRANT`, no policy) on any other
   tenant-scoped table, so it cannot read parties, subscribers, settings, etc.
   The bypass is table-local and role-local, auditable in one policy.
2. **`SECURITY DEFINER` claim function.** A single `claim_outbox_batch(n)`
   function owned by `app_admin`, `SECURITY DEFINER`, that performs the atomic
   claim and returns the batch. `outbox_dispatcher` gets `EXECUTE` on that
   function and on nothing else. The cross-tenant reach lives entirely inside one
   reviewed function body.

Either way the invariant holds: **the dispatcher role can touch `outbox_events`
and nothing else.** A dispatcher connection that tries to `SELECT` a tenant
business table gets "permission denied" — proven by a least-privilege test.

## Atomic leasing (no double-delivery, safe concurrency)

Multiple dispatcher workers may run. Claiming uses `FOR UPDATE SKIP LOCKED` so
workers never block each other and never claim the same row twice:

```sql
UPDATE outbox_events
   SET status = 'claimed', leased_by = :worker_id, leased_at = now()
 WHERE id IN (
   SELECT id FROM outbox_events
    WHERE status = 'pending' AND available_at <= now()
    ORDER BY available_at
    FOR UPDATE SKIP LOCKED
    LIMIT :batch
 )
RETURNING *;
```

New columns on `outbox_events` (a future migration): `leased_by text NULL`,
`leased_at timestamptz NULL`; and the status vocabulary extends to
`pending → claimed → sent | failed | dead`.

## Crash recovery (stale leases)

A worker that dies mid-delivery leaves rows stuck in `claimed`. A row is
reclaimable when `status = 'claimed' AND leased_at < now() - :lease_timeout`. The
claim query above is widened to also pick up stale-`claimed` rows (or a small
reaper resets them to `pending`). `lease_timeout` is a documented config knob.
Because delivery is idempotent (below), reclaiming a row a previous worker had
already delivered but not yet marked `sent` causes at most a duplicate *delivery
attempt*, which the consumer dedupes — never a lost event.

## Retry / dead-letter (evidence retained)

- On delivery failure: `attempts += 1`, `last_error := <message>`, and
  `available_at := now() + backoff(attempts)` (capped exponential), status back
  to `pending`.
- After `max_attempts`: status `dead` (a dead-letter), row **retained** with its
  `last_error` and full history — never silently dropped. A dead row is operator-
  visible and replayable by resetting it to `pending`.
- `sent_at` is stamped on success; `sent` rows are retained for an auditable
  window then archived/pruned by a separate policy (not the relay's job).

## Tenant context restoration (the subtle one)

The dispatcher is cross-tenant only for the **claim**. **Delivery must run with
the event's own tenant context restored**, never with the dispatcher's broad
reach. For each claimed event the relay sets
`SELECT set_config('app.current_tenant', :event.tenant_id, true)` on the delivery
transaction, so any tenant-scoped read the transport/handler performs is isolated
to that event's tenant exactly as a request would be. The dispatcher's
table-local outbox bypass never leaks into delivery-time data access.

## Idempotent delivery

Delivery is at-least-once; the transport/consumer must be idempotent. The event
`id` is the delivery idempotency key (a delivery-side inbox, mirroring
`process_once`, or a transport-native dedupe). "Exactly-once" is not promised and
not needed — the outbox guarantees the event *exists* iff its state change
committed; the relay guarantees it is *eventually delivered at least once*.

## Explicit non-goals / must-nots

- **Not a generic RLS bypass.** `outbox_dispatcher` has zero privilege on any
  tenant business table; its only cross-tenant power is claiming `outbox_events`.
- **No reuse of `platform_api` / `app_admin`.** The relay never authenticates as
  a platform or migration role.
- **Delivery is transport, not decision.** The relay applies no business logic
  and owns no domain state; it moves already-decided events to their transport.
- **The relay is not the vendor fleet runner.** (Fleet/deployment execution is a
  separate, design-gated concern owned by the vendor control plane.)

## Acceptance tests (must exist before slice 2 is "done")

1. **Least privilege:** an `outbox_dispatcher` connection can claim/update
   `outbox_events` but gets `permission denied` on `parties`/`domain_settings`/
   any tenant table.
2. **Concurrent claim, no double-delivery:** N workers over M rows deliver each
   row exactly once (SKIP LOCKED).
3. **Crash recovery:** a stale `claimed` row is reclaimed after `lease_timeout`.
4. **Retry + dead-letter:** a failing event backs off, then lands in `dead` with
   `last_error` after `max_attempts`, retained.
5. **Tenant isolation at delivery:** during delivery of a tenant-A event, a
   tenant-scoped read sees only tenant A's data (context restored), not the
   dispatcher's cross-tenant view.
6. **`outbox_dispatcher` is not `BYPASSRLS`/superuser** (role-hygiene check,
   alongside the existing `app_user`/`platform_api` checks in the RLS catalog).

## Open decisions for implementation

- Scoped RLS policy vs `SECURITY DEFINER` claim function (prefer the policy).
- Relay runtime: in-process worker vs separate process; `LISTEN/NOTIFY` wakeups
  vs polling `available_at`.
- `lease_timeout`, `max_attempts`, backoff curve, and `sent`-retention as config
  knobs with prod-safe defaults.

These are settled when slice 2 is scheduled; this document fixes the security
boundary they must respect.
