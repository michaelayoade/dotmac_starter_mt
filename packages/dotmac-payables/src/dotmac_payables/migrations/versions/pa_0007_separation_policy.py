"""Optional separation of duties: the policy in force is stamped on each payment.

Revision ID: pa_0007_separation_policy
Revises: pa_0006_tax_release_hist_credit

D-9 (Michael's no-compulsory-two-person ruling): separation of duties is an
optional per-tenant policy. ``supplier_payments.separation_enforced`` records the
policy in force when the payment was prepared. It defaults to true, so every
existing row and every writer that does not name the policy stays strict.

The ``ck_supplier_payments_sod`` CHECK is rewritten to
``NOT separation_enforced OR (<a6 predicate>)``: the database relaxes the
separation rule for a row ONLY when that row's stamp is false. An UPDATE trigger
forbids changing the stamp after the fact. Downgrade refuses while any row is
unstamped-strict-false, because restoring the unconditional predicate could
violate the rows that were legitimately written under the relaxed policy.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "pa_0007_separation_policy"
down_revision = "pa_0006_tax_release_hist_credit"
branch_labels = None
depends_on = None

_SCHEMA = "mod_payables"

# Frozen copies (a released migration must not change if the model later does).
_V2_SOD_PREDICATE = (
    "(approved_by IS NULL OR lower(approved_by) <> lower(prepared_by))"
    " AND (recorded_by IS NULL OR (lower(recorded_by) <> lower(prepared_by)"
    " AND lower(recorded_by) <> lower(approved_by)))"
    " AND (reversed_by IS NULL OR (lower(reversed_by) <> lower(recorded_by)"
    " AND lower(reversed_by) <> lower(approved_by)))"
)
SUPPLIER_PAYMENT_SOD_CHECK = "NOT separation_enforced OR (" + _V2_SOD_PREDICATE + ")"

_FLAG_FUNCTION = """
        CREATE FUNCTION mod_payables.protect_supplier_payment_policy()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.separation_enforced IS DISTINCT FROM OLD.separation_enforced THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='the separation-of-duties policy stamped on a supplier payment cannot be changed';
          END IF;
          RETURN NEW;
        END; $$;
"""


def _swap_sod_check(check: str) -> None:
    op.drop_constraint("ck_supplier_payments_sod", "supplier_payments", schema=_SCHEMA)
    op.create_check_constraint(
        "ck_supplier_payments_sod", "supplier_payments", check, schema=_SCHEMA
    )


def upgrade() -> None:
    op.add_column(
        "supplier_payments",
        sa.Column(
            "separation_enforced",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        schema=_SCHEMA,
    )
    _swap_sod_check(SUPPLIER_PAYMENT_SOD_CHECK)
    op.execute(_FLAG_FUNCTION)
    op.execute(
        "REVOKE ALL ON FUNCTION mod_payables.protect_supplier_payment_policy() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER supplier_payments_guard_policy BEFORE UPDATE ON mod_payables.supplier_payments FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_supplier_payment_policy();"
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Block concurrent inserts between the check and the constraint swap.
    op.execute("LOCK TABLE mod_payables.supplier_payments IN SHARE ROW EXCLUSIVE MODE")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM mod_payables.supplier_payments"
            " WHERE NOT separation_enforced)"
        )
    ):
        raise RuntimeError(
            "supplier payments written under a relaxed separation-of-duties "
            "policy require forward correction"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS supplier_payments_guard_policy ON mod_payables.supplier_payments;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mod_payables.protect_supplier_payment_policy();"
    )
    _swap_sod_check(_V2_SOD_PREDICATE)
    op.drop_column("supplier_payments", "separation_enforced", schema=_SCHEMA)
