"""Refund instructions and post-confirmation cash outcomes.

Revision ID: pm_0007_refund_outcomes
Revises: pm_0006_cash_qualification

Two append-only, forced-RLS tables. An insert trigger on instructions repeats the
service's bound at the database: the owning intent row is locked, the instruction
must match its confirmation, its provider reference must be the one derived from
its request id, and its amount may not exceed confirmed cash less every earlier
instruction not observed to fail. There is no fee column and no mandate column.
"""

from __future__ import annotations

# Closed Payments-owned schema/table identifiers only.
# ruff: noqa: S608
import sqlalchemy as sa

from alembic import op

revision = "pm_0007_refund_outcomes"
down_revision = "pm_0006_cash_qualification"
branch_labels = None
depends_on = None
_SCHEMA = "mod_payments"
_TABLES = ("refund_instructions", "payment_cash_outcomes")
_MONEY = sa.Numeric(20, 6)
_KINDS = (
    "REFUND_COMPLETED",
    "REFUND_FAILED",
    "CHARGEBACK",
    "BOUNCE",
    "ADMIN_CORRECTION",
)
_STATES = ("OBSERVED", "ATTENTION_REQUIRED")
_REASONS = (
    "UNKNOWN_CONFIRMATION",
    "UNKNOWN_REFUND_INSTRUCTION",
    "CONFIRMATION_MISMATCH",
    "PROVIDER_MISMATCH",
    "CURRENCY_MISMATCH",
    "AMOUNT_MISMATCH",
    "EXCEEDS_CONFIRMED_CASH",
    "TERMINAL_CONFLICT",
)
_REFUND_KINDS = "'REFUND_COMPLETED', 'REFUND_FAILED'"


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "refund_instructions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("confirmation_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("provider_type", sa.String(40), nullable=False),
        sa.Column("provider_reference", sa.String(64), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("instructed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "confirmation_id"],
            [
                "mod_payments.payment_confirmations.tenant_id",
                "mod_payments.payment_confirmations.id",
            ],
            ondelete="RESTRICT",
            name="fk_refund_instructions_confirmation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "intent_id"],
            [
                "mod_payments.payment_intents.tenant_id",
                "mod_payments.payment_intents.id",
            ],
            ondelete="RESTRICT",
            name="fk_refund_instructions_intent",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_refund_instructions_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "request_id", name="uq_refund_instructions_request"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider_type",
            "provider_reference",
            name="uq_refund_instructions_provider_reference",
        ),
        sa.CheckConstraint(
            "currency_code ~ '^[A-Z]{3}$'", name="ck_refund_instructions_currency"
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_refund_instructions_minor_units"
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount = round(amount, minor_units)",
            name="ck_refund_instructions_amount_positive",
        ),
        sa.CheckConstraint(
            "provider_reference ~ '^rf1-[0-9a-f]{32}$'",
            name="ck_refund_instructions_reference_shape",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_refund_instructions_tenant_confirmation",
        "refund_instructions",
        ["tenant_id", "confirmation_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "payment_cash_outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attention_reason", sa.String(32), nullable=True),
        sa.Column("claimed_confirmation_id", sa.Uuid(), nullable=False),
        sa.Column("confirmation_id", sa.Uuid(), nullable=True),
        sa.Column("instruction_id", sa.Uuid(), nullable=True),
        sa.Column("provider_type", sa.String(40), nullable=True),
        sa.Column("outcome_reference", sa.String(160), nullable=True),
        sa.Column("correction_id", sa.Uuid(), nullable=True),
        sa.Column("refund_reference", sa.String(64), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "confirmation_id"],
            [
                "mod_payments.payment_confirmations.tenant_id",
                "mod_payments.payment_confirmations.id",
            ],
            ondelete="RESTRICT",
            name="fk_cash_outcomes_confirmation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "instruction_id"],
            [
                "mod_payments.refund_instructions.tenant_id",
                "mod_payments.refund_instructions.id",
            ],
            ondelete="RESTRICT",
            name="fk_cash_outcomes_instruction",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cash_outcomes_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider_type",
            "outcome_reference",
            name="uq_cash_outcomes_provider_reference",
        ),
        sa.UniqueConstraint(
            "tenant_id", "correction_id", name="uq_cash_outcomes_correction"
        ),
        sa.CheckConstraint(_in_list("kind", _KINDS), name="ck_cash_outcomes_kind"),
        sa.CheckConstraint(
            _in_list("state", _STATES), name="ck_cash_outcomes_state_value"
        ),
        sa.CheckConstraint(
            f"attention_reason IS NULL OR {_in_list('attention_reason', _REASONS)}",
            name="ck_cash_outcomes_reason",
        ),
        sa.CheckConstraint(
            "currency_code ~ '^[A-Z]{3}$'", name="ck_cash_outcomes_currency"
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_cash_outcomes_minor_units"
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount = round(amount, minor_units)",
            name="ck_cash_outcomes_amount_positive",
        ),
        sa.CheckConstraint(
            "(kind = 'ADMIN_CORRECTION' AND correction_id IS NOT NULL "
            "AND provider_type IS NULL AND outcome_reference IS NULL "
            "AND refund_reference IS NULL) OR "
            "(kind <> 'ADMIN_CORRECTION' AND correction_id IS NULL "
            "AND provider_type IS NOT NULL AND outcome_reference IS NOT NULL "
            f"AND ((kind IN ({_REFUND_KINDS})) = (refund_reference IS NOT NULL)))",
            name="ck_cash_outcomes_identity",
        ),
        sa.CheckConstraint(
            "(state = 'OBSERVED' AND attention_reason IS NULL "
            "AND confirmation_id IS NOT NULL "
            "AND confirmation_id = claimed_confirmation_id "
            f"AND ((kind IN ({_REFUND_KINDS})) = (instruction_id IS NOT NULL))) OR "
            "(state = 'ATTENTION_REQUIRED' AND attention_reason IS NOT NULL)",
            name="ck_cash_outcomes_state",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_cash_outcomes_one_terminal",
        "payment_cash_outcomes",
        ["tenant_id", "instruction_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text(
            "state = 'OBSERVED' AND kind IN ('REFUND_COMPLETED', 'REFUND_FAILED')"
        ),
    )
    op.create_index(
        "ix_cash_outcomes_tenant_confirmation",
        "payment_cash_outcomes",
        ["tenant_id", "claimed_confirmation_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_cash_outcomes_tenant_state",
        "payment_cash_outcomes",
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )

    for table in _TABLES:
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {_SCHEMA}.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id())"
        )
        op.execute(f"GRANT SELECT, INSERT ON {_SCHEMA}.{table} TO app_user")

    op.execute("""
        CREATE FUNCTION mod_payments.refuse_refund_outcome_mutation() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'refund instructions and cash outcomes are append-only'
                USING ERRCODE = 'restrict_violation';
        END $$
    """)
    for table in _TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable AFTER UPDATE OR DELETE "
            f"ON {_SCHEMA}.{table} FOR EACH ROW "
            "EXECUTE FUNCTION mod_payments.refuse_refund_outcome_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE "
            f"ON {_SCHEMA}.{table} FOR EACH STATEMENT "
            "EXECUTE FUNCTION mod_payments.refuse_refund_outcome_mutation()"
        )
        for suffix in ("immutable", "no_truncate"):
            op.execute(
                f"ALTER TABLE {_SCHEMA}.{table} "
                f"ENABLE ALWAYS TRIGGER {table}_{suffix}"
            )

    op.execute("""
        CREATE FUNCTION mod_payments.check_refund_instruction_insert() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
        DECLARE
            intent_status text;
            conf mod_payments.payment_confirmations%ROWTYPE;
            committed numeric;
        BEGIN
            IF current_setting('transaction_isolation') <> 'read committed'
                OR current_setting('transaction_read_only') <> 'off' THEN
                RAISE EXCEPTION 'refund instruction requires READ WRITE READ COMMITTED'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            -- Serialize every refund of this cash on the owning intent row. The
            -- reads below are separate VOLATILE statements, so each takes a fresh
            -- snapshot AFTER the lock and sees every earlier committed refund.
            SELECT status INTO intent_status FROM mod_payments.payment_intents
                WHERE tenant_id = NEW.tenant_id AND id = NEW.intent_id FOR UPDATE;
            IF NOT FOUND OR intent_status <> 'CONFIRMED' THEN
                RAISE EXCEPTION 'only a confirmed payment can be refunded'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            SELECT * INTO conf FROM mod_payments.payment_confirmations
                WHERE tenant_id = NEW.tenant_id AND id = NEW.confirmation_id;
            IF NOT FOUND
                OR conf.intent_id <> NEW.intent_id
                OR conf.provider_type <> NEW.provider_type
                OR conf.currency_code <> NEW.currency_code
                OR conf.minor_units IS DISTINCT FROM NEW.minor_units THEN
                RAISE EXCEPTION 'refund does not match its confirmation'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.provider_reference <> 'rf1-' || substr(encode(sha256(convert_to(
                'payment-refund-reference-v1:' || NEW.tenant_id::text || ':'
                || NEW.request_id::text, 'UTF8')), 'hex'), 1, 32) THEN
                RAISE EXCEPTION 'refund provider reference is not derived from its request'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            SELECT COALESCE(sum(i.amount), 0) INTO committed
                FROM mod_payments.refund_instructions i
                WHERE i.tenant_id = NEW.tenant_id
                  AND i.confirmation_id = NEW.confirmation_id
                  AND NOT EXISTS (
                      SELECT 1 FROM mod_payments.payment_cash_outcomes o
                      WHERE o.tenant_id = i.tenant_id AND o.instruction_id = i.id
                        AND o.state = 'OBSERVED' AND o.kind = 'REFUND_FAILED');
            IF committed + NEW.amount > conf.confirmed_amount THEN
                RAISE EXCEPTION 'refund exceeds confirmed cash less prior refunds'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER refund_instruction_insert BEFORE INSERT "
        f"ON {_SCHEMA}.refund_instructions FOR EACH ROW "
        "EXECUTE FUNCTION mod_payments.check_refund_instruction_insert()"
    )
    op.execute(
        f"ALTER TABLE {_SCHEMA}.refund_instructions "
        "ENABLE ALWAYS TRIGGER refund_instruction_insert"
    )


def downgrade() -> None:
    # Refund history is money evidence and is forward-only; an empty
    # installation has none to preserve and may still retreat.
    retained = 0
    for table in _TABLES:
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        retained += (
            op.get_bind()
            .execute(sa.text(f"SELECT count(*) FROM {_SCHEMA}.{table}"))
            .scalar_one()
        )
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    if retained:
        raise RuntimeError(
            "Refund and cash outcome history is forward-only; "
            "use a reviewed forward migration"
        )
    op.drop_table("payment_cash_outcomes", schema=_SCHEMA)
    op.drop_table("refund_instructions", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_payments.check_refund_instruction_insert()")
    op.execute("DROP FUNCTION mod_payments.refuse_refund_outcome_mutation()")
