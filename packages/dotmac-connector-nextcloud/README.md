# dotmac-connector-nextcloud

Stateless Nextcloud connector for the independently deployed Dotmac Integrator.
It publishes exactly the four managed-collaboration lifecycle contracts and
implements their `plan`, `apply`, `observe`, and `cancel` operations through
Integration SPI 1.2.

The package owns provider wire translation only. Integrator supplies immutable
configuration and materialized held secrets for one invocation; the connector
stores neither. Its HTTP transport enforces HTTPS, rejects local and non-global
targets after DNS resolution, never follows redirects, and distinguishes a
safe retry from an ambiguous mutating outcome.

`NextcloudConnector` accepts an injected `NextcloudTransport`, so conformance
and product acceptance use a deterministic transport without network access.
The metadata-discovered `PLUGIN` uses the package HTTP transport.

The required private facade contract is frozen in [PROTOCOL.md](PROTOCOL.md).
Its fixed routes are an activation prerequisite; the connector never falls back
to direct shell/`occ` execution or pretends standard OCS alone supplies host
lifecycle behavior.

Operation inputs contain desired product state only. `management_endpoint` and
the held `management_secret_ref` (plus `client_secret_ref` for OIDC) arrive in
the SPI configuration/material maps and are never copied into evidence.
