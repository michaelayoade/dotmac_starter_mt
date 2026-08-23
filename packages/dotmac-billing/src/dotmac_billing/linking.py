"""Plane-specific product-subject links to Billing accounts.

The helpers emit a link table into the adopter's own migration lineage.  They
are deliberately separate functions: a boolean plane flag would make one
security shape the accidental default.  Tenant links carry a composite
``(tenant_id, billing_account_id)`` foreign key and forced RLS; platform links
carry no tenant column or RLS and revoke both table- and column-level access
from the tenant application role.
"""

from __future__ import annotations

from typing import Final, Literal

import sqlalchemy as sa
from dotmac_kernel.namespaces import module_schema

from alembic import op

__all__ = [
    "BILLING_ACCOUNTS_TABLE",
    "MODULE_SCHEMA",
    "PLATFORM_BILLING_ACCOUNTS_TABLE",
    "drop_billing_account_link",
    "link_platform_billing_account",
    "link_tenant_billing_account",
]

MODULE_SCHEMA: Final[str] = module_schema("billing")
BILLING_ACCOUNTS_TABLE: Final[str] = "billing_accounts"
PLATFORM_BILLING_ACCOUNTS_TABLE: Final[str] = "platform_billing_accounts"

OnDelete = Literal["CASCADE", "RESTRICT"]
_IDENTIFIER_LIMIT: Final[int] = 63
_COLUMN_PRIVILEGES: Final[tuple[str, ...]] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "REFERENCES",
)


def link_tenant_billing_account(
    *,
    table_name: str,
    subject_table: str,
    subject_column: str = "subject_id",
    subject_schema: str = "public",
    subject_pk: str = "id",
    on_delete_subject: OnDelete,
    on_delete_billing_account: OnDelete = "CASCADE",
    schema: str = "public",
    app_role: str = "app_user",
) -> None:
    """Emit a tenant-plane Billing-account↔product-subject link table.

    ``on_delete_subject`` is required because the adopter owns whether deleting
    its subject cascades or is refused.  The generated Billing reference is
    composite, preventing a link row for one tenant from naming another
    tenant's account.
    """
    _validate_inputs(
        table_name=table_name,
        subject_table=subject_table,
        subject_column=subject_column,
        subject_schema=subject_schema,
        subject_pk=subject_pk,
        schema=schema,
        roles=(app_role,),
    )
    for identifier in (
        f"ix_{table_name}_tenant_id",
        f"{table_name}_tenant_isolation",
    ):
        _check_identifier(identifier)
    _validate_on_delete(on_delete_subject)
    _validate_on_delete(on_delete_billing_account)

    op.create_table(
        table_name,
        sa.Column(
            "tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "billing_account_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            subject_column,
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("billing_account_id", name=f"pk_{table_name}"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "billing_account_id"],
            [
                f"{MODULE_SCHEMA}.{BILLING_ACCOUNTS_TABLE}.tenant_id",
                f"{MODULE_SCHEMA}.{BILLING_ACCOUNTS_TABLE}.id",
            ],
            name=f"fk_{table_name}_billing_account",
            ondelete=on_delete_billing_account,
        ),
        sa.ForeignKeyConstraint(
            [subject_column],
            [f"{subject_schema}.{subject_table}.{subject_pk}"],
            name=f"fk_{table_name}_subject",
            ondelete=on_delete_subject,
        ),
        schema=schema,
    )
    op.create_index(
        f"ix_{table_name}_{subject_column}",
        table_name,
        [subject_column],
        schema=schema,
    )
    op.create_index(
        f"ix_{table_name}_tenant_id",
        table_name,
        ["tenant_id"],
        schema=schema,
    )
    qualified = f"{schema}.{table_name}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_tenant_isolation ON {qualified} "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO {app_role}")


def link_platform_billing_account(
    *,
    table_name: str,
    subject_table: str,
    subject_column: str = "subject_id",
    subject_schema: str = "public",
    subject_pk: str = "id",
    on_delete_subject: OnDelete,
    on_delete_billing_account: OnDelete = "CASCADE",
    schema: str = "public",
    app_role: str = "app_user",
    platform_roles: tuple[str, ...] = ("platform_api", "app_admin"),
) -> None:
    """Emit a platform-plane Billing-account↔product-subject link table."""
    if not platform_roles:
        raise ValueError(
            "platform_roles must name at least one online role; an unreachable "
            "platform link table is not a usable declaration"
        )
    _validate_inputs(
        table_name=table_name,
        subject_table=subject_table,
        subject_column=subject_column,
        subject_schema=subject_schema,
        subject_pk=subject_pk,
        schema=schema,
        roles=(app_role, *platform_roles),
    )
    _validate_on_delete(on_delete_subject)
    _validate_on_delete(on_delete_billing_account)

    columns = ("billing_account_id", subject_column, "linked_at")
    op.create_table(
        table_name,
        sa.Column(
            "billing_account_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            subject_column,
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("billing_account_id", name=f"pk_{table_name}"),
        sa.ForeignKeyConstraint(
            ["billing_account_id"],
            [f"{MODULE_SCHEMA}.{PLATFORM_BILLING_ACCOUNTS_TABLE}.id"],
            name=f"fk_{table_name}_billing_account",
            ondelete=on_delete_billing_account,
        ),
        sa.ForeignKeyConstraint(
            [subject_column],
            [f"{subject_schema}.{subject_table}.{subject_pk}"],
            name=f"fk_{table_name}_subject",
            ondelete=on_delete_subject,
        ),
        schema=schema,
    )
    op.create_index(
        f"ix_{table_name}_{subject_column}",
        table_name,
        [subject_column],
        schema=schema,
    )
    qualified = f"{schema}.{table_name}"
    for role in platform_roles:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO {role}")
    op.execute(f"REVOKE ALL PRIVILEGES ON {qualified} FROM {app_role}")
    column_list = ", ".join(columns)
    for privilege in _COLUMN_PRIVILEGES:
        op.execute(f"REVOKE {privilege} ({column_list}) ON {qualified} FROM {app_role}")


def drop_billing_account_link(*, table_name: str, schema: str = "public") -> None:
    """Drop a link emitted by either plane-specific helper."""
    _check_identifier(table_name)
    _check_identifier(schema)
    op.drop_table(table_name, schema=schema)


def _validate_inputs(
    *,
    table_name: str,
    subject_table: str,
    subject_column: str,
    subject_schema: str,
    subject_pk: str,
    schema: str,
    roles: tuple[str, ...],
) -> None:
    for identifier in (
        table_name,
        subject_table,
        subject_column,
        subject_schema,
        subject_pk,
        schema,
        *roles,
        f"pk_{table_name}",
        f"fk_{table_name}_billing_account",
        f"fk_{table_name}_subject",
        f"ix_{table_name}_{subject_column}",
    ):
        _check_identifier(identifier)


def _validate_on_delete(value: OnDelete) -> None:
    if value not in {"CASCADE", "RESTRICT"}:
        raise ValueError(f"unsupported ON DELETE action: {value!r}")


def _check_identifier(name: str) -> None:
    if not name or len(name) > _IDENTIFIER_LIMIT:
        raise ValueError(
            f"identifier {name!r} must be 1..{_IDENTIFIER_LIMIT} characters"
        )
    if name != name.strip().lower() or not name.replace("_", "").isalnum():
        raise ValueError(
            f"identifier {name!r} must contain only lowercase letters, digits, "
            "and underscores"
        )
