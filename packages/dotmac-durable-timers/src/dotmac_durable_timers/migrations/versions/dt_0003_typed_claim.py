"""Verify the Kernel's type-scoped outbox claim without changing it.

The job runner claims only ``durable_timers.job_due.v1`` through
``claim_batch_by_type``, so a foreign module's event is never leased. This
lineage owns no DDL for it: it requires the ``outbox_typed_claim.v1`` effect
(bound by the assembly to the Kernel revision that grants the dispatcher
EXECUTE) and refuses the migration if the database lacks it.

Revision ID: dt_0003_typed_claim
Revises: dt_0002_job_states
Create Date: 2026-11-25
"""

from __future__ import annotations

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "dt_0003_typed_claim"
down_revision = "dt_0002_job_states"
branch_labels = None

MODULE_CODE = "durable_timers"
COMMON_REQUIRES = ("outbox_typed_claim.v1",)
TENANT_REQUIRES: tuple[str, ...] = ()
PLATFORM_REQUIRES: tuple[str, ...] = ()

depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    module=MODULE_CODE,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
)


def upgrade() -> None:
    require_prerequisites(op.get_bind(), COMMON_REQUIRES)


def downgrade() -> None:
    # Verification introduced no storage; the Kernel keeps the claim function.
    pass
