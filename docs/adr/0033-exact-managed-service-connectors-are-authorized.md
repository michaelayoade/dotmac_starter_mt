# ADR-0033: Exact managed-service connector distributions are authorized

- **Status:** Accepted
- **Date:** 2026-08-17
- **Decision owner:** Michael
- **Amends:** [ADR-0030](0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
  section 6, which previously authorized connector dossiers and contracts but
  no real connector distribution
- **Related:** [ADR-0024](0024-apps-compose-by-synchronizing-data.md),
  [ADR-0032](0032-integrator-executes-approved-provisioning-commands.md),
  [ADR-0034](0034-managed-capability-contracts-are-separate-product-artifacts.md),
  [managed-service connector sources](../inventories/managed-service-connector-sources.md)

## Context

ADR-0030 deliberately blocked every connector distribution until the
Integrator held secrets, the initial providers were selected, and a later
decision named exact packages. The held-secret resolver now exists, the
managed-service provider set is known, and the Rule-24 inventory records which
behaviour is product-first and which is greenfield-after-inventory.

Sequencing is still not authorization. This decision supplies the exact names
ADR-0030 required; it does not waive a product owner's contract, conformance,
release, or adoption gate.

## Decision

### 1. Seven distributions, and only these seven, are authorized

| Distribution | Provider/wire boundary | Contract that must exist before implementation |
|---|---|---|
| `dotmac-connector-contabo` | the current AS51167 IaaS provider plus its authoritative DNS and PTR APIs | Vendor CP's typed fleet/IaaS desired-state contract and the Domains-owned `dns.authoritative.v1` contract |
| `dotmac-connector-keycloak-admin` | Keycloak Admin REST for realm, OIDC-client and stable-reference user lifecycle | the managed-service identity realm/client/user contract; application login continues to use `dotmac-auth-oidc` and each product's exact issuer/subject binding owner |
| `dotmac-connector-mailcow` | supported Mailcow domain, mailbox, alias, quota and delivery APIs | the managed-email lifecycle contract and its provider-free conformance kit |
| `dotmac-connector-nextcloud` | Nextcloud OCS, `user_oidc`, user/group/quota and file-verification APIs | the managed-collaboration lifecycle contract and its provider-free conformance kit |
| `dotmac-connector-dotmac-erp` | ERP's versioned service API only | ERP-owned application, person/external-binding and session-revocation ports; never admin-bypass routes or database access |
| `dotmac-connector-dotmac-academy` | Academy's versioned service API only | Academy-owned application and learner/external-binding ports; learning decisions stay in Academy |
| `dotmac-connector-dotmac-host-agent` | the mutually authenticated constrained deployment-agent protocol | the closed deployment-bundle contract (including upgrade/update), backup/restore and health-probe contracts plus their target-agent conformance kit |

No wildcard is granted. Another provider, product, control panel, generic
automation runner, registrar, DNS service, or host tool requires its own
inventory and an amendment naming its exact distribution.

### 2. Capability families remain independently bindable

A distribution may implement more than one family without merging their
bindings. `dotmac-connector-contabo` may implement both IaaS and authoritative
DNS/PTR, but a customer can replace DNS without replacing compute and vice
versa. Credentials, activation, health, configuration revision and selected
binding remain independent per capability family.

The DNS capability is exactly **`dns.authoritative.v1`**. Like every capability
executed through Integration SPI 1.2 it declares the engine operations `plan`,
`apply`, `observe`, and `cancel`. Its typed request and result schemas carry the
provider-neutral DNS resource kinds `zone`, `recordset`, and `observation`.
PTR is expressed through the typed recordset/observation shape; it does not
create a Contabo-named capability or a second DNS owner.

This distinction is load-bearing: capability operations are the transaction and
replay protocol the Integration engine invokes, while resource kinds are the
domain objects one such operation plans, applies or observes. Calling the DNS
resource kinds operations would produce an owner contract the SPI correctly
refuses at connector discovery.

The same rule applies to application lifecycle, backup/restore and update when
they have genuinely distinct provider, credential, release or failure
boundaries. A lifecycle is not split per verb merely to make an API method a
capability.

### 3. Owner contracts and conformance come first

This authorization becomes executable for one distribution only after all of
the following are checked in and released:

1. the named domain/product owner declares the capability id, version, typed
   inputs, typed outputs and supported operations;
2. that owner ships a provider-free fake and port conformance suite;
3. the distribution passes the released `dotmac-integration` SPI conformance
   suite, including plan/apply/observe/cancel where it provisions;
4. ambiguous provider outcomes settle to reconciliation-required and cannot be
   blindly replayed;
5. configuration names only immutable secret references, while Integrator
   materializes the held values outside the database transaction; and
6. the distribution's own tests include planted sensitivities for provider
   leakage, secret leakage, redirect/endpoint safety, replay, collision,
   idempotency and unsupported operations.

Connector source imports the Integration SPI and its exact product-owned
contract catalogue only. The SPI and catalogue may depend on Kernel's canonical
value grammar, but a connector does not import Kernel directly; that would turn
the narrow plugin API into access to unrelated local runtime authorities. The
release gate derives the allowed catalogue import roots from
`source_dependencies` and rejects every other direct Dotmac import.

A connector is stateless: it owns no table, migration, schedule, retry engine,
approval, product status, destination scope, or business decision.

### 4. Version one has no secret-output channel

Version one consumes **pre-created held secret material**. The immutable
configuration holds a secret reference; Integrator resolves it only for the
selected invocation. A connector never generates a client secret, password,
app password, API token, private key, or recovery code and returns it through a
receipt, evidence document, result, exception, or log.

If a provider cannot accept caller-supplied material and can only return a new
secret, that operation is unsupported in version one. Adding a typed secret
write boundary requires a separate decision; serializing the value into
provisioning evidence is never an interim solution.

### 5. The host agent is closed, not a remote shell

Vendor CP supplies an approved plan and Integrator invokes only a versioned,
allowlisted bundle operation supported by the target agent. No request,
configuration field, endpoint, receipt or plugin API may carry arbitrary shell
text, an argv vector, an SSH command, a startup script, executable bytes, or a
generic file-and-run instruction. Provider-specific host behavior belongs in a
versioned agent bundle with typed inputs, typed evidence and bounded
compensation.

`dotmac-connector-dotmac-host-agent` is the Integrator plugin for that closed
protocol. It is not authority for the agent's allowlist, package trust,
privileges or rollback policy.

### 6. Seabone is acceptance infrastructure only

Seabone is the isolated target for the first end-to-end conformance and failure
rehearsal. Acceptance must use disposable state, non-production identities and
non-production DNS names, and must prove cleanup. Passing on Seabone does not
authorize production access or deployment, does not make Seabone a fleet
control plane, and does not replace a released connector artifact plus its
recorded conformance evidence.

## Consequences

- Connector implementation may begin independently after its own owner-port
  gate is green; the seven packages do not need one integration branch or one
  release train.
- Keycloak proves identity only. ERP, Academy, Workspace and Nextcloud retain
  their own users, authorization, sessions and exact external bindings.
- Mailcow and Nextcloud remain replaceable transports; Vendor CP owns desired
  managed-service state and Integrator owns command/operation/receipt evidence.
- Contabo provider names, SKUs, endpoints and wire statuses remain inside its
  connector. Provider-neutral IaaS and DNS contracts contain none of them.
- This decision authorizes code review, not production mutation. Production
  access still requires Michael to name the target host explicitly.

## Alternatives rejected

**One generic infrastructure connector.** It would move provider branching and
wire mapping into the shared engine and make replacement a code change rather
than a binding change.

**One generic SSH connector.** A signed arbitrary command remains arbitrary
remote code execution. Approval provenance does not make an unbounded command
surface safe.

**Return generated secrets in evidence and move them later.** Evidence is
durable and replicated; this would turn the receipt chain into a secret store
and violate the held-secret boundary at the first successful client creation.

**Bind Contabo compute and DNS as one capability.** The current provider happens
to supply both, but they have independent replacement, credential and failure
boundaries. Combining them would make a DNS migration require an IaaS cutover.
