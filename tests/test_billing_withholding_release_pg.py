"""Q-F: release of a frozen expected-withholding component (bi_0020), PostgreSQL.

A release raises the cash an invoice requires (frozen minus discharged minus
released); it posts nothing, emits one ids-only outbox event and is append-only.
"""

# SQL identifiers below are fixed Billing-owned table names.
# ruff: noqa: S608
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_billing import service
from dotmac_billing.commands import (
    WITHHOLDING_RELEASE_PERMISSION,
    ApplyAvailableCreditCommand,
    ReleaseWithholdingComponent,
    ReverseWithholdingDischarge,
    ReverseWithholdingRelease,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import (
    accept_withholding_discharge,
    apply_available_credit,
    reverse_withholding_discharge,
)
from dotmac_billing.withholding_release import (
    get_withholding_component_position,
    release_withholding_component,
    reverse_withholding_release,
)
from dotmac_kernel.cache import PlatformScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as billing
from tests import test_billing_withholding_frozen as frozen

_money = billing._money
ACTOR = "finance-admin:fixture"


class _Permissions:
    def __init__(self, *actors: str) -> None:
        self.actors = set(actors)
        self.asked: list[str] = []

    def holds(self, *, scope, actor_ref, code) -> bool:
        self.asked.append(code)
        return actor_ref in self.actors and code == WITHHOLDING_RELEASE_PERMISSION


PERMS = _Permissions(ACTOR)


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _component_id(session, invoice_id):
    return session.execute(
        text(
            "SELECT id FROM mod_billing.document_withholding_components "
            "WHERE document_id = :d"
        ),
        {"d": invoice_id},
    ).scalar_one()


def _release(
    scope, account, invoice_id, component_id, *, amount="5.00", key=None, **kw
):
    fields = {
        "command_id": key or f"release:{uuid4()}",
        "actor_ref": ACTOR,
        "reason": "customer did not deduct",
        "expected_amount": _money(amount),
    }
    fields.update(kw)
    return ReleaseWithholdingComponent(
        scope, account.id, invoice_id, component_id, **fields
    )


def _undo(scope, release_id, *, key=None):
    return ReverseWithholdingRelease(
        scope, release_id, key or f"undo:{uuid4()}", ACTOR, "certificate mistaken"
    )


def _draw(session, scope, account, invoice_id, amount, tag):
    return apply_available_credit(
        session,
        scope=scope,
        command=ApplyAvailableCreditCommand(
            account.id, invoice_id, _money(amount), billing.NOW, tag
        ),
    )


def _world(scratch):
    admin_url, tenant_url, _ = scratch
    scope = frozen._scope(admin_url)
    return scope, tenant_url


def _count(session, table, document_id=None):
    sql = f"SELECT count(*) FROM mod_billing.{table}"
    params = {}
    if document_id is not None:
        sql += " WHERE document_id = :d"
        params = {"d": document_id}
    return session.execute(text(sql), params).scalar_one()


def _code(refused) -> str:
    return refused.value.code


def test_release_of_an_undischarged_component_frees_the_cash_requirement(scratch):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, _ = frozen._invoice(session, scope, elected=True)
        frozen._fund_credit(session, scope, account, "100.00")
        with pytest.raises(BillingRuleViolation) as refused:
            _draw(session, scope, account, invoice_id, "100.00", f"full-{invoice_id}")
        assert _code(refused) == "billing.credit_exceeds_non_withholding_room"
        cid = _component_id(session, invoice_id)
        events_before = session.execute(
            text(
                "SELECT count(*) FROM public.outbox_events "
                "WHERE event_type = 'billing.withholding.released.v1'"
            )
        ).scalar_one()
        groups = _count(session, "posting_groups")
        facts = _count(session, "accounting_facts")
        exposures = _count(session, "receivable_exposure_facts")
        released = release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, cid),
            permissions=PERMS,
        )
        assert _count(session, "posting_groups") == groups
        assert _count(session, "accounting_facts") == facts
        assert _count(session, "receivable_exposure_facts") == exposures
        assert released.amount_delta.amount == Decimal("5.00")
        assert released.recorded_at is not None
        (position,) = get_withholding_component_position(
            session, scope=scope, document_id=invoice_id
        )
        assert position.residual.amount == 0
        assert position.released.amount == Decimal("5.00")
        _draw(session, scope, account, invoice_id, "100.00", f"full2-{invoice_id}")
        # Nothing was posted for the release itself (only the draw added a group).
        # The release itself wrote nothing; the credit draw is a separate posting.
        assert _count(session, "accounting_facts") >= facts
        assert _count(session, "posting_groups") > groups
        events = session.execute(
            text(
                "SELECT payload FROM public.outbox_events "
                "WHERE event_type = 'billing.withholding.released.v1'"
            )
        ).all()
        assert len(events) == events_before + 1
        payload = events[-1].payload
        assert set(payload) == {
            "release_id",
            "billing_account_id",
            "document_id",
            "component_id",
        }
        session.rollback()


def test_partly_discharged_releases_only_the_residual_and_a_stale_amount_is_refused(
    scratch,
):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, evidence = frozen._invoice(session, scope, elected=True)
        accept_withholding_discharge(
            session,
            scope=scope,
            command=frozen._discharge(
                account, invoice_id, scope, evidence, accepted="3.00"
            ),
        )
        cid = _component_id(session, invoice_id)
        with pytest.raises(BillingRuleViolation) as refused:
            release_withholding_component(
                session,
                scope=scope,
                command=_release(scope, account, invoice_id, cid, amount="5.00"),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_amount_stale"
        released = release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, cid, amount="2.00"),
            permissions=PERMS,
        )
        assert released.amount_delta.amount == Decimal("2.00")
        session.rollback()


def test_fully_discharged_is_refused_but_discharged_then_reversed_is_releasable(
    scratch,
):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, evidence = frozen._invoice(session, scope, elected=True)
        discharge = accept_withholding_discharge(
            session,
            scope=scope,
            command=frozen._discharge(account, invoice_id, scope, evidence),
        )
        cid = _component_id(session, invoice_id)
        with pytest.raises(BillingRuleViolation) as refused:
            release_withholding_component(
                session,
                scope=scope,
                command=_release(scope, account, invoice_id, cid),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_nothing_to_release"
        reverse_withholding_discharge(
            session,
            scope=scope,
            command=ReverseWithholdingDischarge(
                scope,
                discharge.id,
                f"reverse:{discharge.id}",
                f"reverse-source:{discharge.id}",
                "finance-user:fixture",
                "trusted-assembly-audit:fixture",
                billing.NOW,
            ),
        )
        released = release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, cid),
            permissions=PERMS,
        )
        assert released.amount_delta.amount == Decimal("5.00")
        session.rollback()


def test_a_discharge_after_a_release_is_refused_by_service_and_by_database(
    scratch, monkeypatch
):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, evidence = frozen._invoice(session, scope, elected=True)
        cid = _component_id(session, invoice_id)
        release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, cid),
            permissions=PERMS,
        )
        with pytest.raises(BillingRuleViolation) as refused:
            accept_withholding_discharge(
                session,
                scope=scope,
                command=frozen._discharge(account, invoice_id, scope, evidence),
            )
        assert _code(refused) == "billing.withholding_exceeds_frozen_component"
        monkeypatch.setattr(
            service, "_require_frozen_withholding", lambda *a, **k: None
        )
        with pytest.raises(DBAPIError, match="leaves its frozen component"):
            accept_withholding_discharge(
                session,
                scope=scope,
                command=frozen._discharge(account, invoice_id, scope, evidence),
            )
        session.rollback()


def test_idempotency_replay_conflict_and_a_second_key(scratch):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, _ = frozen._invoice(session, scope, elected=True)
        cid = _component_id(session, invoice_id)
        command = _release(scope, account, invoice_id, cid, key="release:fixed")
        first = release_withholding_component(
            session, scope=scope, command=command, permissions=PERMS
        )
        again = release_withholding_component(
            session, scope=scope, command=command, permissions=PERMS
        )
        assert first.release_id == again.release_id
        assert _count(session, "withholding_releases", invoice_id) == 1
        with pytest.raises(IdempotencyConflict):
            release_withholding_component(
                session,
                scope=scope,
                command=replace(command, reason="a different reason"),
                permissions=PERMS,
            )
        with pytest.raises(BillingRuleViolation) as refused:
            release_withholding_component(
                session,
                scope=scope,
                command=_release(scope, account, invoice_id, cid),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_nothing_to_release"
        session.rollback()


def test_two_concurrent_sessions_exactly_one_release_wins(scratch):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, _ = frozen._invoice(session, scope, elected=True)
        cid = _component_id(session, invoice_id)
        account = SimpleNamespace(id=account.id)  # detach before the session closes
        session.commit()
    outcome: list[str] = []
    started = threading.Event()

    def second():
        try:
            with billing._session(url, scope) as other:
                started.set()
                release_withholding_component(
                    other,
                    scope=scope,
                    command=_release(scope, account, invoice_id, cid),
                    permissions=PERMS,
                )
                other.commit()
                outcome.append("committed")
        except BillingRuleViolation as error:
            outcome.append(error.code)

    with billing._session(url, scope) as holder:
        release_withholding_component(
            holder,
            scope=scope,
            command=_release(scope, account, invoice_id, cid),
            permissions=PERMS,
        )
        thread = threading.Thread(target=second, daemon=True)
        thread.start()
        assert started.wait(timeout=30)
        time.sleep(1.0)
        assert outcome == [] and thread.is_alive()  # waits on the account lock
        holder.commit()
    thread.join(timeout=60)
    assert outcome == ["billing.withholding_release_nothing_to_release"]
    with billing._session(url, scope) as check:
        assert _count(check, "withholding_releases", invoice_id) == 1


def test_undo_reblocks_the_residual_and_a_new_release_is_allowed(scratch):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, _ = frozen._invoice(session, scope, elected=True)
        frozen._fund_credit(session, scope, account, "100.00")
        cid = _component_id(session, invoice_id)
        released = release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, cid),
            permissions=PERMS,
        )
        undo = reverse_withholding_release(
            session,
            scope=scope,
            command=_undo(scope, released.release_id),
            permissions=PERMS,
        )
        assert undo.amount_delta.amount == Decimal("-5.00")
        assert undo.reverses_release_id == released.release_id
        with pytest.raises(BillingRuleViolation) as refused:
            _draw(session, scope, account, invoice_id, "100.00", f"d-{invoice_id}")
        assert _code(refused) == "billing.credit_exceeds_non_withholding_room"
        with pytest.raises(BillingRuleViolation) as refused:
            reverse_withholding_release(
                session,
                scope=scope,
                command=_undo(scope, released.release_id),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_already_reversed"
        with pytest.raises(BillingRuleViolation) as refused:
            reverse_withholding_release(
                session,
                scope=scope,
                command=_undo(scope, undo.release_id),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_undo_terminal"
        again = release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, cid),
            permissions=PERMS,
        )
        assert again.release_id != released.release_id
        session.rollback()


def test_undo_is_refused_when_the_released_room_was_already_used(scratch):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, _ = frozen._invoice(session, scope, elected=True)
        frozen._fund_credit(session, scope, account, "100.00")
        cid = _component_id(session, invoice_id)
        released = release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, cid),
            permissions=PERMS,
        )
        _draw(session, scope, account, invoice_id, "100.00", f"all-{invoice_id}")
        with pytest.raises(BillingRuleViolation) as refused:
            reverse_withholding_release(
                session,
                scope=scope,
                command=_undo(scope, released.release_id),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_undo_room_used"
        session.rollback()


def test_refusals_platform_reader_permission_outstanding_and_foreign_component(
    scratch,
):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, _ = frozen._invoice(session, scope, elected=True)
        other_account, other_invoice, _ = frozen._invoice(session, scope, elected=True)
        cid = _component_id(session, invoice_id)
        command = _release(scope, account, invoice_id, cid)
        with pytest.raises(BillingRuleViolation) as refused:
            release_withholding_component(
                session, scope=PlatformScope(), command=command, permissions=PERMS
            )
        assert _code(refused) == "billing.withholding_release_platform_unsupported"
        with pytest.raises(BillingRuleViolation) as refused:
            release_withholding_component(
                session, scope=scope, command=command, permissions=None
            )
        assert _code(refused) == "billing.withholding_release_permission_unavailable"
        with pytest.raises(BillingRuleViolation) as refused:
            release_withholding_component(
                session,
                scope=scope,
                command=command,
                permissions=_Permissions("someone-else"),
            )
        assert _code(refused) == "billing.withholding_release_permission_denied"
        with pytest.raises(BillingRuleViolation) as refused:
            release_withholding_component(
                session,
                scope=scope,
                command=replace(command, reason="  "),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_evidence_required"
        foreign = _component_id(session, other_invoice)
        with pytest.raises(BillingRuleViolation) as refused:
            release_withholding_component(
                session,
                scope=scope,
                command=_release(scope, account, invoice_id, foreign),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_component_not_found"
        assert other_account.id != account.id
        # Void invoice: not live.
        service.void_document(
            session,
            scope=scope,
            document_id=invoice_id,
            actor_ref="finance-user:fixture",
            occurred_at=billing.NOW,
            source_ref=str(uuid4()),
        )
        with pytest.raises(BillingRuleViolation):
            release_withholding_component(
                session, scope=scope, command=command, permissions=PERMS
            )
        session.rollback()


def test_nothing_outstanding_is_refused(scratch):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, evidence = frozen._invoice(session, scope, elected=True)
        frozen._fund_credit(session, scope, account, "100.00")
        _draw(session, scope, account, invoice_id, "95.00", f"p-{invoice_id}")
        accept_withholding_discharge(
            session,
            scope=scope,
            command=frozen._discharge(account, invoice_id, scope, evidence),
        )
        cid = _component_id(session, invoice_id)
        with pytest.raises(BillingRuleViolation) as refused:
            release_withholding_component(
                session,
                scope=scope,
                command=_release(scope, account, invoice_id, cid),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_nothing_outstanding"
        session.rollback()


def test_database_append_only_rls_clock_and_cap(scratch):
    admin_url, url, _ = scratch
    scope = frozen._scope(admin_url)
    other_scope = frozen._scope(admin_url)
    with billing._session(url, scope) as session:
        account, invoice_id, _ = frozen._invoice(session, scope, elected=True)
        cid = _component_id(session, invoice_id)
        released = release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, cid),
            permissions=PERMS,
        )
        session.commit()
        stored = session.execute(
            text(
                "SELECT recorded_at, clock_timestamp() AS now_db "
                "FROM mod_billing.withholding_releases WHERE id = :i"
            ),
            {"i": released.release_id},
        ).one()
        assert stored.recorded_at <= stored.now_db
        for statement in (
            "UPDATE mod_billing.withholding_releases SET reason = 'x'",
            "DELETE FROM mod_billing.withholding_releases",
        ):
            with pytest.raises(DBAPIError):
                session.execute(text(statement))
            session.rollback()
        # A direct insert past the frozen amount is refused by the trigger.
        insert = (
            "INSERT INTO mod_billing.withholding_releases "
            "(id, tenant_id, billing_account_id, document_id, component_id, "
            "command_id, actor_ref, reason, amount_delta, currency, minor_units, "
            "reverses_release_id, recorded_at) VALUES (:i, :t, :a, :d, :c, :k, "
            "'a', 'r', :amount, 'NGN', 2, :rev, '2001-01-01') RETURNING recorded_at"
        )
        base = {
            "t": scope.tenant_id,
            "a": account.id,
            "d": invoice_id,
            "c": cid,
        }
        with pytest.raises(DBAPIError, match="leaves its frozen component"):
            session.execute(
                text(insert),
                {**base, "i": uuid4(), "k": f"raw:{uuid4()}", "amount": 1, "rev": None},
            )
        session.rollback()
        # A raw undo satisfies the cap (net zero): the database, not the caller,
        # stamps recorded_at.
        stamped = session.execute(
            text(insert),
            {
                **base,
                "i": uuid4(),
                "k": f"raw:{uuid4()}",
                "amount": Decimal("-5.00"),
                "rev": released.release_id,
            },
        ).scalar_one()
        assert stamped.year >= 2026
        assert stamped >= stored.recorded_at
        session.rollback()
    with billing._session(url, other_scope) as other:
        assert _count(other, "withholding_releases") == 0


def test_migration_and_model_agree_on_the_table_shape(scratch):
    from dotmac_billing import models
    from sqlalchemy import create_engine, inspect

    admin_url, _, _ = scratch
    engine = create_engine(admin_url)
    inspector = inspect(engine)
    live = {
        c["name"] for c in inspector.get_columns("withholding_releases", "mod_billing")
    }
    assert live == set(models.WithholdingRelease.__table__.columns.keys())
    live_constraints = {
        c["name"]
        for c in inspector.get_unique_constraints("withholding_releases", "mod_billing")
    } | {
        c["name"]
        for c in inspector.get_check_constraints("withholding_releases", "mod_billing")
    }
    declared = {
        c.name
        for c in models.WithholdingRelease.__table__.constraints
        if c.name and not c.name.startswith("fk_")
    }
    assert declared - {f"{models.WithholdingRelease.__tablename__}_pkey"} <= (
        live_constraints
    )
    engine.dispose()


def _two_code_invoice(session, scope):
    """An invoice of 100 with two frozen components: A = 5.00 and B = 3.00."""
    from tests.billing_frozen_fixtures import withholding_command

    account, invoice_id, _ = frozen._invoice(session, scope, elected=False)
    withholding_command(session, account.id, invoice_id, scope=scope, claimed="5.00")
    command_b = withholding_command(
        session, account.id, invoice_id, scope=scope, claimed="3.00"
    )
    ids = dict(
        session.execute(
            text(
                "SELECT tax_amount, id "
                "FROM mod_billing.document_withholding_components "
                "WHERE document_id = :d"
            ),
            {"d": invoice_id},
        ).all()
    )
    return account, invoice_id, ids[Decimal("5.00")], ids[Decimal("3.00")], command_b


def test_two_code_invoice_releasing_one_component_leaves_the_other_blocked(scratch):
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, a, b, _ = _two_code_invoice(session, scope)
        frozen._fund_credit(session, scope, account, "100.00")
        release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, a, amount="5.00"),
            permissions=PERMS,
        )
        positions = {
            p.component_id: p
            for p in get_withholding_component_position(
                session, scope=scope, document_id=invoice_id
            )
        }
        assert positions[a].residual.amount == 0
        assert positions[b].residual.amount == Decimal("3.00")
        with pytest.raises(BillingRuleViolation) as refused:
            _draw(session, scope, account, invoice_id, "100.00", f"all-{invoice_id}")
        assert _code(refused) == "billing.credit_exceeds_non_withholding_room"
        _draw(session, scope, account, invoice_id, "97.00", f"most-{invoice_id}")
        session.rollback()


def test_undo_is_refused_when_another_component_still_holds_the_room(scratch):
    """A=5, B=3, A released, 94 paid, outstanding 6: undoing A would leave -2."""
    scope, url = _world(scratch)
    with billing._session(url, scope) as session:
        account, invoice_id, a, b, command_b = _two_code_invoice(session, scope)
        frozen._fund_credit(session, scope, account, "100.00")
        released = release_withholding_component(
            session,
            scope=scope,
            command=_release(scope, account, invoice_id, a, amount="5.00"),
            permissions=PERMS,
        )
        _draw(session, scope, account, invoice_id, "94.00", f"p-{invoice_id}")
        with pytest.raises(BillingRuleViolation) as refused:
            reverse_withholding_release(
                session,
                scope=scope,
                command=_undo(scope, released.release_id),
                permissions=PERMS,
            )
        assert _code(refused) == "billing.withholding_release_undo_room_used"
        # B's certificate can still be accepted.
        accept_withholding_discharge(session, scope=scope, command=command_b)
        session.rollback()
