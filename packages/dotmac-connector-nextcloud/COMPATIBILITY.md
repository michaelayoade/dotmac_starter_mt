# Compatibility

`dotmac-connector-nextcloud` 0.1.0a1 requires:

- `dotmac-integration >=0.1.0a6,<0.2.0` and SPI `>=1.2,<2.0`;
- `dotmac-managed-collaboration-contracts >=0.1.0a1,<0.2.0`;
- a Nextcloud management endpoint reachable only by HTTPS and resolving solely
  to globally routable addresses from the Integrator network; and
- pre-created held authorization material. The connector cannot create or
  return a password, client secret, app password, token, key, or recovery code.

The connector refuses redirects. A connect failure is retryable; a timeout or
transport break after a mutating request may have reached Nextcloud and is
reported as ambiguous for reconciliation rather than replayed blindly.

Application backup/restore/upgrade/suspension/decommission and the complete
OIDC security observation require a bounded Nextcloud management surface that
returns the exact owner-schema evidence. Standard OCS provisioning endpoints
alone do not satisfy those operations; enablement must fail until isolated
acceptance proves that surface and every contract activation check.
