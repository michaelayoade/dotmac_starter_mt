"""Invoke Foundation (the sole runner). The agent launches; it never runs the sequence.

The environment handed to the runner is an explicit allow-list: nothing is
inherited, so no DB credential or secret-zero can leak into Foundation. The
variable names below are the agent-to-bindings contract (WP5a reads them).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess  # nosec B404 - argv list, shell=False, absolute executable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from .jobs import JOB_TERM_GRACE
from .refusal import Refusal

ENV_PATH: Final = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class RunSpec:
    dispatch_id: str
    target_ref: str
    product: str
    plan_digest: str
    params_env: Path
    agent_config: Path | None
    bindings_env: tuple[tuple[str, str], ...] = ()
    credentials_directory: str | None = None
    legacy_env: Path | None = None
    lease_id: str | None = None
    #: A signed role job (`run_job`): the four variables Foundation's operator-job
    #: entry reads. All four or none.
    job_name: str | None = None
    job_params_env: Path | None = None
    job_secrets_dir: Path | None = None
    job_argv_digest: str | None = None
    #: Upper bound on this run, set from what is left of the host lease (WP3B).
    #: The runner uses the smaller of this and its configured timeout.
    timeout_seconds: int | None = None
    #: Foundation mounts ONLY these files (per file, read-only), not the directory.
    job_secret_files: tuple[str, ...] = ()
    #: The render unit's InvocationID (32 hex): Foundation re-reads the unit and
    #: requires it to match, proving the result is from THIS dispatch. Role jobs only.
    job_render_invocation: str | None = None
    #: R6-1: the ABSOLUTE deadline (epoch seconds) Foundation stops the job at,
    #: `start + timeout_seconds`, and the agent's own kill bound in seconds from the
    #: start: the deadline plus a margin that covers Foundation's stop, rm and receipt.
    job_deadline: int | None = None
    job_bound_seconds: int | None = None


#: Foundation writes its job receipt as the LAST JSON line of its stdout. The agent
#: keeps at most this many trailing bytes (the job's own stdio is DEVNULL inside
#: Foundation, so no job output is mixed in).
MAX_RECEIPT_BYTES: Final = 64 * 1024
#: Foundation's stdout is small. A capture larger than this is flagged `oversized`
#: (a clean exit then fails as `job_receipt_invalid`) but is never limited while the
#: job runs: RLIMIT_FSIZE would raise SIGXFSZ and could kill Foundation mid-cleanup.
MAX_CAPTURE_BYTES: Final = 1024 * 1024


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    timed_out: bool
    #: A `run_job` invocation only: Foundation's receipt (the last JSON line of its
    #: stdout) or, when there is none, why (`missing` / `malformed`).
    receipt: dict[str, object] | None = None
    receipt_error: str | None = None


def parse_receipt(tail: bytes) -> tuple[dict[str, object] | None, str | None]:
    """The last non-empty line must be a JSON object, else `missing`/`malformed`."""
    lines = [
        ln for ln in tail.decode("utf-8", errors="replace").splitlines() if ln.strip()
    ]
    if not lines:
        return None, "missing"
    try:
        document = json.loads(lines[-1])
    except ValueError:
        return None, "malformed"
    if not isinstance(document, dict):
        return None, "malformed"
    return document, None


class Runner(Protocol):
    def run(self, spec: RunSpec) -> RunResult: ...


def runner_environment(spec: RunSpec) -> dict[str, str]:
    """An allow-list. The bindings' own names come from the root-owned agent
    config (`bindings_env`); the agent adds `DOTMAC_DEPLOY_*` and
    `CREDENTIALS_DIRECTORY`, from which the bindings read only `pop-key` to sign
    `consume`. No DSN, no token, nothing inherited (ruling R3)."""
    env = {
        "PATH": ENV_PATH,
        "LANG": "C.UTF-8",
        **dict(spec.bindings_env),
        "DOTMAC_DEPLOY_DISPATCH_ID": spec.dispatch_id,
        "DOTMAC_DEPLOY_TARGET_REF": spec.target_ref,
        "DOTMAC_DEPLOY_PRODUCT": spec.product,
        "DOTMAC_DEPLOY_PARAMS_ENV": str(spec.params_env),
        "DOTMAC_DEPLOY_PLAN_DIGEST": spec.plan_digest,
    }
    if spec.agent_config is not None:
        env["DOTMAC_DEPLOY_AGENT_CONFIG"] = str(spec.agent_config)
    if spec.credentials_directory is not None:
        env["CREDENTIALS_DIRECTORY"] = spec.credentials_directory
    if spec.legacy_env is not None:
        env["DOTMAC_DEPLOY_LEGACY_ENV"] = str(spec.legacy_env)
    if spec.lease_id is not None:
        env["DOTMAC_DEPLOY_LEASE_ID"] = spec.lease_id
    if spec.job_name is not None:
        if (
            spec.job_params_env is None
            or spec.job_secrets_dir is None
            or spec.job_argv_digest is None
        ):
            raise Refusal("runner_start_failed")
        env["DOTMAC_DEPLOY_JOB"] = spec.job_name
        env["DOTMAC_DEPLOY_JOB_PARAMS_ENV"] = str(spec.job_params_env)
        env["DOTMAC_DEPLOY_JOB_SECRETS_DIR"] = str(spec.job_secrets_dir)
        env["DOTMAC_DEPLOY_JOB_ARGV_DIGEST"] = spec.job_argv_digest
        env["DOTMAC_DEPLOY_JOB_SECRET_FILES"] = ",".join(spec.job_secret_files)
        if spec.job_render_invocation is not None:
            env["DOTMAC_DEPLOY_JOB_RENDER_INVOCATION"] = spec.job_render_invocation
        if spec.job_deadline is None:
            raise Refusal("runner_start_failed")  # a job always has a deadline
        env["DOTMAC_DEPLOY_JOB_DEADLINE"] = str(spec.job_deadline)
    return env


class SubprocessRunner:
    #: Foundation's operator-job entry does not exist yet (it waits on Michael's
    #: approval of the increment). The launcher checks this BEFORE any lease, mint
    #: or file, and `run` refuses a job spec too: no Foundation entry point that
    #: is not there is ever called.
    job_entry_available = False

    def __init__(
        self,
        argv: tuple[str, ...],
        timeout_seconds: int,
        *,
        term_grace_seconds: float = 60.0,
        job_entry_available: bool = False,
    ) -> None:
        if not argv or not os.path.isabs(argv[0]):
            raise Refusal("config_invalid")
        self._argv = argv
        self._timeout = timeout_seconds
        #: How long SIGTERM is given to end the process group before any SIGKILL.
        self._grace = term_grace_seconds
        self.job_entry_available = job_entry_available

    def run(self, spec: RunSpec) -> RunResult:
        if spec.job_name is not None and not self.job_entry_available:
            raise Refusal("foundation_job_entry_unavailable")
        capture: int | None = None
        if spec.job_name is not None:
            # An anonymous file (opened, then unlinked) in the agent's own run
            # directory (its RuntimeDirectory, never /run/dotmac), not a pipe: nothing
            # has to drain it while the job runs, and only the TAIL is ever read.
            path = spec.params_env.parent / f".foundation-stdout-{os.getpid()}"
            try:
                capture = os.open(
                    path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                os.unlink(path)
            except OSError:
                raise Refusal("runner_start_failed") from None
        try:
            process = subprocess.Popen(  # nosec B603  # noqa: S603
                list(self._argv),
                env=runner_environment(spec),
                stdin=subprocess.DEVNULL,
                stdout=capture if capture is not None else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            if capture is not None:
                os.close(capture)
            raise Refusal("runner_start_failed") from None
        bound = spec.job_bound_seconds if spec.job_bound_seconds else self._timeout
        grace = (
            min(self._grace, JOB_TERM_GRACE)
            if spec.job_name is not None
            else self._grace
        )
        try:
            limit = (
                bound
                if spec.timeout_seconds is None
                else min(bound, spec.timeout_seconds)
            )
            try:
                code = process.wait(timeout=limit)
                result = RunResult(code, False)
            except subprocess.TimeoutExpired:
                # SIGTERM only first: Foundation stops and removes its job container
                # and writes its receipt. SIGKILL skips that `finally`, so it comes
                # only after the grace, as a last resort.
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                result = RunResult(None, True)
            if capture is None:
                return result
            size = os.fstat(capture).st_size
            tail = os.pread(
                capture, MAX_RECEIPT_BYTES, max(0, size - MAX_RECEIPT_BYTES)
            )
            receipt, error = parse_receipt(tail)
            if size > MAX_CAPTURE_BYTES:
                receipt, error = None, "oversized"  # only the tail was read
            return RunResult(result.exit_code, result.timed_out, receipt, error)
        finally:
            if capture is not None:
                os.close(capture)
