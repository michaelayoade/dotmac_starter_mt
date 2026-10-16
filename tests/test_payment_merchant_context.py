"""Real pm5 tenant/RLS/lifecycle canaries; never provider-origin qualification."""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_payments import (
    AcceptMerchantContextV1,
    AuthorizedMerchantReviewV1,
    Conflict,
    GetOriginalMerchantContextV1,
    MerchantConfigurationV1,
    MerchantContextV1,
    MerchantReviewEvidenceV1,
    MerchantReviewOperation,
    MerchantTransportContextV1,
    ResolveMerchantContextV1,
    RevokeMerchantContextV1,
    accept_merchant_context,
    get_original_merchant_context,
    merchant_review_request_fingerprint,
    resolve_current_merchant_context,
    revoke_merchant_context,
)
from dotmac_payments.models import MerchantContext
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_payments_isolation import (
    _payments_config,
    _tenant_session,
)
from tests.test_payments_isolation import (
    migrated as payments_migrated,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def migrated(request: pytest.FixtureRequest) -> Iterator[tuple[str, str]]:
    # Reuse the established hosted migration/cleanup owner, but a missing
    # PostgreSQL target is a failure for these authoritative canaries.
    if not (os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")):
        pytest.fail("merchant context proofs require a migrated PostgreSQL target")
    yield from payments_migrated.__wrapped__(request)


@dataclass
class FinanceReviewFixtureAuthority:
    """Synthetic installed authority admits only pre-recorded exact reviews.

    This is a test authority, not a production verifier or an always-yes stub.
    No real Finance review or provider evidence is manufactured by these tests.
    """

    approvals: set[tuple[UUID, UUID, str]] = field(default_factory=set)
    calls: int = 0
    deny: bool = False
    fingerprint_override: str | None = None
    reviewer_reference: str = "fixture:authenticated-finance-reviewer"
    reviewed_at: datetime = NOW

    def approve(
        self,
        scope: TenantScope,
        command: AcceptMerchantContextV1 | RevokeMerchantContextV1,
    ) -> None:
        self.approvals.add(
            (
                scope.tenant_id,
                command.command_id,
                merchant_review_request_fingerprint(command),
            )
        )

    def authorize(
        self,
        db: Session,
        *,
        scope: TenantScope,
        command: AcceptMerchantContextV1 | RevokeMerchantContextV1,
        request_fingerprint: str,
    ) -> AuthorizedMerchantReviewV1:
        self.calls += 1
        if (
            self.deny
            or (scope.tenant_id, command.command_id, request_fingerprint)
            not in self.approvals
        ):
            raise Conflict(
                "Finance reviewer lacks current permission or exact reviewed decision"
            )
        return AuthorizedMerchantReviewV1(
            scope=scope,
            command_id=command.command_id,
            operation=MerchantReviewOperation.ACCEPT
            if type(command) is AcceptMerchantContextV1
            else MerchantReviewOperation.REVOKE,
            request_fingerprint=self.fingerprint_override or request_fingerprint,
            reviewer_reference=self.reviewer_reference,
            reviewed_at=self.reviewed_at,
        )


def _request() -> AcceptMerchantContextV1:
    return AcceptMerchantContextV1(
        command_id=uuid4(),
        configuration=MerchantConfigurationV1(
            MerchantTransportContextV1(
                "paystack",
                "production",
                "installation:1",
                "reconcile:1",
                "revision:1",
                "a" * 64,
            ),
            "business:123",
        ),
        evidence=MerchantReviewEvidenceV1(
            "fixture-archive:reviewed-business-identity", "b" * 64
        ),
        effective_from=NOW,
    )


def _seed(migrated: tuple[str, str]) -> tuple[TenantScope, TenantScope]:
    tenants = (TenantScope(uuid4()), TenantScope(uuid4()))
    engine = create_engine(migrated[0])
    try:
        with engine.begin() as connection:
            for scope in tenants:
                connection.execute(
                    text(
                        "INSERT INTO public.tenants (id,slug,name) "
                        "VALUES (:id,:slug,:slug)"
                    ),
                    {"id": scope.tenant_id, "slug": f"merchant-test-{scope.tenant_id}"},
                )
    finally:
        engine.dispose()
    return tenants


def _accept(
    migrated: tuple[str, str],
    scope: TenantScope,
    request: AcceptMerchantContextV1,
    authority: FinanceReviewFixtureAuthority,
) -> MerchantContextV1:
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        result = accept_merchant_context(
            db, scope=scope, command=request, authority=authority
        )
        db.commit()
        return result


@contextmanager
def _owner(
    migrated: tuple[str, str], tenant: UUID, *, isolation: str = "READ COMMITTED"
) -> Iterator[Session]:
    engine = create_engine(migrated[0], isolation_level=isolation)
    try:
        with Session(engine) as db:
            db.execute(
                text("SELECT set_config('app.current_tenant',:tenant,true)"),
                {"tenant": str(tenant)},
            )
            yield db
            db.commit()
    finally:
        engine.dispose()


def test_real_tenant_isolation_and_full_review_replay(
    migrated: tuple[str, str],
) -> None:
    scope, other = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    first = _accept(migrated, scope, request, authority)
    assert _accept(migrated, scope, request, authority) == first
    assert authority.calls == 2  # Fresh authorization precedes even exact replay.
    query = ResolveMerchantContextV1(request.configuration.transport, NOW)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert resolve_current_merchant_context(db, scope=scope, query=query) == first
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM public.outbox_events "
                    "WHERE event_type='payments.merchant_context.accepted.v1'"
                )
            )
            == 1
        )
    with _tenant_session(migrated[1], other.tenant_id) as db:
        assert db.scalar(select(MerchantContext)) is None
        with pytest.raises(Conflict):
            resolve_current_merchant_context(db, scope=other, query=query)
        with pytest.raises(Conflict):
            get_original_merchant_context(
                db,
                scope=other,
                query=GetOriginalMerchantContextV1(
                    first.context_id, query.transport, NOW
                ),
            )
        # Explicit WHERE and forced RLS both refuse a mismatched caller scope.
        with pytest.raises(Conflict):
            resolve_current_merchant_context(db, scope=scope, query=query)
    authority.approve(other, request)
    # IDs are global PKs; another tenant's command cannot steal an existing ID.
    with pytest.raises(DBAPIError):
        _accept(migrated, other, request, authority)
    authority.deny = True
    with pytest.raises(Conflict, match="current permission"):
        _accept(migrated, scope, request, authority)


def test_wrong_or_mutated_authority_attestation_refuses_before_write(
    migrated: tuple[str, str],
) -> None:
    scope, _ = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority(fingerprint_override="c" * 64)
    authority.approve(scope, request)
    with pytest.raises(Conflict, match="exact request"):
        _accept(migrated, scope, request, authority)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert db.scalar(select(MerchantContext)) is None
    authority.fingerprint_override = None
    _accept(migrated, scope, request, authority)
    changed = replace(
        request, evidence=MerchantReviewEvidenceV1("fixture-archive:changed", "d" * 64)
    )
    authority.approve(scope, changed)
    with pytest.raises(Conflict, match="other material"):
        _accept(migrated, scope, changed, authority)


def test_supersession_and_revocation_preserve_original_evidence(
    migrated: tuple[str, str],
) -> None:
    scope, _ = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    original = _accept(migrated, scope, request, authority)
    replacement = replace(
        request,
        command_id=uuid4(),
        effective_from=NOW + timedelta(days=1),
        supersedes_context_id=original.context_id,
        configuration=replace(
            request.configuration, physical_merchant_id="business:456"
        ),
    )
    authority.approve(scope, replacement)
    current = _accept(migrated, scope, replacement, authority)
    revoke = RevokeMerchantContextV1(
        uuid4(),
        current.context_id,
        MerchantReviewEvidenceV1("fixture-archive:revocation", "c" * 64),
    )
    authority.approve(scope, revoke)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        with pytest.raises(Conflict):
            resolve_current_merchant_context(
                db,
                scope=scope,
                query=ResolveMerchantContextV1(request.configuration.transport, NOW),
            )
        assert (
            get_original_merchant_context(
                db,
                scope=scope,
                query=GetOriginalMerchantContextV1(
                    original.context_id,
                    request.configuration.transport,
                    NOW + timedelta(days=2),
                ),
            )
            == original
        )
        ended = revoke_merchant_context(
            db, scope=scope, command=revoke, authority=authority
        )
        assert (
            revoke_merchant_context(
                db, scope=scope, command=revoke, authority=authority
            )
            == ended
        )
        db.commit()
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        with pytest.raises(Conflict):
            resolve_current_merchant_context(
                db,
                scope=scope,
                query=ResolveMerchantContextV1(
                    replacement.configuration.transport, NOW + timedelta(days=2)
                ),
            )
        assert (
            get_original_merchant_context(
                db,
                scope=scope,
                query=GetOriginalMerchantContextV1(
                    current.context_id,
                    replacement.configuration.transport,
                    NOW + timedelta(days=2),
                ),
            )
            == current
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE mod_payments.merchant_contexts SET physical_merchant_id='changed'",
        "DELETE FROM mod_payments.merchant_contexts",
        "TRUNCATE mod_payments.merchant_contexts CASCADE",
        "UPDATE mod_payments.merchant_context_ends SET evidence_sha256=repeat('d',64)",
        "DELETE FROM mod_payments.merchant_context_ends",
        "TRUNCATE mod_payments.merchant_context_ends",
    ],
)
def test_final_history_guards_refuse_owner_dml(
    migrated: tuple[str, str], statement: str
) -> None:
    scope, _ = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    original = _accept(migrated, scope, request, authority)
    revoke = RevokeMerchantContextV1(uuid4(), original.context_id, request.evidence)
    authority.approve(scope, revoke)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        revoke_merchant_context(db, scope=scope, command=revoke, authority=authority)
        db.commit()
    with pytest.raises(DBAPIError), _owner(migrated, scope.tenant_id) as db:
        db.execute(text(statement))


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_public_and_raw_insert_refuse_stale_isolation(
    migrated: tuple[str, str], isolation: str
) -> None:
    scope, _ = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    original = _accept(migrated, scope, request, authority)
    replacement = replace(
        request,
        command_id=uuid4(),
        effective_from=NOW + timedelta(days=1),
        supersedes_context_id=original.context_id,
    )
    authority.approve(scope, replacement)
    with (
        pytest.raises(Conflict, match="READ WRITE READ COMMITTED"),
        _owner(migrated, scope.tenant_id, isolation=isolation) as db,
    ):
        accept_merchant_context(
            db, scope=scope, command=replacement, authority=authority
        )
    with (
        pytest.raises(DBAPIError, match="READ WRITE READ COMMITTED"),
        _owner(migrated, scope.tenant_id, isolation=isolation) as db,
    ):
        db.execute(
            text(
                "INSERT INTO mod_payments.merchant_contexts "
                "SELECT :id,tenant_id,:id,request_fingerprint,evidence_reference,"
                "evidence_sha256,reviewer_reference,reviewed_at,provider_type,"
                "environment,physical_merchant_id,installation_reference,"
                "capability_reference,config_revision_reference,config_digest,"
                "effective_from,effective_until FROM mod_payments.merchant_contexts "
                "WHERE id=:original"
            ),
            {"id": uuid4(), "original": original.context_id},
        )
    with (
        pytest.raises(DBAPIError, match="READ WRITE READ COMMITTED"),
        _owner(migrated, scope.tenant_id, isolation=isolation) as db,
    ):
        db.execute(
            text(
                "INSERT INTO mod_payments.merchant_context_ends "
                "(id,tenant_id,command_id,request_fingerprint,evidence_reference,"
                "evidence_sha256,reviewer_reference,reviewed_at,context_id,"
                "operation,effective_at) VALUES (:id,:tenant,:command,:digest,"
                "'fixture:review',:digest,'fixture:reviewer',now(),:original,"
                "'REVOKE',now())"
            ),
            {
                "id": uuid4(),
                "tenant": scope.tenant_id,
                "command": uuid4(),
                "digest": "a" * 64,
                "original": original.context_id,
            },
        )
    revoke = RevokeMerchantContextV1(uuid4(), original.context_id, request.evidence)
    authority.approve(scope, revoke)
    with (
        pytest.raises(Conflict, match="READ WRITE READ COMMITTED"),
        _owner(migrated, scope.tenant_id, isolation=isolation) as db,
    ):
        revoke_merchant_context(db, scope=scope, command=revoke, authority=authority)
    with _owner(migrated, scope.tenant_id, isolation=isolation) as db:
        assert (
            get_original_merchant_context(
                db,
                scope=scope,
                query=GetOriginalMerchantContextV1(
                    original.context_id, request.configuration.transport, NOW
                ),
            )
            == original
        )


def test_concurrent_rc_reviews_accept_one_interval(migrated: tuple[str, str]) -> None:
    scope, _ = _seed(migrated)
    requests = (_request(), _request())
    ready = Event()
    release = Event()

    def first() -> None:
        authority = FinanceReviewFixtureAuthority()
        authority.approve(scope, requests[0])
        with _tenant_session(migrated[1], scope.tenant_id) as db:
            accept_merchant_context(
                db, scope=scope, command=requests[0], authority=authority
            )
            ready.set()
            assert release.wait(10)
            db.commit()

    def second() -> None:
        assert ready.wait(10)
        authority = FinanceReviewFixtureAuthority()
        authority.approve(scope, requests[1])
        release.set()
        with pytest.raises(Conflict, match="competing accepted"):
            _accept(migrated, scope, requests[1], authority)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first), pool.submit(second)]
        for future in futures:
            future.result(timeout=20)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert len(db.scalars(select(MerchantContext)).all()) == 1


@pytest.mark.parametrize(
    "migrated", ["heads", "pm_0004_purchase_funding"], indirect=True
)
def test_fresh_and_pm4_upgrade_rls_acl_and_downgrade(migrated: tuple[str, str]) -> None:
    from alembic import command

    command.upgrade(_payments_config(), "heads")
    engine = create_engine(migrated[0])
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                    "WHERE oid='mod_payments.merchant_contexts'::regclass"
                )
            ).one() == (True, True)
            assert connection.scalar(
                text(
                    "SELECT has_table_privilege('app_user',"
                    "'mod_payments.merchant_contexts','INSERT')"
                )
            )
            assert not connection.scalar(
                text(
                    "SELECT has_table_privilege('app_user',"
                    "'mod_payments.merchant_contexts','UPDATE')"
                )
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE tgrelid IN "
                        "('mod_payments.merchant_contexts'::regclass,"
                        "'mod_payments.merchant_context_ends'::regclass) "
                        "AND NOT tgisinternal AND tgenabled='A'"
                    )
                )
                == 7
            )
        with pytest.raises(RuntimeError, match="forward-only"):
            command.downgrade(_payments_config(), "pm_0004_purchase_funding")
    finally:
        engine.dispose()


def test_final_insert_guard_observes_late_before_trigger(
    migrated: tuple[str, str],
) -> None:
    scope, _ = _seed(migrated)
    original_request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, original_request)
    _accept(migrated, scope, original_request, authority)
    with _owner(migrated, scope.tenant_id) as db:
        db.execute(
            text("""
            CREATE FUNCTION mod_payments.test_late_merchant_context_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN NEW.installation_reference='installation:1'; RETURN NEW; END $$;
            CREATE TRIGGER zzzz_late_merchant_context_mutation
            BEFORE INSERT ON mod_payments.merchant_contexts FOR EACH ROW
            EXECUTE FUNCTION mod_payments.test_late_merchant_context_mutation();
        """)
        )
    different = replace(
        original_request,
        command_id=uuid4(),
        configuration=replace(
            original_request.configuration,
            transport=replace(
                original_request.configuration.transport,
                installation_reference="installation:2",
            ),
        ),
    )
    authority.approve(scope, different)
    # Service precheck sees an independent slot; final AFTER sees the actual
    # tuple produced by the hostile BEFORE trigger and refuses the overlap.
    with pytest.raises(DBAPIError, match="competing accepted"):
        _accept(migrated, scope, different, authority)


def test_direct_sql_overlap_and_cross_tenant_end_fk_refuse(
    migrated: tuple[str, str],
) -> None:
    scope, other = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    original = _accept(migrated, scope, request, authority)
    with (
        pytest.raises(DBAPIError, match="competing accepted"),
        _owner(migrated, scope.tenant_id) as db,
    ):
        # Table is migration-created; the final INSERT guard must protect
        # direct writers as well as the public typed command.
        db.execute(
            text("""
            INSERT INTO mod_payments.merchant_contexts
            SELECT :id,tenant_id,:id,request_fingerprint,evidence_reference,
                evidence_sha256,reviewer_reference,reviewed_at,provider_type,
                environment,physical_merchant_id,installation_reference,
                capability_reference,config_revision_reference,config_digest,
                effective_from,effective_until
            FROM mod_payments.merchant_contexts WHERE id=:original
        """),
            {"id": uuid4(), "original": original.context_id},
        )
    with pytest.raises(DBAPIError), _owner(migrated, other.tenant_id) as db:
        db.execute(
            text("""
            INSERT INTO mod_payments.merchant_context_ends
            (id,tenant_id,command_id,request_fingerprint,evidence_reference,
             evidence_sha256,reviewer_reference,reviewed_at,context_id,
             operation,effective_at)
            VALUES (:id,:tenant,:command,:digest,'fixture:review',:digest,
                'fixture:reviewer',now(),:original,'REVOKE',now())
        """),
            {
                "id": uuid4(),
                "tenant": other.tenant_id,
                "command": uuid4(),
                "digest": "a" * 64,
                "original": original.context_id,
            },
        )


@pytest.mark.parametrize("migrated", ["pm_0004_purchase_funding"], indirect=True)
def test_populated_pm4_intent_survives_pm5(migrated: tuple[str, str]) -> None:
    from decimal import Decimal

    from dotmac_kernel.money import Money, currency
    from dotmac_payments import OpenPaymentIntent, PaymentPurpose, open_payment_intent

    from alembic import command

    scope, _ = _seed(migrated)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        intent = open_payment_intent(
            db,
            scope=scope,
            command=OpenPaymentIntent(
                payer_reference="fixture:payer",
                purpose=PaymentPurpose.PURCHASE_FUNDING,
                requested=Money.of(Decimal("100.00"), currency("NGN")),
                reference="fixture:pm4-purchase",
                provider_type="paystack",
                channel="web",
                target_reference="fixture:purchase",
                opened_at=NOW,
            ),
        )
        intent_id = intent.id
        db.commit()
    command.upgrade(_payments_config(), "heads")
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        result = db.execute(
            text("""
            SELECT id,purpose,target_reference,requested_amount FROM
            mod_payments.payment_intents WHERE reference='fixture:pm4-purchase'
        """)
        ).one()
        assert tuple(result) == (
            intent_id,
            "PURCHASE_FUNDING",
            "fixture:purchase",
            Decimal("100.00"),
        )
    value = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, value)
    assert (
        _accept(migrated, scope, value, authority).configuration == value.configuration
    )


def test_raw_rc_insert_waits_then_rechecks_committed_interval(
    migrated: tuple[str, str],
) -> None:
    from time import monotonic, sleep

    scope, _ = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    locked = Event()
    release = Event()
    second_started = Event()
    second_pid: list[int] = []
    original_id = request.command_id

    def first() -> None:
        with _tenant_session(migrated[1], scope.tenant_id) as db:
            accept_merchant_context(
                db, scope=scope, command=request, authority=authority
            )
            locked.set()
            assert release.wait(15)
            db.commit()

    def second() -> None:
        assert locked.wait(10)
        with pytest.raises(DBAPIError, match="competing accepted"):
            with _owner(migrated, scope.tenant_id) as db:
                second_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
                second_started.set()
                # Independent direct insert starts BEFORE first commits. Its
                # final guard must see the first row after waiting on the lock.
                db.execute(
                    text("""
                    INSERT INTO mod_payments.merchant_contexts
                    (id,tenant_id,command_id,request_fingerprint,evidence_reference,
                     evidence_sha256,reviewer_reference,reviewed_at,provider_type,
                     environment,physical_merchant_id,installation_reference,
                     capability_reference,config_revision_reference,config_digest,
                     effective_from)
                    VALUES (:id,:tenant,:id,:digest,'fixture:review',:digest,
                     'fixture:reviewer',now(),'paystack','production','business:456',
                     'installation:1','reconcile:1','revision:1',:digest,:start)
                """),
                    {
                        "id": uuid4(),
                        "tenant": scope.tenant_id,
                        "digest": "a" * 64,
                        "start": NOW,
                    },
                )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first), pool.submit(second)]
        try:
            assert second_started.wait(10)
            deadline = monotonic() + 10
            with _owner(migrated, scope.tenant_id) as monitor:
                while monotonic() < deadline:
                    monitor.execute(text("SELECT pg_stat_clear_snapshot()"))
                    waiting = monitor.scalar(
                        text("SELECT wait_event FROM pg_stat_activity WHERE pid=:pid"),
                        {"pid": second_pid[0]},
                    )
                    if waiting == "advisory":
                        break
                    sleep(0.01)
                else:
                    pytest.fail("direct INSERT never reached the scoped advisory fence")
        finally:
            release.set()
        for future in futures:
            future.result(timeout=20)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert db.scalars(select(MerchantContext.id)).all() == [original_id]


def test_reads_and_replays_ignore_dirty_identity_map_without_flushing(
    migrated: tuple[str, str],
) -> None:
    from dotmac_payments.models import MerchantContextEnd
    from sqlalchemy import event

    scope, _ = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    original = _accept(migrated, scope, request, authority)
    query = ResolveMerchantContextV1(request.configuration.transport, NOW)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        dirty = db.get(MerchantContext, original.context_id)
        assert dirty is not None
        dirty.physical_merchant_id = "caller:unreviewed-merchant"
        dirty.evidence_sha256 = "c" * 64
        dirty.config_digest = "d" * 64

        def forbidden_flush(*_args: object) -> None:
            pytest.fail("merchant evidence read/replay attempted to flush caller state")

        event.listen(db, "before_flush", forbidden_flush)
        try:
            assert (
                resolve_current_merchant_context(db, scope=scope, query=query)
                == original
            )
            assert (
                get_original_merchant_context(
                    db,
                    scope=scope,
                    query=GetOriginalMerchantContextV1(
                        original.context_id, query.transport, NOW
                    ),
                )
                == original
            )
            assert (
                accept_merchant_context(
                    db, scope=scope, command=request, authority=authority
                )
                == original
            )
            with db.no_autoflush:
                assert (
                    resolve_current_merchant_context(db, scope=scope, query=query)
                    == original
                )
            assert dirty.physical_merchant_id == "caller:unreviewed-merchant"
            assert dirty in db.dirty
        finally:
            event.remove(db, "before_flush", forbidden_flush)
            db.rollback()
    revoke = RevokeMerchantContextV1(uuid4(), original.context_id, request.evidence)
    authority.approve(scope, revoke)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        ended = revoke_merchant_context(
            db, scope=scope, command=revoke, authority=authority
        )
        db.commit()
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        dirty_end = db.get(MerchantContextEnd, ended.end_id)
        assert dirty_end is not None
        dirty_end.reviewer_reference = "caller:unreviewed-reviewer"
        event.listen(db, "before_flush", forbidden_flush)
        try:
            assert (
                revoke_merchant_context(
                    db, scope=scope, command=revoke, authority=authority
                )
                == ended
            )
            assert (
                get_original_merchant_context(
                    db,
                    scope=scope,
                    query=GetOriginalMerchantContextV1(
                        original.context_id, query.transport, NOW
                    ),
                )
                == original
            )
        finally:
            event.remove(db, "before_flush", forbidden_flush)
            db.rollback()


@pytest.mark.parametrize("first_operation", ["ACCEPT", "REVOKE"])
def test_command_identity_fences_acceptance_and_raw_revocation_across_slots(
    migrated: tuple[str, str], first_operation: str
) -> None:
    from time import monotonic, sleep

    scope, _ = _seed(migrated)
    existing_request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, existing_request)
    existing = _accept(migrated, scope, existing_request, authority)
    shared_command = uuid4()
    acceptance = replace(
        existing_request,
        command_id=shared_command,
        configuration=replace(
            existing_request.configuration,
            transport=replace(
                existing_request.configuration.transport,
                installation_reference="installation:independent",
            ),
        ),
    )
    revocation = RevokeMerchantContextV1(
        shared_command, existing.context_id, existing_request.evidence
    )
    authority.approve(scope, acceptance)
    authority.approve(scope, revocation)
    locked, release, second_started = Event(), Event(), Event()
    second_pid: list[int] = []

    def raw_revoke(db: Session) -> None:
        db.execute(
            text("""
            INSERT INTO mod_payments.merchant_context_ends
            (id,tenant_id,command_id,request_fingerprint,evidence_reference,
             evidence_sha256,reviewer_reference,reviewed_at,context_id,operation,effective_at)
            VALUES (:id,:tenant,:command,:fingerprint,'fixture:review',:digest,
                'fixture:reviewer',now(),:original,'REVOKE',now())
        """),
            {
                "id": uuid4(),
                "tenant": scope.tenant_id,
                "command": shared_command,
                "fingerprint": merchant_review_request_fingerprint(revocation),
                "digest": "a" * 64,
                "original": existing.context_id,
            },
        )

    def first() -> None:
        with _owner(migrated, scope.tenant_id) as db:
            if first_operation == "ACCEPT":
                accept_merchant_context(
                    db, scope=scope, command=acceptance, authority=authority
                )
            else:
                raw_revoke(db)
            locked.set()
            assert release.wait(15)

    def second() -> None:
        assert locked.wait(10)
        expected_error = DBAPIError if first_operation == "ACCEPT" else Conflict
        with pytest.raises(expected_error), _owner(migrated, scope.tenant_id) as db:
            second_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            second_started.set()
            if first_operation == "ACCEPT":
                raw_revoke(db)
            else:
                accept_merchant_context(
                    db, scope=scope, command=acceptance, authority=authority
                )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first), pool.submit(second)]
        try:
            assert second_started.wait(10)
            deadline = monotonic() + 10
            with _owner(migrated, scope.tenant_id) as monitor:
                while monotonic() < deadline:
                    monitor.execute(text("SELECT pg_stat_clear_snapshot()"))
                    if (
                        monitor.scalar(
                            text(
                                "SELECT wait_event FROM pg_stat_activity WHERE pid=:pid"
                            ),
                            {"pid": second_pid[0]},
                        )
                        == "advisory"
                    ):
                        break
                    sleep(0.01)
                else:
                    pytest.fail("competing operation never reached the command fence")
        finally:
            release.set()
        for future in futures:
            future.result(timeout=20)
    with _owner(migrated, scope.tenant_id) as db:
        accepted = db.scalar(
            text(
                "SELECT count(*) FROM mod_payments.merchant_contexts "
                "WHERE command_id=:command"
            ),
            {"command": shared_command},
        )
        ended = db.scalar(
            text(
                "SELECT count(*) FROM mod_payments.merchant_context_ends "
                "WHERE command_id=:command"
            ),
            {"command": shared_command},
        )
        assert (accepted, ended) == ((1, 0) if first_operation == "ACCEPT" else (0, 1))


def test_fresh_replay_authority_retains_original_review_attestation(
    migrated: tuple[str, str],
) -> None:
    scope, _ = _seed(migrated)
    command = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, command)
    original = _accept(migrated, scope, command, authority)
    authority.reviewer_reference = "fixture:another-currently-authorized-reviewer"
    authority.reviewed_at = NOW + timedelta(days=1)
    assert _accept(migrated, scope, command, authority) == original
    revoke = RevokeMerchantContextV1(uuid4(), original.context_id, command.evidence)
    authority.approve(scope, revoke)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        ended = revoke_merchant_context(
            db, scope=scope, command=revoke, authority=authority
        )
        db.commit()
    authority.reviewer_reference = "fixture:third-currently-authorized-reviewer"
    authority.reviewed_at = NOW + timedelta(days=2)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert (
            revoke_merchant_context(
                db, scope=scope, command=revoke, authority=authority
            )
            == ended
        )


@pytest.mark.parametrize(
    "changed_field",
    [
        "scope",
        "command_id",
        "operation",
        "reviewer_reference",
        "naive_time",
        "future_time",
    ],
)
def test_authority_result_binding_fields_are_independently_checked(
    migrated: tuple[str, str], changed_field: str
) -> None:
    scope, _ = _seed(migrated)
    command = _request()

    class MalformedAuthority(FinanceReviewFixtureAuthority):
        def authorize(
            self,
            db: Session,
            *,
            scope: TenantScope,
            command: AcceptMerchantContextV1 | RevokeMerchantContextV1,
            request_fingerprint: str,
        ) -> AuthorizedMerchantReviewV1:
            result = super().authorize(
                db,
                scope=scope,
                command=command,
                request_fingerprint=request_fingerprint,
            )
            if changed_field == "scope":
                return replace(result, scope=TenantScope(uuid4()))
            if changed_field == "command_id":
                return replace(result, command_id=uuid4())
            if changed_field == "operation":
                return replace(result, operation=MerchantReviewOperation.REVOKE)
            if changed_field == "reviewer_reference":
                return replace(result, reviewer_reference=" ")
            if changed_field == "naive_time":
                return replace(result, reviewed_at=NOW.replace(tzinfo=None))
            return replace(result, reviewed_at=datetime.now(UTC) + timedelta(days=1))

    authority = MalformedAuthority()
    authority.approve(scope, command)
    with pytest.raises(Conflict):
        _accept(migrated, scope, command, authority)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert db.scalar(select(MerchantContext)) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("physical_merchant_id", "caller:altered-merchant"),
        ("evidence_sha256", "c" * 64),
    ],
)
def test_late_before_content_change_refuses_and_rolls_back_whole_command(
    migrated: tuple[str, str], field: str, value: str
) -> None:
    scope, _ = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    with _owner(migrated, scope.tenant_id) as db:
        # Closed canary fields/literals, never request SQL or external data.
        db.execute(
            text(f"""
            CREATE FUNCTION mod_payments.test_late_review_content()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN NEW.{field}='{value}'; RETURN NEW; END $$;
            CREATE TRIGGER zzzz_late_review_content BEFORE INSERT
            ON mod_payments.merchant_contexts FOR EACH ROW
            EXECUTE FUNCTION mod_payments.test_late_review_content();
        """)
        )
    with pytest.raises(Conflict, match="differs from the authorized review"):
        _accept(migrated, scope, request, authority)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert (
            db.scalar(text("SELECT count(*) FROM mod_payments.merchant_contexts")) == 0
        )
        assert (
            db.scalar(text("SELECT count(*) FROM mod_payments.merchant_context_ends"))
            == 0
        )
        assert db.scalar(text("SELECT count(*) FROM public.outbox_events")) == 0


@pytest.mark.parametrize("migrated", ["pm_0004_purchase_funding"], indirect=True)
def test_pm5_target_installs_real_bound_outbox_provider(
    migrated: tuple[str, str],
) -> None:
    from dotmac_kernel.migrations.verify import require_prerequisites
    from dotmac_kernel.prerequisites import OUTBOX_RELAY_V1

    from alembic import command
    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    binding = next(
        item
        for item in ASSEMBLY_PREREQUISITE_BINDINGS
        if item.prerequisite == OUTBOX_RELAY_V1.name
    )
    assert (binding.provider_owner, binding.provider_revision) == (
        "kernel",
        "0012_platform_outbox",
    )
    command.upgrade(_payments_config(), "pm_0005_merchant_context")
    engine = create_engine(migrated[0])
    try:
        with engine.connect() as connection:
            require_prerequisites(connection, (OUTBOX_RELAY_V1.name,))
            assert connection.scalar(
                text("SELECT to_regclass('public.platform_outbox_events')")
            )
    finally:
        engine.dispose()
    scope, _ = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    assert (
        _accept(migrated, scope, request, authority).configuration
        == request.configuration
    )


@pytest.mark.parametrize("migrated", ["pm_0004_purchase_funding"], indirect=True)
def test_pm5_refuses_stamped_missing_outbox_catalog_before_ddl(
    migrated: tuple[str, str],
) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    from alembic import command

    cfg = _payments_config()
    command.upgrade(cfg, "0012_platform_outbox")
    engine = create_engine(migrated[0])
    try:
        with engine.begin() as connection:
            # Revision identity still claims the provider, but its platform
            # half is absent. No forged table/GRANT or manual metadata fallback.
            connection.execute(text("DROP TABLE public.platform_outbox_events CASCADE"))
        with pytest.raises(PrerequisiteNotSatisfiedError):
            command.upgrade(cfg, "pm_0005_merchant_context")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT to_regclass('mod_payments.merchant_contexts')")
                )
                is None
            )
            assert (
                connection.scalar(
                    text("SELECT to_regclass('mod_payments.merchant_context_ends')")
                )
                is None
            )
    finally:
        engine.dispose()


def test_distinct_commands_and_transport_slots_do_not_share_fence(
    migrated: tuple[str, str],
) -> None:
    scope, _ = _seed(migrated)
    first_request = _request()
    second_request = replace(
        _request(),
        configuration=replace(
            first_request.configuration,
            transport=replace(
                first_request.configuration.transport,
                installation_reference="installation:independent",
            ),
        ),
    )
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, first_request)
    authority.approve(scope, second_request)
    locked, release, independent_committed = Event(), Event(), Event()

    def first() -> None:
        with _tenant_session(migrated[1], scope.tenant_id) as db:
            accept_merchant_context(
                db, scope=scope, command=first_request, authority=authority
            )
            locked.set()
            assert release.wait(15)
            db.commit()

    def second() -> None:
        assert locked.wait(10)
        _accept(migrated, scope, second_request, authority)
        independent_committed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first), pool.submit(second)]
        try:
            assert independent_committed.wait(
                10
            ), "independent slot was globally serialized"
        finally:
            release.set()
        for future in futures:
            future.result(timeout=20)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert len(db.scalars(select(MerchantContext.id)).all()) == 2


@pytest.mark.parametrize("operation", ["supersession", "revocation"])
def test_late_before_end_evidence_change_rolls_back_complete_command(
    migrated: tuple[str, str], operation: str
) -> None:
    scope, _ = _seed(migrated)
    request = _request()
    authority = FinanceReviewFixtureAuthority()
    authority.approve(scope, request)
    original = _accept(migrated, scope, request, authority)
    with _owner(migrated, scope.tenant_id) as db:
        db.execute(
            text("""
            CREATE FUNCTION mod_payments.test_late_end_content()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                NEW.evidence_reference='caller:altered-end-evidence';
                RETURN NEW;
            END $$;
            CREATE TRIGGER zzzz_late_end_content BEFORE INSERT
            ON mod_payments.merchant_context_ends FOR EACH ROW
            EXECUTE FUNCTION mod_payments.test_late_end_content();
        """)
        )
    if operation == "supersession":
        replacement = replace(
            request,
            command_id=uuid4(),
            effective_from=NOW + timedelta(days=1),
            supersedes_context_id=original.context_id,
        )
        authority.approve(scope, replacement)
        with pytest.raises(Conflict, match="predecessor differs"):
            _accept(migrated, scope, replacement, authority)
    else:
        revoke = RevokeMerchantContextV1(uuid4(), original.context_id, request.evidence)
        authority.approve(scope, revoke)
        with pytest.raises(Conflict, match="termination differs"):
            with _tenant_session(migrated[1], scope.tenant_id) as db:
                revoke_merchant_context(
                    db, scope=scope, command=revoke, authority=authority
                )
                db.commit()
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert (
            db.scalar(text("SELECT count(*) FROM mod_payments.merchant_contexts")) == 1
        )
        assert (
            db.scalar(text("SELECT count(*) FROM mod_payments.merchant_context_ends"))
            == 0
        )
        assert db.scalar(text("SELECT count(*) FROM public.outbox_events")) == 1
