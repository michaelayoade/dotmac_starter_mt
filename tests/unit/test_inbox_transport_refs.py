"""Transport-message correlation contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from dotmac_inbox import (
    AddressForm,
    ChannelSpec,
    ConversationConflict,
    Direction,
    ImportMessageTransportRef,
    MessageIdScope,
    ThreadIdentity,
    Transport,
    TransportMessageIdentity,
    TransportMessageIdScope,
    bind_message_transport_ref,
    create_conversation,
    find_message_by_transport_ref,
    import_message_transport_ref,
    record_message,
    register_channels,
    reset_channel_registry_for_tests,
)
from dotmac_inbox.models import Conversation, Message, MessageTransportRef
from dotmac_inbox.threading import InboundIdentity
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def db():
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
            MessageTransportRef.__table__,
        ],
    )
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _message(db: Session, *, account: str = "acct-a", channel: str = "email"):
    tenant = Tenant(slug=f"t-{uuid4().hex[:8]}", name="Tenant")
    db.add(tenant)
    db.flush()
    identity = InboundIdentity(
        channel=channel,
        account_scope=account,
        contact="person@example.net",
        external_thread_id=f"thread-{account}",
        external_message_id="admission-1",
    )
    conversation = create_conversation(db, tenant_id=tenant.id, identity=identity)
    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=identity,
        direction=Direction.INBOUND,
        occurred_at=datetime(2026, 9, 5, tzinfo=UTC),
    )
    return tenant, conversation, message


@pytest.fixture(autouse=True)
def declarations():
    reset_channel_registry_for_tests()
    register_channels(
        [
            ChannelSpec(
                "email",
                "tests",
                AddressForm.EMAIL,
                Transport.EXTERNAL,
                ThreadIdentity.PROVIDER,
                MessageIdScope.GLOBAL,
            ),
            ChannelSpec(
                "internal",
                "tests",
                AddressForm.OPAQUE,
                Transport.INTERNAL,
                ThreadIdentity.SUPPLIED,
                MessageIdScope.SUPPLIED,
            ),
            ChannelSpec(
                "whatsapp",
                "tests",
                AddressForm.PHONE,
                Transport.EXTERNAL,
                ThreadIdentity.DERIVED,
                MessageIdScope.ACCOUNT,
            ),
        ]
    )
    yield
    reset_channel_registry_for_tests()


def test_global_key_is_framed_and_preserves_delimiters_and_unicode() -> None:
    left = TransportMessageIdentity("email", "a:b/é")
    right = TransportMessageIdentity("email", "a", account_scope=None)
    assert left.raw_ref == "a:b/é"
    assert left.key.startswith("tm1:")
    assert left.key != right.key


def test_account_scope_is_explicit_and_bounded() -> None:
    identity = TransportMessageIdentity(
        "whatsapp", "provider-id", account_scope="acct-1"
    )
    assert identity.scope is TransportMessageIdScope.ACCOUNT
    with pytest.raises(ValueError, match="account_scope"):
        TransportMessageIdentity("whatsapp", "provider-id")
    with pytest.raises(ValueError, match="255"):
        TransportMessageIdentity("email", "é" * 256)


def test_none_scope_refuses_binding_key_and_internal_mapping_is_none() -> None:
    identity = TransportMessageIdentity("internal", "local")
    assert identity.scope is TransportMessageIdScope.NONE
    with pytest.raises(ValueError, match="NONE"):
        _ = identity.key


def test_supplied_external_channel_requires_transport_scope() -> None:
    with pytest.raises(ValueError, match="explicitly declare"):
        ChannelSpec(
            "supplied",
            "tests",
            AddressForm.OPAQUE,
            Transport.EXTERNAL,
            ThreadIdentity.SUPPLIED,
            MessageIdScope.SUPPLIED,
        )


def test_positional_label_compatibility_is_preserved() -> None:
    spec = ChannelSpec(
        "email2",
        "tests",
        AddressForm.EMAIL,
        Transport.EXTERNAL,
        ThreadIdentity.PROVIDER,
        MessageIdScope.GLOBAL,
        "legacy label",
    )
    assert spec.label == "legacy label"


def test_transport_table_forbids_none_and_has_tenant_message_identity() -> None:
    checks = {
        c.name: str(c.sqltext)
        for c in MessageTransportRef.__table__.constraints
        if hasattr(c, "sqltext")
    }
    assert "scope IN ('global', 'account')" in checks["ck_message_transport_refs_scope"]
    assert "uq_messages_tenant_id_id" in {c.name for c in Message.__table__.constraints}


def test_ib0003_migration_declares_ordered_fk_rls_and_append_only_contract():
    source = (
        Path(__file__).parents[2]
        / "packages/dotmac-inbox/src/dotmac_inbox/migrations/versions/"
        "ib_0003_transport_refs.py"
    ).read_text()
    assert source.index("create_unique_constraint") < source.index("create_table")
    assert "scope IN ('global', 'account')" in source
    assert "CREATE TRIGGER message_transport_refs_append_only" in source
    assert (
        "GRANT SELECT, INSERT ON mod_inbox.message_transport_refs TO app_user" in source
    )
    assert "cannot downgrade inbox transport refs while evidence exists" in source


def test_bind_supports_aliases_and_exact_replay_without_activity_change(db: Session):
    tenant, conversation, message = _message(db)
    first_activity = conversation.last_message_at
    one = TransportMessageIdentity("email", "receipt-1")
    two = TransportMessageIdentity("email", "receipt-2")
    first = bind_message_transport_ref(
        db, tenant_id=tenant.id, message_id=message.id, transport_identity=one
    )
    assert (
        bind_message_transport_ref(
            db, tenant_id=tenant.id, message_id=message.id, transport_identity=one
        ).id
        == first.id
    )
    bind_message_transport_ref(
        db, tenant_id=tenant.id, message_id=message.id, transport_identity=two
    )
    lookup = find_message_by_transport_ref(
        db, tenant_id=tenant.id, transport_identity=two
    )
    assert lookup is not None
    assert lookup.id == message.id
    assert conversation.last_message_at == first_activity
    assert message.transport_message_ref == "admission-1"


def test_lookup_is_isolated_by_tenant_even_for_the_same_provider_reference(
    db: Session,
) -> None:
    first_tenant, _, first_message = _message(db, account="acct-first")
    second_tenant, _, second_message = _message(db, account="acct-second")
    identity = TransportMessageIdentity("email", "shared-provider-reference")
    bind_message_transport_ref(
        db,
        tenant_id=first_tenant.id,
        message_id=first_message.id,
        transport_identity=identity,
    )
    bind_message_transport_ref(
        db,
        tenant_id=second_tenant.id,
        message_id=second_message.id,
        transport_identity=identity,
    )

    first_lookup = find_message_by_transport_ref(
        db, tenant_id=first_tenant.id, transport_identity=identity
    )
    second_lookup = find_message_by_transport_ref(
        db, tenant_id=second_tenant.id, transport_identity=identity
    )
    assert first_lookup is not None
    assert first_lookup.id == first_message.id
    assert second_lookup is not None
    assert second_lookup.id == second_message.id


def test_bind_refuses_wrong_message_channel_account_and_collision(db: Session):
    tenant, conversation, message = _message(db)
    other_tenant, other_conversation, other = _message(
        db, account="acct-b", channel="whatsapp"
    )
    with pytest.raises(ConversationConflict):
        bind_message_transport_ref(
            db,
            tenant_id=tenant.id,
            message_id=message.id,
            transport_identity=TransportMessageIdentity(
                "whatsapp", "x", account_scope="acct-a"
            ),
        )
    with pytest.raises(ConversationConflict):
        bind_message_transport_ref(
            db,
            tenant_id=tenant.id,
            message_id=message.id,
            transport_identity=TransportMessageIdentity(
                "whatsapp", "x", account_scope="acct-b"
            ),
        )
    with pytest.raises(ConversationConflict):
        bind_message_transport_ref(
            db,
            tenant_id=other_tenant.id,
            message_id=other.id,
            transport_identity=TransportMessageIdentity(
                "whatsapp", "x", account_scope="acct-a"
            ),
        )
    bind_message_transport_ref(
        db,
        tenant_id=tenant.id,
        message_id=message.id,
        transport_identity=TransportMessageIdentity("email", "collision"),
    )
    second = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=InboundIdentity(
            channel="email",
            account_scope="acct-a",
            contact="person@example.net",
            external_thread_id="thread-acct-a",
            external_message_id="admission-2",
        ),
        direction=Direction.INBOUND,
        occurred_at=datetime(2026, 9, 5, 0, 1, tzinfo=UTC),
    )
    with pytest.raises(ConversationConflict):
        bind_message_transport_ref(
            db,
            tenant_id=tenant.id,
            message_id=second.id,
            transport_identity=TransportMessageIdentity("email", "collision"),
        )
    assert other_conversation.account_scope == "acct-b"


def test_lookup_fails_closed_on_material_corruption_and_none_refuses(db: Session):
    tenant, _, message = _message(db)
    identity = TransportMessageIdentity("email", "corrupt-me")
    bind_message_transport_ref(
        db, tenant_id=tenant.id, message_id=message.id, transport_identity=identity
    )
    row = db.scalar(
        select(MessageTransportRef).where(MessageTransportRef.message_id == message.id)
    )
    assert row is not None
    row.raw_ref = "different"
    with pytest.raises(ConversationConflict):
        find_message_by_transport_ref(
            db, tenant_id=tenant.id, transport_identity=identity
        )
    with pytest.raises(ValueError, match="NONE"):
        bind_message_transport_ref(
            db,
            tenant_id=tenant.id,
            message_id=message.id,
            transport_identity=TransportMessageIdentity("internal", "none"),
        )


def test_historical_transport_ref_import_replays_and_preserves_message(db: Session):
    tenant, _, message = _message(db)
    created = datetime(2026, 9, 5, tzinfo=UTC)
    command = ImportMessageTransportRef(
        id=uuid4(),
        message_id=message.id,
        identity=TransportMessageIdentity("email", "historical"),
        created_at=created,
        updated_at=created,
    )
    imported = import_message_transport_ref(db, tenant_id=tenant.id, command=command)
    assert (
        import_message_transport_ref(db, tenant_id=tenant.id, command=command).id
        == imported.id
    )
    assert message.message_key == "email:m:admission-1"
    with pytest.raises(ConversationConflict):
        import_message_transport_ref(
            db,
            tenant_id=tenant.id,
            command=ImportMessageTransportRef(
                id=command.id,
                message_id=message.id,
                identity=TransportMessageIdentity("email", "different"),
                created_at=created,
                updated_at=created,
            ),
        )
    with pytest.raises(ConversationConflict, match="reused differently"):
        import_message_transport_ref(
            db,
            tenant_id=tenant.id,
            command=ImportMessageTransportRef(
                id=uuid4(),
                message_id=message.id,
                identity=command.identity,
                created_at=created,
                updated_at=created,
            ),
        )
