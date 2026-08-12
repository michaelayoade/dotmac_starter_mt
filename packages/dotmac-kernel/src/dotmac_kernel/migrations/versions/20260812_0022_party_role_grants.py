"""Free the name `party_roles` for the archetype's "PartyRole" (ADR-0019).

This table is an RBAC grant — `(tenant_id, party_id, role_id)` into `roles`,
a catalog of permission bundles. It is not a "PartyRole" in the sense the Party
archetype (Fowler; Arlow & Neustadt; TM Forum SID) means: a concurrent,
temporal business capacity such as customer, reseller or staff.

Sub already models both, correctly separated — its `app/models/party.py`
for capacity and `app/models/rbac.py` for authorization — and uses
`party_roles` for the capacity. The kernel had the archetype's name on the
wrong table. ADR-0019 rules that `party_roles` is reserved fleet-wide for the
capacity, so the grant is renamed here.

Renaming rather than recreating is deliberate: `ALTER TABLE ... RENAME TO`
preserves the rows, the RLS policy's effect, the grants and the foreign keys
pointing at it. What it does NOT rename is the dependent object names, so each
constraint, index and the policy is renamed explicitly. A half-renamed table is
the state to avoid — `party_role_grants` carrying `uq_party_roles_member` reads
as a leftover and invites someone to "fix" it in a later migration that then
collides with a product's own object of that name.

Revision ID: 0022_party_role_grants
Revises: 0021_setting_scope_alignment
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022_party_role_grants"
down_revision = "0021_setting_scope_alignment"
branch_labels = None
depends_on = None

_OLD_TABLE = "party_roles"
_NEW_TABLE = "party_role_grants"

#: Every dependent object that carries the old table's name, as
#: (old_name, new_name). Constraints and indexes share one namespace per table
#: in Postgres, so one ALTER form does not cover both — see `_rename_objects`.
_CONSTRAINTS = (
    ("uq_party_roles_member", "uq_party_role_grants_member"),
    ("fk_party_roles_tenant_party", "fk_party_role_grants_tenant_party"),
    ("fk_party_roles_tenant_role", "fk_party_role_grants_tenant_role"),
)
_INDEXES = (
    ("ix_party_roles_tenant_id", "ix_party_role_grants_tenant_id"),
    ("ix_party_roles_party_id", "ix_party_role_grants_party_id"),
    ("ix_party_roles_role_id", "ix_party_role_grants_role_id"),
)
_POLICY = ("party_roles_tenant_isolation", "party_role_grants_tenant_isolation")


def _table_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() "
                "AND c.relname = :name AND c.relkind = 'r'"
            ),
            {"name": name},
        )
        .scalar()
    )


def _constraint_exists(table: str, name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = current_schema() "
                "AND t.relname = :table AND c.conname = :name"
            ),
            {"table": table, "name": name},
        )
        .scalar()
    )


def _index_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() "
                "AND c.relname = :name AND c.relkind = 'i'"
            ),
            {"name": name},
        )
        .scalar()
    )


def _policy_exists(table: str, name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_policies "
                "WHERE schemaname = current_schema() "
                "AND tablename = :table AND policyname = :name"
            ),
            {"table": table, "name": name},
        )
        .scalar()
    )


def _rename_objects(table: str, old_to_new: tuple[tuple[str, str], ...]) -> None:
    """Rename constraints and indexes, skipping any already at the new name.

    Constraints are renamed through ALTER TABLE and indexes through ALTER
    INDEX; a unique CONSTRAINT owns its index, so renaming the constraint
    renames that index with it and the index pass then finds nothing to do.
    """
    for old, new in old_to_new:
        if _constraint_exists(table, old):
            op.execute(sa.text(f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}"))
        elif _index_exists(old):
            op.execute(sa.text(f"ALTER INDEX {old} RENAME TO {new}"))


def _rename_policy(table: str, old: str, new: str) -> None:
    if _policy_exists(table, old):
        op.execute(sa.text(f"ALTER POLICY {old} ON {table} RENAME TO {new}"))


def upgrade() -> None:
    if not _table_exists(_OLD_TABLE):
        # Already renamed, or a product adopting the lineage never had the
        # kernel's grant table. Both are fine; refuse only if NEITHER exists,
        # which would mean the RBAC grant is missing entirely.
        if not _table_exists(_NEW_TABLE):
            raise RuntimeError(
                f"neither {_OLD_TABLE} nor {_NEW_TABLE} exists; refusing to "
                "continue with no RBAC grant table to rename or adopt"
            )
        return

    if _table_exists(_NEW_TABLE):
        raise RuntimeError(
            f"both {_OLD_TABLE} and {_NEW_TABLE} exist; refusing to guess which "
            "holds the authoritative grants. Reconcile them explicitly."
        )

    op.execute(sa.text(f"ALTER TABLE {_OLD_TABLE} RENAME TO {_NEW_TABLE}"))
    _rename_objects(_NEW_TABLE, (*_CONSTRAINTS, *_INDEXES))
    _rename_policy(_NEW_TABLE, *_POLICY)


def downgrade() -> None:
    if not _table_exists(_NEW_TABLE):
        raise RuntimeError(f"{_NEW_TABLE} is missing; refusing an ambiguous downgrade.")
    if _table_exists(_OLD_TABLE):
        raise RuntimeError(
            f"{_OLD_TABLE} already exists; downgrading would collide with it."
        )

    op.execute(sa.text(f"ALTER TABLE {_NEW_TABLE} RENAME TO {_OLD_TABLE}"))
    _rename_objects(
        _OLD_TABLE,
        tuple((new, old) for old, new in (*_CONSTRAINTS, *_INDEXES)),
    )
    _rename_policy(_OLD_TABLE, _POLICY[1], _POLICY[0])
