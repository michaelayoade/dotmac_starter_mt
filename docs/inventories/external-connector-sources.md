# External-connector sources — what the Integrator has to absorb

**As of:** 2026-08-15 (counts 2026-08-14; coverage and execution record
2026-08-15)
**Measured by:** [`scripts/external_connector_sweep.py`](../../scripts/external_connector_sweep.py)
**Frozen baseline:** [`external-connector-baseline.json`](external-connector-baseline.json)
**Ratchet:** `tests/architecture/test_external_connector_ratchet.py`
**Decision:** ADR-0024 § 6 (the Integrator is the sole external connector
control plane); AGENTS.md rule 28

Read under the same two cautions as every file in this directory
([README](README.md)): facts go stale, and **a row here is not permission to
extract anything**. This is step 1 of the Integrator sequence — make the surface
countable and stop it growing — not an extraction plan.

## The layering this measures the distance to

| Artifact | Owns | Location |
|---|---|---|
| Product | Provider-neutral APIs, business decisions, local records | Each independent product assembly |
| Reusable engine | Registry, installations, bindings, secret references, inbox/outbox, retries, checkpoints, audit and repair | Starter's stateful `dotmac-integration` module, with its own `mod_*` schema and lineage |
| Deployment | Pins kernel, `dotmac-integration` and connector packages; runs the engine | Thin `dotmac_integrator` assembly repository and independent runtime |
| Connector plugin | Provider authentication, wire translation and I/O only | Independently released plugin distribution |
| HTTP client library | Transport policy only; no registry or orchestration | `dotmac-integration-client` distribution |

Plugins are discovered through package metadata, target a versioned SPI, declare
typed configuration and capabilities, and fail closed on incompatible or
duplicate bindings. **The `dotmac-integration` module contains no provider enum
and no conditional tree** — the ADR-0008 rule that governs every other Dotmac
vocabulary, applied here. Independent deployment describes the thin assembly's
runtime boundary; it does not move reusable engine code outside Starter.

## The measurement

Six categories, one per responsibility ADR-0024 § 6 moves out of a product
runtime. Counts are **files**, not call sites: a file is the unit a retirement
actually deletes.

| Repo | http_client | webhook_surface | provider_credential | connector_task | sync_checkpoint | delivery_retry |
|---|---:|---:|---:|---:|---:|---:|
| `dotmac_academy_app` | 3 | 0 | 2 | 0 | 0 | 0 |
| `dotmac_crm` | 33 | 7 | 5 | 10 | 4 | 9 |
| `dotmac_erp` | 21 | 11 | 3 | 12 | 7 | 6 |
| `dotmac_sub` | 37 | 4 | 2 | 18 | 8 | 8 |
| `dotmac_vendor_control_plane` | 0 | 0 | 0 | 0 | 0 | 0 |
| **Total** | **94** | **22** | **12** | **40** | **19** | **23** |

### What the numbers say

- **Sub is the extraction source, and the measurement agrees.** It leads on
  `http_client` (37) and `connector_task` (18) and holds the only real
  `IntegrationCheckpoint`/`ConnectorConfig` control-plane models. Step 3 of the
  sequence — extract product-first from Sub — is pointed at the right product.
- **Sub's `ConnectorType` enum is exactly what must NOT be ported.**
  `app/models/connector.py` declares `webhook|http|email|whatsapp|smtp|stripe|
  twilio|facebook|instagram|custom`. That is the provider enum ADR-0024 forbids
  in the `dotmac-integration` module. The mechanism (installations, bindings,
  checkpoints, retries) ports; the catalogue becomes plugin package metadata. A port that
  brings the enum has rebuilt the thing the ADR rejects.
- **The vendor control plane is already at the target shape** — zero in every
  category. It is the proof the layering is reachable, and the ratchet asserts
  it stays there.
- **CRM's 33 `http_client` files are mostly retirement, not migration.** CRM is
  being decommissioned; per Michael's sequence, *do not recreate ERPNext or CRM
  plugins if those systems are being retired.* Those counts should fall to zero
  by deletion, not by porting.
- **Academy is small and late.** 3 clients, 2 credential holders, no webhooks —
  it can adopt the Integrator after the first real cutover rather than during.

## What each detector sees — and does not

Stated because a ratchet whose rules are unwritten becomes a number nobody can
act on, and because ADR-0018 requires the detector to carry its own sensitivity
proof (they are in the test file).

| Category | Counts a file that… | Deliberately does NOT see |
|---|---|---|
| `http_client` | imports `httpx`/`requests`/`aiohttp`/`urllib3` **and** calls a request method | a client injected as a typed parameter; a client behind a product wrapper already |
| `webhook_surface` | declares a route whose path contains `webhook`/`callback`/`/hooks`/`ipn`, or a function named `verify_signature`-ish | a provider callback mounted at a domain-shaped path |
| `provider_credential` | assigns a name containing a **named provider** and ending in a secret suffix | a provider secret held under a generic name (`api_key`), which is indistinguishable from the product's own |
| `connector_task` | has a decorated task function whose name mentions sync/connector/integration/poll/fetch, in a module mentioning a scheduler | a connector run triggered inline from a request path |
| `sync_checkpoint` | declares a class named `*Checkpoint`/`*SyncState`, or a `*Cursor` that **also names its feed** (`*SyncCursor`, `ErpDomainSyncCursor`, `StripeCursor`), or a `last_synced_at`-family column | a cursor stored in a settings row or a JSON blob; a bare `*Cursor` with no feed in its name and no watermark column |
| `delivery_retry` | mentions dead-letter/backoff/requeue **and** also carries a connector surface | retry policy centralised in a shared helper with no connector import |

**Why `*Cursor` alone is not enough (2026-08-14).** The first live rise this
ratchet reported was a false one: `dotmac_sub.sync_checkpoint: 9 > baseline 8`,
caused by `InboxTeamRoundRobinCursor` (`app/models/team_inbox.py`, landed with
conversational AI intake) — durable per-team ROTATION state for assigning inbox
conversations to staff (`service_team_id`, `last_assigned_person_id`,
`rotation_count`). No feed, no watermark, no external system; it matched only
because "cursor" is a substring. "Checkpoint" and "sync state" name durable
progress over a stream, but "cursor" is also the ordinary word for a pagination
cursor and a DBAPI cursor, so a bare `*Cursor` must now also name the feed it
tracks. Recall is unaffected for anything that stores a position: the watermark
COLUMN rule is untouched and independent of the class name. **The baseline was
NOT raised** — the correct response to a miscount is a better detector, and
raising it would have converted a detector bug into a permanently weakened
guard. Sub measures 8 again, and no other repo's number moved.

Known imprecision, accepted for the freeze: ERP's `dependency_health.py` and
`monitoring.py` are counted as `http_client` because they do make direct
outbound calls, though a health probe is arguably not a provider connector.
They are left in rather than special-cased — an exclusion list is where a
ratchet starts lying, and the number only has to be consistent to be useful.

ERP's `EventHandlerCheckpoint` (`app/models/finance/platform/`) is the other
borderline `sync_checkpoint`: it is per-handler idempotency over ERP's own
`platform.event_outbox`, not a position in an external feed. It stays counted,
deliberately — the outbox exists "for reliable event delivery" and carries
dead-lettering, so it sits inside the delivery machinery ADR-0024 § 6 moves to
the Integrator. If a later review decides otherwise, that is a re-baseline of
ERP with the reason in the diff, not a quiet change to the detector.

Tests, migrations and scripts are excluded everywhere. A test that fakes a
provider is how a connector is verified, not a connector; a connector in a
migration is history.

## Coverage — what the measurement does not reach (2026-08-15)

A bounded measurement that does not state its bounds reads as "covered
everything": the strongest possible claim made by the weakest possible evidence.
The sweep therefore prints a `COVERAGE` block on **every** run — not behind a
flag, because a disclosure nobody turned on is not a disclosure. Four bounds
exist and all four are now said out loud rather than left to be inferred from
the source.

**1. Only one subtree per repository is read.** As measured on 2026-08-15:

| Repo | Subtree | Files measured | Runtime `.py` files elsewhere in the repo |
|---|---|---:|---:|
| `dotmac_academy_app` | `app/` | 152 | 4 |
| `dotmac_crm` | `app/` | 784 | 73 |
| `dotmac_erp` | `app/` | 1452 | 185 |
| `dotmac_sub` | `app/` | 1931 | 282 |
| `dotmac_vendor_control_plane` | `src/vendor_cp/` | 59 | 1 |

The right-hand column is the size of the bound, and it is stated as a number so
a reviewer can weigh it rather than guess at it. ERP's figure is 185 and not the
3,432 a naive walk reports: `dotmac_erp/worktrees/` holds two nested git
checkouts whose thousands of files are COPIES of code measured elsewhere. They
are pruned on a premise anyone can check — the directory contains a `.git`
entry — and the pruning is itself printed, because a silent correction to a
coverage number is just another silent cap.

**2. A file that cannot be read is not a clean file.** An unreadable or
unparseable file used to return an empty classification, which is
indistinguishable from "no connector here" and silently lowers a count. Worse,
the fall would then surface through the ratchet's *falling* direction as "lower
the baseline", inviting a reviewer to ratify an undercount as a retirement.
Such files are now named, and they make `--check` refuse. Zero today.

**3. An absent enumerated repository is UNMEASURED, never zero** — unchanged,
and the ratchet abstains.

**4. Five repositories are measured; the fleet has more.** Every other Dotmac
Python distribution is now listed in the sweep's `OUT_OF_SCOPE` with a premise a
reader can check against that repository, per ADR-0018 — an exemption states an
enforceable premise or the region is unmonitored rather than exempt, and
"grandfathered" is refused by the guard as a description of history rather than
a premise. The load-bearing distinction is **destination versus source**:
`dotmac_integrator` and `dotmac-integration-client` are where connector surface
is supposed to ARRIVE, so counting them would make every successful migration
read as a regression. A distribution in neither list is reported as
UNCLASSIFIED; `--strict-coverage` turns that into a refusal, which is how CI
runs it, while a workstation carrying a second clone of an already-measured repo
is not a governance defect.

### Execution record

| Date | Fleet root | Result |
|---|---|---|
| 2026-08-15 | `/Users/michaelayoade/Downloads/management` | **PASS** — every count identical to the frozen baseline; zero unmeasurable files; 4 unclassified entries, all local clones of already-measured repositories (`crm-wt`, `crm-verify`, `erp-wt`, `academy-metrics-wt`) |

The baseline is therefore **unchanged by this execution**, which is the outcome
worth recording: a ratchet run that produces no diff is evidence the frozen
numbers still describe the fleet, and a ratchet nobody has run since the day it
was written is a file, not a guard.

## The ratchet

Two-directional. Rising fails ("a new direct connector surface landed"); falling
**also** fails unless the baseline is lowered in the same change
(`python scripts/external_connector_sweep.py --write-baseline`), so a retirement
is reviewable as a diff and a detector that quietly stops matching cannot pass
as progress.

It **abstains** when the fleet is not checked out beside Starter. Scoring a
repository it cannot see as zero would report the duplication as solved. It also
abstains when a file inside a measured subtree could not be read — the counts
are then an undercount of unknown size, and failing on the cause beats letting
it surface as a mislabelled symptom in the falling direction.

Baselines freeze **counts only**. Coverage is a property of the run rather than
of the fleet: file totals move with every unrelated commit in a sibling
repository, so freezing them would demand a re-baseline for changes this ratchet
does not govern and would bury the disclosure in diff noise — which is how
disclosures stop being read.

## Sequence this belongs to

1. **This document** — inventory and ratchets. *Done for the six categories
   above.*
2. Extract the mature generic control-plane behaviour and parity tests
   product-first from Sub into Starter's stateful `dotmac-integration` module —
   **without** its `ConnectorType` enum.
3. Implement the module's SPI, package discovery and shared fake-connector
   conformance kit.
4. Create the thin `dotmac_integrator` assembly repository. It pins kernel, the
   exact module release and connector distributions; it does not own a second
   engine implementation.
5. Shadow the first genuinely required, non-retiring external capability through
   that deployment.
6. Delete each product connector after verified cutover, lowering this baseline
   in the same change. Do not recreate ERPNext or CRM plugins if those systems
   are being retired.
