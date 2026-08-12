# ADR-0021: The tenant workspace is a third plane

**Status:** Accepted
**Date:** 2026-08-12
**Decision owner:** Michael
**Relates to:** ADR-0003 (deployment profiles and the product assemblies),
ADR-0004 (the platform control plane), ADR-0006 (extraction and module
lineages), ADR-0007 (deployment-authenticated applied state), ADR-0015 (an
assembly, or it is a fork), ADR-0017 (adoption is the scarce resource)

**Numbering note.** This ADR took 0021 to leave 0020 for *Billing owns
operational receivables*, accepted the same day. That file is **untracked** at
the time of writing, and an untracked file reserves nothing — a number is
authoritative only once it is committed. So this number is provisional until
Billing's ADR-0020 lands, and Billing's ADR-0020 must land **first**. If it does
not, this ADR is renumbered to 0020 rather than leaving a permanent gap in the
sequence.

## Context

Dotmac has two planes today. The **vendor control plane** represents Dotmac's
own operators and owns accounts, offers, approvals, contracts, allocations,
licensing, provisioning and its console. The **product data planes** — Sub, ERP,
Academy, this starter — each own their domain and their own effective
authorization.

The customer does not have a plane. An ISP operator who is a tenant of three
Dotmac applications has no place to answer the one question that spans them:
*which of my people may use which of my applications.* That question is real,
it is asked by a customer administrator rather than by a Dotmac operator, and
today it has nowhere to live. So it lands in one of two wrong places:

1. **In the vendor control plane**, because that is already where "which
   applications does this customer have" is decided. This makes Dotmac's
   operator console the authority for the customer's staff access.
2. **In whichever product has the best admin UI**, which makes one data plane
   the authority over its siblings and violates the standing fleet rule that
   Dotmac applications integrate through APIs and webhooks only — never by one
   owning another's identity.

Both are ownership violations. The first is also a security failure, and it is
the one this ADR is mainly about.

### The trust boundaries are genuinely different

| Plane | Represents | Owns |
| --- | --- | --- |
| Vendor control plane | Dotmac / vendor operators | contracts, licences, deployments, commercial application availability |
| Tenant Workspace | the customer / operator | their cross-application administration **intent** |
| Target application | the running product | its effective local roles, permissions and domain data |

```
Vendor CP ── signed app entitlements ──> Tenant Workspace
                                               │
                                     access allocations
                              ┌────────────────┼───────────────┐
                              v                v               v
                            Sub          Backoffice         Academy
                      local enforcement local enforcement local enforcement
```

Authority flows down that diagram and never up. A target application never asks
the Workspace whether a request is authorized; it decides for itself, from its
own tables.

### The containment invariant

> **A vendor-control-plane compromise must not automatically grant access
> inside customer applications.**

This is the requirement the whole design exists to satisfy, and it is not
satisfied by good intentions about who calls what. It is satisfied structurally,
by three properties that each have to hold on their own:

- The vendor plane's signed artefacts convey **commercial availability**, never
  person-to-role assignment. An attacker holding the vendor signing key can
  make an application *available* to a tenant. That is a billing problem, not a
  breach of the tenant's data.
- The Workspace and the applications share **no database, no session, no cookie
  and no guard**. A stolen Workspace session is not a session anywhere else.
- The target application is the **only writer of its own effective role
  grants**, and it validates every allocation against its own catalogue before
  writing anything.

Remove any one and the invariant fails. That is why the ADR states three
separate decisions rather than one.

## Decision

### 1. `dotmac_workspace` is an independent ADR-0003 assembly

Its own repository, database, sessions, cookies, guards and deployment profile.
It is not a vendor-control-plane surface, not a feature of a product assembly,
and not a second schema inside anything.

Under ADR-0015's fleet-wide rule it composes `dotmac_kernel.create_app` rather
than hand-building a FastAPI application — the whole point of that ADR is that
an assembly which builds its own app silently declines every control the kernel
performs inside `create_app`, and a plane whose job is a security boundary is
the worst possible place to rediscover that.

Why not inside the vendor control plane: the trust boundary above. Why not a
feature of the starter or of Sub: a workspace living inside one target
application would make that application the authority over its siblings, which
is the second wrong place from the Context.

### 2. Three questions, three owners — and they are not the same question

This is the semantic distinction the rest of the design hangs on, so it is
stated as a decision rather than left as commentary:

- **The vendor control plane issues which applications the tenant commercially
  owns.** Entitlement. Signed, delivered, verified locally, projected into the
  receiver's own grants — the shape ADR-0007 and WS8 already establish.
- **The Workspace administers which of the tenant's people and groups may enter
  those applications.** Intent, and only intent.
- **Each target application evaluates its own roles and permissions.**
  Enforcement, from its own tables, at request time.

The binding corollary:

> **The vendor control plane must never become the authority for tenant users.**

Concretely: a contract that carries commercial entitlement and person-or-group
assignment in the same signed document is prohibited. If a future
`AccessGrantSet` is ever drafted that way, it is split before it lands — one
document for what the tenant is entitled to, a separate one for who may enter.
Merging them would put the vendor's signing key in the path of the customer's
staff access, which is precisely the containment invariant inverted.

### 3. Directory visibility is not authorization

A binding in the application directory records that the tenant *has* an
application. It says nothing whatever about whether the person looking at the
screen may enter it.

> **The launcher renders a link. The target application authenticates and
> authorizes whoever follows it.**

Stated as a decision because it is the single most likely misreading of the
directory module, and because getting it wrong breaks the containment invariant
from the *Workspace* side rather than the vendor side. If following a launcher
tile ever produced access that the target application had not itself decided,
the Workspace would have become an identity provider for its siblings — the
thing decision 2 exists to prevent, arrived at from the other direction.

The directory is therefore an inventory with lifecycle, freshness and
reconciliation state. It is not an access control list, it holds no grants, and
it must never acquire a column that reads like one.

### 4. Two modules, with permanent contract ownership

| Contract | Permanent owner |
| --- | --- |
| `ApplicationDescriptor` | `dotmac-application-directory` |
| `AccessGrantSet` | `dotmac-application-access` |
| `AccessGrantApplicator` | `dotmac-application-access` |
| generic signed-envelope / keyring mechanism | a future kernel extraction from `licensing` |

**These three application contracts are not temporary module code awaiting
kernel promotion.** A contract belongs with the domain that defines it, and
`ApplicationDescriptor` is meaningless outside a portfolio of connected
applications. Only the generic cryptographic mechanism in the fourth row is a
kernel-promotion candidate, because it is the only one of the four whose
meaning does not depend on this domain at all.

`dotmac-application-directory` owns the tenant's connected-application
portfolio: the binding to a Workspace tenant, the logical application code and
instance reference, the target's local tenant reference, admin URL and API
audience, the descriptor version and digest, the binding lifecycle (invited,
pending verification, active, suspended, detached), the binding source
(vendor-managed allocation, OEM allocation, customer-attached), and descriptor
freshness with reconciliation status. It does **not** own the deployment, the
product catalogue, the entitlement or the remote application; each of those
stays a reference to its owner.

`dotmac-application-access` owns the tenant-admin workflow for cross-app
access: request and approval, the member-to-binding-to-role-codes allocation,
delegation policy and role allowlist, the versioned content-bound grant set,
delivery, acknowledgement, refusal and revocation, desired-versus-applied
drift, idempotent reconciliation and the official timeline. It records desired
allocation and acknowledgement. It is **not** runtime authorization, and the
target application remains the only writer of its effective role grants.

### 5. This wave lands the directory only

1. This ADR.
2. `dotmac-application-directory`, with its namespace allocation.
3. The Workspace launcher, built so that decision 3 is visible in the code and
   not only in this document.
4. **Deferred:** `dotmac-application-access`, signed grant sets, the applicator.
5. The access module's namespace is allocated **when its complete consumer
   slice starts**, not now. An allocation is cheap to add and permanent once
   added; allocating ahead of the slice would put a schema name in the
   fleet-wide ledger with nothing behind it.

### 6. Why access is deferred: there is no correct way to sign it yet

`AccessGrantSet` is specified as signed, and the kernel already contains a
proven DSSE-style Ed25519 envelope with domain separation, a closed-world
keyring and fail-closed verification. It is not reusable as it stands:

- the envelope verifier is **private** and hard-wired to the
  `dotmac-licence-envelope/1` schema
  (`packages/dotmac-kernel/src/dotmac_kernel/licensing.py:1268`);
- the **public** operation parses a licence-specific payload
  (`packages/dotmac-kernel/src/dotmac_kernel/licensing.py:1424`).

That leaves exactly three moves, and all three are wrong:

- **Do not import the private verifier.** A private name is a name with no
  compatibility promise; a second consumer of it converts every future
  licensing refactor into a cross-module break.
- **Do not disguise an access grant as a licence.** Different issuer, different
  trust anchor, different audience. Reusing the licence schema would mean a
  vendor-issued licence key and a workspace-issued access key verify documents
  in the same namespace — the containment invariant, defeated by a wire format.
- **Do not copy the envelope implementation.** That is a second writer of a
  security mechanism, and the copy is the one that will not receive the next
  fix.

The right move is a generic signed-document mechanism in the kernel, which
ADR-0017 blocks today. So the access slice waits for the mechanism rather than
shipping on a wrong one. Deferring a module is cheap; unpicking a wire format
in the field is not.

### 7. ADR-0017's exception is not available here

ADR-0017 §2 permits a facility that a live adoption *asks for*: "'A product will
need this' is not demand; 'a product is blocked on this today' is"
(`docs/adr/0017-adoption-is-the-scarce-resource.md:99`).

Two greenfield consumers arriving together does not meet that test, and this
ADR records that explicitly because the temptation is structural rather than
occasional: **any** new programme can produce two consumers of its own making
and present them as demand. If that counted, the exception would swallow the
moratorium, and every future programme would carry the same argument. The
blocked product has to be one that exists independently of the change asking
for the exception.

The one kernel change this wave does make is the directory's row in
`MIGRATION_OWNER_LEDGER`. That is a namespace **allocation**, not a facility —
it adds no behaviour and nothing consumes it but the module it names. This is
the same change `dotmac-ticketing` made after ADR-0017 was accepted.

### 8. After the lineage gate: extract a generic signed-document mechanism

When the moratorium lifts, extract the envelope, keyring and digest layer from
`licensing` into a kernel mechanism, **preserving the existing licence wire
bytes** — the installed base holds signed licences, and an extraction that
changes what verifies is a re-issue programme rather than a refactor. Licensing
and application access then consume it with **distinct schemas and distinct
domain separators**.

The domain separator is not decoration. ADR-0007 already records why: a
protocol that has a key holder sign caller-influenced bytes becomes a forgery
oracle for every other protocol sharing its domain. Two document families under
one separator would let a document of one kind verify as the other.

## What the Workspace does not own, and does not build

Recorded because each of these has an owner already, and a workspace is exactly
the kind of cross-cutting surface that attracts re-implementations:

- **No proprietary identity provider.** Federated login is external OIDC. ERP
  holds the fleet's only implementation — Authorization Code with PKCE,
  discovery and JWKS validation, `(issuer, subject)` binding to a local person,
  external roles deliberately ignored, a local session issued only after
  validation.

  Whether that makes it the **mandatory product-first source** under ADR-0006 is
  **conditional and currently unresolved**, and this ADR does not assert it.
  `app/config.py:133` reads `OIDC_ENABLED` with a default of `false`, so whether
  any production deployment authenticates through it is a fact on the ERP host
  rather than in git:

  - **enabled in production** → ERP is the mandatory product-first source; the
    extraction ports that implementation and its four boundary tests, and ERP is
    the first cutover;
  - **not enabled anywhere** → there is no production implementation,
    `product-first` does not apply, and the work is
    `greenfield-after-inventory` *informed by* ERP's code — a materially weaker
    claim about the design being proven.

  Resolving it requires reading `OIDC_ENABLED` on the ERP production host, which
  requires that host to be named explicitly. See
  `docs/inventories/oidc-sources.md`, which this paragraph must not outrun.

  The extraction itself waits on the ADR-0017 lineage gate either way; the source
  audit and the parity suite do not, and have started.
- **No global Party or person database.** Each application keeps its own.
- **No shared session or cookie service.** Decision 1 forbids it.
- **No shared application database.**
- **No generic data-federation module.**
- **No second audit, messaging, inbox, idempotency, permissions or entitlement
  mechanism.** The kernel owns all six.
- **No tenant-portal domain module.** The portal is the `dotmac_workspace`
  assembly's UI facet, not a domain.
- **No deployment, product catalogue, entitlement or remote-application
  ownership.** Those stay references to the fleet, licensing and application
  owners.

Application connectivity, when the Workspace needs API or event transport, is
**not** a new connector registry. Sub already holds version-pinned integration
installations, immutable configuration revisions, capability bindings, event
subscriptions, inbox/delivery state and checkpoints
(`dotmac_sub:app/models/integration_platform.py`), and that is the product-first
source for a future `dotmac-integrations` module once its production adoption is
verified.

## Consequences

- The customer-facing cross-application question finally has an owner, and the
  vendor control plane is explicitly relieved of it. That is a narrowing of the
  vendor plane's authority, not an addition to it.
- **The Workspace launcher ships before there is any access workflow behind
  it.** A tenant administrator can see their portfolio and follow a link;
  granting a colleague access to a target application is still done in that
  application. This is a real functional gap for a wave, and it is the honest
  consequence of decision 6 — the alternative was shipping a wire format we
  would have to unpick.
- `dotmac-application-directory` ships with one consumer, the Workspace. Under
  ADR-0017 §1 that is work in progress until the Workspace runs it, and the
  module's dossier says so rather than claiming adoption it has not earned.
- A fourth plane's worth of operational surface — deployment, backups,
  monitoring, an on-call story — arrives with `dotmac_workspace`. Assemblies are
  not free, and this ADR is also a decision to carry that cost.
- The fleet acquires a second application that must never be allowed to become
  an identity provider. Decision 3 is the guard, and it needs a test in the
  Workspace repository rather than only a paragraph here.
