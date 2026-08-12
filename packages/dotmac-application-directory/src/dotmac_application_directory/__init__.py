"""DotMac Application Directory — a tenant's connected-application portfolio.

The third installable stateful module (ADR-0006 D1), and the permanent owner of
the `ApplicationDescriptor` contract (ADR-0021 §4).

It answers one question: **which applications does this tenant have, how do we
reach each one, and how much do we currently trust what we know about it.**

It does not answer, and must never be extended to answer, who may enter them.
Directory visibility is not authorization (ADR-0021 §3): a binding is inventory,
`ACTIVE` means launchable rather than permitted, and the target application
remains the only writer of its own effective role grants. Desired allocation is
`dotmac-application-access`'s domain, deferred per ADR-0021 §5.

Nor does it own the deployment, the product catalogue, the entitlement or the
remote application. Each of those stays a reference to its owner — the vendor
control plane's `allocations`/`provisioning`, `dotmac_kernel.entitlements`, and
the application itself.

Public surface, in dependency order:

- `descriptor` — `ApplicationDescriptor`, `ApplicationRole`, and the two digests
- `lifecycle` — `BindingState`, `BindingSource`, `ReconciliationStatus`, the
  guarded transitions and `is_launchable`
- `models` — `ApplicationBinding`, bound to `mod_appdir`
- `service` — the one writer of a binding row
- `manifest` — the `ModuleManifest` an assembly composes
"""

from __future__ import annotations

from dotmac_application_directory.descriptor import (
    ApplicationDescriptor,
    ApplicationRole,
    DescriptorError,
)
from dotmac_application_directory.lifecycle import (
    BindingLifecycleError,
    BindingSource,
    BindingState,
    ReconciliationStatus,
    allowed_transitions,
    can_transition,
    is_launchable,
    require_transition,
)
from dotmac_application_directory.manifest import module
from dotmac_application_directory.migrations import versions_dir
from dotmac_application_directory.models import SCHEMA, ApplicationBinding
from dotmac_application_directory.service import (
    BindingAlreadyExists,
    BindingNotFound,
    DirectoryError,
    ReconcileOutcome,
    attach_application,
    get_binding,
    launchable_bindings,
    list_bindings,
    mark_reconciliation_failed,
    reconcile_descriptor,
    transition,
)

__version__ = "0.1.0a1"

__all__ = [
    "SCHEMA",
    "ApplicationBinding",
    "ApplicationDescriptor",
    "ApplicationRole",
    "BindingAlreadyExists",
    "BindingLifecycleError",
    "BindingNotFound",
    "BindingSource",
    "BindingState",
    "DescriptorError",
    "DirectoryError",
    "ReconcileOutcome",
    "ReconciliationStatus",
    "__version__",
    "allowed_transitions",
    "attach_application",
    "can_transition",
    "get_binding",
    "is_launchable",
    "launchable_bindings",
    "list_bindings",
    "mark_reconciliation_failed",
    "module",
    "reconcile_descriptor",
    "require_transition",
    "transition",
    "versions_dir",
]
