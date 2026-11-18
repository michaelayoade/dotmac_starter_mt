"""Dispatch identity: the dedupe key and rendered snapshot (ADR-0006 § 5g, K-3).

Creates `communication_dispatches`: one row per outbound message identity, holding
the product-supplied idempotency key (UNIQUE per tenant), the rendered snapshot
(S6 `OutboundMessage` parts), its content fingerprint and template provenance.

Deliberately NOT a queue. The kernel outbox stays the queue; the outbox event for
a dispatch carries only `{"dispatch_id": ...}`.

Privilege posture (this is the point of the migration):
- tenant RLS with FORCE, same policy shape as `communication_deliveries` (0020);
- `app_user`: SELECT, INSERT, and column-level UPDATE on the outcome/redaction
  columns only. No DELETE, no UPDATE of key, address, fingerprint or provenance;
- `outbox_dispatcher`: NOTHING (explicitly revoked here; its only door is the
  claim/settle SECURITY DEFINER functions, and rendered bodies are personal data);
- the Academy "lab worker" role concept: NOTHING (it is not granted here);
- `platform_api`: nothing (this is tenant data).

Additive. Downgrade drops the table, which is only safe while it is empty
(working down = drop on an empty table); it refuses otherwise rather than lose
the only record of which legacy keys were already sent.

Revision ID: 0032_communication_dispatches
Revises: 0031_rate_limit_buckets
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0032_communication_dispatches"
down_revision = "0031_rate_limit_buckets"
branch_labels = None
depends_on = None

_TABLE = "communication_dispatches"
_JSON = postgresql.JSONB(none_as_null=True)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("dispatch_id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("address", sa.String(320), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("text_body", sa.Text(), nullable=True),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column("headers", _JSON, nullable=True),
        sa.Column("attachments", _JSON, nullable=True),
        sa.Column("content_fingerprint", sa.String(64), nullable=True),
        sa.Column("template_provenance", sa.String(255), nullable=False),
        sa.Column("template_revision_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_communication_dispatches_tenant",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'dead', 'suppressed', "
            "'history')",
            name="ck_communication_dispatches_status",
        ),
        sa.CheckConstraint(
            "status <> 'history' OR (address IS NULL AND subject IS NULL "
            "AND text_body IS NULL AND html_body IS NULL AND headers IS NULL "
            "AND attachments IS NULL AND content_fingerprint IS NULL)",
            name="ck_communication_dispatches_history_bare",
        ),
        sa.CheckConstraint(
            "status = 'history' OR (address IS NOT NULL "
            "AND content_fingerprint IS NOT NULL)",
            name="ck_communication_dispatches_content_identity",
        ),
        sa.CheckConstraint(
            "status = 'history' OR redacted_at IS NOT NULL "
            "OR text_body IS NOT NULL OR html_body IS NOT NULL",
            name="ck_communication_dispatches_has_body",
        ),
        sa.CheckConstraint(
            "redacted_at IS NULL OR status <> 'pending'",
            name="ck_communication_dispatches_redact_terminal",
        ),
    )
    op.create_index(
        "uq_communication_dispatches_tenant_key",
        _TABLE,
        ["tenant_id", "idempotency_key"],
        unique=True,
    )
    op.create_index("ix_communication_dispatches_tenant_id", _TABLE, ["tenant_id"])

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
          USING (tenant_id = app_current_tenant_id())
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(f"REVOKE ALL ON {_TABLE} FROM PUBLIC;")
    op.execute(f"REVOKE ALL ON {_TABLE} FROM outbox_dispatcher;")
    op.execute(f"GRANT SELECT, INSERT ON {_TABLE} TO app_user;")
    op.execute(
        f"GRANT UPDATE (status, terminal_at, redacted_at, subject, text_body, "
        f"html_body, headers, attachments) ON {_TABLE} TO app_user;"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {_TABLE})")).scalar():
        raise RuntimeError(
            f"refusing to drop non-empty {_TABLE}: it is the only record of "
            "dispatch identities (including imported legacy keys)"
        )
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE};")
    op.drop_index("ix_communication_dispatches_tenant_id", table_name=_TABLE)
    op.drop_index("uq_communication_dispatches_tenant_key", table_name=_TABLE)
    op.drop_table(_TABLE)
