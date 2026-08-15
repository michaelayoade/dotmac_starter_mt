# WhatsApp/Meta ingress connector — extraction dossier

Dated 2026-08-15. **Pre-registered**: this is an `EXTRACTION.toml` written
before the package it describes is allowed to exist.

[ADR-0030](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
§ 6 blocks connector implementation and permits *dossiers, capability contracts
and conformance specifications*. So the dossier lives here as a document rather
than as `packages/dotmac-connector-whatsapp/EXTRACTION.toml`: creating that path
would create the package, which is exactly the thing not authorized. When the
gate opens the TOML block in § 1 moves to that path unchanged.

**Companion documents.**
[`whatsapp-connector-sources.md`](whatsapp-connector-sources.md) is the source
inventory — what exists in the fleet, the ingress/egress split, the SPI gap, the
shadow plan. This dossier is the *disposition*: which source code ports, which
does not, what the parity obligations are, and what the retirement gate is.
The conformance obligations are
[`../superpowers/specs/2026-08-15-meta-whatsapp-ingress-conformance.md`](../superpowers/specs/2026-08-15-meta-whatsapp-ingress-conformance.md),
and their executable form is `tests/unit/test_meta_whatsapp_ingress_conformance.py`
over the corpus in `tests/fixtures/meta_whatsapp/`.

---

## 0. Authorization state — read this before writing any code

| Gate | State on 2026-08-15 |
|---|---|
| ADR-0030 § 6 names the distribution | **NO.** No connector appears in the ten authorized names. |
| Integrator secret resolver complete | **NO.** Under construction. Until an installation passes connection validation with materialised secrets, no connector is operationally complete. |
| Michael has named the exact provider/package | **NO.** |
| SPI carries an ingress hook | **NO.** `modes` is decorative; `handler_for` is the only data-movement factory and `DispatchRequest` carries neither raw body nor headers. Being corrected. |

Four gates, four negatives. This dossier, the specification and the fixture
corpus are the permitted work. **Nothing in this document authorizes a
connector**, and its existence is not a step toward authorization — it is the
evidence that must exist *before* authorization can be considered.

---

## 1. The dossier

```toml
schema_version = 1
package = "PENDING — ADR-0030 § 6 must name the exact distribution"
classification = "connector-plugin"
status = "specification-only"
source_mode = "product-first"
owner = "Meta/WhatsApp Cloud API wire format and provider I/O — nothing else"

# ADR-0024 §§ 6-7. A connector translates a wire format and performs provider
# I/O. It never imports product code, opens a product database, decides
# business state, or implements its own retry/checkpoint engine. Ingress-only
# narrows that further: this connector performs no provider I/O AT ALL. It
# receives bytes, proves they came from Meta, and returns typed observations.
contract = "Verify a Meta webhook signature over the exact bytes received; answer the subscription handshake; normalise one Meta batch into a tuple of typed observations with stable, provider-derived event identities. NOT what a message means, NOT who sent it in product terms, NOT whether to reply, NOT delivery, NOT retry."

# No persistence plane. A connector plugin owns no tables: receipts,
# deduplication and delivery evidence live in dotmac-integration's mod_intg
# platform plane. If this package ever acquires a migration lineage, something
# has gone wrong.
planes = "none"

modes = ["INGRESS"]
capabilities = ["messaging.inbound.v1"]

source_repositories = ["dotmac_sub", "dotmac_crm"]

# THE QUALIFYING SOURCE. Sub, and only Sub. Blob digests are recorded so a
# reader can prove which revision was read rather than trusting the line
# numbers to have held.
source_paths = [
  "dotmac_sub:app/api/inbox_webhooks.py",                              # 6ba45f701, blob dd9e5f9d3
  "dotmac_sub:app/services/integrations/whatsapp_installation.py",     # 8b11635ad, blob 1ef4d6bef
  "dotmac_sub:app/services/integrations/connectors/whatsapp_runtime.py", # b3cecb605
]
preserved_tests = [
  "dotmac_sub:tests/test_team_inbox_whatsapp_webhook.py",              # 638c7f8bb, blob 73f16f03f
]

# NOT ported, and named so nobody ports them by reflex:
#
#   the egress two-thirds     build_text_payload, build_template_payload,
#                             build_media_payload, _endpoint, _media_endpoint,
#                             _headers, _response_receipt, _template_variables,
#                             _ordered_template_parameters,
#                             WhatsAppRuntimeRunner. Ingress-only means the
#                             connector cannot send, so it ships none of it.
#   *_capability.py           Sub business policy: what a message MEANS to Sub.
#   team_inbox_*              conversation threading, subscriber matching,
#                             media assets, delivery-status projection. All
#                             product decisions.
#   meta_inbox_webhooks.py    the SOCIAL receiver (Messenger/Instagram). A
#                             sibling channel and a separate capability; read
#                             for requirements, and specifically NOT for its
#                             handshake, which is circular (see § 3.1).
not_ported = ["build_*_payload", "_endpoint", "_media_endpoint", "_headers", "_response_receipt", "WhatsAppRuntimeRunner", "*_capability.py", "team_inbox_*"]

contract_consumers = []
candidate_consumers = ["dotmac_integrator"]
inventory_evidence = [
  "docs/inventories/whatsapp-connector-sources.md",
  "docs/inventories/whatsapp-connector-dossier.md",
  "docs/superpowers/specs/2026-08-15-meta-whatsapp-ingress-conformance.md",
]

first_cutover = "Sub's WhatsApp inbound webhook (`POST /webhooks/whatsapp/meta`). Ingress-only and read-only in effect: the connector produces observations, and every decision about what they mean stays with Sub. Deliberately NOT the social channels, which are a separate capability with their own dossier, and deliberately NOT any send path."

shadow_and_drift = "An ingress-edge MIRROR, not dual subscription. Meta documents ONE configured callback endpoint per app; a per-WABA override MOVES the destination rather than duplicating delivery, so 'subscribe both and compare' is not available — pointing Meta at the Integrator would be a cutover, not a shadow. The edge forwards a copy preserving the EXACT raw body and signature header (a re-serialised copy invalidates the HMAC and produces drift that is an artefact of the mirror); Sub remains the sole response owner and the mirror is fire-and-forget; the Integrator records receipts only. Comparison is on provider_event_id coverage and normalised field equality."

local_copy_retirement = "Sub retires `app/api/inbox_webhooks.py`, `_verify_meta_signature`, `_iter_meta_whatsapp_messages`, `_iter_meta_whatsapp_statuses` and the WhatsApp verify-token/signing-secret handling once the connector is composed and the callback URL is repointed. Sub KEEPS `whatsapp_capability.py` and everything under team_inbox — what a message means to Sub was never the connector's. The external-connector ratchet baseline is lowered in the SAME change as the retirement, so it is reviewable as a diff rather than asserted."

next_action = "BLOCKED on four gates (§ 0). The permitted next artefacts are: the social-channel sibling dossier, and re-running the fixture corpus against Sub's receiver as a parity harness once the SPI carries an ingress hook. No package directory is created until ADR-0030 § 6 names the distribution."

# PARITY DISPOSITIONS — one per source suite, and the reason.
[parity]
"tests/test_team_inbox_whatsapp_webhook.py" = "port-fixtures-not-assertions"
"tests/test_team_inbox_meta_social_webhook.py" = "keep-in-sub"
"tests/test_integration_whatsapp_capability.py" = "keep-in-sub"
"tests/test_web_integrations_whatsapp.py" = "keep-in-sub"
"tests/test_team_inbox_whatsapp_contact_search.py" = "keep-in-sub"
"tests/test_notifications_template_preview_and_whatsapp.py" = "exclude-egress"

[parity.ported_to]
"tests/test_team_inbox_whatsapp_webhook.py" = "tests/fixtures/meta_whatsapp/ + tests/unit/test_meta_whatsapp_ingress_conformance.py"

[parity.reasons]
"port-fixtures-not-assertions" = "The REQUEST BODIES are the durable asset and port verbatim, with provenance. The ASSERTIONS do not: every one of them is about Sub's conversations, subscribers, media assets and delivery-status projection — product state a connector must never touch. Six of the corpus's eight bodies come from this file."
"keep-in-sub" = "Product business policy or product UI. What a message means to Sub, and how Sub renders it, stays with Sub under ADR-0024."
"exclude-egress" = "Send-side. Out of scope for an ingress-only connector."
```

---

## 2. What ports, exactly

| Sub surface | Disposition | Note |
|---|---|---|
| `_verify_meta_signature` / signature construction | **Port the construction, replace the plumbing** | `hmac.new(secret, raw_body, sha256)` compared with `compare_digest` is correct. Everything around it — the `Session`, the `HTTPException`, the single secret — is replaced. |
| `verify_whatsapp_webhook_challenge` | **Port the behaviour** | The `{disabled, enabled}` state rule is the precedent for spec **WAI-2**. |
| `VerifyWhatsAppWebhookChallengeQuery` | **Port the pattern** | `field(repr=False)` on the presented token, so the value cannot reach a log through a traceback. Sub tests this explicitly. |
| `_iter_meta_whatsapp_messages` | **Port the traversal, replace the filtering** | The `entry → changes → value → messages` walk is right. The `continue` filtering is not (§ 3.4). |
| `_iter_meta_whatsapp_statuses` | **Port the traversal, add identity** | Statuses currently have no event identity at all. |
| `_whatsapp_attachments` | **Port** | Reference-only media shaping: id, mime type, caption, filename, coordinates. No fetch. |
| `normalize_inbound_webhook` | **Requirement input only** | Documented as *"Normalize a verified provider fact without persistence or decisions"* — the right shape, but it handles ONE message and reads a payload shape (`payload["message"]`) that the webhook never produces. It is the runtime's internal envelope, not Meta's. |
| `normalize_phone_identifier` | **Do not port** | Identity normalisation into a product's address space is a product decision. |

`dotmac_crm`'s `meta_webhooks.py` (3282 LOC) and `crm_webhooks.py` (1175 LOC)
are **requirement input, never an extraction source** — fused with CRM domain
decisions (`_mark_whatsapp_channel_invalid_from_status`, `_fetch_profile_name`,
`_collect_meta_attribution`) and calling the Graph API mid-webhook. Their
behaviours become parity obligations, not ported code.

---

## 3. What a future connector must NOT inherit

Fourteen findings, from reading the production code. Each is a real line in a
live system, not a hypothetical.

### 3.1 The circular handshake (social receiver)

`meta_inbox_webhooks._verify_token(db)` → `inbound_secret_material(db)` →
`require_binding(db)` → `require_enabled_capability_binding`. The **GET**
handshake therefore needs an *enabled* binding — which cannot exist until the
handshake succeeds. Sub's **WhatsApp** path fixed this in commit `8b11635ad` by
resolving the installation directly and admitting `{disabled, enabled}`. Inherit
the WhatsApp path. → spec **WAI-2**

### 3.2 Request-digest event identity

`provider_event_id = f"meta:{sha256(raw_body).hexdigest()}"` — one identity per
**request**, not per event. Deduplicates an exact retry and nothing else: a
message regrouped into a different batch is recorded twice, and twenty good
events share the fate of the one bad entry beside them. → spec **WAI-26**,
`test_a_request_digest_identity_cannot_deduplicate_a_regrouped_event`

### 3.3 Statuses with no identity of their own

`delivered` and `read` for one `wamid` are two events. Sub carries no status
identity at all — it projects straight onto the outbound message row, so the
last write wins, and retries arrive out of order. → spec **WAI-21**

### 3.4 Silent drop of anything unparseable

```python
if not sender or not body:
    continue
```

with `_text_body` returning `""` for every type outside
`_WHATSAPP_QUALIFYING_MESSAGE_TYPES`. A `reaction`, an `order`, a `system`
notification, or any type Meta adds next year: no row, no metric, no receipt —
and Meta will not resend it. → spec **WAI-30**,
fixture `08_unsupported_message_type.json`

### 3.5 Presentation decided inside ingress

`_text_body` returns `f"[{message_type}]"` placeholders for a product's message
list, and `is_echo` filtering decides which of your own messages to ignore. Both
are product rendering/business rules living in wire-format code. → spec § 5

### 3.6 Non-deterministic timestamps

```python
datetime.fromtimestamp(float(...), tz=UTC) if ....isdigit() else datetime.now(UTC)
```

A missing or non-numeric timestamp becomes *now*, so the same bytes normalise
differently on every replay and conformance becomes unprovable. → spec **WAI-32**

### 3.7 Magnitude-guessed time units

WhatsApp sends seconds; Messenger/Instagram send milliseconds. The social
receiver guesses: `if timestamp < 100_000_000_000`. A capability declares its
unit. → spec § 5

### 3.8 The fallback secret that is not a rotation mechanism

`_whatsapp_signature_fallback_secret` reaches into a **different** connector's
secret material through `except Exception: return None`. Cross-installation
secret borrowing dressed as rotation: no window, no ordering, no observability,
no retirement step, and a load failure is indistinguishable from absence.
→ spec **WAI-14..19**

### 3.9 Database sessions in verification

`_verify_token(db)`, `_app_secret(db)`,
`_whatsapp_signature_fallback_secret(db)`, `inbound_secret_material(db)`. A
connector receives materialised secrets, never a connection. → ADR-0024 § 7

### 3.10 Graph API calls inside the webhook

The social receiver calls `fetch_contact_profile` — network I/O — during
normalisation, inside `except Exception: profile = None`. Ingress `verify` and
`normalize` are pure. → spec **WAI-32**

### 3.11 HTTP vocabulary inside connector logic

`HTTPException(401)` / `(503)` raised from what would be connector code. A
connector returns typed outcomes and stable error codes; status codes belong to
the ingress edge. → ADR-0024 § 6

### 3.12 One receipt, one consequence, for a whole batch

`consequence_json` carries `{"processed": n, "items": [...]}` for the entire
request, so a partial failure has no per-event record and a redelivery cannot
be reconciled event by event. → spec **WAI-24**, **WAI-33**

### 3.13 No request-size limit before HMAC

Verification runs over an unbounded body. HMAC over an unbounded body is a cheap
way to spend the process's memory, and the limit belongs to the edge so no
single connector can opt out of it. → spec **WAI-13**

### 3.14 Provider response bodies stored verbatim

`output["response"] = response.text[:2000]` (egress; not ported, but named so it
is not reintroduced). A provider body in stored output is customer message
content in the operational ledger. → spec **WAI-12**

---

## 4. Fixture corpus provenance

Eight bodies under `tests/fixtures/meta_whatsapp/bodies/`. Six verbatim from
Sub's production test suite, two composed for cases Sub cannot produce.

| Fixture | Source | Verbatim |
|---|---|---|
| `01_text_message.json` | `dotmac_sub:tests/test_team_inbox_whatsapp_webhook.py::test_meta_whatsapp_webhook_creates_native_inbox_message` L235-269 | yes |
| `02_media_image_message.json` | …`::test_meta_whatsapp_webhook_preserves_media_message` L302-330 | yes |
| `03_location_message.json` | …`::test_meta_whatsapp_webhook_renders_location_as_google_maps_link` L351-381 | yes |
| `04_status_delivered.json` | …`::test_meta_whatsapp_webhook_updates_outbound_delivery_status` L491-515 | yes |
| `05_status_failed_unknown_message.json` | …`::test_meta_whatsapp_webhook_acknowledges_unknown_status` L537-560 | yes |
| `07_empty_entry.json` | …`::test_meta_whatsapp_webhook_rejects_bad_signature` L223 | reformatted |
| `06_batch_mixed.json` | composed from L235-269 + L491-515 | **no** |
| `08_unsupported_message_type.json` | composed against `inbox_webhooks._WHATSAPP_QUALIFYING_MESSAGE_TYPES` | **no** |

All at `dotmac_sub` commit `638c7f8bb` (tests, blob `73f16f03f`) and `6ba45f701`
(`inbox_webhooks.py`, blob `dd9e5f9d3`).

The two composed fixtures exist because **Sub cannot produce them**: it has no
multi-entry, partly-malformed fixture (its receiver `continue`s past what it
cannot parse, so the failing case was never captured), and no unsupported-type
fixture (it produces no output to assert on). The manifest records
`composed_because` for both, and
`test_every_fixture_records_where_it_came_from` fails a non-verbatim fixture
that does not explain itself — so invention can never masquerade as evidence.

**No real secret is committed.** Every key in the corpus contains the literal
`NOT-A-SECRET`; signatures are generated from those test keys over the exact
file bytes by `regenerate.py`; and
`test_no_fixture_carries_credential_shaped_material` fails on `bao://`,
`env://`, `Bearer `, `access_token`, `EAAG` or `app_secret` appearing anywhere
under the fixture root.

---

## 5. Open items this dossier does not close

1. **The SPI has no ingress hook.** `modes` is decorative; `DispatchRequest`
   carries no raw body and no headers. Owned elsewhere; tracked in
   `whatsapp-connector-sources.md`.
2. **The Integrator secret resolver.** ADR-0030 § 6 completion work. Until an
   installation reaches `enabled` with materialised secrets, no connector is
   operationally complete regardless of test coverage.
3. **The fleet ratchet is unmonitored in CI.** `test_external_connector_ratchet`
   skips when the fleet is not checked out beside the Starter, which is always
   true in CI. Per ADR-0018 that is *unmonitored rather than exempt*. Not
   closed here.
4. **The social channels (Messenger, Instagram, comments) need their own
   dossier.** They are a separate capability with a different payload shape
   (`messaging[]` rather than `changes[].value.messages[]`), a different time
   unit, and a receiver that still carries the circular handshake and a
   mid-webhook Graph call.
