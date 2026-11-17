"""Lane 3, the LAUNCHER's protocol driver (E7, composing T2-T5).

One function, :func:`drive`, runs the launcher's side of one target session over
an already-open :class:`Channel`: send the start parameters, check the target's
placement report, measure items 11-16 inside the window, hand back the
ciphertext, verify the receipt against values only the launcher holds, and read
the terminal report. It decides nothing about the host and writes nothing; the
caller releases the lease from the result (`lane3_launcher_lease.release_for`).

Every collaborator is injected, so the whole exchange is testable against a
scripted target over pipes. The CLI and the execution-repo launcher that call this
are a separate, later change: they own the dispatch, the OIDC-bound release and
the protected environment.

## Fail-closed

* Any refusal, timeout or malformed frame sends `abort` (best effort) and then
  reads the target's terminal report, so the target unwinds through its own
  cleanup. An absent terminal report is returned as such and the caller writes
  NO release: absence means HELD.
* The launcher never accepts a `ready` that names another host or machine than
  the ones it pinned, and never accepts a receipt it cannot bind to its own
  ciphertext, run, lease, launch key and host.
"""

from __future__ import annotations

import base64
import dataclasses
import os
import pathlib
import subprocess
import sys
import threading
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import exposure_rehearsal_runner as controller_half
import lane3_evidence_finalization as evidence_finalization
import lane3_launcher_evidence as launcher_evidence
import lane3_launcher_lease as launcher_lease
import lane3_launcher_probe as launcher_probe
import lane3_lease_store
from dotmac_deployment_foundation.rehearsal import (
    ExecutionRunBindingV1,
    RehearsalReceiptV2,
)
from lane3_session_channel import Channel, ChannelRefused

__all__ = [
    "DriveInputs",
    "DriveResult",
    "StderrSink",
    "drive",
    "open_ssh_channel",
]


@dataclasses.dataclass(frozen=True, slots=True)
class DriveInputs:
    """Everything one drive needs. Every field is required; nothing defaults."""

    #: Every start key EXCEPT `store_time`: the driver adds that from the store.
    start: dict[str, Any]
    store: lane3_lease_store.LeaseStore
    held: lane3_lease_store.HeldLease
    authorization_run: str
    expected_host_id: str
    expected_machine_id: str
    execution_run: ExecutionRunBindingV1
    controller_identity: str
    lease_id: str
    probe_inputs: launcher_probe.ProbeInputs
    qualified_evidence: dict[str, Any]
    fixture_digest: str
    foundation_revision: str
    foundation_artifact: str
    probe_vantage_ref: str
    encryption: evidence_finalization.EncryptionContext
    evidence_output: pathlib.Path
    ready_seconds: float
    window_seconds: float
    evidence_seconds: float
    terminal_seconds: float
    measurers: launcher_probe.Measurers | None = None


@dataclasses.dataclass(slots=True)
class DriveResult:
    """What happened. `terminal` is None when the target never reported."""

    terminal: launcher_lease.Terminal | None
    receipt: RehearsalReceiptV2 | None = None
    ciphertext: bytes | None = None
    #: The first reason the launcher stopped trusting the run, or "".
    refusal_code: str = ""
    #: The target reported `ready`: it was on a host with a plan in hand.
    ready_seen: bool = False
    plan_digest: str = ""


def _abort(channel: Channel, reason: str) -> None:
    try:
        channel.send("abort", {"reason": reason[:200]})
    except ChannelRefused:
        pass


def _terminal(
    channel: Channel, seconds: float, result: DriveResult, code: str
) -> DriveResult:
    if code and not result.refusal_code:
        result.refusal_code = code
    try:
        _, data = channel.receive("terminal", timeout=seconds)
        result.terminal = launcher_lease.parse_terminal(data)
    except ChannelRefused as exc:
        result.refusal_code = result.refusal_code or exc.code
    except launcher_lease.LeaseCustodyRefused as exc:
        result.refusal_code = result.refusal_code or exc.code
    return result


def drive(channel: Channel, inputs: DriveInputs) -> DriveResult:
    result = DriveResult(terminal=None)
    try:
        # The target's only notion of "now" is the STORE's time, taken here, not
        # this runner's or the target's own clock.
        try:
            lane3_lease_store.require_database_time(inputs.store)
            store_time = inputs.store.database_time()
        except lane3_lease_store.StoreRefused as exc:
            result.refusal_code = exc.code
            return result
        channel.send("start", {**inputs.start, "store_time": store_time.isoformat()})
        kind, data = channel.receive("ready", "terminal", timeout=inputs.ready_seconds)
        if kind == "terminal":
            return _finish_terminal(result, data)
        result.ready_seen = True
        result.plan_digest = data["plan_digest"]
        if (
            data["host_id"] != inputs.expected_host_id
            or data["machine_id"] != inputs.expected_machine_id
            or not data["plan_digest"]
        ):
            _abort(channel, "placement-mismatch")
            return _terminal(
                channel, inputs.terminal_seconds, result, "placement-mismatch"
            )
        # The lease must still cover this run on STORE time at the moment the
        # target is let go. A store that says no aborts the target here.
        try:
            inputs.store.covers(inputs.held, authorization_run=inputs.authorization_run)
        except lane3_lease_store.StoreRefused as exc:
            _abort(channel, "lease-not-covering")
            return _terminal(channel, inputs.terminal_seconds, result, exc.code)
        # The gate: the target takes no grant and no lock until it reads this.
        channel.send("proceed", {"plan_digest": data["plan_digest"]})

        kind, data = channel.receive(
            "window_open", "terminal", timeout=inputs.window_seconds
        )
        if kind == "terminal":
            return _finish_terminal(result, data)
        try:
            measurement = launcher_probe.measure_external_rows(
                inputs.probe_inputs,
                inputs.qualified_evidence,
                measurers=inputs.measurers,
            )
        except (
            controller_half.DeploymentFoundationError,
            subprocess.TimeoutExpired,
            OSError,
        ):
            _abort(channel, "probe-measurement-refused")
            return _terminal(
                channel, inputs.terminal_seconds, result, "probe-measurement-refused"
            )
        rows = measurement.rows
        channel.send("external_results", {"rows": rows})

        kind, data = channel.receive(
            "evidence_request", "terminal", timeout=inputs.evidence_seconds
        )
        if kind == "terminal":
            return _finish_terminal(result, data)
        try:
            result.ciphertext = launcher_evidence.finalize(
                probe_bytes=measurement.document_bytes(),
                exposure_calls_b64=data["exposure_calls_b64"],
                encryption=inputs.encryption,
                output=inputs.evidence_output,
            )
        except (
            launcher_evidence.EvidenceHandBackRefused,
            evidence_finalization.FinalizationRefused,
            evidence_finalization.FinalizationUnavailable,
        ) as exc:
            _abort(channel, "evidence-refused")
            code = getattr(exc, "code", "evidence-refused")
            return _terminal(channel, inputs.terminal_seconds, result, code)
        channel.send(
            "evidence_ciphertext",
            {"ciphertext_b64": _b64(result.ciphertext)},
        )

        kind, data = channel.receive(
            "receipt", "terminal", timeout=inputs.evidence_seconds
        )
        if kind == "terminal":
            return _finish_terminal(result, data)
        code = ""
        try:
            result.receipt = launcher_evidence.verify_receipt(
                receipt_b64=data["receipt_b64"],
                receipt_digest=data["receipt_digest"],
                ciphertext=result.ciphertext,
                execution_run=inputs.execution_run,
                controller_identity=inputs.controller_identity,
                lease_id=inputs.lease_id,
                host_id=inputs.expected_host_id,
                sent_rows=rows,
                foundation_revision=inputs.foundation_revision,
                foundation_artifact=inputs.foundation_artifact,
                fixture_digest=inputs.fixture_digest,
                probe_vantage_ref=inputs.probe_vantage_ref,
                plan_digest=result.plan_digest,
            )
        except launcher_evidence.EvidenceHandBackRefused as exc:
            code = exc.code
        return _terminal(channel, inputs.terminal_seconds, result, code)
    except ChannelRefused as exc:
        result.refusal_code = exc.code
        return result


def _finish_terminal(result: DriveResult, data: dict[str, Any]) -> DriveResult:
    try:
        result.terminal = launcher_lease.parse_terminal(data)
    except launcher_lease.LeaseCustodyRefused as exc:
        result.refusal_code = exc.code
    return result


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class StderrSink:
    """Target stderr, drained into a bounded private file and nowhere else.

    The session's stderr carries Foundation's annotations and its refusal text.
    None of it is echoed to the launcher's own output, so it can never become
    workflow log output; it is kept, bounded, for an operator to read from the
    private file. Bytes beyond the bound are read and discarded so the target can
    never block on a full pipe.
    """

    def __init__(self, path: pathlib.Path, limit: int) -> None:
        """Create the file FIRST (create-only, `0600`), before anything spawns."""
        self.path = path
        self.limit = limit
        self.truncated = False
        self._stream: Any = None
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._file = os.fdopen(descriptor, "wb")
        self._thread: threading.Thread | None = None

    def attach(self, stream: Any) -> None:
        self._stream = stream
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        written = 0
        try:
            while True:
                chunk = self._stream.read(65536)
                if not chunk:
                    break
                room = self.limit - written
                if room > 0:
                    self._file.write(chunk[:room])
                    written += min(room, len(chunk))
                if len(chunk) > room:
                    self.truncated = True
        finally:
            self._file.close()

    def join(self, timeout: float = 10.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)


def open_ssh_channel(
    argv: list[str], *, stderr_path: pathlib.Path, stderr_limit: int
) -> tuple[subprocess.Popen[bytes], Channel, StderrSink]:
    """Start the session over SSH and wrap its stdio as the launcher's channel.

    stderr goes to a bounded private file, never to this process's own output.
    """
    if type(stderr_limit) is not int or stderr_limit < 1:
        raise ValueError("stderr_limit must be a positive integer")
    # The file exists before the child does: a name that is taken refuses here.
    sink = StderrSink(stderr_path, stderr_limit)
    process = subprocess.Popen(  # nosec B603 - fixed argv, no shell
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    sink.attach(process.stderr)
    # Duplicated, so closing the channel never double-closes the Popen's files.
    channel = Channel(
        os.dup(process.stdout.fileno()),
        os.dup(process.stdin.fileno()),
        role="launcher",
    )
    return process, channel, sink
