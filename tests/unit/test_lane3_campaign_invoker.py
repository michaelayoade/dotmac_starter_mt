"""Canonical invoker behavior; real child execution belongs to hosted CI only."""

from __future__ import annotations

import importlib
import os
import pathlib
import selectors
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))


def invoker():
    return importlib.import_module("lane3_campaign_invoker")


@pytest.fixture
def owned_child(monkeypatch):
    """Replace transport only, retaining real pipes, sessions and descendants."""
    module = invoker()
    popen = subprocess.Popen
    processes = []
    calls = []

    def install(code):
        def launch(args, **kwargs):
            calls.append((args, kwargs))
            process = popen([sys.executable, "-I", "-B", "-c", code], **kwargs)
            processes.append(process)
            return process

        monkeypatch.setattr(module.subprocess, "Popen", launch)
        return processes, calls

    yield install
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


@pytest.mark.parametrize("role", ["bridge", "remote"])
@pytest.mark.parametrize("failure", [False, True])
def test_owned_ssh_closes_pipes_and_reaps_actual_group(owned_child, role, failure):
    module = invoker()
    processes, calls = owned_child("import time\ntime.sleep(30)\n")
    expected_error = RuntimeError("synthetic-operation-failed")
    try:
        with module.owned_ssh(role) as process:
            assert process.pid == os.getpgid(process.pid)
            assert process.stdin is not None and process.stdout is not None
            if failure:
                raise expected_error
    except RuntimeError as error:
        assert failure and error is expected_error
    assert len(processes) == 1
    assert calls[0][0] == module.ssh_args(role)
    assert calls[0][1]["start_new_session"] is True
    assert process.poll() is not None
    assert process.stdin.closed and process.stdout.closed


@pytest.mark.parametrize("role", ["", "management", "remote;true", [], None])
def test_unknown_ssh_role_never_creates_a_process(owned_child, role):
    module = invoker()
    processes, _ = owned_child("raise SystemExit(99)")
    with pytest.raises((ValueError, RuntimeError, TypeError, KeyError)):
        with module.owned_ssh(role):
            pytest.fail("an unknown role acquired a process")
    assert processes == []


@pytest.mark.parametrize("failure", [None, "custody", "exchange"])
def test_b8_custody_precedes_actual_remote_launch_and_closes_on_failure(
    owned_child, failure
):
    module = invoker()
    events = []
    processes, _ = owned_child("import sys\nsys.stdin.buffer.read()\n")
    result = {
        "matrix_result": {},
        "full_b8_complete": False,
        "admission_changed": False,
    }

    class Exchange:
        def prepare_custody(self):
            events.append("custody")
            assert not processes
            if failure == "custody":
                raise RuntimeError("synthetic-custody-failure")

        def exchange(self, process):
            events.append("exchange")
            assert process is processes[0] and process.poll() is None
            if failure == "exchange":
                raise RuntimeError("synthetic-exchange-failure")
            return result

        def close(self):
            events.append("close")

    operator = SimpleNamespace(
        B8Stdio=Exchange,
        deployment_inputs=lambda: {"ssh_destination": "vm124-l3mgmt"},
    )
    if failure:
        with pytest.raises(RuntimeError, match="synthetic-"):
            module.b8_exchange(operator)
    else:
        assert module.b8_exchange(operator) == result
    assert events[0] == "custody" and events[-1] == "close"
    assert len(processes) == (0 if failure == "custody" else 1)
    if processes:
        assert processes[0].poll() is not None
        assert processes[0].stdin.closed and processes[0].stdout.closed


def test_owned_ssh_kills_descendant_after_its_leader_has_exited(owned_child):
    module = invoker()
    processes, _ = owned_child(
        "import os,time\n"
        "if os.fork() == 0:\n"
        " os.write(1, str(os.getpid()).encode()+b'\\n')\n"
        " time.sleep(30)\n"
        "else: os._exit(0)\n"
    )
    with module.owned_ssh("bridge") as process:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(5), "synthetic child did not become ready"
            child_pid = int(process.stdout.readline(32))
        process.wait(timeout=5)
        assert process.returncode == 0
        assert os.getpgid(child_pid) == process.pid
    assert processes[0].stdout.closed
    # Hosted Linux may briefly retain a killed orphan as a zombie. It must
    # never remain runnable or keep a transport pipe alive.
    stat = pathlib.Path(f"/proc/{child_pid}/stat")
    deadline = time.monotonic() + 5
    while stat.exists():
        try:
            if stat.read_text().split(") ", 1)[1].split()[0] == "Z":
                break
        except FileNotFoundError:
            break
        assert time.monotonic() < deadline, "owned descendant survived cleanup"
        time.sleep(0.01)


def management_operator(defect=None):
    events = []
    qualification = {"workflow_sha": "a" * 40, "repository_id": 9}
    closure = {"archive": {"evidence": {"outcome": "BOUNDED_PROOF"}}}

    class Management:
        phase = "initial"
        finish_attempted = False

        def __init__(self, process):
            self.process = process

        def retained_launch(self):
            events.append("retain")
            self.phase = "bootstrapped"
            if defect == "retain":
                raise EOFError("synthetic-retain-eof")

        def finish(self):
            events.append("finish")
            self.finish_attempted = True
            if defect == "finish":
                raise RuntimeError("synthetic-archive-unproved")
            self.phase = "finished"
            return closure

        def failure_finalize(self):
            events.append("failure-finalize")
            self.finish_attempted = True
            self.phase = "finished"

        def close(self):
            events.append("close")
            self.process.stdin.close()

    def decide(operation, deadline):
        events.append((operation, deadline))
        if defect == "decide-eof":
            raise EOFError("synthetic-decide-eof")
        return {
            "status": "REFUSED" if defect == "decide" else "GRANTED",
            "category": "synthetic",
            "manifest_digest": "b" * 64,
        }

    def run(args, deadline):
        events.append((tuple(args), deadline))
        if defect == "timeout":
            raise TimeoutError("synthetic-terminal-timeout")
        return {
            "id": 12,
            "run_attempt": 1,
            "head_sha": "a" * 40,
            "repository": {"id": 9, "full_name": "synthetic/repository"},
            "status": "completed",
            "conclusion": "failure",
        }

    return (
        SimpleNamespace(
            ManagementStdio=Management,
            deployment_inputs=lambda: {
                "ssh_destination": "vm124-l3mgmt",
                "github_run_id": 12,
                "qualification": qualification,
            },
            coordinator_binding=lambda: {"repository": "synthetic/repository"},
            coordinator_cli=decide,
            _run_owned=run,
        ),
        events,
        closure,
    )


def test_management_returns_closed_checkpoint_without_accepting_workflow_proof(
    owned_child,
):
    module = invoker()
    processes, _ = owned_child("import sys\nsys.stdin.buffer.read()\n")
    operator, events, closure = management_operator()
    result = module.management_checkpoint(operator)
    assert result["closure"] is closure
    assert result["proof_acceptance"] == "ROOT_REVIEW_REQUIRED"
    assert result["run"]["conclusion"] == "failure"
    assert events[0] == "retain" and events[-2:] == ["finish", "close"]
    assert events.count("retain") == 1
    assert events[1][0] == "decide"
    assert events[2][0] == ("gh", "api", "repos/synthetic/repository/actions/runs/12")
    assert events[1][1] == events[2][1]
    assert processes[0].poll() is not None
    assert not {"B8Stdio", "proof_accepted"} & result.keys()


@pytest.mark.parametrize(
    "defect", ["retain", "decide", "decide-eof", "timeout", "finish"]
)
def test_management_failures_never_return_checkpoint_or_retry(owned_child, defect):
    module = invoker()
    processes, _ = owned_child("import sys\nsys.stdin.buffer.read()\n")
    operator, events, _ = management_operator(defect)
    with pytest.raises((RuntimeError, EOFError, TimeoutError)):
        module.management_checkpoint(operator)
    assert events.count("retain") == 1
    assert events[-1] == "close"
    assert events.count("failure-finalize") == (defect != "finish")
    assert events.count("finish") == (defect == "finish")
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


@pytest.mark.parametrize("phase", ["decide", "finish"])
def test_management_preserves_later_operation_phase_and_prior_cleanup(
    owned_child, phase
):
    module = invoker()
    canonical = importlib.import_module("lane3_campaign_operator")
    owned_child("import sys\nsys.stdin.buffer.read()\n")
    operator, events, _ = management_operator()
    operator.CampaignRefused = canonical.CampaignRefused
    operator.failure_diagnostic = canonical.failure_diagnostic

    def refuse(*args):
        raise canonical.CampaignRefused(
            "code-pin",
            diagnostic={
                "phase": "unproven",
                "stage": "operator",
                "category": "code-pin",
                "cleanup": "unproven",
            },
        )

    if phase == "decide":
        operator.coordinator_cli = refuse
    else:
        operator.ManagementStdio.finish = refuse
    with pytest.raises(canonical.CampaignRefused) as error:
        module.management_checkpoint(operator)
    diagnostic = canonical.failure_diagnostic(error.value)
    assert diagnostic == {
        "phase": phase,
        "stage": "operator",
        "category": "code-pin",
        "cleanup": "unproven",
    }
    assert events.count("retain") == 1
    assert events[-1] == "close"


@pytest.mark.parametrize(
    "code,accepted",
    [
        ("print('L3MGMT_TESTS_OK')", True),
        ("print('L3MGMT_TESTS_OK'); raise SystemExit(1)", False),
        ("print('prefix L3MGMT_TESTS_OK suffix')", False),
        ("import time; time.sleep(30)", False),
        ("import os; os.write(1,b'x'*1000001)", False),
    ],
)
def test_harness_requires_bounded_output_exact_marker_and_successful_exit(
    owned_child, tmp_path, code, accepted
):
    module = invoker()
    processes, calls = owned_child(code)
    evidence = tmp_path / "absent-evidence"
    measurements = []
    deadlines = []

    def remaining(deadline):
        deadlines.append(deadline)
        # An accelerated synthetic parent budget keeps the actual stalled
        # child case bounded without waiting 180 seconds in hosted CI.
        return min(1.0, deadline - time.monotonic())

    operator = SimpleNamespace(
        measured=lambda path, pin: measurements.append((path, pin)),
        remaining=remaining,
        coordinator_binding=lambda: {},
        deployment_inputs=lambda: {"bundle_digest": "b" * 64},
    )
    started = time.monotonic()
    if accepted:
        assert module.supervised_harness(operator, tmp_path, evidence) == {
            "harness": "PASS",
            "evidence_leaf": str(evidence),
        }
    else:
        with pytest.raises(module.InvokerRefused):
            module.supervised_harness(operator, tmp_path, evidence)
    assert measurements == [(tmp_path / "l3mgmt-tests.sh", module.HARNESS_SHA256)]
    assert calls[0][0] == [
        "/bin/bash",
        str(tmp_path / "l3mgmt-tests.sh"),
        "full",
        "b" * 64,
        str(evidence),
    ]
    assert len(set(deadlines)) == 1
    assert 179 <= deadlines[0] - started <= 181
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


@pytest.mark.parametrize("role", ["bridge", "remote"])
def test_ssh_command_binds_only_the_fixed_destination_and_canonical_role(role):
    assert invoker().ssh_args(role) == [
        "ssh",
        "-T",
        "-oBatchMode=yes",
        "-oConnectTimeout=10",
        "pve-vm-124",
        "sudo -n /usr/bin/python3 -I -B /usr/local/libexec/dotmac-lane3-campaign/"
        + "lane3_campaign_"
        + role
        + ".py",
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 13),
        ("id", True),
        ("run_attempt", 2),
        ("run_attempt", True),
        ("head_sha", "c" * 40),
        ("repository", {"id": 10, "full_name": "synthetic/repository"}),
        ("repository", {"id": True, "full_name": "synthetic/repository"}),
        ("repository", {"id": 9, "full_name": "foreign/repository"}),
        ("status", "unknown"),
    ],
)
def test_terminal_run_refuses_foreign_or_malformed_coordinates(field, value):
    module = invoker()
    operator, _, _ = management_operator()
    original = operator._run_owned

    def drift(args, deadline):
        run = original(args, deadline)
        run[field] = value
        return run

    operator._run_owned = drift
    with pytest.raises(module.InvokerRefused):
        module._terminal_run(operator, time.monotonic() + 5)


def test_terminal_run_observations_share_the_original_deadline(monkeypatch):
    module = invoker()
    operator, events, _ = management_operator()
    original = operator._run_owned
    remaining_deadlines = []
    sleeps = []

    def run(args, deadline):
        value = original(args, deadline)
        if len(events) == 1:
            value["status"] = "in_progress"
        return value

    operator._run_owned = run
    operator.remaining = lambda deadline: remaining_deadlines.append(deadline) or 1
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    deadline = time.monotonic() + 5
    result = module._terminal_run(operator, deadline)
    assert result["run_id"] == 12
    assert len(events) == 2 and all(event[1] == deadline for event in events)
    assert remaining_deadlines == [deadline] and sleeps == [1]


def test_stdin_close_failure_still_terminates_and_reaps_owned_child(owned_child):
    module = invoker()
    processes, _ = owned_child("import time\ntime.sleep(30)\n")

    class FailingClose:
        def __init__(self, stream):
            self.stream = stream

        @property
        def closed(self):
            return self.stream.closed

        def close(self):
            self.stream.close()
            raise OSError("synthetic-close-failure")

    try:
        with module.owned_ssh("bridge") as process:
            process.stdin = FailingClose(process.stdin)
    except OSError as error:
        assert str(error) == "synthetic-close-failure"
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed
