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
