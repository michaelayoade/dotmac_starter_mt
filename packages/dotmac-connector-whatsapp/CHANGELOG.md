# Changelog

## 0.1.0a4 — unreleased

Approved-template catalogue and attachment validation. Both gates run BEFORE
the wire call, because both refusals are ones the connector already holds the
facts to make and paying a provider round trip for them turns a content error
into what looks like a transport fault.

- Adds `messaging.templates.read.v1` — Sub's production capability id, not a new
  one — in `ConnectorMode.POLL`. It reads the WABA's message-template catalogue
  through the paging cursor and emits one `whatsapp.message_template.v1`
  observation per (name, language), carrying the provider status, approval,
  category and the parameter counts derived from the BODY, HEADER and URL
  buttons. The identity is content-derived, so an unchanged catalogue redelivers
  ids the inbox already holds and a status change arrives as a new fact.
- `send_template` now refuses, with a typed terminal outcome and no wire call, a
  template that is absent from the catalogue (`template_not_found`), approved
  only in another language (`template_language_unavailable`), not approved
  (`template_not_approved`, carrying the provider status as the reason), or
  supplied with a parameter set the catalogue does not describe
  (`template_variable_arity_mismatch` — checked per component, so a body,
  header or indexed button parameter cannot be silently omitted).
- The catalogue is memoized in process under an explicit freshness policy:
  fresh is served, cold and stale both re-read, and a failed read EVICTS and
  refuses the send. There is deliberately no stale-while-revalidate and no
  serve-stale-on-failure — Meta withdraws an approval without telling the
  sender, so serving a cached one past its TTL is exactly the assumption this
  gate exists to remove. `template_cache_ttl_seconds` (default 300, Sub's
  number; `0` disables reuse) and `template_page_size` are per-binding knobs.
  The cache key is scoped to the WABA, API version, template name and a
  per-process HMAC fingerprint of the access token, so one installation's
  answer is never served to another.
- A catalogue read failure is classified as Sub classifies it:
  `template_provider_unavailable` (retryable) for transport, including a read
  timeout — a GET has no effect to duplicate, so it is not ambiguous —
  `template_provider_retryable` for 429/5xx with the provider's `retry-after`,
  `template_provider_rejected` for any other non-2xx, and
  `template_response_invalid` for a body that is not the catalogue contract. A
  catalogue longer than the connector will follow fails rather than truncating.
- `waba_id` is now REQUIRED on a `messaging.send.v1` binding. The pre-flight
  gate has no fail-open branch, so an installation that cannot name its account
  cannot be told whether a template is approved.
- Adds attachment validation against Meta's per-type contract: supported MIME
  sets per media type, per-type size limits (image 5 MiB, document 100 MiB,
  audio 16 MiB, video 16 MiB) as the documented defaults of a `media_limits`
  configuration object, and the caption/filename rules — caption on
  image/document/video only (1024 characters), filename on document only (255).
  A configuration may NARROW a limit and never widen one; the schema's maximum
  is the provider's own number.
- Two ported behaviours change deliberately, both recorded in `EXTRACTION.toml`.
  An over-long caption or filename is now REFUSED rather than truncated, and a
  caption on audio is refused rather than dropped: editing product content to
  fit a provider constraint is a decision belonging to whoever wrote the
  message. And the `application/octet-stream` upload default is retired —
  Meta accepts that type for no media kind, so every upload it produced was
  streamed in full and then rejected. The `attachment` filename default stays.
- Internal only: the Graph host, client construction, typed refusals and HTTP
  outcome classification move to `wire.py` so the send, the upload and the
  catalogue read cannot disagree about retryability.
- The exact a3 manifest is preserved as `DELIVERY_MANIFEST`, so an installation
  pinned to the published a3 digest resolves to a known contract instead of an
  unknown one.

## 0.1.0a3 — released

Peeled tag `dotmac-connector-whatsapp-v0.1.0a3` points at commit
`70459efd468dd2dcc9e31693b9910b04fec21447`. The heading below read
"unreleased" after the tag was cut; it is corrected here rather than left as a
claim that the artifact does not exist.

- Adds `messaging.send.v1` in `ConnectorMode.DELIVERY` while retaining the
  exact published a1 and a2 ingress manifests.
- Ports Sub's text, template and media Graph wire shapes and provider response
  classification through an injected HTTP transport.
- Requires an explicit Graph API version, phone-number id and timeout for a
  send binding; the connector provides no ageing API-version fallback.
- Returns only typed outcome status, provider message reference and numeric
  HTTP status. Provider response bodies and exception text are never retained.

## 0.1.0a2 — 2026-08-19

Published, installed back from the private index, conformance-checked and
tagged from exact main SHA `fb9aea0` by release run `32236093441`.

- Targets `dotmac-integration` SPI 1.3 and floors on its published `0.1.0a10`
  release.
- Declares the exact primary, previous-rotation and handshake secret bindings
  used by the executable ingress handler.
- Declares explicit deny-all provider egress; this ingress-only connector does
  not contact the provider.
- Retains the exact published a1 manifest and its configuration behavior during
  the bounded adoption window.

## 0.1.0a1 — 2026-08-17

- First ingress-only connector for `messaging.receive.v1`.
- Exact-byte HMAC verification with ordered rotation evidence.
- Provider handshake and full-batch message/status/error normalization.
