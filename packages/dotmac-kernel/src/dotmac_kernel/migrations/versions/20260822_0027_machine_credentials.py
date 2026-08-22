"""Machine credentials — the tenant-scoped `X-Api-Key` row.

Hard rule 11 in ONE migration: `tenant_id NOT NULL`, composite uniques that
include it, RLS ENABLEd *and* FORCEd with an isolation policy, and the online
grants. FORCE matters — without it the table owner, which migrations run as,
bypasses its own policy.

Extracted product-first from `dotmac_sub` and `dotmac_erp` (inventory:
`docs/inventories/machine-credential-sources.md`). Four port deltas, each of
them a defect in at least one source rather than an improvement for its own
sake:

1. **`scopes` is NOT NULL with no default.** ERP's is nullable and its
   `has_scope` returns True for everything when the list is empty — documented
   in its own docstring as the grandfathered default. A credential that never
   said what it may do can do anything, which is the single most dangerous
   behaviour in either source. Here the row cannot exist without an answer.

2. **The stored hash scheme is CHECKed.** Sub accepts either an HMAC or a plain
   unsalted SHA-256 form and rehashes the weak one on use; ERP stores only the
   weak form. `key_hash LIKE 'hmac-sha256:%'` means a weak row cannot sit here
   unnoticed, and there is no code path that would write one.

3. **No `last_used_at`.** Both sources have it and both WRITE it during
   authentication — Sub commits it inside a GET. The column is absent so the
   write cannot come back; usage observation belongs to the audit trail.

4. **No human FK.** ERP requires a `person_id` and loads the `Person`; Sub's
   `subscriber_id` is optional and falls back to the credential's own id, so it
   means two different things depending on how the row was made. A machine
   principal has neither.

`key_hash` is globally unique rather than unique per tenant: one raw key must
resolve to at most one credential, or the same secret could be minted in two
tenants and RLS would silently decide which one answered.

Revision ID: 0027_machine_credentials
Revises: 0026_platform_audit_log
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0027_machine_credentials"
down_revision = "0026_platform_audit_log"
branch_labels = None
depends_on = None

_TABLE = "machine_credentials"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("key_hash", sa.String(120), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_machine_credentials_tenant",
        ),
        sa.UniqueConstraint("key_hash", name="uq_machine_credentials_key_hash"),
        sa.UniqueConstraint(
            "tenant_id", "label", name="uq_machine_credentials_tenant_label"
        ),
        sa.CheckConstraint(
            "length(trim(label)) > 0", name="ck_machine_credentials_label_nonempty"
        ),
        sa.CheckConstraint(
            "key_hash LIKE 'hmac-sha256:%'",
            name="ck_machine_credentials_key_hash_scheme",
        ),
    )
    op.create_index(f"ix_{_TABLE}_tenant_id", _TABLE, ["tenant_id"])

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
          USING (tenant_id = app_current_tenant_id())
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_user;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE};")
    op.drop_index(f"ix_{_TABLE}_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
