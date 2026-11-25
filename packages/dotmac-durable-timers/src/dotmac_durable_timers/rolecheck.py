"""Boot-time read-back of the two database identities (never string compares).

The split between the dispatcher role and the tenant role is the runner's main
safety property, so it is checked against the database rather than inferred
from two DSN strings: two different DSNs can log in as the same role, and a
``DATABASE_URL`` for a superuser or BYPASSRLS role runs every handler with RLS
void.

Each probe runs in its own short session that is closed (never committed).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

#: Tables the dispatcher role must NOT be able to read (tenant and module data).
TENANT_PROBE_TABLE = "mod_timers.timers"


class RoleSplitError(RuntimeError):
    """The database roles behind the two DSNs are not the required split."""


@dataclass(frozen=True, slots=True)
class RoleFacts:
    name: str
    superuser: bool
    bypass_rls: bool


def read_role(factory: Callable[[], Session]) -> RoleFacts:
    with factory() as db:
        row = db.execute(
            text(
                "SELECT r.rolname, r.rolsuper, r.rolbypassrls "
                "FROM pg_catalog.pg_roles r WHERE r.rolname = current_user"
            )
        ).one()
    return RoleFacts(str(row[0]), bool(row[1]), bool(row[2]))


def dispatcher_can_read_tenant_table(factory: Callable[[], Session]) -> bool:
    try:
        with factory() as db:
            return bool(
                db.scalar(
                    text(
                        "SELECT pg_catalog.has_table_privilege("
                        "current_user, :table, 'SELECT')"
                    ),
                    {"table": TENANT_PROBE_TABLE},
                )
            )
    except DBAPIError:
        # No USAGE on the schema: the name cannot even be resolved, which is
        # the strongest form of "cannot read it".
        return False


def verify_role_split(
    *,
    tenant_factory: Callable[[], Session],
    dispatcher_factory: Callable[[], Session],
) -> tuple[RoleFacts, RoleFacts]:
    """Refuse (``RoleSplitError``) unless the roles are two distinct, ordinary
    roles and the dispatcher cannot read tenant data. Returns both role facts."""
    tenant = read_role(tenant_factory)
    dispatcher = read_role(dispatcher_factory)
    problems: list[str] = []
    if tenant.name == dispatcher.name:
        problems.append(
            f"both DSNs log in as role {tenant.name!r}; the dispatcher and tenant "
            "roles must differ"
        )
    if tenant.superuser or tenant.bypass_rls:
        problems.append(
            f"the tenant role {tenant.name!r} is superuser or BYPASSRLS, which "
            "would void row-level security for every handler"
        )
    if dispatcher.superuser or dispatcher.bypass_rls:
        problems.append(
            f"the dispatcher role {dispatcher.name!r} is superuser or BYPASSRLS"
        )
    if dispatcher_can_read_tenant_table(dispatcher_factory):
        problems.append(
            f"the dispatcher role {dispatcher.name!r} can read {TENANT_PROBE_TABLE}"
        )
    if problems:
        raise RoleSplitError("; ".join(problems))
    return tenant, dispatcher


__all__ = ["RoleFacts", "RoleSplitError", "read_role", "verify_role_split"]
