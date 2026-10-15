"""Real retained timers, isolated private root marker/remover and network ns.

Test-local translation confines the fixed production paths and exact two-unit
allowlist to unique fixture objects. Production parsing, commands, deadlines,
ordering and readback remain in use; no SSH account or sudo rule is created.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import time
import uuid

import lane3_campaign_management as cm
import pytest


class FixtureBackend(cm.SystemdBackend):
    def __init__(self, binding, directory, namespace, prefix):
        super().__init__(binding)
        self.directory = directory
        self.namespace = namespace
        self._marker = directory / "owned"
        self._remover = directory / "remove.sh"
        self.prefix = prefix
        self.fail = ""
        self.events = []

    def translated(self, text):
        for logical, real in (
            (self.binding.old_unit, self.prefix + "-old"),
            (self.binding.new_unit, self.prefix + "-new"),
            (cm.ACTIVATION, self.prefix + "-activation.service"),
        ):
            text = text.replace(logical, real)
        return text

    def _run(self, argv):
        translated = [self.translated(arg) for arg in argv]
        if argv[0] == "/usr/bin/systemd-run":
            translated.insert(1, "--property=NetworkNamespacePath=" + self.namespace)
        output = super()._run(translated)
        # systemd's ExecStart readback must name our exact private script before
        # translating the fixture's path back to the production fixed identity.
        output = output.replace(str(self._remover), cm.REMOVER)
        for logical, real in (
            (self.binding.old_unit, self.prefix + "-old"),
            (self.binding.new_unit, self.prefix + "-new"),
            (cm.ACTIVATION, self.prefix + "-activation.service"),
        ):
            output = output.replace(real, logical)
        return output

    def arm(self, unit, owner, deadline_ns):
        self.events.append("arm")
        if self.fail == "new-failed":
            raise cm.Refused("synthetic-failure")
        super().arm(unit, owner, deadline_ns)
        if self.fail == "lost":
            raise TimeoutError("PRIVATE-LOST-RESPONSE-CANARY")

    def stop(self, unit):
        self.events.append("stop:" + unit)
        if self.fail == "old-stop" and unit == self.binding.old_unit:
            return
        super().stop(unit)

    def remove(self):
        self.events.append("remove")
        super().remove()

    def old_arm(self):
        self._run(
            [
                "/usr/bin/systemd-run",
                "--unit=" + self.binding.old_unit,
                "--description=" + self.binding.old_owner,
                "--collect",
                "--service-type=exec",
                "--property=Conflicts=" + cm.ACTIVATION,
                "--property=After=" + cm.ACTIVATION,
                "--property=RuntimeMaxSec=45s",
                "--property=TimeoutStartSec=45s",
                "--property=TimeoutStopSec=5s",
                "--property=KillMode=control-group",
                "--on-active=220s",
                "--timer-property=AccuracySec=1us",
                "/bin/sh",
                str(self._remover),
            ]
        )


@pytest.fixture
def management(tmp_path, monkeypatch):
    # Keep an independently owned namespace alive for EVERY systemd child.
    ns = subprocess.Popen(  # noqa: S603 - fixed namespace fixture argv
        ["/usr/bin/unshare", "--net", "/bin/sleep", "120"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    namespace = f"/proc/{ns.pid}/ns/net"
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if ns.poll() is not None:
                raise AssertionError("disposable namespace failed")
            if (
                pathlib.Path(namespace).exists()
                and pathlib.Path(namespace).readlink()
                != pathlib.Path("/proc/self/ns/net").readlink()
            ):
                break
            time.sleep(0.02)
        else:
            raise AssertionError("disposable namespace unproven")
        marker = tmp_path / "owned"
        marker.write_text(
            json.dumps(
                {
                    "schema": "dotmac.l3mgmt-owned.v1",
                    "run_id": "a" * 32,
                    "state": "created",
                }
            )
        )
        marker.chmod(0o600)
        script = tmp_path / "remove.sh"
        script.write_text(
            "#!/bin/sh\nrm -- '" + str(marker) + "' || exit 1\nprintf 'REMOVED_OK\\n'\n"
        )
        script.chmod(0o700)
        helper = tmp_path / "lane3_campaign_management.py"
        state_root = tmp_path / "callbacks"
        lock = tmp_path / ".lock"
        prefix = "l3h-it-retained-" + uuid.uuid4().hex
        replacements = {
            "HELPER": str(helper),
            "STATE_ROOT": str(state_root),
            "LEGACY_LOCK": str(lock),
            "REMOVER": str(script),
            "MARKER": str(marker),
            "ACTIVATION": prefix + "-activation.service",
        }
        source = pathlib.Path(cm.__file__).read_text()
        for name, value in replacements.items():
            source = source.replace(getattr(cm, name), value)
            monkeypatch.setattr(cm, name, value)
        helper.write_text(source)
        helper.chmod(0o444)
        binding = cm.Binding(
            "a" * 32,
            pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            hashlib.sha256(marker.read_bytes()).hexdigest(),
            hashlib.sha256(script.read_bytes()).hexdigest(),
            time.monotonic_ns(),
            hashlib.sha256(helper.read_bytes()).hexdigest(),
        )
        host = FixtureBackend(binding, tmp_path, namespace, prefix)
        host.old_arm()
        try:
            yield host, cm.Management(binding, host)
        finally:
            for unit in (binding.old_unit, binding.new_unit):
                subprocess.run(  # noqa: S603 - own unique generated units
                    [
                        "/usr/bin/systemctl",
                        "stop",
                        host.translated(unit) + ".timer",
                        host.translated(unit) + ".service",
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
    finally:
        ns.terminate()
        ns.wait(timeout=5)


def test_real_arm_readback_old_removal_and_cleanup(management):
    host, manager = management
    receipt = manager.retain()
    assert host.timer(manager.binding.old_unit) is None
    state = host.timer(manager.binding.new_unit)
    assert state and state.waiting
    assert 600 <= (state.deadline_ns - host.clock_ns()) / cm.NS <= 900
    assert manager.gate_prepare(receipt) == receipt
    assert manager.gate_bootstrap(receipt) == receipt
    manager.finish(receipt, active_lease=False)
    assert host.marker_binding() is None
    assert host.timer(manager.binding.new_unit) is None


@pytest.mark.parametrize("failure", ["lost", "new-failed", "old-stop"])
def test_real_unknown_or_failed_transition_retains_old_timer_and_cleans(
    management, failure
):
    host, manager = management
    host.fail = failure
    with pytest.raises(cm.Refused, match="transition-unproven") as error:
        manager.retain()
    assert "PRIVATE" not in str(error.value)
    assert host.timer(manager.binding.old_unit) is not None
    assert host.marker_binding() is None
    assert host.events[-1] == "remove"
    if failure == "lost":
        assert host.timer(manager.binding.new_unit) is not None


def test_real_pinned_script_drift_refuses_without_mutation(management):
    host, manager = management
    host._remover.write_text("#!/bin/sh\nexit 99\n")
    with pytest.raises(cm.Refused, match="binding-mismatch"):
        manager.retain()
    assert host.events == []
    assert host.timer(manager.binding.old_unit) is not None
    assert host.marker_binding() is not None


@pytest.mark.parametrize("drift", ["marker", "remover", "helper"])
def test_real_callback_rejects_later_activation_or_pinned_bytes_drift(
    management, drift
):
    host, manager = management
    manager.retain()
    if drift == "marker":
        obj = json.loads(host._marker.read_text())
        obj["run_id"] = "b" * 32
        host._marker.write_text(json.dumps(obj))
    elif drift == "remover":
        host._remover.write_text("#!/bin/sh\nexit 99\n")
    else:
        path = pathlib.Path(cm.HELPER)
        path.chmod(0o644)
        path.write_bytes(path.read_bytes() + b"\n")
        path.chmod(0o444)
    before = host._marker.read_bytes()
    host._run(
        [
            "/usr/bin/systemd-run",
            "--unit=" + cm.ACTIVATION,
            "--collect",
            "--service-type=exec",
            "--property=RuntimeMaxSec=20s",
            "/bin/sleep",
            "20",
        ]
    )
    try:
        result = subprocess.run(  # noqa: S603 - exact staged callback and run
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                cm.HELPER,
                "--expire",
                manager.binding.run_id,
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 1
        assert result.stdout.strip() == b"MANAGEMENT_REMOVAL_UNPROVEN"
        assert host._marker.read_bytes() == before
        assert host._show(cm.ACTIVATION)["ActiveState"] == "active"
    finally:
        host._run(["/usr/bin/systemctl", "stop", cm.ACTIVATION])


def test_real_autonomous_bound_callback_removes_only_original_run(management):
    host, manager = management
    manager.retain()
    unit = host.prefix + "-callback-fire"
    try:
        host._run(
            [
                "/usr/bin/systemd-run",
                "--unit=" + unit,
                "--collect",
                "--service-type=exec",
                "--property=RuntimeMaxSec=55s",
                "--property=TimeoutStartSec=55s",
                "--property=TimeoutStopSec=5s",
                "--property=KillMode=control-group",
                "--on-active=1s",
                "--timer-property=AccuracySec=1us",
                "/usr/bin/python3",
                "-I",
                "-B",
                cm.HELPER,
                "--expire",
                manager.binding.run_id,
            ]
        )
        until = time.monotonic() + 10
        while host._marker.exists() and time.monotonic() < until:
            time.sleep(0.05)
        assert not host._marker.exists()
    finally:
        subprocess.run(  # noqa: S603 - exact unique owned callback fixture
            ["/usr/bin/systemctl", "stop", unit + ".timer", unit + ".service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )


@pytest.mark.parametrize("point", ["old-stop-readback", "second-retained-check"])
def test_real_stopped_original_then_refusal_keeps_actual_stage(
    management, monkeypatch, point
):
    host, manager = management
    timer = host.timer
    original_stopped = False
    retained_reads = 0
    stop = host.stop

    def observed_stop(unit):
        nonlocal original_stopped
        stop(unit)
        if unit == manager.binding.old_unit:
            original_stopped = True

    def failing_read(unit):
        nonlocal retained_reads
        if unit == manager.binding.new_unit:
            retained_reads += 1
        if original_stopped and (
            (point == "old-stop-readback" and unit == manager.binding.old_unit)
            or (
                point == "second-retained-check"
                and unit == manager.binding.new_unit
                and retained_reads == 3
            )
        ):
            # First confirm the actual collected original is absent, including
            # the real service PID/cgroup parser, before injecting a refusal.
            assert timer(manager.binding.old_unit) is None
            raise cm.Refused("systemd-unproven")
        return timer(unit)

    monkeypatch.setattr(host, "stop", observed_stop)
    monkeypatch.setattr(host, "timer", failing_read)
    with pytest.raises(cm.Refused, match="transition-unproven") as error:
        manager.retain()
    assert cm.failure_diagnostic(error.value, "retain") == {
        "phase": "retain",
        "stage": point,
        "category": "systemd-unproven",
        "cleanup": "completed",
    }
    assert host.marker_binding() is None
    assert timer(manager.binding.old_unit) is None
    assert timer(manager.binding.new_unit) is not None
