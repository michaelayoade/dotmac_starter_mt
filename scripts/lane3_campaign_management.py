"""One-shot, host-local retained-management backstop, independent of runtime 85.

This helper owns only its two exact timers and the pinned legacy remover. It
never creates SSH trust, edits sudoers, extends a lease or reconstructs receipts.
All deadlines and receipt ages use the management host's CLOCK_MONOTONIC.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Protocol

NS = 1_000_000_000
MAX_SECONDS = 900
REMOVAL_MARGIN_SECONDS = 60
HELPER = "/usr/local/libexec/dotmac-lane3-campaign/lane3_campaign_management.py"
STATE_ROOT = "/root/dotmac-l3mgmt/campaign"
LEGACY_LOCK = "/root/dotmac-l3mgmt/.lock"
REMOVER = "/root/dotmac-l3mgmt/remove.sh"
MARKER = "/root/dotmac-l3mgmt/owned"
ACTIVATION = "dotmac-l3mgmt-activate.service"


class Refused(RuntimeError):
    """A fixed public reason, with no command output or secret material."""


@dataclass(frozen=True)
class Binding:
    run_id: str
    boot_id: str
    marker_sha256: str
    removal_sha256: str
    started_ns: int
    helper_sha256: str
    old_unit: str = "dotmac-l3mgmt-remove-live"
    old_owner: str = "l3mgmt trust removal (live window)"

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[0-9a-f]{32}", self.run_id)
            or not re.fullmatch(r"[0-9a-f-]{36}", self.boot_id)
            or any(
                not re.fullmatch(r"[0-9a-f]{64}", x)
                for x in (self.marker_sha256, self.removal_sha256, self.helper_sha256)
            )
            or type(self.started_ns) is not int
            or self.started_ns <= 0
            or self.old_unit != "dotmac-l3mgmt-remove-live"
            or self.old_owner != "l3mgmt trust removal (live window)"
        ):
            raise Refused("binding-invalid")

    @property
    def new_unit(self) -> str:
        return "dotmac-l3mgmt-retained-" + self.run_id

    @property
    def deadline_ns(self) -> int:
        return self.started_ns + MAX_SECONDS * NS

    @property
    def expiry_ns(self) -> int:
        return self.deadline_ns - REMOVAL_MARGIN_SECONDS * NS

    @property
    def owner(self) -> str:
        return f"dotmac-l3mgmt:{self.run_id}:{self.boot_id}:{self.deadline_ns}"


@dataclass(frozen=True)
class TimerState:
    owner: str
    argv: tuple[str, ...]
    deadline_ns: int
    waiting: bool
    bounded: bool


@dataclass(frozen=True)
class Receipt:
    run_id: str
    boot_id: str
    deadline_ns: int
    verified_ns: int
    expiry_ns: int


class Backend(Protocol):
    def clock_ns(self) -> int: ...
    def boot_id(self) -> str: ...
    def marker_binding(self) -> tuple[str, str, str] | None: ...
    def removal_digest(self) -> str: ...
    def helper_digest(self) -> str: ...
    def timer(self, unit: str) -> TimerState | None: ...
    def arm(self, unit: str, owner: str, deadline_ns: int) -> None: ...
    def stop(self, unit: str) -> None: ...
    def remove(self) -> None: ...


class Management:
    def __init__(self, binding: Binding, backend: Backend):
        self.binding, self.backend = binding, backend
        self._attempted = False
        self._receipt: Receipt | None = None

    def _bindings(self) -> None:
        b, host = self.binding, self.backend
        if (
            host.boot_id() != b.boot_id
            or host.marker_binding() != (b.run_id, "created", b.marker_sha256)
            or host.removal_digest() != b.removal_sha256
            or host.helper_digest() != b.helper_sha256
        ):
            raise Refused("binding-mismatch")
        now = host.clock_ns()
        if not b.started_ns <= now < b.deadline_ns:
            raise Refused("deadline-invalid")

    def _timer(self, unit: str, owner: str, minimum: int, *, retained: bool) -> None:
        state = self.backend.timer(unit)
        now = self.backend.clock_ns()
        if (
            state is None
            or not state.waiting
            or not state.bounded
            or state.owner != owner
            or state.argv
            != (
                (
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    HELPER,
                    "--expire",
                    self.binding.run_id,
                )
                if retained
                else ("/bin/sh", REMOVER)
            )
            or state.deadline_ns - now < minimum * NS
            or state.deadline_ns - now > (MAX_SECONDS if retained else 225) * NS
            or (retained and not 0 <= self.binding.expiry_ns - state.deadline_ns < 1000)
        ):
            raise Refused("timer-unproven")

    def retain(self) -> Receipt:
        if self._attempted:
            raise Refused("already-attempted")
        self._attempted = True
        try:
            self._bindings()
            self._timer(
                self.binding.old_unit, self.binding.old_owner, 1, retained=False
            )
            if self.backend.timer(self.binding.new_unit) is not None:
                raise Refused("timer-collision")
        except Refused:
            raise
        except Exception:
            raise Refused("preflight-unproven") from None
        try:
            self.backend.arm(
                self.binding.new_unit, self.binding.owner, self.binding.expiry_ns
            )
            self._bindings()
            self._timer(self.binding.new_unit, self.binding.owner, 600, retained=True)
            self.backend.stop(self.binding.old_unit)
            if self.backend.timer(self.binding.old_unit) is not None:
                raise Refused("old-stop-unproven")
            self._bindings()
            self._timer(self.binding.new_unit, self.binding.owner, 600, retained=True)
        except Exception:
            # Never disarm either possible backstop after an unknown result. The
            # old timer is untouched until a new timer has independent readback.
            self._cleanup()
            raise Refused("transition-unproven") from None
        receipt = Receipt(
            self.binding.run_id,
            self.binding.boot_id,
            self.binding.deadline_ns,
            self.backend.clock_ns(),
            self.binding.expiry_ns,
        )
        self._receipt = receipt
        return receipt

    def _cleanup(self) -> bool:
        try:
            self._bindings()
            self.backend.remove()
            return True
        except Exception:
            # Pinned-script drift/unknown ownership cannot authorize execution.
            # Keep the armed timers and report the fixed failure to the caller.
            return False

    def _gate(self, receipt: Receipt, minimum: int, age: int) -> Receipt:
        try:
            if self._receipt is None or receipt != self._receipt:
                raise Refused("receipt-unproven")
            self._bindings()
            now = self.backend.clock_ns()
            if not 0 <= now - receipt.verified_ns <= age * NS:
                raise Refused("receipt-stale")
            self._timer(
                self.binding.new_unit, self.binding.owner, minimum, retained=True
            )
            return receipt
        except Refused:
            raise
        except Exception:
            raise Refused("gate-unproven") from None

    def gate_prepare(self, receipt: Receipt) -> Receipt:
        return self._gate(receipt, 600, 30)

    def gate_bootstrap(self, receipt: Receipt) -> Receipt:
        return self._gate(receipt, 420, 60)

    def finish(self, receipt: Receipt, *, active_lease: bool) -> None:
        if active_lease is not False:
            raise Refused("lease-active")
        try:
            if self._receipt is None or receipt != self._receipt:
                raise Refused("receipt-unproven")
            self._bindings()
            self.backend.remove()
            # The remover receipt is insufficient: ownership marker must be gone.
            if self.backend.marker_binding() is not None:
                raise Refused("cleanup-unproven")
            self.backend.stop(self.binding.new_unit)
            if self.backend.timer(self.binding.new_unit) is not None:
                raise Refused("cleanup-unproven")
            self._receipt = None
        except Refused:
            raise
        except Exception:
            raise Refused("cleanup-unproven") from None


class SystemdBackend:
    """Fixed root paths and unit allowlist; no caller command or path interface."""

    def __init__(self, binding: Binding):
        self.binding = binding
        self._remover = pathlib.Path(REMOVER)
        self._marker = pathlib.Path(MARKER)

    def clock_ns(self) -> int:
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC)

    def boot_id(self) -> str:
        return pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    @staticmethod
    def _read_root_file(path: pathlib.Path) -> bytes:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
                or info.st_size > 65536
            ):
                raise Refused("file-unproven")
            return os.read(fd, 65537)
        finally:
            os.close(fd)

    def marker_binding(self) -> tuple[str, str, str] | None:
        try:
            data = self._read_root_file(self._marker)
        except FileNotFoundError:
            return None
        obj = json.loads(data)
        if obj.get("schema") != "dotmac.l3mgmt-owned.v1":
            raise Refused("marker-unproven")
        return obj.get("run_id"), obj.get("state"), hashlib.sha256(data).hexdigest()

    def helper_digest(self) -> str:
        path = pathlib.Path(HELPER)
        if stat.S_IMODE(path.lstat().st_mode) != 0o444:
            raise Refused("helper-unproven")
        return hashlib.sha256(self._read_root_file(path)).hexdigest()

    def removal_digest(self) -> str:
        return hashlib.sha256(self._read_root_file(self._remover)).hexdigest()

    @staticmethod
    def _run(argv: list[str]) -> str:
        try:
            result = subprocess.run(argv, capture_output=True, check=False, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            raise Refused("systemd-unproven") from None
        if result.returncode:
            raise Refused("systemd-unproven")
        return result.stdout.decode("utf-8", "strict").strip()

    def _unit(self, unit: str) -> None:
        if unit not in (self.binding.old_unit, self.binding.new_unit):
            raise Refused("unit-unowned")

    def _show(self, name: str) -> dict[str, str]:
        output = self._run(["/usr/bin/systemctl", "show", name])
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def timer(self, unit: str) -> TimerState | None:
        self._unit(unit)
        timer = self._show(unit + ".timer")
        service = self._show(unit + ".service")
        stopped = (
            service.get("ActiveState") in ("inactive", "failed")
            and service.get("MainPID") == "0"
            and service.get("ControlPID") == "0"
            and service.get("ControlGroup", "") == ""
        )
        if timer.get("LoadState") == "not-found" or (
            timer.get("ActiveState") == "inactive" and timer.get("SubState") == "dead"
        ):
            if not stopped:
                raise Refused("timer-remover-active")
            return None
        path_result = self._run(
            [
                "/usr/bin/busctl",
                "call",
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager",
                "GetUnit",
                "s",
                unit + ".timer",
            ]
        )
        match = re.fullmatch(
            r'o "(/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+)"', path_result
        )
        if not match:
            raise Refused("timer-unproven")
        raw = self._run(
            [
                "/usr/bin/busctl",
                "get-property",
                "org.freedesktop.systemd1",
                match[1],
                "org.freedesktop.systemd1.Timer",
                "NextElapseUSecMonotonic",
            ]
        )
        deadline = re.fullmatch(r"t ([0-9]+)", raw)
        execution = service.get("ExecStart", "")
        argv_match = re.fullmatch(
            r"\{ path=(?:/bin/sh|/usr/bin/python3) ; "
            r"argv\[\]=([^;]+) ; ignore_errors=no ; "
            r"start_time=[^;]* ; stop_time=[^;]* ; pid=[0-9]+ ; "
            r"code=[^;]* ; status=[^;]* \}",
            execution,
        )
        argv = tuple(argv_match[1].strip().split()) if argv_match else ()
        bounded = (
            stopped
            and service.get("Type") in ("exec", "simple")
            and service.get("RuntimeMaxUSec")
            == ("55s" if unit == self.binding.new_unit else "45s")
            and service.get("TimeoutStartUSec")
            == ("55s" if unit == self.binding.new_unit else "45s")
            and service.get("TimeoutStopUSec") == "5s"
            and service.get("KillMode") == "control-group"
            and (
                (
                    unit == self.binding.new_unit
                    and ACTIVATION not in service.get("Conflicts", "").split()
                )
                or (
                    unit == self.binding.old_unit
                    and ACTIVATION in service.get("Conflicts", "").split()
                    and ACTIVATION in service.get("After", "").split()
                )
            )
            and service.get("Transient") == "yes"
            and timer.get("Transient") == "yes"
            and timer.get("Unit") == unit + ".service"
        )
        return TimerState(
            service.get("Description", ""),
            argv,
            int(deadline[1]) * 1000 if deadline else 0,
            timer.get("ActiveState") == "active" and timer.get("SubState") == "waiting",
            bounded,
        )

    def arm(self, unit: str, owner: str, deadline_ns: int) -> None:
        self._unit(unit)
        if (
            unit != self.binding.new_unit
            or owner != self.binding.owner
            or deadline_ns != self.binding.expiry_ns
        ):
            raise Refused("arm-unowned")
        remaining = (deadline_ns - self.clock_ns()) // 1000
        if not 0 < remaining <= MAX_SECONDS * 1_000_000:
            raise Refused("deadline-invalid")
        if self.helper_digest() != self.binding.helper_sha256:
            raise Refused("binding-mismatch")
        self._save_binding()
        self._run(
            [
                "/usr/bin/systemd-run",
                "--unit=" + unit,
                "--description=" + owner,
                "--collect",
                "--service-type=exec",
                "--property=RuntimeMaxSec=55s",
                "--property=TimeoutStartSec=55s",
                "--property=TimeoutStopSec=5s",
                "--property=KillMode=control-group",
                "--on-boot=" + str(deadline_ns // 1000) + "us",
                "--timer-property=AccuracySec=1us",
                "/usr/bin/python3",
                "-I",
                "-B",
                HELPER,
                "--expire",
                self.binding.run_id,
            ]
        )

    def stop(self, unit: str) -> None:
        self._unit(unit)
        self._run(["/usr/bin/systemctl", "stop", unit + ".timer"])

    def _save_binding(self) -> None:
        root = pathlib.Path(STATE_ROOT)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise Refused("state-unproven")
        directory = root / self.binding.run_id
        directory.mkdir(mode=0o700)
        fd = os.open(
            directory / "state.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            data = json.dumps(
                {
                    "schema": "dotmac.management-callback.v1",
                    "binding": asdict(self.binding),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            if os.write(fd, data) != len(data):
                raise Refused("state-unproven")
            os.fsync(fd)
        finally:
            os.close(fd)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def remove(self) -> None:
        if os.geteuid() != 0:
            raise Refused("root-required")
        fd = os.open(
            LEGACY_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
            ):
                raise Refused("lock-unproven")
            until = self.clock_ns() + 5 * NS
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if self.clock_ns() >= until:
                        raise Refused("lock-busy") from None
                    time.sleep(0.02)
            # The marker check precedes activation stop: a stale callback cannot
            # stop or delete a later run's trust, even after the old deadline.
            if (
                self.boot_id() != self.binding.boot_id
                or self.marker_binding()
                != (self.binding.run_id, "created", self.binding.marker_sha256)
                or self.helper_digest() != self.binding.helper_sha256
            ):
                raise Refused("binding-mismatch")
            remover = self._read_root_file(self._remover)
            if hashlib.sha256(remover).hexdigest() != self.binding.removal_sha256:
                raise Refused("binding-mismatch")
            try:
                self._run(["/usr/bin/systemctl", "stop", ACTIVATION])
            except Refused:
                pass
            state = self._show(ACTIVATION)
            if (
                state.get("ActiveState") not in ("inactive", "failed")
                or state.get("MainPID") != "0"
                or state.get("ControlPID") != "0"
                or state.get("ControlGroup", "") != ""
            ):
                raise Refused("activation-stop-unproven")
            result = subprocess.run(
                ["/bin/sh"],
                input=remover,
                capture_output=True,
                timeout=45,
                check=False,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "L3_LOCKED": "1"},
            )
            if result.returncode != 0 or result.stdout.strip() != b"REMOVED_OK":
                raise Refused("cleanup-unproven")
        finally:
            os.close(fd)


def main(argv: list[str]) -> int:
    try:
        if (
            len(argv) != 2
            or argv[0] != "--expire"
            or not re.fullmatch(r"[0-9a-f]{32}", argv[1])
        ):
            raise Refused("usage-refused")
        directory = pathlib.Path(STATE_ROOT) / argv[1]
        for path in (directory.parent, directory):
            info = path.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise Refused("state-unproven")
        state_path = directory / "state.json"
        if stat.S_IMODE(state_path.lstat().st_mode) != 0o600:
            raise Refused("state-unproven")
        state = json.loads(SystemdBackend._read_root_file(state_path))
        if (
            set(state) != {"schema", "binding"}
            or state["schema"] != "dotmac.management-callback.v1"
        ):
            raise Refused("state-unproven")
        binding = Binding(**state["binding"])
        if binding.run_id != argv[1]:
            raise Refused("state-unproven")
        SystemdBackend(binding).remove()
        print("REMOVED_OK")
        return 0
    except Exception:
        print("MANAGEMENT_REMOVAL_UNPROVEN")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
