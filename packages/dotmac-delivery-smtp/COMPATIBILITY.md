# Compatibility - dotmac-delivery-smtp 0.1.0a1

- **dotmac-kernel >=0.1.0a109** (declared floor): `OutboundMessage`
  parts/headers/`AttachmentRef`, the header-declaring provider protocol and the
  receipt statuses. The REAL floor is the first kernel release containing S6
  (`c968075a`) and K-2 (`2ebfc761`); the workspace kernel is `0.1.0a109+dev`, so
  a higher floor would break the lock today. It MUST be raised in the kernel
  allocation PR before S12 (release).
- Standard library only (`smtplib`, `ssl`, `email`). No third-party dependency.
- STARTTLS submission only (no implicit-TLS port 465 mode in 0.1).
- SMTP settings are read at platform scope only (see `reader.py`).
- Timeouts: 15 s connect/command, 180 s for the final DATA reply (both must be
  > 0). **Lease coupling:** the kernel relay's `RelayPolicy.stale_lease_seconds`
  is 300; a second worker may reclaim the lease and double-send if one attempt
  outlasts it. `data_timeout` plus the other step timeouts (about 7 x `timeout`)
  must stay below `stale_lease_seconds`; raise the lease before raising these.
- Attachments: 10 MiB each, 20 MiB total; TLS 1.2 minimum on caller contexts.

## Governance ADR 0011: recorded transfer (Michael, 2026-11-18)

`provider.py` opens SMTP connections, so the engineering-standards
`outbound_transport` arm counts it. Michael explicitly approved loosening
Governance ADR 0011 for this one surface: Starter's
`.dotmac/standards-profile.json` `external_connector_surface.baselines.outbound_transport`
moves 0 -> 1, with no conserved exclusion (an exclusion is not viable because
the module is counted).

- The single counted module is `src/dotmac_delivery_smtp/provider.py`. A second
  SMTP user anywhere in Starter exceeds the baseline and fails CI.
- This is a TRANSFER, not new surface: it replaces Academy's direct SMTP sender
  (`app/email.py`, Academy `fc8d15b`). Academy's own `outbound_transport` count
  must be lowered with cutover evidence in Academy work package A4.
- Retirement target: a `dotmac-connector-smtp` distribution run by Academy's
  companion Integrator (Integrator topology ruling D10, 2026-11-18). Starter's
  baseline then returns to 0 with cutover evidence (ADR 0011 statement 4).
