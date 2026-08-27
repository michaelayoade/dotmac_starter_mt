# Changelog

## 0.1.0a1 (unreleased)

Initial NiRA `.ng` CoCCA-EPP connector.

- EPP-over-TLS transport (RFC 5734 framing, greeting, login/logout, result-code
  classification) on the Python standard library — no third-party EPP dependency.
- Command frames: domain check/info/create/renew/update-NS/transfer,
  host create/check, contact check, with the fee-1.0 extension on check.
- DELIVERY adapter with a per-capability operation allow-list checked for
  totality at import (no operation reachable through zero or two capabilities).
- Exact OT&E egress declaration and runtime allowlist, client-PEM plumbing,
  authenticated health validation, and phase-aware ambiguous-write handling.
- Conservative result classification: provider reads await the owning domain
  result schema, and 2302/2303 require identity reconciliation.
- POLL adapter over the registry message queue with two-phase consumption: a
  call returns one unacknowledged head, and only a later call may acknowledge
  that head after Integration supplies its durably persisted cursor. The typed
  observation retains serialized `resData`; configuration and login failures
  raise into Integration's durable failure/backoff ledger.
- Offline conformance kit: a plaintext fake EPP server plus focused delivery
  and polling safety canaries.

Mechanically held back from release. Not yet done: owning-domain command/result
contracts, DNSSEC update frames, production-host review, and authenticated live
OT&E proof (pending the source-IP whitelist for the Integrator host).
