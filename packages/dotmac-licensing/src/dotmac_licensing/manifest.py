"""Licensing's `ModuleManifest` — the fifteenth stateful module.

Must match `LICENSING_MIGRATION_OWNER` in the kernel ledger exactly or
`NamespaceRegistry.from_manifests` refuses the composition at boot:
`short_code="licensing"` → `mod_licensing`, `migration_prefix="li"` →
`li_0001_…`, `migration_branch="licensing"`, and `platform_tables` bounding
what the composed gate will accept the migration creating.

## Platform plane only, and here the reason is a security boundary

`tables=()` is a DECLARATION, not an oversight (ADR-0023 rejects inferring a
plane from a missing `tenant_id`). But this module's plane choice is stronger
than the usual "no tenant consumer exists": a tenant data plane installing
licence ISSUANCE would put the thing that decides what a deployment may do
inside the deployment it decides about.

The receiving half is already elsewhere and already correct —
`dotmac_kernel.licensing` verifies a signed envelope fully OFFLINE, and the
assembly's own `licensing` feature projects it into local grants. A data plane
learns what it may do from a document it can verify without asking anyone,
which is the entire point of a signed licence. Reading the issuer's tables
instead would replace an offline cryptographic check with a network dependency
and a trust relationship.

The import-linter contract forbidding this repository's assembly from importing
`dotmac_licensing` is what keeps that true rather than merely current.

## Three audit actions, split by WHO acted

`licence.issued`, `licence.transitioned`, `licence.acknowledged`. Not one code,
and not a code per verb.

The split is by actor, because that is the question an operator reading an audit
trail is actually answering: *did we do this, or did they?* Issuance and
transition are the issuer's acts; an acknowledgement is a REMOTE party's claim
that this module recorded after checking. Collapsing them would make that
distinction invisible without opening every detail blob — and it is the
distinction that matters when a licence's standing is disputed.

Contrast `dotmac-commercial-agreements`, which declares exactly one: every
transition there is the operator's own act, so there is nothing to distinguish.

## Two logical prerequisites, both written at REQUEST time

Neither is created by this module's own migrations, and an undeclared runtime
dependency is still a dependency — it just has no DDL to betray it. An adopter
that runs its own lineage and never ran the kernel's would pass every gate this
module has, migrate cleanly, and die on `UndefinedTable` at the first issuance.

- Every command delegates at-most-once to the kernel (hard rule 23, ADR-0014),
  writing `public.platform_idempotency_records`.
- Every command writes `public.platform_audit_events` through
  `write_platform_audit_event`, inside the same operation.

COMMON rather than `platform_requires`: this module has exactly one plane,
`supported_plane_sets` is unset, so the declared platform plane installs
atomically and there is no selection under which the requirement could lapse.
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
_TEXT = _base_type("text", "text")
_TIMESTAMPTZ = _base_type("timestamptz", "timestamp with time zone")
_JSONB = _base_type("jsonb", "jsonb")


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


# Facts observed live against PostgreSQL 16 (kernel + assembly + this module's
# migrations composed to head `li_0001_licensing`) via
# `dotmac_kernel.database_catalog_comparator.observe_postgres_tables_columns`,
# self-verified with `compare_module_database_catalog` before being frozen
# here. Never hand-derived from the migration source.
_SIGNING_KEYS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("key_id", 2, _varchar(120), nullable=False),
    _col("public_key_b64", 3, _varchar(200), nullable=False),
    _col("status", 4, _varchar(20), nullable=False),
    _timestamp_col("created_at", 5),
    _timestamp_col("updated_at", 6),
)
_LICENCES_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("subject_ref", 2, _varchar(200), nullable=False),
    _col("product_code", 3, _varchar(120), nullable=False),
    _col("generation", 4, _INT4, nullable=False),
    _timestamp_col("created_at", 5),
    _timestamp_col("updated_at", 6),
)
_LICENCE_ISSUANCES_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("licence_id", 2, _UUID, nullable=False),
    _col("version", 3, _INT4, nullable=False),
    _col("agreement_ref", 4, _varchar(200), nullable=False),
    _col("allocation_ref", 5, _varchar(200), nullable=False),
    _col("digest", 6, _varchar(128), nullable=False),
    _col("key_id", 7, _varchar(120), nullable=False),
    _col("envelope", 8, _JSONB, nullable=False),
    _col("status", 9, _varchar(20), nullable=False),
    _col("record_version", 10, _INT4, nullable=False),
    _col("valid_from", 11, _TIMESTAMPTZ, nullable=True),
    _col("valid_until", 12, _TIMESTAMPTZ, nullable=True),
    _col("grace_days", 13, _INT4, nullable=False),
    _col("deployment_ref", 14, _varchar(200), nullable=True),
    _col("activated_at", 15, _TIMESTAMPTZ, nullable=True),
    _col("suspended_reason", 16, _TEXT, nullable=True),
    _col("replaced_by_version", 17, _INT4, nullable=True),
    _timestamp_col("created_at", 18),
    _timestamp_col("updated_at", 19),
)
_LICENCE_ACKNOWLEDGEMENTS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("issuance_id", 2, _UUID, nullable=False),
    _col("licence_version", 3, _INT4, nullable=False),
    _col("digest", 4, _varchar(128), nullable=False),
    _col("outcome", 5, _varchar(20), nullable=False),
    _col("reason", 6, _varchar(120), nullable=True),
    _col("reported_at", 7, _TIMESTAMPTZ, nullable=False),
    _col("reported_deployment_ref", 8, _varchar(200), nullable=False),
    _col("authenticated_deployment_ref", 9, _varchar(200), nullable=True),
    _timestamp_col("created_at", 10),
    _timestamp_col("updated_at", 11),
)
_REVOCATIONS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("licence_id", 2, _UUID, nullable=False),
    _col("reason", 3, _varchar(200), nullable=False),
    _col("actor_ref", 4, _varchar(200), nullable=True),
    _timestamp_col("created_at", 5),
    _timestamp_col("updated_at", 6),
)
_REVOCATION_LISTS_COLUMNS = (
    _col("id", 1, _UUID, nullable=False),
    _col("list_version", 2, _INT4, nullable=False),
    _col("digest", 3, _varchar(128), nullable=False),
    _col("key_id", 4, _varchar(120), nullable=False),
    _col("entry_count", 5, _INT4, nullable=False),
    _col("envelope", 6, _JSONB, nullable=False),
    _timestamp_col("created_at", 7),
    _timestamp_col("updated_at", 8),
)

_DATABASE_CATALOG = ModuleDatabaseCatalogContributionV1(
    lineage_head="li_0001_licensing",
    tables=(
        ModuleDatabaseTableContractV1(
            name="licence_acknowledgements",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_LICENCE_ACKNOWLEDGEMENTS_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="licence_issuances",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_LICENCE_ISSUANCES_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="licences",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_LICENCES_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="revocation_lists",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_REVOCATION_LISTS_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="revocations",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_REVOCATIONS_COLUMNS,
        ),
        ModuleDatabaseTableContractV1(
            name="signing_keys",
            relation_kind=DatabaseRelationKind.TABLE,
            columns=_SIGNING_KEYS_COLUMNS,
        ),
    ),
)

module = ModuleManifest(
    code="licensing",
    version="0.1.0a1+dev",
    core=False,
    short_code="licensing",
    migration_prefix="li",
    migration_branch="licensing",
    tables=(),
    platform_tables=(
        "signing_keys",
        "licences",
        "licence_issuances",
        "licence_acknowledgements",
        "revocations",
        "revocation_lists",
    ),
    database_catalog=_DATABASE_CATALOG,
    requires=(IDEMPOTENCY_LEDGER_V1.name, PLATFORM_AUDIT_LOG_V1.name),
    audit_actions=(
        "licence.issued",
        "licence.transitioned",
        "licence.acknowledged",
    ),
)

__all__ = ["module"]
