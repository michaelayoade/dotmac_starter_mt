# dotmac-delivery-smtp

SMTP adapter for the kernel delivery-provider seam
(`dotmac_kernel.delivery_providers`), channel `email`. Standard library only.

- `SmtpDeliveryProvider` implements `DeliveryProvider` and declares the
  features (`html`, `attachments`) and header names it honours.
- **Settings** (`delivery_smtp` domain): host, port, starttls, username, from
  address, entered in the settings UI through the kernel settings mechanism.
  `resolve_config(db, production=...)` builds the `SmtpConfig` at PLATFORM scope
  only (a tenant row could otherwise redirect the platform password). No env file, no `env_var`.
- **Password** is not a setting. It is read by NAME from the kernel
  `SecretSource` (`dotmac_kernel.secret_sources`); the product binds its current
  custody to that seam. This package adds no secret path.
- **STARTTLS** is required in production (configuration is refused otherwise),
  the server certificate and hostname are verified, and the adapter refuses to
  send if the server does not offer STARTTLS. Credentials are never sent in
  plaintext.
- **MIME** is built from the `OutboundMessage` parts: text/html alternative,
  allowed headers only, attachments via an injected resolver
  (`AttachmentRef -> bytes`; no storage here). `Message-ID` is
  `<dispatch_id@from-domain>`.
- **Admission hook**: a callable invoked before any connection (the product
  passes e.g. its mail-gate lease). A refusal raises `SmtpAdmissionRefused`: nothing is connected and no
  receipt is written. S8 follow-up: kernel `DeliveryDeferred` (K-4).
- **Classification** is documented in `classify.py`: RCPT 5.1.1/2/3/6/10 `bounced`,
  5.7.x `rejected`, sender-side 5.1.7/5.1.8 and any MAIL-stage 5xx raise (operator fault), soft 5.2/5.4 and 4xx/timeouts/connection errors `failed`.
  A reply lost after the DATA payload raises `SmtpOutcomeUnknown` (SMTP has no
  idempotency key, so the kernel contract forbids returning `failed`).

Bounce coverage is synchronous only; asynchronous DSNs are not captured.
