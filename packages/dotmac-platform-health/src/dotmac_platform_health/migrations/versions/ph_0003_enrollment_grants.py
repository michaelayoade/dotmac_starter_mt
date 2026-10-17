"""Permanent enrollment binding and nullable, never inferred observation lineage."""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ph_0003_enrollment_grants"
down_revision = "ph_0002_freshness_snapshot"
branch_labels = None
REQUIRES = ("module_database_roles.v1",)
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_health"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.create_table(
        "health_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enrollment_ref", sa.String(200), nullable=False),
        sa.Column("installation_ref", sa.String(200), nullable=False),
        sa.Column("projection_fingerprint", sa.String(64), nullable=False),
        sa.Column("projection", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("enrollment_ref", name="uq_health_grants_enrollment"),
        sa.UniqueConstraint("installation_ref", name="uq_health_grants_installation"),
        schema=_SCHEMA,
    )
    op.create_table(
        "health_grant_components",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["grant_id"], ["mod_health.health_grants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["component_id"], ["mod_health.health_components.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "component_id", name="uq_health_grant_components_permanent"
        ),
        sa.UniqueConstraint(
            "grant_id", "component_id", name="uq_health_grant_components_pair"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "health_grant_closures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["grant_id"], ["mod_health.health_grants.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("grant_id", name="uq_health_grant_closures_grant"),
        schema=_SCHEMA,
    )
    # No default or UPDATE: historical observations remain unbound forever.
    op.add_column(
        "health_observations",
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_health_observations_grant",
        "health_observations",
        "health_grants",
        ["grant_id"],
        ["id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_health_observations_membership",
        "health_observations",
        "health_grant_components",
        ["grant_id", "component_id"],
        ["grant_id", "component_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    for table in ("health_grants", "health_grant_components", "health_grant_closures"):
        op.execute(f"GRANT SELECT, INSERT ON mod_health.{table} TO platform_api;")
        op.execute(f"GRANT SELECT, INSERT ON mod_health.{table} TO app_admin;")
        op.execute(
            f"REVOKE UPDATE, DELETE ON mod_health.{table} FROM platform_api, app_admin;"
        )
        op.execute(f"REVOKE ALL ON mod_health.{table} FROM app_user;")
    op.execute("""
        CREATE FUNCTION mod_health.reject_evidence_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'health evidence is immutable and retained';
        END $$;
    """)
    for table in (
        "health_grants",
        "health_grant_components",
        "health_grant_closures",
        "health_observations",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON mod_health.{table} FOR EACH ROW EXECUTE FUNCTION mod_health.reject_evidence_mutation();"
        )
        # TRUNCATE does not fire row-level DELETE triggers.
        op.execute(
            f"CREATE TRIGGER retained_evidence BEFORE TRUNCATE ON mod_health.{table} FOR EACH STATEMENT EXECUTE FUNCTION mod_health.reject_evidence_mutation();"
        )
    op.execute("""
        CREATE FUNCTION mod_health.check_observation_binding() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE reserved uuid; bound_source text;
        BEGIN
          PERFORM id FROM mod_health.health_components WHERE id = NEW.component_id FOR UPDATE;
          SELECT grant_id INTO reserved FROM mod_health.health_grant_components WHERE component_id = NEW.component_id;
          IF NEW.grant_id IS NULL AND reserved IS NOT NULL THEN
            RAISE EXCEPTION 'component is reserved to authenticated enrollment';
          END IF;
          IF NEW.grant_id IS NOT NULL THEN
            SELECT projection->>'source_ref' INTO bound_source FROM mod_health.health_grants WHERE id = NEW.grant_id;
            IF reserved IS DISTINCT FROM NEW.grant_id OR bound_source IS DISTINCT FROM NEW.source_ref THEN
              RAISE EXCEPTION 'observation grant binding mismatch';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER observation_binding BEFORE INSERT ON mod_health.health_observations
        FOR EACH ROW EXECUTE FUNCTION mod_health.check_observation_binding();
    """)
    op.execute("""
        CREATE FUNCTION mod_health.lock_component_reservation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          PERFORM id FROM mod_health.health_components WHERE id = NEW.component_id FOR UPDATE;
          RETURN NEW;
        END $$;
        CREATE TRIGGER component_reservation BEFORE INSERT ON mod_health.health_grant_components
        FOR EACH ROW EXECUTE FUNCTION mod_health.lock_component_reservation();
    """)
    op.execute("""
        CREATE FUNCTION mod_health.protect_reserved_component_code() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF OLD.code IS DISTINCT FROM NEW.code AND EXISTS (
            SELECT 1 FROM mod_health.health_grant_components WHERE component_id = OLD.id
          ) THEN RAISE EXCEPTION 'reserved component identity is permanent'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER reserved_component_code BEFORE UPDATE ON mod_health.health_components
        FOR EACH ROW EXECUTE FUNCTION mod_health.protect_reserved_component_code();
    """)


def downgrade() -> None:
    # Hold the entire affected owner set through the guard and transactional
    # DDL. Otherwise an uncommitted writer can be invisible to the guard, then
    # commit while a later DDL statement waits for its lock, erasing provenance.
    # Component-first ordering matches the admission writers' lock authority.
    op.execute("""
        LOCK TABLE mod_health.health_components,
          mod_health.health_grants, mod_health.health_grant_components,
          mod_health.health_grant_closures, mod_health.health_observations,
          mod_health.health_projections IN ACCESS EXCLUSIVE MODE;
    """)
    # Retained provenance cannot be silently erased, including populated legacy
    # observations whose NULL provenance is itself meaningful evidence.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM mod_health.health_grants)
             OR EXISTS (SELECT 1 FROM mod_health.health_observations) THEN
            RAISE EXCEPTION 'populated health evidence downgrade refused';
          END IF;
        END $$;
    """)
    op.execute("DROP TRIGGER observation_binding ON mod_health.health_observations;")
    op.execute("DROP TRIGGER immutable_evidence ON mod_health.health_observations;")
    op.execute("DROP TRIGGER retained_evidence ON mod_health.health_observations;")
    op.execute("DROP TRIGGER reserved_component_code ON mod_health.health_components;")
    op.drop_constraint(
        "fk_health_observations_membership",
        "health_observations",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_health_observations_grant",
        "health_observations",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("health_observations", "grant_id", schema=_SCHEMA)
    for table in ("health_grant_closures", "health_grant_components", "health_grants"):
        op.drop_table(table, schema=_SCHEMA)
    for function in (
        "protect_reserved_component_code",
        "lock_component_reservation",
        "check_observation_binding",
        "reject_evidence_mutation",
    ):
        op.execute(f"DROP FUNCTION mod_health.{function}();")
