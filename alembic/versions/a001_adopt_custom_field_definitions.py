"""adopt custom_field_definitions (assembly lineage root)

The ASSEMBLY-owned half of the old kernel `0004` (kernel-boundary Task 1c). It
creates the `custom_field_definitions` table (the `custom_fields` feature's
per-tenant field metadata) + its RLS + grants.

Independent assembly lineage (design amendment 1): this is a lineage ROOT
(`down_revision = None`, `branch_labels = ("assembly",)`) that only DEPENDS on
the kernel head via `depends_on = "0007_platform_identity"` — it is NOT a linear
continuation of the kernel chain. The revision graph therefore has two heads,
`kernel` (0007) and `assembly` (a001).

Idempotent-adoptive (design amendment 3): the SAME revision both *creates* the
table on a fresh database and *adopts* it on an existing v0.8 database (where
the old, un-reduced 0004 already created it). On adoption it verifies the
COMPLETE table contract and fails closed on any deviation — it never stamps
over a divergent or absent-but-recorded table.

Destructive downgrade (design amendment 4): `downgrade()` drops the table and
ALL its tenant data, so it refuses to run unless an approved operator runbook
sets `DOTMAC_ALLOW_DESTRUCTIVE_CF_DOWNGRADE=1`. Routine deploy rollback does
NOT use this path — it un-records `a001` via `alembic stamp` (table preserved);
see docs/superpowers/reviews/2026-07-30-kernel-migration-split-design.md.

Revision ID: a001_adopt_custom_field_definitions
Revises: (root; depends_on 0007_platform_identity)
Create Date: 2026-07-30

"""

from __future__ import annotations

import os

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision id kept ≤32 chars (Alembic's alembic_version.version_num is
# varchar(32)); the file name stays descriptive.
revision = "a001_adopt_cfd"
down_revision = None
branch_labels = ("assembly",)
depends_on = "0007_platform_identity"

_TABLE = "custom_field_definitions"
_POLICY = "custom_field_definitions_tenant_isolation"
_UNIQUE = "uq_custom_field_definitions_tenant_entity_code"
_CHECK = "ck_custom_field_definitions_field_type"
_INDEX = "ix_custom_field_definitions_tenant_id"
_TENANT_POLICY_EXPR = "(tenant_id = app_current_tenant_id())"
_GRANTED_ROLES = ("app_user", "platform_api")
# The only roles permitted ANY privilege on the adopted table.
_ALLOWED_GRANTEES = frozenset({"app_user", "platform_api", "app_admin"})
_FIELD_TYPES = (
    "TEXT",
    "TEXTAREA",
    "NUMBER",
    "DECIMAL",
    "DATE",
    "DATETIME",
    "BOOLEAN",
    "SELECT",
    "MULTISELECT",
    "EMAIL",
    "URL",
    "PHONE",
    "CURRENCY",
)

# (name, nullable, server_default_contains) — server_default_contains is a
# substring the pg default expression must contain, or None to skip.
_EXPECTED_COLUMNS: tuple[tuple[str, bool, str | None], ...] = (
    ("id", False, None),
    ("tenant_id", False, None),
    ("entity_type", False, None),
    ("field_code", False, None),
    ("field_name", False, None),
    ("description", True, None),
    ("field_type", False, None),
    ("field_options", True, None),
    ("is_required", False, "false"),
    ("default_value", True, None),
    ("validation_regex", True, None),
    ("validation_message", True, None),
    ("min_value", True, None),
    ("max_value", True, None),
    ("max_length", True, None),
    ("display_order", False, "0"),
    ("section_name", True, None),
    ("placeholder", True, None),
    ("help_text", True, None),
    ("show_in_list", False, "false"),
    ("show_in_form", False, "true"),
    ("show_in_detail", False, "true"),
    ("is_active", False, "true"),
    ("created_at", False, "now()"),
    ("updated_at", False, "now()"),
)


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind):
        _verify_contract(bind)  # adopt: raises on any deviation
        return
    _create()  # fresh: create table + RLS + grants


def downgrade() -> None:
    if os.getenv("DOTMAC_ALLOW_DESTRUCTIVE_CF_DOWNGRADE") != "1":
        raise RuntimeError(
            "refusing to drop adopted custom_field_definitions (destructive — "
            "drops the table and ALL its tenant data). Routine rollback un-records "
            "this revision with `alembic stamp` instead (table preserved); see the "
            "migration-split design doc. Set DOTMAC_ALLOW_DESTRUCTIVE_CF_DOWNGRADE=1 "
            "per an approved operator runbook to authorize the drop."
        )
    op.execute(f"REVOKE ALL ON {_TABLE} FROM app_user, platform_api;")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_table(_TABLE)


# ─────────────────────────────────────────────────────────────────────────────
# Fresh create (the old 0004 assembly DDL, verbatim)
# ─────────────────────────────────────────────────────────────────────────────


def _create() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("field_code", sa.String(50), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("field_type", sa.String(20), nullable=False),
        sa.Column("field_options", postgresql.JSONB),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("default_value", sa.String(500)),
        sa.Column("validation_regex", sa.String(500)),
        sa.Column("validation_message", sa.String(200)),
        sa.Column("min_value", sa.String(50)),
        sa.Column("max_value", sa.String(50)),
        sa.Column("max_length", sa.Integer),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("section_name", sa.String(100)),
        sa.Column("placeholder", sa.String(200)),
        sa.Column("help_text", sa.String(500)),
        sa.Column(
            "show_in_list", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column("show_in_form", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "show_in_detail", sa.Boolean, nullable=False, server_default=sa.true()
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "field_type IN ("
            "'TEXT', 'TEXTAREA', 'NUMBER', 'DECIMAL', 'DATE', 'DATETIME', "
            "'BOOLEAN', 'SELECT', 'MULTISELECT', 'EMAIL', 'URL', 'PHONE', "
            "'CURRENCY'"
            ")",
            name=_CHECK,
        ),
        sa.UniqueConstraint("tenant_id", "entity_type", "field_code", name=_UNIQUE),
    )
    op.create_index(_INDEX, _TABLE, ["tenant_id"])
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} "
        f"USING {_TENANT_POLICY_EXPR} WITH CHECK {_TENANT_POLICY_EXPR};"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_user;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")


# ─────────────────────────────────────────────────────────────────────────────
# Full-contract adoption verification (design amendment 3) — fail-closed
# ─────────────────────────────────────────────────────────────────────────────


class _AdoptionContractError(RuntimeError):
    """Raised when an existing custom_field_definitions table does not match
    the exact shape a fresh `_create()` would have produced. Fail-closed: the
    revision is NOT recorded, nothing is stamped over."""


def _fail(detail: str) -> None:
    raise _AdoptionContractError(
        f"cannot adopt {_TABLE}: {detail}. Existing table diverges from the "
        "expected contract; refusing to record a001 over it."
    )


def _table_exists(bind) -> bool:
    return bool(
        bind.exec_driver_sql(
            "SELECT to_regclass('public.custom_field_definitions') IS NOT NULL"
        ).scalar()
    )


def _verify_contract(bind) -> None:
    _verify_primary_key(bind)
    _verify_foreign_key(bind)
    _verify_index(bind)
    _verify_columns(bind)
    _verify_unique(bind)
    _verify_check(bind)
    _verify_rls(bind)
    _verify_grants(bind)


def _verify_primary_key(bind) -> None:
    cols = (
        bind.exec_driver_sql(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'custom_field_definitions'::regclass AND i.indisprimary"
        )
        .scalars()
        .all()
    )
    if list(cols) != ["id"]:
        _fail(f"primary key must be (id), found {list(cols)}")


def _verify_foreign_key(bind) -> None:
    row = bind.exec_driver_sql(
        "SELECT confrelid::regclass::text, confdeltype, "
        "  (SELECT array_agg(attname ORDER BY x.ord) "
        "   FROM unnest(conkey) WITH ORDINALITY x(attnum, ord) "
        "   JOIN pg_attribute a ON a.attrelid = conrelid AND a.attnum = x.attnum) "
        "FROM pg_constraint "
        "WHERE conrelid = 'custom_field_definitions'::regclass AND contype = 'f'"
    ).first()
    if row is None:
        _fail("missing foreign key on tenant_id")
    ref_table, deltype, cols = row
    if ref_table != "tenants" or list(cols) != ["tenant_id"]:
        _fail(f"FK must be (tenant_id) -> tenants, found {list(cols)} -> {ref_table}")
    if deltype != "c":  # 'c' == CASCADE
        _fail("FK tenant_id must be ON DELETE CASCADE")


def _verify_index(bind) -> None:
    exists = bind.exec_driver_sql(
        "SELECT 1 FROM pg_indexes WHERE schemaname='public' "
        "AND tablename='custom_field_definitions' AND indexname=%s",
        (_INDEX,),
    ).scalar()
    if not exists:
        _fail(f"missing index {_INDEX}")


def _verify_columns(bind) -> None:
    rows = bind.exec_driver_sql(
        "SELECT column_name, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='custom_field_definitions'"
    ).all()
    actual = {r[0]: (r[1] == "YES", r[2]) for r in rows}
    expected_names = {c[0] for c in _EXPECTED_COLUMNS}
    if set(actual) != expected_names:
        missing = expected_names - set(actual)
        extra = set(actual) - expected_names
        _fail(f"column set mismatch (missing={missing}, extra={extra})")
    for name, nullable, default_contains in _EXPECTED_COLUMNS:
        is_nullable, default = actual[name]
        if is_nullable != nullable:
            _fail(f"column {name} nullability is {is_nullable}, expected {nullable}")
        if default_contains is not None:
            if default is None or default_contains not in default:
                _fail(
                    f"column {name} default {default!r} must contain "
                    f"{default_contains!r}"
                )


def _verify_unique(bind) -> None:
    row = bind.exec_driver_sql(
        "SELECT (SELECT array_agg(attname ORDER BY x.ord) "
        "        FROM unnest(conkey) WITH ORDINALITY x(attnum, ord) "
        "        JOIN pg_attribute a ON a.attrelid=conrelid AND a.attnum=x.attnum) "
        "FROM pg_constraint "
        "WHERE conrelid='custom_field_definitions'::regclass "
        "AND contype='u' AND conname=%s",
        (_UNIQUE,),
    ).first()
    if row is None or list(row[0]) != ["tenant_id", "entity_type", "field_code"]:
        _fail(
            f"unique constraint {_UNIQUE} must be (tenant_id, entity_type, field_code)"
        )


def _verify_check(bind) -> None:
    src = bind.exec_driver_sql(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='custom_field_definitions'::regclass "
        "AND contype='c' AND conname=%s",
        (_CHECK,),
    ).scalar()
    if src is None:
        _fail(f"missing check constraint {_CHECK}")
    for value in _FIELD_TYPES:
        if f"'{value}'" not in src:
            _fail(f"check {_CHECK} must permit field_type {value!r}; def is {src!r}")


def _verify_rls(bind) -> None:
    flags = bind.exec_driver_sql(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid='custom_field_definitions'::regclass"
    ).first()
    if not flags or not flags[0] or not flags[1]:
        _fail("RLS must be ENABLEd AND FORCEd")
    policy = bind.exec_driver_sql(
        "SELECT qual, with_check FROM pg_policies "
        "WHERE schemaname='public' AND tablename='custom_field_definitions' "
        "AND policyname=%s",
        (_POLICY,),
    ).first()
    if policy is None:
        _fail(f"missing RLS policy {_POLICY}")
    qual, with_check = policy
    norm = _TENANT_POLICY_EXPR.replace(" ", "")
    if (qual or "").replace(" ", "") != norm:
        _fail(f"policy USING must be {_TENANT_POLICY_EXPR}, found {qual!r}")
    if (with_check or "").replace(" ", "") != norm:
        _fail(f"policy WITH CHECK must be {_TENANT_POLICY_EXPR}, found {with_check!r}")


def _verify_grants(bind) -> None:
    rows = bind.exec_driver_sql(
        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND table_name='custom_field_definitions'"
    ).all()
    granted: dict[str, set[str]] = {}
    for grantee, priv in rows:
        granted.setdefault(grantee, set()).add(priv)
    # Required DML for the two app roles.
    for role in _GRANTED_ROLES:
        have = granted.get(role, set())
        missing = {"SELECT", "INSERT", "UPDATE", "DELETE"} - have
        if missing:
            _fail(f"role {role} missing grants {sorted(missing)}")
    # No grant to any grantee outside the allowlist (e.g. PUBLIC, or a stray
    # role) — an unsafe grant is a divergence just like a missing one.
    unexpected = set(granted) - _ALLOWED_GRANTEES
    if unexpected:
        _fail(f"unexpected grantee(s) on the table: {sorted(unexpected)}")
