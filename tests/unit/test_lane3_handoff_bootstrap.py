"""Synthetic pre-import byte and protected-metadata canaries (hosted CI)."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "bootstrap_under_test",
    Path(__file__).resolve().parents[2] / "scripts/lane3_handoff_bootstrap.py",
)
assert SPEC is not None and SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    if not Path("/proc/sys/kernel/random/boot_id").exists():
        pytest.skip("Linux boot binding")
    lease = tmp_path / ("1" * 32)
    bundle = lease / "supplier"
    bundle.mkdir(parents=True)
    lease.chmod(0o750)
    source = bundle / "lane3_bootstrap_canary.py"
    source.write_text("VALUE = 1\n")
    source.chmod(0o440)
    modules = {source.name: hashlib.sha256(source.read_bytes()).hexdigest()}
    manifest = {
        "schema": bootstrap.SCHEMA,
        "starter_commit": "a" * 40,
        "admission_digest": "b" * 64,
        "supplier_digest": hashlib.sha256(bootstrap._canonical(modules)).hexdigest(),
        "modules": modules,
    }
    installation = bundle / "supplier-installation.json"
    installation.write_text(json.dumps(manifest))
    installation.chmod(0o440)
    bundle.chmod(0o550)
    now = time.monotonic_ns()
    launch = {
        "protocol": bootstrap.PROTOCOL,
        "lease_id": lease.name,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "launched_at_monotonic_ns": now,
        "expires_at_monotonic_ns": now + 60_000_000_000,
        "admission_digest": manifest["admission_digest"],
        "supplier_digest": manifest["supplier_digest"],
    }
    record = lease / "launch.json"
    record.write_text(json.dumps(launch))
    record.chmod(0o640)
    # Unit tests cannot create a root-owned ancestor tree. Only owner metadata
    # and descriptor traversal are injected here; hosted root integration uses
    # the actual descriptor walk and modes. Bytes/digests/imports remain real.
    real_fstat = os.fstat

    def root_stat(fd):
        info = real_fstat(fd)
        return SimpleNamespace(
            **{
                key: getattr(info, key)
                for key in (
                    "st_mode",
                    "st_gid",
                    "st_nlink",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
            },
            st_uid=0,
        )

    monkeypatch.setattr(bootstrap.os, "fstat", root_stat)
    monkeypatch.setattr(
        bootstrap, "_directory", lambda p: os.open(p, os.O_RDONLY | os.O_DIRECTORY)
    )
    monkeypatch.setattr(bootstrap, "_isolated", lambda: True)
    # Other unit files import supplier modules during collection. The bootstrap
    # itself runs in a fresh isolated process in the production-path suite.
    prior = {
        key: value for key, value in sys.modules.items() if key.startswith("lane3_")
    }
    for key in prior:
        monkeypatch.delitem(sys.modules, key)
    original_path = sys.path[:]
    original_bytecode = sys.dont_write_bytecode
    yield bundle, record, launch
    sys.path[:] = original_path
    sys.dont_write_bytecode = original_bytecode
    bundle.chmod(0o750)
    sys.modules.pop("lane3_bootstrap_canary", None)
    sys.modules.update(prior)


def test_verified_bytes_import(staged):
    bundle, record, _ = staged
    assert (
        bootstrap.load_verified_bundle(bundle, record)["lane3_bootstrap_canary"].VALUE
        == 1
    )


def test_changed_local_byte_refuses_before_import(staged):
    bundle, record, _ = staged
    source = bundle / "lane3_bootstrap_canary.py"
    source.chmod(0o600)
    source.write_text("VALUE = 2\n")
    source.chmod(0o440)
    with pytest.raises(bootstrap.BootstrapRefused, match="^bootstrap.refused$"):
        bootstrap.load_verified_bundle(bundle, record)
    assert "lane3_bootstrap_canary" not in sys.modules


@pytest.mark.parametrize(
    "field,value",
    [
        ("admission_digest", "c" * 64),
        ("supplier_digest", "c" * 64),
        ("boot_id", "foreign"),
        ("launched_at_monotonic_ns", 1),
        ("expires_at_monotonic_ns", 1),
    ],
)
def test_launch_binding_refuses(staged, field, value):
    bundle, record, launch = staged
    launch[field] = value
    record.write_text(json.dumps(launch))
    with pytest.raises(bootstrap.BootstrapRefused):
        bootstrap.load_verified_bundle(bundle, record)
    assert "lane3_bootstrap_canary" not in sys.modules


def test_no_unisolated_import(staged, monkeypatch):
    bundle, record, _ = staged
    monkeypatch.setattr(bootstrap, "_isolated", lambda: False)
    with pytest.raises(bootstrap.BootstrapRefused):
        bootstrap.load_verified_bundle(bundle, record)


def test_lazy_import_cannot_restore_ambient_path(staged, tmp_path):
    bundle, record, launch = staged
    (tmp_path / "ambient_bootstrap_canary.py").write_text("VALUE = 99\n")
    source = bundle / "lane3_bootstrap_canary.py"
    source.chmod(0o600)
    source.write_text(
        "def lazy():\n    import ambient_bootstrap_canary\n"
        "    return ambient_bootstrap_canary.VALUE\n"
    )
    source.chmod(0o440)
    installation = bundle / "supplier-installation.json"
    manifest = json.loads(installation.read_text())
    manifest["modules"][source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest["supplier_digest"] = hashlib.sha256(
        bootstrap._canonical(manifest["modules"])
    ).hexdigest()
    installation.chmod(0o600)
    installation.write_text(json.dumps(manifest))
    installation.chmod(0o440)
    launch["supplier_digest"] = manifest["supplier_digest"]
    record.write_text(json.dumps(launch))
    sys.path.insert(0, str(tmp_path))
    module = bootstrap.load_verified_bundle(bundle, record)["lane3_bootstrap_canary"]
    with pytest.raises(ModuleNotFoundError):
        module.lazy()


def test_group_writable_module_refuses(staged):
    bundle, record, _ = staged
    (bundle / "lane3_bootstrap_canary.py").chmod(0o460)
    with pytest.raises(bootstrap.BootstrapRefused):
        bootstrap.load_verified_bundle(bundle, record)


def test_byte_canary_detects_weakened_hash_predicate(staged):
    bundle, record, _ = staged
    canary = bundle / "lane3_bootstrap_canary.py"
    canary.chmod(0o600)
    canary.write_text("VALUE = 2\n")
    canary.chmod(0o440)
    with pytest.raises(bootstrap.BootstrapRefused):
        bootstrap._load(bundle, record)
    source = inspect.getsource(bootstrap._load)
    guard = "if hashlib.sha256(raw).hexdigest() != digest:"
    assert source.count(guard) == 1
    namespace = dict(vars(bootstrap))
    exec(  # noqa: S102 — fixed synthetic mutation, no caller-supplied code
        compile(
            source.replace(guard, "if False:"),
            "<synthetic-weakened-byte-guard>",
            "exec",
        ),
        namespace,
    )
    assert namespace["_load"](bundle, record)["lane3_bootstrap_canary"].VALUE == 2


def test_read_rejects_symlink(staged):
    bundle, _, _ = staged
    bundle.chmod(0o750)
    link = bundle / "lane3_link.py"
    link.symlink_to("lane3_bootstrap_canary.py")
    fd = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            bootstrap._read(fd, link.name, stat.S_IMODE(0o440), os.getgid())
    finally:
        os.close(fd)
