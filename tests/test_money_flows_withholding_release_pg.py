"""Q-F: the balance pass after a released expected-withholding component (PG).

Billing's release raises the cash an invoice requires, so an invoice the balance
could not pay whole may fit now. ``apply_balance_on_withholding_released`` runs
the shared pass in a new transaction.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from dotmac_billing import (
    get_withholding_release,
    release_withholding_component,
    reverse_withholding_release,
)
from dotmac_billing import models as billing_models
from dotmac_billing.commands import (
    WITHHOLDING_RELEASE_PERMISSION,
    ReleaseWithholdingComponent,
    ReverseWithholdingRelease,
)
from dotmac_money_flows import (
    BalancePassFailed,
    InvoiceIssuedV1,
    WithholdingReleasedV1,
    apply_balance_on_invoice_issued,
    apply_balance_on_withholding_released,
)
from sqlalchemy import select

from tests import test_billing_isolation as b
from tests.billing_frozen_fixtures import withholding_command
from tests.money_flows_fixtures import scratch_database, session
from tests.test_money_flows_settle_issue_pg import (
    _allocations,
    _credit,
    _outstanding,
    _ports,
    _world,
)

ACTOR = "finance-admin:fixture"


@pytest.fixture(scope="module")
def scratch():
    with scratch_database() as urls:
        yield urls


class _Reader:
    def holds(self, *, scope, actor_ref, code) -> bool:
        return actor_ref == ACTOR and code == WITHHOLDING_RELEASE_PERMISSION


def _freeze_and_release(scratch, scope, account_id, doc):
    """Freeze 54.00 of expected withholding, then release it; return the release."""
    with session(scratch, scope) as db:
        withholding_command(
            db, account_id, doc, scope=scope, claimed="54.00", accepted="54.00"
        )
        db.commit()
    with session(scratch, scope) as db:
        component = db.scalar(
            select(billing_models.DocumentWithholdingComponent.id).where(
                billing_models.DocumentWithholdingComponent.document_id == doc
            )
        )
    return component


def _release(scratch, scope, account_id, doc, component):
    with session(scratch, scope) as db:
        release = release_withholding_component(
            db,
            scope=scope,
            command=ReleaseWithholdingComponent(
                scope,
                account_id,
                doc,
                component,
                f"release:{component}",
                ACTOR,
                "customer did not deduct",
                b._money("54.00"),
            ),
            permissions=_Reader(),
        )
        db.commit()
        return get_withholding_release(db, scope=scope, release_id=release.release_id)


def test_after_a_release_the_pass_pays_the_invoice_whole_and_a_replay_pays_nothing(
    scratch,
):
    scope, account_id, (doc,) = _world(scratch, balance="144.00")
    component = _freeze_and_release(scratch, scope, account_id, doc)
    with session(scratch, scope) as db:
        before = apply_balance_on_invoice_issued(
            db, scope=scope, request=InvoiceIssuedV1(doc), ports=_ports()
        )
        db.commit()
    assert before.applied_document_ids == ()  # the withholding room blocks it
    release = _release(scratch, scope, account_id, doc, component)
    request = WithholdingReleasedV1(
        account_id, doc, release.release_id, release.recorded_at
    )

    def run():
        with session(scratch, scope) as db:
            outcome = apply_balance_on_withholding_released(
                db, scope=scope, request=request, ports=_ports()
            )
            db.commit()
            return outcome

    outcome = run()
    assert outcome.applied_document_ids == (doc,)
    with session(scratch, scope) as db:
        assert _outstanding(db, scope, doc) == 0
        assert _credit(db, scope, account_id) == 0
        assert _allocations(db) == 1
        # Dating: the application is never earlier than the decision.
        occurred = db.scalar(
            select(billing_models.PostingGroup.occurred_at).where(
                billing_models.PostingGroup.source_ref.like(
                    f"money-flows:settle:release:{release.release_id}:%"
                )
            )
        )
        assert occurred is not None
        assert occurred >= release.recorded_at
    replay = run()
    assert replay.applied_document_ids == ()
    assert replay.reason_code == "invoice_not_open"
    with session(scratch, scope) as db:
        assert _allocations(db) == 1


def test_a_prepaid_account_and_an_empty_balance_are_quiet_no_ops(scratch):
    scope, account_id, (doc,) = _world(scratch, contract="prepaid", balance="144.00")
    component = _freeze_and_release(scratch, scope, account_id, doc)
    release = _release(scratch, scope, account_id, doc, component)
    request = WithholdingReleasedV1(
        account_id, doc, release.release_id, release.recorded_at + timedelta(0)
    )
    with session(scratch, scope) as db:
        outcome = apply_balance_on_withholding_released(
            db, scope=scope, request=request, ports=_ports()
        )
        db.commit()
    assert outcome.reason_code == "account_not_postpaid"

    scope, account_id, (doc,) = _world(scratch)
    component = _freeze_and_release(scratch, scope, account_id, doc)
    release = _release(scratch, scope, account_id, doc, component)
    with session(scratch, scope) as db:
        outcome = apply_balance_on_withholding_released(
            db,
            scope=scope,
            request=WithholdingReleasedV1(
                account_id, doc, release.release_id, release.recorded_at
            ),
            ports=_ports(),
        )
    assert outcome.reason_code == "no_balance"


def _undo(scratch, scope, release_id):
    with session(scratch, scope) as db:
        undo = reverse_withholding_release(
            db,
            scope=scope,
            command=ReverseWithholdingRelease(
                scope, release_id, f"undo:{release_id}", ACTOR, "certificate mistaken"
            ),
            permissions=_Reader(),
        )
        db.commit()
        return get_withholding_release(db, scope=scope, release_id=undo.release_id)


def test_the_undo_row_is_refused_as_the_trigger_and_a_wrong_instant_raises(scratch):
    scope, account_id, (doc,) = _world(scratch, balance="144.00")
    component = _freeze_and_release(scratch, scope, account_id, doc)
    release = _release(scratch, scope, account_id, doc, component)
    undo = _undo(scratch, scope, release.release_id)
    assert undo.reverses_release_id == release.release_id

    def refused(request):
        with session(scratch, scope) as db:
            with pytest.raises(BalancePassFailed) as caught:
                apply_balance_on_withholding_released(
                    db, scope=scope, request=request, ports=_ports()
                )
            return caught.value.code

    assert (
        refused(
            WithholdingReleasedV1(account_id, doc, undo.release_id, undo.recorded_at)
        )
        == "withholding_trigger_is_reversal"
    )
    assert (
        refused(
            WithholdingReleasedV1(
                account_id,
                doc,
                release.release_id,
                release.recorded_at + timedelta(seconds=1),
            )
        )
        == "withholding_instant_mismatch"
    )
    assert (
        refused(
            WithholdingReleasedV1(
                account_id, uuid4(), release.release_id, release.recorded_at
            )
        )
        == "withholding_trigger_mismatch"
    )
    with session(scratch, scope) as db:
        assert _allocations(db) == 0


def test_a_release_that_was_undone_later_runs_quietly(scratch):
    """Not special-cased: the undo restored the room, so Billing's gates say no fit."""
    scope, account_id, (doc,) = _world(scratch, balance="144.00")
    component = _freeze_and_release(scratch, scope, account_id, doc)
    release = _release(scratch, scope, account_id, doc, component)
    _undo(scratch, scope, release.release_id)
    with session(scratch, scope) as db:
        outcome = apply_balance_on_withholding_released(
            db,
            scope=scope,
            request=WithholdingReleasedV1(
                account_id, doc, release.release_id, release.recorded_at
            ),
            ports=_ports(),
        )
        db.commit()
    assert outcome.applied_document_ids == ()
    with session(scratch, scope) as db:
        assert _allocations(db) == 0
