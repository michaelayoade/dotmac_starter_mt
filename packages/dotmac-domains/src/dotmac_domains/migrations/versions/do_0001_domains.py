"""Create the tenant-only registered-domain lifecycle owner.

Revision ID: do_0001_domains
Revises: (lineage root)
Create Date: 2026-08-19

Commands, outcomes, observations and desired-state revisions are append-only at
the database.  Every table has tenant_id NOT NULL, composite tenant identity,
FORCEd RLS, and a policy in this same revision.  Provider observations may
exist before a local service does, so their service correlation is nullable;
when populated, a composite foreign key proves same-tenant identity.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "do_0001_domains"
down_revision = None
branch_labels = ("domains",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "tenant_audit_log.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
    "outbox_relay.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_domains"


def _identity() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
    )


def _tenant_constraints(name: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id"),
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_domains;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_domains TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        "domain_services",
        *_identity(),
        sa.Column("registered_name", sa.String(253), nullable=False),
        sa.Column("lifecycle_state", sa.String(48), nullable=False),
        sa.Column("state_effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_line_ref", sa.String(255), nullable=False),
        sa.Column("offer_version_ref", sa.String(255), nullable=False),
        sa.Column("commercial_renewal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registrar_binding_ref", sa.String(255), nullable=True),
        sa.Column("dns_binding_ref", sa.String(255), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("domain_services"),
        sa.CheckConstraint("row_version >= 0", name="ck_domain_services_row_version"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_services_tenant_id",
        "domain_services",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_domain_services_tenant_active_name",
        "domain_services",
        ["tenant_id", "registered_name"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text(
            "lifecycle_state NOT IN ('registration_failed', 'released')"
        ),
    )
    op.create_index(
        "ix_domain_services_tenant_state",
        "domain_services",
        ["tenant_id", "lifecycle_state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "domain_commands",
        *_identity(),
        sa.Column("domain_service_id", sa.Uuid(), nullable=False),
        sa.Column("command_kind", sa.String(48), nullable=False),
        sa.Column("idempotency_scope", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(120), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("domain_commands"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            ["mod_domains.domain_services.tenant_id", "mod_domains.domain_services.id"],
            ondelete="RESTRICT",
            name="fk_domain_commands_service",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_scope",
            "idempotency_key",
            name="uq_domain_commands_tenant_scope_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_commands_tenant_id",
        "domain_commands",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_commands_tenant_service",
        "domain_commands",
        ["tenant_id", "domain_service_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "domain_command_outcomes",
        *_identity(),
        sa.Column("domain_service_id", sa.Uuid(), nullable=False),
        sa.Column("domain_command_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_key", sa.String(255), nullable=False),
        sa.Column("outcome_kind", sa.String(32), nullable=False),
        sa.Column("outcome_class", sa.String(40), nullable=False),
        sa.Column("provider_reference", sa.String(255), nullable=True),
        sa.Column("reason_code", sa.String(160), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("domain_command_outcomes"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            ["mod_domains.domain_services.tenant_id", "mod_domains.domain_services.id"],
            ondelete="RESTRICT",
            name="fk_domain_command_outcomes_service",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "domain_command_id"],
            ["mod_domains.domain_commands.tenant_id", "mod_domains.domain_commands.id"],
            ondelete="RESTRICT",
            name="fk_domain_command_outcomes_command",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "domain_command_id",
            "evidence_key",
            name="uq_domain_command_outcomes_command_evidence",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_command_outcomes_tenant_id",
        "domain_command_outcomes",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_command_outcomes_tenant_service",
        "domain_command_outcomes",
        ["tenant_id", "domain_service_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "domain_observations",
        *_identity(),
        sa.Column("domain_service_id", sa.Uuid(), nullable=True),
        sa.Column("registered_name", sa.String(253), nullable=False),
        sa.Column("capability_binding_ref", sa.String(255), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("observation_kind", sa.String(120), nullable=False),
        sa.Column("provider_statuses", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redemption_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_nameservers", postgresql.JSONB(), nullable=False),
        sa.Column("observed_contact_digest", sa.String(64), nullable=True),
        sa.Column("source_mode", sa.String(16), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("domain_observations"),
        sa.UniqueConstraint(
            "tenant_id",
            "capability_binding_ref",
            "provider_event_id",
            name="uq_domain_observations_binding_event",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            ["mod_domains.domain_services.tenant_id", "mod_domains.domain_services.id"],
            ondelete="RESTRICT",
            name="fk_domain_observations_service",
        ),
        sa.CheckConstraint(
            "source_mode IN ('ingress', 'poll')",
            name="ck_domain_observations_source_mode",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_observations_tenant_id",
        "domain_observations",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_observations_tenant_name_time",
        "domain_observations",
        ["tenant_id", "registered_name", "observed_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "dns_observations",
        *_identity(),
        sa.Column("domain_service_id", sa.Uuid(), nullable=True),
        sa.Column("zone_name", sa.String(253), nullable=False),
        sa.Column("capability_binding_ref", sa.String(255), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("observed_nameservers", postgresql.JSONB(), nullable=False),
        sa.Column("observed_recordsets", postgresql.JSONB(), nullable=False),
        sa.Column("observed_recordsets_digest", sa.String(64), nullable=False),
        sa.Column("source_mode", sa.String(16), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("dns_observations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            ["mod_domains.domain_services.tenant_id", "mod_domains.domain_services.id"],
            ondelete="RESTRICT",
            name="fk_dns_observations_service",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "capability_binding_ref",
            "provider_event_id",
            name="uq_dns_observations_binding_event",
        ),
        sa.CheckConstraint(
            "source_mode IN ('ingress', 'poll')",
            name="ck_dns_observations_source_mode",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_dns_observations_tenant_id",
        "dns_observations",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_dns_observations_tenant_zone_time",
        "dns_observations",
        ["tenant_id", "zone_name", "observed_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "domain_intents",
        *_identity(),
        sa.Column("domain_service_id", sa.Uuid(), nullable=False),
        sa.Column("intent_kind", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("domain_intents"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            ["mod_domains.domain_services.tenant_id", "mod_domains.domain_services.id"],
            ondelete="RESTRICT",
            name="fk_domain_intents_service",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "domain_service_id",
            "intent_kind",
            "version",
            name="uq_domain_intents_service_kind_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_domain_intents_version"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_intents_tenant_id",
        "domain_intents",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_intents_tenant_service_kind",
        "domain_intents",
        ["tenant_id", "domain_service_id", "intent_kind"],
        schema=_SCHEMA,
    )

    op.create_table(
        "domain_holds",
        *_identity(),
        sa.Column("domain_service_id", sa.Uuid(), nullable=False),
        sa.Column("hold_code", sa.String(120), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("reason_code", sa.String(160), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_reason", sa.String(160), nullable=True),
        *_tenant_constraints("domain_holds"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            ["mod_domains.domain_services.tenant_id", "mod_domains.domain_services.id"],
            ondelete="RESTRICT",
            name="fk_domain_holds_service",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_holds_tenant_id",
        "domain_holds",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_domain_holds_one_active_source",
        "domain_holds",
        [
            "tenant_id",
            "domain_service_id",
            "hold_code",
            "source_owner",
            "source_reference",
        ],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("cleared_at IS NULL"),
    )

    op.create_table(
        "domain_attention_conditions",
        *_identity(),
        sa.Column("domain_service_id", sa.Uuid(), nullable=False),
        sa.Column("source_command_id", sa.Uuid(), nullable=True),
        sa.Column("condition_code", sa.String(120), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("reason_code", sa.String(160), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_code", sa.String(160), nullable=True),
        *_tenant_constraints("domain_attention_conditions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            ["mod_domains.domain_services.tenant_id", "mod_domains.domain_services.id"],
            ondelete="RESTRICT",
            name="fk_domain_attention_conditions_service",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_domain_attention_conditions_tenant_id",
        "domain_attention_conditions",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_domain_attention_one_open",
        "domain_attention_conditions",
        ["tenant_id", "domain_service_id", "condition_code"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )

    op.execute(
        """
        CREATE FUNCTION mod_domains.refuse_evidence_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'domain command, outcome, registrar/DNS observation and intent evidence is immutable'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER domain_commands_immutable BEFORE UPDATE OR DELETE ON mod_domains.domain_commands FOR EACH ROW EXECUTE FUNCTION mod_domains.refuse_evidence_mutation();"
    )
    op.execute(
        "CREATE TRIGGER domain_command_outcomes_immutable BEFORE UPDATE OR DELETE ON mod_domains.domain_command_outcomes FOR EACH ROW EXECUTE FUNCTION mod_domains.refuse_evidence_mutation();"
    )
    op.execute(
        "CREATE TRIGGER domain_observations_immutable BEFORE UPDATE OR DELETE ON mod_domains.domain_observations FOR EACH ROW EXECUTE FUNCTION mod_domains.refuse_evidence_mutation();"
    )
    op.execute(
        "CREATE TRIGGER dns_observations_immutable BEFORE UPDATE OR DELETE ON mod_domains.dns_observations FOR EACH ROW EXECUTE FUNCTION mod_domains.refuse_evidence_mutation();"
    )
    op.execute(
        "CREATE TRIGGER domain_intents_immutable BEFORE UPDATE OR DELETE ON mod_domains.domain_intents FOR EACH ROW EXECUTE FUNCTION mod_domains.refuse_evidence_mutation();"
    )
    op.execute(
        """
        CREATE FUNCTION mod_domains.refuse_hold_rewrite()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR OLD.cleared_at IS NOT NULL
               OR NEW.cleared_at IS NULL
               OR NEW.cleared_reason IS NULL
               OR (to_jsonb(NEW) - 'cleared_at' - 'cleared_reason')
                  IS DISTINCT FROM
                  (to_jsonb(OLD) - 'cleared_at' - 'cleared_reason') THEN
                RAISE EXCEPTION 'a domain hold may only move once from open to cleared'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER domain_holds_controlled_update BEFORE UPDATE OR DELETE ON mod_domains.domain_holds FOR EACH ROW EXECUTE FUNCTION mod_domains.refuse_hold_rewrite();"
    )
    op.execute(
        """
        CREATE FUNCTION mod_domains.refuse_attention_rewrite()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR OLD.resolved_at IS NOT NULL
               OR NEW.resolved_at IS NULL
               OR NEW.resolution_code IS NULL
               OR (to_jsonb(NEW) - 'resolved_at' - 'resolution_code')
                  IS DISTINCT FROM
                  (to_jsonb(OLD) - 'resolved_at' - 'resolution_code') THEN
                RAISE EXCEPTION 'domain attention may only move once from open to resolved'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER domain_attention_controlled_update BEFORE UPDATE OR DELETE ON mod_domains.domain_attention_conditions FOR EACH ROW EXECUTE FUNCTION mod_domains.refuse_attention_rewrite();"
    )
    op.execute(
        """
        CREATE FUNCTION mod_domains.refuse_service_identity_rewrite()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR NEW.row_version <> OLD.row_version + 1
               OR (to_jsonb(NEW)
                    - 'lifecycle_state'
                    - 'state_effective_at'
                    - 'commercial_renewal_at'
                    - 'registrar_binding_ref'
                    - 'dns_binding_ref'
                    - 'row_version'
                    - 'updated_at')
                  IS DISTINCT FROM
                  (to_jsonb(OLD)
                    - 'lifecycle_state'
                    - 'state_effective_at'
                    - 'commercial_renewal_at'
                    - 'registrar_binding_ref'
                    - 'dns_binding_ref'
                    - 'row_version'
                    - 'updated_at') THEN
                RAISE EXCEPTION 'a domain service may only advance owner-controlled state'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER domain_services_controlled_update BEFORE UPDATE OR DELETE ON mod_domains.domain_services FOR EACH ROW EXECUTE FUNCTION mod_domains.refuse_service_identity_rewrite();"
    )

    op.execute("ALTER TABLE mod_domains.domain_services ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_domains.domain_services FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY domain_services_tenant_isolation ON mod_domains.domain_services USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_domains.domain_services TO app_user;")
    op.execute(
        "GRANT UPDATE (lifecycle_state, state_effective_at, commercial_renewal_at, registrar_binding_ref, dns_binding_ref, row_version, updated_at) ON mod_domains.domain_services TO app_user;"
    )
    op.execute("ALTER TABLE mod_domains.domain_commands ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_domains.domain_commands FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY domain_commands_tenant_isolation ON mod_domains.domain_commands USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_domains.domain_commands TO app_user;")
    op.execute(
        "ALTER TABLE mod_domains.domain_command_outcomes ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_domains.domain_command_outcomes FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY domain_command_outcomes_tenant_isolation ON mod_domains.domain_command_outcomes USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_domains.domain_command_outcomes TO app_user;"
    )
    op.execute("ALTER TABLE mod_domains.domain_observations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_domains.domain_observations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY domain_observations_tenant_isolation ON mod_domains.domain_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_domains.domain_observations TO app_user;")
    op.execute("ALTER TABLE mod_domains.dns_observations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_domains.dns_observations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY dns_observations_tenant_isolation ON mod_domains.dns_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_domains.dns_observations TO app_user;")
    op.execute("ALTER TABLE mod_domains.domain_intents ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_domains.domain_intents FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY domain_intents_tenant_isolation ON mod_domains.domain_intents USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_domains.domain_intents TO app_user;")
    op.execute("ALTER TABLE mod_domains.domain_holds ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_domains.domain_holds FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY domain_holds_tenant_isolation ON mod_domains.domain_holds USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_domains.domain_holds TO app_user;")
    op.execute(
        "GRANT UPDATE (cleared_at, cleared_reason) ON mod_domains.domain_holds TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_domains.domain_attention_conditions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_domains.domain_attention_conditions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY domain_attention_conditions_tenant_isolation ON mod_domains.domain_attention_conditions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_domains.domain_attention_conditions TO app_user;"
    )
    op.execute(
        "GRANT UPDATE (resolved_at, resolution_code) ON mod_domains.domain_attention_conditions TO app_user;"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS mod_domains CASCADE;")
