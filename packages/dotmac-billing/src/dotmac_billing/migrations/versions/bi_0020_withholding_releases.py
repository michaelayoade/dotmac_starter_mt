"""Withholding releases: staff release of frozen expected withholding (Q-F).

A frozen expected-withholding component (bi_0016) that the customer did not in
fact deduct can never be discharged, so its undischarged residual blocks the
invoice's cash requirement for good. This revision adds the append-only
``withholding_releases`` table (tenant plane only): a release is a positive
``amount_delta`` against one frozen component row, an undo is the appended exact
negative (``reverses_release_id``). Nothing is updated or deleted (the shared
``refuse_mutation`` trigger); the online role has SELECT and INSERT only.

* The link is the frozen component's row id (composite FK with tenant, account
  and document), never a copied value.
* A BEFORE INSERT trigger stamps ``recorded_at`` from the database clock, locks
  the document row, and requires ``0 <= net discharged + net released + delta <=
  frozen amount``; an undo must equal minus the original and name the same
  component.
* ``cap_withholding_component`` (bi_0016) is replaced so a discharge also counts
  net releases: a discharge after a release cannot exceed the frozen amount.

No GL, posting-group, accounting-fact or position change: the receivable was
never booked net of the expectation. Additive; no row is backfilled (existing
components read ``released = 0``). No downgrade.
"""

from __future__ import annotations

# All DDL identifiers are closed Billing literals.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bi_0020_withholding_releases"
down_revision = "bi_0019_opening_dual_control"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
_TABLE = "withholding_releases"
_COMPONENTS = "document_withholding_components"


def upgrade() -> None:
    # Tenant-plane table: nothing to do for a platform-only composition.
    if ModulePlane.TENANT not in selected_module_planes("billing"):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("component_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.String(255), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.String(255)),
        sa.Column("amount_delta", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("reverses_release_id", sa.Uuid()),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            name=f"fk_{_TABLE}_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "component_id", "billing_account_id", "document_id"],
            [
                "mod_billing.document_withholding_components.tenant_id",
                "mod_billing.document_withholding_components.id",
                "mod_billing.document_withholding_components.billing_account_id",
                "mod_billing.document_withholding_components.document_id",
            ],
            name=f"fk_{_TABLE}_component",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{_TABLE}_id"),
        sa.UniqueConstraint(
            "tenant_id", "id", "component_id", name=f"uq_{_TABLE}_component_row"
        ),
        sa.UniqueConstraint("tenant_id", "command_id", name=f"uq_{_TABLE}_command"),
        sa.UniqueConstraint(
            "tenant_id", "reverses_release_id", name=f"uq_{_TABLE}_reversal"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reverses_release_id", "component_id"],
            [
                "mod_billing.withholding_releases.tenant_id",
                "mod_billing.withholding_releases.id",
                "mod_billing.withholding_releases.component_id",
            ],
            name=f"fk_{_TABLE}_reverses",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("amount_delta <> 0", name=f"ck_{_TABLE}_amount"),
        sa.CheckConstraint(
            "(reverses_release_id IS NULL AND amount_delta > 0) "
            "OR (reverses_release_id IS NOT NULL AND amount_delta < 0)",
            name=f"ck_{_TABLE}_direction",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3 "
            "AND minor_units BETWEEN 0 AND 6",
            name=f"ck_{_TABLE}_currency",
        ),
        sa.CheckConstraint(
            "length(trim(command_id)) > 0 AND length(trim(actor_ref)) > 0 "
            "AND length(trim(reason)) > 0 "
            "AND (evidence_ref IS NULL OR length(trim(evidence_ref)) > 0)",
            name=f"ck_{_TABLE}_attribution",
        ),
        schema=_SCHEMA,
    )
    op.execute(f"""
        ALTER TABLE mod_billing.{_TABLE} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_billing.{_TABLE} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON mod_billing.{_TABLE}
          USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
          WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
        REVOKE ALL ON mod_billing.{_TABLE} FROM PUBLIC, app_user, platform_api;
        GRANT SELECT, INSERT ON mod_billing.{_TABLE} TO app_user;
        CREATE TRIGGER {_TABLE}_append_only BEFORE UPDATE OR DELETE
          ON mod_billing.{_TABLE}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
    """)  # nosec B608 -- closed table identifiers; no input reaches this DDL
    op.execute(f"""
        CREATE FUNCTION mod_billing.admit_withholding_release() RETURNS trigger AS $$
        DECLARE
            doc record;
            component record;
            original record;
            discharged numeric;
            released numeric;
        BEGIN
            -- Database time, never the caller's.
            NEW.recorded_at := clock_timestamp();
            -- One writer per document at a time (as the discharge path).
            SELECT * INTO doc FROM mod_billing.documents x
             WHERE x.id = NEW.document_id AND x.tenant_id = NEW.tenant_id
             FOR UPDATE;
            IF doc.id IS NULL OR doc.document_kind <> 'invoice' OR doc.issued_at IS NULL
               OR doc.billing_account_id IS DISTINCT FROM NEW.billing_account_id
               OR doc.currency <> NEW.currency OR doc.minor_units <> NEW.minor_units
               OR NEW.amount_delta <> round(NEW.amount_delta, NEW.minor_units)
            THEN
                RAISE EXCEPTION 'withholding release needs its issued invoice and exact money'
                  USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO component FROM mod_billing.{_COMPONENTS} c
             WHERE c.id = NEW.component_id AND c.tenant_id = NEW.tenant_id;
            IF component.id IS NULL OR component.currency <> NEW.currency
               OR component.minor_units <> NEW.minor_units THEN
                RAISE EXCEPTION 'withholding release disagrees with its frozen component'
                  USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.reverses_release_id IS NOT NULL THEN
                SELECT * INTO original FROM mod_billing.{_TABLE} r
                 WHERE r.id = NEW.reverses_release_id AND r.tenant_id = NEW.tenant_id;
                IF original.id IS NULL OR original.reverses_release_id IS NOT NULL
                   OR NEW.amount_delta <> -original.amount_delta THEN
                    RAISE EXCEPTION 'a release undo must be the exact inverse of a release'
                      USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            SELECT coalesce(sum(w.amount_delta), 0) INTO discharged
              FROM mod_billing.withholding_discharges w
             WHERE w.document_id = component.document_id
               AND w.tax_source_owner = component.tax_source_owner
               AND w.determination_ref = component.determination_ref
               AND w.component_ref = component.component_ref
               AND w.tenant_id = NEW.tenant_id;
            SELECT coalesce(sum(r.amount_delta), 0) INTO released
              FROM mod_billing.{_TABLE} r
             WHERE r.component_id = NEW.component_id AND r.tenant_id = NEW.tenant_id;
            IF discharged + released + NEW.amount_delta < 0
               OR discharged + released + NEW.amount_delta > component.tax_amount THEN
                RAISE EXCEPTION 'withholding release leaves its frozen component'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER {_TABLE}_admission BEFORE INSERT ON mod_billing.{_TABLE}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_withholding_release();
    """)  # nosec B608 -- closed identifiers; no input reaches this DDL
    # The discharge cap now counts net releases too (bi_0016 body + releases).
    op.execute(f"""
        CREATE OR REPLACE FUNCTION mod_billing.cap_withholding_component() RETURNS trigger AS $$
        DECLARE
            component record;
            total numeric;
            released numeric;
        BEGIN
            SELECT * INTO component FROM mod_billing.{_COMPONENTS} x
             WHERE x.document_id = NEW.document_id
               AND x.tax_source_owner = NEW.tax_source_owner
               AND x.determination_ref = NEW.determination_ref
               AND x.component_ref = NEW.component_ref AND x.tenant_id = NEW.tenant_id;
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
            SELECT coalesce(sum(x.amount_delta), 0) + NEW.amount_delta INTO total
              FROM mod_billing.withholding_discharges x
             WHERE x.document_id = NEW.document_id
               AND x.tax_source_owner = NEW.tax_source_owner
               AND x.determination_ref = NEW.determination_ref
               AND x.component_ref = NEW.component_ref AND x.tenant_id = NEW.tenant_id;
            SELECT coalesce(sum(r.amount_delta), 0) INTO released
              FROM mod_billing.{_TABLE} r
             WHERE r.component_id = component.id AND r.tenant_id = NEW.tenant_id;
            IF total < 0 OR total + released > component.tax_amount THEN
                RAISE EXCEPTION 'cumulative withholding discharge leaves its frozen component'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed identifiers; no input reaches this DDL


def downgrade() -> None:
    raise RuntimeError(
        "Withholding releases are append-only financial evidence; no downgrade"
    )
