"""PostgreSQL proofs for ``tenant_audit_log.v1``.

Orders is the first publishable tenant module to call ``write_audit_event``.
Its lineage creates none of the kernel audit storage, so the dependency is a
named, live-verified effect rather than an assumption about the adopter.
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

PREREQUISITE = "tenant_audit_log.v1"
TABLE = "public.audit_events"


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


def test_the_migrated_database_satisfies_the_tenant_audit_contract() -> None:
    from dotmac_kernel.migrations.verify import require_prerequisites

    engine = create_engine(_admin_url())
    with _bound_prerequisites(), engine.connect() as conn:
        require_prerequisites(conn, (PREREQUISITE,))
    engine.dispose()


def test_the_online_tenant_role_can_append_but_not_rewrite_history() -> None:
    tenant_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    with _broken(()) as conn:
        conn.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name) "
                "VALUES (CAST(:id AS uuid), :slug, 'Audit proof')"
            ),
            {"id": tenant_id, "slug": f"audit-proof-{tenant_id[:8]}"},
        )
        with conn.begin_nested():
            conn.execute(text("SET LOCAL ROLE app_user"))
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": tenant_id},
            )
            conn.execute(
                text(
                    "INSERT INTO public.audit_events "
                    "(id, tenant_id, actor_type, action, entity_type) VALUES "
                    "(CAST(:id AS uuid), CAST(:tenant AS uuid), 'system', "
                    "'proof.appended', 'proof')"
                ),
                {"id": event_id, "tenant": tenant_id},
            )

        for statement in (
            "UPDATE public.audit_events SET action = 'proof.rewritten' "
            "WHERE id = CAST(:id AS uuid)",
            "DELETE FROM public.audit_events WHERE id = CAST(:id AS uuid)",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with conn.begin_nested():
                    conn.execute(text("SET LOCAL ROLE app_user"))
                    conn.execute(
                        text(
                            "SELECT set_config("
                            "'app.current_tenant', :tenant, true)"
                        ),
                        {"tenant": tenant_id},
                    )
                    conn.execute(text(statement), {"id": event_id})


BREAKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "table-absent",
        (f"ALTER TABLE {TABLE} RENAME TO audit_events_gone",),
        r"public\.audit_events does not exist",
    ),
    (
        "actor-shape-absent",
        (f"ALTER TABLE {TABLE} DROP COLUMN actor_type",),
        r"columns differ .*'actor_type'",
    ),
    (
        "actor-type-wrong-length",
        (
            f"ALTER TABLE {TABLE} ALTER COLUMN actor_type "
            "TYPE varchar(31) USING actor_type::varchar(31)",
        ),
        r"audit_events\.actor_type length=31, expected 32",
    ),
    (
        "actor-type-not-null",
        (f"ALTER TABLE {TABLE} ALTER COLUMN actor_type SET NOT NULL",),
        r"audit_events\.actor_type nullable=False, expected True",
    ),
    (
        "occurred-at-loses-timezone",
        (
            f"ALTER TABLE {TABLE} ALTER COLUMN occurred_at "
            "TYPE timestamp without time zone "
            "USING occurred_at AT TIME ZONE 'UTC'",
        ),
        r"audit_events\.occurred_at timezone=False, expected True",
    ),
    (
        "details-default-absent",
        (f"ALTER TABLE {TABLE} ALTER COLUMN details DROP DEFAULT",),
        r"audit_events\.details has no server default",
    ),
    (
        "details-default-wrong-value",
        (f"ALTER TABLE {TABLE} ALTER COLUMN details SET DEFAULT '[]'::jsonb",),
        r"details must default to an empty JSON object",
    ),
    (
        "occurred-at-default-fixed-value",
        (
            f"ALTER TABLE {TABLE} ALTER COLUMN occurred_at "
            "SET DEFAULT '2000-01-01T00:00:00Z'::timestamptz",
        ),
        r"occurred_at must default to the current transaction timestamp",
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
        (f"ALTER TABLE {TABLE} DROP CONSTRAINT audit_events_pkey",),
        r"primary key on \('id',\)",
    ),
    (
        "tenant-foreign-key-absent",
        (f"ALTER TABLE {TABLE} DROP CONSTRAINT audit_events_tenant_id_fkey",),
        r"foreign key .*tenant_id.*public\.tenants.*ON DELETE CASCADE",
    ),
    (
        "tenant-foreign-key-wrong-schema",
        (
            "CREATE SCHEMA audit_decoy",
            "CREATE TABLE audit_decoy.tenants (id uuid PRIMARY KEY)",
            f"ALTER TABLE {TABLE} DROP CONSTRAINT audit_events_tenant_id_fkey",
            f"ALTER TABLE {TABLE} ADD CONSTRAINT audit_events_tenant_id_fkey "
            "FOREIGN KEY (tenant_id) REFERENCES "
            "audit_decoy.tenants(id) ON DELETE CASCADE",
        ),
        r"foreign key .*tenant_id.*public\.tenants.*ON DELETE CASCADE",
    ),
    (
        "tenant-index-absent",
        ("DROP INDEX public.ix_audit_events_tenant_id",),
        r"index on \('tenant_id',\)",
    ),
    (
        "tenant-index-unique",
        (
            "DROP INDEX public.ix_audit_events_tenant_id",
            f"CREATE UNIQUE INDEX ix_audit_events_tenant_id ON {TABLE} (tenant_id)",
        ),
        r"non-unique, non-partial index on \('tenant_id',\)",
    ),
    (
        "tenant-index-partial",
        (
            "DROP INDEX public.ix_audit_events_tenant_id",
            f"CREATE INDEX ix_audit_events_tenant_id ON {TABLE} "
            "(tenant_id) WHERE false",
        ),
        r"non-unique, non-partial index on \('tenant_id',\)",
    ),
    (
        "rls-disabled",
        (f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY",),
        r"must have FORCEd row-level security",
    ),
    (
        "rls-not-forced",
        (f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY",),
        r"must have FORCEd row-level security",
    ),
    (
        "policy-using-all-rows",
        (
            f"ALTER POLICY audit_events_tenant_isolation ON {TABLE} "
            "USING (true)",
        ),
        r"USING and WITH CHECK both call app_current_tenant_id",
    ),
    (
        "policy-check-all-rows",
        (
            f"ALTER POLICY audit_events_tenant_isolation ON {TABLE} "
            "WITH CHECK (true)",
        ),
        r"USING and WITH CHECK both call app_current_tenant_id",
    ),
    (
        "tenant-role-cannot-read",
        (f"REVOKE SELECT ON {TABLE} FROM app_user",),
        r"app_user needs table-level SELECT",
    ),
    (
        "tenant-role-cannot-append",
        (f"REVOKE INSERT ON {TABLE} FROM app_user",),
        r"app_user needs table-level INSERT",
    ),
    (
        "tenant-role-can-rewrite",
        (f"GRANT UPDATE ON {TABLE} TO app_user",),
        r"app_user must not hold UPDATE",
    ),
    (
        "tenant-role-can-delete",
        (f"GRANT DELETE ON {TABLE} TO app_user",),
        r"app_user must not hold DELETE",
    ),
    (
        "tenant-role-can-truncate",
        (f"GRANT TRUNCATE ON {TABLE} TO app_user",),
        r"app_user must not hold TRUNCATE",
    ),
    (
        "tenant-role-can-trigger",
        (f"GRANT TRIGGER ON {TABLE} TO app_user",),
        r"app_user must not hold TRIGGER",
    ),
    (
        "tenant-role-has-column-rewrite",
        (f"GRANT UPDATE (details) ON {TABLE} TO app_user",),
        r"app_user must not hold column-level UPDATE",
    ),
    (
        "tenant-role-has-column-references",
        (f"GRANT REFERENCES (details) ON {TABLE} TO app_user",),
        r"app_user must not hold column-level REFERENCES",
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
    """Sensitivity: the hostile cases prove more than table presence."""
    for case, statements, _ in BREAKS:
        if case == "table-absent":
            continue
        with _broken(statements) as conn:
            exists = conn.execute(
                text("SELECT to_regclass(:table) IS NOT NULL"), {"table": TABLE}
            ).scalar_one()
            assert exists, f"weak verifier unexpectedly noticed {case}"
