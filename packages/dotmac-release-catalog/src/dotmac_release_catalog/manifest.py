"""Release catalogue's `ModuleManifest` — the third stateful module.

Four fields make it stateful, and they must match this module's row in
`dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER`
(`RELEASE_CATALOG_MIGRATION_OWNER`) exactly, or `NamespaceRegistry.from_manifests`
refuses the composition at boot:

- `short_code="rel"` → the derived, read-only schema `mod_rel`
- `migration_prefix="rl"` → revision ids `rl_0001_…`
- `migration_branch="release_catalog"` → how an `alembic_version` row is attributed
- `tables=(...)` / `platform_tables=(...)` → the composed gate rejects a
  migration creating anything outside these declarations, in both directions

## Both tables are PLATFORM-plane (ADR-0023), and now say so

Until `0.1.0a4` this manifest declared both tables in the TENANT tuple, while
`rl_0001` created neither with row-level security and REVOKEd both from
`app_user`. Every word of prose in this package — and the kernel's own ledger
comment beside `RELEASE_CATALOG_MIGRATION_OWNER` — already called them platform
catalog tables; only the declaration disagreed.

The disagreement survived because this repository composes the module in no
assembly, so the live-catalog gate never walked `mod_rel`. It would have failed
at the first vendor control plane that DID compose it: `audit_snapshot` requires
RLS ENABLEd AND FORCEd plus a policy for every table not declared platform, so a
correct migration would have been reported as a broken one — in an adopter's
deployment rather than here. `tests/test_release_catalog_platform_plane.py` now
runs that gate against a real scratch database, so the claim is proven where it
is made rather than deferred to a consumer.

`tables=()` is written out rather than omitted, exactly as `dotmac-integration`
writes it: "this module owns no tenant data" is a STATEMENT a reader and the gate
can both see, not an absence either could read as an oversight.

## Not a selectable module (ADR-0028)

No `supported_plane_sets`, deliberately. The one supported combination is
platform-only, so `dotmac_kernel.planes.supported_plane_sets` derives it and the
contract stays ATOMIC — an assembly composing this module makes no plane choice,
because there is no second answer to choose between. Declaring a single-element
`supported_plane_sets` would ask every consumer to restate the only possibility.

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
    version="0.1.0a4",
    core=False,
    # ── D1 database identity ────────────────────────────────────────────────
    short_code="rel",
    migration_prefix="rl",
    migration_branch="release_catalog",
    tables=(),
    platform_tables=("release_artifacts", "artifact_attestations"),
)

__all__ = ["module"]
