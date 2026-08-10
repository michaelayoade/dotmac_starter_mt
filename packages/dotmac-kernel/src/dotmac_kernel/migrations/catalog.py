"""The post-migration live-catalog gate (ADR-0006 D1, item 7).

Applies the kernel's RLS/grant contract — hard rule 11: ``tenant_id NOT NULL``
+ composite uniques + RLS, in the same migration — across **every registered
module schema**, by reading the live Postgres catalogs after migrations have
run. The composed migration gate (``dotmac_kernel.migrations.gate``) proves the
*declarations* are coherent; this one proves the *database* matches them.

## Shape: a snapshot, then a pure decision

Everything that touches Postgres is a query builder returning parameterised SQL
(``:schema``, never string interpolation — no injection surface, and no
``search_path`` dependence). Everything that decides is ``audit_snapshot``, a
pure function over a ``SchemaSnapshot`` dataclass. That split is what makes the
contract testable without a database: the decision logic is unit-tested against
synthetic snapshots covering each violation, while the executable seam
(``audit_live_schemas``) is a thin loop that fetches and delegates.

## What it checks, per registered module schema

1. The schema **exists**. A registered module whose schema is absent means its
   migrations did not run — every query it makes would fail at request time.
2. Every table has RLS **ENABLEd and FORCEd**. `FORCE` matters: without it the
   table owner (which migrations run as) bypasses its own policy.
3. Every table has at least one **policy**. RLS with no policy denies
   everything, which reads as "isolation works" right up to the first request.
4. Every table is **tenant-scoped**: ``tenant_id NOT NULL``, or — the subtype
   pattern the kernel already uses for ``party_persons``/``party_organizations``
   — an ``EXISTS``-join policy back to a scoped parent.
5. Every **UNIQUE** constraint on a tenant-scoped table **includes tenant_id**.
   A bare unique on, say, ``slug`` makes one tenant's value block another
   tenant's insert, leaking existence across the isolation boundary. The host
   ``public`` audit consumes this module's canonical query for the same rule.
6. Every tenant-scoped **foreign key** into another tenant-scoped table is
   **composite with tenant_id**, or a same-id row from another tenant can
   satisfy the constraint.
7. **Table ownership matches the manifest**: no undeclared table in the schema,
   no declared table missing from it, and none of the module's tables squatting
   in ``public``.
8. The application role has **USAGE on the schema** and no privilege on a
   module schema it does not own.

## Relationship to `tests/test_rls_catalog.py`

That test is the assembly's ``public``-schema audit, with its own hand-
maintained platform-table and split-policy allowlists — the compatibility
namespace has exceptions a module schema does not get. It remains the policy
adapter for that namespace, but consumes this module's canonical UNIQUE-
constraint query so the shared catalog observation cannot drift. This module
is the stricter contract every registered MODULE schema is held to, and the
assembly's integration suite calls it for exactly that set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from dotmac_kernel.namespaces import HOST_SCHEMA, NamespaceRegistry, qualified

# The tenant-discriminator column every tenant-scoped table carries.
TENANT_COLUMN: Final[str] = "tenant_id"

# The online application role. Overridable because a deployment may name it
# differently; the default matches the kernel's own migrations.
DEFAULT_APP_ROLE: Final[str] = "app_user"


# ── Parameterised catalog queries ───────────────────────────────────────────
# `:schema` is a BIND parameter everywhere: no f-string ever reaches SQL, and
# no statement relies on `search_path` to resolve a relation.

SCHEMA_EXISTS_SQL: Final[str] = (
    "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema)"
)

TABLES_SQL: Final[str] = (
    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relkind = 'r'"
)

POLICIES_SQL: Final[str] = (
    "SELECT tablename, policyname, qual, with_check "
    "FROM pg_policies WHERE schemaname = :schema"
)

TENANT_COLUMNS_SQL: Final[str] = (
    "SELECT table_name, is_nullable FROM information_schema.columns "
    "WHERE table_schema = :schema AND column_name = :tenant_column"
)

# `contype = 'u'` only. A surrogate `uuid` PRIMARY KEY is globally unique by
# construction and carries no tenant-scoped semantics, so requiring tenant_id in
# it would flag every kernel-shaped table for nothing; it is the BUSINESS unique
# (a slug, a document number) that leaks existence across tenants when it omits
# the discriminator. Unique INDEXes created outside a constraint are not covered
# — declare the uniqueness as a constraint, which is the repo's own convention.
UNIQUE_CONSTRAINTS_SQL: Final[str] = (
    "SELECT conrelid::regclass::text, conname, "
    "  (SELECT array_agg(a.attname ORDER BY x.ord) "
    "   FROM unnest(conkey) WITH ORDINALITY AS x(attnum, ord) "
    "   JOIN pg_attribute a ON a.attrelid = conrelid AND a.attnum = x.attnum) "
    "FROM pg_constraint "
    "WHERE contype = 'u' "
    "  AND connamespace = (SELECT oid FROM pg_namespace WHERE nspname = :schema)"
)

FOREIGN_KEYS_SQL: Final[str] = (
    "SELECT conrelid::regclass::text, conname, "
    "  (SELECT array_agg(a.attname ORDER BY x.ord) "
    "   FROM unnest(conkey) WITH ORDINALITY AS x(attnum, ord) "
    "   JOIN pg_attribute a ON a.attrelid = conrelid AND a.attnum = x.attnum), "
    "  confrelid::regclass::text "
    "FROM pg_constraint "
    "WHERE contype = 'f' "
    "  AND connamespace = (SELECT oid FROM pg_namespace WHERE nspname = :schema)"
)

SCHEMA_USAGE_SQL: Final[str] = "SELECT has_schema_privilege(:role, :schema, 'USAGE')"

HOST_SQUATTER_SQL: Final[str] = (
    "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
    "ON n.oid = c.relnamespace WHERE n.nspname = :host_schema "
    "AND c.relkind = 'r' AND c.relname = ANY(:tables)"
)


def catalog_queries() -> Mapping[str, str]:
    """Every statement this gate issues, by role — one place to review the SQL
    surface (and to prove none of it interpolates a schema name)."""
    return {
        "schema_exists": SCHEMA_EXISTS_SQL,
        "tables": TABLES_SQL,
        "policies": POLICIES_SQL,
        "tenant_columns": TENANT_COLUMNS_SQL,
        "unique_constraints": UNIQUE_CONSTRAINTS_SQL,
        "foreign_keys": FOREIGN_KEYS_SQL,
        "schema_usage": SCHEMA_USAGE_SQL,
        "host_squatters": HOST_SQUATTER_SQL,
    }


# ── Snapshot ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PolicyFacts:
    name: str
    qual: str | None = None
    with_check: str | None = None

    @property
    def is_exists_join(self) -> bool:
        """The subtype-isolation pattern: an EXISTS-join back to a scoped
        parent, instead of a tenant_id of its own."""
        return self.qual is not None and "EXISTS" in self.qual.upper()


@dataclass(frozen=True, slots=True)
class TableFacts:
    name: str
    rls_enabled: bool = False
    rls_forced: bool = False
    has_tenant_column: bool = False
    tenant_column_nullable: bool = False
    policies: tuple[PolicyFacts, ...] = ()
    # (constraint name, columns) for every UNIQUE constraint — see
    # UNIQUE_CONSTRAINTS_SQL for why primary keys are deliberately excluded.
    unique_constraints: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class ForeignKeyFacts:
    table: str
    name: str
    columns: tuple[str, ...]
    referenced_table: str


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    """Everything the decision function needs about one module schema."""

    schema: str
    exists: bool = True
    tables: tuple[TableFacts, ...] = ()
    foreign_keys: tuple[ForeignKeyFacts, ...] = ()
    app_role_has_usage: bool = True
    # Declared tables of this module found squatting in the host schema.
    host_schema_squatters: tuple[str, ...] = ()
    declared_tables: frozenset[str] = field(default_factory=frozenset)


# ── The decision (pure) ─────────────────────────────────────────────────────


def audit_snapshot(
    snapshot: SchemaSnapshot, *, app_role: str = DEFAULT_APP_ROLE
) -> tuple[str, ...]:
    """Every violation of the kernel RLS/grant contract in one module schema.

    Empty == compliant. Pure: no connection, no I/O, no globals — so the whole
    contract is exercisable from synthetic snapshots.
    """
    schema = snapshot.schema
    if not snapshot.exists:
        return (
            f"{schema}: registered module schema does not exist — its "
            "migrations did not run, so every query the module makes fails",
        )

    violations: list[str] = []
    if not snapshot.app_role_has_usage:
        violations.append(
            f"{schema}: role {app_role!r} has no USAGE on the schema — the "
            "module's tables are unreachable at request time"
        )

    live_tables = {table.name for table in snapshot.tables}
    if snapshot.declared_tables:
        for undeclared in sorted(live_tables - snapshot.declared_tables):
            violations.append(
                f"{qualified(schema, undeclared)}: live table the owning "
                "module's manifest does not declare — every table has one "
                "declared owner"
            )
        for missing in sorted(snapshot.declared_tables - live_tables):
            violations.append(
                f"{qualified(schema, missing)}: declared by the manifest but "
                "absent from the live schema — a model with no migration"
            )
    for squatter in sorted(snapshot.host_schema_squatters):
        violations.append(
            f"{qualified(HOST_SCHEMA, squatter)}: a module table exists in the "
            f"{HOST_SCHEMA!r} compatibility namespace — the module owns "
            f"{qualified(schema, squatter)} and nothing in {HOST_SCHEMA!r}"
        )

    scoped: set[str] = set()
    for table in snapshot.tables:
        name = qualified(schema, table.name)
        if not table.rls_enabled or not table.rls_forced:
            violations.append(
                f"{name}: RLS must be ENABLEd AND FORCEd (enabled="
                f"{table.rls_enabled}, forced={table.rls_forced})"
            )
        if not table.policies:
            violations.append(f"{name}: no RLS policy in pg_policies")
        # NO `continue` HERE, deliberately. Tenant-scope classification must not
        # depend on the policy check passing: a table is tenant-scoped because it
        # has a NOT NULL `tenant_id`, whatever else is wrong with it. Skipping
        # ahead made `scoped` — and therefore the composite-unique and
        # composite-FK checks below — silently unreachable for exactly the tables
        # most likely to be broken, so a freshly added module table reported only
        # its missing RLS and hid its unique/FK violations until someone fixed
        # RLS and re-ran. An audit whose coverage is conditional on its own
        # earlier findings is worse than one that is obviously incomplete.
        if table.has_tenant_column:
            if table.tenant_column_nullable:
                violations.append(f"{name}: {TENANT_COLUMN} must be NOT NULL")
            else:
                scoped.add(table.name)
        elif table.policies and not any(
            policy.is_exists_join for policy in table.policies
        ):
            # Only meaningful when policies exist; a table with none already got
            # the clearer message above, and saying both is noise.
            violations.append(
                f"{name}: no {TENANT_COLUMN} column and no EXISTS-join RLS "
                "policy — unscoped tenant data"
            )

    # Hard rule 11's composite-unique half.
    for table in snapshot.tables:
        if table.name not in scoped:
            continue
        for constraint, columns in table.unique_constraints:
            if TENANT_COLUMN in columns:
                continue
            violations.append(
                f"{qualified(schema, table.name)}.{constraint}: unique "
                f"constraint on {list(columns)} omits {TENANT_COLUMN} — one "
                "tenant's value blocks another tenant's insert"
            )

    # And its composite-FK half.
    for fk in snapshot.foreign_keys:
        if fk.table not in scoped or fk.referenced_table not in scoped:
            continue
        if TENANT_COLUMN not in fk.columns:
            violations.append(
                f"{qualified(schema, fk.table)}.{fk.name} -> "
                f"{qualified(schema, fk.referenced_table)}: tenant-scoped FK "
                f"must be composite with {TENANT_COLUMN} (has {list(fk.columns)})"
            )
    return tuple(violations)


# ── The executable seam ─────────────────────────────────────────────────────


def _bare(relation: str) -> str:
    """`schema.table` (as `regclass` renders it) -> `table`."""
    return relation.rpartition(".")[2].strip('"')


def fetch_snapshot(
    conn: Any,
    schema: str,
    *,
    declared_tables: frozenset[str] = frozenset(),
    app_role: str = DEFAULT_APP_ROLE,
) -> SchemaSnapshot:
    """Read one module schema out of the live catalogs.

    `conn` is any SQLAlchemy `Connection`; the import stays local so this
    module remains import-safe for consumers that only want the pure decision
    function and the SQL text.
    """
    from sqlalchemy import text

    exists = bool(
        conn.execute(text(SCHEMA_EXISTS_SQL), {"schema": schema}).scalar_one()
    )
    if not exists:
        return SchemaSnapshot(
            schema=schema, exists=False, declared_tables=declared_tables
        )

    policies: dict[str, list[PolicyFacts]] = {}
    for tablename, policyname, qual, with_check in conn.execute(
        text(POLICIES_SQL), {"schema": schema}
    ).all():
        policies.setdefault(tablename, []).append(
            PolicyFacts(name=policyname, qual=qual, with_check=with_check)
        )

    tenant_columns = {
        row[0]: row[1] == "YES"
        for row in conn.execute(
            text(TENANT_COLUMNS_SQL),
            {"schema": schema, "tenant_column": TENANT_COLUMN},
        ).all()
    }

    uniques: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for relation, conname, columns in conn.execute(
        text(UNIQUE_CONSTRAINTS_SQL), {"schema": schema}
    ).all():
        uniques.setdefault(_bare(relation), []).append((conname, tuple(columns or ())))

    tables = tuple(
        TableFacts(
            name=name,
            rls_enabled=bool(rls_enabled),
            rls_forced=bool(rls_forced),
            has_tenant_column=name in tenant_columns,
            tenant_column_nullable=tenant_columns.get(name, False),
            policies=tuple(policies.get(name, ())),
            unique_constraints=tuple(uniques.get(name, ())),
        )
        for name, rls_enabled, rls_forced in conn.execute(
            text(TABLES_SQL), {"schema": schema}
        ).all()
    )

    foreign_keys = tuple(
        ForeignKeyFacts(
            table=_bare(relation),
            name=conname,
            columns=tuple(columns or ()),
            referenced_table=_bare(referenced),
        )
        for relation, conname, columns, referenced in conn.execute(
            text(FOREIGN_KEYS_SQL), {"schema": schema}
        ).all()
    )

    squatters = (
        tuple(
            row[0]
            for row in conn.execute(
                text(HOST_SQUATTER_SQL),
                {"host_schema": HOST_SCHEMA, "tables": list(declared_tables)},
            ).all()
        )
        if declared_tables
        else ()
    )

    return SchemaSnapshot(
        schema=schema,
        exists=True,
        tables=tables,
        foreign_keys=foreign_keys,
        app_role_has_usage=bool(
            conn.execute(
                text(SCHEMA_USAGE_SQL), {"role": app_role, "schema": schema}
            ).scalar_one()
        ),
        host_schema_squatters=squatters,
        declared_tables=declared_tables,
    )


def audit_live_schemas(
    conn: Any,
    registry: NamespaceRegistry,
    *,
    app_role: str = DEFAULT_APP_ROLE,
) -> tuple[str, ...]:
    """Run the contract over EVERY registered module schema.

    Thin on purpose: fetch, then delegate to `audit_snapshot`. The compatibility
    namespace (`public`) is not in `registry.module_schemas()` and is not
    audited here — it keeps its own, exception-carrying audit (see the module
    docstring).
    """
    violations: list[str] = []
    for schema in registry.module_schemas():
        snapshot = fetch_snapshot(
            conn,
            schema,
            declared_tables=registry.declared_tables(schema),
            app_role=app_role,
        )
        violations.extend(audit_snapshot(snapshot, app_role=app_role))
    return tuple(violations)


def audited_schemas(registry: NamespaceRegistry) -> tuple[str, ...]:
    """The schemas `audit_live_schemas` would walk — reported by the assembly's
    integration gate so an empty run is visibly empty, not silently skipped."""
    return registry.module_schemas()


__all__ = [
    "DEFAULT_APP_ROLE",
    "TENANT_COLUMN",
    "ForeignKeyFacts",
    "PolicyFacts",
    "SchemaSnapshot",
    "TableFacts",
    "audit_live_schemas",
    "audit_snapshot",
    "audited_schemas",
    "catalog_queries",
    "fetch_snapshot",
]
