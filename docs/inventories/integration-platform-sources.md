# Integration-platform sources — what `dotmac-integration` extracts from Sub

**As of:** 2026-08-13
**Source:** `dotmac_sub` (`app/models/integration_platform.py`, `app/services/integrations/`)
**Decision:** ADR-0024 §§ 6–7 · **Dossier:** `packages/dotmac-integration/EXTRACTION.toml`
**Related measurement:** [`external-connector-sources.md`](external-connector-sources.md)

Read under this directory's standing cautions ([README](README.md)): facts go
stale, and **a row here is not permission to extract anything**. ADR-0006's
product-first rule still gates every move.

## The headline

Sub already has a **mature, generic connector control plane** — seven tables and
an engine that carries leasing, idempotency keys, payload digests, immutable
config revisions and optimistic-locked checkpoints. This is not a greenfield
design exercise; it is a port of working code, which is exactly what ADR-0006's
product-first amendment requires.

Two facts shape the whole extraction:

1. **It is already platform-shaped.** Not one of the seven tables carries a
   `tenant_id`. Sub runs one operator tenant, so its integration platform was
   built control-plane-scoped from the start. That is why the module is
   **platform-plane only** under ADR-0023 — `platform_tables` with an empty
   tenant `tables` tuple.
2. **It has zero cross-product consumers.** ERP, CRM and Academy reference none
   of these tables or models. So this is a single-source extraction, and
   "a second consumer proves reuse" comes after the first cutover, not before.

## The seven tables

| Table | Owns | Key invariant already present |
|---|---|---|
| `integration_installations` | a configured connector instance | `(connector_key, name)` unique; state machine `draft→validating→enabled→disabled→quarantined→retired`; `manifest_digest` |
| `integration_config_revisions` | immutable configuration history | `(installation, revision)` unique; `config_digest`; **`secret_refs` holds REFERENCES, never values** |
| `integration_capability_bindings` | which capability this installation serves | `(installation_id, capability_id)` unique; `scope_json`, `policy_json` |
| `integration_event_subscriptions` | which events a binding wants | `(binding, event_type)` unique; `filter_json`, `payload_policy_json` |
| `integration_inbox` | verified inbound receipts | **`(binding, provider_event_id)` unique — the dedup key**; `payload_digest`, `consequence_json` |
| `integration_deliveries` | outbound attempts | `idempotency_key`, `payload_digest`, `attempt_count`, `next_attempt_at`, **`leased_until`**; dispatcher index on `(state, next_attempt_at)` |
| `integration_checkpoints` | polling cursors | `(job_id, binding)` unique; `version` for optimistic locking; `cursor_json`, `advanced_at` |

`secret_refs` and the immutable-revision shape are the two most valuable things
here: both are what ADR-0024 § 7 demands, already built and already tested.

## Classification — every piece, one destination

### Generic engine → `dotmac-integration` module

`installations.py` · `manifest.py` · `registry.py` · `inbox.py` · `delivery.py`
· `runtime.py` · `runtime_execution.py` · `runner_protocol.py` ·
`egress_gateway.py` · `egress_policy.py` · `external_runner.py`, plus all seven
tables above.

### Provider plugins → independently released distributions

`connectors/`: `whatsapp_runtime.py` · `meta_social_runtime.py` ·
`nextcloud_talk.py` · `payment_gateway.py` · `http_webhook.py` ·
`fiber_inquiry_http.py` · `lead_capture_http.py` · `dotmac_crm.py` ·
`dotmac_erp.py`.

**`dotmac_crm.py` and `dotmac_erp.py` are not ported.** Per Michaels direction,
CRM and ERPNext paths are retiring; recreating them as plugins would preserve an
architecture that is being removed.

### Sub business policy → stays in Sub

The `*_capability.py` handlers (`whatsapp_`, `meta_social_`, `payment_`,
`nextcloud_talk_`, `crm_`, `erp_`), `crm_ticket_readiness.py`,
`backoffice_contracts.py`, `*_installation.py` wrappers, and
`podman_transport.py`.

This is the boundary that matters: a capability *handler* decides what a message
or payment **means** to Sub. ADR-0024 keeps that with the product and moves only
the machinery that carried it.

## Binding multiplicity — enabled is not selected

An earlier revision of this file recorded Sub's `(installation_id,
capability_id)` constraint as a defect against ADR-0024 § 7. **That was a
misreading and is corrected here**, because a document that quietly drops an
error teaches nobody why it was wrong.

§ 7 says "each `(installation, capability)` has exactly one active connector
binding" — the *tuple*, never a global per-capability constraint. Sub enforces
exactly that tuple. Two distinct concepts were being conflated:

| | meaning | multiplicity |
|---|---|---|
| **enabled** | this installation is capable and permitted to implement the capability | **many** installations may be enabled for one capability |
| **selected** | the binding chosen for one concrete dispatch | **exactly one**; zero or several fail closed |

Sub already separates them. `require_enabled_capability_binding` filters enabled
bindings, narrows by an optional `connector_key`, and where several remain
requires exactly one `policy_json.default is True` — raising *"multiple enabled
bindings; exactly one must be default"* otherwise. **Routing never reads
`scope_json`**; its only observed consumer displays configured ERP domains
(`erp_admin.py`).

So the module preserves the tuple constraint, does not constrain `capability_id`
alone, and puts no raw `scope_json` in any uniqueness constraint — JSON equality
cannot detect overlapping scopes. If scoped routing ever gains a real consumer
it needs a separate typed route contract with canonical keys and overlap rules.

## The one real gap

**No SPI version range is stored.** `connector_version` and `manifest_digest`
exist, but nothing records the SPI range a connector was built against, so
"an incompatible SPI version refuses activation" cannot be enforced. Slice 1
adds it, checked at discovery, startup **and** activation.

## Parity evidence available to port

26 test files in `dotmac_sub/tests/` cover this area, including
`test_integration_installations.py`, `test_integration_installation_api.py`,
`test_integration_delivery.py`, `test_integration_capability_sync.py`,
`test_integration_manifest_adoption.py`, `test_connector_services.py`,
`test_connector_auth_config_encryption.py`, `test_connector_header_masking.py`
and `test_external_connector_end_to_end.py`.

Ported **with the code they prove**, slice by slice — not in one batch.

## Retirement paths

Sub retires the engine modules above once the module is composed and cut over,
keeping its `*_capability.py` handlers.

ERP, CRM and Academy have **no dependency on this platform**; their retirement is
of their own direct connector surface, already frozen and counted in
[`external-connector-sources.md`](external-connector-sources.md) — 21, 33 and 3
`http_client` files respectively. CRMs is deletion, not migration.
