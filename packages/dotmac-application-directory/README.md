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
| `ApplicationDescriptor` | what an application publishes about one instance: code, instance, local tenant ref, admin URL, API audience, version, role catalogue, and two digests |
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
    ApplicationDescriptor, ApplicationRole, BindingSource, BindingState,
    attach_application, launchable_bindings, reconcile_descriptor, transition,
)

descriptor = ApplicationDescriptor(
    application_code="sub",
    instance_ref="sub-lagos-1",
    local_tenant_ref="9f2c…",
    admin_url="https://sub.example.net/admin",
    api_audience="https://sub.example.net/api",
    descriptor_version=1,
    roles=(
        ApplicationRole("support.agent", "Support agent", delegable=True),
        ApplicationRole("billing.admin", "Billing administrator", delegable=True),
        # Not delegable: the application declines to let a Workspace
        # administrator ever request it.
        ApplicationRole("owner", "Account owner"),
    ),
)

binding = attach_application(
    db, tenant_id=tenant.id, descriptor=descriptor,
    source=BindingSource.VENDOR_ALLOCATION,
)
transition(db, binding, BindingState.PENDING_VERIFICATION)
transition(db, binding, BindingState.ACTIVE)

# Later, having re-read the descriptor from the application:
outcome = reconcile_descriptor(db, binding, observed, now=utcnow())

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

Requires a kernel that allocates `mod_appdir` in `MIGRATION_OWNER_LEDGER`
(`>=0.1.0a46`); an earlier one refuses the composition at boot with
`UnallocatedNamespaceError`.

## Status

`0.1.0a1`, dossier status `audit-complete` — inventoried, deliberately drawn,
**not yet adopted by anything**. Under ADR-0017 §1 that makes it work in
progress rather than delivered, and `EXTRACTION.toml` says so.
