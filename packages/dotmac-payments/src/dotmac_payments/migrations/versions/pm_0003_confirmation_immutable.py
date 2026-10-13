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


def _bound_lock() -> None:
    op.execute("/* mod_payments guard transition */ SET LOCAL lock_timeout = '5s'")
    op.execute(
        "/* mod_payments guard transition */ SET LOCAL statement_timeout = '60s'"
    )
    # Serialize concurrent inserts with the downgrade evidence decision as well
    # as mutations with the trigger/privilege transition. Fail rather than wait
    # indefinitely behind a live writer. Alembic owns the DDL transaction.
    op.execute("LOCK TABLE mod_payments.payment_confirmations IN ACCESS EXCLUSIVE MODE")


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
    _bound_lock()
    # Never mistake a tenant-filtered empty result for absence of evidence.
    # A non-BYPASSRLS migration owner fails closed under FORCE ROW LEVEL SECURITY.
    op.execute("/* mod_payments evidence check */ SET LOCAL row_security = off")
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
