# WhatsApp / Meta connector — extraction sources

Dated 2026-08-14. As-built characterisation of what already exists in the fleet,
written before any connector code, per hard rule 22 (product-first extraction).
Facts, not mandates.

The target is the first real connector for the Integrator: **ingress-only**
Meta/WhatsApp, a separately released distribution discovered through the
`dotmac_integration.connectors` entry-point group.

## Blocking finding: SPI 1.0 declares `INGRESS` but cannot run it

**This must be resolved before any connector code is written.**

`dotmac-integration 0.1.0a1` ships `ConnectorMode.INGRESS`, and
`conformance.FakePlugin` declares it. Nothing consumes it. The `ConnectorPlugin`
protocol offers exactly four members — `manifest`, `historical_manifests`,
`modes`, `handler_for`, `validate_connection` — and the only executable one is
`handler_for`, which returns a `CapabilityHandler` taking a `DispatchRequest`.

`DispatchRequest` carries `capability_id`, `event_type`, `payload`, `config`,
`secrets`, `idempotency_key`. It carries **no raw body and no headers**.

An ingress connector therefore has no seam for the three things it must do:

1. **Verify the provider signature.** Meta signs the raw body with
   HMAC-SHA256 and presents `X-Hub-Signature-256`. Verification needs the exact
   bytes and the header; a parsed `payload` cannot reproduce them.
2. **Answer the verification handshake.** Meta subscribes an endpoint with a
   `GET` carrying `hub.mode` / `hub.verify_token` / `hub.challenge`, and expects
   the challenge echoed.
3. **Normalise a raw webhook** into the `(provider_event_id, event_type,
   payload)` triple that `receive_verified` requires.

The module is explicit that this is the connector's job —
`receive_verified`'s docstring reads *"Signature verification belongs to the
connector that knows the provider's scheme"* — but gives the connector nowhere
to do it.

### The workarounds, and why each is refused

| Route | Refused because |
|---|---|
| The assembly imports the connector and verifies | The assembly would name a provider. Fails `test_the_assembly_stays_thin.py`, and makes the deployment know a specific provider — ADR-0024's parallel-authority failure. |
| The connector ships a FastAPI router the assembly mounts | Couples a connector distribution to a web framework and to the assembly's HTTP surface. The SPI has no mount point, so this would be a private side-channel between two distributions that are supposed to meet only at the SPI. |
| Accept unverified webhooks | A public, unauthenticated endpoint that writes to the inbox. Not a trade-off. |

### Proposed resolution

Extend the SPI in `dotmac-integration` with an ingress hook, release the next
alpha, raise the assembly pin, then build the connector. Sketch:

```python
@runtime_checkable
class IngressHandler(Protocol):
    def challenge(self, params: Mapping[str, str], *, config, secrets) -> str | None: ...
    def verify(self, raw_body: bytes, headers: Mapping[str, str], *,
               config, secrets) -> bool: ...
    def normalize(self, raw_body: bytes, headers: Mapping[str, str]
                  ) -> tuple[InboundEvent, ...]: ...
```

with `ConnectorPlugin.ingress_handler_for(capability_id)` alongside
`handler_for`, and an `InboundEvent(provider_event_id, event_type, payload)`
carrying exactly what `receive_verified` takes. The assembly then grows ONE
provider-agnostic route — `POST /ingress/{connector_key}/{capability_id}` plus
its `GET` twin — that resolves the plugin by key through discovery.

Note `normalize` returns a TUPLE: a single Meta webhook POST carries a batched
`entry[].changes[].value.messages[]` and can contain several messages. A
one-event signature would silently drop all but the first.

## Source 1 — `dotmac_sub` (the qualifying source)

Already structured as a connector runtime with a registry, which is why it
qualifies rather than merely existing.

| File | LOC | Relevance |
|---|---|---|
| `app/services/integrations/connectors/whatsapp_runtime.py` | 584 | The runtime. Mixed ingress/egress. |
| `app/api/meta_inbox_webhooks.py` | 486 | Receiver: signature check, handshake, fan-out. |
| `app/services/integrations/registry.py` | 895 | Registry; `key="whatsapp"` entry at line 564 pointing at the runtime module. |
| `tests/test_team_inbox_whatsapp_webhook.py` | 575 | Parity tests to port. |
| `tests/test_team_inbox_meta_social_webhook.py` | 474 | Sibling channel; requirement input. |

### In scope (ingress)

- `normalize_inbound_webhook(provider, payload)` — already documented as
  *"Normalize a verified provider fact without persistence or decisions"*, which
  is precisely the shape the SPI wants. The closest thing to a drop-in port in
  the whole inventory.
- `_verify_meta_signature` / `_signature_matches` — `hmac.new(secret, raw_body,
  sha256)`, compared with `hmac.compare_digest`. Correct construction; port as
  written.
- `_verify_token` — the handshake token, read from settings.
- `_message_text`, `_message_attachments`, `_event_timestamp` — payload shaping.
- `normalize_phone_identifier` — sender identity normalisation.

### Explicitly OUT of scope (egress / DELIVERY mode)

`build_text_payload`, `build_template_payload`, `build_media_payload`,
`_endpoint`, `_media_endpoint`, `_headers`, `_response_receipt`,
`_template_variables`, `WhatsAppRuntimeRunner`, `_ordered_template_parameters`.

Roughly two thirds of `whatsapp_runtime.py` is send-side. Ingress-only means
this connector declares `frozenset({ConnectorMode.INGRESS})` and ships none of
it — a connector that could send would need a shadow plan for outbound traffic
too, and outbound is where a mistake reaches a customer.

### Coupling to remove during the port

- `_verify_token(db)` and `_whatsapp_signature_fallback_secret(db)` take a
  **Session**. A connector receives `config` and `secrets` and must never see a
  database — the module materialises secrets before dispatch precisely so a
  plugin cannot reach persistence.
- The receiver writes directly to Sub's team-inbox tables. In the Integrator the
  connector normalises and returns; `receive_verified` records.
- `_signature_fallback_secret` exists for a rotation window. Carry the behaviour,
  but as an explicit `secrets` key rather than a database lookup.

## Source 2 — `dotmac_crm` (requirement input, NOT an extraction source)

| File | LOC |
|---|---|
| `app/services/meta_webhooks.py` | 3282 |
| `app/web/public/crm_webhooks.py` | 1175 |

Larger, and fused with CRM domain decisions: `_mark_whatsapp_channel_invalid_from_status`,
`_fetch_profile_name`, `_fetch_instagram_message_attachments`,
`_collect_meta_attribution`, `_extract_identity_metadata`. These decide
CRM-owned state and call the Graph API mid-webhook.

Extracting from here would drag CRM's domain into a connector. It is read for
**requirements** — particularly attachment normalisation and status-callback
handling, which Sub's version covers less thoroughly — and its behaviours are
recorded as parity obligations rather than ported code.

## Shadow plan (before anything is retired)

The current owner is Sub's `meta_inbox_webhooks` receiver. Shadow means Meta
delivers to both, the Integrator records receipts, and the two are compared on
`provider_event_id` coverage and normalised field equality. Nothing in Sub is
disabled and the external-connector ratchet baseline is **not** lowered until a
cutover is agreed on that evidence. The ratchet moves in the same change that
retires the old path — never before.

## Sequence

1. Resolve the SPI ingress gap; release the next `dotmac-integration` alpha.
2. Raise the assembly pin; add the provider-agnostic ingress route.
3. Build `dotmac-connector-whatsapp` as a Starter package, ingress-only, porting
   the Sub surface above with its parity tests.
4. Publish; install into the Integrator deployment.
5. Shadow against Sub.
6. Cut over and lower the ratchet in one change.
