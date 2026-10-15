"""Exercise native build refusals using synthetic wheels; never compile here."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    native = tmp_path / "native"
    native.mkdir()
    (native / "Cargo.lock").write_text("# synthetic build input\n")
    source = (ROOT / "scripts/native_build_template.py").read_text()
    source = source.replace("__DOTMAC_PACKAGE__", "example_owner")
    source = source.replace("__DOTMAC_MATURIN__", "1.14.1")
    source = source.replace("__DOTMAC_RUST__", "1.90.0")
    path = tmp_path / "build.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("native_build_canary", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.importlib.metadata, "version", lambda _: "1.14.1")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/verified/rustc")
    monkeypatch.setattr(
        module.subprocess, "check_output", lambda *a, **k: "rustc 1.90.0 (fixture)"
    )
    return module


def _builder(monkeypatch: pytest.MonkeyPatch, hook, extra: str | None = None):
    suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
    extension = "example_owner/_native" + suffix

    def build(argv, *, cwd, env, check):
        assert "--locked" in argv and "--release" in argv and check
        assert Path(cwd).name == "native"
        assert env["CARGO_TARGET_DIR"]
        output = Path(argv[argv.index("--out") + 1])
        output.mkdir(parents=True)
        with zipfile.ZipFile(output / "synthetic.whl", "w") as archive:
            archive.writestr(extension, b"known compiled bytes")
            archive.writestr(
                "example_build.dist-info/METADATA", "Name: example-build\nVersion: 1\n"
            )
            if extra:
                archive.writestr(extra, b"planted unexpected payload")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(hook.subprocess, "run", build)
    return extension


def test_valid_single_owner_extension_is_copied(tmp_path, monkeypatch) -> None:
    hook = _hook(tmp_path, monkeypatch)
    extension = _builder(monkeypatch, hook)
    hook.build_native()
    assert (tmp_path / "src" / extension).read_bytes() == b"known compiled bytes"


@pytest.mark.parametrize(
    "plant",
    ["../outside", "/outside", "other/_native.so", "example_owner/.libs/libextra.so"],
)
def test_planted_wheel_payload_is_refused_before_copy(
    tmp_path, monkeypatch, plant
) -> None:
    # Same hook accepts the valid wheel above and rejects each planted violation.
    hook = _hook(tmp_path, monkeypatch)
    _builder(monkeypatch, hook, plant)
    with pytest.raises(RuntimeError):
        hook.build_native()
    assert not (tmp_path / "src").exists()


def test_missing_lock_refuses_before_starting_compiler(tmp_path, monkeypatch) -> None:
    hook = _hook(tmp_path, monkeypatch)
    (tmp_path / "native/Cargo.lock").unlink()
    monkeypatch.setattr(
        hook.subprocess, "run", lambda *a, **k: pytest.fail("started build")
    )
    with pytest.raises(RuntimeError, match="Cargo.lock"):
        hook.build_native()


def test_wrong_compiler_is_refused(tmp_path, monkeypatch) -> None:
    hook = _hook(tmp_path, monkeypatch)
    monkeypatch.setattr(
        hook.subprocess, "check_output", lambda *a, **k: "rustc 1.89.0 (fixture)"
    )
    with pytest.raises(RuntimeError, match="rustc 1.90.0"):
        hook.build_native()


@pytest.mark.parametrize("missing", ("original", "rebuilt"))
def test_artifact_qualification_requires_each_wheel(tmp_path, monkeypatch, missing):
    spec = importlib.util.spec_from_file_location(
        "native_qualification_canary", ROOT / "scripts/native_qualify.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "packages", lambda: [(Path("example-owner"), "example_owner")]
    )
    monkeypatch.setattr(module, "verify_wheel", lambda *args: None)
    (tmp_path / "rebuilt").mkdir()
    directory = tmp_path / "rebuilt" if missing == "original" else tmp_path
    (directory / "example_owner-1-cp312-cp312-linux_x86_64.whl").touch()
    with pytest.raises(RuntimeError, match="expected one example_owner wheel"):
        module.verify_artifacts(tmp_path)


@pytest.mark.parametrize(
    "symbol", ("mpd_qadd", "_mpd_add", "MPD_MINALLOC", "dm_decimal_add")
)
def test_decimal_symbol_families_must_remain_private(tmp_path, monkeypatch, symbol):
    spec = importlib.util.spec_from_file_location(
        "native_linkage_canary", ROOT / "scripts/native_qualify.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wheel = tmp_path / "inventory.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("dotmac_inventory/_native.so", b"synthetic ELF")
        archive.writestr("native/vendor/LICENSE.mpdecimal.txt", "upstream notice")
        archive.writestr(
            "inventory.dist-info/WHEEL",
            "Root-Is-Purelib: false\nTag: cp312-cp312-linux_x86_64\n",
        )
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda argv, **kwargs: "" if argv[0] == "readelf" else f"0000 T {symbol}\n",
    )
    with pytest.raises(RuntimeError, match="private symbols"):
        module.verify_wheel(wheel, "dotmac_inventory")


def test_decimal_source_mirror_checks_bytes_and_extra_files(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "native_source_mirror_canary", ROOT / "scripts/sync_native_builds.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    canonical = tmp_path / "native/decimal-core"
    canonical.mkdir(parents=True)
    (canonical / "Cargo.toml").write_text("canonical source\n")
    (canonical / "archive.tar.gz").write_bytes(b"exact pinned source bytes")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DECIMAL_CORE", canonical)
    package = tmp_path / "packages/example"
    assert module.sync_decimal_core(package, check=False) == []
    assert module.sync_decimal_core(package, check=True) == []
    embedded = package / "native/decimal-core"
    (embedded / "archive.tar.gz").write_bytes(b"changed source bytes")
    (embedded / "stale.rs").write_text("obsolete source")
    assert set(module.sync_decimal_core(package, check=True)) == {
        "packages/example/native/decimal-core/archive.tar.gz",
        "packages/example/native/decimal-core/stale.rs",
    }
    module.sync_decimal_core(package, check=False)
    assert not (embedded / "stale.rs").exists()
    assert (embedded / "archive.tar.gz").read_bytes() == b"exact pinned source bytes"
