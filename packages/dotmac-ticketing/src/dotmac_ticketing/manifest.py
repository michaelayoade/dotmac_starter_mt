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
tenant entitled to it" is a real question with a real answer rather than a
formality, and it is what a `ticketing.use` capability will gate once there is a
router to gate. See the comment in the manifest body for why that declaration is
deliberately absent from this release.
"""

from __future__ import annotations

from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(
    code="ticketing",
    version="0.1.0a1",
    core=False,
    # ── D1 database identity ────────────────────────────────────────────────
    short_code="tkt",
    migration_prefix="tk",
    migration_branch="ticketing",
    tables=("tickets", "ticket_comments"),
    # ── No capabilities, permissions or audit actions YET ───────────────────
    # This release ships no routers, and every one of those declarations exists
    # to gate or annotate a route. CI proved the point rather than leaving it to
    # judgement: `test_every_declared_capability_is_enforced_somewhere` failed
    # this manifest with "capability code(s) declared but enforced by no mounted
    # route: ['ticketing.use'] — wire a require_capability guard, or drop the
    # declaration until there is something to gate."
    #
    # Dropping it is the honest half of that instruction. A declared code with
    # no consumer is dead vocabulary that reads as a working gate, which is the
    # failure mode ADR-0008's registries exist to prevent, and it would have
    # shipped as a permanent allowlist entry if the contract were weaker.
    #
    # `ticketing.use`, the read/work/administer permission split, and the five
    # audit actions all land in the release that ships the routers — with the
    # guards that reference them in the same change.
)

__all__ = ["module"]
