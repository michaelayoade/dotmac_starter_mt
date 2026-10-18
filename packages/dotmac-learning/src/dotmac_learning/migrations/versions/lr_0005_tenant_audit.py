"""Verify Kernel-owned tenant audit storage without changing it.

Unpublished incubation: requires the released Kernel tenant_audit_log.v1 API.
The assembly supplies its provider binding; this lineage owns no audit DDL.
"""

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import TENANT_AUDIT_LOG_V1, resolve_depends_on

from alembic import op

revision = "lr_0005_tenant_audit"
down_revision = "lr_0004_assessment"
branch_labels = None
REQUIRES = (TENANT_AUDIT_LOG_V1.name,)
depends_on = resolve_depends_on(REQUIRES)


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)


def downgrade() -> None:
    # Verification introduced no storage; the Kernel retains its audit history.
    pass
