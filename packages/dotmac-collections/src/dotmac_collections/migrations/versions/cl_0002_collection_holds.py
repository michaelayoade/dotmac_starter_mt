"""Add the per-account access hold: case hold instant, R10 holds and subject state.

Revision ID: cl_0002_collection_holds
Revises: cl_0001_collections
Create Date: 2026-11-21
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "cl_0002_collection_holds"
down_revision = "cl_0001_collections"
branch_labels = None
depends_on = None

MODULE_CODE = "collections"
TENANT_TABLES = (
    "collection_holds",
    "collection_hold_lifts",
    "collection_subject_access",
)
PLATFORM_TABLES = tuple(f"platform_{table}" for table in TENANT_TABLES)
TENANT_SEQUENCE = "collection_subject_access_version_seq"
PLATFORM_SEQUENCE = "platform_collection_subject_access_version_seq"
# Holds and lifts are facts: written once and never changed.
INSERT_ONLY_TENANT = ("collection_holds", "collection_hold_lifts")
_SCHEMA = "mod_coll"
_STATES = "'clear', 'held', 'suppressed'"


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _allow_skipped_attempts(table: str, constraint: str, kinds: str) -> None:
    """A skipped reminder is recorded as a step attempt of kind ``skipped``."""

    op.drop_constraint(constraint, table, schema=_SCHEMA, type_="check")
    op.create_check_constraint(
        constraint, table, f"request_kind IN ({kinds})", schema=_SCHEMA
    )


_KINDS_A2 = "'notice', 'action'"
_KINDS_A3 = "'notice', 'action', 'skipped'"


def _upgrade_tenant_plane() -> None:
    _allow_skipped_attempts(
        "collection_step_attempts", "ck_collection_step_attempts_kind", _KINDS_A3
    )
    op.add_column(
        "collection_cases",
        sa.Column("access_hold_from", sa.DateTime(timezone=True)),
        schema=_SCHEMA,
    )
    op.add_column(
        "collection_cases",
        sa.Column("access_hold_engaged_at", sa.DateTime(timezone=True)),
        schema=_SCHEMA,
    )
    op.add_column(
        "collection_cases",
        sa.Column("due_at", sa.DateTime(timezone=True)),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_holds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("external_ref", sa.String(255), nullable=False),
        sa.Column("placement_ordinal", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("evidence_ref", sa.String(255), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("approval_ref", sa.String(255)),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_collection_holds_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "external_ref",
            "placement_ordinal",
            name="uq_collection_holds_placement",
        ),
        sa.CheckConstraint(
            "placement_ordinal > 0", name="ck_collection_holds_placement_ordinal"
        ),
        sa.CheckConstraint(
            "strpos(external_ref, ':') > 1", name="ck_collection_holds_external_ref"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_collection_holds_subject",
        "collection_holds",
        ["tenant_id", "subject_ref"],
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_hold_lifts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("hold_id", sa.Uuid(), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("evidence_ref", sa.String(255), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("approval_ref", sa.String(255)),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hold_id"],
            ["mod_coll.collection_holds.tenant_id", "mod_coll.collection_holds.id"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_hold_lifts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "hold_id", name="uq_collection_hold_lifts_hold"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "collection_subject_access",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.BigInteger()),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_subject_access_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "subject_ref", name="uq_collection_subject_access_subject"
        ),
        sa.CheckConstraint(
            f"state IN ({_STATES})", name="ck_collection_subject_access_state"
        ),
        sa.CheckConstraint(
            "version IS NULL OR version > 0",
            name="ck_collection_subject_access_version",
        ),
        schema=_SCHEMA,
    )
    op.execute(f"CREATE SEQUENCE {_SCHEMA}.{TENANT_SEQUENCE};")


def _upgrade_platform_plane() -> None:
    _allow_skipped_attempts(
        "platform_collection_step_attempts",
        "ck_platform_collection_step_attempts_kind",
        _KINDS_A3,
    )
    op.add_column(
        "platform_collection_cases",
        sa.Column("access_hold_from", sa.DateTime(timezone=True)),
        schema=_SCHEMA,
    )
    op.add_column(
        "platform_collection_cases",
        sa.Column("access_hold_engaged_at", sa.DateTime(timezone=True)),
        schema=_SCHEMA,
    )
    op.add_column(
        "platform_collection_cases",
        sa.Column("due_at", sa.DateTime(timezone=True)),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_collection_holds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("external_ref", sa.String(255), nullable=False),
        sa.Column("placement_ordinal", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("evidence_ref", sa.String(255), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("approval_ref", sa.String(255)),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "external_ref",
            "placement_ordinal",
            name="uq_platform_collection_holds_placement",
        ),
        sa.CheckConstraint(
            "placement_ordinal > 0", name="ck_platform_collection_holds_ordinal"
        ),
        sa.CheckConstraint(
            "strpos(external_ref, ':') > 1",
            name="ck_platform_collection_holds_external_ref",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_collection_holds_subject",
        "platform_collection_holds",
        ["subject_ref"],
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_collection_hold_lifts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hold_id", sa.Uuid(), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("evidence_ref", sa.String(255), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("approval_ref", sa.String(255)),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["hold_id"], ["mod_coll.platform_collection_holds.id"]),
        sa.UniqueConstraint("hold_id", name="uq_platform_collection_hold_lifts_hold"),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_collection_subject_access",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.BigInteger()),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "subject_ref", name="uq_platform_collection_subject_access_subject"
        ),
        sa.CheckConstraint(
            f"state IN ({_STATES})", name="ck_platform_collection_subject_access_state"
        ),
        sa.CheckConstraint(
            "version IS NULL OR version > 0",
            name="ck_platform_collection_subject_access_version",
        ),
        schema=_SCHEMA,
    )
    op.execute(f"CREATE SEQUENCE {_SCHEMA}.{PLATFORM_SEQUENCE};")


def _install_tenant_security() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_coll.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_coll.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_coll.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(f"REVOKE ALL ON mod_coll.{table} FROM PUBLIC;")
        op.execute(f"REVOKE ALL ON mod_coll.{table} FROM platform_api;")
        privileges = (
            "SELECT, INSERT"
            if table in INSERT_ONLY_TENANT
            else "SELECT, INSERT, UPDATE"
        )
        op.execute(f"GRANT {privileges} ON mod_coll.{table} TO app_user;")
    op.execute(f"REVOKE ALL ON SEQUENCE {_SCHEMA}.{TENANT_SEQUENCE} FROM PUBLIC;")
    op.execute(f"REVOKE ALL ON SEQUENCE {_SCHEMA}.{TENANT_SEQUENCE} FROM platform_api;")
    op.execute(f"GRANT USAGE ON SEQUENCE {_SCHEMA}.{TENANT_SEQUENCE} TO app_user;")


def _install_platform_security() -> None:
    for table in PLATFORM_TABLES:
        op.execute(f"REVOKE ALL ON mod_coll.{table} FROM PUBLIC;")
        op.execute(f"REVOKE ALL ON mod_coll.{table} FROM app_user;")
        op.execute(f"GRANT SELECT, INSERT ON mod_coll.{table} TO platform_api;")
    op.execute(
        "GRANT UPDATE ON mod_coll.platform_collection_subject_access TO platform_api;"
    )
    op.execute(f"REVOKE ALL ON SEQUENCE {_SCHEMA}.{PLATFORM_SEQUENCE} FROM PUBLIC;")
    op.execute(f"REVOKE ALL ON SEQUENCE {_SCHEMA}.{PLATFORM_SEQUENCE} FROM app_user;")
    op.execute(
        f"GRANT USAGE ON SEQUENCE {_SCHEMA}.{PLATFORM_SEQUENCE} TO platform_api;"
    )


def _install_immutability(planes: frozenset[ModulePlane]) -> None:
    tables: list[str] = []
    if ModulePlane.TENANT in planes:
        tables.extend(INSERT_ONLY_TENANT)
    if ModulePlane.PLATFORM in planes:
        tables.extend(f"platform_{table}" for table in INSERT_ONLY_TENANT)
    for table in tables:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE "
            f"ON mod_coll.{table} FOR EACH ROW "
            "EXECUTE FUNCTION mod_coll.refuse_fact_mutation();"
        )


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.TENANT in planes:
        _upgrade_tenant_plane()
        _install_tenant_security()
    if ModulePlane.PLATFORM in planes:
        _upgrade_platform_plane()
        _install_platform_security()
    _install_immutability(planes)


def downgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.PLATFORM in planes:
        for table in reversed(PLATFORM_TABLES):
            op.drop_table(table, schema=_SCHEMA)
        op.execute(f"DROP SEQUENCE {_SCHEMA}.{PLATFORM_SEQUENCE};")
        _allow_skipped_attempts(
            "platform_collection_step_attempts",
            "ck_platform_collection_step_attempts_kind",
            _KINDS_A2,
        )
        op.drop_column("platform_collection_cases", "due_at", schema=_SCHEMA)
        op.drop_column(
            "platform_collection_cases", "access_hold_engaged_at", schema=_SCHEMA
        )
        op.drop_column("platform_collection_cases", "access_hold_from", schema=_SCHEMA)
    if ModulePlane.TENANT in planes:
        for table in reversed(TENANT_TABLES):
            op.drop_table(table, schema=_SCHEMA)
        op.execute(f"DROP SEQUENCE {_SCHEMA}.{TENANT_SEQUENCE};")
        _allow_skipped_attempts(
            "collection_step_attempts", "ck_collection_step_attempts_kind", _KINDS_A2
        )
        op.drop_column("collection_cases", "due_at", schema=_SCHEMA)
        op.drop_column("collection_cases", "access_hold_engaged_at", schema=_SCHEMA)
        op.drop_column("collection_cases", "access_hold_from", schema=_SCHEMA)
