"""Verify the outbox relay IPAM events are enqueued into. No DDL.

Revision ID: ip_0003_outbox_relay
Revises: ip_0002_extracted
Create Date: 2026-10-28

``dotmac_ipam.outbox.publish`` calls the Kernel's ``enqueue_event`` for every
vocabulary event, so the module writes ``public.outbox_events`` at command time
and creates no such table itself. Deploy is the last moment a missing relay is a
failed migration rather than a failed allocation in production (same shape and
reasoning as ``ap_0002_outbox_relay``). ``ip_0001`` already verifies the
module's other effects; they are not re-verified here.
"""

from __future__ import annotations

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ip_0003_outbox_relay"
down_revision = "ip_0002_extracted"
branch_labels = None

REQUIRES = ("outbox_relay.v1",)
depends_on = resolve_depends_on(REQUIRES)


def upgrade() -> None:
    """Prove the relay, change nothing."""
    require_prerequisites(op.get_bind(), REQUIRES)


def downgrade() -> None:
    """Nothing to undo: ``upgrade`` created no object and wrote no row."""
