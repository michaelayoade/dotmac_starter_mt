"""PostgreSQL RLS canary for payment intents."""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money, currency
from dotmac_payments.contracts import (
    ConfirmationSource,
    Conflict,
    OpenPaymentIntent,
    PaymentIntentStatus,
    PaymentPurpose,
    RecordConfirmation,
    ReviewTransferProof,
    SubmitTransferProof,
    TransferProofState,
)
from dotmac_payments.models import (
    PaymentConfirmation,
    PaymentIntent,
    PaymentTransferProof,
)
from dotmac_payments.service import (
    cancel_payment_intent,
    expire_payment_intent,
    open_payment_intent,
    record_confirmation,
    review_transfer_proof,
    submit_transfer_proof,
)
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
VERSIONS = ROOT / "packages/dotmac-payments/src/dotmac_payments/migrations/versions"
TABLES = ("payment_intents", "payment_transfer_proofs", "payment_confirmations")


def _url(base: str, database: str, user: str | None = None) -> str:
    prefix, _, _ = base.rpartition("/")
    if user:
        scheme, _, host = prefix.partition("://")
        prefix = f"{scheme}://{user}@{host.rpartition('@')[2]}"
    return f"{prefix}/{database}"


@pytest.fixture
def migrated(request) -> Iterator[tuple[str, str]]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    if not superuser:
        pytest.skip("PostgreSQL URL required")
    name = f"payments_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin = _url(superuser, name, "app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
        os.environ["MIGRATION_DATABASE_URL"] = admin
        command.upgrade(cfg, getattr(request, "param", "heads"))
        yield admin, _url(superuser, name, "app_user")
    finally:
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def test_all_payments_rows_are_cross_tenant_isolated(
    migrated: tuple[str, str],
) -> None:
    admin_url, app_url = migrated
    tenants = (uuid.uuid4(), uuid.uuid4())
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        for index, tenant in enumerate(tenants):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :slug)"
                ),
                {"id": tenant, "slug": f"pm-{index}"},
            )
            intent = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO mod_payments.payment_intents "
                    "(id, tenant_id, reference, payer_reference, purpose, "
                    "provider_type, channel, currency_code, requested_amount, "
                    "status, opened_at) VALUES (:id, :tenant, :reference, :payer, "
                    "'INVOICE_SETTLEMENT', 'PAYSTACK', 'WEB', 'NGN', 15000, "
                    "'PENDING', now())"
                ),
                {
                    "id": intent,
                    "tenant": tenant,
                    "reference": f"ref-{index}",
                    "payer": f"customer-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_payments.payment_transfer_proofs "
                    "(id, tenant_id, intent_id, submitted_reference, "
                    "document_reference, currency_code, declared_amount, state, "
                    "declared_at) VALUES (:id, :tenant, :intent, :submitted, "
                    "'file:1', 'NGN', 15000, 'SUBMITTED', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "intent": intent,
                    "submitted": f"bank-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_payments.payment_confirmations "
                    "(id, tenant_id, intent_id, source, provider_type, "
                    "external_reference, currency_code, confirmed_amount, "
                    "observed_at) VALUES (:id, :tenant, :intent, "
                    "'PROVIDER_CALLBACK', 'PAYSTACK', :external, 'NGN', 15000, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "intent": intent,
                    "external": f"ps-{index}",
                },
            )
    admin.dispose()
    app = create_engine(app_url)
    try:
        for tenant in tenants:
            with app.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant)},
                )
                for table in TABLES:
                    query = f"SELECT tenant_id FROM mod_payments.{table}"  # noqa: S608
                    assert set(connection.execute(text(query)).scalars()) == {tenant}
    finally:
        app.dispose()


@contextmanager
def _tenant_session(app_url, tenant_id):
    engine = create_engine(app_url)

    @event.listens_for(engine, "begin")
    def tenant_context(connection):
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )

    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _seed_pending(migrated, *, with_proof=False):
    admin_url, app_url = migrated
    tenant_id = uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        connection.execute(
            text("INSERT INTO public.tenants (id,slug,name) VALUES (:id,:slug,:slug)"),
            {"id": tenant_id, "slug": f"payments:{tenant_id}"},
        )
    admin.dispose()
    now = datetime(2026, 10, 13, tzinfo=UTC)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        intent = open_payment_intent(
            session,
            scope=scope,
            command=OpenPaymentIntent(
                payer_reference="customer:race",
                purpose=PaymentPurpose.INVOICE_SETTLEMENT,
                requested=Money.of("15000", currency("NGN")),
                reference="race-intent",
                provider_type="paystack",
                channel="web",
                opened_at=now,
                expires_at=now + timedelta(hours=1),
            ),
        )
        proof_id = None
        if with_proof:
            proof = submit_transfer_proof(
                session,
                scope=scope,
                command=SubmitTransferProof(
                    intent_id=intent.id,
                    declared=Money.of("15000", currency("NGN")),
                    document_reference="file:race",
                    submitted_reference="proof-reference",
                    declared_at=now,
                ),
            )
            proof_id = proof.id
        intent_id = intent.id
        session.commit()
    return scope, intent_id, proof_id, now


def test_intent_replay_binds_the_request_in_migrated_tenant_scope(migrated):
    from dotmac_kernel.cache import PlatformScope
    from dotmac_kernel.money import Currency

    scope, _, _, now = _seed_pending(migrated)
    command = OpenPaymentIntent(
        payer_reference="customer:binding",
        target_reference="invoice:binding",
        purpose=PaymentPurpose.INVOICE_SETTLEMENT,
        requested=Money.of("123.456789", Currency("XTS", 6)),
        reference="binding-replay",
        provider_type="paystack",
        channel="web",
        opened_at=now,
        expires_at=now + timedelta(hours=1),
    )
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        first = open_payment_intent(session, scope=scope, command=command)
        intent_id = first.id
        session.commit()
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        replay = open_payment_intent(session, scope=scope, command=command)
        assert replay.id == intent_id
        for changed in (
            replace(command, target_reference="invoice:other"),
            replace(command, target_reference=None),
            replace(command, purpose=PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT),
            replace(command, provider_type="bank"),
            replace(command, channel="transfer"),
            replace(command, payer_reference="customer:other"),
            replace(command, requested=Money.of("123.456788", Currency("XTS", 6))),
            replace(command, requested=Money.of("123.456789", Currency("XXX", 6))),
            replace(command, requested=Money.of("123.456", Currency("XTS", 3))),
            replace(command, opened_at=now + timedelta(seconds=1)),
            replace(command, expires_at=now + timedelta(hours=2)),
            replace(command, expires_at=None),
        ):
            with pytest.raises(Conflict):
                open_payment_intent(session, scope=scope, command=changed)
        assert (
            open_payment_intent(
                session,
                scope=scope,
                command=replace(
                    command, opened_at=None, provider_type=" PAYSTACK ", channel=" WEB "
                ),
            ).id
            == intent_id
        )
        with pytest.raises(TypeError, match="requires TenantScope"):
            open_payment_intent(session, scope=PlatformScope(), command=command)
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaymentIntent)
                .where(PaymentIntent.reference == command.reference)
            )
            == 1
        )
        session.rollback()

    foreign, _, _, _ = _seed_pending(migrated)
    with _tenant_session(migrated[1], foreign.tenant_id) as session:
        assert session.get(PaymentIntent, intent_id) is None
        other = open_payment_intent(
            session,
            scope=foreign,
            command=replace(command, target_reference="invoice:foreign"),
        )
        assert other.id != intent_id
        session.commit()
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        assert (
            open_payment_intent(session, scope=scope, command=command).id == intent_id
        )
        rollback_command = replace(command, reference="binding-rollback")
        pending = open_payment_intent(session, scope=scope, command=rollback_command)
        pending_id = pending.id
        assert (
            open_payment_intent(session, scope=scope, command=rollback_command).id
            == pending_id
        )
        session.rollback()
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        assert session.get(PaymentIntent, pending_id) is None
        assert (
            open_payment_intent(session, scope=scope, command=command).id == intent_id
        )


def _race(app_url, scope, intent_id, operations, *, proof_id=None):
    barrier = threading.Barrier(len(operations))
    results, errors = [], []

    def runner(operation):
        try:
            with _tenant_session(app_url, scope.tenant_id) as session:
                # A previously loaded PENDING ORM object must be refreshed after
                # the row lock wakes behind the winner's commit.
                loaded_intent = session.get(PaymentIntent, intent_id)
                assert loaded_intent is not None
                assert loaded_intent.status is PaymentIntentStatus.PENDING
                loaded_proof = (
                    None
                    if proof_id is None
                    else session.get(PaymentTransferProof, proof_id)
                )
                if proof_id is not None:
                    assert loaded_proof is not None
                    assert loaded_proof.state is TransferProofState.SUBMITTED
                barrier.wait(timeout=20)
                row = operation(session)
                row_id = row.id
                session.commit()
                results.append(row_id)
        except BaseException as error:
            errors.append(error)

    threads = [
        threading.Thread(target=runner, args=(operation,)) for operation in operations
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "payment lifecycle lock order deadlocked"
    return results, errors


@pytest.mark.parametrize("same_reference", [True, False])
def test_confirmation_race_serializes_same_intent_and_exact_replay(
    migrated, same_reference
):
    scope, intent_id, _, now = _seed_pending(migrated)
    references = ("same", "same" if same_reference else "different")
    commands = tuple(
        RecordConfirmation(
            intent_id=intent_id,
            source=ConfirmationSource.PROVIDER_CALLBACK,
            external_reference=reference,
            confirmed=Money.of("15000", currency("NGN")),
            observed_at=now,
        )
        for reference in references
    )
    results, errors = _race(
        migrated[1],
        scope,
        intent_id,
        tuple(
            lambda session, command=command: record_confirmation(
                session, scope=scope, command=command
            )
            for command in commands
        ),
    )
    assert len(results) == (2 if same_reference else 1)
    assert len(set(results)) == 1
    assert len(errors) == (0 if same_reference else 1)
    assert all(isinstance(error, Conflict) for error in errors)
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        assert (
            session.scalar(select(func.count()).select_from(PaymentConfirmation)) == 1
        )
        intent = session.get(PaymentIntent, intent_id)
        assert intent.status is PaymentIntentStatus.CONFIRMED
        assert intent.confirmed_amount == 15000


@pytest.mark.parametrize("terminal", ["cancel", "expire", "proof"])
def test_confirmation_and_terminal_intent_mutations_have_one_winner(migrated, terminal):
    scope, intent_id, proof_id, now = _seed_pending(
        migrated, with_proof=terminal == "proof"
    )
    command = RecordConfirmation(
        intent_id=intent_id,
        source=ConfirmationSource.PROVIDER_CALLBACK,
        external_reference="provider-reference",
        confirmed=Money.of("15000", currency("NGN")),
        observed_at=now,
    )

    def other(session):
        if terminal == "cancel":
            return cancel_payment_intent(
                session, scope=scope, intent_id=intent_id, cancelled_at=now
            )
        if terminal == "expire":
            return expire_payment_intent(
                session, scope=scope, intent_id=intent_id, now=now + timedelta(hours=2)
            )
        return review_transfer_proof(
            session,
            scope=scope,
            command=ReviewTransferProof(
                proof_id=proof_id,
                accept=True,
                reviewer="agent:reviewer",
                reviewed_at=now,
            ),
        )

    results, errors = _race(
        migrated[1],
        scope,
        intent_id,
        (
            lambda session: record_confirmation(session, scope=scope, command=command),
            other,
        ),
        proof_id=proof_id,
    )
    assert len(results) == len(errors) == 1
    assert isinstance(errors[0], Conflict)
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        intent = session.get(PaymentIntent, intent_id)
        confirmations = tuple(session.scalars(select(PaymentConfirmation)))
        assert len(confirmations) == (
            1 if intent.status is PaymentIntentStatus.CONFIRMED else 0
        )
        if terminal == "proof":
            assert len(confirmations) == 1
            proof = session.get(PaymentTransferProof, proof_id)
            if confirmations[0].source is ConfirmationSource.TRANSFER_PROOF:
                assert proof.state is TransferProofState.ACCEPTED
                assert proof.reviewer == "agent:reviewer"
            else:
                assert proof.state is TransferProofState.SUBMITTED
                assert proof.reviewer is None
                assert proof.reviewed_at is None


def test_concurrent_reviews_cannot_accept_and_reject_the_same_proof(migrated):
    scope, intent_id, proof_id, now = _seed_pending(migrated, with_proof=True)
    commands = tuple(
        ReviewTransferProof(
            proof_id=proof_id,
            accept=accept,
            reviewer=f"agent:{accept}",
            reviewed_at=now,
            rejection_reason=None if accept else "unverified",
        )
        for accept in (True, False)
    )
    results, errors = _race(
        migrated[1],
        scope,
        intent_id,
        tuple(
            lambda session, command=command: review_transfer_proof(
                session, scope=scope, command=command
            )
            for command in commands
        ),
        proof_id=proof_id,
    )
    assert len(results) == len(errors) == 1
    assert isinstance(errors[0], Conflict)
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        proof = session.get(PaymentTransferProof, proof_id)
        assert proof.state in (TransferProofState.ACCEPTED, TransferProofState.REJECTED)
        assert proof.reviewer == f"agent:{proof.state is TransferProofState.ACCEPTED}"
        assert session.scalar(
            select(func.count()).select_from(PaymentConfirmation)
        ) == (1 if proof.state is TransferProofState.ACCEPTED else 0)


@pytest.mark.parametrize(
    "code,scale,value",
    [
        ("JPY", 0, "123"),
        ("NGN", 2, "123.45"),
        ("BHD", 3, "123.456"),
        ("XTS", 6, "123.456789"),
    ],
)
def test_exact_confirmed_cash_is_fresh_scoped_and_rollback_owned(
    migrated, code, scale, value
):
    from dotmac_kernel.money import Currency
    from dotmac_payments import (
        ConfirmationNotFound,
        GetConfirmedCashV1,
        get_confirmed_cash_v1,
    )

    scope, _, _, now = _seed_pending(migrated)
    money = Money.of(value, Currency(code, scale))
    app_url = migrated[1]
    with _tenant_session(app_url, scope.tenant_id) as session:
        intent = open_payment_intent(
            session,
            scope=scope,
            command=OpenPaymentIntent(
                payer_reference="precision-payer",
                purpose=PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT,
                requested=money,
                reference="precision",
                provider_type="bank",
                channel="transfer",
                opened_at=now,
            ),
        )
        proof = submit_transfer_proof(
            session,
            scope=scope,
            command=SubmitTransferProof(
                intent_id=intent.id,
                declared=money,
                document_reference="proof",
                submitted_reference="precision-proof",
            ),
        )
        review_transfer_proof(
            session,
            scope=scope,
            command=ReviewTransferProof(
                proof_id=proof.id,
                accept=True,
                reviewer="reviewer",
                reviewed_at=now,
            ),
        )
        confirmation = session.scalar(
            select(PaymentConfirmation).where(
                PaymentConfirmation.intent_id == intent.id
            )
        )
        query = GetConfirmedCashV1(confirmation.id)
        intent_id = intent.id
        session.commit()
    with _tenant_session(app_url, scope.tenant_id) as session:
        observation = get_confirmed_cash_v1(session, scope=scope, query=query)
        assert observation.confirmed == money
        assert observation.intent_id == intent_id
        assert observation.observed_at == now
        stale = session.get(PaymentConfirmation, query.confirmation_id)
        stale.confirmed_amount = Money.of("9", money.currency).amount
        assert get_confirmed_cash_v1(session, scope=scope, query=query) == observation
        # Reads do not flush dirty evidence, and leave transaction authority here.
        session.rollback()
    foreign = TenantScope(uuid.uuid4())
    with _tenant_session(app_url, foreign.tenant_id) as session:
        with pytest.raises(ConfirmationNotFound):
            get_confirmed_cash_v1(session, scope=foreign, query=query)
    with _tenant_session(app_url, scope.tenant_id) as session:
        pending = open_payment_intent(
            session,
            scope=scope,
            command=OpenPaymentIntent(
                payer_reference="rollback-payer",
                purpose=PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT,
                requested=money,
                reference="rollback",
                provider_type="bank",
                channel="transfer",
                opened_at=now,
            ),
        )
        confirmation = record_confirmation(
            session,
            scope=scope,
            command=RecordConfirmation(
                intent_id=pending.id,
                source=ConfirmationSource.MANUAL,
                external_reference="rollback-confirmation",
                confirmed=money,
                observed_at=now,
            ),
        )
        rolled_back = GetConfirmedCashV1(confirmation.id)
        assert (
            get_confirmed_cash_v1(session, scope=scope, query=rolled_back).confirmed
            == money
        )
        session.rollback()
    with _tenant_session(app_url, scope.tenant_id) as session:
        with pytest.raises(ConfirmationNotFound):
            get_confirmed_cash_v1(session, scope=scope, query=rolled_back)


@pytest.mark.parametrize("migrated", ["pm_0001_payment_intents"], indirect=True)
def test_precision_upgrade_preserves_legacy_evidence_and_refuses_guessing(migrated):
    from dotmac_payments import (
        GetConfirmedCashV1,
        UnresolvedMoneyPrecision,
        get_confirmed_cash_v1,
    )

    from alembic import command
    from alembic.config import Config

    admin_url, app_url = migrated
    tenant_id, intent_id, proof_id, confirmation_id = (uuid.uuid4() for _ in range(4))
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO public.tenants (id,slug,name) VALUES (:id,:slug,:slug)"),
            {"id": tenant_id, "slug": f"legacy:{tenant_id}"},
        )
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        connection.execute(
            text("""INSERT INTO mod_payments.payment_intents
            (id,tenant_id,reference,payer_reference,purpose,provider_type,channel,
             currency_code,requested_amount,status,opened_at)
            VALUES (:id,:tenant,'legacy','payer','ACCOUNT_CREDIT_DEPOSIT',
                    'BANK','TRANSFER','BHD',123.456,'PENDING',now())"""),
            {"id": intent_id, "tenant": tenant_id},
        )
        connection.execute(
            text("""INSERT INTO mod_payments.payment_transfer_proofs
            (id,tenant_id,intent_id,submitted_reference,document_reference,
             currency_code,declared_amount,state,declared_at)
            VALUES (:id,:tenant,:intent,'legacy-proof','file:legacy','BHD',
                    123.456,'SUBMITTED',now())"""),
            {"id": proof_id, "tenant": tenant_id, "intent": intent_id},
        )
        connection.execute(
            text("""INSERT INTO mod_payments.payment_confirmations
            (id,tenant_id,intent_id,source,provider_type,external_reference,
             currency_code,confirmed_amount,observed_at)
            VALUES (:id,:tenant,:intent,'MANUAL','BANK','legacy-confirmation',
                    'BHD',123.456,now())"""),
            {"id": confirmation_id, "tenant": tenant_id, "intent": intent_id},
        )
        before = {
            table: connection.execute(
                text(f"SELECT to_jsonb(t) FROM mod_payments.{table} t")  # noqa: S608 — closed TABLES
            ).scalar_one()
            for table in TABLES
        }
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
    command.upgrade(cfg, "pm_0002_money_precision")
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        for table in TABLES:
            after = connection.execute(
                text(f"SELECT to_jsonb(t) FROM mod_payments.{table} t")  # noqa: S608 — closed TABLES
            ).scalar_one()
            assert after.pop("minor_units") is None
            assert after == before[table]
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        with pytest.raises(UnresolvedMoneyPrecision):
            get_confirmed_cash_v1(
                session, scope=scope, query=GetConfirmedCashV1(confirmation_id)
            )
        with pytest.raises(UnresolvedMoneyPrecision):
            review_transfer_proof(
                session,
                scope=scope,
                command=ReviewTransferProof(
                    proof_id=proof_id, accept=True, reviewer="reviewer"
                ),
            )
        proof = session.get(PaymentTransferProof, proof_id)
        assert proof.state is TransferProofState.SUBMITTED
        assert proof.reviewer is None
        cancel_payment_intent(session, scope=scope, intent_id=intent_id)
        session.commit()
    engine.dispose()


def _confirmed_evidence(migrated, *, with_proof=False):
    scope, intent_id, _, now = _seed_pending(migrated, with_proof=with_proof)
    command = RecordConfirmation(
        intent_id=intent_id,
        source=ConfirmationSource.PROVIDER_CALLBACK,
        external_reference="immutable-confirmation",
        confirmed=Money.of("15000", currency("NGN")),
        observed_at=now,
    )
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        confirmation = record_confirmation(session, scope=scope, command=command)
        confirmation_id = confirmation.id
        assert (
            record_confirmation(session, scope=scope, command=command).id
            == confirmation_id
        )
        session.commit()
    return scope, intent_id, confirmation_id


@pytest.mark.parametrize("actor", ["app_user", "owner"])
@pytest.mark.parametrize("operation", ["update", "noop", "delete", "cascade"])
def test_confirmation_database_mutations_are_refused(migrated, actor, operation):
    """Core DML bypasses the ORM listeners, including parent FK cascades."""
    from sqlalchemy import delete, update
    from sqlalchemy.exc import DBAPIError

    scope, intent_id, confirmation_id = _confirmed_evidence(migrated)
    confirmation = PaymentConfirmation.__table__
    statements = {
        "update": update(confirmation).values(confirmed_amount=1),
        "noop": update(confirmation).values(
            confirmed_amount=confirmation.c.confirmed_amount
        ),
        "delete": delete(confirmation),
        "cascade": delete(PaymentIntent.__table__).where(PaymentIntent.id == intent_id),
    }
    engine = create_engine(migrated[1 if actor == "app_user" else 0])
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(scope.tenant_id)},
                )
                with pytest.raises(DBAPIError) as refused, connection.begin_nested():
                    connection.execute(statements[operation])
                sqlstate = getattr(refused.value.orig, "sqlstate", None) or getattr(
                    refused.value.orig, "pgcode", None
                )
                expected = (
                    "23001" if actor == "owner" or operation == "cascade" else "42501"
                )
                assert sqlstate == expected
                assert (
                    connection.scalar(select(confirmation.c.confirmed_amount)) == 15000
                )
                assert connection.scalar(select(confirmation.c.id)) == confirmation_id
                assert connection.scalar(select(PaymentIntent.id)) == intent_id
    finally:
        engine.dispose()


def _payments_config():
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
    return cfg


@pytest.mark.parametrize("migrated", ["pm_0002_money_precision"], indirect=True)
def test_immutability_upgrade_preserves_populated_predecessor(migrated):
    from alembic import command

    scope, _, confirmation_id = _confirmed_evidence(migrated, with_proof=True)
    engine = create_engine(migrated[0])
    with engine.begin() as connection:
        # Include legacy NULL-scale evidence without inferring a repair.
        connection.execute(
            PaymentConfirmation.__table__.update().values(minor_units=None)
        )
        tables = (
            PaymentIntent.__table__,
            PaymentTransferProof.__table__,
            PaymentConfirmation.__table__,
        )
        before = {
            table.name: connection.execute(select(table)).all() for table in tables
        }
    command.upgrade(_payments_config(), "pm_0003_confirmation_immutable")
    with engine.begin() as connection:
        after = {
            table.name: connection.execute(select(table)).all() for table in tables
        }
        assert after == before
        assert after["payment_confirmations"][0]._mapping["minor_units"] is None
    with pytest.raises(RuntimeError, match="confirmation evidence exists"):
        command.downgrade(_payments_config(), "pm_0002_money_precision")
    from dotmac_payments import (
        GetConfirmedCashV1,
        UnresolvedMoneyPrecision,
        get_confirmed_cash_v1,
    )
    from sqlalchemy.exc import DBAPIError

    with _tenant_session(migrated[1], scope.tenant_id) as session:
        assert session.get(PaymentConfirmation, confirmation_id) is not None
        with pytest.raises(UnresolvedMoneyPrecision):
            get_confirmed_cash_v1(
                session, scope=scope, query=GetConfirmedCashV1(confirmation_id)
            )
    # Failed downgrade leaves both privileges and trigger protection installed.
    with engine.begin() as connection:
        with (
            pytest.raises(DBAPIError, match="evidence is immutable"),
            connection.begin_nested(),
        ):
            connection.execute(PaymentConfirmation.__table__.delete())
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege('app_user', "
                "'mod_payments.payment_confirmations', 'UPDATE,DELETE')"
            )
        )
    engine.dispose()


def test_empty_immutability_downgrade_and_reupgrade(migrated):
    from alembic import command

    cfg = _payments_config()
    command.downgrade(cfg, "pm_0002_money_precision")
    engine = create_engine(migrated[0])
    with engine.begin() as connection:
        assert connection.scalar(
            text(
                "SELECT has_table_privilege('app_user', "
                "'mod_payments.payment_confirmations', 'UPDATE,DELETE')"
            )
        )
        assert (
            connection.scalar(
                text(
                    "SELECT to_regprocedure("
                    "'mod_payments.refuse_confirmation_mutation()')"
                )
            )
            is None
        )
    command.upgrade(cfg, "pm_0003_confirmation_immutable")
    with engine.begin() as connection:
        for privilege in ("UPDATE", "DELETE"):
            assert not connection.scalar(
                text(
                    "SELECT has_table_privilege('app_user', "
                    "'mod_payments.payment_confirmations', :privilege)"
                ),
                {"privilege": privilege},
            )
        for privilege in ("SELECT", "INSERT"):
            assert connection.scalar(
                text(
                    "SELECT has_table_privilege('app_user', "
                    "'mod_payments.payment_confirmations', :privilege)"
                ),
                {"privilege": privilege},
            )
    engine.dispose()


@pytest.mark.parametrize(
    "code,scale,value",
    [
        ("JPY", 0, "123"),
        ("NGN", 2, "123.45"),
        ("BHD", 3, "123.456"),
        ("XTS", 6, "123.456789"),
        ("NGN", 3, "123.456"),
    ],
)
def test_intent_reference_lookup_is_fresh_scoped_and_rollback_owned(
    migrated, code, scale, value
):
    from dotmac_kernel.money import Currency
    from dotmac_payments import (
        GetPaymentIntentV1,
        PaymentIntentObservationV1,
        get_payment_intent_v1,
    )

    scope, _, _, now = _seed_pending(migrated)
    foreign, _, _, _ = _seed_pending(migrated)
    money = Money.of(value, Currency(code, scale))
    command = OpenPaymentIntent(
        payer_reference="payer:lookup",
        target_reference="invoice:lookup",
        purpose=PaymentPurpose.INVOICE_SETTLEMENT,
        requested=money,
        reference="lookup-reference",
        provider_type="paystack",
        channel="web",
        opened_at=now,
        expires_at=now + timedelta(hours=1),
    )
    query = GetPaymentIntentV1("  lookup-reference  ")
    app_url = migrated[1]
    with _tenant_session(app_url, scope.tenant_id) as session:
        intent = open_payment_intent(session, scope=scope, command=command)
        intent_id = intent.id
        session.commit()
    with _tenant_session(app_url, foreign.tenant_id) as session:
        assert get_payment_intent_v1(session, scope=foreign, query=query) is None
        other = open_payment_intent(
            session,
            scope=foreign,
            command=replace(
                command,
                payer_reference="payer:foreign",
                target_reference="invoice:foreign",
            ),
        )
        foreign_id = other.id
        session.commit()
    with _tenant_session(app_url, scope.tenant_id) as session:
        expected = PaymentIntentObservationV1(
            scope=scope,
            intent_id=intent_id,
            reference=command.reference,
            payer_reference=command.payer_reference,
            target_reference=command.target_reference,
            purpose=command.purpose,
            requested=money,
            confirmed=None,
            provider_type="PAYSTACK",
            channel="WEB",
            status=PaymentIntentStatus.PENDING,
            opened_at=now,
            expires_at=command.expires_at,
            settled_at=None,
        )
        assert get_payment_intent_v1(session, scope=scope, query=query) == expected
        assert (
            get_payment_intent_v1(
                session, scope=scope, query=GetPaymentIntentV1("unknown")
            )
            is None
        )
        # Explicit SQL tenant scope must hold even against an RLS-visible row.
        assert get_payment_intent_v1(session, scope=foreign, query=query) is None
        stale = session.get(PaymentIntent, intent_id)
        stale.reference = "dirty-reference"
        stale.payer_reference = "dirty-payer"
        stale.target_reference = None
        stale.requested_amount = Money.of("9", money.currency).amount
        stale.minor_units = None
        stale.provider_type = "DIRTY"
        stale.channel = "DIRTY"
        stale.status = PaymentIntentStatus.CANCELLED
        stale.opened_at = now + timedelta(days=1)
        stale.expires_at = None
        stale.settled_at = now + timedelta(days=2)
        assert get_payment_intent_v1(session, scope=scope, query=query) == expected
        assert stale in session.dirty
        assert stale.reference == "dirty-reference"
        session.rollback()
    with _tenant_session(app_url, foreign.tenant_id) as session:
        observation = get_payment_intent_v1(session, scope=foreign, query=query)
        assert observation.intent_id == foreign_id
        assert observation.payer_reference == "payer:foreign"
        assert observation.target_reference == "invoice:foreign"
    with _tenant_session(app_url, scope.tenant_id) as session:
        assert get_payment_intent_v1(session, scope=scope, query=query) == expected
        settled_at = now + timedelta(minutes=10)
        record_confirmation(
            session,
            scope=scope,
            command=RecordConfirmation(
                intent_id=intent_id,
                source=ConfirmationSource.PROVIDER_CALLBACK,
                external_reference="lookup-settlement",
                confirmed=money,
                observed_at=settled_at,
            ),
        )
        session.commit()
    with _tenant_session(app_url, scope.tenant_id) as session:
        assert get_payment_intent_v1(session, scope=scope, query=query) == replace(
            expected,
            status=PaymentIntentStatus.CONFIRMED,
            confirmed=money,
            settled_at=settled_at,
        )
        rollback_query = GetPaymentIntentV1("lookup-rollback")
        pending = open_payment_intent(
            session,
            scope=scope,
            command=replace(
                command,
                reference=rollback_query.reference,
                target_reference=None,
                expires_at=None,
            ),
        )
        rolled_back = get_payment_intent_v1(session, scope=scope, query=rollback_query)
        assert rolled_back.intent_id == pending.id
        assert rolled_back.target_reference is None
        assert rolled_back.expires_at is None
        session.rollback()
    with _tenant_session(app_url, scope.tenant_id) as session:
        assert get_payment_intent_v1(session, scope=scope, query=rollback_query) is None


def _purchase_migration_snapshot(connection):
    rows = {
        table: connection.execute(
            text(f"SELECT to_jsonb(t) FROM mod_payments.{table} t ORDER BY id")  # noqa: S608 — closed TABLES
        )
        .scalars()
        .all()
        for table in TABLES
    }
    posture = connection.execute(
        text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, c.relacl::text "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = 'mod_payments' "
            "AND c.relkind = 'r' ORDER BY c.relname"
        )
    ).all()
    policies = connection.execute(
        text(
            "SELECT * FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'mod_payments' ORDER BY tablename, policyname"
        )
    ).all()
    confirmation_guard = connection.execute(
        text(
            "SELECT pg_catalog.pg_get_triggerdef(t.oid) FROM pg_catalog.pg_trigger t "
            "WHERE t.tgrelid = 'mod_payments.payment_confirmations'::regclass "
            "AND NOT t.tgisinternal ORDER BY t.tgname"
        )
    ).all()
    return rows, posture, policies, confirmation_guard


@pytest.mark.parametrize(
    "migrated", ["heads", "pm_0003_confirmation_immutable"], indirect=True
)
def test_purchase_funding_fresh_and_populated_pm3_upgrade(migrated):
    """Real pm3 DDL/data, never a stamp or metadata-created predecessor."""
    from dotmac_kernel.cache import PlatformScope
    from dotmac_payments import (
        GetConfirmedCashV1,
        GetPaymentIntentV1,
        get_confirmed_cash_v1,
        get_payment_intent_v1,
    )
    from sqlalchemy.exc import DBAPIError

    from alembic import command as alembic_command

    scope, legacy_id, legacy_confirmation = _confirmed_evidence(
        migrated, with_proof=True
    )
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        for purpose in (
            PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT,
            PaymentPurpose.SERVICE_FEE,
        ):
            open_payment_intent(
                session,
                scope=scope,
                command=OpenPaymentIntent(
                    reference=f"legacy-{purpose.value}",
                    payer_reference="legacy:payer",
                    purpose=purpose,
                    requested=Money.of("15000", currency("NGN")),
                    provider_type="paystack",
                    channel="web",
                    opened_at=datetime(2026, 10, 13, tzinfo=UTC),
                ),
            )
        session.commit()
    # Heads now extend past pm_0004; descend through the still-empty
    # successor tables so the pm3-to-pm4 round trip below starts at pm_0004.
    alembic_command.downgrade(_payments_config(), "pm_0004_purchase_funding")
    admin = create_engine(migrated[0])
    with admin.begin() as connection:
        before = _purchase_migration_snapshot(connection)
    alembic_command.upgrade(_payments_config(), "pm_0004_purchase_funding")
    with admin.begin() as connection:
        assert _purchase_migration_snapshot(connection) == before
    # With only legacy evidence, round-trip downgrade preserves all original rows
    # and pm3's confirmation immutability. Funding evidence later prevents it.
    alembic_command.downgrade(_payments_config(), "pm_0003_confirmation_immutable")
    alembic_command.upgrade(_payments_config(), "pm_0004_purchase_funding")
    with admin.begin() as connection:
        assert _purchase_migration_snapshot(connection) == before

    now = datetime(2026, 10, 13, tzinfo=UTC)
    money = Money.of("15000", currency("NGN"))
    opening = OpenPaymentIntent(
        reference="purchase-funding",
        payer_reference="payer:purchase",
        target_reference="obligation:purchase-1 ",
        purpose=PaymentPurpose.PURCHASE_FUNDING,
        requested=money,
        provider_type="paystack",
        channel="web",
        opened_at=now,
        expires_at=now + timedelta(hours=1),
    )
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        intent = open_payment_intent(session, scope=scope, command=opening)
        intent_id = intent.id
        assert (
            open_payment_intent(session, scope=scope, command=opening).id == intent_id
        )
        query = GetPaymentIntentV1(opening.reference)
        observation = get_payment_intent_v1(session, scope=scope, query=query)
        assert observation.purpose is PaymentPurpose.PURCHASE_FUNDING
        assert observation.target_reference == opening.target_reference
        for changed in (
            replace(opening, target_reference="obligation:purchase-1"),
            replace(opening, target_reference="obligation:purchase-2"),
            replace(opening, target_reference=None),
            *(
                replace(opening, purpose=purpose)
                for purpose in (
                    PaymentPurpose.INVOICE_SETTLEMENT,
                    PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT,
                    PaymentPurpose.SERVICE_FEE,
                )
            ),
        ):
            with pytest.raises(Conflict):
                open_payment_intent(session, scope=scope, command=changed)
        with pytest.raises(TypeError, match="TenantScope"):
            open_payment_intent(session, scope=PlatformScope(), command=opening)
        confirmation_command = RecordConfirmation(
            intent_id=intent_id,
            source=ConfirmationSource.PROVIDER_CALLBACK,
            external_reference="purchase-provider-fact",
            confirmed=money,
            observed_at=now,
        )
        confirmation = record_confirmation(
            session, scope=scope, command=confirmation_command
        )
        confirmation_id = confirmation.id
        assert (
            record_confirmation(session, scope=scope, command=confirmation_command).id
            == confirmation_id
        )
        cash = get_confirmed_cash_v1(
            session, scope=scope, query=GetConfirmedCashV1(confirmation_id)
        )
        assert cash.intent_id == intent_id
        assert cash.confirmed == money
        with pytest.raises(Conflict):
            record_confirmation(
                session,
                scope=scope,
                command=replace(confirmation_command, intent_id=legacy_id),
            )
        assert (
            get_confirmed_cash_v1(
                session, scope=scope, query=GetConfirmedCashV1(legacy_confirmation)
            ).intent_id
            == legacy_id
        )
        session.commit()
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        assert (
            open_payment_intent(session, scope=scope, command=opening).status
            is PaymentIntentStatus.CONFIRMED
        )
        assert (
            get_payment_intent_v1(session, scope=scope, query=query).target_reference
            == opening.target_reference
        )
        # A no-op binding update remains compatible with lifecycle writes.
        session.execute(
            PaymentIntent.__table__.update()
            .where(PaymentIntent.id == intent_id)
            .values(target_reference=opening.target_reference)
        )
        pending_id = open_payment_intent(
            session, scope=scope, command=replace(opening, reference="purchase-pending")
        ).id
        # Raw insert cannot bypass the required target or expanded finite purpose set.
        inserted = dict(
            session.execute(
                select(PaymentIntent.__table__).where(PaymentIntent.id == intent_id)
            )
            .mappings()
            .one()
        )
        for invalid in (
            {"target_reference": None},
            {"target_reference": "   "},
            {"target_reference": "\t\r\n"},
            {"purpose": "UNDECLARED_PURPOSE"},
        ):
            forged = {
                **inserted,
                **invalid,
                "id": uuid.uuid4(),
                "reference": str(uuid.uuid4()),
            }
            with pytest.raises(DBAPIError), session.begin_nested():
                session.execute(PaymentIntent.__table__.insert().values(**forged))
        session.commit()

    for actor in migrated:
        engine = create_engine(actor)
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(scope.tenant_id)},
            )
            for values in (
                {"target_reference": "obligation:other"},
                {"purpose": PaymentPurpose.INVOICE_SETTLEMENT},
            ):
                with (
                    pytest.raises(DBAPIError, match="binding is immutable"),
                    connection.begin_nested(),
                ):
                    connection.execute(
                        PaymentIntent.__table__.update()
                        .where(PaymentIntent.id == intent_id)
                        .values(**values)
                    )
            with (
                pytest.raises(DBAPIError, match="binding is immutable"),
                connection.begin_nested(),
            ):
                connection.execute(
                    PaymentIntent.__table__.delete().where(
                        PaymentIntent.id == pending_id
                    )
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    PaymentConfirmation.__table__.update()
                    .where(PaymentConfirmation.id == confirmation_id)
                    .values(confirmed_amount=1)
                )
        engine.dispose()

    foreign, _, _, _ = _seed_pending(migrated)
    with _tenant_session(migrated[1], foreign.tenant_id) as session:
        assert get_payment_intent_v1(session, scope=foreign, query=query) is None
        assert session.get(PaymentConfirmation, confirmation_id) is None
        assert (
            session.execute(
                PaymentIntent.__table__.update()
                .where(PaymentIntent.id == intent_id)
                .values(channel="FOREIGN")
            ).rowcount
            == 0
        )
        other = open_payment_intent(
            session,
            scope=foreign,
            command=replace(opening, target_reference="obligation:foreign"),
        )
        assert other.id != intent_id
        forged = dict(
            session.execute(
                select(PaymentIntent.__table__).where(PaymentIntent.id == other.id)
            )
            .mappings()
            .one()
        )
        forged.update(
            id=uuid.uuid4(), tenant_id=scope.tenant_id, reference="forged-purchase"
        )
        with (
            pytest.raises(DBAPIError, match="row-level security"),
            session.begin_nested(),
        ):
            session.execute(PaymentIntent.__table__.insert().values(**forged))
        session.rollback()
    with pytest.raises(RuntimeError, match="purchase funding intents exist"):
        alembic_command.downgrade(_payments_config(), "pm_0003_confirmation_immutable")
    with admin.begin() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM mod_payments.payment_intents "
                    "WHERE purpose = 'PURCHASE_FUNDING'"
                )
            )
            == 2
        )
    admin.dispose()


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("actor", ["owner", "app_user"])
def test_purchase_checkout_binding_cannot_move_or_reopen(migrated, confirmed, actor):
    """A reference move must not free its natural key for a different purchase."""
    from dotmac_payments import GetPaymentIntentV1, get_payment_intent_v1
    from sqlalchemy.exc import DBAPIError

    scope, _, _, now = _seed_pending(migrated)
    opening = OpenPaymentIntent(
        reference="purchase-immutable-reference",
        payer_reference="payer:purchase",
        target_reference="purchase:original",
        purpose=PaymentPurpose.PURCHASE_FUNDING,
        requested=Money.of("15000", currency("NGN")),
        provider_type="paystack",
        channel="web",
        opened_at=now,
        expires_at=now + timedelta(hours=1),
    )
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        intent = open_payment_intent(session, scope=scope, command=opening)
        intent_id = intent.id
        if confirmed:
            record_confirmation(
                session,
                scope=scope,
                command=RecordConfirmation(
                    intent_id=intent_id,
                    source=ConfirmationSource.PROVIDER_CALLBACK,
                    external_reference="immutable-purchase-confirmation",
                    confirmed=opening.requested,
                    observed_at=now,
                ),
            )
        expected = get_payment_intent_v1(
            session, scope=scope, query=GetPaymentIntentV1(opening.reference)
        )
        session.commit()
    foreign, _, _, _ = _seed_pending(migrated)
    engine = create_engine(migrated[0 if actor == "owner" else 1])
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(scope.tenant_id)},
        )
        for values in (
            {"id": uuid.uuid4()},
            {"tenant_id": foreign.tenant_id},
            {"reference": "purchase-moved"},
            {"payer_reference": "payer:other"},
            {"target_reference": "purchase:other"},
            {"purpose": PaymentPurpose.SERVICE_FEE},
            {"requested_amount": 16000},
            {"currency_code": "USD"},
            {"minor_units": 3},
            {"provider_type": "OTHER"},
            {"channel": "OTHER"},
            {"opened_at": now + timedelta(minutes=1)},
            {"expires_at": now + timedelta(hours=2)},
        ):
            # FK/RLS may refuse identity changes before the AFTER guard; every
            # path must refuse, without weakening the original row or its key.
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    PaymentIntent.__table__.update()
                    .where(PaymentIntent.id == intent_id)
                    .values(**values)
                )
    engine.dispose()
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        assert (
            get_payment_intent_v1(
                session, scope=scope, query=GetPaymentIntentV1(opening.reference)
            )
            == expected
        )
        assert (
            get_payment_intent_v1(
                session, scope=scope, query=GetPaymentIntentV1("purchase-moved")
            )
            is None
        )
        with pytest.raises(Conflict, match="different target"):
            open_payment_intent(
                session,
                scope=scope,
                command=replace(opening, target_reference="purchase:replacement"),
            )
        assert (
            open_payment_intent(session, scope=scope, command=opening).id == intent_id
        )


def test_purchase_binding_guard_observes_later_before_trigger(migrated):
    """A BEFORE guard would miss a later trigger's otherwise valid mutation."""
    from sqlalchemy.exc import DBAPIError

    scope, _, _, now = _seed_pending(migrated)
    with _tenant_session(migrated[1], scope.tenant_id) as session:
        intent = open_payment_intent(
            session,
            scope=scope,
            command=OpenPaymentIntent(
                reference="purchase-before-trigger",
                payer_reference="payer:1",
                target_reference="purchase:1",
                purpose=PaymentPurpose.PURCHASE_FUNDING,
                requested=Money.of("15000", currency("NGN")),
                provider_type="paystack",
                channel="web",
                opened_at=now,
            ),
        )
        intent_id = intent.id
        session.commit()
    admin = create_engine(migrated[0])
    with admin.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION mod_payments.test_late_purchase_mutator()
            RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
            BEGIN
                NEW.reference := 'purchase-trigger-moved';
                RETURN NEW;
            END; $$;
            CREATE TRIGGER zzz_test_late_purchase_mutator
            BEFORE UPDATE ON mod_payments.payment_intents
            FOR EACH ROW EXECUTE FUNCTION mod_payments.test_late_purchase_mutator();
        """)
        )
    try:
        with _tenant_session(migrated[1], scope.tenant_id) as session:
            with (
                pytest.raises(DBAPIError, match="binding is immutable"),
                session.begin_nested(),
            ):
                session.execute(
                    PaymentIntent.__table__.update()
                    .where(PaymentIntent.id == intent_id)
                    .values(channel="WEB")
                )
            assert (
                session.scalar(
                    select(PaymentIntent.reference).where(PaymentIntent.id == intent_id)
                )
                == "purchase-before-trigger"
            )
            session.rollback()
    finally:
        with admin.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER zzz_test_late_purchase_mutator "
                    "ON mod_payments.payment_intents"
                )
            )
            connection.execute(
                text("DROP FUNCTION mod_payments.test_late_purchase_mutator()")
            )
        admin.dispose()
