"""Hosted-only canonical supervisors with disposable synthetic child processes."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from conftest import SCRIPTS


def ready(proc):
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        assert selector.select(5), "synthetic supervisor did not become ready"
        return proc.stdout.readline()


@pytest.fixture
def supervisor(tmp_path):
    # Root-owned /run avoids pytest's writable /tmp ancestors. No production
    # issuer, SSH, credentials, OpenBao, KMS or persistent service is used.
    directory = Path("/run") / ("b8-adapter-" + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    processes = []

    def launch(child, *, accelerated=False, bad_pin=False):
        operation = directory / "configure.sh"
        operation.write_text(
            "#!/bin/bash\nexec "
            + sys.executable
            + " "
            + str(directory / "child.py")
            + "\n"
        )
        operation.chmod(0o600)
        (directory / "child.py").write_text(child)
        deployment = {
            "remote_pins": {
                str(operation): "0" * 64
                if bad_pin
                else hashlib.sha256(operation.read_bytes()).hexdigest()
            },
            "remote_output_directory": str(directory),
            "remote_configure": str(operation),
        }
        script = (
            "import sys,time\n"
            + "sys.path.insert(0,"
            + repr(str(SCRIPTS))
            + ")\n"
            + "import lane3_campaign_issuer as issuer\n"
        )
        if accelerated:
            # Accelerate only the test clock; execute the actual canonical
            # process supervisor, communicate timeout, SIGINT and SIGKILL paths.
            script += "clock=time.monotonic\ntime.monotonic=lambda:clock()*200\n"
        script += "issuer.remote_main(" + repr(deployment) + ")\n"
        proc = subprocess.Popen(  # noqa: S603 - synthetic owned local interpreter
            [sys.executable, "-I", "-B", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        processes.append(proc)
        return proc

    yield directory, launch
    for proc in processes:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
    import shutil

    shutil.rmtree(directory)


def test_actual_supervisor_hup_still_sends_one_sigint_and_refuses_late_cleanup(
    supervisor,
):
    directory, launch = supervisor
    completed = directory / "cleaned"
    child = (
        "import pathlib,signal,time,os\n"
        + "p=pathlib.Path("
        + repr(str(completed))
        + ")\n"
        + "def cleanup(sig,frame):\n p.write_text('SIGINT');raise SystemExit(0)\n"
        + "signal.signal(signal.SIGINT,cleanup)\n"
        + "pathlib.Path("
        + repr(str(directory / "child-ready"))
        + ").write_text(str(os.getpid()))\n"
        + "while True:time.sleep(.05)\n"
    )
    proc = launch(child)
    assert ready(proc) == b"B8_DIRECT_READY_V1\n"
    proc.stdin.write(b'{"seconds":2,"payload":{"shares":["SECRET-CANARY"]}}\n')
    proc.stdin.flush()
    deadline = time.monotonic() + 3
    while not (directory / "child-ready").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (directory / "child-ready").exists()
    os.kill(proc.pid, signal.SIGHUP)
    proc.stdin.close()
    assert proc.wait(timeout=6) == 3
    assert completed.read_text() == "SIGINT"
    assert proc.stdout.read() == b""
    assert b"SECRET-CANARY" not in proc.stderr.read()


def test_actual_hard_cutoff_never_infers_cleanup_or_issues_certificates(supervisor):
    directory, launch = supervisor
    child = (
        "import pathlib,signal,time,os\n"
        + "signal.signal(signal.SIGINT,signal.SIG_IGN)\n"
        + "pathlib.Path("
        + repr(str(directory / "pid"))
        + ").write_text(str(os.getpid()))\n"
        + "while True:time.sleep(.05)\n"
    )
    proc = launch(child, accelerated=True)
    assert ready(proc) == b"B8_DIRECT_READY_V1\n"
    out, err = proc.communicate(b'{"seconds":2,"payload":{}}\n', timeout=6)
    assert proc.returncode == 3 and out == b"" and err == b""
    pid = int((directory / "pid").read_text())
    assert not Path("/proc", str(pid)).exists()
    assert not (directory / "certificates-private.json").exists()
    assert not (directory / "b8-b8-issuer-record.json").exists()


@pytest.mark.parametrize("defect", ["pin", "collision"])
def test_supervisor_pin_or_previous_result_refuses_before_readiness(supervisor, defect):
    directory, launch = supervisor
    if defect == "collision":
        (directory / "certificates-private.json").write_text("synthetic-existing")
    proc = launch("raise SystemExit('child-must-not-run')\n", bad_pin=defect == "pin")
    out, err = proc.communicate(timeout=5)
    assert proc.returncode == 2 and out == b"" and err == b""


@pytest.mark.parametrize(
    "qualification,result", [(True, 0), (True, 1), (True, 2), (False, 0), (True, True)]
)
def test_actual_nonroot_pinned_runner_qualifies_before_matrix(
    supervisor, qualification, result
):
    import pwd

    directory, _ = supervisor
    directory.chmod(0o711)
    account = pwd.getpwnam("nobody")
    work = directory / "work"
    work.mkdir(mode=0o700)
    os.chown(work, account.pw_uid, account.pw_gid)
    cfg = work / "matrix.json"
    cfg.write_text('{"material":{"synthetic":"public"}}')
    execution = work / "execution.json"
    execution.write_text(
        json.dumps({"execution_deadline_ns": time.monotonic_ns() + 20_000_000_000})
    )
    for path in (cfg, execution):
        path.chmod(0o600)
        os.chown(path, account.pw_uid, account.pw_gid)
    matrix = directory / "b8-proof-matrix.py"
    matrix.write_text(
        "import pathlib,sys\n"
        "def private_file(*args):pass\n"
        "def validate(cfg):pass\n"
        "def main():\n"
        " p=pathlib.Path(sys.argv[1]).parent\n"
        " assert (p/'qualified').read_text()=='yes'\n"
        " (p/'matrix-ran').write_text('yes')\n" + " return " + repr(result) + "\n"
    )
    qualifier = directory / "b8-source-qualification.py"
    qualifier.write_text(
        "def qualify_session(cfg,view,matrix):\n"
        " view.remaining()\n"
        " (view.work/'qualified').write_text('yes')\n"
        + " return "
        + repr(qualification)
        + "\n"
    )
    pins = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (matrix, qualifier)
    }
    for path in (matrix, qualifier):
        path.chmod(0o644)
    # The checkout may have root-only ancestors inside the integration image.
    # Stage the exact canonical bytes in the fixture deployment directory;
    # the nobody worker must read production code without checkout access.
    canonical = (SCRIPTS / "lane3_campaign_runner.py").read_bytes()
    deployed_runner = directory / "lane3_campaign_runner.py"
    deployed_runner.write_bytes(canonical)
    deployed_runner.chmod(0o644)
    assert (
        hashlib.sha256(deployed_runner.read_bytes()).digest()
        == hashlib.sha256(canonical).digest()
    )
    code = (
        "import sys,json\n"
        + "sys.path.insert(0,"
        + repr(str(directory))
        + ")\n"
        + "import lane3_campaign_runner as runner\n"
        + "runner.configure("
        + repr({"pins": pins, "code_directory": str(directory)})
        + ")\n"
        + "sys.argv=['runner',"
        + repr(str(cfg))
        + "]\n"
        + "try: result=runner.main()\n"
        + "except BaseException: result=2\n"
        + "raise SystemExit(result)\n"
    )
    child = subprocess.run(  # noqa: S603 - exact disposable unprivileged worker
        [
            "/usr/bin/setpriv",
            "--reuid=" + str(account.pw_uid),
            "--regid=" + str(account.pw_gid),
            "--clear-groups",
            sys.executable,
            "-I",
            "-B",
            "-c",
            code,
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == (result if qualification and type(result) is int else 2)
    assert (work / "qualified").exists()
    assert (work / "matrix-ran").exists() is qualification
