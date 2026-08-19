# Publication lifecycle extraction dossier

**As of:** 2026-08-19
**Starter branch base:** `6c97175611ca31eb684ac1afcd6493f506eafdd7`
**Audited Mkt source:** `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d`

This is the product-first contract for `dotmac-publishing`, the second executable
slice of the decomposed marketing suite. The Mkt remote was rechecked directly
on 2026-08-19 and `refs/heads/main` still resolved to the revision above.

Gate 0 freezes the source behavior and its intentional corrections. Gate 1 is
recorded RED on Observer at exact commit
`a941c6975e5f49520c07608ba2c0fc4e7e6172a3`: runtime code does not exist yet.
Neither gate authorizes a namespace allocation, package release, composition,
provider connection, data migration, writer switch or adoption.

## 1. One owner and the facts it owns

`dotmac-publishing` owns a tenant's local decision to publish one immutable
snapshot, the selected opaque delivery targets, the desired delivery instant,
one retry-safe attempt history per target, normalized delivery observations and
the derived aggregate publication state.

It does not own the source content, a target installation, provider identity,
credentials, provider capability discovery, wire payloads, remote polling or
raw transport receipts. Those boundaries are deliberate:

- `dotmac-content` may supply an immutable value, but publishing imports no
  sibling package and accepts any contract-compatible snapshot from an
  assembly.
- `dotmac-integration` owns installations, bindings, inbox/outbox transport
  mechanics and normalized connector receipt delivery. Publishing stores only
  an opaque `target_ref` and its own local observation.
- Connector plugins own provider names, SDKs, endpoints, credentials, OAuth,
  wire mapping and remote capability checks.
- A timer implementation wakes a due publication through a typed port. The
  package does not import `dotmac-durable-timers` or run a poller.
- Backoffice owns actor authentication, authorization and target-selection
  policy. The package records an opaque actor reference and validates only its
  own lifecycle.

Publication and campaigns are different owners. `dotmac-campaigns` decides
audience and recipient progression; it may request a publication by opaque
snapshot/target references, but neither package imports the other.

## 2. Revision-1 contract

The module is tenant-plane only. Its proposed allocation is owner/short code/
branch `publishing`, prefix `pb`, schema `mod_publishing`, and kernel alpha a76
on the current local suite branch. The allocation lands only with the manifest
and root migration in Gate 2; this dossier does not reserve an empty namespace.

The four initial tables are:

| Table | Required shape |
|---|---|
| `publication_releases` | `tenant_id NOT NULL`; request key/fingerprint; opaque source and actor refs; requested delivery instant; immutable snapshot payload/version/digest; derived aggregate state; timestamps; unique `(tenant_id, id)` and request key |
| `publication_deliveries` | `tenant_id NOT NULL`; composite release FK; opaque target ref; optional variant key; current derived delivery state; unique per tenant/release/target |
| `publication_attempts` | `tenant_id NOT NULL`; composite delivery FK; monotonic attempt number; kernel outbox event ref; requested/completed instants; derived attempt state; unique per tenant/delivery/attempt |
| `publication_observations` | `tenant_id NOT NULL`; composite attempt FK; deduplication receipt ref; normalized outcome; optional opaque remote ref and bounded failure detail; observed/recorded instants; immutable after insert |

Every table is created with forced RLS, the tenant policy and exact app-role
grants in the same migration. Every same-module relationship includes
`tenant_id`. Source, actor, target, outbox, receipt and remote references are
opaque and have no foreign key to a sibling module, product or Integrator
table. The backing state columns remain strings rather than database enums.

The snapshot is copied into the release as canonical JSON plus a SHA-256 digest
before any timer or outbox intent is created. Editing or archiving the source
content cannot mutate a requested publication. The snapshot contains authored
title/body/variant and ordered creative references, never ORM objects,
callbacks, sessions, provider URLs or credentials.

## 3. Lifecycle and reconciliation

Publication aggregate states are `scheduled`, `dispatching`, `partial`,
`published`, `failed`, and `cancelled`. Delivery states are `pending`,
`intent_published`, `accepted`, `published`, `failed`, and `cancelled`.

- a newly accepted request starts scheduled with one pending delivery per
  distinct target;
- a due wake-up creates one attempt and one transactional outbox command per
  eligible delivery, then derives dispatching;
- duplicate request keys with the same fingerprint replay; a changed
  fingerprint conflicts;
- a failed delivery may be retried through a new monotonic attempt; old attempts
  and observations are never overwritten;
- published and cancelled delivery outcomes are terminal;
- all targets published derives published;
- a terminal mix with at least one published target derives partial;
- all terminal without a published target derives failed, except all-cancelled
  derives cancelled;
- a mix containing any non-terminal delivery remains dispatching; and
- observation replay is idempotent by receipt reference and conflicts when the
  same receipt claims different content.

The aggregate state has one canonical writer: the reconciler derives it from
delivery state after every accepted observation. An importer never assigns the
release aggregate directly. A remote success without a matching local
attempt/target is recorded as a refused observation, not used to invent local
authority.

Cancellation before dispatch cancels pending deliveries and the timer.
Withdrawing a remotely published target is a new typed intent and attempt; it
never hard-deletes the release, delivery, prior attempt or observation. Remote
edit likewise creates a new immutable release revision rather than mutating
the snapshot that explained an earlier side effect.

## 4. Source parity and intentional corrections

| Mkt source behavior | Revision-1 disposition | Proof |
|---|---|---|
| `Post.status=planned`, `scheduled_at`, and `publish_due_posts` ordered by due time | **Port behavior**, replacing the 60-second Celery poll with a typed exact-timer wake-up | scheduling and due-order canaries |
| one `PostDelivery` per selected Channel | **Port meaning** as one delivery per opaque target ref | per-release target uniqueness canary |
| one successful delivery marks the Post published while failed targets remain failed | **Port evidence, correct aggregate** to explicit terminal `partial` rather than hiding drift under published | aggregate lifecycle matrix |
| every delivery failure leaves the Post un-published and raises | **Port evidence**, retaining all attempts/observations even when the caller fails | all-failed reconciliation canary |
| failed delivery stores error text but later attempts overwrite the same row | **Correct** to immutable monotonic attempts plus bounded normalized observations | retry and replay canaries |
| provider adapter call occurs inside the database transaction | **Reject**; write a typed kernel outbox command in the local transaction | architecture and service canaries |
| mutable Post/Asset rows are read at dispatch time | **Correct**; freeze a digest-addressed snapshot before scheduling or dispatch | snapshot immutability/digest canary |
| Channel owns provider enum, encrypted credentials, account id and connection state | **Move** to Integrator installation/binding and connector plugins; publishing holds only opaque target refs | forbidden-surface canary |
| provider-specific content/media validation | **Split**: publishing checks a non-empty snapshot/target; connector capability validation and wire constraints stay with the plugin | contract and provider-import canaries |
| `publish_due_posts` catches one expected failure and continues | **Port** as independent target/release attempts; one failure cannot roll back another release | service parity canary |
| direct remote update mutates the original Post and Delivery | **Correct** to a new immutable release revision and outbox intent | revision lineage canary |
| direct remote delete hard-deletes the local Post | **Correct** to withdrawal intent with retained evidence | terminal/history canary |
| task constructs `SessionLocal`, commits and rolls back | **Reject**; kernel transaction boundaries own commit/rollback | architecture canary |
| web routes query, authorize, call providers, commit and roll back | **Replace** with thin Backoffice adapters over the owner | adapter retirement ratchet |

The qualifying code paths are Mkt `app/models/post.py`,
`app/models/post_delivery.py`, `app/models/channel.py`,
`app/services/post_service.py`, `app/services/publishing_service.py`,
`app/tasks/publish_scheduled.py` and the publication entry points in
`app/web/campaigns.py`. The selected behavior tests are the Post/PostDelivery
parts of `tests/test_marketing_models.py` and the complete
`TestPublishingService` block in `tests/test_marketing_services.py`.

## 5. Typed seams

`PublicationSnapshotV1` is a frozen ORM-free value. `RequestPublication`
contains an idempotency request key, aware desired instant, snapshot and a
non-empty tuple of unique opaque targets. `DispatchPublicationV1` contains the
release, delivery and attempt identifiers, opaque target, immutable snapshot
and stable command key. `DeliveryObservationV1` carries a deduplication receipt,
attempt identity, normalized outcome, aware observation time and optional
opaque remote reference/bounded error detail.

The package writes the kernel outbox directly because the kernel is the one
owner of transactional intent persistence. Provider transport does not appear
as a callback from the service. A product adapter receives normalized
observations through its own authenticated/deduplicated ingress and delegates
to this owner.

Scheduling uses a small protocol declared by publishing and implemented by the
assembly. The timer scope names the operation (`publishing.dispatch`), never an
HTTP route. Repeated scheduling replaces by generation; stale wakes cannot
dispatch.

## 6. Cutover gate

The exact source writers and retirement proofs are frozen in
[`publishing-writer-retirement.toml`](publishing-writer-retirement.toml).
Backoffice is cutover 1 and must consume a released package from its own
runtime/database. Installation alone is not adoption.

Every Mkt Post, PostDelivery and provider action enters a total classifier.
Editorial fields map through the content workstream; publication fields map to
one release/delivery/attempt history or an explicit rejected/archive
disposition. Shadowing compares snapshots, target selection, requested time,
attempt ordering, remote refs and aggregate state. A source writer reaches
retired only when it is deleted or structurally unreachable and the
two-directional ratchet is lowered in the same change.

Sub may later pin the same released contract and run its own lineage. No
Backoffice or Mkt database, row, ORM, session or filesystem becomes Sub's
source of truth.

## 7. Gate 1 RED evidence

On 2026-08-19, a fresh detached writable Observer checkout at exact commit
`a941c6975e5f49520c07608ba2c0fc4e7e6172a3`, with full history and all 84 tag
refs, installed from the committed lock using Poetry 2.4.1. The focused command
ran the marketing source audit, publishing architecture contract and publishing
lifecycle contract with repository addopts cleared only to expose the exact
summary.

The result was the intended RED: **32 failed and 15 passed**. Every failure was
either the explicit absent-distribution assertion or the controlled
`dotmac_publishing` missing-package failure. The 15 passing tests covered the
suite source/dossier/retirement evidence, the product-first package dossier and
the provider/sibling-import boundary that is meaningful before code exists.
No unrelated regression appeared.

At the same exact commit, Ruff lint passed and the pinned formatter reported all
three focused test files already formatted. This is controlled pre-
implementation evidence, not a failed candidate and not permission to release
or adopt. Gate 2 is now the next action.

## 8. Gate 2 implementation evidence

The executable Gate 2 candidate is frozen at exact local commit
`5a1892c3aac30b607cc28baa52217870e97bc63c`. A fresh detached writable
Observer checkout with all 84 tags installed the committed lock using Poetry
2.4.1. That revision contains `dotmac-publishing 0.1.0a1`, the independent
`pb_0001_publishing` lineage, and kernel a76's permanent
`publishing`/`pb`/`mod_publishing` allocation.

Validation was green at that exact code revision:

- 141 focused source-audit, architecture, lifecycle, service, namespace,
  kernel-floor, publication-ledger and generated-catalog tests passed;
- `make check` passed the exact Poetry lock, Ruff, all nine import contracts,
  mypy over 323 source files, Bandit, the composed migration gate, UI assets,
  generated module catalogue and formatting;
- the complete unit/architecture lane completed without failure across 3,855
  collected cases, retaining only repository-defined skips;
- the complete disposable-PostgreSQL integration lane completed without
  failure across 497 collected cases; and
- the publishing-only kernel-plus-module migration suite passed all four live
  PostgreSQL canaries: catalog agreement, forced RLS on all four tables,
  two-tenant read isolation, cross-tenant write refusal and unscoped fail-closed
  behavior. Its per-case databases and the outer Compose project/network were
  removed after the run.

The full sweeps materially tightened the candidate before this milestone:
persisted snapshots now fail closed on malformed stored fields, the namespace
ratchet explicitly enumerates the sixteenth owner, and target order is a stored,
uniquely constrained release fact rather than timestamp/UUID accident.

This closes implementation Gate 2 only. Publishing a1 and kernel a76 remain
unpublished; publishing is absent from the release allowlist and from this
reference assembly; Backoffice has not pinned, migrated, shadowed or retired a
writer; and every `PUB-R*` row remains `not-started`. Release and adoption need
separate authorization and their own proof.
