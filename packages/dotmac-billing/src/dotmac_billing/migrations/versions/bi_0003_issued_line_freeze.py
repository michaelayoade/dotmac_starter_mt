"""Close line mutation for every issued Billing document kind.

Keep the original trigger identities; serialize insertion, edits, reparenting
and deletion against issuance. Historical documents and facts stay untouched.
"""

from __future__ import annotations

# All SQL fragments come from the two fixed module planes and boolean mode.
# ruff: noqa: S608
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy import text

from alembic import op

revision = "bi_0003_issued_line_freeze"
down_revision = "bi_0002_multiline"
branch_labels = None
depends_on = None


def _replace(*, platform: bool, all_kinds: bool) -> None:
    # Both inputs are booleans supplied by this migration: the only prefix is
    # "platform_" or "", and every remaining SQL fragment below is a literal.
    # Runtime document/tenant values remain trigger fields, never interpolated.
    prefix = "platform_" if platform else ""
    tenant_match = "" if platform else " AND tenant_id = NEW.tenant_id"
    kind_declaration = "" if all_kinds else "kind text;"
    selection = (
        "issued_at INTO issued"
        if all_kinds
        else "issued_at, document_kind INTO issued, kind"
    )
    predicate = (
        "issued IS NOT NULL" if all_kinds else "issued IS NOT NULL AND kind = 'invoice'"
    )
    noun = "document" if all_kinds else "invoice"
    op.execute(f"""
    CREATE OR REPLACE FUNCTION mod_billing.refuse_{prefix}issued_invoice_line_insert()
    RETURNS trigger AS $$
    DECLARE
        issued timestamptz;
        {kind_declaration}
    BEGIN
        SELECT {selection} FROM mod_billing.{prefix}documents
         WHERE id = NEW.document_id{tenant_match}
         FOR UPDATE;
        IF {predicate} THEN
            RAISE EXCEPTION 'issued {noun} refuses additional lines'
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """)  # nosec B608 -- boolean-selected module literals only; no untrusted SQL input


def _replace_mutation(*, platform: bool, locking: bool) -> None:
    # Prefix, function name and tenant predicates are closed literal choices;
    # OLD/NEW values are evaluated by PostgreSQL, never Python interpolation.
    prefix = "platform_" if platform else ""
    name = "platform" if platform else "tenant"
    tenant_old = "" if platform else " AND tenant_id = OLD.tenant_id"
    tenant_new = "" if platform else " AND tenant_id = NEW.tenant_id"
    if locking:
        body = f"""
        DECLARE
            parent record;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                FOR parent IN
                    SELECT issued_at FROM mod_billing.{prefix}documents
                     WHERE id = OLD.document_id{tenant_old}
                     FOR UPDATE
                LOOP
                    IF parent.issued_at IS NOT NULL THEN
                        RAISE EXCEPTION 'issued billing document line is immutable'
                            USING ERRCODE = 'restrict_violation';
                    END IF;
                END LOOP;
                RETURN OLD;
            END IF;
            -- Reparenting locks both scoped parents in a deterministic order.
            -- Visit matching parents, including drafts, in a stable lock order.
            FOR parent IN
                SELECT issued_at FROM mod_billing.{prefix}documents
                 WHERE (id = OLD.document_id{tenant_old})
                    OR (id = NEW.document_id{tenant_new})
                 ORDER BY id
                 FOR UPDATE
            LOOP
                IF parent.issued_at IS NOT NULL THEN
                    RAISE EXCEPTION 'issued billing document line is immutable'
                        USING ERRCODE = 'restrict_violation';
                END IF;
            END LOOP;
            RETURN NEW;
        END;
        """  # nosec B608 -- boolean-selected prefix/predicates only; no untrusted SQL input
    else:
        # Restore bi_0002's predecessor guard on downgrade.
        body = f"""
        BEGIN
            IF EXISTS (
                SELECT 1 FROM mod_billing.{prefix}documents
                 WHERE id = OLD.document_id{tenant_old}
                   AND issued_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'issued billing document line is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        """  # nosec B608 -- same closed prefix/predicates as upgrade; no untrusted SQL input
    op.execute(f"""
    CREATE OR REPLACE FUNCTION mod_billing.freeze_{name}_document_line()
    RETURNS trigger AS $$ {body} $$ LANGUAGE plpgsql
    """)


def _apply(*, all_kinds: bool) -> None:
    planes = selected_module_planes("billing")
    selected = [
        plane for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM) if plane in planes
    ]
    # Drain writers using the predecessor functions before replacing them.
    # Parent tables precede child tables in the same plane order on every run.
    tables = [
        f"{'platform_' if plane == ModulePlane.PLATFORM else ''}{table}"
        for table in ("documents", "document_lines")
        for plane in selected
    ]
    connection = op.get_bind()
    previous_timeout = connection.execute(text("SHOW lock_timeout")).scalar_one()
    previous_statement_timeout = connection.execute(
        text("SHOW statement_timeout")
    ).scalar_one()
    # Keep the bounded lock operation together; every selected table is
    # explicitly qualified, including each subsequent table in the list.
    op.execute(f"""
        SET LOCAL lock_timeout = '5s';
        SET LOCAL statement_timeout = '60s';
        LOCK TABLE mod_billing.{', mod_billing.'.join(tables)} IN ACCESS EXCLUSIVE MODE;
    """)
    # Restore the caller's settings; the acquired table locks remain held until
    # the migration transaction ends, including all function replacements.
    connection.execute(
        text(
            "SELECT pg_catalog.set_config('lock_timeout', :timeout, true), "
            "pg_catalog.set_config('statement_timeout', :statement_timeout, true)"
        ),
        {
            "timeout": previous_timeout,
            "statement_timeout": previous_statement_timeout,
        },
    )
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane in planes:
            _replace(platform=plane == ModulePlane.PLATFORM, all_kinds=all_kinds)
            _replace_mutation(platform=plane == ModulePlane.PLATFORM, locking=all_kinds)


def upgrade() -> None:
    _apply(all_kinds=True)


def downgrade() -> None:
    _apply(all_kinds=False)
