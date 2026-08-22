# ADR-0029: Cross-application access is requested, approved and issued by three owners

**Status:** Accepted
**Date:** 2026-08-14
**Decision owner:** Michael
**Scope:** the Workspace access programme; the approval-ownership rule it applies
is FLEET-WIDE and already ADR-0026's.
**Amends:** [ADR-0021](0021-the-tenant-workspace-is-a-third-plane.md) §4
**Relates to:** [ADR-0026](0026-approvals-decide-approval-never-the-transition.md)
(the rule being applied), [ADR-0024](0024-apps-compose-by-synchronizing-data.md)
(apps compose by synchronizing data), [ADR-0017](0017-adoption-is-the-scarce-resource.md)
(the access module is still unbuilt and unauthorized).

## Context

Two accepted ADRs name a different owner for the same decision.

- **ADR-0021 §4** assigns `dotmac-application-access` the tenant-admin workflow
  for cross-application access, *including "request and approval"*. Written
  2026-08-12, when no approvals module existed.
- **ADR-0026**, accepted two days later and explicitly FLEET-WIDE for its module
  boundary, gives `dotmac-approvals` sole ownership of whether a required set of
  eligible actors has approved *this exact content* under *this exact policy
  revision*, answering `pending | approved | rejected | cancelled` and nothing
  else. It states plainly that a consuming domain does not implement a second
  approval lifecycle.

Left as they stand, approval state has two named owners the moment
`dotmac-application-access` is built. The conflict is currently harmless —
nothing implements either half — and that is exactly why it should be resolved
now, before code makes one reading true by accident.

The later, fleet-wide, more specific decision wins. ADR-0021 §4 was not wrong
when written; it was written before the owner existed.

## Decision

### 1. Three owners, named

| Owner | Owns | Does NOT own |
|---|---|---|
| `dotmac-approvals` | the approval **request** and the **decision evidence**: who approved what content under which policy revision, and whether that decision is still valid | what the approval is *for*; it never issues or revokes access |
| `dotmac-application-access` | **desired** cross-application access: grant-set issuance, delivery, acknowledgement, drift against applied state, and revocation | whether a request was approved; it never records a decision |
| the Workspace assembly | **routing and reaction**: selecting the policy revision, and calling application-access when an approval event arrives | neither module's internals; it holds no approval state and no grant state |

### 2. The sequence

```
application-access   creates the content-bound access subject
        ↓  (the assembly passes an already-resolved policy revision)
dotmac-approvals     decides pending | approved | rejected | cancelled
        ↓  outbox event
the Workspace        reacts, and calls…
        ↓
application-access   issues or refuses the AccessGrantSet
```

The digest binding is what makes this safe, and it is ADR-0026 §2's property
applied here: the approval is bound to the exact content of the access request.
Change the requested roles, the target application or the delegation, and the
digest changes, which makes the prior approval **stale rather than
transferable**. A new request is required. Without content binding, "approved"
would be a token that could be moved onto a broader grant than the one anyone
looked at.

### 3. Neither module imports the other

`dotmac-approvals` does not know an access grant exists; `dotmac-application-access`
does not know what an approval policy is. They compose through the ASSEMBLY, over
typed contracts and the outbox event ADR-0026 §6 already specifies — which is the
same shape ADR-0024 requires of applications, applied one level down to modules
that share a runtime but not a lifecycle.

This is what keeps `dotmac-approvals` optional. A product that wants
cross-application access without an approval step composes application-access
alone, and the assembly calls it directly. If access imported approvals, that
deployment would be impossible.

### 4. Policy SELECTION stays with the domain

ADR-0026 §7a's seam holds unchanged: the caller arrives having already resolved
`(policy_code, policy_version)`, and the approvals module never derives one. For
access, the resolver is the **Workspace assembly** — which application, which
target, which roles are delegable, and therefore which policy applies is
Workspace's routing decision, expressed in Workspace's vocabulary.

Neither shared module gains a threshold table, a role catalogue predicate, or any
other way to answer "which policy applies here". That is the mistake §7a refused
for finance and it is refused here for the same reason: a module that could
select its own policy would need the requesting domain's vocabulary.

### 5. ADR-0021 §4 is amended, not withdrawn

Everything else in that section stands — the permanent contract ownership, the
directory/access split, and above all §3's rule that **directory visibility is
not authorization**. Only the words "request and approval" in the
application-access row are superseded: `dotmac-application-access` owns the
REQUEST as an access subject (what is being asked for, by whom, over which
binding) and owns nothing about the DECISION.

The distinction is not a quibble. Owning the subject means access-module tables
hold the requested roles and the requester; owning the decision would mean
holding approver identities, quorum arithmetic and terminal state — a second
implementation of ADR-0026's module, inside a module that is not allowed to have
one.

## Consequences

- `dotmac-application-access` gains, when built: the access subject and its
  content digest, desired-state issuance, `AccessGrantSet`, delivery
  acknowledgement, drift and revocation. It gains no approver, no quorum, no
  policy version and no decision history.
- The Workspace assembly gains an event handler and a policy-selection rule. It
  remains the only place the two modules meet.
- `dotmac-approvals` gains nothing at all. Access is one more subject type a
  consuming module declares (ADR-0026 §4: subject types are declared on the
  consuming module's manifest as an ADR-0008 registry), which is the extension
  point working as intended rather than a change.
- **Nothing here authorizes building `dotmac-application-access`.** ADR-0021 §5
  defers it until a generic signed-document mechanism exists, ADR-0021 §8
  sequences that extraction after the lineage gate, and ADR-0017's moratorium is
  unlifted for it — the 2026-08-14 amendment covering the identity seams and
  `dotmac-auth-oidc` is explicit that it does not extend here. This ADR settles
  the boundary so the module is not designed twice; it does not start it.

## Alternatives rejected

**Let `dotmac-application-access` own approval, per ADR-0021 as written.** It
would re-implement eligibility, quorum, self-approval exclusion, delegation
provenance and terminal state — every property ADR-0026 measured across two
products and consolidated. The second implementation is the one that will not
receive the next fix, and its approval records would not be comparable with the
fleet's.

**Let `dotmac-approvals` issue the grant when it approves.** The obvious
convenience, and ADR-0026 §6 already refused it in general terms: a module that
executed the consequence would be deciding a transition it does not own, and
would need application-access's vocabulary and a second writer on its tables.
The outbox event gives the same automation with the ownership intact.

**Have the two modules talk directly, skipping the assembly.** An import in
either direction makes both un-releasable without the other and makes
approvals-free access impossible. The assembly is the only place that legitimately
knows both exist.

**Leave the conflict and resolve it when the module is built.** Cheap now,
expensive later: the first implementer would read whichever ADR they found first,
and a wrong choice would be discovered after there were rows in a table.
