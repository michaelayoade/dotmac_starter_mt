# dotmac-campaigns

`dotmac-campaigns` owns provider-neutral outbound campaign progression for one
tenant: immutable revisions, audience/recipient snapshots, ordered one-time and
nurture steps, consent receipts, delivery intents, normalized observations,
unsubscribe/response evidence, rebuildable counters and repair.

It does not discover an audience. A product submits typed candidate facts and
keeps Party, customer, financial and cohort policy. It does not render a
template, select provider credentials, run a scheduler, deliver a message,
create a conversation or create a Lead. Assemblies bind Template Studio,
Durable Timers and provider-neutral sender ports; kernel consent,
idempotency and outbox remain the corresponding universal owners.

The complete source and retirement evidence is in
[`EXTRACTION.toml`](EXTRACTION.toml) and
[`docs/inventories/campaigns-sources.md`](../../docs/inventories/campaigns-sources.md).
ADR-0056 owns the boundary and the Sub-first/Backoffice-second sequence.

## Public shape

The top-level package exports commands, value contracts, lifecycle services,
read models, the manifest and lineage discovery. Product adapters should type
against the protocols in `dotmac_campaigns.contracts`; shipped fakes and
conformance checks live in `dotmac_campaigns.fakes`.

All mutators receive a caller-owned SQLAlchemy `Session`, mutate/flush and never
commit or roll back. The package is tenant-only in V1 and the independent `ca`
lineage creates every table in `mod_campaigns` with forced RLS.

## Dated greetings (source preparation)

`0.1.0a1+dev` is unpublished source preparation; the published a1 stays immutable.
`DatedCampaignOccurrence(local_date=..., local_time=..., timezone=...)`
represents one explicitly chosen local day. Its `as_campaign(...)` method
returns the existing typed `CreateCampaign` for one immediate step at that
local time, with delivery expiring at the next local midnight. The caller
supplies the channel, template, sender and retention deadlines. Invalid zones,
offset-bearing wall clocks and ambiguous/nonexistent local times are refused.
The shared module supplies no country default, public-holiday lookup or annual
recurrence.

`CreateCampaign` and `ReviseCampaign` also accept an optional
`delivery_expires_at`. NULL retains existing behavior and legacy idempotency
fingerprints. A bounded deadline must follow the scheduled time and fit PII
retention; it belongs to the immutable revision, not the evidence-retention
clock. Apply `ca_0002_delivery_deadline` before using this source version.
Existing rows remain NULL. The expansion is forward-only: dropping the column
would remove a previously admitted delivery restriction.

The existing lifecycle and ports remain the execution path. Late current due
work is receipted without rendering or publishing; work whose next send window
is beyond the deadline is also refused. Assemblies must call
`authorize_delivery` immediately before provider I/O, including retries, so
publication before midnight does not permit transport after midnight.
The gate checks transport admission; it cannot recall a message already
accepted by a provider.
Expiry records the campaigns policy owner and does not unsubscribe a customer,
shorten evidence retention or overwrite an earlier terminal delivery outcome.

Products still own customer selection/eligibility, authenticated staff audit,
preview and confirmation UI, transport configuration and delivery workers.
Drafts remain revisable. Confirmed snapshots keep the existing immutability
contract; changing a confirmed future greeting requires cancellation and an
explicitly reviewed replacement in the product workflow.
`campaign_snapshot` includes the schedule zone and deadline for their UI.
This package change does not install a Selfcare page, activate campaigns in
the reference app, or switch a product's current writer. The dated port
inventory and adoption gates are in
[`dated-holiday-campaigns.md`](../../docs/inventories/dated-holiday-campaigns.md).
