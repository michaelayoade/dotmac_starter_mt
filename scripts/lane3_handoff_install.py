"""Offline, root-only Lane 3 installation and fixed management dispatcher.

The install transaction is also a refusal barrier. Configuration is never
consumed across a partially committed installation. Supplier bytes remain the
original launcher pin; installing this code grants no per-run approval.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BUNDLE_SCHEMA = "dotmac.lane3.handoff-install-bundle.v1"
INPUT_SCHEMA = "dotmac.lane3.handoff-install-input.v1"
RECORD_SCHEMA = "dotmac.lane3.handoff-installed.v1"
TRANSACTION_SCHEMA = "dotmac.lane3.handoff-install-transaction.v1"
UNINSTALL_SCHEMA = "dotmac.lane3.handoff-uninstall-intent.v1"
SUPPLIER_COMMIT = "f867f20a2d17372172ca3a0d09a5eb4a72b62961"
SUPPLIER_HASHES = {
    "lane3_topology.py": (
        "418c87cf6fdbb56d8d391f9efaf1c0ca7618fff1fcc43261e9266c05268e97a6"
    ),
    "lane3_topology_source.py": (
        "83f590635439e8b5f2faa1d33f3a9ad430350b5dd976aacacba5c8cb088756db"
    ),
    "lane3_openbao_topology.py": (
        "2315721bdcaa6f581b26b355c4e65bea0fdf69e5ebfa19431ca8467ea6c3151d"
    ),
    "lane3_github_oidc.py": (
        "0472a5783aa68bea07f59191ad28a3d0f766616a2cc3b93e61bf25151c8f6135"
    ),
    "lane3_wireguard_topology.py": (
        "356fef42f4304418b91dc795dfd7d99263469de07e501d421860168a5de1b06a"
    ),
    "lane3_broker_origin.py": (
        "6b1ee5f5afa9fc5f3f2e2f748dbf3c015feee6bbdb69240df4f70ea4d98c41a9"
    ),
    "lane3_handoff_protocol.py": (
        "acddc0c5ffa3eba1fce3ac17eac3e454f362d301934a720dc40473d8a93d47cd"
    ),
    "lane3_handoff_client.py": (
        "a6123b5ed3a8ce2d2ca92ecc68d0b7086dbb4424a5a342b74ec728e52317e96d"
    ),
    "lane3_handoff_bootstrap.py": (
        "861c72440f4f42e3323adcf05feebd43daa1875db826df6594f63ebeae0808ce"
    ),
}
CONTROLLER_MODULES = (
    "lane3_broker_origin.py",
    "lane3_handoff_controller.py",
    "lane3_handoff_protocol.py",
    "lane3_handoff_resolver.py",
    "lane3_wireguard_topology.py",
    "lane3_openbao_topology.py",
    "lane3_topology_source.py",
    "lane3_topology.py",
)
RUNTIME_MODULES = (
    *CONTROLLER_MODULES,
    "lane3_handoff_coordinator.py",
    "lane3_campaign_bootstrap.py",
    "lane3_handoff_install.py",
)
# The previously installed closure is recognized only as rollback/prior
# evidence. Every new target and every new dispatcher still requires the
# complete current closure, including the bootstrap generator.
LEGACY_RUNTIME_MODULES = (
    "lane3_broker_origin.py",
    "lane3_handoff_controller.py",
    "lane3_handoff_protocol.py",
    "lane3_handoff_resolver.py",
    "lane3_wireguard_topology.py",
    "lane3_openbao_topology.py",
    "lane3_topology_source.py",
    "lane3_topology.py",
    "lane3_handoff_coordinator.py",
    "lane3_handoff_install.py",
)
MANAGEMENT_OPS = frozenset(
    {"prepare", "timing", "bootstrap", "status", "grant", "refuse", "cleanup"}
)
CONFIG_FILES = frozenset(
    {"host.json", "policy.json", "admission.json", "supplier-installation.json"}
)
UNIT_NAME = "dotmac-lane3-handoff-reconcile.service"
MAX_FILE = 1 << 20
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class InstallRefused(RuntimeError):
    """A fixed refusal label; never include input or exception text."""

    def __init__(self, label: str = "installation.refused") -> None:
        super().__init__(label)
        self.label = label


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise InstallRefused()
            out[key] = value
        return out

    def reject(_: str) -> None:
        raise InstallRefused()

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
        if type(value) is not dict:
            raise InstallRefused()
        return value
    except (ValueError, RecursionError):
        raise InstallRefused() from None


def exact(value: Any, keys: set[str] | frozenset[str]) -> None:
    if type(value) is not dict or set(value) != keys:
        raise InstallRefused()


def hex_value(value: Any, size: int = 64) -> str:
    if (
        type(value) is not str
        or re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is None
    ):
        raise InstallRefused()
    return value


def validate_manifest(
    value: dict[str, Any], *, allow_legacy_runtime: bool = False
) -> None:
    exact(value, {"schema", "runtime_commit", "supplier_commit", "runtime", "supplier"})
    if value["schema"] != BUNDLE_SCHEMA or value["supplier_commit"] != SUPPLIER_COMMIT:
        raise InstallRefused()
    hex_value(value["runtime_commit"], 40)
    runtime = value["runtime"]
    if not (
        allow_legacy_runtime
        and type(runtime) is dict
        and set(runtime) == set(LEGACY_RUNTIME_MODULES)
    ):
        exact(runtime, set(RUNTIME_MODULES))
    for checksum in value["runtime"].values():
        hex_value(checksum)
    if value["supplier"] != SUPPLIER_HASHES:
        raise InstallRefused()


def validate_supplier_installation(value: dict[str, Any], admission: bytes) -> None:
    exact(
        value,
        {"schema", "starter_commit", "admission_digest", "supplier_digest", "modules"},
    )
    if (
        value["schema"] != "dotmac.lane3.supplier-installation.v1"
        or value["starter_commit"] != SUPPLIER_COMMIT
        or value["modules"] != SUPPLIER_HASHES
        or value["supplier_digest"] != digest(canonical(SUPPLIER_HASHES))
        or value["admission_digest"] != digest(admission)
    ):
        raise InstallRefused()


def management_operation(args: list[str]) -> str:
    if len(args) != 1 or args[0] not in MANAGEMENT_OPS:
        raise InstallRefused("operation.invalid")
    return args[0]


@dataclass(frozen=True)
class Paths:
    prefix: Path = Path("/")

    def at(self, value: str) -> Path:
        return self.prefix / value.lstrip("/")

    @property
    def config(self) -> Path:
        return self.at("/etc/dotmac-lane3-handoff")

    @property
    def state(self) -> Path:
        return self.at("/var/lib/dotmac-lane3-handoff")

    @property
    def runtime(self) -> Path:
        return self.at("/opt/dotmac-lane3-handoff")

    @property
    def unit(self) -> Path:
        return self.at("/etc/systemd/system/" + UNIT_NAME)

    @property
    def ready(self) -> Path:
        return self.at("/run/dotmac-lane3-handoff-install-ready.json")

    @property
    def record(self) -> Path:
        return self.state / "installation.json"

    @property
    def transaction(self) -> Path:
        return self.state / "install-transaction.json"

    @property
    def intent(self) -> Path:
        return self.state / "uninstall-intent.json"

    @property
    def wants(self) -> Path:
        return self.unit.parent / "multi-user.target.wants" / UNIT_NAME

    @property
    def requires(self) -> Path:
        return self.unit.parent / "multi-user.target.requires" / UNIT_NAME

    @property
    def stage(self) -> Path:
        return self.config.with_name(".dotmac-lane3-handoff.stage")

    @property
    def backup(self) -> Path:
        return self.config.with_name(".dotmac-lane3-handoff.backup")


def directory(path: Path, *, mode: int | None = None) -> int:
    """Descriptor-walk all components, rejecting symlinks and writable parents."""
    if not path.is_absolute() or ".." in path.parts:
        raise InstallRefused("installation.path")
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise InstallRefused("installation.path")
        if mode is not None and (
            stat.S_IMODE(os.fstat(fd).st_mode) != mode or os.fstat(fd).st_gid != 0
        ):
            raise InstallRefused("installation.path")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_file(path: Path, mode: int) -> bytes:
    parent = directory(path.parent)
    try:
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
            or not 0 < before.st_size <= MAX_FILE
        ):
            raise InstallRefused("installation.path")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_FILE + 1)
        after = os.fstat(fd)
        if len(raw) != before.st_size or (before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise InstallRefused("installation.path")
        return raw
    finally:
        os.close(fd)


def exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def make_directory(path: Path, mode: int) -> None:
    parent = directory(path.parent)
    try:
        os.mkdir(path.name, mode, dir_fd=parent)
        child = os.open(path.name, DIR_FLAGS, dir_fd=parent)
        try:
            os.fchmod(child, mode)
            os.fchown(child, 0, 0)
            os.fsync(child)
        finally:
            os.close(child)
        os.fsync(parent)
    finally:
        os.close(parent)


def write_file(path: Path, raw: bytes, mode: int) -> None:
    parent = directory(path.parent)
    temp = path.name + ".install-tmp"
    try:
        fd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            mode,
            dir_fd=parent,
        )
        try:
            os.fchmod(fd, mode)
            os.fchown(fd, 0, 0)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def cleanup_temp(path: Path, mode: int) -> None:
    temp = path.with_name(path.name + ".install-tmp")
    if not exists(temp):
        return
    parent = directory(temp.parent)
    try:
        fd = os.open(
            temp.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_gid != 0
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != mode
                or info.st_size > MAX_FILE
            ):
                raise InstallRefused("installation.drift")
        finally:
            os.close(fd)
        os.unlink(temp.name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def rename(source: Path, target: Path) -> None:
    if source.parent != target.parent or exists(target):
        raise InstallRefused("installation.drift")
    fd = directory(source.parent)
    try:
        os.rename(source.name, target.name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)


def unlink(path: Path) -> None:
    fd = directory(path.parent)
    try:
        os.unlink(path.name, dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)


def check_tree(
    path: Path,
    files: dict[str, str],
    mode: int,
    dir_mode: int,
    *,
    opaque: frozenset[str] = frozenset(),
) -> None:
    fd = directory(path, mode=dir_mode)
    try:
        expected = {name.split("/")[0] for name in files}
        if set(os.listdir(fd)) - opaque != expected:
            raise InstallRefused("installation.drift")
    finally:
        os.close(fd)
    groups: dict[str, dict[str, str]] = {}
    for name, checksum in files.items():
        if "/" in name:
            head, rest = name.split("/", 1)
            groups.setdefault(head, {})[rest] = checksum
        elif digest(read_file(path / name, mode)) != checksum:
            raise InstallRefused("installation.drift")
    for name, children in groups.items():
        check_tree(path / name, children, mode, dir_mode)


def remove_tree(path: Path, files: dict[str, str], mode: int, dir_mode: int) -> None:
    """Remove only an exactly measured owned tree; never recursively sweep."""
    check_tree(path, files, mode, dir_mode)
    for name in files:
        unlink(path / name)
    for child in sorted({name.split("/")[0] for name in files if "/" in name}):
        (path / child).rmdir()
    path.rmdir()
    fd = directory(path.parent)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def unit_bytes(bundle_digest: str, baseline_unit: str) -> bytes:
    hex_value(bundle_digest)
    if (
        type(baseline_unit) is not str
        or re.fullmatch(r"[A-Za-z0-9_-]+\.service", baseline_unit) is None
        or baseline_unit == UNIT_NAME
    ):
        raise InstallRefused()
    return (
        "[Unit]\nDescription=Reconcile Lane 3 handoff before management admission\n"
        f"Requires={baseline_unit}\nAfter=local-fs.target {baseline_unit}\n"
        "Before=multi-user.target\n"
        "[Service]\nType=oneshot\nRemainAfterExit=yes\nUser=root\nGroup=root\nUMask=0077\n"
        "ExecStart=/usr/bin/python3 -I -B /opt/dotmac-lane3-handoff/"
        f"{bundle_digest}/lane3_handoff_install.py boot-reconcile\n"
        "TimeoutStartSec=45\n[Install]\nWantedBy=multi-user.target\n"
    ).encode()


def readiness(record: dict[str, Any], boot: str) -> dict[str, str]:
    return {
        "schema": "dotmac.lane3.handoff-install-ready.v1",
        "boot_id": boot,
        "bundle_digest": record["bundle_digest"],
        "config_digest": record["config_digest"],
    }


def valid_readiness(receipt: dict[str, Any], record: dict[str, Any], boot: str) -> bool:
    return receipt == readiness(record, boot)


class System:
    def run(self, argv: list[str]) -> str:
        result = subprocess.run(
            argv, check=False, capture_output=True, env=ENV, timeout=60
        )
        if result.returncode or len(result.stdout) > MAX_FILE:
            raise InstallRefused("installation.system")
        return result.stdout.decode("utf-8", errors="strict").strip()

    def checkpoint(self, _: str) -> None:
        pass

    def boot_id(self) -> str:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


class Installer:
    def __init__(self, paths: Paths | None = None, system: Any = None) -> None:
        self.paths = paths or Paths()
        self.system = system or System()
        self.lock_fd: int | None = None

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        if self.lock_fd is not None:
            yield
            return
        if not exists(self.paths.state):
            make_directory(self.paths.state, 0o700)
        dfd = directory(self.paths.state, mode=0o700)
        try:
            fd = os.open(
                "lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dfd,
            )
        finally:
            os.close(dfd)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_gid != 0
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise InstallRefused("installation.path")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise InstallRefused("lock.busy") from None
            self.lock_fd = fd
            yield
        finally:
            self.lock_fd = None
            os.close(fd)

    def no_lease(self) -> None:
        if exists(self.paths.state / "journal.json"):
            raise InstallRefused("installation.lease")

    def no_transaction(self) -> None:
        if exists(self.paths.transaction):
            raise InstallRefused("installation.pending")

    def no_uninstall(self) -> None:
        if exists(self.paths.intent):
            raise InstallRefused("installation.pending")

    def record(self, *, allow_legacy_runtime: bool = False) -> dict[str, Any]:
        value = strict_json(read_file(self.paths.record, 0o600))
        self.validate_record_value(value, allow_legacy_runtime=allow_legacy_runtime)
        return value

    def validate_input(self, value: dict[str, Any]) -> None:
        exact(value, {"schema", "baseline_unit", "admission", "files"})
        if value["schema"] != INPUT_SCHEMA:
            raise InstallRefused()
        unit_bytes("0" * 64, value["baseline_unit"])
        files = value["files"]
        if type(files) is not dict or set(files) not in (
            CONFIG_FILES,
            CONFIG_FILES | {"coordinator.json"},
        ):
            raise InstallRefused()
        for checksum in files.values():
            hex_value(checksum)
        admission = value["admission"]
        exact(admission, {"repository", "path", "commit", "sha256"})
        if (
            type(admission["repository"]) is not str
            or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", admission["repository"])
            is None
        ):
            raise InstallRefused()
        if (
            type(admission["path"]) is not str
            or re.fullmatch(
                r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.json", admission["path"]
            )
            is None
            or ".." in admission["path"].split("/")
        ):
            raise InstallRefused()
        hex_value(admission["commit"], 40)
        if admission["sha256"] != files["admission.json"]:
            raise InstallRefused()

    def verify_record(
        self, record: dict[str, Any], *, allow_approval: bool = False
    ) -> None:
        runtime = self.paths.runtime / record["bundle_digest"]
        files = dict(
            record["manifest"]["runtime"], **{"bundle.json": record["bundle_digest"]}
        )
        check_tree(runtime, files, 0o444, 0o755)
        check_tree(
            self.paths.config,
            record["config_files"],
            0o600,
            0o700,
            opaque=frozenset({"approval.json"}) if allow_approval else frozenset(),
        )
        if digest(read_file(self.paths.unit, 0o644)) != record["unit_sha256"]:
            raise InstallRefused("installation.drift")

    def verify(self) -> dict[str, Any]:
        with self.locked():
            self.no_transaction()
            self.no_uninstall()
            record = self.record()
            self.verify_record(record, allow_approval=True)
            return record

    def _controller(self, record: dict[str, Any]) -> Any:
        runtime = self.paths.runtime / record["bundle_digest"]
        # No supplier import ever occurs here. Root-staged runtime was measured
        # in full before any module executes, with an isolated interpreter.
        for name, module in tuple(sys.modules.items()):
            if name.startswith("lane3_") and name != "lane3_handoff_install":
                filename = getattr(module, "__file__", None)
                if filename is None or Path(filename) != runtime / (name + ".py"):
                    raise InstallRefused("installation.import")
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(runtime))
        hc = importlib.import_module("lane3_handoff_controller")
        controller = hc.Controller(
            paths=hc.Paths(
                run_root=self.paths.at("/run/dotmac-lane3-handoff"),
                state_root=self.paths.state,
                config_root=self.paths.config,
                work_root=self.paths.at("/run/dotmac-lane3-handoff-work"),
            ),
            module_dir=runtime,
        )
        controller._lock_fd = self.lock_fd
        return controller

    def validate_configuration(
        self, record: dict[str, Any], config: dict[str, bytes]
    ) -> None:
        controller = self._controller(record)
        hc = sys.modules["lane3_handoff_controller"]
        host = hc.parse_host_config(strict_json(config["host.json"]))
        policy = hc.parse_policy(strict_json(config["policy.json"]))
        hashes = {k: record["manifest"]["runtime"][k] for k in CONTROLLER_MODULES}
        if host.policy_digest != policy.digest or host.controller_digest != digest(
            canonical(hashes)
        ):
            raise InstallRefused()
        strict_json(config["admission.json"])
        validate_supplier_installation(
            strict_json(config["supplier-installation.json"]), config["admission.json"]
        )
        if "coordinator.json" in config:
            coordinator = importlib.import_module("lane3_handoff_coordinator")
            cfg = coordinator.parse_config(strict_json(config["coordinator.json"]))
            if (
                cfg.starter_commit != SUPPLIER_COMMIT
                or cfg.supplier_modules
                != {"scripts/" + k: v for k, v in SUPPLIER_HASHES.items()}
                or cfg.admission != record["input"]["admission"]
                or cfg.controller_path
                != str(
                    self.paths.runtime
                    / record["bundle_digest"]
                    / "lane3_handoff_install.py"
                )
            ):
                raise InstallRefused()
        del controller

    def install(
        self, bundle: Path, bundle_digest: str, config_input: Path, config_digest: str
    ) -> None:
        hex_value(bundle_digest)
        hex_value(config_digest)
        manifest_raw = read_file(bundle / "bundle.json", 0o600)
        input_raw = read_file(config_input / "installation-input.json", 0o600)
        manifest, inputs = strict_json(manifest_raw), strict_json(input_raw)
        validate_manifest(manifest)
        self.validate_input(inputs)
        # Canonical encodings make external pins identical to installed records.
        if (
            manifest_raw != canonical(manifest)
            or input_raw != canonical(inputs)
            or digest(manifest_raw) != bundle_digest
            or digest(input_raw) != config_digest
        ):
            raise InstallRefused()
        check_tree(
            bundle,
            {
                "bundle.json": bundle_digest,
                **{"runtime/" + k: v for k, v in manifest["runtime"].items()},
                **{"supplier/" + k: v for k, v in SUPPLIER_HASHES.items()},
            },
            0o600,
            0o700,
        )
        check_tree(
            config_input,
            {"installation-input.json": config_digest, **inputs["files"]},
            0o600,
            0o700,
        )
        config = {
            name: read_file(config_input / name, 0o600) for name in inputs["files"]
        }
        record = {
            "schema": RECORD_SCHEMA,
            "bundle_digest": bundle_digest,
            "config_digest": config_digest,
            "manifest": manifest,
            "input": inputs,
            "config_files": {
                **inputs["files"],
                **{"supplier/" + k: v for k, v in SUPPLIER_HASHES.items()},
            },
            "unit_sha256": digest(unit_bytes(bundle_digest, inputs["baseline_unit"])),
        }
        with self.locked():
            self.no_transaction()
            self.no_uninstall()
            self.no_lease()
            cleanup_temp(self.paths.transaction, 0o600)
            prior = (
                self.record(allow_legacy_runtime=True)
                if exists(self.paths.record)
                else None
            )
            if prior is not None:
                self.verify_record(prior)
            elif exists(self.paths.config) or exists(self.paths.unit):
                raise InstallRefused("installation.unowned")
            if exists(self.paths.stage) or exists(self.paths.backup):
                raise InstallRefused("installation.drift")
            self.refuse_requires_link()
            self.remove_wants_link(remove=False)
            if not exists(self.paths.runtime):
                make_directory(self.paths.runtime, 0o755)
            runtime = self.paths.runtime / bundle_digest
            runtime_files = {**manifest["runtime"], "bundle.json": bundle_digest}
            runtime_new = not exists(runtime)
            transaction = {
                "schema": TRANSACTION_SCHEMA,
                "phase": "committing",
                "prior": prior,
                "target": record,
                "runtime_new": runtime_new,
            }
            write_file(self.paths.transaction, canonical(transaction), 0o600)
            self.system.checkpoint("intent")
            if runtime_new:
                make_directory(runtime, 0o755)
                for name in manifest["runtime"]:
                    write_file(
                        runtime / name,
                        read_file(bundle / "runtime" / name, 0o600),
                        0o444,
                    )
                write_file(runtime / "bundle.json", manifest_raw, 0o444)
            check_tree(runtime, runtime_files, 0o444, 0o755)
            self.validate_configuration(record, config)
            if exists(self.paths.ready):
                read_file(self.paths.ready, 0o600)
                unlink(self.paths.ready)
            make_directory(self.paths.stage, 0o700)
            make_directory(self.paths.stage / "supplier", 0o700)
            for name, raw in config.items():
                write_file(self.paths.stage / name, raw, 0o600)
            for name in SUPPLIER_HASHES:
                write_file(
                    self.paths.stage / "supplier" / name,
                    read_file(bundle / "supplier" / name, 0o600),
                    0o600,
                )
            check_tree(self.paths.stage, record["config_files"], 0o600, 0o700)
            self.system.checkpoint("staged")
            if prior is not None:
                rename(self.paths.config, self.paths.backup)
            self.system.checkpoint("backup")
            rename(self.paths.stage, self.paths.config)
            self.system.checkpoint("activated")
            write_file(
                self.paths.unit,
                unit_bytes(bundle_digest, inputs["baseline_unit"]),
                0o644,
            )
            write_file(self.paths.record, canonical(record), 0o600)
            transaction["phase"] = "awaiting_reconcile"
            write_file(self.paths.transaction, canonical(transaction), 0o600)
            self.system.checkpoint("awaiting_reconcile")
        # Never wait on a service which needs the same controller lock.
        self.system.run(["/usr/bin/systemctl", "daemon-reload"])
        self.system.run(["/usr/bin/systemctl", "enable", UNIT_NAME])
        self.system.run(["/usr/bin/systemctl", "restart", UNIT_NAME])
        with self.locked():
            self.no_lease()
            if self.transaction() != transaction:
                raise InstallRefused("installation.drift")
            self.verify_record(record)
            self.require_ready(record)
            transaction["phase"] = "committed"
            write_file(self.paths.transaction, canonical(transaction), 0o600)
            self.system.checkpoint("committed")
            self.finish_committed(transaction)
            self.system.checkpoint("complete")

    def transaction(self) -> dict[str, Any]:
        value = strict_json(read_file(self.paths.transaction, 0o600))
        exact(value, {"schema", "phase", "prior", "target", "runtime_new"})
        if value["schema"] != TRANSACTION_SCHEMA or value["phase"] not in {
            "committing",
            "awaiting_reconcile",
            "committed",
        }:
            raise InstallRefused()
        if type(value["runtime_new"]) is not bool or value["target"] is None:
            raise InstallRefused()
        # Records carry only reviewed file names and checksums. Validate by the
        # same parser without trusting any path from a transaction document.
        if value["prior"] is not None:
            self.validate_record_value(value["prior"], allow_legacy_runtime=True)
        self.validate_record_value(value["target"])
        return value

    def validate_record_value(
        self, value: dict[str, Any], *, allow_legacy_runtime: bool = False
    ) -> None:
        exact(
            value,
            {
                "schema",
                "bundle_digest",
                "config_digest",
                "manifest",
                "input",
                "config_files",
                "unit_sha256",
            },
        )
        validate_manifest(value["manifest"], allow_legacy_runtime=allow_legacy_runtime)
        self.validate_input(value["input"])
        expected = {
            **value["input"]["files"],
            **{"supplier/" + k: v for k, v in SUPPLIER_HASHES.items()},
        }
        if (
            value["schema"] != RECORD_SCHEMA
            or value["config_files"] != expected
            or digest(canonical(value["manifest"])) != value["bundle_digest"]
            or digest(canonical(value["input"])) != value["config_digest"]
            or value["unit_sha256"]
            != digest(
                unit_bytes(value["bundle_digest"], value["input"]["baseline_unit"])
            )
        ):
            raise InstallRefused()

    def entrypoint(self, record: dict[str, Any]) -> None:
        expected = (
            self.paths.runtime / record["bundle_digest"] / "lane3_handoff_install.py"
        )
        if (
            Path(__file__).absolute() != expected
            or digest(read_file(expected, 0o444))
            != record["manifest"]["runtime"][expected.name]
        ):
            raise InstallRefused("installation.entrypoint")

    def finish_committed(self, pending: dict[str, Any]) -> None:
        prior = pending["prior"]
        if exists(self.paths.backup):
            if prior is None:
                raise InstallRefused("installation.drift")
            self.remove_partial_tree(
                self.paths.backup, prior["config_files"], 0o600, 0o700
            )
        unlink(self.paths.transaction)

    def require_ready(self, record: dict[str, Any]) -> None:
        receipt = strict_json(read_file(self.paths.ready, 0o600))
        if (
            not valid_readiness(receipt, record, self.system.boot_id())
            or self.system.run(["/usr/bin/systemctl", "is-active", UNIT_NAME])
            != "active"
        ):
            raise InstallRefused("installation.not_ready")

    def boot_reconcile(self) -> dict[str, Any]:
        with self.locked():
            self.no_uninstall()
            record = self.record()
            if exists(self.paths.transaction):
                pending = self.transaction()
                if (
                    pending["phase"] not in {"awaiting_reconcile", "committed"}
                    or pending["target"] != record
                ):
                    raise InstallRefused("installation.pending")
            self.verify_record(record, allow_approval=True)
            self.entrypoint(record)
            cleanup_temp(self.paths.ready, 0o600)
            if exists(self.paths.ready):
                read_file(self.paths.ready, 0o600)
                unlink(self.paths.ready)
            result = self._controller(record).reconcile()
            write_file(
                self.paths.ready,
                canonical(readiness(record, self.system.boot_id())),
                0o600,
            )
            return result

    def dispatch(self, op: str, raw: bytes) -> dict[str, Any]:
        management_operation([op])
        if len(raw) > MAX_FILE:
            raise InstallRefused("operation.invalid")
        with self.locked():
            self.no_transaction()
            self.no_uninstall()
            record = self.record()
            self.verify_record(record, allow_approval=True)
            self.entrypoint(record)
            if op == "prepare":
                self.require_ready(record)
            request = strict_json(raw)
            if request.get("op") != op:
                raise InstallRefused("operation.invalid")
            controller = self._controller(record)
            return getattr(controller, op)(request)

    def recover(self) -> str:
        """Explicit rollback of interrupted install; never clean up a lease."""
        with self.locked():
            self.no_lease()
            self.no_uninstall()
            pending = self.transaction()
            # Unknown enablement links refuse before any recovery mutation.
            self.refuse_requires_link()
            self.remove_wants_link(remove=False)
            prior, target = pending["prior"], pending["target"]
            if pending["phase"] == "committed":
                if self.record() != target:
                    raise InstallRefused("installation.drift")
                self.verify_record(target)
                self.finish_committed(pending)
                return "COMMIT_FINISHED"
            for path, mode in (
                (self.paths.transaction, 0o600),
                (self.paths.record, 0o600),
                (self.paths.unit, 0o644),
                (self.paths.ready, 0o600),
            ):
                cleanup_temp(path, mode)
            # A partial stage is allowed only as a subset of exact target bytes.
            if exists(self.paths.stage):
                self.remove_partial_stage(target)
            if exists(self.paths.config):
                if prior is not None and not exists(self.paths.backup):
                    check_tree(self.paths.config, prior["config_files"], 0o600, 0o700)
                else:
                    self.remove_partial_tree(
                        self.paths.config, target["config_files"], 0o600, 0o700
                    )
            if exists(self.paths.backup):
                if prior is None:
                    raise InstallRefused("installation.drift")
                check_tree(self.paths.backup, prior["config_files"], 0o600, 0o700)
                rename(self.paths.backup, self.paths.config)
            if exists(self.paths.unit):
                checksum = digest(read_file(self.paths.unit, 0o644))
                if checksum not in {
                    target["unit_sha256"],
                    prior["unit_sha256"] if prior else None,
                }:
                    raise InstallRefused("installation.drift")
            if exists(self.paths.record):
                current = self.record(allow_legacy_runtime=True)
                if current not in (prior, target):
                    raise InstallRefused("installation.drift")
            if exists(self.paths.ready):
                read_file(self.paths.ready, 0o600)
                unlink(self.paths.ready)
            if prior is not None:
                write_file(
                    self.paths.unit,
                    unit_bytes(prior["bundle_digest"], prior["input"]["baseline_unit"]),
                    0o644,
                )
                write_file(self.paths.record, canonical(prior), 0o600)
                self.verify_record(prior)
            else:
                self.retire_unit()
                for path in (self.paths.unit, self.paths.record):
                    if exists(path):
                        unlink(path)
            # Keep barrier across daemon reload; no service needs this lock.
            self.system.run(["/usr/bin/systemctl", "daemon-reload"])
            runtime = self.paths.runtime / target["bundle_digest"]
            if pending["runtime_new"] and exists(runtime):
                info = runtime.lstat()
                if (
                    stat.S_ISDIR(info.st_mode)
                    and info.st_uid == 0
                    and info.st_gid == 0
                    and stat.S_IMODE(info.st_mode) == 0o700
                ):
                    fd = directory(runtime, mode=0o700)
                    try:
                        os.fchmod(fd, 0o755)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                self.remove_partial_tree(
                    runtime,
                    {
                        **target["manifest"]["runtime"],
                        "bundle.json": target["bundle_digest"],
                    },
                    0o444,
                    0o755,
                )
            unlink(self.paths.transaction)
        # Previous authority needs fresh reconciliation before any prepare.
        if prior is not None:
            self.system.run(["/usr/bin/systemctl", "restart", UNIT_NAME])
        return "ROLLED_BACK"

    def retire_unit(self) -> None:
        self.refuse_requires_link()
        self.remove_wants_link()
        state = self.system.run(
            ["/usr/bin/systemctl", "show", "--property=LoadState", "--value", UNIT_NAME]
        )
        if state == "loaded":
            self.system.run(["/usr/bin/systemctl", "stop", UNIT_NAME])
        elif state != "not-found":
            raise InstallRefused("installation.system")

    def refuse_requires_link(self) -> None:
        """A leftover hard-requirement link is never ours to interpret."""
        if exists(self.paths.requires):
            raise InstallRefused("installation.drift")

    def remove_wants_link(self, *, remove: bool = True) -> None:
        link = self.paths.wants
        if not exists(link):
            return
        fd = directory(link.parent)
        try:
            info = os.stat(link.name, dir_fd=fd, follow_symlinks=False)
            target = os.readlink(link.name, dir_fd=fd)
            if (
                not stat.S_ISLNK(info.st_mode)
                or info.st_uid != 0
                or target not in {"../" + UNIT_NAME, str(self.paths.unit)}
            ):
                raise InstallRefused("installation.drift")
            if remove:
                os.unlink(link.name, dir_fd=fd)
                os.fsync(fd)
        finally:
            os.close(fd)

    def uninstall(self) -> str:
        """Remove a completed installation; an intent makes reruns finish it."""
        with self.locked():
            self.no_lease()
            self.no_transaction()
            cleanup_temp(self.paths.intent, 0o600)
            if exists(self.paths.intent):
                record = self.uninstall_intent()
            elif not exists(self.paths.record):
                self.uninstall_readback()
                return "ABSENT"
            else:
                record = self.record()
                self.verify_record(record)
                self.refuse_requires_link()
                if exists(self.paths.wants):
                    self.remove_wants_link(remove=False)
                if exists(self.paths.ready):
                    read_file(self.paths.ready, 0o600)
                if exists(self.paths.stage) or exists(self.paths.backup):
                    raise InstallRefused("installation.drift")
                write_file(
                    self.paths.intent,
                    canonical({"schema": UNINSTALL_SCHEMA, "record": record}),
                    0o600,
                )
            self.system.checkpoint("uninstall_intent")
            self.refuse_requires_link()
            if exists(self.paths.unit):
                cleanup_temp(self.paths.unit, 0o644)
                if digest(read_file(self.paths.unit, 0o644)) != record["unit_sha256"]:
                    raise InstallRefused("installation.drift")
                self.remove_wants_link(remove=False)
                self.stop_reconcile_unit()
                self.system.checkpoint("uninstall_stopped")
                self.remove_wants_link()
                unlink(self.paths.unit)
            else:
                self.remove_wants_link()
            self.system.checkpoint("uninstall_unit_removed")
            self.system.run(["/usr/bin/systemctl", "daemon-reload"])
            self.system.checkpoint("uninstall_reloaded")
            runtime = self.paths.runtime / record["bundle_digest"]
            if exists(runtime):
                self.remove_partial_tree(
                    runtime,
                    {
                        **record["manifest"]["runtime"],
                        "bundle.json": record["bundle_digest"],
                    },
                    0o444,
                    0o755,
                )
            self.remove_empty_runtime_parent()
            self.system.checkpoint("uninstall_runtime_removed")
            if exists(self.paths.config):
                self.remove_partial_tree(
                    self.paths.config, record["config_files"], 0o600, 0o700
                )
            self.system.checkpoint("uninstall_config_removed")
            cleanup_temp(self.paths.ready, 0o600)
            if exists(self.paths.ready):
                read_file(self.paths.ready, 0o600)
                unlink(self.paths.ready)
            self.system.checkpoint("uninstall_ready_removed")
            cleanup_temp(self.paths.record, 0o600)
            if exists(self.paths.record):
                if self.record() != record:
                    raise InstallRefused("installation.drift")
                unlink(self.paths.record)
            self.system.checkpoint("uninstall_record_removed")
            self.uninstall_readback(record)
            unlink(self.paths.intent)
            return "UNINSTALLED"

    def uninstall_intent(self) -> dict[str, Any]:
        value = strict_json(read_file(self.paths.intent, 0o600))
        exact(value, {"schema", "record"})
        if value["schema"] != UNINSTALL_SCHEMA:
            raise InstallRefused()
        self.validate_record_value(value["record"])
        return value["record"]

    def stop_reconcile_unit(self) -> None:
        """Disable then stop the unit; positively observed absence is fine."""
        state = self.system.run(
            ["/usr/bin/systemctl", "show", "--property=LoadState", "--value", UNIT_NAME]
        )
        if state == "loaded":
            self.system.run(["/usr/bin/systemctl", "disable", UNIT_NAME])
            self.system.run(["/usr/bin/systemctl", "stop", UNIT_NAME])
        elif state != "not-found":
            raise InstallRefused("installation.system")

    def uninstall_readback(self, record: dict[str, Any] | None = None) -> None:
        paths = [
            self.paths.unit,
            self.paths.wants,
            self.paths.requires,
            self.paths.config,
            self.paths.stage,
            self.paths.backup,
            self.paths.ready,
            self.paths.record,
            self.paths.transaction,
        ]
        if record is not None:
            paths.append(self.paths.runtime / record["bundle_digest"])
        if any(exists(path) for path in paths):
            raise InstallRefused("installation.unowned")

    def remove_empty_runtime_parent(self) -> None:
        if not exists(self.paths.runtime):
            return
        fd = directory(self.paths.runtime, mode=0o755)
        try:
            empty = not os.listdir(fd)
        finally:
            os.close(fd)
        if empty:
            self.paths.runtime.rmdir()
            parent = directory(self.paths.runtime.parent)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)

    def remove_partial_stage(self, target: dict[str, Any]) -> None:
        self.remove_partial_tree(self.paths.stage, target["config_files"], 0o600, 0o700)

    def remove_partial_tree(
        self, path: Path, expected: dict[str, str], mode: int, dir_mode: int
    ) -> None:
        files: dict[str, str] = {}
        fd = directory(path, mode=dir_mode)
        try:
            names = os.listdir(fd)
        finally:
            os.close(fd)
        for key in expected:
            cleanup_temp(path / key, mode)
        names = [name for name in names if not name.endswith(".install-tmp")]
        for name in names:
            if name == "supplier":
                child = directory(path / name, mode=dir_mode)
                try:
                    for module in os.listdir(child):
                        key = "supplier/" + module
                        if key not in expected:
                            raise InstallRefused("installation.drift")
                        files[key] = expected[key]
                finally:
                    os.close(child)
            elif name in expected:
                files[name] = expected[name]
            else:
                raise InstallRefused("installation.drift")
        if "supplier" in names and not any(k.startswith("supplier/") for k in files):
            (path / "supplier").rmdir()
        remove_tree(path, files, mode, dir_mode)


def build(source: Path, revision: str, output: Path) -> str:
    """Build from exact Git objects, offline, never from dirty worktree bytes."""
    hex_value(revision, 40)
    if output.exists():
        raise InstallRefused()

    def git(*args: str) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(source), *args],
            check=True,
            capture_output=True,
            env=ENV,
            timeout=30,
        )
        return result.stdout

    if (
        git("status", "--porcelain")
        or git("rev-parse", "HEAD").decode().strip() != revision
    ):
        raise InstallRefused("installation.source")
    runtime = {
        name: git("show", revision + ":scripts/" + name) for name in RUNTIME_MODULES
    }
    supplier = {
        name: git("show", SUPPLIER_COMMIT + ":scripts/" + name)
        for name in SUPPLIER_HASHES
    }
    if {name: digest(raw) for name, raw in supplier.items()} != SUPPLIER_HASHES:
        raise InstallRefused("installation.source")
    manifest = {
        "schema": BUNDLE_SCHEMA,
        "runtime_commit": revision,
        "supplier_commit": SUPPLIER_COMMIT,
        "runtime": {name: digest(raw) for name, raw in runtime.items()},
        "supplier": dict(SUPPLIER_HASHES),
    }
    output.mkdir(mode=0o700)
    for group, values in (("runtime", runtime), ("supplier", supplier)):
        (output / group).mkdir(mode=0o700)
        for name, raw in values.items():
            path = output / group / name
            path.write_bytes(raw)
            path.chmod(0o600)
    (output / "bundle.json").write_bytes(canonical(manifest))
    (output / "bundle.json").chmod(0o600)
    return digest(canonical(manifest))


def main(argv: list[str]) -> int:
    os.umask(0o077)
    try:
        if len(argv) >= 2 and argv[1] == "build":
            parser = argparse.ArgumentParser()
            parser.add_argument("--source", type=Path, required=True)
            parser.add_argument("--revision", required=True)
            parser.add_argument("--output", type=Path, required=True)
            args = parser.parse_args(argv[2:])
            print(
                json.dumps(
                    {"bundle_digest": build(args.source, args.revision, args.output)}
                )
            )
            return 0
        if os.geteuid() != 0 or not sys.flags.isolated:
            raise InstallRefused("operation.invalid")
        installer = Installer()
        if len(argv) == 2 and argv[1] in MANAGEMENT_OPS:
            result = installer.dispatch(argv[1], sys.stdin.buffer.read(MAX_FILE + 1))
        elif argv[1:] == ["boot-reconcile"]:
            result = installer.boot_reconcile()
        elif argv[1:] == ["uninstall"]:
            result = {"status": installer.uninstall()}
        elif argv[1:] == ["recover"]:
            result = {"status": installer.recover()}
        elif argv[1:] == ["verify"]:
            installer.verify()
            result = {"status": "VERIFIED"}
        elif len(argv) >= 2 and argv[1] == "install":
            parser = argparse.ArgumentParser()
            parser.add_argument("--bundle", type=Path, required=True)
            parser.add_argument("--bundle-digest", required=True)
            parser.add_argument("--config-input", type=Path, required=True)
            parser.add_argument("--config-digest", required=True)
            args = parser.parse_args(argv[2:])
            installer.install(
                args.bundle, args.bundle_digest, args.config_input, args.config_digest
            )
            result = {"status": "INSTALLED"}
        else:
            raise InstallRefused("operation.invalid")
        print(json.dumps(result))
        return 0
    except Exception as exc:
        trusted_exceptions = [InstallRefused]
        for name, attr in (
            ("lane3_handoff_controller", "ControllerRefused"),
            ("lane3_handoff_protocol", "ProtocolRefused"),
        ):
            module = sys.modules.get(name)
            if module is not None:
                trusted_exceptions.append(getattr(module, attr))
        label = (
            exc.label
            if isinstance(exc, tuple(trusted_exceptions))
            else "installation.refused"
        )
        print(
            json.dumps(
                {
                    "protocol": "dotmac.lane3.broker-handoff.v1",
                    "status": "REFUSED",
                    "category": label,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
