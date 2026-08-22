# Changelog

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
