"""Shared fixed-window counters for the anonymous-route rate limit.

Revision ID: 0031_rate_limit_buckets
Revises: 0030_credential_tenant_key
Create Date: 2026-11-09

`rate_limit_buckets` is the kernel Postgres store behind the per-route
anonymous rate limit (`dotmac_kernel.middleware.rate_limit.PostgresRateLimitStore`).
A process-local counter cannot enforce a limit across workers or nodes, which is
why production refuses the in-memory store for these routes.

Platform catalog posture, like `platform_idempotency_records` (0018): NO
`tenant_id`, NO RLS (a bucket is a shared counter written before any tenant
session exists; the tenant is part of the hashed key), GRANTed to
`platform_api`/`app_admin` and REVOKEd from `app_user`. `key_hash` is a SHA-256
digest of the limiter key; the table never holds a client address in the clear.
Additive only: no existing table, row, grant or policy changes. Rows are
transient (`expires_at`) and the store prunes them opportunistically.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031_rate_limit_buckets"
down_revision = "0030_credential_tenant_key"
branch_labels = None
depends_on = None

_TABLE = "public.rate_limit_buckets"


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "key_hash", "window_start", name="pk_rate_limit_buckets"
        ),
        sa.CheckConstraint("hit_count >= 1", name="ck_rate_limit_buckets_hit_count"),
        schema="public",
    )
    op.create_index(
        "ix_rate_limit_buckets_expires_at",
        "rate_limit_buckets",
        ["expires_at"],
        schema="public",
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_admin;")
    op.execute(f"REVOKE ALL ON {_TABLE} FROM app_user;")


def downgrade() -> None:
    # Counters are transient and hold no evidence; dropping them is lossless in
    # effect. The anonymous limit then has no shared store and a production
    # deployment configured for it refuses to start.
    op.drop_index(
        "ix_rate_limit_buckets_expires_at",
        table_name="rate_limit_buckets",
        schema="public",
    )
    op.drop_table("rate_limit_buckets", schema="public")
