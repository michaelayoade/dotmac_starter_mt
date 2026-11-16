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
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import exposure_rehearsal_runner as controller_half
import lane3_evidence_finalization as evidence_finalization
import lane3_launcher_evidence as launcher_evidence
import lane3_launcher_lease as launcher_lease
import lane3_launcher_probe as launcher_probe
from dotmac_deployment_foundation.rehearsal import (
    ExecutionRunBindingV1,
    RehearsalReceiptV2,
)
from lane3_session_channel import Channel, ChannelRefused

__all__ = ["DriveInputs", "DriveResult", "drive", "open_ssh_channel"]


@dataclasses.dataclass(frozen=True, slots=True)
class DriveInputs:
    """Everything one drive needs. Every field is required; nothing defaults."""

    start: dict[str, Any]
    expected_host_id: str
    expected_machine_id: str
    execution_run: ExecutionRunBindingV1
    controller_identity: str
    lease_id: str
    probe_inputs: launcher_probe.ProbeInputs
    qualified_evidence: dict[str, Any]
    probe_bytes: bytes
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
        channel.send("start", inputs.start)
        kind, data = channel.receive("ready", "terminal", timeout=inputs.ready_seconds)
        if kind == "terminal":
            return _finish_terminal(result, data)
        if (
            data["host_id"] != inputs.expected_host_id
            or data["machine_id"] != inputs.expected_machine_id
            or not data["plan_digest"]
        ):
            _abort(channel, "placement-mismatch")
            return _terminal(
                channel, inputs.terminal_seconds, result, "placement-mismatch"
            )

        kind, data = channel.receive(
            "window_open", "terminal", timeout=inputs.window_seconds
        )
        if kind == "terminal":
            return _finish_terminal(result, data)
        try:
            rows = launcher_probe.measure_external_rows(
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
        channel.send("external_results", {"rows": rows})

        kind, data = channel.receive(
            "evidence_request", "terminal", timeout=inputs.evidence_seconds
        )
        if kind == "terminal":
            return _finish_terminal(result, data)
        try:
            result.ciphertext = launcher_evidence.finalize(
                probe_bytes=inputs.probe_bytes,
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


def open_ssh_channel(argv: list[str]) -> tuple[subprocess.Popen[bytes], Channel]:
    """Start the session over SSH and wrap its stdio as the launcher's channel.

    stderr is inherited: Foundation's annotations (and the session's refusal
    text, which names fields and never values) reach the launcher's own log.
    """
    process = subprocess.Popen(  # nosec B603 - fixed argv, no shell
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, shell=False
    )
    assert process.stdin is not None and process.stdout is not None
    # Duplicated, so closing the channel never double-closes the Popen's files.
    channel = Channel(
        os.dup(process.stdout.fileno()),
        os.dup(process.stdin.fileno()),
        role="launcher",
    )
    return process, channel
