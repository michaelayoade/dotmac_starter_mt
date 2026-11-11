"""One contract line is never charged twice for the same instant of service.

Adds, on both planes, a trigger that refuses an occurrence whose rating
coverage ``[rating_coverage_start, rating_coverage_end)`` overlaps another
non-cancelled occurrence of the same (tenant, contract, contract line key).  The
key is the contract and the stable line KEY, not the contract version, so the
same coverage recorded again under a NEW contract version is refused too.  A
``cancelled`` occurrence never blocks.

This is the su_0001 mechanism (``prevent_tenant_contract_overlap``): an
advisory transaction lock keyed on the scope, then an overlap probe that
raises ``exclusion_violation``.  It needs no extension, creates nothing in
``public`` and is not deferrable.  It is NOT safe under REPEATABLE READ (the
probe would read a stale snapshot), so the trigger refuses to run there.
(su_0001's contract-overlap triggers have the same gap; that is a recorded
follow-up and is deliberately not changed here.)

Corrections
-----------
Billing reads a correction as a REPLACEMENT: the successor obligation carries
the whole amount, and a superseded obligation cannot be invoiced.  So a
correction may overlap its own correction chain (the occurrences it replaces and
those that replace it), and a superseded occurrence stops blocking.  The chain
must be one contract line: a correction whose predecessor has a different
contract or line key is refused.  A new unique ``(tenant, corrects_occurrence_id)``
gives an occurrence at most one LIVE successor, as Billing does (it is a partial
unique index over non-cancelled rows, so a cancelled successor frees the slot).
A correction may replace only an ``emitted`` occurrence, and it is exempt from
overlap with its immediate predecessor only (not with older ancestors, which may
be live again once an intermediate correction is cancelled).  A rival successor
of the same predecessor is left to that constraint (so the refusal names it)
rather than reported as a coverage overlap.

At a contract-version supersession at ``S`` the new version's first occurrence
covers ``[S, end)``; an old emitted occurrence straddling ``S`` is re-covered
``[P0, S)`` by a correction.  An old pending occurrence is cancelled.

The trigger re-checks on UPDATE only when state or coverage changes, and skips a
superseded or cancelled row, so acknowledging an old occurrence is never blocked
by a later one.

Existing rows
-------------
A trigger cannot be added ``NOT VALID``.  The upgrade counts, under an ACCESS
EXCLUSIVE lock, existing overlapping current occurrences and duplicate
corrections, and REFUSES before any DDL when there are any.  It repairs nothing;
the rows go to the manual exceptions review in
``docs/inventories/subscriptions-catalog-price-exceptions.md``.

Revision ID: su_0005_line_coverage_overlap
Revises: su_0004_catalog_price_line
Create Date: 2026-11-09
"""

from __future__ import annotations

from typing import NamedTuple

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "su_0005_line_coverage_overlap"
down_revision = "su_0004_catalog_price_line"
branch_labels = None
depends_on = None

MODULE_CODE = "subscriptions"
_SCHEMA = "mod_subscriptions"


class _PlaneTables(NamedTuple):
    occurrences: str
    function_name: str
    trigger_name: str
    unique_name: str
    index_name: str
    tenant_scoped: bool


_TABLES = {
    ModulePlane.TENANT: _PlaneTables(
        occurrences="recurring_charge_occurrences",
        function_name="prevent_occurrence_coverage_overlap",
        trigger_name="occurrences_coverage_overlap",
        unique_name="uq_occurrences_correction",
        index_name="ix_occurrences_line_coverage",
        tenant_scoped=True,
    ),
    ModulePlane.PLATFORM: _PlaneTables(
        occurrences="platform_recurring_charge_occurrences",
        function_name="prevent_platform_occurrence_coverage_overlap",
        trigger_name="platform_occurrences_coverage_overlap",
        unique_name="uq_platform_occurrences_correction",
        index_name="ix_platform_occurrences_line_coverage",
        tenant_scoped=False,
    ),
}


def _q(table: str) -> str:
    return f"{_SCHEMA}.{table}"


def _selected() -> tuple[_PlaneTables, ...]:
    planes = selected_module_planes(MODULE_CODE)
    return tuple(_TABLES[plane] for plane in ModulePlane if plane in planes)


def _scope(tables: _PlaneTables, left: str, right: str) -> str:
    columns = ["contract_id", "contract_line_key"]
    if tables.tenant_scoped:
        columns.insert(0, "tenant_id")
    return " AND ".join(f"{left}.{c} = {right}.{c}" for c in columns)


def _refuse_existing_conflicts(tables: _PlaneTables) -> None:
    occurrences = _q(tables.occurrences)
    op.execute(f"LOCK TABLE {occurrences} IN ACCESS EXCLUSIVE MODE;")
    bind = op.get_bind()
    tenant = "a.tenant_id = b.tenant_id AND " if tables.tenant_scoped else ""
    successor_tenant = (
        "AND s.tenant_id = {alias}.tenant_id " if tables.tenant_scoped else ""
    )

    def is_current(alias: str) -> str:
        return (
            f"{alias}.state <> 'cancelled' AND NOT EXISTS (SELECT 1 FROM {occurrences} s "  # noqa: S608 # nosec B608 -- identifiers are module-owned literals
            f"WHERE s.corrects_occurrence_id = {alias}.id AND s.state <> 'cancelled' "
            + successor_tenant.format(alias=alias)
            + ")"
        )

    overlapping = bind.execute(
        sa.text(
            f"SELECT count(*) FROM {occurrences} a JOIN {occurrences} b "  # noqa: S608 # nosec B608 -- identifiers are module-owned literals
            f"ON a.id < b.id AND {tenant}{_scope(tables, 'a', 'b')} "
            f"AND {is_current('a')} AND {is_current('b')} "
            "AND a.rating_coverage_start < b.rating_coverage_end "
            "AND b.rating_coverage_start < a.rating_coverage_end"
        )
    ).scalar_one()
    keys = (
        "tenant_id, corrects_occurrence_id"
        if tables.tenant_scoped
        else "corrects_occurrence_id"
    )
    duplicated = bind.execute(
        sa.text(
            f"SELECT count(*) FROM (SELECT 1 FROM {occurrences} "  # noqa: S608 # nosec B608 -- identifiers are module-owned literals
            f"WHERE corrects_occurrence_id IS NOT NULL AND state <> 'cancelled' "
            f"GROUP BY {keys} "
            "HAVING count(*) > 1) d"
        )
    ).scalar_one()
    if overlapping or duplicated:
        raise RuntimeError(
            f"{occurrences} holds {overlapping} overlapping current occurrence "
            f"pair(s) and {duplicated} occurrence(s) with more than one correction "
            "successor. Nothing was changed - send them to the manual exceptions "
            "review (docs/inventories/subscriptions-catalog-price-exceptions.md) "
            "and re-run"
        )


def _function_sql(tables: _PlaneTables) -> str:
    occ = _q(tables.occurrences)
    key_tenant = "NEW.tenant_id::text || ':' || " if tables.tenant_scoped else ""

    # Row-scope predicates, spelled per alias (the platform plane has no tenant).
    def same_scope(alias: str) -> str:
        parts = [
            f"{alias}.contract_id = NEW.contract_id",
            f"{alias}.contract_line_key = NEW.contract_line_key",
        ]
        if tables.tenant_scoped:
            parts.insert(0, f"{alias}.tenant_id = NEW.tenant_id")
        return " AND ".join(parts)

    def same_tenant(alias: str) -> str:
        return f" AND {alias}.tenant_id = NEW.tenant_id" if tables.tenant_scoped else ""

    return f"""
        CREATE OR REPLACE FUNCTION {_q(tables.function_name)}()
        RETURNS trigger AS $$
        BEGIN
          IF current_setting('transaction_isolation') = 'repeatable read' THEN
            RAISE EXCEPTION
              'occurrence coverage exclusion is not safe under REPEATABLE READ'
              USING ERRCODE = 'feature_not_supported';
          END IF;
          IF TG_OP = 'UPDATE'
             AND OLD.state = NEW.state
             AND OLD.rating_coverage_start = NEW.rating_coverage_start
             AND OLD.rating_coverage_end = NEW.rating_coverage_end THEN
            RETURN NEW;
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            {key_tenant}NEW.contract_id::text || ':' || NEW.contract_line_key::text, 0));
          IF NEW.corrects_occurrence_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM {occ} p
             WHERE p.id = NEW.corrects_occurrence_id AND {same_scope('p')}
          ) THEN
            RAISE EXCEPTION
              'a correction must replace an occurrence of the same contract line'
              USING ERRCODE = 'check_violation';
          END IF;
          IF NEW.corrects_occurrence_id IS NOT NULL AND NEW.state <> 'cancelled'
             AND NOT EXISTS (
            SELECT 1 FROM {occ} p
             WHERE p.id = NEW.corrects_occurrence_id AND p.state = 'emitted'
          ) THEN
            RAISE EXCEPTION
              'a correction may replace only an emitted occurrence'
              USING ERRCODE = 'check_violation';
          END IF;
          IF NEW.state = 'cancelled' OR EXISTS (
            SELECT 1 FROM {occ} s
             WHERE s.corrects_occurrence_id = NEW.id AND s.state <> 'cancelled'
               {same_tenant('s')}
          ) THEN
            RETURN NEW;
          END IF;
          IF EXISTS (
            WITH RECURSIVE down(id) AS (
              SELECT o.id FROM {occ} o
               WHERE o.corrects_occurrence_id = NEW.id{same_tenant('o')}
              UNION ALL
              SELECT o.id FROM {occ} o JOIN down d ON o.corrects_occurrence_id = d.id
               WHERE true{same_tenant('o')}
            )
            SELECT 1 FROM {occ} o
             WHERE {same_scope('o')}
               AND o.id <> NEW.id
               AND o.state <> 'cancelled'
               AND o.rating_coverage_start < NEW.rating_coverage_end
               AND NEW.rating_coverage_start < o.rating_coverage_end
               AND o.id IS DISTINCT FROM NEW.corrects_occurrence_id
               AND o.id NOT IN (SELECT id FROM down)
               AND (NEW.corrects_occurrence_id IS NULL
                    OR o.corrects_occurrence_id IS DISTINCT FROM NEW.corrects_occurrence_id)
               AND NOT EXISTS (
                 SELECT 1 FROM {occ} s
                  WHERE s.corrects_occurrence_id = o.id AND s.state <> 'cancelled'
                    AND s.id <> NEW.id{same_tenant('s')})
          ) THEN
            RAISE EXCEPTION
              'occurrence coverage overlaps another charge on the same contract line'
              USING ERRCODE = 'exclusion_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """  # noqa: S608 # nosec B608 -- identifiers are module-owned literals


def _upgrade_plane(tables: _PlaneTables) -> None:
    occurrences = _q(tables.occurrences)
    if tables.tenant_scoped:
        unique_columns = ["tenant_id", "corrects_occurrence_id"]
        index_columns = [
            "tenant_id",
            "contract_id",
            "contract_line_key",
            "rating_coverage_start",
        ]
    else:
        unique_columns = ["corrects_occurrence_id"]
        index_columns = ["contract_id", "contract_line_key", "rating_coverage_start"]
    # Partial: a cancelled successor does not use up the predecessor's slot.
    op.create_index(
        tables.unique_name,
        tables.occurrences,
        unique_columns,
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("state <> 'cancelled'"),
    )
    op.create_index(
        tables.index_name, tables.occurrences, index_columns, schema=_SCHEMA
    )
    op.execute(_function_sql(tables))
    op.execute(
        f"CREATE TRIGGER {tables.trigger_name} "
        f"BEFORE INSERT OR UPDATE ON {occurrences} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q(tables.function_name)}();"
    )


def upgrade() -> None:
    selected = _selected()
    for tables in selected:
        _refuse_existing_conflicts(tables)
    for tables in selected:
        _upgrade_plane(tables)


def downgrade() -> None:
    for tables in reversed(_selected()):
        occurrences = _q(tables.occurrences)
        op.execute(f"DROP TRIGGER IF EXISTS {tables.trigger_name} ON {occurrences};")
        op.execute(f"DROP FUNCTION IF EXISTS {_q(tables.function_name)}();")
        op.drop_index(tables.index_name, table_name=tables.occurrences, schema=_SCHEMA)
        op.drop_index(tables.unique_name, table_name=tables.occurrences, schema=_SCHEMA)
