# People, work and shared workflows

- Short explanations of package responsibilities, not a claim of production readiness.
- Check the [catalog](../MODULE_CATALOG.md) for installation, release and adoption evidence.
- A **tenant** is a customer workspace; the **platform** is the operator control plane.
- [All module groups](index.md).

## Employee directory (`dotmac-people`)

- **What it does:** Manages employee relationships, departments, positions and reporting lines.
- **Use it when:** Building an employment directory.
- **Keep in mind:** It does not own payroll calculation or workforce dispatch.
- **Details:** [Package guide](../../packages/dotmac-people/README.md) · [Source and contract](../../packages/dotmac-people/EXTRACTION.toml).

## Workforce (`dotmac-workforce`)

- **What it does:** Tracks teams, skills, shifts, availability and dispatch decisions.
- **Use it when:** Choosing who can carry out operational work.
- **Keep in mind:** Employee identity and the work item's lifecycle belong to their own modules.
- **Details:** [Package guide](../../packages/dotmac-workforce/README.md) · [Source and contract](../../packages/dotmac-workforce/EXTRACTION.toml).

## Projects (`dotmac-projects`)

- **What it does:** Manages projects, tasks, dependencies, scheduling and assignments.
- **Use it when:** Organizing a project and its related tasks.
- **Keep in mind:** It does not become the owner of every business process linked to a task.
- **Details:** [Package guide](../../packages/dotmac-projects/README.md) · [Source and contract](../../packages/dotmac-projects/EXTRACTION.toml).

## Work orders (`dotmac-work-orders`)

- **What it does:** Tracks physical work execution, assignments, logs and evidence.
- **Use it when:** Managing installation, repair or other field work.
- **Keep in mind:** Product-specific subjects and commercial consequences remain outside the module.
- **Details:** [Package guide](../../packages/dotmac-work-orders/README.md) · [Source and contract](../../packages/dotmac-work-orders/EXTRACTION.toml).

## Ticketing (`dotmac-ticketing`)

- **What it does:** Tracks requests for work, status changes, assignments and comments.
- **Use it when:** Building support or internal request workflows.
- **Keep in mind:** It does not own the customer, asset or service a ticket concerns.
- **Details:** [Package guide](../../packages/dotmac-ticketing/README.md) · [Source and contract](../../packages/dotmac-ticketing/EXTRACTION.toml).

## Approvals (`dotmac-approvals`)

- **What it does:** Records versioned approval rules and content-bound requests and decisions.
- **Use it when:** Requiring review before a significant business action.
- **Keep in mind:** An approval authorizes the owning service to act; it does not perform that action.
- **Details:** [Package guide](../../packages/dotmac-approvals/README.md) · [Source and contract](../../packages/dotmac-approvals/EXTRACTION.toml).

## Workflow runtime (`dotmac-workflow-runtime`)

- **What it does:** Runs resumable workflows against a fixed definition and records checkpoints.
- **Use it when:** Continuing multi-step work after failures with explicit repair evidence.
- **Keep in mind:** It executes supplied definitions rather than owning each participating domain.
- **Details:** [Package guide](../../packages/dotmac-workflow-runtime/README.md) · [Source and contract](../../packages/dotmac-workflow-runtime/EXTRACTION.toml).

## Durable timers (`dotmac-durable-timers`)

- **What it does:** Records durable due times, generations, cancellations and accepted triggers.
- **Use it when:** Revisiting work at a known time without losing scheduling identity.
- **Keep in mind:** It decides when to revisit, not what business action is allowed.
- **Details:** [Package guide](../../packages/dotmac-durable-timers/README.md) · [Source and contract](../../packages/dotmac-durable-timers/EXTRACTION.toml).

## Response-time promises (`dotmac-response-obligations`)

- **What it does:** Tracks response and resolution deadlines, pauses and breaches.
- **Use it when:** Measuring whether a response-time promise was kept.
- **Keep in mind:** Scheduling and escalation policy are separate responsibilities.
- **Details:** [Package guide](../../packages/dotmac-response-obligations/README.md) · [Source and contract](../../packages/dotmac-response-obligations/EXTRACTION.toml).

## Operational escalations (`dotmac-operational-escalations`)

- **What it does:** Applies versioned escalation policy and tracks acknowledgement and resolution.
- **Use it when:** Deciding how an operational issue should be escalated.
- **Keep in mind:** It does not collect measurements or deliver notifications itself.
- **Details:** [Package guide](../../packages/dotmac-operational-escalations/README.md) · [Source and contract](../../packages/dotmac-operational-escalations/EXTRACTION.toml).

## Learning (`dotmac-learning`)

- **What it does:** Provides course and activity structures, learner participation, results, progress and certificates.
- **Use it when:** Building learning delivery and assessment features.
- **Keep in mind:** Some continuation work is prepared source; check the exact release and adoption evidence.
- **Details:** [Package guide](../../packages/dotmac-learning/README.md) · [Source and contract](../../packages/dotmac-learning/EXTRACTION.toml).
