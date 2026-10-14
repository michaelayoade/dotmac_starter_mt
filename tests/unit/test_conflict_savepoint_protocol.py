"""Hosted parity for Sub's exact-handle completion guard and Kernel lifecycle."""

from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import pytest
from dotmac_kernel._transactions import conflict_savepoint as private_helper
from dotmac_kernel.transactions import (
    ConflictSavepointPhase,
    ConflictSavepointProtocolError,
    claim_conflict_savepoint_commit,
    conflict_savepoint,
    conflict_savepoint_requires_rollback,
    current_conflict_savepoint,
)
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "conflict_protocol_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)


@pytest.fixture
def session():
    # SQLite's legacy implicit-BEGIN mode can physically commit the root on
    # RELEASE of its first savepoint. Use real explicit root transactions.
    engine = create_engine("sqlite://", connect_args={"isolation_level": None})

    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    event.listen(engine, "begin", begin)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
        db.rollback()
        assert db.info == {}, "helper lifecycle leaked past its context"
    engine.dispose()


@contextmanager
def owner_guard(db):
    """The product owns this policy; Kernel only supplies exact evidence."""
    root = db.get_transaction()
    forbidden_rollbacks = []

    def reject_commit(current):
        state = current_conflict_savepoint(current)
        if (
            state is not None
            and state.root is root
            and claim_conflict_savepoint_commit(current, expected_root=root)
        ):
            return
        raise RuntimeError("participant completion forbidden")

    def record_rollback(current, previous):
        state = current_conflict_savepoint(current)
        if state is not None and state.root is root:
            if (
                state.phase is ConflictSavepointPhase.ROLLBACK
                and previous is state.savepoint
            ):
                return
            if (
                previous.parent is state.savepoint
                and not previous.nested
                and not state.savepoint.is_active
            ):
                return
        forbidden_rollbacks.append(previous)

    event.listen(db, "before_commit", reject_commit)
    event.listen(db, "after_soft_rollback", record_rollback)
    try:
        yield
        if forbidden_rollbacks:
            raise RuntimeError("participant rollback forbidden")
    finally:
        event.remove(db, "before_commit", reject_commit)
        event.remove(db, "after_soft_rollback", record_rollback)


def test_identity_immutable_observation_and_single_use_release(session):
    assert conflict_savepoint is private_helper
    session.begin()
    root = session.get_transaction()
    assert current_conflict_savepoint(session) is None
    assert not claim_conflict_savepoint_commit(session, expected_root=root)
    releases = []

    def claim(current):
        state = current_conflict_savepoint(current)
        assert state.phase is ConflictSavepointPhase.RELEASE
        assert state.root is root
        assert state.savepoint is current.get_nested_transaction()
        assert claim_conflict_savepoint_commit(current, expected_root=root)
        assert not claim_conflict_savepoint_commit(current, expected_root=root)
        releases.append(state)

    event.listen(session, "before_commit", claim)
    try:
        with conflict_savepoint(session):
            body = current_conflict_savepoint(session)
            assert body.phase is ConflictSavepointPhase.BODY
            assert not claim_conflict_savepoint_commit(
                session, expected_root=session.get_transaction()
            )
            with pytest.raises(FrozenInstanceError):
                body.phase = ConflictSavepointPhase.RELEASE
            session.add(Item(name="success"))
    finally:
        event.remove(session, "before_commit", claim)
    assert len(releases) == 1
    assert body.phase is ConflictSavepointPhase.BODY
    assert session.get_transaction() is root and root.is_active
    assert current_conflict_savepoint(session) is None
    assert not claim_conflict_savepoint_commit(
        session, expected_root=session.get_transaction()
    )


def test_nested_helpers_restore_parent_and_isolate_sessions(session):
    with Session(session.bind) as other, conflict_savepoint(session):
        parent = current_conflict_savepoint(session)
        assert current_conflict_savepoint(other) is None
        assert not claim_conflict_savepoint_commit(other, expected_root=parent.root)
        with pytest.raises(ValueError, match="inner"), conflict_savepoint(session):
            child = current_conflict_savepoint(session)
            assert child.root is parent.root
            assert child.savepoint is not parent.savepoint
            raise ValueError("inner")
        assert current_conflict_savepoint(session) is parent
        with conflict_savepoint(session):
            assert current_conflict_savepoint(session).savepoint is not parent.savepoint
        assert current_conflict_savepoint(session) is parent
    assert current_conflict_savepoint(session) is None


def test_body_failure_retains_rollback_observation_through_events(session):
    session.begin()
    root = session.get_transaction()
    observations = []

    def rolled_back(current, previous):
        state = current_conflict_savepoint(current)
        observations.append((state, previous))
        assert state.phase is ConflictSavepointPhase.ROLLBACK
        assert not claim_conflict_savepoint_commit(current, expected_root=root)

    event.listen(session, "after_soft_rollback", rolled_back)
    failure = ValueError("body failed")
    try:
        with pytest.raises(ValueError) as caught, conflict_savepoint(session):
            session.add(Item(name="discard"))
            session.flush()
            raise failure
    finally:
        event.remove(session, "after_soft_rollback", rolled_back)
    assert caught.value is failure
    assert observations[-1][0].savepoint is observations[-1][1]
    assert session.get_transaction() is root and root.is_active
    assert session.scalars(select(Item)).all() == []
    assert current_conflict_savepoint(session) is None


def test_real_flush_constraint_failure_restores_attached_inactive_savepoint(session):
    session.add(Item(name="duplicate"))
    session.flush()
    root = session.get_transaction()
    seen = []

    def rolled_back(current, previous):
        seen.append((current_conflict_savepoint(current), previous))

    event.listen(session, "after_soft_rollback", rolled_back)
    try:
        with owner_guard(session), pytest.raises(IntegrityError):
            with conflict_savepoint(session):
                session.add(Item(name="duplicate"))
                session.flush()
    finally:
        event.remove(session, "after_soft_rollback", rolled_back)
    assert seen[0][0].phase is ConflictSavepointPhase.BODY
    assert seen[0][1].parent is seen[0][0].savepoint
    assert seen[-1][0].phase is ConflictSavepointPhase.ROLLBACK
    assert seen[-1][1] is seen[-1][0].savepoint
    assert session.get_transaction() is root and root.is_active
    assert session.is_active and not session.in_nested_transaction()
    assert not conflict_savepoint_requires_rollback(session, expected_root=root)
    assert current_conflict_savepoint(session) is None
    session.add(Item(name="after-conflict"))
    session.flush()


@pytest.mark.parametrize("action", ["commit", "savepoint_commit", "root_commit"])
def test_body_completion_never_claims_helper_permission(session, action):
    session.begin()
    root = session.get_transaction()
    with owner_guard(session), pytest.raises(RuntimeError, match="forbidden"):
        with conflict_savepoint(session):
            session.add(Item(name="forbidden"))
            session.flush()
            if action == "commit":
                session.commit()
            elif action == "root_commit":
                root.commit()
            else:
                session.get_nested_transaction().commit()
    assert session.get_transaction() is root and root.is_active
    assert session.scalars(select(Item)).all() == []
    assert current_conflict_savepoint(session) is None


@pytest.mark.parametrize("action", ["rollback", "savepoint_rollback", "root_rollback"])
def test_product_guard_rejects_body_owned_rollback(session, action):
    """Kernel reports evidence; the product guard rejects participant rollback."""
    session.begin()
    root = session.get_transaction()
    with pytest.raises(RuntimeError, match="completed|rollback forbidden"):
        with owner_guard(session):
            try:
                with conflict_savepoint(session):
                    session.add(Item(name="forbidden"))
                    session.flush()
                    if action == "rollback":
                        session.rollback()
                    elif action == "root_rollback":
                        root.rollback()
                    else:
                        session.get_nested_transaction().rollback()
            except ConflictSavepointProtocolError:
                # A helper could catch its own protocol error. The product's
                # boundary still sees the illegal BODY rollback event.
                assert current_conflict_savepoint(session) is None
    assert current_conflict_savepoint(session) is None
    assert session.scalars(select(Item)).all() == []


@pytest.mark.parametrize("action", ["commit", "rollback"])
def test_manual_handle_completion_is_detected_and_state_does_not_leak(session, action):
    session.begin()
    root = session.get_transaction()
    with pytest.raises(ConflictSavepointProtocolError, match="completed"):
        with conflict_savepoint(session):
            getattr(session.get_nested_transaction(), action)()
    assert current_conflict_savepoint(session) is None
    assert not claim_conflict_savepoint_commit(session, expected_root=root)
    assert conflict_savepoint_requires_rollback(session, expected_root=root)
    session.rollback()
    assert not conflict_savepoint_requires_rollback(session, expected_root=root)
    assert session.info == {}
    session.begin()
    fresh_root = session.get_transaction()
    assert fresh_root is not root
    with owner_guard(session), conflict_savepoint(session):
        pass
    assert not conflict_savepoint_requires_rollback(session, expected_root=fresh_root)
    assert session.info == {}


def test_release_event_failure_cleans_claim_and_rolls_back(session):
    session.begin()
    root = session.get_transaction()
    failure = ValueError("event failed")

    def fail(current):
        assert claim_conflict_savepoint_commit(current, expected_root=root)
        raise failure

    event.listen(session, "before_commit", fail)
    try:
        with pytest.raises(ValueError) as caught, conflict_savepoint(session):
            session.add(Item(name="discard"))
            session.flush()
    finally:
        event.remove(session, "before_commit", fail)
    assert caught.value is failure
    assert session.get_transaction() is root and root.is_active
    assert current_conflict_savepoint(session) is None
    assert not claim_conflict_savepoint_commit(
        session, expected_root=session.get_transaction()
    )
    assert session.scalars(select(Item)).all() == []


def test_entry_preflush_has_no_helper_state(session):
    session.add(Item(name="pending"))
    root = session.get_transaction()
    observations = []

    def flushing(current, _context, _instances):
        observations.append(current_conflict_savepoint(current))
        assert not claim_conflict_savepoint_commit(current, expected_root=root)

    event.listen(session, "before_flush", flushing)
    try:
        with conflict_savepoint(session):
            pass
    finally:
        event.remove(session, "before_flush", flushing)
    assert observations == [None]


@pytest.mark.parametrize("failure_event", ["after_commit", "after_transaction_end"])
def test_release_completion_event_failure_preserves_exception_and_detaches_handle(
    session, failure_event
):
    session.begin()
    root = session.get_transaction()
    failure = ValueError(f"{failure_event} failed")
    targets = []
    released = []

    def fail(current, transaction=None):
        if not targets or released:
            return
        if failure_event == "after_transaction_end" and transaction is not targets[0]:
            return
        state = current_conflict_savepoint(current)
        if state is None or state.savepoint is not targets[0]:
            return
        released.append(state.savepoint)
        assert state.phase is ConflictSavepointPhase.RELEASE
        assert not state.savepoint.is_active
        assert not claim_conflict_savepoint_commit(current, expected_root=root)
        raise failure

    event.listen(session, failure_event, fail)
    try:
        with owner_guard(session), pytest.raises(ValueError) as caught:
            with conflict_savepoint(session):
                targets.append(current_conflict_savepoint(session).savepoint)
                session.add(Item(name="released-child"))
    finally:
        event.remove(session, failure_event, fail)
    assert caught.value is failure
    assert released == targets
    assert session.get_transaction() is root and root.is_active
    assert session.get_nested_transaction() is None
    assert current_conflict_savepoint(session) is None
    assert not claim_conflict_savepoint_commit(session, expected_root=root)
    assert conflict_savepoint_requires_rollback(session, expected_root=root)
    for complete in (session.commit, root.commit):
        with pytest.raises(ConflictSavepointProtocolError, match="must roll back"):
            complete()
        assert session.get_transaction() is root and root.is_active
    # RELEASE already happened before the listener failed. Only the owner
    # can discard the staged child work, and it retains a usable root to do so.
    assert session.scalars(select(Item.name)).all() == ["released-child"]
    session.rollback()
    assert not conflict_savepoint_requires_rollback(session, expected_root=root)
    assert session.scalars(select(Item)).all() == []
    fresh_root = session.get_transaction()
    assert fresh_root is not root
    with owner_guard(session), conflict_savepoint(session):
        session.add(Item(name="fresh-root"))
    assert not conflict_savepoint_requires_rollback(session, expected_root=fresh_root)


@pytest.mark.parametrize(
    "cleanup_event", ["after_soft_rollback", "after_transaction_end"]
)
@pytest.mark.parametrize("after_release", [True, False])
def test_cleanup_listener_failure_preserves_first_error_and_latches_only_root(
    session, cleanup_event, after_release
):
    session.begin()
    root = session.get_transaction()
    primary = ValueError("first failure")
    original_cause = LookupError("original cause")
    primary.__cause__ = original_cause
    cleanup_failures = []

    def fail_after_commit(_current):
        raise primary

    def fail_cleanup(current, transaction):
        state = current_conflict_savepoint(current)
        if state is not None and transaction is state.savepoint:
            cleanup_failures.append(transaction)
            raise RuntimeError("cleanup failed")

    event.listen(session, cleanup_event, fail_cleanup)
    if after_release:
        event.listen(session, "after_commit", fail_after_commit)
    try:
        with pytest.raises(ValueError) as caught:
            with conflict_savepoint(session):
                session.add(Item(name="staged"))
                session.flush()
                if not after_release:
                    raise primary
    finally:
        event.remove(session, cleanup_event, fail_cleanup)
        if after_release:
            event.remove(session, "after_commit", fail_after_commit)
    assert caught.value is primary
    assert caught.value.__cause__ is original_cause
    # A COMMITTED handle has no soft-rollback event; close emits transaction-end.
    if not after_release or cleanup_event == "after_transaction_end":
        assert cleanup_failures
    assert session.get_transaction() is root and root.is_active
    assert conflict_savepoint_requires_rollback(session, expected_root=root)
    assert current_conflict_savepoint(session) is None
    assert not claim_conflict_savepoint_commit(session, expected_root=root)
    with Session(session.bind) as other:
        other.begin()
        assert not conflict_savepoint_requires_rollback(other, expected_root=root)
        assert not conflict_savepoint_requires_rollback(
            session, expected_root=other.get_transaction()
        )
    with pytest.raises(ConflictSavepointProtocolError, match="must roll back"):
        root.commit()
    session.rollback()
    assert not conflict_savepoint_requires_rollback(session, expected_root=root)
    assert session.info == {}
    session.begin()
    fresh_root = session.get_transaction()
    assert fresh_root is not root
    with owner_guard(session), conflict_savepoint(session):
        session.add(Item(name="fresh-root"))
    assert not conflict_savepoint_requires_rollback(session, expected_root=fresh_root)


def test_wrong_root_claim_does_not_consume_the_current_release(session):
    session.begin()
    root = session.get_transaction()
    with Session(session.bind) as other:
        other.begin()
        wrong_root = other.get_transaction()

        def claim(current):
            assert not claim_conflict_savepoint_commit(
                current, expected_root=wrong_root
            )
            assert not claim_conflict_savepoint_commit(other, expected_root=root)
            assert claim_conflict_savepoint_commit(current, expected_root=root)

        event.listen(session, "before_commit", claim)
        try:
            with conflict_savepoint(session):
                pass
        finally:
            event.remove(session, "before_commit", claim)


def test_release_time_flush_failure_cleans_state_and_preserves_root(session):
    session.add(Item(name="duplicate"))
    session.flush()
    root = session.get_transaction()
    with owner_guard(session), pytest.raises(IntegrityError):
        with conflict_savepoint(session):
            session.add(Item(name="duplicate"))
    assert session.is_active and session.get_transaction() is root
    assert not session.in_nested_transaction()
    assert current_conflict_savepoint(session) is None
    assert not conflict_savepoint_requires_rollback(session, expected_root=root)


@pytest.mark.parametrize("event_name", ["before_commit", "before_flush"])
@pytest.mark.parametrize("listener_first", [True, False])
@pytest.mark.parametrize("root_direct", [True, False])
@pytest.mark.parametrize("swallow", [True, False])
def test_reentrant_root_completion_never_escapes_owner_guard(
    session, event_name, listener_first, root_direct, swallow
):
    session.begin()
    root = session.get_transaction()
    attempts = []
    failures = []

    def reenter(current, *_args):
        state = current_conflict_savepoint(current)
        if state is None or state.phase is not ConflictSavepointPhase.RELEASE:
            return
        if attempts:
            return
        attempts.append(True)
        try:
            if root_direct:
                root.commit()
            else:
                current.commit()
        except (RuntimeError, SQLAlchemyError) as exc:
            failures.append(exc)
            if not swallow:
                raise
        else:
            pytest.fail("reentrant completion succeeded")

    if listener_first:
        event.listen(session, event_name, reenter)
    try:
        with owner_guard(session):
            if not listener_first:
                event.listen(session, event_name, reenter)
            try:
                with conflict_savepoint(session):
                    session.add(Item(name="forbidden"))
            except (RuntimeError, SQLAlchemyError) as exc:
                failures.append(exc)
    finally:
        event.remove(session, event_name, reenter)
    assert attempts == [True]
    assert failures, "the reentrant call must have been refused"
    assert session.get_transaction() is root and root.is_active
    assert current_conflict_savepoint(session) is None
    assert not claim_conflict_savepoint_commit(
        session, expected_root=session.get_transaction()
    )
    # A swallowed, rejected attempt may let the legitimate nested release
    # finish. The enclosing owner can still discard every staged effect.
    session.rollback()
    assert session.scalars(select(Item)).all() == []
