"""R8: post-seal migration items (bi_0021), PostgreSQL.

Every scenario runs in its own tenant, with its own provisioned and sealed
opening window (``dual_control`` off unless a test says otherwise), as the real
ordinary role. The opening import supplies the sealed capture namespace and two
imported open items (residuals 70.00 and 50.00; legacy totals 120.00 and 80.00).
"""

# SQL identifiers below are fixed Billing-owned table names.
# ruff: noqa: S608
from __future__ import annotations

import threading
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest
from dotmac_billing import list_open_receivable_documents, models
from dotmac_billing import migration_items as mi
from dotmac_billing import service as s
from dotmac_billing.commands import (
    MIGRATION_ITEM_PERMISSION,
    AllocationCommand,
    ReversePostingGroupCommand,
)
from dotmac_billing.document_queries import get_document_exposure
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.migration_items import (
    MigrationSourceV1,
    RecordMigrationCreditV1,
    RecordMigrationDebitV1,
    ReverseMigrationItemV1,
    get_accounting_fact_v7,
    get_open_item_tax_snapshots,
    record_migration_credit,
    record_migration_debit,
    reverse_migration_item,
)
from dotmac_billing.withholding_release import (
    release_withholding_component,
    reverse_withholding_release,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from alembic import command as alembic_command
from tests import test_billing_isolation as b
from tests import test_billing_non_cash_credit as noncash
from tests import test_billing_opening_cash_import as cash
from tests import test_billing_opening_receivable_import as ar
from tests import test_billing_withholding_frozen as frozen
from tests import test_billing_withholding_release_pg as qf

ACTOR = "holding-release:fixture"
NOW = ar.evidence.NOW


class _Reader:
    def __init__(self, *actors: str) -> None:
        self.actors = set(actors)

    def holds(self, *, scope, actor_ref, code) -> bool:
        return actor_ref in self.actors and code == MIGRATION_ITEM_PERMISSION


PERMS = _Reader(ACTOR)


@pytest.fixture(scope="module")
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        with cash._opening_scratch(urls) as scoped:
            yield scoped


def _code(refused) -> str:
    return refused.value.code.removeprefix("billing.")


def _world(scratch, *, dual="off", stage="sealed"):
    """A fresh tenant. stage: 'sealed' | 'provisioned' | 'none'."""
    scope, url = noncash._scope_url(scratch, "tenant")
    world = SimpleNamespace(
        scope=scope, url=url, cmd=None, receipt=None, account=None, cap=None
    )
    if stage == "none":
        return world
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd = ar._command(db, scope, account)
        cash._prepare_window(db, scope, cmd, dual)
        world.cmd, world.account, world.cap = cmd, account, cmd.manifest.capture
        if stage == "sealed":
            world.receipt = cash._import_command(
                db,
                scope=scope,
                command=cmd,
                verifier=ar.evidence.TrustedVerifier(),
                version=2,
            )
            cash._seal_window(db, scope)
        db.commit()
    return world


def _source(world, key, number=None) -> MigrationSourceV1:
    cap = world.cap
    return MigrationSourceV1(cap.installation_id, cap.source_system, key, number)


def _debit(world, account, key, *, amount="70.00", index=0, **changes):
    item = world.cmd.manifest.receivables[index]
    fields = {
        "scope": world.scope,
        "billing_account_id": account,
        "source": _source(world, key),
        "original": item.original,
        "amount": b._money(amount),
        "collection": item.collection,
        "evidence_ref": f"archive:{key}",
        "evidence_digest": "d" * 64,
        "actor_ref": ACTOR,
        "command_id": f"debit:{uuid4()}",
        "occurred_at": NOW,
    }
    fields.update(changes)
    return RecordMigrationDebitV1(**fields)


def _credit(world, document_id, key, *, amount="10.00", total="50.00", **changes):
    fields = {
        "scope": world.scope,
        "source": _source(world, key, f"CN-{key}"),
        "source_total": b._money(total),
        "target_document_id": document_id,
        "amount": b._money(amount),
        "evidence_ref": f"archive:{key}",
        "evidence_digest": "c" * 64,
        "actor_ref": ACTOR,
        "command_id": f"credit:{uuid4()}",
        "occurred_at": NOW,
    }
    fields.update(changes)
    return RecordMigrationCreditV1(**fields)


def _reverse(world, item_id, *, reason="entered in error"):
    return ReverseMigrationItemV1(
        world.scope, item_id, ACTOR, f"reverse:{uuid4()}", reason
    )


def _record(db, world, command):
    return record_migration_debit(
        db, scope=world.scope, command=command, permissions=PERMS
    )


def _apply(db, world, command):
    return record_migration_credit(
        db, scope=world.scope, command=command, permissions=PERMS
    )


def _undo(db, world, command):
    return reverse_migration_item(
        db, scope=world.scope, command=command, permissions=PERMS
    )


def _collectible(db, world, account):
    return cash._position(db, world.scope, account).collectible_receivable


def _fund(db, world, account, amount):
    return s.accept_settlement(
        db,
        scope=world.scope,
        command=b._settlement_command(
            account, scope=world.scope, source_key=str(uuid4()), amount=amount
        ),
        accepted_confirmation_evidence=frozenset({"provider_settled"}),
    )


def _allocate(db, world, settlement, document_id, amount):
    return s.allocate_settlement(
        db,
        scope=world.scope,
        command=AllocationCommand(
            settlement.id, document_id, b._money(amount), NOW, str(uuid4())
        ),
    )


def _fact_id(db, group_id):
    return db.execute(
        text(
            "SELECT id FROM mod_billing.accounting_facts "
            "WHERE posting_group_id = :g AND fact_version = 7"
        ),
        {"g": group_id},
    ).scalar_one()


# 1. Debit happy path ------------------------------------------------------


def test_debit_books_a_receivable_with_evidence_and_no_revenue_or_tax(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account = cash._new_account(db, w.scope)
        command = _debit(w, account, "late-1")
        events_before = db.execute(
            text(
                "SELECT count(*) FROM public.outbox_events "
                "WHERE event_type = 'billing.accounting.fact.v7'"
            )
        ).scalar_one()
        item = _record(db, w, command)
        assert item.kind == "debit" and item.generation == 1
        assert item.receivable_delta == b._money("70.00")
        assert item.legacy_source == _source(w, "late-1")
        assert item.recorded_at is not None

        document = db.get(models.BillingDocument, item.document_id)
        original = command.original
        assert document.authoring_mode == "migration_item"
        assert document.document_number == original.document_number
        assert document.document_kind == "invoice" and document.issued_at is not None
        assert document.series_code is None and document.obligation_id is None
        assert document.grand_total == original.grand_total.amount
        assert document.tax_total == original.tax_total.amount

        # Evidence only: the legacy snapshots, summing to the legacy tax.
        tax = get_open_item_tax_snapshots(
            db, scope=w.scope, document_id=item.document_id
        )
        assert tax.authoring_mode == "migration_item"
        assert tax.tax_total == original.tax_total
        assert len(tax.snapshots) == len(original.tax_snapshots) == 1
        assert sum(x.tax_amount.amount for x in tax.snapshots) == (
            original.tax_total.amount
        )

        # One receivable effect, one V7 fact and the usual position facts.
        group = db.get(models.PostingGroup, item.posting_group_id)
        assert group.group_kind == "migration_debit_recorded"
        effects = (
            db.execute(
                select(models.PostingEffect).where(
                    models.PostingEffect.posting_group_id == group.id
                )
            )
            .scalars()
            .all()
        )
        assert [(e.lane, e.amount_delta) for e in effects] == [
            ("receivable", Decimal("70.00"))
        ]
        fact = get_accounting_fact_v7(db, scope=w.scope, fact_id=_fact_id(db, group.id))
        assert fact.effect_kind == "migration_debit_recorded"
        assert fact.effects[0].amount_delta == b._money("70.00")
        assert fact.item.legacy_source.number == original.document_number
        assert fact.item.document_id == item.document_id
        assert fact.reverses_fact_id is None
        assert (
            db.execute(
                text(
                    "SELECT count(*) FROM public.outbox_events "
                    "WHERE event_type = 'billing.accounting.fact.v7'"
                )
            ).scalar_one()
            == events_before + 1
        )

        # Exposure (V1), the open-documents list and the account position.
        exposure = get_document_exposure(db, scope=w.scope, document_id=document.id)
        assert exposure.collectible_receivable == b._money("70.00")
        assert exposure.financial_state == "open"
        page = list_open_receivable_documents(
            db, scope=w.scope, account=account, currency=b.Currency("NGN", 2)
        )
        assert [e.document_id for e in page.entries] == [document.id]
        assert _collectible(db, w, account) == Decimal("70.00")

        # Exact replay returns the stored item and writes nothing.
        groups = db.scalar(select(func.count()).select_from(models.PostingGroup))
        assert _record(db, w, command) == item
        assert db.scalar(select(func.count()).select_from(models.PostingGroup)) == (
            groups
        )
        db.commit()


# 2. Debit refusals --------------------------------------------------------


def test_debit_refusals(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account = cash._new_account(db, w.scope)

        def refused(command, perms=PERMS):
            with pytest.raises(BillingRuleViolation) as caught:
                record_migration_debit(
                    db, scope=w.scope, command=command, permissions=perms
                )
            db.rollback()
            return _code(caught)

        assert refused(_debit(w, account, "k1"), _Reader()) == (
            "migration_item_permission_denied"
        )
        assert refused(_debit(w, account, "k2"), None) == (
            "migration_item_permission_unavailable"
        )
        wrong = MigrationSourceV1("installation:elsewhere", "legacy", "k3")
        assert refused(_debit(w, account, "k3", source=wrong)) == (
            "migration_item_namespace_mismatch"
        )
        carried = w.cmd.manifest.receivables[0].source_invoice_key
        with pytest.raises(BillingConflict) as caught:
            _record(db, w, _debit(w, account, carried))
        assert _code(caught) == "migration_debit_key_carried"
        db.rollback()
        # More than the legacy invoice's own total.
        assert refused(_debit(w, account, "k4", amount="120.01")) == (
            "migration_debit_original_invalid"
        )
        first = _record(db, w, _debit(w, account, "k5"))
        db.commit()
        with pytest.raises(BillingConflict) as caught:
            _record(db, w, _debit(w, account, "k5"))
        assert _code(caught) == "migration_debit_key_claimed"
        db.rollback()
        assert first.generation == 1


def test_debit_needs_a_sealed_off_window(scratch):
    unsealed = _world(scratch, stage="provisioned")
    on = _world(scratch, dual="on")
    with b._session(unsealed.url, unsealed.scope) as db:
        account = cash._new_account(db, unsealed.scope)
        with pytest.raises(BillingRuleViolation) as caught:
            _record(db, unsealed, _debit(unsealed, account, "u1"))
        assert _code(caught) == "migration_item_window_not_sealed"
    with b._session(on.url, on.scope) as db:
        account = cash._new_account(db, on.scope)
        with pytest.raises(BillingRuleViolation) as caught:
            _record(db, on, _debit(on, account, "o1"))
        assert _code(caught) == "migration_item_dual_control_unsupported"
    # No window at all: a tenant that never provisioned one, with a donor's shapes.
    donor = _world(scratch)
    other = _world(scratch, stage="none")
    other.cmd, other.cap = donor.cmd, donor.cap
    with b._session(other.url, other.scope) as db:
        account = cash._new_account(db, other.scope)
        with pytest.raises(BillingRuleViolation) as caught:
            _record(db, other, _debit(other, account, "n1"))
        assert _code(caught) == "migration_item_window_missing"


def test_a_raw_online_insert_cannot_skip_the_database_rules(scratch):
    """The online role holds INSERT: the trigger, not Python, is the bound."""
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account = cash._new_account(db, w.scope)
        item = _record(db, w, _debit(w, account, "raw-1"))
        db.commit()
        # A group of an R8 kind with no item, V7 fact or effect is refused at commit.
        version = db.execute(
            text(
                "SELECT coalesce(max(source_version), 0) + 1 "
                "FROM mod_billing.posting_groups WHERE billing_account_id = :a"
            ),
            {"a": account},
        ).scalar_one()
        db.execute(
            text(
                "INSERT INTO mod_billing.posting_groups (id, tenant_id, "
                "billing_account_id, group_kind, currency, minor_units, source_ref, "
                "source_version, occurred_at) VALUES (:id, :t, :a, "
                "'migration_credit_applied', 'NGN', 2, :ref, :v, now())"
            ),
            {
                "id": uuid4(),
                "t": w.scope.tenant_id,
                "a": account,
                "ref": f"migration-item:{uuid4()}",
                "v": version,
            },
        )
        with pytest.raises(DBAPIError) as caught:
            db.commit()
        assert "exactly one item" in str(caught.value)
        db.rollback()
        # A generic reversal of an R8 group is refused by the owner and by SQL.
        with pytest.raises(BillingRuleViolation) as refused:
            s.reverse_posting_group(
                db,
                scope=w.scope,
                command=ReversePostingGroupCommand(
                    item.posting_group_id, NOW, f"raw-reverse-{uuid4()}"
                ),
            )
        assert _code(refused) == "migration_item_owner_required"
        db.rollback()


# 3. Debit then the ordinary lifecycle ------------------------------------


def test_a_debit_document_is_paid_credited_and_resolved_like_an_open_item(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account = cash._new_account(db, w.scope)
        item = _record(db, w, _debit(w, account, "life-1"))
        doc = item.document_id
        settlement = _fund(db, w, account, "100.00")
        db.commit()
        with pytest.raises(BillingRuleViolation):  # only the 70.00 residual is room
            _allocate(db, w, settlement, doc, "70.01")
        db.rollback()
        _allocate(db, w, settlement, doc, "30.00")
        assert get_document_exposure(
            db, scope=w.scope, document_id=doc
        ).collectible_receivable == b._money("40.00")
        ar._credit(db, w.scope, doc, "10.00")  # a native credit note
        assert get_document_exposure(
            db, scope=w.scope, document_id=doc
        ).collectible_receivable == b._money("30.00")
        assert _collectible(db, w, account) == Decimal("30.00")
        db.commit()


# 4. Debit reversal --------------------------------------------------------


def test_debit_reversal_while_untouched_then_rerecord_at_generation_two(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account = cash._new_account(db, w.scope)
        first = _record(db, w, _debit(w, account, "rev-1"))
        reversal = _undo(db, w, _reverse(w, first.item_id))
        assert reversal.kind == "reversal"
        assert reversal.receivable_delta == b._money("-70.00")
        assert reversal.reverses_item_id == first.item_id
        exposure = get_document_exposure(
            db, scope=w.scope, document_id=first.document_id
        )
        assert exposure.collectible_receivable == b._money("0.00")
        assert exposure.financial_state == "cancelled"
        assert _collectible(db, w, account) == Decimal("0")
        fact = get_accounting_fact_v7(
            db, scope=w.scope, fact_id=_fact_id(db, reversal.posting_group_id)
        )
        assert fact.effect_kind == "migration_item_reversed"
        assert fact.reverses_fact_id == _fact_id(db, first.posting_group_id)
        db.commit()
        with pytest.raises(BillingRuleViolation) as caught:
            _undo(db, w, _reverse(w, first.item_id))
        assert _code(caught) == "migration_item_already_reversed"
        db.rollback()
        second = _record(db, w, _debit(w, account, "rev-1", amount="60.00"))
        assert second.generation == 2
        assert second.document_id != first.document_id
        assert _collectible(db, w, account) == Decimal("60.00")
        db.commit()


def test_debit_reversal_after_a_payment_is_refused_by_python_and_by_the_database(
    scratch,
):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account = cash._new_account(db, w.scope)
        item = _record(db, w, _debit(w, account, "touched-1"))
        _allocate(db, w, _fund(db, w, account, "10.00"), item.document_id, "10.00")
        db.commit()
        with pytest.raises(BillingRuleViolation) as caught:
            _undo(db, w, _reverse(w, item.item_id))
        assert _code(caught) == "migration_debit_touched"
        db.rollback()
        # Defeat the Python guard: the admission trigger still refuses.
        with mock.patch.object(
            s, "_document_outstanding", return_value=Decimal("70.00")
        ):
            with pytest.raises(DBAPIError) as caught:
                _undo(db, w, _reverse(w, item.item_id))
        assert "room between zero and its base" in str(caught.value)
        db.rollback()


def test_two_concurrent_records_of_one_key_have_one_winner(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account = cash._new_account(db, w.scope)
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def attempt(n):
        with b._session(w.url, w.scope) as db:
            command = _debit(w, account, "race-1", command_id=f"race-{n}")
            barrier.wait(timeout=30)
            try:
                _record(db, w, command)
                db.commit()
                outcomes.append("ok")
            except Exception as exc:
                db.rollback()
                outcomes.append(exc)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert outcomes.count("ok") == 1, outcomes
    (loser,) = (o for o in outcomes if o != "ok")
    assert isinstance(loser, BillingConflict | DBAPIError), loser
    with b._session(w.url, w.scope) as db:
        assert (
            db.scalar(
                select(func.count()).where(
                    models.MigrationItem.source_key == "race-1",
                    models.MigrationItem.kind == "debit",
                )
            )
            == 1
        )


# 5. Credits ---------------------------------------------------------------


def test_credit_against_imported_native_and_debit_targets_split_and_bounded(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        first, second = ar._bases(db, w.scope, w.receipt)
        # Imported target: residual 70.00 falls, exposure V2 is sealed and admitted.
        a = _apply(db, w, _credit(w, first.document_id, "cn-1", amount="20.00"))
        assert a.kind == "credit" and a.receivable_delta == b._money("-20.00")
        assert ar._remaining(db, w.scope, first) == Decimal("50.00")
        exposure = get_document_exposure(
            db, scope=w.scope, document_id=first.document_id
        )
        assert exposure.collectible_receivable == b._money("50.00")
        fact = get_accounting_fact_v7(
            db, scope=w.scope, fact_id=_fact_id(db, a.posting_group_id)
        )
        assert fact.effects[0].amount_delta == b._money("-20.00")
        # The same legacy note split over a second invoice, up to its total.
        _apply(db, w, _credit(w, second.document_id, "cn-1", amount="30.00"))
        assert ar._remaining(db, w.scope, second) == Decimal("20.00")
        db.commit()
        with pytest.raises(BillingRuleViolation) as caught:
            _apply(db, w, _credit(w, second.document_id, "cn-1", amount="0.01"))
        assert _code(caught) == "migration_credit_exceeds_source_total"
        db.rollback()
    with b._session(w.url, w.scope) as db:
        # A differing total, then a second account, for the same note.
        _, second = ar._bases(db, w.scope, w.receipt)
        with pytest.raises(BillingRuleViolation) as caught:
            _apply(
                db,
                w,
                _credit(w, second.document_id, "cn-1", amount="1.00", total="60.00"),
            )
        assert _code(caught) == "migration_credit_source_total_differs"
        db.rollback()
        other = cash._new_account(db, w.scope)
        native = cash._invoice(db, w.scope, other)
        db.commit()
        with pytest.raises(BillingRuleViolation) as caught:
            _apply(db, w, _credit(w, native.id, "cn-1", amount="1.00"))
        assert _code(caught) == "migration_credit_source_spread"
        db.rollback()
        # Native target (120.00) and a migration debit target.
        credit = _apply(db, w, _credit(w, native.id, "cn-2", amount="20.00"))
        assert credit.document_id == native.id
        assert get_document_exposure(
            db, scope=w.scope, document_id=native.id
        ).collectible_receivable == b._money("100.00")
        debit = _record(db, w, _debit(w, other, "tgt-1"))
        _apply(db, w, _credit(w, debit.document_id, "cn-3", amount="10.00"))
        assert get_document_exposure(
            db, scope=w.scope, document_id=debit.document_id
        ).collectible_receivable == b._money("60.00")
        # Over the room is refused.
        with pytest.raises(BillingRuleViolation):
            _apply(
                db,
                w,
                _credit(w, debit.document_id, "cn-4", amount="60.01", total="100.00"),
            )
        db.rollback()


# 6. Q-F interaction -------------------------------------------------------


def test_a_credit_stays_out_of_the_withholding_room_until_a_governed_release(
    scratch,
):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account, invoice_id, _ = frozen._invoice(db, w.scope, elected=True)
        db.commit()
        # Total 100.00, 5.00 frozen: a credit may take at most 95.00.
        with pytest.raises(BillingRuleViolation) as caught:
            _apply(
                db,
                w,
                _credit(w, invoice_id, "qf-1", amount="95.01", total="200.00"),
            )
        assert _code(caught) == "migration_credit_exceeds_non_withholding_room"
        db.rollback()
        # The database refuses it too when Python is defeated.
        with mock.patch.object(
            s, "_undischarged_frozen_withholding", return_value=Decimal("0")
        ):
            with pytest.raises(DBAPIError) as caught:
                _apply(
                    db,
                    w,
                    _credit(w, invoice_id, "qf-1", amount="95.01", total="200.00"),
                )
        assert "non-withholding room" in str(caught.value)
        db.rollback()
        cid = qf._component_id(db, invoice_id)
        released = release_withholding_component(
            db,
            scope=w.scope,
            command=qf._release(w.scope, account, invoice_id, cid),
            permissions=qf.PERMS,
        )
        # After the governed release the same credit fits (and then all of it).
        _apply(db, w, _credit(w, invoice_id, "qf-1", amount="100.00", total="200.00"))
        # The undo of the release now finds the room used.
        with pytest.raises(BillingRuleViolation) as caught:
            reverse_withholding_release(
                db,
                scope=w.scope,
                command=qf._undo(w.scope, released.release_id),
                permissions=qf.PERMS,
            )
        assert _code(caught) == "withholding_release_undo_room_used"
        db.rollback()


# 7. Credit reversal -------------------------------------------------------


def test_credit_reversal_restores_room_and_the_credit_can_be_reapplied(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        first, _ = ar._bases(db, w.scope, w.receipt)
        applied = _apply(db, w, _credit(w, first.document_id, "cn-r", amount="20.00"))
        reversal = _undo(db, w, _reverse(w, applied.item_id))
        assert reversal.receivable_delta == b._money("20.00")
        assert ar._remaining(db, w.scope, first) == Decimal("70.00")
        again = _apply(db, w, _credit(w, first.document_id, "cn-r", amount="50.00"))
        assert again.item_id != applied.item_id
        assert ar._remaining(db, w.scope, first) == Decimal("20.00")
        db.commit()


# 8. The debit document is owned by R8 -------------------------------------


def test_the_debit_document_is_reversed_only_through_r8(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account = cash._new_account(db, w.scope)
        item = _record(db, w, _debit(w, account, "own-1"))
        db.commit()
        with pytest.raises(BillingRuleViolation) as caught:
            s.void_document(
                db,
                scope=w.scope,
                document_id=item.document_id,
                actor_ref="billing-test",
                occurred_at=NOW,
                source_ref=str(uuid4()),
            )
        assert _code(caught) == "migration_item_owner_required"
        db.rollback()
        # SQL refuses a voided event on it as well.
        db.add(
            models.DocumentEvent(
                id=uuid4(),
                tenant_id=w.scope.tenant_id,
                document_id=item.document_id,
                event_kind="voided",
                reason=f"raw-{uuid4()}",
                actor_ref="raw",
                occurred_at=NOW,
            )
        )
        with pytest.raises(DBAPIError) as caught:
            db.flush()
        assert "cannot be voided" in str(caught.value)
        db.rollback()
        # A Q-F release finds no frozen component on it.
        with pytest.raises(BillingRuleViolation) as caught:
            release_withholding_component(
                db,
                scope=w.scope,
                command=qf._release(
                    w.scope, SimpleNamespace(id=account), item.document_id, uuid4()
                ),
                permissions=qf.PERMS,
            )
        assert _code(caught) == "withholding_release_component_not_found"
        db.rollback()


# 10. Append-only, RLS, grants ---------------------------------------------


def test_append_only_forced_rls_and_exact_grants(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        account = cash._new_account(db, w.scope)
        item = _record(db, w, _debit(w, account, "ao-1"))
        db.commit()
        for statement in (
            "UPDATE mod_billing.migration_items SET reason = 'x'",
            "DELETE FROM mod_billing.migration_items",
        ):
            with pytest.raises(DBAPIError):
                db.execute(text(statement))
            db.rollback()
        privileges = set(
            db.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_schema = 'mod_billing' "
                    "AND table_name = 'migration_items' "
                    "AND grantee = current_user"
                )
            ).scalars()
        )
        assert privileges == {"SELECT", "INSERT"}
        assert db.execute(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid = 'mod_billing.migration_items'::regclass"
            )
        ).scalar_one()
        assert not db.execute(
            text(
                "SELECT has_table_privilege('platform_api', "
                "'mod_billing.migration_items', 'SELECT,INSERT')"
            )
        ).scalar_one()
        assert (
            db.execute(
                text("SELECT to_regclass('mod_billing.platform_migration_items')")
            ).scalar_one()
            is None
        )
    other = TenantScope(b._create_tenants(scratch[0], 1)[0])
    with b._session(w.url, other) as db:
        assert db.scalar(select(func.count()).select_from(models.MigrationItem)) == 0
    with b._session(w.url, w.scope) as db:
        assert db.get(models.MigrationItem, item.item_id) is not None


# 13. Tax evidence ---------------------------------------------------------


def test_tax_snapshot_read_covers_both_modes_and_refuses_native_and_missing(scratch):
    w = _world(scratch)
    with b._session(w.url, w.scope) as db:
        for basis in ar._bases(db, w.scope, w.receipt):
            tax = get_open_item_tax_snapshots(
                db, scope=w.scope, document_id=basis.document_id
            )
            original = basis.source.original
            assert tax.authoring_mode == "imported_open_item"
            assert tax.tax_total == original.tax_total
            assert tax.snapshots == tuple(original.tax_snapshots)
        account = cash._new_account(db, w.scope)
        native = cash._invoice(db, w.scope, account)
        with pytest.raises(BillingRuleViolation) as caught:
            get_open_item_tax_snapshots(db, scope=w.scope, document_id=native.id)
        assert _code(caught) == "open_item_tax_snapshots_native_document"
        db.rollback()
        # Tax is non-zero but no rows exist (the writer is suppressed): refused.
        with mock.patch.object(mi, "write_tax_snapshots", lambda *a, **k: None):
            item = _record(db, w, _debit(w, account, "notax-1"))
        assert _debit(w, account, "x").original.tax_total.amount > 0
        with pytest.raises(BillingRuleViolation) as caught:
            get_open_item_tax_snapshots(db, scope=w.scope, document_id=item.document_id)
        assert _code(caught) == "open_item_tax_snapshots_unavailable"
        db.rollback()


# 12. Upgrade bi_0020 -> bi_0021 with data ---------------------------------

PREDECESSOR = "bi_0020_withholding_releases"
TARGET = "bi_0021_migration_items"


@pytest.fixture
def at_predecessor():
    with b._scratch_database(
        (ModulePlane.TENANT,), target_revision=PREDECESSOR
    ) as urls:
        with cash._opening_scratch(urls, planes=("tenant",)) as scratch:

            def upgrade():
                alembic_command.upgrade(
                    cash.predecessor._config((ModulePlane.TENANT,)), TARGET
                )

            yield scratch, upgrade


def _room_and_live(db, tenant_id, document_id, account_id):
    return db.execute(
        text(
            "SELECT mod_billing.opening_invoice_room(:d, :t), "
            "mod_billing.has_live_invoice_issuance(:d, :a, 'NGN', 2, :t)"
        ),
        {"d": document_id, "t": tenant_id, "a": account_id},
    ).one()


def test_upgrade_with_data_keeps_every_room_and_then_admits_a_migration_credit(
    at_predecessor,
):
    scratch, upgrade = at_predecessor
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with cash._session(scratch, url, scope) as db:
        account = cash._new_account(db, scope)
        cmd = ar._command(db, scope, account)
        cash._prepare_window(db, scope, cmd, "off")
        receipt = cash._import_command(
            db,
            scope=scope,
            command=cmd,
            verifier=ar.evidence.TrustedVerifier(),
            version=2,
        )
        cash._seal_window(db, scope)
        db.commit()
        imported = ar._bases(db, scope, receipt)
        other = cash._new_account(db, scope)
        native = cash._invoice(db, scope, other)
        db.commit()
        targets = [(x.document_id, account) for x in imported] + [(native.id, other)]
        before = [
            _room_and_live(db, scope.tenant_id, doc, acct) for doc, acct in targets
        ]
        assert [r[0] for r in before] == [
            Decimal("70"),
            Decimal("50"),
            Decimal("120"),
        ]
        assert all(r[1] for r in before)
        db.rollback()
        upgrade()

    with cash._session(scratch, url, scope) as db:
        after = [
            _room_and_live(db, scope.tenant_id, doc, acct) for doc, acct in targets
        ]
        assert after == before
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'mod_billing' "
                    "AND table_name = 'migration_items'"
                )
            )
            == 1
        )
        world = SimpleNamespace(scope=scope, cap=cmd.manifest.capture)
        credit = record_migration_credit(
            db,
            scope=scope,
            command=_credit(world, imported[0].document_id, "up-1", amount="20.00"),
            permissions=PERMS,
        )
        assert credit.receivable_delta == b._money("-20.00")
        assert _room_and_live(db, scope.tenant_id, *targets[0])[0] == Decimal("50")
        db.commit()
