"""Private same-root identity persistence; selectors never confer authority.

The operation owner establishes its physical conflict SAVEPOINT, clean ORM
phases and complete canonical lock closure. This module creates no transaction,
performs no ORM flush, and does not select credential or session material.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from sys import exc_info
from typing import cast
from uuid import UUID

from sqlalchemy import Table, event, select, update
from sqlalchemy.engine import Connection, NestedTransaction, RootTransaction
from sqlalchemy.orm import Session, SessionTransaction

from dotmac_kernel._transactions import _require_owner_rollback
from dotmac_kernel.models import AuthSession
from dotmac_kernel.transactions import (
    ConflictSavepointPhase,
    ConflictSavepointProtocolError,
    current_conflict_savepoint,
)

_FAILED_SCOPES = "_dotmac_identity_failed_savepoints"


def _fail_scope(
    db: Session, root: SessionTransaction, savepoint: SessionTransaction
) -> None:
    if db.get_transaction() is root:
        db.info.setdefault(_FAILED_SCOPES, {})[savepoint] = root


@event.listens_for(Session, "before_commit")
def _reject_failed_identity_savepoint(db: Session) -> None:
    root, savepoint = db.get_transaction(), db.get_nested_transaction()
    if root is not None and db.info.get(_FAILED_SCOPES, {}).get(savepoint) is root:
        raise ConflictSavepointProtocolError(
            "identity storage operation requires savepoint rollback"
        )


@event.listens_for(Session, "after_transaction_end")
def _clear_identity_savepoint_failure(
    db: Session, transaction: SessionTransaction
) -> None:
    failed = db.info.get(_FAILED_SCOPES)
    if failed is not None:
        failed.pop(transaction, None)
        if transaction.parent is None:
            for savepoint, root in tuple(failed.items()):
                if root is transaction:
                    failed.pop(savepoint, None)
        if not failed:
            db.info.pop(_FAILED_SCOPES, None)


@contextmanager
def _storage_phase(db: Session) -> Generator[None, None, None]:
    """Guard this immediate-Core phase and latch failures until its SP rollback.

    The caller supplied the actual public SAVEPOINT. A caught callback/refusal
    cannot authorize its later release, but rollback clears this exact-scope
    latch and preserves the caller's earlier flushed root work.
    """
    root, savepoint = _clean_boundary(db)

    def check(*args: object) -> None:
        try:
            _clean_boundary(db, root=root, savepoint=savepoint)
            if connection is not None and (
                physical_root is None
                or not physical_root.is_active
                or connection.get_transaction() is not physical_root
                or physical_savepoint is None
                or not physical_savepoint.is_active
                or connection.get_nested_transaction() is not physical_savepoint
            ):
                raise ConflictSavepointProtocolError(
                    "identity storage physical savepoint differs"
                )
        except BaseException:
            _fail_scope(db, root, savepoint)
            raise

    def no_flush_or_commit(*args: object) -> None:
        _fail_scope(db, root, savepoint)
        raise ConflictSavepointProtocolError(
            "identity storage cannot flush or complete transactions"
        )

    connection_listeners = (
        ("before_cursor_execute", check),
        ("after_cursor_execute", check),
        ("commit", no_flush_or_commit),
        ("rollback", no_flush_or_commit),
        ("savepoint", no_flush_or_commit),
        ("release_savepoint", no_flush_or_commit),
        ("rollback_savepoint", no_flush_or_commit),
    )
    connection: Connection | None = None
    physical_root: RootTransaction | None = None
    physical_savepoint: NestedTransaction | None = None
    event.listen(db, "before_flush", no_flush_or_commit)
    event.listen(db, "before_commit", no_flush_or_commit)
    event.listen(db, "do_orm_execute", check)
    try:
        with db.no_autoflush:
            check()
            connection = db.connection()
            physical_root = connection.get_transaction()
            physical_savepoint = connection.get_nested_transaction()
            check()
            for name, callback in connection_listeners:
                event.listen(connection, name, callback)
            yield
            check()
    except BaseException:
        _fail_scope(db, root, savepoint)
        if connection is not None and (
            physical_root is None
            or not physical_root.is_active
            or connection.get_transaction() is not physical_root
            or physical_savepoint is None
            or not physical_savepoint.is_active
            or connection.get_nested_transaction() is not physical_savepoint
        ):
            _require_owner_rollback(db, root)
        raise
    finally:
        preserving_primary = exc_info()[0] is not None
        cleanup_failed = False
        if connection is not None:
            for name, callback in connection_listeners:
                try:
                    if event.contains(connection, name, callback):
                        event.remove(connection, name, callback)
                except BaseException:
                    cleanup_failed = True
        for name, callback in (
            ("before_flush", no_flush_or_commit),
            ("before_commit", no_flush_or_commit),
            ("do_orm_execute", check),
        ):
            try:
                if event.contains(db, name, callback):
                    event.remove(db, name, callback)
            except BaseException:
                cleanup_failed = True
        if cleanup_failed:
            _fail_scope(db, root, savepoint)
            _require_owner_rollback(db, root)
            if not preserving_primary:
                raise ConflictSavepointProtocolError(
                    "identity storage guard cleanup differs"
                )


def _clean_boundary(
    db: Session,
    *,
    root: SessionTransaction | None = None,
    savepoint: SessionTransaction | None = None,
) -> tuple[SessionTransaction, SessionTransaction]:
    # This inspection MUST precede connection acquisition or attribute refresh.
    # False-positive dirty membership is deliberately refused, too.
    if db.new or db.dirty or db.deleted:
        raise ConflictSavepointProtocolError(
            "identity storage requires clean pending state"
        )
    state = current_conflict_savepoint(db)
    if (
        state is None
        or state.phase is not ConflictSavepointPhase.BODY
        or not state.root.is_active
        or not state.savepoint.is_active
        or db.get_transaction() is not state.root
        or db.get_nested_transaction() is not state.savepoint
        or (root is not None and state.root is not root)
        or (savepoint is not None and state.savepoint is not savepoint)
        or db.info.get(_FAILED_SCOPES, {}).get(state.savepoint) is state.root
    ):
        raise ConflictSavepointProtocolError(
            "identity storage requires its caller savepoint"
        )
    return state.root, state.savepoint


def _uuid(value: object) -> None:
    if not isinstance(value, UUID):
        raise ValueError("identity storage selector shape differs")


def _ids(values: tuple[UUID, ...]) -> None:
    if not isinstance(values, tuple):
        raise ValueError("identity storage cohort shape differs")
    for value in values:
        _uuid(value)
    if len(set(values)) != len(values):
        raise ValueError("identity storage cohort shape differs")


def _revoke_sessions(
    db: Session,
    *,
    tenant_id: UUID,
    party_id: UUID,
    session_ids: tuple[UUID, ...] | None,
    revoked_at: datetime,
    binding_id: UUID | None = None,
) -> tuple[UUID, ...]:
    """The single private revocation DML owner, including binding selection.

    Binding adapters retain their exact selector and must already hold Party,
    binding and the complete ordered session cohort. None means the entire
    selected scope; an empty tuple never widens it. Expiry is not a predicate.
    """
    root, savepoint = _clean_boundary(db)
    _uuid(tenant_id)
    _uuid(party_id)
    if binding_id is not None:
        _uuid(binding_id)
    if session_ids is not None:
        _ids(session_ids)
    if (
        not isinstance(revoked_at, datetime)
        or revoked_at.tzinfo is None
        or revoked_at.utcoffset() is None
    ):
        raise ValueError("identity storage requires an aware operation timestamp")
    table = cast(Table, AuthSession.__table__)
    predicates = [table.c.tenant_id == tenant_id, table.c.party_id == party_id]
    if binding_id is not None:
        predicates.append(table.c.external_identity_binding_id == binding_id)
    if session_ids is not None:
        if not session_ids:
            return ()
        predicates.append(table.c.id.in_(session_ids))
    with _storage_phase(db):
        _clean_boundary(db, root=root, savepoint=savepoint)
        existing = tuple(
            db.execute(
                select(table.c.id).where(*predicates).order_by(table.c.id)
            ).scalars()
        )
        _clean_boundary(db, root=root, savepoint=savepoint)
        if session_ids is not None and existing != tuple(sorted(session_ids)):
            raise ValueError("identity storage cohort differs")
        if not existing:
            return ()
        # RETURNING is the persisted outcome, not a predicted count. Prior
        # timestamps, hashes, expiry and provenance remain byte-for-byte owned
        # by their original writers.
        changed = tuple(
            db.execute(
                update(table)
                .where(
                    *predicates, table.c.id.in_(existing), table.c.revoked_at.is_(None)
                )
                .values(revoked_at=revoked_at, updated_at=revoked_at)
                .returning(table.c.id)
            ).scalars()
        )
        _clean_boundary(db, root=root, savepoint=savepoint)
    return tuple(sorted(changed))
