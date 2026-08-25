# ADR-0033: Digital Media owns reusable assets, rights and renditions

- **Status:** Accepted
- **Date:** 2026-08-19
- **Decision owner:** Michael
- **Evidence:** `docs/inventories/digital-media-sources.md`

## Context

Products currently store avatars, attachments, campaign creatives, field
evidence and generated media in domain-specific forms. `dotmac-files` correctly
owns opaque bytes, but byte ownership cannot answer which reusable asset a file
revision belongs to, whether its rights permit a use, or which derived rendition
is current. The existing Media Observations candidate is about external
advertising facts, and Content owns editorial context rather than reusable media.

Without a named DAM owner, every product would independently create revision,
rights and transformation policy beside Files. Giving those decisions to Files
would instead make the physical object store interpret business meaning.

## Decision

Create the tenant-only optional `dotmac-digital-media` module.

Digital Media is the sole writer of reusable media asset identity, immutable
source-revision lineage, canonical current revision, descriptive metadata,
technical metadata observations, library/collection/classification state,
rights versions and usage evaluation, rendition provenance/lifecycle,
media-specific grants and annotations, usage-reference observations and its
append-only evidence.

Revisions bind an opaque `dotmac-files` UUID, checksum, media type and length.
They never own or rewrite bytes. A replacement creates a new immutable revision
and moves only the asset's current pointer. Technical metadata is an observation
about the exact checksum; it cannot replace the source.

Renditions bind one exact source revision and checksum plus a versioned recipe
and engine. Outputs are opaque file UUID/checksum pairs. They are repairable
projections and never implicitly become source revisions.

Rights definitions are immutable versions. Evaluation uses explicit time,
territory, channel, purpose, commercial/modification intent and the exact
legal/privacy evidence reference supplied by its owner. Digital Media enforces
that evidence but does not decide consent or release validity.

The lifecycle is `ingesting`, `quarantined`, `available`, `restricted`,
`expired`, `withdrawn`, `archived`. Files/security scanning supplies a typed
safety observation; Digital Media decides usability. Exact-content approval is
observed from `dotmac-approvals`, checksum-bound, and never publishes or changes
the asset lifecycle automatically.

Rights expiry/review and other scheduled wake-ups are emitted as typed intent
and accepted only against the exact current rights version. Durable Timers owns
generation and wake-up mechanics; Digital Media has no clock scan.

Consumers own relationship meaning:

- Documents owns embed position and purpose;
- Content owns role, order, caption and contextual alt text;
- Publishing owns release target and outcome; and
- domain owners retain campaign, product, event and business-subject meaning.

They synchronize opaque exact revision/rendition references. Digital Media
records deduplicated usage observations so it can answer who uses a revision;
those observations do not transfer relationship authority.

An exact media revision becomes a managed record only through explicit
declaration. From declaration onward, Records is sole writer of retention,
legal hold, preservation and disposition for that revision. Digital Media keeps
asset and rendition authority and must consult the supplied Records decision
before requesting physical deletion. Files performs deletion only for the
authorized owner.

## Persistence and composition

The first lineage owns:

- `media_libraries`
- `media_assets`
- `media_revisions`
- `media_metadata_observations`
- `media_collections`
- `media_collection_items`
- `media_classification_assignments`
- `media_relationships`
- `media_rights_versions`
- `media_renditions`
- `media_access_grants`
- `media_annotations`
- `media_saved_selections`
- `media_usage_observations`
- `media_events`

All tables are tenant scoped with same-revision forced RLS and tenant-composite
module foreign keys. Immutable evidence tables are protected by database
triggers. The package imports the kernel only. It is built in Starter but not
composed until a named product adopter performs its authority cutover.

## Consequences

- Products gain one consistent exact-revision, rights and rendition contract.
- Files remains a physical byte owner and cannot become a hidden DAM.
- Content and Documents must migrate raw file references to exact media
  revisions without importing the DAM package or sharing its database.
- Search, thumbnails and transcodes remain rebuildable.
- A record declaration does not move reusable asset identity into Records.
- Provider-specific extraction/transformation stays outside the module.
- The first adopter must retire a previous reusable-asset writer; a package
  installation alone is not adoption.

## Rejected alternatives

### Put DAM behavior in `dotmac-files`

Rejected because bytes, validation and deletion are physical concerns. Rights,
collections and creative revision identity are domain meaning.

### Expand `dotmac-media-observations`

Rejected because provider-reported advertising hierarchy and performance facts
are observations about remote entities, not uploaded creative assets.

### Leave creative files inside Content

Rejected because the same revision can serve Documents, Content, Publications,
training and evidence. Content still owns contextual creative meaning.

### Let every domain manage rights and renditions

Rejected because it creates multiple rights and transformation authorities and
makes cross-domain usage/disposition unanswerable.

## Amendment discipline

This ADR is accepted on the isolated branch whose base publishes kernel a73.
If another pending branch consumes a74 or ADR-0033 first, this branch must be
rebased and renumbered before merge. The ownership decision survives; allocation
coordinates do not.
