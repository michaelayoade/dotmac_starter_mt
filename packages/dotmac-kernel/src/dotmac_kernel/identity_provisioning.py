"""Consumed offline bootstrap owner, on the strictly issued platform root.

Composition is trusted Python code. Public DTOs and platform provenance alone
never authorize a write: the owner checks actual paired DB identities and effects.
All late validation uses the supplied physical Connection, including PREPARED.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, NoReturn, Protocol, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.orm import Session

from dotmac_kernel import identity_storage
from dotmac_kernel.audit import AuditEvent, write_audit_event
from dotmac_kernel.credential_lifecycle import (
    CredentialAssignmentAuditEntryV1,
    CredentialAssignmentAuthorityEvidenceV1,
    CredentialAssignmentCommandV1,
    CredentialAssignmentReceiptV1,
    CredentialCreateAbsentFromProofV1,
    CredentialCreateAbsentV1,
    CredentialLifecycle,
    CredentialReplaceSelectedV1,
    CredentialSnapshot,
    CredentialSubjectRef,
)
from dotmac_kernel.models import (
    Party,
    PartyPerson,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
    UserCredential,
)
from dotmac_kernel.session_runtime import (
    DatabaseRuntime,
    _identity_provisioning_assembly,
    _install_identity_provisioning,
    _protected_platform_operation,
)

__all__ = (
    "BootstrapTenantV1",
    "BootstrapRoleV1",
    "BootstrapPolicyV1",
    "BootstrapProductPort",
    "BootstrapProductOperation",
    "IdentityProvisioningAssemblyV1",
    "BootstrapTenantResultV1",
    "install_identity_provisioning",
    "bootstrap_tenant",
)


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("offline bootstrap admission or outcome differs")


def _text(value: str, maximum: int) -> None:
    _require(type(value) is str and bool(value.strip()) and len(value) <= maximum)


@dataclass(frozen=True, slots=True)
class BootstrapTenantV1:
    slug: str
    name: str
    owner_email: str

    def __post_init__(self) -> None:
        _text(self.slug, 63)
        _text(self.name, 120)
        _text(self.owner_email, 320)


@dataclass(frozen=True, slots=True)
class BootstrapRoleV1:
    slug: str
    name: str

    def __post_init__(self) -> None:
        _text(self.slug, 63)
        _text(self.name, 120)


@dataclass(frozen=True, slots=True)
class BootstrapPolicyV1:
    allowed_database_role_pairs: tuple[tuple[str, str], ...]
    roles: tuple[BootstrapRoleV1, ...]
    initial_role_slug: str
    tenant_is_active: bool

    def __post_init__(self) -> None:
        pairs = self.allowed_database_role_pairs
        _require(type(pairs) is tuple and bool(pairs))
        for pair in pairs:
            _require(type(pair) is tuple and len(pair) == 2)
            for database_role in pair:
                _text(database_role, 63)
        _require(len(set(pairs)) == len(pairs))
        _require(type(self.roles) is tuple and bool(self.roles))
        for role in self.roles:
            _require(type(role) is BootstrapRoleV1)
            role.__post_init__()
        _text(self.initial_role_slug, 63)
        slugs = tuple(role.slug for role in self.roles)
        _require(len(set(slugs)) == len(slugs) and self.initial_role_slug in slugs)
        _require(type(self.tenant_is_active) is bool)


class BootstrapProductOperation(Protocol):
    @property
    def policy(self) -> BootstrapPolicyV1: ...

    def person_values(
        self, *, tenant_id: UUID, party_id: UUID, occurred_at: datetime
    ) -> identity_storage.NewPersonValues: ...

    def create_person(
        self, db: Session, *, values: identity_storage.NewPersonValues
    ) -> None: ...

    def create_absent(
        self,
        db: Session,
        command: CredentialCreateAbsentV1,
        *,
        password_hash: str,
        reset_required: Literal[False],
    ) -> CredentialSnapshot: ...

    def observe_credential(
        self, connection: Connection, command: CredentialCreateAbsentV1
    ) -> CredentialSnapshot: ...

    def revalidate(self, connection: Connection) -> None: ...

    def validate(self, subject: CredentialSubjectRef, secret: str) -> None: ...


class BootstrapProductPort(Protocol):
    def open(
        self, connection: Connection, command: BootstrapTenantV1
    ) -> BootstrapProductOperation: ...


@dataclass(frozen=True, slots=True)
class IdentityProvisioningAssemblyV1:
    runtime: DatabaseRuntime
    source_application: str
    product: BootstrapProductPort

    def __post_init__(self) -> None:
        _require(isinstance(self.runtime, DatabaseRuntime))
        _text(self.source_application, 120)
        _require(callable(getattr(self.product, "open", None)))


@dataclass(frozen=True, slots=True)
class BootstrapTenantResultV1:
    tenant_id: UUID
    slug: str
    owner_party_id: UUID
    assignment: CredentialAssignmentReceiptV1


def install_identity_provisioning(assembly: IdentityProvisioningAssemblyV1) -> None:
    if type(assembly) is not IdentityProvisioningAssemblyV1:
        raise TypeError("identity provisioning requires a typed assembly")
    assembly.__post_init__()
    _install_identity_provisioning(assembly)


# Fixed canonical write-set only. These Core projections address actual public
# relations; their metadata never substitutes for the independent catalog checks.
_TABLES = tuple(
    cast(sa.Table, model.__table__).to_metadata(sa.MetaData(), schema="public")
    for model in (
        Tenant,
        Party,
        PartyPerson,
        Role,
        PartyRoleGrant,
        UserCredential,
        AuditEvent,
    )
)
_TENANT, _PARTY, _PERSON, _ROLE, _GRANT, _CREDENTIAL, _AUDIT = _TABLES


def _normalize(expression: str) -> str:
    return (
        re.sub(r'[\s()"]', "", expression)
        .replace("public.", "")
        .replace("pg_catalog.", "")
        .replace("::text", "")
        .lower()
    )


def _safe_logging(connection: Connection) -> None:
    """Refuse material-bearing SQL under unsafe standard SQLAlchemy logging."""
    _require(connection.engine.hide_parameters is True)
    _require(connection.engine.echo is False)
    _require(not connection.engine.logger.isEnabledFor(logging.DEBUG))


def _lookup_floor(connection: Connection) -> None:
    # Operator-free admission query must precede product/settings/catalog access.
    schemas, temp_oid, temp_name, uuid_oid, canonical_uuid, text_oid, canonical_text = (
        connection.execute(
            sa.text(
                "SELECT pg_catalog.current_schemas(true), pg_catalog.pg_my_temp_sche"
                "ma(), "
                "pg_catalog.pg_my_temp_schema()::pg_catalog.regnamespace::pg_catalog"
                ".text, "
                "pg_catalog.to_regtype('uuid')::pg_catalog.oid, "
                "pg_catalog.to_regtype('pg_catalog.uuid')::pg_catalog.oid, "
                "pg_catalog.to_regtype('text')::pg_catalog.oid, "
                "pg_catalog.to_regtype('pg_catalog.text')::pg_catalog.oid"
            )
        ).one()
    )
    remaining = [name for name in schemas if not (temp_oid and name == temp_name)]
    _require(bool(remaining) and remaining[0] == "pg_catalog")
    _require(uuid_oid == canonical_uuid and text_oid == canonical_text)


def _catalog(connection: Connection) -> tuple[object, ...]:
    """Actual keys, FK effects, RLS/helper and effective write privileges.

    This is deliberately bounded to the seven relations the owner consumes.
    Product extension checks stay in the product operation, on this Connection.
    """
    inspector = sa.inspect(connection)
    facts: list[object] = []
    function = connection.execute(
        sa.text(
            "SELECT p.prosrc, l.lanname, p.provolatile, p.prosecdef, "
            "p.prorettype::pg_catalog.regtype::pg_catalog.text, "
            "p.proconfig, pg_catalog.has_function_privilege(current_user,p.oid,'EXEC"
            "UTE'), p.oid "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prol"
            "ang "
            "WHERE p.oid=pg_catalog.to_regprocedure('public.app_current_tenant_id()')"
        )
    ).one()
    _require(
        _normalize(function[0])
        == _normalize(
            "BEGIN RETURN NULLIF(pg_catalog.current_setting('app.current_tenant', tr"
            "ue), '')::uuid; "
            "EXCEPTION WHEN invalid_text_representation THEN RETURN NULL; END;"
        )
    )
    _require(tuple(function[1:7]) == ("plpgsql", "s", False, "uuid", None, True))
    facts.append(tuple(function))
    for table in _TABLES:
        name = "public." + table.name
        # Generic ORM primitives use these unqualified names. Pin their actual
        # resolution instead of imposing one deployment search_path spelling.
        _require(
            connection.scalar(
                sa.text(
                    "SELECT pg_catalog.to_regclass(:short)::pg_catalog.oid = "
                    "pg_catalog.to_regclass(:qualified)::pg_catalog.oid"
                ),
                {"short": table.name, "qualified": name},
            )
            is True
        )
        columns = inspector.get_columns(table.name, schema="public")
        _require({column["name"] for column in columns} == set(table.c.keys()))
        by_name = {column["name"]: column for column in columns}
        for column in table.c:
            live = by_name[column.name]
            _require(live["nullable"] == column.nullable)
            _require(
                live["type"]._type_affinity is column.type._type_affinity
                or (
                    table.name == "parties"
                    and column.name == "party_type"
                    and isinstance(live["type"], sa.String)
                )
            )
            if isinstance(column.type, sa.String):
                _require(
                    cast(sa.String, live["type"]).length
                    == (
                        20
                        if table.name == "parties" and column.name == "party_type"
                        else column.type.length
                    )
                )
            if isinstance(column.type, sa.DateTime):
                _require(cast(sa.DateTime, live["type"]).timezone is True)
        posture = connection.execute(
            sa.text(
                "SELECT c.oid,c.relkind,c.relrowsecurity,c.relforcerowsecurity, "
                "pg_catalog.has_schema_privilege(current_user,'public','USAGE'), "
                "pg_catalog.has_table_privilege(current_user,c.oid,'SELECT'), "
                "pg_catalog.has_table_privilege(current_user,c.oid,'INSERT') "
                "FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(:rela"
                "tion)"
            ),
            {"relation": name},
        ).one()
        _require(
            posture[1] == "r"
            and tuple(posture[2:4])
            == ((False, False) if table is _TENANT else (True, True))
        )
        _require(all(posture[4:]))
        if table is not _AUDIT:
            _require(
                connection.scalar(
                    sa.text(
                        "SELECT pg_catalog.has_table_privilege(current_user,pg_catal"
                        "og.to_regclass(:relation),'UPDATE')"
                    ),
                    {"relation": name},
                )
                is True
            )
        constraints = connection.execute(
            sa.text(
                "SELECT c.contype, c.convalidated, c.condeferrable, c.condeferred, "
                "ARRAY(SELECT a.attname FROM pg_catalog.unnest(c.conkey) WITH ORDINA"
                "LITY k(n,o) "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnu"
                "m=k.n ORDER BY k.o), "
                "c.confrelid::pg_catalog.regclass::pg_catalog.text, "
                "ARRAY(SELECT a.attname FROM pg_catalog.unnest(c.confkey) WITH ORDIN"
                "ALITY k(n,o) "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.confrelid AND a.attn"
                "um=k.n ORDER BY k.o), "
                "c.confdeltype, pg_catalog.pg_get_constraintdef(c.oid), c.oid, c.con"
                "fupdtype, c.confmatchtype "
                "FROM pg_catalog.pg_constraint c WHERE c.conrelid=pg_catalog.to_regc"
                "lass(:relation) ORDER BY c.oid"
            ),
            {"relation": name},
        ).all()
        _require(all(row[1] and not row[2] and not row[3] for row in constraints))
        _require(
            [tuple(row[4]) for row in constraints if row[0] == "p"]
            == [tuple(column.name for column in table.primary_key.columns)]
        )
        expected_fks = {
            (
                tuple(element.parent.name for element in fk.elements),
                (
                    fk.elements[0].target_fullname.rsplit(".", 1)[0]
                    if "." in fk.elements[0].target_fullname.rsplit(".", 1)[0]
                    else "public." + fk.elements[0].target_fullname.rsplit(".", 1)[0]
                ),
                tuple(
                    element.target_fullname.rsplit(".", 1)[1] for element in fk.elements
                ),
                {"CASCADE": "c", "RESTRICT": "r", "SET NULL": "n", None: "a"}[
                    fk.ondelete
                ],
            )
            for fk in table.foreign_key_constraints
        }
        actual_fks = {
            (
                tuple(row[4]),
                row[5] if "." in row[5] else "public." + row[5],
                tuple(row[6]),
                row[7],
            )
            for row in constraints
            if row[0] == "f"
        }
        _require(actual_fks == expected_fks)
        _require(
            all(
                row[10] == "a" and row[11] == "s"
                for row in constraints
                if row[0] == "f"
            )
        )
        if table is _PARTY:
            checks = [row[8] for row in constraints if row[0] == "c"]
            _require(
                len(checks) == 1
                and _normalize(checks[0])
                == _normalize(
                    "CHECK (((party_type)::pg_catalog.text = ANY ((ARRAY['person'::c"
                    "haracter varying, "
                    "'organization'::character varying])::pg_catalog.text[])))"
                )
            )
        indexes = connection.execute(
            sa.text(
                "SELECT i.indisunique,i.indisprimary,i.indisvalid,i.indisready,i.ind"
                "islive, "
                "pg_catalog.pg_get_indexdef(i.indexrelid),pg_catalog.pg_get_expr(i.i"
                "ndpred,i.indrelid), "
                "pg_catalog.pg_get_expr(i.indexprs,i.indrelid), "
                "ARRAY(SELECT a.attname FROM pg_catalog.unnest(i.indkey) WITH ORDINA"
                "LITY k(n,o) "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnu"
                "m=k.n "
                "WHERE k.o<=i.indnkeyatts ORDER BY k.o), i.indexrelid "
                "FROM pg_catalog.pg_index i WHERE i.indrelid=pg_catalog.to_regclass("
                ":relation) ORDER BY i.indexrelid"
            ),
            {"relation": name},
        ).all()
        _require(all(all(row[2:5]) for row in indexes))
        unique = {
            tuple(row[8])
            for row in indexes
            if row[0] and row[6] is None and row[7] is None
        }
        expected_unique = {tuple(column.name for column in table.primary_key.columns)}
        expected_unique.update(
            tuple(column.name for column in item.columns)
            for item in table.constraints
            if isinstance(item, sa.UniqueConstraint)
        )
        expected_unique.update(
            tuple(column.name for column in item.expressions)
            for item in table.indexes
            if item.unique
            and all(isinstance(column, sa.Column) for column in item.expressions)
        )
        _require(unique == expected_unique)
        if table is _PARTY:
            email = [row for row in indexes if row[0] and row[6] is not None]
            _require(
                len(email) == 1
                and _normalize(email[0][6]) == "emailisnotnull"
                and _normalize(email[0][7] or "") == "loweremail"
                and tuple(email[0][8]) == ("tenant_id",)
            )
        policies = connection.execute(
            sa.text(
                "SELECT p.oid,p.polrelid,p.polcmd,p.polroles,p.polpermissive, "
                "pg_catalog.pg_get_expr(p.polqual,p.polrelid), "
                "pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid) "
                "FROM pg_catalog.pg_policy p WHERE p.polrelid=pg_catalog.to_regclass"
                "(:relation) "
                "ORDER BY p.oid"
            ),
            {"relation": name},
        ).all()
        dependencies: tuple[object, ...] = ()
        if table is _TENANT:
            _require(not policies)
        else:
            _require(len(policies) == 1)
            policy_oid, relation_oid, command, roles, permissive, using, check = (
                policies[0]
            )
            _require(relation_oid == posture[0])
            _require(command == "*" and list(roles) == [0] and permissive is True)
            class_oid, proc_oid = connection.execute(
                sa.text(
                    "SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.o"
                    "id, "
                    "'pg_catalog.pg_proc'::pg_catalog.regclass::pg_catalog.oid"
                )
            ).one()
            attributes = {
                (row[0], row[1]): row[2]
                for row in connection.execute(
                    sa.text(
                        "SELECT a.attrelid,a.attname,a.attnum FROM pg_catalog.pg_att"
                        "ribute a "
                        "WHERE a.attrelid IN (:own,pg_catalog.to_regclass('public.pa"
                        "rties')) "
                        "AND a.attnum>0 AND NOT a.attisdropped"
                    ),
                    {"own": relation_oid},
                )
            }
            expected_dependencies = {
                (class_oid, relation_oid, 0, "a"),
                (proc_oid, function[7], 0, "n"),
            }
            expected = "tenant_id=app_current_tenant_id"
            if table is _PERSON:
                parties_oid = connection.scalar(
                    sa.text(
                        "SELECT pg_catalog.to_regclass('public.parties')::pg_catalog"
                        ".oid"
                    )
                )
                expected_dependencies.update(
                    {
                        (
                            class_oid,
                            relation_oid,
                            attributes[(relation_oid, "party_id")],
                            "n",
                        ),
                        (class_oid, parties_oid, attributes[(parties_oid, "id")], "n"),
                        (
                            class_oid,
                            parties_oid,
                            attributes[(parties_oid, "tenant_id")],
                            "n",
                        ),
                    }
                )
                expected = (
                    "existsselect1frompartiespwherep.id=party_persons.party_i"
                    "dandp.tenant_id=app_current_tenant_id"
                )
            else:
                expected_dependencies.add(
                    (
                        class_oid,
                        relation_oid,
                        attributes[(relation_oid, "tenant_id")],
                        "n",
                    )
                )
            dependencies = tuple(
                tuple(row)
                for row in connection.execute(
                    sa.text(
                        "SELECT DISTINCT d.refclassid,d.refobjid,d.refobjsubid,d.dep"
                        "type "
                        "FROM pg_catalog.pg_depend d "
                        "WHERE d.classid='pg_catalog.pg_policy'::pg_catalog.regclass "
                        "AND d.objid=:policy AND d.objsubid=0 "
                        "ORDER BY d.refclassid,d.refobjid,d.refobjsubid,d.deptype"
                    ),
                    {"policy": policy_oid},
                )
            )
            _require(set(dependencies) == expected_dependencies)
            # Shape is supplementary: dependency identities above are authoritative.
            _require(
                all(_normalize(value or "") == expected for value in (using, check))
            )
        _require(
            not connection.scalar(
                sa.text(
                    "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid"
                    "=pg_catalog.to_regclass(:relation) "
                    "AND NOT tgisinternal AND tgenabled<>'D') OR EXISTS(SELECT 1 FRO"
                    "M pg_catalog.pg_rewrite "
                    "WHERE ev_class=pg_catalog.to_regclass(:relation) AND rulename<>"
                    "'_RETURN')"
                ),
                {"relation": name},
            )
        )
        facts.append(
            (
                tuple(posture),
                tuple(
                    (
                        column["name"],
                        str(column["type"]),
                        column["nullable"],
                        str(column.get("default")),
                    )
                    for column in columns
                ),
                tuple(tuple(row) for row in constraints),
                tuple(tuple(row) for row in indexes),
                tuple(tuple(row) for row in policies),
                dependencies,
            )
        )
    return tuple(facts)


def _database_identity(
    connection: Connection, policy: BootstrapPolicyV1
) -> tuple[object, ...]:
    _require(not connection.get_execution_options().get("schema_translate_map"))
    actual = tuple(
        connection.execute(
            sa.text(
                "SELECT d.oid,d.datname,l.oid,l.rolname,l.rolcanlogin,l.rolsuper,l.r"
                "olbypassrls, "
                "e.oid,e.rolname,e.rolsuper,e.rolbypassrls, "
                "pg_catalog.current_setting('transaction_isolation'), "
                "pg_catalog.current_setting('transaction_read_only'), "
                "l.rolcreaterole,l.rolcreatedb,l.rolreplication,l.rolinherit,l.rolco"
                "nnlimit,l.rolvaliduntil, "
                "e.rolcreaterole,e.rolcreatedb,e.rolreplication,e.rolinherit,e.rolco"
                "nnlimit,e.rolvaliduntil "
                "FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_roles l CROS"
                "S JOIN pg_catalog.pg_roles e "
                "WHERE d.datname=pg_catalog.current_database() AND l.rolname=session"
                "_user AND e.rolname=current_user"
            )
        ).one()
    )
    _require((actual[3], actual[8]) in policy.allowed_database_role_pairs)
    _require(actual[4] is True and actual[5] is False and actual[9] is False)
    _require(actual[11:13] == ("read committed", "off"))
    return actual


class _UnusedRecoveryPorts:
    """The closed create-absent assignment never invokes recovery/verification."""

    def load(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")

    def lock_for_update(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")

    def create(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")

    def write_hash(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")

    def request_revocation(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")

    def emit(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")

    def consume(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")

    def record_event(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")

    def record_receipt(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")

    def find_receipt(self, *args: object, **kwargs: object) -> NoReturn:
        raise ValueError("bootstrap cannot invoke recovery or verification")


class _Bootstrap:
    """Private authority lifetime is shorter than material-free late validation."""

    def __init__(
        self,
        assembly: IdentityProvisioningAssemblyV1,
        command: BootstrapTenantV1,
        db: Session,
    ) -> None:
        self.assembly = assembly
        self.command = command
        self.db: Session | None = db
        self.connection: Connection | None = None
        self.operation: BootstrapProductOperation | None = None
        self.policy: BootstrapPolicyV1 | None = None
        self.identity: tuple[object, ...] | None = None
        self.catalog: tuple[object, ...] | None = None
        self.tenant_id = uuid4()
        self.party_id = uuid4()
        self.scope_set = False
        self.canonical: tuple[object, ...] | None = None
        self.assignment: CredentialCreateAbsentV1 | None = None
        self.evidence: CredentialAssignmentAuthorityEvidenceV1 | None = None
        self.snapshot: CredentialSnapshot | None = None
        self.audit_id: UUID | None = None
        self.audit_values: tuple[object, ...] | None = None
        self.assignment_live = False

    def revalidate(self, connection: Connection) -> None:
        _safe_logging(connection)
        _lookup_floor(connection)
        _require(connection is self.connection or self.connection is None)
        if self.operation is None:
            self.connection = connection
            self.operation = self.assembly.product.open(connection, self.command)
            self.policy = self.operation.policy
            _require(type(self.policy) is BootstrapPolicyV1)
            self.policy.__post_init__()
            self.identity = _database_identity(connection, self.policy)
            self.catalog = _catalog(connection)
        if not (self.operation is not None and self.policy is not None):
            raise ValueError("offline bootstrap admission or outcome differs")
        self.policy.__post_init__()
        _require(
            type(self.operation.policy) is BootstrapPolicyV1
            and self.operation.policy == self.policy
        )
        _require(_database_identity(connection, self.policy) == self.identity)
        _require(_catalog(connection) == self.catalog)
        self.operation.revalidate(connection)
        for setting in self.assembly.runtime.tenant_settings:
            actual = connection.scalar(
                sa.text("SELECT pg_catalog.current_setting(:setting,true)"),
                {"setting": setting},
            )
            _require(
                actual == str(self.tenant_id)
                if self.scope_set
                else actual in (None, "")
            )
        _require(
            connection.scalar(sa.text("SELECT public.app_current_tenant_id()"))
            == (self.tenant_id if self.scope_set else None)
        )
        if self.canonical is not None:
            _require(self.rows(connection) == self.canonical)
        if self.snapshot is not None:
            self.credential(connection)
        if self.audit_id is not None:
            _require(self.audit_row(connection) == self.audit_values)

    def rows(self, connection: Connection) -> tuple[object, ...]:
        return (
            tuple(
                connection.execute(
                    sa.select(_TENANT).where(_TENANT.c.id == self.tenant_id)
                ).one()
            ),
            tuple(
                connection.execute(
                    sa.select(_PARTY).where(
                        _PARTY.c.tenant_id == self.tenant_id,
                        _PARTY.c.id == self.party_id,
                    )
                ).one()
            ),
            tuple(
                connection.execute(
                    sa.select(_PERSON).where(_PERSON.c.party_id == self.party_id)
                ).one()
            ),
            tuple(
                tuple(row)
                for row in connection.execute(
                    sa.select(_ROLE)
                    .where(_ROLE.c.tenant_id == self.tenant_id)
                    .order_by(_ROLE.c.id)
                )
            ),
            tuple(
                tuple(row)
                for row in connection.execute(
                    sa.select(_GRANT)
                    .where(
                        _GRANT.c.tenant_id == self.tenant_id,
                        _GRANT.c.party_id == self.party_id,
                    )
                    .order_by(_GRANT.c.id)
                )
            ),
        )

    def cohort(self, connection: Connection) -> tuple[UUID, ...]:
        return tuple(
            connection.scalars(
                sa.select(_CREDENTIAL.c.id)
                .where(
                    _CREDENTIAL.c.tenant_id == self.tenant_id,
                    _CREDENTIAL.c.party_id == self.party_id,
                )
                .order_by(_CREDENTIAL.c.id)
                .with_for_update()
            )
        )

    def credential(self, connection: Connection) -> None:
        _safe_logging(connection)
        command, snapshot = self.assignment, self.snapshot
        if not (
            command is not None and snapshot is not None and self.operation is not None
        ):
            raise ValueError("offline bootstrap admission or outcome differs")
        _require(
            self.cohort(connection) == (UUID(command.target_subject.credential_ref),)
        )
        _safe_logging(connection)
        row = (
            connection.execute(
                sa.select(_CREDENTIAL).where(
                    _CREDENTIAL.c.id == UUID(command.target_subject.credential_ref)
                )
            )
            .mappings()
            .one()
        )
        _require(
            row["tenant_id"] == self.tenant_id
            and row["party_id"] == self.party_id
            and row["password_hash"] == snapshot.password_hash
        )
        _safe_logging(connection)
        observed = self.operation.observe_credential(connection, command)
        _require(
            type(observed) is CredentialSnapshot
            and observed == snapshot
            and observed.reset_required is False
        )

    def audit_row(self, connection: Connection) -> tuple[object, ...]:
        return tuple(
            connection.execute(
                sa.select(_AUDIT).where(_AUDIT.c.id == self.audit_id)
            ).one()
        )

    def authorize_and_lock(
        self, command: CredentialAssignmentCommandV1
    ) -> CredentialAssignmentAuthorityEvidenceV1:
        _require(
            self.assignment_live
            and command is self.assignment
            and self.evidence is None
            and self.db is not None
        )
        if not (self.connection is not None and self.identity is not None):
            raise ValueError("offline bootstrap admission or outcome differs")
        self.revalidate(self.connection)
        _require(not self.cohort(self.connection))
        # Length-independent structured database/login identity; effective role
        # is separately captured in audit details and pinned through commit.
        actor = "postgresql:" + str(self.identity[0]) + ":" + str(self.identity[2])
        self.evidence = CredentialAssignmentAuthorityEvidenceV1(
            command, "system", actor, str(uuid4())
        )
        return self.evidence

    def lock_current(self, command: CredentialAssignmentCommandV1) -> None:
        _require(
            self.assignment_live
            and command is self.assignment
            and self.evidence is not None
        )
        if not (self.connection is not None):
            raise ValueError("offline bootstrap admission or outcome differs")
        _require(not self.cohort(self.connection))
        return None

    def create_absent(
        self,
        command: CredentialCreateAbsentV1 | CredentialCreateAbsentFromProofV1,
        *,
        password_hash: str,
        reset_required: Literal[False],
    ) -> CredentialSnapshot:
        _require(
            self.assignment_live
            and command is self.assignment
            and self.db is not None
            and reset_required is False
        )
        if not (
            self.connection is not None
            and self.db is not None
            and self.operation is not None
        ):
            raise ValueError("offline bootstrap admission or outcome differs")
        if type(command) is not CredentialCreateAbsentV1:
            raise TypeError("bootstrap accepts only its exact create-absent assignment")
        _require(not self.cohort(self.connection))
        _safe_logging(self.connection)
        snapshot = self.operation.create_absent(
            self.db, command, password_hash=password_hash, reset_required=False
        )
        _require(
            type(snapshot) is CredentialSnapshot
            and snapshot.subject == command.target_subject
            and snapshot.password_hash == password_hash
            and snapshot.reset_required is False
        )
        self.snapshot = snapshot
        self.credential(self.connection)
        return snapshot

    def replace_selected(
        self,
        command: CredentialReplaceSelectedV1,
        *,
        password_hash: str,
        credential_version: int,
        reset_required: Literal[False],
    ) -> NoReturn:
        raise ValueError("bootstrap cannot replace an existing credential")

    def record_assignment(self, entry: CredentialAssignmentAuditEntryV1) -> str:
        _require(
            self.assignment_live
            and self.db is not None
            and entry.command is self.assignment
            and entry.authority is self.evidence
        )
        if not (
            self.connection is not None
            and self.db is not None
            and self.assignment is not None
            and self.evidence is not None
            and self.snapshot is not None
            and self.identity is not None
        ):
            raise ValueError("offline bootstrap admission or outcome differs")
        self.revalidate(self.connection)
        self.credential(self.connection)
        _require(
            entry.credential_version == self.snapshot.credential_version
            and entry.previous_credential_version is None
        )
        details = {
            "tenant_id": str(self.tenant_id),
            "party_id": str(self.party_id),
            "credential_id": self.assignment.target_subject.credential_ref,
            "credential_version": entry.credential_version,
            "reset_required": False,
            "authority_evidence_ref": self.evidence.authority_evidence_ref,
            "correlation_ref": self.assignment.correlation_ref,
            "database": {"oid": self.identity[0], "name": self.identity[1]},
            "login": {"oid": self.identity[2], "name": self.identity[3]},
            "effective_role": {"oid": self.identity[7], "name": self.identity[8]},
        }
        row = write_audit_event(
            self.db,
            tenant_id=self.tenant_id,
            actor_type="system",
            actor_id=self.evidence.actor_ref,
            actor_party_id=None,
            source_application=self.assembly.source_application,
            action=entry.action,
            entity_type="user_credential",
            entity_id=self.assignment.target_subject.credential_ref,
            occurred_at=entry.occurred_at,
            details=details,
        )
        self.audit_id = row.id
        actual = (
            self.connection.execute(sa.select(_AUDIT).where(_AUDIT.c.id == row.id))
            .mappings()
            .one()
        )
        _require(
            actual["tenant_id"] == self.tenant_id
            and actual["actor_type"] == "system"
            and actual["actor_id"] == self.evidence.actor_ref
            and actual["actor_party_id"] is None
        )
        _require(
            actual["source_application"] == self.assembly.source_application
            and actual["action"] == entry.action
            and actual["entity_type"] == "user_credential"
            and actual["entity_id"] == self.assignment.target_subject.credential_ref
            and actual["occurred_at"] == entry.occurred_at
            and actual["details"] == details
        )
        self.audit_values = self.audit_row(self.connection)
        return str(row.id)


def bootstrap_tenant(
    db: Session, command: BootstrapTenantV1, *, password: str
) -> BootstrapTenantResultV1:
    """Create the first canonical owner; observation returns before root commit.

    The CLI must wait for platform_session exit before announcing creation.
    Raw material is never closed over by the surviving physical-commit validator.
    """
    state: _Bootstrap | None = None
    try:
        if type(command) is not BootstrapTenantV1:
            raise TypeError("bootstrap requires a closed tenant command")
        command.__post_init__()
        assembly = _identity_provisioning_assembly()
        state = _Bootstrap(assembly, command, db)
        with _protected_platform_operation(db, state.revalidate) as connection:
            now = datetime.now(UTC)
            policy, operation = state.policy, state.operation
            if not (policy is not None and operation is not None):
                raise ValueError("offline bootstrap admission or outcome differs")
            # Real canonical Tenant exists before any tenant-scoped writes.
            tenant = identity_storage.create_tenant(
                db,
                values=identity_storage.NewTenantValues(
                    state.tenant_id,
                    command.slug,
                    command.name,
                    policy.tenant_is_active,
                    now,
                    now,
                ),
            )
            _require(
                (tenant.id, tenant.slug, tenant.name, tenant.is_active)
                == (
                    state.tenant_id,
                    command.slug,
                    command.name,
                    policy.tenant_is_active,
                )
            )
            assembly.runtime.set_tenant(db, state.tenant_id, transaction_local=True)
            state.scope_set = True
            state.revalidate(connection)
            values = operation.person_values(
                tenant_id=state.tenant_id, party_id=state.party_id, occurred_at=now
            )
            _require(
                type(values) is identity_storage.NewPersonValues
                and values.id == state.party_id
                and values.tenant_id == state.tenant_id
            )
            operation.create_person(db, values=values)
            parties = identity_storage.lock_parties(
                db, tenant_id=state.tenant_id, party_ids=(state.party_id,)
            )
            _require(
                len(parties) == 1
                and parties[0]
                == identity_storage.PartyFact(
                    values.id,
                    values.tenant_id,
                    PartyType.person,
                    values.email,
                    values.display_name,
                    values.is_active,
                )
            )
            _require(
                identity_storage.lock_person(
                    db, tenant_id=state.tenant_id, party_id=state.party_id
                )
                == identity_storage.PersonFact(
                    state.party_id, values.first_name, values.last_name
                )
            )
            role_ids = {role.slug: uuid4() for role in policy.roles}
            roles = identity_storage.provision_missing_roles(
                db,
                tenant_id=state.tenant_id,
                catalog=identity_storage.lock_roles(db, tenant_id=state.tenant_id),
                configured_names={role.slug: role.name for role in policy.roles},
                missing_role_ids=role_ids,
                created_at=now,
                updated_at=now,
            )
            _require(
                {(role.slug, role.name) for role in roles}
                == {(role.slug, role.name) for role in policy.roles}
            )
            _require(
                all(
                    role.tenant_id == state.tenant_id and role.id == role_ids[role.slug]
                    for role in roles
                )
            )
            selected = next(
                role for role in roles if role.slug == policy.initial_role_slug
            )
            grant_id = uuid4()
            grant = identity_storage.ensure_grant(
                db,
                tenant_id=state.tenant_id,
                party_id=state.party_id,
                role_id=selected.id,
                new_grant_id=grant_id,
                created_at=now,
                updated_at=now,
            )
            _require(
                grant
                == identity_storage.GrantFact(
                    grant_id, state.tenant_id, state.party_id, selected.id
                )
            )
            _require(
                identity_storage.lock_grants(
                    db, tenant_id=state.tenant_id, party_ids=(state.party_id,)
                )
                == (grant,)
            )
            _require(
                identity_storage.credential_member_ids(
                    db, tenant_id=state.tenant_id, party_id=state.party_id
                )
                == ()
            )
            actual_tenant = (
                connection.execute(
                    sa.select(_TENANT).where(_TENANT.c.id == state.tenant_id)
                )
                .mappings()
                .one()
            )
            _require(
                all(
                    actual_tenant[key] == expected
                    for key, expected in {
                        "id": state.tenant_id,
                        "slug": command.slug,
                        "name": command.name,
                        "is_active": policy.tenant_is_active,
                        "suspended_at": None,
                        "deleted_at": None,
                        "created_at": now,
                        "updated_at": now,
                    }.items()
                )
            )
            actual_party = (
                connection.execute(
                    sa.select(_PARTY).where(_PARTY.c.id == state.party_id)
                )
                .mappings()
                .one()
            )
            _require(
                all(
                    actual_party[key] == expected
                    for key, expected in {
                        "id": values.id,
                        "tenant_id": values.tenant_id,
                        "party_type": PartyType.person,
                        "email": values.email,
                        "display_name": values.display_name,
                        "is_active": values.is_active,
                        "custom_fields": values.custom_fields,
                        "created_at": values.created_at,
                        "updated_at": values.updated_at,
                    }.items()
                )
            )
            _require(
                identity_storage.lock_person(
                    db, tenant_id=state.tenant_id, party_id=state.party_id
                )
                == identity_storage.PersonFact(
                    state.party_id, values.first_name, values.last_name
                )
            )
            _require(
                set(identity_storage.lock_roles(db, tenant_id=state.tenant_id))
                == set(roles)
            )
            state.canonical = state.rows(connection)
            state.assignment = CredentialCreateAbsentV1(
                CredentialSubjectRef(
                    assembly.source_application,
                    "person",
                    str(state.party_id),
                    str(uuid4()),
                ),
                str(state.tenant_id),
                "tenant-bootstrap",
                (str(selected.id),),
                "tenant_bootstrap",
                str(uuid4()),
            )
            state.assignment_live = True
            # Unused recovery ports cannot be reached by this closed assignment.
            unused = _UnusedRecoveryPorts()
            lifecycle = CredentialLifecycle(
                store=unused,
                sessions=unused,
                recovery=unused,
                audit=unused,
                policy=operation,
                assignment_authority=state,
                assignment_store=state,
                assignment_audit=state,
            )
            try:
                receipt = lifecycle.assign_authorized_password(
                    state.assignment, password
                )
            finally:
                state.assignment_live = False
            db.flush()
            state.revalidate(connection)
            return BootstrapTenantResultV1(
                state.tenant_id, command.slug, state.party_id, receipt
            )
    finally:
        del password
        if state is not None:
            # Surviving validators retain observations/private equality snapshot,
            # never a Session-access path or live assignment authority.
            state.assignment_live = False
            state.db = None
            state.evidence = None
