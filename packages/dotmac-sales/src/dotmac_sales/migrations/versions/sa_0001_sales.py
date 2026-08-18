"""Create tenant sales state through immutable accepted Quote handoff.

Revision ID: sa_0001_sales
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "sa_0001_sales"
down_revision = None
branch_labels = ("sales",)
REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
    "outbox_relay.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_sales"
_TABLES = (
    "pipelines",
    "pipeline_stages",
    "leads",
    "lead_origins",
    "quotes",
    "quote_lines",
    "quote_discount_revisions",
)


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["public.tenants.id"],
        ondelete="CASCADE",
        name=f"fk_sales_{table}_tenant",
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_sales;")
    op.execute("GRANT USAGE ON SCHEMA mod_sales TO app_user, app_admin;")
    op.execute("REVOKE ALL ON SCHEMA mod_sales FROM platform_api;")

    op.create_table(
        "pipelines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("pipelines"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sales_pipelines_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_sales_pipelines_tenant_name"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_pipelines_tenant", "pipelines", ["tenant_id"], schema=_SCHEMA
    )

    op.create_table(
        "pipeline_stages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("default_probability", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("pipeline_stages"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_sales_pipeline_stages_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "pipeline_id",
            "id",
            name="uq_sales_pipeline_stages_tenant_pipeline_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "pipeline_id",
            "name",
            name="uq_sales_pipeline_stages_tenant_pipeline_name",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "pipeline_id",
            "order_index",
            name="uq_sales_pipeline_stages_tenant_pipeline_order",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pipeline_id"],
            ["mod_sales.pipelines.tenant_id", "mod_sales.pipelines.id"],
            ondelete="CASCADE",
            name="fk_sales_pipeline_stages_pipeline",
        ),
        sa.CheckConstraint(
            "default_probability BETWEEN 0 AND 100",
            name="ck_sales_pipeline_stages_probability",
        ),
        sa.CheckConstraint("order_index >= 0", name="ck_sales_pipeline_stages_order"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_pipeline_stages_tenant_pipeline",
        "pipeline_stages",
        ["tenant_id", "pipeline_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_kind", sa.String(60), nullable=False),
        sa.Column("subject_opaque_id", sa.String(200), nullable=False),
        sa.Column("subject_version", sa.String(120), nullable=True),
        sa.Column("subject_label", sa.String(240), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("stage_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("probability", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("estimated_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("expected_close_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("won_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lost_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        _tenant_fk("leads"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sales_leads_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pipeline_id"],
            ["mod_sales.pipelines.tenant_id", "mod_sales.pipelines.id"],
            ondelete="RESTRICT",
            name="fk_sales_leads_pipeline",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pipeline_id", "stage_id"],
            [
                "mod_sales.pipeline_stages.tenant_id",
                "mod_sales.pipeline_stages.pipeline_id",
                "mod_sales.pipeline_stages.id",
            ],
            ondelete="RESTRICT",
            name="fk_sales_leads_stage",
        ),
        sa.CheckConstraint(
            "status IN ('new','contacted','qualified','proposal','negotiation','won','lost')",
            name="ck_sales_leads_status",
        ),
        sa.CheckConstraint(
            "probability BETWEEN 0 AND 100", name="ck_sales_leads_probability"
        ),
        sa.CheckConstraint(
            "estimated_value IS NULL OR estimated_value >= 0",
            name="ck_sales_leads_estimated_value",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_leads_tenant_status",
        "leads",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_leads_tenant_pipeline",
        "leads",
        ["tenant_id", "pipeline_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_leads_tenant_subject",
        "leads",
        ["tenant_id", "subject_kind", "subject_opaque_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "lead_origins",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("capture_method", sa.String(60), nullable=False),
        sa.Column("source_kind", sa.String(120), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("source_interaction_id", sa.String(240), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        _tenant_fk("lead_origins"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_sales_lead_origins_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "lead_id",
            "capture_method",
            "source_kind",
            "source_ref",
            name="uq_sales_lead_origins_source_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "lead_id"],
            ["mod_sales.leads.tenant_id", "mod_sales.leads.id"],
            ondelete="CASCADE",
            name="fk_sales_lead_origins_lead",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_lead_origins_tenant_lead",
        "lead_origins",
        ["tenant_id", "lead_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "quotes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount_type", sa.String(30), nullable=True),
        sa.Column("discount_value", sa.Numeric(18, 4), nullable=True),
        sa.Column("discount_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount_revision", sa.Integer(), nullable=False),
        sa.Column("tax_rate", sa.Numeric(9, 4), nullable=False),
        sa.Column("tax_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("total", sa.Numeric(18, 2), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("authored_by_kind", sa.String(60), nullable=False),
        sa.Column("authored_by_opaque_id", sa.String(200), nullable=False),
        sa.Column("authored_by_label", sa.String(240), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_kind", sa.String(60), nullable=True),
        sa.Column("accepted_by_opaque_id", sa.String(200), nullable=True),
        sa.Column("accepted_by_label", sa.String(240), nullable=True),
        sa.Column("accepted_event_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_snapshot_sha256", sa.String(64), nullable=True),
        sa.Column("accepted_handoff", postgresql.JSONB(), nullable=True),
        *_timestamps(),
        _tenant_fk("quotes"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sales_quotes_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "accepted_event_id",
            name="uq_sales_quotes_tenant_accepted_event",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "lead_id"],
            ["mod_sales.leads.tenant_id", "mod_sales.leads.id"],
            ondelete="RESTRICT",
            name="fk_sales_quotes_lead",
        ),
        sa.CheckConstraint(
            "status IN ('draft','sent','accepted','rejected','expired')",
            name="ck_sales_quotes_status",
        ),
        sa.CheckConstraint(
            "subtotal >= 0 AND discount_amount >= 0 AND tax_total >= 0 AND total >= 0",
            name="ck_sales_quotes_nonnegative_money",
        ),
        sa.CheckConstraint(
            "tax_rate BETWEEN 0 AND 100", name="ck_sales_quotes_tax_rate"
        ),
        sa.CheckConstraint(
            "discount_type IS NULL OR discount_type IN ('percentage','fixed_amount')",
            name="ck_sales_quotes_discount_type",
        ),
        sa.CheckConstraint(
            "(status <> 'accepted') OR (accepted_at IS NOT NULL AND accepted_by_kind IS NOT NULL AND accepted_by_opaque_id IS NOT NULL AND accepted_event_id IS NOT NULL AND accepted_snapshot_sha256 IS NOT NULL AND accepted_handoff IS NOT NULL)",
            name="ck_sales_quotes_accepted_snapshot_complete",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_quotes_tenant_status",
        "quotes",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_quotes_tenant_lead",
        "quotes",
        ["tenant_id", "lead_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "quote_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("gross_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("catalogue_ref", sa.String(240), nullable=True),
        sa.Column("pricing_snapshot_ref", sa.String(240), nullable=True),
        *_timestamps(),
        _tenant_fk("quote_lines"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_sales_quote_lines_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "quote_id",
            "position",
            name="uq_sales_quote_lines_tenant_quote_position",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "quote_id"],
            ["mod_sales.quotes.tenant_id", "mod_sales.quotes.id"],
            ondelete="CASCADE",
            name="fk_sales_quote_lines_quote",
        ),
        sa.CheckConstraint("position > 0", name="ck_sales_quote_lines_position"),
        sa.CheckConstraint("quantity > 0", name="ck_sales_quote_lines_quantity"),
        sa.CheckConstraint(
            "unit_price >= 0 AND gross_amount >= 0 AND discount_amount >= 0 AND tax_amount >= 0 AND amount >= 0",
            name="ck_sales_quote_lines_nonnegative_money",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_quote_lines_tenant_quote",
        "quote_lines",
        ["tenant_id", "quote_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "quote_discount_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("discount_type", sa.String(30), nullable=True),
        sa.Column("discount_value", sa.Numeric(18, 4), nullable=True),
        sa.Column("discount_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("actor_kind", sa.String(60), nullable=False),
        sa.Column("actor_opaque_id", sa.String(200), nullable=False),
        sa.Column("actor_label", sa.String(240), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
        _tenant_fk("discount_revisions"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_sales_discount_revisions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "quote_id",
            "revision",
            name="uq_sales_discount_revisions_tenant_quote_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "quote_id",
            "command_id",
            name="uq_sales_discount_revisions_tenant_quote_command",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "quote_id"],
            ["mod_sales.quotes.tenant_id", "mod_sales.quotes.id"],
            ondelete="CASCADE",
            name="fk_sales_discount_revisions_quote",
        ),
        sa.CheckConstraint("revision > 0", name="ck_sales_discount_revision_positive"),
        sa.CheckConstraint(
            "action IN ('created','changed','removed')",
            name="ck_sales_discount_revision_action",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sales_discount_revisions_tenant_quote",
        "quote_discount_revisions",
        ["tenant_id", "quote_id"],
        schema=_SCHEMA,
    )

    _install_rls_and_grants()
    _install_immutability_triggers()


def _install_rls_and_grants() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_sales.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_sales.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation
                ON mod_sales.{table}
                USING (tenant_id = public.app_current_tenant_id())
                WITH CHECK (tenant_id = public.app_current_tenant_id());
            """
        )
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE mod_sales.{table} FROM platform_api;"
        )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_sales.pipelines TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_sales.pipeline_stages TO app_user;"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON mod_sales.leads TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_sales.lead_origins TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON mod_sales.quotes TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_sales.quote_lines TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_sales.quote_discount_revisions TO app_user;"
    )


def _install_immutability_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION mod_sales.refuse_accepted_quote_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = ''
        AS $$
        BEGIN
            IF OLD.status = 'accepted' THEN
                RAISE EXCEPTION 'accepted Quote % is immutable', OLD.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER sales_quotes_accepted_immutable
        BEFORE UPDATE OR DELETE ON mod_sales.quotes
        FOR EACH ROW EXECUTE FUNCTION mod_sales.refuse_accepted_quote_mutation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_sales.refuse_accepted_quote_child_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = ''
        AS $$
        DECLARE
            target_quote uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                target_quote := OLD.quote_id;
            ELSE
                target_quote := NEW.quote_id;
            END IF;
            IF EXISTS (
                SELECT 1 FROM mod_sales.quotes
                WHERE id = target_quote AND status = 'accepted'
            ) THEN
                RAISE EXCEPTION 'accepted Quote % children are immutable', target_quote
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER sales_quote_lines_accepted_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON mod_sales.quote_lines
        FOR EACH ROW EXECUTE FUNCTION mod_sales.refuse_accepted_quote_child_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER sales_discount_revisions_accepted_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON mod_sales.quote_discount_revisions
        FOR EACH ROW EXECUTE FUNCTION mod_sales.refuse_accepted_quote_child_mutation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_sales.refuse_append_only_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = ''
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = 'check_violation';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER sales_lead_origins_append_only
        BEFORE UPDATE OR DELETE ON mod_sales.lead_origins
        FOR EACH ROW EXECUTE FUNCTION mod_sales.refuse_append_only_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER sales_discount_revisions_append_only
        BEFORE UPDATE OR DELETE ON mod_sales.quote_discount_revisions
        FOR EACH ROW EXECUTE FUNCTION mod_sales.refuse_append_only_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS mod_sales CASCADE;")
