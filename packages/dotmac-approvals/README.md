# dotmac-approvals

One decision, and never the transition that follows:

> Has the required set of eligible actors approved **this exact content** under
> **this exact policy revision**, and is that decision still valid?

The module answers `pending | approved | rejected | cancelled` and emits an
event. The subject's owner reacts and runs its own guarded transition.
**Approving a payment does not post it** — Finance still decides whether an
approved payment may be posted.

Boundary: [ADR-0026](../../docs/adr/0026-approvals-decide-approval-never-the-transition.md).
Evidence: the [A1 source audit](../../docs/inventories/approvals-workflow-source-audit.md)
and its [24-row disposition ledger](../../docs/inventories/approval-workflow-dispositions.toml).
Extraction dossier: [`EXTRACTION.toml`](EXTRACTION.toml).

## The two planes are named, not flagged

There is no `platform=` argument and no nullable tenant anywhere in the API:

```python
publish_tenant_policy_version(db, tenant_id=…, revision=…)
request_tenant_approval(db, tenant_id=…, …)
record_tenant_decision(db, tenant_id=…, …)
evaluate_tenant_approval(db, tenant_id=…, request_id=…)
cancel_tenant_request(db, tenant_id=…, …)

publish_platform_policy_version(db, revision=…)
request_platform_approval(db, …)
record_platform_decision(db, …)
evaluate_platform_approval(db, request_id=…)
cancel_platform_request(db, …)
```

A caller states its security context by naming the operation, so putting a row
in the wrong plane is a `TypeError` at the call site rather than a discovery
later. Tenant tables carry `tenant_id NOT NULL` and FORCEd RLS; platform tables
carry no tenant column and are REVOKEd from `app_user`. No foreign key crosses
between them.

The **rules** are shared and pure: `policy.py` imports no session, no model and
no plane, so both surfaces reach the same verdict rather than drifting.

## What it will not do

- **Perform the transition.** It returns `ApprovalEvent` values; `outbox.py` is
  an optional adapter onto the kernel's transactional outbox. A module that
  executed the consequence would need a domain vocabulary and would become a
  second writer on the subject.
- **Route by amount.** Threshold selection and FX conversion stay in the domain
  (ADR-0026 § 7a). The caller arrives with `(policy_code, policy_version)`
  already resolved, so the module never holds Money, a currency or a rate date.
- **Own a subject vocabulary.** `subject_type` is declared by the consuming
  module's manifest. A `policy_code`, by contrast, is operator configuration
  created at runtime — data, not a declaration (§ 4).
- **Commit.** Services `add`/`flush` only; `dotmac_kernel.db` keeps transaction
  authority. This is a deliberate correction of the source, which called
  `db.commit()` inside the service.
- **Query your identity estate.** Role membership arrives on the `Actor` value.

## Safety properties, and where they come from

Ported from ERP (the production tenant lifecycle): ordered levels, per-level
quorum, user/role eligibility, segregation of duties, delegation provenance, MFA
evidence, requester-only cancellation, append-only decision history.

Mandatory port deltas from the vendor control plane, all of which ERP lacked:
immutable `(policy_code, version)` revisions, content-digest binding,
fail-closed evaluation when a policy or version is missing, command idempotency,
distinct-actor quorum, and self-approval exclusion.

Two behaviours were deliberately not ported: ERP's mutable policy row, and its
fail-open "a missing workflow may mean no approval is required".

## Status

`audit-complete` with zero contract consumers. Vendor CP is cutover 1 (its plane
has no `tenant_id` prerequisite), ERP is cutover 2 after its E8 gate. The
package is absent from `.github/release-modules.json` until its live Postgres
migration and catalog gate has passed — that absence is the safety mechanism.
