"""A declared mode is a promise the kit verifies, not a string.

`ConnectorMode` shipped in SPI 1.0 with three members and nothing consulting it.
The consequences ran from "cannot work" to "works wrongly":

* `INGRESS` had no executable protocol at all, so a webhook connector could be
  declared and never run;
* `POLL` likewise — a label with no machinery behind it;
* `DELIVERY` was invoked WITHOUT checking the declaration, so a binding pointed
  at an ingress-only connector reached `handler_for` and failed with an
  AttributeError from inside a lookup, which reads as a broken plugin rather
  than as a misconfigured binding.

These tests hold the fix in place: a base protocol carrying identity and
metadata, one executable protocol per mode, and an implication checked in BOTH
directions.
"""

from __future__ import annotations

import pytest
from dotmac_integration.conformance import (
    ConformanceFailure,
    assert_plugin_conforms,
    fake_plugin,
)
from dotmac_integration.spi import (
    MODE_PROTOCOLS,
    Acknowledgement,
    ConnectorMode,
    ConnectorPlugin,
    DeliveryPlugin,
    InboundEvent,
    IngressPlugin,
    IngressRequest,
    ModeNotDeclaredError,
    PollPlugin,
    require_mode,
)

# ── The shape of the split ──────────────────────────────────────────────────


def test_the_base_protocol_carries_no_data_movement() -> None:
    """`handler_for` is not on the base.

    A base demanding it forces an ingress-only connector to either lie or raise,
    and it is exactly what made `modes` decorative: if every plugin must supply
    a delivery handler, declaring `DELIVERY` says nothing.
    """
    base = {m for m in dir(ConnectorPlugin) if not m.startswith("_")}
    assert base == {"manifest", "historical_manifests", "modes", "validate_connection"}
    assert "handler_for" not in base


@pytest.mark.parametrize(
    ("mode", "protocol", "factory"),
    [
        (ConnectorMode.DELIVERY, DeliveryPlugin, "handler_for"),
        (ConnectorMode.INGRESS, IngressPlugin, "ingress_handler_for"),
        (ConnectorMode.POLL, PollPlugin, "poll_handler_for"),
    ],
)
def test_each_mode_has_exactly_one_executable_protocol(
    mode: ConnectorMode, protocol: type, factory: str
) -> None:
    contract = MODE_PROTOCOLS[mode]
    assert contract.protocol is protocol
    assert contract.factory == factory
    added = {m for m in dir(protocol) if not m.startswith("_")} - {
        m for m in dir(ConnectorPlugin) if not m.startswith("_")
    }
    assert added == {factory}, f"{protocol.__name__} should add exactly {factory!r}"


def test_every_mode_is_covered_by_the_contract_map() -> None:
    """A new mode cannot be added without deciding what makes it runnable.

    This is the omission that left `POLL` unimplementable: the enum grew and
    nothing forced a matching protocol.
    """
    assert set(MODE_PROTOCOLS) == set(ConnectorMode)


# ── The implication, both directions ────────────────────────────────────────


def test_a_declared_mode_must_be_implemented() -> None:
    """Direction 1, ISOLATED.

    The fake declares all three modes and implements delivery and ingress. Only
    `POLL` is unimplemented, and the opposite rule cannot fire because
    everything it implements is also declared — so a pass here would mean the
    declares-without-implementing rule genuinely caught it, not the other one.
    """
    plugin = fake_plugin(modes_=frozenset(ConnectorMode))
    with pytest.raises(ConformanceFailure) as excinfo:
        assert_plugin_conforms(plugin)
    message = str(excinfo.value)
    assert "declares mode 'poll'" in message
    assert "poll_handler_for" in message


def test_an_implemented_mode_must_be_declared() -> None:
    """Direction 2.

    Undeclared-but-implemented is not harmless: the runtime starts workers from
    `modes`, so a delivery handler nobody declared is simply never called, and
    the connector looks installed and inert.
    """
    plugin = fake_plugin(modes_=frozenset({ConnectorMode.INGRESS}))
    with pytest.raises(ConformanceFailure) as excinfo:
        assert_plugin_conforms(plugin)
    message = str(excinfo.value)
    assert "implements 'handler_for'" in message
    assert "does not declare mode 'delivery'" in message


def test_a_plugin_declaring_what_it_implements_conforms() -> None:
    """The positive case, so the two rules above cannot pass by rejecting
    everything."""
    plugin = fake_plugin()
    assert_plugin_conforms(plugin)
    assert plugin.modes == frozenset({ConnectorMode.INGRESS, ConnectorMode.DELIVERY})
    assert isinstance(plugin, DeliveryPlugin)
    assert isinstance(plugin, IngressPlugin)
    assert not isinstance(plugin, PollPlugin)


def test_a_connector_declaring_no_mode_is_still_refused() -> None:
    """Unchanged by the split, and asserted so the mode loop cannot swallow it —
    an empty `modes` makes both directions of the implication vacuously true."""
    with pytest.raises(ConformanceFailure, match="declares no modes"):
        assert_plugin_conforms(fake_plugin(modes_=frozenset()))


# ── The dispatch-time refusal ───────────────────────────────────────────────


def test_require_mode_refuses_an_undeclared_mode() -> None:
    plugin = fake_plugin(modes_=frozenset({ConnectorMode.INGRESS}))
    with pytest.raises(ModeNotDeclaredError) as excinfo:
        require_mode(plugin, ConnectorMode.DELIVERY)
    assert "does not declare mode 'delivery'" in str(excinfo.value)
    assert "['ingress']" in str(
        excinfo.value
    ), "the message must say what it DOES declare"


def test_require_mode_accepts_a_declared_and_implemented_mode() -> None:
    require_mode(fake_plugin(), ConnectorMode.DELIVERY)
    require_mode(fake_plugin(), ConnectorMode.INGRESS)


def test_the_refusal_names_the_connector() -> None:
    """An operator reading a log needs to know WHICH connector, not just that
    one of them was wrong."""
    plugin = fake_plugin(modes_=frozenset({ConnectorMode.INGRESS}))
    with pytest.raises(ModeNotDeclaredError, match="conformance_fake"):
        require_mode(plugin, ConnectorMode.DELIVERY)


# ── The ingress contract ────────────────────────────────────────────────────


def test_normalize_returns_a_tuple_of_events_and_an_acknowledgement() -> None:
    """One Meta POST batches `entry[].changes[].value.messages[]`.

    A single-event signature would silently drop every message after the first,
    and the loss would be invisible: the provider gets a 200 and never resends.

    SPI 1.2 pairs that tuple with the acknowledgement the provider should
    receive, built HERE — before anything is persisted — because this is the
    last connector code that runs on the delivery path.
    """
    events = (
        InboundEvent(provider_event_id="wamid.1", event_type="message", payload={}),
        InboundEvent(provider_event_id="wamid.2", event_type="message", payload={}),
    )
    ack = Acknowledgement(body=b"received")
    plugin = fake_plugin(inbound=events, acknowledgement=ack)
    handler = plugin.ingress_handler_for("conformance.echo.v1")

    got_events, got_ack = handler.normalize(IngressRequest(raw_body=b"{}"), config={})

    assert isinstance(got_events, tuple)
    assert len(got_events) == 2
    assert got_ack == ack


def test_all_three_hooks_receive_the_same_envelope_object() -> None:
    """Identity, not equality.

    What `verify` authenticated and what `normalize` interpreted must be the
    same bytes. Two equal copies would satisfy a value comparison while still
    letting an engine re-read or re-decode the body between the calls — and a
    signature check that guards a different byte string guards nothing.
    """
    plugin = fake_plugin()
    handler = plugin.ingress_handler_for("conformance.echo.v1")
    request = IngressRequest(
        raw_body=b'{"a":1}', headers={"h": "v"}, params={"challenge": "c"}
    )

    handler.challenge(request, config={}, secrets={})
    handler.verify(request, config={}, secrets={})
    handler.normalize(request, config={})

    assert len(plugin.requests_seen) == 3
    assert all(seen is request for seen in plugin.requests_seen)


def test_verify_receives_the_exact_bytes_it_was_given() -> None:
    """A re-serialized body invalidates any real HMAC.

    The fake records what it was handed so this is checkable without a signing
    secret — the engine's job is to pass the bytes through untouched, and that
    is what is asserted.
    """
    raw = b'{"entry":[{"changes":[]}]}  '  # trailing space is significant
    plugin = fake_plugin()
    handler = plugin.ingress_handler_for("conformance.echo.v1")
    handler.verify(
        IngressRequest(
            raw_body=raw, headers={"X-Hub-Signature-256": "sha256=deadbeef"}
        ),
        config={},
        secrets={},
    )
    assert plugin.verified == [raw]


def test_an_ingress_handler_can_reject() -> None:
    plugin = fake_plugin(signature_valid=False)
    handler = plugin.ingress_handler_for("conformance.echo.v1")
    assert (
        handler.verify(IngressRequest(raw_body=b"{}"), config={}, secrets={}) is False
    )


def test_challenge_returns_an_acknowledgement_or_none() -> None:
    """`None` means "not mine", and the engine turns it into a stated 400
    rather than falling through to the delivery path.

    The kit's fake reads a NEUTRAL `"challenge"` parameter. Which part of the
    request identifies a real handshake is the connector's business, and naming
    a particular provider's would breach ADR-0024 § 7 inside the module itself.
    """
    handler = fake_plugin().ingress_handler_for("conformance.echo.v1")
    assert handler.challenge(IngressRequest(), config={}, secrets={}) is None
    answered = handler.challenge(
        IngressRequest(params={"challenge": "abc"}), config={}, secrets={}
    )
    assert answered == Acknowledgement(body=b"abc")
    # The media type is left unset, so the ENGINE decides it. A connector that
    # picked one here would be choosing part of the response it was not granted.
    assert answered is not None and answered.media_type is None


def test_an_undeclared_capability_has_no_ingress_handler() -> None:
    """Same refusal the delivery factory already made. A typo must fail at
    binding time, not at the first webhook."""
    with pytest.raises(Exception):  # noqa: B017 - any refusal is correct here
        fake_plugin().ingress_handler_for("nope.not.declared.v1")
