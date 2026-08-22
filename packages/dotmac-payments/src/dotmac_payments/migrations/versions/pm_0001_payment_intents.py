"""Create payment intents, transfer proofs and append-only confirmations.

Revision ID: pm_0001_payment_intents
Revises: (lineage root)
Create Date: 2026-08-22
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "pm_0001_payment_intents"
down_revision = None
branch_labels = ("payments",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_payments"

_MONEY = sa.Numeric(20, 6)
_PURPOSES = ("INVOICE_SETTLEMENT", "ACCOUNT_CREDIT_DEPOSIT", "SERVICE_FEE")
_INTENT_STATUSES = ("PENDING", "CONFIRMED", "EXPIRED", "CANCELLED")
_PROOF_STATES = ("SUBMITTED", "ACCEPTED", "REJECTED")
_SOURCES = (
    "PROVIDER_CALLBACK",
    "PROVIDER_RECONCILIATION",
    "TRANSFER_PROOF",
    "MANUAL",
)
_TENANT_TABLES = (
    "payment_intents",
    "payment_transfer_proofs",
    "payment_confirmations",
)


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
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
    )


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_payments;")
    op.execute("REVOKE ALL ON SCHEMA mod_payments FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_payments TO app_user, app_admin;")

    op.create_table(
        "payment_intents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(120), nullable=False),
        sa.Column("payer_reference", sa.String(160), nullable=False),
        sa.Column("target_reference", sa.String(160), nullable=True),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("provider_type", sa.String(40), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("requested_amount", _MONEY, nullable=False),
        sa.Column("confirmed_amount", _MONEY, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_payment_intents_tenant",
        ),
        sa.CheckConstraint(
            _in_list("purpose", _PURPOSES), name="ck_payment_intents_purpose"
        ),
        sa.CheckConstraint(
            _in_list("status", _INTENT_STATUSES), name="ck_payment_intents_status"
        ),
        sa.CheckConstraint(
            "length(currency_code) = 3", name="ck_payment_intents_currency"
        ),
        sa.CheckConstraint(
            "requested_amount > 0", name="ck_payment_intents_requested_positive"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_payment_intents_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "reference", name="uq_payment_intents_tenant_reference"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_payment_intents_tenant_payer",
        "payment_intents",
        ["tenant_id", "payer_reference"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_payment_intents_tenant_status",
        "payment_intents",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "payment_transfer_proofs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_reference", sa.String(120), nullable=False),
        sa.Column("document_reference", sa.String(160), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("declared_amount", _MONEY, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer", sa.String(160), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_transfer_proofs_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "intent_id"],
            [
                "mod_payments.payment_intents.tenant_id",
                "mod_payments.payment_intents.id",
            ],
            ondelete="CASCADE",
            name="fk_transfer_proofs_tenant_intent",
        ),
        sa.CheckConstraint(
            _in_list("state", _PROOF_STATES), name="ck_transfer_proofs_state"
        ),
        sa.CheckConstraint(
            "length(currency_code) = 3", name="ck_transfer_proofs_currency"
        ),
        sa.CheckConstraint(
            "declared_amount > 0", name="ck_transfer_proofs_declared_positive"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_transfer_proofs_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "submitted_reference",
            name="uq_transfer_proofs_tenant_submitted_reference",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_transfer_proofs_tenant_intent",
        "payment_transfer_proofs",
        ["tenant_id", "intent_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "payment_confirmations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("provider_type", sa.String(40), nullable=False),
        sa.Column("external_reference", sa.String(160), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("confirmed_amount", _MONEY, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_payment_confirmations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "intent_id"],
            [
                "mod_payments.payment_intents.tenant_id",
                "mod_payments.payment_intents.id",
            ],
            ondelete="CASCADE",
            name="fk_payment_confirmations_tenant_intent",
        ),
        sa.CheckConstraint(
            _in_list("source", _SOURCES), name="ck_payment_confirmations_source"
        ),
        sa.CheckConstraint(
            "length(currency_code) = 3", name="ck_payment_confirmations_currency"
        ),
        sa.CheckConstraint(
            "confirmed_amount > 0", name="ck_payment_confirmations_amount_positive"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_payment_confirmations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider_type",
            "external_reference",
            name="uq_payment_confirmations_tenant_provider_external",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_payment_confirmations_tenant_intent_observed",
        "payment_confirmations",
        ["tenant_id", "intent_id", "observed_at"],
        schema=_SCHEMA,
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_payments.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_payments.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_payments.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payments.{table} TO app_user;"
        )


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_payments;")
