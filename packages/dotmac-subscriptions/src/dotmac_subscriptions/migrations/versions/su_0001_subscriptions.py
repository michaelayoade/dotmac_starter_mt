"""Create selectable tenant and platform subscriptions planes.

Revision ID: su_0001_subscriptions
Revises: lineage root
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "su_0001_subscriptions"
down_revision = None
branch_labels = ("subscriptions",)

MODULE_CODE = "subscriptions"
COMMON_REQUIRES = ("module_database_roles.v1", "idempotency_ledger.v1")
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    module=MODULE_CODE,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
)

_SCHEMA = "mod_subscriptions"


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _tenant() -> sa.Column[Any]:
    return sa.Column("tenant_id", sa.Uuid(), nullable=False)


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE", name=name
    )


def _created() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def _offer_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        _created(),
    ]


def _offer_version_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_code", sa.String(120), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawal_reason", sa.Text(), nullable=True),
        sa.Column("withdrawal_command_id", sa.Uuid(), nullable=True),
        _created(),
    ]


def _price_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("offer_version_id", sa.Uuid(), nullable=False),
        sa.Column("price_key", sa.String(120), nullable=False),
        sa.Column("charge_model_code", sa.String(120), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("scale", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        _created(),
    ]


def _contract_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("source_code", sa.String(120), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        _created(),
    ]


def _contract_version_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("source_code", sa.String(120), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declared_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate_basis", sa.String(40), nullable=False),
        sa.Column("rate_unit", sa.String(12), nullable=False),
        sa.Column("rate_quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("service_interval_unit", sa.String(12), nullable=False),
        sa.Column("service_interval_count", sa.Integer(), nullable=False),
        sa.Column("invoice_interval_unit", sa.String(12), nullable=False),
        sa.Column("invoice_interval_count", sa.Integer(), nullable=False),
        sa.Column("collection_timing", sa.String(16), nullable=False),
        sa.Column("alignment", sa.String(32), nullable=False),
        sa.Column("anchor_day", sa.Integer(), nullable=True),
        sa.Column("end_of_month_rule", sa.String(40), nullable=False),
        sa.Column("timezone_name", sa.String(120), nullable=False),
        sa.Column("proration_policy", sa.String(40), nullable=False),
        sa.Column("rating_policy_version", sa.String(80), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_actor", sa.String(160), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column("terminal_command_id", sa.Uuid(), nullable=True),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        _created(),
    ]


def _line_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("contract_line_key", sa.Uuid(), nullable=False),
        sa.Column("charge_model_code", sa.String(120), nullable=False),
        sa.Column("source_code", sa.String(120), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("product_link_ref", sa.String(255), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("scale", sa.Integer(), nullable=False),
        sa.Column("offer_version_id", sa.Uuid(), nullable=False),
        sa.Column("offer_version", sa.Integer(), nullable=False),
        sa.Column(
            "entitlement_codes",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        _created(),
    ]


def _occurrence_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("contract_line_key", sa.Uuid(), nullable=False),
        sa.Column("charge_model_code", sa.String(120), nullable=False),
        sa.Column("source_code", sa.String(120), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("pre_tax_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("amount_scale", sa.Integer(), nullable=False),
        sa.Column("rating_coverage_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rating_coverage_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rating_unit_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("rating_quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("rating_rate_basis", sa.String(40), nullable=False),
        sa.Column("rating_rate_unit", sa.String(12), nullable=False),
        sa.Column("rating_rate_quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("rating_rate_units", sa.Numeric(38, 28), nullable=False),
        sa.Column("rating_proration_policy", sa.String(40), nullable=False),
        sa.Column("rating_proration_factor", sa.Numeric(38, 28), nullable=False),
        sa.Column("rating_timezone_name", sa.String(120), nullable=False),
        sa.Column("rating_policy_version", sa.String(80), nullable=False),
        sa.Column("offer_version_ref", sa.String(180), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("emitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("output_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("corrects_occurrence_id", sa.Uuid(), nullable=True),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        _created(),
    ]


def _version_checks(prefix: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint("version > 0", name=f"ck_{prefix}_version"),
        sa.CheckConstraint("source_version > 0", name=f"ck_{prefix}_source_version"),
        sa.CheckConstraint(
            "state IN ('draft', 'effective', 'superseded', 'ended', 'cancelled')",
            name=f"ck_{prefix}_state",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at", name=f"ck_{prefix}_interval"
        ),
        sa.CheckConstraint(
            "declared_ends_at IS NULL OR declared_ends_at > starts_at",
            name=f"ck_{prefix}_declared_interval",
        ),
        sa.CheckConstraint(
            "service_interval_count > 0 AND invoice_interval_count > 0",
            name=f"ck_{prefix}_interval_counts",
        ),
        sa.CheckConstraint("rate_quantity > 0", name=f"ck_{prefix}_rate_quantity"),
        sa.CheckConstraint(
            "anchor_day IS NULL OR (anchor_day >= 1 AND anchor_day <= 31)",
            name=f"ck_{prefix}_anchor_day",
        ),
    ]


def _occurrence_checks(prefix: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint("period_end > period_start", name=f"ck_{prefix}_period"),
        sa.CheckConstraint(
            "rating_coverage_end > rating_coverage_start "
            "AND rating_coverage_start >= period_start "
            "AND rating_coverage_end <= period_end",
            name=f"ck_{prefix}_coverage",
        ),
        sa.CheckConstraint(
            "pre_tax_amount >= 0 AND rating_unit_price >= 0 "
            "AND rating_quantity > 0 AND rating_rate_quantity > 0 "
            "AND rating_rate_units >= 0",
            name=f"ck_{prefix}_rating_values",
        ),
        sa.CheckConstraint(
            "rating_proration_factor >= 0 AND rating_proration_factor <= 1",
            name=f"ck_{prefix}_proration",
        ),
        sa.CheckConstraint("generation > 0", name=f"ck_{prefix}_generation"),
        sa.CheckConstraint(
            "state IN ('scheduled', 'due', 'emitted', 'cancelled')",
            name=f"ck_{prefix}_state",
        ),
        sa.CheckConstraint(
            "amount_scale >= 0 AND amount_scale <= 6", name=f"ck_{prefix}_scale"
        ),
    ]


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    require_prerequisites(op.get_bind(), COMMON_REQUIRES)
    if ModulePlane.TENANT in planes:
        require_prerequisites(op.get_bind(), TENANT_REQUIRES)
    if ModulePlane.PLATFORM in planes:
        require_prerequisites(op.get_bind(), PLATFORM_REQUIRES)

    op.execute("CREATE SCHEMA IF NOT EXISTS mod_subscriptions;")
    op.execute("GRANT USAGE ON SCHEMA mod_subscriptions TO app_admin;")
    if ModulePlane.TENANT in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_subscriptions TO app_user;")
        _upgrade_tenant()
    if ModulePlane.PLATFORM in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_subscriptions TO platform_api;")
        _upgrade_platform()
    _install_structural_guards()


def _upgrade_tenant() -> None:
    op.create_table(
        "offers",
        _id(),
        _tenant(),
        *_offer_columns(),
        _tenant_fk("fk_offers_tenant"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_offers_tenant_code"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_offers_tenant_id_id"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'withdrawn')",
            name="ck_offers_status",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "offer_versions",
        _id(),
        _tenant(),
        *_offer_version_columns(),
        _tenant_fk("fk_offer_versions_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offer_id"],
            ["mod_subscriptions.offers.tenant_id", "mod_subscriptions.offers.id"],
            name="fk_offer_versions_offer",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "offer_id", "version", name="uq_offer_versions_identity"
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_offer_versions_command"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_offer_versions_tenant_id_id"),
        sa.CheckConstraint("version > 0", name="ck_offer_versions_version"),
        sa.CheckConstraint(
            "source_version > 0", name="ck_offer_versions_source_version"
        ),
        sa.CheckConstraint(
            "state IN ('published', 'withdrawn')",
            name="ck_offer_versions_state",
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_offer_versions_interval",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "offer_version_prices",
        _id(),
        _tenant(),
        *_price_columns(),
        _tenant_fk("fk_offer_version_prices_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offer_version_id"],
            [
                "mod_subscriptions.offer_versions.tenant_id",
                "mod_subscriptions.offer_versions.id",
            ],
            name="fk_offer_version_prices_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "offer_version_id",
            "price_key",
            name="uq_offer_version_prices_key",
        ),
        sa.CheckConstraint(
            "amount >= 0 AND quantity > 0", name="ck_offer_version_prices_amounts"
        ),
        sa.CheckConstraint("scale >= 0 AND scale <= 6", name="ck_offer_prices_scale"),
        schema=_SCHEMA,
    )
    op.create_table(
        "subscription_contracts",
        _id(),
        _tenant(),
        *_contract_columns(),
        _tenant_fk("fk_subscription_contracts_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "source_code", "source_id", name="uq_contracts_source"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_contracts_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_table(
        "subscription_contract_versions",
        _id(),
        _tenant(),
        *_contract_version_columns(),
        *_version_checks("contract_versions"),
        _tenant_fk("fk_contract_versions_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"],
            [
                "mod_subscriptions.subscription_contracts.tenant_id",
                "mod_subscriptions.subscription_contracts.id",
            ],
            name="fk_contract_versions_contract",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supersedes_id"],
            [
                "mod_subscriptions.subscription_contract_versions.tenant_id",
                "mod_subscriptions.subscription_contract_versions.id",
            ],
            name="fk_contract_versions_supersedes",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "contract_id", "version", name="uq_contract_versions_number"
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_contract_versions_idempotency"
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_contract_versions_command"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_contract_versions_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "subscription_contract_lines",
        _id(),
        _tenant(),
        *_line_columns(),
        _tenant_fk("fk_contract_lines_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_version_id"],
            [
                "mod_subscriptions.subscription_contract_versions.tenant_id",
                "mod_subscriptions.subscription_contract_versions.id",
            ],
            name="fk_contract_lines_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offer_version_id"],
            [
                "mod_subscriptions.offer_versions.tenant_id",
                "mod_subscriptions.offer_versions.id",
            ],
            name="fk_contract_lines_offer_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "contract_version_id",
            "contract_line_key",
            name="uq_contract_lines_lineage",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "contract_version_id",
            "charge_model_code",
            "product_link_ref",
            name="uq_contract_lines_component",
        ),
        sa.CheckConstraint(
            "quantity > 0 AND unit_price >= 0", name="ck_contract_lines_amounts"
        ),
        sa.CheckConstraint("scale >= 0 AND scale <= 6", name="ck_contract_lines_scale"),
        sa.CheckConstraint(
            "source_version > 0 AND offer_version > 0",
            name="ck_contract_lines_versions",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "recurring_charge_occurrences",
        _id(),
        _tenant(),
        *_occurrence_columns(),
        *_occurrence_checks("occurrences"),
        _tenant_fk("fk_occurrences_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_version_id"],
            [
                "mod_subscriptions.subscription_contract_versions.tenant_id",
                "mod_subscriptions.subscription_contract_versions.id",
            ],
            name="fk_occurrences_contract_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_version_id", "contract_line_key"],
            [
                "mod_subscriptions.subscription_contract_lines.tenant_id",
                "mod_subscriptions.subscription_contract_lines.contract_version_id",
                "mod_subscriptions.subscription_contract_lines.contract_line_key",
            ],
            name="fk_occurrences_contract_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "corrects_occurrence_id"],
            [
                "mod_subscriptions.recurring_charge_occurrences.tenant_id",
                "mod_subscriptions.recurring_charge_occurrences.id",
            ],
            name="fk_occurrences_correction",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "contract_line_key",
            "contract_version_id",
            "charge_model_code",
            "source_code",
            "source_id",
            "source_version",
            "period_start",
            "period_end",
            "currency",
            name="uq_occurrences_natural_identity",
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_occurrences_idempotency"
        ),
        sa.UniqueConstraint("tenant_id", "command_id", name="uq_occurrences_command"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_occurrences_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_offers_tenant_status",
        "offers",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_offer_versions_effective",
        "offer_versions",
        ["tenant_id", "offer_id", "effective_from"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_contract_versions_effective",
        "subscription_contract_versions",
        ["tenant_id", "contract_id", "starts_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_contract_lines_version",
        "subscription_contract_lines",
        ["tenant_id", "contract_version_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_occurrences_contract",
        "recurring_charge_occurrences",
        ["tenant_id", "contract_version_id", "period_start"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_occurrences_unacknowledged",
        "recurring_charge_occurrences",
        ["tenant_id", "output_acknowledged_at"],
        schema=_SCHEMA,
    )
    op.execute(
        """
        ALTER TABLE mod_subscriptions.offers ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_subscriptions.offers FORCE ROW LEVEL SECURITY;
        CREATE POLICY offers_tenant_isolation ON mod_subscriptions.offers
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        ALTER TABLE mod_subscriptions.offer_versions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_subscriptions.offer_versions FORCE ROW LEVEL SECURITY;
        CREATE POLICY offer_versions_tenant_isolation ON mod_subscriptions.offer_versions
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        ALTER TABLE mod_subscriptions.offer_version_prices ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_subscriptions.offer_version_prices FORCE ROW LEVEL SECURITY;
        CREATE POLICY offer_version_prices_tenant_isolation ON mod_subscriptions.offer_version_prices
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        ALTER TABLE mod_subscriptions.subscription_contracts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_subscriptions.subscription_contracts FORCE ROW LEVEL SECURITY;
        CREATE POLICY subscription_contracts_tenant_isolation ON mod_subscriptions.subscription_contracts
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        ALTER TABLE mod_subscriptions.subscription_contract_versions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_subscriptions.subscription_contract_versions FORCE ROW LEVEL SECURITY;
        CREATE POLICY subscription_contract_versions_tenant_isolation ON mod_subscriptions.subscription_contract_versions
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        ALTER TABLE mod_subscriptions.subscription_contract_lines ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_subscriptions.subscription_contract_lines FORCE ROW LEVEL SECURITY;
        CREATE POLICY subscription_contract_lines_tenant_isolation ON mod_subscriptions.subscription_contract_lines
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        ALTER TABLE mod_subscriptions.recurring_charge_occurrences ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_subscriptions.recurring_charge_occurrences FORCE ROW LEVEL SECURITY;
        CREATE POLICY recurring_charge_occurrences_tenant_isolation ON mod_subscriptions.recurring_charge_occurrences
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.offers TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.offer_versions TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.offer_version_prices TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.subscription_contracts TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.subscription_contract_versions TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.subscription_contract_lines TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.recurring_charge_occurrences TO app_user;
        """
    )


def _upgrade_platform() -> None:
    op.create_table(
        "platform_offers",
        _id(),
        *_offer_columns(),
        sa.UniqueConstraint("code", name="uq_platform_offers_code"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'withdrawn')",
            name="ck_platform_offers_status",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_offer_versions",
        _id(),
        *_offer_version_columns(),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["mod_subscriptions.platform_offers.id"],
            name="fk_platform_offer_versions_offer",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "offer_id", "version", name="uq_platform_offer_versions_identity"
        ),
        sa.UniqueConstraint("command_id", name="uq_platform_offer_versions_command"),
        sa.CheckConstraint("version > 0", name="ck_platform_offer_versions_version"),
        sa.CheckConstraint(
            "source_version > 0", name="ck_platform_offer_versions_source_version"
        ),
        sa.CheckConstraint(
            "state IN ('published', 'withdrawn')",
            name="ck_platform_offer_versions_state",
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_platform_offer_versions_interval",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_offer_version_prices",
        _id(),
        *_price_columns(),
        sa.ForeignKeyConstraint(
            ["offer_version_id"],
            ["mod_subscriptions.platform_offer_versions.id"],
            name="fk_platform_offer_version_prices_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "offer_version_id", "price_key", name="uq_platform_offer_prices_key"
        ),
        sa.CheckConstraint(
            "amount >= 0 AND quantity > 0", name="ck_platform_offer_prices_amounts"
        ),
        sa.CheckConstraint(
            "scale >= 0 AND scale <= 6", name="ck_platform_offer_prices_scale"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_subscription_contracts",
        _id(),
        *_contract_columns(),
        sa.UniqueConstraint(
            "source_code", "source_id", name="uq_platform_contracts_source"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_subscription_contract_versions",
        _id(),
        *_contract_version_columns(),
        *_version_checks("platform_contract_versions"),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["mod_subscriptions.platform_subscription_contracts.id"],
            name="fk_platform_contract_versions_contract",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["mod_subscriptions.platform_subscription_contract_versions.id"],
            name="fk_platform_contract_versions_supersedes",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "contract_id", "version", name="uq_platform_contract_versions_number"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_platform_contract_versions_idempotency"
        ),
        sa.UniqueConstraint("command_id", name="uq_platform_contract_versions_command"),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_subscription_contract_lines",
        _id(),
        *_line_columns(),
        sa.ForeignKeyConstraint(
            ["contract_version_id"],
            ["mod_subscriptions.platform_subscription_contract_versions.id"],
            name="fk_platform_contract_lines_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["offer_version_id"],
            ["mod_subscriptions.platform_offer_versions.id"],
            name="fk_platform_contract_lines_offer_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "contract_version_id",
            "contract_line_key",
            name="uq_platform_contract_lines_lineage",
        ),
        sa.UniqueConstraint(
            "contract_version_id",
            "charge_model_code",
            "product_link_ref",
            name="uq_platform_contract_lines_component",
        ),
        sa.CheckConstraint(
            "quantity > 0 AND unit_price >= 0",
            name="ck_platform_contract_lines_amounts",
        ),
        sa.CheckConstraint(
            "scale >= 0 AND scale <= 6", name="ck_platform_contract_lines_scale"
        ),
        sa.CheckConstraint(
            "source_version > 0 AND offer_version > 0",
            name="ck_platform_contract_lines_versions",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_recurring_charge_occurrences",
        _id(),
        *_occurrence_columns(),
        *_occurrence_checks("platform_occurrences"),
        sa.ForeignKeyConstraint(
            ["contract_version_id"],
            ["mod_subscriptions.platform_subscription_contract_versions.id"],
            name="fk_platform_occurrences_contract_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_version_id", "contract_line_key"],
            [
                "mod_subscriptions.platform_subscription_contract_lines.contract_version_id",
                "mod_subscriptions.platform_subscription_contract_lines.contract_line_key",
            ],
            name="fk_platform_occurrences_contract_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["corrects_occurrence_id"],
            ["mod_subscriptions.platform_recurring_charge_occurrences.id"],
            name="fk_platform_occurrences_correction",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "contract_line_key",
            "contract_version_id",
            "charge_model_code",
            "source_code",
            "source_id",
            "source_version",
            "period_start",
            "period_end",
            "currency",
            name="uq_platform_occurrences_natural_identity",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_platform_occurrences_idempotency"
        ),
        sa.UniqueConstraint("command_id", name="uq_platform_occurrences_command"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_offers_status",
        "platform_offers",
        ["status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_offer_versions_effective",
        "platform_offer_versions",
        ["offer_id", "effective_from"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_contract_versions_effective",
        "platform_subscription_contract_versions",
        ["contract_id", "starts_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_contract_lines_version",
        "platform_subscription_contract_lines",
        ["contract_version_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_occurrences_contract",
        "platform_recurring_charge_occurrences",
        ["contract_version_id", "period_start"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_occurrences_unacknowledged",
        "platform_recurring_charge_occurrences",
        ["output_acknowledged_at"],
        schema=_SCHEMA,
    )
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.platform_offers TO platform_api;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.platform_offer_versions TO platform_api;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.platform_offer_version_prices TO platform_api;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.platform_subscription_contracts TO platform_api;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.platform_subscription_contract_versions TO platform_api;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.platform_subscription_contract_lines TO platform_api;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.platform_recurring_charge_occurrences TO platform_api;
        REVOKE ALL PRIVILEGES ON mod_subscriptions.platform_offers FROM app_user;
        REVOKE ALL PRIVILEGES ON mod_subscriptions.platform_offer_versions FROM app_user;
        REVOKE ALL PRIVILEGES ON mod_subscriptions.platform_offer_version_prices FROM app_user;
        REVOKE ALL PRIVILEGES ON mod_subscriptions.platform_subscription_contracts FROM app_user;
        REVOKE ALL PRIVILEGES ON mod_subscriptions.platform_subscription_contract_versions FROM app_user;
        REVOKE ALL PRIVILEGES ON mod_subscriptions.platform_subscription_contract_lines FROM app_user;
        REVOKE ALL PRIVILEGES ON mod_subscriptions.platform_recurring_charge_occurrences FROM app_user;
        """
    )


def _install_structural_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_subscriptions.refuse_immutable_row()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'mod_subscriptions.% is immutable; % is refused',
            TG_TABLE_NAME, TG_OP USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION mod_subscriptions.freeze_offer_version_content()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'published offer versions cannot be deleted'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF (to_jsonb(NEW) - ARRAY[
                'state', 'withdrawn_at', 'withdrawal_reason',
                'withdrawal_command_id'
              ]) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY[
                'state', 'withdrawn_at', 'withdrawal_reason',
                'withdrawal_command_id'
              ]) THEN
            RAISE EXCEPTION 'published offer version content is immutable'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF OLD.state <> 'published' OR NEW.state <> 'withdrawn'
             OR OLD.withdrawn_at IS NOT NULL
             OR OLD.withdrawal_reason IS NOT NULL
             OR OLD.withdrawal_command_id IS NOT NULL
             OR NEW.withdrawn_at IS NULL
             OR NULLIF(BTRIM(NEW.withdrawal_reason), '') IS NULL
             OR NEW.withdrawal_command_id IS NULL THEN
            RAISE EXCEPTION 'offer version update is not a valid withdrawal'
              USING ERRCODE = 'restrict_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION mod_subscriptions.freeze_contract_version_content()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'effective contract versions cannot be deleted'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF (to_jsonb(NEW) - ARRAY[
                'state', 'ends_at', 'superseded_at', 'terminal_actor',
                'terminal_reason', 'terminal_command_id'
              ]) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY[
                'state', 'ends_at', 'superseded_at', 'terminal_actor',
                'terminal_reason', 'terminal_command_id'
              ]) THEN
            RAISE EXCEPTION 'effective contract version content is immutable'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF OLD.state <> 'effective'
             OR NEW.state NOT IN ('superseded', 'ended')
             OR NEW.ends_at IS NULL
             OR NEW.superseded_at IS NULL
             OR NULLIF(BTRIM(NEW.terminal_actor), '') IS NULL
             OR NULLIF(BTRIM(NEW.terminal_reason), '') IS NULL
             OR NEW.terminal_command_id IS NULL THEN
            RAISE EXCEPTION 'contract version update is not a valid terminal transition'
              USING ERRCODE = 'restrict_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION mod_subscriptions.freeze_occurrence_content()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'recurring charge occurrences cannot be deleted'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF (to_jsonb(NEW) - 'output_acknowledged_at') IS DISTINCT FROM
             (to_jsonb(OLD) - 'output_acknowledged_at') THEN
            RAISE EXCEPTION 'recurring charge occurrence content is immutable'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF OLD.output_acknowledged_at IS NOT NULL OR
             NEW.output_acknowledged_at IS NULL THEN
            RAISE EXCEPTION 'occurrence acknowledgement is append-only'
              USING ERRCODE = 'restrict_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION mod_subscriptions.prevent_tenant_contract_overlap()
        RETURNS trigger AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(
            NEW.tenant_id::text || ':' || NEW.contract_id::text, 0));
          IF NEW.state IN ('effective', 'superseded', 'ended') AND EXISTS (
            SELECT 1 FROM mod_subscriptions.subscription_contract_versions v
             WHERE v.tenant_id = NEW.tenant_id
               AND v.contract_id = NEW.contract_id
               AND v.id <> NEW.id
               AND v.state IN ('effective', 'superseded', 'ended')
               AND v.starts_at < COALESCE(NEW.ends_at, 'infinity'::timestamptz)
               AND NEW.starts_at < COALESCE(v.ends_at, 'infinity'::timestamptz)
          ) THEN
            RAISE EXCEPTION 'subscription contract effective intervals overlap'
              USING ERRCODE = 'exclusion_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION mod_subscriptions.prevent_platform_contract_overlap()
        RETURNS trigger AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.contract_id::text, 0));
          IF NEW.state IN ('effective', 'superseded', 'ended') AND EXISTS (
            SELECT 1 FROM mod_subscriptions.platform_subscription_contract_versions v
             WHERE v.contract_id = NEW.contract_id
               AND v.id <> NEW.id
               AND v.state IN ('effective', 'superseded', 'ended')
               AND v.starts_at < COALESCE(NEW.ends_at, 'infinity'::timestamptz)
               AND NEW.starts_at < COALESCE(v.ends_at, 'infinity'::timestamptz)
          ) THEN
            RAISE EXCEPTION 'platform subscription contract intervals overlap'
              USING ERRCODE = 'exclusion_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('mod_subscriptions.offer_versions') IS NOT NULL THEN
            CREATE TRIGGER offer_versions_content_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.offer_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_offer_version_content();
            CREATE TRIGGER offer_version_prices_immutable
              BEFORE UPDATE OR DELETE ON mod_subscriptions.offer_version_prices
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.refuse_immutable_row();
            CREATE TRIGGER contract_versions_content_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.subscription_contract_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_contract_version_content();
            CREATE TRIGGER contract_versions_no_overlap
              BEFORE INSERT OR UPDATE ON mod_subscriptions.subscription_contract_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.prevent_tenant_contract_overlap();
            CREATE TRIGGER contract_lines_immutable
              BEFORE UPDATE OR DELETE ON mod_subscriptions.subscription_contract_lines
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.refuse_immutable_row();
            CREATE TRIGGER recurring_charge_occurrences_content_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.recurring_charge_occurrences
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_occurrence_content();
          END IF;
          IF to_regclass('mod_subscriptions.platform_offer_versions') IS NOT NULL THEN
            CREATE TRIGGER platform_offer_versions_content_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.platform_offer_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_offer_version_content();
            CREATE TRIGGER platform_offer_version_prices_immutable
              BEFORE UPDATE OR DELETE ON mod_subscriptions.platform_offer_version_prices
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.refuse_immutable_row();
            CREATE TRIGGER platform_contract_versions_content_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.platform_subscription_contract_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_contract_version_content();
            CREATE TRIGGER platform_contract_versions_no_overlap
              BEFORE INSERT OR UPDATE ON mod_subscriptions.platform_subscription_contract_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.prevent_platform_contract_overlap();
            CREATE TRIGGER platform_contract_lines_immutable
              BEFORE UPDATE OR DELETE ON mod_subscriptions.platform_subscription_contract_lines
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.refuse_immutable_row();
            CREATE TRIGGER platform_recurring_charge_occurrences_content_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.platform_recurring_charge_occurrences
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_occurrence_content();
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.PLATFORM in planes:
        op.drop_table("platform_recurring_charge_occurrences", schema=_SCHEMA)
        op.drop_table("platform_subscription_contract_lines", schema=_SCHEMA)
        op.drop_table("platform_subscription_contract_versions", schema=_SCHEMA)
        op.drop_table("platform_subscription_contracts", schema=_SCHEMA)
        op.drop_table("platform_offer_version_prices", schema=_SCHEMA)
        op.drop_table("platform_offer_versions", schema=_SCHEMA)
        op.drop_table("platform_offers", schema=_SCHEMA)
    if ModulePlane.TENANT in planes:
        op.drop_table("recurring_charge_occurrences", schema=_SCHEMA)
        op.drop_table("subscription_contract_lines", schema=_SCHEMA)
        op.drop_table("subscription_contract_versions", schema=_SCHEMA)
        op.drop_table("subscription_contracts", schema=_SCHEMA)
        op.drop_table("offer_version_prices", schema=_SCHEMA)
        op.drop_table("offer_versions", schema=_SCHEMA)
        op.drop_table("offers", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_subscriptions CASCADE;")
