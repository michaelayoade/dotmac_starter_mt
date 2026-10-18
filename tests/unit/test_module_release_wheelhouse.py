"""Hosted-only canaries for binary staging before offline installation."""

from __future__ import annotations

import argparse
import importlib
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def release(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("release_module")


def wheel(directory, name, version="1.0.0", requires=(), extras=()):
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{name.replace('-', '_')}-{version}"
    path = directory / f"{stem}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{stem}.dist-info/METADATA",
            f"Name: {name}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {edge}\n" for edge in requires)
            + "".join(f"Provides-Extra: {extra}\n" for extra in extras),
        )
    return path


def stage(release, tmp_path, root):
    return release._stage_wheels(
        tmp_path / "pip",
        tmp_path / "staged",
        pins=[],
        local=[root],
        permitted={"dotmac-root", "dotmac-child"},
        owner_policy={"dotmac-root": {"dotmac-child"}},
        private_index=None,
    )


@pytest.mark.parametrize(
    "edge",
    [
        "dotmac-child[feature]>=1",
        "dotmac-child>=1; extra == 'feature'",
        "dotmac-child>=1; python_version >= '0'",
        "dotmac-child>=1; python_version < '0'",
        "dotmac-child>=1; sys_platform == 'linux'",
        "dotmac-child>=1; sys_platform == 'never'",
        "dotmac-child @ https://unapproved.invalid/child.whl",
        "dotmac-child @ https://unapproved.invalid/source.tar.gz ; "
        "python_version < '0'",
        "vendor @ https://unapproved.invalid/source.tar.gz ; python_version < '0'",
    ],
)
def test_unsupported_edge_refused_before_any_dependency_handoff(
    release, monkeypatch, tmp_path, edge
):
    root = wheel(tmp_path / "root", "dotmac-root", requires=[edge])
    requests = []
    monkeypatch.setattr(release, "_download_one", lambda *args: requests.append(args))
    with pytest.raises(release.ReleaseRefused, match="Unsupported") as error:
        stage(release, tmp_path, root)
    assert requests == []
    assert "https://" not in str(error.value)


@pytest.mark.parametrize("name", ["dotmac-child", "vendor"])
def test_malformed_url_marker_refused_before_any_dependency_handoff(
    release, monkeypatch, tmp_path, name
):
    # A URL needs whitespace before its marker delimiter under PEP 508.
    # Preserve the malformed cases separately from valid unsupported URLs.
    edge = f"{name} @ https://unapproved.invalid/source.tar.gz; python_version < '0'"
    root = wheel(tmp_path / "root", "dotmac-root", requires=[edge])
    requests = []
    monkeypatch.setattr(release, "_download_one", lambda *args: requests.append(args))
    with pytest.raises(release.ReleaseRefused, match="malformed") as error:
        stage(release, tmp_path, root)
    assert requests == []
    assert "https://" not in str(error.value)


def test_third_party_extra_context_and_inactive_marker(release, monkeypatch, tmp_path):
    root = wheel(tmp_path / "root", "dotmac-root", requires=["vendor[feature]>=1"])
    requests = []

    def download(pip, name, specifier, index, output):
        requests.append((name, specifier, index))
        if name == "vendor":
            return wheel(
                output,
                name,
                requires=[
                    "leaf>=1; extra == 'feature'",
                    "absent>=1; extra == 'other'",
                    "inactive>=1; python_version < '0'",
                ],
                extras=["feature", "other"],
            )
        assert name == "leaf"
        return wheel(output, name)

    monkeypatch.setattr(release, "_download_one", download)
    staged = stage(release, tmp_path, root)
    assert {release._wheel_identity(path)[0] for path in staged} == {
        "dotmac-root",
        "vendor",
        "leaf",
    }
    assert {name for name, _, _ in requests} == {"vendor", "leaf"}
    assert all(
        "[" not in specifier and ";" not in specifier for _, specifier, _ in requests
    )


def test_transitive_source_url_refused_before_pip_can_prepare_it(
    release, monkeypatch, tmp_path
):
    root = wheel(tmp_path / "root", "dotmac-root", requires=["vendor>=1"])
    requests = []

    def download(pip, name, specifier, index, output):
        requests.append(name)
        assert name == "vendor"
        return wheel(
            output, name, requires=["source @ https://unapproved.invalid/source.tar.gz"]
        )

    monkeypatch.setattr(release, "_download_one", download)
    with pytest.raises(release.ReleaseRefused, match="direct-reference"):
        stage(release, tmp_path, root)
    assert requests == ["vendor"]


@pytest.mark.parametrize("edge", ["dotmac-unknown>=1", "dotmac-child[feature]>=1"])
def test_third_party_cannot_introduce_unapproved_first_party(
    release, monkeypatch, tmp_path, edge
):
    root = wheel(tmp_path / "root", "dotmac-root", requires=["vendor>=1"])
    requests = []

    def download(pip, name, specifier, index, output):
        requests.append(name)
        assert name == "vendor"
        return wheel(output, name, requires=[edge])

    monkeypatch.setattr(release, "_download_one", download)
    with pytest.raises(release.ReleaseRefused):
        stage(release, tmp_path, root)
    assert requests == ["vendor"]


def test_public_resolution_substituting_first_party_refuses_before_install(
    release, monkeypatch, tmp_path
):
    root = wheel(tmp_path / "root", "dotmac-root", requires=["vendor>=1"])
    monkeypatch.setattr(
        release,
        "_download_one",
        lambda *args: wheel(tmp_path / "substitute", "dotmac-child"),
    )
    with pytest.raises(release.ReleaseRefused, match="does not satisfy"):
        stage(release, tmp_path, root)


def test_conflicting_local_pin_refuses(release, tmp_path):
    root = wheel(tmp_path / "root", "dotmac-root", requires=["dotmac-child>=2"])
    child = wheel(tmp_path / "child", "dotmac-child")
    with pytest.raises(release.ReleaseRefused, match="does not satisfy"):
        release._stage_wheels(
            tmp_path / "pip",
            tmp_path / "staged",
            pins=[],
            local=[root, child],
            permitted={"dotmac-root", "dotmac-child"},
            owner_policy={"dotmac-root": {"dotmac-child"}},
            private_index=None,
        )


def test_nonconverging_candidates_fail_closed(release, monkeypatch, tmp_path):
    root = wheel(tmp_path / "root", "dotmac-root", requires=["vendor>=1"])

    def download(pip, name, specifier, index, output):
        assert name == "vendor"
        return wheel(
            output,
            name,
            "1.0.0" if "<2" in specifier else "2.0.0",
            requires=["vendor>=1" if "<2" in specifier else "vendor<2"],
        )

    monkeypatch.setattr(release, "_download_one", download)
    with pytest.raises(release.ReleaseRefused, match="did not converge"):
        stage(release, tmp_path, root)


@pytest.mark.parametrize(
    "violation", ["unavailable", "sdist", "extra-wheel", "substitute"]
)
def test_one_download_is_binary_only_no_deps_and_fails_closed(
    release, monkeypatch, tmp_path, violation
):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert "--no-deps" in command and "--only-binary=:all:" in command
        assert "--extra-index-url" not in command
        assert "PIP_EXTRA_INDEX_URL" not in kwargs["env"]
        assert kwargs["env"]["PIP_CONFIG_FILE"] != ""
        assert kwargs["capture_output"] and not kwargs["check"]
        output = Path(command[command.index("--dest") + 1])
        if violation == "unavailable":
            return SimpleNamespace(returncode=1)
        if violation == "sdist":
            (output / "vendor.tar.gz").write_bytes(b"must never prepare metadata")
        elif violation == "extra-wheel":
            wheel(output, "vendor")
            wheel(output, "unexpected")
        else:
            wheel(output, "dotmac-child")
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://unapproved.invalid/simple")
    monkeypatch.setattr(release.subprocess, "run", run)
    with pytest.raises(release.ReleaseRefused):
        release._download_one(
            tmp_path / "pip",
            "vendor",
            ">=1",
            "https://public.invalid/simple",
            tmp_path / "out",
        )
    assert len(commands) == 1


def test_offline_install_never_resolves_or_reaches_an_index(
    release, monkeypatch, tmp_path
):
    candidate = wheel(tmp_path / "wheel", "dotmac-root")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["capture_output"] and not kwargs["check"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release.subprocess, "run", run)
    release._offline_install(tmp_path / "pip", [candidate], tmp_path)
    assert commands == [
        [
            str(tmp_path / "pip"),
            "install",
            "--quiet",
            "--no-index",
            "--no-deps",
            str(candidate),
        ]
    ]


def test_smoke_refusal_happens_before_any_install(release, monkeypatch, tmp_path):
    target = wheel(
        tmp_path / "target",
        "dotmac-fulfillment",
        "0.1.0a2",
        requires=["vendor @ https://unapproved.invalid/source.tar.gz"],
    )
    kernel = wheel(tmp_path / "kernel", "dotmac-kernel", "0.1.0a106")
    timer = wheel(tmp_path / "timer", "dotmac-durable-timers", "0.1.0a1")
    commands = []
    monkeypatch.setattr(
        release, "_venv", lambda _: (tmp_path / "python", tmp_path / "pip")
    )
    monkeypatch.setattr(
        release.subprocess, "run", lambda *args, **kwargs: commands.append(args)
    )
    with pytest.raises(release.ReleaseRefused, match="direct-reference"):
        release.cmd_verify_wheel(
            argparse.Namespace(
                distribution="dotmac-fulfillment",
                dist=str(target.parent),
                kernel_dist=str(kernel.parent),
                dependency_dist=[str(timer.parent)],
                emit_dependency_manifest=None,
            )
        )
    assert commands == []


def test_verifier_parser_is_exact_hashlocked_dependency_free(release):
    from importlib import metadata

    release._packaging_api()
    assert metadata.version("packaging") == "26.2"
    assert not metadata.requires("packaging")
    lock = ROOT / ".github" / "bootstrap" / "release-verifier-requirements-py312.txt"
    block = lock.read_text().splitlines(keepends=True)[1:]
    assert (
        "".join(block)
        in (
            ROOT / ".github" / "bootstrap" / "poetry-requirements-py312.txt"
        ).read_text()
    )
