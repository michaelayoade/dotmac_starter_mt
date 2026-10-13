"""Bounded owner of canonical campaign adapters; no activation or live CLI.

The root orchestrator supplies the measured, configured canonical operator.
Management ends CLOSED before independent human proof review. Calling B8 is a
separate protected owner action, never a consequence of broker decide PASS.
"""

import os
import re
import selectors
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

BUNDLE = "85edaf15e8eb97f5900986d7f1246d25274eede2c20e35deda607ffb244b2499"
HARNESS_SHA256 = "29f003a56baca7b00e8ccb3e75096c5219e805994a4e7add96b44c82cb7dc9cc"
SSH_DESTINATION = "pve-vm-124"
COORDINATOR_ALIAS = "vm124-l3mgmt"
LIBEXEC = "/usr/local/libexec/dotmac-lane3-campaign/"


class InvokerRefused(RuntimeError):
    pass


def ssh_args(role):
    if role not in ("bridge", "remote"):
        raise InvokerRefused("ssh-role")
    return [
        "ssh",
        "-T",
        "-oBatchMode=yes",
        "-oConnectTimeout=10",
        SSH_DESTINATION,
        "sudo -n /usr/bin/python3 -I -B " + LIBEXEC + "lane3_campaign_" + role + ".py",
    ]


def _signal_group(process, signum):
    # Groups are created here only. An exited leader can still own descendants.
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


@contextmanager
def _owned_process(args):
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        yield process
    finally:
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        finally:
            try:
                _signal_group(process, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            finally:
                try:
                    # Cover descendants even after a successful leader exit.
                    _signal_group(process, signal.SIGKILL)
                    process.wait(timeout=5)
                finally:
                    if process.stdout is not None:
                        process.stdout.close()


@contextmanager
def owned_ssh(role):
    with _owned_process(ssh_args(role)) as process:
        yield process


def supervised_harness(operator, tools, evidence_leaf):
    """Preserved 44-case activation harness, one 180-second parent deadline.

    The root first measures every tool against its approved fourteen-file
    manifest. This function additionally measures the exact harness itself.
    Evidence leaf must not exist; the original harness creates it exclusively.
    """
    tools, evidence_leaf = Path(tools), Path(evidence_leaf)
    if not tools.is_absolute() or not evidence_leaf.is_absolute():
        raise InvokerRefused("harness-path")
    if evidence_leaf.exists() or evidence_leaf.is_symlink():
        raise InvokerRefused("harness-evidence-present")
    script = tools / "l3mgmt-tests.sh"
    operator.measured(script, HARNESS_SHA256)
    deadline = time.monotonic() + 180
    with _owned_process(
        ["/bin/bash", str(script), "full", BUNDLE, str(evidence_leaf)]
    ) as process:
        process.stdin.close()
        output = bytearray()
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = operator.remaining(deadline)
                if not selector.select(remaining):
                    raise InvokerRefused("harness-deadline")
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 1_000_000:
                    raise InvokerRefused("harness-output-limit")
        process.wait(timeout=operator.remaining(deadline))
        if process.returncode != 0 or b"L3MGMT_TESTS_OK" not in output.splitlines():
            raise InvokerRefused("harness-unproved")
    return {"harness": "PASS", "evidence_leaf": str(evidence_leaf)}


def _terminal_run(operator, deadline):
    """Wait for scheduling completion only; this is NOT proof validation."""
    binding = operator.deployment_inputs()
    config = operator.coordinator_binding()
    repository = config.get("repository")
    if type(repository) is not str or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise InvokerRefused("run-repository")
    run_id = binding["github_run_id"]
    endpoint = f"repos/{repository}/actions/runs/{run_id}"
    while True:
        run = operator._run_owned(["gh", "api", endpoint], deadline)
        if (
            type(run) is not dict
            or type(run.get("id")) is not int
            or run["id"] != run_id
            or type(run.get("run_attempt")) is not int
            or run["run_attempt"] != 1
            or run.get("head_sha") != binding["qualification"]["workflow_sha"]
            or type(run.get("repository")) is not dict
            or type(run["repository"].get("id")) is not int
            or run["repository"]["id"] != binding["qualification"]["repository_id"]
            or run["repository"].get("full_name") != repository
        ):
            raise InvokerRefused("run-binding")
        if run.get("status") == "completed":
            return {
                "repository": repository,
                "run_id": run_id,
                "run_attempt": 1,
                "conclusion": run.get("conclusion"),
            }
        if run.get("status") not in (
            "queued",
            "in_progress",
            "waiting",
            "pending",
            "requested",
        ):
            raise InvokerRefused("run-status")
        time.sleep(min(2, operator.remaining(deadline)))


def management_checkpoint(operator):
    """After activation/tests/fail2ban close/key reload, retain once and close.

    No activation, timer reset, retry or proof admission is performed here.
    Returned archive is actual authenticated canonical finish output. The
    independent root reviews exact workflow steps/proof/baseline afterward.
    """
    binding = operator.deployment_inputs()
    if binding["ssh_destination"] != COORDINATOR_ALIAS:
        raise InvokerRefused("ssh-binding")
    operator.coordinator_binding()
    # Retain must precede original start+220. Include launch in this 420-second
    # ceiling, leaving 180 for canonical finish before original start+840.
    deadline = time.monotonic() + 420
    with owned_ssh("bridge") as process:
        management = operator.ManagementStdio(process)
        try:
            management.retained_launch()  # one canonical absolute 60s call
            decision = operator.coordinator_cli("decide", deadline)
            if (
                type(decision) is not dict
                or set(decision) != {"status", "category", "manifest_digest"}
                or decision["status"] != "GRANTED"
            ):
                raise InvokerRefused("broker-decision-refused")
            run = _terminal_run(operator, deadline)
            closed = management.finish()
            return {
                "run": run,
                "qualification": binding["qualification"],
                "closure": closed,
                "proof_acceptance": "ROOT_REVIEW_REQUIRED",
            }
        except BaseException:
            if (
                management.phase in ("retained", "prepared", "bootstrapped")
                and not management.finish_attempted
            ):
                try:
                    management.failure_finalize()
                except BaseException:
                    management.failure = "UNPROVEN"
            raise
        finally:
            management.close()


def b8_exchange(operator):
    """Explicit root invocation AFTER independent CLOSED/proof/baseline review.

    There is deliberately no automatic transition from management or boolean
    approval parameter. The measured owner caller authorizes this phase. Its
    prepared custody and exchange remain in this same process.
    """
    if operator.deployment_inputs()["ssh_destination"] != COORDINATOR_ALIAS:
        raise InvokerRefused("ssh-binding")
    session = operator.B8Stdio()
    try:
        session.prepare_custody()
        with owned_ssh("remote") as process:
            return session.exchange(process)
    finally:
        session.close()
