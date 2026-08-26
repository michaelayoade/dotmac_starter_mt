# dotmac-managed-suite-contracts

Immutable, value-free mappings between exact capability operation schemas.
Product owners define what an operation accepts and returns; this catalogue
defines which public, non-secret output may satisfy which downstream input in a
managed-suite deployment.

Version `0.1.0a1` carries six compositions:

- `managed-suite.identity-federation.v1` maps the exact realm `apply` result's
  `/issuer_url` into each confidential OIDC client and user, and `/realm_ref`
  into each user.
- `managed-suite.identity-account-federation.v1` maps the identity user's
  stable `/identity_ref`, exact `/issuer_url` and immutable `/subject` only
  into explicitly selected Managed Collaboration user instances.
- `managed-suite.email-federation.v1` supplies the exact client id and issuer
  evidence only to Managed Email instances whose held input declares
  `resource_kind=application`.
- `managed-suite.email-application-dependencies.v1` makes the successful
  application instance's public `application_ref` a prerequisite for every
  explicitly mapped domain/mailbox/alias/quota/delivery/app-password/DKIM
  instance. This is the owner-signed ordering edge; Vendor never infers it
  from a capability name.
- `managed-suite.collaboration-federation.v1` supplies the same exact public
  client evidence to Managed Collaboration's `user_oidc` input.
- `managed-suite.email-dns.v1` supplies public DNS requirements only from
  explicitly mapped domain/DKIM instances to authoritative-DNS recordset
  instances.

The mapping pins both owners, capability/schema versions, operation codes,
schema references, schema digests, RFC 6901 pointers, and optional closed
instance selectors. Selectors are schema-checked owner contract literals, not
runtime customer values. Every edge also declares its exact coverage axis:
identity/application dependencies cover every matching target exactly once,
while email DNS covers every matching domain or DKIM source exactly once.
Importing the package
cross-checks them against exact `0.1.0a1` identity, email, collaboration and
domains catalogue dependencies, including public/non-secret classification and
type/format parity.
The externally owned documents used for that check are exported separately as
`COMPOSITION_DEPENDENCY_CONTRACTS` and `COMPOSITION_DEPENDENCY_SCHEMAS`. They
are verification evidence, not suite-owned capabilities, and therefore never
appear in the suite Product Manifest or its empty `CAPABILITY_CONTRACTS` /
`CAPABILITY_SCHEMAS` tuples.

## Deliberately absent

There are no ERP, Academy, Workspace, infrastructure or host mappings in this
release. Those owner artifacts either remain product-local or expose no exact
public-output to declared-input relation yet. Edges are added only after both
ends exist; this package never invents another owner's fields to make a
deployment plan look complete.

This package contains no runtime evidence value, provider client, connector,
network call, persistence, migration, secret, retry engine, or deployment
decision. Vendor CP selects the exact composition artifact. Integrator resolves
the later value from a signed upstream receipt and injects it only through the
approved binding.
