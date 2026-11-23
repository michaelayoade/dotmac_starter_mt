"""dotmac-deploy-agent: governed digest transfer through a loopback registry."""
# ruff: noqa: E501

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from dotmac_deploy_agent.canon import sha256_hex
from dotmac_deploy_agent.commands import CommandResult
from dotmac_deploy_agent.config import AgentConfig
from dotmac_deploy_agent.materials import ImageSpec
from dotmac_deploy_agent.refusal import Refusal
from dotmac_deploy_agent.transfer import ImageTransfer, registry_config

from tests.unit.deploy_agent_support import (
    FakeCommands,
    Keys,
    make_config,
    make_materials,
    make_rig,
)

MANIFEST = json.dumps(
    {"mediaType": "application/vnd.oci.image.manifest.v1+json"}
).encode()
INDEX = json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json"}).encode()
DIGEST = "sha256:" + sha256_hex(MANIFEST)
IMAGE_ID = "sha256:" + "f" * 64
REFERENCE = f"127.0.0.1:5000/org/app@{DIGEST}"
ARCHIVE = f"app-{DIGEST.removeprefix('sha256:')}.oci.tar"


class FakeHandle:
    def __init__(self, *, stubborn: bool = False, immortal: bool = False) -> None:
        self.code: int | None = None
        self.stubborn = stubborn  # ignores SIGTERM, dies on SIGKILL
        self.immortal = immortal  # ignores everything
        self.signals: list[str] = []

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.signals.append("TERM")
        if not self.stubborn and not self.immortal:
            self.code = 0

    def kill(self) -> None:
        self.signals.append("KILL")
        if not self.immortal:
            self.code = -9

    def wait(self, timeout: float) -> None:
        if self.code is None:
            raise Refusal("transfer_registry_left_running")


class FakeLauncher:
    def __init__(self, handle: FakeHandle | None = None) -> None:
        self.handle = handle or FakeHandle()
        self.started: list[tuple[str, ...]] = []
        self.config_text: str | None = None

    def start(self, argv: tuple[str, ...]) -> FakeHandle:
        self.started.append(argv)
        self.config_text = Path(argv[2]).read_text()
        return self.handle


@dataclass
class Setup:
    config: AgentConfig
    commands: FakeCommands
    launcher: FakeLauncher
    transfer: ImageTransfer
    archive: Path
    image: ImageSpec
    clock: list[float]
    reported: list[str | None]


def setup(
    tmp_path: Path,
    *,
    launcher: FakeLauncher | None = None,
    probe_open_after: bool = False,
    raw: bytes = MANIFEST,
) -> Setup:
    tmp_path.mkdir(parents=True, exist_ok=True)
    registry = tmp_path / "registry-bin"
    registry.write_bytes(b"registry")
    skopeo = tmp_path / "skopeo-bin"
    skopeo.write_bytes(b"skopeo")
    config = make_config(
        tmp_path,
        Keys(),
        registry_binary=registry,
        registry_binary_sha256=sha256_hex(b"registry"),
        skopeo_path=skopeo,
        skopeo_sha256=sha256_hex(b"skopeo"),
        skopeo_policy=tmp_path / "policy.json",
    )
    (tmp_path / "policy.json").write_text("{}")
    (tmp_path / "policy.json").chmod(0o644)
    registry.chmod(0o755)
    skopeo.chmod(0o755)
    config.run_dir.mkdir(mode=0o700)
    config.inbox_dir.mkdir(mode=0o700)
    archive = config.inbox_dir / ARCHIVE
    archive.write_bytes(b"archive")
    commands = FakeCommands()
    commands.script[("inspect", "--raw")] = CommandResult(0, raw)
    commands.script[("image", "inspect")] = CommandResult(
        0, f'"{IMAGE_ID}" ["{REFERENCE}"]\n'.encode()
    )
    launcher = launcher or FakeLauncher()
    clock = [0.0]
    reported: list[str | None] = []

    def probe(_port: int) -> bool:
        if probe_open_after:
            return True
        return launcher.handle.poll() is None

    def manifest_probe(_port: int, _path: str, digest: str) -> str | None:
        reported.append(digest)
        return digest

    transfer = ImageTransfer(
        config,
        commands,
        launcher,
        probe=probe,
        manifest_probe=manifest_probe,
        monotonic=lambda: clock[0],
        sleep=lambda _s: None,
        root_uid=os.geteuid(),
    )
    image = ImageSpec(
        "app", "transfer", "registry.example.invalid/org/app", DIGEST,
        "127.0.0.1:5000/org/app", DIGEST,
    )  # fmt: skip
    return Setup(config, commands, launcher, transfer, archive, image, clock, reported)


def test_transfer_runs_the_steps_and_tears_down(tmp_path: Path) -> None:
    s = setup(tmp_path)
    [result] = s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert (
        result.as_details()
        == {
            "service": "app",
            "media_type": "application/vnd.oci.image.manifest.v1+json",
            "image_id": IMAGE_ID,  # recorded, not compared (the image store decides its form)
            "repo_digest": REFERENCE,
            "registry_stopped": True,
            "archive_deleted": True,
        }
    )
    inspect_raw, skopeo, pull, inspect = s.commands.calls
    policy = str(s.config.skopeo_policy)
    assert inspect_raw[1:] == (
        "--policy",
        policy,
        "inspect",
        "--raw",
        f"oci-archive:{s.archive}",
    )
    assert skopeo[1:] == (
        "--policy",
        policy,
        "copy",
        "--preserve-digests",
        "--dest-tls-verify=false",
        "--dest-no-creds",
        f"oci-archive:{s.archive}",
        f"docker://{REFERENCE}",
    )
    assert pull[1:] == ("pull", REFERENCE)
    assert inspect[1:4] == ("image", "inspect", "--format")
    assert s.reported == [
        DIGEST
    ]  # the loopback registry's Docker-Content-Digest was proved
    assert not s.archive.exists()
    assert not s.config.registry_dir.exists()
    assert s.launcher.handle.poll() is not None


def test_an_index_digest_is_copied_with_all(tmp_path: Path) -> None:
    index_digest = "sha256:" + sha256_hex(INDEX)
    s = setup(tmp_path, raw=INDEX)
    reference = f"127.0.0.1:5000/org/app@{index_digest}"
    archive = s.config.inbox_dir / f"app-{index_digest.removeprefix('sha256:')}.oci.tar"
    archive.write_bytes(b"archive")
    s.commands.script[("image", "inspect")] = CommandResult(
        0, f'"{IMAGE_ID}" ["{reference}"]\n'.encode()
    )
    image = replace(s.image, release_digest=index_digest, planned_digest=index_digest)
    [result] = s.transfer.transfer_all((image,), ttl_seconds=900)
    copy = next(c for c in s.commands.calls if "copy" in c)
    assert "--all" in copy and "--preserve-digests" in copy
    assert result.media_type == "application/vnd.oci.image.index.v1+json"


def test_an_archive_whose_manifest_is_not_the_release_digest_is_refused_first(
    tmp_path: Path,
) -> None:
    s = setup(
        tmp_path, raw=MANIFEST + b" "
    )  # a rewritten manifest: other bytes, other digest
    with pytest.raises(Refusal) as caught:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert caught.value.code == "transfer_digest_mismatch"
    assert s.launcher.started == []  # nothing was started for a wrong archive


def test_a_loopback_registry_that_reports_another_digest_is_refused(
    tmp_path: Path,
) -> None:
    s = setup(tmp_path)
    s.transfer._manifest_probe = lambda *_a: "sha256:" + "0" * 64  # type: ignore[method-assign]
    with pytest.raises(Refusal) as caught:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert caught.value.code == "transfer_digest_mismatch"
    assert not s.archive.exists() and s.launcher.handle.poll() is not None


def test_registry_config_binds_loopback_only(tmp_path: Path) -> None:
    s = setup(tmp_path)
    text = registry_config(s.config, 5000)
    assert "addr: 127.0.0.1:5000\n" in text
    assert "0.0.0.0" not in text  # noqa: S104
    assert "localhost" not in text
    assert str(s.config.registry_dir / "data") in text  # tmpfs under /run


@pytest.mark.parametrize(
    "planned",
    [
        "0.0.0.0:5000/org/app",
        "localhost:5000/org/app",
        "192.0.2.1:5000/org/app",
        "127.0.0.2:5000/org/app",
        "127.0.0.1:80/org/app",
        "127.0.0.1/org/app",
        "[::1]:5000/org/app",
        "registry.example.invalid/org/app",
    ],
)
def test_registry_loopback_only_and_stopped(tmp_path: Path, planned: str) -> None:
    s = setup(tmp_path)
    image = replace(s.image, planned_repository=planned)
    with pytest.raises(Refusal) as caught:
        s.transfer.transfer_all((image,), ttl_seconds=900)
    assert caught.value.code == "transfer_registry_not_loopback"
    assert s.launcher.started == [] and s.commands.calls == []
    assert s.archive.exists()  # nothing was started, nothing was torn down


def test_a_registry_that_will_not_stop_is_refused(tmp_path: Path) -> None:
    for handle in (FakeHandle(stubborn=True), FakeHandle(immortal=True)):
        s = setup(
            tmp_path / handle.__class__.__name__ / str(id(handle)),
            launcher=FakeLauncher(handle),
        )
        if handle.stubborn:
            # SIGTERM ignored, SIGKILL works: the agent escalates and succeeds.
            s.transfer.transfer_all((s.image,), ttl_seconds=900)
            assert handle.signals == ["TERM", "KILL"]
        else:
            with pytest.raises(Refusal) as caught:
                s.transfer.transfer_all((s.image,), ttl_seconds=900)
            assert caught.value.code == "transfer_registry_left_running"
        assert not s.archive.exists()


def test_a_port_still_open_after_teardown_is_left_running(tmp_path: Path) -> None:
    s = setup(tmp_path, probe_open_after=True)
    with pytest.raises(Refusal) as caught:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert caught.value.code == "transfer_registry_left_running"


@pytest.mark.parametrize(
    "inspect_output",
    [
        f'"{IMAGE_ID}" ["127.0.0.1:5000/org/app@sha256:{"b" * 64}"]\n',  # other digest
        f'"{IMAGE_ID}" []\n',  # no RepoDigest recorded
        "garbage\n",
    ],
)
def test_transfer_digest_mismatch_refused(tmp_path: Path, inspect_output: str) -> None:
    s = setup(tmp_path)
    s.commands.script[("image", "inspect")] = CommandResult(0, inspect_output.encode())
    with pytest.raises(Refusal) as caught:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert caught.value.code == "transfer_digest_mismatch"
    # A refusal still tears everything down.
    assert not s.archive.exists() and not s.config.registry_dir.exists()
    assert s.launcher.handle.poll() is not None


def test_transfer_repository_must_be_the_release_path(tmp_path: Path) -> None:
    s = setup(tmp_path)
    image = replace(s.image, planned_repository="127.0.0.1:5000/org/other")
    with pytest.raises(Refusal) as caught:
        s.transfer.transfer_all((image,), ttl_seconds=900)
    assert caught.value.code == "transfer_repository_mismatch"
    assert s.launcher.started == []
    other_digest = replace(s.image, planned_digest="sha256:" + "0" * 64)
    with pytest.raises(Refusal) as digest:
        s.transfer.transfer_all((other_digest,), ttl_seconds=900)
    assert digest.value.code == "transfer_repository_mismatch"


def test_pull_or_skopeo_failure_refuses_and_tears_down(tmp_path: Path) -> None:
    for verb in ("copy", "pull"):
        s = setup(tmp_path / verb)
        s.commands.script[(verb,)] = CommandResult(1, b"")
        with pytest.raises(Refusal) as caught:
            s.transfer.transfer_all((s.image,), ttl_seconds=900)
        assert caught.value.code == "transfer_command_failed"
        assert not s.archive.exists() and s.launcher.handle.poll() is not None


def test_unpinned_binaries_are_refused_before_anything_starts(tmp_path: Path) -> None:
    s = setup(tmp_path)
    s.config.skopeo_path.write_bytes(b"tampered")
    with pytest.raises(Refusal) as caught:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert caught.value.code == "transfer_binary_unpinned"
    assert s.launcher.started == []


def test_inbox_and_archive_rules(tmp_path: Path) -> None:
    s = setup(tmp_path / "wide")
    s.config.inbox_dir.chmod(0o755)
    with pytest.raises(Refusal) as wide:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert wide.value.code == "transfer_inbox_unsafe"

    s = setup(tmp_path / "missing")
    s.archive.unlink()
    with pytest.raises(Refusal) as missing:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert missing.value.code == "transfer_archive_missing"

    s = setup(tmp_path / "symlink")
    s.archive.unlink()
    s.archive.symlink_to(tmp_path / "elsewhere")
    (tmp_path / "elsewhere").write_bytes(b"x")
    with pytest.raises(Refusal) as link:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert link.value.code == "transfer_archive_missing"

    # The archive name is derived from the service and digest: a differently named
    # file in the inbox is not picked up.
    s = setup(tmp_path / "othername")
    s.archive.rename(s.config.inbox_dir / "other.oci.tar")
    with pytest.raises(Refusal) as other:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert other.value.code == "transfer_archive_missing"


def test_registry_life_is_capped_at_the_lease_ttl(tmp_path: Path) -> None:
    s = setup(tmp_path)
    original = s.commands.run

    def slow(argv: tuple[str, ...], *, timeout: float) -> CommandResult:
        s.clock[0] += 901.0  # the copy outlasts the whole lease
        return original(argv, timeout=timeout)

    s.commands.run = slow  # type: ignore[method-assign]
    with pytest.raises(Refusal) as caught:
        s.transfer.transfer_all((s.image,), ttl_seconds=900)
    assert caught.value.code == "transfer_deadline_exceeded"


def test_a_pull_image_is_not_ours_to_move(tmp_path: Path) -> None:
    s = setup(tmp_path)
    image = replace(s.image, source="pull")
    assert s.transfer.transfer_all((image,), ttl_seconds=900) == ()
    assert s.commands.calls == [] and s.launcher.started == []


def test_the_executable_environment_carries_no_registry_credential(
    tmp_path: Path,
) -> None:
    from dotmac_deploy_agent import commands

    assert set(commands.ENV) == {"PATH", "LANG"}
    assert os.path.isabs(setup(tmp_path).config.docker_path.as_posix())


def test_app_image_must_be_a_planned_image_exactly(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    other = f"127.0.0.1:5000/org/app@sha256:{'c' * 64}"
    rig.cp.materials = make_materials(app_image=other)
    result = rig.launcher.run_once()
    assert (result.outcome, result.reason) == (
        "refused",
        "transfer_repository_mismatch",
    )
    assert rig.transfer.calls == [] and rig.runner.specs == []
    assert not rig.cp.lease_log  # no lease was taken for an unbound image


def test_binaries_and_the_policy_must_be_root_owned_and_not_group_or_world_writable(
    tmp_path: Path,
) -> None:
    """L4: a hash pin alone leaves a window between hash and exec."""
    for name, victim in (
        ("registry", lambda s: s.config.registry_binary),
        ("skopeo", lambda s: s.config.skopeo_path),
        ("policy", lambda s: s.config.skopeo_policy),
    ):
        s = setup(tmp_path / name)
        victim(s).chmod(0o775)  # group-writable
        with pytest.raises(Refusal) as caught:
            s.transfer.transfer_all((s.image,), ttl_seconds=900)
        assert caught.value.code == "transfer_binary_unpinned", name
        assert s.launcher.started == []
    s = setup(tmp_path / "owner")
    other = ImageTransfer(
        s.config, s.commands, s.launcher, root_uid=os.geteuid() + 1,
        manifest_probe=lambda *_a: DIGEST,
    )  # fmt: skip
    with pytest.raises(Refusal) as owner:
        other.transfer_all((s.image,), ttl_seconds=900)
    assert owner.value.code == "transfer_binary_unpinned"
