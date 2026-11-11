"""Process-level fetch spy for the V3 data-only refusal conformance kit.

Test tooling, not production code. It records and BLOCKS socket connects, name
resolution, ``urllib`` opens and opens of watched local paths while active, so
a leaking engine is both detected and unable to reach anything. It sees only
the current Python process; a subprocess or remote engine needs its own
observation point.
"""

from __future__ import annotations

import builtins
import io
import os
import socket
import urllib.request
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock


class ProcessFetchSpy:
    def __init__(self, *, watched_paths: tuple[Path, ...] = ()) -> None:
        self._watched = {str(path.resolve()) for path in watched_paths}
        self._events: list[str] = []

    def fetches(self) -> tuple[str, ...]:
        return tuple(self._events)

    def _blocked(self, label: str):
        def spy(*args, **kwargs):
            self._events.append(label)
            raise OSError(f"fetch blocked by test spy: {label}")

        return spy

    def _watching_open(self, label: str, real):
        def spy(file, *args, **kwargs):
            try:
                text = os.fsdecode(file) if not isinstance(file, int) else ""
            except TypeError:
                text = ""
            resolved = str(Path(text).resolve()) if text else ""
            if text.startswith(("file:", "http:", "https:")) or (
                resolved in self._watched
            ):
                self._events.append(f"{label}:{text}")
                raise PermissionError("local read blocked by test spy")
            return real(file, *args, **kwargs)

        return spy

    @contextmanager
    def watching(self) -> Iterator[None]:
        self._events = []
        with ExitStack() as stack:
            for target, name in (
                (socket.socket, "connect"),
                (socket.socket, "connect_ex"),
                (socket, "create_connection"),
                (socket, "getaddrinfo"),
                (socket, "gethostbyname"),
                (urllib.request, "urlopen"),
                (urllib.request.OpenerDirector, "open"),
            ):
                stack.enter_context(
                    mock.patch.object(
                        target, name, self._blocked(f"{target.__name__}.{name}")
                    )
                )
            for target, name in ((builtins, "open"), (io, "open"), (os, "open")):
                stack.enter_context(
                    mock.patch.object(
                        target,
                        name,
                        self._watching_open(
                            f"{target.__name__}.{name}", getattr(target, name)
                        ),
                    )
                )
            yield
