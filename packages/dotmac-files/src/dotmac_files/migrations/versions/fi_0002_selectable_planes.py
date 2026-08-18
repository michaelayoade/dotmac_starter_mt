"""Make the released atomic files lineage tenant-selectable (ADR-0028).

``fi_0001_stored_files`` shipped in ``dotmac-files 0.1.0a2`` and its bytes are
immutable.  It creates both declared planes.  ERP and Academy are named
tenant-only candidates for the deferred cohort, so this additive revision makes
that final catalogue possible without rewriting released history or moving
authority during module readiness:

* TENANT+PLATFORM keeps the historical catalogue unchanged;
* TENANT locks the platform table, refuses if it contains any row, then drops
  it while the lock is still held.

The refusal is the upgrade contract for any previously installed a2 database.
A populated platform table is evidence of a real control-plane owner and may
not be destroyed to satisfy a new assembly selection.  Such an adopter must
keep the full selection until an explicit data migration exists.

PLATFORM-only is deliberately unsupported.  No named adopter needs it, and the
released root requires the tenant catalogue before it can create either plane.

Revision ID: fi_0002_selectable_planes
Revises: fi_0001_stored_files
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "fi_0002_selectable_planes"
down_revision = "fi_0001_stored_files"
branch_labels = None
depends_on = None

MODULE_CODE = "files"
_SCHEMA = "mod_files"
_PLATFORM_TABLE = "platform_stored_files"


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.PLATFORM in planes:
        return

    # The check and DROP are one decision under one lock.  Checking before the
    # lock would let a concurrent a2 writer add a row between proof and DROP.
    op.execute("LOCK TABLE mod_files.platform_stored_files IN ACCESS EXCLUSIVE MODE;")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM mod_files.platform_stored_files) THEN
                RAISE EXCEPTION
                    'mod_files platform plane is not empty; refusing tenant-only '
                    'selection until its rows have an explicit owner and migration'
                    USING ERRCODE = 'object_not_in_prerequisite_state';
            END IF;
        END
        $$;
        """
    )
    op.execute("DROP TABLE mod_files.platform_stored_files;")


def downgrade() -> None:
    """Restore a2's atomic catalogue before its root becomes the head again."""
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.PLATFORM in planes:
        return

    op.create_table(
        _PLATFORM_TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_media_type", sa.String(200), nullable=False),
        sa.Column("detected_media_type", sa.String(200), nullable=False),
        sa.Column("checksum_sha256", sa.String(71), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("missing_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider_code",
            "storage_key",
            name="uq_platform_stored_files_provider_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_stored_files_state",
        _PLATFORM_TABLE,
        ["state"],
        schema=_SCHEMA,
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_files.platform_stored_files TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_files.platform_stored_files TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_files.platform_stored_files FROM app_user;")
