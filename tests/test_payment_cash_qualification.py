"""Hosted PostgreSQL proofs; trusted synthetic originals are not production proof."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.money import Currency, Money
from dotmac_payments import (
    AuthorizedProviderCashV1,
    Conflict,
    OriginalProviderCashEvidenceV1,
    QualifyProviderCashV1,
    RevokeMerchantContextV1,
    accept_merchant_context,
    get_provider_cash_qualification,
    provider_cash_economic_identity,
    provider_cash_request_fingerprint,
    qualify_provider_cash,
    require_provider_cash_admission,
    revoke_merchant_context,
)
from dotmac_payments.models import ProviderCashQualification
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_payment_merchant_context import (
    NOW,
    FinanceReviewFixtureAuthority,
    _accept,
    _owner,
    _request,
    _seed,
)
from tests.test_payment_merchant_context import migrated as merchant_migrated
from tests.test_payments_isolation import _payments_config, _tenant_session


@pytest.fixture
def migrated(request):
    yield from merchant_migrated.__wrapped__(request)


@dataclass
class OriginalFixtureAuthority:
    """One pre-registered exact original; no provider network or default verdict."""

    original: AuthorizedProviderCashV1
    deny: bool = False
    calls: int = 0

    def verify_original(self, db: Session, *, scope, request, request_fingerprint):
        self.calls += 1
        if (
            self.deny
            or scope != self.original.scope
            or request.command_id != self.original.command_id
            or request.original_evidence != self.original.original_evidence
            or request_fingerprint != self.original.request_fingerprint
        ):
            raise Conflict("original authenticated evidence unavailable")
        return self.original


def _fixture(migrated, *, acceptance=None):
    scope, other = _seed(migrated)
    review = FinanceReviewFixtureAuthority()
    acceptance = acceptance or _request()
    review.approve(scope, acceptance)
    context = _accept(migrated, scope, acceptance, review)
    request = QualifyProviderCashV1(
        uuid4(),
        OriginalProviderCashEvidenceV1(
            "fixture:archive-owner", "authenticated:original:1", "capture-v2", "c" * 64
        ),
    )
    original = AuthorizedProviderCashV1(
        scope,
        request.command_id,
        provider_cash_request_fingerprint(request),
        request.original_evidence,
        acceptance.configuration.transport,
        context.context_id,
        NOW + timedelta(hours=1),
        "capture",
        "provider-transaction:1",
        "historical-checkout:1",
        "succeeded",
        Money.of("123.45", Currency("NGN", 2)),
        NOW - timedelta(days=30),
        NOW + timedelta(hours=1),
    )
    return scope, other, request, OriginalFixtureAuthority(original), review


def _qualify(migrated, scope, request, authority):
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        row = qualify_provider_cash(
            db, scope=scope, request=request, authority=authority
        )
        db.commit()
        return row


def test_original_fact_without_fabricated_checkout_replay_end_and_new_admission(
    migrated,
):
    scope, _, request, authority, review = _fixture(migrated)
    row = _qualify(migrated, scope, request, authority)
    assert row.source_namespace.startswith("pcn1:")
    assert row.source_key == authority.original.transaction_id
    assert row.original.paid_at < row.original.verified_at
    assert _qualify(migrated, scope, request, authority) == row
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert (
            require_provider_cash_admission(
                db,
                scope=scope,
                qualification_id=row.qualification_id,
                authority=authority,
            )
            == row
        )
        end = RevokeMerchantContextV1(
            uuid4(), row.original.context_id, _request().evidence
        )
        review.approve(scope, end)
        revoke_merchant_context(db, scope=scope, command=end, authority=review)
        db.commit()
    assert _qualify(migrated, scope, request, authority) == row
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert (
            get_provider_cash_qualification(
                db, scope=scope, qualification_id=row.qualification_id
            )
            == row
        )
        with pytest.raises(Conflict):
            require_provider_cash_admission(
                db,
                scope=scope,
                qualification_id=row.qualification_id,
                authority=authority,
            )
    authority.deny = True
    with pytest.raises(Conflict):
        _qualify(migrated, scope, request, authority)


@pytest.mark.parametrize(
    "field,change",
    [
        ("scope", lambda original: replace(original.scope, tenant_id=uuid4())),
        ("request_fingerprint", lambda original: "d" * 64),
        (
            "original_evidence",
            lambda original: replace(original.original_evidence, sha256="d" * 64),
        ),
        ("context_id", lambda original: uuid4()),
        (
            "transport",
            lambda original: replace(original.transport, config_digest="d" * 64),
        ),
        ("paid_at", lambda original: None),
    ],
)
def test_forged_attestation_and_missing_original_fields_refuse(migrated, field, change):
    scope, _, request, authority, _ = _fixture(migrated)
    authority.original = replace(
        authority.original, **{field: change(authority.original)}
    )
    with pytest.raises(Conflict):
        _qualify(migrated, scope, request, authority)


def test_same_physical_cash_cannot_get_second_original_binding(migrated):
    scope, _, request, authority, _ = _fixture(migrated)
    _qualify(migrated, scope, request, authority)
    second = replace(
        request,
        command_id=uuid4(),
        original_evidence=replace(
            request.original_evidence, reference="original:second"
        ),
    )
    authority.original = replace(
        authority.original,
        command_id=second.command_id,
        request_fingerprint=provider_cash_request_fingerprint(second),
        original_evidence=second.original_evidence,
    )
    with pytest.raises(Conflict, match="already has"):
        _qualify(migrated, scope, second, authority)


def test_dirty_identity_map_never_supplies_original_cash(migrated):
    scope, _, request, authority, _ = _fixture(migrated)
    row = _qualify(migrated, scope, request, authority)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        loaded = db.execute(
            select(ProviderCashQualification).where(
                ProviderCashQualification.id == row.qualification_id
            )
        ).scalar_one()
        loaded.transaction_id = "dirty:forged"
        assert (
            get_provider_cash_qualification(
                db, scope=scope, qualification_id=row.qualification_id
            )
            == row
        )
        assert (
            require_provider_cash_admission(
                db,
                scope=scope,
                qualification_id=row.qualification_id,
                authority=authority,
            )
            == row
        )
        assert loaded in db.dirty
        db.rollback()


@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE mod_payments.provider_cash_qualifications SET transaction_id='moved'",
        "DELETE FROM mod_payments.provider_cash_qualifications",
        "TRUNCATE mod_payments.provider_cash_qualifications",
    ],
)
def test_owner_raw_mutation_and_truncate_refused(migrated, operation):
    scope, _, request, authority, _ = _fixture(migrated)
    _qualify(migrated, scope, request, authority)
    with pytest.raises(DBAPIError):
        with _owner(migrated, scope.tenant_id) as db:
            db.execute(text(operation))


def test_rls_and_catalog_integrity(migrated):
    scope, other, request, authority, _ = _fixture(migrated)
    row = _qualify(migrated, scope, request, authority)
    with _tenant_session(migrated[1], other.tenant_id) as db:
        assert db.execute(select(ProviderCashQualification.id)).all() == []
        with pytest.raises(Conflict):
            get_provider_cash_qualification(
                db, scope=other, qualification_id=row.qualification_id
            )
    with _owner(migrated, scope.tenant_id) as db:
        assert tuple(
            db.execute(
                text(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                    "WHERE oid='mod_payments.provider_cash_qualifications'::regclass"
                )
            ).one()
        ) == (True, True)
        assert (
            db.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE "
                    "tgrelid='mod_payments.provider_cash_qualifications'::regclass "
                    "AND NOT tgisinternal AND tgenabled='A'"
                )
            ).scalar_one()
            == 3
        )


@pytest.mark.parametrize("migrated", ["pm_0005_merchant_context"], indirect=True)
def test_actual_pm5_to_pm6_and_forward_only_history(migrated):
    from alembic import command

    # Genuine predecessor migration, not stamp or create_all. Existing PM5 rows
    # are populated before the successor creates its independent fact table.
    scope, _, request, authority, _ = _fixture(migrated)
    command.upgrade(_payments_config(), "heads")
    _qualify(migrated, scope, request, authority)
    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(_payments_config(), "pm_0005_merchant_context")


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_new_qualification_rejects_non_rc(migrated, isolation):
    scope, _, request, authority, _ = _fixture(migrated)
    with _owner(migrated, scope.tenant_id, isolation=isolation) as db:
        with pytest.raises(Conflict, match="READ WRITE READ COMMITTED"):
            qualify_provider_cash(db, scope=scope, request=request, authority=authority)


def test_final_insert_guard_checks_late_before_tuple(migrated):
    scope, _, request, authority, _ = _fixture(migrated)
    with _owner(migrated, scope.tenant_id) as db:
        db.execute(
            text(
                """CREATE FUNCTION mod_payments.test_late_cash_rewrite()
                RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                NEW.transaction_id := 'late:forged'; RETURN NEW; END $$"""
            )
        )
        db.execute(
            text(
                "CREATE TRIGGER zzzz_late_cash_rewrite BEFORE INSERT ON "
                "mod_payments.provider_cash_qualifications FOR EACH ROW "
                "EXECUTE FUNCTION mod_payments.test_late_cash_rewrite()"
            )
        )
    with pytest.raises(DBAPIError):
        _qualify(migrated, scope, request, authority)


def test_admission_transport_fence_blocks_revocation_until_outer_commit(migrated):
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from time import monotonic, sleep

    scope, _, request, authority, review = _fixture(migrated)
    row = _qualify(migrated, scope, request, authority)
    end = RevokeMerchantContextV1(uuid4(), row.original.context_id, _request().evidence)
    review.approve(scope, end)
    backend = Queue()

    def revoke():
        with _tenant_session(migrated[1], scope.tenant_id) as db:
            backend.put(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
            revoke_merchant_context(db, scope=scope, command=end, authority=review)
            db.commit()

    with _tenant_session(migrated[1], scope.tenant_id) as db:
        require_provider_cash_admission(
            db, scope=scope, qualification_id=row.qualification_id, authority=authority
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(revoke)
            pid = backend.get(timeout=10)
            try:
                with _owner(migrated, scope.tenant_id) as observer:
                    deadline = monotonic() + 10
                    blocked = False
                    while monotonic() < deadline:
                        observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                        blocked = bool(
                            observer.execute(
                                text("SELECT cardinality(pg_blocking_pids(:pid))"),
                                {"pid": pid},
                            ).scalar_one()
                        )
                        if blocked:
                            break
                        sleep(0.01)
                    assert blocked, "revocation did not wait on the admission fence"
                    assert not future.done()
            finally:
                db.commit()
            future.result(timeout=15)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        with pytest.raises(Conflict):
            require_provider_cash_admission(
                db,
                scope=scope,
                qualification_id=row.qualification_id,
                authority=authority,
            )


def test_parallel_distinct_commands_cannot_duplicate_physical_cash(migrated):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    scope, _, request, first, _ = _fixture(migrated)
    second_request = replace(request, command_id=uuid4())
    second = OriginalFixtureAuthority(
        replace(
            first.original,
            command_id=second_request.command_id,
            request_fingerprint=provider_cash_request_fingerprint(second_request),
        )
    )
    barrier = Barrier(2)

    def run(command, authority):
        barrier.wait(timeout=10)
        try:
            return _qualify(migrated, scope, command, authority)
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(run, request, first),
            pool.submit(run, second_request, second),
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sum(row is not None for row in results) == 1


def test_sql_python_codec_matches_utf8_and_decimal_canonical_capture(migrated):
    scope, _, request, authority, _ = _fixture(migrated)
    # UTF-8 byte lengths differ from character lengths; delimiters are data.
    authority.original = replace(
        authority.original,
        transaction_kind="capturé:名",
        transaction_id="clé:1:名",
        checkout_reference="checkout:é:名",
        money=Money.of("123.4500", Currency("NGN", 2)),
    )
    row = _qualify(migrated, scope, request, authority)
    with _owner(migrated, scope.tenant_id) as db:
        digest = db.execute(
            text(
                "SELECT mod_payments.provider_cash_digest("
                "'domain',ARRAY['a:b','c']) <> "
                "mod_payments.provider_cash_digest("
                "'domain',ARRAY['a','b:c'])"
            )
        ).scalar_one()
        assert digest is True
    assert row.original.money.amount == authority.original.money.amount
    assert _qualify(migrated, scope, request, authority) == row


def test_cross_tenant_raw_insert_is_refused(migrated):
    scope, other, request, authority, _ = _fixture(migrated)
    row = _qualify(migrated, scope, request, authority)
    table = ProviderCashQualification.__table__
    with _owner(migrated, scope.tenant_id) as db:
        values = dict(
            db.execute(select(*table.c).where(table.c.id == row.qualification_id))
            .mappings()
            .one()
        )
    values["id"] = uuid4()
    with _tenant_session(migrated[1], other.tenant_id) as db:
        with pytest.raises(DBAPIError):
            db.execute(table.insert().values(**values))
        db.rollback()


def test_same_command_concurrent_different_transport_conflicts_and_exact_replays(
    migrated,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    scope, _, request, first, review = _fixture(migrated)
    independent = _request()
    independent = replace(
        independent,
        configuration=replace(
            independent.configuration,
            transport=replace(
                independent.configuration.transport,
                installation_reference="installation:independent",
            ),
        ),
    )
    review.approve(scope, independent)
    context = _accept(migrated, scope, independent, review)
    second_request = replace(
        request,
        original_evidence=replace(
            request.original_evidence, reference="authenticated:independent-original"
        ),
    )
    second = OriginalFixtureAuthority(
        replace(
            first.original,
            request_fingerprint=provider_cash_request_fingerprint(second_request),
            original_evidence=second_request.original_evidence,
            context_id=context.context_id,
            transport=independent.configuration.transport,
            transaction_id="provider-transaction:independent",
        )
    )
    barrier = Barrier(2)

    def run(command, authority):
        barrier.wait(timeout=10)
        try:
            return _qualify(migrated, scope, command, authority)
        except Conflict:
            return None

    attempts = [(request, first), (second_request, second)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(run, command, authority) for command, authority in attempts
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sum(result is not None for result in results) == 1
    for (command, authority), result in zip(attempts, results, strict=True):
        if result is None:
            with pytest.raises(Conflict, match="command replay differs"):
                _qualify(migrated, scope, command, authority)
        else:
            assert _qualify(migrated, scope, command, authority) == result


def test_accepted_configuration_rotation_cannot_duplicate_or_rebind_same_cash(migrated):
    scope, _, request, original_authority, review = _fixture(migrated)
    original = _qualify(migrated, scope, request, original_authority)
    rotation_at = NOW + timedelta(hours=2)
    configuration = _request().configuration
    rotated = replace(
        _request(),
        configuration=replace(
            configuration,
            transport=replace(
                configuration.transport,
                config_revision_reference="revision:rotated",
                config_digest="d" * 64,
            ),
        ),
        effective_from=rotation_at,
        supersedes_context_id=original.original.context_id,
    )
    review.approve(scope, rotated)
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        replacement = accept_merchant_context(
            db, scope=scope, command=rotated, authority=review
        )
        db.commit()
    changed_request = replace(
        request,
        command_id=uuid4(),
        original_evidence=replace(
            request.original_evidence, reference="authenticated:rotated-verification"
        ),
    )
    changed = replace(
        original_authority.original,
        command_id=changed_request.command_id,
        request_fingerprint=provider_cash_request_fingerprint(changed_request),
        original_evidence=changed_request.original_evidence,
        transport=rotated.configuration.transport,
        context_id=replacement.context_id,
        verified_at=rotation_at,
        observed_at=rotation_at,
    )
    assert provider_cash_economic_identity(
        changed, replacement.configuration.physical_merchant_id
    ) == (original.source_namespace, original.source_key, original.source_version)
    with pytest.raises(Conflict, match="already has an original qualification"):
        _qualify(migrated, scope, changed_request, OriginalFixtureAuthority(changed))
    assert _qualify(migrated, scope, request, original_authority) == original
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert (
            get_provider_cash_qualification(
                db, scope=scope, qualification_id=original.qualification_id
            ).original.context_id
            == original.original.context_id
        )
        with pytest.raises(Conflict):
            require_provider_cash_admission(
                db,
                scope=scope,
                qualification_id=original.qualification_id,
                authority=original_authority,
            )


def test_expired_context_uses_fresh_server_time_but_preserves_historical_replay(
    migrated, monkeypatch
):
    import dotmac_payments.cash_qualification as cash_owner

    expiration = datetime.now(UTC) + timedelta(days=1)
    scope, _, request, authority, _ = _fixture(
        migrated, acceptance=replace(_request(), effective_until=expiration)
    )
    original = _qualify(migrated, scope, request, authority)

    class ServerClockAfterExpiration(datetime):
        @classmethod
        def now(cls, tz=None):
            # Move only the shared owner's server clock; no request/evidence
            # timestamp changes, no DB row mutation and no lifecycle overwrite.
            return expiration + timedelta(seconds=1)

    monkeypatch.setattr(cash_owner, "datetime", ServerClockAfterExpiration)
    assert _qualify(migrated, scope, request, authority) == original
    with _tenant_session(migrated[1], scope.tenant_id) as db:
        assert (
            get_provider_cash_qualification(
                db, scope=scope, qualification_id=original.qualification_id
            )
            == original
        )
        with pytest.raises(Conflict, match="applicable unended merchant context"):
            require_provider_cash_admission(
                db,
                scope=scope,
                qualification_id=original.qualification_id,
                authority=authority,
            )


def test_read_only_transaction_cannot_admit_qualified_provider_cash(migrated):
    from sqlalchemy import create_engine

    scope, _, request, authority, _ = _fixture(migrated)
    original = _qualify(migrated, scope, request, authority)
    engine = create_engine(migrated[0])
    try:
        with Session(engine) as db:
            # Set mode before any query, then establish the normal tenant scope.
            db.execute(text("SET TRANSACTION READ ONLY"))
            db.execute(
                text("SELECT set_config('app.current_tenant',:tenant,true)"),
                {"tenant": str(scope.tenant_id)},
            )
            assert (
                get_provider_cash_qualification(
                    db, scope=scope, qualification_id=original.qualification_id
                )
                == original
            )
            with pytest.raises(Conflict, match="READ WRITE READ COMMITTED"):
                require_provider_cash_admission(
                    db,
                    scope=scope,
                    qualification_id=original.qualification_id,
                    authority=authority,
                )
    finally:
        engine.dispose()
