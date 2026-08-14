# ADR-0026: Approvals decide approval, never the transition

**Status:** Accepted
**Date:** 2026-08-14
**Decision owner:** Michael
**Scope:** FLEET-WIDE for the module boundary; the module itself is optional.
**Relates to:** ADR-0006 (product-first extraction, D1 namespaces),
ADR-0008 (declaration registries, never enums), ADR-0017 (adoption is the
scarce resource — its 2026-08-14 amendment carries the owner-directed
exception that authorises implementation, see § 8), ADR-0023 (dual-plane modules
declare both persistence planes), ADR-0024 (apps compose by synchronizing
data), ADR-0025 (the shape this decision follows), hard rule 11 (tenant-scoped
tables), hard rule 24 (product-first extraction), hard rule 27 (dual-plane
declaration).

## Context

The 2026-08-14 A1 source audit
([`approvals-workflow-source-audit.md`](../inventories/approvals-workflow-source-audit.md),
row ledger [`approval-workflow-dispositions.toml`](../inventories/approval-workflow-dispositions.toml))
measured the fleet family previously called `governance-workflow` and found it
was a **prefix bucket, not a capability**: its 24 rows resolve to eight distinct
owners. Only five rows share one contract — ERP's `approval_workflow`,
`approval_request` and `approval_decision`, and Vendor CP's `approval_policies`
and `approval_records`.

That finding is the whole reason this ADR is narrow. A `dotmac-workflow` module
drawn around the bucket would have fused approvals with a trigger-condition-action
rules engine, a form builder, HR disciplinary case records, IFRS disclosure
state and an orphaned work-item table — five capabilities with five owners,
sharing nothing but a naming prefix.

Within the approval subset, ERP and Vendor CP each built a different half:

- **ERP** has the production tenant-plane lifecycle — threshold selection with
  FX conversion (which stays ERP's — see § 7a), ordered approval levels,
  per-level quorum, user/role eligibility, segregation of duties, delegation
  provenance, MFA evidence, a decision history, and a real domain consumer
  (fixed-asset GL reconciliation) that refuses its own consequence until
  approval exists. Its policy row is
  **mutable**, it has **no idempotency** on submit or decision, its three tables
  carry an `organization_id` with **no RLS**, and its service calls `commit()`
  and raises HTTP exceptions.
- **Vendor CP** has the safety identity — immutable `(policy_code, version)`
  revisions, content-hash binding, fail-closed evaluation when a policy or
  version is missing, kernel idempotency, distinct-actor quorum and
  self-approval exclusion — and almost no lifecycle at all. Its
  `ApprovalRecord` is one approver's approval of one `(subject, content_hash)`:
  there is no request id, no requester, no rejection, no cancellation and no
  terminal state. `ApprovalPolicy` carries a quorum and a self-approval flag,
  and no levels, eligibility or routing.

Neither is a superset. Merging by similarity would produce a module that is
weaker than either source in the dimension that source got right.

## Decision

### 1. The module owns one question

`dotmac-approvals` owns exactly this decision:

> Has the required set of eligible actors approved **this exact content** under
> **this exact policy revision**, and is that decision still valid?

It answers `pending | approved | rejected | cancelled` and nothing else. It does
not approve an invoice, activate a contract, post a journal, deploy a release,
or change any subject's lifecycle. Approving a payment does not post it: Finance
still decides whether an approved payment may be posted, and remains the only
writer of its own tables.

Approval records are **evidence**. They are never a second writer of the
subject, and the module never imports a consuming domain's models.

### 2. Approval binds to a content digest, not to a row

A request carries `subject_type`, `subject_id`, `policy_code`, `policy_version`,
`content_digest` and `requested_by`. The digest is what was approved. Changing
the amount, the payload or any other approved field produces a different digest,
which makes the prior approval **stale** rather than transferable — a new
request is required.

This is Vendor CP's property, and it is a mandatory port delta over ERP, whose
request references a mutable document by id alone.

### 3. Policy revisions are immutable and fail closed

A policy is selected by stable `policy_code` plus an **exact** version. Versions
are immutable, and a policy change never reinterprets an existing request. A
required policy or version that is missing makes approval **unavailable** — it
is never implicitly satisfied.

ERP's current behaviour, where a missing workflow can mean "no approval
required", is not ported. That is a fail-open default, and the audit records it
as one.

### 4. Subject types are declared; a policy code is data

ERP's `document_type` examples and its automation `WorkflowEntityType` enum are
finance and HR vocabulary that happen to live inside a generic file. They are
not a fleet vocabulary and do not enter the module.

The line runs between what CODE owns and what an OPERATOR owns, and the two
halves get opposite treatment.

**Code-owned, therefore declared.** A subject type names a kind of thing a
module has — an invoice, a release, a reconciliation package. Only a code change
can introduce one, so consuming modules declare their subject types on their
module manifests as ADR-0008 declaration registries, and a service or boot gate
rejects an undeclared reference. The same is true of any policy *mechanism* or
*kind* schema the module supports — the shapes of predicate a policy may
express are code, not configuration.

**Operator-owned, therefore data.** A `policy_code` is NOT declared. It is
scoped database identity, created at runtime as configuration. Both sources
already work this way, and the vendor control plane's
`publish_policy_version` accepts a previously unseen code as an ordinary
argument (`src/vendor_cp/approvals/service.py`, `policy_code` a plain
`String(120)` column). Requiring every code in a manifest would make adding an
approval policy — an operator's configuration change — a software release.

So the column stays a plain string with its scope, and uniqueness plus the
immutable `(policy_code, version)` pair is what gives it identity. Fail-closed
evaluation (§ 3) is what protects a typo'd code: an unknown code makes approval
*unavailable*, which is the correct refusal and does not need a registry to
produce it.

No provider name, ORM model, route or product identifier enters the module.

### 5. Two explicit planes, named separately

The module is dual-plane under ADR-0023, for the same reason `dotmac-ticketing`
is: tenant approvals serve ERP and back-office subjects, platform approvals
serve Vendor CP release and deployment subjects, and the two run in different
security contexts.

The planes are **explicit in the API**, not a parameter:

```
request_tenant_approval(...)      request_platform_approval(...)
record_tenant_decision(...)       record_platform_decision(...)
evaluate_tenant_approval(...)     evaluate_platform_approval(...)
```

There is no `platform=False` flag, no nullable `tenant_id` and no defaulted
plane column. Shared policy and lifecycle code is pure and imports no
persistence. Tenant tables carry `tenant_id NOT NULL`, composite uniques and
FORCEd RLS in the same migration (hard rule 11); platform tables carry no tenant
column, no RLS, and are REVOKEd from the tenant application role across every
table and column privilege while remaining reachable by the platform runtime
role (ADR-0023). **No foreign key crosses the planes**, and no foreign key
points into an adopting product's domain schema — a subject id is a reference,
not a relation.

### 6. Consequences are delivered as events, not executed

When approval state changes the module emits an outbox event —
`approval.requested`, `approval.approved`, `approval.rejected`,
`approval.cancelled`. The consuming product handles that event by calling its
own authoritative service, which performs its own guarded transition:

```
dotmac-approvals decides approval state
        ↓ outbox event
Finance / ERP / Vendor service decides the business transition
```

Cross-application traffic goes through the Integrator as versioned API/webhook
transport (ADR-0024). The approval module loads no external connector, joins no
database across applications, and never calls a consuming domain directly.

This is the line that keeps the module from becoming the workflow engine the
audit refused to build: a module that executed the consequence would be deciding
transitions it does not own.

### 7. The source ruling is `module ← ERP`, with named Vendor CP deltas

ERP is the extraction source: it supplies the larger production-used lifecycle
and its focused service and consumer tests. Vendor CP is **not a competing
second source** — it contributes six mandatory port deltas that ERP
demonstrably lacks (immutable policy versions, content-digest binding,
fail-closed evaluation, command idempotency, distinct-actor quorum,
self-approval exclusion) and is the real platform-plane consumer that requires
them.

Two behaviours present in the sources are deliberately **not** ported as-is:
ERP's direct `commit()` and HTTP exceptions (services mutate and flush; the
kernel's `db.py` keeps transaction authority — hard rule 8), and ERP's mutable
policy row.

### 7a. Routing stays with the domain; the module owns the lifecycle

Threshold selection and FX conversion — *which* policy applies to a ₦4.2m
invoice dated last Tuesday — is a finance decision expressed in finance
vocabulary, over amounts, currencies and rate dates the module has no business
holding. It stays in ERP.

The seam is exact: **the domain selects the policy revision, the module owns the
request and the decisions.** A caller arrives having already resolved
`(policy_code, policy_version)` and passes it in; the module never derives one,
and therefore never needs a threshold table, a Money type, an FX rate or a
conversion date.

This corrects a contradiction in the A1 audit: its proposed adoption sequence
listed "threshold selection" among the behaviours to port, while the retirement
contract in the same evidence said thresholds stay in ERP. The retirement
contract was right. Routing is out of the shared contract everywhere — it is
neither ported, nor wrapped as an optional predicate, nor represented by a
Money or FX parameter on any module entry point.

It also removes the module's only reason to depend on kernel Money/FX contracts,
which is worth having: an approval module that knew about currency would be one
schema change away from owning pricing.

### 7b. Cutover order, and what each adopter actually proves

**Vendor CP first, ERP after its E8 gate.** Vendor CP is the cleaner first
adopter: it already needs content-bound approval for fleet plans, its plane has
no `tenant_id` prerequisite, and its local implementation is two tables rather
than three plus a routing service.

But its adoption is a **capability gain as well as a relocation**, and the
comparison has to say so. Vendor CP has policy versions and per-actor approval
records and nothing else — no request id, no requester, no rejection, no
cancellation, no terminal state — so there is no old request lifecycle to
compare a new one against. Concretely:

- **Shadow-compare only the six safety properties it actually implements**:
  policy-version selection for an exact `(policy_code, version)`, quorum
  arithmetic over the same distinct-actor set, self-approval refusal,
  fail-closed refusal on a missing policy or version, content-hash mismatch,
  and idempotent re-recording.
- **Prove the request lifecycle from ERP parity plus new module tests.** Pending
  → approved/rejected/cancelled has no Vendor CP counterpart; asserting equality
  against a system that cannot express the states would be a test that passes
  because both sides are empty.
- **Import policy versions directly**, preserving `version` and any stored hash.
  An import that recomputed a hash would silently revalidate content nobody
  re-approved.
- **Decide the historical `approval_records` separately.** They cannot become
  full requests losslessly — the requester and the terminal state were never
  recorded. Either they stay read-only historical evidence beside the module, or
  they get an explicit, documented lossy-import model with synthesised fields
  marked as synthesised. That decision belongs to the cutover change, and this
  ADR does not make it.

ERP's cutover is the opposite shape: the lifecycle exists and is compared
directly, while the six safety properties have no old-path counterpart and are
proven by the ported Vendor CP tests. Its open requests are reconciled
explicitly — each re-expressed against an immutable policy version and a
computed digest, with any whose subject content has changed surfaced as stale
rather than migrated as approved.

### 8. What this ADR authorizes — and what it does not

This ADR is a **decision, not an artifact**. It fixes the boundary, the source
ruling, the plane model and the cutover shape. It creates no package, no
namespace allocation, no migration lineage and no release-eligibility row.

**Michael explicitly directed implementation on the same day**, and
[ADR-0017's 2026-08-14 amendment](0017-adoption-is-the-scarce-resource.md)
records that owner-directed exception for this named module only, in the same
narrow form it records for `dotmac-files`, `dotmac-ticketing` and
`dotmac-imports`. That direction is the authority for writing the module; the
source evidence above only explains what the module should be. It creates no
general route around the moratorium for another dossier or extraction candidate.
The exception is genuinely narrow: the audit found no independently blocked
product — Vendor CP and ERP each have a working implementation — so nothing here
satisfies ADR-0017's demand-pulled exception, and the named direction is the
whole of the authority.

**The namespace and the dossier land with the module, not ahead of it.** An
earlier revision of this change allocated `mod_approvals` in the ledger and
opened `packages/dotmac-approvals/EXTRACTION.toml` beside an empty
distribution. That was withdrawn on review for two reasons, and the second is
the durable one:

- the kernel alpha train is contended — three branches were minting adjacent
  alphas, and the release-blocked integration train has priority — so an
  allocation made here would have to be renumbered at every rebase until the
  module actually shipped;
- a ledger row with no manifest behind it is a state the kernel has no general
  way to express. Holding it honest took a bespoke architecture test guarding
  one package's emptiness. If "allocated but unbuilt" is a state worth having,
  it deserves a generic mechanism in `MIGRATION_OWNER_LEDGER` and a generic
  gate covering any dormant row — not a one-off. Neither is needed if the
  allocation simply travels with the code that uses it.

So the module change allocates its own namespace against the then-current kernel
alpha and opens its dossier in the same diff. `.github/release-modules.json`
gains an entry later still, only after the live Postgres migration and catalog
gate has passed — absence stays the safety mechanism, so the release entry lands
with the proof rather than ahead of it.

It also does not adjudicate `dotmac-automation` (ERP's `workflow_rule`,
`workflow_rule_version`, `workflow_execution`) or `dotmac-forms` (ERP's seven
`forms.*` tables). Each needs its own product-first audit and its own decision.

## Consequences

- The `governance-workflow` family name is retired. Four measured families
  replace it — `approvals`, `workflow-automation`, `forms-data-capture`,
  `work-items` — plus corrections to existing domain families. No fleet total
  changes.
- ERP's `case_action`, `case_document`, `case_response` and `case_witness` stay
  with people/payroll; moving them would transfer employment authority.
  `disclosure_checklist` stays with finance. `control_evidence` has no
  production writer and no focused test, so under the zero-consumer rule it is
  retirement work, not extraction behaviour.
- ERP's `workflow_task` is not a source: it has UI and a service but no
  production constructor and no focused behaviour suite. The orphan retires in
  ERP; a shared work-item inbox needs a fresh inventory when a consumer asks
  for one.
- Sub's `admin_alerts` and `admin_whats_new_items` were classification errors
  and are reclassified to notifications/observability and help content.
- Vendor CP's adoption retires `approval_policies` and its local writer after
  the six safety properties compare clean; what becomes of the historical
  `approval_records` is a separate decision that cutover must make (§ 7b). ERP's
  retires the three `audit.approval_*` writers and their direct service commits,
  after E8, keeping its threshold/FX routing (§ 7a). Neither cutover shares a
  database.
- The next change under the ADR-0017 amendment is the module itself: its
  namespace allocation against the then-current kernel alpha, its dossier, the
  contract, both persistence planes and the parity tests. Shadow comparisons and
  writer retirements follow per adopter, each its own change.
- Nothing in this repository yet claims `dotmac-approvals` exists. That is
  deliberate — there is no package, no ledger row and no release entry to keep
  truthful in the meantime, and no bespoke gate holding an empty distribution in
  a state the kernel cannot otherwise express.

## Alternatives rejected

**Build `dotmac-workflow` around the measured bucket.** This is the option the
audit was run to test, and the measurement refuted it: eight owners, one prefix.
A module drawn there would own approval state, automation rules, form
definitions, disciplinary records and tax evidence simultaneously — no single
decision owner, and no boundary test could be written for it.

**Merge ERP and Vendor CP as co-equal sources.** Merge-by-similarity produces a
union of two data models rather than one contract. Naming ERP as the source and
Vendor CP's properties as *mandatory deltas* keeps the parity evidence
attributable: each delta is a named behaviour with a named test that must pass
after the port.

**One plane with a nullable tenant or a `platform` flag.** Refused by ADR-0023
and by the kernel's live-catalog gate, not merely by review. A nullable
`tenant_id` makes the tenant-isolation canary unwritable, and a defaulted plane
flag makes the safe case the one a caller has to remember.

**Let the module perform the approved transition.** It is the obvious
convenience and it destroys the boundary: the module would need a domain
vocabulary, a domain import and a second writer on the subject. The outbox event
in § 6 gives the consuming domain the same automation with the ownership intact.

**Port ERP's mutable workflow row and add versioning later.** Existing requests
would be reinterpretable by a policy edit for the whole interim, which is the
exact defect content binding exists to prevent, and there is no migration that
recovers what an old request meant.

**Take ERP's threshold routing as an "optional typed policy predicate".** The
audit's own phrasing, and it smuggles the whole problem in: a predicate over
amount and currency needs Money, an FX rate and a conversion date, which is
finance's model living in a shared module under a neutral name. Optional does
not help — the module would still have to define the type. § 7a keeps selection
in the domain instead, which costs the caller one already-resolved argument.

**Declare `policy_code` in module manifests, like subject types.** Symmetrical
and wrong. A subject type can only appear through a code change; a policy code
appears when an operator configures one, and both sources already accept new
codes at runtime. Declaring them would put a release between an operator and
their own approval policy. Subject types stay declared; codes stay data.

**Reserve the namespace now and build the module later.** Tried in an earlier
revision of this change and withdrawn — see § 8. Three branches were minting
adjacent kernel alphas, so the reservation would be renumbered at every rebase,
and a ledger row with no manifest had to be held honest by a test guarding one
package's emptiness. The allocation costs nothing to defer and travels
naturally with the code that uses it.
