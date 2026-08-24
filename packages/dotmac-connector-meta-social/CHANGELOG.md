# Changelog

## 0.1.0a2 — unreleased

Declared as a2, not folded into a1. a1 IS published (peeled tag
`dotmac-connector-meta-social-v0.1.0a1` points at commit
`c1921f6c4b9360c8b0005bddbf85e04e86d29a73`), and the delivery slice below adds
a capability id, per-capability modes, three secret bindings, two egress hosts
and an SPI 1.4 floor — every one of them inside the manifest digest an
installation adopts by.

- The exact a1 ingress manifest is preserved as `INGRESS_MANIFEST`, so an
  installation pinned to digest
  `61b5ba0e929bec924af4a338408a10db1035efef1589b7abaaf3152586b734e4` resolves to
  a known contract. `docs/inventories/released-manifest-digests.json` records
  that digest and `make manifest-digest-check` fails if it ever moves.
- Add `messaging.send.v1` in `ConnectorMode.DELIVERY` beside the existing
  `messaging.receive.v1` ingress capability, and map each capability to its own
  mode so an engine cannot reach the wrong factory.
- Add Facebook Messenger and Instagram Direct sends, porting Sub's exact
  per-channel hosts, credential bindings and wire encodings — including the
  compact JSON-string recipient/message pair Instagram Login requires and the
  `messaging_type: RESPONSE` Messenger field.
- Add Facebook and Instagram comment replies on the parent comment's own Graph
  edge (`/comments` and `/replies` respectively), ported from Sub's Meta Pages
  adapter.
- Classify Graph responses by ERROR CODE before HTTP status: documented
  throttles (`4`, `17`, `32`, `341`, `613`) and transient failures (`1`, `2`)
  are retryable even under a 4xx; messaging-policy refusals — the closed
  window among them — and invalid material are terminal; a read timeout after
  the request started is `RECONCILIATION_REQUIRED` and is never retried.
- Raise the SPI floor to `>=1.4,<2.0` and the `dotmac-integration` floor to
  `0.1.0a14`; declare exactly `graph.facebook.com` and `graph.instagram.com`
  as egress, replacing the ingress-only deny-all set.
- Require an explicit Graph API version, auth mode and timeout for a send
  binding. Sub's `v21.0` fallback and its silent normalization of an unknown
  auth mode are deliberately not ported.
- Return only typed outcome status, the provider message reference and the
  numeric HTTP status. Graph `error.message` prose is never read or retained.
- Not ported, deliberately: the messaging-window decision and the permission to
  respond (owned by the product), Sub's `preview` dry run (a delivery `Outcome`
  has no field to return a rendered payload), and contact profile lookup (the
  DELIVERY contract has no result body, so a lookup has nowhere to return one
  — see `EXTRACTION.toml`).

## 0.1.0a1 — 2026-08-20

Published, installed back from the private index, conformance-checked and
tagged from exact main SHA `c1921f6` by release run `32361197839`.

- Add exact-byte Meta app signature verification with bounded secret rotation.
- Add subscription challenge handling.
- Normalize Facebook Messenger, Instagram DM, Facebook comment and Instagram
  comment batches into independent provider events.
- Keep echoes, unsupported changes and malformed items as record-only transport
  evidence instead of silently dropping them.
- Declare exact secret bindings and deny all external egress.
