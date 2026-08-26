# dotmac-connector-mailcow

Stateless Mailcow administrative-API connector for the independently deployed
Dotmac Integrator. It implements the product-owned `email.lifecycle.v1`
contract and contains no persistence, scheduling, retry engine, business
decision, generated-secret output, or product database access.

Version one deliberately refuses two operations that Mailcow's supported
surface cannot yet prove safely:

- browser SSO activation, until an immutable issuer/subject-to-mailbox mapping
  and same-email-recreation refusal pass the isolated acceptance kit;
- app-password creation, until a typed held-secret write boundary exists.

Domains and mailboxes are created from exact product-owned capacity, protocol
and relay policy. Mailboxes use Mailcow's supported `generic-oidc` auth source,
which deliberately accepts an empty local-password state; the request carries
no password-shaped field. Existing domains, mailboxes, aliases, quotas,
delivery flags and DKIM public records can be reconciled. App passwords can be
revoked by an approved opaque provider reference. The remaining refusals are
activation gates, not TODO-shaped green paths: the connector returns stable
terminal reason codes before unsafe I/O.

The browser-SSO refusal is structural evidence, not cautionary prose. In
Mailcow source revision `06424670fa5d60fee851f58bfc49f66086d5f0a6`,
`identity_provider('verify-sso')` selects a mailbox by the user-info `email`
claim and does not persist or compare issuer/subject. The suite standard is an
exact immutable `(issuer, subject)` binding, so configuring native SSO would
make a deleted-and-recreated same-email identity inherit the mailbox.

The default transport uses HTTPS, refuses redirects and environment proxies,
bounds time and response bytes, and never renders the API key or request body.
Tests inject a deterministic transport. Provider acceptance belongs on
disposable Seabone state and does not authorize production access.

The exact upstream source findings and activation boundary are recorded in
[`PROVIDER_INVENTORY.md`](PROVIDER_INVENTORY.md).
