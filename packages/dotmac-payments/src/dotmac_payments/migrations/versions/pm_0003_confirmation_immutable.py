"""Make confirmed cash evidence append-only at the database boundary.

Revision ID: pm_0003_confirmation_immutable
Revises: pm_0002_money_precision
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "pm_0003_confirmation_immutable"
down_revision = "pm_0002_money_precision"
branch_labels = None
depends_on = None


def _bound_lock(*, downgrade: bool = False) -> None:
    connection = op.get_bind()
    previous_timeout = connection.execute(sa.text("SHOW lock_timeout")).scalar_one()
    previous_statement_timeout = connection.execute(
        sa.text("SHOW statement_timeout")
    ).scalar_one()
    # Serialize concurrent inserts with the downgrade evidence decision as well
    # as mutations with the trigger/privilege transition. Fail rather than wait
    # indefinitely behind a live writer. Alembic owns the DDL transaction.
    if downgrade:
        # Never mistake a tenant-filtered empty result for absence of evidence.
        # A non-BYPASSRLS owner fails closed under FORCE ROW LEVEL SECURITY.
        op.execute("""
            SET LOCAL lock_timeout = '5s';
            SET LOCAL statement_timeout = '60s';
            SET LOCAL row_security = off;
            LOCK TABLE mod_payments.payment_confirmations IN ACCESS EXCLUSIVE MODE;
        """)
    else:
        op.execute("""
            SET LOCAL lock_timeout = '5s';
            SET LOCAL statement_timeout = '60s';
            LOCK TABLE mod_payments.payment_confirmations IN ACCESS EXCLUSIVE MODE;
        """)
    # Settings belong to the caller; the transaction retains the table lock
    # through the trigger/privilege transition after restoring both bounds.
    connection.execute(
        sa.text(
            "SELECT pg_catalog.set_config('lock_timeout', :timeout, true), "
            "pg_catalog.set_config('statement_timeout', :statement_timeout, true)"
        ),
        {
            "timeout": previous_timeout,
            "statement_timeout": previous_statement_timeout,
        },
    )


def upgrade() -> None:
    _bound_lock()
    op.execute(
        """
        CREATE FUNCTION mod_payments.refuse_confirmation_mutation()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'payment confirmation evidence is immutable; % refused', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER payment_confirmations_immutable "
        "BEFORE UPDATE OR DELETE ON mod_payments.payment_confirmations "
        "FOR EACH ROW EXECUTE FUNCTION mod_payments.refuse_confirmation_mutation()"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON mod_payments.payment_confirmations FROM app_user"
    )


def downgrade() -> None:
    _bound_lock(downgrade=True)
    evidence = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM mod_payments.payment_confirmations LIMIT 1"))
        .first()
    )
    if evidence is not None:
        raise RuntimeError(
            "cannot downgrade Payments while confirmation evidence exists"
        )
    op.execute(
        "DROP TRIGGER payment_confirmations_immutable ON mod_payments.payment_confirmations"
    )
    op.execute("DROP FUNCTION mod_payments.refuse_confirmation_mutation()")
    op.execute("GRANT UPDATE, DELETE ON mod_payments.payment_confirmations TO app_user")
