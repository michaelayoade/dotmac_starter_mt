# Delivery and outbox sources — Sub, and the kernel engine that already exists

**As of:** 2026-08-10
**Starter:** `integration/communication-capability-map` (main `d93164b` + 3)
**ERP:** `3c86b5a9` (`chore/governance-b1dfd82-schema-v3`)
**Sub:** `5d6f115b7` (`feat/hold-boot-secrets`)

The inventory for the **delivery/outbox** owner named by ADR-0006 § 5c, taken
after consent landed (kernel `0.1.0a34`) because § 5c sequences consent first.

Step 2 of the extraction procedure in
[`module-extraction-sources.md`](module-extraction-sources.md). Facts, not a
mandate.

## Two headline findings, and both change the shape of the work

### 1. The kernel already owns the delivery engine. Do not build a second one.

Sub's `Notification` queue and the kernel's `OutboxEvent` are the same machine,
built twice:

| Concern | Sub `Notification` + `tasks/notifications.py` | Kernel `OutboxEvent` + relay |
|---|---|---|
| Queue state | `status` queued → sending → delivered/failed/canceled | `status` pending → claimed → sent/dead-letter |
| Attempt count | `retry_count` | `attempts` |
| Backoff | `_retry_backoff_minutes(retry_count)` step table | `available_at` |
| Worker claim | `status = sending` + `_sending_timeout_minutes` reclaim | `leased_by`/`leased_at` + stale-lease reclaim |
| Dead letter | `status = failed` past `_max_retries` | retained dead-letter status |
| Scheduling | `send_at` | `available_at` |

Sub's predates the kernel's and could not have used it. The 2026-08-09 amendment
governs this exactly: *a capability a second Dotmac app would otherwise
reimplement is built in the shared layer* — and this one has already been
reimplemented, so porting Sub's queue wholesale would install the duplicate
permanently rather than retire it.

ADR-0014 also already states the rule from the other side: **"Non-transactional
effects belong in the outbox."** Sending an email is the canonical
non-transactional effect. The kernel's outbox is where it goes.

So the delivery owner is **not a queue**. It is a typed payload on the existing
outbox, a provider seam, and the two things below that genuinely do not exist.

### 2. Sub's bounce → consent feedback loop does not exist

This one is worth stating precisely, because it changes what consent is worth in
production.

- `DeliveryStatus.bounced` is **declared and never assigned**. Every other
  `grep` hit for "bounced" in `dotmac_sub/app` is the word *debounced* from
  network monitoring.
- `SuppressionReason.bounce` and `SuppressionReason.complaint` have **zero call
  sites**.
- Exactly **one** site in the whole product writes a suppression:
  `comms_campaigns.py:355`, the campaign unsubscribe link.

So Sub's consent ledger is, operationally, **unsubscribe-only**. The `all` scope
— the one that stops sending to a dead address, and the only one that protects
transactional delivery — is designed, documented, and never populated by any
automated path. `NotificationDelivery` rows are written on send, but nothing ever
ingests a provider's later verdict.

That is the gap that makes "consent before delivery" more than a sequencing
slogan. A ledger nothing writes to is a ledger that answers "yes, send" forever.

## What Sub does have that is worth porting

| Piece | Where | Keep |
|---|---|---|
| Delivery receipt | `NotificationDelivery` (`provider`, `provider_message_id`, `status`, `response_code`, `response_body`, `occurred_at`) | **Yes** — the kernel outbox records that we dispatched, never what the provider said |
| Receipt idempotency | partial unique index on `(provider, provider_message_id) WHERE is_active AND both NOT NULL` | **Yes** — this is what makes a provider webhook safe to redeliver |
| Per-channel rate limit | `_per_channel_rate_limit(db)` | **Yes**, as an outbox dispatch policy |
| Queue-age expiry | `_expire_stale_notifications` / `_max_queue_age_hours` | **Yes** — a two-day-old outage SMS should not send |
| Provider adapters | `email.py` (1,651), `sms.py` (428), whatsapp capability | **Seam only** — see below |

Total source under review: `tasks/notifications.py` (877),
`services/notification.py` (1,422), `email.py` (1,651), `sms.py` (428).

## What must NOT come across

- **The second queue.** `Notification.status`/`retry_count`/`send_at` and the
  whole claim-and-retry loop in `tasks/notifications.py`. The kernel outbox owns
  it.
- **Provider clients.** SMTP, Twilio, Africa's Talking and Meta Cloud API
  clients are product dependencies. The kernel ships a `Protocol` and no client,
  exactly as ADR-0009 ships a `SecretSource` seam and no store client. A product
  that sends by SMTP brings its own SMTP.
- **`NotificationChannel` as an enum** — an open registered string, as consent
  already does.
- **No tenancy.** Same as consent: Sub is single-tenant, the extracted tables
  are tenant-scoped with RLS from their first migration.
- **`db.commit()` inside services.** `services/notification.py` and the task
  module commit their own transactions in several places.

## The shape this points to

1. **`communication_deliveries`** — the receipt. Tenant-scoped, RLS, keyed for
   idempotent provider callbacks on `(tenant_id, provider, provider_message_id)`.
2. **A provider `Protocol`** — `send(message) -> DeliveryReceipt`. Kernel-side
   contract, product-side implementation.
3. **The feedback loop, which is new code rather than a port**: a receipt whose
   status is `bounced` or `complaint` calls
   `consent.suppress(scope=all, reason=...)`. This is the piece that makes the
   consent ledger self-maintaining, and no product has it.
4. **Dispatch rides `dotmac_kernel.messaging`** — a typed outbox payload, not a
   new table, not a new worker.

Ordering note: (3) is the highest-value increment and the smallest. It is also
the only part with no source implementation to port, so it needs its own tests
rather than inherited ones.

## Behaviour proofs available to port

Sub's delivery tests are entangled with its queue, which is the part not being
ported, so the harvest is smaller than the line count suggests:

| Test | Proves | Portable? |
|---|---|---|
| `tests/test_email_services.py` (24) | send path, receipt writing | Partly — the receipt assertions |
| `tests/test_notification_queue_suppression.py` (4) | the queue asks consent before sending | **Yes** — already ported into `tests/unit/test_consent.py` |
| queue/retry/backoff tests in `tests/` | Sub's own queue engine | **No** — the kernel outbox has its own |

## Not covered

Campaign send mechanics, the team-inbox delivery receipts
(`team_inbox_delivery_receipts.py`, a different transport), and Sub's
`CommunicationIntentRecord` — the durable reason/policy record, which is a
separate § 5c concern and would need its own dossier if it is ever extracted.
