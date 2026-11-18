# Compatibility - dotmac-delivery-smtp 0.1.0a1

- **dotmac-kernel >=0.1.0a109**: `OutboundMessage` parts/headers/`AttachmentRef`,
  the header-declaring provider protocol and the receipt statuses.
- Standard library only (`smtplib`, `ssl`, `email`). No third-party dependency.
- STARTTLS submission only (no implicit-TLS port 465 mode in 0.1).
