"""tenant_applied_licences — the WS8 receiver's durable replay record.

Assembly lineage continuation (a001 → a002). Creates the tenant-scoped,
RLS-protected `tenant_applied_licences` table (hard rule 11: `tenant_id NOT
NULL` + composite unique + ENABLE/FORCE RLS + tenant-isolation policy +
online-role grants, all in this one migration — the 0010 tenant-table pattern).

The kernel WS8 verifier owns no storage; THIS table is the receiver-owned
`AppliedLicence` record `verify_licence` uses as its replay/rollback guard.

Idempotent-adoptive like a001 (rehearsal 6's runtime-rollback procedure stamps
the assembly branch to base while the tables persist, so a re-upgrade meets an
existing table): when the table already exists, the SAME revision verifies the
complete contract and fails closed on any deviation instead of creating.

Destructive downgrade guard (a001 pattern): `downgrade()` drops the table and
ALL tenant replay records — dropping replay state re-opens the stale-licence
window — so it refuses unless an approved operator runbook sets
`DOTMAC_ALLOW_DESTRUCTIVE_LICENCE_DOWNGRADE=1`. Routine deploy rollback
un-records via `alembic stamp` (table preserved), never this path.

Revision ID: a002_applied_licences
Revises: a001_adopt_cfd
Create Date: 2026-08-01
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision id kept ≤32 chars (alembic_version.version_num is varchar(32)).
revision = "a002_applied_licences"
down_revision = "a001_adopt_cfd"
branch_labels = None
depends_on = None

_TABLE = "tenant_applied_licences"
_POLICY = f"{_TABLE}_tenant_isolation"
_UNIQUE = "uq_applied_licence_tenant_lineage"
_INDEX = "ix_tenant_applied_licences_tenant_id"
_ALLOWED_GRANTEES = frozenset({"app_user", "platform_api", "app_admin"})
# (name, nullable)
_EXPECTED_COLUMNS: tuple[tuple[str, bool], ...] = (
    ("id", False),
    ("tenant_id", False),
    ("licence_id", False),
    ("licence_version", False),
    ("digest", False),
    ("validity", False),
    ("created_at", False),
    ("updated_at", False),
)


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind):
        _verify_contract(bind)  # adopt: verify the full shape, record only
        return
    _create()


def downgrade() -> None:
    if os.getenv("DOTMAC_ALLOW_DESTRUCTIVE_LICENCE_DOWNGRADE") != "1":
        raise RuntimeError(
            f"refusing destructive downgrade: dropping {_TABLE} deletes every "
            "tenant's applied-licence replay record (re-opening the "
            "stale-licence window). Routine rollback un-records a002 via "
            "'alembic stamp' with the table preserved. Set "
            "DOTMAC_ALLOW_DESTRUCTIVE_LICENCE_DOWNGRADE=1 only under an "
            "approved operator runbook."
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
        sa.Column("licence_id", sa.String(length=200), nullable=False),
        sa.Column("licence_version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("validity", sa.String(length=20), nullable=False),
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
            name="fk_applied_licence_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "licence_id", name=_UNIQUE),
    )
    op.create_index(_INDEX, _TABLE, ["tenant_id"])

    # Tenant isolation (0001/0008/0010 pattern): ENABLE + FORCE RLS + policy.
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


# ── Adoption verification (fail closed; a001 pattern, compact) ──────────────


class _AdoptionContractError(RuntimeError):
    """The existing table diverges from what a fresh _create() would produce —
    refuse to record a002 over it."""


def _fail(detail: str) -> None:
    raise _AdoptionContractError(
        f"cannot adopt {_TABLE}: {detail}. Existing table diverges from the "
        "expected contract; refusing to record a002 over it."
    )


def _table_exists(bind) -> bool:
    return bool(
        bind.exec_driver_sql(
            "SELECT to_regclass('public.tenant_applied_licences') IS NOT NULL"
        ).scalar()
    )


def _verify_contract(bind) -> None:
    cols = dict(
        bind.exec_driver_sql(
            "SELECT attname, NOT attnotnull FROM pg_attribute "
            "WHERE attrelid = 'tenant_applied_licences'::regclass AND attnum > 0 "
            "AND NOT attisdropped"
        ).all()
    )
    expected = dict(_EXPECTED_COLUMNS)
    if cols != expected:
        _fail(f"columns/nullability mismatch: {sorted(cols.items())}")

    pk = (
        bind.exec_driver_sql(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
            "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'tenant_applied_licences'::regclass "
            "AND i.indisprimary"
        )
        .scalars()
        .all()
    )
    if list(pk) != ["id"]:
        _fail(f"primary key must be (id), found {list(pk)}")

    fk = bind.exec_driver_sql(
        "SELECT confrelid::regclass::text, confdeltype FROM pg_constraint "
        "WHERE conrelid = 'tenant_applied_licences'::regclass AND contype = 'f'"
    ).first()
    if fk is None or fk[0] != "tenants" or fk[1] != "c":
        _fail(f"tenant_id FK must reference tenants ON DELETE CASCADE, found {fk}")

    unique = bind.exec_driver_sql(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'tenant_applied_licences'::regclass AND contype = 'u'"
    ).scalar()
    if unique != _UNIQUE:
        _fail(f"unique constraint {_UNIQUE} missing, found {unique!r}")

    rls = bind.exec_driver_sql(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'tenant_applied_licences'::regclass"
    ).first()
    if rls is None or not (rls[0] and rls[1]):
        _fail("row-level security must be ENABLEd and FORCEd")

    policy = bind.exec_driver_sql(
        "SELECT qual FROM pg_policies "
        "WHERE tablename = 'tenant_applied_licences' "
        "AND policyname = 'tenant_applied_licences_tenant_isolation'"
    ).scalar()
    if policy is None or "app_current_tenant_id()" not in policy:
        _fail(f"tenant-isolation policy {_POLICY} missing or altered: {policy!r}")

    rows = bind.exec_driver_sql(
        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND table_name='tenant_applied_licences'"
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
