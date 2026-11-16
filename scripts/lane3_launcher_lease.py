"""Lane 3, the LAUNCHER half of lease custody and release (E7 / T4).

The release principal is the GitHub workload (`HostLeaseReleaseV1` accepts only
`github_actions_workload`), and only the launcher holds the OIDC token that
proves it. Moving the write onto the target would need a new principal kind
(Foundation bytes) or token forwarding (rejected), so custody stays here:

* BEFORE launch the launcher loads the lease from the store it was told to use,
  checks it covers this authorization run and names this run's launch key, and
  stages a READ-ONLY COPY for the target's pre-mutation `covers()` recheck. The
  copy is written by Foundation's own writer into a private staging directory,
  never serialized here;
* AFTER the run the target reports a closed terminal outcome. This module parses
  it back into the types the existing terminal record understands and hands it to
  `exposure_rehearsal_runner.record_terminal`, which builds `HostLeaseReleaseV1`
  as the proven workload and writes it through the package's create-only writer.
  There is no second writer, and nothing here builds a release itself.

## Fail-closed

The lease store location is a required input with no default (exit-list U5: the
authoritative store under ephemeral runners is not yet decided). A terminal
report that disagrees with what the launcher independently knows (the machine id
it pinned, the receipt digest it verified) writes NO release, so the lease stays
held, which is the designed outcome of an unprovable run.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shlex
import stat
import sys
from datetime import datetime
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import exposure_rehearsal_runner as controller_half
from dotmac_deployment_foundation.controller_identity import (
    ControllerSshFingerprintV1,
)
from dotmac_deployment_foundation.lease import HostLease, load_lease, write_lease
from dotmac_deployment_foundation.lease_release import (
    CleanupDisposition,
    TerminalOutcome,
    TerminalRefusal,
)

__all__ = [
    "LeaseCustodyRefused",
    "Terminal",
    "parse_terminal",
    "release_for",
    "stage_readonly_copy",
    "take_custody",
]

_MAX_ACTS: Final = 64
_MAX_COMMANDS: Final = 64
_MAX_TEXT: Final = 4000
_MAX_NOTES: Final = 64


class LeaseCustodyRefused(Exception):
    """Custody or the terminal report is unacceptable. A CODE, never a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def take_custody(
    *,
    target: str,
    lease_store: str,
    authorization_run: str,
    controller_identity: ControllerSshFingerprintV1,
    now: datetime,
) -> HostLease:
    """The held lease, or a refusal. `lease_store` has no default (U5)."""
    if not isinstance(lease_store, str) or not lease_store.strip():
        raise LeaseCustodyRefused("lease-store-required")
    try:
        lease = load_lease(target, directory=lease_store)
        lease.covers(now=now, authorization_run_id=authorization_run)
    except controller_half.DeploymentFoundationError:
        raise LeaseCustodyRefused("lease-not-held") from None
    if controller_identity != lease.controller_identity_fingerprint:
        raise LeaseCustodyRefused("lease-names-another-controller")
    return lease


def stage_readonly_copy(lease: HostLease, staging_dir: pathlib.Path) -> pathlib.Path:
    """Write the lease record into a private directory with the package's writer.

    Returns the file. The caller ships exactly this file to the target's per-run
    directory; both the file and the directory are made read-only here so a
    stray write cannot alter what is shipped.
    """
    staging_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if staging_dir.is_symlink() or stat.S_IMODE(staging_dir.stat().st_mode) & 0o077:
        raise LeaseCustodyRefused("staging-directory-unsafe")
    if any(staging_dir.iterdir()):
        raise LeaseCustodyRefused("staging-directory-not-empty")
    written = write_lease(lease, directory=staging_dir)
    os.chmod(written, 0o400)
    os.chmod(staging_dir, 0o500)
    return written


class Terminal:
    """The target's terminal report, parsed into the vocabulary Foundation owns."""

    __slots__ = (
        "acts",
        "cleanup",
        "exit_status",
        "host_mutated",
        "lease_in_hand",
        "machine_id",
        "notes",
        "receipt_digest",
        "refusal",
    )

    def __init__(self, **values: Any) -> None:
        for name in self.__slots__:
            setattr(self, name, values[name])


def _text(value: object, *, code: str, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise LeaseCustodyRefused(code)
    return value


def _act(document: object) -> controller_half.CleanupAct:
    if not isinstance(document, dict) or set(document) != {
        "act",
        "disposition",
        "detail",
        "commands",
    }:
        raise LeaseCustodyRefused("terminal-act-shape")
    commands = document["commands"]
    if not isinstance(commands, list) or len(commands) > _MAX_COMMANDS:
        raise LeaseCustodyRefused("terminal-act-shape")
    captured = []
    for command in commands:
        if not isinstance(command, dict) or set(command) != {
            "command",
            "exit_code",
            "output",
        }:
            raise LeaseCustodyRefused("terminal-command-shape")
        code = command["exit_code"]
        if type(code) is not int:
            raise LeaseCustodyRefused("terminal-command-shape")
        try:
            argv = tuple(shlex.split(_text(command["command"], code="terminal-text")))
        except ValueError:
            raise LeaseCustodyRefused("terminal-command-shape") from None
        captured.append(
            controller_half.Captured(
                argv=argv,
                exit_code=code,
                output=_text(command["output"], code="terminal-text"),
            )
        )
    try:
        disposition = CleanupDisposition(document["disposition"])
    except ValueError:
        raise LeaseCustodyRefused("terminal-disposition") from None
    return controller_half.CleanupAct(
        _text(document["act"], code="terminal-text"),
        disposition,
        _text(document["detail"], code="terminal-text"),
        tuple(captured),
    )


def parse_terminal(data: dict[str, Any]) -> Terminal:
    """The closed `terminal` frame as typed values, or a refusal.

    The reported `cleanup` is DERIVED again from the reported acts rather than
    believed: a report whose summary disagrees with its own acts is refused.
    """
    refusal_text = data["refusal"]
    if refusal_text == "":
        refusal = None
    else:
        try:
            refusal = TerminalRefusal(refusal_text)
        except ValueError:
            raise LeaseCustodyRefused("terminal-refusal") from None
    receipt_digest = _text(data["receipt_digest"], code="terminal-text", limit=200)
    if receipt_digest and refusal is not None:
        raise LeaseCustodyRefused("terminal-receipt-and-refusal")
    for flag in ("host_mutated", "lease_in_hand"):
        if type(data[flag]) is not bool:
            raise LeaseCustodyRefused("terminal-flag")
    machine_id = _text(data["machine_id"], code="terminal-text", limit=64)
    if machine_id and (
        len(machine_id) != 32 or any(c not in "0123456789abcdef" for c in machine_id)
    ):
        raise LeaseCustodyRefused("terminal-machine-id")
    acts_raw = data["cleanup_acts"]
    if not isinstance(acts_raw, list) or len(acts_raw) > _MAX_ACTS:
        raise LeaseCustodyRefused("terminal-act-shape")
    acts = [_act(item) for item in acts_raw]
    notes_raw = data["notes"]
    if not isinstance(notes_raw, list) or len(notes_raw) > _MAX_NOTES:
        raise LeaseCustodyRefused("terminal-notes")
    notes = [_text(item, code="terminal-notes") for item in notes_raw]
    status = data["exit_status"]
    if type(status) is not int or not 0 <= status <= 255:
        raise LeaseCustodyRefused("terminal-exit-status")
    try:
        reported = CleanupDisposition(data["cleanup"])
    except ValueError:
        raise LeaseCustodyRefused("terminal-disposition") from None
    probe = controller_half.TerminalContext()
    probe.acts.extend(acts)
    if probe.cleanup_disposition() is not reported:
        raise LeaseCustodyRefused("terminal-cleanup-inconsistent")
    return Terminal(
        acts=acts,
        cleanup=reported,
        exit_status=status,
        host_mutated=data["host_mutated"],
        lease_in_hand=data["lease_in_hand"],
        machine_id=machine_id,
        notes=notes,
        receipt_digest=receipt_digest,
        refusal=refusal,
    )


def release_for(
    terminal: Terminal,
    *,
    lease: HostLease,
    controller_identity: ControllerSshFingerprintV1,
    expected_machine_id: str,
    verified_receipt_digest: str,
    release_args: argparse.Namespace,
) -> str:
    """Write the release for this terminal report, or none. Returns its digest.

    NONE is written, and the lease stays held, when the report cannot be
    reconciled with what the launcher knows: a machine id other than the pinned
    one, or a receipt digest other than the one it verified. `release_args`
    carries the fields `record_terminal` reads (`target`, `lease_dir` = the REAL
    store, `vm_slot`, the revisions, `principal_audience`, output paths).
    """
    if terminal.machine_id and terminal.machine_id != expected_machine_id:
        raise LeaseCustodyRefused("terminal-machine-mismatch")
    if terminal.receipt_digest:
        if terminal.receipt_digest != verified_receipt_digest:
            raise LeaseCustodyRefused("terminal-receipt-unverified")
        outcome: TerminalOutcome | None = TerminalOutcome(
            receipt_digest=terminal.receipt_digest
        )
    elif terminal.refusal is not None:
        outcome = TerminalOutcome(refusal=terminal.refusal)
    else:
        outcome = None
    ctx = controller_half.TerminalContext()
    ctx.lease = lease if terminal.lease_in_hand else None
    ctx.controller_identity = controller_identity
    ctx.host_mutated = terminal.host_mutated
    ctx.vm_installation_id = terminal.machine_id
    ctx.acts.extend(terminal.acts)
    ctx.notes.extend(terminal.notes)
    ctx.receipt_digest = terminal.receipt_digest
    return controller_half.record_terminal(ctx, release_args, outcome)
