"""PostgreSQL proofs for ``platform_audit_log.v1``.

The two published module callers write ``public.platform_audit_events`` at
request time, while their own lineages create none of its storage.  This suite
proves the logical prerequisite against the migrated catalogue and then breaks
one observable at a time.  The privilege cases are the load-bearing ones: an
audit trail writable through ``UPDATE`` or ``DELETE`` by the online platform
role is editable history, not an audit log.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator, Sequence

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

PREREQUISITE = "platform_audit_log.v1"
TABLE = "public.platform_audit_events"


def _admin_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these proofs need PostgreSQL")
    return url


@contextlib.contextmanager
def _bound_prerequisites() -> Iterator[None]:
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        yield
    finally:
        install_prerequisite_bindings(previous)


@contextlib.contextmanager
def _broken(statements: Sequence[str]) -> Iterator[Connection]:
    engine = create_engine(_admin_url())
    conn = engine.connect()
    transaction = conn.begin()
    try:
        for statement in statements:
            conn.execute(text(statement))
        yield conn
    finally:
        transaction.rollback()
        conn.close()
        engine.dispose()


def test_the_migrated_database_satisfies_the_platform_audit_contract() -> None:
    from dotmac_kernel.migrations.verify import require_prerequisites

    engine = create_engine(_admin_url())
    with _bound_prerequisites(), engine.connect() as conn:
        require_prerequisites(conn, (PREREQUISITE,))
    engine.dispose()


def test_the_online_platform_role_can_append_but_not_rewrite_history() -> None:
    event_id = str(uuid.uuid4())
    with _broken(()) as conn:
        with conn.begin_nested():
            conn.execute(text("SET LOCAL ROLE platform_api"))
            conn.execute(
                text(
                    "INSERT INTO public.platform_audit_events "
                    "(id, action, entity_type) "
                    "VALUES (CAST(:id AS uuid), 'proof.appended', 'proof')"
                ),
                {"id": event_id},
            )

        for statement in (
            "UPDATE public.platform_audit_events SET action = 'proof.rewritten' "
            "WHERE id = CAST(:id AS uuid)",
            "DELETE FROM public.platform_audit_events " "WHERE id = CAST(:id AS uuid)",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with conn.begin_nested():
                    conn.execute(text("SET LOCAL ROLE platform_api"))
                    conn.execute(text(statement), {"id": event_id})


BREAKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "table-absent",
        (f"ALTER TABLE {TABLE} RENAME TO platform_audit_events_gone",),
        r"public\.platform_audit_events does not exist",
    ),
    (
        "column-absent",
        (f"ALTER TABLE {TABLE} DROP COLUMN entity_type",),
        r"columns differ .*'entity_type'",
    ),
    (
        "details-default-absent",
        (f"ALTER TABLE {TABLE} ALTER COLUMN details DROP DEFAULT",),
        r"platform_audit_events\.details has no server default",
    ),
    (
        "details-default-wrong-value",
        (f"ALTER TABLE {TABLE} ALTER COLUMN details " "SET DEFAULT '[]'::jsonb",),
        r"details must default to an empty JSON object",
    ),
    (
        "created-at-default-fixed-value",
        (
            f"ALTER TABLE {TABLE} ALTER COLUMN created_at "
            "SET DEFAULT '2000-01-01T00:00:00Z'::timestamptz",
        ),
        r"created_at must default to the current transaction timestamp",
    ),
    (
        "primary-key-absent",
        (f"ALTER TABLE {TABLE} DROP CONSTRAINT platform_audit_events_pkey",),
        r"primary key on \('id',\)",
    ),
    (
        "actor-foreign-key-absent",
        (f"ALTER TABLE {TABLE} DROP CONSTRAINT fk_platform_audit_events_admin",),
        r"foreign key .*actor_admin_id.*platform_admins.*ON DELETE SET NULL",
    ),
    (
        "actor-foreign-key-wrong-schema",
        (
            "CREATE SCHEMA audit_decoy",
            "CREATE TABLE audit_decoy.platform_admins (id uuid PRIMARY KEY)",
            f"ALTER TABLE {TABLE} DROP CONSTRAINT fk_platform_audit_events_admin",
            f"ALTER TABLE {TABLE} ADD CONSTRAINT fk_platform_audit_events_admin "
            "FOREIGN KEY (actor_admin_id) REFERENCES "
            "audit_decoy.platform_admins(id) ON DELETE SET NULL",
        ),
        r"foreign key .*actor_admin_id.*public\.platform_admins",
    ),
    (
        "actor-index-absent",
        ("DROP INDEX public.ix_platform_audit_events_actor_admin_id",),
        r"needs a non-unique, non-partial index on \('actor_admin_id',\)",
    ),
    (
        "actor-index-unique",
        (
            "DROP INDEX public.ix_platform_audit_events_actor_admin_id",
            "CREATE UNIQUE INDEX ix_platform_audit_events_actor_admin_id "
            f"ON {TABLE} (actor_admin_id)",
        ),
        r"needs a non-unique, non-partial index on \('actor_admin_id',\)",
    ),
    (
        "actor-index-partial",
        (
            "DROP INDEX public.ix_platform_audit_events_actor_admin_id",
            "CREATE INDEX ix_platform_audit_events_actor_admin_id "
            f"ON {TABLE} (actor_admin_id) WHERE false",
        ),
        r"needs a non-unique, non-partial index on \('actor_admin_id',\)",
    ),
    (
        "platform-table-has-rls",
        (f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",),
        r"must carry no row-level security",
    ),
    (
        "tenant-role-can-read",
        (f"GRANT SELECT ON {TABLE} TO app_user",),
        r"app_user.*table or column privilege",
    ),
    (
        "platform-role-cannot-read",
        ("REVOKE SELECT ON public.platform_audit_events FROM platform_api",),
        r"platform_api.*needs table-level SELECT",
    ),
    (
        "platform-role-cannot-append",
        (f"REVOKE INSERT ON {TABLE} FROM platform_api",),
        r"platform_api.*needs table-level INSERT",
    ),
    (
        "platform-role-can-rewrite",
        (f"GRANT UPDATE ON {TABLE} TO platform_api",),
        r"platform_api.*must not hold UPDATE",
    ),
    (
        "platform-role-can-delete",
        (f"GRANT DELETE ON {TABLE} TO platform_api",),
        r"platform_api.*must not hold DELETE",
    ),
    (
        "platform-role-has-column-rewrite",
        (f"GRANT UPDATE (details) ON {TABLE} TO platform_api",),
        r"platform_api.*column-level UPDATE",
    ),
)


@pytest.mark.parametrize(("case", "statements", "expected"), BREAKS)
def test_each_broken_observable_is_refused_specifically(
    case: str, statements: tuple[str, ...], expected: str
) -> None:
    from dotmac_kernel.migrations.verify import (
        PrerequisiteNotSatisfiedError,
        require_prerequisites,
    )

    with _bound_prerequisites(), _broken(statements) as conn:
        with pytest.raises(PrerequisiteNotSatisfiedError, match=expected):
            require_prerequisites(conn, (PREREQUISITE,))


def test_a_table_name_only_verifier_misses_every_nonexistence_break() -> None:
    """Sensitivity companion: the hostile cases prove contract, not fixtures."""
    for case, statements, _ in BREAKS:
        if case == "table-absent":
            continue
        with _broken(statements) as conn:
            exists = conn.execute(
                text("SELECT to_regclass(:table) IS NOT NULL"), {"table": TABLE}
            ).scalar_one()
            assert exists, f"weak verifier unexpectedly noticed {case}"
