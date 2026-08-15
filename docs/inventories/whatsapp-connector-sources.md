# WhatsApp / Meta connector — extraction sources

Dated 2026-08-14. As-built characterisation of what already exists in the fleet,
written before any connector code, per **hard rule 24** (product-first
extraction; `AGENTS.md` numbering is authoritative). Facts, not mandates.

The target is the first real connector for the Integrator: **ingress-only**
Meta/WhatsApp, a separately released distribution discovered through the
`dotmac_integration.connectors` entry-point group.

## Resolved finding: `modes` was decorative across the whole SPI

**Resolved in `0.1.0a3`/`0.1.0a4` (SPI 1.1) and refined in `0.1.0a5` (SPI 1.2), which this document specified.** The
analysis below is retained as the requirement record; it describes
`dotmac-integration 0.1.0a1` as shipped, not the current module. See
"Proposed resolution" for what landed.

`dotmac-integration 0.1.0a1` declared three modes — `INGRESS`, `POLL`,
`DELIVERY` — and **nothing consulted `modes` anywhere**. Verified at the time: no
reference in `dispatch.py`, `execution.py` or `selection.py`; `POLL` appeared
only in the enum definition.

`ConnectorPlugin` lists **five** members: `manifest`, `historical_manifests`,
`modes`, `handler_for`, `validate_connection`. Two are callable —
`validate_connection` is invoked at activation — but `handler_for` is the **only
data-movement factory**, and it returns a `CapabilityHandler` taking a
`DispatchRequest` of `capability_id`, `event_type`, `payload`, `config`,
`secrets`, `idempotency_key`.

Three consequences, in order of severity:

1. **Ingress cannot run at all.** `DispatchRequest` carries no raw body and no
   headers, so a webhook connector cannot verify a signature (Meta signs the RAW
   body, HMAC-SHA256, `X-Hub-Signature-256`), answer the subscription handshake
   (`GET` with `hub.mode`/`hub.verify_token`/`hub.challenge`, challenge echoed),
   or normalise into the `(provider_event_id, event_type, payload)` triple
   `receive_verified` requires.
2. **Poll cannot run at all.** No scheduling hook, no cursor seam — the mode is a
   label with no machinery behind it.
3. **Delivery runs unchecked.** Dispatch calls `handler_for` without asking
   whether the plugin declares `DELIVERY`. An ingress-only connector that a
   binding pointed at would be invoked as a delivery target, and the failure
   would surface as a confusing handler error rather than a refusal.

The module is explicit that verification is the connector's job —
`receive_verified`'s docstring reads *"Signature verification belongs to the
connector that knows the provider's scheme"* — but gives the connector nowhere
to do it.

### Workarounds, each refused

| Route | Refused because |
|---|---|
| The assembly verifies | The assembly would name a provider. Fails `test_the_assembly_stays_thin.py`, and recreates the parallel authority ADR-0024 removes. |
| The connector ships a FastAPI router | Couples a connector distribution to a web framework and to the assembly's HTTP surface — a private side-channel between two distributions that should meet only at the SPI. |
| Accept unverified webhooks | A public, unauthenticated write path into the inbox. Not a trade-off. |

### Proposed resolution

**A small base protocol plus mode-specific executable protocols**, each with its
own conformance check, so a declared mode is a promise the kit verifies rather
than a string:

```python
class ConnectorPlugin(Protocol):            # base: identity and metadata only
    manifest; historical_manifests; modes; validate_connection

class DeliveryPlugin(ConnectorPlugin):      # MODE: DELIVERY
    def handler_for(self, capability_id: str) -> CapabilityHandler: ...

class IngressPlugin(ConnectorPlugin):       # MODE: INGRESS
    def ingress_handler_for(self, capability_id: str) -> IngressHandler: ...

class PollPlugin(ConnectorPlugin):          # MODE: POLL
    def poll_handler_for(self, capability_id: str) -> PollHandler: ...
```

Conformance asserts the implication in both directions: a plugin declaring a
mode must satisfy that mode's protocol, and a plugin satisfying one must declare
it. Dispatch then refuses a binding whose plugin does not declare `DELIVERY`,
instead of discovering it at call time.

`IngressHandler` is `challenge` / `verify` / `normalize`, where `normalize`
returns a **tuple** — one Meta POST batches `entry[].changes[].value.messages[]`,
and a single-event signature would silently drop all but the first.

#### The ingress URL addresses a binding, not a (connector, capability) pair

`/ingress/{connector_key}/{capability_id}` is **ambiguous**: several
installations may serve the same pair, and the request would carry no way to
choose between them. Two installations of the same connector — a production WABA
and a test one, or two tenants' — are the normal case, not an edge case.

The URL must resolve **one stable binding/endpoint identifier**. From that the
module derives everything else: the installation, the connector, the capability,
the pinned manifest, the active config revision, and the secret references. A
caller never names a connector, and rotating any of those does not change the
URL the provider is configured with — which matters because the provider-side
endpoint is a manual configuration change.

#### Ingress keeps the three-phase shape

Same discipline dispatch already uses, for the same reason — no DB transaction
is held across provider-facing work:

1. **Prepare, under transaction.** Resolve the binding, load the config revision
   and manifest pin, materialise secrets.
2. **Invoke the plugin with no session.** `verify` then `normalize`. The plugin
   receives bytes, headers, config and materialised secrets, and never a
   `Session`.
3. **Record the whole tuple atomically.** Every normalised event from that
   request is written in one transaction.

**One event collision must roll back the entire provider batch.** A partial
write leaves the provider believing the batch was accepted while some events
were never recorded, and Meta will not resend the ones that landed. Rolling the
whole batch back makes a retry correct: `receive_verified` is idempotent, so the
already-seen events are recognised and the missing ones are written.

#### Material handling

Raw body, signature headers and materialised secrets are **ephemeral and never
logged** — not at debug, not in an error payload, not in a stored diagnostic. A
signature in a log is a replay aid; a body in a log is customer message content
in the observability stack.

Failures use **typed outcomes and error codes**, not strings carrying fragments
of the request. A **generic request-size limit** applies at the ingress edge,
before verification: HMAC over an unbounded body is a cheap way to spend the
process's memory, and the limit belongs to the edge rather than to any connector
so a single connector cannot opt out of it.

## Source 1 — `dotmac_sub` (the qualifying source)

Already structured as a connector runtime with a registry, which is why it
qualifies rather than merely existing.

| File | LOC | Relevance |
|---|---|---|
| `app/services/integrations/connectors/whatsapp_runtime.py` | 584 | The runtime. Mixed ingress/egress. |
| `app/api/meta_inbox_webhooks.py` | 486 | Receiver: signature check, handshake, fan-out. |
| `app/services/integrations/registry.py` | 895 | Registry; `key="whatsapp"` entry at line 564. |
| `tests/test_team_inbox_whatsapp_webhook.py` | 575 | Parity tests to port. |
| `tests/test_team_inbox_meta_social_webhook.py` | 474 | Sibling channel; requirement input. |

### In scope (ingress)

- `normalize_inbound_webhook(provider, payload)` — already documented as
  *"Normalize a verified provider fact without persistence or decisions"*, which
  is precisely the shape the SPI wants. The closest thing to a drop-in port in
  the whole inventory. Must grow a batch return.
- `_verify_meta_signature` / `_signature_matches` — `hmac.new(secret, raw_body,
  sha256)` compared with `hmac.compare_digest`. Correct construction; port as
  written.
- `_verify_token` — the handshake token.
- `_message_text`, `_message_attachments`, `_event_timestamp` — payload shaping.
- `normalize_phone_identifier` — sender identity normalisation.

### Explicitly OUT of scope (egress / `DELIVERY`)

`build_text_payload`, `build_template_payload`, `build_media_payload`,
`_endpoint`, `_media_endpoint`, `_headers`, `_response_receipt`,
`_template_variables`, `WhatsAppRuntimeRunner`, `_ordered_template_parameters`.

Roughly two thirds of `whatsapp_runtime.py` is send-side. Ingress-only means
this connector declares `frozenset({ConnectorMode.INGRESS})`, satisfies
`IngressPlugin` only, and ships none of it — a connector that could send would
need a shadow plan for outbound traffic too, and outbound is where a mistake
reaches a customer.

### Coupling to remove during the port

- `_verify_token(db)` and `_whatsapp_signature_fallback_secret(db)` take a
  **Session**. A connector receives `config` and `secrets` and must never see a
  database.
- The receiver writes directly to Sub's team-inbox tables. In the Integrator the
  connector normalises and returns; `receive_verified` records.
- `_signature_fallback_secret` exists for a rotation window. Carry the behaviour
  as an explicit `secrets` key, not a database lookup.

## Source 2 — `dotmac_crm` (requirement input, NOT an extraction source)

`app/services/meta_webhooks.py` (3282 LOC) and `app/web/public/crm_webhooks.py`
(1175 LOC). Fused with CRM domain decisions —
`_mark_whatsapp_channel_invalid_from_status`, `_fetch_profile_name`,
`_fetch_instagram_message_attachments`, `_collect_meta_attribution`,
`_extract_identity_metadata` — which decide CRM-owned state and call the Graph
API mid-webhook.

Extracting from here would drag CRM's domain into a connector. It is read for
**requirements**, particularly attachment normalisation and status-callback
handling, and its behaviours become parity obligations rather than ported code.

## Shadow plan — an ingress-edge mirror, not dual delivery

**Meta does not fan out.** The WhatsApp Business Platform documents **one
configured callback endpoint** per app; per-WABA overrides **change the
destination** rather than duplicate delivery.[^meta] So "subscribe both and
compare" is not available — configuring the Integrator's URL would *move*
traffic off Sub, which is a cutover, not a shadow.

The shadow is therefore a **temporary mirror at the ingress edge**, in front of
Sub's receiver:

- the edge forwards a copy to the Integrator preserving the **exact raw body and
  the signature header** — any re-serialisation invalidates the HMAC and would
  make the Integrator reject traffic Sub accepted, producing a difference that
  is an artefact of the mirror rather than of the connector;
- **Sub remains the response owner.** The provider sees Sub's status code and
  only Sub's; the mirror is fire-and-forget and its failures must never affect
  the response Meta receives, or the shadow becomes an availability risk to the
  live path;
- **the Integrator records receipts only** — no replies, no Graph API calls, no
  writes outside `mod_intg`;
- comparison is on `provider_event_id` coverage and normalised field equality.

The mirror is explicitly temporary and is removed at cutover, when the callback
URL is repointed at the Integrator. The external-connector ratchet baseline is
**not** lowered before that, and moves in the **same change** that retires
Sub's receiver.

[^meta]: Meta's official WhatsApp Business Platform collection —
https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api?entity=request-13382743-b37ef0a5-f8be-4e42-bfd0-3557a7d6b754

## Open item: the fleet ratchet is unmonitored in CI

`test_external_connector_ratchet` skips when the fleet is not checked out beside
the Starter, which is always true in CI. It currently fails locally:
`dotmac_sub.sync_checkpoint: 9 > baseline 8` — a new direct connector surface
landed in Sub while the Integrator that replaces it was being built.

**The baseline is not being raised to 9.** That is live drift, and per ADR-0018
a guard that abstains where the decision is made is *unmonitored rather than
exempt* — raising the number would convert an unenforced guard into a silently
weakened one. Both the drift and the CI abstention need addressing; neither is
in scope here, and neither is closed by this document.

## Sequence

1. Resolve the SPI gap — base protocol plus mode-specific protocols, ingress
   hook, binding-addressed route, mode-checked dispatch — and release the next
   `dotmac-integration` alpha **as its own change**. *Landed in tree as
   `0.1.0a5` (SPI 1.1 protocols at `a3`, `ingress.py` and migration `ig_0003`
   at `a4`, then SPI 1.2's immutable `IngressRequest` envelope and
   connector-supplied `Acknowledgement` at `a5`, with the empty-body handshake
   inference removed — a bodyless POST is a delivery, so handshake and delivery
   need the DISTINCT GET and POST routes step 2 adds). The RELEASE of that alpha
   is still outstanding, and step 2 blocks on it.*
2. Raise the assembly pin; add the provider-agnostic binding-addressed route
   pair with its size limit.
3. Build `dotmac-connector-whatsapp` as a Starter package, ingress-only, porting
   the Sub surface above with its parity tests.
4. Publish; install into the Integrator deployment.
5. Mirror at the ingress edge and compare.
6. Repoint the callback, retire Sub's receiver, and lower the ratchet in one
   change.
