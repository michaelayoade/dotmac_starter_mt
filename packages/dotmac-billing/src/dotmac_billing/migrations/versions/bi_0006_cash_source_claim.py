"""Immutable provenance-only claims for every actual confirmed settlement.

Fence older writers before upgrade: every new settlement must now carry its
claim by outer transaction completion. No monetary or retry evidence is edited.
"""

from __future__ import annotations

# SQL identifiers are closed Billing plane/table literals.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0006_cash_source_claim"
down_revision = "bi_0005_non_cash_credit"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"


def _elements(platform: bool) -> tuple[SchemaItem, ...]:
    prefix = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]
    parent = f"{_SCHEMA}.{prefix}confirmed_settlements"
    columns: list[SchemaItem] = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
        sa.Column("settlement_id", sa.Uuid(), nullable=False),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(120), nullable=False),
        sa.Column("source_settlement_key", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            *scope, "settlement_id", name=f"uq_{prefix}cash_claim_settlement"
        ),
        sa.UniqueConstraint(
            *scope,
            "source_system",
            "source_settlement_key",
            name=f"uq_{prefix}cash_claim_source",
        ),
        sa.ForeignKeyConstraint(
            [
                *scope,
                "settlement_id",
                "billing_account_id",
                "source_system",
                "source_settlement_key",
                "currency",
                "minor_units",
            ],
            [
                f"{parent}.{key}"
                for key in (
                    *scope,
                    "id",
                    "billing_account_id",
                    "source_system",
                    "source_settlement_key",
                    "currency",
                    "minor_units",
                )
            ],
            name=f"fk_{prefix}cash_claim_settlement",
            ondelete="RESTRICT",
        ),
    ]
    if not platform:
        columns.append(
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["public.tenants.id"],
                name="fk_cash_source_claims_tenant",
                ondelete="CASCADE",
            )
        )
    return tuple(columns)


def _create(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]
    settlement = f"{prefix}confirmed_settlements"
    claim = f"{prefix}cash_source_claims"
    op.create_unique_constraint(
        f"uq_{prefix}settlements_claim_binding",
        settlement,
        [
            *scope,
            "id",
            "billing_account_id",
            "source_system",
            "source_settlement_key",
            "currency",
            "minor_units",
        ],
        schema=_SCHEMA,
    )
    # Literal table declarations remain visible to the composed DDL scanner.
    if platform:
        op.create_table("platform_cash_source_claims", *_elements(True), schema=_SCHEMA)
    else:
        op.create_table("cash_source_claims", *_elements(False), schema=_SCHEMA)
    if platform:
        op.execute("""
            REVOKE ALL ON mod_billing.platform_cash_source_claims FROM app_user;
            GRANT SELECT, INSERT ON mod_billing.platform_cash_source_claims TO platform_api;
        """)
    else:
        op.execute("""
            ALTER TABLE mod_billing.cash_source_claims ENABLE ROW LEVEL SECURITY;
            ALTER TABLE mod_billing.cash_source_claims FORCE ROW LEVEL SECURITY;
            CREATE POLICY cash_source_claims_tenant_isolation ON mod_billing.cash_source_claims
              USING (tenant_id = public.app_current_tenant_id())
              WITH CHECK (tenant_id = public.app_current_tenant_id());
            GRANT SELECT, INSERT ON mod_billing.cash_source_claims TO app_user;
            REVOKE ALL ON mod_billing.cash_source_claims FROM platform_api;
        """)
    scope_columns = "tenant_id, " if not platform else ""
    joined_scope = "AND c.tenant_id = s.tenant_id" if not platform else ""
    op.execute(f"""
        INSERT INTO mod_billing.{claim}
          (id, {scope_columns}settlement_id, billing_account_id, source_system,
           source_settlement_key, currency, minor_units, recorded_at)
        SELECT id, {scope_columns}id, billing_account_id, source_system,
          source_settlement_key, currency, minor_units, statement_timestamp()
        FROM mod_billing.{settlement};
        DO $$ BEGIN
          IF (SELECT count(*) FROM mod_billing.{claim}) <>
             (SELECT count(*) FROM mod_billing.{settlement})
          OR EXISTS (
            SELECT 1 FROM mod_billing.{settlement} s
            LEFT JOIN mod_billing.{claim} c ON c.settlement_id = s.id {joined_scope}
            WHERE c.id IS NULL OR c.billing_account_id <> s.billing_account_id
              OR c.source_system <> s.source_system
              OR c.source_settlement_key <> s.source_settlement_key
              OR c.currency <> s.currency OR c.minor_units <> s.minor_units
          ) THEN
            RAISE EXCEPTION 'cash-source backfill must preserve every exact settlement identity'
              USING ERRCODE = 'check_violation';
          END IF;
        END $$;
        CREATE TRIGGER {claim}_append_only
          BEFORE UPDATE OR DELETE ON mod_billing.{claim}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
    """)  # nosec B608 -- fixed planes and scope fragments
    scope_match = "AND c.tenant_id = NEW.tenant_id" if not platform else ""
    op.execute(f"""
        CREATE FUNCTION mod_billing.check_{prefix}settlement_cash_claim()
        RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM mod_billing.{claim} c
            WHERE c.settlement_id = NEW.id {scope_match}
              AND c.billing_account_id = NEW.billing_account_id
              AND c.source_system = NEW.source_system
              AND c.source_settlement_key = NEW.source_settlement_key
              AND c.currency = NEW.currency AND c.minor_units = NEW.minor_units
          ) THEN
            RAISE EXCEPTION 'confirmed settlement requires its exact immutable cash-source claim; fence old writers'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE CONSTRAINT TRIGGER {settlement}_cash_claim_complete
          AFTER INSERT ON mod_billing.{settlement}
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION mod_billing.check_{prefix}settlement_cash_claim();
    """)  # nosec B608 -- fixed planes and scope fragments


def _apply(*, upgrade: bool) -> None:
    prefixes = [
        "platform_" if plane == ModulePlane.PLATFORM else ""
        for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM)
        if plane in selected_module_planes("billing")
    ]
    connection = op.get_bind()
    previous = {
        name: connection.execute(sa.text(f"SHOW {name}")).scalar_one()
        for name in ("lock_timeout", "statement_timeout", "row_security")
    }
    tables = [f"{prefix}confirmed_settlements" for prefix in prefixes]
    if not upgrade:
        tables.extend(f"{prefix}cash_source_claims" for prefix in prefixes)
    op.execute(f"""
        SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '60s';
        LOCK TABLE mod_billing.{', mod_billing.'.join(tables)} IN ACCESS EXCLUSIVE MODE;
        SET LOCAL row_security = off;
    """)  # nosec B608 -- fixed qualified module tables
    if not upgrade:
        for prefix in prefixes:
            if (
                connection.execute(
                    sa.text(
                        "SELECT 1 FROM mod_billing.platform_cash_source_claims LIMIT 1"
                        if prefix
                        else "SELECT 1 FROM mod_billing.cash_source_claims LIMIT 1"
                    )
                ).first()
                is not None
            ):
                raise RuntimeError(
                    "cannot downgrade Billing while cash-source claims exist"
                )
    for prefix in prefixes:
        if upgrade:
            _create(bool(prefix))
        else:
            op.execute(f"""
                DROP TRIGGER {prefix}confirmed_settlements_cash_claim_complete
                  ON mod_billing.{prefix}confirmed_settlements;
                DROP FUNCTION mod_billing.check_{prefix}settlement_cash_claim();
            """)  # nosec B608 -- fixed plane/table names
            op.drop_table(f"{prefix}cash_source_claims", schema=_SCHEMA)
            op.drop_constraint(
                f"uq_{prefix}settlements_claim_binding",
                f"{prefix}confirmed_settlements",
                type_="unique",
                schema=_SCHEMA,
            )
    for name, value in previous.items():
        connection.execute(
            sa.text("SELECT pg_catalog.set_config(:name, :value, true)"),
            {"name": name, "value": value},
        )


def upgrade() -> None:
    _apply(upgrade=True)


def downgrade() -> None:
    _apply(upgrade=False)
