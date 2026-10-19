"""Caller-transaction historical identity maintenance, owned by Kernel.

This is a lossless copy boundary, not runtime provisioning or an authorization
service. A reviewed, trusted decoder reads eight locked source relations in the
same database; it is not a Python sandbox. Products retain account/credential
policy extensions. Installed unchanged Alembic replay and per-revision hosted
observations qualify the migration chain; the head/catalog checks below alone
cannot prove replay. Locks exclude concurrent transactions only until the caller
ends its transaction. Lasting writer fencing, reference rebinding, population
provenance and reverse migration retaining new facts remain cutover gates.

All failures require caller rollback. No Session, transaction control, source
writes, credential generation, normalization, upsert or conflict skipping occurs.
Caller engine configuration must hide parameters and disable SQL/result logging
before source material is read. Receipts contain counts and references only.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.sql import quoted_name

from alembic.script import ScriptDirectory
from dotmac_kernel.audit import (
    AuditEvent,
    _current_audit_event_values,
    _write_audit_event_on_connection,
)
from dotmac_kernel.audit_actions import active_audit_actions
from dotmac_kernel.identity import normalize_email, person_display_name
from dotmac_kernel.migrations import versions_dir
from dotmac_kernel.migrations.gate import run_gate
from dotmac_kernel.migrations.verify import (
    verify_module_database_roles,
    verify_party_person_catalog,
    verify_tenant_audit_log,
    verify_tenant_scope_catalog,
)
from dotmac_kernel.models import (
    AuthSession,
    Party,
    PartyPerson,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
    TenantDomain,
    UserCredential,
)
from dotmac_kernel.modules import ModuleRegistry
from dotmac_kernel.planes import installed_module_plane_selections
from dotmac_kernel.prerequisites import PrerequisiteBinding, installed_bindings


class IdentityHistoryImportRefused(ValueError):
    """Stable material-free refusal; caller must roll back its whole operation."""


@dataclass(frozen=True, slots=True, repr=False)
class HistoricalJsonbV1:
    """Exact source JSONB text, read by the trusted decoder with JSONB::text.

    PostgreSQL validates and binds this text; Python never parses its numbers.
    This is data only, with no SQL fragments, callbacks or maintenance authority.
    """

    raw_json: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalRowV1:
    id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalTimestampedRowV1(HistoricalRowV1):
    updated_at: datetime


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalTenantV1(HistoricalTimestampedRowV1):
    slug: str
    name: str
    is_active: bool
    suspended_at: datetime | None
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalTenantDomainV1(HistoricalTimestampedRowV1):
    tenant_id: UUID
    domain: str
    verified_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalPersonV1(HistoricalTimestampedRowV1):
    tenant_id: UUID
    email: str
    first_name: str
    last_name: str


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalCredentialV1(HistoricalTimestampedRowV1):
    tenant_id: UUID
    person_id: UUID
    password_hash: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalSessionV1(HistoricalTimestampedRowV1):
    tenant_id: UUID
    person_id: UUID
    token_hash: str = field(repr=False)
    expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalRoleV1(HistoricalTimestampedRowV1):
    tenant_id: UUID
    slug: str
    name: str


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalPersonRoleV1(HistoricalTimestampedRowV1):
    tenant_id: UUID
    person_id: UUID
    role_id: UUID


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class HistoricalAuditEventV1(HistoricalRowV1):
    tenant_id: UUID
    actor_person_id: UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    details: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class IdentityHistoryRowsV1:
    tenants: tuple[HistoricalTenantV1, ...]
    tenant_domains: tuple[HistoricalTenantDomainV1, ...]
    people: tuple[HistoricalPersonV1, ...]
    user_credentials: tuple[HistoricalCredentialV1, ...]
    auth_sessions: tuple[HistoricalSessionV1, ...]
    roles: tuple[HistoricalRoleV1, ...]
    person_roles: tuple[HistoricalPersonRoleV1, ...]
    audit_events: tuple[HistoricalAuditEventV1, ...]


@dataclass(frozen=True, slots=True)
class IdentityHistoryCohortV1:
    tenants: frozenset[UUID]
    tenant_domains: frozenset[UUID]
    people: frozenset[UUID]
    user_credentials: frozenset[UUID]
    auth_sessions: frozenset[UUID]
    roles: frozenset[UUID]
    person_roles: frozenset[UUID]
    audit_events: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class IdentityHistoryRelationsV1:
    """Eight fixed relation names in one relocated schema, bound by OID."""

    schema: str
    tenants: int
    tenant_domains: int
    people: int
    user_credentials: int
    auth_sessions: int
    roles: int
    person_roles: int
    audit_events: int


@dataclass(frozen=True, slots=True)
class IdentityHistoryManifestV1:
    relations: IdentityHistoryRelationsV1
    cohort: IdentityHistoryCohortV1
    source_revision: str
    session_issuance_review_ref: str
    password_session_ids: frozenset[UUID]
    installed_kernel_head: str


@dataclass(frozen=True, slots=True, repr=False)
class IdentityHistoryImportOperationV1:
    """Current activation/audit facts, never a historical actor or permit token."""

    is_active: bool
    activated_person_ids: frozenset[UUID]
    reason: str
    decision_ref: str
    actor_type: str
    actor_id: str
    source_application: str
    actor_party_id: UUID | None = None
    actor_label: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityHistoryCountsV1:
    tenants: int
    tenant_domains: int
    people: int
    user_credentials: int
    auth_sessions: int
    roles: int
    person_roles: int
    audit_events: int


@dataclass(frozen=True, slots=True)
class IdentityHistoryImportReceiptV1:
    counts: IdentityHistoryCountsV1
    decision_ref: str
    source_revision: str
    current_audit_ids: tuple[UUID, ...]


class IdentityHistorySourceV1(Protocol):
    """Reviewed connection-bound decoder; arbitrary Python is not sandboxed.

    Decode all eight families, never mutate relations or log source rows/errors.
    The importer cross-checks the complete source PK cohort under its own locks.
    Products own any extension-field extraction and population review.
    """

    def decode(self, connection: Connection) -> IdentityHistoryRowsV1: ...


_FAMILIES = tuple(f.name for f in fields(IdentityHistoryRowsV1))
_ROW_TYPES = (
    HistoricalTenantV1,
    HistoricalTenantDomainV1,
    HistoricalPersonV1,
    HistoricalCredentialV1,
    HistoricalSessionV1,
    HistoricalRoleV1,
    HistoricalPersonRoleV1,
    HistoricalAuditEventV1,
)
_TARGET_MODELS = (
    Tenant.__table__,
    TenantDomain.__table__,
    Party.__table__,
    PartyPerson.__table__,
    UserCredential.__table__,
    AuthSession.__table__,
    Role.__table__,
    PartyRoleGrant.__table__,
    AuditEvent.__table__,
)
_TARGETS = tuple(
    cast(sa.Table, table).to_metadata(sa.MetaData(), schema="public")
    for table in _TARGET_MODELS
)
_ACTION = "identity.history_imported"
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")


def _refuse(condition: bool, reason: str) -> None:
    if not condition:
        raise IdentityHistoryImportRefused(reason)


def _check_connection(connection: Connection) -> None:
    _refuse(not connection.closed and not connection.invalidated, "connection")
    root = connection.get_transaction()
    _refuse(
        root is not None
        and root.is_active
        and connection.in_transaction()
        and not connection.in_nested_transaction(),
        "caller root transaction required",
    )
    _refuse(
        connection.dialect.name == "postgresql"
        and connection.dialect.driver == "psycopg",
        "PostgreSQL psycopg required",
    )
    _refuse(
        getattr(connection.connection.driver_connection, "autocommit", None) is False,
        "non-autocommit required",
    )
    _refuse(connection.engine.hide_parameters is True, "parameter hiding required")
    _refuse(connection.engine.echo is False, "SQL echo must be disabled")
    _refuse(
        not connection.engine.logger.isEnabledFor(logging.DEBUG),
        "result logging must be disabled",
    )
    _refuse(
        connection.get_execution_options().get("isolation_level") != "AUTOCOMMIT",
        "autocommit refused",
    )


def _operation_preflight(
    expected: IdentityHistoryManifestV1, operation: IdentityHistoryImportOperationV1
) -> None:
    _refuse(
        type(expected) is IdentityHistoryManifestV1, "typed expected manifest required"
    )
    _refuse(type(expected.cohort) is IdentityHistoryCohortV1, "typed cohort required")
    _refuse(
        type(expected.relations) is IdentityHistoryRelationsV1,
        "typed relations required",
    )
    _refuse(
        type(operation) is IdentityHistoryImportOperationV1, "typed operation required"
    )
    for name in _FAMILIES:
        ids = getattr(expected.cohort, name)
        _refuse(
            type(ids) is frozenset and all(type(value) is UUID for value in ids),
            "exact UUID cohort required",
        )
        oid = getattr(expected.relations, name)
        _refuse(type(oid) is int and oid > 0, "source OID required")
    _refuse(operation.is_active is True, "explicit current activation required")
    _refuse(
        type(operation.activated_person_ids) is frozenset
        and operation.activated_person_ids == expected.cohort.people,
        "activation cohort differs",
    )
    for value in (
        operation.reason,
        operation.decision_ref,
        expected.session_issuance_review_ref,
    ):
        _refuse(
            type(value) is str and bool(value.strip()) and len(value) <= 120,
            "review reference required",
        )
    _refuse(
        bool(_REVISION.fullmatch(expected.source_revision)), "source revision required"
    )
    _refuse(
        type(expected.password_session_ids) is frozenset
        and expected.password_session_ids == expected.cohort.auth_sessions,
        "session issuance population not covered",
    )
    _refuse(
        type(operation.actor_id) is str
        and bool(operation.actor_id.strip())
        and len(operation.actor_id) <= 120,
        "explicit current actor required",
    )
    _refuse(
        operation.actor_label is None
        or (type(operation.actor_label) is str and len(operation.actor_label) <= 160),
        "current actor label shape",
    )
    _refuse(
        operation.actor_party_id is None or type(operation.actor_party_id) is UUID,
        "current actor enrichment shape",
    )
    _refuse(
        type(operation.source_application) is str
        and bool(operation.source_application.strip()),
        "explicit current source required",
    )
    registry = active_audit_actions()
    registry.require(_ACTION)
    _refuse(registry.owner(_ACTION) == "kernel", "Kernel audit owner required")
    _refuse(bool(expected.cohort.tenants), "empty tenant cohort refused")
    for tenant in expected.cohort.tenants:
        _current_audit_event_values(
            tenant_id=tenant,
            action=_ACTION,
            entity_type="identity_history_import",
            actor_type=operation.actor_type,
            actor_id=operation.actor_id,
            actor_party_id=operation.actor_party_id,
            actor_label=operation.actor_label,
            source_application=operation.source_application,
        )


def _relation_fact(connection: Connection, schema: str, name: str) -> tuple[int, str]:
    row = connection.execute(
        sa.text(
            "SELECT c.oid, pg_get_userbyid(c.relowner), c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=:schema AND c.relname=:name"
        ),
        {"schema": schema, "name": name},
    ).one_or_none()
    if not (row is not None and row[2] == "r" and row[1] == "app_admin"):
        raise IdentityHistoryImportRefused("relation ownership or kind")
    return row[0], row[1]


def _lock_relations(
    connection: Connection, expected: IdentityHistoryManifestV1
) -> dict[tuple[str, str], int]:
    relations = expected.relations
    _refuse(
        type(relations.schema) is str
        and bool(_IDENTIFIER.fullmatch(relations.schema))
        and relations.schema != "public"
        and not relations.schema.startswith("pg_"),
        "relocated source schema required",
    )
    names = [(relations.schema, name) for name in _FAMILIES]
    names.extend(("public", table.name) for table in _TARGETS)
    before = {
        (schema, name): _relation_fact(connection, schema, name)[0]
        for schema, name in names
    }
    for name in _FAMILIES:
        _refuse(
            before[relations.schema, name] == getattr(relations, name),
            "source OID differs",
        )
    quote = connection.dialect.identifier_preparer.quote_identifier
    # One deterministic ordering for every call, source and target together.
    for schema, name in sorted(names):
        connection.execute(
            sa.text(
                f"LOCK TABLE {quote(schema)}.{quote(name)} IN ACCESS EXCLUSIVE MODE"
            )
        )
    for schema, name in names:
        _refuse(
            _relation_fact(connection, schema, name)[0] == before[schema, name],
            "relation OID changed",
        )

    return before


def _installed_kernel_coverage(
    connection: Connection,
    expected_head: str,
    assembly_versions: Path | None,
    module_registry: ModuleRegistry | None = None,
    module_versions: Mapping[str, Path] | None = None,
    prerequisite_bindings: tuple[PrerequisiteBinding, ...] = (),
) -> None:
    """Resolve live heads against the whole trusted installed source composition.

    Graph ancestry is necessary evidence, not proof that upgrades were run.
    Hosted replay observations and the live catalog remain separate checks.
    """
    _refuse(
        type(prerequisite_bindings) is tuple
        and all(
            type(binding) is PrerequisiteBinding for binding in prerequisite_bindings
        ),
        "typed explicit prerequisite bindings required",
    )
    bindings = tuple(
        sorted(prerequisite_bindings, key=lambda binding: binding.prerequisite)
    )
    _refuse(
        len({b.prerequisite for b in bindings}) == len(bindings),
        "duplicate prerequisite binding",
    )
    planes = installed_module_plane_selections()
    # Script loading resolves logical dependencies from process-global state.
    # The caller's env.py owns installation; this importer must never mutate it.
    _refuse(installed_bindings() == bindings, "installed prerequisite bindings differ")
    _refuse(
        module_registry is None or isinstance(module_registry, ModuleRegistry),
        "typed module registry required",
    )
    registry = module_registry if module_registry is not None else ModuleRegistry(())
    manifests = registry.startup_order()  # ALL installed modules, including disabled.
    stateful = {m.code: m for m in manifests if m.is_stateful}
    _refuse(
        module_versions is None or isinstance(module_versions, Mapping),
        "module version mapping required",
    )
    supplied = {} if module_versions is None else dict(module_versions)
    _refuse(set(supplied) == set(stateful), "installed module locations differ")
    kernel_location = versions_dir().resolve(strict=True)
    locations = [kernel_location]
    namespaces = registry.namespaces()
    wanted_owners = {kernel_location: namespaces.get("kernel")}
    if assembly_versions is not None:
        _refuse(isinstance(assembly_versions, Path), "assembly source path required")
        assembly_location = assembly_versions.resolve(strict=True)
        locations.append(assembly_location)
        wanted_owners[assembly_location] = namespaces.get("assembly")
    for code, manifest in stateful.items():
        _refuse(isinstance(supplied[code], Path), "module source path required")
        location = supplied[code].resolve(strict=True)
        owner = manifest.migration_owner()
        if owner is None:
            raise IdentityHistoryImportRefused("stateful module owner absent")
        actual_owner = namespaces.get(code)
        _refuse(
            actual_owner is not None
            and (
                actual_owner.owner,
                actual_owner.prefix,
                actual_owner.branch_label,
                actual_owner.db_schema,
            )
            == (owner.owner, owner.prefix, owner.branch_label, owner.db_schema),
            "module namespace owner differs",
        )
        locations.append(location)
        wanted_owners[location] = owner
    for index, location in enumerate(locations):
        _refuse(location.is_dir(), "migration source directory required")
        for other in locations[:index]:
            _refuse(
                location != other
                and location not in other.parents
                and other not in location.parents,
                "migration source locations overlap",
            )
    report = run_gate(
        manifests,
        locations,
        registry=namespaces,
        bindings=bindings,
        module_planes=planes,
    )
    _refuse(report.ok, "installed migration composition refused")
    for record in report.revisions:
        attributed_owner = report.attribution[record.revision]
        wanted = wanted_owners[record.location]
        _refuse(
            wanted is not None
            and attributed_owner
            == {
                "owner": wanted.owner,
                "branch_label": wanted.branch_label,
                "db_schema": wanted.db_schema,
            },
            "migration source owner differs",
        )
    _refuse(installed_bindings() == bindings, "installed prerequisite bindings changed")
    _refuse(
        installed_module_plane_selections() == planes,
        "installed module plane selections changed",
    )
    kernel_scripts = ScriptDirectory(
        str(kernel_location.parent), version_locations=[str(kernel_location)]
    )
    _refuse(
        kernel_scripts.get_heads() == [expected_head],
        "installed Kernel source head differs",
    )
    _refuse(installed_bindings() == bindings, "installed prerequisite bindings changed")
    _refuse(
        installed_module_plane_selections() == planes,
        "installed module plane selections changed",
    )
    scripts = ScriptDirectory(
        str(kernel_location.parent), version_locations=[str(p) for p in locations]
    )
    scripts.get_heads()  # Load the WHOLE graph, without requiring every source head applied.
    _refuse(installed_bindings() == bindings, "installed prerequisite bindings changed")
    _refuse(
        installed_module_plane_selections() == planes,
        "installed module plane selections changed",
    )
    # The composed graph may not substitute or shadow an installed Kernel file.
    for record in report.revisions:
        if record.location == kernel_location:
            installed = kernel_scripts.get_revision(record.revision)
            composed = scripts.get_revision(record.revision)
            _refuse(
                installed is not None
                and composed is not None
                and Path(installed.path).resolve(strict=True)
                == Path(composed.path).resolve(strict=True)
                == record.path.resolve(strict=True),
                "installed Kernel revision source differs",
            )
    _refuse(installed_bindings() == bindings, "installed prerequisite bindings changed")
    _refuse(
        installed_module_plane_selections() == planes,
        "installed module plane selections changed",
    )
    live_heads = set(
        connection.scalars(sa.text("SELECT version_num FROM public.alembic_version"))
    )
    _refuse(bool(live_heads), "migration heads absent")
    visited: set[str] = set()
    visiting: set[str] = set()
    exact_revisions = {record.revision for record in report.revisions}

    def visit(revision: str) -> None:
        _refuse(revision in exact_revisions, "noncanonical live migration revision")
        _refuse(revision not in visiting, "migration graph cycle")
        if revision in visited:
            return
        node = scripts.get_revision(revision)
        _refuse(node is not None, "unknown live migration head")
        visiting.add(revision)
        for value in (node.down_revision, node.dependencies):
            edges = (value,) if isinstance(value, str) else (value or ())
            for edge in edges:
                _refuse(edge in exact_revisions, "noncanonical migration dependency")
                dependency = scripts.get_revision(edge)
                _refuse(dependency is not None, "unknown migration dependency")
                visit(dependency.revision)
        visiting.remove(revision)
        visited.add(revision)

    for head in live_heads:
        visit(head)
    _refuse(expected_head in visited, "Kernel head not installed")
    _refuse(installed_bindings() == bindings, "installed prerequisite bindings changed")
    _refuse(
        installed_module_plane_selections() == planes,
        "installed module plane selections changed",
    )


def _target_catalog_preflight(
    connection: Connection,
    expected: IdentityHistoryManifestV1,
    assembly_versions: Path | None = None,
    module_registry: ModuleRegistry | None = None,
    module_versions: Mapping[str, Path] | None = None,
    prerequisite_bindings: tuple[PrerequisiteBinding, ...] = (),
) -> None:
    role = connection.execute(
        sa.text(
            "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
    ).one()
    _refuse(tuple(role) == ("app_admin", False, True), "offline app_admin required")
    _refuse(
        connection.scalar(sa.text("SELECT current_schemas(false)")) == ["public"]
        and connection.scalar(sa.text("SELECT current_schemas(true)"))
        == ["pg_catalog", "public"],
        "public-only search path without temporary shadowing required",
    )
    _refuse(
        not connection.get_execution_options().get("schema_translate_map"),
        "schema translation refused",
    )
    _installed_kernel_coverage(
        connection,
        expected.installed_kernel_head,
        assembly_versions,
        module_registry,
        module_versions,
        prerequisite_bindings,
    )
    verify_module_database_roles(connection)
    verify_tenant_scope_catalog(connection)
    verify_party_person_catalog(connection)
    verify_tenant_audit_log(connection)
    inspector = sa.inspect(connection)
    for table in _TARGETS:
        _refuse_enabled_target_triggers(connection, table)
        _refuse_enabled_target_rules(connection, table)
        live_columns = {
            c["name"]: c for c in inspector.get_columns(table.name, schema="public")
        }
        _refuse(
            set(live_columns) == set(table.c.keys()), "target column contract differs"
        )
        for column in table.c:
            live = live_columns[column.name]
            _refuse(live["nullable"] == column.nullable, "target nullability differs")
            _refuse(
                isinstance(live["type"], type(column.type))
                or live["type"]._type_affinity is column.type._type_affinity,
                "target type differs",
            )
            if isinstance(column.type, sa.String):
                _refuse(
                    getattr(live["type"], "length", None)
                    == (
                        20
                        if table.name == "parties" and column.name == "party_type"
                        else column.type.length
                    ),
                    # Canonical 0003 uses VARCHAR(20); the non-native ORM Enum
                    # derives a shorter width from its values. DDL owns width.
                    "target length differs",
                )
            if isinstance(column.type, sa.DateTime):
                _refuse(
                    getattr(live["type"], "timezone", None) is True,
                    "target timezone differs",
                )
        expected_pk = tuple(column.name for column in table.primary_key.columns)
        _refuse(
            tuple(
                inspector.get_pk_constraint(table.name, schema="public")[
                    "constrained_columns"
                ]
            )
            == expected_pk,
            "target primary key differs",
        )
        expected_fks = {
            (
                tuple(element.parent.name for element in constraint.elements),
                constraint.elements[0].target_fullname.rsplit(".", 1)[0],
                tuple(
                    element.target_fullname.rsplit(".", 1)[1]
                    for element in constraint.elements
                ),
                constraint.ondelete,
            )
            for constraint in table.foreign_key_constraints
        }
        live_fks = {
            (
                tuple(fk["constrained_columns"]),
                (fk.get("referred_schema") or "public") + "." + fk["referred_table"],
                tuple(fk["referred_columns"]),
                fk.get("options", {}).get("ondelete"),
            )
            for fk in inspector.get_foreign_keys(table.name, schema="public")
        }
        _refuse(expected_fks == live_fks, "target foreign keys differ")
        expected_unique = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        }
        expected_unique.update(
            tuple(expression.name for expression in index.expressions)
            for index in table.indexes
            if index.unique
            and all(
                isinstance(expression, sa.Column) for expression in index.expressions
            )
        )
        live_unique = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                table.name, schema="public"
            )
        }
        indexes = inspector.get_indexes(table.name, schema="public")
        # A partial/expression/invalid index cannot stand in for a canonical
        # full-table key, even when reflection reports matching column names.
        full_unique_indexes = set(
            connection.scalars(
                sa.text(
                    "SELECT index_relation.relname FROM pg_index i "
                    "JOIN pg_class index_relation ON index_relation.oid=i.indexrelid "
                    "WHERE i.indrelid=to_regclass(:table) AND i.indisunique "
                    "AND i.indisvalid AND i.indisready AND i.indislive "
                    "AND i.indpred IS NULL AND i.indexprs IS NULL"
                ),
                {"table": "public." + table.name},
            )
        )
        live_unique.update(
            tuple(cast(str, name) for name in index["column_names"])
            for index in indexes
            if index.get("unique")
            and index["name"] in full_unique_indexes
            and index.get("dialect_options", {}).get("postgresql_where") is None
            and all(name is not None for name in index["column_names"])
        )
        if table.name == "parties":
            email_indexes = [
                index
                for index in indexes
                if index["name"] == "uq_parties_tenant_lower_email"
            ]
            _refuse(
                len(email_indexes) == 1 and email_indexes[0].get("unique") is True,
                "canonical email index required",
            )
            email_index = email_indexes[0]
            expressions = [
                re.sub(r'[\s()"]', "", value).replace("::text", "")
                for value in email_index.get("expressions", ())
            ]
            predicate = re.sub(
                r'[\s()"]',
                "",
                str(email_index.get("dialect_options", {}).get("postgresql_where", "")),
            ).lower()
            _refuse(
                expressions == ["tenant_id", "loweremail"]
                and predicate == "emailisnotnull",
                "canonical email index shape differs",
            )
        _refuse(expected_unique == live_unique, "target uniqueness differs")
        posture = connection.execute(
            sa.text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:relation)"
            ),
            {"relation": "public." + table.name},
        ).one()
        _refuse(
            tuple(posture)
            == (
                (False, False)
                if table.name in ("tenants", "tenant_domains")
                else (True, True)
            ),
            "target RLS posture differs",
        )
        if table.name not in ("tenants", "tenant_domains", "audit_events"):
            policies = connection.execute(
                sa.text(
                    "SELECT cmd, roles, permissive, qual, with_check FROM pg_policies "
                    "WHERE schemaname='public' AND tablename=:table"
                ),
                {"table": table.name},
            ).all()
            _refuse(len(policies) == 1, "target policy population differs")
            command, roles, permissive, using, check = policies[0]
            _refuse(
                command == "ALL"
                and list(roles) == ["public"]
                and permissive == "PERMISSIVE",
                "target policy scope differs",
            )
            expected_expression = "tenant_id=app_current_tenant_id"
            if table.name == "party_persons":
                expected_expression = "existsselect1frompartiespwherep.id=party_persons.party_idandp.tenant_id=app_current_tenant_id"
            for expression in (using, check):
                normalized = (
                    re.sub(r'[\s()"]', "", expression or "")
                    .replace("public.", "")
                    .lower()
                )
                _refuse(
                    normalized == expected_expression,
                    "target policy expression differs",
                )
        _verify_online_privileges(connection, table)


def _target_preflight(
    connection: Connection,
    expected: IdentityHistoryManifestV1,
    assembly_versions: Path | None = None,
    module_registry: ModuleRegistry | None = None,
    module_versions: Mapping[str, Path] | None = None,
    prerequisite_bindings: tuple[PrerequisiteBinding, ...] = (),
) -> None:
    _target_catalog_preflight(
        connection,
        expected,
        assembly_versions,
        module_registry,
        module_versions,
        prerequisite_bindings,
    )
    for table in _TARGETS:
        _refuse(
            connection.scalar(sa.select(sa.func.count()).select_from(table)) == 0,
            "occupied target refused",
        )


def _refuse_enabled_target_triggers(connection: Connection, table: sa.Table) -> None:
    # The qualified Kernel migration inputs install no user triggers on these
    # nine fixed targets. Enabled custom triggers could silently rewrite copies.
    enabled = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_trigger "
            "WHERE tgrelid=to_regclass(:table) AND NOT tgisinternal AND tgenabled <> 'D')"
        ),
        {"table": "public." + table.name},
    )
    _refuse(enabled is False, "enabled target writer trigger refused")


def _refuse_enabled_target_rules(connection: Connection, table: sa.Table) -> None:
    # Qualified canonical targets have no rewrite rules. An enabled INSERT
    # INSTEAD rule can suppress a Core insert without an error or a copied row.
    enabled = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_rewrite "
            "WHERE ev_class=to_regclass(:table) AND ev_enabled <> 'D')"
        ),
        {"table": "public." + table.name},
    )
    _refuse(enabled is False, "enabled target rewrite rule refused")


def _verify_online_privileges(connection: Connection, table: sa.Table) -> None:
    # Canonical 0001 grants catalog SELECT to app_user and catalog CRUD to
    # platform_api; roles and identity have CRUD for both. 0003 ports those
    # identity grants, and 0022's grant-table rename preserves them. The audit
    # owner's verifier above already enforces append-only SELECT/INSERT plus
    # forbidden table/column privileges for BOTH roles; retain that authority.
    if table.name == "audit_events":
        return
    privileges = (
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    )
    for role in ("app_user", "platform_api"):
        expected = {"SELECT", "INSERT", "UPDATE", "DELETE"}
        if role == "app_user" and table.name in ("tenants", "tenant_domains"):
            expected = {"SELECT"}
        for privilege in privileges:
            table_right = connection.scalar(
                sa.text(
                    "SELECT has_table_privilege(CAST(:role AS name), "
                    "CAST(:table AS text), CAST(:privilege AS text))"
                ),
                {"role": role, "table": "public." + table.name, "privilege": privilege},
            )
            _refuse(
                table_right is (privilege in expected), "target online grants differ"
            )
            if privilege not in expected and privilege in {
                "SELECT",
                "INSERT",
                "UPDATE",
                "REFERENCES",
            }:
                column_right = connection.scalar(
                    sa.text(
                        "SELECT has_any_column_privilege(CAST(:role AS name), "
                        "CAST(:table AS text), CAST(:privilege AS text))"
                    ),
                    {
                        "role": role,
                        "table": "public." + table.name,
                        "privilege": privilege,
                    },
                )
                _refuse(column_right is False, "target online column grants differ")


def _values(row: HistoricalRowV1) -> dict[str, object]:
    return {f.name: getattr(row, f.name) for f in fields(row)}


def _time(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _unique(values: Iterable[object], reason: str) -> None:
    materialized = list(values)
    _refuse(len(set(materialized)) == len(materialized), reason)


def _rows_preflight(
    connection: Connection,
    rows: IdentityHistoryRowsV1,
    expected: IdentityHistoryManifestV1,
) -> None:
    _refuse(type(rows) is IdentityHistoryRowsV1, "typed source rows required")
    schema = expected.relations.schema
    for family, row_type in zip(_FAMILIES, _ROW_TYPES, strict=True):
        collection = getattr(rows, family)
        _refuse(type(collection) is tuple, "immutable source family required")
        _refuse(
            all(type(row) is row_type for row in collection),
            "typed source family required",
        )
        ids = [row.id for row in collection]
        _unique(ids, "duplicate source ID")
        _refuse(all(type(value) is UUID for value in ids), "UUID source IDs required")
        admitted = getattr(expected.cohort, family)
        _refuse(set(ids) == admitted, "decoded cohort differs")
        source = sa.table(
            quoted_name(family, True),
            sa.column("id", sa.Uuid()),
            schema=quoted_name(schema, True),
        )
        live_ids = tuple(connection.scalars(sa.select(source.c.id)))
        _refuse(
            len(live_ids) == len(ids) and set(live_ids) == admitted,
            "locked source cohort differs",
        )
        for row in collection:
            for f in fields(row):
                value = getattr(row, f.name)
                if f.name.endswith("_at") and value is not None:
                    _refuse(_time(value), "timezone-aware source time required")
                if f.name in ("tenant_id", "person_id", "role_id"):
                    _refuse(type(value) is UUID, "UUID reference required")
                if isinstance(row, HistoricalTimestampedRowV1):
                    _refuse(
                        _time(row.created_at) and _time(row.updated_at),
                        "source timestamps required",
                    )
    tenants = {row.id for row in rows.tenants}
    people = {row.id: row for row in rows.people}
    roles = {row.id: row for row in rows.roles}
    for family in _FAMILIES[1:]:
        for row in getattr(rows, family):
            _refuse(row.tenant_id in tenants, "tenant reference outside cohort")
    for person_family in (rows.user_credentials, rows.auth_sessions, rows.person_roles):
        for row in person_family:
            person = people.get(row.person_id)
            _refuse(
                person is not None and person.tenant_id == row.tenant_id,
                "person tenant reference differs",
            )
    for row in rows.person_roles:
        role = roles.get(row.role_id)
        _refuse(
            role is not None and role.tenant_id == row.tenant_id,
            "role tenant reference differs",
        )
    _unique((row.slug for row in rows.tenants), "tenant slug collision")
    _unique((row.domain for row in rows.tenant_domains), "domain collision")
    _unique(
        ((row.tenant_id, row.email) for row in rows.people), "person email collision"
    )
    _unique(((row.tenant_id, row.slug) for row in rows.roles), "role slug collision")
    _unique(
        ((row.tenant_id, row.person_id, row.role_id) for row in rows.person_roles),
        "grant collision",
    )
    _unique(
        ((row.tenant_id, row.token_hash) for row in rows.auth_sessions),
        "session collision",
    )
    for row in rows.people:
        _refuse(
            type(row.email) is str
            and normalize_email(row.email) == row.email
            and bool(row.email)
            and len(row.email) <= 320,
            "noncanonical person email",
        )
        _refuse(
            type(row.first_name) is str
            and type(row.last_name) is str
            and len(row.first_name) <= 100
            and len(row.last_name) <= 100,
            "person name shape",
        )
        _refuse(
            len(person_display_name(row.first_name, row.last_name)) <= 200,
            "display name shape",
        )
    for row in rows.user_credentials:
        _refuse(
            type(row.password_hash) is str and len(row.password_hash) <= 255,
            "credential shape",
        )
    for row in rows.auth_sessions:
        _refuse(
            type(row.token_hash) is str
            and bool(re.fullmatch(r"[0-9a-f]{64}", row.token_hash)),
            "session token shape",
        )
        _refuse(_time(row.expires_at), "session expiry required")
    for row in rows.audit_events:
        _refuse(_time(row.created_at), "historical persistence time required")
        _refuse(
            row.actor_person_id is None or type(row.actor_person_id) is UUID,
            "historical actor shape",
        )
        # Logical historical enrichment has no target FK. Preserve even deleted
        # or foreign historical references; current-event policy is separate.
        if type(row.details) is HistoricalJsonbV1:
            _refuse(type(row.details.raw_json) is str, "historical JSONB text required")
            kind = connection.scalar(
                sa.text("SELECT jsonb_typeof(CAST(:raw AS JSONB))").bindparams(
                    sa.bindparam("raw", type_=sa.Text())
                ),
                {"raw": row.details.raw_json},
            )
            # jsonb_typeof returns a typename even for JSON null. PostgreSQL
            # admits every legal JSONB value, including arbitrary-precision
            # numbers; there is no Python JSON decoder or numeric allowlist.
            _refuse(type(kind) is str, "historical JSONB value required")
        else:
            json.dumps(row.details, allow_nan=False)
    # All Core values must satisfy non-null/type/length contracts before INSERT.
    for table, values in _projections(rows):
        for column in table.c:
            value = values[column.name]
            _refuse(
                value is not None
                or column.nullable
                or isinstance(column.type, sa.JSON),
                "required target value missing",
            )
            if value is None:
                continue
            if isinstance(column.type, sa.String):
                _refuse(
                    isinstance(value, str)
                    and (
                        column.type.length is None or len(value) <= column.type.length
                    ),
                    "target string shape",
                )
            elif isinstance(column.type, sa.Boolean):
                _refuse(type(value) is bool, "target boolean shape")
            elif isinstance(column.type, sa.Uuid):
                _refuse(type(value) is UUID, "target UUID shape")
            elif isinstance(column.type, sa.DateTime):
                _refuse(_time(value), "target time shape")


def _target(table: sa.sql.FromClause) -> sa.Table:
    return next(
        target for target in _TARGETS if target.name == cast(sa.Table, table).name
    )


def _projections(
    rows: IdentityHistoryRowsV1,
) -> Iterable[tuple[sa.Table, dict[str, object]]]:
    for tenant_row in rows.tenants:
        yield _target(Tenant.__table__), _values(tenant_row)
    for domain_row in rows.tenant_domains:
        yield _target(TenantDomain.__table__), _values(domain_row)
    for person_row in rows.people:
        base = _values(person_row)
        del base["first_name"], base["last_name"]
        yield (
            _target(Party.__table__),
            {
                **base,
                "party_type": PartyType.person,
                "display_name": person_display_name(
                    person_row.first_name, person_row.last_name
                ),
                "is_active": True,
                "custom_fields": {},
            },
        )
    for person_row in rows.people:
        yield (
            _target(PartyPerson.__table__),
            {
                "party_id": person_row.id,
                "first_name": person_row.first_name,
                "last_name": person_row.last_name,
            },
        )
    for credential_row in rows.user_credentials:
        values = _values(credential_row)
        values["party_id"] = values.pop("person_id")
        yield _target(UserCredential.__table__), values
    for session_row in rows.auth_sessions:
        values = _values(session_row)
        values["party_id"] = values.pop("person_id")
        values["external_identity_binding_id"] = None
        yield _target(AuthSession.__table__), values
    for role_row in rows.roles:
        yield _target(Role.__table__), _values(role_row)
    for grant_row in rows.person_roles:
        values = _values(grant_row)
        values["party_id"] = values.pop("person_id")
        yield _target(PartyRoleGrant.__table__), values
    for audit_row in rows.audit_events:
        values = _values(audit_row)
        values["actor_party_id"] = values.pop("actor_person_id")
        for name in (
            "actor_type",
            "actor_id",
            "actor_label",
            "source_application",
            "request_id",
            "status_code",
            "is_success",
            "ip_address",
            "user_agent",
            "occurred_at",
        ):
            values[name] = None
        yield _target(AuditEvent.__table__), values


def _historical_insert_values(values: dict[str, object]) -> dict[str, object]:
    details = values.get("details")
    if type(details) is HistoricalJsonbV1:
        _refuse(type(details.raw_json) is str, "historical JSONB text required")
        return {
            **values,
            "details": sa.cast(
                sa.bindparam(
                    "historical_jsonb_raw", value=details.raw_json, type_=sa.Text()
                ),
                JSONB(),
            ),
        }
    return values


def import_identity_history(
    connection: Connection,
    source: IdentityHistorySourceV1,
    expected: IdentityHistoryManifestV1,
    operation: IdentityHistoryImportOperationV1,
    *,
    assembly_versions: Path | None = None,
    module_registry: ModuleRegistry | None = None,
    module_versions: Mapping[str, Path] | None = None,
    prerequisite_bindings: tuple[PrerequisiteBinding, ...] = (),
) -> IdentityHistoryImportReceiptV1:
    """Preflight every family, then copy on the caller's root transaction.

    Caller must roll back on ANY exception. This function never opens/ends a
    transaction, repairs schema, installs a registry or changes engine logging.
    No source rows, driver diagnostics or sensitive fingerprints enter failures.
    Trusted source composition includes the whole assembly and every installed
    stateful module, including disabled modules. Explicit bindings must match
    those already installed by the caller's Alembic environment. No binding or
    registry is installed here, and graph coverage is not a replay attestation.
    """
    stage = "connection preflight"
    try:
        _check_connection(connection)
        root = connection.get_transaction()
        stage = "operation preflight"
        _operation_preflight(expected, operation)
        stage = "relation locking"
        locked_oids = _lock_relations(connection, expected)
        stage = "installed target preflight"
        _target_preflight(
            connection,
            expected,
            assembly_versions,
            module_registry,
            module_versions,
            prerequisite_bindings,
        )
        stage = "source decoding"
        rows = source.decode(connection)
        stage = "complete source preflight"
        _rows_preflight(connection, rows, expected)
        # Re-check OIDs after trusted decoder code, before the first mutation.
        for (schema, name), oid in locked_oids.items():
            _refuse(
                _relation_fact(connection, schema, name)[0] == oid,
                "relation OID changed during decoding",
            )
        _check_connection(connection)
        _refuse(
            connection.get_transaction() is root,
            "caller transaction changed during decoding",
        )
        _operation_preflight(expected, operation)
        for table, values in _projections(rows):
            stage = "copy " + table.name
            # Every column is supplied, including explicit historical NULLs;
            # Core therefore applies no Python/server default to copied fields.
            _refuse(set(values) == set(table.c.keys()), "incomplete target projection")
            connection.execute(
                sa.insert(table).values(**_historical_insert_values(values))
            )
        stage = "current import audit"
        audit_ids = []
        for tenant in sorted(expected.cohort.tenants, key=str):
            counts = {
                name: sum(
                    1
                    for row in getattr(rows, name)
                    if (row.id if name == "tenants" else row.tenant_id) == tenant
                )
                for name in _FAMILIES
            }
            audit_ids.append(
                _write_audit_event_on_connection(
                    connection,
                    tenant_id=tenant,
                    action=_ACTION,
                    entity_type="identity_history_import",
                    actor_type=operation.actor_type,
                    actor_id=operation.actor_id,
                    actor_party_id=operation.actor_party_id,
                    actor_label=operation.actor_label,
                    source_application=operation.source_application,
                    details={
                        "counts": counts,
                        "reason": operation.reason,
                        "decision_ref": operation.decision_ref,
                        "source_revision": expected.source_revision,
                    },
                )
            )
        return IdentityHistoryImportReceiptV1(
            counts=IdentityHistoryCountsV1(
                **{name: len(getattr(rows, name)) for name in _FAMILIES}
            ),
            decision_ref=operation.decision_ref,
            source_revision=expected.source_revision,
            current_audit_ids=tuple(audit_ids),
        )
    except Exception:
        # Driver errors can embed values even when parameters are hidden.
        # Suppressed chaining prevents those diagnostics reaching normal logs.
        raise IdentityHistoryImportRefused(
            "identity history refused during " + stage
        ) from None


def _comparison_predicates(table: sa.Table, values: dict[str, object]) -> list:
    """Typed database equality only; never retrieve sensitive target values."""
    _refuse(set(values) == set(table.c.keys()), "incomplete expected projection")
    predicates = []
    for column in table.c:
        value = values[column.name]
        if isinstance(column.type, sa.JSON):
            expected_json: sa.sql.elements.ColumnElement[object]
            if type(value) is HistoricalJsonbV1:
                _refuse(type(value.raw_json) is str, "historical JSONB text required")
                expected_json = sa.cast(
                    sa.bindparam(None, value.raw_json, type_=sa.Text), JSONB
                )
            else:
                # Use the importer's JSONB serializer, including JSON literal
                # null for Python None; SQL NULL must remain distinguishable.
                expected_json = sa.bindparam(None, value, type_=column.type)
            predicates.append(
                sa.cast(column, sa.Text).is_not_distinct_from(
                    sa.cast(expected_json, sa.Text)
                )
            )
        else:
            expected_scalar = (
                value
                if isinstance(value, sa.sql.elements.ClauseElement)
                else sa.bindparam(None, value, type_=column.type)
            )
            predicates.append(column.is_not_distinct_from(expected_scalar))
    return predicates


def _verify_projected_values(connection: Connection, projections: list) -> None:
    expected_keys: dict[str, set[object]] = {table.name: set() for table in _TARGETS}
    for table, values in projections:
        key = next(iter(table.primary_key.columns))
        identifier = values[key.name]
        _refuse(identifier not in expected_keys[table.name], "duplicate expected key")
        expected_keys[table.name].add(identifier)
        _refuse(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(table)
                .where(*_comparison_predicates(table, values))
            )
            == 1,
            "identity value differs",
        )
    for table in _TARGETS:
        key = next(iter(table.primary_key.columns))
        # Keys only, across the entire target: equal-count replacements and
        # outside-tenant rows cannot satisfy this independent extent check.
        actual = tuple(connection.scalars(sa.select(key)))
        _refuse(
            len(actual) == len(expected_keys[table.name])
            and set(actual) == expected_keys[table.name],
            "identity key extent differs",
        )


def _verification_receipt_preflight(rows, expected, operation, receipt):
    _refuse(type(rows) is IdentityHistoryRowsV1, "typed source rows required")
    _refuse(type(receipt) is IdentityHistoryImportReceiptV1, "typed receipt required")
    _refuse(type(receipt.counts) is IdentityHistoryCountsV1, "typed counts required")
    for name in _FAMILIES:
        count = getattr(receipt.counts, name)
        _refuse(
            type(count) is int and count == len(getattr(rows, name)),
            "receipt counts differ",
        )
    _refuse(
        receipt.decision_ref == operation.decision_ref
        and receipt.source_revision == expected.source_revision,
        "receipt references differ",
    )
    ids = receipt.current_audit_ids
    tenants = sorted(expected.cohort.tenants, key=str)
    _refuse(
        type(ids) is tuple
        and len(ids) == len(tenants)
        and all(type(key) is UUID for key in ids),
        "current audit keys differ",
    )
    _refuse(
        len(set(ids)) == len(ids)
        and not set(ids).intersection(expected.cohort.audit_events),
        "current audit keys overlap",
    )
    return tenants, ids


def verify_identity_history(
    connection: Connection,
    rows: IdentityHistoryRowsV1,
    expected: IdentityHistoryManifestV1,
    operation: IdentityHistoryImportOperationV1,
    receipt: IdentityHistoryImportReceiptV1,
    *,
    assembly_versions: Path | None = None,
    module_registry: ModuleRegistry | None = None,
    module_versions: Mapping[str, Path] | None = None,
    prerequisite_bindings: tuple[PrerequisiteBinding, ...] = (),
) -> None:
    """Independently compare populated targets to expected pre-write data.

    Data comparison only: public rows/receipt confer no import or cutover
    authority. No writes, transaction control, material output or new audit.
    Existing importer catalog predicates are reused; this is not a full RI seal.
    """
    try:
        _check_connection(connection)
        root = connection.get_transaction()
        if root is None:
            raise IdentityHistoryImportRefused("caller root transaction required")
        _operation_preflight(expected, operation)
        tenants, ids = _verification_receipt_preflight(
            rows, expected, operation, receipt
        )
        locked = _lock_relations(connection, expected)
        _target_catalog_preflight(
            connection,
            expected,
            assembly_versions,
            module_registry,
            module_versions,
            prerequisite_bindings,
        )
        _rows_preflight(connection, rows, expected)
        projections = list(_projections(rows))
        for tenant, identifier in zip(tenants, ids, strict=True):
            counts = {
                name: sum(
                    1
                    for row in getattr(rows, name)
                    if (row.id if name == "tenants" else row.tenant_id) == tenant
                )
                for name in _FAMILIES
            }
            values = _current_audit_event_values(
                tenant_id=tenant,
                action=_ACTION,
                entity_type="identity_history_import",
                actor_type=operation.actor_type,
                actor_id=operation.actor_id,
                actor_party_id=operation.actor_party_id,
                actor_label=operation.actor_label,
                source_application=operation.source_application,
                details={
                    "counts": counts,
                    "reason": operation.reason,
                    "decision_ref": operation.decision_ref,
                    "source_revision": expected.source_revision,
                },
            )
            values.update(
                id=identifier,
                occurred_at=sa.func.transaction_timestamp(),
                created_at=sa.func.transaction_timestamp(),
            )
            projections.append((_target(AuditEvent.__table__), values))
        _verify_projected_values(connection, projections)
        for (schema, name), oid in locked.items():
            _refuse(
                _relation_fact(connection, schema, name)[0] == oid,
                "relation OID changed",
            )
        _check_connection(connection)
        _refuse(
            connection.get_transaction() is root and root.is_active,
            "caller root changed",
        )
        _operation_preflight(expected, operation)
    except Exception:
        raise IdentityHistoryImportRefused(
            "identity history verification refused; root rollback required"
        ) from None


__all__ = [
    "HistoricalAuditEventV1",
    "HistoricalCredentialV1",
    "HistoricalJsonbV1",
    "HistoricalPersonRoleV1",
    "HistoricalPersonV1",
    "HistoricalRoleV1",
    "HistoricalSessionV1",
    "HistoricalTenantDomainV1",
    "HistoricalTenantV1",
    "IdentityHistoryCohortV1",
    "IdentityHistoryCountsV1",
    "IdentityHistoryImportOperationV1",
    "IdentityHistoryImportReceiptV1",
    "IdentityHistoryImportRefused",
    "IdentityHistoryManifestV1",
    "IdentityHistoryRelationsV1",
    "IdentityHistoryRowsV1",
    "IdentityHistorySourceV1",
    "import_identity_history",
    "verify_identity_history",
]
