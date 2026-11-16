"""The target half's command runner and its placement self-check (E7 / T1).

`exposure_rehearsal_runner._ssh_runner` carried every host command to the target
over SSH. On the target there is nowhere to carry them: the commands run in
place. This module is the local replacement and nothing more. It is the same
seam, the same `CommandResult` shape and the same failure mapping Foundation's
own default runner uses (a missing binary is 127, a timeout is 124), so a caller
that only branches on `.ok` sees one shape whether the child ran or not.

## The placement self-check

Target-side execution is only meaningful if the process really is on the host
the authorization names. The plan's `host_id` and `host_incarnation` come from
the CP provider's own observation of THIS machine, so a plan rendered on the
wrong host would agree with itself and prove nothing. The launcher therefore
pins what it expects, out of band, in the start frame, and the target compares:

* the plan's `host_id` against the host id the launcher dispatched;
* the plan's `host_incarnation` against the incarnation the launcher pinned;
* this machine's `/etc/machine-id` against the machine id the launcher pinned.

Every comparison is exact and every empty value refuses. Which enrolled value
the launcher pins as `host_incarnation` (the installed signer fingerprint, or
the SSH host key) is exit-list unknown U3; this module only refuses a mismatch.
"""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Mapping, Sequence
from typing import Final

from dotmac_deployment_foundation.engine.run import CommandResult

__all__ = [
    "MACHINE_ID_PATH",
    "PlacementRefused",
    "local_runner",
    "placement_self_check",
    "read_machine_id",
]

MACHINE_ID_PATH: Final = pathlib.Path("/etc/machine-id")
_MACHINE_ID_LENGTH: Final = 32


class PlacementRefused(Exception):
    """The process is not provably on the host the authorization names.

    Raised before the lease is read, the lock is taken or anything mutates.
    The message names the FIELD that disagreed and never a value.
    """


def local_runner(
    argv: Sequence[str],
    *,
    timeout: int = 60,
    env: Mapping[str, str] | None = None,
    capture: bool = True,
) -> CommandResult:
    """Run `argv` on this host: `shell=False`, an argv LIST, never a string.

    Failures that never reach an exit code become a `CommandResult` instead of
    an exception, exactly as Foundation's own runner does, so the Executor and
    the cleanup watcher see one uniform shape.
    """
    try:
        completed = subprocess.run(
            list(argv),
            shell=False,
            timeout=timeout,
            env=dict(env) if env is not None else None,
            capture_output=capture,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            124, "", f"timed out after {timeout}s{': ' + stderr if stderr else ''}"
        )
    except OSError as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(
        completed.returncode, completed.stdout or "", completed.stderr or ""
    )


def read_machine_id(path: pathlib.Path = MACHINE_ID_PATH) -> str:
    """This machine's id: 32 lowercase hex characters, or a refusal."""
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        raise PlacementRefused("the machine id is unreadable") from None
    if len(text) != _MACHINE_ID_LENGTH or any(
        c not in "0123456789abcdef" for c in text
    ):
        raise PlacementRefused("the machine id is not 32 lowercase hex characters")
    return text


def placement_self_check(
    *,
    plan_host_id: str,
    plan_host_incarnation: str,
    expected_host_id: str,
    expected_host_incarnation: str,
    expected_machine_id: str,
    machine_id: str,
) -> None:
    """Refuse unless the plan, the launcher's pins and this machine all agree."""
    for field, value in (
        ("plan host_id", plan_host_id),
        ("plan host_incarnation", plan_host_incarnation),
        ("expected host_id", expected_host_id),
        ("expected host_incarnation", expected_host_incarnation),
        ("expected machine_id", expected_machine_id),
        ("machine_id", machine_id),
    ):
        if not isinstance(value, str) or not value:
            raise PlacementRefused(f"{field} is empty")
    if plan_host_id != expected_host_id:
        raise PlacementRefused("the plan's host_id is not the dispatched host")
    if plan_host_incarnation != expected_host_incarnation:
        raise PlacementRefused("the plan's host_incarnation is not the pinned one")
    if machine_id != expected_machine_id:
        raise PlacementRefused("this machine's id is not the pinned machine id")
