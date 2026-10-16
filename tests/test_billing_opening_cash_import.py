"""Hosted migrated PostgreSQL canaries for immutable residual cash lifecycle.

These fixtures qualify trusted assembly ports; they are not production source
attestations, migration authorization or a runtime import composition.
"""

from __future__ import annotations

# SQL identifiers are fixed Billing plane/model table names; values are bound.
# ruff: noqa: S608
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, Inexact, Rounded, localcontext
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from dotmac_billing import models
from dotmac_billing import opening_queries as q
from dotmac_billing import service as s
from dotmac_billing._receivable_backing import ReceivableBacking
from dotmac_billing.commands import (
    ApplyAvailableCreditCommand,
    DeallocationCommand,
    ReallocationCommand,
    ReversePostingGroupCommand,
)
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.opening_authority import (
    OpeningCashRefundCorrectionVerificationV1,
    OpeningCashRefundVerificationV1,
    OpeningImportReversalVerificationV1,
    OpeningImportVerificationV1,
    opening_cash_command_digest,
)
from dotmac_billing.opening_commands import (
    CorrectOpeningCashRefundV1,
    ImportOpeningAccountV1,
    OpeningCashMovementEvidenceV1,
    RecordOpeningCashRefundV1,
    ReverseCashCreditDrawV1,
    ReverseOpeningAccountImportV1,
)
from dotmac_billing.opening_contracts import (
    OpeningPopulationCountsV1,
    OpeningPreviewRequestV1,
)
from dotmac_billing.opening_validation import (
    opening_manifest_fingerprint,
    preview_opening_account_import,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command as alembic_command
from tests import billing_opening_window_fixtures as window_fixture
from tests import test_billing_isolation as b
from tests import test_billing_non_cash_credit as runtime
from tests import test_billing_withholding_persistence as predecessor
from tests.unit import test_billing_opening_manifest as preflight

REVISION = "bi_0007_opening_cash"


class _OpeningScratch(tuple):
    """Per-test migrated database and its explicit ephemeral role custody."""

    def __new__(cls, urls):
        instance = super().__new__(cls, urls)
        instance.principals = {}
        instance.windows = {}
        return instance


@contextmanager
def _opening_scratch(urls, planes=("tenant", "platform")):
    scratch = _OpeningScratch(urls)
    with ExitStack() as stack:
        for plane in planes:
            scratch.principals[plane] = stack.enter_context(
                window_fixture._principal(scratch, plane)
            )
        yield scratch


@pytest.fixture
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        with _opening_scratch(urls) as scoped:
            yield scoped


@contextmanager
def _session(scratch, url, scope):
    """Privileged fixture transport; actual ordinary role owns caller work.

    The ordinary LOGIN has no membership in the opening principal. Only this
    test harness can select the restricted role, without a second transaction.
    Role-boundary attacks deliberately use b._session and the real LOGIN.
    """
    plane = "tenant" if isinstance(scope, TenantScope) else "platform"
    ordinary = make_url(url).username
    database = make_url(url).database
    engine = create_engine(b._url_for(b._superuser_url(), database))
    with Session(engine) as session:

        @event.listens_for(session, "after_begin")
        def ordinary_role(_session, _transaction, connection):
            if _transaction.nested:
                return
            connection.execute(text(f'SET LOCAL ROLE "{ordinary}"'))
            if isinstance(scope, TenantScope):
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(scope.tenant_id)},
                )

        session.info["opening_scratch"] = scratch
        session.info["opening_principal"] = scratch.principals[plane]
        session.info["opening_ordinary_role"] = ordinary
        assert session.scalar(text("SELECT current_user")) == ordinary
        try:
            yield session
        finally:
            session.close()
            engine.dispose()


@contextmanager
def _as_opening_principal(session):
    """Select actual restricted current_user inside the unchanged caller root."""
    ordinary = session.info["opening_ordinary_role"]
    principal = session.info["opening_principal"][0]
    assert session.scalar(text("SELECT current_user")) == ordinary
    root = session.get_transaction()
    connection = session.connection().connection.driver_connection
    session.execute(text(f'SET LOCAL ROLE "{principal}"'))
    assert session.scalar(text("SELECT current_user")) == principal
    try:
        yield
    except BaseException as original:
        # A PostgreSQL-aborted root cannot execute cleanup SQL. Preserve its
        # original error; caller rollback resets LOCAL and after_begin restores
        # the ordinary role. Never finish the caller transaction here.
        try:
            if session.is_active and connection.info.transaction_status != 3:
                session.execute(text(f'SET LOCAL ROLE "{ordinary}"'))
        except BaseException as cleanup:
            raise original from cleanup
        raise
    else:
        assert session.get_transaction() is root
        session.execute(text(f'SET LOCAL ROLE "{ordinary}"'))
        assert session.scalar(text("SELECT current_user")) == ordinary


def _prepare_window(session, scope, command):
    """Explicitly freeze the entire scenario manifest before admission."""
    scratch = session.info["opening_scratch"]
    if scope not in scratch.windows:
        scratch.windows[scope] = window_fixture._provision(
            scratch, scope, session.info["opening_principal"], command
        )
    return scratch.windows[scope]


def _import_command(session, *, scope, command, verifier=None, version=1):
    """Actual restricted current_user, with the original caller transaction."""
    with _as_opening_principal(session):
        operation = (
            s.import_opening_account if version == 1 else s.import_opening_account_v2
        )
        return operation(session, scope=scope, command=command, verifier=verifier)


def _seal_window(session, scope):
    """Close complete opening custody before ordinary lifecycle operations."""
    window = session.info["opening_scratch"].windows[scope]
    with _as_opening_principal(session):
        return s.seal_opening_window(
            session,
            scope=scope,
            command=window_fixture._seal_command(session, scope, window),
            verifier=window_fixture.TrustedWindowVerifier(),
        )


class TrustedVerifier:
    def __init__(self, mutate=None):
        self.mutate = mutate
        self.calls = []

    def verify_import(self, request):
        self.calls.append(request)
        cmd = request.command
        value = OpeningImportVerificationV1(
            "billing.opening.import.v1",
            cmd.scope,
            request.command_digest,
            request.manifest_fingerprint,
            cmd.partition,
            cmd.target,
            cmd.manifest.capture.installation_id,
            cmd.migration_authority_ref,
            cmd.manifest.capture.source_authority_ref,
            "source-proof:1",
            "cash-proof:1",
            "migration-proof:1",
        )
        return self.mutate(value) if self.mutate else value

    def verify_import_reversal(self, request):
        self.calls.append(request)
        cmd = request.command
        return OpeningImportReversalVerificationV1(
            "billing.opening.import.reverse.v1",
            cmd.scope,
            request.command_digest,
            cmd.receipt_id,
            request.receipt_evidence_digest,
            request.original_posting_group_id,
            request.billing_account_id,
            request.currency,
            request.precision,
            cmd.migration_authority_ref,
            "fresh-migration-reversal-proof",
        )

    def verify_refund(self, request):
        self.calls.append(request)
        value = OpeningCashRefundVerificationV1(
            "billing.opening.cash.refund.v1",
            request.command_digest,
            request.subject,
            "actual-refund-outcome-proof",
            "refund-authority-proof",
        )
        return self.mutate(value) if self.mutate else value

    def verify_refund_correction(self, request):
        self.calls.append(request)
        return OpeningCashRefundCorrectionVerificationV1(
            "billing.opening.cash.refund.correct.v1",
            request.command_digest,
            request.subject,
            request.refund_effect_id,
            request.refund_evidence_digest,
            "independent-refund-correction-outcome-proof",
            "correction-authority-proof",
        )


def _models(plane):
    prefix = "" if plane == "tenant" else "Platform"
    return {
        name: getattr(
            models, prefix + ("BillingDocument" if name == "Document" else name)
        )
        for name in (
            "OpeningImportReceipt",
            "OpeningCashLot",
            "OpeningCashEffect",
            "CashSourceClaim",
            "ConfirmedSettlement",
            "PostingGroup",
            "PostingEffect",
            "AccountingFact",
            "AllocationEffect",
            "ReceivablePositionFact",
            "BillingAccount",
            "Document",
        )
    }


def _new_account(session, scope, *, precision=2):
    account = s.create_billing_account(
        session,
        scope=scope,
        external_account_ref=str(uuid4()),
        currency="NGN",
        minor_units=precision,
    )
    session.commit()
    return account.id


def _command(
    session, scope, account_id, *, amounts=(6000,), precision=2, source_key=None
):
    candidate = preflight._candidate()
    target = q.get_opening_cash_target(
        session, scope=scope, billing_account_id=account_id
    )
    partition = replace(
        candidate.partition,
        account_id=account_id,
        precision=precision,
        source_account_key=f"legacy-account:{account_id}",
    )
    lots, dispositions, population = [], [], []
    for index, amount in enumerate(amounts):
        base = candidate.manifest.cash_lots[0]
        identity = replace(
            base.source_identity,
            key=f"legacy-payment:{source_key or account_id}:{index}",
        )
        consumed = 40 * 10**precision
        lot = replace(
            base,
            lot_id=f"legacy-lot:{account_id}:{index}",
            source_identity=identity,
            partition=partition,
            source_system="legacy-payments",
            source_settlement_key=source_key or f"{account_id}:{index}",
            gross_minor=amount + consumed,
            residual_minor=amount,
            confirmation=replace(
                base.confirmation,
                cash_amount_minor=amount + consumed,
                precision=precision,
            ),
            consumptions=(
                replace(
                    base.consumptions[0],
                    amount_minor=consumed,
                    reference=f"legacy-allocation:{account_id}:{index}",
                ),
            ),
        )
        population.append(identity)
        lots.append(lot)
        dispositions.append(
            replace(
                candidate.manifest.dispositions[0],
                identity=identity,
                partition=partition,
                residual_minor=amount,
            )
        )
    manifest = replace(
        candidate.manifest,
        scope=scope,
        population=tuple(population),
        dispositions=tuple(dispositions),
        cash_lots=tuple(lots),
        counts=OpeningPopulationCountsV1(len(lots), len(lots), 0, 0),
        controls=(
            replace(
                candidate.manifest.controls[0],
                partition=partition,
                available_cash_credit_minor=sum(amounts),
            ),
        ),
    )
    return ImportOpeningAccountV1(
        scope,
        manifest,
        partition,
        target,
        "migration-authority:qualified",
        f"opening:{account_id}",
        preflight.NOW,
    )


def _import(session, scope, account_id, **kwargs):
    cmd = _command(session, scope, account_id, **kwargs)
    _prepare_window(session, scope, cmd)
    receipt = _import_command(
        session, scope=scope, command=cmd, verifier=TrustedVerifier()
    )
    return cmd, receipt


def _position(session, scope, account_id):
    return s.rebuild_receivable_position(
        session,
        scope=scope,
        billing_account_id=account_id,
        currency="NGN",
        minor_units=2,
    )


def _refund(scope, lot_id, amount="10.00"):
    return RecordOpeningCashRefundV1(
        scope,
        lot_id,
        b._money(amount),
        OpeningCashMovementEvidenceV1(
            "payments", str(uuid4()), "1", "b" * 64, "confirmed"
        ),
        "refund-authority:1",
        str(uuid4()),
        preflight.NOW,
    )


def _inverse(scope, draw_id, amount):
    return ReverseCashCreditDrawV1(
        scope,
        draw_id,
        b._money(amount),
        "draw-reversal-authority:1",
        str(uuid4()),
        preflight.NOW,
    )


def _invoice(session, scope, account_id, *, suffix=None):
    obligation = s.accept_rated_obligation(
        session,
        scope=scope,
        command=b._obligation_command(
            account_id, scope=scope, source_fact_id=suffix or str(uuid4())
        ),
        accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
    )
    draft = s.create_draft_document(
        session, scope=scope, command=b._draft_command(obligation.id)
    )
    return s.issue_document(
        session, scope=scope, command=b._issue_command(draft.id), numbering=b.NUMBERING
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_pristine_import_admits_only_verified_residual_with_distinct_accounting(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        cmd, receipt = _import(session, scope, account_id)
        assert receipt.total_residual == b._money("60.00")
        assert receipt.manifest_fingerprint == opening_manifest_fingerprint(
            cmd.manifest
        )
        assert receipt.request_digest == opening_cash_command_digest(cmd)
        assert receipt.target == cmd.target
        assert receipt.target.posting_watermark == "none"
        assert len(receipt.lot_ids) == 1
        assert _position(session, scope, account_id).available_credit == Decimal(
            "60.00"
        )
        selected = _models(plane)
        assert (
            session.scalar(
                select(func.count())
                .select_from(selected["ConfirmedSettlement"])
                .where(selected["ConfirmedSettlement"].billing_account_id == account_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(selected["Document"])
                .where(selected["Document"].billing_account_id == account_id)
            )
            == 0
        )
        fact = q.get_accounting_fact_v4(
            session, scope=scope, posting_group_id=receipt.posting_group_id
        )
        assert fact.fact_version == 4
        assert fact.effect_kind == "opening_cash_imported"
        assert fact.import_receipt == receipt
        assert fact.settlement_allocations == ()
        assert len(fact.opening_cash_effects) == 1
        assert tuple(
            (entry.sequence, entry.allocation_effect_id, entry.opening_effect_id)
            for entry in fact.cash_source_order
        ) == ((1, None, fact.opening_cash_effects[0].effect_id),)
        assert fact.opening_cash_effects[0].effect_kind == "recognition"
        session.commit()
    with _session(scratch, url, scope) as session:
        assert (
            q.get_opening_import_receipt(
                session, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )
        lot = q.get_opening_cash_lot(session, scope=scope, lot_id=receipt.lot_ids[0])
        assert lot.initial_amount == b._money("60.00")
        assert lot.source.gross_minor == 10000
        assert lot.source.consumptions[0].amount_minor == 4000
        assert (
            lot.source.source_settlement_key
            == cmd.manifest.cash_lots[0].source_settlement_key
        )
        assert q.get_opening_cash_lot_position(
            session, scope=scope, lot_id=lot.lot_id
        ).remaining == b._money("60.00")
        _prepare_window(session, scope, cmd)
        assert (
            _import_command(
                session, scope=scope, command=cmd, verifier=TrustedVerifier()
            )
            == receipt
        )


def _counts(session, plane, account_id):
    tables = _models(plane)
    counts = {}
    for name, model in tables.items():
        statement = select(func.count()).select_from(model)
        if name == "BillingAccount":
            statement = statement.where(model.id == account_id)
        elif name == "AccountingFact":
            group = tables["PostingGroup"]
            statement = statement.join(group, model.posting_group_id == group.id).where(
                group.billing_account_id == account_id
            )
        else:
            statement = statement.where(model.billing_account_id == account_id)
        counts[name] = session.scalar(statement)
    return counts


def _retry_count(session, plane, operation, key):
    ledger = b.IdempotencyRecord if plane == "tenant" else b.PlatformIdempotencyRecord
    return session.scalar(
        select(func.count())
        .select_from(ledger)
        .where(ledger.scope == operation, ledger.key == key)
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_preview_and_matching_caller_digests_never_authorize_import(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        cmd = _command(session, scope, account_id)
        preview_request = OpeningPreviewRequestV1(
            cmd.manifest, cmd.partition, cmd.target, cmd.migration_authority_ref
        )
        preview = preview_opening_account_import(
            preview_request, verifier=preflight._Verifier()
        )
        assert preview.admissible
        before = _counts(session, plane, account_id)

        class PreviewOnly:
            def verify_import(self, request):
                return preview

        for verifier in (None, PreviewOnly()):
            with pytest.raises(BillingRuleViolation):
                _prepare_window(session, scope, cmd)
                _import_command(session, scope=scope, command=cmd, verifier=verifier)
            assert _counts(session, plane, account_id) == before


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "field",
    [
        "command_digest",
        "manifest_fingerprint",
        "scope",
        "partition",
        "target",
        "installation_id",
        "migration_authority_ref",
        "source_authority_ref",
    ],
)
def test_import_verifier_is_bound_to_full_exact_command_before_replay(
    scratch, plane, field
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        cmd, receipt = _import(session, scope, account_id)
        session.commit()

        def wrong(value):
            changes = {
                "command_digest": "f" * 64,
                "manifest_fingerprint": "e" * 64,
                "scope": TenantScope(uuid4()),
                "partition": replace(value.partition, source_account_key="wrong"),
                "target": replace(value.target, posting_watermark="wrong"),
                "installation_id": "wrong",
                "migration_authority_ref": "wrong",
                "source_authority_ref": "wrong",
            }
            return replace(value, **{field: changes[field]})

        before = _counts(session, plane, account_id)
        with pytest.raises(BillingRuleViolation):
            _prepare_window(session, scope, cmd)
            _import_command(
                session, scope=scope, command=cmd, verifier=TrustedVerifier(wrong)
            )
        assert _counts(session, plane, account_id) == before
        assert (
            q.get_opening_import_receipt(
                session, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_import_rejects_stale_target_and_changed_content_without_partial_artifacts(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        cmd = _command(session, scope, account_id)
        for target in (
            replace(cmd.target, expected_position_fingerprint="f" * 64),
            replace(cmd.target, posting_watermark=str(uuid4())),
        ):
            before = _counts(session, plane, account_id)
            with pytest.raises((BillingRuleViolation, BillingConflict)):
                _prepare_window(session, scope, replace(cmd, target=target))
                _import_command(
                    session,
                    scope=scope,
                    command=replace(cmd, target=target),
                    verifier=TrustedVerifier(),
                )
            assert _counts(session, plane, account_id) == before
        _prepare_window(session, scope, cmd)
        receipt = _import_command(
            session, scope=scope, command=cmd, verifier=TrustedVerifier()
        )
        session.commit()
        with pytest.raises((IdempotencyConflict, BillingConflict)):
            _prepare_window(
                session, scope, replace(cmd, occurred_at=b.NOW + b.timedelta(seconds=1))
            )
            _import_command(
                session,
                scope=scope,
                command=replace(cmd, occurred_at=b.NOW + b.timedelta(seconds=1)),
                verifier=TrustedVerifier(),
            )
        assert (
            q.get_opening_import_receipt(
                session, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_live_callback_and_import_share_one_permanent_source_claim(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    key = str(uuid4())
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id, source_key=key)
        session.commit()
        offered = replace(
            b._settlement_command(
                account_id, scope=scope, source_key=key, amount="100.00"
            ),
            source_system="legacy-payments",
        )
        with pytest.raises(BillingConflict):
            s.accept_settlement(
                session,
                scope=scope,
                command=offered,
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
        claim = _models(plane)["CashSourceClaim"]
        row = session.scalars(
            select(claim).where(claim.billing_account_id == account_id)
        ).one()
        assert row.settlement_id is None
        assert row.opening_cash_lot_id == receipt.lot_ids[0]
        assert _position(session, scope, account_id).available_credit == Decimal(
            "60.00"
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_import_and_live_callback_race_cannot_double_recognize_cash(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    key = str(uuid4())
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        cmd = _command(session, scope, account_id, source_key=key)
        _prepare_window(session, scope, cmd)
        session.commit()
    offered = replace(
        b._settlement_command(account_id, scope=scope, source_key=key, amount="100.00"),
        source_system="legacy-payments",
    )
    barrier, successes, errors = Barrier(2), [], []

    def run(kind):
        try:
            with _session(scratch, url, scope) as session:
                barrier.wait(timeout=20)
                if kind == "opening":
                    _prepare_window(session, scope, cmd)
                    _import_command(
                        session, scope=scope, command=cmd, verifier=TrustedVerifier()
                    )
                else:
                    s.accept_settlement(
                        session,
                        scope=scope,
                        command=offered,
                        accepted_confirmation_evidence=frozenset({"provider_settled"}),
                    )
                session.commit()
                successes.append(kind)
        except Exception as error:
            errors.append(error)

    threads = [Thread(target=run, args=(kind,)) for kind in ("opening", "live")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert len(successes) == len(errors) == 1
    assert isinstance(
        errors[0], BillingConflict | BillingRuleViolation | IdempotencyConflict
    )
    with _session(scratch, url, scope) as session:
        tables = _models(plane)
        claims = session.scalars(
            select(tables["CashSourceClaim"]).where(
                tables["CashSourceClaim"].billing_account_id == account_id
            )
        ).all()
        assert len(claims) == 1
        assert (claims[0].settlement_id is None) != (
            claims[0].opening_cash_lot_id is None
        )
        assert _position(session, scope, account_id).available_credit == Decimal(
            "60" if successes[0] == "opening" else "100"
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_caller_rollback_removes_receipts_claims_lots_effects_and_outbox(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        before = _counts(session, plane, account_id)
        cmd, receipt = _import(session, scope, account_id)
        assert _counts(session, plane, account_id) != before
        assert (
            _retry_count(
                session, plane, "billing.opening.import.v1", cmd.idempotency_key
            )
            == 1
        )
        session.rollback()
        assert (
            _retry_count(
                session, plane, "billing.opening.import.v1", cmd.idempotency_key
            )
            == 0
        )
        assert _counts(session, plane, account_id) == before
        assert _position(session, scope, account_id).available_credit == 0
        with pytest.raises(BillingRuleViolation):
            q.get_opening_import_receipt(
                session, scope=scope, receipt_id=receipt.receipt_id
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_mixed_cash_draw_and_partial_full_inverse_keep_both_real_source_lineages(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        _seal_window(session, scope)
        ordinary = s.accept_settlement(
            session,
            scope=scope,
            command=b._settlement_command(
                account_id, scope=scope, source_key=str(uuid4()), amount="40.00"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        invoice = _invoice(session, scope, account_id)
        draw_command = ApplyAvailableCreditCommand(
            account_id, invoice.id, b._money("100.00"), preflight.NOW, str(uuid4())
        )
        draw = s.apply_available_credit(session, scope=scope, command=draw_command)
        session.commit()
        original = q.get_accounting_fact_v4(
            session, scope=scope, posting_group_id=draw.id
        )
        assert original.effect_kind == "opening_cash_applied"
        assert (
            len(original.settlement_allocations)
            == len(original.opening_cash_effects)
            == 1
        )
        assert (
            original.settlement_allocations[0].allocation.settlement_id == ordinary.id
        )
        assert original.opening_cash_effects[0].lot_id == receipt.lot_ids[0]
        assert [item.sequence for item in original.cash_source_order] == [1, 2]
        assert {
            item.allocation_effect_id
            for item in original.cash_source_order
            if item.allocation_effect_id
        } == {original.settlement_allocations[0].allocation_effect_id}
        assert {
            item.opening_effect_id
            for item in original.cash_source_order
            if item.opening_effect_id
        } == {original.opening_cash_effects[0].effect_id}
        assert (
            s.apply_available_credit(session, scope=scope, command=draw_command).id
            == draw.id
        )
        assert (
            q.get_accounting_fact_v4(session, scope=scope, posting_group_id=draw.id)
            == original
        )
        assert _position(session, scope, account_id).available_credit == 0
        assert _position(session, scope, account_id).collectible_receivable == Decimal(
            "20"
        )
        partial = _inverse(scope, draw.id, "50.00")
        inverse = s.reverse_cash_credit_draw(session, scope=scope, command=partial)
        assert (
            s.reverse_cash_credit_draw(session, scope=scope, command=partial).id
            == inverse.id
        )
        assert _position(session, scope, account_id).available_credit == Decimal("50")
        assert _position(session, scope, account_id).collectible_receivable == Decimal(
            "70"
        )
        full = s.reverse_cash_credit_draw(
            session, scope=scope, command=_inverse(scope, draw.id, "50.00")
        )
        assert _position(session, scope, account_id).available_credit == Decimal("100")
        assert _position(session, scope, account_id).collectible_receivable == Decimal(
            "120"
        )
        assert q.get_opening_cash_lot_position(
            session, scope=scope, lot_id=receipt.lot_ids[0]
        ).remaining == b._money("60.00")
        facts = [
            q.get_accounting_fact_v4(session, scope=scope, posting_group_id=item.id)
            for item in (inverse, full)
        ]
        opening_inverses = [
            effect for fact in facts for effect in fact.opening_cash_effects
        ]
        assert sum(
            (effect.amount.amount for effect in opening_inverses), Decimal(0)
        ) == Decimal("60")
        assert all(
            effect.original_effect_id == original.opening_cash_effects[0].effect_id
            for effect in opening_inverses
        )
        assert all(fact.corrects_posting_group_id == draw.id for fact in facts)
        before = _counts(session, plane, account_id)
        with pytest.raises(BillingRuleViolation):
            s.reverse_cash_credit_draw(
                session, scope=scope, command=_inverse(scope, draw.id, "0.01")
            )
        assert _counts(session, plane, account_id) == before
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_legacy_reversal_deallocation_and_reallocation_cannot_split_mixed_cash_draw(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _import(session, scope, account_id)
        ordinary = s.accept_settlement(
            session,
            scope=scope,
            command=b._settlement_command(
                account_id, scope=scope, source_key=str(uuid4()), amount="40.00"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        invoice, other = (
            _invoice(session, scope, account_id),
            _invoice(session, scope, account_id),
        )
        draw = s.apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account_id, invoice.id, b._money("100"), preflight.NOW, str(uuid4())
            ),
        )
        accounting = q.get_accounting_fact_v4(
            session, scope=scope, posting_group_id=draw.id
        )
        allocation_id = accounting.settlement_allocations[0].allocation_effect_id
        attempts = (
            lambda: s.reverse_posting_group(
                session,
                scope=scope,
                command=ReversePostingGroupCommand(
                    draw.id, preflight.NOW, str(uuid4())
                ),
            ),
            lambda: s.deallocate_settlement(
                session,
                scope=scope,
                command=DeallocationCommand(
                    allocation_id, b._money("1"), preflight.NOW, str(uuid4())
                ),
            ),
            lambda: s.reallocate_settlement(
                session,
                scope=scope,
                command=ReallocationCommand(
                    ordinary.id,
                    invoice.id,
                    other.id,
                    b._money("1"),
                    preflight.NOW,
                    str(uuid4()),
                ),
            ),
        )
        before = _counts(session, plane, account_id)
        for attempt in attempts:
            with pytest.raises(BillingRuleViolation):
                attempt()
            assert _counts(session, plane, account_id) == before
        assert (
            q.get_accounting_fact_v4(session, scope=scope, posting_group_id=draw.id)
            == accounting
        )
        session.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_unused_refund_requires_evidence_and_full_qualified_correction(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        _seal_window(session, scope)
        offered = _refund(scope, receipt.lot_ids[0], "20.00")
        with pytest.raises(BillingRuleViolation):
            s.record_opening_cash_refund(session, scope=scope, command=offered)
        effect = s.record_opening_cash_refund(
            session, scope=scope, command=offered, verifier=TrustedVerifier()
        )
        assert effect.effect_kind == "refund"
        assert effect.movement_evidence == offered.evidence
        assert (
            s.record_opening_cash_refund(
                session, scope=scope, command=offered, verifier=TrustedVerifier()
            )
            == effect
        )
        assert _position(session, scope, account_id).available_credit == Decimal("40")
        with pytest.raises(BillingRuleViolation):
            s.record_opening_cash_refund(
                session,
                scope=scope,
                command=_refund(scope, receipt.lot_ids[0], "40.01"),
                verifier=TrustedVerifier(),
            )
        correction = CorrectOpeningCashRefundV1(
            scope,
            effect.effect_id,
            b._money("20.00"),
            OpeningCashMovementEvidenceV1(
                "payments-correction", str(uuid4()), "2", "c" * 64, "corrected"
            ),
            "independent-correction-authority",
            str(uuid4()),
            preflight.NOW,
        )
        with pytest.raises(BillingRuleViolation):
            s.correct_opening_cash_refund(session, scope=scope, command=correction)
        with pytest.raises(BillingRuleViolation):
            s.correct_opening_cash_refund(
                session,
                scope=scope,
                command=replace(correction, amount=b._money("10.00")),
                verifier=TrustedVerifier(),
            )
        inverse = s.correct_opening_cash_refund(
            session, scope=scope, command=correction, verifier=TrustedVerifier()
        )
        assert inverse.effect_kind == "refund_correction"
        assert inverse.original_effect_id == effect.effect_id
        assert inverse.amount == effect.amount
        assert inverse.movement_evidence == correction.evidence
        assert _position(session, scope, account_id).available_credit == Decimal("60")
        assert (
            s.correct_opening_cash_refund(
                session, scope=scope, command=correction, verifier=TrustedVerifier()
            )
            == inverse
        )
        with pytest.raises((BillingRuleViolation, BillingConflict)):
            s.correct_opening_cash_refund(
                session,
                scope=scope,
                command=replace(correction, idempotency_key=str(uuid4())),
                verifier=TrustedVerifier(),
            )
        session.commit()
        assert (
            q.get_opening_cash_effect(session, scope=scope, effect_id=effect.effect_id)
            == effect
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_import_unwind_requires_zero_net_uses_fresh_authority_and_never_frees_source(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        cmd, receipt = _import(session, scope, account_id)
        _seal_window(session, scope)
        refund = s.record_opening_cash_refund(
            session,
            scope=scope,
            command=_refund(scope, receipt.lot_ids[0]),
            verifier=TrustedVerifier(),
        )
        unwind = ReverseOpeningAccountImportV1(
            scope,
            receipt.receipt_id,
            "fresh-migration-authority",
            str(uuid4()),
            preflight.NOW,
        )
        with pytest.raises(BillingRuleViolation):
            s.reverse_opening_account_import(
                session, scope=scope, command=unwind, verifier=TrustedVerifier()
            )
        correction = CorrectOpeningCashRefundV1(
            scope,
            refund.effect_id,
            refund.amount,
            OpeningCashMovementEvidenceV1(
                "payments", str(uuid4()), "2", "c" * 64, "corrected"
            ),
            "correction-authority",
            str(uuid4()),
            preflight.NOW,
        )
        s.correct_opening_cash_refund(
            session, scope=scope, command=correction, verifier=TrustedVerifier()
        )
        with pytest.raises(BillingRuleViolation):
            s.reverse_opening_account_import(session, scope=scope, command=unwind)
        inverse = s.reverse_opening_account_import(
            session, scope=scope, command=unwind, verifier=TrustedVerifier()
        )
        assert inverse.receipt_kind == "unwind"
        assert inverse.original_receipt_id == receipt.receipt_id
        assert inverse.total_residual == receipt.total_residual
        assert (
            s.reverse_opening_account_import(
                session, scope=scope, command=unwind, verifier=TrustedVerifier()
            )
            == inverse
        )
        assert _position(session, scope, account_id).available_credit == 0
        assert q.get_opening_cash_lot_position(
            session, scope=scope, lot_id=receipt.lot_ids[0]
        ).unwound
        _prepare_window(session, scope, cmd)
        assert (
            _import_command(
                session, scope=scope, command=cmd, verifier=TrustedVerifier()
            )
            == receipt
        )
        assert _position(session, scope, account_id).available_credit == 0
        source = cmd.manifest.cash_lots[0]
        callback = replace(
            b._settlement_command(
                account_id,
                scope=scope,
                source_key=source.source_settlement_key,
                amount="100.00",
            ),
            source_system=source.source_system,
        )
        with pytest.raises(BillingConflict):
            s.accept_settlement(
                session,
                scope=scope,
                command=callback,
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
        with pytest.raises((BillingRuleViolation, BillingConflict)):
            _prepare_window(session, scope, replace(cmd, idempotency_key=str(uuid4())))
            _import_command(
                session,
                scope=scope,
                command=replace(cmd, idempotency_key=str(uuid4())),
                verifier=TrustedVerifier(),
            )
        claim = _models(plane)["CashSourceClaim"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(claim)
                .where(claim.opening_cash_lot_id == receipt.lot_ids[0])
            )
            == 1
        )
        assert (
            q.get_opening_import_receipt(
                session, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_failure_after_opening_artifacts_and_real_outbox_rolls_back_only_operation(
    scratch, plane, monkeypatch
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        pending = s.create_billing_account(
            session,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        pending_id = pending.id
        cmd = _command(session, scope, account_id)
        before = _counts(session, plane, account_id)
        outbox = b.OutboxEvent if plane == "tenant" else b.PlatformOutboxEvent
        before_events = session.scalar(select(func.count()).select_from(outbox))
        real_emit = s._emit
        observed = []

        def fail_after_real_event(*args, **kwargs):
            real_emit(*args, **kwargs)
            session.flush()
            tables = _models(plane)
            counts = {
                key: session.scalar(
                    select(func.count())
                    .select_from(tables[key])
                    .where(tables[key].billing_account_id == account_id)
                )
                for key in (
                    "OpeningImportReceipt",
                    "OpeningCashLot",
                    "OpeningCashEffect",
                    "CashSourceClaim",
                )
            }
            assert all(counts.values()), counts
            assert (
                session.scalar(select(func.count()).select_from(outbox)) > before_events
            )
            observed.append(counts)
            raise RuntimeError("fault after opening artifacts and outbox")

        monkeypatch.setattr(s, "_emit", fail_after_real_event)
        with pytest.raises(
            RuntimeError, match="fault after opening artifacts and outbox"
        ):
            _prepare_window(session, scope, cmd)
            _import_command(
                session, scope=scope, command=cmd, verifier=TrustedVerifier()
            )
        assert observed
        assert _counts(session, plane, account_id) == before
        assert (
            _retry_count(
                session, plane, "billing.opening.import.v1", cmd.idempotency_key
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(outbox)) == before_events
        assert session.get(_models(plane)["BillingAccount"], pending_id) is not None
        monkeypatch.setattr(s, "_emit", real_emit)
        _prepare_window(session, scope, cmd)
        assert _import_command(
            session, scope=scope, command=cmd, verifier=TrustedVerifier()
        ).total_residual == b._money("60")
        session.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_opening_queries_read_persisted_immutable_evidence_without_autoflush(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        session.commit()
        lot = q.get_opening_cash_lot(session, scope=scope, lot_id=receipt.lot_ids[0])
        row = session.get(_models(plane)["OpeningCashLot"], lot.lot_id)
        row.evidence = {"corrupt": "dirty-identity-map-only"}
        row.evidence_digest = "f" * 64
        assert row in session.dirty
        assert q.get_opening_cash_lot(session, scope=scope, lot_id=lot.lot_id) == lot
        assert (
            q.get_opening_import_receipt(
                session, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )
        assert row in session.dirty
        session.rollback()
        assert q.get_opening_cash_lot(session, scope=scope, lot_id=lot.lot_id) == lot


def test_tenant_opening_evidence_isolated_and_both_role_boundaries_enforced(scratch):
    scope, url = runtime._scope_url(scratch, "tenant")
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        session.commit()
    other = TenantScope(b._create_tenants(scratch[0], 1)[0])
    with b._session(url, other) as session:
        for query, values in (
            (q.get_opening_import_receipt, {"receipt_id": receipt.receipt_id}),
            (q.get_opening_cash_lot, {"lot_id": receipt.lot_ids[0]}),
            (q.get_accounting_fact_v4, {"posting_group_id": receipt.posting_group_id}),
        ):
            with pytest.raises(BillingRuleViolation):
                query(session, scope=other, **values)
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        for plane in ("tenant", "platform"):
            for name in ("OpeningImportReceipt", "OpeningCashLot", "OpeningCashEffect"):
                table = _models(plane)[name].__tablename__
                denied = "platform_api" if plane == "tenant" else "app_user"
                assert not connection.scalar(
                    text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
                    {"role": denied, "table": f"mod_billing.{table}"},
                )
                if plane == "tenant":
                    assert connection.scalar(
                        text(
                            "SELECT relrowsecurity AND relforcerowsecurity "
                            "FROM pg_class "
                            "WHERE oid=CAST(:table AS regclass)"
                        ),
                        {"table": f"mod_billing.{table}"},
                    )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_owner_and_online_role_cannot_amend_or_delete_opening_history(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        session.commit()
    engine = create_engine(scratch[0])
    for name in (
        "OpeningImportReceipt",
        "OpeningCashLot",
        "OpeningCashEffect",
        "CashSourceClaim",
    ):
        table = _models(plane)[name].__table__
        for role_url in (url, scratch[0]):
            with b._session(role_url, scope) as session:
                for statement in (
                    table.update()
                    .where(table.c.billing_account_id == account_id)
                    .values(billing_account_id=account_id),
                    table.delete().where(table.c.billing_account_id == account_id),
                ):
                    with pytest.raises(DBAPIError):
                        session.execute(statement)
                    session.rollback()
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.billing_account_id == account_id)
                )
                == 1
            )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_claim_xor_and_account_affinity_cannot_be_forged_by_direct_sql(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        other_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        session.commit()
    table = _models(plane)["CashSourceClaim"].__table__
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        claim = dict(
            connection.execute(
                select(table).where(table.c.opening_cash_lot_id == receipt.lot_ids[0])
            )
            .mappings()
            .one()
        )
        connection.rollback()

        def remove_only_fixture_claim():
            # Arrange an unclaimed *existing* qualified lot, without disabling
            # FKs, XOR checks or completeness guards. Restore append protection
            # before every tested INSERT, and roll back this entire arrangement.
            connection.execute(
                text(
                    f"ALTER TABLE mod_billing.{table.name} "
                    f"DISABLE TRIGGER {table.name}_append_only"
                )
            )
            connection.execute(table.delete().where(table.c.id == claim["id"]))
            connection.execute(
                text(
                    f"ALTER TABLE mod_billing.{table.name} "
                    f"ENABLE TRIGGER {table.name}_append_only"
                )
            )

        remove_only_fixture_claim()
        connection.execute(table.insert().values(**{**claim, "id": uuid4()}))
        connection.rollback()  # positive control: exact affinity is insertable
        for changes, code in (
            ({"settlement_id": None, "opening_cash_lot_id": None}, "23514"),
            (
                {"settlement_id": uuid4(), "opening_cash_lot_id": receipt.lot_ids[0]},
                "23514",
            ),
            ({"billing_account_id": other_id}, "23503"),
            ({"source_system": "wrong-source-namespace"}, "23503"),
            ({"source_settlement_key": "wrong-source-key"}, "23503"),
            ({"currency": "USD"}, "23503"),
            ({"minor_units": 3}, "23503"),
        ):
            remove_only_fixture_claim()
            trial = {**claim, "id": uuid4(), **changes}
            with pytest.raises(DBAPIError) as error:
                connection.execute(table.insert().values(**trial))
            assert error.value.orig.sqlstate == code
            connection.rollback()
        assert (
            connection.scalar(
                select(func.count())
                .select_from(table)
                .where(table.c.opening_cash_lot_id == receipt.lot_ids[0])
            )
            == 1
        )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_numeric20_6_import_is_exact_under_full_hostile_decimal_context(scratch, plane):
    from decimal import Clamped, Overflow, Subnormal, Underflow

    from dotmac_kernel.money import Currency, Money

    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope, precision=6)
        cmd = _command(session, scope, account_id, amounts=(123456789,), precision=6)
        expected_digest = opening_cash_command_digest(cmd)
        expected = Money.of("123.456789", Currency("NGN", 6))
        with localcontext() as ambient:
            ambient.prec, ambient.Emin, ambient.Emax, ambient.clamp = 3, -2, 2, 1
            for signal in (Inexact, Rounded, Overflow, Underflow, Subnormal, Clamped):
                ambient.traps[signal] = True
            ambient.clear_flags()
            before = (
                ambient.prec,
                ambient.Emin,
                ambient.Emax,
                ambient.clamp,
                dict(ambient.flags),
                dict(ambient.traps),
            )
            _prepare_window(session, scope, cmd)
            receipt = _import_command(
                session, scope=scope, command=cmd, verifier=TrustedVerifier()
            )
            lot = q.get_opening_cash_lot_position(
                session, scope=scope, lot_id=receipt.lot_ids[0]
            )
            assert receipt.total_residual == expected
            assert lot.remaining == expected
            assert receipt.request_digest == expected_digest
            assert (
                ambient.prec,
                ambient.Emin,
                ambient.Emax,
                ambient.clamp,
                dict(ambient.flags),
                dict(ambient.traps),
            ) == before
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_real_bi6_upgrade_preserves_settlement_claims_ledger_and_v1_v3_history(
    plane, monkeypatch
):
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with b._scratch_database(
        (selection,), target_revision="bi_0006_cash_source_claim"
    ) as urls:
        scope, url = runtime._scope_url(urls, plane)
        prefix = "" if plane == "tenant" else "platform_"

        def predecessor_claim(db, *, scope, settlement):
            # The real bi6 table predates the nullable opening-lot column. Only
            # this INSERT shape is historical; acceptance, fingerprint, postings,
            # accounting, retry and outbox use the accepted operation unchanged.
            tenant_column = "tenant_id, " if plane == "tenant" else ""
            tenant_value = ":tenant, " if plane == "tenant" else ""
            values = {
                "id": uuid4(),
                "settlement": settlement.id,
                "account": settlement.billing_account_id,
                "system": settlement.source_system,
                "key": settlement.source_settlement_key,
                "currency": settlement.currency,
                "units": settlement.minor_units,
            }
            if plane == "tenant":
                values["tenant"] = scope.tenant_id
            db.execute(
                text(
                    f"INSERT INTO mod_billing.{prefix}cash_source_claims "
                    f"(id, {tenant_column}settlement_id, billing_account_id, "
                    "source_system, source_settlement_key, currency, minor_units) "
                    f"VALUES (:id, {tenant_value}:settlement, :account, :system, "
                    ":key, :currency, :units)"
                ),
                values,
            )

        def native_predecessor_backing(db, *, scope, document_id, source_version=None):
            # Only the historical fixture uses the accepted native backing math.
            # bi6 cannot contain an imported AR basis. Restore the real resolver
            # before the actual additive upgrade or any successor admission.
            selected = s._models(scope)
            document = s._one(
                db, scope=scope, model=selected.document, row_id=document_id
            )
            assert document.authoring_mode != "imported_open_item"
            resolved = (
                s._document_allocated(db, scope=scope, document_id=document_id)
                + s._document_credited(db, scope=scope, document_id=document_id)
                + s._document_withheld(db, scope=scope, document_id=document_id)
                + s._document_non_cash_applied(db, scope=scope, document_id=document_id)
            )
            remaining = document.grand_total - resolved
            assert 0 <= remaining <= document.grand_total
            group = db.execute(
                s._where_scope(
                    select(selected.group)
                    .where(
                        selected.group.billing_account_id == document.billing_account_id
                    )
                    .order_by(selected.group.source_version.desc())
                    .limit(1),
                    scope,
                    selected.group,
                )
            ).scalar_one()
            arguments = [
                document_id,
                document.billing_account_id,
                document.currency,
                document.minor_units,
            ]
            if plane == "tenant":
                arguments.append(scope.tenant_id)
            active = bool(
                db.scalar(
                    select(
                        getattr(func.mod_billing, f"has_{prefix}live_invoice_issuance")(
                            *arguments
                        )
                    )
                )
            )
            return ReceivableBacking(
                None,
                document.grand_total,
                remaining,
                group.source_version,
                group.id,
                active,
            )

        # Source-compatible predecessor fixture adaptations only. The new
        # runtime is not claimed to support arbitrary execution on old schema.
        # bi6 cannot contain an opening contribution; retain every V1-V3 guard.
        with monkeypatch.context() as historical:
            historical.setattr(s, "_claim_cash_source", predecessor_claim)
            historical.setattr(
                s, "resolve_receivable_backing", native_predecessor_backing
            )
            historical.setattr(
                s, "_require_historical_source_unclaimed", lambda *args, **kwargs: None
            )
            historical.setattr(
                s, "_opening_document_applied", lambda *a, **k: Decimal(0)
            )
            with b._session(url, scope) as session:
                for name in (
                    "opening_import_receipts",
                    "opening_cash_lots",
                    "opening_cash_effects",
                ):
                    assert (
                        session.scalar(
                            text("SELECT to_regclass(:name)"),
                            {"name": f"mod_billing.{prefix}{name}"},
                        )
                        is None
                    )
                account_id = _new_account(session, scope)
                offered = b._settlement_command(
                    account_id, scope=scope, source_key=str(uuid4()), amount="73.25"
                )
                settlement_id = s.accept_settlement(
                    session,
                    scope=scope,
                    command=offered,
                    accepted_confirmation_evidence=frozenset({"provider_settled"}),
                ).id
                invoice = _invoice(session, scope, account_id)
                s.accept_withholding_discharge(
                    session,
                    scope=scope,
                    command=b._withholding_command(
                        account_id,
                        invoice.id,
                        scope=scope,
                        suffix=str(uuid4()),
                        claimed="5.00",
                        accepted="5.00",
                    ),
                )
                grant = s.grant_non_cash_credit(
                    session, scope=scope, command=runtime._grant(scope, account_id)
                )
                s.apply_non_cash_credit(
                    session,
                    scope=scope,
                    command=runtime._apply(scope, grant.grant_id, invoice.id, "20.00"),
                )
                session.commit()
        # All predecessor-only adaptations end before upgrade or new admission.
        engine = create_engine(urls[0])
        with engine.connect() as connection:
            names = (
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='mod_billing' ORDER BY table_name"
                    )
                )
                .scalars()
                .all()
            )
            before = {
                name: connection.execute(
                    text(f"SELECT to_jsonb(x) FROM mod_billing.{name} x ORDER BY id")
                )
                .scalars()
                .all()
                for name in names
            }
            assert {1, 2, 3}.issubset(
                {row["fact_version"] for row in before[f"{prefix}accounting_facts"]}
            )
            assert before[f"{prefix}withholding_discharges"]
            assert before[f"{prefix}non_cash_credit_grants"]
            assert before[f"{prefix}non_cash_credit_effects"]
            ledger = (
                b.IdempotencyRecord
                if plane == "tenant"
                else b.PlatformIdempotencyRecord
            )
            outbox = b.OutboxEvent if plane == "tenant" else b.PlatformOutboxEvent
            ledger_before = (
                connection.execute(select(ledger.__table__)).mappings().all()
            )
            outbox_before = (
                connection.execute(select(outbox.__table__)).mappings().all()
            )
        alembic_command.upgrade(predecessor._config((selection,)), REVISION)
        with engine.connect() as connection:
            after = {
                name: connection.execute(
                    text(f"SELECT to_jsonb(x) FROM mod_billing.{name} x ORDER BY id")
                )
                .scalars()
                .all()
                for name in names
            }
            for name in names:
                if name.endswith("cash_source_claims"):
                    assert after[name] == [
                        {**row, "opening_cash_lot_id": None} for row in before[name]
                    ]
                else:
                    assert after[name] == before[name], name
            assert (
                connection.execute(select(ledger.__table__)).mappings().all()
                == ledger_before
            )
            assert (
                connection.execute(select(outbox.__table__)).mappings().all()
                == outbox_before
            )
        # Preserve the exact bi6-to-bi7 assertions above; current runtime then
        # executes only after its additional immutable AR lineage is installed.
        alembic_command.upgrade(
            predecessor._config((selection,)), "bi_0009_opening_window"
        )
        with (
            _opening_scratch(urls, (plane,)) as scratch,
            _session(scratch, url, scope) as session,
        ):
            assert (
                s.accept_settlement(
                    session,
                    scope=scope,
                    command=offered,
                    accepted_confirmation_evidence=frozenset({"provider_settled"}),
                ).id
                == settlement_id
            )
            assert _position(session, scope, account_id).available_credit == Decimal(
                "73.25"
            )
            other_id = _new_account(session, scope)
            assert _import(session, scope, other_id)[1].total_residual == b._money("60")
            session.commit()
        engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_draw_and_refund_race_share_one_unused_opening_room(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        invoice = _invoice(session, scope, account_id)
        invoice_id = invoice.id
        session.commit()
    draw = ApplyAvailableCreditCommand(
        account_id, invoice_id, b._money("50"), preflight.NOW, str(uuid4())
    )
    refund = _refund(scope, receipt.lot_ids[0], "50")
    barrier, successes, errors = Barrier(2), [], []

    def run(kind):
        try:
            with _session(scratch, url, scope) as session:
                barrier.wait(timeout=20)
                if kind == "draw":
                    s.apply_available_credit(session, scope=scope, command=draw)
                else:
                    s.record_opening_cash_refund(
                        session, scope=scope, command=refund, verifier=TrustedVerifier()
                    )
                session.commit()
                successes.append(kind)
        except Exception as error:
            errors.append(error)

    threads = [Thread(target=run, args=(kind,)) for kind in ("draw", "refund")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert len(successes) == len(errors) == 1
    assert isinstance(errors[0], BillingRuleViolation | BillingConflict)
    with _session(scratch, url, scope) as session:
        position = q.get_opening_cash_lot_position(
            session, scope=scope, lot_id=receipt.lot_ids[0]
        )
        assert position.remaining == b._money("10")
        assert _position(session, scope, account_id).available_credit == Decimal("10")
        assert _position(session, scope, account_id).collectible_receivable == Decimal(
            "70" if successes[0] == "draw" else "120"
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_fault_after_both_mixed_inverse_halves_keeps_original_draw_and_cash_room(
    scratch, plane, monkeypatch
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        s.accept_settlement(
            session,
            scope=scope,
            command=b._settlement_command(
                account_id, scope=scope, source_key=str(uuid4()), amount="40"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        invoice = _invoice(session, scope, account_id)
        draw = s.apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account_id, invoice.id, b._money("100"), preflight.NOW, str(uuid4())
            ),
        )
        draw_id = draw.id
        session.commit()
        before = _counts(session, plane, account_id)
        real_emit = s._emit
        observed = []

        def fail_after_both_halves(*args, **kwargs):
            real_emit(*args, **kwargs)
            session.flush()
            tables = _models(plane)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(tables["OpeningCashEffect"])
                    .where(
                        tables["OpeningCashEffect"].lot_id == receipt.lot_ids[0],
                        tables["OpeningCashEffect"].effect_kind
                        == "application_inverse",
                    )
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(tables["AllocationEffect"])
                    .where(
                        tables["AllocationEffect"].billing_account_id == account_id,
                        tables["AllocationEffect"].amount_delta < 0,
                    )
                )
                == 1
            )
            observed.append(True)
            raise RuntimeError("fault after both inverse sources and outbox")

        monkeypatch.setattr(s, "_emit", fail_after_both_halves)
        inverse = _inverse(scope, draw_id, "100")
        with pytest.raises(
            RuntimeError, match="fault after both inverse sources and outbox"
        ):
            s.reverse_cash_credit_draw(session, scope=scope, command=inverse)
        assert observed
        assert _counts(session, plane, account_id) == before
        assert (
            _retry_count(
                session,
                plane,
                "billing.cash.credit.draw.reverse.v1",
                inverse.idempotency_key,
            )
            == 0
        )
        assert _position(session, scope, account_id).available_credit == 0
        assert q.get_opening_cash_lot_position(
            session, scope=scope, lot_id=receipt.lot_ids[0]
        ).remaining == b._money("0")
        monkeypatch.setattr(s, "_emit", real_emit)
        assert s.reverse_cash_credit_draw(session, scope=scope, command=inverse).id
        session.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "missing", ["AccountingFact", "ReceivablePositionFact", "ReceivablePositionFactV2"]
)
def test_deferred_completeness_requires_accounting_and_both_position_versions(
    scratch, plane, missing, monkeypatch
):
    scope, url = runtime._scope_url(scratch, plane)
    prefix = "" if plane == "tenant" else "Platform"
    omitted_model = getattr(models, prefix + missing)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        before = _counts(session, plane, account_id)
        real_add = session.add
        omitted = []

        def omit_one_actual_artifact(instance, *args, **kwargs):
            if isinstance(instance, omitted_model):
                omitted.append(instance.id)
                return None
            return real_add(instance, *args, **kwargs)

        with monkeypatch.context() as fault:
            fault.setattr(session, "add", omit_one_actual_artifact)
            receipt = _import(session, scope, account_id)[1]
        assert omitted
        with pytest.raises(DBAPIError) as error:
            session.commit()
        assert error.value.orig.sqlstate == "23514"
        session.rollback()
        assert _counts(session, plane, account_id) == before
        with pytest.raises(BillingRuleViolation):
            q.get_opening_import_receipt(
                session, scope=scope, receipt_id=receipt.receipt_id
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "field",
    [
        "command_digest",
        "lot_id",
        "lot_evidence_digest",
        "source_settlement_key",
        "billing_account_id",
    ],
)
def test_refund_qualification_is_bound_to_exact_cash_source_and_command(
    scratch, plane, field
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        session.commit()
        offered = _refund(scope, receipt.lot_ids[0])

        def wrong(value):
            if field == "command_digest":
                return replace(value, command_digest="f" * 64)
            replacement = (
                uuid4() if field in {"lot_id", "billing_account_id"} else "wrong"
            )
            return replace(
                value, subject=replace(value.subject, **{field: replacement})
            )

        before = _counts(session, plane, account_id)
        with pytest.raises(BillingRuleViolation):
            s.record_opening_cash_refund(
                session, scope=scope, command=offered, verifier=TrustedVerifier(wrong)
            )
        assert _counts(session, plane, account_id) == before
        assert q.get_opening_cash_lot_position(
            session, scope=scope, lot_id=receipt.lot_ids[0]
        ).remaining == b._money("60")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_refund_authority_cannot_be_reused_as_correction_or_migration_unwind_authority(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)

    class WrongOperation(TrustedVerifier):
        def verify_refund_correction(self, request):
            return self.verify_refund(request)

        def verify_import_reversal(self, request):
            return True

    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id)
        refund = s.record_opening_cash_refund(
            session,
            scope=scope,
            command=_refund(scope, receipt.lot_ids[0]),
            verifier=TrustedVerifier(),
        )
        correction = CorrectOpeningCashRefundV1(
            scope,
            refund.effect_id,
            refund.amount,
            OpeningCashMovementEvidenceV1(
                "payments", str(uuid4()), "2", "c" * 64, "corrected"
            ),
            "correction-authority",
            str(uuid4()),
            preflight.NOW,
        )
        with pytest.raises(BillingRuleViolation):
            s.correct_opening_cash_refund(
                session, scope=scope, command=correction, verifier=WrongOperation()
            )
        s.correct_opening_cash_refund(
            session, scope=scope, command=correction, verifier=TrustedVerifier()
        )
        unwind = ReverseOpeningAccountImportV1(
            scope,
            receipt.receipt_id,
            "fresh-migration-authority",
            str(uuid4()),
            preflight.NOW,
        )
        with pytest.raises(BillingRuleViolation):
            s.reverse_opening_account_import(
                session, scope=scope, command=unwind, verifier=WrongOperation()
            )
        assert _position(session, scope, account_id).available_credit == Decimal("60")
        session.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_expired_import_retry_record_never_releases_permanent_cash_identity(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        cmd, receipt = _import(session, scope, account_id)
        session.commit()
    ledger = b.IdempotencyRecord if plane == "tenant" else b.PlatformIdempotencyRecord
    engine = create_engine(scratch[0])
    with engine.begin() as connection:
        connection.execute(
            ledger.__table__.delete().where(
                ledger.scope == "billing.opening.import.v1",
                ledger.key == cmd.idempotency_key,
            )
        )
    engine.dispose()
    with _session(scratch, url, scope) as session:
        before = _counts(session, plane, account_id)
        with pytest.raises((BillingRuleViolation, BillingConflict)):
            _prepare_window(session, scope, cmd)
            _import_command(
                session, scope=scope, command=cmd, verifier=TrustedVerifier()
            )
        assert _counts(session, plane, account_id) == before
        assert _position(session, scope, account_id).available_credit == Decimal("60")
        assert (
            q.get_opening_import_receipt(
                session, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "attack", ["application", "application_inverse", "invoice_capacity"]
)
def test_direct_sql_room_guards_distinguish_lot_room_from_original_inverse_room(
    scratch, plane, attack
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _import(
            session,
            scope,
            account_id,
            amounts=(20000,) if attack == "invoice_capacity" else (6000,),
        )
        invoice = _invoice(session, scope, account_id)
        first = s.apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account_id, invoice.id, b._money("10"), preflight.NOW, str(uuid4())
            ),
        )
        if attack in {"application", "invoice_capacity"}:
            effect = q.get_accounting_fact_v4(
                session, scope=scope, posting_group_id=first.id
            ).opening_cash_effects[0]
            # Lot-room case: 60 principal minus 10 used. Invoice-capacity
            # case: ample 190 cash remains but this invoice has only 110 AR.
            positive, excess = (
                ("110.00", "110.01")
                if attack == "invoice_capacity"
                else ("50.00", "50.01")
            )
        else:
            s.apply_available_credit(
                session,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    account_id, invoice.id, b._money("10"), preflight.NOW, str(uuid4())
                ),
            )
            inverse = s.reverse_cash_credit_draw(
                session, scope=scope, command=_inverse(scope, first.id, "5")
            )
            effect = q.get_accounting_fact_v4(
                session, scope=scope, posting_group_id=inverse.id
            ).opening_cash_effects[0]
            # A second draw leaves ample lot capacity. Restoring 5.01 would fit
            # that capacity but over-reverse the first named ten-unit use.
            positive, excess = "5.00", "5.01"
        session.commit()
    table = _models(plane)["OpeningCashEffect"].__table__
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        original = dict(
            connection.execute(select(table).where(table.c.id == effect.effect_id))
            .mappings()
            .one()
        )
        connection.rollback()

        def trial(amount):
            fact = replace(
                effect,
                effect_id=uuid4(),
                sequence=effect.sequence + 1,
                amount=b._money(amount),
            )
            payload = s._jsonable(fact)
            row = {
                key: deepcopy(value)
                for key, value in original.items()
                if key != "created_at"
            }
            row.update(
                id=fact.effect_id,
                sequence=fact.sequence,
                amount_delta=fact.availability_delta.amount,
                evidence=payload,
                evidence_digest=fingerprint_of(payload),
            )
            return row

        connection.execute(table.insert().values(**trial(positive)))
        # Immediate positive control; no incomplete group is committed.
        connection.rollback()
        with pytest.raises(DBAPIError) as error:
            connection.execute(table.insert().values(**trial(excess)))
        assert error.value.orig.sqlstate == "23514"
        connection.rollback()
        assert (
            connection.scalar(
                select(func.count())
                .select_from(table)
                .where(table.c.posting_group_id == effect.posting_group_id)
            )
            == 1
        )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_unused_cash_cannot_mask_reversal_of_settlement_beneath_active_mixed_draw(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id, amounts=(10000,))
        s.accept_settlement(
            session,
            scope=scope,
            command=b._settlement_command(
                account_id, scope=scope, source_key=str(uuid4()), amount="40.00"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        group = _models(plane)["PostingGroup"]
        root = session.execute(
            select(group).where(
                group.billing_account_id == account_id,
                group.group_kind == "settlement_accepted",
            )
        ).scalar_one()
        # Unused ordinary backing may still be reversed and restored. This
        # positive control also creates a real reversal chain for the attack.
        unused_inverse = s.reverse_posting_group(
            session,
            scope=scope,
            command=ReversePostingGroupCommand(root.id, preflight.NOW, str(uuid4())),
        )
        restored = s.reverse_posting_group(
            session,
            scope=scope,
            command=ReversePostingGroupCommand(
                unused_inverse.id, preflight.NOW, str(uuid4())
            ),
        )
        invoice = _invoice(session, scope, account_id)
        draw = s.apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account_id, invoice.id, b._money("100"), preflight.NOW, str(uuid4())
            ),
        )
        fact = q.get_accounting_fact_v4(session, scope=scope, posting_group_id=draw.id)
        assert len(fact.settlement_allocations) == len(fact.opening_cash_effects) == 1
        assert _position(session, scope, account_id).available_credit == Decimal("40")
        before = _counts(session, plane, account_id)
        offered = ReversePostingGroupCommand(restored.id, preflight.NOW, str(uuid4()))
        with pytest.raises(BillingRuleViolation) as refused:
            s.reverse_posting_group(session, scope=scope, command=offered)
        assert refused.value.code == "billing.opening_cash_owner_required"
        assert _counts(session, plane, account_id) == before
        assert _position(session, scope, account_id).available_credit == Decimal("40")
        assert q.get_opening_cash_lot_position(
            session, scope=scope, lot_id=receipt.lot_ids[0]
        ).remaining == b._money("40")
        s.reverse_cash_credit_draw(
            session, scope=scope, command=_inverse(scope, draw.id, "100")
        )
        # Once the complete mixed draw has been undone, the same proposed root
        # correction is admissible and leaves exactly the imported backing.
        s.reverse_posting_group(session, scope=scope, command=offered)
        assert _position(session, scope, account_id).available_credit == Decimal("100")
        assert q.get_opening_cash_lot_position(
            session, scope=scope, lot_id=receipt.lot_ids[0]
        ).remaining == b._money("100")
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_zero_residual_receipt_identity_survives_complete_opening_unwind(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        offered, receipt = _import(session, scope, account_id, amounts=(0, 6000))
        assert len(receipt.lot_ids) == 2
        before = tuple(
            q.get_opening_cash_lot_position(session, scope=scope, lot_id=lot_id)
            for lot_id in receipt.lot_ids
        )
        assert tuple(item.remaining for item in before) == (
            b._money("0"),
            b._money("60"),
        )
        assert all(not item.unwound for item in before)
        reversed_receipt = s.reverse_opening_account_import(
            session,
            scope=scope,
            command=ReverseOpeningAccountImportV1(
                scope,
                receipt.receipt_id,
                "migration-authority:full-unwind",
                str(uuid4()),
                preflight.NOW,
            ),
            verifier=TrustedVerifier(),
        )
        assert reversed_receipt.original_receipt_id == receipt.receipt_id
        assert _position(session, scope, account_id).available_credit == 0
        for lot_id in receipt.lot_ids:
            position = q.get_opening_cash_lot_position(
                session, scope=scope, lot_id=lot_id
            )
            assert position.remaining == b._money("0")
            assert position.unwound
        claim = _models(plane)["CashSourceClaim"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(claim)
                .where(claim.billing_account_id == account_id)
            )
            == 2
        )
        zero_source = offered.manifest.cash_lots[0]
        callback = replace(
            b._settlement_command(
                account_id,
                scope=scope,
                source_key=zero_source.source_settlement_key,
                amount="40.00",
            ),
            source_system=zero_source.source_system,
        )
        counts = _counts(session, plane, account_id)
        with pytest.raises(BillingConflict) as refused:
            s.accept_settlement(
                session,
                scope=scope,
                command=callback,
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
        assert refused.value.code == "billing.cash_source_claimed"
        assert _counts(session, plane, account_id) == counts
        session.commit()


def _semantic_query_history(scratch, plane):
    """Committed valid sources for recomputed-hash query counterexamples."""
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _, receipt = _import(session, scope, account_id, amounts=(10000,))
        s.accept_settlement(
            session,
            scope=scope,
            command=b._settlement_command(
                account_id, scope=scope, source_key=str(uuid4()), amount="40.00"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        invoice = _invoice(session, scope, account_id)
        draw = s.apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account_id, invoice.id, b._money("80.00"), preflight.NOW, str(uuid4())
            ),
        )
        draw_id = draw.id
        refund = s.record_opening_cash_refund(
            session,
            scope=scope,
            command=_refund(scope, receipt.lot_ids[0], "10.00"),
            verifier=TrustedVerifier(),
        )
        correction = s.correct_opening_cash_refund(
            session,
            scope=scope,
            command=CorrectOpeningCashRefundV1(
                scope,
                refund.effect_id,
                refund.amount,
                OpeningCashMovementEvidenceV1(
                    "payments-correction", str(uuid4()), "2", "c" * 64, "corrected"
                ),
                "independent-correction-authority",
                str(uuid4()),
                preflight.NOW,
            ),
            verifier=TrustedVerifier(),
        )
        other_refund = s.record_opening_cash_refund(
            session,
            scope=scope,
            command=_refund(scope, receipt.lot_ids[0], "5.00"),
            verifier=TrustedVerifier(),
        )
        inverse = s.reverse_cash_credit_draw(
            session, scope=scope, command=_inverse(scope, draw_id, "10.00")
        )
        inverse_id = inverse.id
        session.commit()
    return scope, receipt, draw_id, refund, correction, other_refund, inverse_id


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "tamper",
    [
        "duplicate_opening",
        "duplicate_settlement",
        "posted_amount",
        "nested_receipt",
        "corrects_group",
        "target_currency",
        "target_precision",
        "refund_subject",
        "correction_original",
    ],
)
def test_opening_queries_refuse_semantic_tampering_even_with_recomputed_kernel_hash(
    scratch, plane, tamper
):
    scope, receipt, draw_id, refund, correction, other_refund, inverse_id = (
        _semantic_query_history(scratch, plane)
    )
    selected = _models(plane)
    accounting = tamper in {
        "duplicate_opening",
        "duplicate_settlement",
        "posted_amount",
        "nested_receipt",
        "corrects_group",
    }
    if accounting:
        table = selected["AccountingFact"].__table__
        payload_column, digest_column = "fact_payload", "fact_digest"
        group_id = (
            receipt.posting_group_id
            if tamper == "nested_receipt"
            else inverse_id
            if tamper == "corrects_group"
            else draw_id
        )
        predicate = table.c.posting_group_id == group_id

        def read(session):
            return q.get_accounting_fact_v4(
                session, scope=scope, posting_group_id=group_id
            )
    elif tamper.startswith("target_"):
        table = selected["OpeningImportReceipt"].__table__
        payload_column, digest_column = "evidence", "evidence_digest"
        predicate = table.c.id == receipt.receipt_id

        def read(session):
            return q.get_opening_import_receipt(
                session, scope=scope, receipt_id=receipt.receipt_id
            )
    else:
        table = selected["OpeningCashEffect"].__table__
        payload_column, digest_column = "evidence", "evidence_digest"
        effect_id = (
            refund.effect_id if tamper == "refund_subject" else correction.effect_id
        )
        predicate = table.c.id == effect_id

        def read(session):
            return q.get_opening_cash_effect(session, scope=scope, effect_id=effect_id)

    # Owner-only fixture corruption. Disable only the actual UPDATE/DELETE
    # append trigger, never INSERT admission, FK/RLS or completeness guards.
    # No corrupted history is committed or repaired: rollback restores it.
    with _session(scratch, scratch[0], scope) as owner:
        untouched = read(owner)
        row = owner.execute(select(table).where(predicate)).mappings().one()
        payload = deepcopy(row[payload_column])
        scalars = {}
        if tamper in {"duplicate_opening", "duplicate_settlement"}:
            references = [
                (item["allocation_effect_id"], item["opening_effect_id"])
                for item in payload["cash_source_order"]
            ]
            assert len(references) == len(set(references)) == 2
            source_order = deepcopy(payload["cash_source_order"])
            key = (
                "opening_cash_effects"
                if tamper == "duplicate_opening"
                else "settlement_allocations"
            )
            assert len(payload[key]) == 1
            payload[key].append(deepcopy(payload[key][0]))
            assert payload["cash_source_order"] == source_order
        elif tamper == "posted_amount":
            payload["effects"][0]["amount_delta"]["amount"] = "0.01"
        elif tamper == "nested_receipt":
            assert payload["import_receipt"]["receipt_id"] == str(receipt.receipt_id)
            payload["import_receipt"]["total_residual"]["amount"] = "99.99"
        elif tamper == "corrects_group":
            assert payload["corrects_posting_group_id"] == str(draw_id)
            payload["corrects_posting_group_id"] = str(receipt.posting_group_id)
        elif tamper in {"target_currency", "target_precision"}:
            field, changed = (
                ("currency", "USD") if tamper == "target_currency" else ("precision", 3)
            )
            payload["target"][field] = changed
            # Keep the proof's target internally equal, so the counterexample
            # reaches the persisted account/currency binding rather than a
            # simple target-versus-verification inequality.
            payload["verification"]["target"][field] = changed
            assert payload["verification"]["target"] == payload["target"]
        elif tamper == "refund_subject":
            payload["movement_verification"]["subject"]["source_settlement_key"] = (
                "wrong-live-source-key"
            )
        else:
            other_row = (
                owner.execute(select(table).where(table.c.id == other_refund.effect_id))
                .mappings()
                .one()
            )
            # A real same-lot original preserves every FK coordinate. It has a
            # different amount, so this drift must fail the full linked-refund
            # semantic comparison even with a matching original-evidence hash.
            assert other_refund.amount != correction.amount
            payload["original_effect_id"] = str(other_refund.effect_id)
            payload["movement_verification"]["refund_effect_id"] = str(
                other_refund.effect_id
            )
            payload["movement_verification"]["refund_evidence_digest"] = other_row[
                "evidence_digest"
            ]
            scalars["original_effect_id"] = other_refund.effect_id
        digest = fingerprint_of(payload)
        assert digest != row[digest_column]
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"DISABLE TRIGGER {table.name}_append_only"
            )
        )
        owner.execute(
            table.update()
            .where(predicate)
            .values(**{payload_column: payload, digest_column: digest, **scalars})
        )
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"ENABLE TRIGGER {table.name}_append_only"
            )
        )
        stored = owner.execute(select(table).where(predicate)).mappings().one()
        assert fingerprint_of(stored[payload_column]) == stored[digest_column]
        with pytest.raises(BillingRuleViolation) as refused:
            read(owner)
        assert refused.value.code == "billing.opening_evidence_integrity"
        owner.rollback()
        assert read(owner) == untouched


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_two_pending_opening_draws_commit_with_distinct_historical_exposure(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    exposure = (
        models.ReceivableExposureFact
        if plane == "tenant"
        else models.PlatformReceivableExposureFact
    )
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _import(session, scope, account_id, amounts=(10000,))
        invoice = _invoice(session, scope, account_id)
        first = s.apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account_id, invoice.id, b._money("10.00"), preflight.NOW, str(uuid4())
            ),
        )
        first_id = first.id
        second = s.apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account_id, invoice.id, b._money("20.00"), preflight.NOW, str(uuid4())
            ),
        )
        second_id = second.id
        # One outer commit validates both pending historical watermarks. No
        # intermediate commit or constraint-mode change can hide the first.
        session.commit()
        rows = session.scalars(
            select(exposure)
            .where(
                exposure.document_id == invoice.id,
                exposure.posting_group_watermark.in_((first_id, second_id)),
            )
            .order_by(exposure.source_version)
        ).all()
        assert len(rows) == 2
        assert [row.posting_group_watermark for row in rows] == [first_id, second_id]
        assert [row.collectible_receivable for row in rows] == [
            Decimal("110"),
            Decimal("90"),
        ]
        assert rows[0].source_version < rows[1].source_version
        assert _position(session, scope, account_id).collectible_receivable == Decimal(
            "90"
        )
        assert _position(session, scope, account_id).available_credit == Decimal("70")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("policy_before_first", [True, False])
def test_exposure_cutoff_handles_wht_noncash_credit_and_credit_void_between_draws(
    scratch, plane, policy_before_first
):
    from dotmac_billing.commands import IssueCreditNote

    scope, url = runtime._scope_url(scratch, plane)
    exposure = (
        models.ReceivableExposureFact
        if plane == "tenant"
        else models.PlatformReceivableExposureFact
    )
    with _session(scratch, url, scope) as session:
        account_id = _new_account(session, scope)
        _import(session, scope, account_id, amounts=(10000,))
        invoice = _invoice(session, scope, account_id)

        def policy_sources():
            s.accept_withholding_discharge(
                session,
                scope=scope,
                command=b._withholding_command(
                    account_id,
                    invoice.id,
                    scope=scope,
                    suffix=str(uuid4()),
                    claimed="5.00",
                    accepted="5.00",
                ),
            )
            grant = s.grant_non_cash_credit(
                session, scope=scope, command=runtime._grant(scope, account_id)
            )
            s.apply_non_cash_credit(
                session,
                scope=scope,
                command=runtime._apply(scope, grant.grant_id, invoice.id, "10.00"),
            )
            return s.issue_credit_note(
                session,
                scope=scope,
                command=IssueCreditNote(
                    invoice.id,
                    b._money("10.00"),
                    b._money("0.00"),
                    b._money("10.00"),
                    "source-qualified correction",
                    "credit",
                    b.NOW.date(),
                    "billing-test",
                    str(uuid4()),
                    preflight.NOW,
                    "credit.standard",
                    "1",
                ),
                numbering=b.NUMBERING,
            )

        credit = policy_sources() if policy_before_first else None
        first = s.apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account_id, invoice.id, b._money("10.00"), preflight.NOW, str(uuid4())
            ),
        )
        first_id = first.id
        if credit is None:
            credit = policy_sources()
        assert _position(session, scope, account_id).collectible_receivable == Decimal(
            "85"
        )
        s.void_document(
            session,
            scope=scope,
            document_id=credit.id,
            actor_ref="billing-test",
            occurred_at=preflight.NOW,
            source_ref=str(uuid4()),
        )
        assert _position(session, scope, account_id).collectible_receivable == Decimal(
            "95"
        )
        second = s.apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account_id, invoice.id, b._money("20.00"), preflight.NOW, str(uuid4())
            ),
        )
        second_id = second.id
        # WHT, noncash, credit issue, credit void and both draws remain in this
        # same outer transaction until the real deferred validation at commit.
        session.commit()
        rows = session.scalars(
            select(exposure)
            .where(
                exposure.document_id == invoice.id,
                exposure.posting_group_watermark.in_((first_id, second_id)),
            )
            .order_by(exposure.source_version)
        ).all()
        assert len(rows) == 2
        assert [row.posting_group_watermark for row in rows] == [first_id, second_id]
        assert rows[0].collectible_receivable == Decimal(
            "85" if policy_before_first else "110"
        )
        assert rows[1].collectible_receivable == Decimal("75")
        assert rows[0].source_version < rows[1].source_version
        assert _position(session, scope, account_id).collectible_receivable == Decimal(
            "75"
        )
        assert _position(session, scope, account_id).available_credit == Decimal("70")


def _native_target_write(session, scope, account_id, kind):
    """Prepare a legal unposted native write, with target mutation still pending."""
    prefix = "" if isinstance(scope, TenantScope) else "Platform"
    obligation_model = getattr(models, prefix + "RatedObligation")
    document_model = getattr(models, prefix + "BillingDocument")
    if kind == "obligation":
        command = b._obligation_command(
            account_id, scope=scope, source_fact_id=str(uuid4())
        )

        def write(db):
            row = s.accept_rated_obligation(
                db,
                scope=scope,
                command=command,
                accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
            )
            return row.id

        return write, obligation_model

    source_account = _new_account(session, scope)
    obligation = s.accept_rated_obligation(
        session,
        scope=scope,
        command=b._obligation_command(
            source_account, scope=scope, source_fact_id=str(uuid4())
        ),
        accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
    )
    draft = s.create_draft_document(
        session, scope=scope, command=b._draft_command(obligation.id)
    )
    table = document_model.__table__
    row = dict(
        session.execute(select(table).where(table.c.id == draft.id)).mappings().one()
    )
    # A draft may be unlinked; no issued evidence, lines, obligation or money is
    # moved. Copy the actual authored snapshots to isolate the documents guard.
    row.update(id=uuid4(), obligation_id=None)
    assert row["lifecycle"] == "draft" and row["issued_at"] is None
    session.commit()
    if kind == "draft_move":
        session.execute(table.insert().values(**row))
        session.commit()

        def write(db):
            result = db.execute(
                table.update()
                .where(
                    table.c.id == row["id"],
                    table.c.lifecycle == "draft",
                    table.c.issued_at.is_(None),
                )
                .values(billing_account_id=account_id)
            )
            assert result.rowcount == 1
            return row["id"]
    else:
        row["billing_account_id"] = account_id

        def write(db):
            db.execute(table.insert().values(**row))
            return row["id"]

    return write, document_model


def _native_target_count(session, model, account_id):
    return session.scalar(
        select(func.count())
        .select_from(model)
        .where(model.billing_account_id == account_id)
    )


def _assert_target_isolation(session, isolation):
    assert isolation in {"READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"}
    # The harness sets tenant context on connect. Finish that bootstrap
    # transaction, then prove the isolation of the transaction under test.
    session.execute(
        text(
            "SET SESSION CHARACTERISTICS AS TRANSACTION " f"ISOLATION LEVEL {isolation}"
        )
    )
    session.commit()
    assert (
        session.scalar(text("SELECT current_setting('transaction_isolation')"))
        == isolation.lower()
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", ["obligation", "draft_insert", "draft_move"])
@pytest.mark.parametrize("native_commits", [True, False])
@pytest.mark.parametrize(
    "isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
)
def test_unposted_native_write_after_acquired_snapshot_cannot_hide_from_import(
    scratch, plane, kind, native_commits, isolation
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as db:
        account_id = _new_account(db, scope)
        command = _command(db, scope, account_id)
        _prepare_window(db, scope, command)
        write, native_model = _native_target_write(db, scope, account_id, kind)
        db.commit()
    acquired, completed = Barrier(2), Barrier(2)
    snapshots, native_ids, receipts, refusals, writer_errors = [], [], [], [], []

    def importer():
        try:
            with _session(scratch, url, scope) as db:
                _assert_target_isolation(db, isolation)
                assert _native_target_count(db, native_model, account_id) == 0
                assert _counts(db, plane, account_id)["PostingGroup"] == 0
                snapshots.append(db.scalar(text("SELECT pg_current_snapshot()::text")))
                acquired.wait(timeout=30)
                completed.wait(timeout=30)
                _prepare_window(db, scope, command)
                receipt = _import_command(
                    db, scope=scope, command=command, verifier=TrustedVerifier()
                )
                db.commit()
                receipts.append(receipt)
        except Exception as error:
            refusals.append(error)

    def native_writer():
        try:
            with _session(scratch, url, scope) as db:
                acquired.wait(timeout=30)
                native_ids.append(write(db))
                if native_commits:
                    db.commit()
                else:
                    db.rollback()
                completed.wait(timeout=30)
        except Exception as error:
            writer_errors.append(error)
            acquired.abort()
            completed.abort()

    threads = [Thread(target=importer), Thread(target=native_writer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()
    assert snapshots and snapshots[0]
    assert len(native_ids) == 1 and writer_errors == []
    with _session(scratch, url, scope) as db:
        assert _native_target_count(db, native_model, account_id) == int(native_commits)
        if native_commits:
            assert receipts == [] and len(refusals) == 1
            if isolation in {"REPEATABLE READ", "SERIALIZABLE"}:
                assert isinstance(refusals[0], DBAPIError), refusals
                assert refusals[0].orig.sqlstate == "40001"
            else:
                assert isinstance(refusals[0], BillingRuleViolation), refusals
                assert refusals[0].code == "billing.opening_target_not_pristine"
            assert _counts(db, plane, account_id)["OpeningImportReceipt"] == 0
            assert _counts(db, plane, account_id)["PostingGroup"] == 0
            assert _position(db, scope, account_id).available_credit == 0
            # A serialization refusal needs a new transaction. The new snapshot
            # now sees the unposted native evidence and truthfully refuses it.
            with pytest.raises(BillingRuleViolation) as fresh:
                _prepare_window(db, scope, command)
                _import_command(
                    db, scope=scope, command=command, verifier=TrustedVerifier()
                )
            assert fresh.value.code == "billing.opening_target_not_pristine"
        else:
            assert refusals == [] and len(receipts) == 1
            assert _position(db, scope, account_id).available_credit == Decimal("60")
            _prepare_window(db, scope, command)
            assert (
                _import_command(
                    db, scope=scope, command=command, verifier=TrustedVerifier()
                )
                == receipts[0]
            )
            assert _counts(db, plane, account_id)["OpeningImportReceipt"] == 1


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", ["obligation", "draft_insert", "draft_move"])
@pytest.mark.parametrize("import_first", [True, False])
def test_same_outer_transaction_import_and_native_write_have_explicit_order(
    scratch, plane, kind, import_first
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as db:
        account_id = _new_account(db, scope)
        command = _command(db, scope, account_id)
        write, native_model = _native_target_write(db, scope, account_id, kind)
        db.commit()
        if import_first:
            _prepare_window(db, scope, command)
            receipt = _import_command(
                db, scope=scope, command=command, verifier=TrustedVerifier()
            )
            write(db)
            assert _native_target_count(db, native_model, account_id) == 1
            db.commit()
            assert (
                q.get_opening_import_receipt(
                    db, scope=scope, receipt_id=receipt.receipt_id
                )
                == receipt
            )
            assert _position(db, scope, account_id).available_credit == Decimal("60")
        else:
            write(db)
            with pytest.raises(BillingRuleViolation) as refusal:
                _prepare_window(db, scope, command)
                _import_command(
                    db, scope=scope, command=command, verifier=TrustedVerifier()
                )
            assert refusal.value.code == "billing.opening_target_not_pristine"
            assert _native_target_count(db, native_model, account_id) == 1
            db.commit()
            assert _counts(db, plane, account_id)["OpeningImportReceipt"] == 0
            assert _position(db, scope, account_id).available_credit == 0


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", ["obligation", "draft_insert", "draft_move"])
def test_read_committed_prior_native_state_is_refused_by_service_and_sql_admission(
    scratch, plane, kind, monkeypatch
):
    scope, url = runtime._scope_url(scratch, plane)
    with _session(scratch, url, scope) as db:
        account_id = _new_account(db, scope)
        command = _command(db, scope, account_id)
        write, native_model = _native_target_write(db, scope, account_id, kind)
        write(db)
        db.commit()
        _assert_target_isolation(db, "READ COMMITTED")
        assert _native_target_count(db, native_model, account_id) == 1
        before = _counts(db, plane, account_id)
        with pytest.raises(BillingRuleViolation) as refusal:
            _prepare_window(db, scope, command)
            _import_command(
                db, scope=scope, command=command, verifier=TrustedVerifier()
            )
        assert refusal.value.code == "billing.opening_target_not_pristine"
        # This one fixture bypasses only the friendly service pristine check to
        # reach actual SQL receipt admission. Authority, exact target, sources,
        # group/effects/outbox and every database guard remain active. It is a
        # rejected SQL attack, never an approved public import success.
        with monkeypatch.context() as direct_sql_probe:
            direct_sql_probe.setattr(
                s, "_require_pristine_opening_target", lambda *args, **kwargs: None
            )
            with pytest.raises(DBAPIError) as error:
                _prepare_window(db, scope, command)
                _import_command(
                    db, scope=scope, command=command, verifier=TrustedVerifier()
                )
        assert error.value.orig.sqlstate == "23514"
        assert "pristine" in str(error.value.orig).lower()
        assert _counts(db, plane, account_id) == before
        assert _native_target_count(db, native_model, account_id) == 1
        assert (
            _retry_count(
                db, plane, "billing.opening.import.v1", command.idempotency_key
            )
            == 0
        )
        assert _position(db, scope, account_id).available_credit == 0
