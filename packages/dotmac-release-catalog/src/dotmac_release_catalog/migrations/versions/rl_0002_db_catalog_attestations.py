"""Make verified database-catalogue attestations singular per artifact/kind.

Database catalogues are retained canonical documents. The specialised service
writers parse their held bytes with the Kernel parser, verify the expected
digest, and verify the snapshot's subject/version against the immutable release
artifact before inserting the ordinary attestation pointer. The bytes remain
the authority for snapshot subject and scope; this table deliberately stores no
second, drift-prone copy of them.

Unlike signatures, a module or product database catalogue is one exact
declaration for an artifact. Partial unique indexes enforce that fact under
concurrent inserts while preserving the existing `(artifact, kind, digest)`
unique that permits multiple ordinary attestations of one kind.

Revision ID: rl_0002_db_catalog_attestations
Revises: rl_0001_release_artifacts
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "rl_0002_db_catalog_attestations"
down_revision = "rl_0001_release_artifacts"
branch_labels = None
depends_on = None

_SCHEMA = "mod_rel"
_TABLE = "artifact_attestations"


def upgrade() -> None:
    op.create_index(
        "uq_artifact_attestations_module_database_catalog",
        _TABLE,
        ["artifact_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("attestation_kind = 'module_database_catalog'"),
    )
    op.create_index(
        "uq_artifact_attestations_product_database_catalog",
        _TABLE,
        ["artifact_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("attestation_kind = 'product_database_catalog'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_artifact_attestations_product_database_catalog",
        table_name=_TABLE,
        schema=_SCHEMA,
    )
    op.drop_index(
        "uq_artifact_attestations_module_database_catalog",
        table_name=_TABLE,
        schema=_SCHEMA,
    )
