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

from collections.abc import Sequence
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from dotmac_kernel.namespaces import HOST_SCHEMA
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
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


def _fail(prerequisite_name: str, detail: str) -> None:
    binding = binding_for(prerequisite_name)
    raise PrerequisiteNotSatisfiedError(
        f"{prerequisite_name} is bound to {binding.provider_revision!r} "
        f"(owner {binding.provider_owner!r}) but that provider did not supply "
        f"it: {detail}. A binding is a claim about the database, so it is "
        "checked against the database — fix the provider revision, or bind a "
        "provider that truthfully supplies the effect."
    )


def _assert_columns(
    bind: Connection, table: str, contracts: dict[str, _ColumnContract]
) -> None:
    inspector = sa.inspect(bind)
    columns = {
        column["name"]: column
        for column in inspector.get_columns(table, schema=HOST_SCHEMA)
    }
    if set(columns) != set(contracts):
        missing = sorted(set(contracts) - set(columns))
        extra = sorted(set(columns) - set(contracts))
        _fail(
            TENANT_SCOPE_CATALOG_V1.name,
            f"{HOST_SCHEMA}.{table} columns differ (missing={missing}, "
            f"unexpected={extra})",
        )
    for name, (
        expected,
        nullable,
        length,
        timezone,
        needs_default,
    ) in contracts.items():
        column = columns[name]
        actual = column["type"]
        if not isinstance(actual, expected):
            _fail(
                TENANT_SCOPE_CATALOG_V1.name,
                f"{HOST_SCHEMA}.{table}.{name} is {actual!s}, expected "
                f"{expected.__name__}",
            )
        if bool(column["nullable"]) is not nullable:
            _fail(
                TENANT_SCOPE_CATALOG_V1.name,
                f"{HOST_SCHEMA}.{table}.{name} nullable={column['nullable']!r}, "
                f"expected {nullable!r}",
            )
        if length is not None and getattr(actual, "length", None) != length:
            _fail(
                TENANT_SCOPE_CATALOG_V1.name,
                f"{HOST_SCHEMA}.{table}.{name} length="
                f"{getattr(actual, 'length', None)!r}, expected {length}",
            )
        if (
            timezone is not None
            and bool(getattr(actual, "timezone", False)) is not timezone
        ):
            _fail(
                TENANT_SCOPE_CATALOG_V1.name,
                f"{HOST_SCHEMA}.{table}.{name} timezone="
                f"{getattr(actual, 'timezone', None)!r}, expected {timezone!r}",
            )
        if needs_default and column.get("default") is None:
            _fail(
                TENANT_SCOPE_CATALOG_V1.name,
                f"{HOST_SCHEMA}.{table}.{name} has no server default",
            )


def verify_tenant_scope_catalog(bind: Connection) -> None:
    """Prove `public.tenants` + `app_current_tenant_id()` really exist here."""
    name = TENANT_SCOPE_CATALOG_V1.name
    inspector = sa.inspect(bind)

    for table in ("tenants", "tenant_domains"):
        if not inspector.has_table(table, schema=HOST_SCHEMA):
            _fail(name, f"{HOST_SCHEMA}.{table} does not exist")

    _assert_columns(bind, "tenants", _TENANT_COLUMNS)
    _assert_columns(bind, "tenant_domains", _TENANT_DOMAIN_COLUMNS)

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

#: `rolbypassrls` is checked only for `app_admin`: it is the whole point of that
#: role (offline/migration work must see every tenant's rows), and an
#: `app_admin` without it turns every maintenance job into a silent zero-row
#: success. The two online roles must NOT have it, for the mirror-image reason.
_REQUIRED_ROLES: Final[dict[str, bool]] = {
    "app_admin": True,
    "app_user": False,
    "platform_api": False,
}


def verify_module_database_roles(bind: Connection) -> None:
    """Prove the three grantable roles exist, with the right RLS posture."""
    name = MODULE_DATABASE_ROLES_V1.name
    if bind.dialect.name != "postgresql":
        return

    rows = bind.execute(
        sa.text(
            "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = ANY(:names)"
        ),
        {"names": list(_REQUIRED_ROLES)},
    ).all()
    found = {str(row[0]): bool(row[1]) for row in rows}

    missing = sorted(set(_REQUIRED_ROLES) - set(found))
    if missing:
        _fail(
            name,
            f"database role(s) {missing} do not exist. A module never creates a "
            "role — creating one needs privileges a module migration must not "
            "assume, and a module that invents roles is a second authority over "
            "cluster access",
        )

    for role, expected_bypass in _REQUIRED_ROLES.items():
        if found[role] is not expected_bypass:
            posture = "BYPASSRLS" if expected_bypass else "NOBYPASSRLS"
            _fail(
                name,
                f"role {role!r} must be {posture}; an online role that bypasses "
                "RLS defeats every module's tenant isolation, and an app_admin "
                "that does not turns maintenance into silent zero-row success",
            )


# ── Dispatch ────────────────────────────────────────────────────────────────

_VERIFIERS: Final[dict[str, Any]] = {
    TENANT_SCOPE_CATALOG_V1.name: verify_tenant_scope_catalog,
    MODULE_DATABASE_ROLES_V1.name: verify_module_database_roles,
}


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
        _VERIFIERS[name](bind)


__all__ = [
    "PrerequisiteNotSatisfiedError",
    "require_prerequisites",
    "verify_module_database_roles",
    "verify_tenant_scope_catalog",
]
