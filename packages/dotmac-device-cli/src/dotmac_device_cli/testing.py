"""Test doubles for hosts and for the conformance suite. No real I/O."""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field


class FakeClock:
    """Deterministic time: ``sleep`` and idle channel reads advance it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += max(0.0, seconds)


# Where a device stops and waits for the host: after a pager marker, a
# '{ ... }:' continuation or a (y/n) question. The reply after the gate is held
# until the host sends something.
_CONFIRM_GATE = r"(?:\(y/n\)|\[Y/N\])(?:\[[yn]\])?:?"


@dataclass
class ReplayChannel:
    """Replays a transcript as a device would: gated, byte-exact, recorded.

    ``replies`` maps an exact sent line (with its newline) to the device's full
    reply. ``default_reply`` (a template using ``{command}``) answers any other
    line. A reply is cut after each gate; the rest is released by the host's next
    send, which is recorded in ``sent``.
    """

    clock: FakeClock
    prompt: str = "lab-olt#"
    pager_regex: str = r"---- More(?:\s*\([^)]*\)\s*)?----"
    continuation_regex: str = r"\{[^\r\n{}]*\}\s*:?"
    replies: Mapping[str, str] = field(default_factory=dict)
    default_reply: str | None = None
    eof_after_answer: bool = False
    sent: list[str] = field(default_factory=list)
    _out: list[str] = field(default_factory=list)
    _held: list[str] = field(default_factory=list)
    _closed: bool = False
    _timeout: float = 0.8

    def __post_init__(self) -> None:
        self._out.append(f"\r\n{self.prompt}")
        self._gate = re.compile(
            f"{self.pager_regex}|{self.continuation_regex}|{_CONFIRM_GATE}"
        )

    # ShellChannel
    def send(self, data: str) -> int:
        self.sent.append(data)
        if self._held:
            segment = self._held.pop(0)
            if segment:
                self._out.append(segment)
            if not self._held and self.eof_after_answer:
                self._pending_close = True
            return len(data)
        if data == "\n":
            self._out.append(f"\r\n{self.prompt}")
            return len(data)
        reply = self.replies.get(data)
        if reply is None and self.default_reply is not None:
            reply = self.default_reply.replace("{command}", data.rstrip("\n"))
        if reply is not None:
            first, *rest = self._split(reply)
            self._out.append(first)
            self._held.extend(rest)
        return len(data)

    _pending_close = False

    def _split(self, reply: str) -> list[str]:
        parts: list[str] = []
        pos = 0
        for m in self._gate.finditer(reply):
            parts.append(reply[pos : m.end()])
            pos = m.end()
        parts.append(reply[pos:])
        return parts

    def recv(self, nbytes: int) -> bytes:
        if self._out:
            data = self._out.pop(0).encode("utf-8")
            if len(data) > nbytes:
                self._out.insert(0, data[nbytes:].decode("utf-8", errors="replace"))
                data = data[:nbytes]
            return data
        if self._pending_close or self._closed:
            self._closed = True
            return b""
        self.clock.now += self._timeout
        raise TimeoutError("idle")

    def recv_ready(self) -> bool:
        return bool(self._out)

    def settimeout(self, seconds: float) -> None:
        self._timeout = seconds

    @property
    def closed(self) -> bool:
        return self._closed


class InMemoryLockBackend:
    """Stands in for the shared database: advisory-lock semantics, many owners."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._exclusive: dict[int, int] = {}
        self._shared: dict[int, set[int]] = {}
        self._next_owner = 0

    def store(self) -> InMemoryLockStore:
        with self._guard:
            self._next_owner += 1
            return InMemoryLockStore(self, self._next_owner)

    def held_exclusive(self) -> set[int]:
        return set(self._exclusive)


class InMemoryLockStore:
    """One pinned 'connection': closing it releases everything it holds."""

    def __init__(self, backend: InMemoryLockBackend, owner: int) -> None:
        self._b = backend
        self._owner = owner
        self.closed = False

    def try_lock(self, key: int) -> bool:
        with self._b._guard:
            holder = self._b._exclusive.get(key)
            others = self._b._shared.get(key, set()) - {self._owner}
            if (holder is not None and holder != self._owner) or others:
                return False
            self._b._exclusive[key] = self._owner
            return True

    def unlock(self, key: int) -> None:
        with self._b._guard:
            if self._b._exclusive.get(key) == self._owner:
                del self._b._exclusive[key]

    def try_lock_shared(self, key: int) -> bool:
        with self._b._guard:
            holder = self._b._exclusive.get(key)
            if holder is not None and holder != self._owner:
                return False
            self._b._shared.setdefault(key, set()).add(self._owner)
            return True

    def unlock_shared(self, key: int) -> None:
        with self._b._guard:
            self._b._shared.get(key, set()).discard(self._owner)

    def close(self) -> None:
        with self._b._guard:
            self.closed = True
            for key in [k for k, o in self._b._exclusive.items() if o == self._owner]:
                del self._b._exclusive[key]
            for owners in self._b._shared.values():
                owners.discard(self._owner)
