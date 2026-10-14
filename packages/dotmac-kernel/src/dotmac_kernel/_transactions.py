"""Internal transaction mechanics shared by kernel services.

This module deliberately has no engine, settings or session-factory imports.
Kernel services that receive an assembly-owned ``Session`` may therefore use a
SAVEPOINT without importing :mod:`dotmac_kernel.db` and constructing the
kernel's configured database runtime. :mod:`dotmac_kernel.transactions` is the
public engine-free surface; :mod:`dotmac_kernel.db` retains its compatibility
re-export.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from types import TracebackType

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction


class ConflictSavepointProtocolError(RuntimeError):
    """Participant completion or unsafe savepoint recovery forbids completion."""


class ConflictSavepointPhase(str, Enum):
    """The helper's current operation; observing it grants no permission."""

    BODY = "body"
    RELEASE = "release"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class ConflictSavepointState:
    """Immutable evidence for a caller's transaction-completion guard."""

    root: SessionTransaction
    savepoint: SessionTransaction
    phase: ConflictSavepointPhase


@dataclass
class _Frame:
    state: ConflictSavepointState
    release_unclaimed: bool = False


_STACK_KEY = "_dotmac_kernel_conflict_savepoint_stack"
_FAILED_ROOT_KEY = "_dotmac_kernel_conflict_savepoint_failed_root"


def conflict_savepoint_requires_rollback(
    db: Session, *, expected_root: SessionTransaction
) -> bool:
    """Whether failed savepoint recovery requires this root's owner to abort.

    This exact-root latch outlives the helper's lifecycle observation. It holds
    no exception or callback, and clears only when the matching root ends.
    """
    return (
        _FAILED_ROOT_KEY in db.info
        and db.info[_FAILED_ROOT_KEY] is expected_root
        and db.get_transaction() is expected_root
    )


def _require_owner_rollback(db: Session, root: SessionTransaction) -> None:
    if db.get_transaction() is root:
        db.info[_FAILED_ROOT_KEY] = root


@event.listens_for(Session, "before_commit")
def _reject_failed_root_commit(db: Session) -> None:
    root = db.get_transaction()
    if root is not None and conflict_savepoint_requires_rollback(
        db, expected_root=root
    ):
        raise ConflictSavepointProtocolError(
            "Conflict savepoint recovery failed; the root owner must roll back"
        )


@event.listens_for(Session, "after_transaction_end")
def _clear_ended_root_failure(db: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None and db.info.get(_FAILED_ROOT_KEY) is transaction:
        db.info.pop(_FAILED_ROOT_KEY, None)


def current_conflict_savepoint(db: Session) -> ConflictSavepointState | None:
    """Observe this session's innermost helper, including rollback events.

    An observation is never a completion permission. During rollback the
    savepoint may already be detached or inactive; its identity remains useful
    to classify ``after_soft_rollback`` events until the helper exits.
    """
    stack: list[_Frame] = db.info.get(_STACK_KEY, [])
    if not stack or db.get_transaction() is not stack[-1].state.root:
        return None
    return stack[-1].state


def claim_conflict_savepoint_commit(
    db: Session, *, expected_root: SessionTransaction
) -> bool:
    """Consume permission for the helper's exact RELEASE, once.

    A product guard supplies its command's stored root, rather than trusting a
    root inferred from whatever transaction happens to be current. BODY and
    ROLLBACK never authorize completion; arbitrary nested transactions never
    qualify. The caller still owns policy and all outer transaction completion.
    """
    stack: list[_Frame] = db.info.get(_STACK_KEY, [])
    if not stack:
        return False
    frame = stack[-1]
    state = frame.state
    if (
        not frame.release_unclaimed
        or conflict_savepoint_requires_rollback(db, expected_root=expected_root)
        or state.phase is not ConflictSavepointPhase.RELEASE
        or state.root is not expected_root
        or db.get_transaction() is not expected_root
        or not expected_root.is_active
        or db.get_nested_transaction() is not state.savepoint
        or not state.savepoint.is_active
    ):
        return False
    frame.release_unclaimed = False
    return True


@contextmanager
def conflict_savepoint(db: Session) -> Generator[None, None, None]:
    """Run an expected-conflict mutation inside a SAVEPOINT (F3 fix).

    The caller's session boundary owns the outer transaction and may establish
    transaction-local state such as ``app.current_tenant`` for RLS. A bare
    ``db.rollback()`` would discard that entire transaction and its local
    state. ``db.begin_nested()`` instead rolls back only the SAVEPOINT while
    preserving the caller's transaction, then re-raises the exception
    unchanged.

    IMPORTANT: the mutation (``db.add(...)``, or setting attributes on an
    already-loaded object) must happen INSIDE the ``with`` block. Entering a
    nested transaction auto-flushes pending changes before the SAVEPOINT is
    established, so a mutation made before this helper would not be protected.

    Expected usage::

        try:
            with conflict_savepoint(db):
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            raise ConflictError("...") from exc

    A service must never call ``db.rollback()`` directly. The assembly-owned
    boundary remains responsible for committing or rolling back the outer
    transaction.
    """
    # begin_nested auto-flushes BEFORE creating the savepoint. Do not publish
    # state or permission for that caller-owned preflush.
    enclosing_savepoint = db.get_nested_transaction()
    savepoint = db.begin_nested()
    root = db.get_transaction()
    assert root is not None
    frame = _Frame(ConflictSavepointState(root, savepoint, ConflictSavepointPhase.BODY))
    stack: list[_Frame] = db.info.setdefault(_STACK_KEY, [])
    stack.append(frame)
    primary_exception: BaseException | None = None
    primary_traceback: TracebackType | None = None
    try:
        # Keep SQLAlchemy's real transaction context checks active, rather than
        # replacing its context with a naked begin/commit/rollback sequence.
        try:
            with savepoint:
                try:
                    yield
                    if (
                        db.get_transaction() is not root
                        or not root.is_active
                        or db.get_nested_transaction() is not savepoint
                        or not savepoint.is_active
                    ):
                        raise ConflictSavepointProtocolError(
                            "A participant completed the conflict savepoint"
                        )
                    frame.state = replace(
                        frame.state, phase=ConflictSavepointPhase.RELEASE
                    )
                    frame.release_unclaimed = True
                    savepoint.commit()
                    frame.release_unclaimed = False
                except BaseException as exc:
                    primary_exception = exc
                    primary_traceback = exc.__traceback__
                    frame.release_unclaimed = False
                    frame.state = replace(
                        frame.state, phase=ConflictSavepointPhase.ROLLBACK
                    )
                    recovered = False
                    if (
                        db.get_transaction() is root
                        and root.is_active
                        and db.get_nested_transaction() is savepoint
                    ):
                        try:
                            # Includes attached inactive failed-flush handles.
                            # A COMMITTED handle instead refuses rollback.
                            savepoint.rollback()
                            recovered = (
                                db.get_transaction() is root
                                and root.is_active
                                and db.get_nested_transaction() is enclosing_savepoint
                            )
                        except BaseException:
                            recovered = False
                    if not recovered:
                        _require_owner_rollback(db, root)
                        if db.get_nested_transaction() is savepoint:
                            try:
                                # Only detach this handle. Never complete root.
                                savepoint.close()
                            except BaseException:
                                # The original failure remains the diagnostic;
                                # the exact-root latch refuses later completion.
                                _require_owner_rollback(db, root)
                    raise
        except BaseException as exc:
            # A real transaction context may itself fail while cleaning up.
            # Preserve the first failure across __exit__ as well as close().
            if primary_exception is None:
                primary_exception = exc
                primary_traceback = exc.__traceback__
                _require_owner_rollback(db, root)
            elif exc is not primary_exception:
                _require_owner_rollback(db, root)
            raise primary_exception.with_traceback(
                primary_traceback
            ) from primary_exception.__cause__
    finally:
        frame.release_unclaimed = False
        # Pop only this exact frame, after rollback/context exit events, so
        # nested helpers restore their parent's immutable observation.
        if stack and stack[-1] is frame:
            stack.pop()
        else:
            _require_owner_rollback(db, root)
            db.info.pop(_STACK_KEY, None)
            if primary_exception is None:
                raise ConflictSavepointProtocolError(
                    "Conflict savepoint lifecycle stack changed"
                )
        if not stack:
            db.info.pop(_STACK_KEY, None)
