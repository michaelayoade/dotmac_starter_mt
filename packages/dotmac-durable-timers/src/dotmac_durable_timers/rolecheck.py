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

#: Tables the dispatcher role must NOT be able to read (tenant and module data,
#: and the outbox itself: it claims only through the SECURITY DEFINER function).
DISPATCHER_FORBIDDEN_TABLES = (
    "mod_timers.timers",
    "mod_timers.job_states",
    "public.outbox_events",
)
#: The typed claim the dispatcher must be able to execute (kernel 0035/0036).
TYPED_CLAIM_SIGNATURE = (
    "public.claim_outbox_batch_by_type(text, integer, integer, text[])"
)
#: Roles a tenant login must not be a member of.
TENANT_FORBIDDEN_MEMBERSHIPS = ("outbox_dispatcher", "app_admin")
#: Schemas whose relations a tenant login must not own.
OWNERSHIP_SCHEMAS = ("public", "mod_timers")


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


def _scalar(factory: Callable[[], Session], sql: str, **params: object) -> object:
    with factory() as db:
        return db.scalar(text(sql), params)


def dispatcher_readable_tables(factory: Callable[[], Session]) -> list[str]:
    readable: list[str] = []
    for table in DISPATCHER_FORBIDDEN_TABLES:
        try:
            granted = _scalar(
                factory,
                "SELECT pg_catalog.has_table_privilege(current_user, :t, 'SELECT')",
                t=table,
            )
        except DBAPIError:
            # No USAGE on the schema (or no such table): it cannot be read.
            continue
        if granted:
            readable.append(table)
    return readable


def verify_role_split(
    *,
    tenant_factory: Callable[[], Session],
    dispatcher_factory: Callable[[], Session],
) -> tuple[RoleFacts, RoleFacts]:
    """Refuse (``RoleSplitError``) unless the roles are the required split.

    Negative checks (superuser, BYPASSRLS, dispatcher reads tenant data) are not
    enough on their own, so the identities are also checked positively: the
    dispatcher can EXECUTE the typed claim; the tenant role is not a member of
    ``outbox_dispatcher`` or ``app_admin`` and owns no relation; both DSNs name
    the same database. Returns both role facts."""
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
    for table in dispatcher_readable_tables(dispatcher_factory):
        problems.append(f"the dispatcher role {dispatcher.name!r} can read {table}")
    try:
        can_claim = bool(
            _scalar(
                dispatcher_factory,
                "SELECT pg_catalog.has_function_privilege("
                "current_user, :sig, 'EXECUTE')",
                sig=TYPED_CLAIM_SIGNATURE,
            )
        )
    except DBAPIError:
        can_claim = False
    if not can_claim:
        problems.append(
            f"the dispatcher role {dispatcher.name!r} cannot EXECUTE the typed "
            "outbox claim (kernel migration 0036 not applied or not granted)"
        )
    for member_of in TENANT_FORBIDDEN_MEMBERSHIPS:
        try:
            member = bool(
                _scalar(
                    tenant_factory,
                    "SELECT pg_catalog.pg_has_role(current_user, :r, 'USAGE')",
                    r=member_of,
                )
            )
        except DBAPIError:
            member = False  # the role does not exist, so it cannot be a member
        if member:
            problems.append(
                f"the tenant role {tenant.name!r} is a member of {member_of!r}"
            )
    owned = _scalar(
        tenant_factory,
        "SELECT count(*) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = ANY(:schemas) "
        "AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND c.relowner = (SELECT oid FROM pg_catalog.pg_roles "
        "WHERE rolname = current_user)",
        schemas=list(OWNERSHIP_SCHEMAS),
    )
    if owned:
        problems.append(
            f"the tenant role {tenant.name!r} owns {owned} relation(s) in "
            f"{', '.join(OWNERSHIP_SCHEMAS)}; an owner is exempt from RLS"
        )
    tenant_db = _scalar(tenant_factory, "SELECT current_database()")
    dispatcher_db = _scalar(dispatcher_factory, "SELECT current_database()")
    if tenant_db != dispatcher_db:
        problems.append(
            "the two DSNs name different databases; the claim and the handler "
            "must run in the same database"
        )
    if problems:
        raise RoleSplitError("; ".join(problems))
    return tenant, dispatcher


__all__ = ["RoleFacts", "RoleSplitError", "read_role", "verify_role_split"]
