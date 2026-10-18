"""Logical release edges are reviewed independently of Python requirements.

Execution of these canaries, including the real manifest registry sensitivity
proof, belongs to Git-hosted CI only.
"""

# ruff: noqa: S603 -- trusted hosted interpreter and reviewed fixture wheels

from __future__ import annotations

import argparse
import copy
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def release(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("release_module")


@pytest.fixture()
def policy(release, monkeypatch, tmp_path):
    """Source-only graphs exercise resolver refusal, never fake providers."""
    rows = {}
    for code in ("root", "timer", "other"):
        package = tmp_path / code
        source = package / "src" / f"dotmac_{code}"
        source.mkdir(parents=True)
        (package / "EXTRACTION.toml").write_text('classification="optional-module"\n')
        (package / "pyproject.toml").write_text(
            f'[tool.poetry]\nname="dotmac-{code}"\nversion="1.0.0"\n'
        )
        (source / "manifest.py").write_text(
            f'module = ModuleManifest(code="{code}", version="1.0.0")\n'
        )
        rows[f"dotmac-{code}"] = {
            "package_dir": str(package),
            "import_name": f"dotmac_{code}",
            "manifest_attr": "module",
            "kernel_floor": "0.1.0a106",
            "db_schema": None,
            "tag_prefix": f"dotmac-{code}-v",
            "wheel_contents": {"allowed_requires": []},
        }
    monkeypatch.setattr(release, "load_allowlist", lambda: copy.deepcopy(rows))

    def edge(code, dependencies, pins):
        row = rows[f"dotmac-{code}"]
        path = Path(row["package_dir"]) / "src" / row["import_name"] / "manifest.py"
        path.write_text(
            f'module = ModuleManifest(code="{code}", version="1.0.0", '
            f"dependencies={dependencies!r})\n"
        )
        row["module_dependencies"] = pins

    return rows, edge


def test_real_fulfillment_closure_and_shared_root_are_deduplicated(release):
    closure = release._module_closure(
        ["dotmac-fulfillment==0.1.0a2", "dotmac-durable-timers==0.1.0a1"]
    )
    assert [entry["code"] for entry in closure] == ["durable_timers", "fulfillment"]
    assert closure[1]["dependencies"] == ("durable_timers",)
    assert closure[0]["version"] == "0.1.0a1"
    assert (
        "dotmac-durable-timers" not in closure[1]["wheel_contents"]["allowed_requires"]
    )
    assert [
        path.name for path in release._smoke_dependencies(closure, "dotmac-fulfillment")
    ] == ["dotmac-durable-timers"]


def test_dependency_free_and_existing_python_input(release):
    closure = release._module_closure(["dotmac-brand-profiles==0.1.0a1"])
    assert [entry["code"] for entry in closure] == ["brand_profiles"]
    assert [
        path.name
        for path in release._smoke_dependencies(closure, "dotmac-brand-profiles")
    ] == ["dotmac-ui"]


@pytest.mark.parametrize(
    ("dependencies", "pins", "message"),
    [
        (("timer",), {}, "policy disagrees"),
        ((), {"timer": "dotmac-timer==1.0.0"}, "policy disagrees"),
        (("timer",), {"timer": "dotmac-timer>=1.0.0"}, "exact pin"),
        (("timer",), {"timer": "dotmac-missing==1.0.0"}, "not an allowlisted"),
        (("missing",), {"missing": "dotmac-timer==1.0.0"}, "declares code"),
        (("timer", "timer"), {"timer": "dotmac-timer==1.0.0"}, "duplicate"),
    ],
)
def test_refuses_unreviewed_or_invalid_edges(
    release, policy, dependencies, pins, message
):
    _, edge = policy
    edge("root", dependencies, pins)
    with pytest.raises(release.ReleaseRefused, match=message):
        release._module_closure(["dotmac-root==1.0.0"])


def test_refuses_conflicting_explicit_and_transitive_pin(release, policy):
    _, edge = policy
    edge("root", ("timer",), {"timer": "dotmac-timer==1.0.0"})
    with pytest.raises(release.ReleaseRefused, match="conflicting pins"):
        release._module_closure(["dotmac-root==1.0.0", "dotmac-timer==2.0.0"])


def test_refuses_cycles(release, policy):
    _, edge = policy
    edge("root", ("timer",), {"timer": "dotmac-timer==1.0.0"})
    edge("timer", ("root",), {"root": "dotmac-root==1.0.0"})
    with pytest.raises(release.ReleaseRefused, match="cycle"):
        release._module_closure(["dotmac-root==1.0.0"])


def test_refuses_ambiguous_manifest_code(release, policy):
    rows, _ = policy
    rows["dotmac-alias"] = rows["dotmac-timer"]
    with pytest.raises(release.ReleaseRefused, match="duplicate module code"):
        release._module_closure(["dotmac-root==1.0.0"])


def test_real_registry_still_refuses_fulfillment_without_timer():
    from dotmac_fulfillment import module
    from dotmac_kernel.modules import MissingModuleDependencyError, ModuleRegistry

    with pytest.raises(MissingModuleDependencyError, match="durable_timers"):
        ModuleRegistry([module])


def test_registry_installs_same_exact_closure(release, monkeypatch, tmp_path):
    commands, registered = [], []
    monkeypatch.setattr(release, "_registry_wheels", lambda *_args: [])
    monkeypatch.setattr(
        release, "_venv", lambda _: (tmp_path / "python", tmp_path / "pip")
    )
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda cmd, **_: (commands.append(cmd), SimpleNamespace(returncode=0))[1],
    )
    monkeypatch.setattr(
        release, "_verify_installed", lambda *args: registered.append(args)
    )
    release.cmd_verify_registry(
        argparse.Namespace(
            pin=["dotmac-fulfillment==0.1.0a2"],
            kernel="0.1.0a106",
            index="https://example.invalid/simple",
        )
    )
    assert commands[0][:5] == [
        str(tmp_path / "pip"),
        "install",
        "--quiet",
        "--no-index",
        "--no-deps",
    ]
    assert [entry["code"] for entry in registered[0][1]] == [
        "durable_timers",
        "fulfillment",
    ]


@pytest.fixture(scope="module")
def installed_release(tmp_path_factory):
    """Hosted qualifier supplies genuine candidate/published wheel bindings.

    Normal unit CI does not have protected registry access and must report this
    coverage UNQUALIFIED. The separate hosted qualification binds published
    Kernel106/Timers-a1 and the candidate Fulfillment-a2 wheel explicitly.
    """
    import os
    import subprocess
    import sys

    supplied = os.environ.get("MODULE_RELEASE_CLOSURE_WHEEL_DIR")
    if not supplied:
        pytest.skip("UNQUALIFIED: hosted MODULE_RELEASE_CLOSURE_WHEEL_DIR not supplied")
    wheels = sorted(Path(supplied).resolve().glob("*.whl"))
    assert (
        len(wheels) == 3
    ), "qualification requires exactly kernel, timer and candidate wheels"
    assert {wheel.name.split("-")[0] for wheel in wheels} == {
        "dotmac_kernel",
        "dotmac_durable_timers",
        "dotmac_fulfillment",
    }
    expected_versions = {
        "dotmac_kernel": "0.1.0a106",
        "dotmac_durable_timers": "0.1.0a1",
        "dotmac_fulfillment": "0.1.0a2",
    }
    assert all(
        wheel.name.split("-")[1] == expected_versions[wheel.name.split("-")[0]]
        for wheel in wheels
    )
    directory = tmp_path_factory.mktemp("logical-closure-installed")
    subprocess.run([sys.executable, "-m", "venv", str(directory / "venv")], check=True)
    python = (
        directory
        / "venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "release_module_hosted", ROOT / "scripts" / "release_module.py"
    )
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    closure = release._module_closure(["dotmac-fulfillment==0.1.0a2"])
    permitted, owner_policy = release._module_wheel_policy(closure)
    pip = python.parent / ("pip.exe" if sys.platform == "win32" else "pip")
    staged = release._stage_wheels(
        pip,
        directory / "wheelhouse",
        pins=[],
        local=wheels,
        permitted=permitted,
        owner_policy=owner_policy,
        private_index=None,
    )
    release._offline_install(pip, staged, directory)
    return python, directory, wheels


def test_genuine_installed_fulfillment_timer_closure(release, installed_release):
    python, directory, wheels = installed_release
    import subprocess

    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            release._PROVENANCE.format(
                expected={
                    release._wheel_distribution(wheel): str(wheel) for wheel in wheels
                },
                checkout=str(ROOT),
            ),
        ],
        cwd=directory,
        check=True,
    )
    closure = release._module_closure(["dotmac-fulfillment==0.1.0a2"])
    release._verify_installed(python, release._installed_targets(closure))


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "0.1.0a999"),
        ("code", "unexpected_code"),
        ("dependencies", ()),
        ("db_schema", "mod_wrong"),
    ],
)
def test_installed_manifest_must_independently_match_plan(
    release, installed_release, field, value
):
    import subprocess

    python, directory, _ = installed_release
    targets = release._installed_targets(
        release._module_closure(["dotmac-fulfillment==0.1.0a2"])
    )
    targets[-1][field] = value
    result = subprocess.run(
        [str(python), "-I", "-c", release._REGISTER.format(names=targets)],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "AssertionError" in result.stderr


def test_genuine_checkout_import_refused(release, installed_release):
    import subprocess

    python, directory, _ = installed_release
    targets = release._installed_targets(
        release._module_closure(["dotmac-fulfillment==0.1.0a2"])
    )
    source = ROOT / "packages" / "dotmac-fulfillment" / "src"
    script = (
        f"import sys; sys.path.insert(0, {str(source)!r})\n"
        + release._REGISTER.format(names=targets)
    )
    result = subprocess.run(
        [str(python), "-I", "-c", script],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "AssertionError" in result.stderr


def test_required_native_extension_cannot_be_missing(release, installed_release):
    import subprocess

    python, directory, _ = installed_release
    # Sensitivity proof: enrollment of a pure wheel must refuse a missing
    # extension rather than silently running its Python implementation.
    script = release._NATIVE_REGISTER.format(
        names=["dotmac_fulfillment"], checkout=str(ROOT)
    )
    result = subprocess.run(
        [str(python), "-I", "-c", script],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr


def test_registry_root_version_does_not_have_to_equal_checkout(release):
    closure = release._module_closure(["dotmac-fulfillment==0.1.0a1"])
    assert closure[-1]["version"] == "0.1.0a1"
    assert closure[-1]["dependencies"] == ("durable_timers",)


def _smoke_wheels(release, tmp_path):
    import zipfile

    closure = release._module_closure(["dotmac-fulfillment==0.1.0a2"])
    wheels = []
    for member in closure:
        name = member["distribution"].replace("-", "_")
        version = member["version"]
        wheel = tmp_path / f"{name}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                f"{name}-{version}.dist-info/METADATA",
                f"Name: {member['distribution']}\nVersion: {version}\n",
            )
        wheels.append(wheel)
    return closure, wheels


@pytest.mark.parametrize(
    "violation", ["missing", "extra", "duplicate", "wrong-version"]
)
def test_wheel_inputs_are_exact_reviewed_closure(release, tmp_path, violation):
    closure, wheels = _smoke_wheels(release, tmp_path)
    entry = release.resolve("dotmac-fulfillment")
    # Positive sensitivity anchor precedes each independently planted defect.
    release._verify_smoke_inputs(entry, closure, wheels, kernel=False)
    if violation == "missing":
        wheels.pop(0)
    elif violation == "extra":
        import zipfile

        extra = tmp_path / "dotmac_unknown-1.0.0-py3-none-any.whl"
        with zipfile.ZipFile(extra, "w") as archive:
            archive.writestr(
                "dotmac_unknown-1.0.0.dist-info/METADATA",
                "Name: dotmac-unknown\nVersion: 1.0.0\n",
            )
        wheels.append(extra)
    elif violation == "duplicate":
        wheels.append(wheels[0])
    else:
        wheels[0] = wheels[0].rename(
            tmp_path / "dotmac_durable_timers-0.1.0a999-py3-none-any.whl"
        )
    with pytest.raises(release.ReleaseRefused):
        release._verify_smoke_inputs(entry, closure, wheels, kernel=False)


def test_all_closure_native_expectations_reach_installed_check(
    release, monkeypatch, tmp_path
):
    original = release._native_package
    monkeypatch.setattr(
        release,
        "_native_package",
        lambda entry: entry["import_name"]
        if entry["code"] == "durable_timers"
        else original(entry),
    )
    calls = []
    monkeypatch.setattr(release, "_registry_wheels", lambda *_args: [])
    monkeypatch.setattr(
        release, "_venv", lambda _: (tmp_path / "python", tmp_path / "pip")
    )
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(release, "_verify_installed", lambda *args: calls.append(args))
    release.cmd_verify_registry(
        argparse.Namespace(
            pin=["dotmac-fulfillment==0.1.0a2"],
            kernel="0.1.0a106",
            index="https://example.invalid/simple",
        )
    )
    assert calls[0][2] == ["dotmac_durable_timers"]


def _write_registry_wheel(directory, name, version, requires=()):
    import zipfile

    stem = f"{name.replace('-', '_')}-{version}"
    path = directory / f"{stem}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{stem}.dist-info/METADATA",
            f"Name: {name}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires),
        )
    return path


def test_registry_python_requirements_come_from_private_artifact_not_head(
    release, monkeypatch, tmp_path
):
    closure = release._module_closure(["dotmac-brand-profiles==0.1.0a1"])
    requests = []

    def download(pip, name, specifier, index, directory):
        requests.append((name, specifier, index))
        directory.mkdir(parents=True)
        if name == "dotmac-brand-profiles":
            return _write_registry_wheel(
                directory,
                name,
                "0.1.0a1",
                (
                    "dotmac-kernel (>=0.1.0a106)",
                    "dotmac-ui (>=0.1.0a7)",
                ),
            )
        if name == "dotmac-kernel":
            return _write_registry_wheel(directory, name, "0.1.0a106")
        assert name == "dotmac-ui"
        # HEAD declares UI a8; the actual artifact's range can resolve a7.
        return _write_registry_wheel(directory, name, "0.1.0a7")

    monkeypatch.setattr(release, "_download_one", download)
    wheels = release._registry_wheels(
        argparse.Namespace(kernel="0.1.0a106", index="https://private.invalid/simple"),
        closure,
        tmp_path / "pip",
        tmp_path / "wheels",
    )
    assert [release._wheel_identity(wheel) for wheel in wheels] == [
        ("dotmac-brand-profiles", "0.1.0a1"),
        ("dotmac-kernel", "0.1.0a106"),
        ("dotmac-ui", "0.1.0a7"),
    ]
    assert all(index == "https://private.invalid/simple" for _, _, index in requests)
    assert not any("0.1.0a8" in spec for _, spec, _ in requests)
