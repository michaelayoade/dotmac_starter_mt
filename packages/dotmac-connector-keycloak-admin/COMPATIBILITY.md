# Compatibility

`dotmac-connector-keycloak-admin` 0.1.0a1 requires:

- `dotmac-integration >=0.1.0a6` and SPI `>=1.2,<2.0`;
- `dotmac-managed-identity-contracts ==0.1.0a1`.

It declares only `ConnectorMode.PROVISION` and exactly
`identity.realm.lifecycle.v1`, `identity.oidc-client.lifecycle.v1` and
`identity.user.lifecycle.v1`.
`admin_secret_ref` resolves to the connector's exact service-account JSON shape
(`client_id`, `client_secret`); token exchange and every Admin REST call remain
inside the selected non-master realm.

The user lifecycle is preprovisioned-only. It correlates by the stable
`dotmac.identity_ref` attribute, never email, emits exact issuer/subject
evidence, creates no credential value, assigns no product role and treats a
disable as incomplete until provider logout succeeds.

The first release remains blocked until kernel 0.1.0a69, Integration 0.1.0a6
and the managed-identity catalogue 0.1.0a1 are published and verified. A
checkout-only dependency is not an installable release floor.
