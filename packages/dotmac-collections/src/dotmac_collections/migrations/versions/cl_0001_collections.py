"""Create the tenant-only Collections policy, case, and evidence plane.

Revision ID: cl_0001_collections
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "cl_0001_collections"
down_revision = None
branch_labels = ("collections",)

MODULE_CODE = "collections"
COMMON_REQUIRES = ("module_database_roles.v1", "outbox_relay.v1")
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
PLATFORM_REQUIRES = ()
TENANT_TABLES = (
    "collection_policies",
    "collection_policy_versions",
    "collection_policy_steps",
    "collection_cases",
    "collection_case_exposures",
    "collection_case_transitions",
    "collection_step_attempts",
    "payment_arrangements",
    "payment_arrangement_exposures",
    "payment_arrangement_installments",
    "payment_arrangement_settlement_receipts",
    "collection_grace_grants",
    "collection_notice_requests",
    "collection_notice_receipts",
    "collection_action_requests",
    "collection_action_receipts",
    "collection_reconciliations",
)

depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    module=MODULE_CODE,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
)

_SCHEMA = "mod_coll"
_MONEY = sa.Numeric(20, 6)


def _upgrade_tenant_plane() -> None:
    op.create_table(
        "collection_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("policy_code", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_collection_policies_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "policy_code", name="uq_collection_policies_code"
        ),
        sa.CheckConstraint("policy_code <> ''", name="ck_collection_policies_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_policy_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("collection_timing", sa.String(20), nullable=False),
        sa.Column("grace", sa.JSON()),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("publication_reason", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version_fingerprint", sa.String(71), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "mod_coll.collection_policies.tenant_id",
                "mod_coll.collection_policies.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_policy_versions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "policy_id",
            "version",
            name="uq_collection_policy_versions_number",
        ),
        sa.CheckConstraint("version > 0", name="ck_collection_policy_versions_version"),
        sa.CheckConstraint(
            "collection_timing IN ('advance', 'arrears')",
            name="ck_collection_policy_versions_timing",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_policy_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("step_code", sa.String(120), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("offset_seconds", sa.Integer(), nullable=False),
        sa.Column("offset_anchor", sa.String(50), nullable=False),
        sa.Column("request_kind", sa.String(20), nullable=False),
        sa.Column("action_code", sa.String(120)),
        sa.Column("receipt_required", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_version_id"],
            [
                "mod_coll.collection_policy_versions.tenant_id",
                "mod_coll.collection_policy_versions.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_policy_steps_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "policy_version_id",
            "ordinal",
            name="uq_collection_policy_steps_ordinal",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "policy_version_id",
            "step_code",
            name="uq_collection_policy_steps_code",
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_collection_policy_steps_ordinal"),
        sa.CheckConstraint(
            "offset_seconds >= 0", name="ck_collection_policy_steps_offset"
        ),
        sa.CheckConstraint(
            "offset_anchor IN ('exposure_at', 'request_at', "
            "'accepted_notice_receipt_at')",
            name="ck_collection_policy_steps_anchor",
        ),
        sa.CheckConstraint(
            "(request_kind = 'notice' AND action_code IS NULL) OR "
            "(request_kind = 'action' AND action_code IS NOT NULL)",
            name="ck_collection_policy_steps_request",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("exposure_ref", sa.String(255), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("service_ref", sa.String(255)),
        sa.Column("collection_timing", sa.String(20), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("lifecycle", sa.String(32), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("position_fingerprint", sa.String(255), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_version_id"],
            [
                "mod_coll.collection_policy_versions.tenant_id",
                "mod_coll.collection_policy_versions.id",
            ],
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_collection_cases_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "exposure_ref",
            name="uq_collection_cases_exposure",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('open', 'paused', 'resolved', 'cancelled')",
            name="ck_collection_cases_lifecycle",
        ),
        sa.CheckConstraint("source_version > 0", name="ck_collection_cases_version"),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_case_exposures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("exposure_ref", sa.String(255), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("position_fingerprint", sa.String(255), nullable=False),
        sa.Column("position_snapshot", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["mod_coll.collection_cases.tenant_id", "mod_coll.collection_cases.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_case_exposures_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "source_version",
            name="uq_collection_case_exposures_version",
        ),
        sa.CheckConstraint(
            "source_version > 0", name="ck_collection_case_exposures_version"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_case_transitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("transition_ordinal", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(32)),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["mod_coll.collection_cases.tenant_id", "mod_coll.collection_cases.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_case_transitions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "transition_ordinal",
            name="uq_collection_case_transitions_ordinal",
        ),
        sa.CheckConstraint(
            "transition_ordinal > 0", name="ck_collection_case_transitions_ordinal"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_step_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("policy_step_code", sa.String(120), nullable=False),
        sa.Column("attempt_ordinal", sa.Integer(), nullable=False),
        sa.Column("request_kind", sa.String(20), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("decision_fingerprint", sa.String(255), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["mod_coll.collection_cases.tenant_id", "mod_coll.collection_cases.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_step_attempts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "policy_step_code",
            "attempt_ordinal",
            name="uq_collection_step_attempts_attempt",
        ),
        sa.CheckConstraint("attempt_ordinal > 0", name="ck_collection_step_attempts_ordinal"),
        sa.CheckConstraint(
            "request_kind IN ('notice', 'action')",
            name="ck_collection_step_attempts_kind",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "payment_arrangements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("arrangement_ref", sa.String(255), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("lifecycle", sa.String(32), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_payment_arrangements_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "arrangement_ref", name="uq_payment_arrangements_ref"
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_payment_arrangements_currency",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_payment_arrangements_precision"
        ),
        sa.CheckConstraint(
            "lifecycle IN ('proposed', 'accepted', 'completed', 'cancelled', 'defaulted')",
            name="ck_payment_arrangements_lifecycle",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "payment_arrangement_exposures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("arrangement_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("exposure_ref", sa.String(255), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("position_fingerprint", sa.String(255), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("service_ref", sa.String(255)),
        sa.Column("admitted_amount", _MONEY, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "arrangement_id"],
            [
                "mod_coll.payment_arrangements.tenant_id",
                "mod_coll.payment_arrangements.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_payment_arrangement_exposures_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "source_owner",
            "exposure_ref",
            name="uq_payment_arrangement_exposures_member",
        ),
        sa.CheckConstraint(
            "source_version > 0", name="ck_payment_arrangement_exposures_version"
        ),
        sa.CheckConstraint(
            "admitted_amount >= 0", name="ck_payment_arrangement_exposures_amount"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "payment_arrangement_installments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("arrangement_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "arrangement_id"],
            [
                "mod_coll.payment_arrangements.tenant_id",
                "mod_coll.payment_arrangements.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_payment_arrangement_installments_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "ordinal",
            name="uq_payment_arrangement_installments_ordinal",
        ),
        sa.CheckConstraint(
            "ordinal > 0", name="ck_payment_arrangement_installments_ordinal"
        ),
        sa.CheckConstraint("amount > 0", name="ck_payment_arrangement_installments_amount"),
        schema=_SCHEMA,
    )
    op.create_table(
        "payment_arrangement_settlement_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("arrangement_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("settlement_ref", sa.String(255), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("receipt_fingerprint", sa.String(255), nullable=False),
        sa.Column("settled_amount", _MONEY, nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "arrangement_id"],
            [
                "mod_coll.payment_arrangements.tenant_id",
                "mod_coll.payment_arrangements.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_payment_arrangement_settlement_receipts_tenant_id_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "source_owner",
            "settlement_ref",
            name="uq_payment_arrangement_settlement_receipts_source",
        ),
        sa.CheckConstraint(
            "source_version > 0",
            name="ck_payment_arrangement_settlement_receipts_version",
        ),
        sa.CheckConstraint(
            "settled_amount > 0",
            name="ck_payment_arrangement_settlement_receipts_amount",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_grace_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("anchor_kind", sa.String(50), nullable=False),
        sa.Column("anchor_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["mod_coll.collection_cases.tenant_id", "mod_coll.collection_cases.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_grace_grants_tenant_id_id"
        ),
        sa.CheckConstraint(
            "duration_seconds >= 0", name="ck_collection_grace_grants_duration"
        ),
        sa.CheckConstraint(
            "anchor_kind IN ('exposure_at', 'request_at', "
            "'accepted_notice_receipt_at')",
            name="ck_collection_grace_grants_anchor",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_notice_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(255), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("policy_step_code", sa.String(120), nullable=False),
        sa.Column("attempt_ordinal", sa.Integer(), nullable=False),
        sa.Column("purpose_code", sa.String(120), nullable=False),
        sa.Column("decision_evidence", sa.JSON(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["mod_coll.collection_cases.tenant_id", "mod_coll.collection_cases.id"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_notice_requests_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_collection_notice_requests_key"
        ),
        sa.CheckConstraint(
            "attempt_ordinal > 0", name="ck_collection_notice_requests_ordinal"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_notice_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_kind", sa.String(32), nullable=False),
        sa.Column("owner_code", sa.String(120), nullable=False),
        sa.Column("owner_receipt_id", sa.String(255), nullable=False),
        sa.Column("receipt_fingerprint", sa.String(255), nullable=False),
        sa.Column("receipt_evidence", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                "mod_coll.collection_notice_requests.tenant_id",
                "mod_coll.collection_notice_requests.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_notice_receipts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "request_id", name="uq_collection_notice_receipts_request"
        ),
        sa.CheckConstraint(
            "receipt_kind IN ('accepted', 'suppressed', 'unavailable', 'failed')",
            name="ck_collection_notice_receipts_kind",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_action_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(255), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("policy_step_code", sa.String(120), nullable=False),
        sa.Column("attempt_ordinal", sa.Integer(), nullable=False),
        sa.Column("action_code", sa.String(120), nullable=False),
        sa.Column("effect_scope", sa.String(120), nullable=False),
        sa.Column("decision_evidence", sa.JSON(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["mod_coll.collection_cases.tenant_id", "mod_coll.collection_cases.id"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_action_requests_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_collection_action_requests_key"
        ),
        sa.CheckConstraint(
            "attempt_ordinal > 0", name="ck_collection_action_requests_ordinal"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_action_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_kind", sa.String(32), nullable=False),
        sa.Column("owner_code", sa.String(120), nullable=False),
        sa.Column("owner_receipt_id", sa.String(255), nullable=False),
        sa.Column("receipt_fingerprint", sa.String(255), nullable=False),
        sa.Column("receipt_evidence", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                "mod_coll.collection_action_requests.tenant_id",
                "mod_coll.collection_action_requests.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_action_receipts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "request_id", name="uq_collection_action_receipts_request"
        ),
        sa.CheckConstraint(
            "receipt_kind IN ('applied', 'refused', 'deferred', 'failed')",
            name="ck_collection_action_receipts_kind",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_reconciliations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("exposure_ref", sa.String(255), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("source_fingerprint", sa.String(255), nullable=False),
        sa.Column("rebuilt_fingerprint", sa.String(255), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["mod_coll.collection_cases.tenant_id", "mod_coll.collection_cases.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_reconciliations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "source_version",
            name="uq_collection_reconciliations_version",
        ),
        sa.CheckConstraint(
            "source_version > 0", name="ck_collection_reconciliations_version"
        ),
        sa.CheckConstraint(
            "outcome IN ('match', 'drift')", name="ck_collection_reconciliations_outcome"
        ),
        schema=_SCHEMA,
    )


def _install_security() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_coll.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_coll.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_coll.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(f"REVOKE ALL ON mod_coll.{table} FROM PUBLIC;")
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_coll.{table} TO app_user;"
        )
    op.execute("GRANT USAGE ON SCHEMA mod_coll TO app_user;")


def _install_immutability() -> None:
    op.execute(
        """
        CREATE FUNCTION mod_coll.refuse_fact_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'mod_coll.% is immutable; % refused', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    immutable_tables = (
        "collection_policy_versions",
        "collection_policy_steps",
        "collection_case_exposures",
        "collection_case_transitions",
        "collection_step_attempts",
        "payment_arrangement_exposures",
        "payment_arrangement_installments",
        "payment_arrangement_settlement_receipts",
        "collection_grace_grants",
        "collection_notice_requests",
        "collection_notice_receipts",
        "collection_action_requests",
        "collection_action_receipts",
        "collection_reconciliations",
    )
    for table in immutable_tables:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE "
            f"ON mod_coll.{table} FOR EACH ROW "
            "EXECUTE FUNCTION mod_coll.refuse_fact_mutation();"
        )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), COMMON_REQUIRES)
    require_prerequisites(op.get_bind(), TENANT_REQUIRES)
    op.execute("CREATE SCHEMA mod_coll;")
    op.execute("GRANT USAGE ON SCHEMA mod_coll TO app_admin;")
    _upgrade_tenant_plane()
    _install_security()
    _install_immutability()


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_coll.refuse_fact_mutation();")
    op.execute("DROP SCHEMA mod_coll;")
