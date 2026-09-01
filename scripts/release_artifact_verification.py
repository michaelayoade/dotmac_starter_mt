#!/usr/bin/env python3
"""Provider-neutral release artifact equality and install decision."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path


class ReleaseArtifactRefused(ValueError):
    """The supplied observations do not prove one immutable release."""


@dataclass(frozen=True)
class RetainedBuildFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class RegistryFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class CleanInstallObservation:
    name: str
    distribution: str
    version: str
    dependencies_resolved: bool
    metadata_matches: bool
    import_passed: bool


def canonical_kernel_filenames(version: str) -> frozenset[str]:
    component = r"(?:0|[1-9][0-9]*)"
    if (
        re.fullmatch(
            rf"{component}\.{component}\.{component}(?:a|b|rc)[1-9][0-9]*", version
        )
        is None
    ):
        raise ReleaseArtifactRefused(
            "kernel version is not a canonical public prerelease"
        )
    return frozenset(
        {
            f"dotmac_kernel-{version}-py3-none-any.whl",
            f"dotmac_kernel-{version}.tar.gz",
        }
    )


def observe_file(path: Path, kind: type[RetainedBuildFile] | type[RegistryFile]):
    payload = path.read_bytes()
    return kind(
        name=path.name,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _one_by_name(items: Iterable[object], *, label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        name = getattr(item, "name", None)
        if not isinstance(name, str) or not name:
            raise ReleaseArtifactRefused(f"{label} contains an unnamed observation")
        if name in result:
            raise ReleaseArtifactRefused(f"{label} contains duplicate {name}")
        result[name] = item
    return result


def verify_release_artifacts(
    *,
    expected_names: frozenset[str],
    retained: Iterable[RetainedBuildFile],
    registry: Iterable[RegistryFile],
    installs: Iterable[CleanInstallObservation],
    distribution: str,
    version: str,
) -> dict[str, object]:
    if len(expected_names) != 2:
        raise ReleaseArtifactRefused("a release must enumerate exactly wheel and sdist")
    build_by_name = _one_by_name(retained, label="retained build")
    registry_by_name = _one_by_name(registry, label="registry read-back")
    install_by_name = _one_by_name(installs, label="clean installs")
    for label, observed in (
        ("retained build", build_by_name),
        ("registry read-back", registry_by_name),
        ("clean installs", install_by_name),
    ):
        if set(observed) != expected_names:
            raise ReleaseArtifactRefused(
                f"{label} names {sorted(observed)}, expected {sorted(expected_names)}"
            )

    files: list[dict[str, object]] = []
    for name in sorted(expected_names):
        built = build_by_name[name]
        read_back = registry_by_name[name]
        installed = install_by_name[name]
        assert isinstance(built, RetainedBuildFile)
        assert isinstance(read_back, RegistryFile)
        assert isinstance(installed, CleanInstallObservation)
        if (built.size, built.sha256) != (read_back.size, read_back.sha256):
            raise ReleaseArtifactRefused(f"registry bytes differ for {name}")
        if installed.distribution != distribution or installed.version != version:
            raise ReleaseArtifactRefused(f"installed metadata differs for {name}")
        if not all(
            (
                installed.dependencies_resolved,
                installed.metadata_matches,
                installed.import_passed,
            )
        ):
            raise ReleaseArtifactRefused(f"clean install did not pass for {name}")
        files.append(
            {
                "name": name,
                "size": built.size,
                "build_sha256": built.sha256,
                "registry_sha256": read_back.sha256,
                "byte_equal": True,
                "clean_install": asdict(installed),
            }
        )
    return {"verdict": "verified", "files": files}


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
