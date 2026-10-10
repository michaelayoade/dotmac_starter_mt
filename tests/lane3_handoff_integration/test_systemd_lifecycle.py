"""Hosted real systemd effectors; bounded units owned only by this test.

These measure the production effectors, not full boot/recovery ordering or
controller lock-collision rollback. Those remain acceptance prerequisites.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import time
import uuid

from conftest import hc


def active(unit: str) -> bool:
    result = subprocess.run(  # noqa: S603 - fixed command and own generated unit
        ["/usr/bin/systemctl", "is-active", "--quiet", unit],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def wait(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("bounded systemd effect did not complete")


def test_production_timer_fires_and_disarm_prevents_callback():
    host = hc.SystemHost()
    prefix = "l3h-it-timer-" + uuid.uuid4().hex
    marker = pathlib.Path("/run") / (prefix + ".json")
    unit, canceled = prefix + "-fire", prefix + "-cancel"
    callback = [
        "/usr/bin/python3",
        "-I",
        "-c",
        "import pathlib,time;pathlib.Path("
        + repr(str(marker))
        + ").write_text(str(time.monotonic()))",
    ]
    started = time.monotonic()
    try:
        host.arm_timer(unit, 1, callback)
        wait(marker.exists)
        elapsed = float(marker.read_text()) - started
        assert 0.5 <= elapsed <= 5, "real timer must fire within its measured bound"
        marker.unlink()
        host.arm_timer(canceled, 2, callback)
        host.disarm_timer(canceled)
        assert not active(canceled + ".timer")
        time.sleep(3)
        assert not marker.exists(), "a disarmed production timer must not fire"
    finally:
        for owned in (unit, canceled):
            subprocess.run(  # noqa: S603 - fixed command and own generated unit
                ["/usr/bin/systemctl", "stop", owned + ".timer", owned + ".service"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        marker.unlink(missing_ok=True)


def test_production_service_runs_in_expected_cgroup_and_stops():
    host = hc.SystemHost()
    unit = "l3h-it-service-" + uuid.uuid4().hex
    marker = pathlib.Path("/run") / (unit + ".json")
    script = (
        "import json,pathlib,time;pathlib.Path("
        + repr(str(marker))
        + ").write_text(json.dumps({'cgroup':"
        "pathlib.Path('/proc/self/cgroup').read_text()}));"
        "time.sleep(30)"
    )
    try:
        host.start_service(
            unit,
            ["/usr/bin/python3", "-I", "-c", script],
            ["Type=exec", "RuntimeMaxSec=20"],
        )
        wait(marker.exists)
        assert host.unit_cgroup(unit) in json.loads(marker.read_text())["cgroup"]
        host.stop_unit(unit)
        wait(lambda: not active(unit + ".service"))
    finally:
        subprocess.run(  # noqa: S603 - fixed command and own generated unit
            ["/usr/bin/systemctl", "stop", unit + ".service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        marker.unlink(missing_ok=True)


def test_production_launcher_delivers_fixed_metadata_and_isolated_python():
    host = hc.SystemHost()
    unit = "l3h-it-runner-" + uuid.uuid4().hex
    directory = pathlib.Path("/run") / unit
    directory.mkdir(mode=0o700)
    marker = directory / "metadata.json"
    script = directory / "run.sh"
    worker = directory / "worker.py"
    worker.write_text(
        "import json,os,pathlib,sys\n"
        "pathlib.Path('metadata.json').write_text(json.dumps({"
        "'isolated':sys.flags.isolated,'lease':os.environ.get('DOTMAC_LANE3_LEASE_ID'),"
        "'supplier':os.environ.get('DOTMAC_LANE3_SUPPLIER_DIR')}))\n"
    )
    script.write_text("#!/bin/sh\nexec /usr/bin/python3 -I ./worker.py\n")
    script.chmod(0o700)
    lease = "1" * 32
    supplier = str(directory / "supplier")
    try:
        host.launch_runner(
            unit,
            ["WorkingDirectory=" + str(directory), "RuntimeMaxSec=15"],
            "/usr/bin/python3",
            json.dumps(
                {
                    "jit": "synthetic-jit-canary",
                    "lease_id": lease,
                    "supplier_dir": supplier,
                }
            ).encode(),
        )
        wait(marker.exists)
        assert json.loads(marker.read_text()) == {
            "isolated": 1,
            "lease": lease,
            "supplier": supplier,
        }
    finally:
        subprocess.run(  # noqa: S603 - own generated unit only
            ["/usr/bin/systemctl", "stop", unit + ".service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        marker.unlink(missing_ok=True)
        script.unlink()
        worker.unlink()
        directory.rmdir()
