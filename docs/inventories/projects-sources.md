# Projects product-first source inventory

**Audited:** 2026-08-18
**Starter:** current `feat/dotmac-positioning-boundary` worktree
**Sub:** `a9da920926a9d9212a8cf03a4744b48a1d4e14f2`
**ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`
**CRM:** `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`

All three product worktrees had unrelated local changes. The cited project
paths were clean, so the revisions above identify the behavior reviewed. This
inventory resolves the fleet matrix's former ERP-versus-Sub source question
before shared implementation, as required by ADR-0006's product-first rule.

## Verdict

`dotmac-projects` is a reusable tenant module extracted **from Sub**, with ERP
as the concrete second candidate and CRM as retirement evidence.

| Repository | Existing behavior | Evidence quality | Verdict |
|---|---|---|---|
| `dotmac_sub` | Project/task/template/dependency lifecycle plus ISP installation, vendor, SLA, notification and work-order consequences | Named SOT owner; large focused service suite; architecture boundary; expected-state, lock-order, relationship and completion guards | **Qualifying source and first cutover** |
| `dotmac_crm` | Earlier buildout-oriented copy of the Sub project stack | Focused regressions exist, but service owns commits/rollbacks and the copy is already consolidating into Sub | Mandatory ancestry/retirement evidence, not a consumer |
| `dotmac_erp` | Internal delivery projects plus tasks, templates, four dependency types, milestones, resources, time, costs and Gantt reads | Production-shaped breadth, but no focused tests for its core PM task/template/dependency services were found | Requirements source and concrete second candidate, not the base |

The boundary is project/task/template/dependency/assignment structure. The
project's business subject and every product consequence stay outside it.

## Sub — qualifying implementation

Authoritative paths:

- `dotmac_sub:app/models/project.py`
- `dotmac_sub:app/schemas/project.py`
- `dotmac_sub:app/services/projects.py`
- `dotmac_sub:app/api/projects.py`
- `dotmac_sub:app/web/admin/projects.py`
- `dotmac_sub:docs/designs/PROJECTS_SOT_COMPLETION.md`

Proof to port or preserve:

- `dotmac_sub:tests/test_projects_service.py`
- `dotmac_sub:tests/architecture/test_projects_sot_boundary.py`
- `dotmac_sub:tests/test_projects_api.py`
- `dotmac_sub:tests/test_project_assignment_engine.py`
- `dotmac_sub:tests/test_web_projects_service.py`

The checked-in design names `operations.project_lifecycle` as the sole owner.
The core service provides the strongest matching behavior in the fleet:

- expected-status and terminal-state transition refusal;
- project-before-task lock ordering;
- same-project parent and dependency validation;
- hierarchy and dependency-cycle detection;
- task completion refusal while a dependency remains incomplete;
- atomic dependency replacement;
- template instantiation with calculated task dates; and
- transaction-managed audit/event integration around the owning command.

The last item is product integration, not module scope. The reusable owner
returns typed state; Sub retains audit vocabulary, customer delivery, finance
notifications, vendor review, installation verification and work-order effects.

## CRM — ancestor and retirement evidence

Relevant paths:

- `dotmac_crm:app/models/projects.py`
- `dotmac_crm:app/schemas/projects.py`
- `dotmac_crm:app/services/projects.py`
- `dotmac_crm:tests/test_project_creation_regressions.py`
- `dotmac_crm:tests/test_project_sla_lifecycle.py`
- `dotmac_crm:tests/test_project_fiber_sla_lifecycle.py`

CRM contains the same project/task/template ancestry as Sub, including focused
regressions, but its service still performs direct transaction commits and
rollbacks and the tables have no reusable tenant/RLS contract. Its buildout and
fiber behaviors are product consequences now assigned to Sub. Copying CRM would
therefore select the weaker owner and preserve a transaction-authority defect.

CRM UUIDs may be retained as migration identity during Sub adoption. They are
not a permanent shared authority and CRM is not counted as a second consumer.

## ERP — second-candidate requirements

Relevant paths:

- `dotmac_erp:app/models/finance/core_org/project.py`
- `dotmac_erp:app/models/pm/task.py`
- `dotmac_erp:app/models/pm/project_template.py`
- `dotmac_erp:app/models/pm/project_template_task.py`
- `dotmac_erp:app/models/pm/task_dependency.py`
- `dotmac_erp:app/services/pm/task_service.py`
- `dotmac_erp:app/services/pm/gantt_service.py`
- `dotmac_erp:app/services/pm/resource_service.py`
- `dotmac_erp:app/services/pm/milestone_service.py`
- `dotmac_erp:app/services/pm/time_entry_service.py`
- `dotmac_erp:app/services/pm/expense_integration.py`

ERP proves the second adoption is concrete and sets exclusions. Its reusable
overlap is core project identity, task hierarchy, templates, assignments and
dependencies. Milestones, resource allocation, time entry, finance dimensions,
budget/cost, attachments and project expenses stay ERP-owned.

The dedicated PM services generally flush rather than commit, but web paths
also mutate project rows directly and the audit found no focused tests naming
`TaskService`, project templates or task-dependency behavior. ERP therefore
cannot displace Sub's tested lifecycle as the source. Those paths need
characterization canaries before ERP cutover.

## Vocabulary mapping

| Meaning | Module/Sub | ERP source mapping |
|---|---|---|
| planned | `planned` | `PLANNING` |
| active | `active` | `ACTIVE` |
| paused | `on_hold` | `ON_HOLD` |
| complete | `completed` | `COMPLETED` |
| canceled | `canceled` | `CANCELLED` |
| task waiting | `todo` or `backlog` | `OPEN` |
| task active | `in_progress` | `IN_PROGRESS` |
| task review | `review` | `PENDING_REVIEW` |
| task complete | `done` | `COMPLETED` |
| task canceled | `canceled` | `CANCELLED` |
| task paused | `blocked` or product-owned reason | `ON_HOLD` |

The ERP adapter must select the exact `OPEN` and `ON_HOLD` mappings before its
shadow phase; the table records the available product-neutral targets, not an
implicit migration choice.

## Mandatory corrections and canaries

### D1 — dependency types were stored but not executed

Sub stores finish-to-start, start-to-start, finish-to-finish and
start-to-finish, plus lag, but its date calculator treats every edge as
zero-lag finish-to-start. ERP reports dependency types in Gantt output but does
not supply an equivalent scheduler proof. The module executes all four
constraint formulas with explicit lag and has a behavior canary for each.

### D2 — template services can own the transaction

Some Sub template paths commit internally. Module services only mutate and
flush; the adopter owns commit/rollback and product consequences in one
transaction.

### D3 — source tables do not provide the target isolation contract

The module's seven initial tables carry `tenant_id UUID NOT NULL`, composite
tenant/project foreign keys, composite tenant uniqueness, and ENABLEd plus
FORCEd RLS in their creating migration. A PostgreSQL canary proves tenant
visibility and rejects a cross-tenant task/project reference; a sensitivity
companion disables RLS and demonstrates that the detector sees both tenants.

### D4 — product meaning must not leak into the shared schema

Subscriber, customer, lead, quote, order, buildout, work-order, ticket,
business-unit, cost-centre, budget and project-type columns are forbidden in
the module models. Each product owns its local subject link and consequences.

## Initial package contract

The audit-complete `0.1.0a1` package owns seven tenant tables in
`mod_projects`: projects, templates, template tasks, template dependencies,
tasks, task dependencies and task assignees. It declares the `pj` lineage and
requires the tenant-scope catalogue and module database roles.

The initial package deliberately excludes comments, files, milestones, time
entries, resources and costs. Those are not zero-consumer stubs; an adopter may
request a new typed seam only with source behavior and a named owner.

## Cutover and retirement

1. Validate the package and its SQLite/architecture/PostgreSQL canaries on a
   fresh Observer worktree.
2. Release the compatible kernel allocation and package when the Sub adoption
   slice is ready.
3. In Sub, compose the `pj` lineage, create product-owned subject links and
   backfill the neutral aggregate without changing authority.
4. Shadow owner commands in the same transaction and compare statuses,
   hierarchy, dependency sets, instantiated tasks, dates and assignments.
5. Seal one-writer cutover only after zero unexplained differences and cross-
   tenant proof.
6. Retire Sub's generic project/task/template/dependency/assignment models and
   writers; keep its ISP consequences and rebind them to the module contract.
7. Route CRM retirement through that Sub cutover.
8. Adopt ERP on an exact released pin after status mapping and focused parity
   canaries; retire only its overlapping core writer, not its PM/finance owners.

Until step 6, the package is `audit-complete`, not adopted. Until ERP completes
step 8, it is not reuse-proven.
