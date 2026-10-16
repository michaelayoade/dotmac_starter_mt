"""Permit purchase funding with an exact, immutable opaque destination.

Revision ID: pm_0004_purchase_funding
Revises: pm_0003_confirmation_immutable
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "pm_0004_purchase_funding"
down_revision = "pm_0003_confirmation_immutable"
branch_labels = None
depends_on = None

_SCHEMA = "mod_payments"
_TABLE = "payment_intents"
_PREVIOUS_PURPOSES = "'INVOICE_SETTLEMENT', 'ACCOUNT_CREDIT_DEPOSIT', 'SERVICE_FEE'"


def _lock(*, downgrade: bool = False) -> dict[str, str]:
    connection = op.get_bind()
    settings = {
        name: connection.execute(sa.text(f"SHOW {name}")).scalar_one()
        for name in ("lock_timeout", "statement_timeout", "row_security")
    }
    if downgrade:
        # FORCE RLS must never hide funding rows from the downgrade decision.
        # A role unable to bypass RLS fails rather than reporting false absence.
        op.execute("""
            SET LOCAL lock_timeout = '5s';
            SET LOCAL statement_timeout = '60s';
            SET LOCAL row_security = off;
            LOCK TABLE mod_payments.payment_intents IN ACCESS EXCLUSIVE MODE;
        """)
    else:
        op.execute("""
            SET LOCAL lock_timeout = '5s';
            SET LOCAL statement_timeout = '60s';
            LOCK TABLE mod_payments.payment_intents IN ACCESS EXCLUSIVE MODE;
        """)
    return settings


def _restore(settings: dict[str, str]) -> None:
    for name, value in settings.items():
        op.get_bind().execute(
            sa.text("SELECT pg_catalog.set_config(:name, :value, true)"),
            {"name": name, "value": value},
        )


def _purpose_check(values: str) -> None:
    op.drop_constraint("ck_payment_intents_purpose", _TABLE, schema=_SCHEMA)
    op.create_check_constraint(
        "ck_payment_intents_purpose", _TABLE, f"purpose IN ({values})", schema=_SCHEMA
    )


def upgrade() -> None:
    settings = _lock()
    _purpose_check(f"{_PREVIOUS_PURPOSES}, 'PURCHASE_FUNDING'")
    op.create_check_constraint(
        "ck_payment_intents_purchase_target",
        _TABLE,
        "purpose != 'PURCHASE_FUNDING' OR "
        "(target_reference IS NOT NULL AND target_reference ~ '[^[:space:]]')",
        schema=_SCHEMA,
    )
    op.execute("""
        CREATE FUNCTION mod_payments.refuse_purchase_binding_mutation()
        RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.purpose = 'PURCHASE_FUNDING' THEN
                    RAISE EXCEPTION 'purchase funding binding is immutable'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF (OLD.purpose = 'PURCHASE_FUNDING' OR NEW.purpose = 'PURCHASE_FUNDING')
               AND ROW(OLD.id, OLD.tenant_id, OLD.reference, OLD.payer_reference,
                       OLD.target_reference, OLD.purpose, OLD.requested_amount,
                       OLD.currency_code, OLD.minor_units, OLD.provider_type,
                       OLD.channel, OLD.opened_at, OLD.expires_at)
                   IS DISTINCT FROM
                   ROW(NEW.id, NEW.tenant_id, NEW.reference, NEW.payer_reference,
                       NEW.target_reference, NEW.purpose, NEW.requested_amount,
                       NEW.currency_code, NEW.minor_units, NEW.provider_type,
                       NEW.channel, NEW.opened_at, NEW.expires_at) THEN
                RAISE EXCEPTION 'purchase funding binding is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    # AFTER observes the final tuple even if another BEFORE trigger changes it.
    # Lifecycle/confirmation writes and no-op binding updates remain valid.
    op.execute(
        "CREATE TRIGGER payment_intents_purchase_binding_immutable "
        "AFTER UPDATE OR DELETE ON mod_payments.payment_intents "
        "FOR EACH ROW EXECUTE FUNCTION mod_payments.refuse_purchase_binding_mutation()"
    )
    _restore(settings)


def downgrade() -> None:
    settings = _lock(downgrade=True)
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM mod_payments.payment_intents "
                "WHERE purpose = 'PURCHASE_FUNDING' LIMIT 1"
            )
        )
        .first()
        is not None
    ):
        raise RuntimeError(
            "cannot downgrade Payments while purchase funding intents exist"
        )
    op.execute(
        "DROP TRIGGER payment_intents_purchase_binding_immutable "
        "ON mod_payments.payment_intents"
    )
    op.execute("DROP FUNCTION mod_payments.refuse_purchase_binding_mutation()")
    op.drop_constraint("ck_payment_intents_purchase_target", _TABLE, schema=_SCHEMA)
    _purpose_check(_PREVIOUS_PURPOSES)
    _restore(settings)
