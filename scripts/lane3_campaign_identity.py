#!/usr/bin/env python3
"""Source-owned temporary B8 account lifecycle; no shadow reads or process kills.

The persistent root journal proves which exact mutations completed. An uncertain
creation response is never promoted into deletion authority. Timer retries retain
that evidence. The unit permits at most 30 start attempts, including callbacks
killed by RuntimeMaxSec; no rearm or reset-failed clears that lifetime limit.
Reboot recovery still requires explicit owner reconciliation.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import grp
import hashlib
import json
import math
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

IDENTITY_ROOT = Path("/var/lib/dotmac-lane3-campaign/identities")
TRANSPORT_ROOT = Path("/run/dotmac-lane3-campaign/transport")
CLIENT_ROOT = Path("/var/lib/dotmac-lane3-campaign/client")


class IdentityRefused(RuntimeError):
    """Value-free ownership or cleanup refusal."""


def safe(method):
    @functools.wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            raise IdentityRefused("identity.operation_failed") from None

    return call


def trusted_directory(path, *, private=False):
    path = Path(path)
    if not path.is_absolute():
        raise IdentityRefused("identity.files")
    for parent in (path, *path.parents):
        s = parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise IdentityRefused("identity.files")
    if private and stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise IdentityRefused("identity.files")


def command(argv, *, timeout=10):
    result = subprocess.run(
        argv,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    if result.returncode:
        raise IdentityRefused("identity.effector")
    return result


def lookup(function, value):
    try:
        return function(value)
    except KeyError:
        return None


def absent(name, uid, gid):
    if (
        lookup(pwd.getpwnam, name) is not None
        or lookup(pwd.getpwuid, uid) is not None
        or lookup(grp.getgrnam, name) is not None
        or lookup(grp.getgrgid, gid) is not None
    ):
        raise IdentityRefused("identity.not_absent")


def exact_user(record):
    name, uid, gid = record["user"], record["uid"], record["gid"]
    by_name, by_id = lookup(pwd.getpwnam, name), lookup(pwd.getpwuid, uid)
    if by_name is None and by_id is None:
        return False
    if (
        by_name is None
        or by_id is None
        or by_id.pw_name != name
        or (by_name.pw_uid, by_name.pw_gid, by_name.pw_dir, by_name.pw_shell)
        != (uid, gid, "/nonexistent", "/usr/sbin/nologin")
        or set(os.getgrouplist(name, gid)) - {gid}
    ):
        raise IdentityRefused("identity.drift")
    return True


def exact_group(record):
    name, gid = record["user"], record["gid"]
    by_name, by_id = lookup(grp.getgrnam, name), lookup(grp.getgrgid, gid)
    if by_name is None and by_id is None:
        return False
    if (
        by_name is None
        or by_id is None
        or by_id.gr_name != name
        or by_name.gr_gid != gid
        or by_name.gr_mem
        or any(p.pw_gid == gid and p.pw_name != name for p in pwd.getpwall())
    ):
        raise IdentityRefused("identity.drift")
    return True


def no_uid_processes(uid):
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            try:
                rows = (path / "status").read_text().splitlines()
            except (FileNotFoundError, ProcessLookupError):
                continue
            row = next(row for row in rows if row.startswith("Uid:"))
            if uid in map(int, row.split()[1:]):
                raise IdentityRefused("identity.processes")


def write_record(directory, record):
    temporary = directory / "owner.next"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        )
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise IdentityRefused("identity.files")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, directory / "owner.json")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_record(directory):
    fd = os.open(directory / "owner.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_nlink != 1
            or stat.S_IMODE(s.st_mode) != 0o600
            or s.st_size > 4096
        ):
            raise IdentityRefused("identity.files")
        record = json.loads(os.read(fd, s.st_size + 1))
    finally:
        os.close(fd)
    if (
        set(record)
        != {
            "session_id",
            "user",
            "uid",
            "gid",
            "group_attempted",
            "group_created",
            "user_attempted",
            "user_created",
        }
        or record["session_id"] != directory.name
        or record["user"] != "l3b8-" + directory.name[:12]
        or any(
            type(record[k]) is not bool
            for k in (
                "group_attempted",
                "group_created",
                "user_attempted",
                "user_created",
            )
        )
    ):
        raise IdentityRefused("identity.record")
    validate(directory.name, record["uid"], record["gid"])
    return record


def validate(session_id, uid, gid):
    if (
        not isinstance(session_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", session_id)
        or type(uid) is not int
        or not 0 < uid < 2**32 - 1
        or type(gid) is not int
        or not 0 < gid < 2**32 - 1
    ):
        raise IdentityRefused("identity.input")


def protected_json(path):
    trusted_directory(path.parent, private=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_nlink != 1
            or stat.S_IMODE(s.st_mode) != 0o600
            or s.st_size > 1_000_000
        ):
            raise IdentityRefused("identity.closure")
        return json.loads(os.read(fd, s.st_size + 1))
    finally:
        os.close(fd)


def verify_resources_closed(record):
    session = record["session_id"]
    client = protected_json(CLIENT_ROOT / session / "closed.json")
    transport = protected_json(TRANSPORT_ROOT / session / "state.json")
    identity = client.get("identity", {})
    manifest = transport.get("manifest", {})
    binding = transport.get("binding", {})
    expected = {"user": record["user"], "uid": record["uid"], "gid": record["gid"]}
    if (
        client.get("schema") != "dotmac.b8-client-closure.v1"
        or client.get("status") != "closed"
        or client.get("phase") not in ("not-started", "finished")
        or client.get("session_id") != session
        or transport.get("schema") != "dotmac.b8-ssh-state.v1"
        or transport.get("status") != "closed"
        or transport.get("phase") not in ("not-started", "finished")
        or binding.get("session_id") != session
        or not isinstance(binding.get("manifest_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", binding["manifest_sha256"])
        or hashlib.sha256(
            json.dumps(
                manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()
        != binding["manifest_sha256"]
        or client.get("manifest_sha256") != binding["manifest_sha256"]
        or identity.get("manifest_sha256") != binding["manifest_sha256"]
    ):
        raise IdentityRefused("identity.closure")
    for field, value in expected.items():
        if type(identity.get(field)) is not type(value) or identity[field] != value:
            raise IdentityRefused("identity.closure")
        key = "runner_" + field
        if type(manifest.get(key)) is not type(value) or manifest[key] != value:
            raise IdentityRefused("identity.closure")
    namespace = client.get("network_namespace")
    if not isinstance(namespace, str) or not re.fullmatch(
        r"/proc/[1-9][0-9]*/ns/net", namespace
    ):
        raise IdentityRefused("identity.closure")
    net = Path(namespace).parent.parent / "net"
    for protocol in ("tcp", "tcp6", "udp", "udp6", "raw", "raw6"):
        for row in (net / protocol).read_text().splitlines()[1:]:
            fields = row.split()
            if len(fields) > 7 and fields[7] == str(record["uid"]):
                raise IdentityRefused("identity.sockets")
    for field in ("control", "bundle"):
        path = Path(client[field])
        if path.exists() or path.is_symlink():
            raise IdentityRefused("identity.closure")


@contextlib.contextmanager
def transport_operation_lock():
    trusted_directory(TRANSPORT_ROOT, private=True)
    fd = os.open(TRANSPORT_ROOT / ".lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_nlink != 1
            or stat.S_IMODE(s.st_mode) != 0o600
        ):
            raise IdentityRefused("identity.closure_lock")
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise IdentityRefused("identity.closure_lock") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


class SystemBackend:
    root = IDENTITY_ROOT

    def operation_lock(self):
        return transport_operation_lock()

    def verify_closure(self, record):
        verify_resources_closed(record)

    def arm(self, session_id, seconds):
        launcher = Path(__file__).absolute()
        trusted_directory(launcher.parent)
        s = launcher.lstat()
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_nlink != 1
            or s.st_mode & 0o022
        ):
            raise IdentityRefused("identity.code")
        unit = "dotmac-b8-identity-" + session_id
        command(
            [
                "/usr/bin/systemd-run",
                "--quiet",
                "--unit=" + unit,
                "--on-active=" + str(seconds) + "s",
                "--timer-property=AccuracySec=1us",
                "--property=User=root",
                "--property=Type=exec",
                "--property=RuntimeMaxSec=30",
                "--property=Restart=on-failure",
                "--property=RestartSec=1",
                "--property=StartLimitIntervalSec=infinity",
                "--property=StartLimitBurst=30",
                "/usr/bin/python3",
                "-I",
                str(launcher),
                "cleanup",
                session_id,
            ]
        )
        command(["/usr/bin/systemctl", "is-active", "--quiet", unit + ".timer"])

    def disarm(self, session_id):
        command(
            [
                "/usr/bin/systemctl",
                "stop",
                "dotmac-b8-identity-" + session_id + ".timer",
            ]
        )

    def create_group(self, name, gid, *, timeout=10):
        command(["/usr/sbin/groupadd", "--gid", str(gid), name], timeout=timeout)

    def create_user(self, name, uid, gid, *, timeout=10):
        command(
            [
                "/usr/sbin/useradd",
                "--uid",
                str(uid),
                "--gid",
                str(gid),
                "--no-user-group",
                "--no-create-home",
                "--home-dir",
                "/nonexistent",
                "--shell",
                "/usr/sbin/nologin",
                "--password",
                "!",
                "--no-log-init",
                name,
            ],
            timeout=timeout,
        )

    def remove_user(self, name):
        command(["/usr/sbin/userdel", name])

    def remove_group(self, name):
        command(["/usr/sbin/groupdel", name])


class OwnedIdentity:
    def __init__(
        self, session_id, uid, gid, *, seconds=240, backend=None, clock=time.monotonic
    ):
        validate(session_id, uid, gid)
        if type(seconds) is not int or not 1 <= seconds <= 240:
            raise IdentityRefused("identity.window")
        self.session_id, self.uid, self.gid = session_id, uid, gid
        self.name = "l3b8-" + session_id[:12]
        self.backend = backend or SystemBackend()
        self.root = self.backend.root
        self.directory = self.root / session_id
        self.seconds, self.clock = seconds, clock

    def remaining(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise IdentityRefused("identity.deadline")
        return remaining

    @safe
    def create(self):
        if os.geteuid() != 0:
            raise IdentityRefused("identity.root")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        trusted_directory(self.root, private=True)
        absent(self.name, self.uid, self.gid)
        no_uid_processes(self.uid)
        self.directory.mkdir(mode=0o700)
        lock = os.open(
            self.directory / "lock",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            record = {
                "session_id": self.session_id,
                "user": self.name,
                "uid": self.uid,
                "gid": self.gid,
                "group_attempted": False,
                "group_created": False,
                "user_attempted": False,
                "user_created": False,
            }
            write_record(self.directory, record)
            self.deadline = self.clock() + self.seconds
            # Timer may commit and lose its response. Journal persists and the
            # deterministic unit remains the independent cleanup owner.
            self.backend.arm(self.session_id, math.floor(self.remaining()))
            absent(self.name, self.uid, self.gid)
            record["group_attempted"] = True
            write_record(self.directory, record)
            self.backend.create_group(
                self.name, self.gid, timeout=min(10, self.remaining())
            )
            if not exact_group(record):
                raise IdentityRefused("identity.group_unconfirmed")
            record["group_created"] = True
            write_record(self.directory, record)
            # Verify the user namespace again after the group mutation.
            if (
                lookup(pwd.getpwnam, self.name) is not None
                or lookup(pwd.getpwuid, self.uid) is not None
            ):
                raise IdentityRefused("identity.not_absent")
            no_uid_processes(self.uid)
            record["user_attempted"] = True
            write_record(self.directory, record)
            self.backend.create_user(
                self.name, self.uid, self.gid, timeout=min(10, self.remaining())
            )
            if not exact_user(record) or not exact_group(record):
                raise IdentityRefused("identity.user_unconfirmed")
            record["user_created"] = True
            write_record(self.directory, record)
            self.remaining()
            return {"user": self.name, "uid": self.uid, "gid": self.gid}
        finally:
            os.close(lock)

    @safe
    def cleanup(self):
        cleanup_session(self.session_id, backend=self.backend)


@safe
def cleanup_session(session_id, *, backend=None):
    validate(session_id, 1, 1)
    backend = backend or SystemBackend()
    if os.geteuid() != 0:
        raise IdentityRefused("identity.root")
    root = backend.root
    trusted_directory(root, private=True)
    directory = root / session_id
    if not directory.exists():
        return
    try:
        trusted_directory(directory, private=True)
        lock = os.open(directory / "lock", os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        if not directory.exists():
            return
        raise
    try:
        s = os.fstat(lock)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or s.st_nlink != 1
            or stat.S_IMODE(s.st_mode) != 0o600
        ):
            raise IdentityRefused("identity.files")
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not directory.exists():
            return
        record = read_record(directory)
        user_exists, group_exists = exact_user(record), exact_group(record)
        if (user_exists and not record["user_created"]) or (
            group_exists and not record["group_created"]
        ):
            raise IdentityRefused("identity.uncertain")
        with backend.operation_lock():
            backend.verify_closure(record)
            if exact_user(record) != user_exists or exact_group(record) != group_exists:
                raise IdentityRefused("identity.drift")
            no_uid_processes(record["uid"])
            if user_exists:
                backend.remove_user(record["user"])
                if exact_user(record):
                    raise IdentityRefused("identity.user_cleanup")
            if group_exists and exact_group(record):
                # userdel may itself remove this exact empty matching group under
                # USERGROUPS_ENAB. Absence is already the desired verified state.
                # Recheck foreign primary/supplementary members immediately before
                # the exact group deletion; no group is removed by name alone.
                if not exact_group(record):
                    raise IdentityRefused("identity.drift")
                backend.remove_group(record["user"])
                if exact_group(record):
                    raise IdentityRefused("identity.group_cleanup")
            if exact_user(record) or exact_group(record):
                raise IdentityRefused("identity.cleanup")
            no_uid_processes(record["uid"])
            backend.disarm(session_id)
            shutil.rmtree(directory)

    finally:
        os.close(lock)


def main():
    try:
        if len(sys.argv) != 3 or sys.argv[1] != "cleanup":
            raise IdentityRefused("identity.operation")
        cleanup_session(sys.argv[2])
        return 0
    except BaseException:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
