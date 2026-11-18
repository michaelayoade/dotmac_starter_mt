"""Lane 3, the LAUNCHER half of lease custody and release (E7 / T4, U5).

The release principal is the GitHub workload (`HostLeaseReleaseV1` accepts only
`github_actions_workload`), and only the launcher holds the OIDC token that
proves it. Moving the write onto the target would need a new principal kind
(Foundation bytes) or token forwarding (rejected), so custody stays here.

## One store, on database time (U5)

Custody and release both go through a :class:`~lane3_lease_store.LeaseStore`, the
interface this lane requires of the CP production database. :func:`take_custody`
refuses a store that cannot supply database time and takes the lease exclusively
under the store's row lock; expiry and `covers` are judged on store time. The
release is built here as the proven workload and written into THAT SAME store via
`store.release(..., host_mutated=...)`; there is no second ledger.

* BEFORE launch the launcher holds the lease and stages a READ-ONLY COPY for the
  target, written by Foundation's own writer, never serialized here;
* AFTER the run the target reports a closed terminal outcome, parsed here back
  into the types Foundation owns. A report the launcher cannot reconcile with what
  it knows (the pinned machine id, the receipt digest it verified) writes NO
  release;
* a runner that dies, or a target that never reports, writes no release: the
  lease expires on store time and the host is treated as MUTATED, never released
  as untouched. Once the target has reported `ready` the launcher forces
  `host_mutated=True` whatever the target says.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import stat
import sys
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import exposure_rehearsal_runner as controller_half
from dotmac_deployment_foundation.controller_identity import (
    ControllerSshFingerprintV1,
)
from dotmac_deployment_foundation.lease import HostLease, write_lease
from dotmac_deployment_foundation.lease_release import (
    CleanupDisposition,
    TerminalOutcome,
    TerminalRefusal,
)
from lane3_lease_store import (
    HeldLease,
    LeaseStore,
    StoreRefused,
    require_database_time,
)

__all__ = [
    "EVIDENCE_FILE",
    "RELEASE_FILE",
    "LeaseCustodyRefused",
    "Terminal",
    "parse_terminal",
    "release_for",
    "release_with_record",
    "stage_readonly_copy",
    "take_custody",
    "write_terminal_records",
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
    store: LeaseStore,
    host_id: str,
    authorization_run: str,
    controller_identity: ControllerSshFingerprintV1,
    ttl_seconds: int,
) -> HeldLease:
    """The exclusively held lease, or a refusal. The store is a required input.

    A store that cannot supply database time is refused before anything is taken.
    """
    try:
        checked = require_database_time(store)
        held = checked.acquire_exclusive(
            host_id=host_id,
            authorization_run=authorization_run,
            controller_identity=controller_identity,
            ttl_seconds=ttl_seconds,
        )
    except StoreRefused as exc:
        raise LeaseCustodyRefused(exc.code) from None
    if controller_identity != held.lease.controller_identity_fingerprint:
        raise LeaseCustodyRefused("lease-names-another-controller")
    return held


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


def release_with_record(
    terminal: Terminal,
    *,
    store: LeaseStore,
    held: HeldLease,
    controller_identity: ControllerSshFingerprintV1,
    expected_machine_id: str,
    verified_receipt_digest: str,
    ready_seen: bool,
    release_args: argparse.Namespace,
) -> tuple[str, Any]:
    """Write the release into the lease's own store, or none: `(digest, record)`.

    NONE is written (`("", None)`), and the lease is left to expire on store time
    as a mutated host, when the report names no member of Foundation's closed
    vocabulary. A report the launcher cannot reconcile with what it knows raises
    and writes nothing. `release_args` carries the fields `build_release` reads
    (`vm_slot`, the revisions, `candidate_version`, `authorization_run`,
    `principal_audience`, `timeout`).
    """
    if terminal.machine_id and terminal.machine_id != expected_machine_id:
        raise LeaseCustodyRefused("terminal-machine-mismatch")
    # Once the target reported `ready` it was on the host with a plan in hand, so
    # the launcher can no longer vouch that the host is untouched: whatever the
    # target reports about its own mutation, it is recorded as attempted.
    host_mutated = terminal.host_mutated or ready_seen
    if terminal.receipt_digest:
        if terminal.receipt_digest != verified_receipt_digest:
            raise LeaseCustodyRefused("terminal-receipt-unverified")
        outcome = TerminalOutcome(receipt_digest=terminal.receipt_digest)
    elif terminal.refusal is not None:
        refusal = terminal.refusal
        # `PRECONDITION_UNFIT` asserts an untouched host. With the host recorded
        # as mutated that claim is false, so it degrades exactly as the target's
        # own classifier degrades it, and can never close as REUSABLE.
        if refusal is TerminalRefusal.PRECONDITION_UNFIT and host_mutated:
            refusal = TerminalRefusal.HOST_STATE_UNCERTIFIED
        outcome = TerminalOutcome(refusal=refusal)
    else:
        return "", None
    ctx = controller_half.TerminalContext()
    ctx.lease = held.lease if terminal.lease_in_hand else None
    ctx.controller_identity = controller_identity
    ctx.host_mutated = host_mutated
    ctx.vm_installation_id = terminal.machine_id
    ctx.acts.extend(terminal.acts)
    ctx.notes.extend(terminal.notes)
    ctx.receipt_digest = terminal.receipt_digest
    try:
        release = controller_half.build_release(ctx, release_args, outcome)
    except (
        controller_half.ReleaseNotWritable,
        controller_half.PrincipalUnprovable,
    ):
        raise LeaseCustodyRefused("release-not-writable") from None
    try:
        digest = store.release(held, release=release, host_mutated=host_mutated)
    except StoreRefused as exc:
        raise LeaseCustodyRefused(exc.code) from None
    return digest, release


def release_for(terminal: Terminal, **kwargs: Any) -> str:
    """:func:`release_with_record`, reduced to the release digest (`""` for none)."""
    return release_with_record(terminal, **kwargs)[0]


# The names the existing `exposure-rehearsal.yml` upload step publishes
# (`lane3-terminal-release`), so these records reach the store on every outcome.
RELEASE_FILE: Final = "lane3-lease-release.json"
EVIDENCE_FILE: Final = "lane3-terminal-evidence.json"


def _create_only(path: pathlib.Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


def write_terminal_records(
    directory: pathlib.Path,
    *,
    evidence: dict[str, Any],
    release_record: Any | None,
    released_at: str | None = None,
) -> list[pathlib.Path]:
    """Create-only terminal records for EVERY outcome. Never overwrites.

    The evidence file is always written, so a refusal, a release and a no-release
    each leave a record and absence is never silent. The release file is a copy of
    the release document and exists only when a release was written to the store.
    When `released_at` is given (the STORE's stamp) the copy carries it in place of
    the runner's own `released_at`; the runner's value is kept in the evidence.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written: list[pathlib.Path] = []
    if release_record is not None:
        path = directory / RELEASE_FILE
        document = dict(release_record.as_document())
        if released_at is not None:
            document["released_at"] = released_at
        _create_only(path, json.dumps(document, indent=2, allow_nan=False))
        written.append(path)
    path = directory / EVIDENCE_FILE
    _create_only(path, json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    written.append(path)
    return written
