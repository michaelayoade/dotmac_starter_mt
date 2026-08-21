# ADR-0051: Projects own work structure, not product consequences

**Status:** Accepted
**Date:** 2026-08-18
**Decision owner:** Michael
**Scope:** FLEET-WIDE. Applies to every Dotmac application that manages a
project/task graph and to the reusable `dotmac-projects` module.
**Relates to:** ADR-0006 (product-first extraction), ADR-0008 (declaration
registries), ADR-0010 (thin adapters), ADR-0014 (at-most-once execution),
ADR-0017 (adoption is scarce), ADR-0023 (persistence planes), ADR-0024
(applications synchronize data), ADR-0031 (authority cutover evidence)
**Evidence:** [`projects-sources.md`](../inventories/projects-sources.md)

## Context

ERP, CRM and Sub each contain projects, tasks, templates, assignments and task
dependencies. Their project subjects differ: ERP manages internal delivery and
finance-linked work, while Sub and its CRM predecessor manage ISP installation
and buildout work. The fleet inventory nevertheless found ten exact table-name
collisions and the same four dependency types in ERP and Sub.

Keeping three copies preserves parallel lifecycle and graph writers. Combining
every product-specific field into one project model would instead transfer
finance, subscriber, installation and work-order authority into a generic
package. The reusable unit is the work structure between those extremes.

Sub is the qualifying source. It has a checked-in source-of-truth decision,
focused lifecycle and architecture tests, lock ordering, stale-state refusal,
same-project relationship guards, cycle detection and dependency-completion
checks. CRM is the retiring ancestor of that implementation. ERP supplies the
second-consumer requirements and broader project-management exclusions, but it
has no focused service proof comparable to Sub's.

## Decision

### 1. `dotmac-projects` owns the product-neutral work aggregate

`dotmac-projects` is a stateful, independently versioned, tenant-plane module.
Every adopting application installs its own `pj` lineage in its own database
and owns its local rows. No application reads another application's project
schema.

The module owns:

- project and task identity, active state and product-neutral status;
- project/task lifecycle transitions, including expected-state and terminal
  refusal;
- task hierarchy inside one project;
- dependency identity, type, lag, same-project integrity and acyclicity;
- refusal to complete a task while an active dependency is incomplete;
- reusable templates, template tasks and dependency graphs;
- deterministic template instantiation and schedule constraints; and
- opaque task-assignment membership.

Services mutate and flush inside the caller-owned transaction. They never
commit, roll back, deliver notifications or call another application.

### 2. Products own subjects and consequences

The module owns no subscriber, customer, lead, quote, sales order, buildout,
work order, ticket, service, warehouse, cost centre, business unit or project
type. It owns no budget, actual cost, time entry, milestone, resource-capacity,
SLA, escalation, notification, document or numbering decision.

An adopter owns a local relation from its business subject to the local module
project id and validates that relation through its service. Actor and assignee
ids in the module are opaque UUIDs; the module has no foreign key to a product
identity table or sibling module. Product services decide and record every
business consequence, then use the outbox for non-transactional delivery.

### 3. Dependencies are executable constraints, not labels

The public dependency vocabulary is finish-to-start, start-to-start,
finish-to-finish and start-to-finish, with explicit lag. Template scheduling
honours each type. This is a mandatory correction to the source behavior: Sub
stores all four values but its legacy date calculator treats every edge as
zero-lag finish-to-start; ERP reports the types in Gantt output but provides no
equivalent scheduling proof.

Dependency replacement is atomic. Duplicate, self, inactive, cross-project and
cyclic edges are refused before the old set is deleted. A task lock is acquired
only after its owning project is identified and locked, preserving aggregate
lock order.

### 4. Sub is cutover 1; ERP is the second candidate

Sub first expands with the module lineage and a product-owned subject link,
then shadows project/task/template/dependency decisions in the same
transaction. Cutover requires zero unexplained differences, cross-tenant
PostgreSQL proof, a one-writer seal and verified product consequence adapters.
The local generic project/task/template/dependency/assignment writer is then
retired; subscriber, installation, vendor, work-order, SLA, notification and
cost owners remain in Sub.

CRM's project copy retires through the Sub cutover and is not an independent
consumer. ERP follows on a released exact pin, with an explicit status mapping.
ERP retains finance, budgeting, costing, milestones, resource allocation, time
entry and expense relations. Its existing untested web mutations are
requirements to characterize, not parity evidence.

### 5. The initial module is tenant-only

The source applications need a tenant data plane today and no named control
plane application needs projects. The manifest therefore declares only tenant
tables. A platform plane may be added only with a real assembly, supported
plane selection, separate platform tables and the full ADR-0023 isolation
contract; a nullable or sentinel tenant is never an alternative.

## Consequences

- The fleet's previously contested projects row is assigned to
  `dotmac-projects`, sourced product-first from Sub.
- CRM's copy is retirement evidence, not a third authority.
- ERP and Sub share a contract and implementation but never a database.
- Product-specific project screens and adapters may differ while delegating
  structural decisions to the same owner.
- Scheduling semantics, transaction ownership, tenant isolation and graph
  integrity are canaries in the first package version, not deferred cleanup.

## Alternatives rejected

### Keep independent ERP and Sub project engines

Rejected because their project subjects differ but their task/template/DAG
mechanism does not. Keeping both mechanics creates two owners for reusable
graph and lifecycle behavior.

### Move all project-management behavior into the module

Rejected because ERP finance/resource management and Sub installation/work
consequences are separately authoritative domains. Shared fields do not move
those decisions.

### Treat CRM as a second consumer

Rejected because CRM and Sub are one consolidating lineage. A fork proves
duplication, not reuse.
