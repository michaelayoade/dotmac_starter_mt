# People directory extraction audit — ERP, Starter, Sub, CRM and Backoffice

- **As of:** 2026-08-18
- **Starter:** `01c4ffa2` (`origin/main`)
- **ERP:** `dd6416cd` (`origin/main`)
- **Sub:** `a9da9209` (`origin/dev`)
- **CRM:** `d363af3d` (`origin/main`)
- **Backoffice:** `fcdd8270` (`feat/composition-bindings-and-tenancy-gate`)

This is the product-first source audit for the first vertical replacement slice
named by `dotmac_backoffice/AGENTS.md`: the people / identity seam. It is an
inventory and ownership ruling, not a release, composition, deployment or
authority cutover.

## Contract named before the code

The candidate `dotmac-people` module owns a tenant's **employment directory**:

- whether a kernel `Party` holds an employment relationship;
- the stable employee code and employment lifecycle dates/state;
- departments, designations and employment types;
- positions, their hierarchy and vacancy-routing policy;
- temporal employee-to-position assignments; and
- manager, direct-report and approval-chain resolution derived from the
  position tree at an explicit date.

It does **not** own personal identity or contact data, authentication, RBAC,
invitations, attendance, leave, scheduling, discipline, performance, training,
compensation, payroll, bank details, tax, GL dimensions, physical locations,
files, notifications or external-system provisioning. Those are separate
owners or typed consumers of an employee reference.

That separation is load-bearing. ERP's `Employee` row currently carries fields
from most of those excluded domains; copying the row would recreate the ERP
aggregate inside a package instead of extracting an owner.

## Finding

**ERP is the only qualifying implementation source.** It has the production
employee lifecycle, organization catalogue, position hierarchy, temporal
assignments and a versioned `/api/v1/people/hr/*` surface. Its
`PositionService` / `OrgResolver` pair is the strongest reusable behaviour in
the seam.

**Starter already owns identity, and must keep it.** Kernel `Party`,
`PartyPerson`, `PartyOrganization`, credentials, sessions and
`PartyRoleGrant` are not extracted again. `dotmac-people` references the
tenant-scoped Party identity; it never creates a second person table or accepts
an ERP `Person` model.

**Sub and CRM are requirements and projection inputs, not competing HR
sources.** Sub has a mature Party model and staff principal, but no employment
directory. CRM has technicians, agents and a pull from ERP employees, but is
single-tenant and does not own employment decisions. Backoffice has zero
domain tables and is the intended first runtime adopter.

## Fleet inventory

| Product | What exists | Ownership reading | Extraction use |
|---|---|---|---|
| Starter | Kernel `Party` / subtype tables, credentials, sessions and role grants; tenant RLS and composite identity | Identity and authorization foundation | Mandatory identity target; no ERP identity code ports |
| ERP | `people`; `hr.department`, `designation`, `employment_type`, `employee_grade`, `employee`, `position`, `position_assignment`; services and `/api/v1/people/hr/*` | Sole employment-directory writer today, mixed with auth, payroll, attendance, finance and integrations | Qualifying source for the narrow contract and parity tests |
| Sub | `Party`, business roles/memberships, `SystemUser`; remote `workforce_employee_reference` values | Owns ISP Party/staff and consumes ERP workforce identity; no employee lifecycle or org chart | Confirms Party/Account/Principal separation and opaque downstream references |
| CRM | `Person`, `CrmAgent`, technician/skill projections; `dotmac_erp/agent_sync.py` pulls ERP employees | Single-tenant projection consumer; not an employment owner | Existing downstream contract to redirect after cutover, never a source table to merge |
| Backoffice | Zero domain tables; kernel a69 is released but the local skeleton still records a67 | Destination assembly, not a source | First runtime adopter after a released module; no ERP database access |

## The fan-in changes the sequence, not the owner

At the recorded ERP revision, `hr.employee.employee_id` is the target of **131
foreign-key declarations in 69 model files across 18 domain/subdomain
buckets**. It is a hub, not a low-coupling pilot. Therefore “people / identity
seam first” means the employee reference and projection contract is fixed
before any business behaviour moves. It does not mean 131 foreign keys are
repointed as one change, and it does not permit those dependants to keep ERP as
a second employment-lifecycle writer.

The bridge after authority cutover is an ERP-local, rebuildable compatibility
projection fed through the versioned Backoffice API/outbox contract. It keeps
the existing employee identifiers needed by local foreign keys while the
dependent domains move. It makes no employment decision and its adapter is the
only writer of the projected lifecycle columns. Domain fields still embedded
in ERP's wide employee row remain owned by their respective ERP domains until
those domains move; they may not rewrite the projected employment fields.

The projection is transitional evidence, not the destination. A checked-in
writer ratchet must enumerate every ERP path that mutates employee identity,
code, lifecycle state or dates, and reach zero at the sealed cutover. The
`hr.employee` table itself is deleted only after its 131 foreign-key
dependants have moved or been retired. That distinction lets authority move
vertically without either a 131-FK flag day or a parallel writer.

## ERP source surface

### Models to port by behaviour, not by row shape

- `app/models/people/hr/employee.py`
- `app/models/people/hr/department.py`
- `app/models/people/hr/designation.py`
- `app/models/people/hr/employment_type.py`
- `app/models/people/hr/position.py`
- `app/models/people/hr/position_assignment.py`

`EmployeeGrade` is not in the first contract. Its `min_salary` / `max_salary`
band makes it compensation policy, and payroll consumes it directly. Moving the
name while leaving the money would split one concept between two authorities;
moving the money would put payroll into the identity seam. It remains in ERP
until the compensation/payroll slice names its owner.

### Writers and decision logic

- `app/services/people/hr/employees.py` — employee lookup, creation, core
  lifecycle and rehire. Only the employment decisions port; password creation,
  RBAC grants, invite email, Sub provisioning, payroll and attendance fields do
  not.
- `app/services/people/hr/organization.py` — department, designation and
  employment-type catalogues. Location and grade methods stay with their own
  domains.
- `app/services/people/hr/positions.py` — position hierarchy, assignment
  overlap/uniqueness, vacancy state and org-chart projection.
- `app/services/people/hr/org_resolver.py` — date-aware manager, incumbent,
  direct-report and approval-chain resolution with cycle protection and
  explicit vacancy behaviour. Notification delivery is an adapter and does
  not port into the resolver.

### Behaviour proof to preserve

- `tests/people/hr/test_position_assignments.py` — active-primary uniqueness,
  temporal assignments, hierarchy, vacancy coverage, cycle refusal and org
  chart behaviour.
- `tests/people/hr/test_position_role_summary.py` — role/position read
  projection behaviour.
- `tests/people/hr/test_employee_rehire.py` — admissible source states and date
  ordering for rehire.
- `tests/people/hr/test_employee_search.py` — person-backed name/search
  behaviour; ported against kernel Party rather than ERP Person.
- `tests/integration/test_person_services.py` — tenant-filtering evidence for
  ERP Person. Its identity CRUD does not port, but its cross-organization
  denial becomes a real PostgreSQL RLS canary for every module table.

The organization catalogue has materially less focused proof than the position
engine. The extraction must characterize code uniqueness, parent-cycle
refusal, deletion-with-members and headcount behaviour before claiming parity;
an untested source method is not inherited merely because it exists.

## Source defects and couplings not to inherit

1. **Identity duplication.** ERP `people` combines identity, reachability,
   address and lifecycle and has a global unique email. The module uses kernel
   Party and owns none of those columns.
2. **A wide employee aggregate.** Bank details, CTC, salary mode, final-payroll
   state, shift/location defaults, Sub roles and provisioning timestamps are
   excluded from the employment directory.
3. **Two reporting authorities.** `Employee.reports_to_id` is documented as a
   legacy cache while the position tree is canonical. The module stores only
   positions and assignments; it does not recreate the cache.
4. **Two department-head authorities.** `Department.head_id` competes with the
   position incumbent. The module derives the head through the declared head
   position/assignment rather than storing a second employee pointer.
5. **Cross-domain foreign keys.** Department cost centre, employee payroll
   account, attendance shift and physical location links remain with consuming
   domains. A typed reference/adapter is added only when that consumer moves.
6. **Product-specific side effects in the owner.** Passwords, default roles,
   invite email, notifications and Sub synchronization leave through assembly
   adapters/outbox after the employment transaction; the module never imports
   them.
7. **Over-broad export.** ERP's current `EmployeeRead` includes CTC and bank
   fields. Backfill/shadow uses a new narrow versioned projection containing
   only the contract fields and provenance; Backoffice never copies the broad
   response or reads ERP's database.
8. **Application predicates standing in for isolation.** Every module table is
   created with `tenant_id UUID NOT NULL`, tenant-leading uniques and composite
   FKs, RLS ENABLE + FORCE, a tenant policy and exact `app_user` grants in the
   same migration. Parity is proved with separate real-role connections.

## Target persistence and service boundary

The first coherent module lineage owns six tables in `mod_people`:

| Table | Authority |
|---|---|
| `employees` | one tenant employment relationship for one kernel Party; employee code, lifecycle state and dates |
| `departments` | tenant department identity and hierarchy, including one optional declared head position |
| `designations` | tenant job-title catalogue |
| `employment_types` | tenant employment-arrangement catalogue |
| `positions` | tenant seat hierarchy and vacancy-routing policy |
| `position_assignments` | temporal employee occupancy, with at most one active primary assignment per employee and per position |

The lineage needs a new logical prerequisite for the kernel Party catalogue,
verified against the live catalog and bound by each assembly to the revision
that actually supplies it. Naming kernel revision `0003_party_identity`
directly from the module is forbidden.

The module's public surface is typed commands, queries and immutable outcomes.
It takes an already-resolved `TenantScope`; it never resolves a host, tenant,
login or permission. Services mutate and flush only. The kernel remains the one
transaction authority.

## First vertical cutover

The accepted Backoffice programme is a replacement cutover, not an ERP
in-place adoption:

1. Extract ERP's proven behaviour and parity tests into `dotmac-people` and
   release it from Starter.
2. Compose the exact release in Backoffice and apply the module lineage. This
   creates no adoption credit by itself and moves no authority.
3. Add a narrow, versioned ERP read projection for Party, department,
   designation, employment type, employee, position and assignment facts. It
   contains source identity/version and no credential, bank, compensation or
   payroll fields.
4. Backfill kernel Party first, then module rows, preserving source identity as
   provenance. Re-run idempotently and report missing, extra, conflicting and
   stale rows.
5. Shadow Backoffice reads against the ERP API and reconcile to zero for codes,
   lifecycle state/dates, hierarchy, active assignments and date-aware manager
   resolution. ERP remains the sole writer throughout this phase.
6. In one separately authorized cutover, make Backoffice the sole writer and
   fail closed every corresponding ERP mutation path. ERP's existing employee
   key becomes a rebuildable compatibility projection supplied over the
   versioned Backoffice API/outbox contract for the 131 local FK dependants;
   no reverse database access and no dual write.
7. Remove the ERP employment writers immediately with the switch. Remove the
   compatibility projection and obsolete tables later, only after their
   dependent-domain ratchet is zero and the fallback gate has remained unused
   for the accepted observation window.

ERP is the **source product and the first authority retired**. Backoffice is the
first module runtime consumer. This is the replacement exception recorded in
ADR-0006: forcing the retiring ERP to compose the module first would require a
throwaway Party/tenancy redesign and would violate Backoffice's containment
decision.

## Gates before implementation can claim completion

- The replacement-cutover amendment is accepted in the checked-in ADR.
- `dotmac-people` receives a namespace/lineage allocation together with its
  manifest and migration; no namespace is reserved early.
- The Party-catalog prerequisite and live verifier are released in the kernel.
- The first migration has static sensitivity proofs and real PostgreSQL
  canaries for cross-tenant denial, FORCE RLS and exact role grants.
- The ERP projection contract and mutation-path ratchet are checked in before
  shadowing begins.
- The reverse compatibility projection is proved rebuildable and unable to
  originate employee lifecycle decisions before ERP's employment writers are
  disabled; its 131-FK retirement ratchet is separate from the writer ratchet.
- Backoffice remains a thin assembly and imports no ERP, CRM or Sub code/data
  plane.
- Composition, release, deployment and authority cutover remain distinct
  gates. This audit authorizes none of the irreversible ones.

## Verdict

Proceed product-first with ERP as the source and `dotmac-people` as the narrow
employment-directory owner. Start with the replacement-cutover ADR amendment
and dossier; then add the Party prerequisite and the six-table tenant-plane
module canary-first. Do not start workforce, payroll or a broad HR port beside
it.

## Implementation checkpoint — 2026-08-18

The canary-first module foundation now exists on the stacked implementation
branch: kernel a71 names and proves `party_person_catalog.v1` and allocates
`pe`/`mod_people`; `dotmac-people` a1 owns the six tables above with forced RLS,
tenant-composite relations and exact `app_user` grants. The extracted service
preserves code normalization, rehire rules, assignment priority, explicit-date
resolution and vacancy routing without porting delivery side effects.

One source defect is intentionally strengthened rather than copied. ERP's two
partial unique indexes reject only open-ended primary assignments. The module
also checks intervals in its service and its PostgreSQL trigger serializes both
employee and position identities before rejecting any overlapping finite or
open-ended primary interval. Vacancy is derived from those dated assignments;
there is no `is_vacant` column to drift.

This checkpoint is implementation evidence only. The kernel/module versions
remain unreleased, Backoffice has not composed them, no backfill or shadow has
run, and ERP remains the sole authoritative employment writer.
