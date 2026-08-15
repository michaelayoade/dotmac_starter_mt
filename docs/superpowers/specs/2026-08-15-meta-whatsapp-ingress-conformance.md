# Meta/WhatsApp ingress — capability contract and conformance specification

**Date:** 2026-08-15
**Status:** Specification only. **No connector is authorized.**
**Authority:** [ADR-0030](../../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
§ 6 permits *connector dossiers, capability contracts and conformance
specifications*; it blocks connector implementation. This document and the
corpus under `tests/fixtures/meta_whatsapp/` are the permitted artefacts.
**Evidence:**
[`whatsapp-connector-sources.md`](../../inventories/whatsapp-connector-sources.md),
[`whatsapp-connector-dossier.md`](../../inventories/whatsapp-connector-dossier.md)
**Executable form:** `tests/unit/test_meta_whatsapp_ingress_conformance.py`
**Also governed by:** [ADR-0024](../../adr/0024-apps-compose-by-synchronizing-data.md)
§§ 6–7 (a connector translates wire formats and does provider I/O; it never
imports product code, opens a product database, decides business state, or
implements its own retry/checkpoint engine) and
[ADR-0009](../../adr/0009-secrets-are-held-not-dereferenced.md) (a secret is
held, never dereferenced on a request path).

This is not a plan to build a connector. It is the set of obligations a
connector must discharge *before* anyone agrees it works — written now, while
there is no implementation to rationalise around, so the acceptance criteria
cannot be quietly relaxed to match whatever gets built.

Every requirement below carries an id (`WAI-n`) and names the test that binds
it. A requirement with no test is a preference; this document has none of those.

---

## 0. Scope

**Ingress only.** `messaging.inbound.v1`. The connector receives Meta's HTTP
callbacks, proves they came from Meta, and turns each batch into typed
observations. It does not send, does not call the Graph API, does not resolve a
contact profile, and does not decide what any message means.

Send-side — `build_text_payload`, `build_template_payload`,
`build_media_payload`, media upload, template read, `WhatsAppRuntimeRunner` —
is explicitly out of scope, roughly two-thirds of Sub's `whatsapp_runtime.py`.
Outbound is where a mistake reaches a customer, and an ingress-only connector
cannot make that mistake at all.

**What the connector is given:** the raw request bytes, the request headers,
the resolved configuration of one binding, and materialised secrets.
**What it returns:** a tuple of observations, or a typed verification failure.
**What it never receives:** a database session, a product model, or an ORM.

---

## 1. The subscription handshake

Meta verifies a callback URL with a `GET` carrying `hub.mode`,
`hub.verify_token` and `hub.challenge`. The endpoint echoes `hub.challenge` as
`text/plain` when the token matches.

| Id | Requirement | Bound by |
|---|---|---|
| **WAI-1** | `hub.mode` MUST be `subscribe`; anything else is refused. | `test_the_handshake_echoes_the_challenge_verbatim` |
| **WAI-2** | The challenge MUST be answerable while the binding is **configured but disabled**, as well as enabled. | `test_the_handshake_is_answerable_while_configured_but_disabled` |
| **WAI-3** | Answering it MUST NOT create a receipt, MUST NOT enable anything, and MUST NOT reveal whether the binding is enabled. | `test_answering_the_handshake_grants_nothing` |
| **WAI-4** | `hub.verify_token` MUST be compared in constant time. | `test_the_verify_token_is_compared_in_constant_time` |
| **WAI-5** | A wrong token and a missing token MUST produce the identical refusal. | `test_a_wrong_and_a_missing_verify_token_are_indistinguishable` |
| **WAI-6** | `hub.challenge` is echoed verbatim as `text/plain` — never wrapped in JSON. | `test_the_handshake_echoes_the_challenge_verbatim` |

### WAI-2 is a circularity, not a preference

The draft ingress design required an **enabled** binding to answer the `GET`.
Meta cannot satisfy that. The handshake is what *precedes* activation: the Cloud
API will not save a callback URL whose challenge went unanswered, and no event
is delivered until the URL is saved. An implementation that demands enablement
to answer the challenge can therefore never be enabled — the state it requires
is downstream of the request it refuses.

**Team 2 is correcting this in the module.** This specification states the
target behaviour so the two agree rather than being reconciled later:

* **answers the challenge:** `disabled`, `enabled`
* **refuses:** `draft`, `retired`, invalid current config revision, no such
  binding

`disabled` here means *configured and statically validated, not yet enabled* —
which is precisely the state an operator is in while pasting the URL into Meta's
console. `draft` is refused because nothing has been validated yet, and
`retired` because the operator deliberately ended it.

Sub already resolved this once, and its resolution is the precedent:
`verify_whatsapp_webhook_challenge`
(`dotmac_sub/app/services/integrations/whatsapp_installation.py`, commit
`8b11635ad`, "fix WhatsApp pre-activation webhook verification") admits exactly
`{disabled, enabled}`, and its docstring states the rule outright — *"Compare a
setup challenge without granting inbound runtime capability."* Sub's **social**
receiver (`meta_inbox_webhooks._verify_token` → `inbound_secret_material` →
`require_binding`, which requires an *enabled* binding) still has the
circularity. Inherit the WhatsApp path; do not inherit the social one.

### The URL addresses one binding

The handshake and the callback share one binding-addressed URL. Two
installations of the same connector — a production WABA and a test one — are
normal, and `/ingress/{connector_key}/{capability_id}` cannot tell them apart.
A binding-addressed URL also removes Sub's `whatsapp_webhook_configuration_
ambiguous` failure mode by construction: with the binding named in the path,
"which of the two installations did you mean" cannot arise.

---

## 2. Signature verification

Meta signs the body with the app secret, HMAC-SHA256, and presents
`X-Hub-Signature-256: sha256=<64 lowercase hex>`.

| Id | Requirement | Bound by |
|---|---|---|
| **WAI-7** | The HMAC MUST be computed over the **exact bytes received**, never over a re-serialised body. | `test_reserialising_the_body_invalidates_the_signature` |
| **WAI-8** | Comparison MUST use a constant-time primitive over the whole header value including the `sha256=` prefix. | `test_a_forged_signature_costs_the_same_work_as_a_real_one` |
| **WAI-9** | A header that does not match `^sha256=[0-9a-f]{64}$` is refused before any comparison. | `test_a_missing_or_malformed_signature_header_is_refused` |
| **WAI-10** | A missing header and a wrong signature produce the identical refusal. | `test_a_missing_or_malformed_signature_header_is_refused` |
| **WAI-11** | A refused request MUST produce **zero** observations and **no** receipt. | `test_changing_one_byte_invalidates_the_signature` |
| **WAI-12** | The raw body, the signature and the materialised secrets are never logged — not at debug, not in an error payload, not in a stored diagnostic. | § 7, dossier |
| **WAI-13** | A generic request-size limit applies at the ingress edge, **before** verification. | § 7 |

### Why WAI-7 needs a test rather than a comment

`json.loads` then `json.dumps` returns the same *document* and different
*bytes* — key order, whitespace, unicode escaping, float formatting. Every
signature then fails, and the failure presents as a credential or provider
problem rather than as the re-serialisation it is. Teams lose days to this. The
conformance corpus stores each body pretty-printed and signs *those* bytes, so
`test_reserialising_the_body_invalidates_the_signature` demonstrates the
breakage on real fixtures instead of asserting it in prose.

The same constraint governs the shadow mirror in
`whatsapp-connector-sources.md`: the mirror must forward the exact raw body and
the signature header, or the Integrator rejects traffic Sub accepted and the
"difference" is an artefact of the mirror.

### Verification cannot detect a replay

A redelivery is byte-identical and correctly signed — that is what makes it a
redelivery. So the entire burden of not double-recording falls on § 4's event
identities. A connector that tries to solve replay inside `verify` has instead
started rejecting Meta's legitimate retries
(`test_a_replayed_valid_body_still_verifies_so_dedup_is_the_only_defence`).

---

## 3. Signing-secret rotation

Rotating a Meta app secret is not atomic on Meta's side, and in-flight requests
signed with the old secret keep arriving after the new one is in place.

| Id | Requirement | Bound by |
|---|---|---|
| **WAI-14** | The binding's secret refs carry an **ordered set** of active signing secrets, not one value plus a fallback. | `test_both_secrets_verify_during_a_rotation_window` |
| **WAI-15** | A request verifies if **any** active secret matches. | `test_both_secrets_verify_during_a_rotation_window` |
| **WAI-16** | Verification evaluates **every** active secret; no early return on the first match. | `test_verification_evaluates_every_active_secret` |
| **WAI-17** | A secret removed from the active set stops verifying immediately. | `test_a_retired_secret_is_refused_once_it_leaves_the_active_set` |
| **WAI-18** | The connector MUST NOT log which secret matched. It MUST emit a counter keyed by the secret's **position**, so the window can be observed draining. | § 3, below |
| **WAI-19** | Rotation is a new immutable config revision plus an explicit `refresh_secrets()`. Never a TTL, never a per-request fetch. | ADR-0009; Integrator secret resolver |

### The window, as an operator runs it

1. Add the new secret to the active set as a **new config revision**; the old
   one stays. Refresh explicitly.
2. Change the app secret in Meta's console. Traffic now arrives signed with
   either.
3. Watch the per-position counter (WAI-18). Both are non-zero; the old one
   decays.
4. When the old position has counted zero for a full provider retry horizon —
   a configured value, not a constant baked into the connector — publish a
   revision that drops it and refresh again.
5. The old secret now fails closed (WAI-17).

Step 3 is the whole reason WAI-18 exists. Without an observable counter, step 4
is a guess, and the two failure modes of guessing are opposite and both bad:
retire too early and you drop in-flight traffic Meta will not resend; retire
never and a leaked secret stays valid indefinitely.

### What WAI-16 is protecting against

Returning as soon as one secret matches makes response time reveal *which*
secret verified the request. During a rotation window that leaks how far the
rotation has progressed and when the old secret stops being accepted. The work
is bounded by the size of the active set, which the operator controls, so
evaluating all of them costs nothing worth having.

### Sub's fallback is not a rotation mechanism

`meta_inbox_webhooks._whatsapp_signature_fallback_secret` reaches into a
*different* connector's (`whatsapp`) secret material through a bare
`except Exception: return None` and tries it as a second signature. That is
cross-installation secret borrowing that happens to look like rotation. It has
no window, no ordering, no observability and no retirement step, and a failure
to load the fallback is silently indistinguishable from there being none. It
MUST NOT be inherited.

---

## 4. Stable event identities

Deduplication is only as good as the identity behind it. Meta groups events into
batches at its own discretion and may regroup them on retry, so identity must
come from the events, never from the request.

| Id | Requirement | Bound by |
|---|---|---|
| **WAI-20** | A message's identity is `wa:msg:{message.id}` — the provider's `wamid`. | `test_every_identity_is_recomputable_from_the_body` |
| **WAI-21** | A status's identity is `wa:status:{id}:{status}:{timestamp}`. The message id ALONE is not an identity. | `test_one_message_yields_distinct_identities_for_each_status` |
| **WAI-22** | Where Meta supplies no identity (a change-level `errors[]` item), one is derived from the **item's** canonical JSON, never from the request body. | `test_the_derived_error_identity_comes_from_the_item_not_the_request` |
| **WAI-23** | Every observation declares `identity_source` as `provider` or `derived`. | `test_an_identity_declares_whether_the_provider_supplied_it` |
| **WAI-24** | Identities are unique within one request. | `test_event_identities_are_unique_within_one_request` |
| **WAI-25** | An event keeps its identity when Meta regroups it into a different batch. | `test_an_event_keeps_its_identity_when_meta_regroups_the_batch` |
| **WAI-26** | The identity MUST NOT be derived from the raw request body. | `test_a_request_digest_identity_cannot_deduplicate_a_regrouped_event` |

Durable uniqueness is `(capability_binding_id, provider_event_id)` — the key
`receive_verified` already uses. Binding-scoping is what keeps two installations
independent.

### WAI-21: why a status needs more than the message id

One outbound message produces `sent`, then `delivered`, then `read`, each its
own callback carrying the same `id`. Keying on the message id collapses a
message's entire delivery history into one row where the last writer wins — and
because retries arrive out of order, "last writer" is not "latest status". The
corpus carries both callbacks for `wamid.outbound-1` for exactly this.

### WAI-26: the anti-pattern, currently live

`dotmac_sub/app/api/inbox_webhooks.py` computes

```python
provider_event_id=f"meta:{hashlib.sha256(raw_body).hexdigest()}"
```

one identity for the whole **request**. It deduplicates an exact retry and
nothing else:

* a message that reappears in a differently grouped batch has a different
  digest, so it is recorded twice;
* twenty good events inherit the fate of the one bad entry beside them, because
  the batch has a single identity and a single consequence;
* a batch whose only change is Meta's own grouping looks like new traffic.

`test_a_request_digest_identity_cannot_deduplicate_a_regrouped_event` puts the
same message in two fixtures with different request digests, and asserts the
event identity is nonetheless the same one.

### WAI-22: the derived rule, stated exactly

```
wa:error:{scope}:{sha256(canonical_json(item)).hexdigest()[:32]}

scope          = value.metadata.phone_number_id, else entry.id
canonical_json = json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")
```

Derived from the item, so the same error redelivered inside a different batch
still deduplicates. `identity_source: "derived"` tells a consumer that this
identity is weaker than a provider-assigned one — which is a fact worth carrying
rather than hiding behind an identical-looking string.

---

## 5. Batch normalisation

One Meta POST is
`entry[] → changes[] → value → {messages[], statuses[], errors[]}`. One request
becomes 0..N observations, so `normalize` returns a **tuple**; a single-event
signature would silently drop all but the first.

| Id | Requirement | Bound by |
|---|---|---|
| **WAI-27** | Every observation carries an RFC 6901 locator into the request document. | `test_every_declared_locator_resolves_in_its_fixture` |
| **WAI-28** | A structurally bad item becomes its own typed `whatsapp.entry.malformed.v1` observation with a locator and a reason code, and normalisation **continues**. | `test_a_malformed_entry_does_not_suppress_the_rest_of_the_batch` |
| **WAI-29** | A malformed item's observation carries a reason **code**, never a fragment of the request content. | `test_a_malformed_entry_does_not_suppress_the_rest_of_the_batch` |
| **WAI-30** | A message type the connector cannot interpret is observed with `reason_code: message_type_unsupported`, never dropped, and keeps the `wa:msg:` identity space. | `test_an_uninterpretable_message_type_is_observed_rather_than_dropped` |
| **WAI-31** | An empty batch normalises to zero observations and is a **successful** outcome. | `test_an_empty_batch_normalises_to_zero_observations` |
| **WAI-32** | `normalize` is pure: no network, no clock-dependent output, no session. | § 7 |
| **WAI-33** | Every observation from one request is recorded in **one** transaction. One collision rolls the whole batch back. | `whatsapp-connector-sources.md`, three-phase ingress |

### WAI-28 vs WAI-33 — these do not conflict

A malformed item is a **normalisation outcome**; a collision is a **recording
outcome**. Raising on a bad item would discard the good events beside it, and
Meta will not resend them. Rolling back on a collision is correct precisely
because the retry re-derives the same identities and `receive_verified` is
idempotent, so the already-seen events are recognised and the missing ones are
written.

### WAI-30: the silent drop, measured

Sub's receiver ends a message with

```python
body = _text_body(message)
sender = str(message.get("from") or "").strip()
if not sender or not body:
    continue
```

`_text_body` returns `""` for any type outside
`_WHATSAPP_QUALIFYING_MESSAGE_TYPES`, so a `reaction`, an `order`, a `system`
notification or any type Meta adds next year is discarded with no row, no
metric and no receipt — and Meta will not send it again. The fixture
`08_unsupported_message_type.json` exists to make that drop visible; it has no
counterpart in Sub's tests because Sub produces no output to assert on.

### Presentation is not normalisation

`_text_body` also returns `f"[{message_type}]"` — `"[image]"`, `"[location]"`,
`"[contacts]"` — placeholder strings for a product's message list. (Sub's social
receiver does the same, plus `"[quick reply]"`.) That is a **rendering**
decision made inside ingress. A connector normalises the media reference, the
mime type and the caption; what a UI shows when there is no text is the
receiving product's business. Likewise `is_echo` filtering: deciding which of
your own messages to ignore is a product rule, not a wire-format concern.

### Timestamps

WhatsApp sends **seconds**; Messenger and Instagram send **milliseconds**. Sub's
social receiver guesses by magnitude (`if timestamp < 100_000_000_000`). A
capability declares its unit; magnitude heuristics are refused. And a missing or
unparseable timestamp normalises to `None` with a reason code — never to
`datetime.now(UTC)`, which is what Sub does and which makes the same bytes
normalise differently on every replay, so conformance cannot be proven at all
(WAI-32).

---

## 6. Redelivery and tamper

| Id | Requirement | Bound by |
|---|---|---|
| **WAI-34** | A byte-identical redelivery verifies, normalises to the same identities, and records nothing new. | `test_a_replayed_valid_body_still_verifies_so_dedup_is_the_only_defence` + WAI-20..26 |
| **WAI-35** | A body altered by one byte fails verification. | `test_changing_one_byte_invalidates_the_signature` |
| **WAI-36** | A body signed with a secret no longer active fails verification. | `test_a_retired_secret_is_refused_once_it_leaves_the_active_set` |
| **WAI-37** | A partial redelivery — a subset or superset of an earlier batch — records only the events not already seen. | `test_an_event_keeps_its_identity_when_meta_regroups_the_batch` |

---

## 7. Boundaries the connector may not cross (ADR-0024 §§ 6–7)

Not a summary of the ADR — the specific ways *this* provider invites the
violation:

* **No product import, no product database.** Sub's helpers take a `Session`
  (`_verify_token(db)`, `_app_secret(db)`,
  `_whatsapp_signature_fallback_secret(db)`). The connector receives `config`
  and `secrets`, materialised at the boundary, and never a connection.
* **No business decision.** No subscriber matching, no phone-number
  normalisation into a product's identity space, no conversation threading, no
  channel-validity marking. Those are `receive_verified`'s consumers' work.
* **No provider I/O during ingress.** Sub's social receiver calls
  `fetch_contact_profile` — a Graph API call — inside the webhook, wrapped in a
  bare `except Exception: profile = None`. Ingress `verify`/`normalize` are
  pure functions of bytes, headers, config and secrets.
* **No retry or checkpoint engine.** Backoff, leases, dead-lettering and
  cursors belong to `dotmac-integration` (ADR-0030 § 8.1).
* **No HTTP vocabulary.** Sub raises `HTTPException(401)` / `(503)` from inside
  what would be connector code. A connector returns typed outcomes and stable
  error codes; status codes are the ingress edge's business.
* **Nothing sensitive in a diagnostic.** A signature in a log is a replay aid; a
  body in a log is customer message content in the observability stack; a
  `response.text[:2000]` in a stored output is the same thing by another route.

---

## 8. The corpus

`tests/fixtures/meta_whatsapp/`

| Path | What it is |
|---|---|
| `manifest.json` | The specification as data: keys, handshake matrix, identity rules, per-fixture provenance and expected observations. Hand-authored. |
| `bodies/*.json` | Eight request bodies. Signatures are over the exact bytes of these files. |
| `signatures.json` | Generated evidence — byte length, body digest and the header value under each test key. |
| `regenerate.py` | Rewrites `signatures.json`. Run after any deliberate fixture change. |

Six of the eight bodies are verbatim from Sub's production test suite; the
manifest records repo, path, symbol, line range, commit and blob for each, and
`test_every_fixture_records_where_it_came_from` fails a fixture that does not.
The two composed ones must say what was changed and why.

**Every key in the corpus contains the literal string `NOT-A-SECRET`**, and
`test_no_fixture_carries_credential_shaped_material` fails on `bao://`,
`env://`, `Bearer `, `access_token`, `EAAG` or `app_secret` appearing anywhere
under the fixture root. A real Meta app secret must never enter this repository;
a fixture that needs a signature gets one from the committed test key.

### When the gate opens

When ADR-0030 § 6 is amended with a named connector distribution, that
distribution's `normalize` runs against this same `manifest.json` and must
produce exactly the declared observations. Nothing in the corpus changes for
that to happen — which is the point of writing it before the implementation
exists.
