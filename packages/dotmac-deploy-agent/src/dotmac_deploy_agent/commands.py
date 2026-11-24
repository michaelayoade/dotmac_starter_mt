"""External commands the agent may run, behind one seam so tests need no host.

`SubprocessCommands` runs an argv list (never a shell) with an allow-listed
environment: no registry credential variable is ever passed, and no CP DB name.
`ReadOnlyCommands` wraps any runner and refuses everything but a fixed set of
inspection verbs, which is what the reconcile observation is limited to.
"""

from __future__ import annotations

import os
import signal
import subprocess  # nosec B404 - argv lists, shell=False
from dataclasses import dataclass
from typing import Final, Protocol

from .refusal import Refusal

ENV: Final = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
MAX_OUTPUT: Final = 1024 * 1024


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: bytes


class Commands(Protocol):
    def run(self, argv: tuple[str, ...], *, timeout: float) -> CommandResult: ...


class SubprocessCommands:
    def run(self, argv: tuple[str, ...], *, timeout: float) -> CommandResult:
        if not argv or not os.path.isabs(argv[0]):
            raise Refusal("transfer_command_failed")
        try:
            completed = subprocess.run(  # nosec B603  # noqa: S603
                list(argv),
                env=ENV,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=max(timeout, 1.0),
                shell=False,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise Refusal("transfer_command_failed") from None
        return CommandResult(completed.returncode, completed.stdout[:MAX_OUTPUT])


class ReadOnlyCommands:
    """Only `docker inspect`, `docker image inspect` and `docker ps` pass."""

    ALLOWED: Final = frozenset({("inspect",), ("image", "inspect"), ("ps",)})

    def __init__(self, inner: Commands) -> None:
        self._inner = inner

    def run(self, argv: tuple[str, ...], *, timeout: float) -> CommandResult:
        verbs = argv[1:3]
        if not (verbs[:1] in self.ALLOWED or verbs in self.ALLOWED):
            raise Refusal("reconcile_unavailable")
        return self._inner.run(argv, timeout=timeout)


class RegistryHandle(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float) -> None: ...


class RegistryLauncher(Protocol):
    def start(self, argv: tuple[str, ...]) -> RegistryHandle: ...


class _Process:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    def poll(self) -> int | None:
        return self._process.poll()

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)

    def _signal(self, number: int) -> None:
        try:
            os.killpg(self._process.pid, number)
        except ProcessLookupError:
            pass

    def wait(self, timeout: float) -> None:
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Refusal("transfer_registry_left_running") from None


class SubprocessRegistryLauncher:
    def start(self, argv: tuple[str, ...]) -> RegistryHandle:
        if not argv or not os.path.isabs(argv[0]):
            raise Refusal("transfer_command_failed")
        try:
            process = subprocess.Popen(  # nosec B603  # noqa: S603
                list(argv),
                env=ENV,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            raise Refusal("transfer_command_failed") from None
        return _Process(process)
