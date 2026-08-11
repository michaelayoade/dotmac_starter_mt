# ADR-0015: `dotmac_academy_app` is an assembly, or it is a fork

**Status:** Accepted
**Date:** 2026-08-10
**Scope:** `dotmac_academy_app`, with a fleet-wide rule in the Decision.
**Relates to:** ADR-0003 (deployment profiles, the three assemblies), ADR-0013
(the platform declares deployment facts)

## Context

ADR-0003 names three maintained products as thin assemblies on the platform
kernel: the vendor control plane, `dotmac_sub`, and `dotmac_erp`.
`dotmac_academy_app` is not one of them, and has never been decided either way.
It has meanwhile grown into something that is neither — and on 2026-08-10 that
produced three production incidents in a single afternoon.

### What academy actually is

It consumes the kernel's leaf utilities — `db`, `config`, `models`,
`exceptions`, `tenancy` — and reimplements everything structural:

| Layer | Kernel | Academy |
| --- | --- | --- |
| App factory | `create_app` | its own `FastAPI(...)` + its own `lifespan` |
| Middleware | `csrf`, `observability`, `rate_limit`, `security_headers`, `tenant` | **the same five, by the same names** |
| Config | `Settings` | its own, byte-identical for `database_url`, `platform_database_url`, `migration_database_url` |
| Identity | `Tenant`, `TenantDomain`, `Role`, `PartyRole`, `UserCredential`, `AuthSession`, `Base` | `Tenant`, `TenantDomain`, `Role`, `PersonRole`, `UserCredential`, `AuthSession`, `Base` |
| Session/RLS | `db.py` | a fork of `db.py`, retired 2026-08-10 |

This is not a product borrowing a library. It is a **fork of the assembly
layer** that has been drifting since it was taken.

### What the drift cost, measured

Three incidents on 2026-08-10, all the same shape — a kernel behaviour that
academy either did not receive or reimplemented, failing **silently**:

1. **A compliance tool certifying an estate it could not see.** Academy's forked
   `db.py` never gained a non-request session boundary, so every CLI command ran
   with no RLS scope. RLS fails closed, so `audit-banks --tenant-slug dotmac`
   printed `TOTAL 0 0` against a database holding 333 question banks for that
   tenant. `load-banks` was blind the same way and had **deployed nothing for 37
   commits**. Neither command ever errored.

2. **A 502 on deploy.** The deploy recipe had no `poetry install` step, because
   academy's dependencies had never moved. Adopting a kernel package moved them.

3. **A security control configured but not armed.** Academy adopted the kernel's
   `TENANCY=single`. Config validation demanded it and got it. But the kernel
   performs that assertion inside `create_app`'s lifespan, and academy builds its
   own — so `single_tenant_binding()` stayed `None` and the tenant lockdown
   passed every tenant through. Everything *looked* configured.

The third is the one that generalises. Adopting a kernel **setting** is not
adopting the kernel **behaviour** behind it, and nothing in academy's tests or
config validation could tell the difference.

### Why "somewhere in between" is the worst position

Academy is half-converged, and that is strictly worse than either end:

- It reads kernel settings, so its configuration *asserts* kernel behaviour.
- It does not run the kernel's lifespan, so it does not *get* that behaviour.
- Nothing detects the gap, because the configuration is present and valid.

A fully forked academy would at least be honestly self-contained. A fully
converged one would inherit controls automatically. The middle inherits the
claims without the guarantees.

## Decision

**Proposed: `dotmac_academy_app` becomes a fourth ADR-0003 assembly**, and
converges on `create_app`.

And the fleet-wide rule the incidents actually justify, which holds whichever
way academy is decided:

> **An assembly that hand-builds its application does not receive any control
> the kernel performs in `create_app`. Reading a kernel setting is not adopting
> the behaviour behind it.** Either use `create_app`, or explicitly re-run each
> control and prove at runtime that it is armed.

### Sequence

Each step is independently valuable and independently abandonable. Nothing
starts before this ADR is decided, because half-converging is the state that
caused the incidents.

1. **Widen Alembic `target_metadata`** to include the kernel's metadata.
   No behaviour change; without it, autogenerate proposes dropping `tenants`.
2. **Adopt kernel `Base` and `Tenant`.** Removes the two mapped classes for one
   table that academy carries today. Unblocked: academy's `Tenant` was made
   column-identical on 2026-08-10 by moving its two product columns to
   `tenant_entrance_defaults`, and `test_tenant_carries_no_product_columns`
   holds it there.
3. **Adopt the middleware, one at a time, each diff-reviewed.**
   `security_headers`, `csrf`, `rate_limit`, `observability` are expected to be
   near-identical. `tenant` is not — see "What this does not resolve".
4. **Adopt `create_app`.** Last, when little remains to differ. Items 3 and 4 of
   the current backlog dissolve here rather than being solved separately.

### Diff before adopting, every time

**A shared component must be a superset of what it replaces, or adopting it
silently removes a control.** Academy's `TenantResolverMiddleware` carried
`_allow_single_tenant`, a single-tenant lockdown the kernel had no equivalent
of; swapping the kernel's in would have dropped it, and **no test would have
caught it, because an assembly's tests do not know the kernel exists.** That
control is now `TENANCY=single` in kernel 0.1.0a32 — but it got there because
someone diffed, not because anything enforced it.

### Divergence goes upstream, not into an exception list

Every academy-private capability examined on 2026-08-10 turned out to be a
missing kernel capability rather than a genuine local need:

| Academy had privately | Became |
| --- | --- |
| slug-resolving CLI session | `tenant_session_by_slug` (0.1.0a30) |
| bare `SessionLocal` in the resolver | `resolver_session` (0.1.0a32) |
| `_allow_single_tenant` | `TENANCY=single` (0.1.0a32) |

The default assumption should be that a fourth divergence is a fourth gap.

## Consequences

- Academy stops carrying five middleware files, a config, an app factory and an
  identity model that already exist and are already tested elsewhere.
- Kernel fixes reach academy through a version bump instead of not at all.
- The kernel gains a fourth consumer, which raises the bar on its public
  surface — `resolver_session` and `tenant_session_by_slug` both exist because
  academy pressed on it, and both benefit erp and sub.
- Short term, academy's release cadence couples to kernel releases. Those are
  protected `workflow_dispatch` with a human approval gate, so a kernel fix
  academy needs is not self-service.
- Rejecting this ADR is a legitimate outcome, but then the fleet-wide rule above
  becomes mandatory for academy: every kernel control it depends on must be
  re-run in its own lifespan and proven armed at runtime.

## What this does not resolve

- **`Person` vs `Party`.** The kernel generalised person to party so an
  organization can hold a role; academy did not. Only `person_roles` blocks
  kernel-owned roles, and that is a narrow rename — but the full migration
  touches every enrolment, certificate, submission and audit row. It is
  **optional and blocks nothing above**, and worth its cost only if academy
  needs an organization to hold a role. Today nothing does.
- **The resolver's 404 semantics.** Academy 404s every unresolved tenant except
  `/health`; the kernel permits platform paths without a tenant, and bypasses
  `/static/` before touching the database. The second is an improvement academy
  wants. The first is a **loosening**, and the right question is whether
  academy's stricter behaviour is a hardening worth upstreaming as a kernel
  option — not whether academy keeps a private exception.
- **Whether `dotmac_sub` and `dotmac_erp` have the same exposure.** Both define
  their own `Role`/`UserCredential`; erp also defines `Person`/`PersonRole`.
  Neither has been audited against this rule.

## Enforcement

- `tests/architecture/test_kernel_public_surface.py` already AST-scans assembly
  imports — but only under the starter's own `app/`. **A separate repository
  defining its own `Tenant` is caught by nothing today.** Extending that check
  to pinned consumers is the enforcement this ADR needs and does not yet have.
- Academy carries `test_tenant_carries_no_product_columns`, which fails the
  moment a product column returns to `Tenant`.
- The runtime armed-check — assert at startup that each kernel control academy
  depends on is active — is the defence against incident 3 and should exist
  whichever way this ADR is decided. Config validation cannot detect it.
