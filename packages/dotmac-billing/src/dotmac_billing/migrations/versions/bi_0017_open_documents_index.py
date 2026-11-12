"""Index the open-receivable-documents read (``list_open_receivable_documents``).

The read pages one account's issued invoices by the keyset
``(issued_at, id)``. This partial index serves exactly that predicate on both
planes, so a page does not scan the account's other documents:

* tenant plane: ``(tenant_id, billing_account_id, issued_at, id)``;
* platform plane: ``(billing_account_id, issued_at, id)``;

both ``WHERE document_kind = 'invoice' AND issued_at IS NOT NULL``.

An index only: no table, column, constraint, grant or row changes. The
downgrade drops only the indexes this revision added.
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bi_0017_open_documents_index"
down_revision = "bi_0016_withholding_components"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
_WHERE = "document_kind = 'invoice' AND issued_at IS NOT NULL"


def _indexes() -> list[tuple[str, str, list[str]]]:
    planes = selected_module_planes("billing")
    out: list[tuple[str, str, list[str]]] = []
    if ModulePlane.TENANT in planes:
        out.append(
            (
                "ix_documents_open_by_account",
                "documents",
                ["tenant_id", "billing_account_id", "issued_at", "id"],
            )
        )
    if ModulePlane.PLATFORM in planes:
        out.append(
            (
                "ix_platform_documents_open_by_account",
                "platform_documents",
                ["billing_account_id", "issued_at", "id"],
            )
        )
    return out


def upgrade() -> None:
    for name, table, columns in _indexes():
        op.create_index(
            name,
            table,
            columns,
            postgresql_where=sa.text(_WHERE),
            schema=_SCHEMA,
        )


def downgrade() -> None:
    for name, _table, _columns in _indexes():
        op.drop_index(name, schema=_SCHEMA)
