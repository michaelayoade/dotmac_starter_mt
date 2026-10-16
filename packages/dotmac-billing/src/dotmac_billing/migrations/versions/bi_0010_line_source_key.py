"""Expose exact scoped line/document/account foreign-key targets.

This adds provider keys only. Product bridges, existing financial guards and
runtime privileges retain their existing owners.
"""

from __future__ import annotations

from collections.abc import Iterator

from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bi_0010_line_source_key"
down_revision = "bi_0009_opening_window"
branch_labels = None
depends_on = None


def _keys() -> Iterator[tuple[str, str, list[str]]]:
    planes = selected_module_planes("billing")
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane not in planes:
            continue
        tenant = plane is ModulePlane.TENANT
        table = "document_lines" if tenant else "platform_document_lines"
        columns = ["id", "document_id", "billing_account_id"]
        if tenant:
            columns.insert(0, "tenant_id")
        yield table, f"uq_{table}_source_key", columns


def upgrade() -> None:
    for table, name, columns in _keys():
        op.create_unique_constraint(name, table, columns, schema="mod_billing")


def downgrade() -> None:
    # Default PostgreSQL RESTRICT refuses a key bound by an external consumer
    # FK. Never cascade through product schema or remove financial guards.
    for table, name, _columns in _keys():
        op.drop_constraint(name, table, schema="mod_billing", type_="unique")
