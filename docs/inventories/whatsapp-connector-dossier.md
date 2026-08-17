# WhatsApp/Meta ingress connector — extraction dossier

Created 2026-08-15; implementation authorized and materialized 2026-08-17.
The machine-checked dossier now lives at
`packages/dotmac-connector-whatsapp/EXTRACTION.toml`. This document preserves
the extraction judgement and parity disposition; it is not a second contract.

The governing evidence is:

- `docs/inventories/whatsapp-connector-sources.md` — fleet inventory and source
  provenance;
- `docs/superpowers/specs/2026-08-15-meta-whatsapp-ingress-conformance.md` —
  WAI-1..37 requirements;
- `tests/fixtures/meta_whatsapp/` — exact-byte preimplementation corpus;
- `tests/unit/test_meta_whatsapp_ingress_conformance.py` — corpus integrity;
- `tests/unit/test_whatsapp_connector.py` — implementation against that corpus.

## Authorization and release state

| Gate | State |
|---|---|
| exact coordinates named | done: `dotmac-connector-whatsapp`, `dotmac_connector_whatsapp`, `meta_whatsapp`, `messaging.receive.v1` |
| executable ingress SPI | done: SPI 1.2, released in `dotmac-integration 0.1.0a5` |
| implementation authorization | done: Michael directed the first Meta/WhatsApp connector and later directed completion without further decision prompts |
| secret materialization owner | done: `dotmac-integration` owns secret-reference resolution; the connector receives material and never dereferences a store |
| release eligibility | in this slice: the package, allowlist entry, wheel policy and installed conformance proof land together |
| provider operation / adoption | not claimed: requires an exact Integrator pin and Sub shadow/cutover |

ADR-0030's original §6 prohibition is retained as history. Its dated amendment
records the later authorization; the old paragraph is not silently rewritten.

## The extracted unit

The connector owns only the provider protocol edge:

- subscription challenge comparison and raw echo;
- HMAC-SHA256 verification over the exact received bytes;
- active-secret rotation checks, returning only matched positions;
- traversal of one provider batch into 0..N facts;
- stable raw provider identities, or item-derived identities when none exist;
- typed message, media, location, status, error and malformed observations;
- the provider acknowledgement body and media type.

It owns no database, sessions, installation state, retry, dead letter,
checkpoint, HTTP status, provider schedule, product identity, subscriber,
conversation, ticket, entitlement, or delivery consequence. Those boundaries
are checked in `test_whatsapp_connector_boundary.py` and in the connector
release gate.

## Product-first source disposition

Sub is the qualifying source. Its production-shaped WhatsApp runtime already
contained the provider signature scheme, challenge behaviour, payload shapes
and tests. The connector ports that protocol knowledge and the persistence-free
normalization intent.

The following do not port:

- send-side payload builders, Graph API calls, media upload and template I/O —
  this first connector is ingress-only;
- subscriber matching, contact resolution, conversation threading and official
  timeline updates — Sub remains the business owner;
- Sub's local retry/checkpoint/install/config mechanisms —
  `dotmac-integration` owns their reusable equivalents;
- request-digest identity — it deduplicates only an identical batch, so the
  connector uses each raw provider id and a status composite instead;
- presentation placeholders such as `[image]` or `[location]` — typed
  attachments cross the product boundary and the product decides rendering.

CRM is not a consumer or a candidate. Its old direct connector path is being
retired, not rebuilt as a plugin.

## Identity and evidence boundary

The connector emits raw provider identities because the receiving product port
owns its own namespace. Prefixing here and again in Sub would manufacture a
second identity. The rules are:

- message: the provider's raw `wamid`;
- status: `{message-id}:{status}:{provider-timestamp}`;
- provider error: `error:{account-scope}:{sha256(canonical-item)[:32]}`.

Every event also carries `transport_evidence`: an RFC 6901 locator,
`identity_source`, and stable reason code where applicable. This evidence is
for Integrator receipts and diagnostics. The provider-neutral ProductPort
selects the declared product envelope; transport evidence does not become Sub
domain state.

## Cutover and retirement gate

Publication is only a supply-chain fact. The first cutover is complete only
when all of the following hold:

1. `dotmac_integrator` exact-pins `dotmac-integration 0.1.0a5` and the released
   connector, discovers it at boot, and exposes the provider-neutral routes.
2. The assembly records matched-secret-position evidence without learning a
   provider scheme and delivers location/media through Sub's typed port.
3. A shadow mirror forwards the exact raw body and signature header while Sub's
   current callback remains authoritative.
4. Observation ids, count, types, content and refusals agree for the configured
   provider retry horizon. Consequences the old source cannot express are not
   falsely compared.
5. The callback moves to Integrator, Sub's direct verification/normalization
   surface is deleted, and its external-connector ratchet is lowered in the
   same change.

Only step 5 moves the package dossier from `audit-complete` to `adopted`.
