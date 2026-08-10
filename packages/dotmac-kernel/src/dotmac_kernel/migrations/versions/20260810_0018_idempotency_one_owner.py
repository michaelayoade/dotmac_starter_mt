"""At-most-once execution gets one owner (ADR-0014).

Renames the WS3 inbox ledgers into the general idempotency ledgers they always
were, and adds the three columns the shared contract needs:

- `inbox_records`          → `idempotency_records`
- `platform_inbox_records` → `platform_idempotency_records`
- `command_id` → `key`, `command_type` → `operation`
- new: `scope` (NOT NULL), `fingerprint` (nullable), `expires_at` (nullable)

RENAME, not create-and-copy: the tables keep their identity, so every existing
row, index and grant survives and no dedup marker is lost in the window. A lost
marker would mean a previously-processed command re-executing — the exact defect
this subsystem exists to prevent.

Existing rows are backfilled with `scope='inbox'`, matching the scope the
`messaging.process_once` adapters now write. `status` moves from the old
`'processed'` spelling to `'executed'`.

`idempotency_records` keeps the standard kernel tenant-table posture (0001): FK
to tenants, ENABLE + FORCE row-level security with a tenant-isolation policy
keyed on `app_current_tenant_id()`, grants to `app_user`/`platform_api`.
`platform_idempotency_records` keeps the platform catalog posture (0007): no
tenant, no RLS, GRANTed to `platform_api`/`app_admin`, REVOKEd from `app_user`.
Both postures are inherited through the rename; this migration re-points the
policy and constraint NAMES so nothing still says "inbox".

Extends the KERNEL lineage (head was 0017).

Revision ID: 0018_idempotency_one_owner
Revises: 0017_history_actor
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018_idempotency_one_owner"
down_revision = "0017_history_actor"
branch_labels = None
depends_on = None

# The scope stamped on pre-existing rows — the same constant
# `dotmac_kernel.idempotency_models.INBOX_SCOPE` writes for transport deliveries.
_INBOX_SCOPE = "inbox"


def upgrade() -> None:
    _rename_tenant_ledger()
    _rename_platform_ledger()


def _rename_tenant_ledger() -> None:
    op.execute("ALTER TABLE inbox_records RENAME TO idempotency_records;")
    op.execute("ALTER TABLE idempotency_records RENAME COLUMN command_id TO key;")
    op.execute(
        "ALTER TABLE idempotency_records RENAME COLUMN command_type TO operation;"
    )

    # `scope` arrives with a server default purely so existing rows are valid at
    # NOT NULL; the default is then dropped so callers must be explicit.
    op.add_column(
        "idempotency_records",
        sa.Column(
            "scope",
            sa.String(length=120),
            nullable=False,
            server_default=sa.text(f"'{_INBOX_SCOPE}'"),
        ),
    )
    op.alter_column("idempotency_records", "scope", server_default=None)
    op.add_column(
        "idempotency_records",
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "idempotency_records",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        "UPDATE idempotency_records SET status = 'executed' WHERE status = 'processed';"
    )

    # The uniqueness contract widens from (tenant_id, command_id) to
    # (tenant_id, scope, key). Every pre-existing row shares one scope, so the
    # new constraint is satisfied by construction.
    op.execute(
        "ALTER TABLE idempotency_records "
        "DROP CONSTRAINT uq_inbox_records_tenant_command_id;"
    )
    op.create_unique_constraint(
        "uq_idempotency_records_tenant_scope_key",
        "idempotency_records",
        ["tenant_id", "scope", "key"],
    )
    op.create_index(
        "ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"]
    )

    op.execute(
        "ALTER TABLE idempotency_records "
        "RENAME CONSTRAINT fk_inbox_records_tenant TO fk_idempotency_records_tenant;"
    )
    op.execute(
        "ALTER INDEX ix_inbox_records_tenant_id RENAME TO ix_idempotency_records_tenant_id;"
    )
    op.execute(
        "ALTER POLICY inbox_records_tenant_isolation ON idempotency_records "
        "RENAME TO idempotency_records_tenant_isolation;"
    )


def _rename_platform_ledger() -> None:
    op.execute(
        "ALTER TABLE platform_inbox_records RENAME TO platform_idempotency_records;"
    )
    op.execute(
        "ALTER TABLE platform_idempotency_records RENAME COLUMN command_id TO key;"
    )
    op.execute(
        "ALTER TABLE platform_idempotency_records RENAME COLUMN command_type TO operation;"
    )

    op.add_column(
        "platform_idempotency_records",
        sa.Column(
            "scope",
            sa.String(length=120),
            nullable=False,
            server_default=sa.text(f"'{_INBOX_SCOPE}'"),
        ),
    )
    op.alter_column("platform_idempotency_records", "scope", server_default=None)
    op.add_column(
        "platform_idempotency_records",
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "platform_idempotency_records",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        "UPDATE platform_idempotency_records SET status = 'executed' "
        "WHERE status = 'processed';"
    )

    op.execute(
        "ALTER TABLE platform_idempotency_records "
        "DROP CONSTRAINT uq_platform_inbox_command_id;"
    )
    op.create_unique_constraint(
        "uq_platform_idempotency_records_scope_key",
        "platform_idempotency_records",
        ["scope", "key"],
    )
    op.create_index(
        "ix_platform_idempotency_records_expires_at",
        "platform_idempotency_records",
        ["expires_at"],
    )

    # Re-assert the platform catalog posture on the renamed table. Grants follow
    # the table through a rename, but restating them keeps this migration
    # self-describing and survives a hand-repaired database.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON platform_idempotency_records "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON platform_idempotency_records "
        "TO app_admin;"
    )
    op.execute("REVOKE ALL ON platform_idempotency_records FROM app_user;")


def downgrade() -> None:
    _revert_platform_ledger()
    _revert_tenant_ledger()


def _revert_platform_ledger() -> None:
    op.execute(
        "UPDATE platform_idempotency_records SET status = 'processed' "
        "WHERE status = 'executed';"
    )
    op.drop_index(
        "ix_platform_idempotency_records_expires_at",
        table_name="platform_idempotency_records",
    )
    op.execute(
        "ALTER TABLE platform_idempotency_records "
        "DROP CONSTRAINT uq_platform_idempotency_records_scope_key;"
    )
    op.drop_column("platform_idempotency_records", "expires_at")
    op.drop_column("platform_idempotency_records", "fingerprint")
    op.drop_column("platform_idempotency_records", "scope")
    op.execute(
        "ALTER TABLE platform_idempotency_records RENAME COLUMN operation TO command_type;"
    )
    op.execute(
        "ALTER TABLE platform_idempotency_records RENAME COLUMN key TO command_id;"
    )
    op.execute(
        "ALTER TABLE platform_idempotency_records RENAME TO platform_inbox_records;"
    )
    op.create_unique_constraint(
        "uq_platform_inbox_command_id", "platform_inbox_records", ["command_id"]
    )


def _revert_tenant_ledger() -> None:
    op.execute(
        "UPDATE idempotency_records SET status = 'processed' WHERE status = 'executed';"
    )
    op.execute(
        "ALTER POLICY idempotency_records_tenant_isolation ON idempotency_records "
        "RENAME TO inbox_records_tenant_isolation;"
    )
    op.execute(
        "ALTER INDEX ix_idempotency_records_tenant_id RENAME TO ix_inbox_records_tenant_id;"
    )
    op.execute(
        "ALTER TABLE idempotency_records "
        "RENAME CONSTRAINT fk_idempotency_records_tenant TO fk_inbox_records_tenant;"
    )
    op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
    op.execute(
        "ALTER TABLE idempotency_records "
        "DROP CONSTRAINT uq_idempotency_records_tenant_scope_key;"
    )
    op.drop_column("idempotency_records", "expires_at")
    op.drop_column("idempotency_records", "fingerprint")
    op.drop_column("idempotency_records", "scope")
    op.execute(
        "ALTER TABLE idempotency_records RENAME COLUMN operation TO command_type;"
    )
    op.execute("ALTER TABLE idempotency_records RENAME COLUMN key TO command_id;")
    op.execute("ALTER TABLE idempotency_records RENAME TO inbox_records;")
    op.create_unique_constraint(
        "uq_inbox_records_tenant_command_id",
        "inbox_records",
        ["tenant_id", "command_id"],
    )
