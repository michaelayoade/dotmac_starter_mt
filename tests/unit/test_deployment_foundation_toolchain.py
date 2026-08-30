"""The trust anchor must not be whatever `PATH` says.

`ComposeHostEffects` shelled out to `docker`, `git` and `pg_dump` as bare
names. A bare name is not an identity — it is a lookup resolved at exec time
against an environment variable.

These are not convenience calls. `docker image inspect` is how this facility
learns which digest a host is running; `git status --porcelain` is how it learns
whether the deploy tree was modified. They are **evidence-gathering** commands,
so whoever controls which binary answers them controls what the deployment
believes, and every downstream check inherits that answer.

Two layers are tested separately because they fail differently:

- `require_absolute_tool` is a pure string check. It runs at construction, in
  every environment including CI, and removes the `PATH` class entirely.
- `resolve_tool` needs a real filesystem: exists, regular, executable, and not
  replaceable by anyone but the owner. It runs on the production path.

Neither is sufficient alone. Absolute-path-only still lets someone replace the
file at that path; integrity-only leaves `PATH` choosing which file is checked.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.toolchain import (
    DEFAULT_TOOLS,
    require_absolute_tool,
    resolve_tool,
)

# ── layer 1: no PATH lookups ────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["docker", "git", "./docker", "bin/docker", ""])
def test_a_path_resolved_name_is_refused(value: str) -> None:
    with pytest.raises(PreconditionFailed):
        require_absolute_tool(value, what="docker_bin")


def test_a_traversing_absolute_path_is_refused() -> None:
    with pytest.raises(PreconditionFailed, match="traverses"):
        require_absolute_tool("/usr/bin/../bin/docker", what="docker_bin")


def test_an_absolute_path_is_accepted() -> None:
    """Positive control — the check must not refuse everything."""
    assert require_absolute_tool("/usr/bin/docker", what="d") == "/usr/bin/docker"


def test_pg_dump_is_out_of_scope_and_stays_that_way() -> None:
    """A container-resolved command is not a host trust anchor.

    `pg_dump` runs as `docker compose exec -T <db> pg_dump …`, so the
    container's PATH resolves it and the host's cannot influence which binary
    it is. The premise this module rests on — an attacker who controls PATH
    controls the evidence — does not hold there.

    Pinning it would also be wrong on its own terms: `/usr/bin/pg_dump` need
    not exist in a postgres image, which commonly ships it under a versioned
    directory. The image digest owns that binary's identity.

    Asserted rather than left implicit, because the natural next edit is to
    "finish the job" by adding it — and that edit breaks real backups on hosts
    whose image lays pg_dump out differently, which is a worse failure than the
    one it imagines it is preventing.
    """
    from dotmac_deployment_foundation.providers.compose_host import ComposeHostEffects
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    assert "pg_dump" not in DEFAULT_TOOLS
    spec = ProductDeploymentSpec.load("scripts/exposure-rehearsal/product.toml")
    effects = ComposeHostEffects(spec, Path("/tmp"), pg_dump_bin="pg_dump")  # noqa: S108
    assert effects._pg_dump_bin == "pg_dump"


def test_every_shipped_default_is_absolute() -> None:
    """A default of `docker` would protect only deployments that already thought
    about this, which is the opposite of a default."""
    for name, value in DEFAULT_TOOLS.items():
        assert value.startswith("/"), f"{name} default is a PATH lookup"


def test_the_provider_refuses_a_bare_binary_name(tmp_path: Path) -> None:
    """The enforcement point, not just the helper.

    Constructed with a real spec so the refusal comes from the tool check
    rather than from spec parsing.
    """
    from dotmac_deployment_foundation.providers.compose_host import ComposeHostEffects
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    spec = ProductDeploymentSpec.load("scripts/exposure-rehearsal/product.toml")
    with pytest.raises(PreconditionFailed, match="PATH would have to resolve"):
        ComposeHostEffects(spec, tmp_path, docker_bin="docker")


def test_the_provider_accepts_an_absolute_binary(tmp_path: Path) -> None:
    """Positive control for the enforcement point."""
    from dotmac_deployment_foundation.providers.compose_host import ComposeHostEffects
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    spec = ProductDeploymentSpec.load("scripts/exposure-rehearsal/product.toml")
    ComposeHostEffects(spec, tmp_path, docker_bin="/usr/bin/docker")


# ── layer 2: integrity on a real filesystem ─────────────────────────────────


def _tool(directory: Path, *, mode: int = 0o755, body: bytes = b"#!/bin/sh\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)
    path = directory / "docker"
    path.write_bytes(body)
    path.chmod(mode)
    return path


def test_a_well_formed_tool_resolves(tmp_path: Path) -> None:
    """Positive control. Without it every refusal below proves nothing."""
    path = _tool(tmp_path / "bin")
    assert resolve_tool(str(path), what="docker_bin") == str(path)


def test_a_missing_tool_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PreconditionFailed, match="cannot stat"):
        resolve_tool(str(tmp_path / "bin" / "docker"), what="docker_bin")


def test_a_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    with pytest.raises(PreconditionFailed, match="not a regular file"):
        resolve_tool(str(tmp_path / "bin"), what="docker_bin")


def test_a_non_executable_tool_is_refused(tmp_path: Path) -> None:
    path = _tool(tmp_path / "bin", mode=0o644)
    with pytest.raises(PreconditionFailed, match="not executable"):
        resolve_tool(str(path), what="docker_bin")


@pytest.mark.parametrize("extra", [stat.S_IWGRP, stat.S_IWOTH])
def test_a_tool_writable_by_others_is_refused(tmp_path: Path, extra: int) -> None:
    """Group counts. "Only trusted people are in that group" is a claim about
    staffing, not about the filesystem."""
    path = _tool(tmp_path / "bin", mode=0o755 | extra)
    with pytest.raises(PreconditionFailed, match="writable by group or other"):
        resolve_tool(str(path), what="docker_bin")


@pytest.mark.parametrize("extra", [stat.S_IWGRP, stat.S_IWOTH])
def test_a_tool_in_a_writable_directory_is_refused(tmp_path: Path, extra: int) -> None:
    """The check that looks right and isn't, if you only check the file.

    `rename(2)` needs write permission on the DIRECTORY, so a world-writable
    `/usr/local/bin` defeats a mode-755 root-owned binary inside it.
    """
    directory = tmp_path / "bin"
    path = _tool(directory)
    directory.chmod(0o755 | extra)
    try:
        with pytest.raises(PreconditionFailed, match="parent directory"):
            resolve_tool(str(path), what="docker_bin")
    finally:
        directory.chmod(0o755)


# ── optional digest pinning ─────────────────────────────────────────────────


def test_a_matching_digest_pin_passes(tmp_path: Path) -> None:
    import hashlib

    body = b"#!/bin/sh\necho hi\n"
    path = _tool(tmp_path / "bin", body=body)
    digest = hashlib.sha256(body).hexdigest()
    assert resolve_tool(str(path), what="d", expected_sha256=digest) == str(path)
    assert resolve_tool(str(path), what="d", expected_sha256=f"sha256:{digest}")


def test_a_mismatched_digest_pin_is_refused(tmp_path: Path) -> None:
    path = _tool(tmp_path / "bin")
    with pytest.raises(PreconditionFailed, match="not the pinned"):
        resolve_tool(str(path), what="d", expected_sha256="0" * 64)


def test_no_digest_pin_is_not_an_error(tmp_path: Path) -> None:
    """Deliberate: every legitimate upgrade changes the digest, and a guard
    that fails on routine patching gets disabled. Ownership and mode checks
    survive upgrades and are always on."""
    path = _tool(tmp_path / "bin")
    assert resolve_tool(str(path), what="d", expected_sha256="") == str(path)


def test_the_ownership_checks_still_apply_without_a_digest(tmp_path: Path) -> None:
    """The always-on half must not be skipped just because no pin was given."""
    path = _tool(tmp_path / "bin", mode=0o757)
    with pytest.raises(PreconditionFailed, match="writable by group or other"):
        resolve_tool(str(path), what="d", expected_sha256="")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the execute bit")
def test_root_would_skew_the_executable_check() -> None:
    """Documents why the suite is not run as root rather than asserting behaviour."""
    assert os.geteuid() != 0
