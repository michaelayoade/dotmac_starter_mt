"""Make the database default agree with nullable setting ownership.

Sub's first live adoption exposed a mismatch in the reusable contract. The
Python default derives ``platform`` when ``tenant_id`` is NULL and ``tenant``
otherwise, but the database default was always ``tenant`` and the schema had no
alignment CHECK. A raw write omitting both columns could therefore persist a
tenant/NULL row which the resolver could never read.

This migration repairs only the exact legacy shape that the old default could
create (tenant/NULL with no scope id), refuses ambiguous incoherence, changes
the server default to platform, and enforces the pair in the database.

The adoption path is first-class. Sub already owns the exact CHECK and platform
default through its migration 514. When that shape is present, this revision
verifies and adopts it rather than duplicating it. A constraint comment records
whether 0021 created or adopted the CHECK so downgrade restores the correct
predecessor for both a fresh kernel database and the product cutover.

Revision ID: 0021_setting_scope_alignment
Revises: 0020_delivery_receipts
Create Date: 2026-08-11
"""

from __future__ import annotations

import re

import sqlalchemy as sa

from alembic import op

revision = "0021_setting_scope_alignment"
down_revision = "0020_delivery_receipts"
branch_labels = None
depends_on = None

_TABLE = "domain_settings"
_CONSTRAINT = "ck_domain_settings_scope_alignment"
_CREATED_COMMENT = "dotmac-kernel:0021:created"
_ADOPTED_COMMENT = "dotmac-kernel:0021:adopted-existing"
_CHECK_SQL = (
    "(scope_kind = 'platform' AND tenant_id IS NULL) "
    "OR (scope_kind <> 'platform' AND tenant_id IS NOT NULL)"
)


def _scalar(statement: str, **params: object) -> str | int | None:
    value = op.get_bind().execute(sa.text(statement), params).scalar()
    if value is None or isinstance(value, str | int):
        return value
    raise TypeError(f"Unexpected scalar type: {type(value).__name__}")


def _column_default() -> str | None:
    value = _scalar(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name = :table AND column_name = :column",
        table=_TABLE,
        column="scope_kind",
    )
    return str(value) if value is not None else None


def _constraint_info() -> tuple[str, str | None] | None:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT pg_get_constraintdef(c.oid), "
                "obj_description(c.oid, 'pg_constraint') "
                "FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = current_schema() "
                "AND t.relname = :table AND c.conname = :name"
            ),
            {"table": _TABLE, "name": _CONSTRAINT},
        )
        .one_or_none()
    )
    if row is None:
        return None
    return str(row[0]), str(row[1]) if row[1] is not None else None


def _normalise_check(definition: str) -> str:
    without_casts = re.sub(r"::(?:text|character varying)", "", definition.lower())
    return re.sub(r"[\s()]", "", without_casts)


def _expected_check(definition: str) -> bool:
    return _normalise_check(definition) == _normalise_check(f"CHECK ({_CHECK_SQL})")


def _comment(value: str | None) -> None:
    rendered = "NULL" if value is None else f"'{value}'"
    op.execute(
        sa.text(f"COMMENT ON CONSTRAINT {_CONSTRAINT} ON {_TABLE} IS {rendered}")
    )


def upgrade() -> None:
    # The old server default can create exactly this row. It is unambiguously a
    # platform row because no tenant or finer-scope id was supplied.
    collisions = int(
        _scalar(
            "SELECT count(*) FROM domain_settings bad "
            "JOIN domain_settings existing "
            "ON existing.tenant_id IS NULL "
            "AND existing.scope_kind = 'platform' "
            "AND existing.scope_id IS NOT DISTINCT FROM bad.scope_id "
            "AND existing.domain = bad.domain AND existing.key = bad.key "
            "WHERE bad.tenant_id IS NULL AND bad.scope_kind = 'tenant' "
            "AND bad.scope_id IS NULL"
        )
        or 0
    )
    if collisions:
        raise RuntimeError(
            "Cannot repair domain_settings scope defaults: "
            f"{collisions} tenant/NULL row(s) collide with an existing platform row. "
            "Resolve each duplicate explicitly before retrying."
        )

    op.execute(
        sa.text(
            "UPDATE domain_settings SET scope_kind = 'platform' "
            "WHERE tenant_id IS NULL AND scope_kind = 'tenant' "
            "AND scope_id IS NULL"
        )
    )

    ambiguous = int(
        _scalar(
            "SELECT count(*) FROM domain_settings "
            "WHERE (scope_kind = 'platform' AND tenant_id IS NOT NULL) "
            "OR (scope_kind <> 'platform' AND tenant_id IS NULL)"
        )
        or 0
    )
    if ambiguous:
        raise RuntimeError(
            "Cannot enforce domain_settings scope alignment: "
            f"{ambiguous} ambiguous row(s) remain after repairing the old default. "
            "Decide their owner/scope explicitly before retrying."
        )

    existing = _constraint_info()
    if existing is not None:
        definition, comment = existing
        if not _expected_check(definition):
            raise RuntimeError(
                f"{_CONSTRAINT} exists with a different definition; "
                "refusing to adopt an unverified product constraint."
            )
        if "platform" not in (_column_default() or ""):
            raise RuntimeError(
                f"{_CONSTRAINT} already exists but scope_kind does not default "
                "to platform; refusing an incoherent adoption."
            )
        if comment not in (None, _ADOPTED_COMMENT):
            raise RuntimeError(
                f"{_CONSTRAINT} carries an unexpected ownership marker: {comment!r}."
            )
        _comment(_ADOPTED_COMMENT)
        return

    op.alter_column(
        _TABLE,
        "scope_kind",
        server_default=sa.text("'platform'"),
        existing_type=sa.String(length=40),
        existing_nullable=False,
    )
    op.create_check_constraint(_CONSTRAINT, _TABLE, _CHECK_SQL)
    _comment(_CREATED_COMMENT)


def downgrade() -> None:
    existing = _constraint_info()
    if existing is None:
        raise RuntimeError(
            f"{_CONSTRAINT} is missing; refusing an ambiguous downgrade."
        )

    definition, comment = existing
    if not _expected_check(definition):
        raise RuntimeError(
            f"{_CONSTRAINT} has drifted; refusing to remove an unverified constraint."
        )

    if comment == _ADOPTED_COMMENT:
        # Restore the product-owned predecessor: exact CHECK + platform default.
        _comment(None)
        return
    if comment != _CREATED_COMMENT:
        raise RuntimeError(
            f"{_CONSTRAINT} has no recognised 0021 ownership marker; "
            "refusing to guess whether downgrade may remove it."
        )

    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.alter_column(
        _TABLE,
        "scope_kind",
        server_default=sa.text("'tenant'"),
        existing_type=sa.String(length=40),
        existing_nullable=False,
    )
