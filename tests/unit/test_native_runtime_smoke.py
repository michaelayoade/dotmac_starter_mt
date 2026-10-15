"""The compiler-free target smoke must install wheels and load native bytes."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "native_runtime_qualification", ROOT / "scripts/native_qualify.py"
)
assert SPEC and SPEC.loader
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)


def test_runtime_smoke_installs_only_retained_wheels_and_isolates_imports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    target = artifacts / "dotmac_imports-0.2.0a1-cp312-cp312-linux_x86_64.whl"
    target.touch()
    (artifacts / "ignored.tar.gz").touch()
    runtime = artifacts / "runtime"
    runtime.mkdir()
    kernel = runtime / "dotmac_kernel-0.1.0a105-py3-none-any.whl"
    kernel.touch()
    runner = Mock()
    monkeypatch.setattr(qualification, "run", runner)
    monkeypatch.setattr(
        qualification, "packages", lambda: [(Path("imports"), "dotmac_imports")]
    )

    qualification.runtime_smoke(artifacts)

    install, probe = runner.call_args_list
    assert install.args[:6] == (
        qualification.sys.executable,
        "-m",
        "pip",
        "install",
        "--only-binary=:all:",
        "--no-cache-dir",
    )
    assert set(install.args[6:]) == {str(target), str(kernel)}
    assert probe.args[:3] == (qualification.sys.executable, "-I", "-c")
    assert probe.args[-1] == "dotmac_imports"
    assert "ExtensionFileLoader" in probe.args[3]
    assert "site-packages" in probe.args[3]


def test_runtime_smoke_refuses_an_empty_artifact_set(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(RuntimeError, match="retained native and kernel wheels"):
        qualification.runtime_smoke(tmp_path)
