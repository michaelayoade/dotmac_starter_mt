# Mailcow provider inventory

Audited against the maintained `mailcow/mailcow-dockerized` source revision
`06424670fa5d60fee851f58bfc49f66086d5f0a6` on 2026-08-17. This file records
provider facts used by the connector; it does not assign product authority to
Mailcow.

## Supported administrative boundary

- `data/web/api/openapi.yaml` declares `/api/v1` domain, mailbox, alias,
  app-password and DKIM operations authenticated by `X-API-Key`.
- `data/web/json_api.php` maps JSON add/edit/delete requests to the matching
  product services. A response can be HTTP 200 while carrying `danger` or
  `error`, so transport status alone never proves success.
- `data/web/inc/functions.mailbox.inc.php` accepts mailbox
  `authsource=generic-oidc` when the installation uses that source and clears
  local password state for non-Mailcow authentication. The connector can
  therefore create a password-free mailbox without generating or transporting
  a password.
- Domain creation requires aliases/mailboxes and three quota limits. The
  connector supplies them only from the exact managed-email desired document;
  it does not inherit Mailcow template defaults. Backup MX and both unknown/all
  recipient relay modes are explicitly false.
- DKIM generation keeps private material in Mailcow/Redis. The connector reads
  and emits only selector, public TXT record and its digest.

## Browser SSO is not admissible

`data/web/inc/functions.inc.php` `identity_provider('verify-sso')` obtains the
user-info document, requires its `email`, and looks up the mailbox with
`WHERE username = :user`. It neither persists nor compares OIDC issuer and
subject. When `login_provisioning` is enabled it can create a mailbox from that
email.

That is incompatible with the managed identity invariant: authentication is
an exact case-sensitive `(issuer, subject)` binding, while email is mutable
profile data. A deleted identity recreated with the same email could inherit
the old mailbox. The connector therefore returns
`immutable_subject_mapping_unverified` before provider I/O for the application
resource, and the suite orders every mailbox/domain resource behind successful
application activation.

The gate can close only with maintained Mailcow behavior that durably binds and
checks issuer/subject (including same-email recreation refusal), plus isolated
browser acceptance. Enabling native auto-provisioning or translating `sub`
into an email-like username is not equivalent evidence.

## Still unsupported in version one

App-password creation returns newly generated credential material. Integration
has no approved secret-output/write boundary, so creation fails
`secret_write_boundary_required`. Revocation by an already approved opaque
provider reference is supported. No generated credential, DKIM private key or
API key enters evidence, exceptions or receipts.
