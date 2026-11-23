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
from datetime import datetime
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import exposure_rehearsal_runner as controller_half
import lane3_e10_receipt
import lane3_evidence_finalization as evidence_finalization
import lane3_launcher_evidence as launcher_evidence
import lane3_launcher_lease as launcher_lease
import lane3_launcher_negative as launcher_negative
import lane3_launcher_probe as launcher_probe
import lane3_launcher_stage as lane3_stage
import lane3_lease_store
import lane3_negative_proof as negative_proof
from dotmac_deployment_foundation.rehearsal import (
    ExecutionRunBindingV1,
    RehearsalReceiptV2,
)
from lane3_session_channel import Channel, ChannelRefused

__all__ = [
    "Conclusion",
    "DriveInputs",
    "DriveResult",
    "SessionPlan",
    "StderrSink",
    "build_start",
    "conclude",
    "drive",
    "open_ssh_channel",
]


@dataclasses.dataclass(frozen=True, slots=True)
class SessionPlan:
    """The start parameters that are NOT derivable from the pin or the lease.

    Everything that identifies the host, the lease, the controller or the run is
    deliberately absent: :func:`drive` derives those itself from the pinned host
    key and the held lease, so a caller cannot hand the target a start frame that
    disagrees with either.
    """

    run_dir: str
    deploy_dir: str
    lock_dir: str
    foundation_revision: str
    candidate_source_revision: str
    foundation_artifact: str
    candidate_version: str
    probe_vantage_ref: str
    execution_run: ExecutionRunBindingV1
    expected_machine_id: str
    timeout: int
    window_seconds: int
    evidence_seconds: int
    proceed_seconds: int
    #: S8b: `positive` or `negative`. One X1 run drives both, negative first.
    role: str


@dataclasses.dataclass(frozen=True, slots=True)
class DriveInputs:
    """Everything one drive needs. Every field is required; nothing defaults."""

    pin: lane3_stage.PinnedHostKey
    #: `sha256:<hex>` of the raw incarnation public key (CP's
    #: `installed_signer_fingerprint` form). Its own required input: it is NOT the
    #: SSH pin, and the E10 receipt is what links the two.
    expected_host_incarnation: str
    e10: lane3_e10_receipt.E10ReceiptRef
    #: The exact `execution-context.json` that was staged: its
    #: `host_enrolment_ref` must be the receipt's enrolment UUID.
    execution_context: lane3_stage.StagedFile
    #: What `stage_artifacts` returned (token-bound). `drive` requires it to be this
    #: lease's staging and its `execution-context.json` digest to be
    #: `execution_context`'s, so the context checked against the receipt is the
    #: file that reached the target, never a second one.
    staged: lane3_stage.StagedRun
    store: lane3_lease_store.LeaseStore
    held: lane3_lease_store.HeldLease
    plan: SessionPlan
    probe_inputs: launcher_probe.ProbeInputs
    qualified_evidence: dict[str, Any]
    fixture_digest: str
    encryption: evidence_finalization.EncryptionContext
    evidence_output: pathlib.Path
    ready_seconds: float
    terminal_seconds: float
    #: S8b: `launcher_negative.NegativeClaims` for the negative role,
    #: `launcher_negative.PositivePair` for the positive role.
    pair: launcher_negative.NegativeClaims | launcher_negative.PositivePair
    measurers: launcher_probe.Measurers | None = None


def build_start(inputs: DriveInputs, store_time: datetime) -> dict[str, Any]:
    """The start frame, DERIVED: identity from lease and pin, time from the store."""
    plan, lease = inputs.plan, inputs.held.lease
    run_dir = plan.run_dir
    return {
        "foundation_revision": plan.foundation_revision,
        "candidate_source_revision": plan.candidate_source_revision,
        "foundation_artifact": plan.foundation_artifact,
        "authorization_run": lease.authorization_run_id,
        "controller_identity": str(lease.controller_identity_fingerprint),
        "host_id": lease.target,
        "candidate_version": plan.candidate_version,
        "descriptor": f"{run_dir}/product.toml",
        "authorization_document": f"{run_dir}/authorization.json",
        "deploy_dir": plan.deploy_dir,
        "lock_dir": plan.lock_dir,
        "lease_dir": f"{run_dir}/lease",
        "timeout": plan.timeout,
        "probe_vantage_ref": plan.probe_vantage_ref,
        "execution_run": plan.execution_run.as_document(),
        "expected_machine_id": plan.expected_machine_id,
        "expected_host_incarnation": inputs.expected_host_incarnation,
        "window_seconds": plan.window_seconds,
        "evidence_seconds": plan.evidence_seconds,
        "proceed_seconds": plan.proceed_seconds,
        "store_time": store_time.isoformat(),
        "role": plan.role,
    }


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
    #: The NEGATIVE role's unsigned compensation proof (S8b), held in memory only.
    negative_proof: bytes | None = dataclasses.field(default=None, repr=False)
    #: `store-unreachable` or `store-commit-unknown` when the store dropped
    #: during the drive, else "". Never retried (design 3.6).
    store_failure: str = ""


_STORE_DROPS = (
    lane3_lease_store.STORE_UNREACHABLE,
    lane3_lease_store.STORE_COMMIT_UNKNOWN,
)


def _store_refused(result: DriveResult, exc: lane3_lease_store.StoreRefused) -> None:
    """Record a store refusal; a dropped connection is named as such."""
    result.refusal_code = result.refusal_code or exc.code
    if exc.code in _STORE_DROPS and not result.store_failure:
        result.store_failure = exc.code


_DROP_OUTCOMES = {
    lane3_lease_store.STORE_UNREACHABLE: "store_unreachable",
    lane3_lease_store.STORE_COMMIT_UNKNOWN: "store_commit_unknown",
}


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
    expected_pair = {
        "negative": launcher_negative.NegativeClaims,
        "positive": launcher_negative.PositivePair,
    }.get(inputs.plan.role)
    if expected_pair is None or type(inputs.pair) is not expected_pair:
        result.refusal_code = "role-pair-mismatch"
        return result
    try:
        # The target's only notion of "now" is the STORE's time, taken here, not
        # this runner's or the target's own clock.
        try:
            lane3_lease_store.require_database_time(inputs.store)
            store_time = inputs.store.database_time()
        except lane3_lease_store.StoreRefused as exc:
            # Before `start`: nothing was sent, the target is untouched. A dropped
            # store ends here; the lease is left to expire on store time.
            _store_refused(result, exc)
            return result
        held = inputs.held
        if not held.acquired_at <= store_time < held.expires_at:
            result.refusal_code = "store-time-outside-lease-window"
            return result
        # Everything below is checked BEFORE anything is sent to the target.
        # The staging must be this lease's, and the context checked against the
        # receipt must be the file that was staged, byte for byte.
        try:
            staged = inputs.staged
            if not isinstance(staged, lane3_stage.StagedRun):
                raise lane3_stage.StageRefused("staged-run-required")
            staged.require_for(
                run_dir=inputs.plan.run_dir,
                host_id=held.lease.target,
                run_id=held.lease.authorization_run_id,
            )
            digest = staged.digest_of("execution-context.json")
            if digest is None:
                raise lane3_stage.StageRefused("execution-context-not-staged")
            try:
                lane3_stage._require_staged(
                    "execution-context.json", inputs.execution_context
                )
            except lane3_stage.StageRefused:
                raise lane3_stage.StageRefused(
                    "execution-context-digest-mismatch"
                ) from None
            if digest != inputs.execution_context.sha256:
                raise lane3_stage.StageRefused("execution-context-not-the-staged-file")
        except lane3_stage.StageRefused as exc:
            result.refusal_code = exc.code
            return result
        try:
            lane3_e10_receipt.require_vm_slot(inputs.e10)
            inputs.e10.require_matches(
                ssh_fingerprint=inputs.pin.fingerprint,
                alias=inputs.pin.alias,
                expected_host_incarnation=inputs.expected_host_incarnation,
                host_id=held.lease.target,
                machine_id=inputs.plan.expected_machine_id,
                store_time=store_time,
            )
            lane3_e10_receipt.require_enrolment_matches(
                inputs.execution_context.data, inputs.e10
            )
        except lane3_e10_receipt.ReceiptRefused as exc:
            result.refusal_code = exc.code
            return result
        # The receipt is the one the lease was taken under, not a second receipt.
        if inputs.e10 != held.e10_receipt:
            result.refusal_code = "receipt-not-the-leases"
            return result
        # ...and the very receipt staging checked (its digest is token-bound).
        try:
            inputs.staged.require_receipt(inputs.e10)
        except lane3_stage.StageRefused as exc:
            result.refusal_code = exc.code
            return result
        channel.send("start", build_start(inputs, store_time))
        kind, data = channel.receive("ready", "terminal", timeout=inputs.ready_seconds)
        if kind == "terminal":
            return _finish_terminal(result, data)
        result.ready_seen = True
        result.plan_digest = data["plan_digest"]
        if (
            data["host_id"] != inputs.held.lease.target
            or data["machine_id"] != inputs.plan.expected_machine_id
            or data["host_incarnation"] != inputs.expected_host_incarnation
            or not data["plan_digest"]
        ):
            _abort(channel, "placement-mismatch")
            return _terminal(
                channel, inputs.terminal_seconds, result, "placement-mismatch"
            )
        # The lease must still cover this run on STORE time at the moment the
        # target is let go. A store that says no aborts the target here.
        try:
            inputs.store.covers(
                inputs.held, authorization_run=inputs.held.lease.authorization_run_id
            )
        except lane3_lease_store.StoreRefused as exc:
            # After `start`, before `proceed`: the target unwinds through its own
            # cleanup and consumes nothing; the lease expires as mutated.
            _store_refused(result, exc)
            _abort(channel, "lease-not-covering")
            return _terminal(channel, inputs.terminal_seconds, result, exc.code)
        # The gate: the target takes no grant and no lock until it reads this.
        channel.send("proceed", {"plan_digest": data["plan_digest"]})

        if inputs.plan.role == "negative":
            return launcher_negative.drive_negative(channel, inputs, result)

        kind, data = channel.receive(
            "window_open", "terminal", timeout=inputs.plan.window_seconds
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
        # S8b: row 8 is a row the LAUNCHER asserts from the negative drive's proof.
        # Without a proof the row is absent and the target keeps it BLOCKED.
        negative = inputs.pair.negative_proof
        if negative is not None:
            try:
                rows = [*rows, negative_proof.compensation_row(negative)]
            except negative_proof.ProofRefused:
                _abort(channel, "negative-proof-invalid")
                return _terminal(
                    channel,
                    inputs.terminal_seconds,
                    result,
                    "negative-proof-invalid",
                )
        channel.send("external_results", {"rows": rows})

        kind, data = channel.receive(
            "evidence_request", "terminal", timeout=inputs.plan.evidence_seconds
        )
        if kind == "terminal":
            return _finish_terminal(result, data)
        try:
            result.ciphertext = launcher_evidence.finalize(
                probe_bytes=measurement.document_bytes(),
                exposure_calls_b64=data["exposure_calls_b64"],
                negative_compensation=negative,
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
            "receipt", "terminal", timeout=inputs.plan.evidence_seconds
        )
        if kind == "terminal":
            return _finish_terminal(result, data)
        code = ""
        try:
            result.receipt = launcher_evidence.verify_receipt(
                receipt_b64=data["receipt_b64"],
                receipt_digest=data["receipt_digest"],
                ciphertext=result.ciphertext,
                execution_run=inputs.plan.execution_run,
                controller_identity=str(
                    inputs.held.lease.controller_identity_fingerprint
                ),
                lease_id=inputs.held.lease.authorization_run_id,
                host_id=inputs.held.lease.target,
                sent_rows=rows,
                foundation_revision=inputs.plan.foundation_revision,
                foundation_artifact=inputs.plan.foundation_artifact,
                fixture_digest=inputs.fixture_digest,
                probe_vantage_ref=inputs.plan.probe_vantage_ref,
                plan_digest=result.plan_digest,
            )
        except launcher_evidence.EvidenceHandBackRefused as exc:
            code = exc.code
        return _terminal(channel, inputs.terminal_seconds, result, code)
    except ChannelRefused as exc:
        result.refusal_code = exc.code
        return result


@dataclasses.dataclass(frozen=True, slots=True)
class Conclusion:
    """How a run ended, and the records it left. `outcome` is closed."""

    outcome: str
    release_digest: str
    custody_code: str
    paths: tuple[pathlib.Path, ...]


def conclude(
    result: DriveResult,
    *,
    store: lane3_lease_store.LeaseStore,
    held: lane3_lease_store.HeldLease,
    controller_identity: Any,
    expected_machine_id: str,
    release_args: Any,
    output_dir: pathlib.Path,
) -> Conclusion:
    """Release if there is a reconcilable terminal report, and ALWAYS leave records.

    Outcomes: `released`, `refused` (a terminal report the launcher would not
    reconcile or could not write), `store_unreachable` and `store_commit_unknown`
    (the release write hit a dropped store; sent once, never retried, and for the
    second the release may exist, so standing must be read), `no_release` (a
    report naming no member of the closed vocabulary) and `no_terminal` (the
    target never reported). In every
    one the create-only evidence file is written, so a refusal, a release and a
    no-release are each on record and absence is never silent. When no release is
    written the lease is left to EXPIRE on store time as a mutated host.
    """
    outcome, digest, custody_code, record = "no_terminal", "", "", None
    unconfirmed: Any = None
    verified = result.receipt.sha256_digest() if result.receipt is not None else ""
    error: BaseException | None = None
    if result.terminal is not None and result.store_failure:
        # The store already dropped during the drive: nothing further is sent to
        # it (design 3.6). The lease is left to expire on store time.
        outcome = _DROP_OUTCOMES[result.store_failure]
    elif result.terminal is not None:
        try:
            digest, record = launcher_lease.release_with_record(
                result.terminal,
                store=store,
                held=held,
                controller_identity=controller_identity,
                expected_machine_id=expected_machine_id,
                verified_receipt_digest=verified,
                ready_seen=result.ready_seen,
                release_args=release_args,
            )
            outcome = "released" if digest else "no_release"
        except launcher_lease.LeaseCustodyRefused as exc:
            outcome, custody_code = "refused", exc.code
            # The release write is sent once and never retried (design 3.6). A
            # dropped store is its own outcome; for `store-commit-unknown` the
            # release MAY have committed, so standing is read afterwards through
            # the ceremony's owner session and nothing further is written to the
            # store. The release that was sent is kept as an UNCONFIRMED copy.
            if exc.code in _DROP_OUTCOMES:
                outcome = _DROP_OUTCOMES[exc.code]
            if exc.code == lane3_lease_store.STORE_COMMIT_UNKNOWN:
                unconfirmed = exc.release_record
        except Exception as exc:
            outcome, error = "error", exc
    terminal = result.terminal
    store_failure = result.store_failure or (
        custody_code if custody_code in _STORE_DROPS else ""
    )
    commit_unknown = custody_code == lane3_lease_store.STORE_COMMIT_UNKNOWN
    stamp: str | None = None
    stamp_unavailable = False
    if digest:
        # The release committed. The store's own stamp is read once; a drop here
        # must not lose the evidence or the release copy.
        try:
            stored = store.released_at(held.lease.target)
            stamp = stored.isoformat() if stored is not None else None
        except lane3_lease_store.StoreRefused as exc:
            stamp_unavailable = True
            store_failure = store_failure or (
                exc.code
                if exc.code in _STORE_DROPS
                else lane3_lease_store.STORE_UNREACHABLE
            )
        except Exception as exc:
            # A code bug is NOT a store drop: no stamp, the evidence is still
            # written, and the original exception propagates after it.
            outcome, error = "error", error or exc
    evidence = {
        "document": "lane3-launcher-terminal",
        "version": 1,
        "outcome": outcome,
        "drive_refusal_code": result.refusal_code,
        "custody_code": custody_code,
        # `None` = unknown: the commit may or may not have happened.
        "release_written": None if commit_unknown else bool(digest),
        "release_digest": digest,
        "lease_left_to_expire_as_mutated": None if commit_unknown else not digest,
        "store_failure": store_failure,
        "standing_read_required": (
            store_failure == lane3_lease_store.STORE_COMMIT_UNKNOWN or stamp_unavailable
        ),
        "ready_seen": result.ready_seen,
        "plan_digest": result.plan_digest,
        "receipt_digest": verified,
        "target_reported": terminal is not None,
        "target_refusal": (
            terminal.refusal.value
            if terminal is not None and terminal.refusal is not None
            else ""
        ),
        "target_host_mutated": bool(terminal.host_mutated) if terminal else None,
        "host_mutated_recorded": bool(
            (terminal.host_mutated if terminal else False) or result.ready_seen
        ),
        "cleanup": terminal.cleanup.value if terminal is not None else "",
        "target_exit_status": terminal.exit_status if terminal is not None else None,
    }
    if digest:
        evidence["runner_released_at"] = str(getattr(record, "released_at", ""))
        evidence["store_released_at"] = stamp or ""
    try:
        paths = launcher_lease.write_terminal_records(
            output_dir,
            evidence=evidence,
            release_record=record if digest else None,
            released_at=stamp,
            unconfirmed_record=unconfirmed,
        )
    except Exception as write_error:
        if error is not None:
            # The ORIGINAL failure stays the exception; the write failure is
            # recorded on it rather than replacing it.
            error.add_note(f"terminal records also failed: {write_error!r}")
            raise error from None
        raise
    if error is not None:
        raise error
    return Conclusion(
        outcome=outcome,
        release_digest=digest,
        custody_code=custody_code,
        paths=tuple(paths),
    )


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
