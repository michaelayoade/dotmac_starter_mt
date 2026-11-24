"""Expand campaign revisions with an optional immutable delivery deadline.

Revision ID: ca_0002_delivery_deadline
Revises: ca_0001_campaigns
Create Date: 2026-11-23
"""

import sqlalchemy as sa

from alembic import op

revision = "ca_0002_delivery_deadline"
down_revision = "ca_0001_campaigns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows stay unbounded. The existing revision snapshot trigger,
    # tenant-composite keys, grants and forced RLS continue to own this column.
    op.add_column(
        "campaign_revisions",
        sa.Column("delivery_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="mod_campaigns",
    )
    # ca_0001 is published and immutable. Repair its shared trigger here:
    # table-specific record fields must only be prepared in that table's
    # branch, not in an AND expression PostgreSQL may prepare for other rows.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_campaigns.protect_campaign_snapshot()
        RETURNS trigger AS $$
        DECLARE
            owning_campaign uuid;
            lifecycle text;
        BEGIN
            owning_campaign := CASE WHEN TG_OP = 'DELETE'
                                    THEN OLD.campaign_id ELSE NEW.campaign_id END;
            SELECT status INTO lifecycle
              FROM mod_campaigns.campaigns
             WHERE tenant_id = CASE WHEN TG_OP = 'DELETE'
                                    THEN OLD.tenant_id ELSE NEW.tenant_id END
               AND id = owning_campaign;
            IF lifecycle = 'draft' THEN
                RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END IF;
            IF TG_TABLE_NAME = 'campaign_recipients' AND TG_OP = 'UPDATE' THEN
                IF (to_jsonb(NEW) - ARRAY['address','context','scrubbed_at']::text[])
                     = (to_jsonb(OLD) - ARRAY['address','context','scrubbed_at']::text[])
                   AND NEW.address IS NULL
                   AND NEW.context = '{}'::jsonb
                   AND NEW.scrubbed_at IS NOT NULL THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'campaign snapshot is immutable once sending begins'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Removing a campaign delivery deadline would widen permission")
