"""Hosted-only candidate qualification canaries; fakes prove orchestration only."""

from __future__ import annotations

import argparse
import importlib
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def release(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("release_module")


def wheel(directory, name, version, requires=()):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
            f"Name: {name}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {value}\n" for value in requires),
        )
    return path


@pytest.fixture()
def setup(release, monkeypatch, tmp_path):
    """Boundary doubles are NOT native, registry or publication evidence."""
    candidate = wheel(tmp_path / "dist", "dotmac-root", "2")
    sibling = wheel(tmp_path / "published", "dotmac-sibling", "1")
    kernel = wheel(tmp_path / "published", "dotmac-kernel", "3")
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text("source")
    (source / "EXTRACTION.toml").write_text("dossier")
    manifest = source / "src" / "dotmac_root" / "manifest.py"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("module = None\n")
    entry = {
        "distribution": "dotmac-root",
        "version": "2",
        "kernel_floor": "3",
        "package_path": source,
        "import_name": "dotmac_root",
        "manifest_attr": "module",
        "code": "root",
        "dependencies": ("sibling",),
        "db_schema": None,
        "native_package": None,
        "wheel_contents": {"allowed_requires": ["dotmac-kernel"]},
    }
    dependency = {**entry, "distribution": "dotmac-sibling", "version": "1"}
    closure = [entry, dependency]
    monkeypatch.setattr(release, "resolve", lambda _: entry)
    monkeypatch.setattr(release, "_declared", lambda _: {"version": "2"})
    monkeypatch.setattr(release, "_module_closure", lambda _: closure)
    monkeypatch.setattr(release, "cmd_inspect", lambda _: None)
    monkeypatch.setattr(release, "_venv", lambda _: (Path("python"), Path("pip")))
    monkeypatch.setattr(
        release, "_candidate_source_binding", lambda _: {"source": "a" * 64}
    )
    events = []

    def stage(_pip, _directory, **kwargs):
        events.append(("stage", kwargs))
        return [kwargs["local"][0], sibling, kernel]

    monkeypatch.setattr(release, "_stage_wheels", stage)
    monkeypatch.setattr(
        release, "_inspect_native", lambda *_: events.append(("native",))
    )
    monkeypatch.setattr(
        release, "_offline_install", lambda *_: events.append(("install",))
    )
    monkeypatch.setattr(
        release, "_candidate_installed_check", lambda *_: events.append(("verify",))
    )
    args = argparse.Namespace(
        distribution="dotmac-root",
        dist=str(candidate.parent),
        index="private",
        kernel="",
        output=str(tmp_path / "binding"),
    )
    return args, candidate, sibling, kernel, events


def test_candidate_fixed_and_logical_published_pins(release, setup):
    args, candidate, _, _, events = setup
    release.cmd_qualify_candidate(args)
    options = events[0][1]
    assert options["local"][0].name == candidate.name
    assert options["pins"] == ["dotmac-sibling==1", "dotmac-kernel==3"]
    assert not any(pin.startswith("dotmac-root==") for pin in options["pins"])
    assert options["private_index"] == "private"
    assert [event[0] for event in events] == [
        "stage",
        "native",
        "native",
        "install",
        "verify",
    ]
    receipt = json.loads(
        (Path(args.output) / "CandidateQualification.v1.json").read_text()
    )
    assert receipt["candidate"]["sha256"] == release._sha256(candidate)
    assert receipt["source"] == {"source": "a" * 64}
    assert {row["name"] for row in receipt["wheels"]} == {
        "dotmac-root",
        "dotmac-sibling",
        "dotmac-kernel",
    }
    for row in receipt["wheels"]:
        assert release._sha256(Path(args.output) / row["filename"]) == row["sha256"]
    assert "private" not in json.dumps(receipt)
    assert receipt["claim"] == "candidate-installation-only"


@pytest.mark.parametrize("name,version", [("dotmac-other", "2"), ("dotmac-root", "9")])
def test_substituted_target_refused_before_resolution(release, setup, name, version):
    args, candidate, _, _, events = setup
    candidate.unlink()
    wheel(candidate.parent, name, version)
    with pytest.raises(release.ReleaseRefused, match="target identity"):
        release.cmd_qualify_candidate(args)
    assert events == []
    assert not Path(args.output).exists()


@pytest.mark.parametrize("extra", ["extra.whl", "notes.txt"])
def test_malformed_target_directory_refused(release, setup, extra):
    args, candidate, _, _, events = setup
    (candidate.parent / extra).write_text("unexpected")
    with pytest.raises(release.ReleaseRefused, match="exactly one regular wheel"):
        release.cmd_qualify_candidate(args)
    assert events == []


def test_missing_logical_dependency_refused(release, setup, monkeypatch):
    args, candidate, _, kernel, events = setup
    monkeypatch.setattr(
        release, "_stage_wheels", lambda *_a, **k: [k["local"][0], kernel]
    )
    with pytest.raises(release.ReleaseRefused, match="missing logical"):
        release.cmd_qualify_candidate(args)
    assert events == []
    assert not Path(args.output).exists()


@pytest.mark.parametrize(
    "boundary",
    [
        "_stage_wheels",
        "_inspect_native",
        "_offline_install",
        "_candidate_installed_check",
    ],
)
def test_failure_leaves_no_usable_receipt(release, setup, monkeypatch, boundary):
    args, candidate, _, _, _ = setup
    before = candidate.read_bytes()

    def fail(*_args, **_kwargs):
        raise release.ReleaseRefused("boundary refused")

    monkeypatch.setattr(release, boundary, fail)
    with pytest.raises(release.ReleaseRefused, match="boundary refused"):
        release.cmd_qualify_candidate(args)
    assert not Path(args.output).exists()
    assert candidate.read_bytes() == before


def test_existing_output_refused_without_overwrite(release, setup):
    args, _, _, _, events = setup
    Path(args.output).mkdir()
    sentinel = Path(args.output) / "sentinel"
    sentinel.write_text("preserved")
    with pytest.raises(release.ReleaseRefused, match="must not exist"):
        release.cmd_qualify_candidate(args)
    assert sentinel.read_text() == "preserved"
    assert events == []


def test_actual_walker_never_downloads_fixed_candidate(release, monkeypatch, tmp_path):
    candidate = wheel(tmp_path / "candidate", "dotmac-root", "2", ["dotmac-kernel>=3"])
    sibling = wheel(tmp_path / "published", "dotmac-sibling", "1")
    kernel = wheel(tmp_path / "published", "dotmac-kernel", "3")
    requests = []

    def download(_pip, name, specifier, index, _output):
        requests.append((name, specifier, index))
        assert name != "dotmac-root"
        return {"dotmac-sibling": sibling, "dotmac-kernel": kernel}[name]

    monkeypatch.setattr(release, "_download_one", download)
    staged = release._stage_wheels(
        Path("pip"),
        tmp_path / "staged",
        pins=["dotmac-sibling==1", "dotmac-kernel==3"],
        local=[candidate],
        permitted={"dotmac-root", "dotmac-sibling", "dotmac-kernel"},
        owner_policy={"dotmac-root": {"dotmac-kernel"}},
        private_index="private",
    )
    assert candidate in staged
    assert {request[0] for request in requests} == {"dotmac-sibling", "dotmac-kernel"}
    assert all(request[2] == "private" for request in requests)


def test_installed_check_captures_provenance_and_manifest_failures(
    release, monkeypatch, tmp_path
):
    import subprocess
    from types import SimpleNamespace

    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(
        release.ReleaseRefused, match="installed candidate verification failed"
    ):
        release._candidate_installed_check(Path("python"), [], {}, tmp_path)
    command, kwargs = calls[0]
    assert command[1:3] == ["-I", "-c"]
    assert "direct_url.json" in command[3]
    assert "ModuleRegistry(manifests)" in command[3]
    assert "ExtensionFileLoader" in command[3]
    assert kwargs["capture_output"] is True


def test_credentials_removed_from_all_qualifier_children_and_restored(
    release, setup, monkeypatch
):
    import os

    args, _, _, _, _ = setup
    sensitive = {
        "INDEX": "sentinel-index",
        "TWINE_PASSWORD": "sentinel-password",
        "TWINE_USERNAME": "sentinel-user",
        "FORGEJO_TOKEN": "sentinel-token",
        "PIP_INDEX_URL": "sentinel-pip",
        "PRIVATE_INDEX_URL": "sentinel-private",
    }
    for key, value in sensitive.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PUBLIC_INDEX_URL", "https://public.example/simple")
    original_stage = release._stage_wheels
    for boundary in ("cmd_inspect", "_venv", "_inspect_native", "_offline_install"):
        original = getattr(release, boundary)

        def checked(*a, _original=original, **k):
            assert not set(sensitive) & set(os.environ)
            return _original(*a, **k)

        monkeypatch.setattr(release, boundary, checked)

    def stage(*a, **k):
        assert not set(sensitive) & set(os.environ)
        assert os.environ["PUBLIC_INDEX_URL"] == "https://public.example/simple"
        return original_stage(*a, **k)

    def installed(*_a):
        assert not set(sensitive) & set(os.environ)

    monkeypatch.setattr(release, "_stage_wheels", stage)
    monkeypatch.setattr(release, "_candidate_installed_check", installed)
    release.cmd_qualify_candidate(args)
    assert all(os.environ[key] == value for key, value in sensitive.items())
    assert not any(
        value in (Path(args.output) / "CandidateQualification.v1.json").read_text()
        for value in sensitive.values()
    )


def test_staged_target_substitution_refused(release, setup, monkeypatch):
    args, candidate, sibling, kernel, _ = setup
    monkeypatch.setattr(
        release, "_stage_wheels", lambda *_a, **_k: [candidate, sibling, kernel]
    )
    with pytest.raises(release.ReleaseRefused, match="candidate substitution"):
        release.cmd_qualify_candidate(args)
    assert not Path(args.output).exists()


def test_candidate_change_after_install_refused(release, setup, monkeypatch):
    args, candidate, _, _, _ = setup

    def installed(*_a):
        candidate.write_bytes(b"substituted after install")

    monkeypatch.setattr(release, "_candidate_installed_check", installed)
    with pytest.raises(release.ReleaseRefused, match="changed during checks"):
        release.cmd_qualify_candidate(args)
    assert not Path(args.output).exists()


def test_kernel_below_closure_floor_refused(release, setup):
    args, _, _, _, events = setup
    args.kernel = "2"
    with pytest.raises(
        release.ReleaseRefused, match="below the reviewed closure floor"
    ):
        release.cmd_qualify_candidate(args)
    assert events == []
    assert not Path(args.output).exists()


@pytest.mark.parametrize("name", ["dotmac_root-2.tar.gz", "dotmac-root-2.tar.gz"])
def test_build_wheel_plus_matching_sdist_layout(release, setup, monkeypatch, name):
    # Handcrafted bytes exercise layout only; inspect is an explicit boundary
    # double. This is not an sdist build/content or genuine registry proof.
    args, candidate, _, _, _ = setup
    sdist = candidate.parent / name
    sdist.write_bytes(b"synthetic orchestration-only sdist")
    before = {path.name: path.read_bytes() for path in candidate.parent.iterdir()}
    inspections = []
    monkeypatch.setattr(release, "cmd_inspect", lambda given: inspections.append(given))
    release.cmd_qualify_candidate(args)
    assert len(inspections) == 1
    assert Path(inspections[0].dist) == candidate.parent
    assert {
        path.name: path.read_bytes() for path in candidate.parent.iterdir()
    } == before
    assert not list(Path(args.output).glob("*.tar.gz"))
    receipt = json.loads(
        (Path(args.output) / "CandidateQualification.v1.json").read_text()
    )
    assert receipt["candidate"]["sha256"] == release._sha256(candidate)


@pytest.mark.parametrize(
    "name", ["dotmac_root-9.tar.gz", "dotmac_other-2.tar.gz", "dotmac_root-2.zip"]
)
def test_wrong_sdist_refused_before_inspection(release, setup, monkeypatch, name):
    args, candidate, _, _, events = setup
    (candidate.parent / name).write_bytes(b"synthetic wrong sdist")
    inspections = []
    monkeypatch.setattr(release, "cmd_inspect", lambda given: inspections.append(given))
    with pytest.raises(release.ReleaseRefused, match="exactly one regular wheel"):
        release.cmd_qualify_candidate(args)
    assert not inspections
    assert not events
    assert not Path(args.output).exists()


@pytest.mark.parametrize(
    "kind",
    [
        "wheel-symlink",
        "sdist-symlink",
        "subdirectory",
        "duplicate-wheel",
        "duplicate-sdist",
    ],
)
def test_nonregular_or_duplicate_build_artifacts_refused(release, setup, kind):
    args, candidate, _, _, events = setup
    directory = candidate.parent
    if kind == "wheel-symlink":
        real = directory.parent / candidate.name
        candidate.rename(real)
        candidate.symlink_to(real)
    elif kind == "sdist-symlink":
        real = directory.parent / "source.tar.gz"
        real.write_bytes(b"synthetic source")
        (directory / "dotmac_root-2.tar.gz").symlink_to(real)
    elif kind == "subdirectory":
        (directory / "unexpected").mkdir()
    elif kind == "duplicate-wheel":
        wheel(directory, "dotmac-root", "3")
    else:
        (directory / "dotmac_root-2.tar.gz").write_bytes(b"first")
        (directory / "dotmac-root-2.tar.gz").write_bytes(b"second")
    with pytest.raises(release.ReleaseRefused, match="exactly one regular wheel"):
        release.cmd_qualify_candidate(args)
    assert not events
    assert not Path(args.output).exists()


@pytest.mark.parametrize(
    "boundary",
    [
        "cmd_inspect",
        "_venv",
        "_stage_wheels",
        "_offline_install",
        "_candidate_installed_check",
    ],
)
def test_credentials_restored_even_on_early_or_late_failure(
    release, setup, monkeypatch, boundary
):
    import os

    args, _, _, _, _ = setup
    sensitive = {
        "INDEX": "sentinel-index",
        "TWINE_PASSWORD": "sentinel-password",
        "FORGEJO_TOKEN": "sentinel-token",
        "PIP_INDEX_URL": "sentinel-pip",
    }
    for key, value in sensitive.items():
        monkeypatch.setenv(key, value)

    def fail(*_a, **_k):
        assert not set(sensitive) & set(os.environ)
        raise release.ReleaseRefused("boundary failure")

    monkeypatch.setattr(release, boundary, fail)
    with pytest.raises(release.ReleaseRefused):
        release.cmd_qualify_candidate(args)
    assert all(os.environ[key] == value for key, value in sensitive.items())
    assert not Path(args.output).exists()
