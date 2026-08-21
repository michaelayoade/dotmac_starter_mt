# ERP / Backoffice / general module publication readiness

- **Status:** publication preparation only, 2026-08-20
- **Cohort:** Accounting, Analytics, Banking, Documents, Expenses, Finance, Inbox,
  Party, Payables, Payroll, Procurement, Projects, Records, Surveys, Tax and Work
  Orders

This is the cohort-level release gate. The per-package `EXTRACTION.toml` files
own exact source revisions and cutover evidence; the source inventories own the
audited product facts; this file joins those facts into one dependency and
validation view. It does not authorize publication or move product authority.

## Current state

- All sixteen packages declare `0.1.0a1`, `audit-complete`, zero
  `contract_consumers`, an independent tenant lineage and an exact source-pin
  inventory.
- Kernel `0.1.0a85` allocates all sixteen lineages. The allocations are new and
  immutable: Documents uses prefix `do` because `dc` belongs to Deployment
  Control; Party uses `pt` because `pa` belongs to Payables.
- Starter builds and governs the packages but does not compose them. They are
  absent from `.github/release-modules.json`, so the release workflow refuses
  them.
- The declared-publication ledger records kernel a85 and every package a1 as an
  intentional unpublished state. No artifact should be dispatched from this
  preparation change.
- No exact-commit Observer claim exists for the combined cohort yet. That proof
  requires a committed revision and a fresh isolated Observer worktree at that
  exact commit; an uncommitted local worktree is not evidence.

## Adopter and dependency gates

| Module | Qualifying source / cutover 1 | Checked-in seam required before release authorization |
|---|---|---|
| Accounting | ERP | Exact kernel/package pins; tenant, role and idempotency bindings; Approvals/Numbering and producer adapters; full chart/period/journal/ledger backfill, shadow and sealed writer retirement. |
| Analytics | ERP | Metric declaration registry; typed owner-computed batch adapters; latest/history/period and digest-rebuild shadow; legacy projection and reader ratchets. |
| Banking | ERP, after Accounting | Opaque cash-account mapping; statement/cash observation adapter; matching/reconciliation shadow; provider transport retained in Integrator; legacy decision-writer ratchet. |
| Documents | ERP, after its E8 tenant/session/lineage gate | Opaque Files, Approvals and Durable Timers seams; HR handbook/version/acknowledgement backfill; exact-version shadow and writer ratchet. |
| Expenses | ERP | Approvals, Files, Numbering and Finance handoff adapters; request/claim/policy/evaluation backfill and shadow; one sealed writer switch. Backoffice is second, not first. |
| Finance | ERP | Opaque physical-asset and Accounting consequence seams; corrected fixed-asset parity fixtures; book/event backfill and reconciliation; authority-switch rehearsal. |
| Inbox | Sub | Provider-neutral Integrator receive port; product-owned party/ticket/team/file links; conversation/message/read-cursor backfill; sole-writer switch. |
| Party | Sub | Local `party_person_catalog.v1` binding; open vocabulary declarations; role/relationship/membership/contact/external-reference backfill; reader and writer retirement. |
| Payables | ERP, after Accounting | Supplier, Procurement, Approvals and Tax fact adapters; Treasury settlement observations; Accounting consequence/receipt reconciliation; AP backfill and writer retirement. |
| Payroll | ERP, after People and Accounting seams are stable | Opaque employee reference; Tax policy and payment observation ports; structure/run/calculation/liability backfill; parity shadow and writer retirement. |
| Procurement | ERP, after Organization-to-Tenant mapping | Requester, supplier, item, budget, Approvals and receipt adapters; complete sourcing/purchase lifecycle backfill and digest shadow; sealed writer switch. |
| Projects | Sub | Product-owned subject and consequence links; project/task/template/dependency/assignment backfill; lifecycle parity and authoritative writer switch. |
| Records | ERP declaration candidate | Exhaustive retention/deletion/hold writer inventory; opaque source, Files, Approvals and Durable Timers seams; consequence-free shadow with destructive execution disabled. |
| Surveys | Sub | Product-owned ticket/work-order eligibility and consequence adapters; delivery through product outbox; definition/invitation/response backfill and aggregate shadow. |
| Tax | ERP, after Accounting | Governed tenant policy-data loader; invoice/order/payroll fact ports; Sub facts over versioned API/outbox; determination/report/return parity shadow and writer retirement. |
| Work Orders | Sub | Product-owned subject, assignee, evidence and consequence adapters; internal-crew execution backfill and shadow; vendor InstallationProject remains excluded. |

Backoffice remains an independent later consumer for ERP-sourced modules. Its
checked-in rules explicitly deny first-adoption credit to a clean composition
that has not retired the qualifying ERP writer, so this cohort does not add any
Backoffice module composition.

## Exact-commit validation gate

After Michael authorizes a commit, validate that exact commit on Observer in a
fresh isolated writable worktree with the repository-pinned Poetry version
(`2.4.1`). Do not reuse a shared checkout and do not run these tests locally.

1. Run `make check`.
2. Run `make test-unit` with xdist capped at `-n 2` or `-n 3` if parallelism is
   explicitly added; never use `-n auto`.
3. Run `make test-db-up && make test-integration && make test-db-down` against a
   disposable PostgreSQL database, including teardown on failure.
4. Preserve the exact commit, commands and results in the release/adopter
   change. A result from a different commit does not clear this gate.

## Recommended release discipline

Do not publish the cohort as one undifferentiated batch. Publish a package only
when its cutover-1 product branch has the disabled-by-default exact-version
composition, typed adapters, backfill/shadow proof and writer-retirement gate
ready for review. Add the package to the closed release allowlist in that
authorization change, publish kernel a85 at the first approved slice, and then
registry-verify the exact package before enabling the adopter.

Accounting is the dependency hub for Banking, Payables, Payroll, Tax and the
Finance consequence seam, so it is the first ERP financial release candidate.
Sub-owned Party, Projects, Work Orders, Inbox and Surveys form a separate
cutover stream and should advance only when their corresponding Sub owner slice
is ready. Backoffice follows proven ERP releases; it never substitutes for the
source-product authority switch.
