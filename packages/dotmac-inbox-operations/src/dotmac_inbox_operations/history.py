"""Identity-preserving adoption seam for historical inbox operations.

Runtime commands mint operational identities and apply live workflow effects.
These import commands instead reproduce authoritative history under its source
identity.  They validate structural invariants, preserve source timestamps and
make only an exact replay idempotent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_inbox_operations.contracts import (
    AssignmentStatus,
    Conflict,
    ImportAgentPresence,
    ImportConversationAssignment,
    ImportQueueEntry,
    ImportRoundRobinRotation,
    QueueEntryStatus,
)
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxAgentPresence,
    InboxQueue,
    InboxQueueEntry,
    InboxRoundRobinCursor,
)

_Row = TypeVar("_Row")


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-inbox-operations history requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _optional_aware(value: datetime | None, field: str) -> datetime | None:
    return None if value is None else _aware(value, field)


def _instant(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _equal(current: Any, expected: Any) -> bool:
    if isinstance(current, datetime) and isinstance(expected, datetime):
        return _instant(current) == _instant(expected)
    return bool(current == expected)


def _require_same(row: _Row, *, identity: str, expected: dict[str, Any]) -> _Row:
    changed = sorted(
        name
        for name, value in expected.items()
        if not _equal(getattr(row, name), value)
    )
    if changed:
        raise Conflict(
            f"historical {identity} was reused with different {', '.join(changed)}"
        )
    return row


def _history_timestamps(
    created_at: datetime, updated_at: datetime
) -> tuple[datetime, datetime]:
    created = _aware(created_at, "created_at")
    updated = _aware(updated_at, "updated_at")
    if _instant(updated) < _instant(created):
        raise ValueError("updated_at cannot precede created_at")
    return created, updated


def _queue_exists(db: Session, tenant_id: UUID, queue_id: UUID) -> None:
    exists = db.scalar(
        select(InboxQueue.id).where(
            InboxQueue.tenant_id == tenant_id,
            InboxQueue.id == queue_id,
        )
    )
    if exists is None:
        raise Conflict("historical operation queue was not found")


def _insert_or_replay(
    db: Session,
    *,
    row: _Row,
    replay: Callable[[], _Row | None],
    conflict_label: str,
) -> _Row:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = replay()
        if winner is None:
            raise Conflict(
                f"historical {conflict_label} conflicted outside its tenant"
            ) from exc
        return winner
    return row


def import_agent_presence(
    db: Session, *, scope: TenantScope, command: ImportAgentPresence
) -> InboxAgentPresence:
    tenant_id = _tenant(scope)
    agent = _required(command.agent_reference, "agent reference")
    if command.assignment_capacity < 0:
        raise Conflict("assignment capacity must not be negative")
    observed_at = _aware(command.observed_at, "observed_at")
    created_at, updated_at = _history_timestamps(command.created_at, command.updated_at)
    expected = {
        "tenant_id": tenant_id,
        "id": command.id,
        "agent_reference": agent,
        "state": command.state,
        "assignment_capacity": command.assignment_capacity,
        "observed_at": observed_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }

    def replay() -> InboxAgentPresence | None:
        by_id = db.scalar(
            select(InboxAgentPresence).where(
                InboxAgentPresence.tenant_id == tenant_id,
                InboxAgentPresence.id == command.id,
            )
        )
        if by_id is not None:
            return _require_same(
                by_id, identity=f"presence id {command.id}", expected=expected
            )
        by_agent = db.scalar(
            select(InboxAgentPresence).where(
                InboxAgentPresence.tenant_id == tenant_id,
                InboxAgentPresence.agent_reference == agent,
            )
        )
        if by_agent is not None:
            raise Conflict(
                f"historical presence for agent {agent!r} already has id {by_agent.id}"
            )
        return None

    existing = replay()
    if existing is not None:
        return existing
    return _insert_or_replay(
        db,
        row=InboxAgentPresence(**expected),
        replay=replay,
        conflict_label="presence",
    )


def import_conversation_assignment(
    db: Session, *, scope: TenantScope, command: ImportConversationAssignment
) -> ConversationAssignment:
    tenant_id = _tenant(scope)
    _queue_exists(db, tenant_id, command.queue_id)
    conversation = _required(command.conversation_reference, "conversation reference")
    agent = _required(command.agent_reference, "agent reference")
    assigned_at = _aware(command.assigned_at, "assigned_at")
    released_at = _optional_aware(command.released_at, "released_at")
    if command.status is AssignmentStatus.ASSIGNED and released_at is not None:
        raise Conflict("active assignment history cannot carry released_at")
    if command.status is AssignmentStatus.RELEASED and released_at is None:
        raise Conflict("released assignment history requires released_at")
    if released_at is not None and _instant(released_at) < _instant(assigned_at):
        raise Conflict("assignment release cannot precede assignment")
    created_at, updated_at = _history_timestamps(command.created_at, command.updated_at)
    expected = {
        "tenant_id": tenant_id,
        "id": command.id,
        "conversation_reference": conversation,
        "queue_id": command.queue_id,
        "agent_reference": agent,
        "status": command.status,
        "assigned_at": assigned_at,
        "released_at": released_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }

    def replay() -> ConversationAssignment | None:
        by_id = db.scalar(
            select(ConversationAssignment).where(
                ConversationAssignment.tenant_id == tenant_id,
                ConversationAssignment.id == command.id,
            )
        )
        if by_id is not None:
            return _require_same(
                by_id, identity=f"assignment id {command.id}", expected=expected
            )
        if command.status is AssignmentStatus.ASSIGNED:
            active = db.scalar(
                select(ConversationAssignment).where(
                    ConversationAssignment.tenant_id == tenant_id,
                    ConversationAssignment.conversation_reference == conversation,
                    ConversationAssignment.status == AssignmentStatus.ASSIGNED,
                )
            )
            if active is not None:
                raise Conflict(
                    "historical conversation already has another active assignment"
                )
        return None

    existing = replay()
    if existing is not None:
        return existing
    return _insert_or_replay(
        db,
        row=ConversationAssignment(**expected),
        replay=replay,
        conflict_label="assignment",
    )


def import_queue_entry(
    db: Session, *, scope: TenantScope, command: ImportQueueEntry
) -> InboxQueueEntry:
    tenant_id = _tenant(scope)
    _queue_exists(db, tenant_id, command.queue_id)
    conversation = _required(command.conversation_reference, "conversation reference")
    if command.queue_position <= 0:
        raise Conflict("queue position must be positive")
    entered_at = _aware(command.entered_at, "entered_at")
    settled_at = _optional_aware(command.settled_at, "settled_at")
    if command.status is QueueEntryStatus.QUEUED and settled_at is not None:
        raise Conflict("queued history cannot carry settled_at")
    if command.status is not QueueEntryStatus.QUEUED and settled_at is None:
        raise Conflict("settled queue-entry history requires settled_at")
    if settled_at is not None and _instant(settled_at) < _instant(entered_at):
        raise Conflict("queue settlement cannot precede admission")
    created_at, updated_at = _history_timestamps(command.created_at, command.updated_at)
    expected = {
        "tenant_id": tenant_id,
        "id": command.id,
        "queue_id": command.queue_id,
        "conversation_reference": conversation,
        "queue_position": command.queue_position,
        "status": command.status,
        "entered_at": entered_at,
        "settled_at": settled_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }

    def replay() -> InboxQueueEntry | None:
        by_id = db.scalar(
            select(InboxQueueEntry).where(
                InboxQueueEntry.tenant_id == tenant_id,
                InboxQueueEntry.id == command.id,
            )
        )
        if by_id is not None:
            return _require_same(
                by_id, identity=f"queue-entry id {command.id}", expected=expected
            )
        position = db.scalar(
            select(InboxQueueEntry).where(
                InboxQueueEntry.tenant_id == tenant_id,
                InboxQueueEntry.queue_id == command.queue_id,
                InboxQueueEntry.queue_position == command.queue_position,
            )
        )
        if position is not None:
            raise Conflict("historical queue position already belongs to another entry")
        if command.status is QueueEntryStatus.QUEUED:
            active = db.scalar(
                select(InboxQueueEntry).where(
                    InboxQueueEntry.tenant_id == tenant_id,
                    InboxQueueEntry.conversation_reference == conversation,
                    InboxQueueEntry.status == QueueEntryStatus.QUEUED,
                )
            )
            if active is not None:
                raise Conflict("historical conversation is already actively queued")
        return None

    existing = replay()
    if existing is not None:
        return existing
    return _insert_or_replay(
        db,
        row=InboxQueueEntry(**expected),
        replay=replay,
        conflict_label="queue entry",
    )


def import_round_robin_rotation(
    db: Session, *, scope: TenantScope, command: ImportRoundRobinRotation
) -> InboxRoundRobinCursor:
    tenant_id = _tenant(scope)
    _queue_exists(db, tenant_id, command.queue_id)
    if command.rotation_count < 0:
        raise Conflict("rotation count must not be negative")
    agent = (
        None
        if command.last_assigned_agent_reference is None
        else _required(command.last_assigned_agent_reference, "last assigned agent")
    )
    created_at, updated_at = _history_timestamps(command.created_at, command.updated_at)
    expected = {
        "tenant_id": tenant_id,
        "id": command.id,
        "queue_id": command.queue_id,
        "last_assigned_agent_reference": agent,
        "rotation_count": command.rotation_count,
        "created_at": created_at,
        "updated_at": updated_at,
    }

    def replay() -> InboxRoundRobinCursor | None:
        by_id = db.scalar(
            select(InboxRoundRobinCursor).where(
                InboxRoundRobinCursor.tenant_id == tenant_id,
                InboxRoundRobinCursor.id == command.id,
            )
        )
        if by_id is not None:
            return _require_same(
                by_id, identity=f"cursor id {command.id}", expected=expected
            )
        by_queue = db.scalar(
            select(InboxRoundRobinCursor).where(
                InboxRoundRobinCursor.tenant_id == tenant_id,
                InboxRoundRobinCursor.queue_id == command.queue_id,
            )
        )
        if by_queue is not None:
            raise Conflict(f"historical queue cursor already has id {by_queue.id}")
        return None

    existing = replay()
    if existing is not None:
        return existing
    return _insert_or_replay(
        db,
        row=InboxRoundRobinCursor(**expected),
        replay=replay,
        conflict_label="round-robin cursor",
    )


__all__ = [
    "import_agent_presence",
    "import_conversation_assignment",
    "import_queue_entry",
    "import_round_robin_rotation",
]
