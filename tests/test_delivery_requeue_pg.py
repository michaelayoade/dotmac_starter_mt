"""K-5 on real Postgres: the requeue runs as `app_user` under tenant RLS and the
`communication_dispatches` guard trigger, and another tenant cannot reach it.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.delivery_dispatch import DispatchNotFound, create_dispatch
from dotmac_kernel.delivery_requeue import (
    AUDIT_ACTION_REQUEUED,
    RequeueRefused,
    requeue_dead_dispatch,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

KW = {
    "actor_id": "operator-1",
    "source_application": "dotmac_starter_mt",
    "reason": "gate lifted",
}


@pytest.fixture(scope="module")
def engine() -> Generator[Engine, None, None]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    eng = create_engine(url, future=True)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _audit_registry():
    install_audit_actions(AuditActionRegistry.from_manifests(()))


def _as_tenant(engine: Engine, tenant_id: uuid.UUID) -> Session:
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    db.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    return db


@pytest.fixture()
def _cleanup(admin_session, tenant_a, tenant_b):
    yield
    admin_session.rollback()
    for table in ("outbox_events", "audit_events", "communication_dispatches"):
        for t in (tenant_a, tenant_b):
            admin_session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :t"),  # noqa: S608
                {"t": str(t.id)},
            )
    admin_session.commit()


def _dead_dispatch(engine, admin_session, tenant) -> uuid.UUID:
    db = _as_tenant(engine, tenant.id)
    try:
        res = create_dispatch(
            db,
            tenant.id,
            idempotency_key=f"k-{uuid.uuid4().hex[:8]}",
            on_conflict="raise_if_different",
            channel="email",
            category="transactional",
            address="jane@example.com",
            template_provenance="template:welcome@r1",
            subject="Hi",
            text="body",
        )
        db.commit()
    finally:
        db.close()
    admin_session.execute(
        text(
            "UPDATE communication_dispatches SET status = 'dead', terminal_at = now() "
            "WHERE dispatch_id = :d"
        ),
        {"d": str(res.dispatch_id)},
    )
    admin_session.commit()
    return res.dispatch_id


def test_requeue_as_app_user_is_audited_idempotent_and_tenant_scoped(
    engine, admin_session, tenant_a, tenant_b, _cleanup
) -> None:
    did = _dead_dispatch(engine, admin_session, tenant_a)

    # Another tenant's context cannot see it.
    other = _as_tenant(engine, tenant_b.id)
    try:
        with pytest.raises(DispatchNotFound):
            requeue_dead_dispatch(other, tenant_b.id, dispatch_id=did, **KW)
    finally:
        other.rollback()
        other.close()

    results = []
    for _ in range(2):
        db = _as_tenant(engine, tenant_a.id)
        try:
            results.append(
                requeue_dead_dispatch(db, tenant_a.id, dispatch_id=did, **KW)
            )
            db.commit()
        finally:
            db.close()
    assert [r.requeued for r in results] == [True, False]

    assert (
        admin_session.execute(
            text("SELECT status FROM communication_dispatches WHERE dispatch_id = :d"),
            {"d": str(did)},
        ).scalar()
        == "pending"
    )
    audits = admin_session.execute(
        text(
            "SELECT actor_id, details FROM audit_events "
            "WHERE tenant_id = :t AND action = :a"
        ),
        {"t": str(tenant_a.id), "a": AUDIT_ACTION_REQUEUED},
    ).all()
    assert len(audits) == 1 and audits[0].actor_id == "operator-1"
    assert audits[0].details["reason"] == "gate lifted"
    events = admin_session.execute(
        text("SELECT payload FROM outbox_events WHERE tenant_id = :t"),
        {"t": str(tenant_a.id)},
    ).all()
    assert [e.payload for e in events].count(
        {"dispatch_id": str(did)}
    ) == 2  # create + requeue


def test_requeue_does_not_bypass_suppression(
    engine, admin_session, tenant_a, _cleanup
) -> None:
    did = _dead_dispatch(engine, admin_session, tenant_a)
    admin_session.execute(
        text(
            "INSERT INTO communication_suppressions (id, tenant_id, channel, "
            "address, scope, reason) VALUES (:i, :t, 'email', 'jane@example.com', "
            "'all', 'bounce')"
        ),
        {"i": str(uuid.uuid4()), "t": str(tenant_a.id)},
    )
    admin_session.commit()
    db = _as_tenant(engine, tenant_a.id)
    try:
        with pytest.raises(RequeueRefused, match="suppressed"):
            requeue_dead_dispatch(db, tenant_a.id, dispatch_id=did, **KW)
    finally:
        db.rollback()
        db.close()
    admin_session.execute(
        text("DELETE FROM communication_suppressions WHERE tenant_id = :t"),
        {"t": str(tenant_a.id)},
    )
    admin_session.commit()


def _cli(monkeypatch, capsys, did, tenant, *extra) -> tuple[int, str]:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "requeue_cli",
        Path(__file__).resolve().parent.parent / "scripts" / "requeue_dead_dispatch.py",
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    code = module.main(
        [
            "--tenant-id", str(tenant.id),
            "--dispatch-id", str(did),
            "--actor-id", "operator-1",
            "--source-application", "dotmac_starter_mt",
            "--reason", "gate lifted",
            *extra,
        ]
    )  # fmt: skip
    out = capsys.readouterr()
    return code, out.out + out.err


def test_cli_is_a_dry_run_unless_apply(
    engine, admin_session, tenant_a, _cleanup, monkeypatch, capsys
) -> None:
    did = _dead_dispatch(engine, admin_session, tenant_a)

    def status() -> str:
        admin_session.rollback()
        return admin_session.execute(
            text("SELECT status FROM communication_dispatches WHERE dispatch_id = :d"),
            {"d": str(did)},
        ).scalar()

    code, out = _cli(monkeypatch, capsys, did, tenant_a)
    assert code == 0 and "dry run" in out and "would be requeued" in out
    assert status() == "dead"
    assert "jane@example.com" not in out

    code, out = _cli(monkeypatch, capsys, did, tenant_a, "--apply")
    assert code == 0 and "requeued" in out
    assert status() == "pending"

    code, out = _cli(monkeypatch, capsys, did, tenant_a, "--apply")
    assert code == 0 and "already pending" in out
