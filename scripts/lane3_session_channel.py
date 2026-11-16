"""The framed side channel between the Lane 3 launcher and the target session.

E7 / T1 (Gate 2, 0.4.0a2). The Executor now runs ON the target. The launcher
reaches it over one SSH session, and the two halves have to exchange a handful
of typed messages while it runs: the start parameters, the "stack is up" signal,
the externally measured rows, the evidence, the receipt and the terminal report.

## Never stdout

Foundation prints annotations to stdout (`compose_host.py`, the `::notice` and
`::warning` lines), so a stdout that also carried protocol frames would let any
annotation corrupt, or any attacker-influenced container log forge, a frame.
The channel therefore lives on DEDICATED file descriptors:

* the target session calls :meth:`Channel.from_stdio` as its first act, before
  anything is imported that could print. That duplicates the inherited stdin and
  stdout onto private descriptors, then points descriptor 0 at the null device
  and descriptor 1 at stderr. After it returns nothing that writes to "stdout"
  can reach the channel, and nothing that reads stdin can steal a frame;
* tests and a local launcher pass explicit descriptors to :class:`Channel`.

## What a frame is

`4-byte big-endian length | UTF-8 JSON object`, with exactly the keys `kind`,
`seq` and `data`. The vocabulary is CLOSED and directional: each kind has one
sender and a closed key set for its `data`. A frame is refused, never repaired,
when:

* it is longer than its kind's bound (an announced length is checked before a
  single payload byte is read);
* it is not one JSON object, has a duplicate key, a key outside its kind's set,
  or a missing one;
* its kind is not one the receiver may receive at this point;
* its sequence number is not the next one expected. Sequence numbers are per
  direction and start at zero, so a replayed, dropped or reordered frame refuses.

Refusals carry a short stable CODE and never the payload: a frame can hold
evidence. This module is stdlib-only so the launcher can load it before any
candidate wheel is installed.
"""

from __future__ import annotations

import json
import os
import select
import struct
import time
from collections.abc import Mapping
from typing import Any, Final

__all__ = [
    "KINDS",
    "LAUNCHER_TO_TARGET",
    "TARGET_TO_LAUNCHER",
    "Channel",
    "ChannelRefused",
    "ChannelTimeout",
]

#: Default bound for a frame, in bytes. Every kind that can carry evidence has
#: its own, larger, bound below; nothing else needs more than a page or two.
DEFAULT_FRAME_LIMIT: Final = 1024 * 1024
#: Large enough for the base64 of `lane3_evidence_finalization.MAX_MEMBER_BYTES`
#: plus a ciphertext ceiling; the evidence module applies its own, exact bounds.
EVIDENCE_FRAME_LIMIT: Final = 48 * 1024 * 1024

_HEADER: Final = struct.Struct(">I")


class ChannelRefused(Exception):
    """A frame or the channel itself is unacceptable. Carries a CODE, no payload."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ChannelTimeout(ChannelRefused):
    """No complete frame arrived before the deadline."""

    def __init__(self) -> None:
        super().__init__("channel-timeout")


#: kind -> (sender, closed `data` key set, frame bound)
_SPEC: Final[dict[str, tuple[str, frozenset[str], int]]] = {
    # launcher -> target
    "start": (
        "launcher",
        frozenset(
            {
                "foundation_revision",
                "candidate_source_revision",
                "foundation_artifact",
                "authorization_run",
                "controller_identity",
                "host_id",
                "candidate_version",
                "descriptor",
                "authorization_document",
                "deploy_dir",
                "lock_dir",
                "lease_dir",
                "timeout",
                "probe_vantage_ref",
                "execution_run",
                "expected_machine_id",
                "expected_host_incarnation",
                "window_seconds",
                "evidence_seconds",
            }
        ),
        DEFAULT_FRAME_LIMIT,
    ),
    "external_results": ("launcher", frozenset({"rows"}), DEFAULT_FRAME_LIMIT),
    "evidence_ciphertext": (
        "launcher",
        frozenset({"ciphertext_b64"}),
        EVIDENCE_FRAME_LIMIT,
    ),
    "abort": ("launcher", frozenset({"reason"}), DEFAULT_FRAME_LIMIT),
    # target -> launcher
    "ready": (
        "target",
        frozenset({"host_id", "machine_id", "plan_digest"}),
        DEFAULT_FRAME_LIMIT,
    ),
    "window_open": ("target", frozenset({"deadline_seconds"}), DEFAULT_FRAME_LIMIT),
    "evidence_request": (
        "target",
        frozenset({"exposure_calls_b64"}),
        EVIDENCE_FRAME_LIMIT,
    ),
    "receipt": (
        "target",
        frozenset({"receipt_b64", "receipt_digest"}),
        DEFAULT_FRAME_LIMIT,
    ),
    "terminal": (
        "target",
        frozenset(
            {
                "receipt_digest",
                "refusal",
                "host_mutated",
                "lease_in_hand",
                "cleanup",
                "cleanup_acts",
                "machine_id",
                "notes",
                "exit_status",
            }
        ),
        DEFAULT_FRAME_LIMIT,
    ),
}

KINDS: Final = frozenset(_SPEC)
LAUNCHER_TO_TARGET: Final = frozenset(k for k, v in _SPEC.items() if v[0] == "launcher")
TARGET_TO_LAUNCHER: Final = frozenset(k for k, v in _SPEC.items() if v[0] == "target")

_FRAME_KEYS: Final = frozenset({"kind", "seq", "data"})


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChannelRefused("frame-duplicate-key")
        result[key] = value
    return result


def _refuse_constant(_: str) -> Any:
    raise ChannelRefused("frame-non-finite-number")


class Channel:
    """One end of the framed channel. Not thread-safe; one reader, one writer."""

    def __init__(self, read_fd: int, write_fd: int, *, role: str) -> None:
        if role not in {"launcher", "target"}:
            raise ValueError("role must be launcher or target")
        self._read_fd = read_fd
        self._write_fd = write_fd
        self._role = role
        self._send_seq = 0
        self._recv_seq = 0
        self._buffer = b""
        self._closed = False

    @classmethod
    def from_stdio(cls) -> Channel:
        """The TARGET end, taken off the SSH session's stdin and stdout.

        Must be the first thing the session does. Afterwards descriptor 0 is the
        null device and descriptor 1 is stderr, so a library `print` can never
        write a byte onto the channel.
        """
        read_fd = os.dup(0)
        write_fd = os.dup(1)
        null = os.open(os.devnull, os.O_RDONLY)
        try:
            os.dup2(null, 0)
        finally:
            os.close(null)
        os.dup2(2, 1)
        for fd in (read_fd, write_fd):
            os.set_inheritable(fd, False)
        return cls(read_fd, write_fd, role="target")

    @property
    def peer(self) -> str:
        return "launcher" if self._role == "target" else "target"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in {self._read_fd, self._write_fd}:
            try:
                os.close(fd)
            except OSError:
                pass

    # ── sending ────────────────────────────────────────────────────────────

    def send(self, kind: str, data: Mapping[str, Any]) -> None:
        if kind not in _SPEC or _SPEC[kind][0] != self._role:
            raise ChannelRefused("frame-kind-not-sendable")
        _, keys, limit = _SPEC[kind]
        if set(data) != keys:
            raise ChannelRefused("frame-data-keys")
        body = json.dumps(
            {"kind": kind, "seq": self._send_seq, "data": dict(data)},
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > limit:
            raise ChannelRefused("frame-too-large")
        payload = _HEADER.pack(len(body)) + body
        view = memoryview(payload)
        try:
            while view:
                written = os.write(self._write_fd, view)
                view = view[written:]
        except OSError:
            raise ChannelRefused("channel-write-failed") from None
        self._send_seq += 1

    # ── receiving ──────────────────────────────────────────────────────────

    def _read_exactly(self, count: int, deadline: float) -> bytes:
        while len(self._buffer) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChannelTimeout
            try:
                ready, _, _ = select.select([self._read_fd], [], [], remaining)
            except (OSError, ValueError):
                raise ChannelRefused("channel-read-failed") from None
            if not ready:
                raise ChannelTimeout
            try:
                chunk = os.read(self._read_fd, min(65536, count - len(self._buffer)))
            except OSError:
                raise ChannelRefused("channel-read-failed") from None
            if not chunk:
                raise ChannelRefused("channel-closed")
            self._buffer += chunk
        taken, self._buffer = self._buffer[:count], self._buffer[count:]
        return taken

    def receive(self, *expected: str, timeout: float) -> tuple[str, dict[str, Any]]:
        """The next frame, which must be one of `expected`, within `timeout` seconds.

        An unexpected kind refuses: a launcher that sends `abort` while the
        target expects `external_results` is handled by the caller listing
        `abort` among the kinds it will accept, never by this method guessing.
        """
        if not expected or any(
            kind not in _SPEC or _SPEC[kind][0] != self.peer for kind in expected
        ):
            raise ChannelRefused("frame-kind-not-receivable")
        deadline = time.monotonic() + timeout
        (length,) = _HEADER.unpack(self._read_exactly(_HEADER.size, deadline))
        ceiling = max(_SPEC[kind][2] for kind in expected)
        if length < 2 or length > ceiling:
            raise ChannelRefused("frame-length")
        raw = self._read_exactly(length, deadline)
        try:
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_no_duplicates,
                parse_constant=_refuse_constant,
            )
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ChannelRefused("frame-not-json") from None
        if not isinstance(document, dict) or set(document) != _FRAME_KEYS:
            raise ChannelRefused("frame-shape")
        kind, seq, data = document["kind"], document["seq"], document["data"]
        if not isinstance(kind, str) or kind not in expected:
            raise ChannelRefused("frame-kind-unexpected")
        if length > _SPEC[kind][2]:
            raise ChannelRefused("frame-length")
        if type(seq) is not int or seq != self._recv_seq:
            raise ChannelRefused("frame-sequence")
        if not isinstance(data, dict) or set(data) != _SPEC[kind][1]:
            raise ChannelRefused("frame-data-keys")
        self._recv_seq += 1
        return kind, data
