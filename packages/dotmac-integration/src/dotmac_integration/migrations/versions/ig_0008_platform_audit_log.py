"""Verify the append-only platform audit log this module writes. No DDL.

Integration repair and retention operations call
``dotmac_kernel.audit.write_platform_audit_event`` at request time. Their own
lineage creates no ``public.platform_audit_events`` table, so the dependency
must be declared and verified just like the at-most-once ledger verified by
``ig_0007``. Kernel ``0.1.0a68`` names the whole effect as
``platform_audit_log.v1`` and proves its shape, actor foreign key and index,
absence of RLS, tenant-role isolation, and the online platform role's
append-only SELECT+INSERT privileges.

This is a new revision because ``ig_0001`` through ``ig_0007`` have shipped in
published wheels and are immutable history.

Revision ID: ig_0008_platform_audit_log
Revises: ig_0007_idempotency_ledger
Create Date: 2026-08-16
"""

from __future__ import annotations

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ig_0008_platform_audit_log"
down_revision = "ig_0007_idempotency_ledger"
branch_labels = None

# Common rather than platform-conditional because this module is an atomic
# platform-only composition; it has no selectable plane under which the
# request-time dependency can lapse.
COMMON_REQUIRES = ("platform_audit_log.v1",)
TENANT_REQUIRES: tuple[str, ...] = ()
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

depends_on = resolve_depends_on(COMMON_REQUIRES)


def upgrade() -> None:
    """Prove the platform audit effect, changing no object or row."""
    require_prerequisites(op.get_bind(), REQUIRES)


def downgrade() -> None:
    """Nothing to undo: ``upgrade`` only verified the provider catalogue."""

