"""Threading and deduplication — the two rules that read the channel traits.

These are the tests that would have caught the defects the audit found in both
source products, so each one names the defect it stands for.
"""

from __future__ import annotations

import pytest
from dotmac_inbox import (
    AddressForm,
    ChannelSpec,
    InboundIdentity,
    MessageIdScope,
    ThreadIdentity,
    Transport,
    dedup_key,
    register_channels,
    reset_channel_registry_for_tests,
    thread_key,
)

# The three trait combinations that actually occur across Sub's ten channels and
# CRM's six. Between them they cover every branch in both rules.
EMAIL = ChannelSpec(
    code="email",
    owner="test_product",
    address_form=AddressForm.EMAIL,
    transport=Transport.EXTERNAL,
    thread_identity=ThreadIdentity.PROVIDER,
    message_id_scope=MessageIdScope.GLOBAL,
)
WHATSAPP = ChannelSpec(
    code="whatsapp",
    owner="test_product",
    address_form=AddressForm.PHONE,
    transport=Transport.EXTERNAL,
    thread_identity=ThreadIdentity.DERIVED,
    message_id_scope=MessageIdScope.ACCOUNT,
)
FIELD_JOB = ChannelSpec(
    code="field_job",
    owner="test_product",
    address_form=AddressForm.OPAQUE,
    transport=Transport.INTERNAL,
    thread_identity=ThreadIdentity.DERIVED,
    message_id_scope=MessageIdScope.NONE,
)
SUPPLIED = ChannelSpec(
    code="work_event",
    owner="test_product",
    address_form=AddressForm.OPAQUE,
    transport=Transport.INTERNAL,
    thread_identity=ThreadIdentity.SUPPLIED,
    message_id_scope=MessageIdScope.SUPPLIED,
)


@pytest.fixture(autouse=True)
def _registry():
    reset_channel_registry_for_tests()
    register_channels([EMAIL, WHATSAPP, FIELD_JOB, SUPPLIED])
    yield
    reset_channel_registry_for_tests()


# ── Threading ────────────────────────────────────────────────────────────────


def test_a_provider_thread_channel_threads_on_the_provider_id() -> None:
    key = thread_key(
        InboundIdentity(
            channel="email",
            account_scope="support@example.net",
            contact="customer@example.com",
            external_thread_id="<thread-abc@mail>",
        )
    )
    assert key == "email:support@example.net:t:<thread-abc@mail>"


def test_a_provider_thread_channel_falls_back_when_the_id_is_missing() -> None:
    """The first message of a thread, and malformed email headers, both arrive
    with no thread id. Returning None here would push a nullable through every
    caller for a case with an obviously correct answer."""
    key = thread_key(
        InboundIdentity(
            channel="email",
            account_scope="support@example.net",
            contact="customer@example.com",
        )
    )
    assert key == "email:support@example.net:c:customer@example.com"


def test_a_derived_thread_channel_ignores_any_provider_thread_id() -> None:
    """Declaring DERIVED is a statement that the provider's thread id, if any,
    is not to be trusted for threading."""
    key = thread_key(
        InboundIdentity(
            channel="whatsapp",
            account_scope="+2348000000000",
            contact="+2348111111111",
            external_thread_id="ignored",
        )
    )
    assert key == "whatsapp:+2348000000000:c:+2348111111111"


def test_two_connected_accounts_talking_to_one_contact_stay_two_threads() -> None:
    """The reason `account_scope` is in the key on EVERY channel: merging them
    would expose one team's thread to another."""
    first = thread_key(
        InboundIdentity(channel="whatsapp", account_scope="+234800", contact="+234811")
    )
    second = thread_key(
        InboundIdentity(channel="whatsapp", account_scope="+234900", contact="+234811")
    )
    assert first != second


def test_threading_without_an_account_scope_is_refused() -> None:
    with pytest.raises(ValueError, match="account_scope is required"):
        thread_key(
            InboundIdentity(channel="email", account_scope="", contact="a@b.example")
        )


def test_threading_with_neither_a_thread_id_nor_a_contact_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be threaded"):
        thread_key(
            InboundIdentity(channel="email", account_scope="support@x", contact="")
        )


def test_a_supplied_thread_is_contact_independent_and_delimiter_safe() -> None:
    first = thread_key(
        InboundIdentity(
            channel="work_event",
            account_scope="dispatch:a",
            contact=None,
            supplied_thread_ref="work:42|north",
        )
    )
    second = thread_key(
        InboundIdentity(
            channel="work_event",
            account_scope="dispatch:a:work",
            contact=None,
            supplied_thread_ref="42|north",
        )
    )
    assert (
        first == "work_event:t1:"
        "1d3ff13060124a4fcc726e5a320cc8a2200a295f5db3a06990f12b53a9728c86"
    )
    assert first != second
    assert len(first) <= 512


@pytest.mark.parametrize("ref", [None, "", "  ", " " * 256, "x" * 256])
def test_a_supplied_thread_requires_a_bounded_nonblank_ref(ref: str | None) -> None:
    with pytest.raises(ValueError, match="supplied_thread_ref"):
        thread_key(
            InboundIdentity(
                channel="work_event",
                account_scope="dispatch",
                contact=None,
                supplied_thread_ref=ref,
            )
        )


def test_supplied_thread_refs_are_rejected_for_other_thread_traits() -> None:
    with pytest.raises(ValueError, match="valid only"):
        thread_key(
            InboundIdentity(
                channel="whatsapp",
                account_scope="+234800",
                contact="+234811",
                supplied_thread_ref="work-42",
            )
        )


@pytest.mark.parametrize("channel", ["email", "work_event"])
def test_an_oversized_account_scope_is_refused_before_key_generation(
    channel: str,
) -> None:
    identity = InboundIdentity(
        channel=channel,
        account_scope="a" * 161,
        contact=None if channel == "work_event" else "customer@example.net",
        supplied_thread_ref="work-42" if channel == "work_event" else None,
    )
    with pytest.raises(ValueError, match="account_scope exceeds 160"):
        thread_key(identity)
    with pytest.raises(ValueError, match="account_scope exceeds 160"):
        dedup_key(identity)


# ── Deduplication ────────────────────────────────────────────────────────────


def test_a_global_scope_id_dedupes_across_every_account() -> None:
    """An RFC 5322 Message-ID is generated to be globally unique."""
    at_first = dedup_key(
        InboundIdentity(
            channel="email",
            account_scope="support@example.net",
            contact="c@example.com",
            external_message_id="<msg-1@mail>",
        )
    )
    at_second = dedup_key(
        InboundIdentity(
            channel="email",
            account_scope="sales@example.net",
            contact="c@example.com",
            external_message_id="<msg-1@mail>",
        )
    )
    assert at_first == at_second
    assert at_first.derived is False


def test_an_account_scope_id_does_not_collide_across_accounts() -> None:
    """THE defect this module fixes. CRM's narrower partial unique index treats
    the same provider id at a second connected account as a duplicate and drops
    the message; a Messenger id is only meaningful within its page."""
    page_one = dedup_key(
        InboundIdentity(
            channel="whatsapp",
            account_scope="+234800",
            contact="+234811",
            external_message_id="wamid.SHARED",
        )
    )
    page_two = dedup_key(
        InboundIdentity(
            channel="whatsapp",
            account_scope="+234900",
            contact="+234811",
            external_message_id="wamid.SHARED",
        )
    )
    assert page_one != page_two
    assert page_one.derived is False


def test_the_same_id_at_the_same_account_is_a_duplicate() -> None:
    """The other half: at-least-once webhook delivery must still collapse."""

    def key() -> str:
        return dedup_key(
            InboundIdentity(
                channel="whatsapp",
                account_scope="+234800",
                contact="+234811",
                external_message_id="wamid.ABC",
            )
        ).value

    assert key() == key()


def test_a_channel_with_no_usable_id_falls_back_to_a_content_fingerprint() -> None:
    """Declared, not stumbled into — both products model this as 'the column
    happened to be NULL'."""
    result = dedup_key(
        InboundIdentity(
            channel="field_job",
            account_scope="job-42",
            contact="tech-7",
            body="on my way",
        )
    )
    assert result.derived is True
    assert result.value.startswith("field_job:h:")


def test_the_content_fingerprint_separates_different_messages() -> None:
    def key(body: str) -> str:
        return dedup_key(
            InboundIdentity(
                channel="field_job",
                account_scope="job-42",
                contact="tech-7",
                body=body,
            )
        ).value

    assert key("on my way") != key("running late")
    assert key("on my way") == key("on my way")


def test_a_supplied_message_ref_is_account_scoped_and_not_content_derived() -> None:
    first = dedup_key(
        InboundIdentity(
            channel="work_event",
            account_scope="dispatch:a",
            contact=None,
            supplied_thread_ref="work-42",
            supplied_message_ref="event:1|arrived",
            body="same",
        )
    )
    second = dedup_key(
        InboundIdentity(
            channel="work_event",
            account_scope="dispatch:a:event",
            contact=None,
            supplied_thread_ref="work-42",
            supplied_message_ref="1|arrived",
            body="different",
        )
    )
    assert (
        first.value == "work_event:s1:"
        "4756f1211833996af22e5bac55e74a543ca9108140b90b003aac3c4a5c03ef5a"
    )
    assert first != second
    assert first.derived is False


@pytest.mark.parametrize("ref", [None, "", "  ", " " * 256, "x" * 256])
def test_a_supplied_message_requires_a_bounded_nonblank_ref(ref: str | None) -> None:
    with pytest.raises(ValueError, match="supplied_message_ref"):
        dedup_key(
            InboundIdentity(
                channel="work_event",
                account_scope="dispatch",
                contact=None,
                supplied_thread_ref="work-42",
                supplied_message_ref=ref,
            )
        )


def test_supplied_message_refs_are_rejected_for_other_message_scopes() -> None:
    with pytest.raises(ValueError, match="valid only"):
        dedup_key(
            InboundIdentity(
                channel="email",
                account_scope="support@example.net",
                contact="customer@example.net",
                supplied_message_ref="local-1",
            )
        )


def test_a_missing_provider_id_degrades_to_a_fingerprint_rather_than_raising() -> None:
    """Providers do omit ids, and refusing the message loses it entirely. The
    result is flagged `derived` so the caller knows the match is weaker."""
    result = dedup_key(
        InboundIdentity(
            channel="email",
            account_scope="support@example.net",
            contact="c@example.com",
            subject="Hello",
            body="Is anyone there?",
        )
    )
    assert result.derived is True
    assert result.value.startswith("email:h:")


def test_the_fingerprint_does_not_collide_on_field_boundaries() -> None:
    """Joined with a separator, not concatenated: 'ab'+'c' must not equal
    'a'+'bc'. A naive join is a real, silent duplicate."""

    def key(subject: str, body: str) -> str:
        return dedup_key(
            InboundIdentity(
                channel="email",
                account_scope="s@x",
                contact="c@y",
                subject=subject,
                body=body,
            )
        ).value

    assert key("ab", "c") != key("a", "bc")
