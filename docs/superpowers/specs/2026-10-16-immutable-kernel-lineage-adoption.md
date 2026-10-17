# Immutable Kernel lineage adoption: maintenance handoff proposal

Status: proposed, source-only design. No migration implementation, changed
reference-adopter decision, target selection, custody approval, data projection,
deployment authorization or passed adoption gate is asserted.

## Why this is on the learning extraction dependency path

`dotmac-learning` needs a published Kernel containing its namespace allocation.
Academy's a38 application/identity composition cannot consume the current
Kernel browser contract. Its existing identity/facet programme depends on the
Sub tenant-plane/RLS reference proof. Current Sub source still asserts the
expected failure at Kernel revision 0001; a green expected-failure test does not
close that gate.

Current source evidence is Sub `c98e2fa0708d7188fdc4d4d4e9ae7b8c48c645e0`
and Starter `2e0c04ce61c50c95472fb26ad1867d5099edb832`. Twelve relevant Sub
blob identities were compared with cached `a2b128a3504efaa964426782ab94bb84056cc11d`
and matched, including rules, owners, rehearsal and adoption ledger. Earlier
August staging observations are historical, not current deployed-schema proof.

Starter ADR 0017's amendments distinguish the completed Vendor CP general P11
gate from the remaining Sub tenant-plane/RLS reference gate. This proposal
retains that distinction and does not revive the general moratorium.

## Immutable history is a hard constraint

Kernel's published 0001 creates colliding populated relations unconditionally;
0003 has destructive credential/actor transitions and the transient
`party_roles` name. Later additive revisions cannot execute before a failing
root. The historical suggestion to amend adopted initial DDL conflicts with
the current released-migration guard.

The handoff must run the complete installed canonical Kernel lineage unchanged.
No released file edits, stamps, DDL interception, blanket existence skips,
manufactured heads or lower dependency floors can satisfy it.

## Proposed entry algorithm

Use an assembly-owned maintenance handoff, with the existing Party, credential,
RBAC, audit and transaction owners. A temporary assembly-owned escrow schema
holds the predecessor relations while Kernel creates its real target relations
in the vacated compatibility namespace. It is not a module allocation or a new
identity SDK.

The complete unit is fourteen current lineage-head overlaps plus transient
`party_roles`, not only the five revision-0001 collisions. Settings, consent,
machine credentials, idempotency and both outbox planes are part of the closure.
Every incoming FK, view/rule, trigger, function, composite row type, SQL caller,
privilege and current writer must be inventoried independently of the proposed
move list.

`ALTER TABLE ... SET SCHEMA` preserves relation identity and moves associated
objects; it does not redirect references to a new relation later created under
the old name. Relocate the exact old relay functions and their dependency
closure too. Sub's `claim_outbox_batch` and its platform counterpart return the
old outbox row types, and PostgreSQL cannot replace a function with a different
return type under the same signature. Unchanged Kernel 0011/0012 must create
the new public functions without that conflict.

Execution, once separately authorized, is ordered:

1. Prepare the explicit destination contracts, product-owned extensions,
   mapping/import participants and all caller changes. Publish/pin the actual
   artifacts through existing release owners.
2. Enter a proved maintenance window: deny and drain request writers, workers,
   schedulers, dispatchers and privileged helpers. The ordinary sequence that
   migrates while the predecessor serves is incompatible with this handoff.
3. In the existing owner's one database transaction, verify the bound plan,
   source/catalog/package identities, role posture and drained state. Acquire
   the complete deterministic lock set with bounded waits and reobserve under
   those locks.
4. Relocate the collision/dependency closure, revoke access to the escrow
   functions, and execute the entire installed unchanged Kernel lineage on the
   same connection with its genuine independent version history.
5. Apply the reviewed extensions and imports through existing owners; preserve
   stable UUID meaning and all accepted evidence. Rebind every live dependency
   to its final owner and verify effective constraints, privileges and RLS.
   After Kernel 0022 frees its temporary `party_roles` name, return Sub's
   continuing business-capacity authority to its reviewed final location or
   an explicit product extension. Preserve temporal/status/relationship facts
   and caller parity; this authority is not rollback-only escrow.
6. Before commit, verify populated authorization parity, canonical GUC,
   transactional catalog and role-context checks, the real Kernel head, no
   live escrow dependencies and sealed old writers. Stage a durable handoff
   receipt bound to the exact plan, then commit only if all checks pass.
7. Keep maintenance closed through independent post-commit checks, including
   genuine runtime-role logins with own, other, unset and malformed tenant
   contexts. Another connection cannot observe the uncommitted replacement
   schema; same-connection role simulation is not actual-login evidence. Clear old
   pools/prepared sessions, admit only compatible successor processes, and
   retain sealed escrow only for the explicitly reviewed recovery window.

Maintenance denial must survive executor and process restarts. Loss of a commit
acknowledgment leaves admission closed: recovery compares the transactionally
persisted receipt, exact plan identity, genuine heads and final catalog before
classifying the outcome. Never blindly repeat relocation, infer rollback from a
missing acknowledgment or automatically reopen traffic. Define the exact
recovery authority and keep acceptance failure closed.

Seal effective escrow mutations through table/column/PUBLIC grants, inherited
roles, callable helpers and cascades. Preserve predecessor ACL definitions as
recovery evidence while deliberately changing effective access. Prove that an
old writer is refused both after relocation and after commit.

The final ordinary deployment composition must recognize the genuine Sub,
Kernel and module histories and bind prerequisite providers truthfully. Hosted
proof must advance a later additive Kernel revision through that same owning
deployment path; executing Kernel once is not durable lineage adoption.

Static source contains no observed explicit commit/autocommit/concurrent-index
operation in the inspected Kernel chain. That supports a prototype; only hosted
execution of the installed artifact can establish actual transactional behavior.

## Destination and ownership decisions still required

| Source contract | Required work before import |
|---|---|
| Sub credentials include non-password providers, binding identity, standing, lockout/version and MFA context; Kernel password credentials have a non-null hash and a different contract | Explicitly classify and preserve the complete credential/authentication context through the existing owners and reviewed destination/extension contracts. No fake hash, inferred merge or discarded binding. |
| Sub audit preserves unknown historical persistence times and permits entity types wider than Kernel's 120 characters | Define lossless historical-time and width contracts. Preserve actor provenance and forensic values; never substitute occurrence or migration time for unknown persistence time. |
| Sub Party has quarantine, merge and classification decisions; Role has description and activation behavior | Use explicit owned extensions and migrate their actual readers/writers. Stable IDs alone do not prove semantic parity; universal active defaults are invalid. |
| Existing credential projection stages against the current mapped table | Reuse its validation/ownership through a reviewed import participant. The current committing public command cannot be nested inside this handoff. |
| Product database catalogue assumes Starter's assembly-head spelling, while Sub has numeric history | Extend the existing truthful host-lineage composition contract or compose a genuine adoption lineage with provenance. Never relabel the source head. |

Kernel's catalogue describes tables and columns. Separate evidence must establish
FKs, policies, grants, functions and dependency transfer; schema presence is not
that evidence. Preserve Sub's existing transaction authority and canonical
`app.current_tenant`; no new connection may silently escape the owner.

Staff Party authentication is already complete for its bounded slice. Broad
credential convergence remains shadowing with explicit collision, parity and
production-derived rehearsal conditions. Reuse current owners rather than
reopening the completed staff mapping or inferring broader adoption from it.

## Hosted proof and operational boundary

The prototype must execute the actual complete migration chain against populated
fixtures containing all fifteen colliding names, incoming FKs and both relay
return types. Include multiple provider contexts, unknown audit times, product
Party states, role grants, both outbox planes and genuine runtime roles. Plant
failure at each phase and prove relocation, imports, references, grants and
receipt all roll back together. Prove fresh installation and populated handoff;
an identity-only happy path is insufficient. Include lost commit acknowledgment,
receipt-based recovery/replay refusal and post-commit acceptance failure while
maintenance remains closed.

Extend the existing Sub rehearsal evidence owner to cover policy expressions,
function body/owner/security posture, effective table/column/PUBLIC grants and
role membership/posture. Its current V1 fingerprints columns, constraints,
indexes and RLS booleans, which is narrower than this handoff's obligations.
Retain the direct-overlay expected-failure test as diagnostic evidence and add
a separately named full-handoff success contract. Once the accepted adoption
path passes the full actual gate, reconcile the ratchet explicitly without
removing detection of an accidental direct overlay.

Fixtures cannot establish current deployed populations or authorize execution.
Operational use still needs a fresh named source and disposable target, exact
catalog/migration identity, current count-only convergence and caller parity,
deployed GUC/role observations, reviewed principal dispositions, capped expiring
digest-bound plans, attributable approval, approved minimized-evidence custody,
immutable artifacts and separately authorized deployment. No target or approval
is invented here.

Before commit, failure must roll back every phase. After commit and before new
writes, a verified reverse handoff may restore the exact predecessor. After
successor writes start, frozen escrow is stale: require approved forward repair
or a proved reverse migration preserving those new writes. An old image or
moving escrow back is not a safe automatic rollback.

Current Sub audit-attribution/query work (PRs #3635/#3632) and conflict-savepoint
composition (PR #3653) are integration inputs, not completed lineage evidence.
The existing owners and current immutable revisions must be revalidated before
implementing this proposal.
