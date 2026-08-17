"""Fail-closed live verification of a lineage's logical prerequisites.

`dotmac_kernel.prerequisites` lets a module say *what* it needs and an assembly
say *who* supplies it. This module is the reason that indirection is safe: it
checks the claim against the database before the requiring migration does any
DDL.

## Why a declaration is never enough

A binding is a string in a Python file. On its own it can be wrong in exactly
the ways that matter most:

- it can name a revision that was **stamped** rather than run, so
  `alembic_version` says the effect exists and the catalogue says otherwise;
- it can name a revision that **aliases** another lineage without supplying the
  effect at all;
- it can name a provider that supplies a table of the same name and a
  *different shape* — the collision class that
  `docs/inventories/migration-collisions.md` found sixteen real instances of,
  and the one that fails quietly in the dangerous direction.

So the real catalog is inspected for the observable effects the
`PrerequisiteSpec.summary` promises, before any DDL runs. A stamped or aliased
provider fails here, because stamping writes no columns.

This raises; it never warns. A prerequisite that cannot be proven is not
satisfied.

Ordering is NOT checked here — see `require_prerequisites` for why the obvious
version-table check is both wrong and redundant.

Import only from migrations — this module talks to a database, which is exactly
what `dotmac_kernel.prerequisites` refuses to do.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, NoReturn

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from dotmac_kernel.migrations.catalog import DEFAULT_APP_ROLE
from dotmac_kernel.namespaces import HOST_SCHEMA
from dotmac_kernel.prerequisites import (
    IDEMPOTENCY_LEDGER_V1,
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    PLATFORM_AUDIT_LOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    binding_for,
    prerequisite,
    validate_prerequisites,
)


class PrerequisiteNotSatisfiedError(RuntimeError):
    """A declared prerequisite is not actually present in this database."""


# ── tenant_scope_catalog.v1 ─────────────────────────────────────────────────

# The exact kernel Tenant/TenantDomain contract a provider must supply, in the
# form the inspector reports it. Ported from ERP's `20260813_tenant_projection`,
# which proved these checks against a real production-shaped catalogue before
# adopting it (hard rule 22: extract the qualifying product implementation).
#
# Tuple is (type, nullable, length, timezone, needs_server_default).
_ColumnContract = tuple[
    type[sa.types.TypeEngine[Any]], bool, int | None, bool | None, bool
]

_TENANT_COLUMNS: Final[dict[str, _ColumnContract]] = {
    "id": (sa.Uuid, False, None, None, False),
    "slug": (sa.String, False, 63, None, False),
    "name": (sa.String, False, 120, None, False),
    "is_active": (sa.Boolean, False, None, None, True),
    "suspended_at": (sa.DateTime, True, None, True, False),
    "deleted_at": (sa.DateTime, True, None, True, False),
    "created_at": (sa.DateTime, False, None, True, True),
    "updated_at": (sa.DateTime, False, None, True, True),
}

_TENANT_DOMAIN_COLUMNS: Final[dict[str, _ColumnContract]] = {
    "id": (sa.Uuid, False, None, None, False),
    "tenant_id": (sa.Uuid, False, None, None, False),
    "domain": (sa.String, False, 253, None, False),
    "verified_at": (sa.DateTime, True, None, True, False),
    "created_at": (sa.DateTime, False, None, True, True),
    "updated_at": (sa.DateTime, False, None, True, True),
}

# The semantic markers `app_current_tenant_id()` must carry. Checked against the
# real function definition rather than its mere existence, because a function of
# the right name that reads a different GUC — or is VOLATILE, or raises on a
# malformed value instead of returning NULL — silently changes what every RLS
# policy in every composed module evaluates to.
_TENANT_FUNCTION_MARKERS: Final[tuple[str, ...]] = (
    "returns uuid",
    "stable",
    "current_setting('app.current_tenant', true)",
    "invalid_text_representation",
)


def _fail(prerequisite_name: str, detail: str) -> NoReturn:
    """Refuse, naming the binding at fault. Never returns — annotated `NoReturn`
    so a caller may read a row it has just proven present without a redundant
    narrowing branch that no test could ever reach."""
    binding = binding_for(prerequisite_name)
    raise PrerequisiteNotSatisfiedError(
        f"{prerequisite_name} is bound to {binding.provider_revision!r} "
        f"(owner {binding.provider_owner!r}) but that provider did not supply "
        f"it: {detail}. A binding is a claim about the database, so it is "
        "checked against the database — fix the provider revision, or bind a "
        "provider that truthfully supplies the effect."
    )


def _assert_columns(
    bind: Connection,
    name: str,
    table: str,
    contracts: dict[str, _ColumnContract],
) -> None:
    """Prove one table's columns match `contracts`, or fail `name`.

    `name` is a parameter rather than a constant because three prerequisites now
    describe table shapes, and a shared helper that hard-codes one of them
    reports the wrong binding — which sends a reviewer to a provider that is
    not the one at fault.

    The per-column loop variable is `column_name`, not `name`. It was `name`
    until `outbox_relay.v1` needed a column-shape refusal proof: the loop
    shadowed the prerequisite argument, so every per-column `_fail` passed a
    COLUMN name where a prerequisite name belongs and `binding_for` rejected it
    as malformed. The refusal a reader got was
    `InvalidPrerequisiteNameError: prerequisite 'leased_at' must match ...`
    instead of the shape mismatch — right that something is wrong, useless about
    what. Only the whole-set branch above was ever exercised, so nothing caught
    it; `platform-retry-clock-undefaulted` in
    `tests/test_outbox_relay_prerequisite.py` now drives this loop."""
    inspector = sa.inspect(bind)
    columns = {
        column["name"]: column
        for column in inspector.get_columns(table, schema=HOST_SCHEMA)
    }
    if set(columns) != set(contracts):
        missing = sorted(set(contracts) - set(columns))
        extra = sorted(set(columns) - set(contracts))
        _fail(
            name,
            f"{HOST_SCHEMA}.{table} columns differ (missing={missing}, "
            f"unexpected={extra})",
        )
    for column_name, (
        expected,
        nullable,
        length,
        timezone,
        needs_default,
    ) in contracts.items():
        column = columns[column_name]
        actual = column["type"]
        if not isinstance(actual, expected):
            _fail(
                name,
                f"{HOST_SCHEMA}.{table}.{column_name} is {actual!s}, expected "
                f"{expected.__name__}",
            )
        if bool(column["nullable"]) is not nullable:
            _fail(
                name,
                f"{HOST_SCHEMA}.{table}.{column_name} "
                f"nullable={column['nullable']!r}, expected {nullable!r}",
            )
        if length is not None and getattr(actual, "length", None) != length:
            _fail(
                name,
                f"{HOST_SCHEMA}.{table}.{column_name} length="
                f"{getattr(actual, 'length', None)!r}, expected {length}",
            )
        if (
            timezone is not None
            and bool(getattr(actual, "timezone", False)) is not timezone
        ):
            _fail(
                name,
                f"{HOST_SCHEMA}.{table}.{column_name} timezone="
                f"{getattr(actual, 'timezone', None)!r}, expected {timezone!r}",
            )
        if needs_default and column.get("default") is None:
            _fail(
                name,
                f"{HOST_SCHEMA}.{table}.{column_name} has no server default",
            )


def verify_tenant_scope_catalog(bind: Connection) -> None:
    """Prove `public.tenants` + `app_current_tenant_id()` really exist here."""
    name = TENANT_SCOPE_CATALOG_V1.name
    inspector = sa.inspect(bind)

    for table in ("tenants", "tenant_domains"):
        if not inspector.has_table(table, schema=HOST_SCHEMA):
            _fail(name, f"{HOST_SCHEMA}.{table} does not exist")

    _assert_columns(bind, name, "tenants", _TENANT_COLUMNS)
    _assert_columns(bind, name, "tenant_domains", _TENANT_DOMAIN_COLUMNS)

    for table in ("tenants", "tenant_domains"):
        pk = tuple(
            inspector.get_pk_constraint(table, schema=HOST_SCHEMA).get(
                "constrained_columns"
            )
            or ()
        )
        if pk != ("id",):
            _fail(
                name, f"{HOST_SCHEMA}.{table} primary key is {pk!r}, expected ('id',)"
            )

    uniques = {
        tuple(c.get("column_names") or ())
        for c in inspector.get_unique_constraints("tenants", schema=HOST_SCHEMA)
    }
    if ("slug",) not in uniques:
        _fail(name, f"{HOST_SCHEMA}.tenants.slug is not unique")

    indexes = {
        tuple(i.get("column_names") or ())
        for i in inspector.get_indexes("tenant_domains", schema=HOST_SCHEMA)
    }
    if ("tenant_id",) not in indexes:
        _fail(name, f"{HOST_SCHEMA}.tenant_domains.tenant_id is not indexed")

    cascading = [
        fk
        for fk in inspector.get_foreign_keys("tenant_domains", schema=HOST_SCHEMA)
        if fk.get("constrained_columns") == ["tenant_id"]
        and fk.get("referred_table") == "tenants"
        and fk.get("referred_columns") == ["id"]
        and str((fk.get("options") or {}).get("ondelete", "")).upper() == "CASCADE"
    ]
    if len(cascading) != 1:
        _fail(
            name,
            f"{HOST_SCHEMA}.tenant_domains.tenant_id must reference tenants.id "
            "ON DELETE CASCADE exactly once",
        )

    if bind.dialect.name != "postgresql":
        return

    definition = bind.scalar(
        sa.text("SELECT pg_get_functiondef(to_regprocedure(:signature))"),
        {"signature": f"{HOST_SCHEMA}.app_current_tenant_id()"},
    )
    if definition is None:
        _fail(name, f"{HOST_SCHEMA}.app_current_tenant_id() does not exist")
    normalized = " ".join(str(definition).lower().split())
    absent = [marker for marker in _TENANT_FUNCTION_MARKERS if marker not in normalized]
    if absent:
        _fail(
            name,
            f"{HOST_SCHEMA}.app_current_tenant_id() has incompatible semantics "
            f"(missing {absent!r})",
        )

    # The catalogue is platform-level. RLS on it would filter the very rows a
    # module's tenant FK must resolve against, so a provider that "helpfully"
    # protects it has not supplied this prerequisite.
    rls = bind.execute(
        sa.text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid IN (CAST(:tenants AS regclass), CAST(:domains AS regclass))"
        ),
        {
            "tenants": f"{HOST_SCHEMA}.tenants",
            "domains": f"{HOST_SCHEMA}.tenant_domains",
        },
    ).all()
    if len(rls) != 2 or any(row[1] or row[2] for row in rls):
        _fail(name, "the tenant catalogue must not carry RLS")


# ── module_database_roles.v1 ────────────────────────────────────────────────

#: The exact attribute pair each role must have: `(rolbypassrls, rolsuper)`.
#:
#: `app_admin` bypasses RLS because offline/migration work has to see every
#: tenant's rows, and one that cannot turns maintenance into silent zero-row
#: success. It is NOT a superuser: kernel `0001` creates it `LOGIN BYPASSRLS`,
#: and accepting a superuser here would certify a cluster-wide identity — DDL on
#: any database, role creation, `COPY PROGRAM` — to satisfy a requirement that
#: is only ever about reading past RLS.
#:
#: The two ONLINE roles must have neither. They carry request traffic, and a
#: request that bypasses RLS defeats every composed module's tenant isolation at
#: once. Superuser is checked as well as the flag because **a superuser bypasses
#: RLS regardless of `rolbypassrls`** — reading only the flag would certify
#: `app_user SUPERUSER NOBYPASSRLS` as isolated.
_ROLE_CONTRACT: Final[dict[str, tuple[bool, bool]]] = {
    "app_admin": (True, False),
    "app_user": (False, False),
    "platform_api": (False, False),
}
_REQUIRED_ROLES: Final[tuple[str, ...]] = tuple(_ROLE_CONTRACT)


def role_violations(observed: Mapping[str, tuple[bool, bool]]) -> list[str]:
    """Decide role posture from observed `(rolbypassrls, rolsuper)` pairs.

    Split out from the query — like `migrations.catalog`'s builder/decision
    split — so every attribute combination is exercisable without a database.
    """
    problems: list[str] = []
    missing = sorted(set(_ROLE_CONTRACT) - set(observed))
    if missing:
        problems.append(
            f"database role(s) {missing} do not exist. A module never creates a "
            "role — creating one needs privileges a module migration must not "
            "assume, and a module that invents roles is a second authority over "
            "cluster access"
        )
    for role, (want_bypass, want_super) in _ROLE_CONTRACT.items():
        if role not in observed:
            continue
        bypasses, superuser = observed[role]
        if superuser is not want_super:
            problems.append(
                f"role {role!r} must have rolsuper={want_super}. A superuser "
                "bypasses row-level security whether or not rolbypassrls is set, "
                "and carries cluster-wide authority no module needs"
                if want_super is False
                else f"role {role!r} must have rolsuper={want_super}"
            )
        if bypasses is not want_bypass:
            problems.append(
                f"role {role!r} must have rolbypassrls={want_bypass}: "
                + (
                    "offline and migration work would otherwise read zero rows"
                    if want_bypass
                    else "an online role that bypasses RLS defeats every "
                    "composed module's tenant isolation"
                )
            )
    return problems


def verify_module_database_roles(bind: Connection) -> None:
    """Prove the three grantable roles exist, with the right RLS posture.

    `rolsuper` is checked as well as `rolbypassrls`, because **a superuser
    bypasses RLS regardless of that flag.** An earlier draft read only
    `rolbypassrls`, so `app_user SUPERUSER NOBYPASSRLS` satisfied the
    prerequisite while silently defeating tenant isolation for every module in
    the deployment. This mirrors the existing live-catalog invariant
    (`tests/test_rls_catalog.py`), which already requires both false.
    """
    name = MODULE_DATABASE_ROLES_V1.name
    if bind.dialect.name != "postgresql":
        return

    rows = bind.execute(
        sa.text(
            "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles "
            "WHERE rolname = ANY(:names)"
        ),
        {"names": list(_REQUIRED_ROLES)},
    ).all()
    observed = {str(row[0]): (bool(row[1]), bool(row[2])) for row in rows}

    problems = role_violations(observed)
    if problems:
        _fail(name, "; ".join(problems))


# ── idempotency_ledger.v1 ───────────────────────────────────────────────────

# The at-most-once ledger's shape, in the form the inspector reports it. This
# is a RUNTIME prerequisite, unlike the two above: nothing in a requiring
# lineage's DDL touches these tables, so a module that omitted the declaration
# migrated cleanly and then died on the first guarded call — `UndefinedTable`
# from `execute_once`, in the adopter's application, not in its deploy.
#
# `fingerprint` is checked as its OWN nullable column because the defect
# ADR-0014 § 3 exists to prevent is a provider that supplies a table of this
# name with one overloaded reference column (Sub's `ref_id`, meaning a
# fingerprint in two services and a result id in five others). Such a table
# satisfies "has the right name" and silently breaks replay.
_LEDGER_COLUMNS: Final[dict[str, _ColumnContract]] = {
    "id": (sa.Uuid, False, None, None, False),
    "scope": (sa.String, False, 120, None, False),
    "key": (sa.String, False, 200, None, False),
    "fingerprint": (sa.String, True, 64, None, False),
    "operation": (sa.String, False, 120, None, False),
    "status": (sa.String, False, 20, None, False),
    "result": (sa.JSON, False, None, None, True),
    "correlation_id": (sa.String, True, 200, None, False),
    "expires_at": (sa.DateTime, True, None, True, False),
    "created_at": (sa.DateTime, False, None, True, True),
    "updated_at": (sa.DateTime, False, None, True, True),
}

_TENANT_LEDGER_COLUMNS: Final[dict[str, _ColumnContract]] = {
    "tenant_id": (sa.Uuid, False, None, None, False),
    **_LEDGER_COLUMNS,
}

#: table -> (required unique key, RLS required?)
_LEDGER_CONTRACT: Final[dict[str, tuple[tuple[str, ...], bool]]] = {
    "idempotency_records": (("tenant_id", "scope", "key"), True),
    "platform_idempotency_records": (("scope", "key"), False),
}


def verify_idempotency_ledger(bind: Connection) -> None:
    """Prove both at-most-once ledgers exist here, with their plane posture.

    The unique key is the load-bearing check: `execute_once` reserves nothing
    ahead of the effect (ADR-0014 § 5), so a second concurrent attempt is
    stopped by the constraint or it is not stopped at all. A table of the right
    name with the key missing — or widened by a sixth column — turns
    at-most-once into at-least-once without any error at any layer.
    """
    name = IDEMPOTENCY_LEDGER_V1.name
    inspector = sa.inspect(bind)

    for table, (unique_key, _) in _LEDGER_CONTRACT.items():
        if not inspector.has_table(table, schema=HOST_SCHEMA):
            _fail(name, f"{HOST_SCHEMA}.{table} does not exist")

        contracts = (
            _TENANT_LEDGER_COLUMNS if "tenant_id" in unique_key else _LEDGER_COLUMNS
        )
        _assert_columns(bind, name, table, contracts)

        uniques = {
            tuple(c.get("column_names") or ())
            for c in inspector.get_unique_constraints(table, schema=HOST_SCHEMA)
        }
        if unique_key not in uniques:
            _fail(
                name,
                f"{HOST_SCHEMA}.{table} has no unique constraint on "
                f"{unique_key!r} (found {sorted(uniques)!r}) — without it a "
                "concurrent second attempt executes the effect twice",
            )

        indexed = {
            tuple(i.get("column_names") or ())
            for i in inspector.get_indexes(table, schema=HOST_SCHEMA)
        }
        if ("expires_at",) not in indexed:
            _fail(
                name,
                f"{HOST_SCHEMA}.{table} has no index on ('expires_at',) — "
                "retention (`purge_expired`) would scan the whole ledger",
            )

    if bind.dialect.name != "postgresql":
        return

    for table, (_, needs_rls) in _LEDGER_CONTRACT.items():
        row = bind.execute(
            sa.text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relname = :table"
            ),
            {"schema": HOST_SCHEMA, "table": table},
        ).one()
        enabled, forced = bool(row[0]), bool(row[1])
        if needs_rls and not (enabled and forced):
            _fail(
                name,
                f"{HOST_SCHEMA}.{table} must have FORCEd row-level security "
                f"(enabled={enabled}, forced={forced}). Every guarded call in "
                "every composed module writes here, so a ledger without it "
                "leaks one tenant's keys and results to another",
            )
        if not needs_rls and (enabled or forced):
            _fail(
                name,
                f"{HOST_SCHEMA}.{table} is the platform plane and must carry no "
                f"RLS (enabled={enabled}, forced={forced}); its isolation is "
                "the revoked grant, and a policy here evaluates "
                "app_current_tenant_id() where there is no tenant",
            )


# ── platform_audit_log.v1 ──────────────────────────────────────────────────

_PLATFORM_AUDIT_COLUMNS: Final[dict[str, _ColumnContract]] = {
    "id": (sa.Uuid, False, None, None, False),
    "actor_admin_id": (sa.Uuid, True, None, None, False),
    "action": (sa.String, False, 120, None, False),
    "entity_type": (sa.String, False, 120, None, False),
    "entity_id": (sa.String, True, 120, None, False),
    "details": (sa.JSON, False, None, None, True),
    "created_at": (sa.DateTime, False, None, True, True),
}

_TABLE_PRIVILEGE: Final[sa.TextClause] = sa.text(
    "SELECT has_table_privilege(CAST(:role AS name), "
    "CAST(:table AS text), CAST(:privilege AS text))"
)
_COLUMN_PRIVILEGE: Final[sa.TextClause] = sa.text(
    "SELECT has_any_column_privilege(CAST(:role AS name), "
    "CAST(:table AS text), CAST(:privilege AS text))"
)


def _has_table_privilege(
    bind: Connection, role: str, table: str, privilege: str
) -> bool:
    return bool(
        bind.execute(
            _TABLE_PRIVILEGE,
            {"role": role, "table": table, "privilege": privilege},
        ).scalar_one()
    )


def _has_column_privilege(
    bind: Connection, role: str, table: str, privilege: str
) -> bool:
    return bool(
        bind.execute(
            _COLUMN_PRIVILEGE,
            {"role": role, "table": table, "privilege": privilege},
        ).scalar_one()
    )


def verify_platform_audit_log(bind: Connection) -> None:
    """Prove the platform audit writer reaches append-only isolated storage."""
    name = PLATFORM_AUDIT_LOG_V1.name
    table = "platform_audit_events"
    qualified = f"{HOST_SCHEMA}.{table}"
    inspector = sa.inspect(bind)

    if not inspector.has_table(table, schema=HOST_SCHEMA):
        _fail(name, f"{qualified} does not exist")
    _assert_columns(bind, name, table, _PLATFORM_AUDIT_COLUMNS)

    columns = {
        column["name"]: column
        for column in inspector.get_columns(table, schema=HOST_SCHEMA)
    }
    details_default = "".join(str(columns["details"].get("default", "")).split())
    if details_default.lower() not in {"'{}'::json", "'{}'::jsonb"}:
        _fail(
            name,
            f"{qualified}.details must default to an empty JSON object "
            f"(found {columns['details'].get('default')!r})",
        )
    created_at_default = "".join(
        str(columns["created_at"].get("default", "")).split()
    ).lower()
    if created_at_default not in {
        "now()",
        "current_timestamp",
        "transaction_timestamp()",
    }:
        _fail(
            name,
            f"{qualified}.created_at must default to the current transaction "
            f"timestamp (found {columns['created_at'].get('default')!r})",
        )

    primary_key = tuple(
        inspector.get_pk_constraint(table, schema=HOST_SCHEMA).get(
            "constrained_columns"
        )
        or ()
    )
    if primary_key != ("id",):
        _fail(
            name,
            f"{qualified} needs a primary key on ('id',) (found {primary_key!r})",
        )

    foreign_keys = inspector.get_foreign_keys(table, schema=HOST_SCHEMA)
    actor_fk = next(
        (
            fk
            for fk in foreign_keys
            if tuple(fk.get("constrained_columns") or ()) == ("actor_admin_id",)
        ),
        None,
    )
    actor_fk_ok = bool(
        actor_fk
        and actor_fk.get("referred_schema") == HOST_SCHEMA
        and actor_fk.get("referred_table") == "platform_admins"
        and tuple(actor_fk.get("referred_columns") or ()) == ("id",)
        and str((actor_fk.get("options") or {}).get("ondelete", "")).upper()
        == "SET NULL"
    )
    if not actor_fk_ok:
        _fail(
            name,
            f"{qualified} needs a foreign key from ('actor_admin_id',) to "
            "public.platform_admins ('id',) ON DELETE SET NULL",
        )

    actor_indexes = [
        index
        for index in inspector.get_indexes(table, schema=HOST_SCHEMA)
        if tuple(index.get("column_names") or ()) == ("actor_admin_id",)
    ]
    usable_actor_index = next(
        (
            index
            for index in actor_indexes
            if not bool(index.get("unique"))
            and not bool((index.get("dialect_options") or {}).get("postgresql_where"))
        ),
        None,
    )
    if usable_actor_index is None:
        _fail(
            name,
            f"{qualified} needs a non-unique, non-partial index on "
            "('actor_admin_id',)",
        )

    if bind.dialect.name != "postgresql":
        return

    rls = bind.execute(
        sa.text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relname = :table"
        ),
        {"schema": HOST_SCHEMA, "table": table},
    ).one()
    if bool(rls[0]) or bool(rls[1]):
        _fail(
            name,
            f"{qualified} is the platform plane and must carry no row-level "
            f"security (enabled={bool(rls[0])}, forced={bool(rls[1])})",
        )

    if bool(
        bind.execute(
            _ANY_TABLE_PRIVILEGE,
            {"role": DEFAULT_APP_ROLE, "table": qualified},
        ).scalar_one()
    ):
        _fail(
            name,
            f"{DEFAULT_APP_ROLE} holds a table or column privilege on "
            f"{qualified}; the tenant role must not reach the platform log",
        )

    for privilege in ("SELECT", "INSERT"):
        if not _has_table_privilege(bind, "platform_api", qualified, privilege):
            _fail(
                name,
                f"platform_api needs table-level {privilege} on {qualified}; "
                "a partial column grant is not a usable audit writer contract",
            )

    for privilege in ("UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        if _has_table_privilege(bind, "platform_api", qualified, privilege):
            _fail(
                name,
                f"platform_api must not hold {privilege} on {qualified}; "
                "online audit history is append-only",
            )
        if privilege in {"UPDATE", "REFERENCES"} and _has_column_privilege(
            bind, "platform_api", qualified, privilege
        ):
            _fail(
                name,
                f"platform_api must not hold column-level {privilege} on "
                f"{qualified}; a column grant is still an audit-history "
                "mutation path",
            )


# ── outbox_relay.v1 ─────────────────────────────────────────────────────────

# The relay's row shape, in the form the inspector reports it. Like the ledger
# above this is a RUNTIME prerequisite: a consuming lineage's DDL touches none
# of it, so an undeclared consumer migrates cleanly and fails on its first
# claim.
#
# Every column here is load-bearing to the relay loop, which is why the whole
# set is pinned rather than the two lease columns alone:
#   leased_by/leased_at — the lease, and the stale-lease reclaim predicate;
#   attempts/available_at/last_error — the retry state the Python policy
#     recomputes on every failure (`messaging.relay.record_failure`);
#   status — where the DEAD-LETTER outcome is recorded. There is deliberately
#     no `dead_lettered` column: `status = 'dead'` IS the dead letter, and the
#     row is retained rather than deleted, so a provider that drops `status` to
#     a boolean has silently discarded the terminal state.
_RELAY_COLUMNS: Final[dict[str, _ColumnContract]] = {
    "id": (sa.Uuid, False, None, None, False),
    "event_type": (sa.String, False, 120, None, False),
    "payload": (sa.JSON, False, None, None, True),
    "status": (sa.String, False, 20, None, False),
    "attempts": (sa.Integer, False, None, None, True),
    "available_at": (sa.DateTime, False, None, True, True),
    "correlation_id": (sa.String, True, 200, None, False),
    "sent_at": (sa.DateTime, True, None, True, False),
    "last_error": (sa.String, True, 500, None, False),
    "leased_by": (sa.String, True, 200, None, False),
    "leased_at": (sa.DateTime, True, None, True, False),
    "created_at": (sa.DateTime, False, None, True, True),
    "updated_at": (sa.DateTime, False, None, True, True),
}

_TENANT_RELAY_COLUMNS: Final[dict[str, _ColumnContract]] = {
    "tenant_id": (sa.Uuid, False, None, None, False),
    **_RELAY_COLUMNS,
}

#: Both claim-path indexes. `(status, available_at)` serves the pending-and-due
#: half of the claim predicate and `(status, leased_at)` the stale-lease reclaim
#: half — the two arms of the single `WHERE` in `claim_outbox_batch`. Missing
#: either turns every poll of a large outbox into a sequential scan under
#: `FOR UPDATE SKIP LOCKED`, which is a production incident rather than a
#: slowdown: the scan holds locks for its whole duration.
_RELAY_INDEXES: Final[tuple[tuple[str, ...], ...]] = (
    ("status", "available_at"),
    ("status", "leased_at"),
)

#: The owner every relay `SECURITY DEFINER` function must have. Named rather
#: than merely "not a superuser": the definer identity IS the privilege the
#: dispatcher borrows, and `module_database_roles.v1` already fixes `app_admin`
#: as the BYPASSRLS migrator fleet-wide. A function reowned to the cluster
#: superuser turns any future defect in its body into cluster compromise.
_DEFINER_OWNER: Final[str] = "app_admin"

#: Every table privilege, plus the column-level forms of the four that have
#: them. `has_table_privilege` cannot see a column grant, so checking it alone
#: would certify `GRANT SELECT (payload) ON outbox_events TO outbox_dispatcher`
#: as EXECUTE-only. Same pairing the live-catalog gate uses on the platform
#: plane, for the same reason.
#
# Both role and table are CAST explicitly. `has_table_privilege` is overloaded
# on (name, text), (name, oid), (oid, text) and (oid, oid); two untyped bind
# parameters make the call ambiguous and PostgreSQL refuses to resolve it, which
# would fail the verifier for a reason that has nothing to do with the database
# under test.
_ANY_TABLE_PRIVILEGE: Final[sa.TextClause] = sa.text(
    """
    SELECT (
        SELECT bool_or(has_table_privilege(
                   CAST(:role AS name), CAST(:table AS text), p))
          FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE',
                            'TRUNCATE', 'REFERENCES', 'TRIGGER']) AS p
    ) OR (
        SELECT bool_or(has_any_column_privilege(
                   CAST(:role AS name), CAST(:table AS text), p))
          FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES']) AS p
    )
    """
)

#: `grantee = 0` is PUBLIC in an exploded ACL. `acldefault` matters: a NULL
#: `proacl` is not "nobody has it", it is the built-in default — and the default
#: for a function is `EXECUTE` to PUBLIC. Reading `proacl IS NULL` as safe would
#: certify the single most dangerous state this check exists to refuse.
_FUNCTION_FACTS: Final[sa.TextClause] = sa.text(
    """
    SELECT p.prosecdef,
           p.proconfig,
           pg_get_userbyid(p.proowner) AS owner,
           has_function_privilege(
               CAST(:dispatcher AS name), p.oid, 'EXECUTE') AS dispatcher_may,
           EXISTS (
               SELECT 1
                 FROM aclexplode(COALESCE(
                          p.proacl,
                          acldefault(CAST('f' AS "char"), p.proowner))) a
                WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'
           ) AS public_may
      FROM pg_proc p
     WHERE p.oid = to_regprocedure(CAST(:signature AS text))
    """
)


@dataclass(frozen=True, slots=True)
class _RelayPlane:
    """One plane of the relay: its table, its function pair, its dispatcher."""

    table: str
    columns: dict[str, _ColumnContract]
    dispatcher: str
    claim: str
    settle: str
    #: True on the tenant plane, where isolation is a FORCEd policy. False on
    #: the platform plane, where isolation is the revoked `app_user` grant and a
    #: policy would evaluate `app_current_tenant_id()` with no tenant to find.
    policied: bool


_RELAY_PLANES: Final[tuple[_RelayPlane, ...]] = (
    _RelayPlane(
        table="outbox_events",
        columns=_TENANT_RELAY_COLUMNS,
        dispatcher="outbox_dispatcher",
        claim="claim_outbox_batch(text, integer, integer)",
        settle=("settle_outbox_event(uuid, text, text, timestamptz, integer, text)"),
        policied=True,
    ),
    _RelayPlane(
        table="platform_outbox_events",
        columns=_RELAY_COLUMNS,
        dispatcher="platform_outbox_dispatcher",
        claim="claim_platform_outbox_batch(text, integer, integer)",
        settle=(
            "settle_platform_outbox_event"
            "(uuid, text, text, timestamptz, integer, text)"
        ),
        policied=False,
    ),
)

#: The marker a tenant relay policy must carry. Existence of a policy is not
#: enough — `USING (true)` is a policy, and it makes a FORCEd table read as
#: protected while every dispatcher-adjacent session sees every tenant's events.
_RELAY_POLICY_MARKER: Final[str] = "app_current_tenant_id()"


def _search_path_is_empty(proconfig: Sequence[str] | None) -> bool:
    """Does this function pin `search_path` to the empty string?

    Absence is False, deliberately. A `SECURITY DEFINER` function with no
    `search_path` of its own resolves unqualified names through the CALLER's
    path, so any role that can create a schema on that path can shadow a
    function or operator the body uses and have it run as the definer. That is
    a privilege-escalation vector, not a lint, so an unpinned path is refused
    exactly as hard as a missing function.
    """
    for entry in proconfig or ():
        key, _, value = str(entry).partition("=")
        if key.strip().lower() != "search_path":
            continue
        return value.strip().strip('"').strip("'").strip() == ""
    return False


def verify_outbox_relay(bind: Connection) -> None:
    """Prove both relay planes exist here, with their claim path and privileges.

    Checks the whole summary, not the table names. The relay's correctness is
    only half schema: the other half is that a NOBYPASSRLS dispatcher reaches
    cross-tenant rows through two hardened functions and by no other route. A
    provider that supplies the tables and grants the dispatcher `SELECT` has
    satisfied every name in the contract and handed away the entire outbox.
    """
    name = OUTBOX_RELAY_V1.name
    inspector = sa.inspect(bind)

    for plane in _RELAY_PLANES:
        if not inspector.has_table(plane.table, schema=HOST_SCHEMA):
            _fail(name, f"{HOST_SCHEMA}.{plane.table} does not exist")

        _assert_columns(bind, name, plane.table, plane.columns)

        indexed = {
            tuple(index.get("column_names") or ())
            for index in inspector.get_indexes(plane.table, schema=HOST_SCHEMA)
        }
        for required in _RELAY_INDEXES:
            if required not in indexed:
                _fail(
                    name,
                    f"{HOST_SCHEMA}.{plane.table} has no index on {required!r} "
                    f"(found {sorted(indexed)!r}) — the claim predicate scans "
                    "the whole outbox while holding FOR UPDATE locks",
                )

    if bind.dialect.name != "postgresql":
        return

    for plane in _RELAY_PLANES:
        _verify_relay_plane_posture(bind, name, plane)
        _verify_relay_dispatcher(bind, name, plane)
        for signature in (plane.claim, plane.settle):
            _verify_relay_function(bind, name, plane, signature)


def _verify_relay_plane_posture(
    bind: Connection, name: str, plane: _RelayPlane
) -> None:
    """RLS on the tenant plane; a revoked grant, and nothing else, on the peer."""
    row = bind.execute(
        sa.text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relname = :table"
        ),
        {"schema": HOST_SCHEMA, "table": plane.table},
    ).one()
    enabled, forced = bool(row[0]), bool(row[1])

    if not plane.policied:
        if enabled or forced:
            _fail(
                name,
                f"{HOST_SCHEMA}.{plane.table} is the platform plane and must "
                f"carry no row-level security (enabled={enabled}, "
                f"forced={forced}); its isolation is the revoked grant, and a "
                "policy here evaluates app_current_tenant_id() where there is "
                "no tenant",
            )
        held = bind.scalar(
            _ANY_TABLE_PRIVILEGE,
            {"role": DEFAULT_APP_ROLE, "table": f"{HOST_SCHEMA}.{plane.table}"},
        )
        if bool(held):
            _fail(
                name,
                f"{HOST_SCHEMA}.{plane.table} is reachable by "
                f"{DEFAULT_APP_ROLE!r} — on this plane the revoked grant IS the "
                "isolation, so a privilege held there is the whole control "
                "plane exposed to tenant request traffic",
            )
        return

    if not (enabled and forced):
        _fail(
            name,
            f"{HOST_SCHEMA}.{plane.table} must have FORCEd row-level security "
            f"(enabled={enabled}, forced={forced}). The relay claim is "
            "cross-tenant by design, so the table's own policy is the only "
            "thing keeping ordinary request traffic inside one tenant",
        )

    policies = bind.execute(
        sa.text(
            "SELECT policyname, COALESCE(qual, ''), COALESCE(with_check, '') "
            "FROM pg_policies WHERE schemaname = :schema AND tablename = :table"
        ),
        {"schema": HOST_SCHEMA, "table": plane.table},
    ).all()
    if not policies:
        _fail(
            name,
            f"{HOST_SCHEMA}.{plane.table} has FORCEd row-level security and no "
            "policy at all, which denies every row while reading as protected "
            "— the relay's own writes fail and the cause looks like a bug in "
            "the module",
        )
    # Every non-empty expression must carry the marker, checked SEPARATELY per
    # expression. Concatenating `qual` and `with_check` and searching once was
    # the first spelling and it was wrong in the direction that matters:
    # `ALTER POLICY ... USING (true)` leaves `with_check` intact, so the joined
    # string still contained `app_current_tenant_id()` while every SELECT read
    # every tenant's events. A policy is only as tight as its loosest half.
    unkeyed = sorted(
        str(policy[0])
        for policy in policies
        if any(
            str(expression).strip() and _RELAY_POLICY_MARKER not in str(expression)
            for expression in (policy[1], policy[2])
        )
        or not (str(policy[1]).strip() or str(policy[2]).strip())
    )
    if unkeyed:
        _fail(
            name,
            f"{HOST_SCHEMA}.{plane.table} policy/policies {unkeyed!r} do not "
            f"restrict rows by {_RELAY_POLICY_MARKER} — a policy that always "
            "passes is FORCEd row-level security in name only",
        )


def _verify_relay_dispatcher(bind: Connection, name: str, plane: _RelayPlane) -> None:
    """The dispatcher exists, cannot bypass RLS, and touches no table.

    Schema `USAGE` is deliberately NOT checked, and this is an unmonitored
    region rather than an exemption (ADR-0018). Both migrations grant it, so it
    looks like an obvious fifth assertion — but `PUBLIC` holds `USAGE` on
    `public` by default in every supported PostgreSQL, so
    `has_schema_privilege` answers true for the dispatcher whether or not the
    grant was ever made, and revoking it from the role does not change the
    answer. A check no break can falsify proves nothing and reads as though it
    does, which is worse than its absence.
    """
    row = bind.execute(
        sa.text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = :role"),
        {"role": plane.dispatcher},
    ).one_or_none()
    if row is None:
        _fail(
            name,
            f"database role {plane.dispatcher!r} does not exist — the relay's "
            "whole privilege boundary is that one least-privilege role, so "
            "without it a deployment drains the outbox as something else",
        )
    bypasses, superuser = bool(row[0]), bool(row[1])
    if bypasses or superuser:
        _fail(
            name,
            f"role {plane.dispatcher!r} has rolbypassrls={bypasses} "
            f"rolsuper={superuser}; both must be false. The dispatcher connects "
            "to drain events and nothing more — a superuser bypasses row-level "
            "security whether or not rolbypassrls is set",
        )
    held = bind.scalar(
        _ANY_TABLE_PRIVILEGE,
        {"role": plane.dispatcher, "table": f"{HOST_SCHEMA}.{plane.table}"},
    )
    if bool(held):
        _fail(
            name,
            f"role {plane.dispatcher!r} holds table or column privilege on "
            f"{HOST_SCHEMA}.{plane.table} — it must reach rows ONLY through the "
            "SECURITY DEFINER claim/settle pair, or the hardening is decorative",
        )


def _verify_relay_function(
    bind: Connection, name: str, plane: _RelayPlane, signature: str
) -> None:
    """One claim/settle function: it exists, and it is safe to run as its owner."""
    qualified = f"{HOST_SCHEMA}.{signature}"
    facts = bind.execute(
        _FUNCTION_FACTS,
        {"dispatcher": plane.dispatcher, "signature": qualified},
    ).one_or_none()
    if facts is None:
        _fail(
            name,
            f"{qualified} does not exist — the relay claims and settles through "
            "this exact signature, and a provider supplying the table alone "
            "leaves every drain raising UndefinedFunction at request time",
        )
    secdef, proconfig, owner, dispatcher_may, public_may = facts

    if not bool(secdef):
        _fail(
            name,
            f"{qualified} is not SECURITY DEFINER — the dispatcher holds no "
            "privilege on the table, so an INVOKER function makes every claim "
            "permission denied while the catalogue still reads correct",
        )
    if not _search_path_is_empty(proconfig):
        _fail(
            name,
            f"{qualified} is SECURITY DEFINER without an empty search_path "
            f"(proconfig={list(proconfig or ())!r}). Unqualified names then "
            "resolve through the CALLER's path, so anything the caller can "
            "shadow runs with the definer's privilege — this is a "
            "privilege-escalation vector, refused rather than warned about",
        )
    if str(owner) != _DEFINER_OWNER:
        _fail(
            name,
            f"{qualified} is SECURITY DEFINER owned by {str(owner)!r}, expected "
            f"{_DEFINER_OWNER!r} — the owner is the privilege the dispatcher "
            "borrows on every call, so it is part of the contract, not a "
            "deployment detail",
        )
    if not bool(dispatcher_may):
        _fail(
            name,
            f"role {plane.dispatcher!r} cannot EXECUTE {qualified} — it has no "
            "table privilege either, so this plane's relay cannot drain at all",
        )
    if bool(public_may):
        _fail(
            name,
            f"EXECUTE on {qualified} is granted to PUBLIC — every login role in "
            "the cluster can then claim and settle other tenants' events "
            "through a SECURITY DEFINER path that bypasses row-level security",
        )


# ── Dispatch ────────────────────────────────────────────────────────────────

#: A prerequisite's verifier. Takes the migration's bind; raises to refuse.
Verifier = Callable[[Connection], None]


class PrerequisiteVerifierMissingError(RuntimeError):
    """A prerequisite is registered and bound, but nothing can prove it."""


_VERIFIERS: dict[str, Verifier] = {
    TENANT_SCOPE_CATALOG_V1.name: verify_tenant_scope_catalog,
    MODULE_DATABASE_ROLES_V1.name: verify_module_database_roles,
    IDEMPOTENCY_LEDGER_V1.name: verify_idempotency_ledger,
    OUTBOX_RELAY_V1.name: verify_outbox_relay,
    PLATFORM_AUDIT_LOG_V1.name: verify_platform_audit_log,
}


def register_verifier(name: str, verifier: Verifier) -> None:
    """Register the live check for a product-owned prerequisite.

    `register_prerequisites` opens the VOCABULARY; this opens ENFORCEMENT, and
    both halves are needed for the registry to be genuinely open. Registering a
    spec without a verifier used to leave a product-owned prerequisite passing
    declaration and binding, then dying on a `KeyError` mid-migration — an
    "extension point" that only worked for the two effects the kernel shipped.

    A prerequisite is a claim about the database, so a product that names one
    must say how to prove it.
    """
    prerequisite(name)
    existing = _VERIFIERS.get(name)
    if existing is not None and existing is not verifier:
        raise PrerequisiteVerifierMissingError(
            f"prerequisite {name!r} already has a different verifier — one "
            "effect has one proof, and a changed contract is a new `.vN`"
        )
    _VERIFIERS[name] = verifier


def registered_verifiers() -> Mapping[str, Verifier]:
    """Every registered verifier, for diagnostics and coverage checks."""
    return dict(_VERIFIERS)


def require_prerequisites(bind: Connection, names: Sequence[str]) -> None:
    """Verify every declared prerequisite against the database, or refuse.

    Call at the top of a requiring migration's `upgrade()`, before any DDL:

    ```python
    def upgrade() -> None:
        require_prerequisites(
            op.get_bind(),
            ("tenant_scope_catalog.v1", "module_database_roles.v1"),
        )
        ...
    ```

    ## Why there is no "has the provider revision run?" check here

    An earlier draft asserted the bound revision was present in
    `alembic_version`. That was wrong on a fact about Alembic: the version table
    records the current HEAD of each branch, not the history of applied
    revisions. Once the kernel lineage advances past `0001_initial_tenant_schema`
    that row is simply gone, so the check failed against every real database —
    which is exactly how CI found it.

    It was also redundant, which is the more useful half of the lesson. Ordering
    is already guaranteed twice over: `resolve_depends_on` emits a real
    `depends_on` edge, and Alembic will not run a revision before the one it
    depends on; and a binding naming a revision that is not composed at all is
    rejected statically by the gate. What the database can uniquely answer is
    whether the EFFECTS are present — which is what the verifiers below do, and
    which a stamped provider fails regardless of what any version table says.
    """
    validate_prerequisites(names)
    for name in names:
        prerequisite(name)
        verifier = _VERIFIERS.get(name)
        if verifier is None:
            raise PrerequisiteVerifierMissingError(
                f"prerequisite {name!r} is registered and bound but has no "
                "verifier, so nothing can prove it against this database. "
                "Register one with "
                "`dotmac_kernel.migrations.verify.register_verifier(...)` — an "
                "effect that cannot be proven must not be silently assumed."
            )
        verifier(bind)


__all__ = [
    "PrerequisiteNotSatisfiedError",
    "PrerequisiteVerifierMissingError",
    "Verifier",
    "register_verifier",
    "registered_verifiers",
    "require_prerequisites",
    "role_violations",
    "verify_idempotency_ledger",
    "verify_module_database_roles",
    "verify_outbox_relay",
    "verify_platform_audit_log",
    "verify_tenant_scope_catalog",
]
