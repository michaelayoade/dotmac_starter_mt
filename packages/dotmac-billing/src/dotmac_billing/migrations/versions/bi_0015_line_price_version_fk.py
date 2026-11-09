"""A rated document line must name its obligation's own price version.

``document_lines(obligation_id, price_source_version)`` now references
``rated_obligations(id, price_version_id)`` through a composite foreign key, so
the price a line bills at is the price its obligation was rated at, enforced by
the database rather than by service code.

The key is MATCH SIMPLE on purpose: a line without an ``obligation_id`` (a
credit-note line, or any line not backed by a rated obligation) is exempt.
Opening-import invoices carry no document lines at all.

History is not repaired. The constraint is added ``NOT VALID`` (new and updated
rows are checked at once) and ``VALIDATE`` is then attempted. If existing rows
violate it, the validation is skipped, the constraint stays ``NOT VALID`` and a
constraint comment records that the violations belong to the manual exceptions
review. No row is changed.

No downgrade of data; the downgrade drops only the constraints this revision
added.
"""

from __future__ import annotations

# All identifiers are closed Billing plane/table literals.
# ruff: noqa: S608
from collections.abc import Iterator

from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bi_0015_line_price_version_fk"
down_revision = "bi_0014_opening_variance_ledger"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
_NOTE = (
    "NOT VALID: existing lines whose price_source_version differs from the rated "
    "obligation price_version_id are history for the manual exceptions review; "
    "no repair. New and updated rows are enforced."
)


def _planes() -> Iterator[tuple[str, str, str, list[str], list[str]]]:
    planes = selected_module_planes("billing")
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane not in planes:
            continue
        prefix = "" if plane is ModulePlane.TENANT else "platform_"
        scope = ["tenant_id"] if plane is ModulePlane.TENANT else []
        parent = f"{prefix}rated_obligations"
        unique = (
            "uq_rated_obligations_tenant_id_price_version"
            if scope
            else "uq_platform_rated_obligations_id_price_version"
        )
        yield (
            f"{prefix}document_lines",
            parent,
            unique,
            scope,
            [*scope, "id", "price_version_id"],
        )


def upgrade() -> None:
    for table, parent, unique, scope, target in _planes():
        constraint = f"fk_{table}_obligation_price"
        op.create_unique_constraint(unique, parent, target, schema=_SCHEMA)
        local = ", ".join([*scope, "obligation_id", "price_source_version"])
        remote = ", ".join(target)
        op.execute(
            f"ALTER TABLE {_SCHEMA}.{table} ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY ({local}) REFERENCES {_SCHEMA}.{parent} ({remote}) "
            "ON DELETE RESTRICT NOT VALID"
        )
        # VALIDATE bypasses row security for its referential scan, so it sees
        # every tenant. A violation leaves the constraint NOT VALID and noted.
        op.execute(f"""
            DO $$
            BEGIN
                ALTER TABLE {_SCHEMA}.{table} VALIDATE CONSTRAINT {constraint};
            EXCEPTION WHEN foreign_key_violation THEN
                COMMENT ON CONSTRAINT {constraint} ON {_SCHEMA}.{table}
                    IS '{_NOTE}';
            END
            $$;
        """)


def downgrade() -> None:
    for table, parent, unique, _scope, _target in _planes():
        op.drop_constraint(
            f"fk_{table}_obligation_price", table, schema=_SCHEMA, type_="foreignkey"
        )
        op.drop_constraint(unique, parent, schema=_SCHEMA, type_="unique")
