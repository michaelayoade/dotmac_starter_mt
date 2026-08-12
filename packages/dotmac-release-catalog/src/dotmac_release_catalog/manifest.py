"""Release catalogue's `ModuleManifest` — the third stateful module.

Four fields make it stateful, and they must match this module's row in
`dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER`
(`RELEASE_CATALOG_MIGRATION_OWNER`) exactly, or `NamespaceRegistry.from_manifests`
refuses the composition at boot:

- `short_code="rel"` → the derived, read-only schema `mod_rel`
- `migration_prefix="rl"` → revision ids `rl_0001_…`
- `migration_branch="release_catalog"` → how an `alembic_version` row is attributed
- `tables=(...)` → the composed gate rejects a migration creating anything
  outside this declaration, in both directions

## Why `core=False`

Most deployments have no business holding a vendor's release catalogue. It is
installed in a vendor or OEM control-plane assembly and nowhere else — the fleet
parts are deliberately not something a product data plane can compose, and being
non-core is the first half of making that true. The second half is the
import-linter contract that fails the build if a data-plane assembly declares it.

## No capabilities, permissions or audit actions YET

This release ships no routers, and every one of those declarations exists to
gate or annotate a route. `dotmac-ticketing` learned this the loud way: CI
rejected a declared capability code that no mounted route enforced. A declared
code with no consumer is dead vocabulary that reads as a working gate — the
exact failure ADR-0008's registries exist to prevent.

`release_catalog.publish` / `.read` and the publish/attest audit actions land in
the release that ships the routers, with the guards that reference them in the
same change.
"""

from __future__ import annotations

from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(
    code="release_catalog",
    version="0.1.0a1",
    core=False,
    # ── D1 database identity ────────────────────────────────────────────────
    short_code="rel",
    migration_prefix="rl",
    migration_branch="release_catalog",
    tables=("release_artifacts", "artifact_attestations"),
)

__all__ = ["module"]
