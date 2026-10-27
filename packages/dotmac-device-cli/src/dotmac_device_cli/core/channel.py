"""The abstract channel and clock the host injects.

The host (Sub today, Integrator later) opens the transport, authenticates and
hands an already-authenticated interactive channel in. This package never opens
a connection and never sees a credential.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class ShellChannel(Protocol):
    """An authenticated interactive shell. paramiko's ``Channel`` satisfies it."""

    def send(self, data: str) -> int: ...

    def recv(self, nbytes: int) -> bytes:
        """Return received bytes; raise ``TimeoutError`` when idle; ``b""`` at EOF."""
        ...

    def recv_ready(self) -> bool: ...

    def settimeout(self, seconds: float) -> None: ...

    @property
    def closed(self) -> bool: ...


class Clock(Protocol):
    """Injected time so deadlines and grace periods are testable."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class MonotonicClock:
    """The real clock. Hosts pass this; tests pass a fake."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


__all__ = ["Clock", "MonotonicClock", "ShellChannel"]
