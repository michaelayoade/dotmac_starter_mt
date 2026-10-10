"""Verify a root-staged supplier bundle before any supplier import.

The immutable launcher calls this loader from isolated Python. The host owns
the installation manifest and launch record; environment paths are locators,
never authority. This does not attest an uncompromised runner or grant access.
The import check is a consistency check for reviewed bytes, not a Python
sandbox: approved code can perform dynamic imports or change interpreter state.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import re
import stat
import sys
import sysconfig
import time
from pathlib import Path
from types import ModuleType
from typing import Any

MAX_FILE = 1 << 20
PROTOCOL = "dotmac.lane3.broker-handoff.v1"
SCHEMA = "dotmac.lane3.supplier-installation.v1"
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")
MODULE = re.compile(r"lane3_[a-z][a-z0-9_]*\.py")


class BootstrapRefused(RuntimeError):
    """A closed, value-free refusal label."""

    def __init__(self) -> None:
        super().__init__("bootstrap.refused")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BootstrapRefused()
        value[key] = item
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _json(raw: bytes) -> dict[str, Any]:
    def reject(_: str) -> None:
        raise BootstrapRefused()

    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=reject)
    if type(value) is not dict:
        raise BootstrapRefused()
    return value


def _directory(path: Path) -> int:
    if not path.is_absolute() or any(p in (".", "..") for p in path.parts):
        raise BootstrapRefused()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
                raise BootstrapRefused()
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd: int, name: str, mode: int, gid: int) -> bytes:
    opened = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd
    )
    try:
        info = os.fstat(opened)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode
            or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_FILE
        ):
            raise BootstrapRefused()
        with os.fdopen(opened, "rb", closefd=False) as stream:
            raw = stream.read(MAX_FILE + 1)
        after = os.fstat(opened)
        if len(raw) != info.st_size or (
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_size,
        ) != (after.st_mtime_ns, after.st_ctime_ns, after.st_size):
            raise BootstrapRefused()
        return raw
    finally:
        os.close(opened)


def _isolated() -> bool:
    return bool(sys.flags.isolated)


def load_verified_bundle(
    supplier_dir: str | Path, launch_path: str | Path
) -> dict[str, ModuleType]:
    """Verify protected bytes, launch budget and import closure, then import.

    Call once at startup under ``python -I``. A wrong local byte is refused
    before any supplier module executes, even when remote hashes still match.
    """
    try:
        return _load(Path(supplier_dir), Path(launch_path))
    except Exception:
        raise BootstrapRefused() from None


def _load(directory: Path, launch_path: Path) -> dict[str, ModuleType]:
    if not _isolated() or any(
        name.startswith("lane3_") and name != "lane3_handoff_bootstrap"
        for name in sys.modules
    ):
        raise BootstrapRefused()
    if launch_path.parent != directory.parent or launch_path.name != "launch.json":
        raise BootstrapRefused()
    dfd = _directory(directory)
    lfd = _directory(launch_path.parent)
    try:
        dinfo, linfo = os.fstat(dfd), os.fstat(lfd)
        if (
            stat.S_IMODE(dinfo.st_mode) != 0o550
            or stat.S_IMODE(linfo.st_mode) != 0o750
            or dinfo.st_gid != linfo.st_gid
        ):
            raise BootstrapRefused()
        gid = dinfo.st_gid
        manifest = _json(_read(dfd, "supplier-installation.json", 0o440, gid))
        launch = _json(_read(lfd, "launch.json", 0o640, gid))
        if (
            set(manifest)
            != {
                "schema",
                "starter_commit",
                "admission_digest",
                "supplier_digest",
                "modules",
            }
            or manifest["schema"] != SCHEMA
        ):
            raise BootstrapRefused()
        modules = manifest["modules"]
        if type(modules) is not dict or not 1 <= len(modules) <= 32:
            raise BootstrapRefused()
        for name, digest in modules.items():
            if (
                type(name) is not str
                or MODULE.fullmatch(name) is None
                or type(digest) is not str
                or HEX64.fullmatch(digest) is None
            ):
                raise BootstrapRefused()
        if (
            type(manifest["starter_commit"]) is not str
            or HEX40.fullmatch(manifest["starter_commit"]) is None
            or hashlib.sha256(_canonical(modules)).hexdigest()
            != manifest["supplier_digest"]
        ):
            raise BootstrapRefused()
        if (
            set(launch)
            != {
                "protocol",
                "lease_id",
                "boot_id",
                "launched_at_monotonic_ns",
                "expires_at_monotonic_ns",
                "admission_digest",
                "supplier_digest",
            }
            or launch["protocol"] != PROTOCOL
        ):
            raise BootstrapRefused()
        if (
            type(launch["lease_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", launch["lease_id"]) is None
            or launch["lease_id"] != directory.parent.name
            or launch["boot_id"]
            != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        ):
            raise BootstrapRefused()
        for key in ("admission_digest", "supplier_digest"):
            if (
                type(manifest[key]) is not str
                or HEX64.fullmatch(manifest[key]) is None
                or launch[key] != manifest[key]
            ):
                raise BootstrapRefused()
        started, expires = (
            launch["launched_at_monotonic_ns"],
            launch["expires_at_monotonic_ns"],
        )
        now = time.monotonic_ns()
        if (
            type(started) is not int
            or type(expires) is not int
            or not 0 < started <= now < expires
            or now - started >= 45_000_000_000
            or expires - started > 300_000_000_000
        ):
            raise BootstrapRefused()
        if set(os.listdir(dfd)) != set(modules) | {"supplier-installation.json"}:
            raise BootstrapRefused()
        for name, digest in modules.items():
            raw = _read(dfd, name, 0o440, gid)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise BootstrapRefused()
            for node in ast.walk(ast.parse(raw)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                if any(
                    item.startswith("lane3_")
                    and item.split(".")[0] + ".py" not in modules
                    for item in names
                ):
                    raise BootstrapRefused()
    finally:
        os.close(lfd)
        os.close(dfd)
    # The protected directory remains immutable to the runner. No unverified
    # supplier is already cached; bytecode and external lookup paths are off.
    sys.dont_write_bytecode = True
    now = time.monotonic_ns()
    if (
        now >= expires
        or now - started >= 45_000_000_000
        or launch["boot_id"]
        != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    ):
        raise BootstrapRefused()
    stdlib = Path(sysconfig.get_path("stdlib"))
    sys.path[:] = [str(directory), str(stdlib), str(stdlib / "lib-dynload")]
    return {
        name[:-3]: importlib.import_module(name[:-3])
        for name in sorted(modules)
        if name != "lane3_handoff_bootstrap.py"
    }


if __name__ == "__main__":
    # This is a loader API, not a proof command. Never return success merely
    # because a caller ran the file without invoking the reviewed consumer.
    raise SystemExit("bootstrap.refused")
