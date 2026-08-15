"""Integration's `ModuleManifest` — the first PLATFORM-PLANE-ONLY module.

The four D1 fields must match this module's row in
`dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` (`INTEGRATION_MIGRATION_OWNER`)
exactly, or `NamespaceRegistry.from_manifests` refuses the composition at boot.

## Why `tables` is empty and `platform_tables` is not

ADR-0023 lets a module declare two persistence planes. This one declares only
the platform plane, and the empty tenant tuple is written out rather than
omitted so "this module owns no tenant data" is a STATEMENT a reader and the
live-catalog gate can both see.

A connector installation, its configuration revisions and its capability
bindings are control-plane facts about the fleet's integrations. No product
queries them — products receive provider-neutral capability messages over their
own ports (ADR-0024 § 6) — and none of these rows belongs to a tenant of a
product data plane.

This is a port, not a re-scoping: not one of `dotmac_sub`'s seven integration
tables carries a `tenant_id` either.

## Why `core=False`

A deployment with no external integrations is a real deployment. Being
non-core makes "is this deployment integrating with anything" a question with
an answer rather than a formality.
"""

from __future__ import annotations

from dotmac_kernel.modules import ModuleManifest

from dotmac_integration.models import PLATFORM_TABLES, TENANT_TABLES
from dotmac_integration.retention import RETENTION_PLATFORM_TABLES

module = ModuleManifest(
    code="integration",
    version="0.1.0a3",
    core=False,
    # ── D1 database identity ────────────────────────────────────────────────
    short_code="intg",
    migration_prefix="ig",
    migration_branch="integration",
    tables=TENANT_TABLES,
    # COMPOSED from the areas that own the tables, not one hand-maintained
    # list. `models.PLATFORM_TABLES` is the control-plane and execution
    # machinery; `retention.RETENTION_PLATFORM_TABLES` is the legal-hold
    # ledger. Declaring each beside the code that owns it keeps the declaration
    # honest — and keeps two concurrent slices from editing one tuple.
    platform_tables=PLATFORM_TABLES + RETENTION_PLATFORM_TABLES,
    # ── Declared audit actions ──────────────────────────────────────────────
    # The FIXED set this module writes, declared rather than left as string
    # literals scattered through `operations`. ADR-0008's rule applied to an
    # audit vocabulary: a code with no declaration cannot be reviewed or
    # deprecated, and `write_audit_event` refusing an undeclared action is what
    # makes the declaration load-bearing rather than documentation.
    #
    # Every one of these has a real caller in `dotmac_integration.operations`
    # or `dotmac_integration.lifecycle`; a declared action with no writer is
    # dead vocabulary that reads as a working trail.
    #
    # The three `ingress_endpoint.*` codes are the security-relevant ones: an
    # ingress endpoint key is a BEARER credential, so who minted, rotated or
    # revoked one — and when — is the trail an incident actually reads. The
    # KEY is never in the event; see `lifecycle._endpoint_audit`.
    audit_actions=(
        "integration.delivery.replayed",
        "integration.receipt.replayed",
        "integration.leases.released",
        "integration.ingress_endpoint.minted",
        "integration.ingress_endpoint.rotated",
        "integration.ingress_endpoint.revoked",
        # Retention. A sweep that destroys content leaves a record of what it
        # destroyed and under whose retention period; a hold leaves a record of
        # who forbade it. None of these details carries a payload or a
        # `provider_event_id` — see `retention.py`.
        "integration.retention.payloads.redacted",
        "integration.retention.hold.placed",
        "integration.retention.hold.released",
    ),
    # ── No capabilities or permissions YET ──────────────────────────────────
    # Both exist to gate a ROUTE, and this slice ships none. A declared code
    # with no consumer is dead vocabulary that reads as a working gate — the
    # failure ADR-0008's registries exist to prevent. They land with the
    # operations HTTP surface, which belongs to the `dotmac_integrator`
    # assembly, in the same change as the guards that reference them.
    #
    # Audit actions are different and ARE declared above: they are written by
    # this module's own repair commands, which exist now.
)

__all__ = ["module"]
