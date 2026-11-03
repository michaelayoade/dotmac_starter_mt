"""PostgreSQL proofs for refund instruction and cash outcome observation.

Migrated database, real roles, real row locks. The service serializes on the
owning intent row and the insert trigger repeats the bound, so each guard has a
test that fails if only the other one is present.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money, currency
from dotmac_payments.contracts import (
    AttentionReason,
    CashOutcomeKind,
    CashOutcomeState,
    GetRefundableCashV1,
    InstructRefund,
    ObserveCashOutcome,
    RefundExceedsConfirmedCash,
)
from dotmac_payments.models import PaymentCashOutcome, RefundInstruction
from dotmac_payments.refunds import (
    derive_refund_provider_reference,
    get_refundable_cash_v1,
    instruct_refund,
    observe_cash_outcome,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from tests.test_payments_isolation import (
    KERNEL,
    ROOT,
    VERSIONS,
    _confirmed_evidence,
    _tenant_session,
)
from tests.test_payments_isolation import (
    migrated as payments_migrated,
)


@pytest.fixture
def migrated(request: pytest.FixtureRequest) -> Iterator[tuple[str, str]]:
    # Authoritative money proofs: a missing PostgreSQL target is a failure, not
    # a skip. Reuses the established migration and cleanup owner.
    if not (os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")):
        pytest.fail("refund proofs require a migrated PostgreSQL target")
    yield from payments_migrated.__wrapped__(request)


NGN = currency("NGN")
CASH = Decimal("15000.00")  # what `_confirmed_evidence` confirms
NOW = datetime(2026, 11, 3, tzinfo=UTC)


def _config():
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
    return cfg


def _refund_command(confirmation_id, amount, request_id=None):
    return InstructRefund(
        request_id=request_id or uuid.uuid4(),
        confirmation_id=confirmation_id,
        amount=Money.of(Decimal(amount), NGN),
    )


def _outcome_command(confirmation_id, kind, amount, **kwargs):
    if kind is CashOutcomeKind.ADMIN_CORRECTION:
        kwargs.setdefault("correction_id", uuid.uuid4())
    else:
        kwargs.setdefault("provider_type", "paystack")
        kwargs.setdefault("outcome_reference", f"out-{uuid.uuid4()}")
    return ObserveCashOutcome(
        confirmation_id=confirmation_id,
        kind=kind,
        amount=Money.of(Decimal(amount), NGN),
        **kwargs,
    )


def _race(app_url, scope, operations):
    """Run each operation in its own tenant session after a common barrier."""
    barrier = threading.Barrier(len(operations))
    results: list[object] = []
    errors: list[BaseException] = []

    def runner(operation):
        try:
            with _tenant_session(app_url, scope.tenant_id) as session:
                barrier.wait(timeout=20)
                value = operation(session)
                session.commit()
                results.append(value)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=runner, args=(op,)) for op in operations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)
        assert not thread.is_alive(), "refund lock order deadlocked"
    return results, errors


def _instruct(scope, confirmation_id, amount, request_id=None):
    command = _refund_command(confirmation_id, amount, request_id)

    def operation(session):
        return instruct_refund(session, scope=scope, command=command).id

    return operation


def _held(app_url, scope, confirmation_id) -> Decimal:
    with _tenant_session(app_url, scope.tenant_id) as session:
        return get_refundable_cash_v1(
            session, scope=scope, query=GetRefundableCashV1(confirmation_id)
        ).committed.amount


# ----------------------------------------------------- over-refund, concurrent


@pytest.mark.parametrize(
    ("amount", "threads", "winners"), [("8000.00", 4, 1), ("5000.00", 5, 3)]
)
def test_concurrent_refunds_never_exceed_confirmed_cash(
    migrated,
    amount,
    threads,
    winners,
):
    scope, _, confirmation_id = _confirmed_evidence(migrated)
    results, errors = _race(
        migrated[1],
        scope,
        [_instruct(scope, confirmation_id, amount) for _ in range(threads)],
    )
    assert len(results) == winners
    assert len(errors) == threads - winners
    # Refused by the service's own bound, not by a driver or trigger accident.
    assert all(
        isinstance(error, RefundExceedsConfirmedCash) for error in errors
    ), errors
    assert _held(migrated[1], scope, confirmation_id) == Decimal(amount) * winners
    assert _held(migrated[1], scope, confirmation_id) <= CASH


def test_concurrent_replays_of_one_request_create_one_instruction(migrated):
    scope, _, confirmation_id = _confirmed_evidence(migrated)
    request = uuid.uuid4()
    results, errors = _race(
        migrated[1],
        scope,
        [_instruct(scope, confirmation_id, "9000.00", request) for _ in range(5)],
    )
    assert not errors, errors
    assert len(set(results)) == 1
    assert _held(migrated[1], scope, confirmation_id) == Decimal("9000.00")


def test_a_failed_refund_releases_cash_across_transactions(migrated):
    scope, _, confirmation_id = _confirmed_evidence(migrated)
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        instruction = instruct_refund(
            session, scope=scope, command=_refund_command(confirmation_id, "15000.00")
        )
        reference = instruction.provider_reference
        session.commit()
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        with pytest.raises(RefundExceedsConfirmedCash):
            instruct_refund(
                session, scope=scope, command=_refund_command(confirmation_id, "1.00")
            )
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        row = observe_cash_outcome(
            session,
            scope=scope,
            command=_outcome_command(
                confirmation_id,
                CashOutcomeKind.REFUND_FAILED,
                "15000.00",
                refund_reference=reference,
            ),
        )
        assert row.state is CashOutcomeState.OBSERVED
        session.commit()
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        instruct_refund(
            session, scope=scope, command=_refund_command(confirmation_id, "15000.00")
        )
        session.commit()


def test_failure_observation_racing_a_new_refund_never_oversubscribes(migrated):
    scope, _, confirmation_id = _confirmed_evidence(migrated)
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        reference = instruct_refund(
            session, scope=scope, command=_refund_command(confirmation_id, "15000.00")
        ).provider_reference
        session.commit()

    def fail(session):
        return observe_cash_outcome(
            session,
            scope=scope,
            command=_outcome_command(
                confirmation_id,
                CashOutcomeKind.REFUND_FAILED,
                "15000.00",
                refund_reference=reference,
            ),
        ).id

    results, errors = _race(
        migrated[1],
        scope,
        [fail, _instruct(scope, confirmation_id, "15000.00")],
    )
    assert all(isinstance(e, RefundExceedsConfirmedCash) for e in errors), errors
    assert _held(migrated[1], scope, confirmation_id) <= CASH


# ------------------------------------------------- outcomes: races and parking


def test_competing_terminal_outcomes_apply_exactly_one(migrated):
    scope, _, confirmation_id = _confirmed_evidence(migrated)
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        reference = instruct_refund(
            session, scope=scope, command=_refund_command(confirmation_id, "6000.00")
        ).provider_reference
        session.commit()

    def observer(kind):
        def operation(session):
            row = observe_cash_outcome(
                session,
                scope=scope,
                command=_outcome_command(
                    confirmation_id, kind, "6000.00", refund_reference=reference
                ),
            )
            return row.state, row.attention_reason

        return operation

    results, errors = _race(
        migrated[1],
        scope,
        [
            observer(CashOutcomeKind.REFUND_COMPLETED),
            observer(CashOutcomeKind.REFUND_FAILED),
        ],
    )
    assert not errors, errors
    states = sorted(state.value for state, _ in results)
    assert states == ["ATTENTION_REQUIRED", "OBSERVED"]
    assert AttentionReason.TERMINAL_CONFLICT in {reason for _, reason in results}
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        applied = session.scalar(
            select(func.count())
            .select_from(PaymentCashOutcome)
            .where(PaymentCashOutcome.state == CashOutcomeState.OBSERVED)
        )
        assert applied == 1


def test_concurrent_replays_of_an_unmatched_outcome_park_one_row(migrated):
    scope, _, _ = _confirmed_evidence(migrated)
    command = _outcome_command(
        uuid.uuid4(), CashOutcomeKind.CHARGEBACK, "10.00", outcome_reference="dup"
    )

    def operation(session):
        return observe_cash_outcome(session, scope=scope, command=command).id

    results, errors = _race(migrated[1], scope, [operation] * 5)
    assert not errors, errors
    assert len(set(results)) == 1
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        row = session.scalars(select(PaymentCashOutcome)).one()
        assert row.state is CashOutcomeState.ATTENTION_REQUIRED
        assert row.attention_reason is AttentionReason.UNKNOWN_CONFIRMATION


def test_parked_outcome_replay_is_stable_after_the_cash_is_later_confirmed(migrated):
    scope, _, confirmation_id = _confirmed_evidence(migrated)
    ghost = uuid.uuid4()
    command = _outcome_command(ghost, CashOutcomeKind.BOUNCE, "5.00")
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        first = observe_cash_outcome(session, scope=scope, command=command).id
        session.commit()
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        again = observe_cash_outcome(session, scope=scope, command=command)
        assert again.id == first
        assert again.state is CashOutcomeState.ATTENTION_REQUIRED
    assert confirmation_id != ghost


# ---------------------------------------------- the database repeats the bound


def _insert_instruction(connection, scope, confirmation_id, intent_id, /, **overrides):
    request_id = overrides.pop("request_id", uuid.uuid4())
    values = {
        "id": uuid.uuid4(),
        "tenant_id": scope.tenant_id,
        "request_id": request_id,
        "confirmation_id": confirmation_id,
        "intent_id": intent_id,
        "provider_type": "PAYSTACK",
        "provider_reference": derive_refund_provider_reference(scope, request_id),
        "currency_code": "NGN",
        "minor_units": 2,
        "amount": Decimal("100.00"),
        "instructed_at": NOW,
    }
    values.update(overrides)
    connection.execute(
        text(
            "INSERT INTO mod_payments.refund_instructions "
            "(id, tenant_id, request_id, confirmation_id, intent_id, provider_type,"
            " provider_reference, currency_code, minor_units, amount, instructed_at) "
            "VALUES (:id, :tenant_id, :request_id, :confirmation_id, :intent_id,"
            " :provider_type, :provider_reference, :currency_code, :minor_units,"
            " :amount, :instructed_at)"
        ),
        values,
    )


def _app_connection(app_url, tenant_id):
    engine = create_engine(app_url)
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(
        text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )
    return engine, connection, transaction


def _confirmed_ids(migrated):
    scope, intent_id, confirmation_id = _confirmed_evidence(migrated)
    return scope, intent_id, confirmation_id


@pytest.mark.parametrize(
    "case",
    ["over", "wrong_reference", "wrong_provider", "wrong_currency", "wrong_intent"],
)
def test_the_insert_trigger_refuses_a_writer_that_bypasses_the_service(migrated, case):
    scope, intent_id, confirmation_id = _confirmed_ids(migrated)
    overrides = {
        "over": {"amount": Decimal("15000.01")},
        "wrong_reference": {"provider_reference": "rf1-" + "a" * 32},
        "wrong_provider": {"provider_type": "OTHER"},
        "wrong_currency": {"currency_code": "USD"},
        "wrong_intent": {"intent_id": uuid.uuid4()},
    }[case]
    engine, connection, transaction = _app_connection(migrated[1], scope.tenant_id)
    try:
        with pytest.raises(DBAPIError) as refused, connection.begin_nested():
            _insert_instruction(
                connection, scope, confirmation_id, intent_id, **overrides
            )
        sqlstate = getattr(refused.value.orig, "sqlstate", None)
        assert sqlstate in {"23001", "23503"}, sqlstate
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_trigger_counts_prior_instructions_for_a_bypassing_writer(migrated):
    scope, intent_id, confirmation_id = _confirmed_ids(migrated)
    engine, connection, transaction = _app_connection(migrated[1], scope.tenant_id)
    try:
        _insert_instruction(
            connection, scope, confirmation_id, intent_id, amount=Decimal("10000.00")
        )
        with pytest.raises(DBAPIError) as refused, connection.begin_nested():
            _insert_instruction(
                connection,
                scope,
                confirmation_id,
                intent_id,
                amount=Decimal("5000.01"),
            )
        assert refused.value.orig.sqlstate == "23001"
        _insert_instruction(
            connection, scope, confirmation_id, intent_id, amount=Decimal("5000.00")
        )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_sensitivity_without_the_trigger_the_bypass_succeeds(migrated):
    """Proves the refusals above come from the trigger, not from an accident."""
    scope, intent_id, confirmation_id = _confirmed_ids(migrated)
    admin = create_engine(migrated[0])
    try:
        with admin.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE mod_payments.refund_instructions "
                    "DISABLE TRIGGER refund_instruction_insert"
                )
            )
        engine, connection, transaction = _app_connection(migrated[1], scope.tenant_id)
        try:
            _insert_instruction(
                connection,
                scope,
                confirmation_id,
                intent_id,
                amount=Decimal("15000.01"),
            )
        finally:
            transaction.rollback()
            connection.close()
            engine.dispose()
    finally:
        admin.dispose()


# ----------------------------------------------------- append-only, RLS, catalog


def _seed_rows(migrated):
    scope, intent_id, confirmation_id = _confirmed_ids(migrated)
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        instruction = instruct_refund(
            session, scope=scope, command=_refund_command(confirmation_id, "100.00")
        )
        outcome = observe_cash_outcome(
            session,
            scope=scope,
            command=_outcome_command(confirmation_id, CashOutcomeKind.BOUNCE, "1.00"),
        )
        ids = (instruction.id, outcome.id)
        session.commit()
    return scope, ids


@pytest.mark.parametrize("actor", ["app_user", "owner"])
@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
@pytest.mark.parametrize("table", ["refund_instructions", "payment_cash_outcomes"])
def test_instructions_and_outcomes_are_append_only_in_the_database(
    migrated,
    actor,
    operation,
    table,
):
    scope, _ = _seed_rows(migrated)
    statement = {
        "update": f"UPDATE mod_payments.{table} SET amount = 1",  # noqa: S608
        "delete": f"DELETE FROM mod_payments.{table}",  # noqa: S608
        "truncate": f"TRUNCATE mod_payments.{table}",
    }[operation]
    engine = create_engine(migrated[1 if actor == "app_user" else 0])
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(scope.tenant_id)},
            )
            with pytest.raises(DBAPIError) as refused, connection.begin_nested():
                connection.execute(text(statement))
            sqlstate = refused.value.orig.sqlstate
            if actor == "app_user":
                assert sqlstate == "42501"
            elif table == "refund_instructions" and operation == "truncate":
                # The owner is stopped even earlier: the table is FK-referenced.
                assert sqlstate in {"0A000", "23001"}
            else:
                assert sqlstate == "23001"
            count = text(f"SELECT count(*) FROM mod_payments.{table}")  # noqa: S608
            assert connection.scalar(count) == 1
    finally:
        engine.dispose()


def test_refund_rows_are_tenant_isolated(migrated):
    scope, _ = _seed_rows(migrated)
    other = TenantScope(uuid.uuid4())
    admin = create_engine(migrated[0])
    with admin.begin() as connection:
        connection.execute(
            text("INSERT INTO public.tenants (id,slug,name) VALUES (:i,:s,:s)"),
            {"i": other.tenant_id, "s": f"other:{other.tenant_id}"},
        )
    admin.dispose()
    with _tenant_session(migrated[1], other.tenant_id) as session:
        for model in (RefundInstruction, PaymentCashOutcome):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        for model in (RefundInstruction, PaymentCashOutcome):
            assert session.scalar(select(func.count()).select_from(model)) == 1


def test_catalog_has_forced_rls_minimal_grants_and_no_fee_or_mandate_column(migrated):
    engine = create_engine(migrated[0])
    try:
        with engine.connect() as connection:
            for table in ("refund_instructions", "payment_cash_outcomes"):
                forced = connection.scalar(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                        "WHERE oid = to_regclass(:name)"
                    ),
                    {"name": f"mod_payments.{table}"},
                )
                assert forced
                granted = {
                    privilege
                    for privilege in (
                        "SELECT",
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "TRUNCATE",
                    )
                    if connection.scalar(
                        text("SELECT has_table_privilege('app_user', :t, :p)"),
                        {"t": f"mod_payments.{table}", "p": privilege},
                    )
                }
                assert granted == {"SELECT", "INSERT"}
            columns = set(
                connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'mod_payments' AND table_name "
                        "IN ('refund_instructions', 'payment_cash_outcomes')"
                    )
                )
            )
            assert not {c for c in columns if "fee" in c or "mandate" in c}
    finally:
        engine.dispose()


# ----------------------------------------------------------------- migration


def test_empty_downgrade_and_reupgrade(migrated):
    from alembic import command

    cfg = _config()
    command.downgrade(cfg, "pm_0006_cash_qualification")
    engine = create_engine(migrated[0])
    with engine.begin() as connection:
        assert (
            connection.scalar(
                text("SELECT to_regclass('mod_payments.refund_instructions')")
            )
            is None
        )
        assert (
            connection.scalar(
                text(
                    "SELECT to_regprocedure("
                    "'mod_payments.check_refund_instruction_insert()')"
                )
            )
            is None
        )
    command.upgrade(cfg, "pm_0007_refund_outcomes")
    with engine.begin() as connection:
        assert connection.scalar(
            text("SELECT to_regclass('mod_payments.payment_cash_outcomes')")
        )
    engine.dispose()


def test_downgrade_refuses_while_refund_history_exists(migrated):
    from alembic import command

    _seed_rows(migrated)
    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(_config(), "pm_0006_cash_qualification")


@pytest.mark.parametrize("migrated", ["pm_0006_cash_qualification"], indirect=True)
def test_upgrade_preserves_a_populated_predecessor_and_can_refund_its_cash(migrated):
    from alembic import command

    scope, _, confirmation_id = _confirmed_evidence(migrated)
    command.upgrade(_config(), "heads")
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        row = instruct_refund(
            session, scope=scope, command=_refund_command(confirmation_id, "15000.00")
        )
        assert row.provider_reference == derive_refund_provider_reference(
            scope, row.request_id
        )
        session.commit()
