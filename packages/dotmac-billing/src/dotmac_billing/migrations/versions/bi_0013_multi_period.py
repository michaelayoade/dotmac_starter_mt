"""Opt-in multi-period document mode; existing document meaning is unchanged."""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bi_0013_multi_period"
down_revision = "bi_0012_tax_evidence"
branch_labels = None
depends_on = None


def _prefixes() -> tuple[str, ...]:
    selected = selected_module_planes("billing")
    return tuple(
        prefix
        for plane, prefix in (
            (ModulePlane.TENANT, ""),
            (ModulePlane.PLATFORM, "platform_"),
        )
        if plane in selected
    )


def _constraint(prefix: str, *, multi_period: bool) -> None:
    op.drop_constraint(
        "ck_" + prefix + "documents_authoring_mode",
        prefix + "documents",
        schema="mod_billing",
        type_="check",
    )
    modes = "'legacy_single', 'explicit_multiline', 'imported_open_item'"
    if multi_period:
        modes += ", 'explicit_multiperiod'"
    op.create_check_constraint(
        "ck_" + prefix + "documents_authoring_mode",
        prefix + "documents",
        "authoring_mode IN (" + modes + ")",
        schema="mod_billing",
    )


def upgrade() -> None:
    for prefix in _prefixes():
        _constraint(prefix, multi_period=True)


def downgrade() -> None:
    for prefix in _prefixes():
        documents = sa.table(
            prefix + "documents",
            sa.column("authoring_mode", sa.String()),
            schema="mod_billing",
        )
        count = (
            op.get_bind()
            .execute(
                sa.select(sa.func.count())
                .select_from(documents)
                .where(documents.c.authoring_mode == "explicit_multiperiod")
            )
            .scalar_one()
        )
        if count:
            raise RuntimeError("cannot downgrade while multi-period documents exist")
    for prefix in _prefixes():
        _constraint(prefix, multi_period=False)
