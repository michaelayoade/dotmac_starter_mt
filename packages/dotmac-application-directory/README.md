# dotmac-application-directory

A tenant's **connected-application portfolio**: which applications this customer
has, how to reach each one, which tenant it corresponds to inside the target,
and how much the stored descriptor can currently be trusted.

The third installable stateful module (ADR-0006 D1), and the permanent owner of
the `ApplicationDescriptor` contract (ADR-0021 §4).

## The one thing to understand first

> **Directory visibility is not authorization.**

A binding is inventory. `ACTIVE` means *the tenant has this application and our
record of it is believable* — it says nothing about whether the person looking
at the screen may enter. A launcher renders a link; the target application
authenticates and authorizes whoever follows it, and remains the only writer of
its own effective role grants (ADR-0021 §3).

The module has no column naming a person, member, group, role, grant or
permission, and it may never acquire one. That is enforced, not requested:
`tests/architecture/test_application_directory_module.py
::test_the_directory_holds_no_authorization_column` fails the build on one.

Desired allocation — who *should* have access — belongs to
`dotmac-application-access`, deferred by ADR-0021 §5 until a generic
signed-document mechanism exists in the kernel.

## What it owns

| | |
|---|---|
| `ApplicationDescriptor` | what an application publishes about one instance: code, instance, local tenant ref, admin URL, API audience, version, and one content digest |
| `ApplicationBinding` | one row per connected instance, in `mod_appdir` |
| `BindingState` | `invited → pending_verification → active ⇄ suspended`, `detached` terminal |
| `BindingSource` | `vendor_allocation`, `oem_allocation`, `customer_attached` — provenance, never authority |
| `ReconciliationStatus` | `unknown`, `fresh`, `stale`, `failed` |

## What it does not own

The deployment, the product catalogue, the entitlement, the remote application —
each stays a reference to its owner (the vendor control plane's
`allocations`/`provisioning`, `dotmac_kernel.entitlements`, and the application
itself). See `docs/inventories/application-portfolio-sources.md` for the full
concept-to-owner table.

## Usage

```python
from dotmac_application_directory import (
    ApplicationDescriptor, BindingSource, BindingState,
    activate_binding, attach_application, launchable_bindings,
    reconcile_descriptor, transition,
)

descriptor = ApplicationDescriptor(
    application_code="sub",
    instance_ref="sub-lagos-1",
    local_tenant_ref="9f2c…",
    admin_url="https://sub.example.net/admin",
    api_audience="https://sub.example.net/api",
    descriptor_version=1,
)

# Always created INVITED and unverified. There is no `state` argument.
binding = attach_application(
    db, tenant_id=tenant.id, descriptor=descriptor,
    source=BindingSource.VENDOR_ALLOCATION,
)

# ACTIVE requires PROOF: a descriptor actually read from the application.
# `transition` refuses to produce ACTIVE at all.
activate_binding(
    db, tenant_id=tenant.id, binding_id=binding.id,
    observed=read_from_application(), now=utcnow(),
)

# Later, having re-read the descriptor again:
outcome = reconcile_descriptor(
    db, tenant_id=tenant.id, binding_id=binding.id,
    observed=observed, now=utcnow(),
)

for b in launchable_bindings(db, tenant_id=tenant.id):
    ...  # render a tile linking to b.admin_url
```

## Reconciliation refuses more than it accepts

`reconcile_descriptor` returns one of four outcomes, and only two of them adopt
the observed descriptor:

| Outcome | Adopted? | Meaning |
|---|---|---|
| `UNCHANGED` | yes (no-op) | identical to the stored copy |
| `UPDATED` | yes | newer version; copy replaced |
| `REGRESSED` | **no** | application reported a version behind the stored one — usually a lagging replica. Marked `stale`, copy kept |
| `CONFLICT` | **no** | same version, different content. A defect or tampering; marked `failed` |

A failed read never moves `descriptor_refreshed_at`, so an unreachable
application cannot look freshly checked.

## Binding identity is immutable

`(tenant_id, application_code, instance_ref, local_tenant_ref)` identifies the
binding, and reconciliation never rewrites any of it. A descriptor disagreeing on
any of the three application-side values is refused — `DirectoryError`, raised
**before** anything is written — because it describes a different instance, not a
newer version of this one.

`local_tenant_ref` is the load-bearing member. Without it, a live binding could
adopt a newer descriptor naming a different local tenant and stay launchable,
silently re-pointing a tenant administrator's tile at another tenant's instance
inside the same application. Version and digest checks cannot catch that: a
genuine version bump carrying a changed local tenant passes both.

## No role catalogue yet

`ApplicationRole`, `delegable_role_codes` and a separate role-catalogue digest
are deferred: nothing consumes them. The access module that would is deferred by
ADR-0021 §5, and the launcher never reads a role. They return with that slice,
designed against its real needs rather than guessed ahead of them.

## ACTIVE carries proof, and mutations lock the row

`ACTIVE` is reachable only through `activate_binding`, which requires a
descriptor read from the application and refuses if reconciling it would not
adopt, or if the application names a different local tenant than the binding was
created for. `transition` refuses `ACTIVE` outright. There is no `state`
argument on `attach_application`: a binding that is launchable and has never
been verified is not a state this module can produce.

Every mutation takes `(tenant_id, binding_id)` and re-reads the row `FOR UPDATE`
rather than accepting a caller-supplied object. Two reconcilers reading v1
concurrently — one observing v2, the other v3 — would otherwise both pass their
version checks against the stale copy they hold, and the last to commit wins. The
same race let a suspend land after a detach and resurrect a disconnected binding.
Proven by the PostgreSQL concurrency canaries; SQLite omits `FOR UPDATE`
silently, so the unit lane cannot show it.

## Composition

```python
import dotmac_application_directory

assembly = ProductAssemblySpec(
    name="dotmac_workspace",
    modules=[..., dotmac_application_directory.module],
)
```

and the lineage in the assembly's `alembic.ini`:

```
version_locations = …
    .../dotmac_application_directory/migrations/versions
```

Requires `dotmac-kernel >=0.1.0a56`, the release that added the logical
prerequisite contract this module's root revision declares through `requires`.
A kernel below it cannot import the manifest. The `mod_appdir` allocation in
`MIGRATION_OWNER_LEDGER` landed earlier, in `0.1.0a46`; a kernel without it
refuses the composition at boot with `UnallocatedNamespaceError`.

## Status

`0.1.0a3`, dossier status `adopted` — the Tenant Workspace exact-pins the
module and exercised the directory-backed launcher in its 2026-08-16
production pilot at `workspace.dotmac.io`. This proves one consumer, not reuse;
`reuse-proven` still requires a second real production consumer.

The adoption does not make the Workspace an authorization or shared-session
authority. The directory records application inventory only; authentication
stays at the Workspace boundary and every target application owns its own
authorization and session.
