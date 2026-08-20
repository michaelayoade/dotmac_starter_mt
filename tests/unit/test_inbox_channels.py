"""Conversation-channel declaration behavior."""

from __future__ import annotations

import pytest
from dotmac_inbox import (
    AddressForm,
    ChannelSpec,
    MessageIdScope,
    ThreadIdentity,
    Transport,
    UnknownChannelError,
    channel_spec,
    register_channels,
    registered_channels,
    reset_channel_registry_for_tests,
)


def _email(**overrides: object) -> ChannelSpec:
    values = {
        "code": "email",
        "owner": "test_product",
        "address_form": AddressForm.EMAIL,
        "transport": Transport.EXTERNAL,
        "thread_identity": ThreadIdentity.PROVIDER,
        "message_id_scope": MessageIdScope.GLOBAL,
    }
    values.update(overrides)
    return ChannelSpec(**values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_channel_registry_for_tests()
    yield
    reset_channel_registry_for_tests()


def test_a_declared_channel_is_retrievable_with_its_traits() -> None:
    register_channels([_email()])
    spec = channel_spec("email")
    assert spec.thread_identity is ThreadIdentity.PROVIDER
    assert spec.message_id_scope is MessageIdScope.GLOBAL


def test_an_undeclared_channel_is_refused_and_the_error_teaches() -> None:
    register_channels([_email()])
    with pytest.raises(UnknownChannelError) as exc:
        channel_spec("whatsapp")
    assert "email" in str(exc.value)
    assert "register_channels" in str(exc.value)


def test_identical_redeclaration_is_idempotent_but_conflict_is_refused() -> None:
    register_channels([_email()])
    register_channels([_email()])
    assert registered_channels() == (_email(),)
    with pytest.raises(ValueError, match="already declared"):
        register_channels([_email(message_id_scope=MessageIdScope.ACCOUNT)])


def test_internal_transport_cannot_claim_provider_identity() -> None:
    with pytest.raises(ValueError, match="provider thread"):
        _email(
            code="note",
            transport=Transport.INTERNAL,
            thread_identity=ThreadIdentity.PROVIDER,
            message_id_scope=MessageIdScope.NONE,
        )
    with pytest.raises(ValueError, match="message_id_scope"):
        _email(
            code="note",
            transport=Transport.INTERNAL,
            thread_identity=ThreadIdentity.DERIVED,
            message_id_scope=MessageIdScope.ACCOUNT,
        )


@pytest.mark.parametrize("bad", ["", "  ", "Email", "email ", "x" * 41])
def test_channel_codes_fit_the_stored_open_vocabulary(bad: str) -> None:
    with pytest.raises(ValueError):
        _email(code=bad)
