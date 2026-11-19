"""Delivery receipts become append-only for `app_user` (ADR-0006 § 5g, K-6).

`0020_delivery_receipts` granted `app_user` and `platform_api` SELECT, INSERT,
UPDATE and DELETE on `communication_deliveries`. A receipt is a record of what a
provider said; nothing in the kernel updates or deletes one (`record_receipt` and
`send` only INSERT, and a late webhook is a NEW row), so the UPDATE and DELETE
grants only let a tenant-plane bug or a compromised session rewrite or erase the
history that suppression and replay decisions rest on.

This revokes UPDATE and DELETE from `app_user`. It is a privilege tightening:
no table, column, row, policy or data change.

Not changed, deliberately:
- `platform_api` keeps the 0020 grants (the platform plane is not the tenant
  request path; revoking it is a separate, product-wide decision);
- tenant deletion still cascades through the foreign key
  (`fk_communication_deliveries_tenant`, ON DELETE CASCADE), which is executed
  by the referential-integrity trigger with the table owner's rights, not the
  caller's grants.

Downgrade restores the exact 0020 grant, so up/down/up is clean.

Revision ID: 0033_receipts_append_only
Revises: 0032_communication_dispatches
Create Date: 2026-11-19
"""

from __future__ import annotations

from alembic import op

revision = "0033_receipts_append_only"
down_revision = "0032_communication_dispatches"
branch_labels = None
depends_on = None

_TABLE = "communication_deliveries"


def upgrade() -> None:
    op.execute(f"REVOKE UPDATE, DELETE ON {_TABLE} FROM app_user;")


def downgrade() -> None:
    op.execute(f"GRANT UPDATE, DELETE ON {_TABLE} TO app_user;")
