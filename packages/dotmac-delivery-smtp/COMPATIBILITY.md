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
