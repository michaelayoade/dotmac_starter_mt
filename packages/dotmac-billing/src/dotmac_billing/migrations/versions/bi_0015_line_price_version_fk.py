"""A rated document line must name its obligation's own price version.

``document_lines(obligation_id, price_source_version)`` now references
``rated_obligations(id, price_version_id)`` through a composite foreign key, so
the price a line bills at is the price its obligation was rated at, enforced by
the database rather than by service code.

The key is MATCH SIMPLE, so on its own it would exempt any line without an
``obligation_id``. That exemption is therefore keyed on the document, not on
the NULL: a BEFORE INSERT / UPDATE-OF-identity trigger refuses a line with no
obligation unless its parent is a credit note and the line carries the
``price_source_version = 'credit:' || credits_document_id`` the credit writer
sets. Opening-import invoices carry no document lines at all (the importer
writes the document row only), so there is no opening-line exemption. The
trigger fires only on new writes (and on updates of the identity columns); it
never inspects, and so cannot reject, existing rows. They are listed for the
manual exceptions review by ``list_document_line_price_exceptions``.

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
        _create_guard(table, scope)


def _create_guard(table: str, scope: list[str]) -> None:
    """Refuse a new line with no obligation unless it is a credit-note line."""

    prefix = "platform_" if table.startswith("platform_") else ""
    scoped = " AND d.tenant_id = NEW.tenant_id" if scope else ""
    function = f"{_SCHEMA}.require_{prefix}document_line_obligation"
    op.execute(f"""
        CREATE FUNCTION {function}() RETURNS trigger AS $$
        DECLARE
            parent record;
        BEGIN
            IF NEW.obligation_id IS NOT NULL THEN
                RETURN NEW;
            END IF;
            SELECT d.document_kind, d.credits_document_id INTO parent
              FROM {_SCHEMA}.{prefix}documents d
             WHERE d.id = NEW.document_id{scoped};
            IF NOT FOUND THEN
                RETURN NEW;  -- the document foreign key refuses it
            END IF;
            IF parent.document_kind = 'credit_note'
               AND parent.credits_document_id IS NOT NULL
               AND NEW.price_source_version = 'credit:' || parent.credits_document_id::text
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'a document line needs its rated obligation unless it is a credit-note line'
              USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER {table}_price_identity
          BEFORE INSERT OR UPDATE OF obligation_id, price_source_version, document_id
          ON {_SCHEMA}.{table}
          FOR EACH ROW EXECUTE FUNCTION {function}();
    """)  # nosec B608 -- closed plane/table identifiers; no input reaches this DDL
    # The same rule seen from the document: changing a document's kind or the
    # credit it names must not leave an existing line outside the guard. Only a
    # real change fires it, so history is never inspected by a no-op update.
    documents = f"{_SCHEMA}.{prefix}documents"
    doc_function = f"{_SCHEMA}.recheck_{prefix}document_line_obligation"
    scoped_line = " AND l.tenant_id = NEW.tenant_id" if scope else ""
    op.execute(f"""
        CREATE FUNCTION {doc_function}() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM {_SCHEMA}.{table} l
                 WHERE l.document_id = NEW.id{scoped_line}
                   AND l.obligation_id IS NULL
                   AND NOT (
                        NEW.document_kind = 'credit_note'
                        AND NEW.credits_document_id IS NOT NULL
                        AND l.price_source_version
                            = 'credit:' || NEW.credits_document_id::text)
            ) THEN
                RAISE EXCEPTION 'a document line needs its rated obligation unless it is a credit-note line'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER {prefix}documents_line_price_identity
          BEFORE UPDATE OF document_kind, credits_document_id ON {documents}
          FOR EACH ROW
          WHEN (OLD.document_kind IS DISTINCT FROM NEW.document_kind
                OR OLD.credits_document_id IS DISTINCT FROM NEW.credits_document_id)
          EXECUTE FUNCTION {doc_function}();
    """)  # nosec B608 -- closed plane/table identifiers; no input reaches this DDL


def downgrade() -> None:
    for table, parent, unique, _scope, _target in _planes():
        prefix = "platform_" if table.startswith("platform_") else ""
        op.execute(
            f"DROP TRIGGER IF EXISTS {table}_price_identity ON {_SCHEMA}.{table};"
            f"DROP TRIGGER IF EXISTS {prefix}documents_line_price_identity "
            f"ON {_SCHEMA}.{prefix}documents;"
            f"DROP FUNCTION IF EXISTS "
            f"{_SCHEMA}.require_{prefix}document_line_obligation();"
            f"DROP FUNCTION IF EXISTS "
            f"{_SCHEMA}.recheck_{prefix}document_line_obligation();"
        )
        op.drop_constraint(
            f"fk_{table}_obligation_price", table, schema=_SCHEMA, type_="foreignkey"
        )
        op.drop_constraint(unique, parent, schema=_SCHEMA, type_="unique")
