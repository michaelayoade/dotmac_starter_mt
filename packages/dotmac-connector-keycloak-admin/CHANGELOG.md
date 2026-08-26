# Changelog

## 0.1.0a1 — unreleased

- Add realm-scoped Keycloak realm, OIDC confidential-client and stable-reference
  user reconciliation.
- Enforce caller-supplied held client material, RS256, S256, exact redirects and
  audience mapping without a generated-secret output channel.
- Add bounded no-redirect/no-environment-proxy HTTPS transport and stable
  terminal, retryable and ambiguous outcome mapping.
- Disable users with explicit provider-session logout and return public
  issuer/subject evidence without email linking, provider roles or credentials.
