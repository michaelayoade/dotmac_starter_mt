"""Create both declared Billing persistence planes.

Revision ID: bi_0001_billing
Revises: (lineage root)
Create Date: 2026-08-17
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "bi_0001_billing"
down_revision = None
branch_labels = ("billing",)

MODULE_CODE = "billing"
COMMON_REQUIRES = (
    "module_database_roles.v1",
    "idempotency_ledger.v1",
    "outbox_relay.v1",
)
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    module=MODULE_CODE,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
)

_SCHEMA = "mod_billing"
_MONEY = sa.Numeric(20, 6)

_TENANT_TABLES = [
    "billing_accounts",
    "rated_obligations",
    "documents",
    "document_lines",
    "document_events",
    "confirmed_settlements",
    "posting_groups",
    "posting_effects",
    "allocation_effects",
    "applied_tax_snapshots",
    "applied_fx_snapshots",
    "party_tax_identity_snapshots",
    "invoice_document_facts",
    "document_artifacts",
    "accounting_facts",
    "receivable_position_facts",
]
_PLATFORM_TABLES = [
    "platform_billing_accounts",
    "platform_rated_obligations",
    "platform_documents",
    "platform_document_lines",
    "platform_document_events",
    "platform_confirmed_settlements",
    "platform_posting_groups",
    "platform_posting_effects",
    "platform_allocation_effects",
    "platform_applied_tax_snapshots",
    "platform_applied_fx_snapshots",
    "platform_party_tax_identity_snapshots",
    "platform_invoice_document_facts",
    "platform_document_artifacts",
    "platform_accounting_facts",
    "platform_receivable_position_facts",
]
_MUTABLE = ("billing_accounts", "documents", "document_lines", "document_artifacts")
_PLATFORM_MUTABLE = tuple(f"platform_{name}" for name in _MUTABLE)
_TENANT_APPEND_ONLY = tuple(name for name in _TENANT_TABLES if name not in _MUTABLE)
_PLATFORM_APPEND_ONLY = tuple(
    name for name in _PLATFORM_TABLES if name not in _PLATFORM_MUTABLE
)

_REFUSE_FUNCTION = "mod_billing.refuse_mutation"
_FREEZE_TENANT_DOCUMENT = "mod_billing.freeze_tenant_document"
_FREEZE_PLATFORM_DOCUMENT = "mod_billing.freeze_platform_document"
_FREEZE_TENANT_LINE = "mod_billing.freeze_tenant_document_line"
_FREEZE_PLATFORM_LINE = "mod_billing.freeze_platform_document_line"
_SUPERSEDE_ARTIFACT = "mod_billing.allow_artifact_supersession"


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _account_columns() -> list[Any]:
    return [
        sa.Column("external_account_ref", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_account_currency",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_account_minor_units"
        ),
    ]


def _obligation_columns() -> list[Any]:
    return [
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("natural_key_digest", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("contract_line_ref", sa.String(255), nullable=False),
        sa.Column("contract_version", sa.String(120), nullable=False),
        sa.Column("charge_component", sa.String(120), nullable=False),
        sa.Column("source_system", sa.String(120), nullable=False),
        sa.Column("source_kind", sa.String(120), nullable=False),
        sa.Column("source_fact_id", sa.String(255), nullable=False),
        sa.Column("source_fact_version", sa.String(120), nullable=False),
        sa.Column("service_period_status", sa.String(32), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("collection_timing", sa.String(80), nullable=False),
        sa.Column("pre_tax_amount", _MONEY, nullable=False),
        sa.Column("tax_amount", _MONEY, nullable=False),
        sa.Column("total_amount", _MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("rated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_version_id", sa.String(255), nullable=False),
        sa.Column("supersedes_obligation_id", sa.Uuid()),
        _created_at(),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_obligation_currency",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_obligation_minor_units"
        ),
        sa.CheckConstraint(
            "(service_period_status = 'verified' AND period_start IS NOT NULL AND period_end IS NOT NULL AND period_start < period_end) OR "
            "(service_period_status <> 'verified' AND period_start IS NULL AND period_end IS NULL)",
            name="ck_obligation_service_period",
        ),
    ]


def _document_columns() -> list[Any]:
    return [
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_id", sa.Uuid()),
        sa.Column("document_kind", sa.String(24), nullable=False),
        sa.Column("credits_document_id", sa.Uuid()),
        sa.Column("lifecycle", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("series_code", sa.String(80)),
        sa.Column("document_number", sa.String(255)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("subtotal", _MONEY, nullable=False, server_default="0"),
        sa.Column("tax_total", _MONEY, nullable=False, server_default="0"),
        sa.Column("grand_total", _MONEY, nullable=False, server_default="0"),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("due_date_basis", sa.JSON(), nullable=False),
        sa.Column("document_profile_code", sa.String(120), nullable=False),
        sa.Column("document_profile_version", sa.String(120), nullable=False),
        sa.Column("seller_snapshot", sa.JSON(), nullable=False),
        sa.Column("customer_snapshot", sa.JSON(), nullable=False),
        sa.Column("payment_instructions", sa.JSON(), nullable=False),
        sa.Column("brand_asset", sa.JSON(), nullable=False),
        sa.Column("locale", sa.String(40), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_document_currency",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_document_minor_units"
        ),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'issued')", name="ck_document_lifecycle"
        ),
        sa.CheckConstraint(
            "(issued_at IS NULL AND lifecycle = 'draft' AND document_number IS NULL) OR "
            "(issued_at IS NOT NULL AND lifecycle = 'issued' AND document_number IS NOT NULL)",
            name="ck_document_issue_shape",
        ),
    ]


def _line_columns() -> list[Any]:
    return [
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_id", sa.Uuid()),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit_code", sa.String(40), nullable=False),
        sa.Column("unit_amount", _MONEY, nullable=False),
        sa.Column("pre_tax_amount", _MONEY, nullable=False),
        sa.Column("tax_amount", _MONEY, nullable=False),
        sa.Column("total_amount", _MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("price_source_version", sa.String(255), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_line_currency",
        ),
        sa.CheckConstraint("minor_units BETWEEN 0 AND 6", name="ck_line_minor_units"),
    ]


def _event_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
    ]


def _settlement_columns() -> list[Any]:
    return [
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(120), nullable=False),
        sa.Column("source_settlement_key", sa.String(255), nullable=False),
        sa.Column("source_version", sa.String(120), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_evidence", sa.String(120), nullable=False),
        sa.Column("funding_lane", sa.String(32), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_settlement_currency",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_settlement_minor_units"
        ),
        sa.CheckConstraint(
            "funding_lane IN ('available_credit', 'prepaid_funding')",
            name="ck_settlement_funding_lane",
        ),
    ]


def _group_columns() -> list[Any]:
    return [
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("group_kind", sa.String(40), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("source_version", sa.BigInteger(), nullable=False),
        sa.Column("reverses_group_id", sa.Uuid()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_group_currency",
        ),
        sa.CheckConstraint("minor_units BETWEEN 0 AND 6", name="ck_group_minor_units"),
    ]


def _effect_columns() -> list[Any]:
    return [
        sa.Column("posting_group_id", sa.Uuid(), nullable=False),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("lane", sa.String(32), nullable=False),
        sa.Column("amount_delta", _MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "lane IN ('receivable', 'available_credit', 'prepaid_funding')",
            name="ck_effect_lane",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_effect_currency",
        ),
        sa.CheckConstraint("minor_units BETWEEN 0 AND 6", name="ck_effect_minor_units"),
    ]


def _allocation_columns() -> list[Any]:
    return [
        sa.Column("posting_group_id", sa.Uuid(), nullable=False),
        sa.Column("settlement_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid()),
        sa.Column("effect_kind", sa.String(32), nullable=False),
        sa.Column("amount_delta", _MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("offsets_allocation_id", sa.Uuid()),
        _created_at(),
        sa.CheckConstraint(
            "effect_kind IN ('allocation', 'deallocation', 'reallocation', 'refund', 'reversal')",
            name="ck_allocation_effect_kind",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_allocation_currency",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_allocation_minor_units"
        ),
    ]


def _tax_columns() -> list[Any]:
    return [
        sa.Column("obligation_id", sa.Uuid()),
        sa.Column("document_id", sa.Uuid()),
        sa.Column("treatment_code", sa.String(120), nullable=False),
        sa.Column("jurisdiction_code", sa.String(120), nullable=False),
        sa.Column("policy_id", sa.String(255), nullable=False),
        sa.Column("policy_version", sa.String(120), nullable=False),
        sa.Column("rate", sa.Numeric(20, 6), nullable=False),
        sa.Column("taxable_basis", _MONEY, nullable=False),
        sa.Column("tax_amount", _MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_tax_currency",
        ),
        sa.CheckConstraint("minor_units BETWEEN 0 AND 6", name="ck_tax_minor_units"),
    ]


def _fx_columns() -> list[Any]:
    return [
        sa.Column("obligation_id", sa.Uuid()),
        sa.Column("document_id", sa.Uuid()),
        sa.Column("observation_id", sa.String(255), nullable=False),
        sa.Column("observation_version", sa.String(120), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(20, 6), nullable=False),
        sa.Column("rate_purpose", sa.String(120), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rounding_policy", sa.String(120), nullable=False),
        sa.Column("provenance", sa.String(255), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "base_currency = upper(base_currency) AND length(base_currency) = 3",
            name="ck_fx_base",
        ),
        sa.CheckConstraint(
            "quote_currency = upper(quote_currency) AND length(quote_currency) = 3",
            name="ck_fx_quote",
        ),
        sa.CheckConstraint("rate > 0", name="ck_fx_rate"),
    ]


def _party_tax_columns() -> list[Any]:
    return [
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("party_role", sa.String(16), nullable=False),
        sa.Column("identity_type", sa.String(120), nullable=False),
        sa.Column("identity_value", sa.String(255), nullable=False),
        sa.Column("country_code", sa.String(3), nullable=False),
        sa.Column("source_authority", sa.String(120), nullable=False),
        sa.Column("source_version", sa.String(120), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "party_role IN ('seller', 'customer')", name="ck_party_tax_role"
        ),
        sa.CheckConstraint(
            "country_code = upper(country_code) AND length(country_code) = 3",
            name="ck_party_tax_country",
        ),
    ]


def _document_fact_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("contract_version", sa.String(80), nullable=False),
        sa.Column("presentation_model_digest", sa.String(64), nullable=False),
        sa.Column("fact_payload", sa.JSON(), nullable=False),
        _created_at(),
    ]


def _artifact_columns() -> list[Any]:
    return [
        sa.Column("document_fact_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_number", sa.String(255), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("renderer_code", sa.String(120), nullable=False),
        sa.Column("renderer_version", sa.String(120), nullable=False),
        sa.Column("template_version", sa.String(120), nullable=False),
        sa.Column("presentation_model_digest", sa.String(64), nullable=False),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("issued_by", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_by_artifact_id", sa.Uuid()),
        sa.Column("supersession_reason", sa.String(120)),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        sa.Column("withdrawal_reason", sa.String(120)),
        _created_at(),
        sa.CheckConstraint("byte_length >= 0", name="ck_artifact_byte_length"),
    ]


def _accounting_fact_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("posting_group_id", sa.Uuid(), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("source_system", sa.String(120), nullable=False),
        sa.Column("source_authority", sa.String(40), nullable=False),
        sa.Column("effect_kind", sa.String(40), nullable=False),
        sa.Column("fact_digest", sa.String(64), nullable=False),
        sa.Column("fact_payload", sa.JSON(), nullable=False),
        sa.Column("reverses_fact_id", sa.Uuid()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
    ]


def _position_columns() -> list[Any]:
    return [
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("exposure_ref", sa.String(255), nullable=False),
        sa.Column("source_version", sa.BigInteger(), nullable=False),
        sa.Column("posting_group_watermark", sa.Uuid()),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("collectible_receivable", _MONEY, nullable=False),
        sa.Column("available_credit", _MONEY, nullable=False),
        sa.Column("prepaid_funding", _MONEY, nullable=False),
        sa.Column("state_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_authority", sa.String(40), nullable=False),
        sa.Column("derived_from", sa.String(32), nullable=False),
        sa.Column("completeness", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_period", sa.JSON(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("due_date_basis", sa.JSON(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_position_currency",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_position_minor_units"
        ),
    ]


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE", name=name
    )


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    require_prerequisites(op.get_bind(), COMMON_REQUIRES)
    if ModulePlane.TENANT in planes:
        require_prerequisites(op.get_bind(), TENANT_REQUIRES)
    if ModulePlane.PLATFORM in planes:
        require_prerequisites(op.get_bind(), PLATFORM_REQUIRES)

    op.execute("CREATE SCHEMA IF NOT EXISTS mod_billing;")
    op.execute("GRANT USAGE ON SCHEMA mod_billing TO app_admin;")
    if ModulePlane.TENANT in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_billing TO app_user;")
    if ModulePlane.PLATFORM in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_billing TO platform_api;")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_billing.refuse_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'mod_billing.% is immutable; % refused', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_billing.freeze_tenant_document() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.issued_at IS NOT NULL THEN
                RAISE EXCEPTION 'issued billing document is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_billing.freeze_platform_document() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.issued_at IS NOT NULL THEN
                RAISE EXCEPTION 'issued platform billing document is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_billing.freeze_tenant_document_line() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM mod_billing.documents
                 WHERE id = OLD.document_id AND tenant_id = OLD.tenant_id
                   AND issued_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'issued billing document line is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_billing.freeze_platform_document_line() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM mod_billing.platform_documents
                 WHERE id = OLD.document_id AND issued_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'issued platform billing document line is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_billing.allow_artifact_supersession() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'document artifact evidence is append-only'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.superseded_at IS NULL
               AND NEW.superseded_at IS NOT NULL
               AND NEW.superseded_by_artifact_id IS NOT NULL
               AND NEW.supersession_reason IS NOT NULL
               AND (to_jsonb(NEW) - 'superseded_at' - 'superseded_by_artifact_id' - 'supersession_reason')
                   IS NOT DISTINCT FROM
                   (to_jsonb(OLD) - 'superseded_at' - 'superseded_by_artifact_id' - 'supersession_reason') THEN
                RETURN NEW;
            END IF;
            IF OLD.withdrawn_at IS NULL
               AND NEW.withdrawn_at IS NOT NULL
               AND NEW.withdrawal_reason IS NOT NULL
               AND (to_jsonb(NEW) - 'withdrawn_at' - 'withdrawal_reason')
                   IS NOT DISTINCT FROM
                   (to_jsonb(OLD) - 'withdrawn_at' - 'withdrawal_reason') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'document artifact evidence is append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    if ModulePlane.TENANT in planes:
        _upgrade_tenant_plane()
    if ModulePlane.PLATFORM in planes:
        _upgrade_platform_plane()


def _upgrade_tenant_plane() -> None:
    op.create_table(
        "billing_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_account_columns(),
        _tenant_fk("fk_billing_accounts_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_billing_accounts_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "external_account_ref",
            "currency",
            name="uq_billing_accounts_tenant_external_account_ref_currency",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "rated_obligations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_obligation_columns(),
        _tenant_fk("fk_rated_obligations_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_rated_obligations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "natural_key_digest",
            name="uq_rated_obligations_tenant_natural_key_digest",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_document_columns(),
        _tenant_fk("fk_documents_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "series_code",
            "document_number",
            name="uq_documents_tenant_series_code_document_number",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "document_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_line_columns(),
        _tenant_fk("fk_document_lines_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_lines_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "line_number",
            name="uq_document_lines_tenant_document_id_line_number",
        ),
        sa.UniqueConstraint(
            "tenant_id", "obligation_id", name="uq_document_lines_tenant_obligation_id"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "document_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_event_columns(),
        _tenant_fk("fk_document_events_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_events_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "event_kind",
            name="uq_document_events_tenant_document_id_event_kind",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "confirmed_settlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_settlement_columns(),
        _tenant_fk("fk_confirmed_settlements_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_confirmed_settlements_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_settlement_key",
            name="uq_confirmed_settlements_tenant_source_system_source_settlem",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "posting_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_group_columns(),
        _tenant_fk("fk_posting_groups_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_posting_groups_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "billing_account_id",
            "source_version",
            name="uq_posting_groups_tenant_billing_account_id_source_version",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "posting_effects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_effect_columns(),
        _tenant_fk("fk_posting_effects_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_posting_effects_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "posting_group_id",
            "lane",
            name="uq_posting_effects_tenant_posting_group_id_lane",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "allocation_effects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_allocation_columns(),
        _tenant_fk("fk_allocation_effects_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_allocation_effects_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "posting_group_id",
            "settlement_id",
            "document_id",
            name="uq_allocation_effects_tenant_posting_group_id_settlement_id_",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "applied_tax_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_tax_columns(),
        _tenant_fk("fk_applied_tax_snapshots_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_applied_tax_snapshots_tenant_id_id"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "applied_fx_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_fx_columns(),
        _tenant_fk("fk_applied_fx_snapshots_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_applied_fx_snapshots_tenant_id_id"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "party_tax_identity_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_party_tax_columns(),
        _tenant_fk("fk_party_tax_identity_snapshots_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_party_tax_identity_snapshots_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "party_role",
            "identity_type",
            name="uq_party_tax_identity_snapshots_tenant_document_id_party_rol",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "invoice_document_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_document_fact_columns(),
        _tenant_fk("fk_invoice_document_facts_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_invoice_document_facts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "fact_version",
            name="uq_invoice_document_facts_tenant_document_id_fact_version",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "document_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_artifact_columns(),
        _tenant_fk("fk_document_artifacts_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_document_artifacts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_fact_id",
            "media_type",
            "file_id",
            name="uq_artifacts_tenant_file",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "accounting_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_accounting_fact_columns(),
        _tenant_fk("fk_accounting_facts_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_accounting_facts_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "posting_group_id",
            "fact_version",
            name="uq_accounting_facts_tenant_posting_group_id_fact_version",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "receivable_position_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_position_columns(),
        _tenant_fk("fk_receivable_position_facts_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_receivable_position_facts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "exposure_ref",
            "billing_account_id",
            "currency",
            "source_version",
            name="uq_position_tenant_identity_version",
        ),
        schema=_SCHEMA,
    )

    op.create_index(
        "uq_artifacts_tenant_current",
        "document_artifacts",
        ["tenant_id", "document_fact_id", "media_type"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        schema=_SCHEMA,
    )
    op.execute("ALTER TABLE mod_billing.billing_accounts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.billing_accounts FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY billing_accounts_tenant_isolation ON mod_billing.billing_accounts
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_billing.rated_obligations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.rated_obligations FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY rated_obligations_tenant_isolation ON mod_billing.rated_obligations
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_billing.documents ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.documents FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY documents_tenant_isolation ON mod_billing.documents
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_billing.document_lines ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.document_lines FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY document_lines_tenant_isolation ON mod_billing.document_lines
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_billing.document_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.document_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY document_events_tenant_isolation ON mod_billing.document_events
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "ALTER TABLE mod_billing.confirmed_settlements ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_billing.confirmed_settlements FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY confirmed_settlements_tenant_isolation ON mod_billing.confirmed_settlements
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_billing.posting_groups ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.posting_groups FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY posting_groups_tenant_isolation ON mod_billing.posting_groups
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_billing.posting_effects ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.posting_effects FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY posting_effects_tenant_isolation ON mod_billing.posting_effects
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_billing.allocation_effects ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.allocation_effects FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY allocation_effects_tenant_isolation ON mod_billing.allocation_effects
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "ALTER TABLE mod_billing.applied_tax_snapshots ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_billing.applied_tax_snapshots FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY applied_tax_snapshots_tenant_isolation ON mod_billing.applied_tax_snapshots
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "ALTER TABLE mod_billing.applied_fx_snapshots ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_billing.applied_fx_snapshots FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY applied_fx_snapshots_tenant_isolation ON mod_billing.applied_fx_snapshots
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "ALTER TABLE mod_billing.party_tax_identity_snapshots ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_billing.party_tax_identity_snapshots FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY party_tax_identity_snapshots_tenant_isolation ON mod_billing.party_tax_identity_snapshots
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "ALTER TABLE mod_billing.invoice_document_facts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_billing.invoice_document_facts FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY invoice_document_facts_tenant_isolation ON mod_billing.invoice_document_facts
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_billing.document_artifacts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.document_artifacts FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY document_artifacts_tenant_isolation ON mod_billing.document_artifacts
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_billing.accounting_facts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_billing.accounting_facts FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY accounting_facts_tenant_isolation ON mod_billing.accounting_facts
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "ALTER TABLE mod_billing.receivable_position_facts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_billing.receivable_position_facts FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY receivable_position_facts_tenant_isolation ON mod_billing.receivable_position_facts
        USING (tenant_id = public.app_current_tenant_id())
        WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.billing_accounts TO app_user;")
    op.execute("GRANT UPDATE (id) ON mod_billing.billing_accounts TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE ON mod_billing.documents TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_billing.document_lines TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mod_billing.document_artifacts TO app_user;"
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.rated_obligations TO app_user;")
    op.execute(
        """
        CREATE TRIGGER rated_obligations_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.rated_obligations
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.document_events TO app_user;")
    op.execute(
        """
        CREATE TRIGGER document_events_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.document_events
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.confirmed_settlements TO app_user;")
    op.execute(
        """
        CREATE TRIGGER confirmed_settlements_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.confirmed_settlements
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.posting_groups TO app_user;")
    op.execute(
        """
        CREATE TRIGGER posting_groups_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.posting_groups
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.posting_effects TO app_user;")
    op.execute(
        """
        CREATE TRIGGER posting_effects_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.posting_effects
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.allocation_effects TO app_user;")
    op.execute(
        """
        CREATE TRIGGER allocation_effects_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.allocation_effects
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.applied_tax_snapshots TO app_user;")
    op.execute(
        """
        CREATE TRIGGER applied_tax_snapshots_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.applied_tax_snapshots
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.applied_fx_snapshots TO app_user;")
    op.execute(
        """
        CREATE TRIGGER applied_fx_snapshots_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.applied_fx_snapshots
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.party_tax_identity_snapshots TO app_user;"
    )
    op.execute(
        """
        CREATE TRIGGER party_tax_identity_snapshots_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.party_tax_identity_snapshots
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.invoice_document_facts TO app_user;"
    )
    op.execute(
        """
        CREATE TRIGGER invoice_document_facts_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.invoice_document_facts
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_billing.accounting_facts TO app_user;")
    op.execute(
        """
        CREATE TRIGGER accounting_facts_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.accounting_facts
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.receivable_position_facts TO app_user;"
    )
    op.execute(
        """
        CREATE TRIGGER receivable_position_facts_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.receivable_position_facts
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER documents_freeze
        BEFORE UPDATE OR DELETE ON mod_billing.documents
        FOR EACH ROW EXECUTE FUNCTION mod_billing.freeze_tenant_document();
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_lines_freeze
        BEFORE UPDATE OR DELETE ON mod_billing.document_lines
        FOR EACH ROW EXECUTE FUNCTION mod_billing.freeze_tenant_document_line();
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_artifacts_supersession_only
        BEFORE UPDATE OR DELETE ON mod_billing.document_artifacts
        FOR EACH ROW EXECUTE FUNCTION mod_billing.allow_artifact_supersession();
        """
    )


def _upgrade_platform_plane() -> None:
    op.create_table(
        "platform_billing_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_account_columns(),
        sa.UniqueConstraint(
            "external_account_ref",
            "currency",
            name="uq_platform_billing_accounts_external_account_ref_currency",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_rated_obligations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_obligation_columns(),
        sa.UniqueConstraint(
            "natural_key_digest",
            name="uq_platform_rated_obligations_natural_key_digest",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_document_columns(),
        sa.UniqueConstraint(
            "series_code",
            "document_number",
            name="uq_platform_documents_series_code_document_number",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_document_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_line_columns(),
        sa.UniqueConstraint(
            "document_id",
            "line_number",
            name="uq_platform_document_lines_document_id_line_number",
        ),
        sa.UniqueConstraint(
            "obligation_id", name="uq_platform_document_lines_obligation_id"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_document_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_event_columns(),
        sa.UniqueConstraint(
            "document_id",
            "event_kind",
            name="uq_platform_document_events_document_id_event_kind",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_confirmed_settlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_settlement_columns(),
        sa.UniqueConstraint(
            "source_system",
            "source_settlement_key",
            name="uq_platform_confirmed_settlements_source_system_source_settl",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_posting_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_group_columns(),
        sa.UniqueConstraint(
            "billing_account_id",
            "source_version",
            name="uq_platform_posting_groups_billing_account_id_source_version",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_posting_effects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_effect_columns(),
        sa.UniqueConstraint(
            "posting_group_id",
            "lane",
            name="uq_platform_posting_effects_posting_group_id_lane",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_allocation_effects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_allocation_columns(),
        sa.UniqueConstraint(
            "posting_group_id",
            "settlement_id",
            "document_id",
            name="uq_platform_allocation_effects_posting_group_id_settlement_i",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_applied_tax_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_tax_columns(),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_applied_fx_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_fx_columns(),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_party_tax_identity_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_party_tax_columns(),
        sa.UniqueConstraint(
            "document_id",
            "party_role",
            "identity_type",
            name="uq_platform_party_tax_identity_snapshots_document_id_party_r",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_invoice_document_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_document_fact_columns(),
        sa.UniqueConstraint(
            "document_id",
            "fact_version",
            name="uq_platform_invoice_document_facts_document_id_fact_version",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_document_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_artifact_columns(),
        sa.UniqueConstraint(
            "document_fact_id",
            "media_type",
            "file_id",
            name="uq_platform_artifacts_file",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_accounting_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_accounting_fact_columns(),
        sa.UniqueConstraint(
            "posting_group_id",
            "fact_version",
            name="uq_platform_accounting_facts_posting_group_id_fact_version",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_receivable_position_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_position_columns(),
        sa.UniqueConstraint(
            "source_owner",
            "exposure_ref",
            "billing_account_id",
            "currency",
            "source_version",
            name="uq_platform_position_identity_version",
        ),
        schema=_SCHEMA,
    )

    op.create_index(
        "uq_platform_artifacts_current",
        "platform_document_artifacts",
        ["document_fact_id", "media_type"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        schema=_SCHEMA,
    )
    op.execute("REVOKE ALL ON mod_billing.platform_billing_accounts FROM app_user;")
    op.execute("REVOKE ALL ON mod_billing.platform_rated_obligations FROM app_user;")
    op.execute("REVOKE ALL ON mod_billing.platform_documents FROM app_user;")
    op.execute("REVOKE ALL ON mod_billing.platform_document_lines FROM app_user;")
    op.execute("REVOKE ALL ON mod_billing.platform_document_events FROM app_user;")
    op.execute(
        "REVOKE ALL ON mod_billing.platform_confirmed_settlements FROM app_user;"
    )
    op.execute("REVOKE ALL ON mod_billing.platform_posting_groups FROM app_user;")
    op.execute("REVOKE ALL ON mod_billing.platform_posting_effects FROM app_user;")
    op.execute("REVOKE ALL ON mod_billing.platform_allocation_effects FROM app_user;")
    op.execute(
        "REVOKE ALL ON mod_billing.platform_applied_tax_snapshots FROM app_user;"
    )
    op.execute("REVOKE ALL ON mod_billing.platform_applied_fx_snapshots FROM app_user;")
    op.execute(
        "REVOKE ALL ON mod_billing.platform_party_tax_identity_snapshots FROM app_user;"
    )
    op.execute(
        "REVOKE ALL ON mod_billing.platform_invoice_document_facts FROM app_user;"
    )
    op.execute("REVOKE ALL ON mod_billing.platform_document_artifacts FROM app_user;")
    op.execute("REVOKE ALL ON mod_billing.platform_accounting_facts FROM app_user;")
    op.execute(
        "REVOKE ALL ON mod_billing.platform_receivable_position_facts FROM app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_billing_accounts TO platform_api;"
    )
    op.execute(
        "GRANT UPDATE (id) ON mod_billing.platform_billing_accounts TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mod_billing.platform_documents TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_document_lines TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mod_billing.platform_document_artifacts TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_rated_obligations TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_rated_obligations_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_rated_obligations
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_document_events TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_document_events_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_document_events
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_confirmed_settlements TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_confirmed_settlements_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_confirmed_settlements
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_posting_groups TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_posting_groups_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_posting_groups
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_posting_effects TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_posting_effects_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_posting_effects
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_allocation_effects TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_allocation_effects_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_allocation_effects
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_applied_tax_snapshots TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_applied_tax_snapshots_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_applied_tax_snapshots
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_applied_fx_snapshots TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_applied_fx_snapshots_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_applied_fx_snapshots
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_party_tax_identity_snapshots TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_party_tax_identity_snapshots_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_party_tax_identity_snapshots
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_invoice_document_facts TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_invoice_document_facts_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_invoice_document_facts
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_accounting_facts TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_accounting_facts_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_accounting_facts
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_billing.platform_receivable_position_facts TO platform_api;"
    )
    op.execute(
        """
        CREATE TRIGGER platform_receivable_position_facts_append_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_receivable_position_facts
        FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER platform_documents_freeze
        BEFORE UPDATE OR DELETE ON mod_billing.platform_documents
        FOR EACH ROW EXECUTE FUNCTION mod_billing.freeze_platform_document();
        """
    )
    op.execute(
        """
        CREATE TRIGGER platform_document_lines_freeze
        BEFORE UPDATE OR DELETE ON mod_billing.platform_document_lines
        FOR EACH ROW EXECUTE FUNCTION mod_billing.freeze_platform_document_line();
        """
    )
    op.execute(
        """
        CREATE TRIGGER platform_document_artifacts_supersession_only
        BEFORE UPDATE OR DELETE ON mod_billing.platform_document_artifacts
        FOR EACH ROW EXECUTE FUNCTION mod_billing.allow_artifact_supersession();
        """
    )


def downgrade() -> None:
    for table in (*_PLATFORM_TABLES, *_TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS mod_billing.{table} CASCADE;")
    for function in (
        _SUPERSEDE_ARTIFACT,
        _FREEZE_PLATFORM_LINE,
        _FREEZE_TENANT_LINE,
        _FREEZE_PLATFORM_DOCUMENT,
        _FREEZE_TENANT_DOCUMENT,
        _REFUSE_FUNCTION,
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}() CASCADE;")
