"""E7 / T1: the framed side channel between the launcher and the target session.

The properties that matter are structural: a frame is closed, directional,
bounded and sequenced, and the protocol can never share a descriptor with
anything Foundation prints. Each refusal is asserted by its stable code.
"""

from __future__ import annotations

import json
import os
import pathlib
import struct
import subprocess
import sys
from collections.abc import Iterator

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lane3_session_channel import (  # noqa: E402
    KINDS,
    LAUNCHER_TO_TARGET,
    TARGET_TO_LAUNCHER,
    Channel,
    ChannelRefused,
    ChannelTimeout,
)


@pytest.fixture
def pair() -> Iterator[tuple[Channel, Channel, int, int]]:
    """A launcher end and a target end, plus raw descriptors to forge frames."""
    to_target_read, to_target_write = os.pipe()
    to_launcher_read, to_launcher_write = os.pipe()
    launcher = Channel(to_launcher_read, to_target_write, role="launcher")
    target = Channel(to_target_read, to_launcher_write, role="target")
    try:
        yield launcher, target, to_target_write, to_launcher_write
    finally:
        launcher.close()
        target.close()


def _frame(body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + body


def _abort_body(seq: int = 0, extra: str = "") -> bytes:
    body = f'{{"kind":"abort","seq":{seq},"data":{{"reason":"x"}}{extra}}}'
    return body.encode()


def test_the_vocabulary_is_partitioned_by_sender() -> None:
    assert LAUNCHER_TO_TARGET | TARGET_TO_LAUNCHER == KINDS
    assert not LAUNCHER_TO_TARGET & TARGET_TO_LAUNCHER
    assert {
        "start",
        "proceed",
        "external_results",
        "evidence_ciphertext",
        "abort",
        "compensation_probe_done",
    } == LAUNCHER_TO_TARGET
    assert "compensation_window_open" in TARGET_TO_LAUNCHER


def test_a_frame_round_trips_in_both_directions(pair) -> None:
    launcher, target, _, _ = pair
    launcher.send("abort", {"reason": "because"})
    assert target.receive("abort", timeout=2) == ("abort", {"reason": "because"})
    target.send("window_open", {"deadline_seconds": 5})
    assert launcher.receive("window_open", timeout=2) == (
        "window_open",
        {"deadline_seconds": 5},
    )


def test_sequence_numbers_advance_per_direction(pair) -> None:
    launcher, target, _, _ = pair
    launcher.send("abort", {"reason": "one"})
    launcher.send("abort", {"reason": "two"})
    assert target.receive("abort", timeout=2)[1]["reason"] == "one"
    assert target.receive("abort", timeout=2)[1]["reason"] == "two"


def test_a_replayed_or_skipped_sequence_refuses(pair) -> None:
    _, target, to_target_write, _ = pair
    os.write(to_target_write, _frame(_abort_body(seq=3)))
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert refusal.value.code == "frame-sequence"


def test_a_side_cannot_send_the_other_sides_kind(pair) -> None:
    launcher, target, _, _ = pair
    with pytest.raises(ChannelRefused) as refusal:
        target.send("abort", {"reason": "x"})
    assert refusal.value.code == "frame-kind-not-sendable"
    with pytest.raises(ChannelRefused) as refusal:
        launcher.send("window_open", {"deadline_seconds": 1})
    assert refusal.value.code == "frame-kind-not-sendable"


def test_data_keys_are_closed_on_send(pair) -> None:
    launcher, _, _, _ = pair
    for data in ({}, {"reason": "x", "extra": "y"}):
        with pytest.raises(ChannelRefused) as refusal:
            launcher.send("abort", data)
        assert refusal.value.code == "frame-data-keys"


def test_data_keys_are_closed_on_receive(pair) -> None:
    _, target, to_target_write, _ = pair
    body = b'{"kind":"abort","seq":0,"data":{"reason":"x","more":1}}'
    os.write(to_target_write, _frame(body))
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert refusal.value.code == "frame-data-keys"


def test_an_unexpected_kind_refuses(pair) -> None:
    launcher, target, _, _ = pair
    launcher.send("abort", {"reason": "x"})
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("external_results", timeout=2)
    assert refusal.value.code == "frame-kind-unexpected"


def test_a_receiver_cannot_ask_for_its_own_kinds(pair) -> None:
    _, target, _, _ = pair
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("window_open", timeout=1)
    assert refusal.value.code == "frame-kind-not-receivable"


def test_an_oversized_announced_length_refuses_before_reading(pair) -> None:
    _, target, to_target_write, _ = pair
    os.write(to_target_write, struct.pack(">I", 2**31))
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert refusal.value.code == "frame-length"


def test_a_duplicate_key_refuses(pair) -> None:
    _, target, to_target_write, _ = pair
    body = b'{"kind":"abort","kind":"abort","seq":0,"data":{"reason":"x"}}'
    os.write(to_target_write, _frame(body))
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert refusal.value.code == "frame-duplicate-key"


def test_a_non_finite_number_refuses(pair) -> None:
    _, target, to_target_write, _ = pair
    body = b'{"kind":"abort","seq":NaN,"data":{"reason":"x"}}'
    os.write(to_target_write, _frame(body))
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert refusal.value.code == "frame-non-finite-number"


def test_an_extra_top_level_key_refuses(pair) -> None:
    _, target, to_target_write, _ = pair
    os.write(to_target_write, _frame(_abort_body(extra=',"extra":1')))
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert refusal.value.code == "frame-shape"


def test_non_json_refuses(pair) -> None:
    _, target, to_target_write, _ = pair
    os.write(to_target_write, _frame(b"not json at all"))
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert refusal.value.code == "frame-not-json"


def test_silence_times_out(pair) -> None:
    _, target, _, _ = pair
    with pytest.raises(ChannelTimeout) as refusal:
        target.receive("abort", timeout=0.05)
    assert refusal.value.code == "channel-timeout"


def test_a_closed_peer_refuses(pair) -> None:
    launcher, target, _, _ = pair
    launcher.close()
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert refusal.value.code == "channel-closed"


def test_refusals_never_carry_the_payload(pair) -> None:
    _, target, to_target_write, _ = pair
    body = b'{"kind":"abort","seq":0,"data":{"reason":"SENTINEL-SECRET","x":1}}'
    os.write(to_target_write, _frame(body))
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert "SENTINEL-SECRET" not in str(refusal.value)


def test_foundation_style_annotations_cannot_reach_the_protocol() -> None:
    """`from_stdio` moves the channel off stdout, then stdout goes to stderr.

    A child that prints an annotation after taking the channel must leave the
    stdout stream holding exactly its frames and the annotation on stderr.
    """
    code = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from lane3_session_channel import Channel\n"
        "channel = Channel.from_stdio()\n"
        "print('::notice::an annotation')\n"
        "channel.send('window_open', {'deadline_seconds': 5})\n"
        "channel.close()\n"
    )
    completed = subprocess.run(  # noqa: S603 # nosec B603 - fixed argv, no shell
        [sys.executable, "-I", "-c", code, str(SCRIPTS)],
        input=b"",
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    (length,) = struct.unpack(">I", completed.stdout[:4])
    assert len(completed.stdout) == 4 + length
    document = json.loads(completed.stdout[4:])
    assert document == {
        "kind": "window_open",
        "seq": 0,
        "data": {"deadline_seconds": 5},
    }
    assert b"::notice::" not in completed.stdout
    assert b"::notice::an annotation" in completed.stderr


def test_a_number_that_parses_to_infinity_refuses(pair) -> None:
    """`1e999` is not a NaN/Infinity literal, so `parse_constant` never sees it."""
    _, target, to_target_write, _ = pair
    body = b'{"kind":"abort","seq":0,"data":{"reason":1e999}}'
    os.write(to_target_write, _frame(body))
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=2)
    assert refusal.value.code == "frame-non-finite-number"


def test_a_timeout_in_the_middle_of_a_frame_kills_the_channel(pair) -> None:
    launcher, target, to_target_write, _ = pair
    os.write(to_target_write, struct.pack(">I", 100) + b"0123456789")
    with pytest.raises(ChannelTimeout):
        target.receive("abort", timeout=0.1)
    # The stream is now out of step with the framing: nothing may be read again,
    # not even a perfectly good frame that follows.
    launcher.send("abort", {"reason": "late"})
    with pytest.raises(ChannelRefused) as refusal:
        target.receive("abort", timeout=1)
    assert refusal.value.code == "channel-dead"


def test_a_timeout_with_nothing_consumed_leaves_the_channel_usable(pair) -> None:
    launcher, target, _, _ = pair
    with pytest.raises(ChannelTimeout):
        target.receive("abort", timeout=0.05)
    launcher.send("abort", {"reason": "after"})
    assert target.receive("abort", timeout=2)[1]["reason"] == "after"


def test_the_proceed_frame_carries_exactly_the_plan_digest(pair) -> None:
    launcher, target, _, _ = pair
    with pytest.raises(ChannelRefused) as refusal:
        launcher.send("proceed", {})
    assert refusal.value.code == "frame-data-keys"
    launcher.send("proceed", {"plan_digest": "sha256:x"})
    assert target.receive("proceed", timeout=2)[1] == {"plan_digest": "sha256:x"}


def test_the_ready_frame_must_carry_the_hosts_incarnation(pair) -> None:
    launcher, target, _, _ = pair
    base = {"host_id": "h", "machine_id": "m", "plan_digest": "p"}
    with pytest.raises(ChannelRefused) as refusal:
        target.send("ready", base)
    assert refusal.value.code == "frame-data-keys"
    target.send("ready", {**base, "host_incarnation": "sha256:" + "0" * 64})
    assert launcher.receive("ready", timeout=2)[1]["host_incarnation"].startswith(
        "sha256:"
    )


def test_the_compensation_window_frames_are_closed_and_directional(pair) -> None:
    launcher, target, _, _ = pair
    target.send(
        "compensation_window_open",
        {"deadline_seconds": 5, "exposure_calls_b64": "e30="},
    )
    kind, data = launcher.receive("compensation_window_open", timeout=2)
    assert (kind, data["deadline_seconds"]) == ("compensation_window_open", 5)
    launcher.send("compensation_probe_done", {})
    assert target.receive("compensation_probe_done", timeout=2) == (
        "compensation_probe_done",
        {},
    )
    with pytest.raises(ChannelRefused) as extra:
        launcher.send("compensation_probe_done", {"probe": "bytes stay here"})
    assert extra.value.code == "frame-data-keys"
    with pytest.raises(ChannelRefused) as wrong_sender:
        target.send("compensation_probe_done", {})
    assert wrong_sender.value.code == "frame-kind-not-sendable"
    with pytest.raises(ChannelRefused) as missing:
        target.send("compensation_window_open", {"deadline_seconds": 5})
    assert missing.value.code == "frame-data-keys"


def test_the_start_frame_must_carry_the_role(pair) -> None:
    launcher, _, _, _ = pair
    with pytest.raises(ChannelRefused) as caught:
        launcher.send("start", {})
    assert caught.value.code == "frame-data-keys"
