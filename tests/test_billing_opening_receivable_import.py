"""Hosted migrated PostgreSQL proof for atomic documentary AR and cash import.

Trusted fixture ports qualify injected evidence only. They are explicitly not
production source proof, operational migration approval or import composition.
"""

# SQL identifiers below come only from fixed model tables or the scratch catalog.
# ruff: noqa: S608
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from dotmac_billing import models
from dotmac_billing import opening_queries as cash_queries
from dotmac_billing import opening_receivable_queries as q
from dotmac_billing import service as s
from dotmac_billing.commands import (
    AllocationCommand,
    ApplyAvailableCreditCommand,
    DeallocationCommand,
    IssueCreditNote,
    ReallocationCommand,
    ReduceNonCashCreditGrant,
    ReverseNonCashCreditApplication,
    ReverseWithholdingDischarge,
)
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.opening_commands import ReverseOpeningAccountImportV1
from dotmac_billing.opening_receivable_validation import (
    opening_account_command_digest_v2,
    opening_manifest_fingerprint_v2,
    opening_withholding_component_identity,
)
from dotmac_billing.queries import get_receivable_position_v2
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as b
from tests import test_billing_non_cash_credit as noncash
from tests import test_billing_opening_cash_import as cash
from tests.billing_predecessor_fixtures import predecessor_obligation_columns
from tests.unit import test_billing_opening_receivable_contracts as evidence


@pytest.fixture
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        with cash._opening_scratch(urls) as scoped:
            yield scoped


def _command(session, scope, account_id):
    cmd = evidence._candidate()
    partition = replace(
        cmd.partition, account_id=account_id, source_account_key=f"account:{account_id}"
    )
    target = cash_queries.get_opening_cash_target(
        session, scope=scope, billing_account_id=account_id
    )
    namespace = str(account_id)

    def identity(value):
        return replace(value, key=f"{namespace}:{value.key}")

    items = tuple(
        replace(
            item,
            source_identity=identity(item.source_identity),
            partition=partition,
            source_invoice_key=f"{namespace}:{item.source_invoice_key}",
            historical_discharges=tuple(
                replace(
                    effect,
                    reference=f"{namespace}:{effect.reference}",
                    source_key=f"{namespace}:{effect.source_key}",
                    live_identity=f"{namespace}:{effect.live_identity}",
                    ledger_ref=f"{namespace}:{effect.ledger_ref}",
                    replay_bindings=tuple(
                        replace(
                            binding,
                            source_ref=(
                                opening_withholding_component_identity(
                                    tax_source_owner="dotmac-tax",
                                    determination_ref=f"tax-set:{namespace}:{item.source_invoice_key}",
                                    component_ref=f"component:{namespace}:{item.source_invoice_key}",
                                )
                                if binding.operation
                                == "billing.withholding.tax_component"
                                else f"{namespace}:{binding.source_ref}"
                            ),
                            determination_ref=(
                                f"tax-set:{namespace}:{item.source_invoice_key}"
                                if binding.operation
                                == "billing.withholding.tax_component"
                                else binding.determination_ref
                            ),
                            component_ref=(
                                f"component:{namespace}:{item.source_invoice_key}"
                                if binding.operation
                                == "billing.withholding.tax_component"
                                else binding.component_ref
                            ),
                        )
                        for binding in effect.replay_bindings
                    ),
                )
                for effect in item.historical_discharges
            ),
        )
        for item in cmd.manifest.receivables
    )
    lots = tuple(
        replace(
            lot,
            lot_id=f"{namespace}:{lot.lot_id}",
            source_identity=identity(lot.source_identity),
            partition=partition,
            source_settlement_key=f"{namespace}:{lot.source_settlement_key}",
            consumptions=tuple(
                replace(
                    use,
                    reference=f"{namespace}:{use.reference}",
                    target_ref=f"{namespace}:{use.target_ref}",
                )
                for use in lot.consumptions
            ),
        )
        for lot in cmd.manifest.cash_lots
    )
    manifest = replace(
        cmd.manifest,
        scope=scope,
        capture=replace(
            cmd.manifest.capture, installation_id=f"installation:{account_id}"
        ),
        population=tuple(identity(value) for value in cmd.manifest.population),
        dispositions=tuple(
            replace(value, identity=identity(value.identity), partition=partition)
            for value in cmd.manifest.dispositions
        ),
        controls=tuple(
            replace(value, partition=partition) for value in cmd.manifest.controls
        ),
        receivables=items,
        cash_lots=lots,
    )
    return replace(
        cmd, scope=scope, partition=partition, target=target, manifest=manifest
    )


def _import(session, scope, account_id):
    cmd = _command(session, scope, account_id)
    cash._prepare_window(session, scope, cmd)
    return cmd, cash._import_command(
        session,
        scope=scope,
        command=cmd,
        verifier=evidence.TrustedVerifier(),
        version=2,
    )


def _bases(session, scope, receipt):
    return tuple(
        q.get_opening_receivable_basis(session, scope=scope, basis_id=value)
        for value in receipt.basis_ids
    )


def _remaining(session, scope, basis):
    return q.get_opening_receivable_position(
        session, scope=scope, basis_id=basis.basis_id
    ).remaining.amount


def _model(plane, name):
    return getattr(models, ("Platform" if plane == "platform" else "") + name)


def _available_non_cash_credit(db, scope, account_id):
    selected = s._models(scope)
    version = db.execute(
        s._where_scope(
            select(selected.position_fact_v2.source_version)
            .where(selected.position_fact_v2.billing_account_id == account_id)
            .order_by(selected.position_fact_v2.source_version.desc())
            .limit(1),
            scope,
            selected.position_fact_v2,
        )
    ).scalar_one_or_none()
    if version is None:
        # A pristine account has no financial snapshot. Prove absence of actual
        # noncash effects before reporting its zero fixture balance.
        assert (
            db.execute(
                s._where_scope(
                    select(selected.non_cash_effect.id).where(
                        selected.non_cash_effect.billing_account_id == account_id
                    ),
                    scope,
                    selected.non_cash_effect,
                )
            ).first()
            is None
        )
        return Decimal("0")
    return get_receivable_position_v2(
        db,
        scope=scope,
        billing_account_id=account_id,
        source_version=version,
    ).available_non_cash_credit.amount


def _disable_fixture_append_before_insert(db, *tables):
    # Arrange deliberate late corruption before any INSERT queues deferred
    # events. Only UPDATE/DELETE append guards are disabled; admission and
    # production completeness triggers remain active. The fixture rolls this
    # transactional DDL back together with the rejected monetary operation.
    for table in tables:
        db.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"DISABLE TRIGGER {table.name}_append_only"
            )
        )


def _assert_fixture_append_restored(db, *tables):
    for table in tables:
        assert (
            db.scalar(
                text(
                    "SELECT tgenabled FROM pg_trigger "
                    "WHERE tgrelid=CAST(:table AS regclass) AND tgname=:name"
                ),
                {
                    "table": f"mod_billing.{table.name}",
                    "name": f"{table.name}_append_only",
                },
            )
            == "O"
        )


def _counts(session, plane, account_id):
    result = cash._counts(session, plane, account_id)
    for name in (
        "OpeningReceivableBasis",
        "OpeningHistoricalDischargeClaim",
        "ReceivableSourceClaim",
    ):
        model = _model(plane, name)
        result[name] = session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.billing_account_id == account_id)
        )
    return result


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_complete_partition_recognizes_residual_without_issuing_history(scratch, plane):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd, receipt = _import(db, scope, account)
        bases = _bases(db, scope, receipt)
        assert receipt.total_receivable == b._money("120")
        assert receipt.total_residual == b._money("60")
        assert receipt.manifest_fingerprint == opening_manifest_fingerprint_v2(
            cmd.manifest
        )
        assert receipt.request_digest == opening_account_command_digest_v2(cmd)
        assert [basis.initial_receivable.amount for basis in bases] == [
            Decimal("70"),
            Decimal("50"),
        ]
        assert [basis.source.original.grand_total.amount for basis in bases] == [
            Decimal("120"),
            Decimal("80"),
        ]
        assert [len(basis.source.historical_discharges) for basis in bases] == [2, 2]
        assert cash._position(db, scope, account).collectible_receivable == Decimal(
            "120"
        )
        assert cash._position(db, scope, account).available_credit == Decimal("60")
        for name in (
            "RatedObligation",
            "ConfirmedSettlement",
            "AllocationEffect",
            "WithholdingDischarge",
        ):
            model = _model(plane, name)
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.billing_account_id == account)
                )
                == 0
            )
        documents = db.scalars(
            select(_model(plane, "BillingDocument")).where(
                _model(plane, "BillingDocument").billing_account_id == account
            )
        ).all()
        assert len(documents) == 2
        assert all(row.authoring_mode == "imported_open_item" for row in documents)
        assert {row.document_number for row in documents} == {"LEGACY-0", "LEGACY-1"}
        db.commit()
        cash._prepare_window(db, scope, cmd)
        assert (
            cash._import_command(
                db,
                scope=scope,
                command=cmd,
                verifier=evidence.TrustedVerifier(),
                version=2,
            )
            == receipt
        )
        assert (
            q.get_opening_import_receipt_v2(
                db, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )
        for basis in bases:
            exposure = q.get_opening_receivable_exposure(
                db, scope=scope, basis_id=basis.basis_id
            )
            assert exposure.collectible_receivable == basis.initial_receivable
            assert not exposure.automated_collection_allowed
            assert exposure.collection.subject_ref is exposure.collection.due_at is None
            assert exposure.collection.service_ref is None
            assert exposure.financial_state == "open"


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_cross_scope_replay_and_both_role_boundaries_are_refused(scratch, plane):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd, receipt = _import(db, scope, account)
        basis = _bases(db, scope, receipt)[0]
        db.commit()
    other = TenantScope(b._create_tenants(scratch[0], 1)[0])
    with b._session(scratch[1], other) as db:
        for query, kwargs in (
            (q.get_opening_receivable_basis, {"basis_id": basis.basis_id}),
            (q.get_opening_receivable_position, {"basis_id": basis.basis_id}),
            (q.get_opening_receivable_exposure, {"basis_id": basis.basis_id}),
            (q.get_opening_import_receipt_v2, {"receipt_id": receipt.receipt_id}),
        ):
            with pytest.raises(BillingRuleViolation):
                query(db, scope=other, **kwargs)
        with pytest.raises(BillingRuleViolation):
            s.import_opening_account_v2(
                db, scope=other, command=cmd, verifier=evidence.TrustedVerifier()
            )
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        for name in (
            "OpeningReceivableBasis",
            "OpeningHistoricalDischargeClaim",
            "ReceivableSourceClaim",
        ):
            table = _model(plane, name).__table__
            assert not connection.scalar(
                text("SELECT has_table_privilege(:role,:table,'SELECT')"),
                {
                    "role": "platform_api" if plane == "tenant" else "app_user",
                    "table": f"mod_billing.{table.name}",
                },
            )
            if plane == "tenant":
                assert connection.scalar(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                        "WHERE oid=CAST(:table AS regclass)"
                    ),
                    {"table": f"mod_billing.{table.name}"},
                )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "field",
    [
        "operation",
        "scope",
        "command_digest",
        "manifest_fingerprint",
        "partition",
        "target",
        "installation_id",
        "source_authority_ref",
        "migration_authority_ref",
    ],
)
def test_exact_replay_still_requires_full_operation_qualified_verifier(
    scratch, plane, field
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd, receipt = _import(db, scope, account)
        db.commit()

        def mutate(result):
            if field == "operation":
                return cash.OpeningImportVerificationV1(
                    "billing.opening.import.v1",
                    result.scope,
                    result.command_digest,
                    result.manifest_fingerprint,
                    result.partition,
                    result.target,
                    result.installation_id,
                    result.migration_authority_ref,
                    result.source_authority_ref,
                    result.source_binding_proof_ref,
                    result.cash_lifecycle_proof_ref,
                    result.migration_authority_proof_ref,
                )
            changes = {
                "operation": "billing.opening.import.v1",
                "scope": TenantScope(uuid4()),
                "command_digest": "e" * 64,
                "manifest_fingerprint": "d" * 64,
                "partition": replace(result.partition, source_account_key="wrong"),
                "target": replace(result.target, posting_watermark="wrong"),
                "installation_id": "wrong",
                "source_authority_ref": "wrong",
                "migration_authority_ref": "wrong",
            }
            return replace(result, **{field: changes[field]})

        before = _counts(db, plane, account)
        for verifier in (None, evidence.TrustedVerifier(mutate)):
            with pytest.raises(BillingRuleViolation):
                cash._prepare_window(db, scope, cmd)
                cash._import_command(
                    db, scope=scope, command=cmd, verifier=verifier, version=2
                )
        assert _counts(db, plane, account) == before
        assert (
            q.get_opening_import_receipt_v2(
                db, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_stale_target_key_conflict_and_natural_invoice_identity_are_permanent(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd = _command(db, scope, account)
        before = _counts(db, plane, account)
        for target in (
            replace(cmd.target, posting_watermark="stale"),
            replace(cmd.target, expected_position_fingerprint="f" * 64),
        ):
            with pytest.raises((BillingRuleViolation, BillingConflict)):
                cash._prepare_window(db, scope, replace(cmd, target=target))
                cash._import_command(
                    db,
                    scope=scope,
                    command=replace(cmd, target=target),
                    verifier=evidence.TrustedVerifier(),
                    version=2,
                )
        assert _counts(db, plane, account) == before
        cash._prepare_window(db, scope, cmd)
        receipt = cash._import_command(
            db, scope=scope, command=cmd, verifier=evidence.TrustedVerifier(), version=2
        )
        db.commit()
        with pytest.raises((IdempotencyConflict, BillingConflict)):
            cash._prepare_window(
                db,
                scope,
                replace(cmd, occurred_at=cmd.occurred_at + b.timedelta(seconds=1)),
            )
            cash._import_command(
                db,
                scope=scope,
                command=replace(
                    cmd, occurred_at=cmd.occurred_at + b.timedelta(seconds=1)
                ),
                verifier=evidence.TrustedVerifier(),
                version=2,
            )
        other_account = cash._new_account(db, scope)
        other = _command(db, scope, other_account)
        invoice_map = {
            new.source_invoice_key: old.source_invoice_key
            for new, old in zip(
                other.manifest.receivables, cmd.manifest.receivables, strict=True
            )
        }
        manifest = replace(
            other.manifest,
            cash_lots=tuple(
                replace(
                    lot,
                    consumptions=tuple(
                        replace(
                            use,
                            target_ref=invoice_map[use.target_ref],
                        )
                        for use in lot.consumptions
                    ),
                )
                for lot in other.manifest.cash_lots
            ),
            capture=replace(
                other.manifest.capture,
                installation_id=cmd.manifest.capture.installation_id,
            ),
            receivables=tuple(
                replace(new, source_invoice_key=old.source_invoice_key)
                for new, old in zip(
                    other.manifest.receivables, cmd.manifest.receivables, strict=True
                )
            ),
        )
        other = replace(other, manifest=manifest)
        assert evidence.preview_opening_account_import_v2(
            other, verifier=evidence.TrustedVerifier()
        ).admissible
        # The second independently admissible candidate is outside the frozen
        # whole-scope manifest; refusal now precedes natural-source admission.
        with pytest.raises(BillingRuleViolation) as outside:
            cash._prepare_window(db, scope, other)
            cash._import_command(
                db,
                scope=scope,
                command=other,
                verifier=evidence.TrustedVerifier(),
                version=2,
            )
        assert outside.value.code == "billing.opening_window_manifest"
        source_table = _model(plane, "ReceivableSourceClaim").__table__
        original_claim = dict(
            db.execute(select(source_table).limit(1)).mappings().one()
        )
        duplicate_claim = {**original_claim, "id": uuid4()}
        # Exact backing survives; the permanent physical source key alone
        # differs from the primary key and must remain occupied.
        with cash._as_opening_principal(db):
            with pytest.raises(DBAPIError) as occupied:
                with db.begin_nested():
                    db.execute(source_table.insert().values(**duplicate_claim))
            assert occupied.value.orig.sqlstate == "23505"
        assert (
            q.get_opening_import_receipt_v2(
                db, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_historical_payment_and_withholding_cannot_be_replayed_as_live_money(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd, receipt = _import(db, scope, account)
        bases = _bases(db, scope, receipt)
        db.commit()
        before = _counts(db, plane, account)
        for lot in cmd.manifest.cash_lots:
            callback = replace(
                b._settlement_command(
                    account,
                    scope=scope,
                    source_key=lot.source_settlement_key,
                    amount=str(Decimal(lot.gross_minor).scaleb(-2)),
                ),
                source_system=lot.source_system,
            )
            with pytest.raises(BillingConflict):
                s.accept_settlement(
                    db,
                    scope=scope,
                    command=callback,
                    accepted_confirmation_evidence=frozenset({"provider_settled"}),
                )
        for basis in bases:
            prior = basis.source.historical_discharges[1]
            callback = replace(
                b._withholding_command(
                    account,
                    basis.document_id,
                    scope=scope,
                    suffix=str(uuid4()),
                    claimed="10",
                    accepted="10",
                ),
                source_owner=prior.source_system,
                source_ref=prior.live_identity,
            )
            with pytest.raises((BillingConflict, BillingRuleViolation)):
                s.accept_withholding_discharge(db, scope=scope, command=callback)
            # Change the remittance identity: the separately claimed Tax component
            # must still refuse replay of the same accepted historical determination.
            tax_callback = b._withholding_command(
                account,
                basis.document_id,
                scope=scope,
                suffix=basis.source.source_invoice_key,
                claimed="10",
                accepted="10",
            )
            with pytest.raises((BillingConflict, BillingRuleViolation)):
                s.accept_withholding_discharge(db, scope=scope, command=tax_callback)
        assert _counts(db, plane, account) == before
        assert cash._position(db, scope, account).collectible_receivable == Decimal(
            "120"
        )


def _credit(session, scope, document_id, amount):
    return s.issue_credit_note(
        session,
        scope=scope,
        command=IssueCreditNote(
            document_id,
            b._money(amount),
            b._money("0"),
            b._money(amount),
            "evidenced correction",
            "credit",
            evidence.NOW.date(),
            "billing-test",
            str(uuid4()),
            evidence.NOW,
            "credit.standard",
            "1",
        ),
        numbering=b.NUMBERING,
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_ordinary_allocate_reallocate_and_deallocate_use_imported_residual(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _, receipt = _import(db, scope, account)
        first, second = _bases(db, scope, receipt)
        settlement = s.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                account, scope=scope, source_key=str(uuid4()), amount="80"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        # Original gross is 120, but only the imported residual 70 is live room.
        with pytest.raises(BillingRuleViolation):
            s.allocate_settlement(
                db,
                scope=scope,
                command=AllocationCommand(
                    settlement.id,
                    first.document_id,
                    b._money("70.01"),
                    evidence.NOW,
                    str(uuid4()),
                ),
            )
        allocation = s.allocate_settlement(
            db,
            scope=scope,
            command=AllocationCommand(
                settlement.id,
                first.document_id,
                b._money("30"),
                evidence.NOW,
                str(uuid4()),
            ),
        )
        assert (_remaining(db, scope, first), _remaining(db, scope, second)) == (
            Decimal("40"),
            Decimal("50"),
        )
        s.reallocate_settlement(
            db,
            scope=scope,
            command=ReallocationCommand(
                settlement.id,
                first.document_id,
                second.document_id,
                b._money("10"),
                evidence.NOW,
                str(uuid4()),
            ),
        )
        assert (_remaining(db, scope, first), _remaining(db, scope, second)) == (
            Decimal("50"),
            Decimal("40"),
        )
        model = _model(plane, "AllocationEffect")
        positive = db.scalars(
            select(model).where(
                model.posting_group_id == allocation.id, model.amount_delta > 0
            )
        ).one()
        s.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(
                positive.id, b._money("20"), evidence.NOW, str(uuid4())
            ),
        )
        assert (_remaining(db, scope, first), _remaining(db, scope, second)) == (
            Decimal("70"),
            Decimal("40"),
        )
        db.commit()
        assert cash._position(db, scope, account).collectible_receivable == Decimal(
            "110"
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_mixed_cash_draw_partial_and_full_inverse_restore_imported_basis(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _, receipt = _import(db, scope, account)
        cash._seal_window(db, scope)
        basis = _bases(db, scope, receipt)[0]
        ordinary = s.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                account, scope=scope, source_key=str(uuid4()), amount="20"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        draw_command = ApplyAvailableCreditCommand(
            account, basis.document_id, b._money("70"), evidence.NOW, str(uuid4())
        )
        draw = s.apply_available_credit(db, scope=scope, command=draw_command)
        assert _remaining(db, scope, basis) == 0
        assert cash._position(db, scope, account).available_credit == Decimal("10")
        fact = cash_queries.get_accounting_fact_v4(
            db, scope=scope, posting_group_id=draw.id
        )
        assert fact.settlement_allocations[0].allocation.settlement_id == ordinary.id
        assert fact.opening_cash_effects[0].lot_id == receipt.lot_ids[0]
        assert fact.settlement_allocations[0].allocation.amount_delta.amount == Decimal(
            "20"
        )
        assert fact.opening_cash_effects[0].amount.amount == Decimal("50")
        assert (
            s.apply_available_credit(db, scope=scope, command=draw_command).id
            == draw.id
        )
        partial = cash._inverse(scope, draw.id, "25")
        inverse = s.reverse_cash_credit_draw(db, scope=scope, command=partial)
        assert (
            s.reverse_cash_credit_draw(db, scope=scope, command=partial).id
            == inverse.id
        )
        assert _remaining(db, scope, basis) == Decimal("25")
        s.reverse_cash_credit_draw(
            db, scope=scope, command=cash._inverse(scope, draw.id, "45")
        )
        assert _remaining(db, scope, basis) == Decimal("70")
        assert cash._position(db, scope, account).available_credit == Decimal("80")
        assert cash_queries.get_opening_cash_lot_position(
            db, scope=scope, lot_id=receipt.lot_ids[0]
        ).remaining == b._money("60")
        with pytest.raises(BillingRuleViolation):
            s.reverse_cash_credit_draw(
                db, scope=scope, command=cash._inverse(scope, draw.id, "0.01")
            )
        db.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_withholding_noncash_credit_and_credit_note_room_and_owner_inverses(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _, receipt = _import(db, scope, account)
        cash._seal_window(db, scope)
        basis = _bases(db, scope, receipt)[0]
        # Historical WHT is documentary; only this new discharge consumes room.
        wht_cmd = b._withholding_command(
            account,
            basis.document_id,
            scope=scope,
            suffix=str(uuid4()),
            claimed="10",
            accepted="10",
        )
        wht = s.accept_withholding_discharge(db, scope=scope, command=wht_cmd)
        grant = s.grant_non_cash_credit(
            db, scope=scope, command=noncash._grant(scope, account, amount="50")
        )
        applied = s.apply_non_cash_credit(
            db,
            scope=scope,
            command=noncash._apply(scope, grant.grant_id, basis.document_id, "20"),
        )
        credit = _credit(db, scope, basis.document_id, "30")
        assert _remaining(db, scope, basis) == Decimal("10")
        for amount in ("10.01", "70.01", "120"):
            with pytest.raises(BillingRuleViolation):
                _credit(db, scope, basis.document_id, amount)
        for amount in ("20.01", "70.01"):
            with pytest.raises(BillingRuleViolation):
                s.apply_non_cash_credit(
                    db,
                    scope=scope,
                    command=noncash._apply(
                        scope, grant.grant_id, basis.document_id, amount
                    ),
                )
        with pytest.raises(BillingRuleViolation):
            s.void_document(
                db,
                scope=scope,
                document_id=basis.document_id,
                actor_ref="fixture",
                occurred_at=evidence.NOW,
                source_ref=str(uuid4()),
            )
        correction = ReverseNonCashCreditApplication(
            scope,
            applied.effect_id,
            str(uuid4()),
            "finance",
            str(uuid4()),
            "1",
            "finance:1",
            "decision:inverse",
            evidence.NOW,
            b._money("5"),
        )
        s.reverse_non_cash_credit_application(db, scope=scope, command=correction)
        assert _remaining(db, scope, basis) == Decimal("15")
        s.reverse_non_cash_credit_application(
            db,
            scope=scope,
            command=replace(
                correction,
                idempotency_key=str(uuid4()),
                source_ref=str(uuid4()),
                amount=b._money("15"),
            ),
        )
        s.reverse_withholding_discharge(
            db,
            scope=scope,
            command=ReverseWithholdingDischarge(
                scope,
                wht.id,
                str(uuid4()),
                str(uuid4()),
                "finance",
                "inverse-authority",
                evidence.NOW,
            ),
        )
        s.void_document(
            db,
            scope=scope,
            document_id=credit.id,
            actor_ref="fixture",
            occurred_at=evidence.NOW,
            source_ref=str(uuid4()),
        )
        assert _remaining(db, scope, basis) == Decimal("70")
        assert cash._position(db, scope, account).available_credit == Decimal("60")
        db.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_one_outer_transaction_preserves_each_draws_historical_source_version(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _, receipt = _import(db, scope, account)
        basis = _bases(db, scope, receipt)[0]
        first = s.apply_available_credit(
            db,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account, basis.document_id, b._money("10"), evidence.NOW, str(uuid4())
            ),
        )
        first_exposure = q.get_opening_receivable_exposure(
            db, scope=scope, basis_id=basis.basis_id
        )
        assert first_exposure.collectible_receivable == b._money("60")
        wht = s.accept_withholding_discharge(
            db,
            scope=scope,
            command=b._withholding_command(
                account,
                basis.document_id,
                scope=scope,
                suffix=str(uuid4()),
                claimed="5",
                accepted="5",
            ),
        )
        grant = s.grant_non_cash_credit(
            db, scope=scope, command=noncash._grant(scope, account)
        )
        application = s.apply_non_cash_credit(
            db,
            scope=scope,
            command=noncash._apply(scope, grant.grant_id, basis.document_id, "5"),
        )
        credit = _credit(db, scope, basis.document_id, "5")
        s.void_document(
            db,
            scope=scope,
            document_id=credit.id,
            actor_ref="fixture",
            occurred_at=evidence.NOW,
            source_ref=str(uuid4()),
        )
        s.reverse_non_cash_credit_application(
            db,
            scope=scope,
            command=ReverseNonCashCreditApplication(
                scope,
                application.effect_id,
                str(uuid4()),
                "finance",
                str(uuid4()),
                "1",
                "finance:1",
                "inverse",
                evidence.NOW,
                b._money("5"),
            ),
        )
        s.reverse_withholding_discharge(
            db,
            scope=scope,
            command=ReverseWithholdingDischarge(
                scope,
                wht.id,
                str(uuid4()),
                str(uuid4()),
                "finance",
                "inverse-authority",
                evidence.NOW,
            ),
        )
        second = s.apply_available_credit(
            db,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account, basis.document_id, b._money("20"), evidence.NOW, str(uuid4())
            ),
        )
        second_exposure = q.get_opening_receivable_exposure(
            db, scope=scope, basis_id=basis.basis_id
        )
        assert second_exposure.collectible_receivable == b._money("40")
        assert first_exposure.source_version < second_exposure.source_version
        assert first_exposure.posting_group_watermark == first.id
        assert second_exposure.posting_group_watermark == second.id
        # Deferred database guards see every historical watermark at one commit.
        db.commit()
        model = _model(plane, "ReceivableExposureFactV2")
        rows = db.scalars(
            select(model)
            .where(
                model.document_id == basis.document_id,
                model.posting_group_id.in_((first.id, second.id)),
            )
            .order_by(model.source_version)
        ).all()
        assert len(rows) == 2
        assert [row.collectible_receivable for row in rows] == [
            Decimal("60"),
            Decimal("40"),
        ]
        assert [row.posting_group_id for row in rows] == [first.id, second.id]
        assert rows[0].source_version < rows[1].source_version


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_atomic_fault_after_cash_ar_claims_and_outbox_preserves_outer_work(
    scratch, plane, monkeypatch
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd = _command(db, scope, account)
        pending = s.create_billing_account(
            db,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        pending_id = pending.id
        before = _counts(db, plane, account)
        outbox = b.OutboxEvent if plane == "tenant" else b.PlatformOutboxEvent
        old_events = db.scalar(select(func.count()).select_from(outbox))
        real_emit = s._emit
        observed = []

        def fail_after_actual_event(*args, **kwargs):
            real_emit(*args, **kwargs)
            db.flush()
            counts = _counts(db, plane, account)
            assert counts["OpeningReceivableBasis"] == 2
            assert counts["OpeningCashLot"] == 2
            assert counts["CashSourceClaim"] == 2
            assert counts["OpeningHistoricalDischargeClaim"] > 0
            assert db.scalar(select(func.count()).select_from(outbox)) > old_events
            observed.append(counts)
            raise RuntimeError("fault after both opening lanes")

        with monkeypatch.context() as fault:
            fault.setattr(s, "_emit", fail_after_actual_event)
            with pytest.raises(RuntimeError, match="fault after both opening lanes"):
                cash._prepare_window(db, scope, cmd)
                cash._import_command(
                    db,
                    scope=scope,
                    command=cmd,
                    verifier=evidence.TrustedVerifier(),
                    version=2,
                )
        assert observed
        assert _counts(db, plane, account) == before
        assert db.scalar(select(func.count()).select_from(outbox)) == old_events
        assert (
            cash._retry_count(
                db, plane, "billing.opening.import.v2", cmd.idempotency_key
            )
            == 0
        )
        assert db.get(_model(plane, "BillingAccount"), pending_id) is not None
        cash._prepare_window(db, scope, cmd)
        receipt = cash._import_command(
            db, scope=scope, command=cmd, verifier=evidence.TrustedVerifier(), version=2
        )
        assert len(receipt.basis_ids) == len(receipt.lot_ids) == 2
        db.rollback()
        assert _counts(db, plane, account) == before
        assert (
            cash._retry_count(
                db, plane, "billing.opening.import.v2", cmd.idempotency_key
            )
            == 0
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize("version", [1, 2])
def test_concurrent_same_key_retries_converge_or_refuse_changed_evidence(
    scratch, plane, conflicting, version, monkeypatch
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        command = (
            cash._command(db, scope, account, amounts=(3000, 3000))
            if version == 1
            else _command(db, scope, account)
        )
        cash._prepare_window(db, scope, command)
    commands = (
        command,
        replace(command, occurred_at=command.occurred_at + b.timedelta(seconds=1))
        if conflicting
        else command,
    )
    barrier = Barrier(2)
    admission_barrier = Barrier(2)
    real_admit = s._admit_opening_window

    def synchronize_admission(*args, **kwargs):
        # Both Kernel lookups must miss before either waits on custody.
        admission_barrier.wait(timeout=20)
        return real_admit(*args, **kwargs)

    monkeypatch.setattr(s, "_admit_opening_window", synchronize_admission)
    results, failures = [], []

    def run(cmd):
        try:
            with cash._session(scratch, url, scope) as db:
                barrier.wait(timeout=20)
                cash._prepare_window(db, scope, cmd)
                receipt = cash._import_command(
                    db,
                    scope=scope,
                    command=cmd,
                    verifier=(
                        cash.TrustedVerifier()
                        if version == 1
                        else evidence.TrustedVerifier()
                    ),
                    version=version,
                )
                db.commit()
                results.append(receipt)
        except Exception as error:
            failures.append(error)

    threads = [Thread(target=run, args=(cmd,)) for cmd in commands]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=45)
        assert not thread.is_alive()
    if conflicting:
        assert len(results) == len(failures) == 1
        assert isinstance(failures[0], IdempotencyConflict)
    else:
        assert failures == []
        assert len(results) == 2
        assert results[0] == results[1]
    with cash._session(scratch, url, scope) as db:
        counts = _counts(db, plane, account)
        assert counts["OpeningImportReceipt"] == 1
        assert counts["OpeningReceivableBasis"] == (0 if version == 1 else 2)
        assert counts["OpeningCashLot"] == 2
        assert (
            cash._retry_count(
                db, plane, f"billing.opening.import.v{version}", command.idempotency_key
            )
            == 1
        )
        assert cash._position(db, scope, account).collectible_receivable == Decimal(
            "0" if version == 1 else "120"
        )
        assert cash._position(db, scope, account).available_credit == Decimal("60")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_full_unwind_requires_all_owner_inverses_and_never_releases_natural_claims(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd, receipt = _import(db, scope, account)
        first, second = _bases(db, scope, receipt)
        draw = s.apply_available_credit(
            db,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account, first.document_id, b._money("10"), evidence.NOW, str(uuid4())
            ),
        )
        wht = s.accept_withholding_discharge(
            db,
            scope=scope,
            command=b._withholding_command(
                account,
                second.document_id,
                scope=scope,
                suffix=str(uuid4()),
                claimed="5",
                accepted="5",
            ),
        )
        grant = s.grant_non_cash_credit(
            db, scope=scope, command=noncash._grant(scope, account)
        )
        applied = s.apply_non_cash_credit(
            db,
            scope=scope,
            command=noncash._apply(scope, grant.grant_id, second.document_id, "5"),
        )
        credit = _credit(db, scope, second.document_id, "5")
        refund = s.record_opening_cash_refund(
            db,
            scope=scope,
            command=cash._refund(scope, receipt.lot_ids[0], "5"),
            verifier=cash.TrustedVerifier(),
        )
        unwind = ReverseOpeningAccountImportV1(
            scope,
            receipt.receipt_id,
            "fresh-migration-authority",
            str(uuid4()),
            evidence.NOW,
        )

        def refused():
            with pytest.raises(BillingRuleViolation):
                s.reverse_opening_account_import_v2(
                    db, scope=scope, command=unwind, verifier=evidence.TrustedVerifier()
                )

        refused()
        s.reverse_cash_credit_draw(
            db, scope=scope, command=cash._inverse(scope, draw.id, "10")
        )
        refused()
        s.reverse_withholding_discharge(
            db,
            scope=scope,
            command=ReverseWithholdingDischarge(
                scope,
                wht.id,
                str(uuid4()),
                str(uuid4()),
                "finance",
                "inverse",
                evidence.NOW,
            ),
        )
        refused()
        s.reverse_non_cash_credit_application(
            db,
            scope=scope,
            command=ReverseNonCashCreditApplication(
                scope,
                applied.effect_id,
                str(uuid4()),
                "finance",
                str(uuid4()),
                "1",
                "finance:1",
                "inverse",
                evidence.NOW,
                b._money("5"),
            ),
        )
        refused()
        s.void_document(
            db,
            scope=scope,
            document_id=credit.id,
            actor_ref="fixture",
            occurred_at=evidence.NOW,
            source_ref=str(uuid4()),
        )
        assert [_remaining(db, scope, basis) for basis in (first, second)] == [
            Decimal("70"),
            Decimal("50"),
        ]
        refused()
        s.reduce_non_cash_credit_grant(
            db,
            scope=scope,
            command=ReduceNonCashCreditGrant(
                scope,
                grant.grant_id,
                str(uuid4()),
                "finance",
                str(uuid4()),
                "1",
                "finance:1",
                "unused-grant-reduction",
                evidence.NOW,
            ),
        )
        s.correct_opening_cash_refund(
            db,
            scope=scope,
            command=cash.CorrectOpeningCashRefundV1(
                scope,
                refund.effect_id,
                refund.amount,
                cash.OpeningCashMovementEvidenceV1(
                    "payments", str(uuid4()), "2", "c" * 64, "corrected"
                ),
                "refund-correction-authority",
                str(uuid4()),
                evidence.NOW,
            ),
            verifier=cash.TrustedVerifier(),
        )
        with pytest.raises(BillingRuleViolation):
            s.reverse_opening_account_import(
                db, scope=scope, command=unwind, verifier=cash.TrustedVerifier()
            )
        with pytest.raises(BillingRuleViolation):
            s.reverse_posting_group(
                db,
                scope=scope,
                command=cash.ReversePostingGroupCommand(
                    receipt.posting_group_id, evidence.NOW, str(uuid4())
                ),
            )
        inverse = s.reverse_opening_account_import_v2(
            db, scope=scope, command=unwind, verifier=evidence.TrustedVerifier()
        )
        assert inverse.original_receipt_id == receipt.receipt_id
        assert inverse.receipt_kind == "unwind"
        assert cash._position(db, scope, account).collectible_receivable == 0
        assert cash._position(db, scope, account).available_credit == 0
        assert _available_non_cash_credit(db, scope, account) == 0
        for basis in (first, second):
            position = q.get_opening_receivable_position(
                db, scope=scope, basis_id=basis.basis_id
            )
            assert position.unwound
            assert position.remaining == b._money("0")
        assert (
            s.reverse_opening_account_import_v2(
                db, scope=scope, command=unwind, verifier=evidence.TrustedVerifier()
            )
            == inverse
        )
        cash._prepare_window(db, scope, cmd)
        assert (
            cash._import_command(
                db,
                scope=scope,
                command=cmd,
                verifier=evidence.TrustedVerifier(),
                version=2,
            )
            == receipt
        )
        with pytest.raises((BillingConflict, BillingRuleViolation)):
            cash._prepare_window(db, scope, replace(cmd, idempotency_key=str(uuid4())))
            cash._import_command(
                db,
                scope=scope,
                command=replace(cmd, idempotency_key=str(uuid4())),
                verifier=evidence.TrustedVerifier(),
                version=2,
            )
        for name, expected in (("OpeningReceivableBasis", 2), ("CashSourceClaim", 2)):
            model = _model(plane, name)
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.billing_account_id == account)
                )
                == expected
            )
        assert _counts(db, plane, account)["OpeningHistoricalDischargeClaim"] > 0
        db.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_owner_and_online_sql_cannot_amend_or_delete_documentary_history(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _import(db, scope, account)
        db.commit()
    for name in (
        "OpeningReceivableBasis",
        "OpeningHistoricalDischargeClaim",
        "ReceivableExposureFactV2",
        "ReceivableSourceClaim",
    ):
        table = _model(plane, name).__table__
        for role_url in (url, scratch[0]):
            with b._session(role_url, scope) as db:
                if isinstance(scope, TenantScope):
                    # Persist the harness's session-level tenant GUC before a
                    # deliberate failing statement can roll its bootstrap back.
                    assert db.scalar(
                        text("SELECT current_setting('app.current_tenant', true)")
                    ) == str(scope.tenant_id)
                    db.commit()
                count = db.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.billing_account_id == account)
                )
                assert count > 0
                for statement in (
                    table.update()
                    .where(table.c.billing_account_id == account)
                    .values(billing_account_id=account),
                    table.delete().where(table.c.billing_account_id == account),
                ):
                    with pytest.raises(DBAPIError):
                        db.execute(statement)
                    db.rollback()
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(table)
                        .where(table.c.billing_account_id == account)
                    )
                    == count
                )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_raw_sql_claim_affinity_has_an_exact_insertable_positive_control(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        other_account = cash._new_account(db, scope)
        _import(db, scope, account)
        db.commit()
    table = _model(plane, "OpeningHistoricalDischargeClaim").__table__
    keeper = _model(plane, "ReceivableSourceClaim").__table__
    # Only the isolated test harness may select the restricted opening role;
    # the real schema owner and runtime roles cannot assume it.
    engine = create_engine(
        b._url_for(b._superuser_url(), make_url(scratch[0]).database)
    )
    with engine.connect() as connection:
        original = dict(
            connection.execute(
                select(table).where(table.c.billing_account_id == account).limit(1)
            )
            .mappings()
            .one()
        )
        connection.rollback()

        def remove_fixture_claim():
            connection.execute(
                text(
                    f"ALTER TABLE mod_billing.{keeper.name} "
                    f"DISABLE TRIGGER {keeper.name}_append_only"
                )
            )
            connection.execute(
                keeper.delete().where(keeper.c.historical_claim_id == original["id"])
            )
            connection.execute(
                text(
                    f"ALTER TABLE mod_billing.{keeper.name} "
                    f"ENABLE TRIGGER {keeper.name}_append_only"
                )
            )
            connection.execute(
                text(
                    f"ALTER TABLE mod_billing.{table.name} "
                    f"DISABLE TRIGGER {table.name}_append_only"
                )
            )
            connection.execute(table.delete().where(table.c.id == original["id"]))
            connection.execute(
                text(
                    f"ALTER TABLE mod_billing.{table.name} "
                    f"ENABLE TRIGGER {table.name}_append_only"
                )
            )

        def select_opening_principal():
            # Fixture surgery is technical-owner work; candidate admission must
            # exercise the actual restricted principal bound to this window.
            principal = scratch.principals[plane][0]
            connection.execute(text(f'SET LOCAL ROLE "{principal}"'))
            assert connection.scalar(text("SELECT current_user")) == principal
            if isinstance(scope, TenantScope):
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(scope.tenant_id)},
                )

        remove_fixture_claim()
        select_opening_principal()
        connection.execute(table.insert().values(**{**original, "id": uuid4()}))
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        connection.rollback()
        for changes in ({"billing_account_id": other_account}, {"basis_id": uuid4()}):
            remove_fixture_claim()
            select_opening_principal()
            with pytest.raises(DBAPIError) as error:
                connection.execute(
                    table.insert().values(**{**original, "id": uuid4(), **changes})
                )
            # Immediate owner admission is stronger than the later FK: it
            # verifies both existence and the exact account/evidence binding.
            assert error.value.orig.sqlstate == "23514"
            assert (
                "historical replay claim requires its exact immutable owner binding"
                in str(error.value.orig)
            )
            connection.rollback()
        select_opening_principal()
        with pytest.raises(DBAPIError) as error:
            connection.execute(table.insert().values(**{**original, "id": uuid4()}))
        assert error.value.orig.sqlstate == "23505"
        connection.rollback()
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_raw_sql_cash_draw_capacity_uses_residual_not_original_invoice_gross(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd = _command(db, scope, account)
        first_lot, second_lot = cmd.manifest.cash_lots
        abundant = replace(
            first_lot,
            gross_minor=24000,
            residual_minor=20000,
            confirmation=replace(first_lot.confirmation, cash_amount_minor=24000),
        )
        dispositions = tuple(
            replace(value, residual_minor=20000)
            if value.identity == abundant.source_identity
            else value
            for value in cmd.manifest.dispositions
        )
        manifest = replace(
            cmd.manifest,
            cash_lots=(abundant, second_lot),
            dispositions=dispositions,
            controls=(
                replace(cmd.manifest.controls[0], available_cash_credit_minor=20000),
            ),
        )
        cash._prepare_window(db, scope, replace(cmd, manifest=manifest))
        receipt = cash._import_command(
            db,
            scope=scope,
            command=replace(cmd, manifest=manifest),
            verifier=evidence.TrustedVerifier(),
            version=2,
        )
        basis = _bases(db, scope, receipt)[0]
        draw = s.apply_available_credit(
            db,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account, basis.document_id, b._money("10"), evidence.NOW, str(uuid4())
            ),
        )
        effect = cash_queries.get_accounting_fact_v4(
            db, scope=scope, posting_group_id=draw.id
        ).opening_cash_effects[0]
        assert _remaining(db, scope, basis) == Decimal("60")
        assert cash_queries.get_opening_cash_lot_position(
            db, scope=scope, lot_id=receipt.lot_ids[0]
        ).remaining == b._money("190")
        db.commit()
    table = _model(plane, "OpeningCashEffect").__table__
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
            return {
                **deepcopy(original),
                "id": fact.effect_id,
                "sequence": fact.sequence,
                "amount_delta": fact.availability_delta.amount,
                "evidence": payload,
                "evidence_digest": b.fingerprint_of(payload),
            }

        # The positive probe fits both current cash and initial residual room.
        # Roll back before deferred accounting completeness: no probe commits.
        connection.execute(table.insert().values(**trial("60")))
        connection.rollback()
        with pytest.raises(DBAPIError) as error:
            connection.execute(table.insert().values(**trial("60.01")))
        assert error.value.orig.sqlstate == "23514"
        connection.rollback()
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "missing",
    [
        "AccountingFact",
        "ReceivablePositionFact",
        "ReceivablePositionFactV2",
        "ReceivableExposureFactV2",
        "OpeningHistoricalDischargeClaim",
    ],
)
def test_deferred_completeness_refuses_missing_real_opening_artifacts(
    scratch, plane, missing, monkeypatch
):
    scope, url = noncash._scope_url(scratch, plane)
    omitted_model = _model(plane, missing)
    role_url = scratch[0] if missing == "OpeningHistoricalDischargeClaim" else url
    with cash._session(scratch, role_url, scope) as db:
        account = cash._new_account(db, scope)
        before = _counts(db, plane, account)
        actual_add = db.add
        omitted = []

        def omit_actual_artifact(instance, *args, **kwargs):
            if isinstance(instance, omitted_model):
                omitted.append(instance.id)
                return None
            return actual_add(instance, *args, **kwargs)

        restored_tables = ()
        if missing == "OpeningHistoricalDischargeClaim":
            history = omitted_model.__table__
            keeper = _model(plane, "ReceivableSourceClaim").__table__
            restored_tables = (history, keeper)
            _disable_fixture_append_before_insert(db, *restored_tables)
            # First pass all genuine runtime proof reads, then remove one real
            # stored binding. Early service verification is intentionally kept.
            receipt = _import(db, scope, account)[1]
            identity = db.scalar(
                select(history.c.id)
                .where(history.c.billing_account_id == account)
                .limit(1)
            )
            assert identity is not None
            omitted.append(identity)
            db.execute(keeper.delete().where(keeper.c.historical_claim_id == identity))
            db.execute(history.delete().where(history.c.id == identity))
        else:
            with monkeypatch.context() as fault:
                fault.setattr(db, "add", omit_actual_artifact)
                receipt = _import(db, scope, account)[1]
        assert omitted
        with pytest.raises(DBAPIError) as error:
            db.commit()
        assert error.value.orig.sqlstate == "23514"
        db.rollback()
        _assert_fixture_append_restored(db, *restored_tables)
        assert _counts(db, plane, account) == before
        with pytest.raises(BillingRuleViolation):
            q.get_opening_import_receipt_v2(
                db, scope=scope, receipt_id=receipt.receipt_id
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "tamper",
    [
        "basis_original",
        "basis_tax_mapping",
        "receipt_target_currency",
        "receipt_target_precision",
        "accounting_amount",
        "accounting_kind",
        "exposure_state",
        "exposure_owner",
    ],
)
def test_public_queries_refuse_semantic_corruption_with_recomputed_kernel_hash(
    scratch, plane, tamper
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _, receipt = _import(db, scope, account)
        basis = _bases(db, scope, receipt)[0]
        db.commit()
    if tamper.startswith("basis"):
        table = _model(plane, "OpeningReceivableBasis").__table__
        predicate = table.c.id == basis.basis_id
        payload_column, digest_column = "evidence", "evidence_digest"

        def read(db):
            return q.get_opening_receivable_basis(
                db, scope=scope, basis_id=basis.basis_id
            )
    elif tamper.startswith("receipt"):
        table = _model(plane, "OpeningImportReceipt").__table__
        predicate = table.c.id == receipt.receipt_id
        payload_column, digest_column = "evidence", "evidence_digest"

        def read(db):
            return q.get_opening_import_receipt_v2(
                db, scope=scope, receipt_id=receipt.receipt_id
            )
    elif tamper.startswith("accounting"):
        table = _model(plane, "AccountingFact").__table__
        predicate = table.c.posting_group_id == receipt.posting_group_id
        payload_column, digest_column = "fact_payload", "fact_digest"

        def read(db):
            return q.get_accounting_fact_v5(
                db, scope=scope, posting_group_id=receipt.posting_group_id
            )
    else:
        table = _model(plane, "ReceivableExposureFactV2").__table__
        predicate = table.c.basis_id == basis.basis_id
        payload_column, digest_column = "evidence", "evidence_digest"

        def read(db):
            return q.get_opening_receivable_exposure(
                db, scope=scope, basis_id=basis.basis_id
            )

    with cash._session(scratch, scratch[0], scope) as owner:
        untouched = read(owner)
        original = owner.execute(select(table).where(predicate)).mappings().one()
        payload = deepcopy(original[payload_column])
        if tamper == "basis_original":
            payload["source"]["original"]["documentary_digest"] = "f" * 64
        elif tamper == "basis_tax_mapping":
            payload["source"]["tax_mapping_version"] = "unattested-tax-map"
        elif tamper.startswith("receipt"):
            field, value = (
                ("currency", "USD")
                if tamper == "receipt_target_currency"
                else ("precision", 3)
            )
            payload["target"][field] = value
            payload["verification"]["target"][field] = value
        elif tamper == "accounting_amount":
            assert payload["effects"]
            payload["effects"][0]["amount_delta"]["amount"] = "0.01"
        elif tamper == "accounting_kind":
            payload["effect_kind"] = "opening_account_import_reversed"
        elif tamper == "exposure_state":
            assert payload["collectible_receivable"]["amount"] != "0.00"
            payload["financial_state"] = "resolved"
        else:
            payload["source_owner"] = "unattested-owner"
        if tamper.startswith("exposure"):
            semantic = deepcopy(payload)
            semantic.pop("state_fingerprint")
            payload["state_fingerprint"] = b.fingerprint_of(semantic)
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"DISABLE TRIGGER {table.name}_append_only"
            )
        )
        owner.execute(
            table.update()
            .where(predicate)
            .values(
                **{payload_column: payload, digest_column: b.fingerprint_of(payload)}
            )
        )
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"ENABLE TRIGGER {table.name}_append_only"
            )
        )
        with pytest.raises(BillingRuleViolation):
            read(owner)
        owner.rollback()
        assert read(owner) == untouched


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_accounting_v5_distinguishes_residual_recognition_from_new_cash_or_revenue(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _, receipt = _import(db, scope, account)
        fact = q.get_accounting_fact_v5(
            db, scope=scope, posting_group_id=receipt.posting_group_id
        )
        assert fact.fact_version == 5
        assert fact.effect_kind == "opening_account_imported"
        assert fact.import_receipt == receipt
        assert fact.receivable_bases == _bases(db, scope, receipt)
        assert sum(
            (basis.initial_receivable.amount for basis in fact.receivable_bases),
            Decimal(0),
        ) == Decimal("120")
        assert sum(
            (effect.amount.amount for effect in fact.opening_cash_effects), Decimal(0)
        ) == Decimal("60")
        assert all(
            effect.effect_kind == "recognition" for effect in fact.opening_cash_effects
        )
        assert fact.corrects_posting_group_id is None
        for query in (cash_queries.get_accounting_fact_v4,):
            with pytest.raises(BillingRuleViolation):
                query(db, scope=scope, posting_group_id=receipt.posting_group_id)
        db.commit()
        assert (
            q.get_accounting_fact_v5(
                db, scope=scope, posting_group_id=receipt.posting_group_id
            )
            == fact
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_historical_credit_and_noncash_sources_refuse_live_replay_across_versions(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd = _command(db, scope, account)
        first, second = cmd.manifest.receivables
        credit_key, grant_key, apply_key = (f"historical:{uuid4()}" for _ in range(3))
        prior_credit = replace(
            first.historical_discharges[1],
            kind="credit_note",
            source_system="dotmac-billing",
            source_key=credit_key,
            live_identity=credit_key,
            replay_bindings=(
                evidence.OpeningReplayBindingV1(
                    "billing.credit_note.issue", "dotmac-billing", credit_key, "d" * 64
                ),
            ),
        )
        prior_noncash = replace(
            second.historical_discharges[1],
            kind="non_cash_credit",
            source_system="customer-care",
            source_key=grant_key,
            live_identity=apply_key,
            replay_bindings=(
                evidence.OpeningReplayBindingV1(
                    "billing.non_cash_credit.grant",
                    "customer-care",
                    grant_key,
                    "e" * 64,
                ),
                evidence.OpeningReplayBindingV1(
                    "billing.non_cash_credit.apply",
                    "customer-care",
                    apply_key,
                    "f" * 64,
                ),
            ),
        )
        manifest = replace(
            cmd.manifest,
            receivables=(
                replace(
                    first,
                    historical_discharges=(
                        first.historical_discharges[0],
                        prior_credit,
                    ),
                ),
                replace(
                    second,
                    historical_discharges=(
                        second.historical_discharges[0],
                        prior_noncash,
                    ),
                ),
            ),
        )
        cash._prepare_window(db, scope, replace(cmd, manifest=manifest))
        receipt = cash._import_command(
            db,
            scope=scope,
            command=replace(cmd, manifest=manifest),
            verifier=evidence.TrustedVerifier(),
            version=2,
        )
        first_basis, second_basis = _bases(db, scope, receipt)
        db.commit()
        with pytest.raises((BillingConflict, BillingRuleViolation)):
            s.issue_credit_note(
                db,
                scope=scope,
                command=IssueCreditNote(
                    first_basis.document_id,
                    b._money("10"),
                    b._money("0"),
                    b._money("10"),
                    "historical callback",
                    "credit",
                    evidence.NOW.date(),
                    "billing-test",
                    credit_key,
                    evidence.NOW,
                    "credit.standard",
                    "1",
                ),
                numbering=b.NUMBERING,
            )
        old_grant = replace(
            noncash._grant(scope, account, amount="10"),
            source_owner="customer-care",
            source_ref=grant_key,
        )
        for version in ("v1", "different-source-version"):
            with pytest.raises((BillingConflict, BillingRuleViolation)):
                s.grant_non_cash_credit(
                    db,
                    scope=scope,
                    command=replace(
                        old_grant, source_version=version, idempotency_key=str(uuid4())
                    ),
                )
        live_grant = s.grant_non_cash_credit(
            db, scope=scope, command=noncash._grant(scope, account, amount="10")
        )
        callback = replace(
            noncash._apply(scope, live_grant.grant_id, second_basis.document_id, "10"),
            source_owner="customer-care",
            source_ref=apply_key,
        )
        with pytest.raises((BillingConflict, BillingRuleViolation)):
            s.apply_non_cash_credit(db, scope=scope, command=callback)
        assert [
            _remaining(db, scope, value) for value in (first_basis, second_basis)
        ] == [Decimal("70"), Decimal("50")]
        assert cash._position(db, scope, account).available_credit == Decimal("60")
        assert _available_non_cash_credit(db, scope, account) == Decimal("10")
        db.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_expired_retry_record_cannot_free_receivable_or_historical_source_identity(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd, receipt = _import(db, scope, account)
        db.commit()
    ledger = b.IdempotencyRecord if plane == "tenant" else b.PlatformIdempotencyRecord
    engine = create_engine(scratch[0])
    with engine.begin() as connection:
        deleted = connection.execute(
            ledger.__table__.delete().where(
                ledger.scope == "billing.opening.import.v2",
                ledger.key == cmd.idempotency_key,
            )
        )
        assert deleted.rowcount == 1
    engine.dispose()
    with cash._session(scratch, url, scope) as db:
        before = _counts(db, plane, account)
        with pytest.raises((BillingConflict, BillingRuleViolation)):
            cash._prepare_window(db, scope, cmd)
            cash._import_command(
                db,
                scope=scope,
                command=cmd,
                verifier=evidence.TrustedVerifier(),
                version=2,
            )
        assert _counts(db, plane, account) == before
        assert (
            q.get_opening_import_receipt_v2(
                db, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )
        assert [
            _remaining(db, scope, value) for value in _bases(db, scope, receipt)
        ] == [Decimal("70"), Decimal("50")]


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_real_bi7_upgrade_preserves_cash_and_accounting_v1_through_v4_history(
    plane, monkeypatch
):
    from dotmac_billing._receivable_backing import ReceivableBacking

    from alembic import command as alembic_command

    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with b._scratch_database(
        (selection,), target_revision="bi_0007_opening_cash"
    ) as urls:
        scope, url = noncash._scope_url(urls, plane)
        prefix = "" if plane == "tenant" else "platform_"

        def native_predecessor_backing(db, *, scope, document_id, source_version=None):
            # Only the historical fixture uses the accepted native backing math.
            # bi7 cannot contain an imported AR basis. Restore the real resolver
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

        original_window_admission = s._admit_opening_window
        with b._session(urls[0], scope) as catalog:
            assert (
                catalog.scalar(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"mod_billing.{prefix}opening_windows"},
                )
                is None
            )
        with predecessor_obligation_columns(), monkeypatch.context() as historical:
            # Source-compatibility for the verified bi7 fixture only. Its real
            # role, trust, money and existing SQL guards remain unchanged.
            historical.setattr(s, "_admit_opening_window", lambda *args, **kwargs: None)
            historical.setattr(
                s, "resolve_receivable_backing", native_predecessor_backing
            )
            historical.setattr(
                s, "_require_historical_source_unclaimed", lambda *args, **kwargs: None
            )
            with b._session(url, scope) as db:
                account = cash._new_account(db, scope)
                old_command = cash._command(db, scope, account)
                old_receipt = s.import_opening_account(
                    db,
                    scope=scope,
                    command=old_command,
                    verifier=cash.TrustedVerifier(),
                )
                invoice = cash._invoice(db, scope, account)
                s.apply_available_credit(
                    db,
                    scope=scope,
                    command=ApplyAvailableCreditCommand(
                        account, invoice.id, b._money("10"), evidence.NOW, str(uuid4())
                    ),
                )
                original_wht = s.accept_withholding_discharge(
                    db,
                    scope=scope,
                    command=b._withholding_command(
                        account,
                        invoice.id,
                        scope=scope,
                        suffix=str(uuid4()),
                        claimed="5",
                        accepted="5",
                    ),
                )
                grant = s.grant_non_cash_credit(
                    db, scope=scope, command=noncash._grant(scope, account)
                )
                application = s.apply_non_cash_credit(
                    db,
                    scope=scope,
                    command=noncash._apply(scope, grant.grant_id, invoice.id, "20"),
                )
                original_wht_id = original_wht.id
                wht_correction = ReverseWithholdingDischarge(
                    scope,
                    original_wht_id,
                    str(uuid4()),
                    str(uuid4()),
                    "finance",
                    "historical-correction",
                    evidence.NOW,
                )
                s.reverse_withholding_discharge(db, scope=scope, command=wht_correction)
                s.accept_withholding_discharge(
                    db,
                    scope=scope,
                    command=b._withholding_command(
                        account,
                        invoice.id,
                        scope=scope,
                        suffix=str(uuid4()),
                        claimed="5",
                        accepted="5",
                    ),
                )
                nc_correction = ReverseNonCashCreditApplication(
                    scope,
                    application.effect_id,
                    str(uuid4()),
                    "finance",
                    str(uuid4()),
                    "1",
                    "finance",
                    "historical-correction",
                    evidence.NOW,
                    b._money("5"),
                )
                s.reverse_non_cash_credit_application(
                    db, scope=scope, command=nc_correction
                )
                s.apply_non_cash_credit(
                    db,
                    scope=scope,
                    command=noncash._apply(scope, grant.grant_id, invoice.id, "5"),
                )
                nc_reduction = ReduceNonCashCreditGrant(
                    scope,
                    grant.grant_id,
                    str(uuid4()),
                    "finance",
                    str(uuid4()),
                    "1",
                    "finance",
                    "historical-reduction",
                    evidence.NOW,
                    b._money("5"),
                )
                s.reduce_non_cash_credit_grant(db, scope=scope, command=nc_reduction)
                credit = _credit(db, scope, invoice.id, "10")
                s.void_document(
                    db,
                    scope=scope,
                    document_id=credit.id,
                    actor_ref="fixture",
                    occurred_at=evidence.NOW,
                    source_ref=str(uuid4()),
                )
                db.commit()
        engine = create_engine(urls[0])
        ledger = (
            b.IdempotencyRecord if plane == "tenant" else b.PlatformIdempotencyRecord
        )
        outbox = b.OutboxEvent if plane == "tenant" else b.PlatformOutboxEvent
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
            assert {1, 2, 3, 4}.issubset(
                {row["fact_version"] for row in before[f"{prefix}accounting_facts"]}
            )
            assert before[f"{prefix}opening_cash_lots"]
            assert before[f"{prefix}opening_cash_effects"]
            assert before[f"{prefix}cash_source_claims"]
            assert before[f"{prefix}withholding_discharges"]
            assert before[f"{prefix}non_cash_credit_effects"]
            ledger_before = (
                connection.execute(select(ledger.__table__)).mappings().all()
            )
            outbox_before = (
                connection.execute(select(outbox.__table__)).mappings().all()
            )
            assert ledger_before and outbox_before
            assert f"{prefix}receivable_source_claims" not in names
            expected_sources = set()

            def expect_source(namespace, owner, key1, key2, row, pointer):
                expected_sources.add(
                    (
                        namespace,
                        owner,
                        key1,
                        key2,
                        row["billing_account_id"],
                        pointer,
                        row["id"],
                    )
                )

            for row in before[f"{prefix}withholding_discharges"]:
                expect_source(
                    "withholding.source",
                    row["source_owner"],
                    row["source_ref"],
                    "",
                    row,
                    "withholding_discharge_id",
                )
                if row["reverses_discharge_id"] is None:
                    expect_source(
                        "withholding.tax_component",
                        row["tax_source_owner"],
                        row["determination_ref"],
                        row["component_ref"],
                        row,
                        "withholding_discharge_id",
                    )
            for row in before[f"{prefix}non_cash_credit_grants"]:
                expect_source(
                    "non_cash.grant",
                    row["source_owner"],
                    row["source_ref"],
                    "",
                    row,
                    "non_cash_grant_id",
                )
            effects = before[f"{prefix}non_cash_credit_effects"]
            assert {
                "grant",
                "application",
                "application_reversal",
                "unused_reduction",
            }.issubset({row["effect_kind"] for row in effects})
            for row in effects:
                expect_source(
                    "non_cash.effect",
                    row["source_owner"],
                    row["source_ref"],
                    "",
                    row,
                    "non_cash_effect_id",
                )
            documents = {row["id"]: row for row in before[f"{prefix}documents"]}
            credit_roots = [
                row
                for row in before[f"{prefix}invoice_document_facts"]
                if row["fact_version"] == 1
                and documents[row["document_id"]]["document_kind"] == "credit_note"
            ]
            assert credit_roots
            for row in credit_roots:
                # The actual document FK supplies the account for a documentary fact.
                row = {
                    **row,
                    "billing_account_id": documents[row["document_id"]][
                        "billing_account_id"
                    ],
                }
                expect_source(
                    "credit_note.issue",
                    "dotmac-billing",
                    row["fact_payload"]["correlation_id"],
                    "",
                    row,
                    "credit_document_fact_id",
                )
        assert s._admit_opening_window is original_window_admission
        alembic_command.upgrade(
            cash.predecessor._config((selection,)), "bi_0009_opening_window"
        )
        with engine.connect() as connection:
            for name in names:
                assert (
                    connection.execute(
                        text(
                            f"SELECT to_jsonb(x) FROM mod_billing.{name} x ORDER BY id"
                        )
                    )
                    .scalars()
                    .all()
                    == before[name]
                ), name
            assert (
                connection.execute(select(ledger.__table__)).mappings().all()
                == ledger_before
            )
            assert (
                connection.execute(select(outbox.__table__)).mappings().all()
                == outbox_before
            )
        with b._session(url, scope) as db:
            source_model = _model(plane, "ReceivableSourceClaim")
            actual_sources = set()
            pointers = (
                "historical_claim_id",
                "withholding_discharge_id",
                "non_cash_grant_id",
                "non_cash_effect_id",
                "credit_document_fact_id",
            )
            for row in db.scalars(select(source_model)).all():
                populated = [
                    (name, getattr(row, name))
                    for name in pointers
                    if getattr(row, name) is not None
                ]
                assert len(populated) == 1
                pointer, identity = populated[0]
                actual_sources.add(
                    (
                        row.namespace,
                        row.source_owner,
                        row.key1,
                        row.key2,
                        str(row.billing_account_id),
                        pointer,
                        str(identity),
                    )
                )
            assert actual_sources == expected_sources
            assert (
                s.reverse_withholding_discharge(
                    db, scope=scope, command=wht_correction
                ).reverses_discharge_id
                == original_wht_id
            )
            assert (
                s.reverse_non_cash_credit_application(
                    db, scope=scope, command=nc_correction
                ).original_application_id
                == application.effect_id
            )
            assert (
                s.reduce_non_cash_credit_grant(
                    db, scope=scope, command=nc_reduction
                ).effect_kind
                == "unused_reduction"
            )
            assert (
                s.import_opening_account(
                    db,
                    scope=scope,
                    command=old_command,
                    verifier=cash.TrustedVerifier(),
                )
                == old_receipt
            )
            assert (
                cash_queries.get_opening_import_receipt(
                    db, scope=scope, receipt_id=old_receipt.receipt_id
                )
                == old_receipt
            )
            assert cash._position(db, scope, account).collectible_receivable == Decimal(
                "85"
            )
            assert cash._position(db, scope, account).available_credit == Decimal("50")
            fresh_account = cash._new_account(db, scope)
            new_command = _command(db, scope, fresh_account)
            db.commit()
        with cash.window_fixture._principal(urls, plane) as principal:
            with cash.window_fixture._restricted_session(urls, scope, principal) as db:
                with pytest.raises(BillingRuleViolation) as missing:
                    s.import_opening_account_v2(
                        db,
                        scope=scope,
                        command=new_command,
                        verifier=evidence.TrustedVerifier(),
                    )
                assert missing.value.code == "billing.opening_window_missing"
            # Existing immutable receipts cannot be silently adopted into a new
            # window, even by a genuine migrator with a genuine restricted role.
            with pytest.raises(DBAPIError) as bootstrap:
                cash.window_fixture._provision(urls, scope, principal, new_command)
            assert bootstrap.value.orig.sqlstate == "42501"
            assert "historical bootstrap" in str(bootstrap.value.orig)
        engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_snapshot_reads_ignore_dirty_orm_without_inventing_collection_identity(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _, receipt = _import(db, scope, account)
        basis = _bases(db, scope, receipt)[0]
        expected = q.get_opening_receivable_exposure(
            db, scope=scope, basis_id=basis.basis_id
        )
        assert expected.completeness == "complete"
        assert expected.source_owner == "dotmac-billing"
        assert expected.projection_mode == "authoritative"
        assert expected.collection.subject_ref is None
        assert not expected.automated_collection_allowed
        db.commit()
        document = db.get(_model(plane, "BillingDocument"), basis.document_id)
        document.seller_snapshot = {"legal_name": "Current mutable seller"}
        document.grand_total = Decimal("999999")
        assert (
            q.get_opening_receivable_basis(db, scope=scope, basis_id=basis.basis_id)
            == basis
        )
        assert (
            q.get_opening_receivable_exposure(db, scope=scope, basis_id=basis.basis_id)
            == expected
        )
        assert document in db.dirty
        model = _model(plane, "ReceivableExposureFact")
        with db.no_autoflush:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.document_id == basis.document_id)
                )
                == 0
            )
        db.rollback()
        assert (
            q.get_opening_receivable_basis(db, scope=scope, basis_id=basis.basis_id)
            == basis
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_actual_imported_position_is_exact_under_hostile_decimal_context(
    scratch, plane
):
    from decimal import Inexact, Overflow, Rounded, getcontext, localcontext

    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _, receipt = _import(db, scope, account)
        basis = _bases(db, scope, receipt)[0]
        expected = q.get_opening_receivable_position(
            db, scope=scope, basis_id=basis.basis_id
        )
        global_before = getcontext().copy()
        with localcontext() as hostile:
            hostile.prec = 2
            hostile.Emax = 1
            hostile.Emin = -1
            for signal in (Inexact, Rounded, Overflow):
                hostile.traps[signal] = True
            hostile.clear_flags()
            before = (
                hostile.prec,
                hostile.Emax,
                hostile.Emin,
                hostile.rounding,
                hostile.clamp,
                dict(hostile.traps),
                dict(hostile.flags),
            )
            actual = q.get_opening_receivable_position(
                db, scope=scope, basis_id=basis.basis_id
            )
            assert actual == expected
            assert (
                hostile.prec,
                hostile.Emax,
                hostile.Emin,
                hostile.rounding,
                hostile.clamp,
                dict(hostile.traps),
                dict(hostile.flags),
            ) == before
        current = getcontext()
        assert (
            current.prec,
            current.Emax,
            current.Emin,
            current.rounding,
            current.clamp,
            dict(current.traps),
            dict(current.flags),
        ) == (
            global_before.prec,
            global_before.Emax,
            global_before.Emin,
            global_before.rounding,
            global_before.clamp,
            dict(global_before.traps),
            dict(global_before.flags),
        )


_SOURCE_CASES = (
    "withholding_source",
    "withholding_tax_component",
    "noncash_grant",
    "noncash_effect_grant",
    "noncash_application",
    "noncash_application_reversal",
    "noncash_unused_reduction",
    "credit_note",
)


def _source_case(db, scope, kind):
    """Prepare owner prerequisites; leave the colliding command pending."""
    account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
    owner, reference = "customer-care", f"source:{uuid4()}"
    if kind.startswith("withholding"):
        command = b._withholding_command(
            account.id,
            invoice.id,
            scope=scope,
            suffix=str(uuid4()),
            claimed="10",
            accepted="10",
        )
        if kind == "withholding_source":
            binding = evidence.OpeningReplayBindingV1(
                "billing.withholding.accept",
                command.source_owner,
                command.source_ref,
                "a" * 64,
            )
        else:
            tax = command.tax_evidence
            binding = evidence.OpeningReplayBindingV1(
                "billing.withholding.tax_component",
                tax.source_owner,
                opening_withholding_component_identity(
                    tax_source_owner=tax.source_owner,
                    determination_ref=tax.determination_ref,
                    component_ref=tax.component_ref,
                ),
                "a" * 64,
                determination_ref=tax.determination_ref,
                component_ref=tax.component_ref,
            )
        owner_command = s.accept_withholding_discharge
        delta = (Decimal("-10"), Decimal("0"))
    elif kind in {"noncash_grant", "noncash_effect_grant"}:
        command = replace(
            noncash._grant(scope, account.id, amount="10"),
            source_owner=owner,
            source_ref=reference,
        )
        binding = evidence.OpeningReplayBindingV1(
            "billing.non_cash_credit.grant"
            if kind == "noncash_grant"
            else "billing.non_cash_credit.apply",
            owner,
            reference,
            "b" * 64,
        )
        owner_command = s.grant_non_cash_credit
        delta = (Decimal("0"), Decimal("10"))
    elif kind.startswith("noncash_"):
        grant = s.grant_non_cash_credit(
            db, scope=scope, command=noncash._grant(scope, account.id, amount="20")
        )
        if kind == "noncash_application":
            command = replace(
                noncash._apply(scope, grant.grant_id, invoice.id, "10"),
                source_owner=owner,
                source_ref=reference,
            )
            owner_command = s.apply_non_cash_credit
            delta = (Decimal("-10"), Decimal("-10"))
        elif kind == "noncash_application_reversal":
            original = s.apply_non_cash_credit(
                db,
                scope=scope,
                command=noncash._apply(scope, grant.grant_id, invoice.id, "10"),
            )
            command = ReverseNonCashCreditApplication(
                scope,
                original.effect_id,
                str(uuid4()),
                owner,
                reference,
                "1",
                "finance",
                "correction",
                evidence.NOW,
                b._money("5"),
            )
            owner_command = s.reverse_non_cash_credit_application
            delta = (Decimal("5"), Decimal("5"))
        else:
            command = ReduceNonCashCreditGrant(
                scope,
                grant.grant_id,
                str(uuid4()),
                owner,
                reference,
                "1",
                "finance",
                "reduction",
                evidence.NOW,
                b._money("5"),
            )
            owner_command = s.reduce_non_cash_credit_grant
            delta = (Decimal("0"), Decimal("-5"))
        binding = evidence.OpeningReplayBindingV1(
            "billing.non_cash_credit.apply", owner, reference, "c" * 64
        )
    else:
        command = IssueCreditNote(
            invoice.id,
            b._money("10"),
            b._money("0"),
            b._money("10"),
            "source correction",
            "credit",
            evidence.NOW.date(),
            "fixture",
            reference,
            evidence.NOW,
            "credit.standard",
            "1",
        )
        owner_command = s.issue_credit_note
        binding = evidence.OpeningReplayBindingV1(
            "billing.credit_note.issue", "dotmac-billing", reference, "d" * 64
        )
        delta = (Decimal("-10"), Decimal("0"))

    def execute(session, *, source_version=None):
        offered = (
            command
            if source_version is None
            else replace(
                command, source_version=source_version, idempotency_key=str(uuid4())
            )
        )
        if kind == "credit_note":
            return owner_command(
                session, scope=scope, command=offered, numbering=b.NUMBERING
            )
        return owner_command(session, scope=scope, command=offered)

    db.commit()
    return account.id, execute, binding, delta


def _with_historical_binding(command, binding):
    first, second = command.manifest.receivables
    payment, historic = first.historical_discharges
    if binding.operation.startswith("billing.withholding"):
        bindings = tuple(
            binding if value.operation == binding.operation else value
            for value in historic.replay_bindings
        )
    elif binding.operation == "billing.credit_note.issue":
        historic = replace(historic, kind="credit_note", tax_snapshots=())
        bindings = (binding,)
    else:
        historic = replace(historic, kind="non_cash_credit", tax_snapshots=())
        other_operation = (
            "billing.non_cash_credit.apply"
            if binding.operation == "billing.non_cash_credit.grant"
            else "billing.non_cash_credit.grant"
        )
        bindings = (
            binding,
            evidence.OpeningReplayBindingV1(
                other_operation, "historical-owner", str(uuid4()), "e" * 64
            ),
        )
    historic = replace(historic, replay_bindings=bindings)
    result = replace(
        command,
        manifest=replace(
            command.manifest,
            receivables=(
                replace(first, historical_discharges=(payment, historic)),
                second,
            ),
        ),
    )
    preview = evidence.preview_opening_account_import_v2(
        result, verifier=evidence.TrustedVerifier()
    )
    assert preview.admissible, preview.blockers
    return result


def _physical_source(binding):
    namespace = {
        "billing.withholding.accept": "withholding.source",
        "billing.withholding.tax_component": "withholding.tax_component",
        "billing.non_cash_credit.grant": "non_cash.grant",
        "billing.non_cash_credit.apply": "non_cash.effect",
        "billing.credit_note.issue": "credit_note.issue",
    }[binding.operation]
    return (
        namespace,
        binding.source_owner,
        binding.determination_ref or binding.source_ref,
        binding.component_ref or "",
    )


def _source_rows(db, plane, binding):
    model = _model(plane, "ReceivableSourceClaim")
    namespace, owner, key1, key2 = _physical_source(binding)
    return db.scalars(
        select(model).where(
            model.namespace == namespace,
            model.source_owner == owner,
            model.key1 == key1,
            model.key2 == key2,
        )
    ).all()


def _ar_and_noncash(db, scope, account):
    position = cash._position(db, scope, account)
    return position.collectible_receivable, _available_non_cash_credit(
        db, scope, account
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", _SOURCE_CASES)
def test_live_first_source_blocks_same_historical_identity_on_pristine_other_account(
    scratch, plane, kind
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        live_account, execute, binding, delta = _source_case(db, scope, kind)
        before_ar, before_nc = _ar_and_noncash(db, scope, live_account)
        actual = execute(db)
        db.commit()
        replay = execute(db)
        identity = next(
            name for name in ("id", "grant_id", "effect_id") if hasattr(actual, name)
        )
        assert getattr(replay, identity) == getattr(actual, identity)
        if kind.startswith("noncash"):
            with pytest.raises(BillingConflict):
                execute(db, source_version="changed-after-confirmed-source")
        assert _ar_and_noncash(db, scope, live_account) == (
            before_ar + delta[0],
            before_nc + delta[1],
        )
        claims = _source_rows(db, plane, binding)
        assert len(claims) == 1
        assert claims[0].billing_account_id == live_account
        assert claims[0].historical_claim_id is None
        imported_account = cash._new_account(db, scope)
        command = _with_historical_binding(
            _command(db, scope, imported_account), binding
        )
        before = _counts(db, plane, imported_account)
        with pytest.raises((BillingConflict, DBAPIError)):
            cash._prepare_window(db, scope, command)
            cash._import_command(
                db,
                scope=scope,
                command=command,
                verifier=evidence.TrustedVerifier(),
                version=2,
            )
        assert _counts(db, plane, imported_account) == before
        assert _ar_and_noncash(db, scope, imported_account) == (
            Decimal("0"),
            Decimal("0"),
        )
        assert len(_source_rows(db, plane, binding)) == 1
        assert _ar_and_noncash(db, scope, live_account) == (
            before_ar + delta[0],
            before_nc + delta[1],
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", _SOURCE_CASES)
@pytest.mark.parametrize(
    "isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
)
def test_live_history_cross_account_race_uses_one_physical_unique_source_claim(
    scratch, plane, kind, isolation
):
    from sqlalchemy import event

    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        live_account, execute, binding, delta = _source_case(db, scope, kind)
        before_ar, before_nc = _ar_and_noncash(db, scope, live_account)
        imported_account = cash._new_account(db, scope)
        command = _with_historical_binding(
            _command(db, scope, imported_account), binding
        )
        cash._prepare_window(db, scope, command)
    snapshots, mutations = Barrier(2), Barrier(2)
    successes, failures, acquired = [], [], []
    prefix = "platform_" if plane == "platform" else ""
    owner_table = (
        "withholding_discharges"
        if kind.startswith("withholding")
        else "non_cash_credit_grants"
        if kind in {"noncash_grant", "noncash_effect_grant"}
        else "non_cash_credit_effects"
        if kind.startswith("noncash")
        else "invoice_document_facts"
    )

    def run(branch):
        try:
            with cash._session(scratch, url, scope) as db:
                assert isolation in {
                    "READ COMMITTED",
                    "REPEATABLE READ",
                    "SERIALIZABLE",
                }
                db.execute(
                    text(
                        "SET SESSION CHARACTERISTICS AS TRANSACTION "
                        f"ISOLATION LEVEL {isolation}"
                    )
                )
                db.commit()
                assert (
                    db.scalar(text("SELECT current_setting('transaction_isolation')"))
                    == isolation.lower()
                )
                acquired.append(
                    (branch, db.scalar(text("SELECT pg_current_snapshot()::text")))
                )
                # Acquire both real snapshots before either source is claimed.
                snapshots.wait(timeout=30)
                reached = []

                def before_insert(
                    _conn, _cursor, statement, _parameters, _context, _many
                ):
                    target = (
                        owner_table
                        if branch == "live"
                        else "opening_historical_discharge_claims"
                    )
                    if reached or not statement.startswith(
                        f"INSERT INTO mod_billing.{prefix}{target} "
                    ):
                        return
                    if branch == "history":
                        parameters = (
                            _parameters
                            if isinstance(_parameters, list | tuple)
                            else (_parameters,)
                        )
                        selected = []
                        for values in parameters:
                            if not isinstance(values, dict):
                                continue
                            if (
                                values.get("operation"),
                                values.get("source_owner"),
                            ) != (binding.operation, binding.source_owner):
                                continue
                            if binding.operation == "billing.withholding.tax_component":
                                matches = (
                                    values.get("determination_ref"),
                                    values.get("component_ref"),
                                ) == (binding.determination_ref, binding.component_ref)
                            else:
                                matches = values.get("source_ref") == binding.source_ref
                            if matches:
                                selected.append(values)
                        if not selected:
                            return
                    reached.append(target)
                    mutations.wait(timeout=30)

                event.listen(db.get_bind(), "before_cursor_execute", before_insert)
                try:
                    cash._prepare_window(db, scope, command)
                    result = (
                        execute(db)
                        if branch == "live"
                        else cash._import_command(
                            db,
                            scope=scope,
                            command=command,
                            verifier=evidence.TrustedVerifier(),
                            version=2,
                        )
                    )
                    assert reached
                    db.commit()
                    successes.append((branch, result))
                finally:
                    event.remove(db.get_bind(), "before_cursor_execute", before_insert)
        except Exception as error:
            failures.append((branch, error))

    threads = [Thread(target=run, args=(branch,)) for branch in ("live", "history")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()
    assert len(acquired) == 2 and all(snapshot for _, snapshot in acquired)
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0][1], DBAPIError), failures
    assert failures[0][1].orig.sqlstate in {"23505", "40001"}
    with cash._session(scratch, url, scope) as db:
        rows = _source_rows(db, plane, binding)
        assert len(rows) == 1
        claim = rows[0]
        pointers = (
            claim.historical_claim_id,
            claim.withholding_discharge_id,
            claim.non_cash_grant_id,
            claim.non_cash_effect_id,
            claim.credit_document_fact_id,
        )
        assert sum(value is not None for value in pointers) == 1
        if successes[0][0] == "live":
            assert (
                claim.billing_account_id == live_account
                and claim.historical_claim_id is None
            )
            assert _ar_and_noncash(db, scope, live_account) == (
                before_ar + delta[0],
                before_nc + delta[1],
            )
            assert (
                cash._position(db, scope, imported_account).collectible_receivable == 0
            )
            assert _counts(db, plane, imported_account)["OpeningImportReceipt"] == 0
        else:
            assert (
                claim.billing_account_id == imported_account
                and claim.historical_claim_id is not None
            )
            assert _ar_and_noncash(db, scope, live_account) == (before_ar, before_nc)
            assert cash._position(
                db, scope, imported_account
            ).collectible_receivable == Decimal("120")
            assert cash._position(
                db, scope, imported_account
            ).available_credit == Decimal("60")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_historical_tax_component_blocks_a_new_remittance_source_on_another_account(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        live_account, execute, binding, _ = _source_case(
            db, scope, "withholding_tax_component"
        )
        before = _ar_and_noncash(db, scope, live_account)
        imported_account = cash._new_account(db, scope)
        command = _with_historical_binding(
            _command(db, scope, imported_account), binding
        )
        cash._prepare_window(db, scope, command)
        receipt = cash._import_command(
            db,
            scope=scope,
            command=command,
            verifier=evidence.TrustedVerifier(),
            version=2,
        )
        db.commit()
        claim = _source_rows(db, plane, binding)
        assert len(claim) == 1 and claim[0].historical_claim_id is not None
        assert claim[0].billing_account_id == imported_account
        # The new acceptance source is unclaimed; the exact Tax triple owns the refusal.
        live_model = _model(plane, "WithholdingDischarge")
        assert (
            db.scalar(
                select(func.count())
                .select_from(live_model)
                .where(live_model.billing_account_id == live_account)
            )
            == 0
        )
        with pytest.raises((BillingConflict, DBAPIError)):
            execute(db)
        assert _ar_and_noncash(db, scope, live_account) == before
        assert len(_source_rows(db, plane, binding)) == 1
        assert (
            q.get_opening_import_receipt_v2(
                db, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "attack", ["positive", "duplicate_basis", "duplicate_cash_effect", "duplicate_lane"]
)
def test_v5_sql_duplicate_arrays_cannot_omit_actual_sources_with_matching_outbox(
    scratch, plane, attack
):
    scope, _ = noncash._scope_url(scratch, plane)
    with cash._session(scratch, scratch[0], scope) as db:
        account = cash._new_account(db, scope)
        table = _model(plane, "AccountingFact").__table__
        if attack != "positive":
            _disable_fixture_append_before_insert(db, table)
        _, receipt = _import(db, scope, account)
        row = (
            db.execute(
                select(table).where(
                    table.c.posting_group_id == receipt.posting_group_id
                )
            )
            .mappings()
            .one()
        )
        payload = deepcopy(row["fact_payload"])
        outbox = b.OutboxEvent if plane == "tenant" else b.PlatformOutboxEvent
        actual_events = db.scalars(
            select(outbox).where(outbox.event_type == "billing.accounting.fact.v5")
        ).all()
        matching = [
            item for item in actual_events if item.payload["fact_id"] == str(row["id"])
        ]
        assert len(matching) == 1
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgrelid=CAST(:table AS regclass) "
                    "AND tgdeferrable AND tginitdeferred"
                ),
                {"table": f"mod_billing.{table.name}"},
            )
            > 0
        )
        if attack != "positive":
            key = {
                "duplicate_basis": "receivable_bases",
                "duplicate_cash_effect": "opening_cash_effects",
                "duplicate_lane": "effects",
            }[attack]
            assert len(payload[key]) == 2
            assert payload[key][0] != payload[key][1]
            payload[key][1] = deepcopy(payload[key][0])
            db.execute(
                table.update()
                .where(table.c.id == row["id"])
                .values(fact_payload=payload, fact_digest=b.fingerprint_of(payload))
            )
            db.execute(
                outbox.__table__.update()
                .where(outbox.id == matching[0].id)
                .values(payload=payload)
            )
            stored = db.execute(
                select(table.c.fact_payload, table.c.fact_digest).where(
                    table.c.id == row["id"]
                )
            ).one()
            assert stored.fact_payload == payload
            assert stored.fact_digest == b.fingerprint_of(payload)
            assert (
                db.scalar(
                    select(outbox.__table__.c.payload).where(
                        outbox.id == matching[0].id
                    )
                )
                == payload
            )
            # Real AFTER INSERT events are still queued. Matching checksums and
            # outbox bytes cannot substitute for the complete actual source set.
            with pytest.raises(DBAPIError) as error:
                db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            assert error.value.orig.sqlstate == "23514"
        else:
            db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            assert (
                q.get_accounting_fact_v5(
                    db, scope=scope, posting_group_id=receipt.posting_group_id
                ).import_receipt
                == receipt
            )
        db.rollback()
        _assert_fixture_append_restored(db, table)
        assert _counts(db, plane, account)["OpeningImportReceipt"] == 0


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "attack",
    ["positive", "unrelated_group", "basis_document", "version", "money", "evidence"],
)
def test_extra_exposure_sql_must_match_an_actual_impacted_basis_and_group(
    scratch, plane, attack
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        _, receipt = _import(db, scope, account)
        first, second = _bases(db, scope, receipt)
        draw = s.apply_available_credit(
            db,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account, first.document_id, b._money("10"), evidence.NOW, str(uuid4())
            ),
        )
        draw_id = draw.id
        # This group changes account credit only; it does not impact either invoice.
        settlement = s.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                account, scope=scope, source_key=str(uuid4()), amount="1"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        groups = _model(plane, "PostingGroup")
        unrelated = db.scalars(
            select(groups).where(groups.source_ref == f"settlement:{settlement.id}")
        ).one()
        unrelated_id, unrelated_version = unrelated.id, unrelated.source_version
        db.commit()
    table = _model(plane, "ReceivableExposureFactV2").__table__
    with cash._session(scratch, scratch[0], scope) as owner:
        row = dict(
            owner.execute(
                select(table).where(
                    table.c.basis_id == first.basis_id,
                    table.c.posting_group_id == draw_id,
                )
            )
            .mappings()
            .one()
        )
        original = deepcopy(row)
        row["id"] = uuid4()
        payload = deepcopy(row["evidence"])
        if attack == "unrelated_group":
            row.update(posting_group_id=unrelated_id, source_version=unrelated_version)
            payload.update(
                posting_group_watermark=str(unrelated_id),
                source_version=unrelated_version,
            )
        else:
            owner.execute(
                text(
                    f"ALTER TABLE mod_billing.{table.name} "
                    f"DISABLE TRIGGER {table.name}_append_only"
                )
            )
            owner.execute(table.delete().where(table.c.id == original["id"]))
            owner.execute(
                text(
                    f"ALTER TABLE mod_billing.{table.name} "
                    f"ENABLE TRIGGER {table.name}_append_only"
                )
            )
            if attack == "basis_document":
                row["basis_id"] = second.basis_id
                payload["basis_id"] = str(second.basis_id)
            elif attack == "version":
                row["source_version"] += 100
                payload["source_version"] = row["source_version"]
            elif attack == "money":
                row["collectible_receivable"] = Decimal("60.01")
                payload["collectible_receivable"]["amount"] = "60.01"
            elif attack == "evidence":
                payload["collection"]["evidence_ref"] = "unattested-mapping"
        semantic = deepcopy(payload)
        semantic.pop("state_fingerprint")
        payload["state_fingerprint"] = b.fingerprint_of(semantic)
        row.update(evidence=payload, evidence_digest=b.fingerprint_of(payload))
        # Use the actual owner outbox API; no receipt or authority is fabricated.
        s._emit(
            owner,
            scope=scope,
            event_type="billing.receivable.exposure.v2",
            payload=payload,
        )
        if attack == "positive":
            owner.execute(table.insert().values(**row))
            owner.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        else:
            with pytest.raises(DBAPIError) as error:
                owner.execute(table.insert().values(**row))
                owner.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            assert error.value.orig.sqlstate == "23514"
        owner.rollback()
        assert q.get_opening_receivable_exposure(
            owner, scope=scope, basis_id=first.basis_id
        ).collectible_receivable == b._money("60")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", _SOURCE_CASES)
@pytest.mark.parametrize("branch", ["live", "history"])
def test_rolled_back_source_winner_allows_fresh_legitimate_exact_retry(
    scratch, plane, kind, branch
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        live_account, execute, binding, delta = _source_case(db, scope, kind)
        before_ar, before_nc = _ar_and_noncash(db, scope, live_account)
        imported_account = cash._new_account(db, scope)
        command = _with_historical_binding(
            _command(db, scope, imported_account), binding
        )

        def winning_operation():
            cash._prepare_window(db, scope, command)
            return (
                execute(db)
                if branch == "live"
                else cash._import_command(
                    db,
                    scope=scope,
                    command=command,
                    verifier=evidence.TrustedVerifier(),
                    version=2,
                )
            )

        winning_operation()
        assert len(_source_rows(db, plane, binding)) == 1
        db.rollback()
        assert _source_rows(db, plane, binding) == []
        assert _ar_and_noncash(db, scope, live_account) == (before_ar, before_nc)
        assert _counts(db, plane, imported_account)["OpeningImportReceipt"] == 0
        actual = winning_operation()
        db.commit()
        replay = winning_operation()
        identity = next(
            name
            for name in ("id", "receipt_id", "grant_id", "effect_id")
            if hasattr(actual, name)
        )
        assert getattr(replay, identity) == getattr(actual, identity)
        assert len(_source_rows(db, plane, binding)) == 1
        if branch == "live":
            assert _ar_and_noncash(db, scope, live_account) == (
                before_ar + delta[0],
                before_nc + delta[1],
            )
            with pytest.raises((BillingConflict, DBAPIError)):
                cash._prepare_window(db, scope, command)
                cash._import_command(
                    db,
                    scope=scope,
                    command=command,
                    verifier=evidence.TrustedVerifier(),
                    version=2,
                )
        else:
            assert cash._position(
                db, scope, imported_account
            ).collectible_receivable == Decimal("120")
            with pytest.raises((BillingConflict, DBAPIError)):
                execute(db)
        assert len(_source_rows(db, plane, binding)) == 1


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "kind", ["noncash_application_reversal", "noncash_unused_reduction"]
)
def test_historical_source_occupancy_covers_noncash_correction_namespace(
    scratch, plane, kind
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        live_account, execute, binding, _ = _source_case(db, scope, kind)
        before = _ar_and_noncash(db, scope, live_account)
        imported_account = cash._new_account(db, scope)
        command = _with_historical_binding(
            _command(db, scope, imported_account), binding
        )
        cash._prepare_window(db, scope, command)
        cash._import_command(
            db,
            scope=scope,
            command=command,
            verifier=evidence.TrustedVerifier(),
            version=2,
        )
        db.commit()
        with pytest.raises((BillingConflict, DBAPIError)):
            execute(db)
        assert _ar_and_noncash(db, scope, live_account) == before
        claims = _source_rows(db, plane, binding)
        assert len(claims) == 1 and claims[0].historical_claim_id is not None


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_raw_tax_triple_remains_occupied_if_auxiliary_historical_hash_is_corrupted(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        live_account, execute, binding, _ = _source_case(
            db, scope, "withholding_tax_component"
        )
        before = _ar_and_noncash(db, scope, live_account)
        imported_account = cash._new_account(db, scope)
        command = _with_historical_binding(
            _command(db, scope, imported_account), binding
        )
        cash._prepare_window(db, scope, command)
        cash._import_command(
            db,
            scope=scope,
            command=command,
            verifier=evidence.TrustedVerifier(),
            version=2,
        )
        db.commit()
    table = _model(plane, "OpeningHistoricalDischargeClaim").__table__
    with cash._session(scratch, scratch[0], scope) as owner:
        row = (
            owner.execute(
                select(table).where(
                    table.c.operation == "billing.withholding.tax_component",
                    table.c.source_owner == binding.source_owner,
                    table.c.determination_ref == binding.determination_ref,
                    table.c.component_ref == binding.component_ref,
                )
            )
            .mappings()
            .one()
        )
        assert row["source_ref"] == binding.source_ref
        assert row["source_ref"] != "f" * 64
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"DISABLE TRIGGER {table.name}_append_only"
            )
        )
        owner.execute(
            table.update().where(table.c.id == row["id"]).values(source_ref="f" * 64)
        )
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"ENABLE TRIGGER {table.name}_append_only"
            )
        )
        # The raw Tax identity has its own physical uniqueness. It cannot be
        # freed by drift in the redundant callback mapping hash.
        with pytest.raises(BillingConflict) as refusal:
            execute(owner)
        assert refusal.value.code == "billing.historical_discharge_source_claimed"
        keeper = _model(plane, "ReceivableSourceClaim").__table__
        namespace, source_owner, key1, key2 = _physical_source(binding)
        occupied = dict(
            owner.execute(
                select(keeper).where(
                    keeper.c.namespace == namespace,
                    keeper.c.source_owner == source_owner,
                    keeper.c.key1 == key1,
                    keeper.c.key2 == key2,
                )
            )
            .mappings()
            .one()
        )
        # The physical raw triple also refuses independently of the friendly guard.
        with pytest.raises(DBAPIError) as error:
            owner.execute(keeper.insert().values(**{**occupied, "id": uuid4()}))
        assert error.value.orig.sqlstate == "23505"
        owner.rollback()
        assert _ar_and_noncash(owner, scope, live_account) == before
        assert len(_source_rows(owner, plane, binding)) == 1


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_credit_void_and_ordinary_invoice_correlations_do_not_claim_credit_issuance(
    scratch, plane
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        fact_model = _model(plane, "InvoiceDocumentFact")
        original_fact = db.scalars(
            select(fact_model).where(
                fact_model.document_id == invoice.id, fact_model.fact_version == 1
            )
        ).one()
        invoice_key = original_fact.fact_payload["correlation_id"]
        binding = evidence.OpeningReplayBindingV1(
            "billing.credit_note.issue", "dotmac-billing", invoice_key, "a" * 64
        )
        assert _source_rows(db, plane, binding) == []
        credit_command = IssueCreditNote(
            invoice.id,
            b._money("10"),
            b._money("0"),
            b._money("10"),
            "ordinary correlation reuse",
            "credit",
            evidence.NOW.date(),
            "fixture",
            invoice_key,
            evidence.NOW,
            "credit.standard",
            "1",
        )
        credit = s.issue_credit_note(
            db, scope=scope, command=credit_command, numbering=b.NUMBERING
        )
        assert len(_source_rows(db, plane, binding)) == 1
        void_key = f"credit-void:{uuid4()}"
        s.void_document(
            db,
            scope=scope,
            document_id=credit.id,
            source_ref=void_key,
            actor_ref="fixture",
            occurred_at=evidence.NOW,
        )
        void_binding = replace(binding, source_ref=void_key)
        assert _source_rows(db, plane, void_binding) == []
        assert len(_source_rows(db, plane, binding)) == 1
        second = s.issue_credit_note(
            db,
            scope=scope,
            command=replace(credit_command, correlation_id=void_key),
            numbering=b.NUMBERING,
        )
        assert second.id != credit.id
        assert len(_source_rows(db, plane, void_binding)) == 1
        db.commit()
        assert _ar_and_noncash(db, scope, account.id)[0] == Decimal("110")

        # This key is genuinely occupied by an imported historical credit issue.
        # Ordinary issuance and a credit cancellation are distinct operations.
        imported_account = cash._new_account(db, scope)
        historical_key = f"historical-credit:{uuid4()}"
        historical_binding = evidence.OpeningReplayBindingV1(
            "billing.credit_note.issue", "dotmac-billing", historical_key, "b" * 64
        )
        opening = _with_historical_binding(
            _command(db, scope, imported_account), historical_binding
        )
        cash._prepare_window(db, scope, opening)
        receipt = cash._import_command(
            db,
            scope=scope,
            command=opening,
            verifier=evidence.TrustedVerifier(),
            version=2,
        )
        db.commit()
        occupied = _source_rows(db, plane, historical_binding)
        assert len(occupied) == 1
        keeper_id = occupied[0].id
        historical_claim_id = occupied[0].historical_claim_id
        assert historical_claim_id is not None
        assert occupied[0].billing_account_id == imported_account

        neutral_account = cash._new_account(db, scope)
        obligation = s.accept_rated_obligation(
            db,
            scope=scope,
            command=b._obligation_command(
                neutral_account, scope=scope, source_fact_id=str(uuid4())
            ),
            accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
        )
        draft = s.create_draft_document(
            db, scope=scope, command=b._draft_command(obligation.id)
        )
        ordinary = s.issue_document(
            db,
            scope=scope,
            command=replace(b._issue_command(draft.id), correlation_id=historical_key),
            numbering=b.NUMBERING,
        )
        ordinary_id = ordinary.id
        ordinary_fact = db.scalars(
            select(fact_model).where(
                fact_model.document_id == ordinary_id, fact_model.fact_version == 1
            )
        ).one()
        assert ordinary_fact.fact_payload["document_kind"] == "invoice"
        assert ordinary_fact.fact_payload["correlation_id"] == historical_key
        cancelled = _credit(db, scope, ordinary_id, "10")
        cancelled_id = cancelled.id
        s.void_document(
            db,
            scope=scope,
            document_id=cancelled_id,
            source_ref=historical_key,
            actor_ref="fixture",
            occurred_at=evidence.NOW,
        )
        cancelled_fact = db.scalars(
            select(fact_model)
            .where(fact_model.document_id == cancelled_id)
            .order_by(fact_model.fact_version.desc())
        ).first()
        assert cancelled_fact.fact_payload["document_kind"] == "credit_note"
        assert cancelled_fact.fact_payload["document_state"] == "cancelled"
        assert cancelled_fact.fact_payload["correlation_id"] == historical_key
        db.commit()  # Real deferred guards must admit both non-issuance collisions.
        occupied = _source_rows(db, plane, historical_binding)
        assert len(occupied) == 1
        assert occupied[0].id == keeper_id
        assert occupied[0].historical_claim_id == historical_claim_id
        assert occupied[0].billing_account_id == imported_account
        assert cash._position(
            db, scope, neutral_account
        ).collectible_receivable == Decimal("120")
        assert cash._position(
            db, scope, imported_account
        ).collectible_receivable == Decimal("120")
        assert (
            q.get_opening_import_receipt_v2(
                db, scope=scope, receipt_id=receipt.receipt_id
            )
            == receipt
        )
        assert _ar_and_noncash(db, scope, account.id)[0] == Decimal("110")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("attack", ["positive", "key", "account", "xor"])
def test_common_source_keeper_raw_admission_has_exact_backing_positive_control(
    scratch, plane, attack
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        other_account = cash._new_account(db, scope)
        _import(db, scope, account)
        db.commit()
    table = _model(plane, "ReceivableSourceClaim").__table__
    with cash._session(scratch, scratch[0], scope) as owner:
        assert owner.scalar(
            text(
                "SELECT NOT condeferrable FROM pg_constraint "
                "WHERE conrelid=CAST(:table AS regclass) AND conname=:name"
            ),
            {"table": f"mod_billing.{table.name}", "name": f"uq_{table.name}_identity"},
        )
        row = dict(
            owner.execute(
                select(table).where(table.c.billing_account_id == account).limit(1)
            )
            .mappings()
            .one()
        )
        assert row["historical_claim_id"] is not None
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"DISABLE TRIGGER {table.name}_append_only"
            )
        )
        owner.execute(table.delete().where(table.c.id == row["id"]))
        owner.execute(
            text(
                f"ALTER TABLE mod_billing.{table.name} "
                f"ENABLE TRIGGER {table.name}_append_only"
            )
        )
        row["id"] = uuid4()
        if attack == "key":
            row["key1"] = "forged-source-key"
        elif attack == "account":
            row["billing_account_id"] = other_account
        elif attack == "xor":
            row["historical_claim_id"] = None
        if attack == "positive":
            owner.execute(table.insert().values(**row))
            owner.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        else:
            with pytest.raises(DBAPIError) as error:
                owner.execute(table.insert().values(**row))
            assert error.value.orig.sqlstate == "23514"
        owner.rollback()
        assert (
            owner.scalar(
                select(func.count())
                .select_from(table)
                .where(table.c.billing_account_id == account)
            )
            > 0
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_pending_opening_cannot_commit_without_its_actual_source_occupancy(
    scratch, plane
):
    scope, _ = noncash._scope_url(scratch, plane)
    with cash._session(scratch, scratch[0], scope) as owner:
        account = cash._new_account(owner, scope)
        table = _model(plane, "ReceivableSourceClaim").__table__
        _disable_fixture_append_before_insert(owner, table)
        receipt = _import(owner, scope, account)[1]
        identities = owner.scalars(
            select(table.c.id).where(table.c.billing_account_id == account)
        ).all()
        assert identities
        owner.execute(table.delete().where(table.c.id == identities[0]))
        with pytest.raises(DBAPIError) as error:
            owner.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert error.value.orig.sqlstate == "23514"
        owner.rollback()
        _assert_fixture_append_restored(owner, table)
        assert _counts(owner, plane, account)["ReceivableSourceClaim"] == 0
        with pytest.raises(BillingRuleViolation):
            q.get_opening_import_receipt_v2(
                owner, scope=scope, receipt_id=receipt.receipt_id
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", ["obligation", "draft_insert", "draft_move"])
@pytest.mark.parametrize("native_commits", [True, False])
@pytest.mark.parametrize(
    "isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
)
def test_v2_pristine_admission_observes_native_write_after_acquired_snapshot(
    scratch, plane, kind, native_commits, isolation
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        command = _command(db, scope, account)
        cash._prepare_window(db, scope, command)
        write, native_model = cash._native_target_write(db, scope, account, kind)
        db.commit()
    snapshot_ready, native_finished = Barrier(2), Barrier(2)
    snapshots, native_ids, receipts, refusals, writer_errors = [], [], [], [], []

    def import_complete_partition():
        try:
            with cash._session(scratch, url, scope) as db:
                cash._assert_target_isolation(db, isolation)
                assert cash._native_target_count(db, native_model, account) == 0
                assert _counts(db, plane, account)["PostingGroup"] == 0
                snapshots.append(db.scalar(text("SELECT pg_current_snapshot()::text")))
                snapshot_ready.wait(timeout=30)
                native_finished.wait(timeout=30)
                cash._prepare_window(db, scope, command)
                receipt = cash._import_command(
                    db,
                    scope=scope,
                    command=command,
                    verifier=evidence.TrustedVerifier(),
                    version=2,
                )
                db.commit()
                receipts.append(receipt)
        except Exception as error:
            refusals.append(error)

    def write_unposted_native():
        try:
            with cash._session(scratch, url, scope) as db:
                snapshot_ready.wait(timeout=30)
                native_ids.append(write(db))
                if native_commits:
                    db.commit()
                else:
                    db.rollback()
                native_finished.wait(timeout=30)
        except Exception as error:
            writer_errors.append(error)
            snapshot_ready.abort()
            native_finished.abort()

    threads = [
        Thread(target=import_complete_partition),
        Thread(target=write_unposted_native),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()
    assert len(snapshots) == 1 and snapshots[0]
    assert len(native_ids) == 1 and writer_errors == []
    with cash._session(scratch, url, scope) as db:
        # V2 recognizes documentary invoices, so count the exact native row
        # rather than confusing its two imported documents with native drafts.
        native_exists = db.scalar(
            select(native_model.id).where(
                native_model.id == native_ids[0],
                native_model.billing_account_id == account,
            )
        )
        assert (native_exists is not None) == native_commits
        if native_commits:
            assert receipts == [] and len(refusals) == 1
            if isolation in {"REPEATABLE READ", "SERIALIZABLE"}:
                assert isinstance(refusals[0], DBAPIError), refusals
                assert refusals[0].orig.sqlstate == "40001"
            else:
                assert isinstance(refusals[0], BillingRuleViolation), refusals
                assert refusals[0].code == "billing.opening_target_not_pristine"
            counts = _counts(db, plane, account)
            assert all(
                counts[name] == 0
                for name in (
                    "OpeningImportReceipt",
                    "OpeningReceivableBasis",
                    "OpeningCashLot",
                    "CashSourceClaim",
                    "ReceivableSourceClaim",
                    "PostingGroup",
                )
            )
            position = cash._position(db, scope, account)
            assert position.collectible_receivable == position.available_credit == 0
            with pytest.raises(BillingRuleViolation) as fresh:
                cash._prepare_window(db, scope, command)
                cash._import_command(
                    db,
                    scope=scope,
                    command=command,
                    verifier=evidence.TrustedVerifier(),
                    version=2,
                )
            assert fresh.value.code == "billing.opening_target_not_pristine"
        else:
            assert refusals == [] and len(receipts) == 1
            receipt = receipts[0]
            assert receipt.verification.operation == "billing.opening.import.v2"
            assert receipt.total_receivable == b._money("120")
            assert receipt.total_residual == b._money("60")
            assert len(receipt.basis_ids) == len(receipt.lot_ids) == 2
            assert cash._position(db, scope, account).collectible_receivable == Decimal(
                "120"
            )
            assert cash._position(db, scope, account).available_credit == Decimal("60")
            cash._prepare_window(db, scope, command)
            assert (
                cash._import_command(
                    db,
                    scope=scope,
                    command=command,
                    verifier=evidence.TrustedVerifier(),
                    version=2,
                )
                == receipt
            )
            assert _counts(db, plane, account)["OpeningImportReceipt"] == 1


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("kind", ["obligation", "draft_insert", "draft_move"])
@pytest.mark.parametrize("import_first", [True, False])
def test_v2_complete_import_and_native_write_obey_same_outer_transaction_order(
    scratch, plane, kind, import_first
):
    scope, url = noncash._scope_url(scratch, plane)
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        command = _command(db, scope, account)
        write, native_model = cash._native_target_write(db, scope, account, kind)
        db.commit()
        if import_first:
            cash._prepare_window(db, scope, command)
            receipt = cash._import_command(
                db,
                scope=scope,
                command=command,
                verifier=evidence.TrustedVerifier(),
                version=2,
            )
            native_id = write(db)
            assert (
                db.scalar(
                    select(native_model.id).where(
                        native_model.id == native_id,
                        native_model.billing_account_id == account,
                    )
                )
                == native_id
            )
            db.commit()
            assert (
                q.get_opening_import_receipt_v2(
                    db, scope=scope, receipt_id=receipt.receipt_id
                )
                == receipt
            )
            assert cash._position(db, scope, account).collectible_receivable == Decimal(
                "120"
            )
            assert cash._position(db, scope, account).available_credit == Decimal("60")
        else:
            native_id = write(db)
            with pytest.raises(BillingRuleViolation) as refusal:
                cash._prepare_window(db, scope, command)
                cash._import_command(
                    db,
                    scope=scope,
                    command=command,
                    verifier=evidence.TrustedVerifier(),
                    version=2,
                )
            assert refusal.value.code == "billing.opening_target_not_pristine"
            assert (
                db.scalar(
                    select(native_model.id).where(
                        native_model.id == native_id,
                        native_model.billing_account_id == account,
                    )
                )
                == native_id
            )
            db.commit()
            assert _counts(db, plane, account)["OpeningImportReceipt"] == 0
            assert _counts(db, plane, account)["OpeningReceivableBasis"] == 0
            position = cash._position(db, scope, account)
            assert position.collectible_receivable == position.available_credit == 0
