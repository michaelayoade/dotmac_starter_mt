# External-connector sources — what the Integrator has to absorb

**As of:** 2026-08-13
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

| Layer | Owns |
|---|---|
| Product | Provider-neutral APIs, business decisions, local records |
| Integrator core | Installations, bindings, secrets, inbox/outbox, retries, checkpoints, audit and repair |
| Connector plugin | Provider authentication, wire translation and I/O only |
| HTTP client library | Transport policy only; no registry or orchestration |

Plugins are discovered through package metadata, target a versioned SPI, declare
typed configuration and capabilities, and fail closed on incompatible or
duplicate bindings. **The Integrator core contains no provider enum and no
conditional tree** — the ADR-0008 rule that governs every other Dotmac
vocabulary, applied here.

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
  in the Integrator core. The mechanism (installations, bindings, checkpoints,
  retries) ports; the catalogue becomes plugin package metadata. A port that
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
| `sync_checkpoint` | declares a class named `*Checkpoint`/`*Cursor`/`*SyncState`, or a `last_synced_at`-family column | a cursor stored in a settings row or a JSON blob |
| `delivery_retry` | mentions dead-letter/backoff/requeue **and** also carries a connector surface | retry policy centralised in a shared helper with no connector import |

Known imprecision, accepted for the freeze: ERP's `dependency_health.py` and
`monitoring.py` are counted as `http_client` because they do make direct
outbound calls, though a health probe is arguably not a provider connector.
They are left in rather than special-cased — an exclusion list is where a
ratchet starts lying, and the number only has to be consistent to be useful.

Tests, migrations and scripts are excluded everywhere. A test that fakes a
provider is how a connector is verified, not a connector; a connector in a
migration is history.

## The ratchet

Two-directional. Rising fails ("a new direct connector surface landed"); falling
**also** fails unless the baseline is lowered in the same change
(`python scripts/external_connector_sweep.py --write-baseline`), so a retirement
is reviewable as a diff and a detector that quietly stops matching cannot pass
as progress.

It **abstains** when the fleet is not checked out beside Starter. Scoring a
repository it cannot see as zero would report the duplication as solved.

## Sequence this belongs to

1. **This document** — inventory and ratchets. *Done for the six categories
   above.*
2. Create the independent `dotmac_integrator` repository.
3. Extract the mature generic control-plane behaviour and parity tests
   product-first from Sub — **without** its `ConnectorType` enum.
4. Implement the SPI, package discovery and a shared fake-connector conformance
   kit.
5. Shadow the first genuinely required, non-retiring external capability through
   the Integrator.
6. Delete each product connector after verified cutover, lowering this baseline
   in the same change. Do not recreate ERPNext or CRM plugins if those systems
   are being retired.
