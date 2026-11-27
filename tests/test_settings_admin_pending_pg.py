"""Maker-checker proposals are serialised per tenant (PostgreSQL, two sessions).

`pending_changes` is one JSON row; a read-modify-write without a lock lets the
last committer drop a concurrent proposal or resurrect an approved one. The
service takes `pg_advisory_xact_lock` on ('settings_admin.pending', tenant)
before reading it. These tests hold the lock in one transaction and prove a
second session waits, then sees the first session's committed result.
"""

from __future__ import annotations

import threading

import pytest
from dotmac_kernel.models import Party, PartyPerson, PartyType
from dotmac_kernel.settings_models import DomainSettingHistory
from dotmac_settings_admin import service
from sqlalchemy import delete, text
from sqlalchemy.orm import Session, sessionmaker


def _party(session: Session, tenant, email: str) -> Party:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=email.split("@")[0],
        email=email,
    )
    session.add(party)
    session.flush()
    session.add(PartyPerson(party_id=party.id, first_name="A", last_name="B"))
    session.flush()
    return party


@pytest.fixture
def parties(app_client, admin_session: Session, tenant_a):
    maker = _party(admin_session, tenant_a, "maker@pending-pg.example.test")
    other = _party(admin_session, tenant_a, "other@pending-pg.example.test")
    checker = _party(admin_session, tenant_a, "checker@pending-pg.example.test")
    admin_session.commit()
    service.write_setting(
        admin_session,
        tenant_a.id,
        actor=service.Actor(party_id=checker.id),
        domain="settings_admin",
        key="maker_checker",
        value=True,
        reason="enable for the race test",
        may_approve=True,
    )
    admin_session.commit()
    yield maker, other, checker
    admin_session.rollback()
    admin_session.execute(
        delete(DomainSettingHistory).where(
            DomainSettingHistory.tenant_id == tenant_a.id
        )
    )
    admin_session.commit()


def _propose(session: Session, tenant, party: Party, value: str) -> str:
    out = service.write_setting(
        session,
        tenant.id,
        actor=service.Actor(party_id=party.id),
        domain="display",
        key="timezone",
        value=value,
        reason="race",
        may_approve=False,
    )
    assert out.proposal_id
    return out.proposal_id


def _run_blocked(admin_engine, fn):
    """Run `fn(session)` on its own session/thread; return (thread, result box)."""
    box: dict[str, object] = {}

    def target() -> None:
        session = sessionmaker(bind=admin_engine, autoflush=False)()
        try:
            box["value"] = fn(session)
            session.commit()
        except BaseException as exc:  # surfaced to the main thread's assertion
            box["error"] = exc
            session.rollback()
        finally:
            session.close()

    thread = threading.Thread(target=target)
    thread.start()
    return thread, box


def test_a_concurrent_proposal_waits_for_the_lock_and_neither_is_lost(
    admin_engine, admin_session, tenant_a, parties
):
    maker, other, _checker = parties
    first = _propose(
        admin_session, tenant_a, maker, "Europe/London"
    )  # lock held, uncommitted

    thread, box = _run_blocked(
        admin_engine, lambda s: _propose(s, tenant_a, other, "Asia/Tokyo")
    )
    thread.join(timeout=1.5)
    assert thread.is_alive(), "the second proposal must wait for the first's lock"

    admin_session.commit()
    thread.join(timeout=15)
    assert not thread.is_alive() and "error" not in box, box
    second = box["value"]

    admin_session.expire_all()
    ids = {p["id"] for p in service.list_proposals(admin_session, tenant_a.id)}
    assert ids == {first, second}


def test_an_approved_proposal_is_not_resurrected_by_a_concurrent_proposal(
    admin_engine, admin_session, tenant_a, parties
):
    maker, other, checker = parties
    p1 = _propose(admin_session, tenant_a, maker, "Europe/London")
    admin_session.commit()

    # The approver holds the lock (approve not yet committed) ...
    service.approve_proposal(
        admin_session,
        tenant_a.id,
        actor=service.Actor(party_id=checker.id),
        proposal_id=p1,
        reason="ok",
    )
    # ... while another proposal arrives from a session that has not seen it.
    thread, box = _run_blocked(
        admin_engine, lambda s: _propose(s, tenant_a, other, "Asia/Tokyo")
    )
    thread.join(timeout=1.5)
    assert thread.is_alive()

    admin_session.commit()
    thread.join(timeout=15)
    assert "error" not in box, box
    p2 = box["value"]

    admin_session.expire_all()
    ids = {p["id"] for p in service.list_proposals(admin_session, tenant_a.id)}
    assert ids == {p2}, "the approved proposal must not come back"


def test_the_advisory_lock_key_is_per_tenant(admin_session, tenant_a, tenant_b):
    """Different tenants hash to different keys, so they never block each other."""
    keys = {
        admin_session.execute(
            text("SELECT hashtextextended(:k, 0)"),
            {"k": f"settings_admin.pending:{t.id}"},
        ).scalar_one()
        for t in (tenant_a, tenant_b)
    }
    assert len(keys) == 2
