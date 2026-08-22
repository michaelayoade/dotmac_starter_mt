"""Create Fiber Plant.

Revision ID: fp_0001_fiber_plant
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "fp_0001_fiber_plant"
down_revision = None
branch_labels = ("fiber_plant",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_fiber"
_TABLES = (
    "structures",
    "cables",
    "strands",
    "splices",
    "terminations",
    "field_observations",
    "changes",
    "fiber_events",
)


def _tenant(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], name=name, ondelete="CASCADE"
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_fiber;")
    op.execute("GRANT USAGE ON SCHEMA mod_fiber TO app_user, platform_api;")
    op.create_table(
        "structures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("location_ref", sa.String(200), nullable=False),
        sa.Column("asset_ref", sa.String(200)),
        sa.Column("source_ref", sa.String(240)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_fiber_structures_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_fiber_structures_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_fiber_structures_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "cables",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("strand_count", sa.Integer(), nullable=False),
        sa.Column("start_structure_id", sa.Uuid(), nullable=False),
        sa.Column("end_structure_id", sa.Uuid(), nullable=False),
        sa.Column("route_ref", sa.String(200)),
        sa.Column("asset_ref", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_fiber_cables_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "start_structure_id"],
            ["mod_fiber.structures.tenant_id", "mod_fiber.structures.id"],
            name="fk_fiber_cables_start",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "end_structure_id"],
            ["mod_fiber.structures.tenant_id", "mod_fiber.structures.id"],
            name="fk_fiber_cables_end",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_fiber_cables_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fiber_cables_tenant_code"),
        sa.CheckConstraint(
            "strand_count > 0 AND start_structure_id <> end_structure_id",
            name="ck_fiber_cable_shape",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "strands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("cable_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("colour_code", sa.String(40), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_fiber_strands_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cable_id"],
            ["mod_fiber.cables.tenant_id", "mod_fiber.cables.id"],
            name="fk_fiber_strands_cable",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_fiber_strands_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "cable_id", "ordinal", name="uq_fiber_strand_ordinal"
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_fiber_strand_ordinal"),
        schema=_SCHEMA,
    )
    op.create_table(
        "splices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("structure_id", sa.Uuid(), nullable=False),
        sa.Column("left_strand_id", sa.Uuid(), nullable=False),
        sa.Column("right_strand_id", sa.Uuid(), nullable=False),
        sa.Column("loss_db", sa.Numeric(10, 4)),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_fiber_splices_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "structure_id"],
            ["mod_fiber.structures.tenant_id", "mod_fiber.structures.id"],
            name="fk_fiber_splices_structure",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "left_strand_id"],
            ["mod_fiber.strands.tenant_id", "mod_fiber.strands.id"],
            name="fk_fiber_splices_left",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "right_strand_id"],
            ["mod_fiber.strands.tenant_id", "mod_fiber.strands.id"],
            name="fk_fiber_splices_right",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_fiber_splices_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "left_strand_id",
            "right_strand_id",
            name="uq_fiber_splice_pair",
        ),
        sa.CheckConstraint(
            "left_strand_id <> right_strand_id", name="ck_fiber_splice_distinct"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "terminations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("structure_id", sa.Uuid(), nullable=False),
        sa.Column("strand_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_ref", sa.String(200), nullable=False),
        sa.Column("port_ref", sa.String(200)),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_fiber_terminations_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "structure_id"],
            ["mod_fiber.structures.tenant_id", "mod_fiber.structures.id"],
            name="fk_fiber_terminations_structure",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "strand_id"],
            ["mod_fiber.strands.tenant_id", "mod_fiber.strands.id"],
            name="fk_fiber_terminations_strand",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fiber_terminations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "strand_id",
            "endpoint_ref",
            name="uq_fiber_termination_endpoint",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "field_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("observation_kind", sa.String(80), nullable=False),
        sa.Column("result_code", sa.String(120), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_ref", sa.String(200)),
        _tenant("fk_fiber_observations_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fiber_observations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "evidence_ref", name="uq_fiber_observation_evidence"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fiber_observations_subject",
        "field_observations",
        ["tenant_id", "subject_ref", "observed_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("summary", sa.String(240), nullable=False),
        sa.Column("subject_refs", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("desired_fingerprint", sa.String(128), nullable=False),
        sa.Column("as_built_fingerprint", sa.String(128)),
        sa.Column("requested_by_ref", sa.String(200), nullable=False),
        sa.Column("approval_ref", sa.String(240)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        _tenant("fk_fiber_changes_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_fiber_changes_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fiber_changes_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "fiber_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_ref", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_fiber_events_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_fiber_events_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fiber_events_aggregate",
        "fiber_events",
        ["tenant_id", "aggregate_ref", "occurred_at"],
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE FUNCTION mod_fiber.refuse_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Fiber evidence is append-only' USING ERRCODE = '55000'; END; $$;"
    )
    for table in ("splices", "terminations", "field_observations", "fiber_events"):
        op.execute(
            f"CREATE TRIGGER fiber_{table}_append_only BEFORE UPDATE OR DELETE ON mod_fiber.{table} FOR EACH ROW EXECUTE FUNCTION mod_fiber.refuse_evidence_mutation();"
        )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_fiber.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_fiber.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY fiber_{table}_tenant_isolation ON mod_fiber.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table
            in {"splices", "terminations", "field_observations", "fiber_events"}
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"GRANT {privileges} ON mod_fiber.{table} TO app_user;")
        op.execute(f"GRANT {privileges} ON mod_fiber.{table} TO platform_api;")


def downgrade() -> None:
    op.drop_index(
        "ix_fiber_events_aggregate", table_name="fiber_events", schema=_SCHEMA
    )
    op.drop_table("fiber_events", schema=_SCHEMA)
    op.drop_table("changes", schema=_SCHEMA)
    op.drop_index(
        "ix_fiber_observations_subject", table_name="field_observations", schema=_SCHEMA
    )
    op.drop_table("field_observations", schema=_SCHEMA)
    op.drop_table("terminations", schema=_SCHEMA)
    op.drop_table("splices", schema=_SCHEMA)
    op.drop_table("strands", schema=_SCHEMA)
    op.drop_table("cables", schema=_SCHEMA)
    op.drop_table("structures", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_fiber.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_fiber RESTRICT;")
