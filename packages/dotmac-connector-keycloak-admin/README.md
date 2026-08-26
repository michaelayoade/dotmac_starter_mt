# dotmac-connector-keycloak-admin

Stateless Keycloak Admin REST connector for the independently deployed Dotmac
Integrator. It implements exactly `identity.realm.lifecycle.v1`,
`identity.oidc-client.lifecycle.v1` and `identity.user.lifecycle.v1` in
`PROVISION` mode.

The connector has realm-scoped authority only. A realm must already exist; the
connector can reconcile its public issuer and RS256 posture but cannot create a
realm through Keycloak's global `/admin/realms` endpoint and refuses the
`master` realm. OIDC clients are created or reconciled beneath the selected
non-master realm with Authorization Code, S256, RS256, exact redirect URIs and
an audience mapper.

Users are found only through the exact `dotmac.identity_ref` attribute, never
through username, email or display name. A new user receives provider-owned
email-verification and password-enrolment required actions without any
credential value crossing this connector. It delivers Keycloak's one-time
action email once per explicit enrollment revision and preserves that marker
across later mutable profile updates. Public evidence returns the stable
issuer and provider user id as `subject`. Disabling first reconciles the user
disabled and then invokes the realm-scoped logout endpoint; an uncertain
post-mutation result is ambiguous and must be observed before retry.

Integrator supplies `admin_secret_ref` and `client_secret_ref` as already-held
material for one invocation. The admin reference resolves to an exact JSON
service-account credential with `client_id` and `client_secret`; the connector
exchanges it only at the selected non-master realm's token endpoint and never
accepts master-realm credentials. The client reference resolves to the secret
the caller created for the managed confidential client. The connector neither
generates nor reads a client secret from Keycloak and returns only owner-schema
public evidence. Its real transport refuses redirects and environment proxies,
accepts only safe HTTPS base endpoints and realm-scoped paths, and bounds time
and response bytes.

`capability_instance_ref` remains an Integrator orchestration-envelope identity.
It is deliberately absent from the Identity owner's operation schemas and from
the Keycloak wire payload: the connector implements the signed capability step,
while Integration owns instance selection, binding and receipts.

The package requires three currently unreleased first-party artifacts:
`dotmac-kernel` 0.1.0a69, `dotmac-integration` 0.1.0a6 and
`dotmac-managed-identity-contracts` 0.1.0a1. It is therefore intentionally
absent from the connector release allowlist until that dependency train is
published and verified.
