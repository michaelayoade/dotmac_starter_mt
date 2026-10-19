"""Private clean-ORM boundary for participating canonical identity writers.

Writers use immediate Core DML, so there is deliberately no authorized pending
ORM mutation phase. Existing hooks may observe work, but may not queue or flush
unrelated Unit-of-Work changes, complete transactions, or change the root.
This is engineering enforcement of the application's hooks, not a sandbox for
arbitrary in-process code executing its own SQL/DBAPI transactions.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from sys import exc_info
from typing import NoReturn

from sqlalchemy import event
from sqlalchemy.engine import Connection, NestedTransaction, RootTransaction
from sqlalchemy.orm import Session, SessionTransaction

from dotmac_kernel._transactions import _require_owner_rollback
from dotmac_kernel.transactions import (
    ConflictSavepointPhase,
    ConflictSavepointProtocolError,
    conflict_savepoint,
    current_conflict_savepoint,
)


def _pending(db: Session) -> None:
    # Passive collection membership: never load attributes to decide cleanliness.
    if db.new or db.dirty or db.deleted:
        raise ConflictSavepointProtocolError(
            "identity operation requires clean pending state"
        )


@dataclass
class _Operation:
    db: Session
    root: SessionTransaction
    savepoint: SessionTransaction | None = None
    failed: bool = False
    connection: Connection | None = None
    physical_root: RootTransaction | None = None
    physical_savepoint: NestedTransaction | None = None
    physical_guarded: bool = False
    physical_recovery_started: bool = False
    physical_recovered: bool = False
    affected: list[tuple[object, tuple[str, ...]]] = field(default_factory=list)

    def track(self, row: object, attributes: tuple[str, ...]) -> None:
        from sqlalchemy import inspect

        self.check()
        state = inspect(row)
        if state.session is not self.db or not state.persistent:
            self.refuse()
        self.affected.append((row, attributes))

    def refuse(self) -> NoReturn:
        self.failed = True
        raise ConflictSavepointProtocolError("identity operation protocol differs")

    def check(self) -> None:
        try:
            _pending(self.db)
        except ConflictSavepointProtocolError:
            self.failed = True
            raise
        if (
            self.failed
            or self.db.get_transaction() is not self.root
            or not self.root.is_active
        ):
            self.refuse()
        if self.connection is not None:
            if (
                self.physical_root is None
                or not self.physical_root.is_active
                or self.connection.get_transaction() is not self.physical_root
            ):
                self.refuse()
            if self.savepoint is not None and (
                self.physical_savepoint is None
                or not self.physical_savepoint.is_active
                or self.connection.get_nested_transaction()
                is not self.physical_savepoint
            ):
                self.refuse()
        if self.savepoint is not None:
            state = current_conflict_savepoint(self.db)
            if (
                state is None
                or state.root is not self.root
                or state.savepoint is not self.savepoint
                or self.db.get_nested_transaction() is not self.savepoint
                or not self.savepoint.is_active
                or state.phase
                not in (ConflictSavepointPhase.BODY, ConflictSavepointPhase.RELEASE)
            ):
                self.refuse()


@contextmanager
def _operation(db: Session) -> Generator[_Operation, None, None]:
    # These are the first operations, before any connection, ORM refresh,
    # savepoint, callback or implicit autobegin. Preexisting state stays intact.
    _pending(db)
    root = db.get_transaction()
    if root is None or not root.is_active:
        raise ConflictSavepointProtocolError(
            "identity operation requires an active caller root"
        )
    owner = _Operation(db, root)

    def before_flush(*args: object) -> None:
        # All participating mutations are explicit Core statements. Never let
        # begin_nested/release or a callback flush a pending ORM object.
        owner.refuse()

    def before_execute(*args: object) -> None:
        owner.check()

    def cursor_check(*args: object) -> None:
        state = current_conflict_savepoint(db)
        if (
            state is not None
            and state.root is root
            and state.savepoint is owner.savepoint
            and state.phase is ConflictSavepointPhase.ROLLBACK
        ):
            if owner.physical_recovery_started:
                owner.physical_recovered = True
            return  # the public helper owns physical recovery, even after refusal
        owner.check()

    def before_commit(*args: object) -> None:
        owner.check()
        state = current_conflict_savepoint(db)
        if (
            owner.savepoint is None
            or state is None
            or state.savepoint is not owner.savepoint
            or state.phase is not ConflictSavepointPhase.RELEASE
        ):
            owner.refuse()

    def no_physical_completion(*args: object) -> None:
        owner.refuse()

    def physical_release(*args: object) -> None:
        state = current_conflict_savepoint(db)
        if (
            state is None
            or state.savepoint is not owner.savepoint
            or state.phase is not ConflictSavepointPhase.RELEASE
        ):
            owner.refuse()
        owner.check()

    def physical_recovery(*args: object) -> None:
        state = current_conflict_savepoint(db)
        if (
            state is None
            or state.savepoint is not owner.savepoint
            or state.phase is not ConflictSavepointPhase.ROLLBACK
        ):
            owner.refuse()
        owner.physical_recovery_started = True

    physical_listeners = (
        ("before_cursor_execute", cursor_check),
        ("after_cursor_execute", cursor_check),
        ("commit", no_physical_completion),
        ("rollback", no_physical_completion),
        ("savepoint", no_physical_completion),
        ("release_savepoint", physical_release),
        ("rollback_savepoint", physical_recovery),
    )
    listeners = (
        ("before_flush", before_flush),
        ("do_orm_execute", before_execute),
        ("before_commit", before_commit),
    )
    for name, callback in listeners:
        event.listen(db, name, callback)
    connection = None
    completed = False
    try:
        with db.no_autoflush:
            owner.check()
            with conflict_savepoint(db):
                state = current_conflict_savepoint(db)
                if state is None or state.root is not root:
                    owner.refuse()
                owner.savepoint = state.savepoint
                owner.check()
                # Force the actual SAVEPOINT before any product query/mutation.
                # Opening hooks cannot flush: the guard was installed before
                # begin_nested. A hook-produced pending value is never refreshed.
                connection = db.connection()
                owner.connection = connection
                owner.physical_root = connection.get_transaction()
                owner.physical_savepoint = connection.get_nested_transaction()
                owner.check()
                for name, callback in physical_listeners:
                    event.listen(connection, name, callback)
                owner.physical_guarded = True
                yield owner
                owner.check()
            owner.savepoint = None
            owner.check()
            completed = True
    finally:
        preserving_primary = exc_info()[0] is not None
        cleanup_failed = False
        if not completed and db.get_transaction() is root and root.is_active:
            if owner.connection is not None and (
                owner.physical_root is None
                or not owner.physical_root.is_active
                or owner.connection.get_transaction() is not owner.physical_root
                or (owner.physical_guarded and not owner.physical_recovered)
            ):
                _require_owner_rollback(db, root)
            if not (db.new or db.dirty or db.deleted):
                from sqlalchemy import inspect

                for row, attributes in owner.affected:
                    if db.new or db.dirty or db.deleted:
                        _require_owner_rollback(db, root)
                        break
                    row_state = inspect(row)
                    if row_state.session is db and row_state.persistent:
                        try:
                            # Core writes do not create UOW dirty snapshots.
                            # Expire ONLY known clean affected fields after the
                            # owning SP recovers, never unrelated caller state.
                            db.expire(row, attribute_names=list(attributes))
                        except BaseException:
                            _require_owner_rollback(db, root)
            else:
                _require_owner_rollback(db, root)
        owner.affected.clear()
        if connection is not None:
            for name, callback in physical_listeners:
                try:
                    if event.contains(connection, name, callback):
                        event.remove(connection, name, callback)
                except BaseException:
                    cleanup_failed = True
        for name, callback in listeners:
            try:
                if event.contains(db, name, callback):
                    event.remove(db, name, callback)
            except BaseException:
                cleanup_failed = True
        if cleanup_failed:
            _require_owner_rollback(db, root)
            if not preserving_primary:
                raise ConflictSavepointProtocolError(
                    "identity operation guard cleanup differs"
                )
        # Recovery hooks may not leave work outside the recovered SAVEPOINT.
        # Do not replace the primary exception or clear any pending values.
        if db.get_transaction() is root and (db.new or db.dirty or db.deleted):
            _require_owner_rollback(db, root)


def _refresh_sessions(
    db: Session, ids: tuple[object, ...], *, owner: _Operation
) -> None:
    """Owners refresh only already-cached CLEAN affected revocation attributes."""
    from sqlalchemy import inspect

    from dotmac_kernel.models import AuthSession

    _pending(db)
    wanted = set(ids)
    for row in tuple(db.identity_map.values()):
        if isinstance(row, AuthSession):
            identity = inspect(row).identity
            if identity is not None and identity[0] in wanted:
                owner.track(row, ("revoked_at", "updated_at"))
                _pending(db)
                db.refresh(row, attribute_names=["revoked_at", "updated_at"])
                _pending(db)
