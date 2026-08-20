"""Create the tenant expense evidence and policy owner.

Revision ID: ex_0001_expenses
Revises: (lineage root)
Create Date: 2026-08-18

All ten tables carry a required tenant, composite tenant identity and forced
RLS. Internal foreign keys carry the tenant. Published policy/evaluation and
lifecycle evidence is immutable at the database boundary; submitted line
snapshots admit only the claim's explicit approval-amount projection.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ex_0001_expenses"
down_revision = None
branch_labels = ("expenses",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "party_person_catalog.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_expenses"
_TABLES = (
    "expense_categories",
    "expense_policies",
    "expense_policy_rules",
    "expense_requests",
    "expense_request_lines",
    "expense_claims",
    "expense_claim_lines",
    "expense_receipts",
    "expense_policy_evaluations",
    "expense_lifecycle_events",
)


def _identity() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
    )


def _tenant_constraints(table: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{table}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id_id"),
    )


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _updated_at() -> sa.Column[Any]:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _create_categories() -> None:
    op.create_table(
        "expense_categories",
        *_identity(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "requires_receipt", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("receipt_threshold", sa.Numeric(18, 2), nullable=True),
        sa.Column("max_amount_per_line", sa.Numeric(18, 2), nullable=True),
        sa.Column("max_amount_per_claim", sa.Numeric(18, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at(),
        _updated_at(),
        *_tenant_constraints("expense_categories"),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_expense_categories_tenant_code"
        ),
        sa.CheckConstraint(
            "receipt_threshold IS NULL OR receipt_threshold >= 0",
            name="ck_expense_categories_receipt_threshold",
        ),
        sa.CheckConstraint(
            "max_amount_per_line IS NULL OR max_amount_per_line > 0",
            name="ck_expense_categories_line_limit",
        ),
        sa.CheckConstraint(
            "max_amount_per_claim IS NULL OR max_amount_per_claim > 0",
            name="ck_expense_categories_claim_limit",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_categories_tenant_id",
        "expense_categories",
        ["tenant_id"],
        schema=_SCHEMA,
    )


def _create_policies() -> None:
    op.create_table(
        "expense_policies",
        *_identity(),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        *_tenant_constraints("expense_policies"),
        sa.UniqueConstraint(
            "tenant_id",
            "code",
            "version",
            name="uq_expense_policies_tenant_code_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_expense_policies_positive_version"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_expense_policies_effective_dates",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired')",
            name="expense_policy_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_policies_tenant_id",
        "expense_policies",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_policies_tenant_effective",
        "expense_policies",
        ["tenant_id", "status", "effective_from", "effective_to"],
        schema=_SCHEMA,
    )

    op.create_table(
        "expense_policy_rules",
        *_identity(),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target", sa.String(16), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("limit_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("applicability_key", sa.String(200), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        _created_at(),
        _updated_at(),
        *_tenant_constraints("expense_policy_rules"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "mod_expenses.expense_policies.tenant_id",
                "mod_expenses.expense_policies.id",
            ],
            ondelete="CASCADE",
            name="fk_expense_policy_rules_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            [
                "mod_expenses.expense_categories.tenant_id",
                "mod_expenses.expense_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_rules_category",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "policy_id",
            "code",
            name="uq_expense_policy_rules_policy_code",
        ),
        sa.CheckConstraint(
            "target IN ('request', 'claim')", name="expense_policy_target"
        ),
        sa.CheckConstraint(
            "period IN ('transaction', 'day', 'week', 'month', 'quarter', 'year')",
            name="expense_limit_period",
        ),
        sa.CheckConstraint(
            "action IN ('warn', 'require_approval', 'block')",
            name="expense_limit_action",
        ),
        sa.CheckConstraint(
            "limit_amount > 0", name="ck_expense_policy_rules_positive_limit"
        ),
        sa.CheckConstraint("priority >= 0", name="ck_expense_policy_rules_priority"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_policy_rules_tenant_id",
        "expense_policy_rules",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_policy_rules_tenant_policy",
        "expense_policy_rules",
        ["tenant_id", "policy_id", "priority"],
        schema=_SCHEMA,
    )


def _create_requests() -> None:
    op.create_table(
        "expense_requests",
        *_identity(),
        sa.Column("reference", sa.String(80), nullable=False),
        sa.Column("requester_party_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("needed_by", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("total_requested_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("evaluation_batch_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reference", sa.String(255), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        *_tenant_constraints("expense_requests"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requester_party_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="RESTRICT",
            name="fk_expense_requests_requester_party",
        ),
        sa.UniqueConstraint(
            "tenant_id", "reference", name="uq_expense_requests_tenant_reference"
        ),
        sa.CheckConstraint(
            "total_requested_amount > 0", name="ck_expense_requests_positive_total"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'rejected', "
            "'cancelled', 'converted')",
            name="expense_request_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_requests_tenant_id",
        "expense_requests",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_requests_tenant_requester",
        "expense_requests",
        ["tenant_id", "requester_party_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "expense_request_lines",
        *_identity(),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("expected_on", sa.Date(), nullable=False),
        sa.Column("vendor_name", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _created_at(),
        *_tenant_constraints("expense_request_lines"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                "mod_expenses.expense_requests.tenant_id",
                "mod_expenses.expense_requests.id",
            ],
            ondelete="CASCADE",
            name="fk_expense_request_lines_request",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            [
                "mod_expenses.expense_categories.tenant_id",
                "mod_expenses.expense_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_request_lines_category",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "request_id",
            "sequence",
            name="uq_expense_request_lines_request_sequence",
        ),
        sa.CheckConstraint(
            "amount > 0", name="ck_expense_request_lines_positive_amount"
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_expense_request_lines_sequence"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_request_lines_tenant_id",
        "expense_request_lines",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_request_lines_tenant_request",
        "expense_request_lines",
        ["tenant_id", "request_id"],
        schema=_SCHEMA,
    )


def _create_claims() -> None:
    op.create_table(
        "expense_claims",
        *_identity(),
        sa.Column("reference", sa.String(80), nullable=False),
        sa.Column("claimant_party_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("claim_date", sa.Date(), nullable=False),
        sa.Column("expense_period_start", sa.Date(), nullable=True),
        sa.Column("expense_period_end", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("total_claimed_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_approved_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("evaluation_batch_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reference", sa.String(255), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        *_tenant_constraints("expense_claims"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "claimant_party_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="RESTRICT",
            name="fk_expense_claims_claimant_party",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                "mod_expenses.expense_requests.tenant_id",
                "mod_expenses.expense_requests.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_claims_request",
        ),
        sa.UniqueConstraint(
            "tenant_id", "reference", name="uq_expense_claims_tenant_reference"
        ),
        sa.UniqueConstraint(
            "tenant_id", "request_id", name="uq_expense_claims_tenant_request"
        ),
        sa.CheckConstraint(
            "expense_period_end IS NULL OR expense_period_start IS NULL "
            "OR expense_period_end >= expense_period_start",
            name="ck_expense_claims_period_dates",
        ),
        sa.CheckConstraint(
            "total_claimed_amount > 0", name="ck_expense_claims_positive_claimed"
        ),
        sa.CheckConstraint(
            "total_approved_amount IS NULL OR total_approved_amount >= 0",
            name="ck_expense_claims_nonnegative_approved",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'rejected', "
            "'cancelled', 'approval_withdrawn')",
            name="expense_claim_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_claims_tenant_id",
        "expense_claims",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_claims_tenant_claimant",
        "expense_claims",
        ["tenant_id", "claimant_party_id", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_claims_tenant_date",
        "expense_claims",
        ["tenant_id", "claim_date", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "expense_claim_lines",
        *_identity(),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("claimed_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("approved_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("vendor_name", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _created_at(),
        *_tenant_constraints("expense_claim_lines"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            ["mod_expenses.expense_claims.tenant_id", "mod_expenses.expense_claims.id"],
            ondelete="CASCADE",
            name="fk_expense_claim_lines_claim",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            [
                "mod_expenses.expense_categories.tenant_id",
                "mod_expenses.expense_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_claim_lines_category",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "claim_id",
            "sequence",
            name="uq_expense_claim_lines_claim_sequence",
        ),
        sa.CheckConstraint(
            "claimed_amount > 0", name="ck_expense_claim_lines_positive_claimed"
        ),
        sa.CheckConstraint(
            "approved_amount IS NULL OR (approved_amount >= 0 "
            "AND approved_amount <= claimed_amount)",
            name="ck_expense_claim_lines_approved_range",
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_expense_claim_lines_sequence"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_claim_lines_tenant_id",
        "expense_claim_lines",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_claim_lines_tenant_claim",
        "expense_claim_lines",
        ["tenant_id", "claim_id"],
        schema=_SCHEMA,
    )


def _create_receipts() -> None:
    op.create_table(
        "expense_receipts",
        *_identity(),
        sa.Column("claim_line_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("receipt_number", sa.String(100), nullable=True),
        sa.Column("merchant_name", sa.String(200), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("gross_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("verification_reference", sa.String(255), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        *_tenant_constraints("expense_receipts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "claim_line_id"],
            [
                "mod_expenses.expense_claim_lines.tenant_id",
                "mod_expenses.expense_claim_lines.id",
            ],
            ondelete="CASCADE",
            name="fk_expense_receipts_claim_line",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "claim_line_id",
            "file_id",
            name="uq_expense_receipts_line_file",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_expense_receipts_positive_size"),
        sa.CheckConstraint(
            "gross_amount IS NULL OR gross_amount >= 0",
            name="ck_expense_receipts_nonnegative_amount",
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'verified', 'rejected')",
            name="expense_receipt_verification_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_receipts_tenant_id",
        "expense_receipts",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_receipts_tenant_line",
        "expense_receipts",
        ["tenant_id", "claim_line_id"],
        schema=_SCHEMA,
    )


def _create_evidence() -> None:
    op.create_table(
        "expense_policy_evaluations",
        *_identity(),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("rule_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("request_line_id", sa.Uuid(), nullable=True),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("claim_line_id", sa.Uuid(), nullable=True),
        sa.Column("result", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("actual_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("limit_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        *_tenant_constraints("expense_policy_evaluations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "mod_expenses.expense_policies.tenant_id",
                "mod_expenses.expense_policies.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [
                "mod_expenses.expense_policy_rules.tenant_id",
                "mod_expenses.expense_policy_rules.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_rule",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                "mod_expenses.expense_requests.tenant_id",
                "mod_expenses.expense_requests.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_request",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_line_id"],
            [
                "mod_expenses.expense_request_lines.tenant_id",
                "mod_expenses.expense_request_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_request_line",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            ["mod_expenses.expense_claims.tenant_id", "mod_expenses.expense_claims.id"],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_claim",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "claim_line_id"],
            [
                "mod_expenses.expense_claim_lines.tenant_id",
                "mod_expenses.expense_claim_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_policy_evaluations_claim_line",
        ),
        sa.CheckConstraint(
            "(request_id IS NOT NULL AND claim_id IS NULL) OR "
            "(request_id IS NULL AND claim_id IS NOT NULL)",
            name="ck_expense_policy_evaluations_one_subject",
        ),
        sa.CheckConstraint(
            "result IN ('passed', 'warning', 'approval_required', 'blocked')",
            name="expense_evaluation_result",
        ),
        sa.CheckConstraint(
            "actual_amount >= 0", name="ck_expense_policy_evaluations_actual"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_policy_evaluations_tenant_id",
        "expense_policy_evaluations",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_policy_evaluations_batch",
        "expense_policy_evaluations",
        ["tenant_id", "batch_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "expense_lifecycle_events",
        *_identity(),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("from_status", sa.String(40), nullable=True),
        sa.Column("to_status", sa.String(40), nullable=False),
        sa.Column("actor_reference", sa.String(255), nullable=False),
        sa.Column("decision_reference", sa.String(255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        *_tenant_constraints("expense_lifecycle_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                "mod_expenses.expense_requests.tenant_id",
                "mod_expenses.expense_requests.id",
            ],
            ondelete="RESTRICT",
            name="fk_expense_lifecycle_events_request",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            ["mod_expenses.expense_claims.tenant_id", "mod_expenses.expense_claims.id"],
            ondelete="RESTRICT",
            name="fk_expense_lifecycle_events_claim",
        ),
        sa.CheckConstraint(
            "(request_id IS NOT NULL AND claim_id IS NULL) OR "
            "(request_id IS NULL AND claim_id IS NOT NULL)",
            name="ck_expense_lifecycle_events_one_subject",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_lifecycle_events_tenant_id",
        "expense_lifecycle_events",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_lifecycle_events_request",
        "expense_lifecycle_events",
        ["tenant_id", "request_id", "occurred_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_expense_lifecycle_events_claim",
        "expense_lifecycle_events",
        ["tenant_id", "claim_id", "occurred_at"],
        schema=_SCHEMA,
    )


def _install_security() -> None:
    op.execute("ALTER TABLE mod_expenses.expense_categories ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_expenses.expense_categories FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY expense_categories_tenant_isolation "
        "ON mod_expenses.expense_categories "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_categories TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_categories TO app_admin;"
    )
    op.execute("ALTER TABLE mod_expenses.expense_policies ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_expenses.expense_policies FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY expense_policies_tenant_isolation "
        "ON mod_expenses.expense_policies "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_policies TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_policies TO app_admin;"
    )
    op.execute(
        "ALTER TABLE mod_expenses.expense_policy_rules ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_expenses.expense_policy_rules FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY expense_policy_rules_tenant_isolation "
        "ON mod_expenses.expense_policy_rules "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_policy_rules TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_policy_rules TO app_admin;"
    )
    op.execute("ALTER TABLE mod_expenses.expense_requests ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_expenses.expense_requests FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY expense_requests_tenant_isolation "
        "ON mod_expenses.expense_requests "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_requests TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_requests TO app_admin;"
    )
    op.execute(
        "ALTER TABLE mod_expenses.expense_request_lines ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_expenses.expense_request_lines FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY expense_request_lines_tenant_isolation "
        "ON mod_expenses.expense_request_lines "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_request_lines TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_request_lines TO app_admin;"
    )
    op.execute("ALTER TABLE mod_expenses.expense_claims ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_expenses.expense_claims FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY expense_claims_tenant_isolation "
        "ON mod_expenses.expense_claims "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_claims TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_claims TO app_admin;"
    )
    op.execute(
        "ALTER TABLE mod_expenses.expense_claim_lines ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_expenses.expense_claim_lines FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY expense_claim_lines_tenant_isolation "
        "ON mod_expenses.expense_claim_lines "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_claim_lines TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_claim_lines TO app_admin;"
    )
    op.execute("ALTER TABLE mod_expenses.expense_receipts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_expenses.expense_receipts FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY expense_receipts_tenant_isolation "
        "ON mod_expenses.expense_receipts "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_receipts TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_receipts TO app_admin;"
    )
    op.execute(
        "ALTER TABLE mod_expenses.expense_policy_evaluations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_expenses.expense_policy_evaluations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY expense_policy_evaluations_tenant_isolation "
        "ON mod_expenses.expense_policy_evaluations "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_policy_evaluations TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_policy_evaluations TO app_admin;"
    )
    op.execute(
        "ALTER TABLE mod_expenses.expense_lifecycle_events ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_expenses.expense_lifecycle_events FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY expense_lifecycle_events_tenant_isolation "
        "ON mod_expenses.expense_lifecycle_events "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_lifecycle_events TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_expenses.expense_lifecycle_events TO app_admin;"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON mod_expenses.expense_policy_evaluations FROM app_user;"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON mod_expenses.expense_lifecycle_events FROM app_user;"
    )


def _install_immutability() -> None:
    op.execute(
        """
        CREATE FUNCTION mod_expenses.refuse_expense_evidence_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER expense_policy_evaluations_are_append_only
        BEFORE UPDATE OR DELETE ON mod_expenses.expense_policy_evaluations
        FOR EACH ROW EXECUTE FUNCTION mod_expenses.refuse_expense_evidence_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER expense_lifecycle_events_are_append_only
        BEFORE UPDATE OR DELETE ON mod_expenses.expense_lifecycle_events
        FOR EACH ROW EXECUTE FUNCTION mod_expenses.refuse_expense_evidence_mutation();
        """
    )

    op.execute(
        """
        CREATE FUNCTION mod_expenses.guard_published_expense_policy()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'published expense policy is immutable';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'published' THEN
                IF NEW.status <> 'retired'
                   OR NEW.tenant_id <> OLD.tenant_id
                   OR NEW.code <> OLD.code
                   OR NEW.name <> OLD.name
                   OR NEW.version <> OLD.version
                   OR NEW.currency_code <> OLD.currency_code
                   OR NEW.effective_from <> OLD.effective_from
                   OR NEW.effective_to IS DISTINCT FROM OLD.effective_to
                   OR NEW.description IS DISTINCT FROM OLD.description
                   OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
                    RAISE EXCEPTION 'published expense policy is immutable';
                END IF;
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'retired' THEN
                RAISE EXCEPTION 'retired expense policy is immutable';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER published_expense_policy_is_immutable
        BEFORE UPDATE OR DELETE ON mod_expenses.expense_policies
        FOR EACH ROW EXECUTE FUNCTION mod_expenses.guard_published_expense_policy();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_expenses.guard_expense_policy_rule()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            selected_policy uuid;
            selected_tenant uuid;
            policy_status text;
        BEGIN
            selected_policy := COALESCE(NEW.policy_id, OLD.policy_id);
            selected_tenant := COALESCE(NEW.tenant_id, OLD.tenant_id);
            SELECT status INTO policy_status
            FROM mod_expenses.expense_policies
            WHERE tenant_id = selected_tenant AND id = selected_policy;
            IF policy_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'published expense policy rules are immutable';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER published_expense_policy_rules_are_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON mod_expenses.expense_policy_rules
        FOR EACH ROW EXECUTE FUNCTION mod_expenses.guard_expense_policy_rule();
        """
    )

    op.execute(
        """
        CREATE FUNCTION mod_expenses.guard_submitted_expense_line()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent_status text;
        BEGIN
            IF TG_TABLE_NAME = 'expense_request_lines' THEN
                SELECT status INTO parent_status
                FROM mod_expenses.expense_requests
                WHERE tenant_id = COALESCE(NEW.tenant_id, OLD.tenant_id)
                  AND id = COALESCE(NEW.request_id, OLD.request_id);
                IF parent_status IS DISTINCT FROM 'draft' THEN
                    RAISE EXCEPTION 'submitted expense request lines are immutable';
                END IF;
            ELSE
                SELECT status INTO parent_status
                FROM mod_expenses.expense_claims
                WHERE tenant_id = COALESCE(NEW.tenant_id, OLD.tenant_id)
                  AND id = COALESCE(NEW.claim_id, OLD.claim_id);
                IF parent_status IS DISTINCT FROM 'draft' THEN
                    IF TG_OP = 'UPDATE'
                       AND parent_status = 'submitted'
                       AND NEW.tenant_id = OLD.tenant_id
                       AND NEW.id = OLD.id
                       AND NEW.claim_id = OLD.claim_id
                       AND NEW.category_id = OLD.category_id
                       AND NEW.sequence = OLD.sequence
                       AND NEW.description = OLD.description
                       AND NEW.claimed_amount = OLD.claimed_amount
                       AND NEW.expense_date = OLD.expense_date
                       AND NEW.vendor_name IS NOT DISTINCT FROM OLD.vendor_name
                       AND NEW.notes IS NOT DISTINCT FROM OLD.notes
                       AND NEW.created_at = OLD.created_at THEN
                        RETURN NEW;
                    END IF;
                    RAISE EXCEPTION 'submitted expense claim lines are immutable';
                END IF;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER submitted_expense_request_lines_are_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON mod_expenses.expense_request_lines
        FOR EACH ROW EXECUTE FUNCTION mod_expenses.guard_submitted_expense_line();
        """
    )
    op.execute(
        """
        CREATE TRIGGER submitted_expense_lines_are_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON mod_expenses.expense_claim_lines
        FOR EACH ROW EXECUTE FUNCTION mod_expenses.guard_submitted_expense_line();
        """
    )

    op.execute(
        """
        CREATE FUNCTION mod_expenses.guard_expense_receipt()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            selected_line uuid;
            selected_tenant uuid;
            parent_status text;
        BEGIN
            selected_line := COALESCE(NEW.claim_line_id, OLD.claim_line_id);
            selected_tenant := COALESCE(NEW.tenant_id, OLD.tenant_id);
            SELECT claim.status INTO parent_status
            FROM mod_expenses.expense_claim_lines AS line
            JOIN mod_expenses.expense_claims AS claim
              ON claim.tenant_id = line.tenant_id AND claim.id = line.claim_id
            WHERE line.tenant_id = selected_tenant AND line.id = selected_line;

            IF TG_OP = 'UPDATE'
               AND OLD.verification_status = 'pending'
               AND NEW.verification_status IN ('verified', 'rejected')
               AND NEW.verification_reference IS NOT NULL
               AND NEW.verified_at IS NOT NULL
               AND NEW.tenant_id = OLD.tenant_id
               AND NEW.id = OLD.id
               AND NEW.claim_line_id = OLD.claim_line_id
               AND NEW.file_id = OLD.file_id
               AND NEW.original_filename = OLD.original_filename
               AND NEW.media_type = OLD.media_type
               AND NEW.size_bytes = OLD.size_bytes
               AND NEW.sha256 = OLD.sha256
               AND NEW.receipt_number IS NOT DISTINCT FROM OLD.receipt_number
               AND NEW.merchant_name IS NOT DISTINCT FROM OLD.merchant_name
               AND NEW.issued_on IS NOT DISTINCT FROM OLD.issued_on
               AND NEW.gross_amount IS NOT DISTINCT FROM OLD.gross_amount
               AND NEW.currency_code IS NOT DISTINCT FROM OLD.currency_code
               AND NEW.created_at = OLD.created_at THEN
                RETURN NEW;
            END IF;
            IF parent_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'submitted expense receipt metadata is immutable';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'expense receipt metadata is immutable after creation';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER expense_receipt_metadata_is_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON mod_expenses.expense_receipts
        FOR EACH ROW EXECUTE FUNCTION mod_expenses.guard_expense_receipt();
        """
    )

    op.execute(
        """
        CREATE FUNCTION mod_expenses.guard_expense_request_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status = OLD.status THEN RETURN NEW; END IF;
            IF (OLD.status = 'draft' AND NEW.status IN ('submitted', 'cancelled'))
               OR (OLD.status = 'submitted' AND NEW.status IN ('approved', 'rejected', 'cancelled'))
               OR (OLD.status = 'approved' AND NEW.status = 'converted') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'invalid expense request transition % -> %', OLD.status, NEW.status;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER expense_request_transition_is_guarded
        BEFORE UPDATE OF status ON mod_expenses.expense_requests
        FOR EACH ROW EXECUTE FUNCTION mod_expenses.guard_expense_request_transition();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_expenses.guard_expense_claim_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status = OLD.status THEN RETURN NEW; END IF;
            IF (OLD.status = 'draft' AND NEW.status IN ('submitted', 'cancelled'))
               OR (OLD.status = 'submitted' AND NEW.status IN ('approved', 'rejected', 'cancelled'))
               OR (OLD.status = 'rejected' AND NEW.status = 'draft')
               OR (OLD.status = 'approved' AND NEW.status = 'approval_withdrawn') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'invalid expense claim transition % -> %', OLD.status, NEW.status;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER expense_claim_transition_is_guarded
        BEFORE UPDATE OF status ON mod_expenses.expense_claims
        FOR EACH ROW EXECUTE FUNCTION mod_expenses.guard_expense_claim_transition();
        """
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_expenses;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_expenses TO app_user, platform_api, app_admin;"
    )
    _create_categories()
    _create_policies()
    _create_requests()
    _create_claims()
    _create_receipts()
    _create_evidence()
    _install_security()
    _install_immutability()


def downgrade() -> None:
    op.execute("DROP FUNCTION mod_expenses.guard_expense_claim_transition() CASCADE;")
    op.execute("DROP FUNCTION mod_expenses.guard_expense_request_transition() CASCADE;")
    op.execute("DROP FUNCTION mod_expenses.guard_expense_receipt() CASCADE;")
    op.execute("DROP FUNCTION mod_expenses.guard_submitted_expense_line() CASCADE;")
    op.execute("DROP FUNCTION mod_expenses.guard_expense_policy_rule() CASCADE;")
    op.execute("DROP FUNCTION mod_expenses.guard_published_expense_policy() CASCADE;")
    op.execute("DROP FUNCTION mod_expenses.refuse_expense_evidence_mutation() CASCADE;")
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA mod_expenses;")
