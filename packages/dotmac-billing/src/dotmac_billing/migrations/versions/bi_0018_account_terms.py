"""Account terms: dated, append-only contract profile and tenant defaults.

Two append-only tables, on both planes:

* ``billing_account_terms_versions`` -- per billing account, one row per dated
  change of the account's own terms (contract type, period, postpaid billing
  timing, postpaid grace). Every term is nullable: NULL means "use the tenant
  default". A version is a complete statement of the account's overrides, so a
  later version with a NULL term returns that term to the default.
* ``billing_terms_defaults_versions`` -- the tenant-wide default, one row per
  dated change, every term NOT NULL.

``(effective_from, recorded_at)`` is unique per account (and per tenant for
defaults): a version recorded later at the same ``effective_from`` supersedes the
earlier one while both rows stay, so a wrong not-yet-effective version is
correctable. Resolution (``account_terms.py``) takes the latest recorded. Back-dating
is refused by the database: a BEFORE INSERT trigger stamps ``recorded_at`` from the
database clock (a caller-supplied value is overwritten) and refuses a row whose
``effective_from`` is earlier than that instant. Rows are never updated or
deleted (the shared ``refuse_mutation`` trigger); the online role has SELECT and
INSERT only. Billing computes no period arithmetic: callers read the terms at the
start of the period they are charging.

No existing table, column or row changes. No downgrade.
"""

from __future__ import annotations

# All DDL identifiers are closed Billing plane/table literals (see the per-line
# S608 suppressions below).
from collections.abc import Iterator

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0018_account_terms"
down_revision = "bi_0017_open_documents_index"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"


def _planes() -> Iterator[bool]:
    planes = selected_module_planes("billing")
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane in planes:
            yield plane is ModulePlane.PLATFORM


def _terms_checks(table: str, *, nullable: bool) -> tuple[SchemaItem, ...]:
    def member(column: str, values: str) -> str:
        return (
            f"({column} IS NULL OR {column} IN ({values}))"
            if nullable
            else f"{column} IN ({values})"
        )

    grace = (
        "(postpaid_grace IS NULL OR postpaid_grace >= interval '0')"
        if nullable
        else "postpaid_grace >= interval '0'"
    )
    return (
        sa.CheckConstraint(
            member("contract_type", "'prepaid', 'postpaid'"),
            name=f"ck_{table}_contract_type",
        ),
        sa.CheckConstraint(
            member("period", "'daily', 'monthly', 'quarterly', 'annually'"),
            name=f"ck_{table}_period",
        ),
        sa.CheckConstraint(
            member("postpaid_billing_timing", "'period_start', 'period_end'"),
            name=f"ck_{table}_timing",
        ),
        sa.CheckConstraint(grace, name=f"ck_{table}_grace"),
        sa.CheckConstraint(
            "length(trim(actor_ref)) > 0 AND length(trim(reason)) > 0",
            name=f"ck_{table}_attribution",
        ),
    )


def _term_columns(*, nullable: bool) -> tuple[SchemaItem, ...]:
    return (
        sa.Column("contract_type", sa.String(16), nullable=nullable),
        sa.Column("period", sa.String(16), nullable=nullable),
        sa.Column("postpaid_billing_timing", sa.String(16), nullable=nullable),
        sa.Column("postpaid_grace", sa.Interval(), nullable=nullable),
    )


def _common_tail() -> tuple[SchemaItem, ...]:
    return (
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
    )


def _create_account_terms(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    table = f"{prefix}billing_account_terms_versions"
    scope = [] if platform else ["tenant_id"]
    elements: list[SchemaItem] = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        *_term_columns(nullable=True),
        *_common_tail(),
        sa.ForeignKeyConstraint(
            [*scope, "billing_account_id"],
            [
                *(f"{_SCHEMA}.{prefix}billing_accounts.{key}" for key in scope),
                f"{_SCHEMA}.{prefix}billing_accounts.id",
            ],
            name=f"fk_{table}_account",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            *scope,
            "billing_account_id",
            "effective_from",
            "recorded_at",
            name=f"uq_{table}_effective",
        ),
        *_terms_checks(table, nullable=True),
    ]
    if not platform:
        elements.append(
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["public.tenants.id"],
                name=f"fk_{table}_tenant",
                ondelete="CASCADE",
            )
        )
    op.create_table(table, *elements, schema=_SCHEMA)
    _guard(table, prefix, platform)


def _create_default_terms(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    table = f"{prefix}billing_terms_defaults_versions"
    scope = [] if platform else ["tenant_id"]
    elements: list[SchemaItem] = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        *_term_columns(nullable=False),
        *_common_tail(),
        sa.UniqueConstraint(
            *scope, "effective_from", "recorded_at", name=f"uq_{table}_effective"
        ),
        *_terms_checks(table, nullable=False),
    ]
    if not platform:
        elements.append(
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["public.tenants.id"],
                name=f"fk_{table}_tenant",
                ondelete="CASCADE",
            )
        )
    op.create_table(table, *elements, schema=_SCHEMA)
    _guard(table, prefix, platform)


def _guard(table: str, prefix: str, platform: bool) -> None:
    """Grants, forced RLS (tenant plane), append-only and back-dating guards."""
    if platform:
        op.execute(f"""
            REVOKE ALL ON mod_billing.{table} FROM PUBLIC, app_user, platform_api;
            GRANT SELECT, INSERT ON mod_billing.{table} TO platform_api;
        """)  # nosec B608 -- closed table identifiers; no input reaches this DDL
    else:
        op.execute(f"""
            ALTER TABLE mod_billing.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE mod_billing.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON mod_billing.{table}
              USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
              WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
            REVOKE ALL ON mod_billing.{table} FROM PUBLIC, app_user, platform_api;
            GRANT SELECT, INSERT ON mod_billing.{table} TO app_user;
        """)  # nosec B608 -- closed table identifiers; no input reaches this DDL
    op.execute(f"""
        CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE
          ON mod_billing.{table}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        CREATE FUNCTION mod_billing.admit_{table}() RETURNS trigger AS $$
        BEGIN
            -- Database time, never the caller's: a supplied recorded_at is
            -- overwritten, and a version cannot start before it was recorded.
            NEW.recorded_at := clock_timestamp();
            IF NEW.effective_from < NEW.recorded_at THEN
                RAISE EXCEPTION 'account terms cannot be back-dated'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER {table}_admission BEFORE INSERT ON mod_billing.{table}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{table}();
    """)  # nosec B608 -- closed table identifiers; no input reaches this DDL


def upgrade() -> None:
    for platform in _planes():
        _create_account_terms(platform)
        _create_default_terms(platform)


def downgrade() -> None:
    raise RuntimeError("Account terms are append-only financial evidence; no downgrade")
