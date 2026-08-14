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
8. The role that must REACH the tables has **USAGE on the schema** — the
   tenant role when the schema holds tenant-plane tables, the online platform
   role when it holds platform-plane ones, both when it holds both. Asking for
   tenant-role USAGE unconditionally made the contract self-contradictory on a
   platform-only schema, where that role is separately required to hold no
   privilege at all.

## Two planes, two contracts (ADR-0023)

Rules 2-6 above are the TENANT contract, and they are what a module schema gets
by default. A module that genuinely operates in both security contexts declares
its control-plane tables in ``ModuleManifest.platform_tables``, and those are
held to the PLATFORM contract instead:

* **no** ``tenant_id`` column — a platform row belongs to the control plane;
* **no** RLS at all — ENABLEd with no policy denies every row to legitimate
  callers while reading as protected;
* **REVOKEd** from the tenant application role. On this plane the revoke *is*
  the isolation, so it is checked as strictly as a policy is on the other side.
  Otherwise "declare it platform" would be a way to switch isolation off;
* **reachable** by the online platform role: it must have ``USAGE`` on the
  schema and at least one row-level DML privilege on the table. A metadata-only
  or destructive privilege such as ``REFERENCES``, ``TRIGGER`` or ``TRUNCATE``
  does not make an ordinary request path usable.

Additionally, **no foreign key whose source table is in this module schema may
cross the two planes.** An FK is the one crossing the database itself would
enforce and therefore permit, so it is the one that has to be refused here.

That scope is the real one, not a simplification: `FOREIGN_KEYS_SQL` reads
constraints declared *in* the audited schema. A product-owned link table in
`public` that references the wrong plane's table is **unmonitored by this gate**
— the module's per-plane link helpers are what stop it, and an inbound-FK sweep
across every schema is the work that would make it monitored.

The classification is declared, never inferred from the absence of a
``tenant_id``: a tenant table that simply forgot its column would otherwise
reclassify itself as platform and lose its isolation silently. A module that
declares no ``platform_tables`` — every module shipped before this — is audited
exactly as it was.

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

# The ONLINE control-plane role. A platform-plane table must be reachable by it,
# or the plane is declared and unusable — the platform-side equivalent of the
# tenant side's "app role has USAGE on the schema" check. `app_admin` is
# deliberately not it: that is the offline migration role, and a table only
# `app_admin` can reach serves no request.
DEFAULT_PLATFORM_ROLE: Final[str] = "platform_api"

#: Every table privilege PostgreSQL can grant. Named once so the audit cannot
#: silently check a subset (see `ROLE_TABLE_PRIVILEGES_SQL`).
TABLE_PRIVILEGES: Final[tuple[str, ...]] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "REFERENCES",
    "DELETE",
    "TRUNCATE",
    "TRIGGER",
)

# Privileges that make a table usable by an online data path. REFERENCES,
# TRUNCATE and TRIGGER are real privileges and must be absent from `app_user`,
# but holding only one of them does not make `platform_api` able to read or
# mutate ordinary rows.
_ONLINE_DML_PRIVILEGES: Final[frozenset[str]] = frozenset(
    {"SELECT", "INSERT", "UPDATE", "DELETE"}
)


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

# Whether a role holds any privilege on each table. This is what makes a
# platform-plane declaration enforceable rather than descriptive: a platform
# table's isolation is the REVOKE, so an un-revoked one is exactly as exposed as
# a tenant table with no RLS policy, and reads just as safe.
#
# Run once per role — the tenant application role (must hold NOTHING on a
# platform table) and the online platform role (must hold at least one DML
# privilege, or the table is unreachable at request time).
#
# ALL SEVEN table privileges, not just the four DML ones. PostgreSQL's table
# privilege set is SELECT/INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER, and
# the three non-DML ones are not harmless: TRUNCATE empties the table,
# REFERENCES lets a foreign key be pointed at it (which leaks existence and
# blocks deletes), and TRIGGER attaches code to it. A gate that checked only DML
# would pass a platform table an un-revoked role could still truncate.
#
# `has_table_privilege` OR `has_any_column_privilege` for the four privileges
# that can be granted per COLUMN. A column-level `GRANT SELECT (title)` does not
# register as a table-level SELECT, so table-level inquiry alone reports "fully
# revoked" for a role that can still read the column it was granted.
#
# Both functions answer "effectively holds", which is the question that matters:
# a grant reaching the role through PUBLIC or through a role it inherits is
# still a grant.
ROLE_TABLE_PRIVILEGES_SQL: Final[str] = (
    "SELECT c.relname, "
    "  has_table_privilege(:role, c.oid, 'SELECT') "
    "    OR has_any_column_privilege(:role, c.oid, 'SELECT') AS sel, "
    "  has_table_privilege(:role, c.oid, 'INSERT') "
    "    OR has_any_column_privilege(:role, c.oid, 'INSERT') AS ins, "
    "  has_table_privilege(:role, c.oid, 'UPDATE') "
    "    OR has_any_column_privilege(:role, c.oid, 'UPDATE') AS upd, "
    "  has_table_privilege(:role, c.oid, 'REFERENCES') "
    "    OR has_any_column_privilege(:role, c.oid, 'REFERENCES') AS refs, "
    "  has_table_privilege(:role, c.oid, 'DELETE') AS del, "
    "  has_table_privilege(:role, c.oid, 'TRUNCATE') AS trunc, "
    "  has_table_privilege(:role, c.oid, 'TRIGGER') AS trig "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relkind = 'r'"
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
        "role_table_privileges": ROLE_TABLE_PRIVILEGES_SQL,
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
    # Privileges the TENANT application role effectively holds on this table.
    # Only consulted for platform-plane tables, where the REVOKE is the
    # isolation mechanism. Defaults empty so a synthetic snapshot describes a
    # correctly-revoked table unless it says otherwise.
    app_role_privileges: tuple[str, ...] = ()
    # Privileges the ONLINE PLATFORM role effectively holds. Only consulted for
    # platform-plane tables, where holding none means the table is unreachable
    # at request time. Defaults to full DML so a synthetic snapshot describes a
    # usable table unless it says otherwise — the mirror of the field above,
    # whose safe default is the empty set.
    platform_role_privileges: tuple[str, ...] = (
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
    )


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
    # A table grant is ineffective without USAGE on its schema. Only enforced
    # when the manifest declares a platform table, preserving the contract for
    # every existing tenant-only module exactly as before.
    platform_role_has_usage: bool = True
    # Declared tables of this module found squatting in the host schema.
    host_schema_squatters: tuple[str, ...] = ()
    # The subset of `declared_tables` held to the PLATFORM contract (ADR-0023).
    # Everything else in the schema is tenant-scoped, so an empty set — the
    # default — keeps every existing single-plane module audited exactly as
    # before.
    platform_tables: frozenset[str] = frozenset()
    declared_tables: frozenset[str] = field(default_factory=frozenset)


# ── The decision (pure) ─────────────────────────────────────────────────────


def _has_tenant_plane(snapshot: SchemaSnapshot, live_tables: set[str]) -> bool:
    """Does this schema hold any table held to the TENANT contract?

    Declared tables are preferred over live ones: a module that declares a
    tenant table whose migration has not run yet still needs the tenant role to
    reach the schema, and reporting only the missing table would send someone
    to fix the wrong thing.
    """
    known = set(snapshot.declared_tables) or live_tables
    return bool(known - snapshot.platform_tables)


def audit_snapshot(
    snapshot: SchemaSnapshot,
    *,
    app_role: str = DEFAULT_APP_ROLE,
    platform_role: str = DEFAULT_PLATFORM_ROLE,
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
    live_tables = {table.name for table in snapshot.tables}

    # Schema USAGE is required of the role that must REACH the tables, and only
    # of that role.
    #
    # This was unconditional, which made the contract SELF-CONTRADICTORY on a
    # platform-only schema: the tenant role was required to hold USAGE on a
    # schema in which it is separately required to hold no privilege on any
    # table. `dotmac-integration` — the first module whose tables are all
    # platform-plane — correctly grants USAGE to the platform roles alone, and
    # the audit failed a schema that was right.
    #
    # Granting `app_user` USAGE to satisfy the old check would have been worse
    # than the false positive: pointless reachability on a schema the tenant
    # role must never read.
    #
    # Found by the first real-Postgres run of a platform-only module. A
    # synthetic snapshot could not show it, because the contradiction only
    # appears when both halves meet one live schema.
    if _has_tenant_plane(snapshot, live_tables) and not snapshot.app_role_has_usage:
        violations.append(
            f"{schema}: tenant role {app_role!r} has no USAGE on the schema — "
            "the module's TENANT-plane tables are unreachable at request time"
        )

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

    # A declared platform table that does not exist was already reported above
    # as "declared but absent"; intersecting keeps this to tables really there.
    platform = snapshot.platform_tables & live_tables
    if platform and not snapshot.platform_role_has_usage:
        violations.append(
            f"{schema}: online platform role {platform_role!r} has no USAGE on "
            "the schema — its table grants are ineffective and the platform "
            "plane is unreachable at request time"
        )

    scoped: set[str] = set()
    for table in snapshot.tables:
        name = qualified(schema, table.name)
        if table.name in platform:
            violations.extend(
                _audit_platform_table(schema, table, app_role, platform_role)
            )
            continue
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

    # The planes must not reference each other. A tenant row pointing at a
    # platform row (or the reverse) is the crossing ADR-0023 exists to prevent:
    # it would let a tenant-scoped delete cascade into control-plane data, and
    # it would make a platform row's visibility depend on a tenant predicate it
    # has no column to satisfy.
    #
    # SCOPE, stated because the claim is easy to over-read: `FOREIGN_KEYS_SQL`
    # selects constraints whose SOURCE table is in this module schema, so this
    # catches a crossing the MODULE authored. A product-owned link table lives
    # in `public` or a product schema and is therefore NOT seen here — a
    # hand-written link table referencing the wrong plane's ticket is currently
    # unmonitored, not exempt (ADR-0018's distinction). The mitigation is that
    # the module ships one link helper per plane so the correct FK is generated
    # rather than typed; the gate is not the thing stopping it. Closing that
    # properly needs an inbound-FK query over every schema, which is tracked
    # rather than claimed.
    for fk in snapshot.foreign_keys:
        crosses = (fk.table in platform) != (fk.referenced_table in platform)
        if crosses and fk.referenced_table in live_tables:
            violations.append(
                f"{qualified(schema, fk.table)}.{fk.name} -> "
                f"{qualified(schema, fk.referenced_table)}: foreign key crosses "
                "the tenant/platform plane boundary — the two planes share a "
                "lifecycle, never a row (ADR-0023)"
            )
    return tuple(violations)


def _audit_platform_table(
    schema: str, table: TableFacts, app_role: str, platform_role: str
) -> tuple[str, ...]:
    """The PLATFORM contract: no tenant column, no RLS, REVOKEd from the tenant
    application role, and REACHABLE by the online platform role.

    Deliberately NOT "the tenant contract minus RLS". A platform table's
    isolation is the privilege boundary, so the REVOKE is checked as strictly
    as a policy is on the other side — otherwise declaring a table platform
    would be a way to switch isolation off, which is the failure this whole
    split is supposed to make impossible.

    The last check is the one that keeps the plane honest in the other
    direction: a table nobody can reach passes every prohibition here and is
    still broken.
    """
    name = qualified(schema, table.name)
    violations: list[str] = []

    if table.has_tenant_column:
        violations.append(
            f"{name}: declared a PLATFORM table but has a {TENANT_COLUMN} "
            "column — a platform row belongs to the control plane, not to a "
            "tenant. Move it to the tenant plane or drop the column "
            "(ADR-0023)"
        )
    # RLS is checked separately from policies, because ENABLE with NO policy is
    # the worse of the two failures and the one that reads as "protected": RLS
    # denies everything by default, so the table silently returns zero rows to
    # its legitimate control-plane callers rather than erroring.
    if table.rls_enabled or table.rls_forced:
        violations.append(
            f"{name}: declared a PLATFORM table but has row-level security "
            f"(enabled={table.rls_enabled}, forced={table.rls_forced}) — with "
            "no tenant column there is nothing for a predicate to test, and "
            "RLS with no matching policy denies every row to the control "
            "plane. Isolate this plane with GRANT/REVOKE instead (ADR-0023)"
        )
    if table.policies:
        violations.append(
            f"{name}: declared a PLATFORM table but carries RLS policies "
            f"({sorted(policy.name for policy in table.policies)}) — the "
            "predicate has no tenant column to test, so it can only ever deny "
            "everything or nothing"
        )
    if table.app_role_privileges:
        violations.append(
            f"{name}: tenant role {app_role!r} effectively holds "
            f"{sorted(table.app_role_privileges)} on a PLATFORM table — REVOKE "
            "ALL from it. On this plane the revoke IS the isolation, so an "
            "un-revoked platform table is as exposed as an unpolicied tenant "
            "one"
        )
    held_dml = _ONLINE_DML_PRIVILEGES.intersection(table.platform_role_privileges)
    if not held_dml:
        violations.append(
            f"{name}: online platform role {platform_role!r} holds NO DML "
            "privilege on a PLATFORM table (effective privileges: "
            f"{sorted(table.platform_role_privileges)}) — the plane is declared "
            "but unusable for ordinary row access. GRANT it the DML the surface "
            "needs"
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
    platform_tables: frozenset[str] = frozenset(),
    app_role: str = DEFAULT_APP_ROLE,
    platform_role: str = DEFAULT_PLATFORM_ROLE,
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
            schema=schema,
            exists=False,
            declared_tables=declared_tables,
            platform_tables=platform_tables,
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

    # Only meaningful for the platform plane, but fetched for every table so the
    # snapshot stays a plain description of the schema rather than something
    # whose contents depend on the classification being right.
    def _privileges(role: str) -> dict[str, tuple[str, ...]]:
        held: dict[str, tuple[str, ...]] = {}
        for row in conn.execute(
            text(ROLE_TABLE_PRIVILEGES_SQL), {"schema": schema, "role": role}
        ).all():
            relname, flags = row[0], row[1:]
            granted = tuple(
                privilege
                for privilege, has in zip(TABLE_PRIVILEGES, flags, strict=True)
                if has
            )
            if granted:
                held[relname] = granted
        return held

    app_role_privileges = _privileges(app_role)
    platform_role_privileges = _privileges(platform_role)

    tables = tuple(
        TableFacts(
            name=name,
            rls_enabled=bool(rls_enabled),
            rls_forced=bool(rls_forced),
            has_tenant_column=name in tenant_columns,
            tenant_column_nullable=tenant_columns.get(name, False),
            policies=tuple(policies.get(name, ())),
            unique_constraints=tuple(uniques.get(name, ())),
            app_role_privileges=app_role_privileges.get(name, ()),
            platform_role_privileges=platform_role_privileges.get(name, ()),
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
        platform_role_has_usage=bool(
            conn.execute(
                text(SCHEMA_USAGE_SQL), {"role": platform_role, "schema": schema}
            ).scalar_one()
        ),
        host_schema_squatters=squatters,
        declared_tables=declared_tables,
        platform_tables=platform_tables,
    )


def audit_live_schemas(
    conn: Any,
    registry: NamespaceRegistry,
    *,
    app_role: str = DEFAULT_APP_ROLE,
    platform_role: str = DEFAULT_PLATFORM_ROLE,
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
            platform_tables=registry.declared_platform_tables(schema),
            app_role=app_role,
            platform_role=platform_role,
        )
        violations.extend(
            audit_snapshot(snapshot, app_role=app_role, platform_role=platform_role)
        )
    return tuple(violations)


def audited_schemas(registry: NamespaceRegistry) -> tuple[str, ...]:
    """The schemas `audit_live_schemas` would walk — reported by the assembly's
    integration gate so an empty run is visibly empty, not silently skipped."""
    return registry.module_schemas()


__all__ = [
    "DEFAULT_APP_ROLE",
    "DEFAULT_PLATFORM_ROLE",
    "TABLE_PRIVILEGES",
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
