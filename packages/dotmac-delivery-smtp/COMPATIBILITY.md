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
- Timeouts: 15 s connect/command, 600 s for the final DATA reply.
- Attachments: 10 MiB each, 20 MiB total; TLS 1.2 minimum on caller contexts.
