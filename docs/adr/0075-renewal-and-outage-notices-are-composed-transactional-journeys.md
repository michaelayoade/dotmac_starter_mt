# ADR-0075: Renewal and outage notices are composed transactional journeys

- Status: Proposed
- Date: 2026-10-16
- Decider: Michael
- Related: ADR-0014 (at-most-once has one owner), ADR-0016 (payment coverage
  is derived), ADR-0024 (apps compose by synchronizing data), ADR-0032
  (unobserved is UNKNOWN), ADR-0056 (campaigns own outbound progression);
  Sub ADR 0007 and ADR 0020 (billing destination is Starter `dotmac-billing`)

## Context

Dotmac's customer base is mostly prepaid. Revenue depends on subscribers
renewing on time, and their trust depends most on what happens during an outage.
The first two customer journeys Starter should own are:

1. **Renewal and expiry reminders.** Before and at the end of a prepaid
   period, the subscriber gets a reminder with a link to pay.
2. **Proactive outage and maintenance notices.** When an incident or planned
   work affects a subscriber's service path, they are told before they have to
   call. They are told again when service is restored.

These replace a Sub proposal (sub#3658, closed) for birthday and anniversary
emails, which was low-value marketing work built inside a frozen app.

The building blocks already exist as Starter modules:

| Need | Owner |
|---|---|
| Period end, renewal, plan change | `dotmac-subscriptions` |
| Payable position of an obligation | `dotmac-billing` |
| Payment intent, provider checkout, confirmation | `dotmac-payments`, `dotmac-integration` |
| Durable, supersedable timers | `dotmac-durable-timers` |
| Incidents, maintenance windows, impact, notification evidence | `dotmac-network-assurance` |
| Affected paths and reachability | `dotmac-network-topology` |
| Consent and delivery | kernel `consent`, `delivery` |
| Template rendering | `dotmac-template-studio` |

What is missing is the composition between them, plus the rules that keep
it safe.

The current Sub payment link is the counter-example this ADR must not repeat.
Today Sub does all of the following itself:

- decides whether an invoice is payable from its own invoice status and
  `balance_due`;
- computes the amount;
- creates a Sub-owned top-up intent;
- selects the provider route.

All of that is billing logic in Sub.

## Decision

### 1. Both journeys are transactional service communication, not campaigns

- Renewal reminders and outage notices are sent because of the customer's own
  service. They are not promotion.
- Their delivery categories are `service_renewal` and `service_incident`.
- Neither is registered with `register_marketing_categories`. A marketing
  unsubscribe therefore does not stop them.
- The kernel `all` scope (bounce, complaint, erasure) still stops them, and the
  delivery adapter's final consent check still applies.
- They do not run through `dotmac-campaigns`. Campaigns are for audience
  snapshots and marketing nurture.
- These are per-subscription and per-incident triggers, owned by the facts that
  cause them.
- Later journeys that are marketing (win-back, upgrade offers, referral asks)
  go through `dotmac-campaigns` under ADR-0056 and need their own decision.

### 2. Composition lives in the assembly; no new module, no sibling imports

Each journey is a small composer in the tenant assembly that adopts these
modules (ADR-0024).

- It reads typed facts from their owners.
- It schedules work through `dotmac-durable-timers`.
- It sends through the kernel delivery path.
- It records its own evidence.

No module imports a sibling to make a journey work. Sub hosts no journey logic.

The first host is the tenant assembly that adopts `dotmac-billing` for Sub at
the billing cutover. Sub itself hosts no composer.

### 2a. Channels are an ordered list chosen from installed channels

- Each journey category resolves its channels through the kernel
  `channel_policy` setting. Example:
  `{"categories": {"service_renewal": ["whatsapp", "sms", "email"]}}`.
- **Only installed channels can be chosen.** The UI offers only channels whose
  delivery provider the assembly has registered. The setting's write-time
  validator refuses any channel that is not installed. This needs one kernel
  change: the validator is given the installed set.
- **Order is fallback priority, not fan-out.** A notice is sent on the first
  channel in the list that is all of the following:
  - installed;
  - allowed by consent for the category;
  - backed by an address for this subscriber;
  - usable for this message. For example, WhatsApp outside a session is usable
    only with an approved template for the category.
- A channel that is not usable is skipped, and the skip reason is recorded as
  evidence. If no channel is usable, the outcome is recorded as *not delivered*.
  It is never treated as success.
- Fan-out to every listed channel is a later, explicit per-category option.
  It is not the default.
- The suggested default is `whatsapp → sms → email` for both journeys. Tenants
  change it in the UI.

### 3. Journey 1: renewal and expiry reminders

**Scheduling**
- For each subscription period that ends, the composer arms one timer per
  reminder step.
- The steps are tenant parameters entered at ship time; the default is
  T-3 days, T-1 day and T0. A default T+1 step follows the period end.
- The T+1 notice says "service paused" only if `dotmac-network-access`
  observes that service has actually stopped. Otherwise the state is UNKNOWN
  (ADR-0032), and the notice uses neutral wording such as "your plan has
  expired".
- Each step has a natural identity: (subscription, period, step).
- When a renewal, payment, plan change, suspension or cancellation supersedes
  the period, a new timer generation replaces the old one. Stale steps are
  rejected when they fire, not sent.

**At-most-once.** One delivery intent per natural identity (ADR-0014).
Re-arming, replay or a duplicate trigger never sends a second reminder for the
same step.

**Reread when the timer fires.** Before sending, the composer rereads:
- `dotmac-subscriptions`: is this period still the current one, and still
  ending?
- `dotmac-billing`: what is the current payable position of the obligation?

If the subscription is no longer expiring, nothing is sent. If billing's
position is unobserved, it is UNKNOWN (ADR-0032), so the message omits the
amount instead of guessing one. An amount shown in a message is for display
only and is never authority for payment.

**Send windows.** Reminders respect quiet hours in the tenant's timezone. A step
that falls inside quiet hours is deferred to the window's opening, not dropped.

**Rendering.** Templates come from `dotmac-template-studio`, with a closed,
typed placeholder set:
- name,
- plan,
- period end,
- optional display amount,
- pay link.

### 4. The pay link carries only an opaque signed obligation identity

This is the public edge of the single proven money path, not a separate payment
feature.

**The link**
- It carries a signed, opaque reference to the Starter billing obligation.
- It carries no amount, invoice status, provider or metadata that could serve
  as financial identity.

**When the link is opened**
1. The link-edge route verifies the token and applies abuse controls: expiry,
   replay limits and rate limits. That is all Sub does when it hosts this edge.
   Sub reasons about no money.
2. `dotmac-billing` returns the current payable position. The amount is resolved
   now, never taken from the message, a PDF or the link.
3. `dotmac-payments` opens a payment intent targeted structurally at that Starter
   billing document, then starts the provider checkout (for example Paystack)
   through the `dotmac-integration` connector.
4. The route returns `303` to the provider's authorization URL.

**Pay-then-record**
- The browser return records nothing. It only polls a Starter read model to show
  status.
- Money moves only when authenticated provider evidence arrives through
  `dotmac-integration` ingress.
- `dotmac-payments` confirms and qualifies that evidence. `dotmac-billing`
  accepts and allocates it (ADR-0016).

**Deleted from Sub:** intent creation, provider routing, reference minting,
settlement and payability decisions.

### 5. Journey 3: proactive outage and maintenance notices

**Trigger**
- `dotmac-network-assurance` owns the incident, its impact classification and its
  notification evidence.
- The composer acts only on confirmed customer impact, never on a raw alarm.
- An incident must stay impacting for a minimum duration before the first notice
  (flap damping). That duration is a tenant parameter.

**Who is notified**
- `dotmac-network-topology` resolves the affected paths into opaque subject
  references.
- The assembly maps those to subscriptions with active service, and to contact
  destinations.
- If impact cannot be resolved to specific subscribers, the result is UNKNOWN
  (ADR-0032). The composer does **not** fall back to messaging the whole tenant.
- Broadcasting to a whole site is a separate, explicit operator action, with its
  own evidence.

**What is sent.** Each subscriber gets at most:
- one notice when an incident starts;
- a bounded, coalesced number of updates (a tenant parameter);
- one restoration notice.

Each has a natural identity of (incident, subscriber, phase).

**Maintenance windows.** These send one advance notice at a lead time set by the
tenant. If the window is cancelled, the pending notices are superseded through
the timer generation.

**Quiet hours.**
- Unplanned-incident notices may send during quiet hours only when the incident
  affects a whole site or OLT, or its severity is at or above a tenant threshold.
- Everything else waits for the window to open.
- If an incident starts and is restored entirely within quiet hours, there is no
  overnight start or restore notice. Instead, subscribers get one summary notice
  when the window opens, and only if the incident lasted longer than the
  flap-damping duration.

**Notification evidence.** Network-assurance's own records show who was told,
what they were told and when. Support can then answer "were we told?" without
looking at delivery logs.

### 6. Parameters are typed tenant configuration

- Reminder offsets, quiet hours, flap-damping duration, update caps, severity
  threshold, maintenance lead time and link expiry are all typed, validated
  tenant parameters.
- Operators enter them in the UI at ship time, not in hand-authored environment
  files.
- No parameter can turn a transactional category into a marketing one, or the
  reverse.

### 7. Measured outcomes

Each journey ships with the measurement that justifies it:
- **Renewal:** renewal rate on time and within 7 days, compared with the
  baseline before launch.
- **Outage:** inbound tickets and calls per affected subscriber per incident,
  and the time from confirmed impact to first notice.

These are rebuildable projections over the composer's evidence and the owners'
facts. They hold no new authority.

## Consequences

- Both journeys can ship without new modules. The work is two assembly composers,
  their evidence, templates and tests.
- `dotmac-network-assurance` gains its first adopter.
- The renewal journey depends on the Starter billing and payments money path
  being live for the tenant. Until it is, journey 1 can send reminders without
  a pay link. It never falls back to Sub's legacy link.
- Sub loses its payability and intent logic for links (§4). This is consistent
  with the billing freeze and ADR 0020; it adds no billing code to Sub.
- Marketing journeys (win-back, upgrade, referral, NPS follow-up) are explicitly
  out of scope. They need a campaigns-based ADR.

## Rejected alternatives

- **Run both journeys as `dotmac-campaigns` campaigns.** This would put service
  messages under marketing consent and audience snapshots, so an unsubscribe
  could stop a renewal or outage notice. It would also add a scheduler model that
  campaigns deliberately does not own.
- **Keep the pay link in Sub and call Starter for settlement only.** That leaves
  payability, amount and routing in Sub, which is the current defect.
- **Notify the whole tenant when impact is uncertain.** This turns unknown impact
  into noise and erodes trust in real notices.
- **A dedicated `dotmac-customer-journeys` module.** It isn't needed yet. It
  would have to import or mirror its siblings' state, contrary to ADR-0024.
  Reconsider it once three or more journeys share real mechanics.

## Sequencing

Both journeys depend on modules that no product has adopted yet. This ADR does
not move them ahead of Gate 0 or the billing cutover.

1. Ship renewal reminders without a pay link.
2. Add the pay link (§4) once the Starter billing and payments path is live for
   the tenant.
3. Ship outage notices after `dotmac-network-assurance` and
   `dotmac-network-topology` have an adopter.

## Decisions folded in (2026-10-16)

These were open questions in the first draft. They are now answered:

- **Host.** The assembly that adopts `dotmac-billing` for Sub (§2).
- **Channels.** A selectable ordered list, limited to installed channels, used
  first-reachable (§2a).
- **T+1 notice.** Included, with wording that depends on what the network
  actually observes (§3).
- **Quiet hours.** Site/OLT-level or threshold severity only; overnight
  incidents fold into a single morning summary (§5).
