# A1 source audit — approvals are not a generic workflow engine

- **As of:** 2026-08-14
- **Starter:** `042532d2` (`approvals-workflow-audit`)
- **ERP:** `0f4b1698` (`feat/kernel-ui-contract-alignment`; relevant paths clean)
- **Sub:** `2193d04f` (`feat/roles-r1-additive`; relevant paths clean)
- **Vendor CP:** `89848017` (`feat/adopt-kernel-a46-and-vendor-modules`; clean)

This is the product-first audit required by A1 in
[`fleet-decomposition-matrix.md`](fleet-decomposition-matrix.md) and by hard
rule 24. It compares behavior, writers and tests before any shared table or
service is written. Its exact 24-row disposition ledger is
[`approval-workflow-dispositions.toml`](approval-workflow-dispositions.toml).

This document is **evidence and a recommendation**. Its source ruling was
accepted on 2026-08-14 by
[ADR-0026](../adr/0026-approvals-decide-approval-never-the-transition.md), with
**three corrections** to the recommendation — see "Corrections adopted on
review" below. The measurements are unchanged. No package, namespace, module
code, migration or release row is created by this audit or by that ADR.

## Method and evidence control

The fleet baseline was originally measured at ERP `0f4b1698`, CRM `c64b5aa0`,
Sub `9f6f9f36` and Vendor CP `eb667fa`. The classification correction was
replayed against archives of those exact revisions, so the frozen totals remain
1,213 tables and 149 duplicated names. Newer unrelated Sub/Vendor table changes
were deliberately excluded.

The behavioral comparison used the current local source revisions named above.
The relevant ERP and Sub paths had no working-tree differences, and Vendor's
approval implementation is unchanged between the frozen `eb667fa` and reviewed
`89848017` revisions. Unrelated dirty product files were neither read as
evidence nor changed.

For each measured row the audit recorded:

- model and migration owner;
- the production writer, or the absence of one;
- focused behavioral tests, incidental coverage, or an explicit untested state;
- the one target capability and whether the row supplies extraction behavior,
  a mandatory safety delta, a domain-owned fact, or retirement evidence.

The architecture canary freezes the complete row set and demonstrates that the
retired catch-all would misclassify representatives from all eight owners.

## Headline finding

**There is no coherent `governance-workflow` capability to extract.** The old
prefix-based bucket combined eight owners:

| Actual owner | ERP | Sub | Vendor | Outcome |
|---|---:|---:|---:|---|
| approvals | 3 | 0 | 2 | shared `dotmac-approvals` candidate |
| declarative automation | 3 | 0 | 0 | separate `dotmac-automation` audit |
| forms/data capture | 7 | 0 | 0 | separate `dotmac-forms` audit |
| people/payroll discipline | 4 | 0 | 0 | remain with people/payroll |
| finance reporting/tax | 2 | 0 | 0 | remain with finance; one is orphaned |
| human work items | 1 | 0 | 0 | ERP row has no producer; retire it |
| notifications/observability | 0 | 1 | 0 | Sub admin alerts reclassified |
| help/release-note content | 0 | 1 | 0 | Sub What's New reclassified |

The result is four narrow measured families—`approvals`,
`workflow-automation`, `forms-data-capture` and `work-items`—plus corrections to
existing domain families. It changes no fleet total and retires the
`governance-workflow` family name.

## The reusable approval contract

ERP and Vendor CP implement different halves of one approval capability. ERP is
the qualifying production source for the tenant-plane lifecycle. Vendor CP has
the stronger safety identity and is a mandatory port delta, not a competing
second source.

| Dimension | ERP production source | Vendor CP implementation | Required shared contract |
|---|---|---|---|
| policy identity | `(organization_id, workflow_code)`, mutable row | immutable `(policy_code, version)` | immutable policy revision selected by stable code + exact version |
| subject identity | `document_type`, `document_id`, amount/currency | `subject_type`, `subject_id`, `content_hash` | declared subject type + opaque subject id + canonical content digest |
| lifecycle | pending → approved/rejected/cancelled; ordered levels | approval records + pure quorum evaluation — **no request id, requester, rejection, cancellation or terminal state** | request lifecycle plus immutable decisions; content change makes the old request stale |
| approvers | user/role eligibility, per-level count, SoD, delegation provenance, MFA evidence | distinct actors, self-approval exclusion | typed approver resolver, distinct actors, per-level quorum and SoD |
| routing | amount/currency thresholds with FX conversion | none | **CORRECTED — not in the shared contract.** The domain selects the exact policy revision and passes it in (ADR-0026 § 7a) |
| failure | missing workflow may mean no approval required | missing policy fails closed | a required but missing/stale policy is unavailable, never implicitly approved |
| idempotency | none on submit/decision | kernel platform idempotency + uniqueness | command idempotency and database uniqueness own duplicate decisions |
| tenancy | organization column; no RLS on the three approval tables | platform tables, no tenant column | explicit tenant and platform planes, audited independently |
| transaction | service calls `commit()` and raises HTTP exceptions | service mutates/flushes through kernel seams | host transaction authority; typed domain errors; no route concerns |

### One decision owner

`dotmac-approvals` owns only this decision:

> Has the required set of eligible actors approved this exact content under this
> exact policy revision, and is that decision still valid?

It does **not** approve an invoice, activate a contract, post a journal, deploy
a release or change any subject's lifecycle. The owning domain submits a
canonical digest, asks for/evaluates approval, and performs its own guarded
transition. Approval records are evidence; they are never a second writer of
the subject.

### No hardcoded fleet vocabulary

ERP's `document_type` examples and its automation `WorkflowEntityType` enum are
not reusable vocabularies. Subject types and policy KINDS belong to consuming
modules, so the shared implementation needs module-manifest declaration
registries under ADR-0008. Database columns remain plain strings; boot and
service gates reject undeclared references. Provider names, ORM models, routes
and product identifiers never enter the module.

An individual `policy_code` is **not** in that set — it is operator
configuration, created at runtime as scoped data. See the corrections below.

### Explicit planes

The target is dual-plane for the same reason as ticketing:

- tenant approvals support ERP/back-office subjects;
- platform approvals support Vendor CP release/deployment subjects;
- shared pure policy/lifecycle code imports no persistence;
- tenant and platform repositories/services are named explicitly—no nullable
  tenant, `platform=True`, or defaulted plane flag;
- no foreign key crosses planes.

## Source selection and proposed cutover

**Recommended source ruling:** `dotmac-approvals` is `module ← ERP`, with Vendor
CP's immutable version, content binding, fail-closed evaluation, idempotency and
distinct-actor behavior listed as mandatory port deltas.

That is not a compromise or a merge-by-similarity. ERP supplies the larger,
production-used lifecycle and its focused service/consumer tests. Vendor CP
supplies safety properties ERP demonstrably lacks and the real platform-plane
consumer that requires them.

Proposed adoption sequence — **as corrected on review**; steps 1, 2 and 4 were
amended, see below:

1. Allocate one module namespace/lineage and write an `EXTRACTION.toml` **in the
   same change that writes the module**, not ahead of it.
2. Port ERP's request lifecycle, ordered levels, quorum, eligibility, SoD and
   decision history into typed services that mutate/flush. **Threshold and FX
   selection stay in ERP**, which resolves the exact policy revision and passes
   it in.
3. Add Vendor CP's immutable policy versions, canonical content digest,
   distinct actors, self-approval exclusion, idempotency and fail-closed
   evaluation as named port deltas.
4. Compose the platform plane into Vendor CP first. Import its policy versions
   directly, **shadow-compare only the six safety properties it implements**,
   then retire its local writer. Its `approval_records` cannot become requests
   losslessly, so their disposition — read-only historical evidence, or an
   explicit lossy-import model — is a decision that cutover makes. This is the
   first cutover because Vendor already needs content-bound approval for fleet
   plans.
5. Compose the tenant plane into ERP after its E8 lineage gate. Shadow policy
   selection and decision results, reconcile open requests, then retire the
   three `audit.approval_*` writers and direct service commits.

Reuse is proven only after both independent assemblies consume exact releases.
Neither cutover shares a database; application collaboration remains typed
API/event traffic through the appropriate owner.

## Behavioral proof to preserve

| Source | Suite | Preserve |
|---|---|---|
| ERP | `tests/ifrs/platform/test_approval_workflow_service.py` | submission, ordered-level advancement, eligibility, rejection, requester-only cancellation, pending filtering, decision history. **Threshold routing and fallback stay ERP-side** and are preserved as ERP's own tests, not ported into the module |
| ERP | `tests/ifrs/fa/test_fa_gl_reconciliation_package_service.py` | a real domain requests approval and refuses its consequence until approval exists |
| Vendor CP | `tests/unit/test_approvals.py` | immutable policy versions, distinct quorum, content binding, missing-policy fail-closed, self-approval exclusion, idempotent record |

Additional canaries required before release:

- cross-tenant isolation for policies, requests and decisions;
- platform tables have no tenant/RLS and no `app_user` privileges, while the
  platform runtime role remains reachable;
- no cross-plane foreign keys;
- two concurrent approvals by one actor count once;
- two actors satisfying the final quorum produce one terminal transition;
- a subject digest change invalidates the prior decision;
- services never commit, roll back or create a session;
- no approval service imports a consuming domain or mutates its subject.

## The seven non-approval dispositions

### Declarative automation → separate audit

ERP's `workflow_rule`, `workflow_rule_version` and `workflow_execution` are a
real, tested trigger-condition-action engine. They are not approval. The current
service hardcodes finance/HR/fleet entity enums and directly sends email and
notifications, mutates fields, creates tasks and calls webhooks. A reusable
`dotmac-automation` must instead consume module-declared triggers/actions and
delegate consequences through typed owner ports, kernel outbox/idempotency and
the Integrator. It requires its own dossier before implementation.

### Forms/data capture → separate audit

ERP's seven `forms.*` tables hold versioned definitions, sections, fields,
options, submissions and snapshot-backed answers. The only proven consumer is
recruitment, and `FormEngineService` hardcodes applicant mappings. The mechanism
is a credible `dotmac-forms` source only after a separate audit extracts the
typed subject/mapping seam. A submission records evidence; the subject domain
owns every consequence.

### People and finance rows stay with their domains

`case_action`, `case_document`, `case_response` and `case_witness` are children
of ERP disciplinary cases. Moving them would transfer employment authority.
`disclosure_checklist` is IFRS reporting state. `control_evidence` is tax state
with no production writer or focused test; under the zero-consumer rule it is
retirement work, not extraction behavior.

### ERP `workflow_task` is not a source

The table has list/update UI and a service, but no production constructor was
found and there is no focused behavior suite. A future shared work-item inbox
may still be required; this row cannot justify building it. Retire the orphan
in ERP and run a fresh product inventory when a consumer asks for work items.

### Sub's two rows were classification errors

`admin_alerts` is deduplicated observability/notification state with its own
service and tests. `admin_whats_new_items` is scheduled dashboard content with
its own service and tests. Neither participates in business-process state or
human approval.

## Corrections adopted on review — 2026-08-14

The measurements above stand. Three parts of the *recommendation* were wrong and
were corrected before
[ADR-0026](../adr/0026-approvals-decide-approval-never-the-transition.md)
accepted the ruling. Recorded here rather than silently rewritten, because a
recommendation that changed is evidence about the audit's method.

**1. Policy codes are data, not manifest vocabulary.** "Subject types and policy
kinds belong to consuming modules" was right; the drafted ADR turned it into
manifest-declared *policy codes*, which is a different and wrong claim. Both
sources treat a code as scoped data — Vendor CP's `publish_policy_version`
accepts a previously unseen `policy_code` as an ordinary argument over a plain
`String(120)`. Declaring codes would make an operator's configuration change
require a software release. Subject types and policy *mechanism/kind schemas*
are code-owned and stay declaration registries; `policy_code` stays scoped
database identity, and fail-closed evaluation is what protects a typo.

**2. Threshold/FX routing is not in the shared contract.** Step 2 of the
sequence said to port ERP's threshold selection while the retirement contract
said thresholds stay in ERP. The retirement contract was right, and the
"optional typed policy predicates" row overstated it further. The domain selects
the exact policy revision and passes it in; the module owns the request and
decision lifecycle and never sees an amount, a currency or an FX date.

**3. The Vendor CP cutover is capability gain plus relocation, and its proof was
impossible as drafted.** Vendor CP has policy versions and per-actor approval
records only — `ApprovalRecord` carries no request id, requester, rejection,
cancellation or terminal state (`src/vendor_cp/approvals/models.py`). Comparing
"terminal request state" against it would assert equality over states it cannot
express. So: shadow-compare only the six safety properties it implements; prove
the request lifecycle from ERP parity plus new module tests; import policy
versions directly; and decide separately whether its historical `approval_records`
remain read-only evidence or get an explicit lossy-import model, since they
cannot become full requests losslessly.

## Gate after this audit — updated 2026-08-14

The evidence gate and the decision gate are both closed. ADR-0026 accepts the
corrected source ruling and boundary. **It creates nothing**: no package, no
namespace allocation, no migration lineage, no release-eligibility row.

**The implementation gate is open, and the module is unwritten.** ADR-0026 § 8
left ADR-0017's moratorium standing — this audit found no independently blocked
product, since ERP and Vendor CP each run a working implementation — and
[ADR-0017's 2026-08-14 amendment](../adr/0017-adoption-is-the-scarce-resource.md)
then recorded Michael's owner-directed exception for this named module. That is
a named direction rather than a demand pull, and it opens no route around the
moratorium for any other candidate.

The module change allocates `mod_approvals` against the then-current kernel
alpha and opens its `EXTRACTION.toml` in the same diff. Reserving either ahead
of the code was tried and withdrawn: the alpha train is contended, so the
reservation would be renumbered at every rebase, and a ledger row with no
manifest needs a generic mechanism and a generic gate rather than a
package-specific test. ADR-0026 § 8 records that reasoning.
