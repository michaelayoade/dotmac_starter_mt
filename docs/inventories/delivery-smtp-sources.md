# Delivery SMTP adapter: source inventory

Inventory for the `dotmac-delivery-smtp` extraction dossier
(`packages/dotmac-delivery-smtp/EXTRACTION.toml`). Measured 2026-11-18.

## Qualifying source: Academy

`dotmac_academy_app@fc8d15b686f6a60905c88ee6fc709fee0b7cf6b9`.

- `app/services/email.py` lines 43-98: the SMTP send (MIME build, optional
  STARTTLS, optional login, `send_message`, exceptions swallowed to a bool).
  The package keeps the MIME shape and replaces swallowing with classified
  kernel outcomes.
- `app/config.py` ~132: STARTTLS must be true in production when SMTP is
  configured (parity enforced here as a configuration refusal).
- `app/services/settings_store.py` ~180: the effective-settings view; the
  password is environment-only custody today. The package reads the password
  only through the kernel `SecretSource`; Academy binds its current custody.

## Kernel adjacency (not an equivalent)

The kernel ships the provider protocol and no client
(`dotmac_kernel.delivery_providers`). No SMTP client exists elsewhere in
Starter; this package is the first.

## Known limits

Bounce coverage is synchronous only (reply at RCPT/DATA). Asynchronous DSNs
are not captured. The not-attempted admission outcome is `failed` until kernel
`DeliveryDeferred` (K-4, S8) exists.

## ERP and Sub: inventoried, no shared writer

`dotmac_erp@74fa788` and `dotmac_sub@c770788` each hold their own smtplib
sender in `app/services/email.py` (pooled connections, per-sender settings).
They were read for shape only; neither is a qualifying source, neither is
asked to adopt, and nothing is retired there.

## Preserved tests

`dotmac_academy_app:tests/services/test_email.py` is the behaviour reference
for the Academy send path; the package's own tests use a local SMTP server.
