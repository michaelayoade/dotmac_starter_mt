"""Replace invoice anchor-line affinity with account affinity.

Historical document anchors and financial evidence remain unchanged.
"""

from __future__ import annotations

# All SQL identifiers below are fixed migration-owned plane/table names.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bi_0002_multiline"
down_revision = "bi_0001_billing"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"


def _plane(platform: bool, *, upgrade: bool) -> None:
    prefix = "platform_" if platform else ""
    table = f"{prefix}document_lines"
    documents = f"{prefix}documents"
    obligations = f"{prefix}rated_obligations"
    scope_columns = [] if platform else ["tenant_id"]
    freeze_function = f"freeze_{'platform' if platform else 'tenant'}_document_line"
    old_fk = (
        "fk_platform_document_lines_obligation"
        if platform
        else "fk_document_lines_document_obligation"
    )
    document_fk = f"fk_{prefix}document_lines_document_account"
    obligation_fk = f"fk_{prefix}document_lines_obligation_account"
    # Exclude concurrent writers while the issued-line UPDATE guard is absent.
    op.execute(
        f"LOCK TABLE {_SCHEMA}.{documents}, {_SCHEMA}.{table} IN ACCESS EXCLUSIVE MODE"
    )
    if upgrade:
        op.add_column(
            documents,
            sa.Column(
                "authoring_mode",
                sa.String(24),
                nullable=False,
                server_default=sa.text("'legacy_single'"),
            ),
            schema=_SCHEMA,
        )
        op.create_check_constraint(
            f"ck_{documents}_authoring_mode",
            documents,
            "authoring_mode IN ('legacy_single', 'explicit_multiline')",
            schema=_SCHEMA,
        )
        mode_function = f"refuse_{prefix}authoring_mode_change"
        op.execute(f"""CREATE FUNCTION {_SCHEMA}.{mode_function}() RETURNS trigger AS $$
        BEGIN
            IF NEW.authoring_mode IS DISTINCT FROM OLD.authoring_mode THEN
                RAISE EXCEPTION 'billing document authoring mode is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql""")
        op.execute(
            f"CREATE TRIGGER {documents}_authoring_mode BEFORE UPDATE ON {_SCHEMA}.{documents} FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.{mode_function}()"
        )
        op.add_column(
            table,
            sa.Column("billing_account_id", sa.Uuid(), nullable=True),
            schema=_SCHEMA,
        )
        op.execute(f"DROP TRIGGER {table}_freeze ON {_SCHEMA}.{table}")
        tenant_match = "AND line.tenant_id = document.tenant_id" if not platform else ""
        op.execute(
            f"UPDATE {_SCHEMA}.{table} AS line SET billing_account_id = document.billing_account_id FROM {_SCHEMA}.{documents} AS document WHERE line.document_id = document.id {tenant_match}"
        )
        op.execute(
            f"CREATE TRIGGER {table}_freeze BEFORE UPDATE OR DELETE ON {_SCHEMA}.{table} FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.{freeze_function}()"
        )
        op.alter_column(table, "billing_account_id", nullable=False, schema=_SCHEMA)
        op.drop_constraint(old_fk, table, schema=_SCHEMA, type_="foreignkey")
        for name, parent, parent_key in (
            (document_fk, documents, "document_id"),
            (obligation_fk, obligations, "obligation_id"),
        ):
            op.create_foreign_key(
                name,
                table,
                parent,
                [*scope_columns, parent_key, "billing_account_id"],
                [*scope_columns, "id", "billing_account_id"],
                source_schema=_SCHEMA,
                referent_schema=_SCHEMA,
                ondelete="RESTRICT",
            )
        tenant_match = "AND tenant_id = NEW.tenant_id" if not platform else ""
        function = f"refuse_{prefix}issued_invoice_line_insert"
        op.execute(f"""CREATE FUNCTION {_SCHEMA}.{function}() RETURNS trigger AS $$
        DECLARE
            issued timestamptz;
            kind text;
        BEGIN
            SELECT issued_at, document_kind INTO issued, kind FROM {_SCHEMA}.{documents}
             WHERE id = NEW.document_id {tenant_match}
             FOR UPDATE;
            IF issued IS NOT NULL AND kind = 'invoice' THEN
                RAISE EXCEPTION 'issued invoice refuses additional lines' USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql""")
        op.execute(
            f"CREATE TRIGGER {table}_issued_insert BEFORE INSERT ON {_SCHEMA}.{table} FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.{function}()"
        )
    else:
        explicit = (
            op.get_bind()
            .execute(
                sa.text(
                    f"SELECT 1 FROM {_SCHEMA}.{documents} WHERE authoring_mode = 'explicit_multiline' LIMIT 1"
                )
            )
            .first()
        )
        if explicit is not None:
            raise RuntimeError(
                "cannot downgrade Billing while explicit multiline evidence exists"
            )
        # Refuse before any DDL if restoring the anchor FK would lose semantics.
        invalid = (
            op.get_bind()
            .execute(
                sa.text(
                    f"SELECT 1 FROM {_SCHEMA}.{table} AS line JOIN {_SCHEMA}.{documents} AS document ON line.document_id = document.id "
                    + (
                        "AND line.tenant_id = document.tenant_id "
                        if not platform
                        else ""
                    )
                    + "WHERE line.obligation_id IS DISTINCT FROM document.obligation_id LIMIT 1"
                )
            )
            .first()
        )
        if invalid is not None:
            raise RuntimeError(
                "cannot downgrade Billing while multiline evidence exists"
            )
        op.execute(f"DROP TRIGGER {table}_issued_insert ON {_SCHEMA}.{table}")
        op.execute(
            f"DROP FUNCTION {_SCHEMA}.refuse_{prefix}issued_invoice_line_insert()"
        )
        op.drop_constraint(document_fk, table, schema=_SCHEMA, type_="foreignkey")
        op.drop_constraint(obligation_fk, table, schema=_SCHEMA, type_="foreignkey")
        op.create_foreign_key(
            old_fk,
            table,
            documents,
            [*scope_columns, "document_id", "obligation_id"],
            [*scope_columns, "id", "obligation_id"],
            source_schema=_SCHEMA,
            referent_schema=_SCHEMA,
            ondelete="RESTRICT",
        )
        op.drop_column(table, "billing_account_id", schema=_SCHEMA)
        op.execute(f"DROP TRIGGER {documents}_authoring_mode ON {_SCHEMA}.{documents}")
        op.execute(f"DROP FUNCTION {_SCHEMA}.refuse_{prefix}authoring_mode_change()")
        op.drop_constraint(
            f"ck_{documents}_authoring_mode", documents, schema=_SCHEMA, type_="check"
        )
        op.drop_column(documents, "authoring_mode", schema=_SCHEMA)


def upgrade() -> None:
    planes = selected_module_planes("billing")
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane in planes:
            _plane(plane == ModulePlane.PLATFORM, upgrade=True)


def downgrade() -> None:
    planes = selected_module_planes("billing")
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane in planes:
            _plane(plane == ModulePlane.PLATFORM, upgrade=False)
