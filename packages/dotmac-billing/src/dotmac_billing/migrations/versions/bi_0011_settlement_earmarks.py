"""Immutable whole-settlement earmarks and linked capacity attribution.

Billing's public financial owners validate economic admissibility. These tables
freeze every original fact, including against later BEFORE-trigger rewrites;
they do not grant arbitrary SQL callers a correction authorization verifier.
"""

from __future__ import annotations

# Closed Billing-owned plane/table/column identifiers only.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0011_settlement_earmarks"
down_revision = "bi_0010_line_source_key"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
_TABLES = (
    "settlement_earmarks",
    "settlement_earmark_targets",
    "settlement_earmark_effects",
)


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


def _tables(prefix: str) -> None:
    scope = [] if prefix else ["tenant_id"]
    for kind in _TABLES:
        table = prefix + kind
        columns: list[SchemaItem] = [
            sa.Column("id", sa.Uuid(), primary_key=True),
            *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
            sa.Column("billing_account_id", sa.Uuid(), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False),
            sa.Column("minor_units", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                *scope, "id", "billing_account_id", name=f"uq_{table}_account"
            ),
            sa.CheckConstraint("minor_units BETWEEN 0 AND 6"),
            sa.ForeignKeyConstraint(
                [*scope, "billing_account_id"],
                [f"{_SCHEMA}.{prefix}billing_accounts.{key}" for key in (*scope, "id")],
                ondelete="RESTRICT",
            ),
        ]
        if not prefix:
            columns.append(
                sa.ForeignKeyConstraint(
                    ["tenant_id"], ["public.tenants.id"], ondelete="RESTRICT"
                )
            )
        links: list[tuple[str, str]] = []
        if kind == "settlement_earmarks":
            columns.append(
                sa.UniqueConstraint(
                    *scope,
                    "id",
                    "billing_account_id",
                    "settlement_id",
                    name=f"uq_{table}_settlement_binding",
                )
            )
            columns += [
                sa.Column("settlement_id", sa.Uuid(), nullable=False),
                sa.Column("amount", sa.Numeric(20, 6), nullable=False),
                sa.Column("producer_ref", sa.String(255), nullable=False),
                sa.UniqueConstraint(
                    *scope, "settlement_id", name=f"uq_{table}_settlement"
                ),
                sa.CheckConstraint("amount > 0"),
            ]
            links.append(("settlement_id", "confirmed_settlements"))
        else:
            columns.append(sa.Column("earmark_id", sa.Uuid(), nullable=False))
            links.append(("earmark_id", "settlement_earmarks"))
            if kind == "settlement_earmark_targets":
                columns += [
                    sa.Column("source_system", sa.String(120), nullable=False),
                    sa.Column("source_kind", sa.String(120), nullable=False),
                    sa.Column("source_fact_id", sa.String(255), nullable=False),
                    sa.Column("source_fact_version", sa.String(120), nullable=False),
                    sa.Column("amount", sa.Numeric(20, 6), nullable=False),
                    sa.CheckConstraint("amount > 0"),
                    sa.UniqueConstraint(
                        *scope,
                        "earmark_id",
                        "source_system",
                        "source_kind",
                        "source_fact_id",
                        "source_fact_version",
                        name=f"uq_{table}_source",
                    ),
                ]
            else:
                columns += [
                    sa.Column("posting_group_id", sa.Uuid()),
                    sa.Column("allocation_id", sa.Uuid()),
                    sa.Column("original_effect_id", sa.Uuid()),
                    sa.Column("effect_kind", sa.String(32), nullable=False),
                    sa.Column("held_delta", sa.Numeric(20, 6), nullable=False),
                    sa.CheckConstraint("held_delta <> 0"),
                    sa.CheckConstraint(
                        "effect_kind IN ('consumption','restore','release','release_inverse','refund','refund_inverse','backing','backing_inverse')"
                    ),
                ]
                links += [
                    ("posting_group_id", "posting_groups"),
                    ("allocation_id", "allocation_effects"),
                    ("original_effect_id", "settlement_earmark_effects"),
                ]
        if kind != "settlement_earmark_targets":
            columns += [
                sa.Column("source_ref", sa.String(255), nullable=False),
                sa.Column("request_digest", sa.String(64), nullable=False),
                sa.Column("evidence_digest", sa.String(64), nullable=False),
                sa.Column("evidence", sa.JSON(), nullable=False),
                sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            ]
        for column, target in links:
            columns.append(
                sa.ForeignKeyConstraint(
                    [*scope, column, "billing_account_id"],
                    [
                        f"{_SCHEMA}.{prefix}{target}.{key}"
                        for key in (*scope, "id", "billing_account_id")
                    ],
                    ondelete="RESTRICT",
                )
            )
        op.create_table(table, *columns, schema=_SCHEMA)
        op.execute(
            f"REVOKE ALL ON {_SCHEMA}.{table} FROM PUBLIC, app_user, platform_api"
        )
        role = "platform_api" if prefix else "app_user"
        if not prefix:
            op.execute(f"""
                ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY;
                CREATE POLICY tenant_isolation ON {_SCHEMA}.{table}
                  USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                  WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
            """)
        op.execute(f"GRANT SELECT, INSERT ON {_SCHEMA}.{table} TO {role}")
        op.execute(f"""
            CREATE FUNCTION {_SCHEMA}.freeze_{table}() RETURNS trigger
            LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
            BEGIN
              RAISE EXCEPTION 'settlement earmark evidence is immutable' USING ERRCODE = '23514';
            END $$;
            CREATE TRIGGER final_freeze_{table} AFTER UPDATE OR DELETE ON {_SCHEMA}.{table}
              FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.freeze_{table}();
            CREATE TRIGGER refuse_truncate_{table} BEFORE TRUNCATE ON {_SCHEMA}.{table}
              FOR EACH STATEMENT EXECUTE FUNCTION {_SCHEMA}.freeze_{table}();
        """)
    role = "platform_api" if prefix else "app_user"
    op.execute(f"GRANT UPDATE(id) ON {_SCHEMA}.{prefix}billing_accounts TO {role}")


def upgrade() -> None:
    for prefix in _prefixes():
        _tables(prefix)


def downgrade() -> None:
    # One claim is a permanent financial provenance commitment. Refuse before
    # changing any schema, including an empty second plane in a dual-plane DB.
    prefixes = _prefixes()
    for prefix in prefixes:
        if not prefix:
            op.execute(
                f"ALTER TABLE {_SCHEMA}.settlement_earmarks NO FORCE ROW LEVEL SECURITY"
            )
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM {_SCHEMA}.{prefix}settlement_earmarks)
              THEN RAISE EXCEPTION 'cannot downgrade retained settlement earmarks' USING ERRCODE = '23514'; END IF;
            END $$;
        """)
        if not prefix:
            op.execute(
                f"ALTER TABLE {_SCHEMA}.settlement_earmarks FORCE ROW LEVEL SECURITY"
            )
    for prefix in reversed(prefixes):
        for kind in reversed(_TABLES):
            table = prefix + kind
            op.drop_table(table, schema=_SCHEMA)
            op.execute(f"DROP FUNCTION {_SCHEMA}.freeze_{table}()")
