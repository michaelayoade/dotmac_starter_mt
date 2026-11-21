"""PostgreSQL proofs for the GAP-4 per-account hold (``cl_0002``).

Runs only in the hosted lane (no local PostgreSQL): grants, FORCE RLS, the
append-only triggers, the per-plane version sequence and a Barrier race between a
payment-driven resolve and a hold placement with a real lock holder.
"""

# ruff: noqa: F811
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from dotmac_collections.holds import (
    HoldLifted,
    HoldNotLive,
    HoldPlaced,
    LiftCollectionHoldV1,
    PlaceCollectionHoldV1,
)
from dotmac_collections.manifest import module
from dotmac_collections.models import PLATFORM_TABLES, TENANT_TABLES
from dotmac_collections.service import (
    CaseAssessed,
    CollectionCaseService,
    CollectionHoldService,
    CollectionsConflict,
    _lock_key,
    get_collection_subject_state,
)
from dotmac_kernel.cache import PlatformScope, TenantScope, scope_segment
from dotmac_kernel.messaging.models import OutboxEvent, PlatformOutboxEvent
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_collections_isolation import (  # noqa: F401  (fixture import)
    NOW,
    _command,
    _position,
    _publish_policy,
    _reader,
    _seed_tenants,
    _tenant_session,
    scratch,
)

ACCESS_EVENT = "collections.subject.access_hold.v1"
NEW_TENANT = ("collection_holds", "collection_hold_lifts", "collection_subject_access")
DUE = NOW - timedelta(days=1)


def _held_position(scope, *, version=1, amount="100.00", state="open"):
    """An overdue exposure whose hold instant equals its due date: held at once."""

    return replace(
        _position(scope, source_version=version, amount=amount, financial_state=state),
        access_hold_from=DUE,
    )


def _place_command(scope, subject, ref, *, key=None):
    key = key or f"place:{uuid.uuid4()}"
    return PlaceCollectionHoldV1(
        command_id=uuid.uuid4(),
        idempotency_key=key,
        scope=scope,
        subject_ref=subject,
        external_ref=ref,
        reason_code="dispute_open",
        evidence_ref="evidence:1",
        actor_ref="staff:1",
        placed_at=NOW + timedelta(minutes=1),
    )


def _lift_command(scope, subject, ref):
    return LiftCollectionHoldV1(
        command_id=uuid.uuid4(),
        idempotency_key=f"lift:{uuid.uuid4()}",
        scope=scope,
        subject_ref=subject,
        external_ref=ref,
        reason_code="dispute_closed",
        evidence_ref="evidence:2",
        actor_ref="staff:1",
        lifted_at=NOW + timedelta(minutes=2),
    )


def _events(session: Session, active: bool | None = None) -> list:
    rows = [
        row
        for row in session.scalars(select(OutboxEvent)).all()
        if row.event_type == ACCESS_EVENT
    ]
    return (
        rows if active is None else [r for r in rows if r.payload["active"] is active]
    )


def test_catalog_declares_the_new_tables_with_force_rls_and_a_platform_mirror(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, _, _ = scratch
    assert set(NEW_TENANT) <= set(TENANT_TABLES)
    assert {f"platform_{name}" for name in NEW_TENANT} <= set(PLATFORM_TABLES)
    assert tuple(module.platform_tables) == tuple(
        f"platform_{t}" for t in module.tables
    )
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'mod_coll' AND c.relkind = 'r'"
                )
            )
            actual = {
                r.relname: (r.relrowsecurity, r.relforcerowsecurity) for r in rows
            }
            for name in NEW_TENANT:
                assert actual[name] == (True, True)
                assert actual[f"platform_{name}"] == (False, False)
            sequences = set(
                connection.scalars(
                    text(
                        "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'mod_coll' AND c.relkind = 'S'"
                    )
                )
            )
            assert sequences == {
                "collection_subject_access_version_seq",
                "platform_collection_subject_access_version_seq",
            }
            column = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'mod_coll' "
                    "AND column_name = 'access_hold_from' "
                    "AND table_name IN ('collection_cases', "
                    "'platform_collection_cases')"
                )
            )
            assert column == 2
    finally:
        engine.dispose()


def test_roles_reach_only_their_own_plane_and_holds_are_insert_only(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, platform_url = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        placed = CollectionHoldService.place(
            session, _place_command(scope, "billing-account:a", "dispute:1")
        )
        session.commit()
        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE mod_coll.collection_holds SET reason_code = 'x'")
            )
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM mod_coll.collection_holds"))
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(text("SELECT * FROM mod_coll.platform_collection_holds"))
        session.rollback()
        assert isinstance(placed, HoldPlaced)
    admin = create_engine(admin_url)
    try:
        with admin.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text("UPDATE mod_coll.collection_holds SET reason_code = 'x'")
            )
    finally:
        admin.dispose()
    platform = create_engine(platform_url)
    try:
        with Session(platform) as session:
            for table in NEW_TENANT:
                with pytest.raises(DBAPIError):
                    session.execute(text(f"SELECT * FROM mod_coll.{table}"))  # noqa: S608
                session.rollback()
            session.execute(text("SELECT * FROM mod_coll.platform_collection_holds"))
            with pytest.raises(DBAPIError):
                session.execute(
                    text(
                        "UPDATE mod_coll.platform_collection_holds SET reason_code='x'"
                    )
                )
            session.rollback()
            with pytest.raises(DBAPIError):
                session.execute(
                    text(
                        "SELECT nextval("
                        "'mod_coll.collection_subject_access_version_seq')"
                    )
                )
            session.rollback()
            session.execute(
                text(
                    "SELECT nextval("
                    "'mod_coll.platform_collection_subject_access_version_seq')"
                )
            )
    finally:
        platform.dispose()


def test_rls_hides_and_refuses_cross_tenant_holds(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    first, second = _seed_tenants(admin_url)
    for tenant in (first, second):
        with _tenant_session(app_url, tenant) as session:
            CollectionHoldService.place(
                session,
                _place_command(TenantScope(tenant), "billing-account:x", "dispute:rls"),
            )
            session.commit()
    with _tenant_session(app_url, first) as session:
        tenants = session.scalars(
            text("SELECT tenant_id FROM mod_coll.collection_holds")
        ).all()
        assert tenants == [first]
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "INSERT INTO mod_coll.collection_holds (id, tenant_id, "
                    "subject_ref, external_ref, placement_ordinal, reason_code, "
                    "evidence_ref, actor_ref, placed_at) VALUES (:id, :t, 's', "
                    "'k:x', 1, 'r', 'e', 'a', now())"
                ),
                {"id": uuid.uuid4(), "t": second},
            )


def test_the_hold_path_holds_once_and_place_and_lift_are_idempotent(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    subject = "subscriber:concurrency"
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_policy(session, scope)
        opened = CollectionCaseService.assess(
            session,
            command=_command(scope, 1),
            policy_version_id=policy,
            reader=_reader(_held_position(scope)),
            assessed_at=NOW,
        )
        session.commit()
        assert isinstance(opened, CaseAssessed)
        assert len(_events(session, True)) == 1

        command = _place_command(scope, subject, "dispute:pg-1", key="place-1")
        first = CollectionHoldService.place(session, command)
        replay = CollectionHoldService.place(session, command)
        other_key = CollectionHoldService.place(
            session, _place_command(scope, subject, "dispute:pg-1", key="place-2")
        )
        assert first.hold_id == replay.hold_id == other_key.hold_id
        assert replay.replayed and other_key.replayed and not first.replayed
        session.commit()
        with pytest.raises(CollectionsConflict) as conflict:
            CollectionHoldService.place(
                session, _place_command(scope, "subscriber:other", "dispute:pg-1")
            )
        assert conflict.value.code == "hold_subject_conflict"
        session.rollback()
        state = get_collection_subject_state(session, scope=scope, subject_ref=subject)
        assert state.state == "suppressed"
        assert len(_events(session, False)) == 1

        lift = _lift_command(scope, subject, "dispute:pg-1")
        assert isinstance(CollectionHoldService.lift(session, lift), HoldLifted)
        assert CollectionHoldService.lift(session, lift).replayed
        gone = CollectionHoldService.lift(
            session, _lift_command(scope, subject, "dispute:pg-1")
        )
        assert isinstance(gone, HoldNotLive)
        session.commit()
        assert len(_events(session, True)) == 2
        assert (
            get_collection_subject_state(
                session, scope=scope, subject_ref=subject
            ).state
            == "held"
        )


def test_versions_rise_across_tenants_from_one_plane_sequence(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    first, second = _seed_tenants(admin_url)
    versions: list[int] = []
    for tenant in (first, second):
        scope = TenantScope(tenant)
        with _tenant_session(app_url, tenant) as session:
            policy = _publish_policy(session, scope)
            CollectionCaseService.assess(
                session,
                command=_command(scope, 1),
                policy_version_id=policy,
                reader=_reader(_held_position(scope)),
                assessed_at=NOW,
            )
            session.commit()
            state = get_collection_subject_state(
                session, scope=scope, subject_ref="subscriber:concurrency"
            )
            assert state.version is not None
            versions.append(state.version)
    assert versions[0] < versions[1]


def test_race_between_resolve_and_place_blocks_on_the_subject_lock_and_flips_once(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    subject = "subscriber:concurrency"
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_policy(session, scope)
        CollectionCaseService.assess(
            session,
            command=_command(scope, 1),
            policy_version_id=policy,
            reader=_reader(_held_position(scope)),
            assessed_at=NOW,
        )
        session.commit()
        assert len(_events(session, True)) == 1

    key = _lock_key("collections", scope_segment(scope), "subject", subject)
    holder = create_engine(app_url)
    barrier = threading.Barrier(3)
    admin = create_engine(admin_url)

    def blocked_sessions() -> int:
        with admin.connect() as connection:
            return int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND wait_event_type = 'Lock' AND wait_event = 'advisory'"
                    )
                )
                or 0
            )

    def resolve() -> None:
        with _tenant_session(app_url, tenant_id) as session:
            barrier.wait(timeout=20)
            CollectionCaseService.assess(
                session,
                command=_command(scope, 2),
                policy_version_id=policy,
                reader=_reader(
                    _held_position(scope, version=2, amount="0.00", state="resolved")
                ),
                assessed_at=NOW + timedelta(hours=1),
            )
            session.commit()

    def place() -> None:
        with _tenant_session(app_url, tenant_id) as session:
            barrier.wait(timeout=20)
            CollectionHoldService.place(
                session, _place_command(scope, subject, "dispute:race")
            )
            session.commit()

    try:
        with holder.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": key},
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(resolve), pool.submit(place)]
                barrier.wait(timeout=20)
                deadline = time.monotonic() + 20
                while blocked_sessions() < 2 and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert (
                    blocked_sessions() >= 2
                ), "both commands must wait on the subject lock"
                transaction.commit()
                for future in futures:
                    future.result(timeout=30)
    finally:
        holder.dispose()
        admin.dispose()

    with _tenant_session(app_url, tenant_id) as session:
        # Either order ends clear with exactly one service-on event (a1 up, a1 down).
        assert len(_events(session, True)) == 1
        assert len(_events(session, False)) == 1
        state = get_collection_subject_state(session, scope=scope, subject_ref=subject)
        assert state.state == "clear"
        assert state.live_external_refs == ("dispute:race",)


def test_platform_plane_holds_and_flips_on_the_platform_role(
    scratch: tuple[str, str, str],
) -> None:
    _, _, platform_url = scratch
    scope = PlatformScope()
    engine = create_engine(platform_url)
    try:
        with Session(engine) as session:
            policy = _publish_policy(session, scope)
            opened = CollectionCaseService.assess(
                session,
                command=_command(scope, 1),
                policy_version_id=policy,
                reader=_reader(_held_position(scope)),
                assessed_at=NOW,
            )
            session.commit()
            assert isinstance(opened, CaseAssessed)
            events = [
                e
                for e in session.scalars(select(PlatformOutboxEvent)).all()
                if e.event_type == ACCESS_EVENT
            ]
            assert [e.payload["active"] for e in events] == [True]
            CollectionHoldService.place(
                session, _place_command(scope, "subscriber:concurrency", "dispute:plat")
            )
            session.commit()
            state = get_collection_subject_state(
                session, scope=scope, subject_ref="subscriber:concurrency"
            )
            assert state.state == "suppressed"
    finally:
        engine.dispose()
