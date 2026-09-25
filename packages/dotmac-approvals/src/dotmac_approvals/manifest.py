"""Installable module declaration for the approval-state owner.

`mod_approvals` / prefix `ap` / branch label `approvals` are allocated in
`dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` (`APPROVALS_MIGRATION_OWNER`,
kernel `0.1.0a59`), and this manifest must match that row exactly or the module
cannot register at all.

Both plane tuples are populated. That is the ADR-0023 case this module exists to
demonstrate honestly: approvals are a real tenant capability in ERP AND a real
control-plane capability in the vendor control plane, so both planes are
DECLARED rather than one being inferred from a missing column.
"""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.planes import ModulePlane
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    TENANT_SCOPE_CATALOG_V1,
)
from dotmac_kernel.product_database_catalog import (
    DatabaseColumnContractV1,
    DatabaseColumnGeneration,
    DatabaseRelationKind,
    ModuleDatabaseCatalogContributionV1,
    ModuleDatabaseTableContractV1,
    PostgresTypeContractV1,
    PostgresTypeKind,
)

from dotmac_approvals.models import PLATFORM_TABLES, TENANT_TABLES

_PG_CATALOG = "pg_catalog"


def _base_type(name: str, formatted: str) -> PostgresTypeContractV1:
    return PostgresTypeContractV1(
        kind=PostgresTypeKind.BASE, schema=_PG_CATALOG, name=name, formatted=formatted
    )


_UUID = _base_type("uuid", "uuid")
_INT4 = _base_type("int4", "integer")
_BOOL = _base_type("bool", "boolean")
_TEXT = _base_type("text", "text")
_TIMESTAMPTZ = _base_type("timestamptz", "timestamp with time zone")
_JSON = _base_type("json", "json")


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
# migrations composed to head `ap_0002_outbox_relay`, both planes selected) via
# `dotmac_kernel.database_catalog_comparator.observe_postgres_tables_columns`,
# self-verified with `compare_module_database_catalog` before being frozen
# here. Never hand-derived from the migration source.
_APPROVAL_POLICIES_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("tenant_id", 2, _UUID, nullable=False),
    _col("policy_code", 3, _varchar(120), nullable=False),
    _col("version", 4, _INT4, nullable=False),
    _col("levels", 5, _JSON, nullable=False),
    _bool_default_false("allow_self_approval", 6),
    _col("document_digest", 7, _varchar(71), nullable=False),
    _timestamp_col("created_at", 8),
    _timestamp_col("updated_at", 9),
)
_APPROVAL_REQUESTS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("tenant_id", 2, _UUID, nullable=False),
    _col("policy_code", 3, _varchar(120), nullable=False),
    _col("policy_version", 4, _INT4, nullable=False),
    _col("subject_type", 5, _varchar(120), nullable=False),
    _col("subject_id", 6, _varchar(200), nullable=False),
    _col("content_digest", 7, _varchar(71), nullable=False),
    _col("requested_by", 8, _UUID, nullable=False),
    _col("state", 9, _varchar(16), nullable=False),
    _col("current_level", 10, _INT4, nullable=False),
    _col("idempotency_key", 11, _varchar(200), nullable=False),
    _col("completed_at", 12, _TIMESTAMPTZ, nullable=True),
    _col("note", 13, _TEXT, nullable=True),
    _timestamp_col("created_at", 14),
    _timestamp_col("updated_at", 15),
)
_APPROVAL_DECISIONS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("tenant_id", 2, _UUID, nullable=False),
    _col("request_id", 3, _UUID, nullable=False),
    _col("level", 4, _INT4, nullable=False),
    _col("actor_id", 5, _UUID, nullable=False),
    _col("action", 6, _varchar(16), nullable=False),
    _col("comment", 7, _TEXT, nullable=True),
    _col("delegated_from", 8, _UUID, nullable=True),
    _bool_default_false("mfa_verified", 9),
    _col("decided_at", 10, _TIMESTAMPTZ, nullable=False),
    _timestamp_col("created_at", 11),
    _timestamp_col("updated_at", 12),
)
_PLATFORM_APPROVAL_POLICIES_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("policy_code", 2, _varchar(120), nullable=False),
    _col("version", 3, _INT4, nullable=False),
    _col("levels", 4, _JSON, nullable=False),
    _bool_default_false("allow_self_approval", 5),
    _col("document_digest", 6, _varchar(71), nullable=False),
    _timestamp_col("created_at", 7),
    _timestamp_col("updated_at", 8),
)
_PLATFORM_APPROVAL_REQUESTS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("policy_code", 2, _varchar(120), nullable=False),
    _col("policy_version", 3, _INT4, nullable=False),
    _col("subject_type", 4, _varchar(120), nullable=False),
    _col("subject_id", 5, _varchar(200), nullable=False),
    _col("content_digest", 6, _varchar(71), nullable=False),
    _col("requested_by", 7, _UUID, nullable=False),
    _col("state", 8, _varchar(16), nullable=False),
    _col("current_level", 9, _INT4, nullable=False),
    _col("idempotency_key", 10, _varchar(200), nullable=False),
    _col("completed_at", 11, _TIMESTAMPTZ, nullable=True),
    _col("note", 12, _TEXT, nullable=True),
    _timestamp_col("created_at", 13),
    _timestamp_col("updated_at", 14),
)
_PLATFORM_APPROVAL_DECISIONS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("request_id", 2, _UUID, nullable=False),
    _col("level", 3, _INT4, nullable=False),
    _col("actor_id", 4, _UUID, nullable=False),
    _col("action", 5, _varchar(16), nullable=False),
    _col("comment", 6, _TEXT, nullable=True),
    _col("delegated_from", 7, _UUID, nullable=True),
    _bool_default_false("mfa_verified", 8),
    _col("decided_at", 9, _TIMESTAMPTZ, nullable=False),
    _timestamp_col("created_at", 10),
    _timestamp_col("updated_at", 11),
)

# Candidate ap_0003 declaration. These column coordinates are UNVERIFIED until
# Git-hosted PostgreSQL 16 observes and compares the migrated catalogue.
_APPROVAL_WITHDRAWALS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("tenant_id", 2, _UUID, nullable=False),
    _col("request_id", 3, _UUID, nullable=False),
    _col("actor_id", 4, _UUID, nullable=False),
    _col("authority_ref", 5, _varchar(200), nullable=False),
    _col("reason", 6, _TEXT, nullable=False),
    _col("effective_at", 7, _TIMESTAMPTZ, nullable=False),
    _col("external_ref", 8, _varchar(200), nullable=False),
    _col("approved_at", 9, _TIMESTAMPTZ, nullable=False),
    _timestamp_col("created_at", 10),
)
_PLATFORM_APPROVAL_WITHDRAWALS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("request_id", 2, _UUID, nullable=False),
    _col("actor_id", 3, _UUID, nullable=False),
    _col("authority_ref", 4, _varchar(200), nullable=False),
    _col("reason", 5, _TEXT, nullable=False),
    _col("effective_at", 6, _TIMESTAMPTZ, nullable=False),
    _col("external_ref", 7, _varchar(200), nullable=False),
    _col("approved_at", 8, _TIMESTAMPTZ, nullable=False),
    _timestamp_col("created_at", 9),
)


_DATABASE_CATALOG = ModuleDatabaseCatalogContributionV1(
    lineage_head="ap_0003_withdrawals",
    tables=(
        ModuleDatabaseTableContractV1(
            name="approval_decisions",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_APPROVAL_DECISIONS_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="approval_policies",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_APPROVAL_POLICIES_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="approval_requests",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_APPROVAL_REQUESTS_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="approval_withdrawals",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_APPROVAL_WITHDRAWALS_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="platform_approval_decisions",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_PLATFORM_APPROVAL_DECISIONS_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="platform_approval_policies",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_PLATFORM_APPROVAL_POLICIES_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="platform_approval_requests",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_PLATFORM_APPROVAL_REQUESTS_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="platform_approval_withdrawals",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_PLATFORM_APPROVAL_WITHDRAWALS_COLUMNS,
        ),
    ),
)

module = ModuleManifest(
    code="approvals",
    version="0.1.0a7",
    core=False,
    short_code="approvals",
    migration_prefix="ap",
    migration_branch="approvals",
    tables=TENANT_TABLES,
    platform_tables=PLATFORM_TABLES,
    database_catalog=_DATABASE_CATALOG,
    # Split by plane, but NEVER used as the selector. Vendor CP truthfully has
    # the kernel tenant catalogue in its composed database and still selects
    # only PLATFORM here; the assembly's ModulePlaneSelection owns that intent.
    # Roles are common, while only the tenant plane needs the tenant catalogue.
    #
    # Deliberately NOT an identity or RBAC prerequisite either way: role
    # membership arrives on the `Actor` value at the call site, so this module
    # installs beside a product whose RBAC the kernel has never seen.
    #
    # `outbox_relay.v1` (kernel a67) is the effect this module has always
    # needed and could never name: `dotmac_approvals.outbox` enqueues into
    # `public.outbox_events` / `public.platform_outbox_events` at REQUEST time
    # and `ap_0001` creates neither. COMMON because both planes enqueue — the
    # tenant plane through `enqueue_event`, the control plane through
    # `enqueue_platform_event` — so a PLATFORM-only install needs it too.
    requires=(MODULE_DATABASE_ROLES_V1.name, OUTBOX_RELAY_V1.name),
    tenant_requires=(TENANT_SCOPE_CATALOG_V1.name,),
    supported_plane_sets=(
        (ModulePlane.TENANT,),
        (ModulePlane.PLATFORM,),
        (ModulePlane.TENANT, ModulePlane.PLATFORM),
    ),
)

__all__ = ["module"]
