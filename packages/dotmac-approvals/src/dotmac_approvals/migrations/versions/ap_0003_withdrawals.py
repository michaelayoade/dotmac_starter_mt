"""Append-only withdrawal evidence on each selected approvals plane.

Revision ID: ap_0003_withdrawals
Revises: ap_0002_outbox_relay
Create Date: 2026-09-24
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "ap_0003_withdrawals"
down_revision = "ap_0002_outbox_relay"
branch_labels = None
depends_on = None

MODULE_CODE = "approvals"
COMMON_REQUIRES: tuple[str, ...] = ()
TENANT_REQUIRES: tuple[str, ...] = ()
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

_SCHEMA = "mod_approvals"


def _install_withdrawal_guards(*, tenant: bool) -> None:
    """One DB invariant, specialized only by the closed boolean plane switch.

    SQL identifiers are local literals selected by ``tenant``; no request or
    migration configuration string reaches interpolation. An architecture test
    pins the only call sites to literal True/False, the S608 premise.
    """
    table = "approval_withdrawals" if tenant else "platform_approval_withdrawals"
    request = "approval_requests" if tenant else "platform_approval_requests"
    prefix = "tenant" if tenant else "platform"
    join = "r.tenant_id = NEW.tenant_id AND " if tenant else ""
    key = "tenant_id = p_tenant_id AND " if tenant else ""
    row_key = "tenant_id = NEW.tenant_id AND " if tenant else ""
    old_key = "tenant_id = OLD.tenant_id AND " if tenant else ""
    tenant_parameter = "p_tenant_id uuid, " if tenant else ""
    tenant_insert = "tenant_id, " if tenant else ""
    tenant_value = "p_tenant_id, " if tenant else ""
    outbox_table = "outbox_events" if tenant else "platform_outbox_events"
    outbox_tenant_column = "tenant_id, " if tenant else ""
    outbox_tenant_value = "NEW.tenant_id, " if tenant else ""
    # Match Python datetime.isoformat() after service._utc(): six fractional
    # digits when nonzero, no fractional part when zero, always UTC +00:00.
    approved_iso = (
        "(CASE WHEN extract(microseconds FROM v_approved_at)::integer % 1000000 = 0 "
        "THEN to_char(v_approved_at AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS') "
        "ELSE to_char(v_approved_at AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US') END || '+00:00')"
    )
    effective_iso = (
        "(CASE WHEN extract(microseconds FROM v_effective_at)::integer % 1000000 = 0 "
        "THEN to_char(v_effective_at AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS') "
        "ELSE to_char(v_effective_at AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US') END || '+00:00')"
    )
    tenant_guard = (
        "IF p_tenant_id IS DISTINCT FROM public.app_current_tenant_id() THEN "
        "RAISE EXCEPTION 'tenant withdrawal context disagrees' USING ERRCODE='42501'; "
        "END IF; "
        if tenant
        else ""
    )

    # Immediate insert check sees the ORIGINAL approved standing. This DB
    # boundary trusts Approvals-owned request.state; the service has already
    # evaluated the immutable votes and this trigger does NOT implement a
    # second policy evaluator. The deferred check sees the COMMITTED standing.
    op.execute(
        f"CREATE FUNCTION mod_approvals.check_{prefix}_withdrawal_insert() "  # noqa: S608 # nosec B608 -- identifiers are module-owned literals (schema, table, prefix); no input reaches this DDL
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE v_state text; v_completed timestamptz; BEGIN "
        f"SELECT r.state, r.completed_at INTO v_state, v_completed "
        f"FROM mod_approvals.{request} r WHERE {join}r.id = NEW.request_id "
        "FOR UPDATE; "
        "IF NOT FOUND OR v_state IS DISTINCT FROM 'approved' OR "
        "v_completed IS NULL OR NEW.approved_at IS DISTINCT FROM v_completed "
        "THEN RAISE EXCEPTION 'withdrawal requires the original completed "
        "approval and its exact approved_at'; END IF; "
        "RETURN NEW; END $$;"
    )
    op.execute(
        f"CREATE TRIGGER {table}_approved_before_insert BEFORE INSERT "
        f"ON mod_approvals.{table} FOR EACH ROW EXECUTE FUNCTION "
        f"mod_approvals.check_{prefix}_withdrawal_insert();"
    )
    op.execute(
        f"CREATE FUNCTION mod_approvals.check_{prefix}_withdrawal_commit() "  # noqa: S608 # nosec B608 -- identifiers are module-owned literals (schema, table, prefix); no input reaches this DDL
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE v_state text; v_completed timestamptz; BEGIN "
        f"SELECT state, completed_at INTO v_state, v_completed "
        f"FROM mod_approvals.{request} WHERE {row_key}id = NEW.request_id; "
        "IF NOT FOUND OR v_state IS DISTINCT FROM 'withdrawn' OR "
        "v_completed IS DISTINCT FROM NEW.approved_at THEN "
        "RAISE EXCEPTION 'withdrawal evidence and final standing must commit "
        "together'; END IF; RETURN NULL; END $$;"
    )
    op.execute(
        f"CREATE CONSTRAINT TRIGGER {table}_paired_commit AFTER INSERT "
        f"ON mod_approvals.{table} DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION mod_approvals.check_{prefix}_withdrawal_commit();"
    )
    op.execute(
        f"CREATE FUNCTION mod_approvals.check_{prefix}_withdrawn_transition() "  # noqa: S608 # nosec B608 -- identifiers are module-owned literals (schema, table, prefix); no input reaches this DDL
        "RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE v_count integer; BEGIN "
        "IF NEW.state = 'withdrawn' AND OLD.state IS DISTINCT FROM 'withdrawn' THEN "
        "IF current_user <> 'app_admin' OR OLD.state IS DISTINCT FROM 'approved' "
        "OR OLD.completed_at IS NULL OR "
        "NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN "
        "RAISE EXCEPTION 'only the database withdrawal operation may transition "
        "a completed approval' USING ERRCODE='42501'; END IF; "
        f"SELECT count(*) INTO v_count FROM mod_approvals.{table} "
        f"WHERE {old_key}request_id = OLD.id AND approved_at = OLD.completed_at; "
        "IF v_count <> 1 THEN RAISE EXCEPTION 'withdrawn standing requires "
        "exactly one matching evidence row'; END IF; "
        "ELSIF OLD.state = 'withdrawn' AND "
        "(NEW.state IS DISTINCT FROM OLD.state OR "
        "NEW.completed_at IS DISTINCT FROM OLD.completed_at) THEN "
        "RAISE EXCEPTION 'withdrawn standing is terminal'; END IF; "
        "RETURN NEW; END $$;"
    )
    op.execute(
        f"CREATE TRIGGER {request}_withdrawn_guard BEFORE UPDATE OF state, "
        f"completed_at ON mod_approvals.{request} FOR EACH ROW "
        f"EXECUTE FUNCTION mod_approvals.check_{prefix}_withdrawn_transition();"
    )
    op.execute(
        f"CREATE FUNCTION mod_approvals.check_{prefix}_withdrawn_insert() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF NEW.state = 'withdrawn' THEN "
        "RAISE EXCEPTION 'withdrawn standing cannot be inserted directly' "
        "USING ERRCODE='42501'; END IF; RETURN NEW; END $$;"
    )
    op.execute(
        f"CREATE TRIGGER {request}_withdrawn_insert_guard BEFORE INSERT "
        f"ON mod_approvals.{request} FOR EACH ROW EXECUTE FUNCTION "
        f"mod_approvals.check_{prefix}_withdrawn_insert();"
    )

    # The transition, not a chosen caller, owns delivery. Even the table owner
    # cannot pair evidence and state while omitting this AFTER trigger. The
    # evidence UUID is the outbox UUID, so one transition has one durable event.
    op.execute(
        f"CREATE FUNCTION mod_approvals.emit_{prefix}_withdrawal() "  # noqa: S608 # nosec B608 -- identifiers are module-owned literals (schema, table, prefix); no input reaches this DDL
        "RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"DECLARE v_evidence mod_approvals.{table}%ROWTYPE; "
        "v_approved_at timestamptz; v_effective_at timestamptz; BEGIN "
        "IF NEW.state IS DISTINCT FROM 'withdrawn' OR "
        "OLD.state = 'withdrawn' THEN RETURN NEW; END IF; "
        f"SELECT * INTO v_evidence FROM mod_approvals.{table} "
        f"WHERE {row_key}request_id = NEW.id; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'withdrawal evidence missing at "
        "event emission'; END IF; "
        "v_approved_at := v_evidence.approved_at; "
        "v_effective_at := v_evidence.effective_at; "
        f"INSERT INTO public.{outbox_table} "
        f"(id, {outbox_tenant_column}event_type, payload, status, attempts, "
        "correlation_id) VALUES "
        f"(v_evidence.id, {outbox_tenant_value}'approval.withdrawn', "
        "jsonb_build_object("
        "'request_id', NEW.id::text, "
        "'subject_type', NEW.subject_type, 'subject_id', NEW.subject_id, "
        "'policy_code', NEW.policy_code, 'policy_version', NEW.policy_version, "
        "'content_digest', NEW.content_digest, 'state', 'withdrawn', "
        "'withdrawal_id', v_evidence.id::text, "
        f"'approved_at', {approved_iso}, "
        "'actor_id', v_evidence.actor_id::text, "
        "'authority_ref', v_evidence.authority_ref, "
        "'reason', v_evidence.reason, "
        f"'effective_at', {effective_iso}, "
        "'external_ref', v_evidence.external_ref), 'pending', 0, "
        "v_evidence.external_ref); RETURN NEW; END $$;"
    )
    op.execute(
        f"CREATE TRIGGER {request}_withdrawn_event AFTER UPDATE OF state "
        f"ON mod_approvals.{request} FOR EACH ROW "
        f"EXECUTE FUNCTION mod_approvals.emit_{prefix}_withdrawal();"
    )

    # SECURITY DEFINER is the online-role write path. It locks the parent,
    # derives approved_at from the original completed_at, chooses DB time,
    # inserts evidence and changes standing in one statement/transaction; the
    # AFTER trigger above appends the outbox row for EVERY valid transition.
    # Tenant context is checked explicitly even if the owner bypasses RLS.
    op.execute(
        f"CREATE FUNCTION mod_approvals.record_{prefix}_withdrawal("  # noqa: S608 # nosec B608 -- identifiers are module-owned literals (schema, table, prefix); no input reaches this DDL
        f"{tenant_parameter}p_request_id uuid, p_withdrawal_id uuid, "
        "p_actor_id uuid, p_authority_ref text, p_reason text, p_external_ref text) "
        "RETURNS void LANGUAGE plpgsql SECURITY DEFINER "
        "SET search_path = pg_catalog AS $$ "
        "DECLARE v_approved_at timestamptz; v_effective_at timestamptz; BEGIN "
        f"{tenant_guard}"
        "IF p_authority_ref IS NULL OR btrim(p_authority_ref) = '' OR "
        "p_reason IS NULL OR btrim(p_reason) = '' OR "
        "p_external_ref IS NULL OR btrim(p_external_ref) = '' OR "
        "length(p_authority_ref) > 200 OR length(p_external_ref) > 200 THEN "
        "RAISE EXCEPTION 'withdrawal authority, reason and reference are required'; "
        "END IF; "
        f"SELECT completed_at INTO v_approved_at FROM mod_approvals.{request} "
        f"WHERE {key}id = p_request_id AND state = 'approved' FOR UPDATE; "
        "IF NOT FOUND OR v_approved_at IS NULL THEN "
        "RAISE EXCEPTION 'only a completed approval may be withdrawn'; END IF; "
        "v_effective_at := clock_timestamp(); "
        f"INSERT INTO mod_approvals.{table} "
        f"(id, {tenant_insert}request_id, actor_id, authority_ref, reason, "
        "effective_at, external_ref, approved_at) VALUES "
        f"(p_withdrawal_id, {tenant_value}p_request_id, p_actor_id, "
        "p_authority_ref, p_reason, v_effective_at, p_external_ref, v_approved_at); "
        f"UPDATE mod_approvals.{request} SET state = 'withdrawn' "
        f"WHERE {key}id = p_request_id; END $$;"
    )
    signature = (
        "uuid, uuid, uuid, uuid, text, text, text"
        if tenant
        else "uuid, uuid, uuid, text, text, text"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION mod_approvals.record_{prefix}_withdrawal({signature}) "
        "FROM PUBLIC;"
    )
    role = "app_user, platform_api" if tenant else "platform_api"
    op.execute(
        f"GRANT EXECUTE ON FUNCTION mod_approvals.record_{prefix}_withdrawal"
        f"({signature}) TO {role};"
    )


def _evidence_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("authority_ref", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_ref", sa.String(200), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.TENANT in planes:
        op.create_table(
            "approval_withdrawals",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid(), nullable=False),
            sa.Column("request_id", sa.Uuid(), nullable=False),
            *_evidence_columns(),
            sa.ForeignKeyConstraint(
                ["tenant_id", "request_id"],
                [
                    "mod_approvals.approval_requests.tenant_id",
                    "mod_approvals.approval_requests.id",
                ],
                name="fk_approval_withdrawals_request",
            ),
            sa.UniqueConstraint(
                "tenant_id", "request_id", name="uq_approval_withdrawals_request"
            ),
            sa.UniqueConstraint(
                "tenant_id", "external_ref", name="uq_approval_withdrawals_external_ref"
            ),
            sa.CheckConstraint(
                "effective_at >= approved_at",
                name="ck_approval_withdrawals_effective_after_approval",
            ),
            schema=_SCHEMA,
        )
        op.execute(
            "ALTER TABLE mod_approvals.approval_withdrawals ENABLE ROW LEVEL SECURITY;"
        )
        op.execute(
            "ALTER TABLE mod_approvals.approval_withdrawals FORCE ROW LEVEL SECURITY;"
        )
        op.execute(
            "CREATE POLICY approval_withdrawals_tenant_isolation "
            "ON mod_approvals.approval_withdrawals "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            "GRANT SELECT ON mod_approvals.approval_withdrawals "
            "TO app_user, platform_api;"
        )
    if ModulePlane.PLATFORM in planes:
        op.create_table(
            "platform_approval_withdrawals",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("request_id", sa.Uuid(), nullable=False),
            *_evidence_columns(),
            sa.ForeignKeyConstraint(
                ["request_id"],
                ["mod_approvals.platform_approval_requests.id"],
                name="fk_platform_approval_withdrawals_request",
            ),
            sa.UniqueConstraint(
                "request_id", name="uq_platform_approval_withdrawals_request"
            ),
            sa.UniqueConstraint(
                "external_ref", name="uq_platform_approval_withdrawals_external_ref"
            ),
            sa.CheckConstraint(
                "effective_at >= approved_at",
                name="ck_platform_approval_withdrawals_effective_after_approval",
            ),
            schema=_SCHEMA,
        )
        op.execute(
            "GRANT SELECT ON mod_approvals.platform_approval_withdrawals "
            "TO platform_api, app_admin;"
        )
        op.execute(
            "REVOKE ALL ON mod_approvals.platform_approval_withdrawals FROM app_user;"
        )

    # A withdrawal is evidence, not a mutable note. This trigger also fires for
    # the table owner; ordinary DML grants alone cannot make a row immutable.
    op.execute(
        "CREATE FUNCTION mod_approvals.refuse_withdrawal_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'approval withdrawal evidence is append-only'; "
        "END $$;"
    )
    for table, plane in (
        ("approval_withdrawals", ModulePlane.TENANT),
        ("platform_approval_withdrawals", ModulePlane.PLATFORM),
    ):
        if plane in planes:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE "
                f"ON mod_approvals.{table} FOR EACH ROW "
                "EXECUTE FUNCTION mod_approvals.refuse_withdrawal_mutation();"
            )
    if ModulePlane.TENANT in planes:
        _install_withdrawal_guards(tenant=True)
    if ModulePlane.PLATFORM in planes:
        _install_withdrawal_guards(tenant=False)


def downgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    selected = tuple(
        (tenant, plane)
        for tenant, plane in (
            (True, ModulePlane.TENANT),
            (False, ModulePlane.PLATFORM),
        )
        if plane in planes
    )
    # Lock and inspect EVERY selected plane before dropping ANY object. A
    # both-plane downgrade with tenant empty/platform populated must not drop
    # the tenant table before discovering platform evidence. Missing tables
    # are drift and fail at LOCK; there is no blanket IF EXISTS escape hatch.
    for tenant, _plane in selected:
        lock_sql = (
            "LOCK TABLE mod_approvals.approval_withdrawals IN ACCESS EXCLUSIVE MODE;"
            if tenant
            else "LOCK TABLE mod_approvals.platform_approval_withdrawals "
            "IN ACCESS EXCLUSIVE MODE;"
        )
        op.execute(lock_sql)
    for tenant, _plane in selected:
        table = "approval_withdrawals" if tenant else "platform_approval_withdrawals"
        query = (
            "SELECT EXISTS (SELECT 1 FROM mod_approvals.approval_withdrawals)"
            if tenant
            else "SELECT EXISTS (SELECT 1 FROM "
            "mod_approvals.platform_approval_withdrawals)"
        )
        has_evidence = op.get_bind().execute(sa.text(query)).scalar_one()
        if has_evidence:
            raise RuntimeError(
                f"cannot downgrade ap_0003: mod_approvals.{table} contains "
                "immutable withdrawal evidence"
            )
    for tenant, _plane in selected:
        table = "approval_withdrawals" if tenant else "platform_approval_withdrawals"
        request = "approval_requests" if tenant else "platform_approval_requests"
        prefix = "tenant" if tenant else "platform"
        signature = (
            "uuid, uuid, uuid, uuid, text, text, text"
            if tenant
            else "uuid, uuid, uuid, text, text, text"
        )
        op.execute(
            f"DROP FUNCTION mod_approvals.record_{prefix}_withdrawal({signature});"
        )
        op.execute(
            f"DROP TRIGGER {request}_withdrawn_event ON mod_approvals.{request};"
        )
        op.execute(f"DROP FUNCTION mod_approvals.emit_{prefix}_withdrawal();")
        op.execute(
            f"DROP TRIGGER {request}_withdrawn_guard ON mod_approvals.{request};"
        )
        op.execute(
            f"DROP TRIGGER {request}_withdrawn_insert_guard ON mod_approvals.{request};"
        )
        op.execute(f"DROP FUNCTION mod_approvals.check_{prefix}_withdrawn_insert();")
        op.execute(
            f"DROP FUNCTION mod_approvals.check_{prefix}_withdrawn_transition();"
        )
        op.execute(f"DROP TRIGGER {table}_paired_commit ON mod_approvals.{table};")
        op.execute(f"DROP FUNCTION mod_approvals.check_{prefix}_withdrawal_commit();")
        op.drop_table(table, schema=_SCHEMA)
        op.execute(f"DROP FUNCTION mod_approvals.check_{prefix}_withdrawal_insert();")
    op.execute("DROP FUNCTION mod_approvals.refuse_withdrawal_mutation();")
