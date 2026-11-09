"""Hold capped or prepaid renewals at ``pending_validity`` until the owner evidences them.

``su_0001`` .. ``su_0005`` keep their bytes.  This revision is additive except
for two replaced guards:

* the occurrence ``state`` CHECK gains ``pending_validity``;
* five nullable, TYPED columns record the hold reason and the owner's evidence
  (owner code, evidence reference, resolved-at, command id).  There is no JSON.
  They are all NULL until resolution; one CHECK ties them to the state;
* ``freeze_occurrence_content`` (shared by both planes) is replaced.  Every
  other column must stay equal (the ``to_jsonb`` comparison) and the
  acknowledgement rule is kept.  The only state change permitted is
  ``pending_validity -> emitted | cancelled``, and only when the owner's
  evidence columns arrive in the same update.  ``emitted -> cancelled`` and every
  other transition stay forbidden.

A pending row blocks overlap (the su_0005 trigger counts every non-cancelled
state).  The rated output is staged at resolution, by the service, never at the
pending insert.

The downgrade restores the ``su_0001`` function body verbatim and refuses while
any occurrence is held or carries owner evidence, because dropping the columns
would erase it.

Revision ID: su_0006_pending_validity
Revises: su_0005_line_coverage_overlap
Create Date: 2026-11-09
"""

from __future__ import annotations

from typing import NamedTuple

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "su_0006_pending_validity"
down_revision = "su_0005_line_coverage_overlap"
branch_labels = None
depends_on = None

MODULE_CODE = "subscriptions"
_SCHEMA = "mod_subscriptions"
_EVIDENCE = (
    "validity_owner_code",
    "validity_evidence_ref",
    "validity_resolved_at",
    "validity_resolution_command_id",
)
_NULL_EVIDENCE = " AND ".join(f"{c} IS NULL" for c in _EVIDENCE)
_SET_EVIDENCE = " AND ".join(f"{c} IS NOT NULL" for c in _EVIDENCE)
_OLD_STATES = "'scheduled', 'due', 'emitted', 'cancelled'"
_NEW_STATES = _OLD_STATES + ", 'pending_validity'"


class _PlaneTables(NamedTuple):
    occurrences: str
    state_check: str
    hold_check: str
    evidence_check: str
    command_unique: str
    command_columns: tuple[str, ...]


_TABLES = {
    ModulePlane.TENANT: _PlaneTables(
        occurrences="recurring_charge_occurrences",
        state_check="ck_occurrences_state",
        hold_check="ck_occurrences_validity_hold",
        evidence_check="ck_occurrences_validity_evidence",
        command_unique="uq_occurrences_validity_command",
        command_columns=("tenant_id", "validity_resolution_command_id"),
    ),
    ModulePlane.PLATFORM: _PlaneTables(
        occurrences="platform_recurring_charge_occurrences",
        state_check="ck_platform_occurrences_state",
        hold_check="ck_platform_occurrences_validity_hold",
        evidence_check="ck_platform_occurrences_validity_evidence",
        command_unique="uq_platform_occurrences_validity_command",
        command_columns=("validity_resolution_command_id",),
    ),
}

#: The su_0001 function body, byte for byte, restored by the downgrade.
_SU_0001_FUNCTION = """
        CREATE OR REPLACE FUNCTION mod_subscriptions.freeze_occurrence_content()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'recurring charge occurrences cannot be deleted'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF (to_jsonb(NEW) - 'output_acknowledged_at') IS DISTINCT FROM
             (to_jsonb(OLD) - 'output_acknowledged_at') THEN
            RAISE EXCEPTION 'recurring charge occurrence content is immutable'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF OLD.output_acknowledged_at IS NOT NULL OR
             NEW.output_acknowledged_at IS NULL THEN
            RAISE EXCEPTION 'occurrence acknowledgement is append-only'
              USING ERRCODE = 'restrict_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """

_FREEZE_ALLOWED = (
    "ARRAY['output_acknowledged_at', 'state', 'emitted_at', "
    "'validity_owner_code', 'validity_evidence_ref', 'validity_resolved_at', "
    "'validity_resolution_command_id']"
)

_FREEZE_PENDING_VALIDITY = f"""
        CREATE OR REPLACE FUNCTION mod_subscriptions.freeze_occurrence_content()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'recurring charge occurrences cannot be deleted'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF (to_jsonb(NEW) - {_FREEZE_ALLOWED}) IS DISTINCT FROM
             (to_jsonb(OLD) - {_FREEZE_ALLOWED}) THEN
            RAISE EXCEPTION 'recurring charge occurrence content is immutable'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF OLD.state = 'pending_validity' THEN
            IF NEW.state NOT IN ('emitted', 'cancelled')
               OR NULLIF(BTRIM(NEW.validity_owner_code), '') IS NULL
               OR NEW.validity_evidence_ref IS NULL
               OR NEW.validity_resolved_at IS NULL
               OR NEW.validity_resolution_command_id IS NULL
               OR NEW.output_acknowledged_at IS DISTINCT FROM OLD.output_acknowledged_at
               OR (NEW.state = 'cancelled' AND NEW.emitted_at IS DISTINCT FROM OLD.emitted_at)
            THEN
              RAISE EXCEPTION
                'a pending_validity occurrence is resolved only by owner evidence, to emitted or cancelled'
                USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.state IS DISTINCT FROM OLD.state
             OR NEW.emitted_at IS DISTINCT FROM OLD.emitted_at
             OR NEW.validity_owner_code IS DISTINCT FROM OLD.validity_owner_code
             OR NEW.validity_evidence_ref IS DISTINCT FROM OLD.validity_evidence_ref
             OR NEW.validity_resolved_at IS DISTINCT FROM OLD.validity_resolved_at
             OR NEW.validity_resolution_command_id
                IS DISTINCT FROM OLD.validity_resolution_command_id THEN
            RAISE EXCEPTION 'occurrence state is terminal outside pending_validity resolution'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF OLD.output_acknowledged_at IS NOT NULL OR
             NEW.output_acknowledged_at IS NULL THEN
            RAISE EXCEPTION 'occurrence acknowledgement is append-only'
              USING ERRCODE = 'restrict_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """


def _selected() -> tuple[_PlaneTables, ...]:
    planes = selected_module_planes(MODULE_CODE)
    return tuple(_TABLES[plane] for plane in ModulePlane if plane in planes)


def _q(table: str) -> str:
    return f"{_SCHEMA}.{table}"


def _upgrade_plane(tables: _PlaneTables) -> None:
    table = tables.occurrences
    op.add_column(
        table,
        sa.Column("validity_hold_reason", sa.String(16), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        table,
        sa.Column("validity_owner_code", sa.String(120), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        table,
        sa.Column("validity_evidence_ref", sa.Uuid(), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        table,
        sa.Column("validity_resolved_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        table,
        sa.Column("validity_resolution_command_id", sa.Uuid(), nullable=True),
        schema=_SCHEMA,
    )
    op.drop_constraint(tables.state_check, table, type_="check", schema=_SCHEMA)
    op.create_check_constraint(
        tables.state_check, table, f"state IN ({_NEW_STATES})", schema=_SCHEMA
    )
    op.create_check_constraint(
        tables.hold_check,
        table,
        "validity_hold_reason IS NULL OR validity_hold_reason IN ('capped', 'prepaid')",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        tables.evidence_check,
        table,
        "(state = 'pending_validity' AND validity_hold_reason IS NOT NULL AND "
        f"{_NULL_EVIDENCE}) OR (state <> 'pending_validity' AND "
        f"((validity_hold_reason IS NULL AND {_NULL_EVIDENCE}) OR "
        f"(validity_hold_reason IS NOT NULL AND {_SET_EVIDENCE})))",
        schema=_SCHEMA,
    )
    op.create_unique_constraint(
        tables.command_unique, table, list(tables.command_columns), schema=_SCHEMA
    )


def upgrade() -> None:
    for tables in _selected():
        _upgrade_plane(tables)
    op.execute(_FREEZE_PENDING_VALIDITY)


def downgrade() -> None:
    selected = _selected()
    for tables in selected:
        occurrences = _q(tables.occurrences)
        op.execute(f"LOCK TABLE {occurrences} IN ACCESS EXCLUSIVE MODE;")
        held = (
            op.get_bind()
            .execute(
                sa.text(
                    f"SELECT count(*) FROM {occurrences} "  # noqa: S608 # nosec B608 -- module-owned literal identifiers
                    "WHERE validity_hold_reason IS NOT NULL OR state = 'pending_validity'"
                )
            )
            .scalar_one()
        )
        if held:
            raise RuntimeError(
                f"{occurrences} holds {held} held or owner-resolved occurrence(s); "
                "dropping the validity columns would erase that evidence"
            )
    op.execute(_SU_0001_FUNCTION)
    for tables in reversed(selected):
        table = tables.occurrences
        op.drop_constraint(tables.command_unique, table, type_="unique", schema=_SCHEMA)
        op.drop_constraint(tables.evidence_check, table, type_="check", schema=_SCHEMA)
        op.drop_constraint(tables.hold_check, table, type_="check", schema=_SCHEMA)
        op.drop_constraint(tables.state_check, table, type_="check", schema=_SCHEMA)
        op.create_check_constraint(
            tables.state_check, table, f"state IN ({_OLD_STATES})", schema=_SCHEMA
        )
        for column in (
            "validity_resolution_command_id",
            "validity_resolved_at",
            "validity_evidence_ref",
            "validity_owner_code",
            "validity_hold_reason",
        ):
            op.drop_column(table, column, schema=_SCHEMA)
