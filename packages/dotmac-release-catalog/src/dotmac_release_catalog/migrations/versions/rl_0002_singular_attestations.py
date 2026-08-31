"""Make declaration-document attestations singular per artifact.

Signatures are cumulative: several parties may vouch for the same artifact.
Product manifests, module database catalogues and product database catalogues
are different declaration kinds. Each claims to be THE exact declaration
document of its kind built into one artifact, so two rows of the same kind are
contradictory even when their digests differ.

The ordinary unique-index build deliberately performs no cleanup. If a
catalogue already holds duplicate singular claims, upgrade must fail and force
an explicit incident repair rather than choose one as truth.

Revision ID: rl_0002_singular_attestations
Revises: rl_0001_release_artifacts
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "rl_0002_singular_attestations"
down_revision = "rl_0001_release_artifacts"
branch_labels = None
depends_on = None

_SCHEMA = "mod_rel"
_TABLE = "artifact_attestations"
_INDEX = "uq_artifact_attestations_singular_kind"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        _TABLE,
        ["artifact_id", "attestation_kind"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text(
            "attestation_kind IN ('product_manifest', 'module_database_catalog', "
            "'product_database_catalog')"
        ),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE, schema=_SCHEMA)
