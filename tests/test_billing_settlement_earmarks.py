"""Hosted PostgreSQL proofs over real Billing migrations, roles and both planes."""

from __future__ import annotations

# SQL identifiers are closed test-owned table/function/role literals.
# ruff: noqa: S608
from dataclasses import replace
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from dotmac_billing import (
    CorrectSettlementEarmarkV1,
    EarmarkCorrectionVerificationV1,
    EarmarkSettlementV1,
    GetConfirmedSettlementV1,
    GetFundingAvailabilityV1,
    ReleaseEarmarkV1,
    ReverseEarmarkReleaseV1,
    SettlementEarmarkTargetV1,
    correct_settlement_earmark,
    earmark_settlement,
    get_confirmed_settlement,
    get_funding_availability,
    models,
    service,
)
from dotmac_billing.commands import (
    AllocationCommand,
    ApplyAvailableCreditCommand,
    DeallocationCommand,
    ReallocationCommand,
    RefundCommand,
    ReversePostingGroupCommand,
)
from dotmac_billing.contracts import SettlementFundingLane
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError

from alembic import command as alembic_command
from tests import test_billing_isolation as b
from tests import test_billing_non_cash_credit as runtime
from tests import test_billing_withholding_persistence as migration_fixture

scratch = b.scratch


class TrustedVerifier:
    """Test assembly port; input itself is never accepted as a verdict."""

    def __init__(self, *, admitted=True):
        self.admitted = admitted
        self.calls = []

    def verify_correction(self, request):
        self.calls.append(request)
        if not self.admitted:
            return None
        return EarmarkCorrectionVerificationV1(
            request.operation,
            request.command_digest,
            tuple(
                (claim.earmark_id, claim.evidence_fingerprint)
                for claim in request.claims
            ),
            "test-assembly:authorization:1",
            "test-assembly:independent-outcome:1",
        )


def _scope_url(scratch, plane):
    return runtime._scope_url(scratch, plane)


def _model(plane, name):
    return getattr(models, name if plane == "tenant" else "Platform" + name)


def _seed(scratch, plane, *, targets=True, lane=SettlementFundingLane.PREPAID_FUNDING):
    scope, url = _scope_url(scratch, plane)
    source_ids = (str(uuid4()), str(uuid4()))
    with b._session(url, scope) as db:
        account = service.create_billing_account(
            db,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        offered = replace(
            b._settlement_command(
                account.id, scope=scope, source_key=str(uuid4()), amount="240"
            ),
            funding_lane=lane,
        )
        settlement = service.accept_settlement(
            db,
            scope=scope,
            command=offered,
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        command = EarmarkSettlementV1(
            scope,
            account.id,
            settlement.id,
            b._money("240"),
            "purchase:" + str(uuid4()),
            tuple(
                SettlementEarmarkTargetV1(
                    "subscriptions",
                    "recurring_charge_occurrence",
                    source,
                    "1",
                    b._money("120"),
                )
                for source in source_ids
            )
            if targets
            else (),
            "earmark:" + str(uuid4()),
            b.NOW,
        )
        claim = earmark_settlement(db, scope=scope, command=command)
        db.commit()
    return scope, url, command, claim, source_ids


def _invoice(db, scope, account_id, source):
    obligation = service.accept_rated_obligation(
        db,
        scope=scope,
        command=b._obligation_command(account_id, scope=scope, source_fact_id=source),
        accepted_source_kinds=frozenset({"recurring_charge_occurrence"}),
    )
    draft = service.create_draft_document(
        db, scope=scope, command=b._draft_command(obligation.id)
    )
    return service.issue_document(
        db, scope=scope, command=b._issue_command(draft.id), numbering=b.NUMBERING
    )


def _availability(db, scope, command):
    return get_funding_availability(
        db,
        scope=scope,
        request=GetFundingAvailabilityV1(
            scope, command.billing_account_id, command.settlement_id
        ),
    )


def _allocate(db, scope, command, invoice):
    return service.allocate_settlement(
        db,
        scope=scope,
        command=AllocationCommand(
            command.settlement_id, invoice.id, b._money("120"), b.NOW, str(uuid4())
        ),
    )


def _correct(db, scope, claim, change, verifier=None):
    return correct_settlement_earmark(
        db,
        scope=scope,
        command=CorrectSettlementEarmarkV1(scope, (claim.earmark_id,), change),
        verifier=verifier or TrustedVerifier(),
    )


def test_tenant_claim_isolation_and_role_boundary(scratch):
    _scope, url, offered, _, _ = _seed(scratch, "tenant")
    other = TenantScope(b._create_tenants(scratch[0], 1)[0])
    with b._session(url, other) as db:
        assert db.execute(select(models.SettlementEarmark.id)).first() is None
        with pytest.raises(BillingRuleViolation):
            get_confirmed_settlement(
                db,
                scope=other,
                request=GetConfirmedSettlementV1(
                    other, offered.billing_account_id, offered.settlement_id
                ),
            )
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        for table in (
            "settlement_earmarks",
            "settlement_earmark_targets",
            "settlement_earmark_effects",
        ):
            assert connection.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE oid=CAST(:table AS regclass)"
                ),
                {"table": "mod_billing." + table},
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege('app_user', :table, 'SELECT')"),
                {"table": "mod_billing.platform_" + table},
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege('platform_api', :table, 'SELECT')"),
                {"table": "mod_billing." + table},
            )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_original_receipt_claim_replay_scope_money_and_two_period_targets(
    scratch, plane
):
    scope, url, offered, claim, sources = _seed(scratch, plane)
    with b._session(url, scope) as db:
        observation = get_confirmed_settlement(
            db,
            scope=scope,
            request=GetConfirmedSettlementV1(
                scope, offered.billing_account_id, offered.settlement_id
            ),
        )
        assert observation.amount == b._money("240")
        assert observation.funding_lane is SettlementFundingLane.PREPAID_FUNDING
        assert (
            observation.active_backing
            and observation.confirmation_evidence == "provider_settled"
        )
        assert earmark_settlement(db, scope=scope, command=offered) == claim
        initial = _availability(db, scope, offered)
        assert initial.posted == initial.held == b._money("240")
        assert initial.unrestricted == b._money("0")
        for changed in (
            replace(offered, amount=b._money("241"), targets=()),
            replace(offered, producer_ref="other"),
            replace(offered, targets=()),
            replace(offered, source_ref=str(uuid4())),
        ):
            with pytest.raises(
                (IdempotencyConflict, BillingConflict, BillingRuleViolation)
            ):
                earmark_settlement(db, scope=scope, command=changed)
        wrong = _invoice(db, scope, offered.billing_account_id, str(uuid4()))
        with pytest.raises(BillingRuleViolation, match="targets"):
            _allocate(db, scope, offered, wrong)
        first = _invoice(db, scope, offered.billing_account_id, sources[0])
        _allocate(db, scope, offered, first)
        middle = _availability(db, scope, offered)
        assert middle.held == middle.posted == b._money("120")
        assert middle.availability_fingerprint != initial.availability_fingerprint
        second = _invoice(db, scope, offered.billing_account_id, sources[1])
        _allocate(db, scope, offered, second)
        final = _availability(db, scope, offered)
        assert final.held == final.posted == final.unrestricted == b._money("0")
        assert final.availability_fingerprint != middle.availability_fingerprint


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_empty_target_hold_has_no_automatic_eligibility_and_rollback(scratch, plane):
    scope, url, offered, _, _ = _seed(
        scratch, plane, targets=False, lane=SettlementFundingLane.AVAILABLE_CREDIT
    )
    with b._session(url, scope) as db:
        invoice = _invoice(db, scope, offered.billing_account_id, str(uuid4()))
        with pytest.raises(BillingRuleViolation):
            _allocate(db, scope, offered, invoice)
        with pytest.raises(BillingRuleViolation):
            service.apply_available_credit(
                db,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    offered.billing_account_id,
                    invoice.id,
                    b._money("120"),
                    b.NOW,
                    str(uuid4()),
                ),
            )
        assert _availability(db, scope, offered).held == b._money("240")
        db.rollback()
        assert _availability(db, scope, offered).held == b._money("240")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_ordinary_corrections_refuse_trusted_deallocation_and_inverse_replay(
    scratch, plane
):
    scope, url, offered, claim, sources = _seed(scratch, plane)
    allocation = _model(plane, "AllocationEffect")
    with b._session(url, scope) as db:
        invoice = _invoice(db, scope, offered.billing_account_id, sources[0])
        group = _allocate(db, scope, offered, invoice)
        original_id = db.scalar(
            select(allocation.id).where(allocation.posting_group_id == group.id)
        )
        change = DeallocationCommand(original_id, b._money("60"), b.NOW, str(uuid4()))
        with pytest.raises(BillingRuleViolation):
            service.deallocate_settlement(db, scope=scope, command=change)
        with pytest.raises(BillingRuleViolation):
            _correct(db, scope, claim, change, TrustedVerifier(admitted=False))
        trusted = TrustedVerifier()
        corrected = _correct(db, scope, claim, change, trusted)
        assert _availability(db, scope, offered).held == b._money("180")
        assert _correct(db, scope, claim, change, trusted).id == corrected.id
        assert len(trusted.calls) == 2
        with pytest.raises(BillingRuleViolation):
            _correct(db, scope, claim, change, TrustedVerifier(admitted=False))
        with pytest.raises(BillingRuleViolation):
            service.deallocate_settlement(db, scope=scope, command=change)
        inverse = _correct(
            db,
            scope,
            claim,
            ReversePostingGroupCommand(corrected.id, b.NOW, str(uuid4())),
        )
        assert _availability(db, scope, offered).held == b._money("120")
        _correct(
            db,
            scope,
            claim,
            ReversePostingGroupCommand(inverse.id, b.NOW, str(uuid4())),
        )
        assert _availability(db, scope, offered).held == b._money("180")
        assert earmark_settlement(db, scope=scope, command=offered) == claim


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_reallocation_releases_moved_attribution_without_changing_targets(
    scratch, plane
):
    scope, url, offered, claim, sources = _seed(scratch, plane)
    with b._session(url, scope) as db:
        source = _invoice(db, scope, offered.billing_account_id, sources[0])
        target = _invoice(db, scope, offered.billing_account_id, str(uuid4()))
        _allocate(db, scope, offered, source)
        change = ReallocationCommand(
            offered.settlement_id,
            source.id,
            target.id,
            b._money("60"),
            b.NOW,
            str(uuid4()),
        )
        with pytest.raises(BillingRuleViolation):
            service.reallocate_settlement(db, scope=scope, command=change)
        moved = _correct(db, scope, claim, change)
        assert _availability(db, scope, offered).held == b._money("120")
        _correct(
            db, scope, claim, ReversePostingGroupCommand(moved.id, b.NOW, str(uuid4()))
        )
        assert _availability(db, scope, offered).held == b._money("120")
        assert (
            earmark_settlement(db, scope=scope, command=offered).targets
            == claim.targets
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_refund_and_no_allocation_backing_reversal_require_fresh_authority(
    scratch, plane
):
    scope, url, offered, claim, _ = _seed(scratch, plane, targets=False)
    group_model = _model(plane, "PostingGroup")
    with b._session(url, scope) as db:
        refund = RefundCommand(
            offered.settlement_id, b._money("60"), b.NOW, str(uuid4())
        )
        with pytest.raises(BillingRuleViolation):
            service.refund_settlement(db, scope=scope, command=refund)
        refunded = _correct(db, scope, claim, refund)
        assert _availability(db, scope, offered).held == b._money("180")
        _correct(
            db,
            scope,
            claim,
            ReversePostingGroupCommand(refunded.id, b.NOW, str(uuid4())),
        )
        assert _availability(db, scope, offered).held == b._money("240")
        backing = db.scalar(
            select(group_model.id).where(
                group_model.source_ref == f"settlement:{offered.settlement_id}",
                group_model.group_kind == "settlement_accepted",
            )
        )
        change = ReversePostingGroupCommand(backing, b.NOW, str(uuid4()))
        with pytest.raises(BillingRuleViolation):
            service.reverse_posting_group(db, scope=scope, command=change)
        removed = _correct(db, scope, claim, change)
        assert _availability(db, scope, offered).posted == b._money("0")
        _correct(
            db,
            scope,
            claim,
            ReversePostingGroupCommand(removed.id, b.NOW, str(uuid4())),
        )
        assert _availability(db, scope, offered).held == b._money("240")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_release_spending_does_not_doublecount_held_and_inverse_refuses_spent_room(
    scratch, plane
):
    scope, url, offered, claim, _ = _seed(scratch, plane, targets=False)
    with b._session(url, scope) as db:
        release = _correct(
            db,
            scope,
            claim,
            ReleaseEarmarkV1(
                scope, claim.earmark_id, b._money("120"), str(uuid4()), b.NOW
            ),
        )
        before = _availability(db, scope, offered)
        invoice = _invoice(db, scope, offered.billing_account_id, str(uuid4()))
        _allocate(db, scope, offered, invoice)
        after = _availability(db, scope, offered)
        assert before.held == after.held == b._money("120")
        assert after.posted == b._money("120") and after.unrestricted == b._money("0")
        assert after.availability_fingerprint != before.availability_fingerprint
        with pytest.raises(BillingRuleViolation, match="capacity"):
            _correct(
                db,
                scope,
                claim,
                ReverseEarmarkReleaseV1(
                    scope, release, b._money("120"), str(uuid4()), b.NOW
                ),
            )
        assert earmark_settlement(db, scope=scope, command=offered) == claim


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_cash_and_earmark_survive_optional_consequence_savepoint_retry_original(
    scratch, plane
):
    scope, url, offered, claim, sources = _seed(scratch, plane)
    with b._session(url, scope) as db:
        with pytest.raises(RuntimeError):
            with db.begin_nested():
                invoice = _invoice(db, scope, offered.billing_account_id, sources[0])
                _allocate(db, scope, offered, invoice)
                raise RuntimeError("optional entitlement unavailable")
        assert _availability(db, scope, offered).held == b._money("240")
        assert earmark_settlement(db, scope=scope, command=offered) == claim
        invoice = _invoice(db, scope, offered.billing_account_id, sources[0])
        _allocate(db, scope, offered, invoice)
        assert _availability(db, scope, offered).held == b._money("120")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_migration_upgrade_real_predecessor_and_database_final_immutability(
    monkeypatch, plane
):
    planes = (ModulePlane.TENANT,) if plane == "tenant" else (ModulePlane.PLATFORM,)
    with b._scratch_database(planes, target_revision="bi_0010_line_source_key") as urls:
        monkeypatch.setenv("MIGRATION_DATABASE_URL", urls[0])
        alembic_command.upgrade(
            migration_fixture._config(planes), "bi_0011_settlement_earmarks"
        )
        scope, url, offered, claim, _ = _seed(urls, plane, targets=False)
        prefix = "" if plane == "tenant" else "platform_"
        table = prefix + "settlement_earmarks"
        with b._session(url, scope) as db:
            assert earmark_settlement(db, scope=scope, command=offered) == claim
        with b._session(urls[0], scope) as admin:
            for sql in (
                f"UPDATE mod_billing.{table} SET producer_ref='changed' WHERE id=:id",
                f"DELETE FROM mod_billing.{table} WHERE id=:id",
            ):
                with pytest.raises(DBAPIError), admin.begin_nested():
                    admin.execute(text(sql), {"id": claim.earmark_id})
            admin.execute(
                text(
                    "CREATE FUNCTION mod_billing.test_rewrite_earmark() "
                    "RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN NEW.producer_ref='late'; "
                    "RETURN NEW; END $$; "
                    f"CREATE TRIGGER zzz_test_rewrite BEFORE UPDATE "
                    f"ON mod_billing.{table} "
                    "FOR EACH ROW EXECUTE FUNCTION mod_billing.test_rewrite_earmark()"
                )
            )
            with pytest.raises(DBAPIError), admin.begin_nested():
                admin.execute(
                    text(
                        f"UPDATE mod_billing.{table} SET producer_ref=producer_ref "
                        "WHERE id=:id"
                    ),
                    {"id": claim.earmark_id},
                )
            admin.commit()
        with pytest.raises(DBAPIError):
            alembic_command.downgrade(
                migration_fixture._config(planes), "bi_0010_line_source_key"
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("isolation", ["READ COMMITTED", "REPEATABLE READ"])
def test_claim_and_spend_race_never_accepts_both(scratch, plane, isolation):
    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        settlement = service.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                account.id, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        account_id, settlement_id, invoice_id = account.id, settlement.id, invoice.id
        db.commit()
    barrier = Barrier(2)
    results = []

    def run(claiming):
        try:
            with b._session(url, scope) as db:
                db.rollback()
                db.connection(execution_options={"isolation_level": isolation})
                db.execute(
                    select(_model(plane, "BillingAccount").id).where(
                        _model(plane, "BillingAccount").id == account_id
                    )
                ).scalar_one()
                barrier.wait(timeout=10)
                if claiming:
                    earmark_settlement(
                        db,
                        scope=scope,
                        command=EarmarkSettlementV1(
                            scope,
                            account_id,
                            settlement_id,
                            b._money("120"),
                            "purchase:" + str(uuid4()),
                            (),
                            str(uuid4()),
                            b.NOW,
                        ),
                    )
                else:
                    service.allocate_settlement(
                        db,
                        scope=scope,
                        command=AllocationCommand(
                            settlement_id,
                            invoice_id,
                            b._money("120"),
                            b.NOW,
                            str(uuid4()),
                        ),
                    )
                db.commit()
                results.append("claim" if claiming else "spend")
        except (BillingRuleViolation, BillingConflict, DBAPIError):
            results.append("refused")

    threads = [Thread(target=run, args=(value,)) for value in (True, False)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert results.count("refused") == 1
    assert len(results) == 2


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_account_cash_availability_opening_lot_and_released_mixed_draw_inverse(plane):
    from tests import test_billing_opening_cash_import as opening

    planes = (ModulePlane.TENANT,) if plane == "tenant" else (ModulePlane.PLATFORM,)
    with (
        b._scratch_database(planes) as urls,
        opening._opening_scratch(urls, (plane,)) as scoped,
    ):
        scope, url = _scope_url(scoped, plane)
        with opening._session(scoped, url, scope) as db:
            account_id = opening._new_account(db, scope)
            _, receipt = opening._import(db, scope, account_id, amounts=(6000,))
            opening._seal_window(db, scope)
            # The same permanent cash-source namespace cannot be replayed as
            # a new live receipt after supported historical opening admission.
            alias = replace(
                b._settlement_command(
                    account_id, scope=scope, source_key=f"{account_id}:0", amount="60"
                ),
                source_system="legacy-payments",
            )
            with pytest.raises(BillingConflict):
                service.accept_settlement(
                    db,
                    scope=scope,
                    command=alias,
                    accepted_confirmation_evidence=frozenset({"provider_settled"}),
                )
            settlement = service.accept_settlement(
                db,
                scope=scope,
                command=b._settlement_command(
                    account_id, scope=scope, source_key=str(uuid4()), amount="240"
                ),
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
            offered = EarmarkSettlementV1(
                scope,
                account_id,
                settlement.id,
                b._money("240"),
                "purchase:" + str(uuid4()),
                (),
                str(uuid4()),
                b.NOW,
            )
            claim = earmark_settlement(db, scope=scope, command=offered)
            _correct(
                db,
                scope,
                claim,
                ReleaseEarmarkV1(
                    scope, claim.earmark_id, b._money("60"), str(uuid4()), b.NOW
                ),
            )
            request = GetFundingAvailabilityV1(scope, account_id)
            before = get_funding_availability(db, scope=scope, request=request)
            assert before.posted == b._money("300")
            assert before.held == b._money("180")
            assert before.unrestricted == b._money("120")
            invoice = opening._invoice(db, scope, account_id)
            draw = service.apply_available_credit(
                db,
                scope=scope,
                command=ApplyAvailableCreditCommand(
                    account_id, invoice.id, b._money("120"), b.NOW, str(uuid4())
                ),
            )
            after = get_funding_availability(db, scope=scope, request=request)
            assert after.posted == after.held == b._money("180")
            assert after.unrestricted == b._money("0")
            assert after.availability_fingerprint != before.availability_fingerprint
            assert service.get_opening_cash_lot_position(
                db, scope=scope, lot_id=receipt.lot_ids[0]
            ).remaining == b._money("0")
            first_inverse = opening._inverse(scope, draw.id, "60")
            with pytest.raises(BillingRuleViolation):
                service.reverse_cash_credit_draw(db, scope=scope, command=first_inverse)
            _correct(db, scope, claim, first_inverse)
            middle = get_funding_availability(db, scope=scope, request=request)
            assert middle.posted == b._money("240") and middle.held == b._money("180")
            _correct(db, scope, claim, opening._inverse(scope, draw.id, "60"))
            final = get_funding_availability(db, scope=scope, request=request)
            assert final.posted == b._money("300") and final.held == b._money("180")
            assert final.unrestricted == b._money("120")
            assert earmark_settlement(db, scope=scope, command=offered) == claim


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_owner_cannot_update_delete_late_rewrite_or_truncate_any_retained_fact(
    scratch, plane
):
    scope, url, offered, claim, _ = _seed(scratch, plane)
    with b._session(url, scope) as db:
        _correct(
            db,
            scope,
            claim,
            ReleaseEarmarkV1(
                scope, claim.earmark_id, b._money("60"), str(uuid4()), b.NOW
            ),
        )
        db.commit()
    prefix = "" if plane == "tenant" else "platform_"
    with b._session(scratch[0], scope) as admin:
        for kind in (
            "settlement_earmarks",
            "settlement_earmark_targets",
            "settlement_earmark_effects",
        ):
            table = prefix + kind
            for sql in (
                f"UPDATE mod_billing.{table} SET id=id",
                f"DELETE FROM mod_billing.{table}",
                f"TRUNCATE mod_billing.{table} CASCADE",
            ):
                with pytest.raises(DBAPIError), admin.begin_nested():
                    admin.execute(text(sql))
            admin.execute(
                text(
                    f"CREATE FUNCTION mod_billing.test_late_{table}() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN NEW.currency='USD'; RETURN NEW; END $$"
                )
            )
            admin.execute(
                text(
                    "CREATE TRIGGER zzz_late_rewrite BEFORE UPDATE "
                    f"ON mod_billing.{table} FOR EACH ROW EXECUTE FUNCTION "
                    f"mod_billing.test_late_{table}()"
                )
            )
            with pytest.raises(DBAPIError), admin.begin_nested():
                admin.execute(text(f"UPDATE mod_billing.{table} SET id=id"))
        admin.rollback()
    with b._session(url, scope) as db:
        assert earmark_settlement(db, scope=scope, command=offered) == claim
        assert _availability(db, scope, offered).held == b._money("180")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("fault", ["operation", "digest", "fingerprint", "proof"])
def test_verifier_positive_shape_near_misses_cannot_authorize_capacity(
    scratch, plane, fault
):
    scope, url, offered, claim, _ = _seed(scratch, plane)

    class NearMissVerifier(TrustedVerifier):
        def verify_correction(self, request):
            verdict = super().verify_correction(request)
            if fault == "operation":
                return replace(verdict, operation="billing.earmark.unrelated")
            if fault == "digest":
                return replace(verdict, command_digest="0" * 64)
            if fault == "fingerprint":
                return replace(
                    verdict, claim_fingerprints=((claim.earmark_id, "0" * 64),)
                )
            return replace(verdict, authorization_proof_ref=" ")

    with b._session(url, scope) as db:
        before = _availability(db, scope, offered)
        change = ReleaseEarmarkV1(
            scope, claim.earmark_id, b._money("60"), str(uuid4()), b.NOW
        )
        with pytest.raises(BillingRuleViolation):
            _correct(db, scope, claim, change, NearMissVerifier())
        assert _availability(db, scope, offered) == before
        release = _correct(db, scope, claim, change)
        inverse = _correct(
            db,
            scope,
            claim,
            ReverseEarmarkReleaseV1(
                scope, release, b._money("60"), str(uuid4()), b.NOW
            ),
        )
        assert _availability(db, scope, offered).held == b._money("240")
        _correct(
            db,
            scope,
            claim,
            ReverseEarmarkReleaseV1(
                scope, inverse, b._money("60"), str(uuid4()), b.NOW
            ),
        )
        assert _availability(db, scope, offered).held == b._money("180")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_multi_claim_inverse_requires_exact_claim_set_and_one_account(scratch, plane):
    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account = service.create_billing_account(
            db,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        claims = []
        for _ in range(2):
            settlement = service.accept_settlement(
                db,
                scope=scope,
                command=b._settlement_command(
                    account.id, scope=scope, source_key=str(uuid4())
                ),
                accepted_confirmation_evidence=frozenset({"provider_settled"}),
            )
            claim_command = EarmarkSettlementV1(
                scope,
                account.id,
                settlement.id,
                b._money("120"),
                "purchase:" + str(uuid4()),
                (),
                str(uuid4()),
                b.NOW,
            )
            claim = earmark_settlement(db, scope=scope, command=claim_command)
            claims.append(claim)
            _correct(
                db,
                scope,
                claim,
                ReleaseEarmarkV1(
                    scope, claim.earmark_id, b._money("60"), str(uuid4()), b.NOW
                ),
            )
        invoice = _invoice(db, scope, account.id, str(uuid4()))
        draw = service.apply_available_credit(
            db,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account.id, invoice.id, b._money("120"), b.NOW, str(uuid4())
            ),
        )
        request = GetFundingAvailabilityV1(scope, account.id)
        before = get_funding_availability(db, scope=scope, request=request)
        change = ReversePostingGroupCommand(draw.id, b.NOW, str(uuid4()))
        with pytest.raises(BillingRuleViolation):
            _correct(db, scope, claims[0], change)
        assert get_funding_availability(db, scope=scope, request=request) == before
        result = correct_settlement_earmark(
            db,
            scope=scope,
            command=CorrectSettlementEarmarkV1(
                scope, tuple(claim.earmark_id for claim in claims), change
            ),
            verifier=TrustedVerifier(),
        )
        after = get_funding_availability(db, scope=scope, request=request)
        assert result.id != draw.id and after.posted == b._money("240")
        assert after.held == b._money("120") and after.unrestricted == b._money("120")
        other = service.create_billing_account(
            db,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        settlement = service.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                other.id, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        foreign = earmark_settlement(
            db,
            scope=scope,
            command=EarmarkSettlementV1(
                scope,
                other.id,
                settlement.id,
                b._money("120"),
                "purchase:" + str(uuid4()),
                (),
                str(uuid4()),
                b.NOW,
            ),
        )
        with pytest.raises(BillingRuleViolation):
            correct_settlement_earmark(
                db,
                scope=scope,
                command=CorrectSettlementEarmarkV1(
                    scope, (claims[0].earmark_id, foreign.earmark_id), change
                ),
                verifier=TrustedVerifier(),
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_qualified_held_first_refund_retains_released_capacity_and_never_goes_negative(
    scratch, plane
):
    scope, url, offered, claim, _ = _seed(scratch, plane, targets=False)
    with b._session(url, scope) as db:
        _correct(
            db,
            scope,
            claim,
            ReleaseEarmarkV1(
                scope, claim.earmark_id, b._money("120"), str(uuid4()), b.NOW
            ),
        )
        _correct(
            db,
            scope,
            claim,
            RefundCommand(offered.settlement_id, b._money("60"), b.NOW, str(uuid4())),
        )
        first = _availability(db, scope, offered)
        assert first.posted == b._money("180") and first.held == b._money("60")
        assert first.unrestricted == b._money("120")
        _correct(
            db,
            scope,
            claim,
            RefundCommand(offered.settlement_id, b._money("80"), b.NOW, str(uuid4())),
        )
        second = _availability(db, scope, offered)
        assert second.posted == b._money("100") and second.held == b._money("0")
        assert second.unrestricted == b._money("100")
        _correct(
            db,
            scope,
            claim,
            RefundCommand(offered.settlement_id, b._money("20"), b.NOW, str(uuid4())),
        )
        final = _availability(db, scope, offered)
        assert final.posted == final.unrestricted == b._money("80")
        assert final.held == b._money("0")
        assert earmark_settlement(db, scope=scope, command=offered) == claim


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_claimed_inverse_respects_new_receivable_coverage_and_linked_full_room(
    scratch, plane
):
    scope, url, offered, claim, sources = _seed(scratch, plane)
    allocation = _model(plane, "AllocationEffect")
    with b._session(url, scope) as db:
        invoice = _invoice(db, scope, offered.billing_account_id, sources[0])
        allocated = _allocate(db, scope, offered, invoice)
        original_id = db.scalar(
            select(allocation.id).where(allocation.posting_group_id == allocated.id)
        )
        reduced = _correct(
            db,
            scope,
            claim,
            DeallocationCommand(original_id, b._money("60"), b.NOW, str(uuid4())),
        )
        restored = _correct(
            db,
            scope,
            claim,
            ReversePostingGroupCommand(reduced.id, b.NOW, str(uuid4())),
        )
        assert restored.id != reduced.id
        full = _correct(
            db,
            scope,
            claim,
            DeallocationCommand(original_id, b._money("120"), b.NOW, str(uuid4())),
        )
        assert _availability(db, scope, offered).held == b._money("240")
        other = service.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                offered.billing_account_id, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        service.allocate_settlement(
            db,
            scope=scope,
            command=AllocationCommand(
                other.id, invoice.id, b._money("120"), b.NOW, str(uuid4())
            ),
        )
        with pytest.raises(BillingRuleViolation, match="receivable"):
            _correct(
                db,
                scope,
                claim,
                ReversePostingGroupCommand(full.id, b.NOW, str(uuid4())),
            )
        assert _availability(db, scope, offered).held == b._money("240")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_unearmarked_inverse_cannot_overcover_one_invoice_using_other_open_ar(
    scratch, plane
):
    scope, url = _scope_url(scratch, plane)
    allocation = _model(plane, "AllocationEffect")
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        # This second open invoice keeps aggregate account AR nonnegative even
        # if the first invoice were incorrectly covered twice by an inverse.
        _invoice(db, scope, account.id, str(uuid4()))
        first = service.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                account.id, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        posted = service.allocate_settlement(
            db,
            scope=scope,
            command=AllocationCommand(
                first.id, invoice.id, b._money("120"), b.NOW, str(uuid4())
            ),
        )
        original = db.scalar(
            select(allocation.id).where(allocation.posting_group_id == posted.id)
        )
        removed = service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(original, b._money("120"), b.NOW, str(uuid4())),
        )
        service.reverse_posting_group(
            db,
            scope=scope,
            command=ReversePostingGroupCommand(removed.id, b.NOW, str(uuid4())),
        )
        # Descendant-aware room admits a full second deallocation after its
        # original inverse restored the allocation; direct-offset sums do not.
        removed_again = service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(original, b._money("120"), b.NOW, str(uuid4())),
        )
        second = service.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                account.id, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        service.allocate_settlement(
            db,
            scope=scope,
            command=AllocationCommand(
                second.id, invoice.id, b._money("120"), b.NOW, str(uuid4())
            ),
        )
        with pytest.raises(BillingRuleViolation, match="receivable"):
            service.reverse_posting_group(
                db,
                scope=scope,
                command=ReversePostingGroupCommand(
                    removed_again.id, b.NOW, str(uuid4())
                ),
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_unearmarked_backing_removal_requires_zero_consumption_and_retains_history(
    scratch, plane
):
    scope, url = _scope_url(scratch, plane)
    group_model, allocation = (
        _model(plane, "PostingGroup"),
        _model(plane, "AllocationEffect"),
    )
    with b._session(url, scope) as db:
        account, _, invoice = b._seed_invoice(db, scope, suffix=str(uuid4()))
        settlement = service.accept_settlement(
            db,
            scope=scope,
            command=b._settlement_command(
                account.id, scope=scope, source_key=str(uuid4())
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        group = service.allocate_settlement(
            db,
            scope=scope,
            command=AllocationCommand(
                settlement.id, invoice.id, b._money("120"), b.NOW, str(uuid4())
            ),
        )
        original_id = db.scalar(
            select(allocation.id).where(allocation.posting_group_id == group.id)
        )
        backing_id = db.scalar(
            select(group_model.id).where(
                group_model.source_ref == f"settlement:{settlement.id}",
                group_model.group_kind == "settlement_accepted",
            )
        )
        change = ReversePostingGroupCommand(backing_id, b.NOW, str(uuid4()))
        with pytest.raises(BillingRuleViolation) as refused:
            service.reverse_posting_group(db, scope=scope, command=change)
        assert refused.value.code == "billing.settlement_backing_consumed"
        service.deallocate_settlement(
            db,
            scope=scope,
            command=DeallocationCommand(
                original_id, b._money("120"), b.NOW, str(uuid4())
            ),
        )
        removed = service.reverse_posting_group(db, scope=scope, command=change)
        observation = get_confirmed_settlement(
            db,
            scope=scope,
            request=GetConfirmedSettlementV1(scope, account.id, settlement.id),
        )
        assert not observation.active_backing and observation.amount == b._money("120")
        restored = service.reverse_posting_group(
            db,
            scope=scope,
            command=ReversePostingGroupCommand(removed.id, b.NOW, str(uuid4())),
        )
        assert restored.id != removed.id
        assert get_confirmed_settlement(
            db,
            scope=scope,
            request=GetConfirmedSettlementV1(scope, account.id, settlement.id),
        ).active_backing


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_confirmed_receipt_original_hash_keeps_offset_spelling_opaque(scratch, plane):
    from datetime import timedelta, timezone
    from decimal import Decimal

    from dotmac_kernel.money import Money

    scope, url = _scope_url(scratch, plane)
    with b._session(url, scope) as db:
        account = service.create_billing_account(
            db,
            scope=scope,
            external_account_ref=str(uuid4()),
            currency="NGN",
            minor_units=2,
        )
        offered = b._settlement_command(
            account.id, scope=scope, source_key=str(uuid4()), amount="120"
        )
        offered = replace(
            offered,
            occurred_at=offered.occurred_at.astimezone(timezone(timedelta(hours=1))),
            amount=Money.of(Decimal("120.0000"), offered.amount.currency),
        )
        source = service.accept_settlement(
            db,
            scope=scope,
            command=offered,
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        request = GetConfirmedSettlementV1(scope, account.id, source.id)
        original_hash = source.request_fingerprint
        db.commit()
    with b._session(url, scope) as db:
        observed = get_confirmed_settlement(db, scope=scope, request=request)
        assert observed.amount == offered.amount and observed.active_backing
        assert observed.request_fingerprint == original_hash
        assert observed.occurred_at == offered.occurred_at
