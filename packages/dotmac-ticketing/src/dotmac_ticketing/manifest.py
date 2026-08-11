"""Ticketing's `ModuleManifest` — the second stateful module.

Four fields make it stateful, and they must match this module's row in
`dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` (`TICKETING_MIGRATION_OWNER`)
exactly, or `NamespaceRegistry.from_manifests` refuses the composition at boot:

- `short_code="tkt"` → the derived, read-only schema `mod_tkt`
- `migration_prefix="tk"` → revision ids `tk_0001_…`
- `migration_branch="ticketing"` → how an `alembic_version` row is attributed
- `tables=(...)` → the composed gate rejects a migration creating anything
  outside this declaration, in both directions

**`tables` lists only this module's own tables.** Product link tables generated
by `dotmac_ticketing.linking.link_subject` are NOT declared here and must not be:
they live in the product's schema and lineage, and a module claiming ownership
of a table it does not create would make the live-catalog gate wrong in the
direction that matters.

## Why `core=False`

A product may genuinely not want tickets — the vendor control plane's licensing
surface is a plausible deployment with none. Being non-core means "is this
tenant entitled to it" is a real question with a real answer, enforced by
`require_capability` on the router, rather than a formality.
"""

from __future__ import annotations

from dotmac_kernel.capabilities import CapabilitySpec
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.permissions import PermissionSpec

module = ModuleManifest(
    code="ticketing",
    version="0.1.0a1",
    core=False,
    # ── D1 database identity ────────────────────────────────────────────────
    short_code="tkt",
    migration_prefix="tk",
    migration_branch="ticketing",
    tables=("tickets", "ticket_comments"),
    # ── Entitlement ─────────────────────────────────────────────────────────
    capabilities=(
        CapabilitySpec(
            code="ticketing.use",
            description="Raise, work and resolve tickets.",
            default_granted=True,
        ),
    ),
    # ── Permissions ─────────────────────────────────────────────────────────
    # Split deliberately, on the same reasoning as Template Studio's
    # manage/publish split: reading a ticket, working one, and administering the
    # queue are three different decisions, and collapsing them is how an agent
    # ends up able to delete a customer's history because they could reply to it.
    permissions=(
        PermissionSpec(
            code="ticketing.read",
            description="View tickets and their public comments.",
        ),
        PermissionSpec(
            code="ticketing.work",
            description=(
                "Comment, transition status, and take assignment. The everyday "
                "agent permission."
            ),
        ),
        PermissionSpec(
            code="ticketing.administer",
            description=(
                "Reassign across teams, merge, cancel, and edit another agent's "
                "internal notes."
            ),
        ),
    ),
    audit_actions=(
        "ticketing.ticket.created",
        "ticketing.ticket.status_changed",
        "ticketing.ticket.assigned",
        "ticketing.ticket.merged",
        "ticketing.comment.added",
    ),
)

__all__ = ["module"]
