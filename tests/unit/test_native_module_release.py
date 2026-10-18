"""Release canaries use synthetic wheels; hosted qualification owns real binaries."""

from __future__ import annotations

import argparse
import importlib
import importlib.machinery
import importlib.util
import site
import subprocess
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def release(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("release_module")


def entry(tmp_path, *, native=True, module="dotmac_probe", decimal=False):
    project = tmp_path / "package"
    project.mkdir()
    marker = (
        f'[tool.dotmac.native]\npython-package = "{module}"\n'
        f"decimal-core = {str(decimal).lower()}\n"
        if native
        else ""
    )
    (project / "pyproject.toml").write_text(marker)
    return {
        "distribution": "dotmac-probe",
        "package_path": project,
        "import_name": "dotmac_probe",
        "manifest_attr": "module",
        "db_schema": None,
        "wheel_contents": {
            "required": [],
            "forbidden_prefixes": ["app/"],
            "allowed_requires": [],
        },
    }


def wheel(
    tmp_path,
    *,
    libraries=None,
    tag="cp311-cp311-linux_x86_64",
    pure=False,
    metadata_tag=None,
):
    dist = tmp_path / "dist"
    dist.mkdir()
    path = dist / f"dotmac_probe-0.2.0a1-{tag}.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("dotmac_probe/__init__.py", "")
        archive.writestr(
            "dotmac_probe-0.2.0a1.dist-info/METADATA", "Name: dotmac-probe\n"
        )
        archive.writestr(
            "dotmac_probe-0.2.0a1.dist-info/WHEEL",
            f"Wheel-Version: 1.0\nRoot-Is-Purelib: {str(pure).lower()}\n"
            f"Tag: {metadata_tag or tag}\n",
        )
        for name in libraries or []:
            archive.writestr(name, b"synthetic native payload")
    return path


def inspect(release, monkeypatch, metadata, path):
    monkeypatch.setattr(release, "resolve", lambda _name: metadata)
    monkeypatch.setattr(release, "migration_extent_problems", lambda _entry: [])
    release.cmd_inspect(
        argparse.Namespace(distribution="dotmac-probe", dist=str(path.parent))
    )


@pytest.mark.parametrize(
    ("libraries", "tag", "pure"),
    [
        ([], "cp311-cp311-linux_x86_64", False),
        (["dotmac_probe/_native.so"], "py3-none-any", True),
        (["dotmac_probe/_native.so"], "cp311-abi3-linux_x86_64", False),
        (["dotmac_probe/_native.so"], "py3-none-linux_x86_64", False),
        (["dotmac_probe/_native.so"], "cp311-cp311-any", False),
        (["other/_native.so"], "cp311-cp311-linux_x86_64", False),
        (["dotmac_probe/_native.dll"], "cp311-cp311-linux_x86_64", False),
        (
            ["dotmac_probe/_native.so", "dotmac_probe/extra.so"],
            "cp311-cp311-linux_x86_64",
            False,
        ),
    ],
)
def test_native_inspection_refuses_missing_wrong_or_pure_payload(
    release, monkeypatch, tmp_path, libraries, tag, pure
):
    metadata = entry(tmp_path)
    path = wheel(tmp_path, libraries=libraries, tag=tag, pure=pure)
    with pytest.raises(release.ReleaseRefused):
        inspect(release, monkeypatch, metadata, path)


@pytest.mark.parametrize("native", [False, True])
def test_inspection_accepts_legacy_and_enrolled_native(
    release, monkeypatch, tmp_path, native
):
    metadata = entry(tmp_path, native=native)
    path = wheel(
        tmp_path,
        libraries=["dotmac_probe/_native.so"] if native else [],
        tag="cp311-cp311-linux_x86_64" if native else "py3-none-any",
        pure=not native,
    )
    inspect(release, monkeypatch, metadata, path)


def test_native_enrollment_cannot_name_another_package(release, tmp_path):
    with pytest.raises(release.ReleaseRefused):
        release._native_package(entry(tmp_path, module="other"))


def test_native_filename_and_metadata_tags_must_agree(release, monkeypatch, tmp_path):
    metadata = entry(tmp_path)
    path = wheel(
        tmp_path,
        libraries=["dotmac_probe/_native.so"],
        metadata_tag="cp312-cp312-linux_x86_64",
    )
    with pytest.raises(release.ReleaseRefused, match="tags disagree"):
        inspect(release, monkeypatch, metadata, path)


def test_decimal_inspection_calls_canonical_link_and_license_verifier(
    release, monkeypatch, tmp_path
):
    metadata = entry(tmp_path, module="dotmac_inventory", decimal=True)
    metadata["import_name"] = "dotmac_inventory"
    path = wheel(tmp_path, libraries=["dotmac_inventory/_native.so"])
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("licenses/LICENSE.mpdecimal.txt", "upstream license")
    calls = []

    def output(command, *, text):
        calls.append(command)
        assert text is True
        return ""

    monkeypatch.setattr(subprocess, "check_output", output)
    inspect(release, monkeypatch, metadata, path)
    assert [command[0] for command in calls] == ["readelf", "nm"]


@pytest.mark.parametrize("failure", ["license", "dynamic", "symbols"])
def test_decimal_inspection_preserves_canonical_refusals(
    release, monkeypatch, tmp_path, failure
):
    metadata = entry(tmp_path, module="dotmac_inventory", decimal=True)
    metadata["import_name"] = "dotmac_inventory"
    path = wheel(tmp_path, libraries=["dotmac_inventory/_native.so"])
    if failure != "license":
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("licenses/LICENSE.mpdecimal.txt", "upstream license")

    def output(command, *, text):
        if failure == "dynamic" and command[0] == "readelf":
            return "NEEDED libmpdec.so"
        return "000 T mpd_add" if failure == "symbols" else ""

    monkeypatch.setattr(subprocess, "check_output", output)
    with pytest.raises(release.ReleaseRefused):
        inspect(release, monkeypatch, metadata, path)


def test_native_installed_script_is_isolated_and_embeds_exact_targets(
    release, monkeypatch
):
    commands = []
    monkeypatch.setattr(
        release.subprocess, "run", lambda command, **kwargs: commands.append(command)
    )
    release._verify_installed(Path("/venv/python"), [], ["dotmac_probe"])
    (command,) = commands
    assert command[:3] == ["/venv/python", "-I", "-c"]
    assert "dotmac_probe" in command[3]
    assert "ExtensionFileLoader" in command[3]


@pytest.mark.parametrize(
    "location,extension",
    [("site-packages", True), ("checkout", True), ("site-packages", False)],
)
def test_native_loader_proof_rejects_checkout_and_python_fallback(
    release, monkeypatch, tmp_path, location, extension
):
    name = "dotmac_probe._native"
    suffix = importlib.machinery.EXTENSION_SUFFIXES[0] if extension else ".py"
    origin = str(tmp_path / location / "dotmac_probe" / ("_native" + suffix))
    loader = (
        importlib.machinery.ExtensionFileLoader(name, origin)
        if extension
        else importlib.machinery.SourceFileLoader(name, origin)
    )
    module = ModuleType(name)
    module.__file__ = origin
    module.__spec__ = importlib.util.spec_from_loader(name, loader)
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda requested: module if requested == name else None,
    )
    monkeypatch.setattr(
        site,
        "getsitepackages",
        lambda: [str(tmp_path / "site-packages"), str(tmp_path / "checkout")],
    )
    script = release._NATIVE_REGISTER.format(
        names=["dotmac_probe"], checkout=str(tmp_path / "checkout")
    )
    if location == "site-packages" and extension:
        exec(script, {})  # noqa: S102  # nosec B102 - checked-in smoke script
    else:
        with pytest.raises(AssertionError):
            exec(script, {})  # noqa: S102  # nosec B102 - checked-in smoke script


@pytest.mark.parametrize("command", ["wheel", "registry"])
@pytest.mark.parametrize("native", [False, True])
def test_both_release_smokes_derive_native_targets(
    release, monkeypatch, tmp_path, command, native
):
    metadata = entry(tmp_path, native=native)
    descriptor = {
        **metadata,
        "version": "0.2.0a1",
        "code": "probe",
        "dependencies": (),
        "native_package": release._native_package(metadata),
    }
    monkeypatch.setattr(release, "_module_closure", lambda _: [descriptor])
    monkeypatch.setattr(release, "_declared", lambda _: {"version": "0.2.0a1"})
    monkeypatch.setattr(release, "_verify_smoke_inputs", lambda *args, **kwargs: None)
    monkeypatch.setattr(release, "_registry_wheels", lambda *_args: [])
    monkeypatch.setattr(
        release, "_stage_wheels", lambda *args, **kwargs: kwargs["local"]
    )
    path = wheel(tmp_path)
    calls = []
    monkeypatch.setattr(release, "resolve", lambda _name: metadata)
    monkeypatch.setattr(
        release, "_venv", lambda _path: (Path("/venv/python"), Path("/venv/pip"))
    )
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(release, "_verify_installed", lambda *args: calls.append(args))
    if command == "wheel":
        release.cmd_verify_wheel(
            argparse.Namespace(
                distribution="dotmac-probe",
                dist=str(path.parent),
                kernel_dist=None,
                dependency_dist=[],
                emit_dependency_manifest=None,
            )
        )
    else:
        release.cmd_verify_registry(
            argparse.Namespace(
                kernel=None,
                pin=["dotmac-probe==0.2.0a1"],
                index="https://example.invalid/simple",
            )
        )
    assert calls == [
        (
            Path("/venv/python"),
            release._installed_targets([descriptor]),
            ["dotmac_probe"] if native else [],
        )
    ]


@pytest.mark.parametrize("native", [False, True])
def test_resolve_emits_derived_native_package(
    release, monkeypatch, tmp_path, capsys, native
):
    metadata = entry(tmp_path, native=native)
    metadata.update(
        package_dir="packages/dotmac-probe",
        kernel_floor="0.1.0a105",
        tag_prefix="dotmac-probe-v",
    )
    monkeypatch.setattr(release, "resolve", lambda _name: metadata)
    monkeypatch.setattr(
        release,
        "_declared",
        lambda _entry: {
            "name": "dotmac-probe",
            "version": "0.2.0a1",
            "dependencies": {"dotmac-kernel": ">=0.1.0a105"},
        },
    )
    release.cmd_resolve(
        argparse.Namespace(distribution="dotmac-probe", version="0.2.0a1")
    )
    assert (
        f"native_package={'dotmac_probe' if native else ''}\n"
        in capsys.readouterr().out
    )
