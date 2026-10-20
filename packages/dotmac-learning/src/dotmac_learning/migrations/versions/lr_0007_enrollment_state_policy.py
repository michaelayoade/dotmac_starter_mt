"""Require explicit new enrollment state; preserve every accepted row.

Downgrade restores historical schema defaults only. It is not recovery of
post-upgrade product facts or an authorization to discard accepted writes.
"""

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    resolve_depends_on,
)

from alembic import op

revision = "lr_0007_enrollment_state_policy"
down_revision = "lr_0006_delivery_support"
branch_labels = None
REQUIRES = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    PARTY_PERSON_CATALOG_V1.name,
)
depends_on = resolve_depends_on(REQUIRES)


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.alter_column(
        "enrollments", "role_in_cohort", server_default=None, schema="mod_learning"
    )
    op.alter_column("enrollments", "status", server_default=None, schema="mod_learning")


def downgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.alter_column(
        "enrollments", "role_in_cohort", server_default="student", schema="mod_learning"
    )
    op.alter_column(
        "enrollments", "status", server_default="active", schema="mod_learning"
    )
