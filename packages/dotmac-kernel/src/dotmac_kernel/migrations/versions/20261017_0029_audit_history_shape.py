"""Preserve the existing audit history's width and unknown persistence time.

Revision ID: 0029_audit_history_shape
Revises: 0028_machine_attribution
Create Date: 2026-10-17

Only storage shape changes here: no history import, fabricated timestamps,
default changes, or changes to the ordinary writer's validation contract.
"""

from __future__ import annotations

from alembic import op

revision = "0029_audit_history_shape"
down_revision = "0028_machine_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.audit_events "
        "ALTER COLUMN entity_type TYPE VARCHAR(160), "
        "ALTER COLUMN created_at DROP NOT NULL"
    )


def downgrade() -> None:
    # Keep the preflight and DDL atomic against concurrent inserts. Alembic's
    # caller owns the transaction; this function never commits or repairs data.
    op.execute("LOCK TABLE public.audit_events IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.audit_events WHERE created_at IS NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade audit history: created_at contains NULL; original persistence time must not be fabricated';
            END IF;
            IF EXISTS (SELECT 1 FROM public.audit_events WHERE char_length(entity_type) > 120) THEN
                RAISE EXCEPTION 'Cannot downgrade audit history: entity_type exceeds 120 characters; history must not be truncated';
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        "ALTER TABLE public.audit_events "
        "ALTER COLUMN entity_type TYPE VARCHAR(120), "
        "ALTER COLUMN created_at SET NOT NULL"
    )
