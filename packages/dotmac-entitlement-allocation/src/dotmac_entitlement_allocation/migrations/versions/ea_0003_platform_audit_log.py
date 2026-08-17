"""Verify the append-only platform audit log used by staging. No DDL.

``stage_allocation`` calls ``write_platform_audit_event`` inside its
idempotent operation, and this module creates none of
``public.platform_audit_events``. Kernel ``0.1.0a68`` names the effect as
``platform_audit_log.v1`` and verifies its whole shape and online-role
privilege posture.

This is separate from ``ea_0002`` deliberately. The idempotency effect is
provided by kernel ``0018`` while the audit effect is provided by descendant
``0026``; putting both dependencies on one revision gives Alembic two heads
from the same provider lineage to consume and fails during head maintenance.
Chaining one DDL-free verification revision per ordered effect preserves the
logical dependency without inventing a physical cross-lineage edge.

Revision ID: ea_0003_platform_audit_log
Revises: ea_0002_idempotency_ledger
Create Date: 2026-08-16
"""

from __future__ import annotations

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ea_0003_platform_audit_log"
down_revision = "ea_0002_idempotency_ledger"
branch_labels = None

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
