"""Invoke Foundation (the sole runner). The agent launches; it never runs the sequence.

The environment handed to the runner is an explicit allow-list: nothing is
inherited, so no DB credential or secret-zero can leak into Foundation. The
variable names below are the agent-to-bindings contract (WP5a reads them).
"""

from __future__ import annotations

import os
import signal
import subprocess  # nosec B404 - argv list, shell=False, absolute executable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

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
    #: Foundation mounts ONLY these files (per file, read-only), not the directory.
    job_secret_files: tuple[str, ...] = ()
    #: The render unit's InvocationID (32 hex): Foundation re-reads the unit and
    #: requires it to match, proving the result is from THIS dispatch. Role jobs only.
    job_render_invocation: str | None = None


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    timed_out: bool


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
    return env


class SubprocessRunner:
    #: Foundation's operator-job entry does not exist yet (it waits on Michael's
    #: approval of the increment). The launcher checks this BEFORE any lease, mint
    #: or file, and `run` refuses a job spec too: no Foundation entry point that
    #: is not there is ever called.
    job_entry_available = False

    def __init__(self, argv: tuple[str, ...], timeout_seconds: int) -> None:
        if not argv or not os.path.isabs(argv[0]):
            raise Refusal("config_invalid")
        self._argv = argv
        self._timeout = timeout_seconds

    def run(self, spec: RunSpec) -> RunResult:
        if spec.job_name is not None:
            raise Refusal("foundation_job_entry_unavailable")
        try:
            process = subprocess.Popen(  # nosec B603  # noqa: S603
                list(self._argv),
                env=runner_environment(spec),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            raise Refusal("runner_start_failed") from None
        try:
            return RunResult(process.wait(timeout=self._timeout), False)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return RunResult(None, True)
