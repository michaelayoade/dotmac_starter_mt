"""Release catalogue's `ModuleManifest` — the third stateful module.

Four fields make it stateful, and they must match this module's row in
`dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER`
(`RELEASE_CATALOG_MIGRATION_OWNER`) exactly, or `NamespaceRegistry.from_manifests`
refuses the composition at boot:

- `short_code="rel"` → the derived, read-only schema `mod_rel`
- `migration_prefix="rl"` → revision ids `rl_0001_…`
- `migration_branch="release_catalog"` → how an `alembic_version` row is attributed
- `platform_tables=(...)` → the composed gate rejects a migration creating
  anything outside this declaration, in both directions

## Why the platform plane, declared and atomic

The DDL was always control-plane shaped — no `tenant_id`, no RLS, grants to
`platform_api`/`app_admin` and `REVOKE ALL` from `app_user`. Until ADR-0028 the
manifest still declared those tables under `tables=`, which is the tenant slot.
The declaration was simply wrong about what the migration builds, and ADR-0023
is explicit that the plane is DECLARED and never inferred — so a mismatch here
is a real defect, not a formality.

This module is ATOMIC, and says so by saying nothing. `supported_plane_sets`
is deliberately OMITTED rather than written as an explicit `()`: absence already
means atomic, and the generated catalogue renders it that way.

Omitting it is not only tidier: this atomic module needs no plane-selection
keyword. The distribution floor is now `0.1.0a100` because the database-
catalogue attestation writers consume Kernel's typed snapshot parsers. That
floor is independent of this manifest's plane-selection default.

A singleton `((ModulePlane.PLATFORM,),)` would not make the module selectable
either: the current implementation treats one combination as atomic and rejects
an assembly selection anyway. It would be ceremony without a choice.

There is no tenant consumer, and speculative selectability is the ADR-0006 § 5
speculative extraction wearing different clothes. If a real tenant consumer ever
appears, that is a capability EXPANSION needing product-first evidence, tenant
models and migrations, RLS canaries and a new release.

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
from dotmac_kernel.product_database_catalog import (
    DatabaseColumnContractV1,
    DatabaseColumnGeneration,
    DatabaseRelationKind,
    ModuleDatabaseCatalogContributionV1,
    ModuleDatabaseTableContractV1,
    PostgresTypeContractV1,
    PostgresTypeKind,
)

_PG_CATALOG = "pg_catalog"


def _base_type(name: str, formatted: str) -> PostgresTypeContractV1:
    return PostgresTypeContractV1(
        kind=PostgresTypeKind.BASE, schema=_PG_CATALOG, name=name, formatted=formatted
    )


_UUID = _base_type("uuid", "uuid")
_INT8 = _base_type("int8", "bigint")
_TEXT = _base_type("text", "text")
_TIMESTAMPTZ = _base_type("timestamptz", "timestamp with time zone")


def _varchar(length: int) -> PostgresTypeContractV1:
    return _base_type("varchar", f"character varying({length})")


def _col(
    name: str, ordinal: int, pg_type: PostgresTypeContractV1, *, nullable: bool
) -> DatabaseColumnContractV1:
    return DatabaseColumnContractV1(
        name=name, ordinal=ordinal, postgres_type=pg_type, nullable=nullable
    )


def _timestamp_col(
    name: str, ordinal: int, *, nullable: bool = False
) -> DatabaseColumnContractV1:
    if nullable:
        return DatabaseColumnContractV1(
            name=name, ordinal=ordinal, postgres_type=_TIMESTAMPTZ, nullable=True
        )
    return DatabaseColumnContractV1(
        name=name,
        ordinal=ordinal,
        postgres_type=_TIMESTAMPTZ,
        nullable=False,
        generation=DatabaseColumnGeneration.DEFAULT,
        expression="now()",
    )


# Facts observed live against PostgreSQL 16 (kernel + assembly + this module's
# migrations composed to head `rl_0002_db_catalog_attestations`) via
# `dotmac_kernel.database_catalog_comparator.observe_postgres_tables_columns`,
# self-verified with `compare_module_database_catalog` before being frozen
# here. Never hand-derived from the migration source. `rl_0002` adds only
# partial unique indexes (outside V1's tables-and-columns scope), so it does
# not change this module's column facts — only its lineage head.
_RELEASE_ARTIFACTS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("product_code", 2, _varchar(120), nullable=False),
    _col("version", 3, _varchar(120), nullable=False),
    _col("artifact_kind", 4, _varchar(40), nullable=False),
    _col("digest", 5, _varchar(160), nullable=False),
    _col("artifact_ref", 6, _TEXT, nullable=False),
    _col("size_bytes", 7, _INT8, nullable=True),
    _col("source_revision", 8, _varchar(120), nullable=True),
    _timestamp_col("published_at", 9, nullable=True),
    _timestamp_col("created_at", 10),
    _timestamp_col("updated_at", 11),
)
_ARTIFACT_ATTESTATIONS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("artifact_id", 2, _UUID, nullable=False),
    _col("attestation_kind", 3, _varchar(40), nullable=False),
    _col("uri", 4, _TEXT, nullable=False),
    _col("digest", 5, _varchar(160), nullable=False),
    _timestamp_col("created_at", 6),
    _timestamp_col("updated_at", 7),
)

_DATABASE_CATALOG = ModuleDatabaseCatalogContributionV1(
    lineage_head="rl_0002_db_catalog_attestations",
    tables=(
        ModuleDatabaseTableContractV1(
            name="artifact_attestations",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_ARTIFACT_ATTESTATIONS_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="release_artifacts",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_RELEASE_ARTIFACTS_COLUMNS,
        ),
    ),
)

module = ModuleManifest(
    code="release_catalog",
    version="0.1.0a4+dev",
    core=False,
    # ── D1 database identity ────────────────────────────────────────────────
    short_code="rel",
    migration_prefix="rl",
    migration_branch="release_catalog",
    tables=(),
    platform_tables=("release_artifacts", "artifact_attestations"),
    database_catalog=_DATABASE_CATALOG,
)

__all__ = ["module"]
