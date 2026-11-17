"""PostgreSQL proof of the money-flows ``mf_0002`` FundedCharge instance table."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from tests.test_money_flows_migration import (
    KERNEL,
    ROOT,
    VERSIONS,
    _as_tenant,
    _tenant,
)

TABLE = "mod_mflows.funded_charge_instances"


@pytest.fixture
def migrated(_mf_migrated):
    """The shared mf_0001 fixture: a database migrated to every money_flows head."""
    return _mf_migrated


MF_0001_SHA256 = "fa10195f884f7e6d7404e03af52d90003d24b91b7813f6c93ee1ea0c2f3ea03e"

_INSERT = text(
    "INSERT INTO mod_mflows.funded_charge_instances "
    "(id, tenant_id, state, reason_code, policy_version, definition_version, "
    "charge_ref, billing_account_id, service_ref, obligation_id, document_id, "
    "credit_draw_id, funding_settlement_id, deadline_at, resolution_ref, version) "
    "VALUES (:id, :tenant, :state, :reason, 'p1', 'd1', :charge, :account, "
    ":service, :obligation, :document, :draw, :funding, :deadline, :resolution, "
    ":version)"
)

ACCOUNT = uuid.UUID(int=10)
REFS = {
    "obligation": uuid.UUID(int=11),
    "document": uuid.UUID(int=12),
    "draw": uuid.UUID(int=13),
}
FUNDING = uuid.UUID(int=14)

# Inserted shapes per state.
_INSERT_STATES = {
    "pending": {},
    "discharged": dict(REFS),
    "attention_required": {"reason": "tax_evidence_refused"},
}


def _insert(connection, tenant, state, **overrides) -> uuid.UUID:
    row_id = uuid.uuid4()
    values = {
        "id": row_id,
        "tenant": tenant,
        "state": state,
        "reason": None,
        "charge": f"charge-{row_id}",
        "account": ACCOUNT,
        "service": "service-1",
        "obligation": None,
        "document": None,
        "draw": None,
        "funding": None,
        "deadline": None,
        "resolution": None,
        "version": 0,
    }
    values.update(overrides)
    connection.execute(_INSERT, values)
    return row_id


def _make(app, tenant, state):
    with app.begin() as connection:
        _as_tenant(connection, tenant)
        return _insert(connection, tenant, state, **_INSERT_STATES[state])


def _update(app, tenant, row, assignments, **params):
    with app.begin() as connection:
        _as_tenant(connection, tenant)
        connection.execute(
            text(f"UPDATE {TABLE} SET {assignments} WHERE id = :id"),  # noqa: S608
            {"id": row, **params},
        )


def _row(app, tenant, row):
    with app.begin() as connection:
        _as_tenant(connection, tenant)
        return (
            connection.execute(
                text(
                    "SELECT state, reason_code, obligation_id, funding_settlement_id, "  # noqa: S608
                    f"version FROM {TABLE} WHERE id = :id"
                ),
                {"id": row},
            )
            .one()
            ._mapping
        )


def test_table_has_forced_rls_no_delete_and_one_tenant_fk(migrated) -> None:
    engine = create_engine(migrated[0])
    with engine.begin() as connection:
        enabled, forced = connection.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'mod_mflows.funded_charge_instances'::regclass"
            )
        ).one()
        assert enabled and forced
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", True),
            ("DELETE", False),
            ("TRUNCATE", False),
        ):
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege('app_user', :t, :p)"),
                    {"t": TABLE, "p": privilege},
                )
                is expected
            ), privilege
        foreign_keys = list(
            connection.scalars(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_schema = 'mod_mflows' "
                    "AND table_name = 'funded_charge_instances' "
                    "AND constraint_type = 'FOREIGN KEY'"
                )
            )
        )
        assert foreign_keys == ["fk_funded_charge_instances_tenant"]
    engine.dispose()


def test_information_schema_vocabulary_is_instance_state_only(migrated) -> None:
    engine = create_engine(migrated[0])
    with engine.begin() as connection:
        columns = set(
            connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'mod_mflows' "
                    "AND table_name = 'funded_charge_instances'"
                )
            )
        )
        assert columns == {
            "id",
            "tenant_id",
            "state",
            "reason_code",
            "policy_version",
            "definition_version",
            "deadline_at",
            "version",
            "actor_ref",
            "resolution_ref",
            "created_at",
            "updated_at",
            "charge_ref",
            "billing_account_id",
            "service_ref",
            "obligation_id",
            "document_id",
            "credit_draw_id",
            "funding_settlement_id",
        }
        for word in ("amount", "currency", "balance", "total", "timer", "retry"):
            assert not [c for c in columns if word in c]
        constraints = set(
            connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'mod_mflows.funded_charge_instances'::regclass"
                )
            )
        )
        assert {
            "funded_charge_instances_pkey",
            "uq_funded_charge_instances_charge",
            "uq_funded_charge_instances_tenant_id_id",
        } <= constraints
        indexes = set(
            connection.scalars(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'mod_mflows' "
                    "AND tablename = 'funded_charge_instances'"
                )
            )
        )
        assert {
            "ix_funded_charge_instances_account_order",
            "ix_funded_charge_instances_service",
            "ix_funded_charge_instances_state_order",
        } <= indexes
    engine.dispose()


def test_rows_are_tenant_isolated_and_cross_tenant_writes_refused(migrated) -> None:
    admin_url, app_url = migrated
    first, second = _tenant(admin_url), _tenant(admin_url)
    app = create_engine(app_url)
    try:
        _make(app, first, "pending")
        with app.begin() as connection:
            _as_tenant(connection, second)
            assert connection.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 0  # noqa: S608
        with pytest.raises(DBAPIError), app.begin() as connection:
            _as_tenant(connection, second)
            _insert(connection, first, "pending")
    finally:
        app.dispose()


def test_app_user_cannot_delete(migrated) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        _make(app, tenant, "pending")
        with pytest.raises(ProgrammingError), app.begin() as connection:
            _as_tenant(connection, tenant)
            connection.execute(text(f"DELETE FROM {TABLE}"))  # noqa: S608
    finally:
        app.dispose()


@pytest.mark.parametrize("state", sorted(_INSERT_STATES))
def test_each_insertable_state_shape_is_accepted(migrated, state) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        _make(app, tenant, state)
        # A quote deposit may have no service reference.
        with app.begin() as connection:
            _as_tenant(connection, tenant)
            _insert(connection, tenant, state, service=None, **_INSERT_STATES[state])
    finally:
        app.dispose()


@pytest.mark.parametrize(
    "state, overrides",
    [
        ("cancelled", {}),  # unknown state
        ("waiting_funds", {}),  # rev 1 vocabulary is gone
        ("pending", {"reason": "x"}),  # reason only for attention or withdrawn
        ("discharged", {**REFS, "reason": "x"}),
        ("attention_required", {}),  # attention needs a reason
        ("withdrawn", {}),  # withdrawn needs a reason
        ("pending", {"deadline": "2030-01-01T00:00:00+00"}),  # no deadline, ever
        ("pending", {"resolution": "ref"}),  # no resolution, ever
        ("discharged", {}),  # discharged needs all three refs
        (
            "discharged",
            {"obligation": REFS["obligation"], "document": REFS["document"]},
        ),
        ("discharged", {"obligation": REFS["obligation"], "draw": REFS["draw"]}),
        ("discharged", {"document": REFS["document"], "draw": REFS["draw"]}),
        ("pending", {"obligation": REFS["obligation"]}),  # refs only if discharged
        ("pending", {"document": REFS["document"]}),
        ("pending", {"draw": REFS["draw"]}),
        ("attention_required", {"reason": "x", **REFS}),
        ("pending", {"version": -1}),
    ],
)
def test_check_constraints_refuse_invalid_insert_shapes(
    migrated, state, overrides
) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        with pytest.raises(IntegrityError), app.begin() as connection:
            _as_tenant(connection, tenant)
            _insert(connection, tenant, state, **overrides)
    finally:
        app.dispose()


@pytest.mark.parametrize(
    "state, overrides, message",
    [
        ("withdrawn", {"reason": "charge_withdrawn"}, "may only"),
        ("pending", {"version": 1}, "version 0"),
        ("discharged", {**REFS, "version": 2}, "version 0"),
        ("attention_required", {"reason": "x", "version": 3}, "version 0"),
    ],
)
def test_insert_is_restricted_to_three_states_at_version_zero(
    migrated, state, overrides, message
) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        with pytest.raises(DBAPIError, match=message), app.begin() as connection:
            _as_tenant(connection, tenant)
            _insert(connection, tenant, state, **overrides)
    finally:
        app.dispose()


def test_one_instance_per_charge_ref_per_tenant(migrated) -> None:
    admin_url, app_url = migrated
    tenant, other = _tenant(admin_url), _tenant(admin_url)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            _as_tenant(connection, tenant)
            _insert(connection, tenant, "pending", charge="same")
        with pytest.raises(IntegrityError, match="uq_funded_charge_instances_charge"):
            with app.begin() as connection:
                _as_tenant(connection, tenant)
                _insert(connection, tenant, "pending", charge="same")
        # Another tenant may carry the same charge reference.
        with app.begin() as connection:
            _as_tenant(connection, other)
            _insert(connection, other, "pending", charge="same")
    finally:
        app.dispose()


_DISCHARGE = (
    "state = 'discharged', obligation_id = :o, document_id = :d, "
    "credit_draw_id = :c, version = version + 1"
)
_DISCHARGE_PARAMS = {
    "o": REFS["obligation"],
    "d": REFS["document"],
    "c": REFS["draw"],
}


@pytest.mark.parametrize(
    "source, assignments, params, expected",
    [
        ("pending", _DISCHARGE, _DISCHARGE_PARAMS, "discharged"),
        (
            "pending",
            "state = 'discharged', obligation_id = :o, document_id = :d, "
            "credit_draw_id = :c, funding_settlement_id = :f, version = version + 1",
            {**_DISCHARGE_PARAMS, "f": FUNDING},
            "discharged",
        ),
        (
            "pending",
            "state = 'attention_required', reason_code = 'tax_evidence_refused', "
            "funding_settlement_id = :f, version = version + 1",
            {"f": FUNDING},
            "attention_required",
        ),
        (
            "pending",
            "state = 'withdrawn', reason_code = 'charge_withdrawn', "
            "version = version + 1",
            {},
            "withdrawn",
        ),
        (
            "attention_required",
            "reason_code = 'billing_conflict', version = version + 1",
            {},
            "attention_required",
        ),
        (
            "attention_required",
            "state = 'pending', reason_code = NULL, actor_ref = 'staff-1', "
            "version = version + 1",
            {},
            "pending",
        ),
        (
            "attention_required",
            _DISCHARGE.replace(
                "state = 'discharged', ", "state = 'discharged', reason_code = NULL, "
            ),
            _DISCHARGE_PARAMS,
            "discharged",
        ),
        (
            "attention_required",
            "state = 'withdrawn', reason_code = 'contract_type_changed', "
            "version = version + 1",
            {},
            "withdrawn",
        ),
    ],
)
def test_every_allowed_transition_edge_succeeds(
    migrated, source, assignments, params, expected
) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        row = _make(app, tenant, source)
        _update(app, tenant, row, assignments, **params)
        after = _row(app, tenant, row)
        assert after["state"] == expected
        assert after["version"] == 1
    finally:
        app.dispose()


@pytest.mark.parametrize(
    "source, assignments, message",
    [
        # pending never loops or goes backwards
        ("pending", "state = 'pending', version = version + 1", "not allowed"),
        ("pending", "actor_ref = 'x', version = version + 1", "not allowed"),
        # version must advance by exactly one
        (
            "pending",
            "state = 'withdrawn', reason_code = 'r', version = version + 2",
            "version must",
        ),
        ("attention_required", "reason_code = 'again'", "version must"),
        (
            "attention_required",
            "reason_code = 'again', version = version - 1",
            "version must",
        ),
        (
            "attention_required",
            "reason_code = 'again', version = version",
            "version must",
        ),
    ],
)
def test_refused_transition_edges(migrated, source, assignments, message) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        row = _make(app, tenant, source)
        with pytest.raises(DBAPIError, match=message):
            _update(app, tenant, row, assignments)
        assert _row(app, tenant, row)["version"] == 0
    finally:
        app.dispose()


def test_transitions_still_obey_the_check_constraints(migrated) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        pending = _make(app, tenant, "pending")
        # discharge without its references
        with pytest.raises(IntegrityError):
            _update(app, tenant, pending, "state = 'discharged', version = version + 1")
        # attention without a reason
        with pytest.raises(IntegrityError):
            _update(
                app,
                tenant,
                pending,
                "state = 'attention_required', version = version + 1",
            )
        # a deadline or resolution is never recorded
        with pytest.raises(IntegrityError):
            _update(
                app,
                tenant,
                pending,
                "state = 'withdrawn', reason_code = 'r', "
                "deadline_at = now(), version = version + 1",
            )
        with pytest.raises(IntegrityError):
            _update(
                app,
                tenant,
                pending,
                "state = 'withdrawn', reason_code = 'r', "
                "resolution_ref = 'x', version = version + 1",
            )
        # back to pending must clear the reason
        attention = _make(app, tenant, "attention_required")
        with pytest.raises(IntegrityError):
            _update(app, tenant, attention, "state = 'pending', version = version + 1")
        assert _row(app, tenant, pending)["version"] == 0
    finally:
        app.dispose()


def test_trigger_refuses_identity_changes_and_terminal_exits(migrated) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        pending = _make(app, tenant, "pending")
        discharged = _make(app, tenant, "discharged")
        withdrawn = _make(app, tenant, "pending")
        _update(
            app,
            tenant,
            withdrawn,
            "state = 'withdrawn', reason_code = 'charge_withdrawn', "
            "version = version + 1",
        )

        for column, value in (
            ("charge_ref", "other-charge"),
            ("billing_account_id", uuid.uuid4()),
            ("service_ref", "service-2"),
            ("tenant_id", uuid.uuid4()),
            ("id", uuid.uuid4()),
            ("created_at", "2001-01-01T00:00:00+00"),
        ):
            with pytest.raises(DBAPIError, match="identity is immutable"):
                _update(app, tenant, pending, f"{column} = :v", v=value)
        with pytest.raises(DBAPIError, match="identity is immutable"):
            _update(app, tenant, pending, "service_ref = NULL")

        # Terminal rows refuse every update: nothing leaves discharged or withdrawn.
        for row in (discharged, withdrawn):
            for assignment in (
                "state = 'pending', reason_code = NULL, version = version + 1",
                "state = 'attention_required', reason_code = 'x', "
                "version = version + 1",
                "state = 'withdrawn', reason_code = 'x', version = version + 1",
                "version = version + 1",
                "actor_ref = 'x'",
                "funding_settlement_id = gen_random_uuid()",
            ):
                with pytest.raises(DBAPIError, match="terminal state"):
                    _update(app, tenant, row, assignment)

        # A non-terminal row may move, and the database clock stamps updated_at.
        with app.begin() as connection:
            _as_tenant(connection, tenant)
            stamp_before = connection.scalar(
                text(f"SELECT updated_at FROM {TABLE} WHERE id = :id"),  # noqa: S608
                {"id": pending},
            )
        _update(app, tenant, pending, _DISCHARGE, **_DISCHARGE_PARAMS)
        with app.begin() as connection:
            _as_tenant(connection, tenant)
            assert (
                connection.scalar(
                    text(f"SELECT updated_at FROM {TABLE} WHERE id = :id"),  # noqa: S608
                    {"id": pending},
                )
                >= stamp_before
            )
        with pytest.raises(DBAPIError, match="terminal state"):
            _update(app, tenant, pending, "actor_ref = 'x'")
    finally:
        app.dispose()


def test_recorded_discharge_references_are_never_changed(migrated) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        row = _make(app, tenant, "discharged")
        for assignment in (
            "obligation_id = gen_random_uuid()",
            "document_id = gen_random_uuid()",
            "credit_draw_id = gen_random_uuid()",
            "obligation_id = NULL, version = version + 1",
        ):
            with pytest.raises(DBAPIError, match="never changed"):
                _update(app, tenant, row, assignment)
        assert _row(app, tenant, row)["obligation_id"] == REFS["obligation"]
    finally:
        app.dispose()


def test_funding_settlement_follows_the_latest_attempt(migrated) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        row = _make(app, tenant, "pending")
        _update(
            app,
            tenant,
            row,
            "state = 'attention_required', reason_code = 'x', "
            "funding_settlement_id = :f, version = version + 1",
            f=FUNDING,
        )
        _update(
            app,
            tenant,
            row,
            "reason_code = 'y', funding_settlement_id = :f, version = version + 1",
            f=uuid.UUID(int=15),
        )
        assert _row(app, tenant, row)["funding_settlement_id"] == uuid.UUID(int=15)
    finally:
        app.dispose()


def test_mf_0001_is_unchanged(migrated) -> None:
    path = VERSIONS / "mf_0001_settle_instances.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MF_0001_SHA256
    engine = create_engine(migrated[0])
    with engine.begin() as connection:
        assert connection.scalar(
            text("SELECT to_regclass('mod_mflows.settle_instances')")
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid = "
                    "'mod_mflows.settle_instances'::regclass AND NOT tgisinternal"
                )
            )
            == 1
        )
    engine.dispose()


def test_downgrade_refuses_while_rows_exist_and_reupgrades_when_empty(migrated) -> None:
    from alembic import command
    from alembic.config import Config

    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    _make(app, tenant, "pending")
    app.dispose()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
    with pytest.raises(RuntimeError, match="instance rows exist"):
        command.downgrade(cfg, "mf_0001_settle_instances")
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        assert connection.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 1  # noqa: S608
        connection.execute(text("SET LOCAL row_security = off"))
        connection.execute(text(f"DELETE FROM {TABLE}"))  # noqa: S608
    command.downgrade(cfg, "mf_0001_settle_instances")
    with engine.begin() as connection:
        assert (
            connection.scalar(
                text("SELECT to_regclass('mod_mflows.funded_charge_instances')")
            )
            is None
        )
        # mf_0001 and its table are untouched by the mf_0002 downgrade.
        assert connection.scalar(
            text("SELECT to_regclass('mod_mflows.settle_instances')")
        )
    command.upgrade(cfg, "money_flows@head")
    engine.dispose()
