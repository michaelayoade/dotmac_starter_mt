"""Provide the canonical tenant-qualified credential key for product policy FKs.

Revision ID: 0030_credential_tenant_key
Revises: 0029_audit_history_shape
Create Date: 2026-10-17

The existing id primary key remains unchanged. Product-owned credential policy
extensions can reference (tenant_id, id) without storing a redundant party_id;
the credential remains the authority for its party. No credential/hash/session
values, lifecycle behavior, grants or RLS policies change.
"""

from __future__ import annotations

from alembic import op

revision = "0030_credential_tenant_key"
down_revision = "0029_audit_history_shape"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_user_credentials_tenant_id_id",
        "user_credentials",
        ["tenant_id", "id"],
        schema="public",
    )


def downgrade() -> None:
    # Dependent product FKs must be retired by their own lineage first. Normal
    # PostgreSQL RESTRICT refuses the drop; never cascade into product storage.
    op.drop_constraint(
        "uq_user_credentials_tenant_id_id",
        "user_credentials",
        schema="public",
        type_="unique",
    )
