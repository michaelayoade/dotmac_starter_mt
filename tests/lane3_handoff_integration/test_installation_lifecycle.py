"""Hosted installer transactions and real temporary reconciliation units.

The live filesystem and SIGKILL are real. A test System redirects the fixed
production service name to uniquely owned temporary units; enable is recorded
without modifying host boot targets. The actual controller reconciles an owned
nft table in the disposable test network namespace. This is not a reboot test.
"""

from __future__ import annotations

import inspect
import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator

import lane3_handoff_controller as hc
import lane3_handoff_install as hi
import lane3_handoff_protocol as hp
import pytest
from conftest import ENABLED, SCRIPTS


def command(*argv: str, data: bytes | None = None, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed tools and owned test resources
        argv, input=data, capture_output=True, timeout=30, check=False
    )
    if check and result.returncode:
        raise AssertionError("owned installation fixture command failed")
    return result.stdout.decode().strip()


def put(path: pathlib.Path, raw: bytes, mode: int = 0o600) -> None:
    path.write_bytes(raw)
    path.chmod(mode)


def pinned_supplier() -> dict[str, bytes]:
    """Keep the admitted supplier closure independent of current runtime edits."""
    files = {}
    for name in hi.SUPPLIER_HASHES:
        result = subprocess.run(  # noqa: S603 - fixed immutable repository objects
            ["/usr/bin/git", "show", hi.SUPPLIER_COMMIT + ":scripts/" + name],
            cwd=SCRIPTS.parent,
            capture_output=True,
            timeout=30,
            check=True,
        )
        files[name] = result.stdout
    assert {name: hi.digest(raw) for name, raw in files.items()} == hi.SUPPLIER_HASHES
    return files


def snapshot(path: pathlib.Path) -> dict[str, tuple[bytes, int, int, int]]:
    """Compare bytes and authority metadata; no synthetic private value output."""
    result: dict[str, tuple[bytes, int, int, int]] = {}
    if not os.path.lexists(path):
        return result
    for item in [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]:
        info = item.lstat()
        assert not item.is_symlink()
        result[str(item.relative_to(path))] = (
            item.read_bytes() if item.is_file() else b"",
            stat.S_IMODE(info.st_mode),
            info.st_uid,
            info.st_gid,
        )
    return result


def link_snapshot(
    path: pathlib.Path,
) -> tuple[pathlib.Path, int, int, int, int, int, int]:
    info = path.lstat()
    return (
        path.readlink(),
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


class FixtureSystem(hi.System):
    def __init__(self, unit: str, crash: str | None = None) -> None:
        self.unit = unit
        self.crash = crash
        self.enabled = False

    def run(self, argv: list[str]) -> str:
        assert argv[0] == "/usr/bin/systemctl"
        if argv[1:] == ["enable", hi.UNIT_NAME]:
            self.enabled = True
            return ""
        assert (
            argv[1:] == ["daemon-reload"]
            or (
                len(argv) == 3
                and argv[1] in {"restart", "is-active", "disable", "stop"}
                and argv[2] == hi.UNIT_NAME
            )
            or argv[1:] == ["show", "--property=LoadState", "--value", hi.UNIT_NAME]
        )
        return super().run([self.unit if x == hi.UNIT_NAME else x for x in argv])

    def checkpoint(self, name: str) -> None:
        if name == self.crash:
            os.kill(os.getpid(), signal.SIGKILL)


class Installation:
    def __init__(self, root: pathlib.Path) -> None:
        suffix = uuid.uuid4().hex
        self.root = root
        self.paths = hi.Paths(prefix=root / "host")
        self.unit = f"l3h-install-{suffix}.service"
        self.baseline = f"l3h-baseline-{suffix}.service"
        self.table = "l3h_install_" + suffix
        self.system = FixtureSystem(self.unit)
        self.installer = hi.Installer(self.paths, self.system)
        self.unit_paths: list[pathlib.Path] = []
        self.table_created = False
        self.bundle = root / "bundle"
        self.bundle_digest = ""
        self.worker = root / "install-worker.py"
        self.uninstall_worker = root / "uninstall-worker.py"

    def setup(self) -> None:
        for relative in (
            "",
            "etc",
            "etc/systemd",
            "etc/systemd/system",
            "var",
            "var/lib",
            "opt",
            "run",
        ):
            (self.paths.prefix / relative).mkdir(mode=0o700)
        self.bundle.mkdir(mode=0o700)
        runtime = {name: (SCRIPTS / name).read_bytes() for name in hi.RUNTIME_MODULES}
        supplier = pinned_supplier()
        for name, files in (("runtime", runtime), ("supplier", supplier)):
            directory = self.bundle / name
            directory.mkdir(mode=0o700)
            for filename, raw in files.items():
                put(directory / filename, raw)
        manifest = {
            "schema": hi.BUNDLE_SCHEMA,
            "runtime_commit": "a" * 40,
            "supplier_commit": hi.SUPPLIER_COMMIT,
            "runtime": {name: hi.digest(raw) for name, raw in runtime.items()},
            "supplier": dict(hi.SUPPLIER_HASHES),
        }
        raw = hi.canonical(manifest)
        self.bundle_digest = hi.digest(raw)
        put(self.bundle / "bundle.json", raw)
        # Install in a fresh isolated interpreter: pytest/conftest imports must
        # never stand in for the measured modules in the installed runtime.
        put(
            self.worker,
            (
                "import json,os,pathlib,signal,sys\n"
                f"sys.path.insert(0,{str(SCRIPTS)!r})\n"
                "import lane3_handoff_install as hi\n"
                + inspect.getsource(FixtureSystem)
                + "\ntry:\n"
                " os.umask(0o077)\n"
                " crash=None if sys.argv[7]=='-' else sys.argv[7]\n"
                " system=FixtureSystem(sys.argv[6],crash)\n"
                " paths=hi.Paths(prefix=pathlib.Path(sys.argv[1]))\n"
                " hi.Installer(paths,system).install("
                "pathlib.Path(sys.argv[2]),sys.argv[3],pathlib.Path(sys.argv[4]),sys.argv[5])\n"
                "except hi.InstallRefused as exc:\n"
                " print(json.dumps({'refused':exc.label}));sys.exit(1)\n"
                "except OSError:\n"
                " print(json.dumps({'refused':'installation.refused'}));sys.exit(1)\n"
                "except Exception:\n"
                " print(json.dumps({'refused':'unexpected'}));sys.exit(2)\n"
            ).encode(),
        )
        put(
            self.uninstall_worker,
            (
                "import json,os,pathlib,signal,sys\n"
                f"sys.path.insert(0,{str(SCRIPTS)!r})\n"
                "import lane3_handoff_install as hi\n"
                + inspect.getsource(FixtureSystem)
                + "\ntry:\n"
                " os.umask(0o077)\n"
                " crash=None if sys.argv[3]=='-' else sys.argv[3]\n"
                " system=FixtureSystem(sys.argv[2],crash)\n"
                " paths=hi.Paths(prefix=pathlib.Path(sys.argv[1]))\n"
                " installer=hi.Installer(paths,system)\n"
                " print(json.dumps({'status':installer.uninstall()}))\n"
                "except hi.InstallRefused as exc:\n"
                " print(json.dumps({'refused':exc.label}));sys.exit(1)\n"
                "except OSError:\n"
                " print(json.dumps({'refused':'installation.refused'}));sys.exit(1)\n"
                "except Exception:\n"
                " print(json.dumps({'refused':'unexpected'}));sys.exit(2)\n"
            ).encode(),
        )
        rules = (
            f"table inet {self.table} {{\n"
            "  chain output {\n"
            "    type filter hook output priority 0; policy drop;\n"
            '    counter comment "installation-anchor";\n'
            "  }\n"
            "}\n"
        ).encode()
        command("nft", "--check", "-f", "-", data=rules)
        command("nft", "-f", "-", data=rules)
        self.table_created = True
        wrapper = self.root / "reconcile.py"
        installed = self.paths.runtime / self.bundle_digest
        put(
            wrapper,
            (
                "import pathlib,sys\n"
                # Importing from the measured runtime must not add __pycache__.
                "sys.dont_write_bytecode=True\n"
                f"sys.path.insert(0,{str(installed)!r})\n"
                "import lane3_handoff_install as hi\n"
                f"hi.Installer(hi.Paths(prefix=pathlib.Path({str(self.paths.prefix)!r}))).boot_reconcile()\n"
            ).encode(),
        )
        self.dispatcher = self.root / "dispatch-worker.py"
        put(
            self.dispatcher,
            (
                "import json,pathlib,sys\n"
                "sys.dont_write_bytecode=True\n"
                f"sys.path.insert(0,{str(installed)!r})\n"
                "import lane3_handoff_install as hi\n"
                "try:\n"
                f" prefix=pathlib.Path({str(self.paths.prefix)!r})\n"
                " installer=hi.Installer(hi.Paths(prefix=prefix))\n"
                " result=installer.dispatch('status',sys.stdin.buffer.read())\n"
                " print(json.dumps(result))\n"
                "except hi.InstallRefused as exc:\n"
                " print(json.dumps({'refused':exc.label}));sys.exit(1)\n"
            ).encode(),
        )
        marker = self.root / "baseline-ready"
        baseline = (
            "[Unit]\nDescription=Owned synthetic installation baseline\n"
            "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
            f"ExecStart=/usr/bin/touch {marker}\n"
        )
        reconcile = (
            f"[Unit]\nRequires={self.baseline}\nAfter={self.baseline}\n"
            "[Service]\nType=oneshot\nRemainAfterExit=yes\nTimeoutStartSec=20\n"
            f"NetworkNamespacePath=/proc/{os.getpid()}/ns/net\n"
            f"ExecStart=/usr/bin/python3 -I {wrapper}\n"
        )
        for name, content in ((self.baseline, baseline), (self.unit, reconcile)):
            path = pathlib.Path("/run/systemd/system") / name
            assert not path.exists(), "never replace an existing host unit"
            put(path, content.encode(), 0o644)
            self.unit_paths.append(path)
        command("/usr/bin/systemctl", "daemon-reload")

    def inputs(self, version: int) -> tuple[pathlib.Path, str]:
        directory = self.root / f"input-{version}"
        directory.mkdir(mode=0o700)
        policy = {
            "schema": hc.POLICY_SCHEMA,
            "version": version,
            "origins": {
                "schema": "dotmac.lane3.broker-origin-policy.v1",
                "origins": ["https://broker.example"],
            },
            "aliases": {"exact": [], "namespaces": []},
        }
        host = {
            "schema": hc.HOST_SCHEMA,
            "firewall": {
                "family": "inet",
                "table": self.table,
                "chain": "output",
                "anchor_comment": "installation-anchor",
            },
            "policy_digest": hp.digest(policy),
            "controller_digest": hc.controller_digest(SCRIPTS),
            "runner": {
                "archive": str(self.root / "synthetic-unused.tar.gz"),
                "archive_sha256": "b" * 64,
                "workspace_mb": 16,
                "memory_mb": 256,
            },
        }
        admission = hi.canonical(
            {"schema": "synthetic-independent-admission", "version": version}
        )
        installation = {
            "schema": "dotmac.lane3.supplier-installation.v1",
            "starter_commit": hi.SUPPLIER_COMMIT,
            "admission_digest": hi.digest(admission),
            "supplier_digest": hi.digest(hi.canonical(hi.SUPPLIER_HASHES)),
            "modules": dict(hi.SUPPLIER_HASHES),
        }
        files = {
            "host.json": hi.canonical(host),
            "policy.json": hi.canonical(policy),
            "admission.json": admission,
            "supplier-installation.json": hi.canonical(installation),
        }
        inputs = {
            "schema": hi.INPUT_SCHEMA,
            "baseline_unit": self.baseline,
            "admission": {
                "repository": "synthetic/fixture",
                "path": "admission.json",
                "commit": "c" * 40,
                "sha256": hi.digest(admission),
            },
            "files": {name: hi.digest(raw) for name, raw in files.items()},
        }
        for name, raw in files.items():
            put(directory / name, raw)
        raw = hi.canonical(inputs)
        put(directory / "installation-input.json", raw)
        return directory, hi.digest(raw)

    def install(
        self, inputs: tuple[pathlib.Path, str], crash: str | None = None
    ) -> None:
        child = self.start_install(inputs, crash)
        try:
            out, _ = child.communicate(timeout=45)
            if child.returncode == 1:
                raise hi.InstallRefused(json.loads(out)["refused"])
            assert child.returncode == 0, "isolated installer operation failed"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def start_install(
        self, inputs: tuple[pathlib.Path, str], crash: str | None
    ) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 - isolated owned fixture worker
            [
                sys.executable,
                "-I",
                str(self.worker),
                str(self.paths.prefix),
                str(self.bundle),
                self.bundle_digest,
                str(inputs[0]),
                inputs[1],
                self.unit,
                crash or "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def start_uninstall(self, crash: str | None) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 - isolated owned fixture worker
            [
                sys.executable,
                "-I",
                str(self.uninstall_worker),
                str(self.paths.prefix),
                self.unit,
                crash or "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def link(self, kind: str, target: str) -> pathlib.Path:
        """Plant a boot-target link under the isolated prefix, never the host."""
        directory = self.paths.unit.parent / f"multi-user.target.{kind}"
        directory.mkdir(mode=0o755, exist_ok=True)
        directory.chmod(0o755)
        link = directory / hi.UNIT_NAME
        link.symlink_to(target)
        return link

    def installed_paths(self) -> list[pathlib.Path]:
        return [
            self.paths.config,
            self.paths.record,
            self.paths.unit,
            self.paths.ready,
            self.paths.transaction,
            self.paths.intent,
            self.paths.stage,
            self.paths.backup,
            self.paths.runtime / self.bundle_digest,
        ]

    def status(self, lease: str) -> dict[str, object]:
        result = subprocess.run(  # noqa: S603 - exact installed dispatcher fixture
            [sys.executable, "-I", str(self.dispatcher)],
            input=hp.canonical_json(
                {"protocol": hp.PROTOCOL, "op": "status", "lease_id": lease}
            ),
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 1:
            raise hi.InstallRefused(json.loads(result.stdout)["refused"])
        assert result.returncode == 0, "installed status dispatcher failed"
        return json.loads(result.stdout)

    def live_snapshot(self) -> dict[str, dict[str, tuple[bytes, int, int, int]]]:
        return {
            name: snapshot(path)
            for name, path in (
                ("config", self.paths.config),
                ("record", self.paths.record),
                ("unit", self.paths.unit),
                ("ready", self.paths.ready),
                ("runtime", self.paths.runtime),
            )
        }

    def close(self) -> None:
        try:
            if self.unit_paths:
                command(
                    "/usr/bin/systemctl", "stop", self.unit, self.baseline, check=False
                )
                command(
                    "/usr/bin/systemctl",
                    "reset-failed",
                    self.unit,
                    self.baseline,
                    check=False,
                )
                for path in self.unit_paths:
                    path.unlink(missing_ok=True)
                command("/usr/bin/systemctl", "daemon-reload")
        finally:
            if self.table_created:
                command("nft", "delete", "table", "inet", self.table)


@pytest.fixture()
def installation() -> Iterator[Installation]:
    assert ENABLED, "only disposable hosted root integration may install fixtures"
    with tempfile.TemporaryDirectory(prefix="l3h-install-", dir="/run") as directory:
        value = Installation(pathlib.Path(directory))
        try:
            value.setup()
            yield value
        finally:
            value.close()


def kill_install(
    value: Installation, inputs: tuple[pathlib.Path, str], phase: str
) -> None:
    child = value.start_install(inputs, phase)
    try:
        child.communicate(timeout=45)
        assert (
            child.returncode == -signal.SIGKILL
        ), "installer must reach its SIGKILL checkpoint"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


LEGACY_RUNTIME_COMMIT = "6dc1cd68e829225acc930da4aa82a58628ff05c3"


def install_real_legacy_closure(value: Installation):
    """Install actual immutable old ten-file bytes through their old installer."""
    current = (value.bundle, value.bundle_digest, value.worker)
    legacy = value.root / "legacy-bundle"
    legacy.mkdir(mode=0o700)
    runtime = {}
    for name in hi.LEGACY_RUNTIME_MODULES:
        result = subprocess.run(  # noqa: S603 - fixed immutable repository objects
            ["/usr/bin/git", "show", LEGACY_RUNTIME_COMMIT + ":scripts/" + name],
            cwd=SCRIPTS.parent,
            capture_output=True,
            timeout=30,
            check=True,
        )
        runtime[name] = result.stdout
    for group, files in (
        ("runtime", runtime),
        ("supplier", pinned_supplier()),
    ):
        directory = legacy / group
        directory.mkdir(mode=0o700)
        for name, raw in files.items():
            put(directory / name, raw)
    manifest = {
        "schema": hi.BUNDLE_SCHEMA,
        "runtime_commit": LEGACY_RUNTIME_COMMIT,
        "supplier_commit": hi.SUPPLIER_COMMIT,
        "runtime": {name: hi.digest(raw) for name, raw in runtime.items()},
        "supplier": dict(hi.SUPPLIER_HASHES),
    }
    raw = hi.canonical(manifest)
    legacy_digest = hi.digest(raw)
    put(legacy / "bundle.json", raw)
    worker = value.root / "legacy-worker.py"
    old_worker = value.worker.read_bytes().replace(
        repr(str(SCRIPTS)).encode(), repr(str(legacy / "runtime")).encode(), 1
    )
    old_worker = old_worker.replace(
        b"import lane3_handoff_install as hi\n",
        b"sys.dont_write_bytecode=True\nimport lane3_handoff_install as hi\n",
        1,
    )
    put(worker, old_worker)
    wrapper = value.root / "reconcile.py"
    new_installed = value.paths.runtime / value.bundle_digest
    old_installed = value.paths.runtime / legacy_digest
    wrapper.write_bytes(
        wrapper.read_bytes().replace(
            repr(str(new_installed)).encode(), repr(str(old_installed)).encode()
        )
    )
    value.bundle, value.bundle_digest, value.worker = legacy, legacy_digest, worker
    inputs = value.inputs(1)
    host_path = inputs[0] / "host.json"
    host = hi.strict_json(host_path.read_bytes())
    host["controller_digest"] = hp.digest(
        {name: hi.digest(runtime[name]) for name in hi.CONTROLLER_MODULES}
    )
    put(host_path, hi.canonical(host))
    input_path = inputs[0] / "installation-input.json"
    input_doc = hi.strict_json(input_path.read_bytes())
    input_doc["files"]["host.json"] = hi.digest(host_path.read_bytes())
    put(input_path, hi.canonical(input_doc))
    before = value.live_snapshot()
    with pytest.raises(hi.InstallRefused):
        value.installer.install(
            legacy, legacy_digest, inputs[0], hi.digest(input_path.read_bytes())
        )
    assert value.live_snapshot() == before
    value.install((inputs[0], hi.digest(input_path.read_bytes())))
    prior = value.installer.record(allow_legacy_runtime=True)
    value.installer.verify_record(prior)
    assert set(prior["manifest"]["runtime"]) == set(hi.LEGACY_RUNTIME_MODULES)
    value.bundle, value.bundle_digest, value.worker = current
    return prior, old_installed, new_installed


def test_current_runtime_and_pinned_supplier_have_distinct_measured_protocols(
    installation: Installation,
):
    bundle = installation.bundle
    manifest = hi.strict_json((bundle / "bundle.json").read_bytes())
    hi.validate_manifest(manifest)
    for group in ("runtime", "supplier"):
        assert {
            name: hi.digest((bundle / group / name).read_bytes())
            for name in manifest[group]
        } == manifest[group]
    protocol = "lane3_handoff_protocol.py"
    assert (bundle / "runtime" / protocol).read_bytes() == (
        SCRIPTS / protocol
    ).read_bytes()
    assert (bundle / "runtime" / protocol).read_bytes() != (
        bundle / "supplier" / protocol
    ).read_bytes()
    assert manifest["supplier"] == hi.SUPPLIER_HASHES
    assert manifest["supplier_commit"] == hi.SUPPLIER_COMMIT


@pytest.mark.parametrize("rollback", [False, True])
def test_real_old_ten_file_runtime_upgrades_or_restores_exactly(
    installation: Installation, rollback: bool
):
    prior, old_installed, new_installed = install_real_legacy_closure(installation)
    before = installation.live_snapshot()
    wrapper = installation.root / "reconcile.py"
    wrapper.write_bytes(
        wrapper.read_bytes().replace(
            repr(str(old_installed)).encode(), repr(str(new_installed)).encode()
        )
    )
    replacement = installation.inputs(2)
    if rollback:
        kill_install(installation, replacement, "activated")
        pending = installation.installer.transaction()
        assert pending["prior"] == prior
        assert set(pending["target"]["manifest"]["runtime"]) == set(hi.RUNTIME_MODULES)
        wrapper.write_bytes(
            wrapper.read_bytes().replace(
                repr(str(new_installed)).encode(), repr(str(old_installed)).encode()
            )
        )
        assert installation.installer.recover() == "ROLLED_BACK"
        assert installation.live_snapshot() == before
        installation.installer.verify_record(prior)
        installation.installer.require_ready(prior)
    else:
        installation.install(replacement)
        target = installation.installer.verify()
        assert set(target["manifest"]["runtime"]) == set(hi.RUNTIME_MODULES)
        assert "lane3_campaign_bootstrap.py" in target["manifest"]["runtime"]
        assert old_installed.exists()


def test_real_reconcile_unit_publishes_current_boot_readiness(
    installation: Installation,
) -> None:
    installation.install(installation.inputs(1))
    record = installation.installer.verify()
    receipt = hi.strict_json(hi.read_file(installation.paths.ready, 0o600))
    assert hi.valid_readiness(receipt, record, installation.system.boot_id())
    assert (installation.root / "baseline-ready").exists()
    dependencies = command(
        "/usr/bin/systemctl", "show", installation.unit, "--property=Requires,After"
    )
    assert all(installation.baseline in line for line in dependencies.splitlines())
    installation.installer.require_ready(record)
    assert not installation.paths.transaction.exists()
    assert (
        not installation.paths.stage.exists() and not installation.paths.backup.exists()
    )


@pytest.mark.parametrize(
    "phase", ["intent", "staged", "activated", "awaiting_reconcile"]
)
@pytest.mark.parametrize("enabled", [False, True])
def test_sigkill_install_requires_explicit_exact_restore(
    installation: Installation, phase: str, enabled: bool
) -> None:
    installation.install(installation.inputs(1))
    wants = installation.link("wants", "../" + hi.UNIT_NAME) if enabled else None
    link_before = link_snapshot(wants) if wants is not None else None
    before = installation.live_snapshot()
    replacement = installation.inputs(2)
    kill_install(installation, replacement, phase)
    assert installation.paths.transaction.exists()
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.installer.dispatch("status", hi.canonical({"op": "status"}))
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.installer.verify()
    installation.installer.recover()
    assert installation.live_snapshot() == before
    if wants is not None:
        assert link_snapshot(wants) == link_before
    assert not installation.paths.transaction.exists()
    assert (
        not installation.paths.stage.exists() and not installation.paths.backup.exists()
    )
    installation.installer.require_ready(installation.installer.verify())


def test_active_lease_blocks_install_and_recovery(installation: Installation) -> None:
    installation.install(installation.inputs(1))
    before = installation.live_snapshot()
    replacement = installation.inputs(2)
    journal = installation.paths.state / "journal.json"
    put(journal, hi.canonical({"synthetic": "active-lease-barrier"}))
    with pytest.raises(hi.InstallRefused, match="installation.lease"):
        installation.install(replacement)
    assert installation.live_snapshot() == before
    journal.unlink()
    kill_install(installation, replacement, "activated")
    put(journal, hi.canonical({"synthetic": "active-lease-barrier"}))
    with pytest.raises(hi.InstallRefused, match="installation.lease"):
        installation.installer.recover()
    assert installation.paths.transaction.exists()
    journal.unlink()
    installation.installer.recover()
    assert installation.live_snapshot() == before


def test_first_install_intent_recovers_with_absent_service(
    installation: Installation,
) -> None:
    # No loaded temporary reconciliation service stands in for a preinstalled
    # production service. Recovery must accept positively observed absence.
    unit_path = pathlib.Path("/run/systemd/system") / installation.unit
    unit_path.unlink()
    command("/usr/bin/systemctl", "daemon-reload")
    assert (
        command(
            "/usr/bin/systemctl",
            "show",
            "--property=LoadState",
            "--value",
            installation.unit,
            check=False,
        )
        == "not-found"
    )
    kill_install(installation, installation.inputs(1), "intent")
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.installer.dispatch("prepare", hi.canonical({"op": "prepare"}))
    installation.installer.recover()
    for path in (
        installation.paths.config,
        installation.paths.record,
        installation.paths.unit,
        installation.paths.ready,
        installation.paths.transaction,
        installation.paths.stage,
        installation.paths.backup,
        installation.paths.runtime / installation.bundle_digest,
    ):
        assert not path.exists()


@pytest.mark.parametrize("partial_backup", [False, True])
def test_committed_install_finishes_target_after_sigkill(
    installation: Installation, partial_backup: bool
) -> None:
    installation.install(installation.inputs(1))
    replacement = installation.inputs(2)
    kill_install(installation, replacement, "committed")
    target = installation.live_snapshot()
    pending = installation.installer.transaction()
    assert pending["phase"] == "committed"
    assert pending["target"]["config_digest"] == replacement[1]
    if partial_backup:
        # Model a second crash after finalization removed an owned backup file.
        (installation.paths.backup / "admission.json").unlink()
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.installer.dispatch("status", hi.canonical({"op": "status"}))
    installation.installer.recover()
    assert installation.live_snapshot() == target
    record = installation.installer.verify()
    assert record["config_digest"] == replacement[1]
    installation.installer.require_ready(record)
    assert not installation.paths.transaction.exists()
    assert not installation.paths.backup.exists()


def test_controller_lock_collision_refuses_installer_and_dispatcher(
    installation: Installation,
) -> None:
    installation.install(installation.inputs(1))
    replacement = installation.inputs(2)
    before = installation.live_snapshot()
    lock = installation.paths.state / "lock"
    lock_inode = lock.stat().st_ino
    controller = hc.Controller(paths=hc.Paths(state_root=installation.paths.state))
    with controller.locked():
        with pytest.raises(hi.InstallRefused, match="lock.busy"):
            installation.install(replacement)
        with pytest.raises(hi.InstallRefused, match="lock.busy"):
            installation.status("1" * 32)
        with pytest.raises(hi.InstallRefused, match="lock.busy"):
            installation.installer.recover()
    assert lock.stat().st_ino == lock_inode
    assert installation.live_snapshot() == before
    assert not installation.paths.transaction.exists()


@pytest.mark.parametrize("fault", ["changed_mode", "symlink"])
def test_installed_path_drift_refuses_without_replacement(
    installation: Installation, fault: str
) -> None:
    installation.install(installation.inputs(1))
    replacement = installation.inputs(2)
    before = installation.live_snapshot()
    victim = installation.paths.config / "policy.json"
    original = victim.read_bytes()
    canary = installation.root / "outside-owned-configuration"
    put(canary, b"synthetic external file must remain untouched")
    canary_before = snapshot(canary)
    if fault == "changed_mode":
        victim.chmod(0o640)
    else:
        victim.unlink()
        victim.symlink_to(canary)
    try:
        with pytest.raises(hi.InstallRefused):
            installation.install(replacement)
        assert not installation.paths.transaction.exists()
        assert (
            not installation.paths.stage.exists()
            and not installation.paths.backup.exists()
        )
        assert snapshot(canary) == canary_before
        if fault == "changed_mode":
            assert stat.S_IMODE(victim.stat().st_mode) == 0o640
        else:
            assert victim.is_symlink() and victim.readlink() == canary
    finally:
        if fault == "symlink":
            victim.unlink()
            put(victim, original)
        else:
            victim.chmod(0o600)
    assert installation.live_snapshot() == before


def test_exact_prior_inputs_can_be_reinstalled(installation: Installation) -> None:
    original = installation.inputs(1)
    installation.install(original)
    before = installation.live_snapshot()
    replacement = installation.inputs(2)
    installation.install(replacement)
    assert installation.installer.verify()["config_digest"] == replacement[1]
    installation.install(original)
    assert installation.live_snapshot() == before
    assert installation.installer.verify()["config_digest"] == original[1]
    assert not installation.paths.transaction.exists()
    assert (
        not installation.paths.stage.exists() and not installation.paths.backup.exists()
    )


@pytest.mark.parametrize("absolute", [False, True])
def test_install_and_upgrade_preserve_preexisting_exact_wants_link(
    installation: Installation, absolute: bool
) -> None:
    target = str(installation.paths.unit) if absolute else "../" + hi.UNIT_NAME
    wants = installation.link("wants", target)
    link_before = link_snapshot(wants)
    # An exact link may be dangling before the first installation creates its unit.
    assert wants.is_symlink() and not wants.exists()
    for revision in (1, 2):
        inputs = installation.inputs(revision)
        installation.install(inputs)
        record = installation.installer.verify()
        assert record["config_digest"] == inputs[1]
        installation.installer.require_ready(record)
        assert link_snapshot(wants) == link_before
        assert wants.resolve() == installation.paths.unit
        assert not installation.paths.transaction.exists()
        assert not installation.paths.stage.exists()
        assert not installation.paths.backup.exists()


@pytest.mark.parametrize("dangling", [False, True])
def test_upgrade_refuses_foreign_wants_link_without_replacing_prior_installation(
    installation: Installation, dangling: bool
) -> None:
    installation.install(installation.inputs(1))
    foreign = installation.root / "foreign-unit.service"
    if not dangling:
        put(foreign, b"synthetic foreign unit must remain untouched", 0o644)
    wants = installation.link("wants", str(foreign))
    link_before = link_snapshot(wants)
    foreign_before = snapshot(foreign)
    before = installation.live_snapshot()
    replacement = installation.inputs(2)
    with pytest.raises(hi.InstallRefused, match="installation.drift"):
        installation.install(replacement)
    assert installation.live_snapshot() == before
    assert link_snapshot(wants) == link_before
    assert snapshot(foreign) == foreign_before
    assert not installation.paths.transaction.exists()
    assert not installation.paths.stage.exists()
    assert not installation.paths.backup.exists()
    assert command("/usr/bin/systemctl", "is-active", installation.unit) == "active"
    # Sensitivity: replacing only the foreign link admits the same upgrade.
    wants.unlink()
    wants.symlink_to("../" + hi.UNIT_NAME)
    installation.install(replacement)
    assert installation.installer.verify()["config_digest"] == replacement[1]


def test_dynamic_approval_does_not_block_installed_status_dispatch(
    installation: Installation,
) -> None:
    installation.install(installation.inputs(1))
    lease = "1" * 32
    # A synthetic journal supplies only the fields read by status; it grants
    # neither a running identity nor network authority to this filesystem test.
    journal = installation.paths.state / "journal.json"
    put(
        journal,
        hp.canonical_json(
            {
                "schema": hc.JOURNAL_SCHEMA,
                "lease_id": lease,
                "boot_id": installation.system.boot_id(),
                "state": "PREPARED",
                "expires_at_monotonic_ns": None,
                "category": None,
                "report": None,
                "report_digest": None,
            }
        ),
    )
    approval = installation.paths.config / "approval.json"
    put(approval, b"synthetic invalid approval must not be parsed by status")
    before = snapshot(approval)
    response = installation.status(lease)
    assert response["op"] == "status" and response["state"] == "PREPARED"
    assert response["lease_id"] == lease
    assert snapshot(approval) == before


def kill_uninstall(value: Installation, phase: str) -> None:
    child = value.start_uninstall(phase)
    try:
        child.communicate(timeout=45)
        assert (
            child.returncode == -signal.SIGKILL
        ), "uninstaller must reach its SIGKILL checkpoint"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def assert_uninstalled(value: Installation) -> None:
    for path in value.installed_paths():
        assert not path.exists() and not path.is_symlink()
    for kind in ("wants", "requires"):
        assert not (
            value.paths.unit.parent / f"multi-user.target.{kind}" / hi.UNIT_NAME
        ).is_symlink()


def test_generated_unit_is_wanted_not_required_by_boot_target(
    installation: Installation,
) -> None:
    installation.install(installation.inputs(1))
    text = installation.paths.unit.read_text()
    assert "[Install]\nWantedBy=multi-user.target\n" in text
    assert "RequiredBy" not in text
    assert "Before=multi-user.target\n" in text
    assert "TimeoutStartSec=45\n" in text
    assert "ExecStart=/usr/bin/python3 -I -B /opt/dotmac-lane3-handoff/" in text


def test_uninstall_removes_only_the_recorded_installation(
    installation: Installation,
) -> None:
    installation.install(installation.inputs(1))
    wants = installation.link("wants", "../" + hi.UNIT_NAME)
    sibling = installation.paths.runtime / ("f" * 64)
    sibling.mkdir(mode=0o755)
    canary = installation.paths.state / "unrelated-canary"
    put(canary, b"synthetic state outside installer record")
    canary_before = snapshot(canary)
    lock = installation.paths.state / "lock"
    lock_inode = lock.stat().st_ino
    nft_before = command("nft", "list", "table", "inet", installation.table)
    assert installation.installer.uninstall() == "UNINSTALLED"
    assert_uninstalled(installation)
    assert not wants.is_symlink()
    assert snapshot(canary) == canary_before
    assert lock.stat().st_ino == lock_inode
    # A sibling not recorded by the installer keeps the runtime parent alive.
    assert sibling.is_dir() and installation.paths.runtime.is_dir()
    assert command("nft", "list", "table", "inet", installation.table) == nft_before
    assert (
        command("/usr/bin/systemctl", "is-active", installation.unit, check=False)
        != "active"
    )
    # Idempotent: a second run observes absence and changes nothing.
    assert installation.installer.uninstall() == "ABSENT"
    sibling.rmdir()


def test_uninstall_removes_empty_runtime_parent_and_is_repeatable(
    installation: Installation,
) -> None:
    inputs = installation.inputs(1)
    installation.install(inputs)
    assert installation.installer.uninstall() == "UNINSTALLED"
    assert not installation.paths.runtime.exists()
    installation.install(inputs)
    assert installation.installer.verify()["config_digest"]


def test_uninstall_refuses_any_lease_and_deletes_nothing(
    installation: Installation,
) -> None:
    installation.install(installation.inputs(1))
    before = installation.live_snapshot()
    journal = installation.paths.state / "journal.json"
    put(journal, hi.canonical({"synthetic": "active-lease-barrier"}))
    with pytest.raises(hi.InstallRefused, match="installation.lease"):
        installation.installer.uninstall()
    assert installation.live_snapshot() == before
    assert not installation.paths.intent.exists()
    # Sensitivity: the lease alone caused the refusal.
    journal.unlink()
    assert installation.installer.uninstall() == "UNINSTALLED"
    assert_uninstalled(installation)


def test_uninstall_refuses_pending_transaction_and_deletes_nothing(
    installation: Installation,
) -> None:
    installation.install(installation.inputs(1))
    kill_install(installation, installation.inputs(2), "activated")
    before = installation.live_snapshot()
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.installer.uninstall()
    assert installation.live_snapshot() == before
    assert installation.paths.transaction.exists()
    assert not installation.paths.intent.exists()
    installation.installer.recover()
    assert installation.installer.uninstall() == "UNINSTALLED"
    assert_uninstalled(installation)


def mutate(value: Installation, fault: str) -> tuple[pathlib.Path, Callable[[], None]]:
    paths = value.paths
    runtime = paths.runtime / value.bundle_digest / "lane3_handoff_controller.py"
    if fault == "runtime_byte":
        original = runtime.read_bytes()
        runtime.chmod(0o644)
        runtime.write_bytes(original + b"#")
        runtime.chmod(0o444)

        def undo() -> None:
            runtime.chmod(0o644)
            runtime.write_bytes(original)
            runtime.chmod(0o444)

        return runtime, undo
    if fault == "config_mode":
        victim = paths.config / "policy.json"
        victim.chmod(0o640)
        return victim, lambda: victim.chmod(0o600)
    if fault == "config_extra":
        extra = paths.config / "extra.json"
        put(extra, b"{}")
        return extra, extra.unlink
    if fault == "approval_present":
        approval = paths.config / "approval.json"
        put(approval, b"synthetic opaque approval is not installer-owned")
        return approval, approval.unlink
    if fault == "unit_byte":
        original = paths.unit.read_bytes()
        paths.unit.write_bytes(original + b"\n")
        return paths.unit, lambda: paths.unit.write_bytes(original)
    if fault == "ready_mode":
        paths.ready.chmod(0o644)
        return paths.ready, lambda: paths.ready.chmod(0o600)
    if fault == "stray_stage":
        paths.stage.mkdir(mode=0o700)
        return paths.stage, paths.stage.rmdir
    if fault == "requires_link":
        link = value.link("requires", "../" + hi.UNIT_NAME)
        return link, link.unlink
    if fault == "foreign_wants_link":
        link = value.link("wants", "/nonexistent-synthetic-target")
        return link, link.unlink
    raise AssertionError(fault)


@pytest.mark.parametrize(
    "fault",
    [
        "runtime_byte",
        "config_mode",
        "config_extra",
        "approval_present",
        "unit_byte",
        "ready_mode",
        "stray_stage",
        "requires_link",
        "foreign_wants_link",
    ],
)
def test_uninstall_refuses_drift_without_deleting(
    installation: Installation, fault: str
) -> None:
    installation.install(installation.inputs(1))
    _, undo = mutate(installation, fault)
    before = installation.live_snapshot()
    active = command("/usr/bin/systemctl", "is-active", installation.unit)
    with pytest.raises(hi.InstallRefused):
        installation.installer.uninstall()
    assert installation.live_snapshot() == before
    assert not installation.paths.intent.exists()
    assert command("/usr/bin/systemctl", "is-active", installation.unit) == active
    # Sensitivity: the exact drift alone caused the refusal.
    undo()
    assert installation.installer.uninstall() == "UNINSTALLED"
    assert_uninstalled(installation)


@pytest.mark.parametrize(
    "phase",
    [
        "uninstall_intent",
        "uninstall_stopped",
        "uninstall_unit_removed",
        "uninstall_reloaded",
        "uninstall_runtime_removed",
        "uninstall_config_removed",
        "uninstall_ready_removed",
        "uninstall_record_removed",
    ],
)
def test_sigkill_uninstall_is_finished_by_rerun(
    installation: Installation, phase: str
) -> None:
    installation.install(installation.inputs(1))
    installation.link("wants", "../" + hi.UNIT_NAME)
    kill_uninstall(installation, phase)
    assert installation.paths.intent.exists()
    # The barrier holds until the operation is finished explicitly.
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.installer.dispatch("status", hi.canonical({"op": "status"}))
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.installer.verify()
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.installer.boot_reconcile()
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.install(installation.inputs(2))
    with pytest.raises(hi.InstallRefused, match="installation.pending"):
        installation.installer.recover()
    assert installation.installer.uninstall() == "UNINSTALLED"
    assert_uninstalled(installation)
    assert installation.installer.uninstall() == "ABSENT"


@pytest.mark.parametrize(
    ("kind", "target"),
    [("requires", "../" + hi.UNIT_NAME), ("wants", "/nonexistent-synthetic-target")],
)
def test_install_refuses_unowned_boot_link_before_any_change(
    installation: Installation, kind: str, target: str
) -> None:
    link = installation.link(kind, target)
    before = installation.live_snapshot()
    with pytest.raises(hi.InstallRefused, match="installation.drift"):
        installation.install(installation.inputs(1))
    assert installation.live_snapshot() == before
    assert not installation.paths.transaction.exists()
    # Sensitivity: the link alone caused the refusal.
    link.unlink()
    installation.install(installation.inputs(2))
    assert installation.installer.verify()["config_digest"]


def test_uninstall_rerun_rechecks_wants_link_before_disable(
    installation: Installation,
) -> None:
    installation.install(installation.inputs(1))
    wants = installation.link("wants", "../" + hi.UNIT_NAME)
    kill_uninstall(installation, "uninstall_intent")
    wants.unlink()
    wants.symlink_to("/nonexistent-synthetic-target")
    with pytest.raises(hi.InstallRefused, match="installation.drift"):
        installation.installer.uninstall()
    assert installation.paths.intent.exists()
    assert command("/usr/bin/systemctl", "is-active", installation.unit) == "active"
    # Sensitivity: restoring the exact link lets the same rerun finish.
    wants.unlink()
    wants.symlink_to("../" + hi.UNIT_NAME)
    assert installation.installer.uninstall() == "UNINSTALLED"
    assert_uninstalled(installation)


def test_uninstall_intent_with_invalid_record_refuses_and_deletes_nothing(
    installation: Installation,
) -> None:
    installation.install(installation.inputs(1))
    record = installation.installer.record()
    record["config_files"]["../../unrelated"] = "b" * 64
    put(
        installation.paths.intent,
        hi.canonical({"schema": hi.UNINSTALL_SCHEMA, "record": record}),
    )
    before = installation.live_snapshot()
    with pytest.raises(hi.InstallRefused):
        installation.installer.uninstall()
    assert installation.live_snapshot() == before
    installation.paths.intent.unlink()
    assert installation.installer.uninstall() == "UNINSTALLED"


def test_recover_refuses_stale_requires_link_and_removes_exact_wants_link(
    installation: Installation,
) -> None:
    kill_install(installation, installation.inputs(1), "activated")
    requires = installation.link("requires", "../" + hi.UNIT_NAME)
    with pytest.raises(hi.InstallRefused, match="installation.drift"):
        installation.installer.recover()
    # Barrier retained; the refusal precedes any recovery mutation.
    assert installation.paths.transaction.exists()
    assert installation.paths.config.exists()
    assert requires.is_symlink()
    requires.unlink()
    foreign = installation.link("wants", "/nonexistent-synthetic-target")
    with pytest.raises(hi.InstallRefused, match="installation.drift"):
        installation.installer.recover()
    assert installation.paths.transaction.exists()
    assert installation.paths.config.exists()
    foreign.unlink()
    exact_link = installation.link("wants", "../" + hi.UNIT_NAME)
    assert installation.installer.recover() == "ROLLED_BACK"
    assert not exact_link.is_symlink()
    assert not installation.paths.transaction.exists()
