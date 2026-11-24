# Dated holiday campaigns: Starter port

Date: 2026-11-23. This is source preparation for review, not a product cutover.

## Qualifying behavior and destination

The requester redirected the holiday-message proposal from Dotmac Sub to
`dotmac_starter_mt`. The local Sub proposal on
`codex/holiday-message-scheduling` is uncommitted experimental work, not a
production adoption or immutable release coordinate. Its useful canaries cover
explicit local dates, previews, consent rechecks, idempotency and next-day
expiry. It must not be copied into Starter as a SystemJob outbox, Subscriber
table or Celery scheduler.

The existing audit in `campaigns-sources.md`, ADR-0056 and the campaigns dossier
assign outbound progression to `dotmac-campaigns`. Its existing typed
commands already create/revise drafts, ingest a frozen product audience,
schedule work through Durable Timers, pause/resume/cancel, publish through the
kernel outbox, query delivery counters and recheck consent before transport.
The missing reusable behavior is an explicit dated occurrence and a delivery
deadline independent of PII/evidence retention.

## Published source and version boundary

Campaigns a1 was published from `ae5369c0`, release run `32484270372`, as its
CHANGELOG and immutable tag record. Older audit text saying publication is
closed is historical and must not override that evidence. This change uses
`0.1.0a1+dev` for source preparation, following the existing brand-profiles
precedent; it allocates no bare successor and authorizes no publication.
The a1 migration and released manifest digest remain immutable.

## Ownership and adoption

- A typed dated occurrence converts an explicitly selected date, local time
  and IANA zone into a one-time campaign. There is no holiday-calendar lookup,
  yearly recurrence or country-specific default in the shared package.
- Campaign revisions own an optional immutable delivery deadline. Existing
  campaigns have NULL and preserve their old behavior and request fingerprint.
- Campaigns suppresses late due work without rendering or publishing, and its
  final delivery gate refuses queued work at or after the deadline.
- Durable Timers still owns trigger acceptance/generation and the kernel still
  owns consent, idempotency and outbox. Deadline evidence does not assert that
  the customer unsubscribed, and it does not erase prior delivered outcomes.
- Products own authentication, staff audit identity, customer discovery,
  eligibility/opt-in policy, preview/confirmation UX, template/sender adapters,
  provider delivery and the requirement to call the final gate immediately
  before transport.

This PR prepares a Starter package capability. The reference app does not
compose campaigns, and this is not a working Selfcare Holiday Messages page.
Selfcare adoption needs its existing ADR-0056 prerequisites and a separate
product change. The local Sub experiment stays unpublished; it is not a
parallel production writer or an approved fallback.
The shared module retains its current confirmed-snapshot immutability: edit
drafts, or cancel and explicitly confirm a replacement for a future scheduled
greeting. The product UI must not silently mutate a confirmed snapshot.

## Migration and verification plan

`ca_0002_delivery_deadline` expands `campaign_revisions` with a nullable
timestamp. Existing rows stay unbounded (NULL); no deadline is invented and no
data is deleted. Existing revision immutability and forced RLS apply to the
new column. Downgrade is refused because removing an admitted deadline would
silently widen delivery permission.

The same unpublished migration replaces the shared snapshot trigger function
to isolate recipient-only fields inside the recipient UPDATE branch. This
repairs the PostgreSQL undefined-column failure when frozen revisions, steps
or audiences are changed. All frozen snapshots still reject updates/deletes;
only the existing recipient privacy-scrub transition remains permitted.
The published ca_0001 migration bytes are unchanged. The predecessor upgrade
test checks both NULL preservation and the repaired immutability guard.

Canaries cover local-day UTC boundaries, DST ambiguity/gaps, malformed or
inconsistent deadlines, legacy fingerprint compatibility, late due work,
expiry between publication and transport, consent before expiry, terminal
outcome preservation, stale triggers and tenant isolation. PostgreSQL checks
must use the real ca_0001-to-ca_0002 migration and existing module RLS canaries.
Per AGENTS.md, execution belongs to Git-hosted CI; no local test runs or
test/development dependency installation are authorized by this port.
