"""PostgreSQL RLS canary for payment intents."""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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
