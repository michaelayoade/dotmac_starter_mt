"""Pin each observation's freshness policy at acceptance.

Revision ID: ph_0002_freshness_snapshot
Revises: ph_0001_platform_health
Create Date: 2026-09-07

`health_observations.freshness_seconds` is a SNAPSHOT of the owning
component's freshness policy at the moment the observation was accepted.
Before this migration, `rebuild_projections` re-derived `freshness_deadline`
from the component's CURRENT `freshness_seconds` — so a component's freshness
policy could move after an observation was accepted, and the same, unchanged
observation would classify as `fresh` before a rebuild and `stale` after it
(or the reverse), with no new observation ever received. This column pins the
policy to the fact it produced, so `rebuild_projections` reproduces the exact
classification the observation was accepted under, deterministically, no
matter how many times the component's policy has changed since.

Existing rows remain NULL: their acceptance-time policy is unknowable and must
not be reconstructed from the component's current policy. Rebuild and evidence
production refuse while a selected observation has NULL provenance.
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ph_0002_freshness_snapshot"
down_revision = "ph_0001_platform_health"
branch_labels = None
REQUIRES = ("module_database_roles.v1",)
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_health"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.add_column(
        "health_observations",
        sa.Column("freshness_seconds", sa.Integer(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_health_observations_freshness",
        "health_observations",
        "freshness_seconds IS NULL OR freshness_seconds > 0",
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_health_observations_freshness",
        "health_observations",
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_column("health_observations", "freshness_seconds", schema=_SCHEMA)
