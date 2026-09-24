# dotmac-approvals

One decision, and never the transition that follows:

> Has the required set of eligible actors approved **this exact content** under
> **this exact policy revision**, and is that decision still valid?

The module answers `pending | approved | rejected | cancelled | withdrawn` and emits an
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
withdraw_tenant_approval(db, tenant_id=…, request_id=…, actor=…,
                        authority_ref=…, reason=…, external_ref=…)

publish_platform_policy_version(db, revision=…)
request_platform_approval(db, …)
record_platform_decision(db, …)
evaluate_platform_approval(db, request_id=…)
cancel_platform_request(db, …)
withdraw_platform_approval(db, request_id=…, actor=…,
                           authority_ref=…, reason=…, external_ref=…)
```

A caller states its security context by naming the operation, so putting a row
in the wrong plane is a `TypeError` at the call site rather than a discovery
later. Tenant tables carry `tenant_id NOT NULL` and FORCEd RLS; platform tables
carry no tenant column and are REVOKEd from `app_user`. No foreign key crosses
between them.

The **rules** are shared and pure: `policy.py` imports no session, no model and
no plane, so both surfaces reach the same verdict rather than drifting.

Withdrawal is a later, terminal standing of an **approved** request. It never
changes an APPROVE vote or the request's original `completed_at`. A distinct
append-only row records who withdrew it, the caller's authority reference, the
reason, a service-generated UTC effective time, and a stable external reference.
The same reference with identical facts replays with no new event; another
reference or changed facts is refused. Pending, rejected and cancelled requests
cannot become withdrawn. `get_*_request` returns both the original decisions and
the withdrawal evidence, while `evaluation.is_approved` describes current
standing. Public `withdraw_tenant_approval` and `withdraw_platform_approval`
are outbox-owning commands: they persist the evidence, terminal standing and
one `approval.withdrawn` outbox row in the **same caller transaction**. On
PostgreSQL the approved-to-withdrawn transition trigger inserts that row even
for direct paired owner DML;
the public adapter does not enqueue twice. A replay
persists no second event. The event includes the original approval completion time, request,
subject, content digest and withdrawal evidence so a consumer can deduplicate
and project the revocation without reconstructing a decision. The host's
guarded adapter authorizes the actor before invoking this service; Approvals
records that authority and does not decide the host's access policy.

## What it will not do

- **Perform the subject's transition.** Generic lifecycle commands return
  `ApprovalEvent` values; withdrawal additionally requires its public outbox
  adapter because revocation must be delivered. A module that
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

`audit-complete` with zero contract consumers. Vendor CP is cutover 1: its
assembly will explicitly select only `ModulePlane.PLATFORM`, even though the
kernel lineage it already composes truthfully supplies the tenant catalogue.
ERP is cutover 2 after its E8 gate.

**Release-eligible, not yet released.** The live Postgres migration and catalog
gate covers both contracts — `tests/test_approvals_isolation.py` migrates the
`ap` lineage into one scratch database with both planes and another with an
explicit platform-only selection. It proves FORCEd RLS on every tenant table,
`app_user` locked out of every platform table with the platform role still able
to operate, no cross-plane foreign key, duplicate-vote refusal, and no tenant
approval tables in the platform-only database even though kernel `public.tenants`
exists there. The `.github/release-modules.json` entry landed with that proof
rather than ahead of it, which is what ADR-0026 § 8 requires; nothing has been
published yet.
