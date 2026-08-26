# Changelog

## 0.1.0a1 (unreleased)

Initial NiRA `.ng` CoCCA-EPP connector.

- EPP-over-TLS transport (RFC 5734 framing, greeting, login/logout, result-code
  classification) on the Python standard library — no third-party EPP dependency.
- Command frames: domain check/info/create/renew/update-NS/transfer,
  host create/check, contact check, with the fee-1.0 extension on check.
- DELIVERY adapter with a per-capability operation allow-list checked for
  totality at import (no operation reachable through zero or two capabilities).
- POLL adapter over the registry message queue; the cursor advances only past
  messages the registry confirmed dequeued.
- Offline conformance kit: a plaintext fake EPP server exercising framing,
  result mapping and SPI mode discipline. 20 tests.

Not yet done: DNSSEC (secDNS) update frames, and live OT&E proof (pending the
source-IP whitelist for the Integrator host).
