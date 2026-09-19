"""Entitlement allocation's `ModuleManifest` — the fourth stateful module.

Must match `ENTITLEMENT_ALLOCATION_MIGRATION_OWNER` in the kernel ledger exactly
or `NamespaceRegistry.from_manifests` refuses the composition at boot:
`short_code="ealloc"` → `mod_ealloc`, `migration_prefix="ea"` → `ea_0001_…`,
`migration_branch="entitlement_allocation"`, and `tables` bounding what the
composed gate will accept the migration creating.

## `core=False`, and vendor-assembly-only beyond that

Ruling C4 splits allocation from granting: the control plane ALLOCATES, the
product data plane is the only writer of its own `tenant_entitlement_grants`.
A data plane installing this module would be acquiring the wrong half. Being
non-core is the first guard; the import-linter contract forbidding the assembly
from importing it is the second.

## One audit action, declared because it HAS a consumer

`entitlement_allocation.staged` is written by `service.stage_allocation` inside
its idempotent operation, so the declaration is live rather than aspirational —
which is the whole test ADR-0008's registries apply: a declared code with no
consumer is dead vocabulary that reads as a working gate.

Capabilities and permissions stay undeclared for exactly the same reason
inverted: this release ships no routers, so there is nothing for them to gate.
`dotmac-ticketing` proved the point the loud way — CI rejected a declared
capability code no mounted route enforced. They land with the routers, in the
same change as the guards that reference them.
"""

from __future__ import annotations

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import IDEMPOTENCY_LEDGER_V1, PLATFORM_AUDIT_LOG_V1
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
_INT4 = _base_type("int4", "integer")
_BOOL = _base_type("bool", "boolean")
_TIMESTAMPTZ = _base_type("timestamptz", "timestamp with time zone")


def _varchar(length: int) -> PostgresTypeContractV1:
    return _base_type("varchar", f"character varying({length})")


def _col(
    name: str, ordinal: int, pg_type: PostgresTypeContractV1, *, nullable: bool
) -> DatabaseColumnContractV1:
    return DatabaseColumnContractV1(
        name=name, ordinal=ordinal, postgres_type=pg_type, nullable=nullable
    )


def _timestamp_col(name: str, ordinal: int) -> DatabaseColumnContractV1:
    return DatabaseColumnContractV1(
        name=name,
        ordinal=ordinal,
        postgres_type=_TIMESTAMPTZ,
        nullable=False,
        generation=DatabaseColumnGeneration.DEFAULT,
        expression="now()",
    )


def _bool_default_false(name: str, ordinal: int) -> DatabaseColumnContractV1:
    return DatabaseColumnContractV1(
        name=name,
        ordinal=ordinal,
        postgres_type=_BOOL,
        nullable=False,
        generation=DatabaseColumnGeneration.DEFAULT,
        expression="false",
    )


# Facts observed live against PostgreSQL 16 (kernel + assembly + this module's
# migrations composed to head `ea_0003_platform_audit_log`) via
# `dotmac_kernel.database_catalog_comparator.observe_postgres_tables_columns`,
# self-verified with `compare_module_database_catalog` before being frozen
# here. Never hand-derived from the migration source.
_ALLOCATIONS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("contract_ref", 2, _UUID, nullable=False),
    _col("product_code", 3, _varchar(120), nullable=False),
    _col("customer_ref", 4, _varchar(200), nullable=False),
    _col("content_hash", 5, _varchar(128), nullable=False),
    _col("status", 6, _varchar(20), nullable=False),
    _col("source_event_id", 7, _varchar(200), nullable=False),
    _col("snapshot_fingerprint", 8, _varchar(64), nullable=False),
    _timestamp_col("created_at", 9),
    _timestamp_col("updated_at", 10),
    _bool_default_false("sealed", 11),
)
_ALLOCATION_ENTRIES_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("allocation_id", 2, _UUID, nullable=False),
    _col("capability_code", 3, _varchar(120), nullable=False),
    _col("quantity", 4, _INT4, nullable=False),
    _timestamp_col("created_at", 5),
    _timestamp_col("updated_at", 6),
)

_DATABASE_CATALOG = ModuleDatabaseCatalogContributionV1(
    lineage_head="ea_0003_platform_audit_log",
    tables=(
        ModuleDatabaseTableContractV1(
            name="allocation_entries",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_ALLOCATION_ENTRIES_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="allocations",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_ALLOCATIONS_COLUMNS,
        ),
    ),
)

module = ModuleManifest(
    code="entitlement_allocation",
    version="0.1.0a6+dev",
    core=False,
    short_code="ealloc",
    migration_prefix="ea",
    migration_branch="entitlement_allocation",
    tables=(),
    platform_tables=("allocations", "allocation_entries"),
    database_catalog=_DATABASE_CATALOG,
    # ── Logical database prerequisites ──────────────────────────────────────
    # The TWO effects this module needs that its own migrations do not create.
    # `stage_allocation` delegates at-most-once to the kernel (hard rule 21,
    # ADR-0014), so `public.platform_idempotency_records` is written at REQUEST
    # time and nothing in `ea_0001` touches it. Undeclared — every release up
    # to and including `0.1.0a4` — an adopter that runs its own lineage and
    # never ran the kernel's passes every gate this module has, migrates
    # cleanly, and dies on `UndefinedTable` at the first staged activation. A
    # runtime dependency is still a dependency; it just has no DDL to betray
    # it. Same defect as `dotmac-numbering` 0.1.0a1 and `dotmac-integration`
    # 0.1.0a1..a3, found by the kernel persisted-runtime-dependency inventory
    # and named by kernel a66.
    #
    # COMMON, not `platform_requires`, and the reason is the same one
    # integration reached rather than the one numbering had. Numbering is
    # plane-SELECTABLE and both of its planes call one of the pair. This module
    # has exactly one plane: `tables` is empty and `supported_plane_sets` is
    # unset, so the declared platform plane is installed atomically and there
    # is no selection under which the requirement could lapse. A
    # plane-conditional list would be conditioning on something that cannot
    # vary — and `resolve_depends_on` cannot even resolve one here, because a
    # plane list needs `module=`, which reads `selected_module_planes`, which
    # no atomic module may have (`validate_module_plane_selections` refuses a
    # selection when only one plane set is supported). The spec is whole in any
    # case: one name, both ledgers, as `IDEMPOTENCY_LEDGER_V1.summary` states.
    #
    # `write_platform_audit_event` writes `public.platform_audit_events` from
    # inside the same operation. Kernel a68 names and verifies that second
    # effect. `ea_0002` and `ea_0003` verify the two provider effects in their
    # provider-lineage order rather than asking one consumer revision to depend
    # on both an ancestor and its descendant.
    requires=(IDEMPOTENCY_LEDGER_V1.name, PLATFORM_AUDIT_LOG_V1.name),
    audit_actions=("entitlement_allocation.staged",),
)

__all__ = ["module"]
