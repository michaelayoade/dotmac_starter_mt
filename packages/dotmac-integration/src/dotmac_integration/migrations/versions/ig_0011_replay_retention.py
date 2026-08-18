"""Give replay evidence a finite lifetime without erasing legal-hold history.

The 180-day replay-evidence decision requires old, closed receipts to become
deletable.  ``ig_0006`` made ``receipt_legal_holds.receipt_id`` a cascading
foreign key, so deleting a receipt would also delete the evidence that someone
had forbidden its destruction.  That is the opposite of a legal-hold ledger.

This revision removes the cascading FK and replaces its useful guarantees with
two triggers:

* a hold may only be inserted or repointed to a receipt that exists;
* a receipt with an active hold may not be deleted.

Once a hold is released, the receipt may age out while the hold row keeps the
exact receipt UUID and its placement/release history.  The module service owns
the deletion predicate; the triggers make the two legal invariants survive a
direct SQL write as well.

Revision ID: ig_0011_replay_retention
Revises: ig_0010_shadow_evidence
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

revision = "ig_0011_replay_retention"
down_revision = "ig_0010_shadow_evidence"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_HOLDS = "receipt_legal_holds"


def upgrade() -> None:
    op.drop_constraint(
        "fk_receipt_legal_holds_receipt",
        _HOLDS,
        schema=_SCHEMA,
        type_="foreignkey",
    )

    op.execute(
        """
        CREATE FUNCTION mod_intg.require_receipt_for_legal_hold()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM mod_intg.inbox_receipts
                WHERE id = NEW.receipt_id
            ) THEN
                RAISE EXCEPTION 'receipt_legal_hold_requires_live_receipt'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_receipt_legal_hold_requires_receipt
        BEFORE INSERT OR UPDATE OF receipt_id
        ON mod_intg.receipt_legal_holds
        FOR EACH ROW
        EXECUTE FUNCTION mod_intg.require_receipt_for_legal_hold();
        """
    )

    op.execute(
        """
        CREATE FUNCTION mod_intg.refuse_active_held_receipt_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM mod_intg.receipt_legal_holds
                WHERE receipt_id = OLD.id AND released_at IS NULL
            ) THEN
                RAISE EXCEPTION 'active_legal_hold_refuses_receipt_delete'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            RETURN OLD;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_active_legal_hold_refuses_receipt_delete
        BEFORE DELETE ON mod_intg.inbox_receipts
        FOR EACH ROW
        EXECUTE FUNCTION mod_intg.refuse_active_held_receipt_delete();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_active_legal_hold_refuses_receipt_delete "
        "ON mod_intg.inbox_receipts;"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_intg.refuse_active_held_receipt_delete();")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_receipt_legal_hold_requires_receipt "
        "ON mod_intg.receipt_legal_holds;"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_intg.require_receipt_for_legal_hold();")
    # If evidence was already purged, released hold rows deliberately outlive
    # their receipts and this downgrade refuses rather than deleting history to
    # make the old cascading constraint fit again.
    op.create_foreign_key(
        "fk_receipt_legal_holds_receipt",
        _HOLDS,
        "inbox_receipts",
        ["receipt_id"],
        ["id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="CASCADE",
    )
