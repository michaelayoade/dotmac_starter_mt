"""Verify the Kernel ledger used by import evidence claims. No DDL.

Revision ID: ip_0004_import_idempotency
Revises: ip_0003_outbox_relay
Create Date: 2026-10-29

``record_imported`` uses Kernel ``execute_once`` to atomically claim each
aggregate's evidence and outbox pair. The module requires the existing ledger;
it creates no replacement. The relay and ledger prerequisites live in serial
revisions so each adds one independent join to the assembly's migration graph,
including assemblies whose providers are ancestors in the same lineage.
"""

from __future__ import annotations

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ip_0004_import_idempotency"
down_revision = "ip_0003_outbox_relay"
branch_labels = None

REQUIRES = ("idempotency_ledger.v1",)
depends_on = resolve_depends_on(REQUIRES)


def upgrade() -> None:
    """Prove the existing import ledger without changing any row or object."""
    require_prerequisites(op.get_bind(), REQUIRES)


def downgrade() -> None:
    """Nothing to undo: ``upgrade`` created no object and wrote no row."""
