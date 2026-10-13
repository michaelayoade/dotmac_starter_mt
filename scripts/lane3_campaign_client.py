#!/usr/bin/env python3
"""Bounded B8 matrix client. No issuer, custody, firewall or generic executor.

Install this module and the reviewed matrix runner in a root-owned, nonwritable
code directory. A root timer owns cleanup independently of the caller. Child
material is in a separate private workspace; root control state is never handed
to the child. The cleanup unit permits at most 30 start attempts, including
RuntimeMaxSec failures; failed cleanup retains root evidence and unremoved material.
No collection, timer rearm or reset-failed clears that limit. Returned errors
contain category names only.
"""

from __future__ import annotations

import array
import base64
import contextlib
import dataclasses
import fcntl
import functools
import hashlib
import json
import math
import os
import pwd
import re
import runpy
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

NAMES = (
    "observer",
    "jump",
    "probe",
    "expired_observer",
    "wrong_principal",
    "gate0_observer",
    "gate0_jump",
    "plain_key",
)
CLIENT_PROOF_ROOT = Path("/var/lib/dotmac-lane3-campaign/client")
IDENTITY_ROOT = Path("/var/lib/dotmac-lane3-campaign/identities")
TRANSPORT_ROOT = Path("/run/dotmac-lane3-campaign/transport")
PREFIX = "dotmac-b8-client-"
NS = 1_000_000_000
MAX_SECONDS = 240
MIN_WINDOW_SECONDS = 90
CLEANUP_RESERVE_SECONDS = 45
WORKER_STOP_SECONDS = 3
MIN_PROOF_SECONDS = 5
CERTIFICATE_MARGIN_SECONDS = 31


class ClientRefused(RuntimeError):
    """Value-free refusal suitable for receipts."""


def value_free(method):
    @functools.wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            raise ClientRefused("client.operation_failed") from None

    return call


def cleanup_on_failure(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self.cleanup()
            raise

    return call


@dataclasses.dataclass(frozen=True)
class RunnerIdentity:
    user: str
    uid: int
    gid: int
    manifest_sha256: str
    # The caller owns account lifecycle. This primitive never deletes accounts.
    ownership: str = "external-owned"

    def validate(self):
        if (
            not isinstance(self.user, str)
            or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.user)
            or self.user == "root"
            or type(self.uid) is not int
            or not 0 < self.uid < 2**32 - 1
            or type(self.gid) is not int
            or not 0 < self.gid < 2**32 - 1
            or not isinstance(self.manifest_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.manifest_sha256)
            or self.ownership != "external-owned"
        ):
            raise ClientRefused("client.identity")


@dataclasses.dataclass(frozen=True)
class MaterialWindow:
    material_started_ns: int
    material_deadline_ns: int
    cleanup_due_ns: int
    execution_deadline_ns: int
    cleanup_runtime_seconds: int = 30
    cleanup_stop_seconds: int = 3

    def validate(self):
        values = dataclasses.asdict(self)
        reserve = self.material_deadline_ns - self.cleanup_due_ns
        if (
            any(type(value) is not int for value in values.values())
            or self.material_started_ns < 0
            or not 0
            < self.material_deadline_ns - self.material_started_ns
            <= MAX_SECONDS * NS
            or not 1 <= self.cleanup_runtime_seconds <= 30
            or not 1 <= self.cleanup_stop_seconds <= 3
            or reserve
            < (self.cleanup_runtime_seconds + self.cleanup_stop_seconds + 2) * NS
            or self.execution_deadline_ns
            != self.cleanup_due_ns - WORKER_STOP_SECONDS * NS
            or self.execution_deadline_ns <= self.material_started_ns
        ):
            raise ClientRefused("client.material_window")
        return self


def material_window(started_ns, seconds):
    if type(seconds) is not int or not MIN_WINDOW_SECONDS <= seconds <= MAX_SECONDS:
        raise ClientRefused("client.window")
    cap = started_ns + seconds * NS
    due = cap - CLEANUP_RESERVE_SECONDS * NS
    return MaterialWindow(
        started_ns, cap, due, due - WORKER_STOP_SECONDS * NS
    ).validate()


def material_cap_verdict(record):
    if "material_window" not in record:
        return "NOT_STARTED"
    window = MaterialWindow(**record["material_window"]).validate()
    first = record.get("first_cleanup", {})
    destroyed = first.get("destroyed_ns")
    started = first.get("started_ns")
    if (
        first.get("outcome") == "succeeded"
        and type(destroyed) is int
        and type(started) is int
        and window.material_started_ns
        <= started
        <= destroyed
        <= window.material_deadline_ns
    ):
        return "ATTESTED"
    return "UNPROVEN"


def save_owner(control, record):
    fd = os.open(control, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        write_at(fd, "owner.next", canonical(record))
        os.replace("owner.next", "owner.json", src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def approval_digest(identity, cfg, runner_sha256):
    identity.validate()
    return hashlib.sha256(
        canonical(
            {
                "identity": dataclasses.asdict(identity),
                "cfg": cfg,
                "runner_sha256": runner_sha256,
            }
        )
    ).hexdigest()


def launch_budget(remaining, certificate_remaining):
    budget = math.floor(
        min(MAX_SECONDS, remaining, certificate_remaining - CERTIFICATE_MARGIN_SECONDS)
    )
    if budget < MIN_PROOF_SECONDS:
        raise ClientRefused("client.deadline")
    return budget


def certificate_interval(text):
    try:
        blob = base64.b64decode(text.split()[1], validate=True)
        offset = 0

        def take(n):
            nonlocal offset
            if not 0 <= n <= len(blob) - offset:
                raise ValueError
            value = blob[offset : offset + n]
            offset += n
            return value

        def number(fmt):
            return struct.unpack(fmt, take(struct.calcsize(fmt)))[0]

        def string():
            return take(number(">I"))

        if string() != b"ssh-ed25519-cert-v01@openssh.com":
            raise ValueError
        string()
        if len(string()) != 32:
            raise ValueError
        number(">Q")
        if number(">I") != 1:
            raise ValueError
        string()
        string()
        after, before = number(">Q"), number(">Q")
        for _ in range(5):
            string()
        if offset != len(blob) or after >= before:
            raise ValueError
        return after, before
    except (ValueError, IndexError, struct.error):
        raise ClientRefused("client.certificates") from None


def trusted_parent(path):
    """Reject symlinks and writable/non-root code or state ancestors."""
    path = Path(path)
    if not path.is_absolute():
        raise ClientRefused("client.files")
    for parent in (path, *path.parents):
        s = parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ClientRefused("client.files")


@value_free
def pinned_file(path, expected):
    path = Path(path)
    trusted_parent(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_nlink != 1
            or s.st_mode & 0o022
            or s.st_size > 2_000_000
        ):
            raise ClientRefused("client.files")
        raw = os.read(fd, s.st_size + 1)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ClientRefused("client.code_digest")
        return raw
    finally:
        os.close(fd)


def identity_matches(identity):
    identity.validate()
    try:
        user = pwd.getpwnam(identity.user)
        by_uid = pwd.getpwuid(identity.uid)
    except KeyError:
        raise ClientRefused("client.identity") from None
    if (user.pw_uid, user.pw_gid, by_uid.pw_name) != (
        identity.uid,
        identity.gid,
        identity.user,
    ) or set(os.getgrouplist(identity.user, identity.gid)) - {identity.gid}:
        raise ClientRefused("client.identity")


def checked_directory(path, uid, mode):
    s = Path(path).lstat()
    if (
        not stat.S_ISDIR(s.st_mode)
        or s.st_uid != uid
        or stat.S_IMODE(s.st_mode) != mode
    ):
        raise ClientRefused("client.files")
    return s


def checked_workspace_root(path):
    trusted_parent(Path(path).parent)
    s = Path(path).lstat()
    if (
        not stat.S_ISDIR(s.st_mode)
        or s.st_uid != 0
        or (s.st_mode & 0o022 and not s.st_mode & stat.S_ISVTX)
    ):
        raise ClientRefused("client.files")


def write_at(fd, name, data, uid=0, gid=0):
    file = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(file, view)
            if written <= 0:
                raise ClientRefused("client.files")
            view = view[written:]
        os.fchown(file, uid, gid)
        os.fsync(file)
    finally:
        os.close(file)


def read_private(path, uid=0):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != uid
            or stat.S_IMODE(s.st_mode) != 0o600
            or s.st_nlink != 1
            or s.st_size > 1_000_000
        ):
            raise ClientRefused("client.files")
        return os.read(fd, s.st_size + 1)
    finally:
        os.close(fd)


def command(argv, *, timeout=10, check=True):
    result = subprocess.run(
        argv,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    if check and result.returncode:
        raise ClientRefused("client.systemd")
    return result


class SystemBackend:
    """Exact systemd effectors; callers cannot supply a worker command."""

    def window(self, started_ns, seconds):
        return material_window(started_ns, seconds)

    def arm(self, control, window):
        window.validate()
        if window.cleanup_due_ns <= time.monotonic_ns():
            raise ClientRefused("client.deadline")
        unit = control.name + "-cleanup"
        command(
            [
                "/usr/bin/systemd-run",
                "--quiet",
                "--unit=" + unit,
                "--on-boot=" + str(window.cleanup_due_ns // 1000) + "us",
                "--timer-property=AccuracySec=1us",
                "--property=User=root",
                "--property=Type=exec",
                "--property=RuntimeMaxSec=" + str(window.cleanup_runtime_seconds),
                "--property=TimeoutStopSec=" + str(window.cleanup_stop_seconds),
                "--property=KillMode=control-group",
                "--property=Restart=on-failure",
                "--property=RestartSec=1",
                "--property=StartLimitIntervalSec=infinity",
                "--property=StartLimitBurst=30",
                "--property=RestartPreventExitStatus=3",
                "/usr/bin/python3",
                "-I",
                str(Path(__file__).absolute()),
                "cleanup",
                str(control),
            ]
        )
        if not self.timer_verified(control, window):
            raise ClientRefused("client.timer")
        return unit

    def timer_verified(self, control, window):
        unit = control.name + "-cleanup"

        def show(name):
            raw = command(["/usr/bin/systemctl", "show", name]).stdout.decode()
            return dict(row.split("=", 1) for row in raw.splitlines() if "=" in row)

        timer, service = show(unit + ".timer"), show(unit + ".service")
        path = (
            command(
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
            .stdout.decode()
            .strip()
        )
        match = re.fullmatch(
            r'o "(/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+)"', path
        )
        if not match:
            return False
        raw = (
            command(
                [
                    "/usr/bin/busctl",
                    "get-property",
                    "org.freedesktop.systemd1",
                    match[1],
                    "org.freedesktop.systemd1.Timer",
                    "NextElapseUSecMonotonic",
                ]
            )
            .stdout.decode()
            .strip()
        )
        next_elapse = re.fullmatch(r"t ([0-9]+)", raw)
        argv = (
            "/usr/bin/python3 -I "
            + str(Path(__file__).absolute())
            + " cleanup "
            + str(control)
        )
        expected = re.fullmatch(
            r"\{ path=/usr/bin/python3 ; argv\[\]="
            + re.escape(argv)
            + r" ; ignore_errors=no ; start_time=[^;]* ; stop_time=[^;]* ; "
            + r"pid=0 ; code=[^;]* ; status=[^;]* \}",
            service.get("ExecStart", ""),
        )
        return bool(
            next_elapse
            and 0 <= window.cleanup_due_ns - int(next_elapse[1]) * 1000 < 1000
            and timer.get("ActiveState") == "active"
            and timer.get("SubState") == "waiting"
            and timer.get("Unit") == unit + ".service"
            and timer.get("Transient") == "yes"
            and service.get("Transient") == "yes"
            and service.get("Type") == "exec"
            and service.get("RuntimeMaxUSec")
            == str(window.cleanup_runtime_seconds) + "s"
            and service.get("TimeoutStopUSec") == str(window.cleanup_stop_seconds) + "s"
            and service.get("KillMode") == "control-group"
            and expected
            and service.get("Restart") == "on-failure"
            and service.get("RestartUSec") == "1s"
            and service.get("StartLimitBurst") == "30"
            and service.get("StartLimitIntervalUSec") == "infinity"
            and service.get("RestartPreventExitStatus") == "3"
        )

    def disarm(self, timer):
        command(["/usr/bin/systemctl", "stop", timer + ".timer"])

    def start(self, record, budget, network_namespace):
        identity = RunnerIdentity(**record["identity"])
        props = [
            "User=" + str(identity.uid),
            "Group=" + str(identity.gid),
            "SupplementaryGroups=",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
            "NoNewPrivileges=yes",
            "RestrictSUIDSGID=yes",
            "KillMode=control-group",
            "UMask=0077",
            "Type=exec",
            "RuntimeMaxSec=" + str(budget),
            "TimeoutStopSec=3",
            "WorkingDirectory=" + record["work"],
        ]
        if network_namespace is not None:
            if not re.fullmatch(r"/proc/[1-9][0-9]*/ns/net", network_namespace):
                raise ClientRefused("client.namespace")
            props.append("NetworkNamespacePath=" + network_namespace)
        argv = [
            "/usr/bin/systemd-run",
            "--quiet",
            "--collect",
            "--unit=" + record["unit"],
        ]
        argv.extend("--property=" + item for item in props)
        argv += [
            "/usr/bin/python3",
            "-I",
            record["launcher"],
            "child",
            record["work"],
            record["runner"],
            record["runner_sha256"],
            record["ready_socket"],
        ]
        command(argv, timeout=min(10, budget))

    def cgroup(self, unit):
        raw = (
            command(
                [
                    "/usr/bin/systemctl",
                    "show",
                    "--property=ControlGroup",
                    "--value",
                    unit + ".service",
                ]
            )
            .stdout.decode()
            .strip()
        )
        if not re.fullmatch(r"/system.slice/" + re.escape(unit) + r"\.service", raw):
            raise ClientRefused("client.cgroup")
        return raw

    def main_pid(self, unit):
        raw = command(
            [
                "/usr/bin/systemctl",
                "show",
                "--property=MainPID",
                "--value",
                unit + ".service",
            ]
        ).stdout.strip()
        if not raw.isdigit() or int(raw) <= 0:
            raise ClientRefused("client.peer")
        return int(raw)

    def stop(self, record):
        result = command(
            ["/usr/bin/systemctl", "stop", record["unit"] + ".service"], check=False
        )
        if result.returncode not in (0, 5):
            raise ClientRefused("client.cleanup")
        expected = "/system.slice/" + record["unit"] + ".service"
        group = Path("/sys/fs/cgroup" + expected)
        if group.exists():
            for procs in group.rglob("cgroup.procs"):
                if procs.read_text().strip():
                    raise ClientRefused("client.cleanup")
        # cgroup disappearance alone is insufficient: inspect every descendant.
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                try:
                    groups = (entry / "cgroup").read_text().splitlines()
                except (FileNotFoundError, ProcessLookupError):
                    continue
                if any(
                    row.split(":", 2)[-1] == expected
                    or row.split(":", 2)[-1].startswith(expected + "/")
                    for row in groups
                ):
                    raise ClientRefused("client.cleanup")


def verify_peer(connection, identity, cgroup, expected_pid):
    pid, uid, gid = struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    )
    if (uid, gid) != (identity.uid, identity.gid) or pid != expected_pid:
        raise ClientRefused("client.peer")
    proc = Path("/proc") / str(pid)
    # A duplicate primary GID in Groups adds no authority. Any other group is
    # supplementary privilege and is refused, alongside every capability mask.
    fields = dict(
        row.split(":", 1)
        for row in (proc / "status").read_text().splitlines()
        if ":" in row
    )
    if (
        [int(v) for v in fields["Uid"].split()] != [identity.uid] * 4
        or [int(v) for v in fields["Gid"].split()] != [identity.gid] * 4
        or set(map(int, fields["Groups"].split())) - {identity.gid}
        or int(fields["CapEff"], 16)
        or int(fields["CapPrm"], 16)
        or int(fields["CapInh"], 16)
        or int(fields["CapBnd"], 16)
        or int(fields["CapAmb"], 16)
        or fields["NoNewPrivs"].strip() != "1"
        or "0::" + cgroup not in (proc / "cgroup").read_text().splitlines()
    ):
        raise ClientRefused("client.peer")
    data, ancillary, flags, _ = connection.recvmsg(
        64, socket.CMSG_SPACE(array.array("i").itemsize)
    )
    descriptors = []
    try:
        for level, kind, raw in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array("i")
                values.frombytes(raw[: len(raw) - len(raw) % values.itemsize])
                descriptors.extend(values)
        if data != b"B8_READY_V1" or flags or len(descriptors) != 1:
            raise ClientRefused("client.peer")
        fd = descriptors[0]
        inode = os.fstat(fd).st_ino
        with socket.fromfd(fd, socket.AF_INET, socket.SOCK_DGRAM) as proof:
            if (
                proof.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_DGRAM
                or proof.getsockname()[0] != "127.0.0.1"
            ):
                raise ClientRefused("client.socket_uid")
        # Linux sock_i_uid is the UID nftables meta skuid matches. Verify the
        # exact passed descriptor's inode in the child's actual network namespace.
        rows = (proc / "net" / "udp").read_text().splitlines()[1:]
        matching = [
            row.split()
            for row in rows
            if len(row.split()) > 9 and row.split()[9] == str(inode)
        ]
        if len(matching) != 1 or int(matching[0][7]) != identity.uid:
            raise ClientRefused("client.socket_uid")
    finally:
        for fd in descriptors:
            os.close(fd)
    return pid


@contextlib.contextmanager
def transport_operation_lock():
    trusted_parent(TRANSPORT_ROOT)
    checked_directory(TRANSPORT_ROOT, 0, 0o700)
    fd = os.open(TRANSPORT_ROOT / ".lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_nlink != 1
            or stat.S_IMODE(s.st_mode) != 0o600
        ):
            raise ClientRefused("client.closure_lock")
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ClientRefused("client.closure_lock") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def active_owned_identity(session, identity):
    directory = IDENTITY_ROOT / session
    trusted_parent(directory)
    checked_directory(directory, 0, 0o700)
    record = json.loads(read_private(directory / "owner.json"))
    if (
        record.get("session_id") != session
        or record.get("user") != identity.user
        or type(record.get("uid")) is not int
        or record["uid"] != identity.uid
        or type(record.get("gid")) is not int
        or record["gid"] != identity.gid
        or any(
            record.get(key) is not True
            for key in (
                "group_attempted",
                "group_created",
                "user_attempted",
                "user_created",
            )
        )
    ):
        raise ClientRefused("client.identity_owner")


def no_uid_resources(uid, network_namespace):
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit():
            try:
                row = next(
                    row
                    for row in (proc / "status").read_text().splitlines()
                    if row.startswith("Uid:")
                )
            except (FileNotFoundError, ProcessLookupError):
                continue
            if uid in map(int, row.split()[1:]):
                raise ClientRefused("client.uid_processes")
    if not re.fullmatch(r"/proc/[1-9][0-9]*/ns/net", network_namespace):
        raise ClientRefused("client.namespace")
    net = Path(network_namespace).parent.parent / "net"
    for protocol in ("tcp", "tcp6", "udp", "udp6", "raw", "raw6"):
        for row in (net / protocol).read_text().splitlines()[1:]:
            fields = row.split()
            if len(fields) > 7 and fields[7] == str(uid):
                raise ClientRefused("client.uid_sockets")


def write_closure(record, status, phase):
    session = record["session_id"]
    if not re.fullmatch(r"[0-9a-f]{32}", session):
        raise ClientRefused("client.session")
    CLIENT_PROOF_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    trusted_parent(CLIENT_PROOF_ROOT)
    checked_directory(CLIENT_PROOF_ROOT, 0, 0o700)
    directory = CLIENT_PROOF_ROOT / session
    directory.mkdir(mode=0o700, exist_ok=True)
    checked_directory(directory, 0, 0o700)
    proof = {
        "schema": "dotmac.b8-client-closure.v1",
        "session_id": session,
        "identity": record["identity"],
        "manifest_sha256": record["identity"]["manifest_sha256"],
        "status": status,
        "phase": phase,
        "control": record["control"],
        "bundle": record["bundle"],
        "network_namespace": record["network_namespace"],
        "material_cap": material_cap_verdict(record),
        "material_window": record.get("material_window"),
        "first_cleanup": record.get("first_cleanup"),
        "destroyed_ns": record.get("destroyed_ns"),
    }
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        write_at(fd, "closed.next", canonical(proof))
        os.replace("closed.next", "closed.json", src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)


class Session:
    def __init__(
        self,
        identity,
        manifest_path,
        runner_path,
        runner_sha256,
        approval_sha256,
        cfg,
        *,
        custody_ready=False,
        control_root=Path("/run/dotmac-b8-control"),
        workspace_root=Path("/dev/shm"),  # noqa: S108 - bounded memory-only workspace
        backend=None,
        clock=time.monotonic,
        wall_clock=time.time,
        network_namespace=None,
    ):
        self.identity = identity
        self.manifest_path, self.runner_path = Path(manifest_path), Path(runner_path)
        self.runner_sha256, self.approval_sha256 = runner_sha256, approval_sha256
        self.cfg = json.loads(canonical(cfg))
        self.session_id = self.cfg.get("session_id")
        if not isinstance(self.session_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}", self.session_id
        ):
            raise ClientRefused("client.session")
        self.custody_ready = custody_ready
        self.control_root, self.workspace_root = (
            Path(control_root),
            Path(workspace_root),
        )
        self.backend = backend or SystemBackend()
        self.clock, self.wall_clock = clock, wall_clock
        self.network_namespace = self.cfg.get("network_namespace", "/proc/1/ns/net")
        if (
            network_namespace is not None
            and network_namespace != self.network_namespace
        ):
            raise ClientRefused("client.namespace")
        self.control = self.bundle = self.work = self.record = self.timer = None
        self.workfd = None
        self.deadline = self.certificate_until = None
        self.intent = None
        self.cleanup_receipt = None

    @value_free
    def preflight(self):
        if os.geteuid() != 0 or self.custody_ready is not True:
            raise ClientRefused("client.custody_ready")
        identity_matches(self.identity)
        active_owned_identity(self.session_id, self.identity)
        if self.cfg.get("approved_proof_client") is not True:
            raise ClientRefused("client.approval")
        if (
            approval_digest(self.identity, self.cfg, self.runner_sha256)
            != self.approval_sha256
        ):
            raise ClientRefused("client.approval")
        manifest = json.loads(
            pinned_file(self.manifest_path, self.identity.manifest_sha256)
        )
        for field, expected in (
            ("runner_user", self.identity.user),
            ("runner_uid", self.identity.uid),
            ("runner_gid", self.identity.gid),
        ):
            actual = manifest.get(field)
            if type(actual) is not type(expected) or actual != expected:
                raise ClientRefused("client.manifest_identity")
        pinned_file(self.runner_path, self.runner_sha256)
        launcher = Path(__file__).absolute()
        trusted_parent(launcher.parent)
        s = launcher.lstat()
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_mode & 0o022
            or s.st_nlink != 1
        ):
            raise ClientRefused("client.code")

    def remaining(self):
        left = self.deadline - self.clock()
        if left < MIN_PROOF_SECONDS:
            raise ClientRefused("client.deadline")
        return left

    @value_free
    def initialize_closed(self):
        self.preflight()
        with transport_operation_lock():
            return self._initialize_closed_locked()

    def _initialize_closed_locked(self):
        self.preflight()
        record = {
            "session_id": self.session_id,
            "identity": dataclasses.asdict(self.identity),
            "control": str(self.control_root / (PREFIX + self.session_id)),
            "bundle": str(self.workspace_root / (PREFIX + self.session_id)),
            "network_namespace": self.network_namespace,
        }
        previous = CLIENT_PROOF_ROOT / self.session_id / "closed.json"
        if previous.exists():
            proof = json.loads(read_private(previous))
            if (
                proof.get("identity") != record["identity"]
                or proof.get("phase") != "not-started"
                or proof.get("status") != "closed"
            ):
                raise ClientRefused("client.session_reuse")
        for field in ("control", "bundle"):
            path = Path(record[field])
            if path.exists() or path.is_symlink():
                raise ClientRefused("client.not_closed")
        self.backend.stop({"unit": PREFIX + self.session_id + "-matrix"})
        no_uid_resources(self.identity.uid, self.network_namespace)
        write_closure(record, "closed", "not-started")
        return record

    @value_free
    @cleanup_on_failure
    def prepare(self, identity, seconds=240):
        if (
            self.control is not None
            or type(seconds) is not int
            or not MIN_WINDOW_SECONDS <= seconds <= MAX_SECONDS
        ):
            raise ClientRefused("client.window")
        if (
            set(identity) != {"jump_principal", "probe_principal"}
            or identity["jump_principal"] != "lane3jump"
            or not isinstance(identity["probe_principal"], str)
            or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", identity["probe_principal"])
            or identity["probe_principal"]
            in ("root", "lane3obs", "lane3jump", "lane3b8wrong")
        ):
            raise ClientRefused("client.principal")
        if self.cfg.get("probe", {}).get("account") != identity["probe_principal"]:
            raise ClientRefused("client.principal")
        self.preflight()
        with transport_operation_lock():
            intent = self._initialize_closed_locked()
            self.preflight()
            write_closure(intent, "active", "started")
            self.intent = intent
        self.control_root.mkdir(mode=0o700, exist_ok=True)
        trusted_parent(self.control_root)
        checked_workspace_root(self.workspace_root)
        checked_directory(self.control_root, 0, 0o700)
        name = PREFIX + self.session_id
        self.control = self.control_root / name
        self.control.mkdir(mode=0o700)
        self.bundle = self.workspace_root / name
        self.bundle.mkdir(mode=0o700)
        bundlefd = os.open(self.bundle, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(bundlefd, 0o711)
            checked_directory(self.bundle, 0, 0o711)
        finally:
            os.close(bundlefd)
        self.work = self.bundle / "work"
        self.work.mkdir(mode=0o700)
        self.workfd = os.open(self.work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.window = self.backend.window(
            math.floor(self.clock() * NS), seconds
        ).validate()
        self.deadline = self.window.execution_deadline_ns / NS
        self.record = {
            "version": 1,
            "material_window": dataclasses.asdict(self.window),
            "session_id": self.session_id,
            "network_namespace": self.network_namespace,
            "identity": dataclasses.asdict(self.identity),
            "control": str(self.control),
            "bundle": str(self.bundle),
            "work": str(self.work),
            "unit": name + "-matrix",
            "work_inode": os.fstat(self.workfd).st_ino,
            "bundle_inode": self.bundle.stat().st_ino,
            "launcher": str(Path(__file__).absolute()),
            "runner": str(self.runner_path),
            "runner_sha256": self.runner_sha256,
            "ready_socket": name + "-" + uuid.uuid4().hex,
        }
        cfd = os.open(self.control, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            write_at(cfd, "owner.json", canonical(self.record))
            write_at(cfd, "cleanup.lock", b"")
            write_closure(self.record, "active", "started")
        finally:
            os.close(cfd)
        try:
            # Record exists before arming. Arm attempt may commit and lose its
            # response: cleanup always addresses the deterministic timer name.
            self.timer = name + "-cleanup"
            if self.backend.arm(self.control, self.window) != self.timer:
                raise ClientRefused("client.timer")
            if not self.backend.timer_verified(self.control, self.window):
                raise ClientRefused("client.timer")
            public, material = {}, {}
            for keyname in NAMES:
                self.remaining()
                key = self.work / keyname
                command(
                    [
                        "/usr/bin/ssh-keygen",
                        "-q",
                        "-t",
                        "ed25519",
                        "-N",
                        "",
                        "-C",
                        "",
                        "-f",
                        str(key),
                    ],
                    timeout=min(10, self.remaining()),
                )
                for suffix in ("", ".pub"):
                    fd = os.open(
                        keyname + suffix,
                        os.O_RDONLY | os.O_NOFOLLOW,
                        dir_fd=self.workfd,
                    )
                    try:
                        s = os.fstat(fd)
                        if (
                            not stat.S_ISREG(s.st_mode)
                            or s.st_uid != 0
                            or s.st_nlink != 1
                        ):
                            raise ClientRefused("client.files")
                        os.fchmod(fd, 0o600)
                        os.fchown(fd, self.identity.uid, self.identity.gid)
                    finally:
                        os.close(fd)
                material[keyname] = [
                    str(key),
                    None if keyname == "plain_key" else str(key) + "-cert.pub",
                ]
                if keyname != "plain_key":
                    public[keyname] = (
                        read_private(str(key) + ".pub", self.identity.uid)
                        .decode()
                        .strip()
                    )
            self.material = material
            os.fchown(self.workfd, self.identity.uid, self.identity.gid)
            return dict(identity, public_keys=public)
        except BaseException:
            self.cleanup()
            raise

    @value_free
    def install_certificates(self, certificates, *, valid_until_epoch):
        self.remaining()
        if (
            self.certificate_until is not None
            or set(certificates) != set(NAMES) - {"plain_key"}
            or type(valid_until_epoch) not in (int, float)
            or not math.isfinite(valid_until_epoch)
        ):
            raise ClientRefused("client.certificates")
        launch_budget(self.remaining(), valid_until_epoch - self.wall_clock())
        for text in certificates.values():
            if (
                not isinstance(text, str)
                or not text.startswith("ssh-ed25519-cert-v01@openssh.com ")
                or len(text) > 16384
                or "\n" in text.strip()
            ):
                raise ClientRefused("client.certificates")
        intervals = {
            name: certificate_interval(text) for name, text in certificates.items()
        }
        now = self.wall_clock()
        if (
            any(
                after > now
                for name, (after, before) in intervals.items()
                if name != "expired_observer"
            )
            or intervals["expired_observer"][1] > now
        ):
            raise ClientRefused("client.certificates")
        valid_until_epoch = min(
            valid_until_epoch,
            *(
                before
                for name, (after, before) in intervals.items()
                if name != "expired_observer"
            ),
        )
        launch_budget(self.remaining(), valid_until_epoch - now)
        self.verify_workspace()
        for name, text in certificates.items():
            write_at(
                self.workfd,
                name + "-cert.pub",
                (text.strip() + "\n").encode(),
                self.identity.uid,
                self.identity.gid,
            )
        self.certificate_until = valid_until_epoch

    @value_free
    def verify_workspace(self):
        checked_workspace_root(self.workspace_root)
        checked_directory(self.control_root, 0, 0o700)
        checked_directory(self.control, 0, 0o700)
        checked_directory(self.bundle, 0, 0o711)
        s = checked_directory(self.work, self.identity.uid, 0o700)
        if (
            s.st_ino != self.record["work_inode"]
            or self.bundle.stat().st_ino != self.record["bundle_inode"]
            or s.st_ino != os.fstat(self.workfd).st_ino
        ):
            raise ClientRefused("client.files")
        for name in os.listdir(self.workfd):
            s = os.stat(name, dir_fd=self.workfd, follow_symlinks=False)
            if (
                not stat.S_ISREG(s.st_mode)
                or s.st_uid != self.identity.uid
                or s.st_gid != self.identity.gid
                or stat.S_IMODE(s.st_mode) != 0o600
                or s.st_nlink != 1
            ):
                raise ClientRefused("client.files")

    @value_free
    def execute(self):
        try:
            result = self._execute()
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            raise ClientRefused("client.execution") from None
        finally:
            receipt = self.cleanup()
        if receipt and receipt["material_cap"] == "UNPROVEN":
            raise ClientRefused("client.material_cap_unproven")
        return result

    def _execute(self):
        if self.certificate_until is None:
            raise ClientRefused("client.certificates")
        self.preflight()
        self.verify_workspace()
        cfg = dict(
            self.cfg, material=self.material, known_hosts=str(self.work / "known_hosts")
        )
        # Caller supplies public pinned host material as content, never a path
        # handed across ownership boundaries.
        known_hosts = cfg.pop("known_hosts_text", None)
        if (
            not isinstance(known_hosts, str)
            or not known_hosts
            or len(known_hosts) > 131072
        ):
            raise ClientRefused("client.known_hosts")
        write_at(
            self.workfd,
            "known_hosts",
            known_hosts.encode(),
            self.identity.uid,
            self.identity.gid,
        )
        write_at(
            self.workfd,
            "matrix.json",
            canonical(cfg),
            self.identity.uid,
            self.identity.gid,
        )
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind("\0" + self.record["ready_socket"])
            listener.listen(1)
            # Last checks immediately precede process creation, after all file
            # handoffs: neither account nor manifest drift can be overlooked.
            self.preflight()
            budget = launch_budget(
                self.remaining(), self.certificate_until - self.wall_clock()
            )
            self.backend.start(self.record, budget, self.network_namespace)
            listener.settimeout(min(10, self.remaining()))
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(min(10, self.remaining()))
                verify_peer(
                    connection,
                    self.identity,
                    self.backend.cgroup(self.record["unit"]),
                    self.backend.main_pid(self.record["unit"]),
                )
                self.preflight()
                launch_budget(
                    self.remaining(), self.certificate_until - self.wall_clock()
                )
                connection.sendall(b"B8_GO_V1")
                connection.settimeout(self.remaining())
                raw = bytearray()
                while len(raw) <= 1_000_000:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > 1_000_000:
                    raise ClientRefused("client.result")
                envelope = json.loads(raw)
                if (
                    set(envelope) != {"returncode", "result"}
                    or type(envelope["returncode"]) is not int
                    or envelope["returncode"] not in (0, 1, 2)
                    or not isinstance(envelope["result"], dict)
                ):
                    raise ClientRefused("client.result")
                return envelope["result"]
        finally:
            listener.close()

    @value_free
    def cleanup(self):
        if self.workfd is not None:
            os.close(self.workfd)
            self.workfd = None
        if self.record is None:
            if self.intent is None:
                return closed_receipt(self.session_id) if self.cleanup_receipt else None
            self.backend.stop({"unit": PREFIX + self.session_id + "-matrix"})
            no_uid_resources(self.identity.uid, self.network_namespace)
            for field in ("bundle", "control"):
                path = Path(self.intent[field])
                if path.exists() or path.is_symlink():
                    trusted_parent(
                        path.parent
                    ) if field == "control" else checked_workspace_root(path.parent)
                    children = list(path.rglob("*"))
                    for child in (path, *children):
                        mode = child.lstat()
                        if (
                            not stat.S_ISDIR(mode.st_mode)
                            or mode.st_uid != 0
                            or stat.S_IMODE(mode.st_mode) not in (0o700, 0o711)
                        ):
                            raise ClientRefused("client.early_cleanup")
                    for child in sorted(
                        children, key=lambda p: len(p.parts), reverse=True
                    ):
                        child.rmdir()
                    path.rmdir()
            write_closure(self.intent, "closed", "finished")
            self.intent = None
            self.cleanup_receipt = {
                "resource_closed": True,
                "material_cap": "NOT_STARTED",
            }
            return self.cleanup_receipt
        self.cleanup_receipt = cleanup_control(self.control, backend=self.backend)
        self.record = None
        self.timer = None
        self.intent = None
        return self.cleanup_receipt


@value_free
def cleanup_control(control, *, backend=None):
    """Root timer and normal finally share the same verified cleanup owner."""
    backend = backend or SystemBackend()
    control = Path(control)
    if os.geteuid() != 0 or not re.fullmatch(PREFIX + r"[0-9a-f]{32}", control.name):
        raise ClientRefused("client.cleanup_scope")
    trusted_parent(control.parent)
    checked_directory(control.parent, 0, 0o700)
    if not control.exists():
        return closed_receipt(control.name[len(PREFIX) :])
    try:
        checked_directory(control, 0, 0o700)
        lockfd = os.open(control / "cleanup.lock", os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        if not control.exists():
            return closed_receipt(control.name[len(PREFIX) :])
        raise
    try:
        lockstat = os.fstat(lockfd)
        if (
            not stat.S_ISREG(lockstat.st_mode)
            or lockstat.st_uid != 0
            or lockstat.st_nlink != 1
            or stat.S_IMODE(lockstat.st_mode) != 0o600
        ):
            raise ClientRefused("client.cleanup")
        fcntl.flock(lockfd, fcntl.LOCK_EX)
        if not control.exists():
            return closed_receipt(control.name[len(PREFIX) :])
        record = json.loads(read_private(control / "owner.json"))
        identity = RunnerIdentity(**record["identity"])
        identity.validate()
        bundle, work = Path(record["bundle"]), Path(record["work"])
        if (
            record["control"] != str(control)
            or bundle.name != control.name
            or work != bundle / "work"
            or record["unit"] != control.name + "-matrix"
            or not bundle.is_absolute()
        ):
            raise ClientRefused("client.cleanup_scope")
        MaterialWindow(**record["material_window"]).validate()
        first_attempt = "first_cleanup" not in record
        if first_attempt:
            record["first_cleanup"] = {
                "started_ns": time.monotonic_ns(),
                "outcome": "started",
                "destroyed_ns": None,
            }
        elif record["first_cleanup"].get("outcome") == "started":
            record["first_cleanup"]["outcome"] = "failed"
        save_owner(control, record)
        write_closure(record, "active", "started")
        try:
            checked_workspace_root(bundle.parent)
            backend.stop(record)
            no_uid_resources(identity.uid, record["network_namespace"])
            # Stop is proved before any material disappears; failed stop retains timer.
            if bundle.exists():
                s = checked_directory(bundle, 0, 0o711)
                if s.st_ino != record["bundle_inode"]:
                    raise ClientRefused("client.cleanup")
                if work.exists():
                    s = work.lstat()
                    if (
                        not stat.S_ISDIR(s.st_mode)
                        or s.st_ino != record["work_inode"]
                        or s.st_uid not in (0, identity.uid)
                    ):
                        raise ClientRefused("client.cleanup")
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise ClientRefused("client.cleanup")
                shutil.rmtree(bundle)
            if bundle.exists() or bundle.is_symlink():
                raise ClientRefused("client.cleanup")
            no_uid_resources(identity.uid, record["network_namespace"])
            destroyed = time.monotonic_ns()
            record.setdefault("destroyed_ns", destroyed)
            if first_attempt:
                record["first_cleanup"].update(
                    outcome="succeeded", destroyed_ns=destroyed
                )
            save_owner(control, record)
            write_closure(record, "closed", "finished")
            shutil.rmtree(control)
            backend.disarm(control.name + "-cleanup")
            return {
                "resource_closed": True,
                "material_cap": material_cap_verdict(record),
            }
        except BaseException:
            if control.exists() and record["first_cleanup"].get("outcome") == "started":
                record["first_cleanup"]["outcome"] = "failed"
                save_owner(control, record)
                write_closure(record, "active", "started")
            raise

    finally:
        os.close(lockfd)


def closed_receipt(session):
    directory = CLIENT_PROOF_ROOT / session
    trusted_parent(directory)
    checked_directory(directory, 0, 0o700)
    proof = json.loads(read_private(directory / "closed.json"))
    if (
        proof.get("session_id") != session
        or proof.get("status") != "closed"
        or proof.get("material_cap") not in ("ATTESTED", "UNPROVEN", "NOT_STARTED")
    ):
        raise ClientRefused("client.cleanup_unproven")
    return {"resource_closed": True, "material_cap": proof["material_cap"]}


def child_main(work, runner, runner_sha256, ready_socket):
    """Fixed launcher: handshake precedes execution of the pinned matrix file."""
    import contextlib
    import io

    os.umask(0o077)
    work = Path(work)
    if (
        os.getuid() == 0
        or os.geteuid() != os.getuid()
        or os.getgid() == 0
        or set(os.getgroups()) - {os.getgid()}
    ):
        raise ClientRefused("client.child")
    pinned_file(runner, runner_sha256)
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as proof,
    ):
        proof.bind(("127.0.0.1", 0))
        peer.connect("\0" + ready_socket)
        descriptors = array.array("i", [proof.fileno()])
        peer.sendmsg(
            [b"B8_READY_V1"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptors)]
        )
        if peer.recv(32) != b"B8_GO_V1":
            raise ClientRefused("client.child")
        output = io.StringIO()
        rc = 0
        sys.argv = [runner, str(work / "matrix.json")]
        try:
            with (
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                runpy.run_path(runner, run_name="__main__")
        except SystemExit as exc:
            rc = exc.code
        result = json.loads(output.getvalue())
        if type(rc) is not int or rc not in (0, 1, 2) or not isinstance(result, dict):
            raise ClientRefused("client.result")
        peer.sendall(canonical({"returncode": rc, "result": result}))


def main():
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "cleanup":
            receipt = cleanup_control(sys.argv[2])
            print(canonical(receipt).decode())
            return 3 if receipt["material_cap"] == "UNPROVEN" else 0
        elif len(sys.argv) == 6 and sys.argv[1] == "child":
            child_main(*sys.argv[2:])
        else:
            raise ClientRefused("client.operation")
        return 0
    except BaseException:
        # No traceback, argv, material, host or stderr is exposed by this CLI.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
