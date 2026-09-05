"""Focused contract tests for the tenant inbox read surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from dotmac_inbox import (
    ConversationFilter,
    ConversationNotFound,
    Direction,
    MessagePage,
    Status,
    get_conversation,
    list_conversations,
    list_messages,
)
from dotmac_inbox.models import Conversation, ConversationReadState, Message
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_inbox": None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            Conversation.__table__,
            Message.__table__,
            ConversationReadState.__table__,
        ],
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tenant(db: Session, slug: str) -> Tenant:
    tenant = Tenant(slug=slug, name=slug)
    db.add(tenant)
    db.flush()
    return tenant


def _conversation(
    tenant: Tenant,
    identifier: int,
    *,
    status: Status = Status.OPEN,
    last: datetime | None = None,
) -> Conversation:
    value = str(identifier)
    return Conversation(
        id=UUID(int=identifier),
        tenant_id=tenant.id,
        channel="email",
        account_scope="support",
        contact=f"{value}@example.net",
        thread_key=f"thread-{value}",
        status=status.value,
        subject=value,
        last_message_at=last,
    )


def _message(
    tenant: Tenant, conversation: Conversation, identifier: int, occurred: datetime
) -> Message:
    return Message(
        id=UUID(int=identifier),
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        channel="email",
        direction=Direction.INBOUND.value,
        message_key=f"message-{identifier}",
        body=str(identifier),
        occurred_at=occurred,
    )


def test_get_and_list_are_tenant_scoped_and_missing_is_not_found(db: Session) -> None:
    one = _tenant(db, "one")
    two = _tenant(db, "two")
    row = _conversation(one, 1)
    db.add_all([row, _conversation(two, 2)])
    db.flush()

    assert (
        get_conversation(db, tenant_id=one.id, conversation_id=row.id).tenant_id
        == one.id
    )
    assert list_conversations(db, tenant_id=one.id).items[0].id == row.id
    with pytest.raises(ConversationNotFound):
        get_conversation(db, tenant_id=two.id, conversation_id=row.id)
    with pytest.raises(ConversationNotFound):
        list_messages(db, tenant_id=two.id, conversation_id=row.id)


def test_conversation_filters_are_closed_and_composable(db: Session) -> None:
    tenant = _tenant(db, "filters")
    db.add_all(
        [
            _conversation(tenant, 1, status=Status.OPEN),
            _conversation(tenant, 2, status=Status.RESOLVED),
        ]
    )
    db.flush()
    result = list_conversations(
        db,
        tenant_id=tenant.id,
        filters=ConversationFilter(
            statuses=(Status.RESOLVED,), channel="email", account_scope="support"
        ),
    )
    assert [item.status for item in result.items] == [Status.RESOLVED]
    with pytest.raises(ValueError):
        ConversationFilter(statuses=(Status.OPEN, Status.OPEN))


def test_conversation_keyset_order_has_uuid_tie_break_and_no_duplicates(
    db: Session,
) -> None:
    tenant = _tenant(db, "ordering")
    moment = datetime(2026, 9, 1, tzinfo=UTC)
    rows = [_conversation(tenant, number, last=moment) for number in (1, 2, 3)]
    db.add_all(rows)
    db.flush()
    first = list_conversations(db, tenant_id=tenant.id, limit=2)
    second = list_conversations(
        db, tenant_id=tenant.id, cursor=first.next_cursor, limit=2
    )
    assert first.items[0].id > first.items[1].id
    assert {item.id for item in first.items} | {item.id for item in second.items} == {
        UUID(int=1),
        UUID(int=2),
        UUID(int=3),
    }
    assert not ({item.id for item in first.items} & {item.id for item in second.items})


def test_conversation_keyset_keeps_null_timestamps_reachable(db: Session) -> None:
    tenant = _tenant(db, "null-order")
    moment = datetime(2026, 9, 1, tzinfo=UTC)
    db.add_all(
        [
            _conversation(tenant, 1, last=moment),
            _conversation(tenant, 2),
            _conversation(tenant, 3),
        ]
    )
    db.flush()
    first = list_conversations(db, tenant_id=tenant.id, limit=1)
    second = list_conversations(
        db, tenant_id=tenant.id, cursor=first.next_cursor, limit=1
    )
    third = list_conversations(
        db, tenant_id=tenant.id, cursor=second.next_cursor, limit=1
    )
    assert [item.id for item in first.items + second.items + third.items] == [
        UUID(int=1),
        UUID(int=3),
        UUID(int=2),
    ]


def test_message_timeline_is_ascending_with_equal_timestamp_ties(db: Session) -> None:
    tenant = _tenant(db, "timeline")
    conversation = _conversation(tenant, 1)
    moment = datetime(2026, 9, 1, tzinfo=UTC)
    db.add(conversation)
    db.add_all(
        [
            _message(tenant, conversation, number, moment + timedelta(seconds=number))
            for number in (3, 1, 2)
        ]
    )
    db.flush()
    first = list_messages(
        db, tenant_id=tenant.id, conversation_id=conversation.id, limit=2
    )
    second = list_messages(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        cursor=first.next_cursor,
        limit=2,
    )
    assert [item.body for item in first.items + second.items] == ["1", "2", "3"]


def test_cursors_are_opaque_and_bound_to_their_read_kind(db: Session) -> None:
    tenant = _tenant(db, "cursor-kind")
    conversation = _conversation(tenant, 1)
    db.add(conversation)
    db.add(_message(tenant, conversation, 2, datetime(2026, 9, 1, tzinfo=UTC)))
    db.add(_message(tenant, conversation, 3, datetime(2026, 9, 2, tzinfo=UTC)))
    db.flush()
    cursor = list_messages(
        db, tenant_id=tenant.id, conversation_id=conversation.id, limit=1
    ).next_cursor
    assert cursor is not None
    with pytest.raises(ValueError):
        list_conversations(db, tenant_id=tenant.id, cursor=cursor)
    with pytest.raises(ValueError):
        list_messages(
            db,
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            cursor=cursor[:-1] + "!",
        )
    other = _conversation(tenant, 4)
    db.add(other)
    db.flush()
    with pytest.raises(ValueError):
        list_messages(db, tenant_id=tenant.id, conversation_id=other.id, cursor=cursor)


def test_conversation_cursor_is_bound_to_tenant_and_filters(db: Session) -> None:
    one = _tenant(db, "cursor-one")
    two = _tenant(db, "cursor-two")
    db.add_all(
        [
            _conversation(one, 1, last=datetime(2026, 9, 1, tzinfo=UTC)),
            _conversation(one, 3),
            _conversation(two, 2),
        ]
    )
    db.flush()
    cursor = list_conversations(db, tenant_id=one.id, limit=1).next_cursor
    assert cursor is not None
    with pytest.raises(ValueError):
        list_conversations(db, tenant_id=two.id, cursor=cursor)
    with pytest.raises(ValueError):
        list_conversations(
            db,
            tenant_id=one.id,
            filters=ConversationFilter(statuses=(Status.RESOLVED,)),
            cursor=cursor,
        )


@pytest.mark.parametrize("limit", [0, -1, 101, True])
def test_limits_and_cursors_are_validated(db: Session, limit: int) -> None:
    tenant = _tenant(db, f"bounds-{uuid4().hex}")
    conversation = _conversation(tenant, 1)
    db.add(conversation)
    db.flush()
    with pytest.raises(ValueError):
        list_conversations(db, tenant_id=tenant.id, limit=limit)
    with pytest.raises(ValueError):
        list_conversations(db, tenant_id=tenant.id, cursor="not-a-cursor")

    with pytest.raises(ValueError):
        list_messages(
            db,
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            cursor="eyJ2IjoxLCJraW5kIjoibWVzc2FnZXMiLCJhdCI6bnVsbCwiaWQiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAifQ",
        )


def test_empty_pages_are_valid(db: Session) -> None:
    tenant = _tenant(db, "empty")
    assert list_conversations(db, tenant_id=tenant.id).items == ()
    assert list_conversations(db, tenant_id=tenant.id).next_cursor is None
    conversation = _conversation(tenant, 1)
    db.add(conversation)
    db.flush()
    assert list_messages(
        db, tenant_id=tenant.id, conversation_id=conversation.id
    ) == MessagePage((), None)
