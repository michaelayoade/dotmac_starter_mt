# Compatibility - dotmac-delivery-smtp 0.1.0a1

- **dotmac-kernel >=0.1.0a109** (declared floor): `OutboundMessage`
  parts/headers/`AttachmentRef`, the header-declaring provider protocol and the
  receipt statuses. The REAL floor is the first kernel release containing S6
  (`c968075a`) and K-2 (`2ebfc761`); the workspace kernel is `0.1.0a109+dev`, so
  a higher floor would break the lock today. It MUST be raised in the kernel
  allocation PR before S12 (release). S8 (K-4) adds one more: `SmtpAdmissionRefused`
  subclasses `dotmac_kernel.delivery.DeliveryDeferred`, which exists only from
  the first kernel release containing S8 (migration `0033_receipts_append_only`);
  the declared floor is deliberately NOT raised in S8 (a later step raises it),
  so installing this package against an older kernel fails at import.
- Standard library only (`smtplib`, `ssl`, `email`). No third-party dependency.
- STARTTLS submission only (no implicit-TLS port 465 mode in 0.1).
- SMTP settings are read at platform scope only (see `reader.py`).
- Settings specs are NOT registered on import. The composing product calls
  `dotmac_delivery_smtp.settings.register()` once at composition (idempotent),
  alongside installing the module so its `delivery_smtp` setting domain is
  declared. Importing the package alone leaves process-global settings state
  untouched.
- Timeouts (both must be finite and > 0): 15 s per operation and 40 s for
  `data_timeout`, which is reset to 15 s as soon as DATA returns. **Lease
  coupling:** the kernel relay's `RelayPolicy.stale_lease_seconds` is 300; a
  second worker may reclaim the lease and double-send if one attempt outlasts
  it. `data_timeout` is in force for THREE waits inside `smtplib` `data()` (the
  354 go-ahead, the payload send, the final reply) and up to 11 other waits get
  `timeout` (TCP connect, greeting, EHLO, STARTTLS reply, TLS handshake, EHLO,
  AUTH up to 2 replies, MAIL, RCPT, QUIT). Nominal budget: 11 x `timeout` +
  3 x `data_timeout`, which must stay below `stale_lease_seconds`; the defaults
  give 11 x 15 + 3 x 40 = 285. These are per-operation budgets, not a
  wall-clock limit (a server trickling bytes resets each one). A caller that passes
  explicit timeouts must keep inside that budget; it is NOT enforced here. Hard
  enforcement (a construction-time refusal, or K-8 lease renewal / an attempt
  watchdog) is a follow-up. Raise the lease before raising these.
- Attachments: 10 MiB each, 20 MiB total; TLS 1.2 minimum on caller contexts.

## Governance ADR 0011: recorded transfer (Michael, 2026-11-18)

`provider.py` opens SMTP connections, so the engineering-standards
`outbound_transport` arm counts it. Michael explicitly approved loosening
Governance ADR 0011 for this one surface: Starter's
`.dotmac/standards-profile.json` `external_connector_surface.baselines.outbound_transport`
moves 0 -> 1, and `provider.py` is counted (it cannot be excluded). Michael also approved,
on 2026-11-18, one fingerprint-pinned conserved exclusion for the TEST
`tests/unit/test_delivery_smtp.py::test_data_reply_uses_the_long_timeout`;
changing that test changes its fingerprint and needs fresh approval.

- The single counted module is `src/dotmac_delivery_smtp/provider.py`. A second
  SMTP user anywhere in Starter exceeds the baseline and fails CI.
- This is a TRANSFER, not new surface: it replaces Academy's direct SMTP sender
  (`app/email.py`, Academy `fc8d15b`). Academy's own `outbound_transport` count
  must be lowered with cutover evidence in Academy work package A4.
- Retirement target: a `dotmac-connector-smtp` distribution run by Academy's
  companion Integrator (Integrator topology ruling D10, 2026-11-18). Starter's
  baseline then returns to 0 with cutover evidence (ADR 0011 statement 4).
