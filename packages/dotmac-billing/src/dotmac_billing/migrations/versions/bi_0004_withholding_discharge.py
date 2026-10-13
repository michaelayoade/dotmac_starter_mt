"""Add immutable invoice-bound noncash withholding discharges.

No predecessor evidence is rewritten. A populated downgrade refuses globally,
including when the migration role cannot see rows through forced tenant RLS.
"""

from __future__ import annotations

# Interpolated SQL consists only of fixed module-plane names and predicates.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0004_withholding_discharge"
down_revision = "bi_0003_issued_line_freeze"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"


def _table_elements(platform: bool) -> tuple[SchemaItem, ...]:
    prefix = "platform_" if platform else ""
    table = f"{prefix}withholding_discharges"
    scope = [] if platform else ["tenant_id"]
    constraints = []
    for column, parent in (
        ("posting_group_id", "posting_groups"),
        ("document_id", "documents"),
        ("settlement_id", "confirmed_settlements"),
        ("allocation_id", "allocation_effects"),
    ):
        target = f"{_SCHEMA}.{prefix}{parent}"
        constraints.append(
            sa.ForeignKeyConstraint(
                [*scope, column, "billing_account_id"],
                [
                    *(f"{target}.{key}" for key in scope),
                    f"{target}.id",
                    f"{target}.billing_account_id",
                ],
                name=f"fk_{table}_{column}",
                ondelete="RESTRICT",
            )
        )
    if not platform:
        constraints.append(
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["public.tenants.id"],
                name=f"fk_{table}_tenant",
                ondelete="CASCADE",
            )
        )
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
        *(
            sa.Column(key, sa.Uuid(), nullable=False)
            for key in ("billing_account_id", "document_id", "posting_group_id")
        ),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("claim_ref", sa.String(255), nullable=False),
        sa.Column("tax_source_owner", sa.String(120), nullable=False),
        sa.Column("determination_ref", sa.String(255), nullable=False),
        sa.Column("component_ref", sa.String(255), nullable=False),
        sa.Column("claimed_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("amount_delta", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("settlement_id", sa.Uuid()),
        sa.Column("allocation_id", sa.Uuid()),
        sa.Column("reverses_discharge_id", sa.Uuid()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *constraints,
        sa.UniqueConstraint(*scope, "id", name=f"uq_{table}_id"),
        sa.UniqueConstraint(
            *scope,
            "id",
            "billing_account_id",
            "document_id",
            name=f"uq_{table}_document",
        ),
        sa.UniqueConstraint(*scope, "posting_group_id", name=f"uq_{table}_group"),
        sa.UniqueConstraint(
            *scope, "source_owner", "source_ref", name=f"uq_{table}_source"
        ),
        sa.UniqueConstraint(
            *scope, "reverses_discharge_id", name=f"uq_{table}_reversal"
        ),
        sa.ForeignKeyConstraint(
            [*scope, "reverses_discharge_id", "billing_account_id", "document_id"],
            [
                *(f"{_SCHEMA}.{table}.{key}" for key in scope),
                f"{_SCHEMA}.{table}.id",
                f"{_SCHEMA}.{table}.billing_account_id",
                f"{_SCHEMA}.{table}.document_id",
            ],
            name=f"fk_{table}_correction",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "claimed_amount > 0 AND amount_delta <> 0 AND abs(amount_delta) <= claimed_amount",
            name=f"ck_{table}_amount",
        ),
        sa.CheckConstraint(
            "(reverses_discharge_id IS NULL AND amount_delta > 0) OR (reverses_discharge_id IS NOT NULL AND amount_delta < 0)",
            name=f"ck_{table}_direction",
        ),
        sa.CheckConstraint("minor_units BETWEEN 0 AND 6", name=f"ck_{table}_precision"),
    )


def _create(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    table = f"{prefix}withholding_discharges"
    scope = [] if platform else ["tenant_id"]
    # Keep the real table declarations explicit for the composed DDL scanner.
    if platform:
        op.create_table(
            "platform_withholding_discharges",
            *_table_elements(platform=True),
            schema=_SCHEMA,
        )
    else:
        op.create_table(
            "withholding_discharges",
            *_table_elements(platform=False),
            schema=_SCHEMA,
        )
    op.create_index(
        f"uq_{prefix}withholding_tax_component",
        table,
        [*scope, "tax_source_owner", "determination_ref", "component_ref"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("reverses_discharge_id IS NULL"),
    )
    if not platform:
        op.execute("""
            ALTER TABLE mod_billing.withholding_discharges ENABLE ROW LEVEL SECURITY;
            ALTER TABLE mod_billing.withholding_discharges FORCE ROW LEVEL SECURITY;
            CREATE POLICY withholding_discharges_tenant_isolation ON mod_billing.withholding_discharges
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
            GRANT SELECT, INSERT ON mod_billing.withholding_discharges TO app_user;
            REVOKE ALL ON mod_billing.withholding_discharges FROM platform_api;
        """)
    else:
        op.execute("""
            REVOKE ALL ON mod_billing.platform_withholding_discharges FROM app_user;
            GRANT SELECT, INSERT ON mod_billing.platform_withholding_discharges TO platform_api;
        """)
    _guards(platform)


def _guards(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    table = f"{prefix}withholding_discharges"
    # Every relation alias below is x, and each query is scope qualified.
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    tenant_arg = "" if platform else ", scope_id uuid"
    tenant_call = "" if platform else ", NEW.tenant_id"
    issuance_scope = "" if platform else " AND g.tenant_id = scope_id"
    relation_scope = "" if platform else " AND x.tenant_id = g.tenant_id"
    op.execute(f"""
    CREATE FUNCTION mod_billing.has_{prefix}live_invoice_issuance(
        invoice_id uuid, account_id uuid, currency_code text, scale integer{tenant_arg}
    ) RETURNS boolean AS $$
        SELECT count(*) = 1 AND (SELECT count(*) FROM mod_billing.{prefix}posting_groups g
            WHERE g.group_kind = 'invoice_issued' AND g.reverses_group_id IS NULL
              AND g.billing_account_id = account_id AND g.currency = currency_code
              AND g.minor_units = scale AND g.source_ref = 'document:' || invoice_id::text || ':issue'
              {issuance_scope}) = 1
        FROM mod_billing.{prefix}posting_groups g
        WHERE g.group_kind = 'invoice_issued' AND g.reverses_group_id IS NULL
          AND g.billing_account_id = account_id AND g.currency = currency_code
          AND g.minor_units = scale AND g.source_ref = 'document:' || invoice_id::text || ':issue'
          {issuance_scope}
          AND EXISTS (SELECT 1 FROM mod_billing.{prefix}receivable_exposure_facts x
              WHERE x.document_id = invoice_id AND x.billing_account_id = account_id
                AND x.posting_group_watermark = g.id AND x.source_version = g.source_version
                AND x.currency = currency_code AND x.minor_units = scale{relation_scope})
          AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_groups x
              WHERE x.reverses_group_id = g.id{relation_scope})
          AND (SELECT count(*) FROM mod_billing.{prefix}posting_effects x
              WHERE x.posting_group_id = g.id{relation_scope}) = 1
          AND EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects x
              JOIN mod_billing.{prefix}documents d ON d.id = invoice_id
                {'' if platform else 'AND d.tenant_id = g.tenant_id'}
              WHERE x.posting_group_id = g.id AND x.lane = 'receivable'
                AND x.billing_account_id = account_id AND x.currency = currency_code
                AND x.minor_units = scale AND x.amount_delta = d.grand_total
                AND d.billing_account_id = account_id AND d.currency = currency_code
                AND d.minor_units = scale AND d.document_kind = 'invoice'
                AND d.issued_at IS NOT NULL{relation_scope});
    $$ LANGUAGE sql;
    CREATE FUNCTION mod_billing.guard_{prefix}withholding_invoice_reversal() RETURNS trigger AS $$
    DECLARE original record;
    BEGIN
        -- Raw SQL reversal and acceptance use the same account-first serialization.
        PERFORM 1 FROM mod_billing.{prefix}billing_accounts x
          WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
        IF NEW.reverses_group_id IS NOT NULL THEN
            SELECT * INTO original FROM mod_billing.{prefix}posting_groups x
              WHERE x.id = NEW.reverses_group_id{scope};
            IF original.group_kind = 'withholding_reversed'
               OR (original.group_kind = 'withholding_accepted'
                   AND NEW.group_kind <> 'withholding_reversed') THEN
                RAISE EXCEPTION 'withholding reversal requires the dedicated exact inverse owner'
                  USING ERRCODE = 'check_violation';
            END IF;
            IF original.group_kind = 'invoice_issued' AND EXISTS (
                SELECT 1 FROM mod_billing.{table} x
                WHERE x.billing_account_id = NEW.billing_account_id{scope}
                  AND EXISTS (SELECT 1 FROM mod_billing.{prefix}receivable_exposure_facts e
                      WHERE e.document_id = x.document_id
                        AND e.billing_account_id = x.billing_account_id
                        AND e.posting_group_watermark = original.id
                        {'' if platform else 'AND e.tenant_id = x.tenant_id'})
                GROUP BY x.document_id HAVING sum(x.amount_delta) > 0
            ) THEN
                RAISE EXCEPTION 'invoice issuance with active withholding requires its correction owner'
                  USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}posting_groups_withholding_invoice_reversal
      BEFORE INSERT ON mod_billing.{prefix}posting_groups
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}withholding_invoice_reversal();
    """)  # nosec B608 -- fixed plane names and scope predicates only
    op.execute(f"""
    CREATE FUNCTION mod_billing.admit_{prefix}withholding() RETURNS trigger AS $$
    DECLARE
        account record;
        doc record;
        grp record;
        original record;
        linked record;
        used numeric;
    BEGIN
        -- Match the runtime account-first order, serializing competing claims.
        SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x
         WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
        SELECT * INTO doc FROM mod_billing.{prefix}documents x
         WHERE x.id = NEW.document_id{scope} FOR UPDATE;
        SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x
         WHERE x.id = NEW.posting_group_id{scope} FOR UPDATE;
        IF account.id IS NULL OR doc.id IS NULL OR grp.id IS NULL
           OR account.currency <> NEW.currency OR account.minor_units <> NEW.minor_units
           OR doc.billing_account_id IS DISTINCT FROM NEW.billing_account_id
           OR grp.billing_account_id IS DISTINCT FROM NEW.billing_account_id
           OR doc.document_kind <> 'invoice' OR doc.issued_at IS NULL
           OR doc.currency <> NEW.currency OR doc.minor_units <> NEW.minor_units
           OR grp.currency <> NEW.currency OR grp.minor_units <> NEW.minor_units
           OR grp.source_ref <> NEW.source_ref
           OR EXISTS (SELECT 1 FROM mod_billing.{prefix}document_events x
                       WHERE x.document_id = NEW.document_id AND x.event_kind = 'voided'{scope})
           OR NEW.amount_delta <> round(NEW.amount_delta, NEW.minor_units)
           OR NEW.claimed_amount <> round(NEW.claimed_amount, NEW.minor_units)
           OR NEW.amount_delta::text IN ('NaN', 'Infinity', '-Infinity')
           OR NEW.claimed_amount::text IN ('NaN', 'Infinity', '-Infinity')
           OR trim(NEW.source_owner) = '' OR trim(NEW.source_ref) = '' OR trim(NEW.claim_ref) = ''
        THEN
            RAISE EXCEPTION 'invalid withholding document or posting affinity' USING ERRCODE = 'check_violation';
        END IF;
        -- Typed command evidence remains the authority; scalar/JSON disagreement
        -- must not be admitted even when SQL bypasses that public command.
        IF jsonb_typeof(NEW.evidence::jsonb) IS DISTINCT FROM 'object'
           OR (NEW.evidence->>'discharge_id') IS DISTINCT FROM NEW.id::text
           OR (NEW.evidence->>'document_id') IS DISTINCT FROM NEW.document_id::text
           OR (NEW.evidence->>'claim_ref') IS DISTINCT FROM NEW.claim_ref
           OR (NEW.evidence->'tax_evidence'->>'source_owner') IS DISTINCT FROM NEW.tax_source_owner
           OR (NEW.evidence->'tax_evidence'->>'determination_ref') IS DISTINCT FROM NEW.determination_ref
           OR (NEW.evidence->'tax_evidence'->>'component_ref') IS DISTINCT FROM NEW.component_ref
           OR (NEW.evidence->'tax_evidence'->>'billing_account_id') IS DISTINCT FROM NEW.billing_account_id::text
           OR (NEW.evidence->'tax_evidence'->>'document_id') IS DISTINCT FROM NEW.document_id::text
           OR {"(NEW.evidence->'tax_evidence'->'scope')::jsonb IS DISTINCT FROM '{}'::jsonb" if platform else "(NEW.evidence->'tax_evidence'->'scope'->>'tenant_id') IS DISTINCT FROM NEW.tenant_id::text"}
           OR (NEW.evidence->'claimed_amount'->>'amount')::numeric IS DISTINCT FROM NEW.claimed_amount
           OR (NEW.evidence->'accepted_delta'->>'amount')::numeric IS DISTINCT FROM NEW.amount_delta
           OR (NEW.evidence->'claimed_amount'->>'currency') IS DISTINCT FROM NEW.currency
           OR (NEW.evidence->'accepted_delta'->>'currency') IS DISTINCT FROM NEW.currency
           OR (NEW.evidence->'claimed_amount'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
           OR (NEW.evidence->'accepted_delta'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
           OR (NEW.evidence->'tax_evidence'->'amount'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
           OR (NEW.evidence->'tax_evidence'->'amount'->>'currency') IS DISTINCT FROM NEW.currency
           OR (NEW.evidence->'tax_evidence'->'amount'->>'amount')::numeric < NEW.claimed_amount
           OR (NEW.evidence->'tax_evidence'->'amount'->>'amount') IS NULL
           OR (NEW.evidence->'tax_evidence'->'amount'->>'amount')::numeric::text IN ('NaN', 'Infinity', '-Infinity')
           OR nullif(trim(NEW.evidence->>'authorization_actor_ref'), '') IS NULL
           OR nullif(trim(NEW.evidence->>'authorization_evidence_ref'), '') IS NULL
           OR (NEW.evidence->>'settlement_id') IS DISTINCT FROM NEW.settlement_id::text
           OR (NEW.evidence->>'allocation_id') IS DISTINCT FROM NEW.allocation_id::text
           OR (NEW.evidence->>'reverses_discharge_id') IS DISTINCT FROM NEW.reverses_discharge_id::text
        THEN
            RAISE EXCEPTION 'withholding evidence disagrees with persisted scalars' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.settlement_id IS NOT NULL THEN
            SELECT * INTO linked FROM mod_billing.{prefix}confirmed_settlements x
             WHERE x.id = NEW.settlement_id{scope};
            IF linked.id IS NULL OR linked.billing_account_id <> NEW.billing_account_id
               OR linked.currency <> NEW.currency OR linked.minor_units <> NEW.minor_units THEN
                RAISE EXCEPTION 'invalid withholding settlement link' USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        IF NEW.allocation_id IS NOT NULL THEN
            SELECT * INTO linked FROM mod_billing.{prefix}allocation_effects x
             WHERE x.id = NEW.allocation_id{scope};
            IF linked.id IS NULL OR linked.billing_account_id <> NEW.billing_account_id
               OR linked.document_id IS DISTINCT FROM NEW.document_id
               OR linked.settlement_id IS DISTINCT FROM NEW.settlement_id
               OR linked.currency <> NEW.currency OR linked.minor_units <> NEW.minor_units THEN
                RAISE EXCEPTION 'invalid withholding allocation link' USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        IF NEW.reverses_discharge_id IS NOT NULL THEN
            SELECT * INTO original FROM mod_billing.{table} x
             WHERE x.id = NEW.reverses_discharge_id{scope} FOR UPDATE;
            IF original.id IS NULL OR original.reverses_discharge_id IS NOT NULL
               OR NEW.id = original.id OR original.billing_account_id <> NEW.billing_account_id
               OR original.document_id <> NEW.document_id OR original.currency <> NEW.currency
               OR original.minor_units <> NEW.minor_units OR original.amount_delta <> -NEW.amount_delta
               OR original.claimed_amount <> NEW.claimed_amount OR original.claim_ref <> NEW.claim_ref
               OR original.settlement_id IS DISTINCT FROM NEW.settlement_id
               OR original.allocation_id IS DISTINCT FROM NEW.allocation_id
               OR original.evidence::jsonb->'tax_evidence' IS DISTINCT FROM NEW.evidence::jsonb->'tax_evidence'
               OR original.evidence::jsonb->'certificate_observation' IS DISTINCT FROM NEW.evidence::jsonb->'certificate_observation'
               OR grp.group_kind <> 'withholding_reversed'
               OR grp.reverses_group_id IS DISTINCT FROM original.posting_group_id THEN
                RAISE EXCEPTION 'withholding correction must exactly reverse original evidence' USING ERRCODE = 'check_violation';
            END IF;
        ELSE
            IF NOT mod_billing.has_{prefix}live_invoice_issuance(
                NEW.document_id, NEW.billing_account_id, NEW.currency, NEW.minor_units{tenant_call}
            ) THEN
                RAISE EXCEPTION 'withholding requires one unreversed structurally bound invoice issuance'
                  USING ERRCODE = 'check_violation';
            END IF;
            IF grp.group_kind <> 'withholding_accepted' OR grp.reverses_group_id IS NOT NULL THEN
                RAISE EXCEPTION 'withholding acceptance needs its own posting' USING ERRCODE = 'check_violation';
            END IF;
            SELECT coalesce(sum(x.amount_delta), 0) INTO used FROM mod_billing.{table} x
             WHERE x.document_id = NEW.document_id{scope};
            used := used + (SELECT coalesce(sum(x.amount_delta), 0)
                FROM mod_billing.{prefix}allocation_effects x WHERE x.document_id = NEW.document_id{scope});
            used := used + (SELECT coalesce(sum(x.grand_total), 0)
                FROM mod_billing.{prefix}documents x WHERE x.credits_document_id = NEW.document_id
                  AND x.document_kind = 'credit_note' AND x.issued_at IS NOT NULL{scope}
                  AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}document_events e
                      WHERE e.document_id = x.id AND e.event_kind = 'voided'
                      {'' if platform else 'AND e.tenant_id = x.tenant_id'}));
            IF NEW.amount_delta > doc.grand_total - used THEN
                RAISE EXCEPTION 'withholding exceeds invoice outstanding' USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {table}_admission BEFORE INSERT ON mod_billing.{table}
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}withholding();
    CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON mod_billing.{table}
      FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
    """)  # nosec B608 -- fixed plane names and scope predicates only
    op.execute(f"""
    CREATE FUNCTION mod_billing.check_{prefix}withholding_posting() RETURNS trigger AS $$
    DECLARE
        discharge record;
        grp record;
        group_id uuid;
    BEGIN
        IF TG_TABLE_NAME = '{prefix}posting_groups' THEN
            group_id := NEW.id;
        ELSE
            group_id := NEW.posting_group_id;
        END IF;
        SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x
         WHERE x.id = group_id{scope};
        IF grp.group_kind NOT IN ('withholding_accepted', 'withholding_reversed') THEN RETURN NEW; END IF;
        SELECT * INTO discharge FROM mod_billing.{table} x
         WHERE x.posting_group_id = group_id{scope};
        IF grp.group_kind = 'withholding_reversed' AND NOT EXISTS (
            SELECT 1 FROM mod_billing.{table} x
            JOIN mod_billing.{prefix}posting_groups original_group
              ON original_group.id = x.posting_group_id
              {'' if platform else 'AND original_group.tenant_id = x.tenant_id'}
            WHERE x.id = discharge.reverses_discharge_id{scope}
              AND original_group.id = grp.reverses_group_id
              AND original_group.group_kind = 'withholding_accepted'
              AND x.reverses_discharge_id IS NULL
              AND x.document_id = discharge.document_id
              AND x.billing_account_id = discharge.billing_account_id
              AND x.amount_delta = -discharge.amount_delta
              AND x.currency = discharge.currency AND x.minor_units = discharge.minor_units
        ) THEN
            RAISE EXCEPTION 'withholding reversal requires its actual exact inverse discharge row'
              USING ERRCODE = 'check_violation';
        END IF;
        IF discharge.id IS NOT NULL AND discharge.reverses_discharge_id IS NULL
           AND (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{table} x
                WHERE x.document_id = discharge.document_id{scope}) > 0
           AND NOT mod_billing.has_{prefix}live_invoice_issuance(
                discharge.document_id, discharge.billing_account_id,
                discharge.currency, discharge.minor_units{tenant_call}
           ) THEN
            RAISE EXCEPTION 'active withholding requires live invoice issuance'
              USING ERRCODE = 'check_violation';
        END IF;
        IF discharge.id IS NULL OR
           EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects x
             WHERE x.posting_group_id = group_id{scope}) OR
           (SELECT count(*) FROM mod_billing.{prefix}posting_effects x
             WHERE x.posting_group_id = group_id{scope}) <> 1 OR
           NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects x
             WHERE x.posting_group_id = group_id{scope}
               AND x.lane = 'receivable' AND x.amount_delta = -discharge.amount_delta
               AND x.currency = discharge.currency AND x.minor_units = discharge.minor_units
               AND x.billing_account_id = discharge.billing_account_id) THEN
            RAISE EXCEPTION 'withholding posting must contain exactly its noncash AR effect' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE CONSTRAINT TRIGGER {table}_posting AFTER INSERT ON mod_billing.{table}
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}withholding_posting();
    CREATE CONSTRAINT TRIGGER {prefix}posting_groups_withholding AFTER INSERT ON mod_billing.{prefix}posting_groups
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}withholding_posting();
    CREATE CONSTRAINT TRIGGER {prefix}posting_effects_withholding AFTER INSERT ON mod_billing.{prefix}posting_effects
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}withholding_posting();
    CREATE CONSTRAINT TRIGGER {prefix}allocation_effects_withholding AFTER INSERT ON mod_billing.{prefix}allocation_effects
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}withholding_posting();
    """)  # nosec B608 -- fixed plane names and scope predicates only


def _apply(*, upgrade: bool) -> None:
    planes = selected_module_planes("billing")
    prefixes = [
        "platform_" if plane == ModulePlane.PLATFORM else ""
        for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM)
        if plane in planes
    ]
    connection = op.get_bind()
    previous = {
        name: connection.execute(sa.text(f"SHOW {name}")).scalar_one()
        for name in ("lock_timeout", "statement_timeout", "row_security")
    }
    tables = [
        f"{prefix}{table}"
        for table in (
            "billing_accounts",
            "documents",
            "document_events",
            "posting_groups",
            "posting_effects",
            "confirmed_settlements",
            "allocation_effects",
            "receivable_exposure_facts",
            *(("withholding_discharges",) if not upgrade else ()),
        )
        for prefix in prefixes
    ]
    op.execute(f"""
        SET LOCAL lock_timeout = '5s';
        SET LOCAL statement_timeout = '60s';
        LOCK TABLE mod_billing.{', mod_billing.'.join(tables)} IN ACCESS EXCLUSIVE MODE;
        {'SET LOCAL row_security = off;' if not upgrade else ''}
    """)  # nosec B608 -- fixed selected plane/table names only
    if not upgrade:
        # row_security=off refuses to filter rows for a role subject to FORCE
        # RLS; a bypass role instead sees the entire population. Both fail closed.
        for prefix in prefixes:
            if (
                connection.execute(
                    sa.text(
                        "SELECT 1 FROM mod_billing.platform_withholding_discharges LIMIT 1"
                        if prefix
                        else "SELECT 1 FROM mod_billing.withholding_discharges LIMIT 1"
                    )
                ).first()
                is not None
            ):
                raise RuntimeError(
                    "cannot downgrade Billing while withholding evidence exists"
                )
    for prefix in prefixes:
        if upgrade:
            _create(bool(prefix))
        else:
            op.execute(f"""
                DROP TRIGGER {prefix}posting_groups_withholding_invoice_reversal ON mod_billing.{prefix}posting_groups;
                DROP TRIGGER {prefix}posting_groups_withholding ON mod_billing.{prefix}posting_groups;
                DROP TRIGGER {prefix}posting_effects_withholding ON mod_billing.{prefix}posting_effects;
                DROP TRIGGER {prefix}allocation_effects_withholding ON mod_billing.{prefix}allocation_effects;
            """)  # nosec B608 -- fixed plane names only
            op.drop_table(f"{prefix}withholding_discharges", schema=_SCHEMA)
            op.execute(f"""
                DROP FUNCTION mod_billing.admit_{prefix}withholding();
                DROP FUNCTION mod_billing.check_{prefix}withholding_posting();
                DROP FUNCTION mod_billing.guard_{prefix}withholding_invoice_reversal();
                DROP FUNCTION mod_billing.has_{prefix}live_invoice_issuance(uuid, uuid, text, integer{", uuid" if not prefix else ""});
            """)  # nosec B608 -- fixed plane names only
    for name, value in previous.items():
        connection.execute(
            sa.text("SELECT pg_catalog.set_config(:name, :value, true)"),
            {"name": name, "value": value},
        )


def upgrade() -> None:
    _apply(upgrade=True)


def downgrade() -> None:
    _apply(upgrade=False)
