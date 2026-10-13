"""Hosted PostgreSQL behavior canaries for source-specific ordinary credits."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_billing import models
from dotmac_billing.commands import (
    ApplyNonCashCredit,
    GrantNonCashCredit,
    ReduceNonCashCreditGrant,
    ReverseNonCashCreditApplication,
)
from dotmac_billing.contracts import (
    NonCashCreditClassificationV1,
    NonCashCreditPolicyParameterV1,
)
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.service import (
    _document_outstanding,
    accept_withholding_discharge,
    apply_non_cash_credit,
    grant_non_cash_credit,
    rebuild_receivable_position,
    reduce_non_cash_credit_grant,
    reverse_non_cash_credit_application,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from sqlalchemy import select

from tests import test_billing_isolation as b


@pytest.fixture(scope="module")
def scratch():
    from dotmac_kernel.planes import ModulePlane

    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _grant(scope, account, *, amount="50", document=None, apply=False):
    return GrantNonCashCredit(
        scope,
        account,
        b._money(amount),
        NonCashCreditClassificationV1(
            "customer_compensation",
            "commercial-policy",
            "policy:1",
            "1",
            "a" * 64,
            (NonCashCreditPolicyParameterV1("reason", "service interruption"),),
        ),
        str(uuid4()),
        "customer-care",
        str(uuid4()),
        "v1",
        "finance:1",
        "decision:1",
        b.NOW,
        restricted_document_id=document,
        apply_on_issue=apply,
    )


def _apply(scope, grant, invoice, amount=None):
    return ApplyNonCashCredit(
        scope,
        grant,
        invoice,
        str(uuid4()),
        "customer-care",
        str(uuid4()),
        "v1",
        "finance:1",
        "decision:2",
        b.NOW,
        b._money(amount) if amount else None,
    )


def _scope_url(scratch, plane):
    admin, tenant, platform = scratch
    return (
        (TenantScope(b._create_tenants(admin, 1)[0]), tenant)
        if plane == "tenant"
        else (PlatformScope(), platform)
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_source_specific_credit_retention_application_and_exact_replay(scratch, plane):
    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        # WHT leaves exactly thirty live AR. Noncash consumes that room only.
        accept_withholding_discharge(
            db,
            scope=scope,
            command=b._withholding_command(
                account.id,
                invoice.id,
                scope=scope,
                suffix=str(uuid4()),
                accepted="90",
                claimed="90",
            ),
        )
        command = _grant(scope, account.id, document=invoice.id, apply=True)
        grant = grant_non_cash_credit(db, scope=scope, command=command)
        assert _document_outstanding(db, scope=scope, document=invoice) == 0
        position = rebuild_receivable_position(
            db,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert position.available_credit == position.prepaid_funding == 0
        assert grant.original_amount.amount == 50
        effect_model = (
            models.NonCashCreditEffect
            if plane == "tenant"
            else models.PlatformNonCashCreditEffect
        )
        effects = db.scalars(
            select(effect_model).where(effect_model.grant_id == grant.grant_id)
        ).all()
        assert sum(e.availability_delta for e in effects) == 20
        assert sorted(e.availability_delta for e in effects) == [
            Decimal("-30"),
            Decimal("50"),
        ]
        assert grant_non_cash_credit(db, scope=scope, command=command) == grant
        # Natural source retry survives a new retry token and consumed room.
        assert (
            grant_non_cash_credit(
                db, scope=scope, command=replace(command, idempotency_key=str(uuid4()))
            )
            == grant
        )
        with pytest.raises(BillingConflict):
            grant_non_cash_credit(
                db,
                scope=scope,
                command=replace(
                    command, amount=b._money("51"), idempotency_key=str(uuid4())
                ),
            )
        paid_grant = grant_non_cash_credit(
            db,
            scope=scope,
            command=_grant(scope, account.id, document=invoice.id, apply=True),
        )
        assert paid_grant.original_amount.amount == 50
        unlinked = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id, apply=True)
        )
        assert unlinked.restricted_document_id is None


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_partial_correction_caps_and_unused_only_reduction(scratch, plane):
    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        grant = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id)
        )
        applied = apply_non_cash_credit(
            db, scope=scope, command=_apply(scope, grant.grant_id, invoice.id, "30")
        )
        correction = ReverseNonCashCreditApplication(
            scope,
            applied.effect_id,
            str(uuid4()),
            "finance",
            str(uuid4()),
            "1",
            "finance:1",
            "decision:3",
            b.NOW,
            b._money("10"),
        )
        restored = reverse_non_cash_credit_application(
            db, scope=scope, command=correction
        )
        assert restored.availability_delta.amount == 10
        assert (
            reverse_non_cash_credit_application(db, scope=scope, command=correction)
            == restored
        )
        with pytest.raises(BillingRuleViolation):
            reverse_non_cash_credit_application(
                db,
                scope=scope,
                command=replace(
                    correction,
                    idempotency_key=str(uuid4()),
                    source_ref=str(uuid4()),
                    amount=b._money("21"),
                ),
            )
        reduction = ReduceNonCashCreditGrant(
            scope,
            grant.grant_id,
            str(uuid4()),
            "finance",
            str(uuid4()),
            "1",
            "finance:1",
            "decision:4",
            b.NOW,
            b._money("31"),
        )
        with pytest.raises(BillingRuleViolation):
            reduce_non_cash_credit_grant(db, scope=scope, command=reduction)
        reduced = reduce_non_cash_credit_grant(
            db, scope=scope, command=replace(reduction, amount=None)
        )
        assert reduced.availability_delta.amount == -30
        assert _document_outstanding(db, scope=scope, document=invoice) == 100
        with pytest.raises(BillingRuleViolation):
            apply_non_cash_credit(
                db, scope=scope, command=_apply(scope, grant.grant_id, invoice.id)
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_credit_queries_ignore_dirty_orm_and_snapshot_stays_fresh_after_cash(
    scratch, plane
):
    from dotmac_billing import (
        get_non_cash_credit_effect,
        get_non_cash_credit_grant,
        get_receivable_position_v2,
    )
    from dotmac_billing.service import accept_settlement

    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        grant = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id)
        )
        effect = apply_non_cash_credit(
            db, scope=scope, command=_apply(scope, grant.grant_id, invoice.id, "30")
        )
        grant_model = (
            models.NonCashCreditGrant
            if plane == "tenant"
            else models.PlatformNonCashCreditGrant
        )
        effect_model = (
            models.NonCashCreditEffect
            if plane == "tenant"
            else models.PlatformNonCashCreditEffect
        )
        dirty_grant = db.get(grant_model, grant.grant_id)
        dirty_effect = db.get(effect_model, effect.effect_id)
        dirty_grant.original_amount = Decimal("999")
        dirty_effect.availability_delta = Decimal("-999")
        assert (
            get_non_cash_credit_grant(db, scope=scope, grant_id=grant.grant_id) == grant
        )
        assert (
            get_non_cash_credit_effect(db, scope=scope, effect_id=effect.effect_id)
            == effect
        )
        db.expire(dirty_grant)
        db.expire(dirty_effect)
        # A later ordinary cash receipt must still publish the separate credit balance.
        receipt = b._settlement_command(
            account.id, scope=scope, source_key=str(uuid4()), amount="10"
        )
        accept_settlement(db, scope=scope, command=receipt)
        position_model = (
            models.ReceivablePositionFactV2
            if plane == "tenant"
            else models.PlatformReceivablePositionFactV2
        )
        latest = db.scalars(
            select(position_model)
            .where(position_model.billing_account_id == account.id)
            .order_by(position_model.source_version.desc())
        ).first()
        snapshot = get_receivable_position_v2(
            db,
            scope=scope,
            billing_account_id=account.id,
            source_version=latest.source_version,
        )
        assert snapshot.available_non_cash_credit.amount == 20
        assert snapshot.collectible_receivable.amount == 90
        assert snapshot.available_credit.amount == 10
        assert snapshot.prepaid_funding.amount == 0


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_credit_rollback_removes_instrument_effect_and_retry_reservation(
    scratch, plane
):
    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        db.commit()
        command = _grant(scope, account.id, document=invoice.id, apply=True)
        grant = grant_non_cash_credit(db, scope=scope, command=command)
        db.rollback()
        grant_model = (
            models.NonCashCreditGrant
            if plane == "tenant"
            else models.PlatformNonCashCreditGrant
        )
        assert (
            db.scalar(select(grant_model.id).where(grant_model.id == grant.grant_id))
            is None
        )
        retried = grant_non_cash_credit(db, scope=scope, command=command)
        assert retried.grant_id != grant.grant_id
        assert _document_outstanding(db, scope=scope, document=invoice) == 70


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_credit_application_races_serialize_grant_room(scratch, plane):
    import threading

    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        grant = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id)
        )
        commands = [_apply(scope, grant.grant_id, invoice.id, "30") for _ in range(2)]
        db.commit()
    barrier = threading.Barrier(2)
    results = []

    def run(command):
        try:
            with b._session(url, scope) as db:
                barrier.wait(timeout=10)
                result = apply_non_cash_credit(db, scope=scope, command=command)
                db.commit()
                results.append(result)
        except BillingRuleViolation as exc:
            results.append(exc)

    threads = [threading.Thread(target=run, args=(command,)) for command in commands]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
        assert not thread.is_alive(), "credit application admission deadlocked"
    assert len(results) == 2
    assert sum(isinstance(value, BillingRuleViolation) for value in results) == 1
    with b._session(url, scope) as db:
        from dotmac_billing.service import _non_cash_available

        assert _non_cash_available(db, scope=scope, grant_id=grant.grant_id) == 20


def test_credit_cross_tenant_query_and_command_denials(scratch):
    from dotmac_billing import get_non_cash_credit_grant

    admin, url, _ = scratch
    first, second = (TenantScope(value) for value in b._create_tenants(admin, 2))
    with b._session(url, first) as db:
        account, _, invoice = b._seed_invoice(db, first, suffix=str(uuid4()))
        command = _grant(first, account.id)
        grant = grant_non_cash_credit(db, scope=first, command=command)
        db.commit()
    with b._session(url, second) as db:
        with pytest.raises(BillingRuleViolation):
            get_non_cash_credit_grant(db, scope=second, grant_id=grant.grant_id)
        with pytest.raises(BillingRuleViolation):
            grant_non_cash_credit(db, scope=second, command=command)
        with pytest.raises(BillingRuleViolation):
            apply_non_cash_credit(
                db, scope=second, command=_apply(second, grant.grant_id, invoice.id)
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_credit_owner_and_invoice_generic_reversal_bypasses_refused(scratch, plane):
    from dotmac_billing.commands import ReversePostingGroupCommand
    from dotmac_billing.service import (
        _reverse_group,
        reverse_posting_group,
        void_document,
    )

    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        grant = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id, document=invoice.id)
        )
        effect = apply_non_cash_credit(
            db, scope=scope, command=_apply(scope, grant.grant_id, invoice.id, "30")
        )
        group_model = (
            models.PostingGroup if plane == "tenant" else models.PlatformPostingGroup
        )
        grant_model = (
            models.NonCashCreditGrant
            if plane == "tenant"
            else models.PlatformNonCashCreditGrant
        )
        row = db.get(grant_model, grant.grant_id)
        group = db.get(group_model, row.posting_group_id)
        with pytest.raises(BillingRuleViolation):
            reverse_posting_group(
                db,
                scope=scope,
                command=ReversePostingGroupCommand(group.id, b.NOW, str(uuid4())),
            )
        with pytest.raises(BillingRuleViolation):
            _reverse_group(
                db,
                scope=scope,
                original=group,
                occurred_at=b.NOW,
                source_ref=str(uuid4()),
            )
        issuance = db.scalars(
            select(group_model).where(
                group_model.billing_account_id == account.id,
                group_model.group_kind == "invoice_issued",
            )
        ).one()
        with pytest.raises(BillingRuleViolation):
            reverse_posting_group(
                db,
                scope=scope,
                command=ReversePostingGroupCommand(issuance.id, b.NOW, str(uuid4())),
            )
        with pytest.raises(BillingRuleViolation):
            void_document(
                db,
                scope=scope,
                document_id=invoice.id,
                actor_ref="finance",
                occurred_at=b.NOW,
                source_ref=str(uuid4()),
            )
        reverse_non_cash_credit_application(
            db,
            scope=scope,
            command=ReverseNonCashCreditApplication(
                scope,
                effect.effect_id,
                str(uuid4()),
                "finance",
                str(uuid4()),
                "1",
                "finance",
                "decision",
                b.NOW,
            ),
        )
        _reverse_group(
            db,
            scope=scope,
            original=issuance,
            occurred_at=b.NOW,
            source_ref=str(uuid4()),
        )
        with pytest.raises(BillingRuleViolation):
            apply_non_cash_credit(
                db, scope=scope, command=_apply(scope, grant.grant_id, invoice.id, "1")
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_replacement_grant_links_exact_unused_reduction_and_caps_cumulative_value(
    scratch, plane
):
    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, _ = b._seed_invoice(db, scope, suffix=str(uuid4()))
        original = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id)
        )
        reduced = reduce_non_cash_credit_grant(
            db,
            scope=scope,
            command=ReduceNonCashCreditGrant(
                scope,
                original.grant_id,
                str(uuid4()),
                "finance",
                str(uuid4()),
                "1",
                "finance",
                "decision",
                b.NOW,
                b._money("20"),
            ),
        )
        command = replace(
            _grant(scope, account.id, amount="12"),
            replaces_grant_id=original.grant_id,
            replaces_effect_id=reduced.effect_id,
        )
        replaced = grant_non_cash_credit(db, scope=scope, command=command)
        assert replaced.replaces_effect_id == reduced.effect_id
        with pytest.raises(BillingRuleViolation):
            grant_non_cash_credit(
                db,
                scope=scope,
                command=replace(
                    command,
                    idempotency_key=str(uuid4()),
                    source_ref=str(uuid4()),
                    amount=b._money("9"),
                ),
            )
        with pytest.raises(BillingRuleViolation):
            reverse_non_cash_credit_application(
                db,
                scope=scope,
                command=ReverseNonCashCreditApplication(
                    scope,
                    reduced.effect_id,
                    str(uuid4()),
                    "finance",
                    str(uuid4()),
                    "1",
                    "finance",
                    "decision",
                    b.NOW,
                ),
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_explicit_excess_and_restriction_refused_without_cash_draw_room(scratch, plane):
    from dotmac_billing.commands import ApplyAvailableCreditCommand
    from dotmac_billing.service import apply_available_credit

    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        other_account, _, other_invoice = b._seed_invoice(
            db, scope, suffix=str(uuid4())
        )
        grant = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id, document=invoice.id)
        )
        with pytest.raises(BillingRuleViolation):
            apply_non_cash_credit(
                db,
                scope=scope,
                command=_apply(scope, grant.grant_id, other_invoice.id, "1"),
            )
        with pytest.raises(BillingRuleViolation):
            apply_non_cash_credit(
                db, scope=scope, command=_apply(scope, grant.grant_id, invoice.id, "51")
            )
        with pytest.raises(BillingRuleViolation):
            apply_available_credit(
                db,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    account.id, invoice.id, b._money("1"), b.NOW, str(uuid4())
                ),
            )
        assert other_account.id != grant.billing_account_id
        assert _document_outstanding(db, scope=scope, document=invoice) == 120


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("correction", ["reverse", "reduce"])
def test_parallel_corrections_cannot_overrestore_or_overreduce(
    scratch, plane, correction
):
    import threading

    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        grant = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id)
        )
        applied = apply_non_cash_credit(
            db, scope=scope, command=_apply(scope, grant.grant_id, invoice.id, "30")
        )
        if correction == "reverse":
            commands = [
                ReverseNonCashCreditApplication(
                    scope,
                    applied.effect_id,
                    str(uuid4()),
                    "finance",
                    str(uuid4()),
                    "1",
                    "finance",
                    "decision",
                    b.NOW,
                    b._money("20"),
                )
                for _ in range(2)
            ]
            call = reverse_non_cash_credit_application
        else:
            commands = [
                ReduceNonCashCreditGrant(
                    scope,
                    grant.grant_id,
                    str(uuid4()),
                    "finance",
                    str(uuid4()),
                    "1",
                    "finance",
                    "decision",
                    b.NOW,
                    b._money("15"),
                )
                for _ in range(2)
            ]
            call = reduce_non_cash_credit_grant
        db.commit()
    barrier = threading.Barrier(2)
    results = []

    def run(command):
        try:
            with b._session(url, scope) as db:
                barrier.wait(timeout=10)
                result = call(db, scope=scope, command=command)
                db.commit()
                results.append(result)
        except BillingRuleViolation as exc:
            results.append(exc)

    threads = [threading.Thread(target=run, args=(command,)) for command in commands]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
        assert not thread.is_alive(), "noncash correction admission deadlocked"
    assert len(results) == 2
    assert sum(isinstance(value, BillingRuleViolation) for value in results) == 1


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_unrelated_exhausted_grant_and_zero_cash_pool_do_not_consume_grant_room(
    scratch, plane
):
    from dotmac_billing.service import _non_cash_available

    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        first = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id)
        )
        apply_non_cash_credit(
            db, scope=scope, command=_apply(scope, first.grant_id, invoice.id)
        )
        assert _non_cash_available(db, scope=scope, grant_id=first.grant_id) == 0
        second = grant_non_cash_credit(
            db, scope=scope, command=_grant(scope, account.id, amount="30")
        )
        applied = apply_non_cash_credit(
            db, scope=scope, command=_apply(scope, second.grant_id, invoice.id)
        )
        assert applied.availability_delta.amount == -30
        position = rebuild_receivable_position(
            db,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert position.available_credit == 0
        assert position.collectible_receivable == 40


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_natural_replay_respects_used_request_keys_but_accepts_fresh_keys(
    scratch, plane
):
    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        first = _grant(scope, account.id)
        second = _grant(scope, account.id)
        grant_a = grant_non_cash_credit(db, scope=scope, command=first)
        grant_b = grant_non_cash_credit(db, scope=scope, command=second)
        with pytest.raises(IdempotencyConflict, match="Idempotency key"):
            grant_non_cash_credit(
                db,
                scope=scope,
                command=replace(first, idempotency_key=second.idempotency_key),
            )
        assert (
            grant_non_cash_credit(
                db, scope=scope, command=replace(first, idempotency_key=str(uuid4()))
            )
            == grant_a
        )
        apply_a = _apply(scope, grant_a.grant_id, invoice.id, "10")
        apply_b = _apply(scope, grant_b.grant_id, invoice.id, "10")
        effect = apply_non_cash_credit(db, scope=scope, command=apply_a)
        apply_non_cash_credit(db, scope=scope, command=apply_b)
        with pytest.raises(IdempotencyConflict, match="Idempotency key"):
            apply_non_cash_credit(
                db,
                scope=scope,
                command=replace(apply_a, idempotency_key=apply_b.idempotency_key),
            )
        assert (
            apply_non_cash_credit(
                db, scope=scope, command=replace(apply_a, idempotency_key=str(uuid4()))
            )
            == effect
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_v2_query_rejects_every_changed_v1_provenance_or_interpretation_field(
    scratch, plane, monkeypatch
):
    from datetime import timedelta

    from dotmac_billing import get_receivable_position_v2

    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, _ = b._seed_invoice(db, scope, suffix=str(uuid4()))
        model = (
            models.ReceivablePositionFactV2
            if plane == "tenant"
            else models.PlatformReceivablePositionFactV2
        )
        original = (
            db.execute(
                select(model.__table__).where(model.billing_account_id == account.id)
            )
            .mappings()
            .one()
        )
        request = {
            "scope": scope,
            "billing_account_id": account.id,
            "source_version": original["source_version"],
        }
        control = get_receivable_position_v2(db, **request)
        changes = {
            "source_owner": "other-owner",
            "source_version": original["source_version"] + 1,
            "billing_account_id": uuid4(),
            "posting_group_watermark": uuid4(),
            "currency": "USD",
            "minor_units": 3,
            "collectible_receivable": original["collectible_receivable"] + 1,
            "available_credit": Decimal("1"),
            "prepaid_funding": Decimal("1"),
            "source_authority": "external_finance",
            "projection_mode": "shadow",
            "derived_from": "projection",
            "completeness": "partial",
            "completeness_reason_code": "legacy-unverified",
            "financial_state": "resolved",
            "observed_at": original["observed_at"] + timedelta(seconds=1),
        }
        if plane == "tenant":
            changes["tenant_id"] = uuid4()
        execute = db.execute
        for field, changed in changes.items():
            altered = dict(original)
            altered[field] = changed

            class MappingResult:
                def __init__(self, row):
                    self.row = row

                def mappings(self):
                    return self

                def one_or_none(self):
                    return self.row

            def corrupt(statement, *args, _result=MappingResult(altered), **kwargs):
                if any(
                    getattr(table, "name", None) == model.__tablename__
                    for table in statement.get_final_froms()
                ):
                    return _result
                return execute(statement, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(db, "execute", corrupt)
                with pytest.raises(BillingRuleViolation, match="V2 position"):
                    get_receivable_position_v2(db, **request)
        assert get_receivable_position_v2(db, **request) == control
