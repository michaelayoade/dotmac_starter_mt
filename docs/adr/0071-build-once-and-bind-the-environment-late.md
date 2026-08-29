# ADR-0071: Build the software once, bind the environment late

- Status: Proposed
- Date: 2026-08-29
- Deciders: Michael
- Supersedes: none
- Extends: ADR-0070 (deployment is a stateless versioned foundation — this ADR
  states the property that facility's descriptor and rendering contract must
  hold), ADR-0006 (build-once ownership; this applies it to a surface with no
  rows), ADR-0009 (a secret is held, never dereferenced)
- Related: ADR-0003 (composable deployment profiles), ADR-0018 (a guard
  exemption states an enforceable premise), ADR-0024 (applications are
  independent), `packages/dotmac-deployment-control` (durable fleet intent and
  authorization), `packages/dotmac-deployment-foundation` (execution)

## Context

Four Dotmac repositories deploy four different ways, and the observability host
deploys in a fifth way that is not written down anywhere. ADR-0070 gave the
mechanism one owner. It did not state the property that owner exists to
preserve, and a mechanism without a stated property is a mechanism the next
change can quietly break.

The property is visible in what goes wrong when it is absent. Three measured
examples, all from this fleet:

**A tag is not a release.** The observability host runs seven images pinned to
`:latest`. What ran yesterday and what runs after tonight's restart are two
different deployments with one description, and no artefact anywhere records
which was which.

**An environment fact compiled into shared code is a fact nobody can change.**
`trusted_proxies` held CIDRs in a product descriptor. That list decides whose
`X-Forwarded-For` is believed; it differs per environment, and a stale entry
silently makes a spoofed header authoritative. Nothing failed when it went
stale, because a value in Git does not expire.

**Configuration rendered on the host is configuration nobody approved.** The
observability host's live configuration is hand-edited and unversioned: no
version control on `/opt/observability`, 26 unordered `.bak` files serving as
the rollback mechanism, and a hand edit on 2026-08-29 that no gate could refuse
and no receipt records.

These are the two failure modes at either end of one axis, and both are
avoidable at once. Hardcoding one environment into the shared artefact makes
the artefact wrong everywhere else. Rendering arbitrary mutable configuration
on the host makes the running state unattributable. The escape is to make the
artefact carry no environment fact at all, and to bind the environment as a
separately produced, separately signed input at authorization time.

## Decision

### 1. The contract

**Build the software and its policy ONCE, into an immutable artefact carrying
no environment fact. Bind a separately signed environment inventory at
AUTHORIZATION time. Deploy the deterministic result by EXACT DIGEST.**

```
source ──▶ immutable artefact  ┐
                               ├─▶ deterministic rendered configuration
   signed private inventory ───┘        │
                                        ▼
                              authorization (binds every digest)
                                        │
                                        ▼
                                      host
```

### 2. What the immutable artefact contains, and what it must not

**Contains:** schemas and typed models; templates and renderers; the alert and
policy catalogues; conformance evidence; the promotion and rollback manifest;
**exact upstream image digests, never a tag**; the expected service roster and
the digests of the configuration files it renders.

**Must not contain:** a production endpoint, an IP address or CIDR, a host
identity, a credential value — or a credential FILENAME. A basename is a
binding, not a value, and a redaction sweep that covers only value-shaped
material will pass over it.

### 3. What binds late

Environment target identities; resolved endpoints and address families; source
allowlists; authentication material; routing destinations; retention and sizing;
environment-specific topology. Stored privately and referenced **by digest**.

A product declares NAMES — a source set, a material, an endpoint role — and the
fleet-intent owner resolves those names to values. A product repository that
holds the values has taken ownership of a fact it cannot keep current.

### 4. The authorization binds all of it, in one document

Release digest, private-inventory digest, rendered-configuration digest, exact
container image digests, target, approver and rationale.

The load-bearing argument: a deployment is assembled from four independently
produced things, and **any three of them agreeing proves nothing about the
fourth**. The release digest and the rendered digest stay separate fields
rather than collapsing into one, because the bundle is what was built once and
the render is that bundle plus one environment — recording only one makes it
impossible to say afterwards whether a difference came from the software or
from the environment, which is the question this arrangement exists to answer.

### 5. The scope boundary — read this before applying the rule

This binds **deployable software and operational configuration**: release
artefacts, container images, rendered deployment and ingress configuration,
infrastructure policy, and the deployment authorization itself.

It does **NOT** bind, and must not be cited against:

- **tenant data** — rows a tenant owns, created and changed at runtime;
- **domain decisions** — a subscription state, a work-order transition, a
  payout: these have their own owners under ADR-0024 and the source-of-truth
  standard, and "bind late" says nothing about them;
- **databases** — schema and data are governed by the migration rules, not by
  this one;
- **logs and metrics** — observations, produced at runtime by construction;
- **ordinary product settings** — the tenant-scoped settings-as-data surface
  (ADR-0011, ADR-0012) is a runtime read of a row, and deliberately so.

The boundary is not a hedge. A rule stretched over runtime data would forbid
the settings resolver, the audit log and every domain state machine in the
fleet, and a rule that forbids the system it governs gets disabled rather than
narrowed. Stated the other way: this rule governs what is DEPLOYED, never what
is RECORDED.

### 6. What this refuses

- an image reference that is a tag rather than a digest;
- an environment address, CIDR or host identity inside a product repository's
  deployment declaration;
- a deployment asset rendered on the target host rather than rendered
  deterministically and compared byte-for-byte;
- an authorization that names a subset of the four digests;
- a "bind late" claim over tenant data, domain state or product settings — the
  scope boundary is part of the rule, not commentary on it.

## Consequences

Products declare more and resolve less, which moves work to the fleet-intent
owner and is the intended direction: one place that knows the environment, many
places that do not.

Two digests must be produced where one used to be, and both must be recorded.
A pipeline that records only the release digest satisfies the letter of "deploy
by digest" and loses the ability to attribute a change, so it does not satisfy
this ADR.

An artefact that carries no environment fact cannot be tested against a real
environment by inspection alone. That cost is real and is paid by the execution
half: the facility must re-observe the host and compare, rather than trusting
that a correct artefact produced a correct socket.

## Enforcement

**None yet, and that is recorded rather than implied** (ADR-0018). No guard in
this repository reads another repository's release pipeline, and the property
spans repositories by construction. What exists today is partial and local:

- `dotmac-deployment-foundation` refuses a tag in `image.reference`
  (`_DIGEST_REF`), refuses a secret value at parse time (`secrets_guard`,
  ADR-0009), refuses an address literal anywhere in an ingress declaration
  (`IngressPolicy.v1`), and `render --check` is a byte comparison a product's
  own CI runs;
- `DeploymentDescriptorDocument.v1` carries the exact facility version and
  excludes resolved endpoints, addresses and credential bindings, enforced over
  the finished document with a planted-address proof;
- `dotmac-deployment-control` owns the plan digest and the approval binding.

What is NOT enforced: that a product's pipeline produces the four digests, that
the authorization names all of them, and that an environment fact has not
entered a repository this facility does not read. Those are review discipline
until a profile can pin and check them, and the gap belongs in a product
profile rather than in a guard that would have to be believed without evidence.
