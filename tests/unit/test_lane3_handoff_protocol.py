"""Strict wire-protocol canaries for the Lane 3 broker handoff.

Hosted CI only; synthetic values only (``*.example`` hosts, RFC 5737/3849
addresses). Each refusal canary is paired with a sensitivity proof: the same
input with only the guarded property restored is accepted, so a test cannot
pass merely because everything is refused.
"""

from __future__ import annotations

import copy
import pathlib
import socket
import struct
import sys
import threading
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lane3_handoff_protocol as hp

LEASE = "a" * 32
NONCE = "b" * 64
BOOT = "01234567-89ab-cdef-0123-456789abcdef"
SHA = "c" * 40
ORIGIN = "https://broker.example"


def report() -> dict[str, Any]:
    return {
        "protocol": hp.PROTOCOL,
        "op": "report",
        "lease_id": LEASE,
        "nonce": NONCE,
        "origin": ORIGIN,
        "flags": {"explicit_port_present": False, "userinfo_present": False},
        "expected": {
            "run_id": 7,
            "run_attempt": 1,
            "workflow_sha": SHA,
            "starter_commit": SHA,
        },
    }


def grant() -> dict[str, Any]:
    return {
        "protocol": hp.PROTOCOL,
        "state": "GRANTED",
        "lease_id": LEASE,
        "nonce": NONCE,
        "boot_id": BOOT,
        "sequence": 1,
        "expires_at_monotonic_ns": 1_000,
        "binding": {
            "repository_id": 1,
            "run_id": 7,
            "run_attempt": 1,
            "job_id": 9,
            "runner_id": 11,
            "workflow_sha": SHA,
            "workflow_blob": SHA,
            "starter_commit": SHA,
        },
        "origin": ORIGIN,
        "snapshot": {
            "digest": "d" * 64,
            "expires_at_monotonic_ns": 2_000,
            "port": 443,
            "addresses": [
                {"family": 4, "address": "192.0.2.10"},
                {"family": 6, "address": "2001:db8::10"},
            ],
        },
        "policy_digest": "e" * 64,
        "manifest_digest": "f" * 64,
        "controller_digest": "0" * 64,
    }


# ── decoding ────────────────────────────────────────────────────────────────


def test_a_canonical_message_round_trips() -> None:
    raw = hp.encode_message(report())
    assert hp.decode_message(raw) == report()
    assert hp.validate_request(hp.decode_message(raw)) == report()


@pytest.mark.parametrize(
    ("bad", "good"),
    [
        (b'{"a":1,"a":2}', b'{"a":1,"b":2}'),
        (b'{"a":NaN}', b'{"a":0}'),
        (b'{"a":Infinity}', b'{"a":1}'),
        (b'{"a":-Infinity}', b'{"a":-1}'),
        (b'{"a":1} ', b'{"a":1}'),
        (b'{"a":1}{}', b'{"a":1}'),
        (b' {"a":1}', b'{"a":1}'),
        (b'{"a":"\xff"}', b'{"a":"x"}'),
        (b'{"a":"\\ud800"}', b'{"a":"\\u00e9"}'),
        (b'[{"a":1}]', b'{"b":[{"a":1}]}'),
        (b'{"a":{"b":{"c":{"d":{}}}}}', b'{"a":{"b":{"c":{"d":1}}}}'),
        (b'{"a":[[[[1]]]]}', b'{"a":[[[1]]]}'),
    ],
)
def test_strict_decode_refuses_and_is_sensitive(bad: bytes, good: bytes) -> None:
    with pytest.raises(hp.ProtocolRefused) as caught:
        hp.decode_message(bad)
    assert caught.value.label == "frame.invalid"
    assert isinstance(hp.decode_message(good), dict)


def test_message_size_bound_is_exact() -> None:
    def sized(n: int) -> bytes:
        body = b'{"a":"' + b"x" * (n - 8) + b'"}'
        assert len(body) == n
        return body

    assert hp.decode_message(sized(hp.MAX_MESSAGE))
    with pytest.raises(hp.ProtocolRefused):
        hp.decode_message(sized(hp.MAX_MESSAGE + 1))
    with pytest.raises(hp.ProtocolRefused):
        hp.decode_message(b"")


def test_refusal_labels_are_closed() -> None:
    assert hp.ProtocolRefused("anything else").label == "internal"
    assert str(hp.ProtocolRefused("frame.invalid")) == "frame.invalid"
    assert "origin.not_admitted" in hp.CATEGORIES


# ── schemas ────────────────────────────────────────────────────────────────


def _mutations() -> list[tuple[str, Any]]:
    return [
        ("extra key", lambda r: r.update(extra=1)),
        ("missing key", lambda r: r.pop("nonce")),
        ("protocol", lambda r: r.update(protocol="dotmac.lane3.broker-handoff.v0")),
        ("op", lambda r: r.update(op="grant")),
        ("lease upper", lambda r: r.update(lease_id="A" * 32)),
        ("lease short", lambda r: r.update(lease_id="a" * 31)),
        ("nonce short", lambda r: r.update(nonce="b" * 63)),
        ("flag type", lambda r: r["flags"].update(userinfo_present=0)),
        ("flag extra", lambda r: r["flags"].update(other=False)),
        ("run bool", lambda r: r["expected"].update(run_id=True)),
        ("run zero", lambda r: r["expected"].update(run_id=0)),
        ("attempt str", lambda r: r["expected"].update(run_attempt="1")),
        ("sha upper", lambda r: r["expected"].update(workflow_sha="C" * 40)),
        ("expected extra", lambda r: r["expected"].update(url="x")),
        ("origin port", lambda r: r.update(origin=ORIGIN + ":443")),
        ("origin path", lambda r: r.update(origin=ORIGIN + "/")),
        ("origin user", lambda r: r.update(origin="https://u@broker.example")),
        ("origin case", lambda r: r.update(origin="https://Broker.example")),
        ("origin ip", lambda r: r.update(origin="https://192.0.2.1")),
        ("origin http", lambda r: r.update(origin="http://broker.example")),
    ]


@pytest.mark.parametrize(("name", "mutate"), _mutations())
def test_report_schema_refuses_each_mutation(name: str, mutate: Any) -> None:
    value = report()
    mutate(value)
    with pytest.raises(hp.ProtocolRefused):
        hp.validate_request(value)
    # Sensitivity: the unmutated report is accepted.
    assert hp.validate_request(report())


def test_poll_consume_and_response_schemas() -> None:
    poll = {"protocol": hp.PROTOCOL, "op": "poll", "lease_id": LEASE, "nonce": NONCE}
    assert hp.validate_request(dict(poll))
    with pytest.raises(hp.ProtocolRefused):
        hp.validate_request({**poll, "grant_digest": "0" * 64})
    consume = {**poll, "op": "consume", "grant_digest": "0" * 64}
    assert hp.validate_request(dict(consume))
    with pytest.raises(hp.ProtocolRefused):
        hp.validate_request({**consume, "grant_digest": "0" * 63})
    assert hp.response("WAIT")["category"] is None
    assert hp.response("REFUSED", "lease.expired")["category"] == "lease.expired"
    for status, category in (
        ("REFUSED", None),
        ("REFUSED", "free text"),
        ("WAIT", "lease.expired"),
        ("OK", None),
    ):
        with pytest.raises(hp.ProtocolRefused):
            hp.response(status, category)


def test_challenge_schema() -> None:
    challenge = {
        "protocol": hp.PROTOCOL,
        "lease_id": LEASE,
        "nonce": NONCE,
        "boot_id": BOOT,
        "expires_at_monotonic_ns": 5,
    }
    assert hp.validate_challenge(dict(challenge))
    for key, value in (
        ("boot_id", BOOT.upper()),
        ("expires_at_monotonic_ns", 5.0),
        ("nonce", NONCE[:-1]),
    ):
        with pytest.raises(hp.ProtocolRefused):
            hp.validate_challenge({**challenge, key: value})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda g: g.update(sequence=2),
        lambda g: g.update(state="CONSUMED"),
        lambda g: g["binding"].update(job_id=0),
        lambda g: g["binding"].pop("runner_id"),
        lambda g: g["snapshot"].update(port=8443),
        lambda g: g["snapshot"].update(addresses=[]),
        lambda g: g["snapshot"]["addresses"].append(
            {"family": 4, "address": "192.0.2.10"}
        ),
        lambda g: g["snapshot"]["addresses"].__setitem__(
            0, {"family": 6, "address": "192.0.2.10"}
        ),
        lambda g: g["snapshot"]["addresses"].__setitem__(
            1, {"family": 6, "address": "2001:DB8::10"}
        ),
        lambda g: g["snapshot"]["addresses"].__setitem__(
            1, {"family": 6, "address": "2001:db8:0::10"}
        ),
        lambda g: g["snapshot"]["addresses"].extend(
            {"family": 4, "address": f"198.51.100.{i}"} for i in range(31)
        ),
        lambda g: g.update(expires_at_monotonic_ns=3_000),
        lambda g: g.update(origin="https://broker.example."),
    ],
)
def test_grant_schema_refuses_and_is_sensitive(mutate: Any) -> None:
    value = grant()
    mutate(value)
    with pytest.raises(hp.ProtocolRefused):
        hp.validate_grant(value)
    assert hp.validate_grant(grant())


def test_grant_digest_is_canonical_and_whole_object() -> None:
    first = grant()
    reordered = dict(reversed(list(copy.deepcopy(first).items())))
    assert hp.grant_digest(first) == hp.grant_digest(reordered)
    changed = grant()
    changed["snapshot"]["addresses"][0]["address"] = "192.0.2.11"
    assert hp.grant_digest(changed) != hp.grant_digest(first)
    assert len(hp.grant_digest(first)) == 64


# ── framing over a real socket pair ────────────────────────────────────────


def test_frame_round_trip_over_socketpair() -> None:
    left, right = socket.socketpair()
    with left, right:
        hp.write_frame(left, report())
        assert hp.read_frame(right) == report()


def test_trailing_bytes_after_a_frame_refuse() -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(hp.frame(report()) + b"x")
        with pytest.raises(hp.ProtocolRefused):
            hp.read_frame(right)
    # Sensitivity: the same frame without the trailing byte is accepted.
    left, right = socket.socketpair()
    with left, right:
        left.sendall(hp.frame(report()))
        assert hp.read_frame(right)


def test_response_reader_requires_eof() -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(hp.frame(hp.response("WAIT")) + b"\x00")
        left.shutdown(socket.SHUT_WR)
        with pytest.raises(hp.ProtocolRefused):
            hp.read_frame(right, require_eof=True)
    left, right = socket.socketpair()
    with left, right:
        left.sendall(hp.frame(hp.response("WAIT")))
        left.shutdown(socket.SHUT_WR)
        assert hp.read_frame(right, require_eof=True)["status"] == "WAIT"


@pytest.mark.parametrize(
    "payload",
    [
        struct.pack(">I", hp.MAX_MESSAGE + 1),
        struct.pack(">I", 0),
        struct.pack(">I", 50) + b'{"a":',
        b"\x00\x00",
    ],
)
def test_oversized_zero_and_truncated_frames_refuse(payload: bytes) -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(payload)
        left.shutdown(socket.SHUT_WR)
        with pytest.raises(hp.ProtocolRefused):
            hp.read_frame(right, deadline_s=1.0)


def test_a_stalled_peer_hits_the_deadline() -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(struct.pack(">I", 20) + b'{"a":')  # then stall
        done: list[BaseException] = []

        def read() -> None:
            try:
                hp.read_frame(right, deadline_s=0.5)
            except BaseException as exc:
                done.append(exc)

        thread = threading.Thread(target=read)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        assert isinstance(done[0], hp.ProtocolRefused)
