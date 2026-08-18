"""Create selectable tenant and platform durable timer planes.

The timer lineage owns identity and generation state only. Each schedule writes
the already-existing kernel outbox, whose relay owns due selection, leasing,
retry and dead-letter behavior.

Revision ID: dt_0001_durable_timers
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "dt_0001_durable_timers"
down_revision = None
branch_labels = ("durable_timers",)

MODULE_CODE = "durable_timers"
COMMON_REQUIRES = ("module_database_roles.v1", "outbox_relay.v1")
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
PLATFORM_REQUIRES = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    module=MODULE_CODE,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
)

_SCHEMA = "mod_timers"
_TENANT_TABLES = ("timers", "timer_acceptances")
_PLATFORM_TABLES = ("platform_timers", "platform_timer_acceptances")

_TENANT_TRANSITION_SIG = (
    "mod_timers.transition_timer(uuid,uuid,text,text,timestamp with time zone)"
)
_PLATFORM_TRANSITION_SIG = (
    "mod_timers.transition_platform_timer(uuid,text,text,timestamp with time zone)"
)
_TENANT_PURGE_SIG = (
    "mod_timers.purge_timer_history(uuid,timestamp with time zone,integer)"
)
_PLATFORM_PURGE_SIG = (
    "mod_timers.purge_platform_timer_history(timestamp with time zone,integer)"
)


def _timer_columns(*, tenant: bool) -> list[sa.Column[Any]]:
    columns: list[sa.Column[Any]] = [
        sa.Column("id", sa.Uuid(), primary_key=True),
    ]
    if tenant:
        columns.append(sa.Column("tenant_id", sa.Uuid(), nullable=False))
    columns.extend(
        [
            sa.Column("owner", sa.String(120), nullable=False),
            sa.Column("entity_kind", sa.String(120), nullable=False),
            sa.Column("entity_id", sa.String(255), nullable=False),
            sa.Column("purpose", sa.String(120), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("output_event_type", sa.String(120), nullable=False),
            sa.Column("outbox_event_id", sa.Uuid(), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("superseded_at", sa.DateTime(timezone=True)),
            sa.Column("canceled_at", sa.DateTime(timezone=True)),
            sa.Column("fired_at", sa.DateTime(timezone=True)),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
        ]
    )
    return columns


def _timer_checks(prefix: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint("generation > 0", name=f"ck_{prefix}_generation"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'superseded', 'canceled', 'fired')",
            name=f"ck_{prefix}_status",
        ),
        sa.CheckConstraint(
            "(status = 'scheduled' AND superseded_at IS NULL "
            "AND canceled_at IS NULL AND fired_at IS NULL) OR "
            "(status = 'superseded' AND superseded_at IS NOT NULL "
            "AND canceled_at IS NULL AND fired_at IS NULL) OR "
            "(status = 'canceled' AND canceled_at IS NOT NULL "
            "AND superseded_at IS NULL AND fired_at IS NULL) OR "
            "(status = 'fired' AND fired_at IS NOT NULL "
            "AND superseded_at IS NULL AND canceled_at IS NULL)",
            name=f"ck_{prefix}_terminal_instant",
        ),
    ]


def _create_tenant_plane() -> None:
    op.create_table(
        "timers",
        *_timer_columns(tenant=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_timers_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_timers_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner",
            "entity_kind",
            "entity_id",
            "purpose",
            "generation",
            name="uq_timers_identity_generation",
        ),
        *_timer_checks("timers"),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_timers_current_identity",
        "timers",
        ["tenant_id", "owner", "entity_kind", "entity_id", "purpose"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("status = 'scheduled'"),
    )
    op.create_index(
        "ix_timers_terminal_expiry",
        "timers",
        ["tenant_id", "expires_at"],
        schema=_SCHEMA,
        postgresql_where=sa.text("status <> 'scheduled' AND expires_at IS NOT NULL"),
    )
    op.create_table(
        "timer_acceptances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("timer_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_timer_acceptances_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "timer_id"],
            ["mod_timers.timers.tenant_id", "mod_timers.timers.id"],
            ondelete="CASCADE",
            name="fk_timer_acceptances_timer",
        ),
        sa.UniqueConstraint(
            "tenant_id", "timer_id", name="uq_timer_acceptances_tenant_timer"
        ),
        schema=_SCHEMA,
    )
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_timers.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_timers.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON mod_timers.{table}
                USING (tenant_id = public.app_current_tenant_id())
                WITH CHECK (tenant_id = public.app_current_tenant_id());
            """
        )
        op.execute(f"REVOKE ALL ON mod_timers.{table} FROM PUBLIC;")
        op.execute(f"REVOKE ALL ON mod_timers.{table} FROM platform_api;")
        op.execute(f"GRANT SELECT, INSERT ON mod_timers.{table} TO app_user;")


def _create_platform_plane() -> None:
    op.create_table(
        "platform_timers",
        *_timer_columns(tenant=False),
        sa.UniqueConstraint(
            "owner",
            "entity_kind",
            "entity_id",
            "purpose",
            "generation",
            name="uq_platform_timers_identity_generation",
        ),
        *_timer_checks("platform_timers"),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_platform_timers_current_identity",
        "platform_timers",
        ["owner", "entity_kind", "entity_id", "purpose"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("status = 'scheduled'"),
    )
    op.create_index(
        "ix_platform_timers_terminal_expiry",
        "platform_timers",
        ["expires_at"],
        schema=_SCHEMA,
        postgresql_where=sa.text("status <> 'scheduled' AND expires_at IS NOT NULL"),
    )
    op.create_table(
        "platform_timer_acceptances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("timer_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["timer_id"],
            ["mod_timers.platform_timers.id"],
            ondelete="CASCADE",
            name="fk_platform_timer_acceptances_timer",
        ),
        sa.UniqueConstraint("timer_id", name="uq_platform_timer_acceptances_timer"),
        schema=_SCHEMA,
    )
    for table in _PLATFORM_TABLES:
        op.execute(f"REVOKE ALL ON mod_timers.{table} FROM PUBLIC;")
        op.execute(f"REVOKE ALL ON mod_timers.{table} FROM app_user;")
        op.execute(f"GRANT SELECT, INSERT ON mod_timers.{table} TO platform_api;")


def _create_transition_functions(planes: frozenset[ModulePlane]) -> None:
    if ModulePlane.TENANT in planes:
        op.execute(
            """
            CREATE FUNCTION mod_timers.transition_timer(
                p_tenant_id uuid,
                p_timer_id uuid,
                p_expected text,
                p_target text,
                p_changed_at timestamptz
            ) RETURNS boolean
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = ''
            AS $fn$
            DECLARE n integer;
            BEGIN
                IF p_tenant_id IS DISTINCT FROM public.app_current_tenant_id() THEN
                    RAISE EXCEPTION 'tenant context mismatch'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                IF p_expected <> 'scheduled'
                   OR p_target NOT IN ('superseded', 'canceled', 'fired') THEN
                    RAISE EXCEPTION 'invalid timer transition'
                        USING ERRCODE = 'check_violation';
                END IF;
                UPDATE mod_timers.timers
                   SET status = p_target,
                       superseded_at = CASE WHEN p_target = 'superseded'
                                            THEN p_changed_at ELSE NULL END,
                       canceled_at = CASE WHEN p_target = 'canceled'
                                         THEN p_changed_at ELSE NULL END,
                       fired_at = CASE WHEN p_target = 'fired'
                                      THEN p_changed_at ELSE NULL END
                 WHERE tenant_id = p_tenant_id
                   AND id = p_timer_id
                   AND status = p_expected;
                GET DIAGNOSTICS n = ROW_COUNT;
                RETURN n = 1;
            END
            $fn$;
            """
        )
        op.execute(f"ALTER FUNCTION {_TENANT_TRANSITION_SIG} OWNER TO app_admin;")
        op.execute(f"REVOKE ALL ON FUNCTION {_TENANT_TRANSITION_SIG} FROM PUBLIC;")
        op.execute(f"GRANT EXECUTE ON FUNCTION {_TENANT_TRANSITION_SIG} TO app_user;")

    if ModulePlane.PLATFORM in planes:
        op.execute(
            """
            CREATE FUNCTION mod_timers.transition_platform_timer(
                p_timer_id uuid,
                p_expected text,
                p_target text,
                p_changed_at timestamptz
            ) RETURNS boolean
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = ''
            AS $fn$
            DECLARE n integer;
            BEGIN
                IF p_expected <> 'scheduled'
                   OR p_target NOT IN ('superseded', 'canceled', 'fired') THEN
                    RAISE EXCEPTION 'invalid timer transition'
                        USING ERRCODE = 'check_violation';
                END IF;
                UPDATE mod_timers.platform_timers
                   SET status = p_target,
                       superseded_at = CASE WHEN p_target = 'superseded'
                                            THEN p_changed_at ELSE NULL END,
                       canceled_at = CASE WHEN p_target = 'canceled'
                                         THEN p_changed_at ELSE NULL END,
                       fired_at = CASE WHEN p_target = 'fired'
                                      THEN p_changed_at ELSE NULL END
                 WHERE id = p_timer_id AND status = p_expected;
                GET DIAGNOSTICS n = ROW_COUNT;
                RETURN n = 1;
            END
            $fn$;
            """
        )
        op.execute(f"ALTER FUNCTION {_PLATFORM_TRANSITION_SIG} OWNER TO app_admin;")
        op.execute(f"REVOKE ALL ON FUNCTION {_PLATFORM_TRANSITION_SIG} FROM PUBLIC;")
        op.execute(
            f"GRANT EXECUTE ON FUNCTION {_PLATFORM_TRANSITION_SIG} TO platform_api;"
        )


def _create_purge_functions(planes: frozenset[ModulePlane]) -> None:
    if ModulePlane.TENANT in planes:
        op.execute(
            """
            CREATE FUNCTION mod_timers.purge_timer_history(
                p_tenant_id uuid,
                p_before timestamptz,
                p_limit integer
            ) RETURNS integer
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = ''
            AS $fn$
            DECLARE n integer;
            BEGIN
                IF p_tenant_id IS DISTINCT FROM public.app_current_tenant_id() THEN
                    RAISE EXCEPTION 'tenant context mismatch'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                WITH doomed AS (
                    SELECT id
                      FROM mod_timers.timers
                     WHERE tenant_id = p_tenant_id
                       AND status IN ('superseded', 'canceled', 'fired')
                       AND expires_at IS NOT NULL
                       AND expires_at < p_before
                     ORDER BY expires_at, id
                     LIMIT p_limit
                     FOR UPDATE
                ), deleted AS (
                    DELETE FROM mod_timers.timers t
                     USING doomed d
                     WHERE t.tenant_id = p_tenant_id AND t.id = d.id
                    RETURNING 1
                )
                SELECT count(*) INTO n FROM deleted;
                RETURN n;
            END
            $fn$;
            """
        )
        op.execute(f"ALTER FUNCTION {_TENANT_PURGE_SIG} OWNER TO app_admin;")
        op.execute(f"REVOKE ALL ON FUNCTION {_TENANT_PURGE_SIG} FROM PUBLIC;")
        op.execute(f"GRANT EXECUTE ON FUNCTION {_TENANT_PURGE_SIG} TO app_user;")

    if ModulePlane.PLATFORM in planes:
        op.execute(
            """
            CREATE FUNCTION mod_timers.purge_platform_timer_history(
                p_before timestamptz,
                p_limit integer
            ) RETURNS integer
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = ''
            AS $fn$
            DECLARE n integer;
            BEGIN
                WITH doomed AS (
                    SELECT id
                      FROM mod_timers.platform_timers
                     WHERE status IN ('superseded', 'canceled', 'fired')
                       AND expires_at IS NOT NULL
                       AND expires_at < p_before
                     ORDER BY expires_at, id
                     LIMIT p_limit
                     FOR UPDATE
                ), deleted AS (
                    DELETE FROM mod_timers.platform_timers t
                     USING doomed d
                     WHERE t.id = d.id
                    RETURNING 1
                )
                SELECT count(*) INTO n FROM deleted;
                RETURN n;
            END
            $fn$;
            """
        )
        op.execute(f"ALTER FUNCTION {_PLATFORM_PURGE_SIG} OWNER TO app_admin;")
        op.execute(f"REVOKE ALL ON FUNCTION {_PLATFORM_PURGE_SIG} FROM PUBLIC;")
        op.execute(f"GRANT EXECUTE ON FUNCTION {_PLATFORM_PURGE_SIG} TO platform_api;")


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    require_prerequisites(op.get_bind(), COMMON_REQUIRES)
    if ModulePlane.TENANT in planes:
        require_prerequisites(op.get_bind(), TENANT_REQUIRES)
    if ModulePlane.PLATFORM in planes:
        require_prerequisites(op.get_bind(), PLATFORM_REQUIRES)

    op.execute("CREATE SCHEMA mod_timers AUTHORIZATION app_admin;")
    op.execute("REVOKE ALL ON SCHEMA mod_timers FROM PUBLIC;")
    op.execute("REVOKE ALL ON SCHEMA mod_timers FROM app_user;")
    op.execute("REVOKE ALL ON SCHEMA mod_timers FROM platform_api;")
    op.execute("GRANT USAGE ON SCHEMA mod_timers TO app_admin;")
    if ModulePlane.TENANT in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_timers TO app_user;")
        _create_tenant_plane()
    if ModulePlane.PLATFORM in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_timers TO platform_api;")
        _create_platform_plane()
    _create_transition_functions(planes)
    _create_purge_functions(planes)


def downgrade() -> None:
    for signature in (
        _TENANT_PURGE_SIG,
        _PLATFORM_PURGE_SIG,
        _TENANT_TRANSITION_SIG,
        _PLATFORM_TRANSITION_SIG,
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {signature};")
    for table in (*_PLATFORM_TABLES, *_TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS mod_timers.{table} CASCADE;")
    op.execute("DROP SCHEMA IF EXISTS mod_timers;")
