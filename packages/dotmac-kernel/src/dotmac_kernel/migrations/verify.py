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

#: `app_admin` must be able to bypass RLS — offline/migration work has to see
#: every tenant's rows, and an `app_admin` that cannot turns maintenance into
#: silent zero-row success. The two ONLINE roles must not, for the mirror-image
#: reason: they carry request traffic, and a request that bypasses RLS defeats
#: every composed module's tenant isolation at once.
_ONLINE_ROLES: Final[tuple[str, ...]] = ("app_user", "platform_api")
_OFFLINE_ROLES: Final[tuple[str, ...]] = ("app_admin",)
_REQUIRED_ROLES: Final[tuple[str, ...]] = (*_OFFLINE_ROLES, *_ONLINE_ROLES)


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
    found = {str(row[0]): (bool(row[1]), bool(row[2])) for row in rows}

    missing = sorted(set(_REQUIRED_ROLES) - set(found))
    if missing:
        _fail(
            name,
            f"database role(s) {missing} do not exist. A module never creates a "
            "role — creating one needs privileges a module migration must not "
            "assume, and a module that invents roles is a second authority over "
            "cluster access",
        )

    for role in _ONLINE_ROLES:
        bypasses, superuser = found[role]
        if bypasses or superuser:
            reason = "SUPERUSER" if superuser else "BYPASSRLS"
            _fail(
                name,
                f"online role {role!r} is {reason}, so it bypasses row-level "
                "security and every composed module's tenant isolation with it. "
                "A superuser bypasses RLS whether or not rolbypassrls is set, "
                "which is why both are checked",
            )

    for role in _OFFLINE_ROLES:
        bypasses, superuser = found[role]
        if not (bypasses or superuser):
            _fail(
                name,
                f"role {role!r} can neither bypass RLS nor act as superuser, so "
                "offline and migration work would silently read zero rows",
            )


# ── Dispatch ────────────────────────────────────────────────────────────────

#: A prerequisite's verifier. Takes the migration's bind; raises to refuse.
Verifier = Callable[[Connection], None]


class PrerequisiteVerifierMissingError(RuntimeError):
    """A prerequisite is registered and bound, but nothing can prove it."""


_VERIFIERS: dict[str, Verifier] = {
    TENANT_SCOPE_CATALOG_V1.name: verify_tenant_scope_catalog,
    MODULE_DATABASE_ROLES_V1.name: verify_module_database_roles,
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
    "verify_module_database_roles",
    "verify_tenant_scope_catalog",
]
