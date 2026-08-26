# Compatibility

## 0.1.0a1

- Implements Integration SPI 1.2 through `dotmac-integration >=0.1.0a6,<0.2.0`.
- Implements the exact `dotmac-managed-email-contracts 0.1.0a1`
  `email.lifecycle.v1` snapshot and schema bytes.
- Uses Mailcow's supported `/api/v1` administrative routes with `X-API-Key`.
- Treats a write timeout, network loss, empty mutation response or unknown
  mutation envelope as ambiguous; it never blindly replays such an outcome.
- Creates domains only from explicit owner-supplied capacity and no-backup-MX /
  no-unknown-recipient-relay policy.
- Creates password-free mailboxes through the supported `generic-oidc`
  authentication source and explicit protocol-access policy.
- Refuses browser-SSO activation and app-password creation until their
  explicit safety gates are satisfied.
