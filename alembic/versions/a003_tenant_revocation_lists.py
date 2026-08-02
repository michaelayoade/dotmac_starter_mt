"""tenant_revocation_lists — the receiver's imported revocation state.

Assembly lineage continuation (a002 → a003). Tenant-scoped, RLS-protected per
hard rule 11 (`tenant_id NOT NULL` + composite unique + ENABLE/FORCE RLS +
tenant-isolation policy + online-role grants, all in this one migration).

The receiver persists BOTH the last imported `list_version` (so a stale list
cannot be replayed over a newer one) and the revoked set itself (so every
licence application is checked offline, with no vendor call). One row per
tenant.

Idempotent-adoptive + destructive-downgrade guard, same as a002: dropping this
table silently un-revokes every revoked licence for every tenant, which is the
most dangerous downgrade in the assembly — it requires
`DOTMAC_ALLOW_DESTRUCTIVE_LICENCE_DOWNGRADE=1`, the same flag a002 uses, since
the two are one operator decision.

Revision ID: a003_revocation_lists
Revises: a002_applied_licences
Create Date: 2026-08-02
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a003_revocation_lists"
down_revision = "a002_applied_licences"
branch_labels = None
depends_on = None

_TABLE = "tenant_revocation_lists"
_POLICY = f"{_TABLE}_tenant_isolation"
_UNIQUE = "uq_revocation_list_tenant"
_INDEX = "ix_tenant_revocation_lists_tenant_id"
_ALLOWED_GRANTEES = frozenset({"app_user", "platform_api", "app_admin"})
_EXPECTED_COLUMNS: tuple[tuple[str, bool], ...] = (
    ("id", False),
    ("tenant_id", False),
    ("list_version", False),
    ("revoked_licence_ids", False),
    ("created_at", False),
    ("updated_at", False),
)


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind):
        _verify_contract(bind)
        return
    _create()


def downgrade() -> None:
    if os.getenv("DOTMAC_ALLOW_DESTRUCTIVE_LICENCE_DOWNGRADE") != "1":
        raise RuntimeError(
            f"refusing destructive downgrade: dropping {_TABLE} silently "
            "un-revokes every revoked licence for every tenant. Routine "
            "rollback un-records a003 via 'alembic stamp' with the table "
            "preserved. Set DOTMAC_ALLOW_DESTRUCTIVE_LICENCE_DOWNGRADE=1 only "
            "under an approved operator runbook."
        )
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)


def _create() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("list_version", sa.Integer(), nullable=False),
        sa.Column(
            "revoked_licence_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
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
            name="fk_revocation_list_tenant",
        ),
        sa.UniqueConstraint("tenant_id", name=_UNIQUE),
    )
    op.create_index(_INDEX, _TABLE, ["tenant_id"])

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON {_TABLE}
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_user;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")


# ── Adoption verification (fail closed; a002 pattern) ───────────────────────


class _AdoptionContractError(RuntimeError):
    """The existing table diverges from what a fresh _create() would produce."""


def _fail(detail: str) -> None:
    raise _AdoptionContractError(
        f"cannot adopt {_TABLE}: {detail}. Existing table diverges from the "
        "expected contract; refusing to record a003 over it."
    )


def _table_exists(bind) -> bool:
    return bool(
        bind.exec_driver_sql(
            "SELECT to_regclass('public.tenant_revocation_lists') IS NOT NULL"
        ).scalar()
    )


def _verify_contract(bind) -> None:
    cols = dict(
        bind.exec_driver_sql(
            "SELECT attname, NOT attnotnull FROM pg_attribute "
            "WHERE attrelid = 'tenant_revocation_lists'::regclass AND attnum > 0 "
            "AND NOT attisdropped"
        ).all()
    )
    if cols != dict(_EXPECTED_COLUMNS):
        _fail(f"columns/nullability mismatch: {sorted(cols.items())}")

    fk = bind.exec_driver_sql(
        "SELECT confrelid::regclass::text, confdeltype FROM pg_constraint "
        "WHERE conrelid = 'tenant_revocation_lists'::regclass AND contype = 'f'"
    ).first()
    if fk is None or fk[0] != "tenants" or fk[1] != "c":
        _fail(f"tenant_id FK must reference tenants ON DELETE CASCADE, found {fk}")

    unique = bind.exec_driver_sql(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'tenant_revocation_lists'::regclass AND contype = 'u'"
    ).scalar()
    if unique != _UNIQUE:
        _fail(f"unique constraint {_UNIQUE} missing, found {unique!r}")

    rls = bind.exec_driver_sql(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'tenant_revocation_lists'::regclass"
    ).first()
    if rls is None or not (rls[0] and rls[1]):
        _fail("row-level security must be ENABLEd and FORCEd")

    policy = bind.exec_driver_sql(
        "SELECT qual FROM pg_policies "
        "WHERE tablename = 'tenant_revocation_lists' "
        "AND policyname = 'tenant_revocation_lists_tenant_isolation'"
    ).scalar()
    if policy is None or "app_current_tenant_id()" not in policy:
        _fail(f"tenant-isolation policy {_POLICY} missing or altered: {policy!r}")

    rows = bind.exec_driver_sql(
        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND table_name='tenant_revocation_lists'"
    ).all()
    granted: dict[str, set[str]] = {}
    for grantee, priv in rows:
        granted.setdefault(grantee, set()).add(priv)
    for role in ("app_user", "platform_api"):
        missing = {"SELECT", "INSERT", "UPDATE", "DELETE"} - granted.get(role, set())
        if missing:
            _fail(f"role {role} missing grants {sorted(missing)}")
    unexpected = set(granted) - _ALLOWED_GRANTEES
    if unexpected:
        _fail(f"unexpected grantee(s) on the table: {sorted(unexpected)}")
