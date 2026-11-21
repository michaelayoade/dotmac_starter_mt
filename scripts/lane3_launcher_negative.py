"""Lane 3, the LAUNCHER half of the negative target (S8b).

One X1 run drives BOTH targets, negative first. This module is the launcher's
drive of the NEGATIVE target after `proceed`: the target provokes a genuine
exposure-verification failure, the Executor compensates, and the session opens a
bounded `compensation_window_open` with its stack still up and its own cleanup not
yet run. Inside that window the launcher measures the post-compensation probe
(`lane3_launcher_probe.measure_post_compensation`) and replies
`compensation_probe_done`, or `abort`.

Fail-closed:

* a timeout, an `abort`, a malformed frame or a measurement that overran the
  target's deadline is NO PROBE: no proof is built, and the pair cannot publish
  (N7 fails; the positive row 8 is absent);
* the probe is only ever taken inside the window, never after `terminal`, because
  the target unwinds through its own cleanup before it reports and a probe after
  that would measure the host after cleanup, not after compensation;
* the proof bytes (exposure calls and probe) stay with the launcher only; they
  leave in memory to the positive drive, which encrypts them into the bundle.

The proof asserts nothing the offline verifier will not re-derive
(`lane3_negative_proof`, `lane3_offline_orchestrator`).
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import exposure_rehearsal_runner as controller_half
import lane3_launcher_lease as launcher_lease
import lane3_launcher_probe as launcher_probe
import lane3_negative_proof as negative_proof
from lane3_session_channel import Channel, ChannelRefused

__all__ = ["NegativeClaims", "PositivePair", "drive_negative"]

_MAX_CALLS = negative_proof.MAX_CALLS_BYTES


@dataclasses.dataclass(frozen=True, slots=True)
class NegativeClaims:
    """What X1 knows about the pair, for the NEGATIVE drive. All are CLAIMS the
    offline verifier re-derives; a wrong one only makes E9 refuse."""

    positive_dispatch_id: str
    dispatch_id: str
    target_id: str
    execution_sequence: int
    attempt_no: int


@dataclasses.dataclass(frozen=True, slots=True)
class PositivePair:
    """The negative drive's proof, handed (in memory) to the POSITIVE drive."""

    #: None when the negative drive produced no proof: row 8 is then absent.
    negative_proof: bytes | None = dataclasses.field(repr=False)


def _abort(channel: Channel, reason: str) -> None:
    try:
        channel.send("abort", {"reason": reason[:200]})
    except ChannelRefused:
        pass


def _await_terminal(
    channel: Channel, seconds: float, result: Any, code: str
) -> launcher_lease.Terminal | None:
    if code and not result.refusal_code:
        result.refusal_code = code
    try:
        _, data = channel.receive("terminal", timeout=seconds)
        result.terminal = launcher_lease.parse_terminal(data)
    except (ChannelRefused, launcher_lease.LeaseCustodyRefused) as exc:
        result.refusal_code = result.refusal_code or exc.code
        return None
    return result.terminal


def _calls(value: object) -> bytes:
    if not isinstance(value, str) or len(value) > _MAX_CALLS * 2:
        raise ValueError
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise ValueError from None
    if not raw or len(raw) > _MAX_CALLS:
        raise ValueError
    return raw


def drive_negative(
    channel: Channel,
    inputs: Any,
    result: Any,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> Any:
    """Drive the negative target from `proceed` to `terminal`; return `result`.

    `inputs` is the launcher's `DriveInputs` (its `pair` is `NegativeClaims`);
    `result` is its `DriveResult`, filled in place. The caller has already sent
    `proceed` and handles `ChannelRefused`.
    """
    claims = inputs.pair
    kind, data = channel.receive(
        "compensation_window_open", "terminal", timeout=inputs.plan.window_seconds
    )
    if kind == "terminal":
        # No compensation window: the Executor did not refuse-and-compensate.
        try:
            result.terminal = launcher_lease.parse_terminal(data)
        except launcher_lease.LeaseCustodyRefused as exc:
            result.refusal_code = exc.code
        result.refusal_code = result.refusal_code or "no-compensation-window"
        return result
    started = monotonic()
    deadline = data["deadline_seconds"]
    try:
        calls = _calls(data["exposure_calls_b64"])
        if type(deadline) is not int or deadline < 1:
            raise ValueError
    except ValueError:
        _abort(channel, "compensation-window-invalid")
        _await_terminal(
            channel, inputs.terminal_seconds, result, "compensation-window-invalid"
        )
        return result
    try:
        probe_bytes = launcher_probe.measure_post_compensation(
            inputs.probe_inputs,
            inputs.qualified_evidence,
            dispatch_id=claims.dispatch_id,
            host_id=inputs.held.lease.target,
            measurers=inputs.measurers,
        )
    except (
        controller_half.DeploymentFoundationError,
        negative_proof.ProofRefused,
        subprocess.TimeoutExpired,
        OSError,
    ):
        _abort(channel, "probe-measurement-refused")
        _await_terminal(
            channel, inputs.terminal_seconds, result, "probe-measurement-refused"
        )
        return result
    if monotonic() - started > deadline:
        # The target has stopped waiting and is (or soon will be) in cleanup: a
        # late probe would measure the wrong instant. It is no probe.
        _abort(channel, "compensation-window-timeout")
        _await_terminal(
            channel, inputs.terminal_seconds, result, "compensation-window-timeout"
        )
        return result
    channel.send("compensation_probe_done", {})
    terminal = _await_terminal(channel, inputs.terminal_seconds, result, "")
    if terminal is None:
        return result
    try:
        result.negative_proof = negative_proof.build_proof(
            positive_dispatch_id=claims.positive_dispatch_id,
            dispatch_id=claims.dispatch_id,
            target_id=claims.target_id,
            host_id=inputs.held.lease.target,
            execution_sequence=claims.execution_sequence,
            attempt_no=claims.attempt_no,
            lease_authorization_run=inputs.held.lease.authorization_run_id,
            terminal_refusal=(
                "" if terminal.refusal is None else terminal.refusal.value
            ),
            host_mutated=bool(terminal.host_mutated),
            exit_status=terminal.exit_status,
            exposure_calls=calls,
            probe_bytes=probe_bytes,
        )
    except negative_proof.ProofRefused as exc:
        result.refusal_code = result.refusal_code or exc.code
    return result
