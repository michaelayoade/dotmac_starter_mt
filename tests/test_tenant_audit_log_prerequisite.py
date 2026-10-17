"""Hosted PostgreSQL qualification of the existing tenant audit provider.

Catalogue mutations are transactional and rolled back, including role grants.
The migrated database is the positive control; a version stamp supplies no DDL.
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


@contextlib.contextmanager
def _catalog(statements: Sequence[str] = ()) -> Iterator[Connection]:
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — requires hosted PostgreSQL")
    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                for statement in statements:
                    conn.execute(text(statement))
                yield conn
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
        install_prerequisite_bindings(previous)


def _require(conn: Connection) -> None:
    from dotmac_kernel.migrations.verify import require_prerequisites

    require_prerequisites(conn, (PREREQUISITE,))


@pytest.mark.parametrize("role", ("app_user", "platform_api"))
def test_nonbypass_two_tenant_append_and_caller_rollback(role: str) -> None:
    """Real non-bypass writes isolate tenants and stay in the caller transaction."""
    tenant_a, tenant_b, event_a, event_b = (str(uuid.uuid4()) for _ in range(4))
    with _catalog() as conn:
        _require(conn)
        for tenant in (tenant_a, tenant_b):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (CAST(:id AS uuid), :slug, 'Audit proof')"
                ),
                {"id": tenant, "slug": f"audit-{tenant}"},
            )
        caller = conn.begin_nested()
        conn.execute(text(f"SET LOCAL ROLE {role}"))
        assert conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            )
        ).one() == (False, False)
        for tenant, event in ((tenant_a, event_a), (tenant_b, event_b)):
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": tenant},
            )
            conn.execute(
                text(
                    "INSERT INTO public.audit_events "
                    "(id, tenant_id, action, entity_type) "
                    "VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), "
                    "'proof.appended', 'proof')"
                ),
                {"id": event, "tenant": tenant},
            )
        assert conn.execute(
            text(
                "SELECT id::text FROM public.audit_events "
                "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
            ),
            {"a": event_a, "b": event_b},
        ).scalars().all() == [event_b]
        with pytest.raises(DBAPIError, match="row-level security"):
            with conn.begin_nested():
                conn.execute(
                    text(
                        "INSERT INTO public.audit_events "
                        "(id, tenant_id, action, entity_type) "
                        "VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), 'x', 'x')"
                    ),
                    {"id": str(uuid.uuid4()), "tenant": tenant_a},
                )
        for verb in ("UPDATE", "DELETE"):
            statement = (
                "UPDATE public.audit_events SET action = 'rewritten'"
                if verb == "UPDATE"
                else "DELETE FROM public.audit_events"
            )
            with pytest.raises(DBAPIError, match="permission denied"):
                with conn.begin_nested():
                    conn.execute(text(statement))
        caller.rollback()
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM public.audit_events "
                    "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                ),
                {"a": event_a, "b": event_b},
            ).scalar_one()
            == 0
        )


def test_actual_migrated_provider_admits() -> None:
    with _catalog() as conn:
        _require(conn)


BREAKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "missing",
        (f"ALTER TABLE {TABLE} RENAME TO audit_events_missing",),
        "does not exist",
    ),
    (
        "pre-0029",
        (f"ALTER TABLE {TABLE} ALTER COLUMN entity_type TYPE varchar(120)",),
        "entity_type length",
    ),
    (
        "created-not-null",
        (f"ALTER TABLE {TABLE} ALTER COLUMN created_at SET NOT NULL",),
        "created_at nullable",
    ),
    (
        "column-missing",
        (f"ALTER TABLE {TABLE} DROP COLUMN request_id",),
        "columns differ",
    ),
    (
        "details-json",
        (f"ALTER TABLE {TABLE} ALTER COLUMN details TYPE json USING details::json",),
        "expected JSONB",
    ),
    (
        "details-default",
        (f"ALTER TABLE {TABLE} ALTER COLUMN details SET DEFAULT '[]'::jsonb",),
        "empty JSON object",
    ),
    (
        "created-default",
        (f"ALTER TABLE {TABLE} ALTER COLUMN created_at DROP DEFAULT",),
        "created_at has no server default",
    ),
    (
        "occurred-default",
        (
            f"ALTER TABLE {TABLE} ALTER COLUMN occurred_at "
            "SET DEFAULT clock_timestamp()",
        ),
        "current transaction timestamp",
    ),
    (
        "source-default",
        (
            f"ALTER TABLE {TABLE} ALTER COLUMN source_application "
            "SET DEFAULT 'invented'",
        ),
        "must have no server default",
    ),
    (
        "actor-fk",
        (
            f"ALTER TABLE {TABLE} ADD CONSTRAINT proof_actor_fk "
            "FOREIGN KEY (actor_party_id) "
            "REFERENCES public.parties(id)",
        ),
        "must have no actor foreign key",
    ),
    (
        "no-rls",
        (f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY",),
        "ENABLEd and FORCEd",
    ),
    (
        "no-force",
        (f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY",),
        "ENABLEd and FORCEd",
    ),
    (
        "or-true",
        (
            f"ALTER POLICY audit_events_tenant_isolation ON {TABLE} "
            "USING (tenant_id = public.app_current_tenant_id() OR true)",
        ),
        "tenant-equality policy",
    ),
    (
        "wrong-check",
        (f"ALTER POLICY audit_events_tenant_isolation ON {TABLE} WITH CHECK (true)",),
        "tenant-equality policy",
    ),
    (
        "extra-policy",
        (f"CREATE POLICY proof_bypass ON {TABLE} USING (true) WITH CHECK (true)",),
        "exactly one tenant-equality policy",
    ),
    (
        "wrong-roles",
        (f"ALTER POLICY audit_events_tenant_isolation ON {TABLE} TO app_user",),
        "tenant-equality policy",
    ),
    (
        "wrong-command",
        (
            f"DROP POLICY audit_events_tenant_isolation ON {TABLE}",
            f"CREATE POLICY audit_events_tenant_isolation ON {TABLE} FOR SELECT "
            "USING (tenant_id = public.app_current_tenant_id())",
        ),
        "tenant-equality policy",
    ),
)


@pytest.mark.parametrize(("case", "statements", "expected"), BREAKS)
def test_catalog_drift_refuses(
    case: str, statements: tuple[str, ...], expected: str
) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    with _catalog(statements) as conn:
        with pytest.raises(PrerequisiteNotSatisfiedError, match=expected):
            _require(conn)


@pytest.mark.parametrize("role", ("app_user", "platform_api"))
@pytest.mark.parametrize(
    "privilege",
    (
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
        "UPDATE (details)",
        "REFERENCES (id)",
    ),
)
@pytest.mark.parametrize("source", ("direct", "public", "inherited"))
def test_effective_mutation_grants_refuse(
    role: str, privilege: str, source: str
) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    recipient = role if source == "direct" else "PUBLIC"
    setup = []
    if source == "inherited":
        recipient = "tenant_audit_proof_mutator"
        setup = [
            f"CREATE ROLE {recipient} NOLOGIN INHERIT",
            f"GRANT {recipient} TO {role}",
        ]
    setup.append(f"GRANT {privilege} ON {TABLE} TO {recipient}")
    with _catalog(setup) as conn:
        with pytest.raises(PrerequisiteNotSatisfiedError, match="must not hold"):
            _require(conn)


@pytest.mark.parametrize("role", ("app_user", "platform_api"))
@pytest.mark.parametrize("privilege", ("SELECT", "INSERT"))
def test_missing_append_privileges_refuse(role: str, privilege: str) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    with _catalog((f"REVOKE {privilege} ON {TABLE} FROM {role}, PUBLIC",)) as conn:
        with pytest.raises(
            PrerequisiteNotSatisfiedError, match=f"needs table-level {privilege}"
        ):
            _require(conn)


def test_a_stamp_cannot_supply_missing_storage() -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    with _catalog((f"ALTER TABLE {TABLE} RENAME TO audit_events_stamped",)) as conn:
        conn.execute(text("DELETE FROM public.alembic_version"))
        conn.execute(
            text(
                "INSERT INTO public.alembic_version (version_num) "
                "VALUES ('0029_audit_history_shape')"
            )
        )
        with pytest.raises(PrerequisiteNotSatisfiedError, match="does not exist"):
            _require(conn)


def test_table_name_only_proof_misses_real_drift() -> None:
    """Sensitivity: same real catalogue admits before and refuses after mutation."""
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    with _catalog() as conn:
        _require(conn)
        conn.execute(text(f"GRANT UPDATE (details) ON {TABLE} TO PUBLIC"))
        assert conn.execute(
            text("SELECT to_regclass(:table) IS NOT NULL"), {"table": TABLE}
        ).scalar_one()
        with pytest.raises(PrerequisiteNotSatisfiedError, match="column-level UPDATE"):
            _require(conn)


@pytest.mark.parametrize("role", ("app_user", "platform_api"))
@pytest.mark.parametrize("attribute", ("SUPERUSER", "BYPASSRLS"))
def test_online_role_bypass_refuses(role: str, attribute: str) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    with _catalog((f"ALTER ROLE {role} {attribute}",)) as conn:
        with pytest.raises(
            PrerequisiteNotSatisfiedError, match="NOSUPERUSER NOBYPASSRLS"
        ):
            _require(conn)


@pytest.mark.parametrize("role", ("app_user", "platform_api"))
@pytest.mark.parametrize("surface", ("schema", "function"))
def test_unreachable_tenant_boundary_refuses(role: str, surface: str) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    statement = (
        f"REVOKE USAGE ON SCHEMA public FROM {role}, PUBLIC"
        if surface == "schema"
        else f"REVOKE EXECUTE ON FUNCTION public.app_current_tenant_id() "
        f"FROM {role}, PUBLIC"
    )
    with _catalog((statement,)) as conn:
        with pytest.raises(PrerequisiteNotSatisfiedError, match="schema USAGE"):
            _require(conn)


@pytest.mark.parametrize(
    "column",
    (
        "actor_type",
        "actor_id",
        "actor_label",
        "actor_party_id",
        "source_application",
        "entity_id",
        "request_id",
        "status_code",
        "is_success",
        "ip_address",
        "user_agent",
        "occurred_at",
    ),
)
def test_historical_nullable_columns_remain_nullable(column: str) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    with _catalog(
        (
            f"DELETE FROM {TABLE}",  # noqa: S608 -- fixed fixture table
            f"ALTER TABLE {TABLE} ALTER COLUMN {column} SET NOT NULL",
        )
    ) as conn:
        with pytest.raises(PrerequisiteNotSatisfiedError, match=f"{column} nullable"):
            _require(conn)


@pytest.mark.parametrize(
    "column",
    (
        "tenant_id",
        "actor_type",
        "actor_id",
        "actor_label",
        "actor_party_id",
        "source_application",
        "request_id",
        "occurred_at",
    ),
)
@pytest.mark.parametrize("replacement", ("missing", "unique", "partial"))
def test_required_indexes_refuse_drift(column: str, replacement: str) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    statements = [f"DROP INDEX public.ix_audit_events_{column}"]
    if replacement != "missing":
        unique = "UNIQUE " if replacement == "unique" else ""
        partial = " WHERE false" if replacement == "partial" else ""
        statements.append(
            f"CREATE {unique}INDEX ix_audit_events_{column} "
            f"ON {TABLE} ({column}){partial}"
        )
    with _catalog(statements) as conn:
        with pytest.raises(
            PrerequisiteNotSatisfiedError, match="non-unique, non-partial"
        ):
            _require(conn)


def test_wrong_provider_table_name_does_not_supply_the_shape() -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    with _catalog(
        (
            f"ALTER TABLE {TABLE} RENAME TO audit_events_real",
            f"CREATE TABLE {TABLE} (id uuid PRIMARY KEY)",
        )
    ) as conn:
        with pytest.raises(PrerequisiteNotSatisfiedError, match="columns differ"):
            _require(conn)


def test_tenant_foreign_key_must_cascade_to_public_tenants() -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    with _catalog() as conn:
        constraint = conn.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE conrelid = "
                "'public.audit_events'::regclass AND contype = 'f'"
            )
        ).scalar_one()
        # The name is read from this fixture's catalogue, and quoted as an identifier.
        conn.exec_driver_sql(
            'ALTER TABLE public.audit_events DROP CONSTRAINT "'
            + constraint.replace('"', '""')
            + '"'
        )
        with pytest.raises(
            PrerequisiteNotSatisfiedError, match="tenant_id foreign key"
        ):
            _require(conn)


@pytest.mark.parametrize("role", ("app_user", "platform_api"))
def test_public_writer_flush_is_rolled_back_with_the_caller(
    admin_session, tenant_a, monkeypatch, role: str
) -> None:
    from dotmac_kernel import (
        AuditActionRegistry,
        FeatureManifest,
        audit_actions,
        write_audit_event,
    )

    registry = AuditActionRegistry.from_manifests(
        [FeatureManifest(name="tenant_audit_proof", audit_actions=("proof.appended",))]
    )
    monkeypatch.setattr(audit_actions, "_active_registry", registry)
    tenant_id = tenant_a.id
    event_id = None
    with pytest.raises(RuntimeError, match="caller refused"):
        with admin_session.begin_nested():
            admin_session.execute(text(f"SET LOCAL ROLE {role}"))
            admin_session.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_id)},
            )
            event = write_audit_event(
                admin_session,
                tenant_id=tenant_id,
                actor_type="system",
                source_application="tenant_audit_proof",
                action="proof.appended",
                entity_type="proof",
            )
            event_id = event.id
            assert (
                admin_session.execute(
                    text("SELECT count(*) FROM public.audit_events WHERE id = :id"),
                    {"id": event_id},
                ).scalar_one()
                == 1
            )
            raise RuntimeError("caller refused")
    assert event_id is not None
    assert (
        admin_session.execute(
            text("SELECT count(*) FROM public.audit_events WHERE id = :id"),
            {"id": event_id},
        ).scalar_one()
        == 0
    )


def test_marker_spoof_can_route_to_other_tenant_but_is_refused_before_append() -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    tenant_a, tenant_b, event_b = (str(uuid.uuid4()) for _ in range(3))
    with _catalog() as conn:
        _require(conn)
        for tenant in (tenant_a, tenant_b):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (CAST(:id AS uuid), :slug, 'Helper proof')"
                ),
                {"id": tenant, "slug": f"helper-{tenant}"},
            )
        conn.execute(
            text(
                "INSERT INTO public.audit_events (id, tenant_id, action, entity_type) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), 'x', 'x')"
            ),
            {"id": event_b, "tenant": tenant_b},
        )
        # UUID is generated by this test; no caller text enters this function DDL.
        conn.exec_driver_sql(
            "CREATE OR REPLACE FUNCTION public.app_current_tenant_id() RETURNS uuid "
            "LANGUAGE plpgsql STABLE AS $$ BEGIN "
            "-- returns uuid stable current_setting('app.current_tenant', true)\n"
            "-- invalid_text_representation\n"
            f"RETURN '{tenant_b}'::uuid; "
            "IF false THEN RETURN NULLIF("
            "current_setting('app.current_tenant', true), '')::uuid; "
            "END IF; END; $$"
        )
        with conn.begin_nested():
            conn.execute(text("SET LOCAL ROLE app_user"))
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": tenant_a},
            )
            # The literal equality policy is intact, yet A sees B through the spoof.
            assert (
                conn.execute(
                    text(
                        "SELECT id::text FROM public.audit_events "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": event_b},
                ).scalar_one()
                == event_b
            )
        conn.execute(text("RESET ROLE"))
        # No online append occurs before this precise prerequisite refusal.
        with pytest.raises(PrerequisiteNotSatisfiedError, match="accepted Kernel body"):
            _require(conn)


@pytest.mark.parametrize(
    ("case", "statement"),
    (
        ("missing", "DROP FUNCTION public.app_current_tenant_id() CASCADE"),
        (
            "return-type",
            "DROP FUNCTION public.app_current_tenant_id() CASCADE; "
            "CREATE FUNCTION public.app_current_tenant_id() "
            "RETURNS text LANGUAGE sql STABLE "
            "AS $$ SELECT current_setting('app.current_tenant', true) $$",
        ),
        (
            "language",
            "CREATE OR REPLACE FUNCTION public.app_current_tenant_id() "
            "RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$",
        ),
        (
            "set-returning",
            "DROP FUNCTION public.app_current_tenant_id() CASCADE; "
            "CREATE FUNCTION public.app_current_tenant_id() RETURNS SETOF uuid "
            "LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$",
        ),
        (
            "search-path",
            "ALTER FUNCTION public.app_current_tenant_id() "
            "SET search_path = public, pg_catalog",
        ),
        ("volatility", "ALTER FUNCTION public.app_current_tenant_id() VOLATILE"),
        ("definer", "ALTER FUNCTION public.app_current_tenant_id() SECURITY DEFINER"),
        (
            "config",
            "ALTER FUNCTION public.app_current_tenant_id() SET app.current_tenant "
            "TO '00000000-0000-0000-0000-000000000001'",
        ),
    ),
)
def test_safe_helper_catalog_contract_refuses(case: str, statement: str) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    # Execute statements separately for drivers that refuse multiple statements.
    with _catalog(statement.split("; ")) as conn:
        if case == "config":
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(uuid.uuid4())},
            )
            # Canonical body is unchanged, but its function-local GUC overrides
            # the actual caller tenant. The catalogue check must refuse it.
            assert conn.execute(
                text("SELECT public.app_current_tenant_id()")
            ).scalar_one() == uuid.UUID("00000000-0000-0000-0000-000000000001")
        with pytest.raises(PrerequisiteNotSatisfiedError, match="accepted Kernel body"):
            _require(conn)


def test_accepted_helper_observes_two_valid_tenants_and_fails_closed() -> None:
    with _catalog() as conn:
        _require(conn)
        conn.execute(text("RESET app.current_tenant"))
        assert (
            conn.execute(text("SELECT public.app_current_tenant_id()")).scalar_one()
            is None
        )
        for setting in ("", "invalid-uuid", str(uuid.uuid4()), str(uuid.uuid4())):
            conn.execute(
                text("SELECT set_config('app.current_tenant', :setting, true)"),
                {"setting": setting},
            )
            result = conn.execute(
                text("SELECT public.app_current_tenant_id()")
            ).scalar_one()
            assert result == (uuid.UUID(setting) if len(setting) == 36 else None)


def test_unqualified_policy_deparse_cannot_substitute_an_alternate_helper() -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    tenant_a, tenant_b, event_b = (str(uuid.uuid4()) for _ in range(3))
    with _catalog() as conn:
        _require(conn)
        for tenant in (tenant_a, tenant_b):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (CAST(:id AS uuid), :slug, 'Policy helper proof')"
                ),
                {"id": tenant, "slug": f"policy-helper-{tenant}"},
            )
        conn.execute(
            text(
                "INSERT INTO public.audit_events "
                "(id, tenant_id, action, entity_type) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), 'x', 'x')"
            ),
            {"id": event_b, "tenant": tenant_b},
        )
        conn.execute(text("CREATE SCHEMA tenant_audit_policy_proof"))
        conn.execute(
            text("GRANT USAGE ON SCHEMA tenant_audit_policy_proof TO app_user")
        )
        # Test-generated UUID only; actual public helper stays byte-identical.
        conn.exec_driver_sql(
            "CREATE FUNCTION tenant_audit_policy_proof.app_current_tenant_id() "
            "RETURNS uuid LANGUAGE sql STABLE AS $$ "
            f"SELECT '{tenant_b}'::uuid $$"
        )
        conn.execute(
            text(
                "ALTER POLICY audit_events_tenant_isolation ON public.audit_events "
                "USING (tenant_id = tenant_audit_policy_proof.app_current_tenant_id()) "
                "WITH CHECK (tenant_id = "
                "tenant_audit_policy_proof.app_current_tenant_id())"
            )
        )
        conn.execute(text("SET LOCAL search_path = tenant_audit_policy_proof, public"))
        using, check = conn.execute(
            text(
                "SELECT qual, with_check FROM pg_policies WHERE schemaname = 'public' "
                "AND tablename = 'audit_events'"
            )
        ).one()
        assert using == check == "(tenant_id = app_current_tenant_id())"
        conn.execute(text("SET LOCAL ROLE app_user"))
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": tenant_a},
        )
        assert (
            conn.execute(
                text(
                    "SELECT id::text FROM public.audit_events "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": event_b},
            ).scalar_one()
            == event_b
        )
        conn.execute(text("RESET ROLE"))
        with pytest.raises(
            PrerequisiteNotSatisfiedError,
            match="policy must depend on the validated public tenant helper",
        ):
            _require(conn)


def test_unqualified_policy_deparse_cannot_substitute_a_custom_uuid_operator() -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    tenant_a, tenant_b, event_b = (str(uuid.uuid4()) for _ in range(3))
    with _catalog() as conn:
        _require(conn)
        for tenant in (tenant_a, tenant_b):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (CAST(:id AS uuid), :slug, 'Policy operator proof')"
                ),
                {"id": tenant, "slug": f"policy-operator-{tenant}"},
            )
        conn.execute(
            text(
                "INSERT INTO public.audit_events "
                "(id, tenant_id, action, entity_type) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), 'x', 'x')"
            ),
            {"id": event_b, "tenant": tenant_b},
        )
        conn.execute(text("CREATE SCHEMA tenant_audit_operator_proof"))
        conn.execute(
            text("GRANT USAGE ON SCHEMA tenant_audit_operator_proof TO app_user")
        )
        conn.execute(
            text(
                "CREATE FUNCTION tenant_audit_operator_proof.uuid_equals("
                "pg_catalog.uuid, pg_catalog.uuid) "
                "RETURNS boolean LANGUAGE sql IMMUTABLE "
                "AS $$ SELECT true $$"
            )
        )
        conn.execute(
            text(
                "CREATE OPERATOR tenant_audit_operator_proof.= ("
                "LEFTARG = pg_catalog.uuid, RIGHTARG = pg_catalog.uuid, "
                "FUNCTION = tenant_audit_operator_proof.uuid_equals)"
            )
        )
        conn.execute(
            text(
                "ALTER POLICY audit_events_tenant_isolation ON public.audit_events "
                "USING (tenant_id OPERATOR(tenant_audit_operator_proof.=) "
                "public.app_current_tenant_id()) "
                "WITH CHECK (tenant_id OPERATOR(tenant_audit_operator_proof.=) "
                "public.app_current_tenant_id())"
            )
        )
        conn.execute(
            text(
                "SET LOCAL search_path = "
                "tenant_audit_operator_proof, pg_catalog, public"
            )
        )
        using, check = conn.execute(
            text(
                "SELECT qual, with_check FROM pg_catalog.pg_policies "
                "WHERE schemaname = 'public' AND tablename = 'audit_events'"
            )
        ).one()
        assert using == check == "(tenant_id = app_current_tenant_id())"
        conn.execute(text("SET LOCAL ROLE app_user"))
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": tenant_a},
        )
        assert (
            conn.execute(
                text(
                    "SELECT id::text FROM public.audit_events "
                    "WHERE id OPERATOR(pg_catalog.=) CAST(:id AS pg_catalog.uuid)"
                ),
                {"id": event_b},
            ).scalar_one()
            == event_b
        )
        conn.execute(text("RESET ROLE"))
        with pytest.raises(
            PrerequisiteNotSatisfiedError,
            match="policy must use the pg_catalog UUID equality operator",
        ):
            _require(conn)
