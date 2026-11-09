"""Freeze expected-withholding components as structural rows; bind discharges.

At invoice issue the service writes one immutable row per elected
expected-withholding component of the rated obligations' ``dotmac-tax``
determinations (``document_withholding_components``). Applicability, rate and
amount are whatever Tax determined; Billing only freezes and verifies the
result. A component is frozen only when its determination carries a party
classification (the election is a Tax party classification) and a positive
amount, so an invoice that is not elected has no row to discharge.

Each withholding discharge is then tied to its frozen row by a composite foreign
key on the component identity it already stores
(``document_id, tax_source_owner, determination_ref, component_ref``), and a
BEFORE INSERT guard enforces the cumulative cap: the running sum of accepted
deltas for a component (reversals negative) stays within ``[0, frozen amount]``.
The bi_0004 one-discharge-per-component unique index and the permanent
receivable source claim stay: a component is still accepted once. The cap is the
database's own statement that, whatever rows exist, the net accepted amount of a
component never leaves ``[0, frozen amount]``.

Existing discharges predate the frozen rows. The foreign key is added ``NOT
VALID`` (every new row is enforced) and ``VALIDATE`` is attempted; if history
violates it the constraint stays ``NOT VALID`` with a comment naming the manual
exceptions review. No row is backfilled, repaired or rewritten.

A row is written either at issue (``origin = 'issue'``, bound to its rated
obligation) or by the governed late-freeze command for an invoice issued before
this migration or imported as an open item (``origin = 'late_freeze'``, carrying
the command id, actor and reason; the obligation may be absent). Both are the
same append-only structure.

The online role needs SELECT and INSERT on the new table (the issuing path).
No downgrade.
"""

from __future__ import annotations

# All identifiers are closed Billing plane/table literals.
# ruff: noqa: S608
from collections.abc import Iterator

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0016_withholding_components"
down_revision = "bi_0015_line_price_version_fk"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
TAX_OWNER = "dotmac-tax"
_NOTE = (
    "NOT VALID: discharges accepted before withholding components were frozen "
    "have no frozen row; they are history for the manual exceptions review, "
    "not repaired or backfilled. New rows are enforced."
)


def _planes() -> Iterator[bool]:
    planes = selected_module_planes("billing")
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane in planes:
            yield plane is ModulePlane.PLATFORM


def _elements(platform: bool) -> tuple[SchemaItem, ...]:
    prefix = "platform_" if platform else ""
    table = f"{prefix}document_withholding_components"
    scope = [] if platform else ["tenant_id"]

    def fk(columns: list[str], target: str, remote: list[str], name: str) -> SchemaItem:
        return sa.ForeignKeyConstraint(
            [*scope, *columns],
            [
                *(f"{_SCHEMA}.{prefix}{target}.{key}" for key in scope),
                *(f"{_SCHEMA}.{prefix}{target}.{key}" for key in remote),
            ],
            name=name,
            ondelete="RESTRICT",
        )

    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_id", sa.Uuid()),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("command_id", sa.String(255)),
        sa.Column("actor_ref", sa.String(255)),
        sa.Column("reason", sa.Text()),
        sa.Column("tax_source_owner", sa.String(120), nullable=False),
        sa.Column("determination_ref", sa.String(255), nullable=False),
        sa.Column("component_ref", sa.String(255), nullable=False),
        sa.Column("tax_code_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("treatment_code", sa.String(64), nullable=False),
        sa.Column("party_category", sa.String(255)),
        sa.Column("party_classification_id", sa.Uuid(), nullable=False),
        sa.Column("base_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("result_fingerprint", sa.String(68), nullable=False),
        sa.Column("determined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *(
            ()
            if platform
            else (
                sa.ForeignKeyConstraint(
                    ["tenant_id"],
                    ["public.tenants.id"],
                    name=f"fk_{table}_tenant",
                    ondelete="CASCADE",
                ),
            )
        ),
        fk(
            ["document_id", "billing_account_id"],
            "documents",
            ["id", "billing_account_id"],
            f"fk_{table}_document_account",
        ),
        fk(
            ["obligation_id", "billing_account_id"],
            "rated_obligations",
            ["id", "billing_account_id"],
            f"fk_{table}_obligation_account",
        ),
        sa.UniqueConstraint(*scope, "id", name=f"uq_{table}_id"),
        sa.UniqueConstraint(
            *scope,
            "id",
            "billing_account_id",
            "document_id",
            name=f"uq_{table}_document",
        ),
        sa.UniqueConstraint(
            *scope,
            "document_id",
            "tax_source_owner",
            "determination_ref",
            "component_ref",
            name=f"uq_{table}_identity",
        ),
        sa.CheckConstraint(
            f"tax_source_owner = '{TAX_OWNER}'", name=f"ck_{table}_tax_owner"
        ),
        sa.CheckConstraint(
            "tax_amount > 0 AND base_amount >= 0", name=f"ck_{table}_amount"
        ),
        sa.CheckConstraint(
            "(origin = 'issue' AND obligation_id IS NOT NULL "
            "AND command_id IS NULL AND actor_ref IS NULL AND reason IS NULL) "
            "OR (origin = 'late_freeze' AND command_id IS NOT NULL "
            "AND actor_ref IS NOT NULL AND reason IS NOT NULL)",
            name=f"ck_{table}_origin",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3 "
            "AND minor_units BETWEEN 0 AND 6",
            name=f"ck_{table}_currency",
        ),
    )


def _create(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    table = f"{prefix}document_withholding_components"
    discharges = f"{prefix}withholding_discharges"
    scope_x = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    scope_list = [] if platform else ["tenant_id"]
    if platform:
        op.create_table(
            "platform_document_withholding_components",
            *_elements(platform=True),
            schema=_SCHEMA,
        )
        op.execute(f"""
            REVOKE ALL ON mod_billing.{table} FROM PUBLIC, app_user, platform_api;
            GRANT SELECT, INSERT ON mod_billing.{table} TO platform_api;
        """)
    else:
        op.create_table(
            "document_withholding_components",
            *_elements(platform=False),
            schema=_SCHEMA,
        )
        op.execute(f"""
            ALTER TABLE mod_billing.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE mod_billing.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON mod_billing.{table}
              USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
              WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
            REVOKE ALL ON mod_billing.{table} FROM PUBLIC, app_user, platform_api;
            GRANT SELECT, INSERT ON mod_billing.{table} TO app_user;
        """)
    op.execute(f"""
        CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE
          ON mod_billing.{table}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        CREATE FUNCTION mod_billing.admit_{prefix}withholding_component() RETURNS trigger AS $$
        DECLARE
            doc record;
            frozen numeric;
        BEGIN
            SELECT * INTO doc FROM mod_billing.{prefix}documents x
             WHERE x.id = NEW.document_id{scope_x};
            IF doc.id IS NULL OR doc.document_kind <> 'invoice' OR doc.issued_at IS NULL
               OR doc.billing_account_id IS DISTINCT FROM NEW.billing_account_id
               OR doc.currency <> NEW.currency OR doc.minor_units <> NEW.minor_units
               OR NEW.tax_amount <> round(NEW.tax_amount, NEW.minor_units)
               OR NEW.base_amount <> round(NEW.base_amount, NEW.minor_units)
            THEN
                RAISE EXCEPTION 'withholding component needs its issued invoice and exact money'
                  USING ERRCODE = 'check_violation';
            END IF;
            SELECT coalesce(sum(x.tax_amount), 0) INTO frozen
              FROM mod_billing.{table} x WHERE x.document_id = NEW.document_id{scope_x};
            IF frozen + NEW.tax_amount > doc.grand_total THEN
                RAISE EXCEPTION 'frozen withholding exceeds the invoice total'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER {table}_admission BEFORE INSERT ON mod_billing.{table}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}withholding_component();
    """)  # nosec B608 -- closed plane/table identifiers and fixed scope expressions; no input reaches this DDL
    # The discharge side: the cap guard and the foreign key.
    op.execute(f"""
        CREATE FUNCTION mod_billing.cap_{prefix}withholding_component() RETURNS trigger AS $$
        DECLARE
            component record;
            total numeric;
        BEGIN
            SELECT * INTO component FROM mod_billing.{table} x
             WHERE x.document_id = NEW.document_id
               AND x.tax_source_owner = NEW.tax_source_owner
               AND x.determination_ref = NEW.determination_ref
               AND x.component_ref = NEW.component_ref{scope_x};
            IF component.id IS NULL THEN
                RAISE EXCEPTION 'withholding discharge needs a frozen component of this invoice'
                  USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF component.billing_account_id <> NEW.billing_account_id
               OR component.currency <> NEW.currency
               OR component.minor_units <> NEW.minor_units THEN
                RAISE EXCEPTION 'withholding discharge disagrees with its frozen component'
                  USING ERRCODE = 'check_violation';
            END IF;
            -- The admission trigger (earlier by name) already holds the scoped
            -- account lock, which serializes every competing discharge.
            SELECT coalesce(sum(x.amount_delta), 0) + NEW.amount_delta INTO total
              FROM mod_billing.{discharges} x
             WHERE x.document_id = NEW.document_id
               AND x.tax_source_owner = NEW.tax_source_owner
               AND x.determination_ref = NEW.determination_ref
               AND x.component_ref = NEW.component_ref{scope_x};
            IF total < 0 OR total > component.tax_amount THEN
                RAISE EXCEPTION 'cumulative withholding discharge leaves its frozen component'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER {discharges}_component_cap BEFORE INSERT ON mod_billing.{discharges}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.cap_{prefix}withholding_component();
    """)  # nosec B608 -- closed plane/table identifiers and fixed scope expressions; no input reaches this DDL
    local = ", ".join(
        [
            *scope_list,
            "document_id",
            "tax_source_owner",
            "determination_ref",
            "component_ref",
        ]
    )
    constraint = f"fk_{discharges}_frozen_component"
    op.execute(
        f"ALTER TABLE mod_billing.{discharges} ADD CONSTRAINT {constraint} "
        f"FOREIGN KEY ({local}) REFERENCES mod_billing.{table} ({local}) "
        "ON DELETE RESTRICT NOT VALID"
    )
    op.execute(f"""
        DO $$
        BEGIN
            ALTER TABLE mod_billing.{discharges} VALIDATE CONSTRAINT {constraint};
        EXCEPTION WHEN foreign_key_violation THEN
            COMMENT ON CONSTRAINT {constraint} ON mod_billing.{discharges}
                IS '{_NOTE}';
        END
        $$;
    """)


def upgrade() -> None:
    for platform in _planes():
        _create(platform)


def downgrade() -> None:
    raise RuntimeError(
        "Frozen withholding components are financial evidence; no downgrade"
    )
