"""Delivery receipts become append-only for `platform_api` too (ADR-0006 § 5g, K-6 follow-up).

`0033_receipts_append_only` revoked UPDATE and DELETE on `communication_deliveries`
from `app_user` and left `platform_api` on the 0020 grants, pending a check that
nothing on the platform plane writes this table. That check is done: no code
that runs as `platform_api` (the platform session, `PLATFORM_DATABASE_URL`)
touches `communication_deliveries` or `CommunicationDelivery`. The kernel's only
writers are `record_receipt` and `send`, both INSERT-only on the tenant session.

This revokes UPDATE and DELETE from `platform_api`. Privilege tightening only:
no table, column, row, policy or data change. `platform_api` keeps SELECT and
INSERT (the 0020 grant minus the two writes), and tenant deletion still
cascades through the foreign key (`ON DELETE CASCADE`, run by the
referential-integrity trigger with the table owner's rights, not the caller's
grants).

Downgrade restores the exact 0020/0033 grant, so up/down/up is clean.

Revision ID: 0034_receipts_platform_ro
Revises: 0033_receipts_append_only
Create Date: 2026-11-19
"""

from __future__ import annotations

from alembic import op

revision = "0034_receipts_platform_ro"
down_revision = "0033_receipts_append_only"
branch_labels = None
depends_on = None

_TABLE = "communication_deliveries"


def upgrade() -> None:
    op.execute(f"REVOKE UPDATE, DELETE ON {_TABLE} FROM platform_api;")


def downgrade() -> None:
    op.execute(f"GRANT UPDATE, DELETE ON {_TABLE} TO platform_api;")
