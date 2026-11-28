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
    RepublishSubjectStateV1,
)
from dotmac_collections.manifest import module
from dotmac_collections.models import (
    PLATFORM_TABLES,
    TENANT_TABLES,
    CollectionNoticeRequest,
)
from dotmac_collections.service import (
    CaseAssessed,
    CollectionCaseService,
    CollectionHoldService,
    CollectionsConflict,
    ProcessCollectionAccessHoldDueV1,
    ProcessCollectionStepDueV1,
    StepDueBlocked,
    _lock_key,
    get_collection_case,
    get_collection_subject_state,
    republish_subject_state,
)
from dotmac_collections.timers import (
    FakeCollectionsTimer,
    TimerIdentityV1,
    TimerTriggerV1,
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
    _superuser_url,
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


def _trigger(timer, session, scope, case_id, purpose):
    identity = TimerIdentityV1(
        scope, "collections.case", "collection_case", str(case_id), purpose
    )
    handle = timer.current(session, identity)
    assert handle is not None, purpose
    return TimerTriggerV1(
        handle.timer_id,
        handle.identity,
        handle.generation,
        handle.due_at,
        handle.output_event_type,
        handle.expected_source_version,
    )


def _engage(session, scope, timer, case_id, at, position=None):
    """The access-hold wake (the balance pass would have run first)."""

    return CollectionCaseService.process_access_hold_due(
        session,
        command=ProcessCollectionAccessHoldDueV1(
            scope=scope,
            trigger=_trigger(timer, session, scope, case_id, "access_hold"),
            processed_at=at,
        ),
        reader=_reader(position or _held_position(scope)),
        timer=timer,
    )


def _open_held(session, scope, policy, timer=None, *, at=NOW):
    """Open the overdue case and engage its hold: the subject is held."""

    timer = timer or FakeCollectionsTimer()
    opened = CollectionCaseService.assess(
        session,
        command=_command(scope, 1),
        policy_version_id=policy,
        reader=_reader(_held_position(scope)),
        timer=timer,
        assessed_at=at,
    )
    assert isinstance(opened, CaseAssessed)
    engaged = _engage(session, scope, timer, opened.case_id, at)
    assert engaged.subject_state == "held"
    return opened, timer


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
        opened, timer = _open_held(session, scope, policy)
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
        assert isinstance(CollectionHoldService.lift(session, lift, timer), HoldLifted)
        assert CollectionHoldService.lift(session, lift, timer).replayed
        gone = CollectionHoldService.lift(
            session, _lift_command(scope, subject, "dispute:pg-1"), timer
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
            _open_held(session, scope, policy)
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
        _open_held(session, scope, policy)
        session.commit()
        assert len(_events(session, True)) == 1

    key = _lock_key("collections", scope_segment(scope), "subject", subject)
    holder = create_engine(app_url)
    barrier = threading.Barrier(3)
    # Wait events of other roles are visible only to a superuser.
    observer = create_engine(_superuser_url())
    database = create_engine(admin_url).url.database

    def blocked_sessions() -> int:
        with observer.connect() as connection:
            return int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = :d "
                        "AND wait_event_type = 'Lock' AND wait_event = 'advisory'"
                    ),
                    {"d": database},
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
                blocked = 0
                try:
                    barrier.wait(timeout=20)
                    deadline = time.monotonic() + 20
                    while blocked < 2 and time.monotonic() < deadline:
                        time.sleep(0.2)
                        blocked = blocked_sessions()
                finally:
                    # Always release the lock, or a failed proof would hang the pool.
                    transaction.commit()
                for future in futures:
                    future.result(timeout=30)
            assert blocked >= 2, "both commands must wait on the subject lock"
    finally:
        holder.dispose()
        observer.dispose()

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
            opened, _ = _open_held(session, scope, policy)
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


def _assess_open(session, scope, policy, timer, at):
    return CollectionCaseService.assess(
        session,
        command=_command(scope, 1),
        policy_version_id=policy,
        reader=_reader(_held_position(scope)),
        timer=timer,
        assessed_at=at,
    )


def test_a_late_opened_case_is_held_only_when_its_wake_engages(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    late = NOW + timedelta(days=30)  # long past the hold instant
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_policy(session, scope)
        timer = FakeCollectionsTimer()
        opened = _assess_open(session, scope, policy, timer, late)
        session.commit()
        assert isinstance(opened, CaseAssessed)
        assert _events(session) == []  # past the instant, but not engaged
        view = get_collection_case(session, scope=scope, case_id=opened.case_id)
        assert view.access_hold_engaged_at is None
        assert view.access_hold_from == DUE
        wake = _trigger(timer, session, scope, opened.case_id, "access_hold")
        assert wake.due_at == late  # the later of the instant and now

        # A paid invoice means nothing engages (the balance pass ran before the wake).
        paid = _held_position(scope, version=2, amount="0.00", state="resolved")
        done = _engage(session, scope, timer, opened.case_id, late, paid)
        session.commit()
        assert (done.lifecycle, done.subject_state) == ("resolved", "clear")
        assert _events(session) == []


def test_the_wake_engages_an_open_invoice_once(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_policy(session, scope)
        timer = FakeCollectionsTimer()
        opened = _assess_open(session, scope, policy, timer, NOW + timedelta(days=30))
        assert isinstance(opened, CaseAssessed)
        _engage(session, scope, timer, opened.case_id, NOW + timedelta(days=30))
        session.commit()
        assert len(_events(session, True)) == 1
        view = get_collection_case(session, scope=scope, case_id=opened.case_id)
        assert view.access_hold_engaged_at == NOW + timedelta(days=30)
        assert view.due_at == DUE


def test_step_due_under_a_live_hold_writes_no_notice(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope)
        timer = FakeCollectionsTimer()
        opened = _assess_open(session, scope, policy, timer, NOW)
        assert isinstance(opened, CaseAssessed)
        CollectionHoldService.place(
            session, _place_command(scope, "subscriber:concurrency", "dispute:step")
        )
        result = CollectionCaseService.process_step_due(
            session,
            command=ProcessCollectionStepDueV1(
                scope=scope,
                trigger=_trigger(timer, session, scope, opened.case_id, "next_step"),
                processed_at=NOW + timedelta(days=7),
            ),
            reader=_reader(_held_position(scope)),
            timer=timer,
        )
        session.commit()
        assert isinstance(result, StepDueBlocked)
        assert result.reason_code == "collection_hold_active"
        assert session.scalars(select(CollectionNoticeRequest)).all() == []


def test_the_last_lift_re_arms_the_ladder_of_each_active_case(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    subject = "subscriber:concurrency"
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope)
        timer = FakeCollectionsTimer()
        CollectionHoldService.place(
            session, _place_command(scope, subject, "dispute:a")
        )
        opened = _assess_open(session, scope, policy, timer, NOW)
        assert isinstance(opened, CaseAssessed)
        step = TimerIdentityV1(
            scope,
            "collections.case",
            "collection_case",
            str(opened.case_id),
            "next_step",
        )
        assert timer.current(session, step) is None  # no ladder under a hold
        lifted = CollectionHoldService.lift(
            session, _lift_command(scope, subject, "dispute:a"), timer
        )
        session.commit()
        assert isinstance(lifted, HoldLifted)
        assert timer.current(session, step) is not None


def test_two_simultaneous_last_lifts_still_re_arm_the_ladder(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    subject = "subscriber:concurrency"
    timer = FakeCollectionsTimer()
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope)
        for ref in ("dispute:one", "dispute:two"):
            CollectionHoldService.place(session, _place_command(scope, subject, ref))
        opened = _assess_open(session, scope, policy, timer, NOW)
        session.commit()
        assert isinstance(opened, CaseAssessed)
        case_id = opened.case_id
    step = TimerIdentityV1(
        scope, "collections.case", "collection_case", str(case_id), "next_step"
    )

    key = _lock_key("collections", scope_segment(scope), "subject", subject)
    holder = create_engine(app_url)
    observer = create_engine(_superuser_url())
    database = create_engine(admin_url).url.database
    barrier = threading.Barrier(3)

    def waiting() -> int:
        with observer.connect() as connection:
            return int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = :d "
                        "AND wait_event_type = 'Lock' AND wait_event = 'advisory'"
                    ),
                    {"d": database},
                )
                or 0
            )

    def lift(ref: str):
        with _tenant_session(app_url, tenant_id) as session:
            barrier.wait(timeout=20)
            result = CollectionHoldService.lift(
                session, _lift_command(scope, subject, ref), timer
            )
            session.commit()
            return result

    blocked = 0
    try:
        with holder.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": key},
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(lift, "dispute:one"),
                    pool.submit(lift, "dispute:two"),
                ]
                try:
                    barrier.wait(timeout=20)
                    deadline = time.monotonic() + 20
                    while blocked < 2 and time.monotonic() < deadline:
                        time.sleep(0.2)
                        blocked = waiting()
                finally:
                    transaction.commit()
                results = [future.result(timeout=30) for future in futures]
    finally:
        holder.dispose()
        observer.dispose()
    assert blocked >= 2, "both lifts must wait on the shared locks"
    assert all(isinstance(r, HoldLifted) for r in results)
    with _tenant_session(app_url, tenant_id) as session:
        # The second lift saw the first one committed, so exactly one re-armed it.
        assert timer.current(session, step) is not None
        state = get_collection_subject_state(session, scope=scope, subject_ref=subject)
        assert state.live_external_refs == ()


def test_the_placement_ordinal_is_unique_per_reference_and_counts_re_placements(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    subject = "billing-account:ordinal"
    timer = FakeCollectionsTimer()
    with _tenant_session(app_url, tenant_id) as session:
        CollectionHoldService.place(session, _place_command(scope, subject, "kind:ord"))
        CollectionHoldService.lift(
            session, _lift_command(scope, subject, "kind:ord"), timer
        )
        CollectionHoldService.place(session, _place_command(scope, subject, "kind:ord"))
        session.commit()
    admin = create_engine(admin_url)
    try:
        with admin.connect() as connection:
            ordinals = connection.scalars(
                text(
                    "SELECT placement_ordinal FROM mod_coll.collection_holds "
                    "WHERE tenant_id = :t AND external_ref = 'kind:ord' "
                    "ORDER BY placement_ordinal"
                ),
                {"t": tenant_id},
            ).all()
            assert ordinals == [1, 2]
        with admin.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO mod_coll.collection_holds (id, tenant_id, "
                    "subject_ref, external_ref, placement_ordinal, reason_code, "
                    "evidence_ref, actor_ref, placed_at) VALUES (:id, :t, 's', "
                    "'kind:ord', 2, 'r', 'e', 'a', now())"
                ),
                {"id": uuid.uuid4(), "t": tenant_id},
            )
    finally:
        admin.dispose()


def test_republish_allocates_a_fresh_version_from_the_plane_sequence(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    subject = "subscriber:concurrency"
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_policy(session, scope)
        _open_held(session, scope, policy)
        before = get_collection_subject_state(session, scope=scope, subject_ref=subject)
        command = RepublishSubjectStateV1(
            command_id=uuid.uuid4(),
            idempotency_key="republish-1",
            scope=scope,
            subject_ref=subject,
            requested_at=NOW + timedelta(days=1),
        )
        first = republish_subject_state(session, command)
        again = republish_subject_state(session, command)
        session.commit()
        assert first.state == "held" and before.version is not None
        assert first.version > before.version
        assert again.replayed and again.version == first.version
        events = _events(session, True)
        assert sorted(e.payload["source_version"] for e in events) == [
            before.version,
            first.version,
        ]


def test_last_lift_racing_a_case_opening_arms_the_ladder_or_refuses(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    subject = "subscriber:concurrency"
    timer = FakeCollectionsTimer()
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope)
        CollectionHoldService.place(
            session, _place_command(scope, subject, "dispute:r")
        )
        session.commit()

    key = _lock_key("collections", scope_segment(scope), "subject", subject)
    holder = create_engine(app_url)
    observer = create_engine(_superuser_url())
    database = create_engine(admin_url).url.database
    barrier = threading.Barrier(3)

    def waiting() -> int:
        with observer.connect() as connection:
            return int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = :d "
                        "AND wait_event_type = 'Lock' AND wait_event = 'advisory'"
                    ),
                    {"d": database},
                )
                or 0
            )

    def lift():
        with _tenant_session(app_url, tenant_id) as session:
            barrier.wait(timeout=20)
            try:
                result = CollectionHoldService.lift(
                    session, _lift_command(scope, subject, "dispute:r"), timer
                )
                session.commit()
                return result
            except CollectionsConflict as refusal:
                session.rollback()
                return refusal

    def open_case():
        with _tenant_session(app_url, tenant_id) as session:
            barrier.wait(timeout=20)
            result = _assess_open(session, scope, policy, timer, NOW)
            session.commit()
            return result

    blocked = 0
    try:
        with holder.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": key},
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(lift), pool.submit(open_case)]
                try:
                    barrier.wait(timeout=20)
                    deadline = time.monotonic() + 20
                    while blocked < 2 and time.monotonic() < deadline:
                        time.sleep(0.2)
                        blocked = waiting()
                finally:
                    transaction.commit()
                lifted, opened = (f.result(timeout=30) for f in futures)
    finally:
        holder.dispose()
        observer.dispose()
    assert blocked >= 2, "both commands must wait on the subject lock"
    assert isinstance(opened, CaseAssessed)
    step = TimerIdentityV1(
        scope, "collections.case", "collection_case", str(opened.case_id), "next_step"
    )
    with _tenant_session(app_url, tenant_id) as session:
        if isinstance(lifted, CollectionsConflict):
            # The case came first and saw the hold: lift refused, and its redelivery
            # (now that the case is visible) re-arms the ladder.
            assert lifted.code == "subject_cases_changed"
            assert timer.current(session, step) is None
            again = CollectionHoldService.lift(
                session, _lift_command(scope, subject, "dispute:r"), timer
            )
            session.commit()
            assert isinstance(again, HoldLifted)
        else:
            assert isinstance(lifted, HoldLifted)
        # Either way the new case ends with its reminder steps armed.
        assert timer.current(session, step) is not None


def _publish_ladder(session, scope, steps=None) -> uuid.UUID:
    """Notices as (code, ordinal, days, anchor); default due+7 and due+30."""

    steps = steps or (
        ("notice_7", 1, 7, "exposure_at"),
        ("notice_30", 2, 30, "exposure_at"),
    )

    from dotmac_collections.policies import (
        PolicyPublicationV1,
        PolicyStepDraftV1,
        PolicyVersionDraftV1,
    )
    from dotmac_collections.service import (
        CollectionPolicyService,
        CreateCollectionPolicyV1,
    )

    code = f"ladder_{uuid.uuid4().hex}"
    CollectionPolicyService.create(
        session,
        CreateCollectionPolicyV1(
            policy_id=uuid.uuid4(),
            scope=scope,
            policy_code=code,
            description="Skip-past-due ladder",
        ),
    )
    version_id = uuid.uuid4()
    CollectionPolicyService.publish(
        session,
        scope=scope,
        draft=PolicyVersionDraftV1(
            policy_code=code,
            reason_code="invoice_overdue",
            collection_timing="arrears",
            grace=None,
            steps=tuple(
                PolicyStepDraftV1(
                    code=code,
                    ordinal=ordinal,
                    offset=timedelta(days=days),
                    offset_anchor=anchor,
                    request_kind="notice",
                    action_code=None,
                    purpose_code="overdue_notice",
                    effect_scope=None,
                    receipt_required=False,
                    retry_offsets=(),
                )
                for code, ordinal, days, anchor in steps
            ),
        ),
        publication=PolicyPublicationV1(
            policy_version_id=version_id,
            version=1,
            effective_from=NOW - timedelta(days=400),
            actor_ref="service:canary",
            reason="ladder",
            published_at=NOW - timedelta(days=400),
        ),
    )
    return version_id


def _skipped_steps(session) -> list[str]:
    from dotmac_collections.models import CollectionStepAttempt

    return sorted(
        a.policy_step_code
        for a in session.scalars(select(CollectionStepAttempt))
        if a.request_kind == "skipped"
    )


def test_a_case_opened_over_30_days_late_sends_neither_notice(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope)
        timer = FakeCollectionsTimer()
        opened = _assess_open(session, scope, policy, timer, DUE + timedelta(days=45))
        session.commit()
        assert isinstance(opened, CaseAssessed)
        step = TimerIdentityV1(
            scope,
            "collections.case",
            "collection_case",
            str(opened.case_id),
            "next_step",
        )
        assert timer.current(session, step) is None
        assert _skipped_steps(session) == ["notice_30", "notice_7"]
        assert session.scalars(select(CollectionNoticeRequest)).all() == []


def test_a_case_opened_at_due_plus_10_arms_only_the_30_day_notice(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope)
        timer = FakeCollectionsTimer()
        opened = _assess_open(session, scope, policy, timer, DUE + timedelta(days=10))
        session.commit()
        assert isinstance(opened, CaseAssessed)
        step = TimerIdentityV1(
            scope,
            "collections.case",
            "collection_case",
            str(opened.case_id),
            "next_step",
        )
        handle = timer.current(session, step)
        assert handle is not None and handle.due_at == DUE + timedelta(days=30)
        assert _skipped_steps(session) == ["notice_7"]
        assert session.scalars(select(CollectionNoticeRequest)).all() == []


_CHAIN = (
    ("notice_1", 1, 7, "exposure_at"),
    ("notice_2", 2, 23, "request_at"),
)


def test_a_skipped_notice_anchors_the_notice_counted_from_its_request(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope, _CHAIN)
        timer = FakeCollectionsTimer()
        opened = _assess_open(session, scope, policy, timer, DUE + timedelta(days=10))
        session.commit()
        assert isinstance(opened, CaseAssessed)
        step = TimerIdentityV1(
            scope,
            "collections.case",
            "collection_case",
            str(opened.case_id),
            "next_step",
        )
        handle = timer.current(session, step)
        # notice 1 (due+7) skipped; notice 2 counts from it: due+7+23.
        assert handle is not None and handle.due_at == DUE + timedelta(days=30)
        assert _skipped_steps(session) == ["notice_1"]
        assert session.scalars(select(CollectionNoticeRequest)).all() == []


def test_a_chained_ladder_opened_at_due_plus_40_skips_both(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope, _CHAIN)
        timer = FakeCollectionsTimer()
        opened = _assess_open(session, scope, policy, timer, DUE + timedelta(days=40))
        session.commit()
        assert isinstance(opened, CaseAssessed)
        step = TimerIdentityV1(
            scope,
            "collections.case",
            "collection_case",
            str(opened.case_id),
            "next_step",
        )
        assert timer.current(session, step) is None
        assert _skipped_steps(session) == ["notice_1", "notice_2"]
        assert session.scalars(select(CollectionNoticeRequest)).all() == []


_HALTING = (
    ("notice_1", 1, 7, "exposure_at"),
    ("notice_2", 2, 23, "accepted_notice_receipt_at"),
)


def _halt_transitions(session) -> list:
    from dotmac_collections.models import CollectionCaseTransition

    return [
        t
        for t in session.scalars(select(CollectionCaseTransition))
        if t.reason_code == "policy_ladder_halted_no_anchor"
    ]


def test_a_ladder_that_halts_for_want_of_an_anchor_records_one_transition(
    scratch: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope, _HALTING)
        timer = FakeCollectionsTimer()
        opened = _assess_open(session, scope, policy, timer, DUE + timedelta(days=10))
        session.commit()
        assert isinstance(opened, CaseAssessed)
        step = TimerIdentityV1(
            scope,
            "collections.case",
            "collection_case",
            str(opened.case_id),
            "next_step",
        )
        # notice_1 was skipped; notice_2 counts from a receipt that cannot exist.
        assert _skipped_steps(session) == ["notice_1"]
        assert timer.current(session, step) is None
        halts = _halt_transitions(session)
        assert len(halts) == 1
        assert halts[0].case_id == opened.case_id
        assert (halts[0].from_state, halts[0].to_state) == ("active", "active")


def test_resume_and_hold_lift_record_no_second_halt_transition(
    scratch: tuple[str, str, str],
) -> None:
    from dotmac_collections.service import CaseLifecycleCommandV1

    admin_url, app_url, _ = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    subject = "subscriber:concurrency"
    with _tenant_session(app_url, tenant_id) as session:
        policy = _publish_ladder(session, scope, _HALTING)
        timer = FakeCollectionsTimer()
        opened = _assess_open(session, scope, policy, timer, DUE + timedelta(days=10))
        session.commit()
        assert isinstance(opened, CaseAssessed)
        assert len(_halt_transitions(session)) == 1

        def move(to_state: str, at) -> None:
            CollectionCaseService.transition(
                session,
                command=CaseLifecycleCommandV1(
                    command_id=uuid.uuid4(),
                    idempotency_key=f"move:{to_state}:{at}",
                    scope=scope,
                    case_id=opened.case_id,
                    actor_ref="staff:1",
                    reason_code="staff_decision",
                    occurred_at=at,
                ),
                to_state=to_state,  # type: ignore[arg-type]
                timer=timer,
            )

        move("paused", DUE + timedelta(days=11))
        move("active", DUE + timedelta(days=12))
        CollectionHoldService.place(
            session, _place_command(scope, subject, "dispute:halt")
        )
        CollectionHoldService.lift(
            session, _lift_command(scope, subject, "dispute:halt"), timer
        )
        session.commit()
        assert len(_halt_transitions(session)) == 1
