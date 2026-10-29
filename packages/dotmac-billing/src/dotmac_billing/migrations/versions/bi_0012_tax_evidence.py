"""Lossless Tax-authored obligation evidence and final immutable-row guards.

NULL denotes a predecessor V1 obligation. No issued evidence is backfilled or
rerated. New JSON remains on the existing scoped, append-only parent rows.
"""

# Only closed Billing-owned plane/table identifiers are interpolated.
# ruff: noqa: S608

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bi_0012_tax_evidence"
down_revision = "bi_0011_settlement_earmarks"
branch_labels = None
depends_on = None


def _prefixes() -> tuple[str, ...]:
    selected = selected_module_planes("billing")
    return tuple(
        prefix
        for plane, prefix in (
            (ModulePlane.TENANT, ""),
            (ModulePlane.PLATFORM, "platform_"),
        )
        if plane in selected
    )


def upgrade() -> None:
    for prefix in _prefixes():
        op.add_column(
            prefix + "rated_obligations",
            sa.Column("tax_evidence", sa.JSON(), nullable=True),
            schema="mod_billing",
        )
        op.create_check_constraint(
            "ck_" + prefix + "obligation_tax_evidence_array",
            prefix + "rated_obligations",
            "tax_evidence IS NULL OR json_typeof(tax_evidence) = 'array'",
            schema="mod_billing",
        )
        # AFTER protection observes the final tuple even when another BEFORE
        # trigger rewrites NEW. Existing RLS, grants, FKs and BEFORE guards stay.
        for table in (prefix + "rated_obligations", prefix + "invoice_document_facts"):
            op.execute(f"""
                CREATE FUNCTION mod_billing.freeze_{table}_final() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                    RAISE EXCEPTION 'Billing evidence is immutable and append-only'
                        USING ERRCODE = 'check_violation';
                END $$;
                CREATE TRIGGER {table}_immutable_final AFTER UPDATE OR DELETE
                ON mod_billing.{table} FOR EACH ROW
                EXECUTE FUNCTION mod_billing.freeze_{table}_final();
            """)


def downgrade() -> None:
    for prefix in _prefixes():
        # Never discard original successor evidence, including empty V2 sets.
        obligations = sa.table(
            prefix + "rated_obligations",
            sa.column("tax_evidence", sa.JSON()),
            schema="mod_billing",
        )
        document_facts = sa.table(
            prefix + "invoice_document_facts",
            sa.column("contract_version", sa.String()),
            schema="mod_billing",
        )
        count = (
            op.get_bind()
            .execute(
                sa.select(sa.func.count())
                .select_from(obligations)
                .where(obligations.c.tax_evidence.is_not(None))
            )
            .scalar_one()
        )
        facts = (
            op.get_bind()
            .execute(
                sa.select(sa.func.count())
                .select_from(document_facts)
                .where(
                    document_facts.c.contract_version
                    == "billing.invoice.document.fact.v2"
                )
            )
            .scalar_one()
        )
        if count or facts:
            raise RuntimeError(
                "cannot downgrade while immutable V2 Tax evidence exists"
            )
    for prefix in _prefixes():
        for table in (prefix + "rated_obligations", prefix + "invoice_document_facts"):
            op.execute(f"DROP TRIGGER {table}_immutable_final ON mod_billing.{table}")
            op.execute(f"DROP FUNCTION mod_billing.freeze_{table}_final()")
        op.drop_constraint(
            "ck_" + prefix + "obligation_tax_evidence_array",
            prefix + "rated_obligations",
            schema="mod_billing",
            type_="check",
        )
        op.drop_column(
            prefix + "rated_obligations", "tax_evidence", schema="mod_billing"
        )
