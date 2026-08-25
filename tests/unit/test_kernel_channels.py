"""The kernel channel registry — behaviour.

Structure is guarded in `tests/architecture/test_inbox_module.py`; this file is
what the registry DOES.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.channels import (
    AddressForm,
    ChannelSpec,
    MessageIdScope,
    ThreadIdentity,
    Transport,
    UnknownChannelError,
    channel_spec,
    register_channels,
    registered_channels,
    reset_registry_for_tests,
)


def _email(**overrides: object) -> ChannelSpec:
    base = {
        "code": "email",
        "owner": "test_product",
        "address_form": AddressForm.EMAIL,
        "transport": Transport.EXTERNAL,
        "thread_identity": ThreadIdentity.PROVIDER,
        "message_id_scope": MessageIdScope.GLOBAL,
    }
    base.update(overrides)
    return ChannelSpec(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


def test_a_declared_channel_is_retrievable_with_its_traits() -> None:
    register_channels([_email()])
    spec = channel_spec("email")
    assert spec.thread_identity is ThreadIdentity.PROVIDER
    assert spec.message_id_scope is MessageIdScope.GLOBAL


def test_an_undeclared_channel_raises_rather_than_defaulting() -> None:
    """A silently-defaulted channel would inherit some other channel's dedup and
    threading rules — the class of bug only found when two customers' messages
    merge into one conversation."""
    register_channels([_email()])
    with pytest.raises(UnknownChannelError) as excinfo:
        channel_spec("whatsapp")
    message = str(excinfo.value)
    # The error must name what IS declared, or the first thing a developer does
    # is go looking for the registry.
    assert "email" in message
    assert "register_channels" in message


def test_redeclaring_a_channel_identically_is_idempotent() -> None:
    """Import-time declaration runs more than once under test collection."""
    register_channels([_email()])
    register_channels([_email()])
    assert len(registered_channels()) == 1


def test_redeclaring_a_channel_with_different_traits_is_a_conflict() -> None:
    """Two modules disagreeing about whether an address is a phone number is a real
    conflict; last-writer-wins would resolve it silently and arbitrarily."""
    register_channels([_email()])
    with pytest.raises(ValueError, match="already declared"):
        register_channels([_email(address_form=AddressForm.OPAQUE)])


def test_registered_channels_is_ordered_for_stable_output() -> None:
    register_channels(
        [
            _email(code="whatsapp", thread_identity=ThreadIdentity.DERIVED),
            _email(),
            _email(code="chat_widget", thread_identity=ThreadIdentity.DERIVED),
        ]
    )
    assert [s.code for s in registered_channels()] == [
        "chat_widget",
        "email",
        "whatsapp",
    ]


# ── The declarations that cannot be satisfied ────────────────────────────────


def test_an_internal_channel_cannot_claim_provider_thread_identity() -> None:
    """Sub's `field_job` is the worked example: 'no external transport: delivery
    is the shared conversation websocket'. There is no provider to supply a
    thread id, so claiming one is a declaration that never arrives."""
    with pytest.raises(ValueError, match="no provider to supply one"):
        _email(
            code="field_job",
            transport=Transport.INTERNAL,
            thread_identity=ThreadIdentity.PROVIDER,
            message_id_scope=MessageIdScope.NONE,
        )


def test_an_internal_channel_cannot_claim_a_provider_message_id_scope() -> None:
    with pytest.raises(ValueError, match="declare 'none'"):
        _email(
            code="note",
            transport=Transport.INTERNAL,
            thread_identity=ThreadIdentity.DERIVED,
            message_id_scope=MessageIdScope.GLOBAL,
        )


def test_an_internal_channel_with_coherent_traits_is_accepted() -> None:
    spec = _email(
        code="field_job",
        transport=Transport.INTERNAL,
        thread_identity=ThreadIdentity.DERIVED,
        message_id_scope=MessageIdScope.NONE,
        address_form=AddressForm.OPAQUE,
    )
    assert spec.code == "field_job"


# ── Code hygiene, because the code is a stored column ────────────────────────


@pytest.mark.parametrize("bad", ["", "  ", "Email", "email ", "EMAIL"])
def test_a_channel_code_must_be_lowercase_and_unpadded(bad: str) -> None:
    with pytest.raises(ValueError):
        _email(code=bad)


def test_a_channel_code_longer_than_the_column_is_rejected_up_front() -> None:
    """String(40) in three tables. Truncating instead would silently merge two
    channels."""
    with pytest.raises(ValueError, match="String\\(40\\)"):
        _email(code="x" * 41)


def test_a_channel_must_declare_an_owning_module() -> None:
    """A code with no owner cannot be attributed when it turns out to be wrong."""
    with pytest.raises(ValueError, match="owning module"):
        _email(owner="")
