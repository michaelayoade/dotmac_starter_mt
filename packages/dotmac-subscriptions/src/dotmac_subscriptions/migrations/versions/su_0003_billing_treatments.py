"""Add bounded billing arrangements and append-only non-cash grants.

Revision ID: su_0003_billing_treatments
Revises: su_0002_offer_pricing
Create Date: 2026-08-24
"""

from __future__ import annotations

from typing import Any, NamedTuple

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "su_0003_billing_treatments"
down_revision = "su_0002_offer_pricing"
branch_labels = None
depends_on = None

MODULE_CODE = "subscriptions"
_SCHEMA = "mod_subscriptions"


class _PlaneTables(NamedTuple):
    arrangement: str
    grant: str
    contract: str
    version: str
    line: str
    offer_version: str
    occurrence: str
    prefix: str


_TABLES = {
    ModulePlane.TENANT: _PlaneTables(
        arrangement="subscription_billing_arrangements",
        grant="subscription_billing_grants",
        contract="subscription_contracts",
        version="subscription_contract_versions",
        line="subscription_contract_lines",
        offer_version="offer_versions",
        occurrence="recurring_charge_occurrences",
        prefix="billing",
    ),
    ModulePlane.PLATFORM: _PlaneTables(
        arrangement="platform_subscription_billing_arrangements",
        grant="platform_subscription_billing_grants",
        contract="platform_subscription_contracts",
        version="platform_subscription_contract_versions",
        line="platform_subscription_contract_lines",
        offer_version="platform_offer_versions",
        occurrence="platform_recurring_charge_occurrences",
        prefix="platform_billing",
    ),
}


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _tenant() -> sa.Column[Any]:
    return sa.Column("tenant_id", sa.Uuid(), nullable=False)


def _created() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def _arrangement_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("subscription_contract_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("contract_line_key", sa.Uuid(), nullable=False),
        sa.Column("offer_version_id", sa.Uuid(), nullable=False),
        sa.Column("treatment", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_policy_reference", sa.String(200), nullable=False),
        sa.Column("approval_policy_version", sa.String(80), nullable=False),
        sa.Column("approval_policy_max_days", sa.Integer(), nullable=False),
        sa.Column("maximum_recurring_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("scale", sa.Integer(), nullable=False),
        sa.Column("cadence_fingerprint", sa.String(64), nullable=False),
        sa.Column("sponsor_reference", sa.String(200), nullable=True),
        sa.Column("cost_center", sa.String(100), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("approved_by", sa.String(160), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.String(160), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("revocation_command_id", sa.Uuid(), nullable=True),
        sa.Column("revocation_correlation_id", sa.Uuid(), nullable=True),
        sa.Column("revocation_idempotency_key", sa.String(255), nullable=True),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        _created(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _grant_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("arrangement_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_contract_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("contract_line_key", sa.Uuid(), nullable=False),
        sa.Column("treatment", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column("arrangement_reason", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("scale", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        _created(),
    ]


def _arrangement_checks(prefix: str) -> tuple[sa.CheckConstraint, ...]:
    return (
        sa.CheckConstraint(
            "treatment IN ('complimentary', 'sponsored')",
            name=f"ck_{prefix}_arrangements_treatment",
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name=f"ck_{prefix}_arrangements_period"
        ),
        sa.CheckConstraint(
            "maximum_recurring_amount > 0",
            name=f"ck_{prefix}_arrangements_amount",
        ),
        sa.CheckConstraint(
            "scale >= 0 AND scale <= 6", name=f"ck_{prefix}_arrangements_scale"
        ),
        sa.CheckConstraint(
            "approval_policy_max_days BETWEEN 1 AND 366",
            name=f"ck_{prefix}_arrangements_policy_days",
        ),
        sa.CheckConstraint(
            "treatment <> 'sponsored' OR sponsor_reference IS NOT NULL "
            "OR cost_center IS NOT NULL",
            name=f"ck_{prefix}_arrangements_sponsor",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name=f"ck_{prefix}_arrangements_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND revoked_by IS NULL AND revoked_at IS NULL "
            "AND revocation_reason IS NULL AND revocation_command_id IS NULL "
            "AND revocation_correlation_id IS NULL "
            "AND revocation_idempotency_key IS NULL) OR "
            "(status = 'revoked' AND revoked_by IS NOT NULL "
            "AND revoked_at IS NOT NULL AND revoked_at >= approved_at "
            "AND revocation_reason IS NOT NULL "
            "AND revocation_command_id IS NOT NULL "
            "AND revocation_correlation_id IS NOT NULL "
            "AND revocation_idempotency_key IS NOT NULL)",
            name=f"ck_{prefix}_arrangements_revocation_evidence",
        ),
    )


def _grant_checks(prefix: str) -> tuple[sa.CheckConstraint, ...]:
    return (
        sa.CheckConstraint(
            "treatment IN ('complimentary', 'sponsored')",
            name=f"ck_{prefix}_grants_treatment",
        ),
        sa.CheckConstraint("ends_at > starts_at", name=f"ck_{prefix}_grants_period"),
        sa.CheckConstraint("reference_amount > 0", name=f"ck_{prefix}_grants_amount"),
        sa.CheckConstraint(
            "scale >= 0 AND scale <= 6", name=f"ck_{prefix}_grants_scale"
        ),
    )


def _upgrade_tenant(tables: _PlaneTables) -> None:
    op.create_table(
        tables.arrangement,
        _id(),
        _tenant(),
        *_arrangement_columns(),
        *_arrangement_checks(tables.prefix),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            name="fk_billing_arrangements_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "subscription_contract_id"],
            [
                f"{_SCHEMA}.{tables.contract}.tenant_id",
                f"{_SCHEMA}.{tables.contract}.id",
            ],
            name="fk_billing_arrangements_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_version_id"],
            [
                f"{_SCHEMA}.{tables.version}.tenant_id",
                f"{_SCHEMA}.{tables.version}.id",
            ],
            name="fk_billing_arrangements_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_version_id", "contract_line_key"],
            [
                f"{_SCHEMA}.{tables.line}.tenant_id",
                f"{_SCHEMA}.{tables.line}.contract_version_id",
                f"{_SCHEMA}.{tables.line}.contract_line_key",
            ],
            name="fk_billing_arrangements_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offer_version_id"],
            [
                f"{_SCHEMA}.{tables.offer_version}.tenant_id",
                f"{_SCHEMA}.{tables.offer_version}.id",
            ],
            name="fk_billing_arrangements_offer_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_billing_arrangements_tenant_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_billing_arrangements_command"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_billing_arrangements_idempotency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "revocation_command_id",
            name="uq_billing_arrangements_revocation_command",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "revocation_idempotency_key",
            name="uq_billing_arrangements_revocation_idempotency",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        tables.grant,
        _id(),
        _tenant(),
        *_grant_columns(),
        *_grant_checks(tables.prefix),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            name="fk_billing_grants_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "arrangement_id"],
            [
                f"{_SCHEMA}.{tables.arrangement}.tenant_id",
                f"{_SCHEMA}.{tables.arrangement}.id",
            ],
            name="fk_billing_grants_arrangement",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "subscription_contract_id"],
            [
                f"{_SCHEMA}.{tables.contract}.tenant_id",
                f"{_SCHEMA}.{tables.contract}.id",
            ],
            name="fk_billing_grants_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_version_id", "contract_line_key"],
            [
                f"{_SCHEMA}.{tables.line}.tenant_id",
                f"{_SCHEMA}.{tables.line}.contract_version_id",
                f"{_SCHEMA}.{tables.line}.contract_line_key",
            ],
            name="fk_billing_grants_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "occurrence_id"],
            [
                f"{_SCHEMA}.{tables.occurrence}.tenant_id",
                f"{_SCHEMA}.{tables.occurrence}.id",
            ],
            name="fk_billing_grants_occurrence",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_billing_grants_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_billing_grants_command"
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_billing_grants_idempotency"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "occurrence_id",
            name="uq_billing_grants_occurrence",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "starts_at",
            "ends_at",
            name="uq_billing_grants_period",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_billing_arrangements_effective",
        tables.arrangement,
        [
            "tenant_id",
            "subscription_contract_id",
            "contract_line_key",
            "starts_at",
            "ends_at",
        ],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_billing_grants_contract_period",
        tables.grant,
        ["tenant_id", "subscription_contract_id", "contract_line_key", "starts_at"],
        schema=_SCHEMA,
    )
    op.execute(
        f"""
        ALTER TABLE {_SCHEMA}.{tables.arrangement} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {_SCHEMA}.{tables.arrangement} FORCE ROW LEVEL SECURITY;
        CREATE POLICY {tables.arrangement}_tenant_isolation
          ON {_SCHEMA}.{tables.arrangement}
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        ALTER TABLE {_SCHEMA}.{tables.grant} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {_SCHEMA}.{tables.grant} FORCE ROW LEVEL SECURITY;
        CREATE POLICY {tables.grant}_tenant_isolation
          ON {_SCHEMA}.{tables.grant}
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE ON {_SCHEMA}.{tables.arrangement} TO app_user;
        GRANT SELECT, INSERT ON {_SCHEMA}.{tables.grant} TO app_user;
        """
    )


def _upgrade_platform(tables: _PlaneTables) -> None:
    op.create_table(
        tables.arrangement,
        _id(),
        *_arrangement_columns(),
        *_arrangement_checks(tables.prefix),
        sa.ForeignKeyConstraint(
            ["subscription_contract_id"],
            [f"{_SCHEMA}.{tables.contract}.id"],
            name="fk_platform_billing_arrangements_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_version_id"],
            [f"{_SCHEMA}.{tables.version}.id"],
            name="fk_platform_billing_arrangements_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_version_id", "contract_line_key"],
            [
                f"{_SCHEMA}.{tables.line}.contract_version_id",
                f"{_SCHEMA}.{tables.line}.contract_line_key",
            ],
            name="fk_platform_billing_arrangements_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_version_id"],
            [f"{_SCHEMA}.{tables.offer_version}.id"],
            name="fk_platform_billing_arrangements_offer_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "command_id", name="uq_platform_billing_arrangements_command"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_platform_billing_arrangements_idempotency"
        ),
        sa.UniqueConstraint(
            "revocation_command_id",
            name="uq_platform_billing_arrangements_revocation_command",
        ),
        sa.UniqueConstraint(
            "revocation_idempotency_key",
            name="uq_platform_billing_arrangements_revocation_idempotency",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        tables.grant,
        _id(),
        *_grant_columns(),
        *_grant_checks(tables.prefix),
        sa.ForeignKeyConstraint(
            ["arrangement_id"],
            [f"{_SCHEMA}.{tables.arrangement}.id"],
            name="fk_platform_billing_grants_arrangement",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"],
            [f"{_SCHEMA}.{tables.occurrence}.id"],
            name="fk_platform_billing_grants_occurrence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_contract_id"],
            [f"{_SCHEMA}.{tables.contract}.id"],
            name="fk_platform_billing_grants_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_version_id", "contract_line_key"],
            [
                f"{_SCHEMA}.{tables.line}.contract_version_id",
                f"{_SCHEMA}.{tables.line}.contract_line_key",
            ],
            name="fk_platform_billing_grants_line",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("command_id", name="uq_platform_billing_grants_command"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_platform_billing_grants_idempotency"
        ),
        sa.UniqueConstraint(
            "arrangement_id",
            "occurrence_id",
            name="uq_platform_billing_grants_occurrence",
        ),
        sa.UniqueConstraint(
            "arrangement_id",
            "starts_at",
            "ends_at",
            name="uq_platform_billing_grants_period",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_billing_arrangements_effective",
        tables.arrangement,
        ["subscription_contract_id", "contract_line_key", "starts_at", "ends_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_billing_grants_contract_period",
        tables.grant,
        ["subscription_contract_id", "contract_line_key", "starts_at"],
        schema=_SCHEMA,
    )
    op.execute(
        f"""
        GRANT SELECT, INSERT, UPDATE ON {_SCHEMA}.{tables.arrangement} TO platform_api;
        GRANT SELECT, INSERT ON {_SCHEMA}.{tables.grant} TO platform_api;
        REVOKE ALL PRIVILEGES ON {_SCHEMA}.{tables.arrangement} FROM app_user;
        REVOKE ALL PRIVILEGES ON {_SCHEMA}.{tables.grant} FROM app_user;
        """
    )


def _install_guards(planes: frozenset[ModulePlane]) -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_subscriptions.freeze_billing_arrangement()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'billing arrangements cannot be deleted'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF (to_jsonb(NEW) - ARRAY[
                'status', 'revoked_by', 'revoked_at', 'revocation_reason',
                'revocation_command_id', 'revocation_correlation_id',
                'revocation_idempotency_key', 'updated_at'
              ]) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY[
                'status', 'revoked_by', 'revoked_at', 'revocation_reason',
                'revocation_command_id', 'revocation_correlation_id',
                'revocation_idempotency_key', 'updated_at'
              ]) THEN
            RAISE EXCEPTION 'billing arrangement approval evidence is immutable'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF OLD.status <> 'active' OR NEW.status <> 'revoked'
             OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
             OR NULLIF(BTRIM(NEW.revoked_by), '') IS NULL
             OR NULLIF(BTRIM(NEW.revocation_reason), '') IS NULL
             OR NEW.revocation_command_id IS NULL
             OR NEW.revocation_correlation_id IS NULL
             OR NULLIF(BTRIM(NEW.revocation_idempotency_key), '') IS NULL THEN
            RAISE EXCEPTION 'billing arrangement update is not a valid revocation'
              USING ERRCODE = 'restrict_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    if ModulePlane.TENANT in planes:
        op.execute(
            """
            CREATE OR REPLACE FUNCTION mod_subscriptions.guard_tenant_billing_arrangement()
            RETURNS trigger AS $$
            BEGIN
              PERFORM pg_advisory_xact_lock(hashtextextended(
                NEW.tenant_id::text || ':' || NEW.subscription_contract_id::text ||
                ':' || NEW.contract_line_key::text, 0));
              IF EXISTS (
                SELECT 1 FROM mod_subscriptions.subscription_billing_arrangements a
                 WHERE a.tenant_id = NEW.tenant_id
                   AND a.subscription_contract_id = NEW.subscription_contract_id
                   AND a.contract_line_key = NEW.contract_line_key
                   AND a.id <> NEW.id
                   AND a.starts_at < NEW.ends_at
                   AND a.ends_at > NEW.starts_at
                   AND (a.revoked_at IS NULL OR a.revoked_at > NEW.starts_at)
              ) THEN
                RAISE EXCEPTION 'subscription billing arrangements overlap'
                  USING ERRCODE = 'exclusion_violation';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION mod_subscriptions.freeze_tenant_contract_with_arrangement()
            RETURNS trigger AS $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM mod_subscriptions.subscription_billing_arrangements a
                 WHERE a.tenant_id = NEW.tenant_id
                   AND a.subscription_contract_id = NEW.contract_id
                   AND a.ends_at > NEW.starts_at
                   AND (a.revoked_at IS NULL OR a.revoked_at > NEW.starts_at)
              ) THEN
                RAISE EXCEPTION 'revoke open billing arrangement before changing contract terms'
                  USING ERRCODE = 'restrict_violation';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER billing_arrangements_no_overlap
              BEFORE INSERT ON mod_subscriptions.subscription_billing_arrangements
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.guard_tenant_billing_arrangement();
            CREATE TRIGGER billing_arrangements_content_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.subscription_billing_arrangements
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_billing_arrangement();
            CREATE TRIGGER billing_grants_immutable
              BEFORE UPDATE OR DELETE ON mod_subscriptions.subscription_billing_grants
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.refuse_immutable_row();
            CREATE TRIGGER contract_versions_billing_arrangement_freeze
              BEFORE INSERT ON mod_subscriptions.subscription_contract_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_tenant_contract_with_arrangement();
            """
        )
    if ModulePlane.PLATFORM in planes:
        op.execute(
            """
            CREATE OR REPLACE FUNCTION mod_subscriptions.guard_platform_billing_arrangement()
            RETURNS trigger AS $$
            BEGIN
              PERFORM pg_advisory_xact_lock(hashtextextended(
                NEW.subscription_contract_id::text || ':' ||
                NEW.contract_line_key::text, 0));
              IF EXISTS (
                SELECT 1 FROM mod_subscriptions.platform_subscription_billing_arrangements a
                 WHERE a.subscription_contract_id = NEW.subscription_contract_id
                   AND a.contract_line_key = NEW.contract_line_key
                   AND a.id <> NEW.id
                   AND a.starts_at < NEW.ends_at
                   AND a.ends_at > NEW.starts_at
                   AND (a.revoked_at IS NULL OR a.revoked_at > NEW.starts_at)
              ) THEN
                RAISE EXCEPTION 'platform subscription billing arrangements overlap'
                  USING ERRCODE = 'exclusion_violation';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION mod_subscriptions.freeze_platform_contract_with_arrangement()
            RETURNS trigger AS $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM mod_subscriptions.platform_subscription_billing_arrangements a
                 WHERE a.subscription_contract_id = NEW.contract_id
                   AND a.ends_at > NEW.starts_at
                   AND (a.revoked_at IS NULL OR a.revoked_at > NEW.starts_at)
              ) THEN
                RAISE EXCEPTION 'revoke open billing arrangement before changing contract terms'
                  USING ERRCODE = 'restrict_violation';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER platform_billing_arrangements_no_overlap
              BEFORE INSERT ON mod_subscriptions.platform_subscription_billing_arrangements
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.guard_platform_billing_arrangement();
            CREATE TRIGGER platform_billing_arrangements_content_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.platform_subscription_billing_arrangements
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_billing_arrangement();
            CREATE TRIGGER platform_billing_grants_immutable
              BEFORE UPDATE OR DELETE ON mod_subscriptions.platform_subscription_billing_grants
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.refuse_immutable_row();
            CREATE TRIGGER platform_contract_versions_billing_arrangement_freeze
              BEFORE INSERT ON mod_subscriptions.platform_subscription_contract_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_platform_contract_with_arrangement();
            """
        )


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.TENANT in planes:
        _upgrade_tenant(_TABLES[ModulePlane.TENANT])
    if ModulePlane.PLATFORM in planes:
        _upgrade_platform(_TABLES[ModulePlane.PLATFORM])
    _install_guards(planes)


def downgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.PLATFORM in planes:
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "mod_subscriptions.freeze_platform_contract_with_arrangement() CASCADE;"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "mod_subscriptions.guard_platform_billing_arrangement() CASCADE;"
        )
        op.drop_table(_TABLES[ModulePlane.PLATFORM].grant, schema=_SCHEMA)
        op.drop_table(_TABLES[ModulePlane.PLATFORM].arrangement, schema=_SCHEMA)
    if ModulePlane.TENANT in planes:
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "mod_subscriptions.freeze_tenant_contract_with_arrangement() CASCADE;"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "mod_subscriptions.guard_tenant_billing_arrangement() CASCADE;"
        )
        op.drop_table(_TABLES[ModulePlane.TENANT].grant, schema=_SCHEMA)
        op.drop_table(_TABLES[ModulePlane.TENANT].arrangement, schema=_SCHEMA)
    op.execute(
        "DROP FUNCTION IF EXISTS mod_subscriptions.freeze_billing_arrangement();"
    )
