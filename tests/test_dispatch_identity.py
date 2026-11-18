"""Dispatch identity (ADR-0006 § 5g, K-3): RLS, grants, dedupe, payload, load.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

# ruff: noqa: S608  (SQL interpolates only the module-constant table name)

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.delivery_dispatch import (
    DISPATCH_EVENT_TYPE,
    DispatchHistoryOnly,
    DispatchIntegrityError,
    DispatchNotFound,
    DispatchRedacted,
    create_dispatch,
    find_dispatch_by_key,
    import_history_key,
    import_pending,
    load_dispatch,
)
from dotmac_kernel.delivery_providers import AttachmentRef
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

TABLE = "communication_dispatches"


@pytest.fixture(scope="module")
def engine() -> Generator[Engine, None, None]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    eng = create_engine(url, future=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _as_tenant(factory: sessionmaker[Session], tenant_id: uuid.UUID) -> Session:
    session = factory()
    session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    return session


def _retenant(db: Session, tenant_id: uuid.UUID) -> None:
    """The tenant context is transaction-local; re-establish it after a commit."""
    db.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )


def _create(db: Session, tenant_id: uuid.UUID, key: str = "k-1", **kw):
    args = {
        "idempotency_key": key,
        "channel": "email",
        "category": "transactional",
        "address": "jane@example.com",
        "template_provenance": "template:password_reset@rev-1",
        "subject": "Hello",
        "text": "Plain body with token",
        "html": "<p>Html body</p>",
    }
    args.update(kw)
    return create_dispatch(db, tenant_id, **args)


def _outbox(admin_session: Session, tenant_id: uuid.UUID) -> list:
    return admin_session.execute(
        text(
            "SELECT payload, event_type, correlation_id, available_at "
            "FROM outbox_events WHERE tenant_id = :t"
        ),
        {"t": str(tenant_id)},
    ).fetchall()


# --- create / dedupe ---------------------------------------------------------


def test_create_inserts_identity_and_enqueues_id_only_payload(
    tenant_a, admin_session: Session, factory
) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        res = _create(db, tenant_a.id, key="reset:person-1:2026-W47")
        db.commit()
        assert res.created and res.same_content
        did = res.dispatch_id
    finally:
        db.close()

    events = _outbox(admin_session, tenant_a.id)
    assert len(events) == 1
    payload, event_type, correlation_id, _ = events[0]
    assert event_type == DISPATCH_EVENT_TYPE
    # ONLY the id: no token, address, subject, body or key reaches the dispatcher.
    assert payload == {"dispatch_id": str(did)}
    assert correlation_id is None


def test_duplicate_key_returns_existing_and_does_not_enqueue_again(
    tenant_a, admin_session: Session, factory
) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        first = _create(db, tenant_a.id)
        again = _create(db, tenant_a.id)
        different = _create(db, tenant_a.id, text="Other body")
        db.commit()
    finally:
        db.close()
    assert not again.created and again.same_content
    assert again.dispatch_id == first.dispatch_id
    assert not different.created and not different.same_content
    assert len(_outbox(admin_session, tenant_a.id)) == 1


def test_same_key_in_two_tenants_is_two_dispatches(tenant_a, tenant_b, factory) -> None:
    ids = []
    for tenant in (tenant_a, tenant_b):
        db = _as_tenant(factory, tenant.id)
        try:
            res = _create(db, tenant.id, key="shared-key")
            db.commit()
            assert res.created
            ids.append(res.dispatch_id)
        finally:
            db.close()
    assert ids[0] != ids[1]


def test_find_by_key(tenant_a, factory) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        assert find_dispatch_by_key(db, tenant_a.id, "nope") is None
        res = _create(db, tenant_a.id, key="find-me")
        db.commit()
        _retenant(db, tenant_a.id)
        found = find_dispatch_by_key(db, tenant_a.id, "find-me")
        assert found is not None and found.dispatch_id == res.dispatch_id
    finally:
        db.close()


def test_concurrent_creators_of_one_key_yield_one_row_and_one_event(
    tenant_a, admin_session: Session, factory
) -> None:
    first = _as_tenant(factory, tenant_a.id)
    second_done = threading.Event()
    results: dict[str, object] = {}

    def second_creator() -> None:
        db = _as_tenant(factory, tenant_a.id)
        try:
            results["second"] = _create(db, tenant_a.id, key="race")
            db.commit()
        finally:
            db.close()
            second_done.set()

    try:
        results["first"] = _create(first, tenant_a.id, key="race")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(second_creator)
            # The second creator must BLOCK on the uncommitted unique entry.
            time.sleep(0.7)
            assert not second_done.is_set()
            first.commit()
            future.result(timeout=15)
    finally:
        first.close()

    rows = admin_session.execute(
        text(f"SELECT count(*) FROM {TABLE} WHERE tenant_id = :t"),
        {"t": str(tenant_a.id)},
    ).scalar()
    assert rows == 1
    assert len(_outbox(admin_session, tenant_a.id)) == 1
    assert results["first"].created and not results["second"].created  # type: ignore[attr-defined]
    assert (
        results["first"].dispatch_id  # type: ignore[attr-defined]
        == results["second"].dispatch_id  # type: ignore[attr-defined]
    )


def test_a_rolled_back_creator_lets_the_waiting_one_win(
    tenant_a, admin_session: Session, factory
) -> None:
    first = _as_tenant(factory, tenant_a.id)
    results: dict[str, object] = {}

    def second_creator() -> None:
        db = _as_tenant(factory, tenant_a.id)
        try:
            results["second"] = _create(db, tenant_a.id, key="race-rb")
            db.commit()
        finally:
            db.close()

    try:
        _create(first, tenant_a.id, key="race-rb")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(second_creator)
            time.sleep(0.5)
            first.rollback()
            future.result(timeout=15)
    finally:
        first.close()
    assert results["second"].created  # type: ignore[attr-defined]
    assert len(_outbox(admin_session, tenant_a.id)) == 1


def test_invalid_content_is_refused_before_anything_is_written(
    tenant_a, admin_session: Session, factory
) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        with pytest.raises(ValueError):
            _create(db, tenant_a.id, key="bad", headers={"X-Evil": "1"})
        with pytest.raises(ValueError):
            _create(db, tenant_a.id, key="bad", text=None, html=None)
        with pytest.raises(ValueError):
            _create(db, tenant_a.id, key=" padded ")
        db.rollback()
    finally:
        db.close()
    assert _outbox(admin_session, tenant_a.id) == []


# --- load / fingerprint ------------------------------------------------------


def test_load_roundtrips_s6_parts_and_verifies_fingerprint(tenant_a, factory) -> None:
    att = AttachmentRef("document", "doc-1@v3", "invoice.pdf", "application/pdf")
    db = _as_tenant(factory, tenant_a.id)
    try:
        res = _create(
            db,
            tenant_a.id,
            headers={"Reply-To": "support@example.com"},
            attachments=[att],
        )
        db.commit()
        _retenant(db, tenant_a.id)
        loaded = load_dispatch(db, tenant_a.id, res.dispatch_id)
    finally:
        db.close()
    m = loaded.message
    assert m.dispatch_id == res.dispatch_id
    assert (m.subject, m.text, m.html) == (
        "Hello",
        "Plain body with token",
        "<p>Html body</p>",
    )
    assert dict(m.headers) == {"Reply-To": "support@example.com"}
    assert tuple(m.attachments) == (att,)
    assert m.request_fingerprint == loaded.dispatch.content_fingerprint


def test_load_refuses_tampered_content_and_unknown_id(
    tenant_a, admin_session: Session, factory
) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        res = _create(db, tenant_a.id)
        db.commit()
        did = res.dispatch_id
    finally:
        db.close()
    # app_admin tampers with the stored body (app_user may only redact).
    admin_session.execute(
        text(f"UPDATE {TABLE} SET text_body = 'changed' WHERE dispatch_id = :d"),
        {"d": str(did)},
    )
    admin_session.commit()
    db = _as_tenant(factory, tenant_a.id)
    try:
        with pytest.raises(DispatchIntegrityError):
            load_dispatch(db, tenant_a.id, did)
        with pytest.raises(DispatchNotFound):
            load_dispatch(db, tenant_a.id, uuid.uuid4())
    finally:
        db.close()


def test_load_refuses_history_and_redacted(tenant_a, factory) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        hist = import_history_key(
            db,
            tenant_a.id,
            idempotency_key="legacy-1",
            channel="email",
            category="transactional",
            template_provenance="legacy:email_outbox",
        )
        sent = _create(db, tenant_a.id, key="to-redact")
        db.commit()
        _retenant(db, tenant_a.id)
        with pytest.raises(DispatchHistoryOnly):
            load_dispatch(db, tenant_a.id, hist.dispatch_id)
        # The redaction job's shape: terminal status + NULL bodies, fingerprint
        # and provenance kept. app_user holds column UPDATE for exactly this.
        db.execute(
            text(
                f"UPDATE {TABLE} SET status = 'sent', terminal_at = now(), "
                "redacted_at = now(), subject = NULL, text_body = NULL, "
                "html_body = NULL, headers = NULL, attachments = NULL "
                "WHERE dispatch_id = :d"
            ),
            {"d": str(sent.dispatch_id)},
        )
        db.commit()
        _retenant(db, tenant_a.id)
        with pytest.raises(DispatchRedacted):
            load_dispatch(db, tenant_a.id, sent.dispatch_id)
        row = find_dispatch_by_key(db, tenant_a.id, "to-redact")
        assert row.content_fingerprint and row.template_provenance
    finally:
        db.close()


# --- D7 carry-forward --------------------------------------------------------


def test_history_key_is_never_enqueued_and_blocks_a_later_create(
    tenant_a, admin_session: Session, factory
) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        hist = import_history_key(
            db,
            tenant_a.id,
            idempotency_key="weekly:2026-W46:p1",
            channel="email",
            category="transactional",
            template_provenance="legacy:email_outbox",
        )
        row = find_dispatch_by_key(db, tenant_a.id, "weekly:2026-W46:p1")
        assert row.address is None and row.content_fingerprint is None
        again = import_history_key(
            db,
            tenant_a.id,
            idempotency_key="weekly:2026-W46:p1",
            channel="email",
            category="transactional",
            template_provenance="legacy:email_outbox",
        )
        later = _create(db, tenant_a.id, key="weekly:2026-W46:p1")
        db.commit()
    finally:
        db.close()
    assert hist.created and hist.status == "history"
    assert not again.created
    assert not later.created and not later.same_content
    assert later.dispatch_id == hist.dispatch_id
    assert _outbox(admin_session, tenant_a.id) == []


def test_history_import_requires_legacy_provenance(tenant_a, factory) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        with pytest.raises(ValueError):
            import_history_key(
                db,
                tenant_a.id,
                idempotency_key="x",
                channel="email",
                category="transactional",
                template_provenance="template:foo",
            )
    finally:
        db.close()


def test_pending_carried_forward_byte_for_byte_with_available_at(
    tenant_a, admin_session: Session, factory
) -> None:
    due = datetime.now(UTC) + timedelta(hours=3)
    subject = "Réinitialisation  — double  space"
    body = "line1\r\nline2 \t trailing \n"
    html = "<p>café  </p>\n"
    db = _as_tenant(factory, tenant_a.id)
    try:
        res = import_pending(
            db,
            tenant_a.id,
            idempotency_key="legacy-pending-1",
            channel="email",
            category="transactional",
            address="jane@example.com",
            template_provenance="legacy:email_outbox",
            available_at=due,
            subject=subject,
            text=body,
            html=html,
        )
        db.commit()
        _retenant(db, tenant_a.id)
        row = find_dispatch_by_key(db, tenant_a.id, "legacy-pending-1")
        assert (row.subject, row.text_body, row.html_body) == (subject, body, html)
        assert row.template_provenance == "legacy:email_outbox"
        assert row.available_at == due and row.status == "pending"
        assert load_dispatch(db, tenant_a.id, row.dispatch_id).message.text == body
    finally:
        db.close()
    (event,) = _outbox(admin_session, tenant_a.id)
    assert event.payload == {"dispatch_id": str(res.dispatch_id)}
    assert event.available_at == due


def test_pending_import_requires_legacy_provenance(tenant_a, factory) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        with pytest.raises(ValueError):
            import_pending(
                db,
                tenant_a.id,
                idempotency_key="x",
                channel="email",
                category="transactional",
                address="jane@example.com",
                template_provenance="template:foo",
                available_at=datetime.now(UTC),
                text="hi",
            )
    finally:
        db.close()


# --- RLS isolation -----------------------------------------------------------


def test_tenant_a_cannot_see_tenant_b_dispatches(tenant_a, tenant_b, factory) -> None:
    db = _as_tenant(factory, tenant_b.id)
    try:
        res = _create(db, tenant_b.id, key="b-only")
        db.commit()
        b_id = res.dispatch_id
    finally:
        db.close()

    a = _as_tenant(factory, tenant_a.id)
    try:
        assert a.execute(text(f"SELECT 1 FROM {TABLE}")).fetchall() == []
        assert find_dispatch_by_key(a, tenant_a.id, "b-only") is None
        assert find_dispatch_by_key(a, tenant_b.id, "b-only") is None
        with pytest.raises(DispatchNotFound):
            load_dispatch(a, tenant_b.id, b_id)
    finally:
        a.close()


def test_tenant_a_cannot_write_a_dispatch_for_tenant_b(
    tenant_a, tenant_b, factory
) -> None:
    a = _as_tenant(factory, tenant_a.id)
    try:
        with pytest.raises((DBAPIError, IntegrityError)):
            _create(a, tenant_b.id, key="forged")
    finally:
        a.rollback()
        a.close()


def test_no_tenant_context_sees_and_writes_nothing(tenant_a, factory) -> None:
    db = _as_tenant(factory, tenant_a.id)
    try:
        _create(db, tenant_a.id)
        db.commit()
    finally:
        db.close()
    bare = factory()
    try:
        assert bare.execute(text(f"SELECT 1 FROM {TABLE}")).fetchall() == []
    finally:
        bare.close()


# --- grants ------------------------------------------------------------------


def test_force_rls_is_on(admin_session: Session) -> None:
    row = admin_session.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'public.communication_dispatches'::regclass"
        )
    ).one()
    assert tuple(row) == (True, True)


def test_app_user_has_minimal_grants_and_no_delete(
    tenant_a, factory, admin_session: Session
) -> None:
    privs = {
        p: admin_session.execute(
            text("SELECT has_table_privilege('app_user', :t, :p)"),
            {"t": f"public.{TABLE}", "p": p},
        ).scalar()
        for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES")
    }
    # Table-level UPDATE is NOT held; only listed columns are updatable.
    assert privs == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": False,
        "DELETE": False,
        "TRUNCATE": False,
        "REFERENCES": False,
    }
    for col in (
        "idempotency_key",
        "address",
        "content_fingerprint",
        "tenant_id",
        "template_provenance",
        "channel",
        "category",
        "dispatch_id",
    ):
        assert not admin_session.execute(
            text("SELECT has_column_privilege('app_user', :t, :c, 'UPDATE')"),
            {"t": f"public.{TABLE}", "c": col},
        ).scalar(), col

    db = _as_tenant(factory, tenant_a.id)
    try:
        res = _create(db, tenant_a.id)
        db.commit()
        did = str(res.dispatch_id)
        for sql in (
            f"DELETE FROM {TABLE} WHERE dispatch_id = :d",
            f"UPDATE {TABLE} SET content_fingerprint = repeat('0', 64) "
            "WHERE dispatch_id = :d",
            f"UPDATE {TABLE} SET address = 'x@example.com' WHERE dispatch_id = :d",
        ):
            with pytest.raises(DBAPIError):
                db.execute(text(sql), {"d": did})
            db.rollback()
    finally:
        db.close()


def _role_engine(role: str) -> Engine:
    base = os.getenv("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL not set")
    scheme_userhost, _, db = base.rpartition("/")
    scheme, _, userhost = scheme_userhost.partition("://")
    host = userhost.rpartition("@")[2]
    return create_engine(f"{scheme}://{role}@{host}/{db}", future=True)


def test_outbox_dispatcher_has_no_privilege_on_the_table(
    admin_session: Session, tenant_a, factory
) -> None:
    for priv in (
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ):
        assert not admin_session.execute(
            text("SELECT has_table_privilege('outbox_dispatcher', :t, :p)"),
            {"t": f"public.{TABLE}", "p": priv},
        ).scalar(), priv
    cols = (
        admin_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t AND table_schema = 'public'"
            ),
            {"t": TABLE},
        )
        .scalars()
        .all()
    )
    assert cols
    for col in cols:
        for priv in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            assert not admin_session.execute(
                text("SELECT has_column_privilege('outbox_dispatcher', :t, :c, :p)"),
                {"t": f"public.{TABLE}", "c": col, "p": priv},
            ).scalar(), (col, priv)

    db = _as_tenant(factory, tenant_a.id)
    try:
        _create(db, tenant_a.id)
        db.commit()
    finally:
        db.close()
    eng = _role_engine("outbox_dispatcher")
    try:
        with eng.connect() as conn:
            with pytest.raises(DBAPIError):
                conn.execute(text(f"SELECT * FROM {TABLE}"))
            conn.rollback()
            # What the dispatcher DOES see of the event: the id only.
            claimed = conn.execute(
                text(
                    "SELECT payload, correlation_id "
                    "FROM claim_outbox_batch('w', 5, 300)"
                )
            ).fetchall()
            conn.rollback()
        assert claimed
        for payload, correlation_id in claimed:
            assert set(payload) == {"dispatch_id"} and correlation_id is None
    finally:
        eng.dispose()


def test_no_lab_worker_role_holds_a_privilege(admin_session: Session) -> None:
    """The Academy "lab worker" concept is not granted here: no role other than
    the table owner, app_user and the superuser/admin roles has any privilege."""
    grantees = {
        r[0]
        for r in admin_session.execute(
            text(
                "SELECT DISTINCT grantee FROM information_schema.role_table_grants "
                "WHERE table_schema = 'public' AND table_name = :t "
                "UNION SELECT DISTINCT grantee FROM "
                "information_schema.role_column_grants "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": TABLE},
        )
    }
    owner = admin_session.execute(
        text("SELECT tableowner FROM pg_tables WHERE tablename = :t"), {"t": TABLE}
    ).scalar()
    assert grantees - {owner, "app_user", "postgres"} == set()
