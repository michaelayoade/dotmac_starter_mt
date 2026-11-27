"""Governed digest transfer of a private image (DESIGN-rev3 s2.7, W-h).

The archive arrives in the agent's inbox through the attended staging step; no
host holds a registry pull credential and none has registry egress. Inside the
governed deploy op the agent:

1. checks the inbox, the archive and the pinned binaries;
2. starts a transient registry bound to 127.0.0.1 only, storage on tmpfs, life
   capped at the lease TTL;
3. `skopeo copy --preserve-digests [--all] oci-archive:<file>
   docker://127.0.0.1:<port>/<path>@<digest>`, under the enrolment-pinned
   `--policy` and with no credentials (`--all` when the digest is an index);
4. `docker pull` by digest (Docker verifies the content against the digest and
   records a real RepoDigest);
5. stops the registry, deletes its storage and the archive, and checks the
   image ID and RepoDigest against the release.

Any local process can push to the loopback registry while it is up; it cannot
produce content matching the digest, so the pull by digest fails closed.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from .canon import sha256_hex
from .commands import Commands, RegistryHandle, RegistryLauncher
from .config import AgentConfig
from .files import (
    ensure_private_dir,
    read_regular,
    remove_file,
    require_private_dir,
    write_private,
)
from .materials import ImageSpec
from .refusal import Refusal

LOOPBACK: Final = "127.0.0.1"
_PLANNED: Final = re.compile(r"127\.0\.0\.1:([0-9]{4,5})/([a-z0-9][a-z0-9._/-]*)")
_IMAGE_ID: Final = re.compile(r"sha256:[0-9a-f]{64}")
_REPO_PATH: Final = re.compile(r"[^/]+/(.+)")
READY_SECONDS: Final = 15.0
OCI_INDEX: Final = "application/vnd.oci.image.index.v1+json"
INDEX_MEDIA_TYPES: Final = frozenset(
    {
        OCI_INDEX,
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
_ACCEPT: Final = ", ".join(
    (
        *sorted(INDEX_MEDIA_TYPES),
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


@dataclass(frozen=True)
class TransferResult:
    """Names, digests and booleans only: this goes into the signed observation."""

    service: str
    image_id: str
    repo_digest: str
    media_type: str
    registry_stopped: bool
    archive_deleted: bool

    def as_details(self) -> dict[str, object]:
        return {
            "service": self.service,
            "media_type": self.media_type,
            "image_id": self.image_id,
            "repo_digest": self.repo_digest,
            "registry_stopped": self.registry_stopped,
            "archive_deleted": self.archive_deleted,
        }


class Transfer(Protocol):
    def transfer_all(
        self, images: tuple[ImageSpec, ...], *, ttl_seconds: int
    ) -> tuple[TransferResult, ...]: ...


def loopback_port(planned_repository: str) -> tuple[int, str]:
    """`127.0.0.1:<port>/<path>` only; any other host is refused."""
    match = _PLANNED.fullmatch(planned_repository)
    if match is None or not 1024 <= int(match[1]) <= 65535:
        raise Refusal("transfer_registry_not_loopback")
    return int(match[1]), match[2]


def release_path(repository: str) -> str:
    match = _REPO_PATH.fullmatch(repository)
    if match is None:
        raise Refusal("transfer_repository_mismatch")
    return match[1]


def registry_config(config: AgentConfig, port: int) -> str:
    """Distribution registry config: loopback bind, tmpfs storage, nothing else."""
    root = config.registry_dir / "data"
    return (
        "version: 0.1\n"
        "log:\n  level: warn\n"
        "storage:\n"
        f"  filesystem:\n    rootdirectory: {root}\n"
        "  maintenance:\n    uploadpurging:\n      enabled: false\n"
        "http:\n"
        f"  addr: {LOOPBACK}:{port}\n"
    )


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((LOOPBACK, port)) == 0


def loopback_manifest_digest(port: int, path: str, digest: str) -> str | None:
    """`Docker-Content-Digest` the loopback registry reports for `digest`."""
    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=15)
    try:
        connection.request(
            "HEAD", f"/v2/{path}/manifests/{digest}", headers={"Accept": _ACCEPT}
        )
        reply = connection.getresponse()
        reply.read()
        return reply.getheader("Docker-Content-Digest") if reply.status == 200 else None
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def root_owned(path: Path, required_uid: int) -> None:
    """Hash-pinning alone leaves a window between hash and exec: the file (and the
    skopeo policy) must also be owned by root and not writable by group or others."""
    try:
        info = path.lstat()
    except OSError:
        raise Refusal("transfer_binary_unpinned") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != required_uid
        or info.st_mode & 0o022
    ):
        raise Refusal("transfer_binary_unpinned")


def pinned(path: Path, expected_sha256: str, *, required_uid: int = 0) -> None:
    root_owned(path, required_uid)
    try:
        content = read_regular(path, limit=512 * 1024 * 1024)
    except Refusal:
        raise Refusal("transfer_binary_unpinned") from None
    if sha256_hex(content) != expected_sha256:
        raise Refusal("transfer_binary_unpinned")


class ImageTransfer:
    def __init__(
        self,
        config: AgentConfig,
        commands: Commands,
        launcher: RegistryLauncher,
        *,
        probe: Callable[[int], bool] = port_open,
        manifest_probe: Callable[
            [int, str, str], str | None
        ] = loopback_manifest_digest,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        root_uid: int = 0,
    ) -> None:
        self._config = config
        self._commands = commands
        self._launcher = launcher
        self._probe = probe
        self._manifest_probe = manifest_probe
        self._monotonic = monotonic
        self._sleep = sleep
        self._root_uid = root_uid

    # -- public ------------------------------------------------------------
    def transfer_all(
        self, images: tuple[ImageSpec, ...], *, ttl_seconds: int
    ) -> tuple[TransferResult, ...]:
        started = self._monotonic()
        results: list[TransferResult] = []
        for image in images:
            if image.source != "transfer":
                continue  # a public-allowlisted pull is Foundation's, not ours
            deadline = started + ttl_seconds
            results.append(self._transfer(image, deadline))
        return tuple(results)

    # -- one image ---------------------------------------------------------
    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise Refusal("transfer_deadline_exceeded")
        return remaining

    def _validate(self, image: ImageSpec) -> tuple[int, str, Path]:
        port, planned_path = loopback_port(image.planned_repository)
        # The planned repository is the loopback address with the SAME path, and
        # (by construction of `planned_reference`) the SAME digest as the release.
        if (
            planned_path != release_path(image.release_repository)
            or image.planned_digest != image.release_digest
        ):
            raise Refusal("transfer_repository_mismatch")
        inbox = self._config.inbox_dir
        try:
            require_private_dir(inbox)
        except Refusal:
            raise Refusal("transfer_inbox_unsafe") from None
        archive = inbox / image.archive
        try:
            info = archive.lstat()
        except OSError:
            raise Refusal("transfer_archive_missing") from None
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise Refusal("transfer_archive_missing")
        return port, planned_path, archive

    def _transfer(self, image: ImageSpec, deadline: float) -> TransferResult:
        port, planned_path, archive = self._validate(image)
        uid = self._root_uid
        pinned(
            self._config.registry_binary,
            self._config.registry_binary_sha256,
            required_uid=uid,
        )
        pinned(self._config.skopeo_path, self._config.skopeo_sha256, required_uid=uid)
        root_owned(self._config.skopeo_policy, uid)
        reference = f"{LOOPBACK}:{port}/{planned_path}@{image.planned_digest}"
        media_type = self._inspect_archive(image, archive, deadline)

        ensure_private_dir(self._config.registry_dir)
        ensure_private_dir(self._config.registry_dir / "data")
        config_file = self._config.registry_dir / "config.yml"
        write_private(
            config_file, registry_config(self._config, port).encode("ascii"), 0o600
        )
        handle: RegistryHandle | None = None
        stopped = False
        try:
            handle = self._launcher.start(
                (str(self._config.registry_binary), "serve", str(config_file))
            )
            self._await_ready(handle, port, deadline)
            self._run_checked(
                (
                    str(self._config.skopeo_path),
                    "--policy",
                    str(self._config.skopeo_policy),
                    "copy",
                    "--preserve-digests",
                    *(("--all",) if media_type in INDEX_MEDIA_TYPES else ()),
                    "--dest-tls-verify=false",
                    "--dest-no-creds",
                    f"oci-archive:{archive}",
                    f"docker://{reference}",
                ),
                deadline,
            )
            if (
                self._manifest_probe(port, planned_path, image.planned_digest)
                != image.planned_digest
            ):
                raise Refusal("transfer_digest_mismatch")
            self._run_checked(
                (str(self._config.docker_path), "pull", reference), deadline
            )
            image_id, repo_digest = self._verify(reference, deadline)
        finally:
            stopped = self._teardown(handle, port)
            shutil.rmtree(self._config.registry_dir, ignore_errors=True)
            remove_file(archive)
        if not stopped:
            raise Refusal("transfer_registry_left_running")
        return TransferResult(
            image.service, image_id, repo_digest, media_type,
            registry_stopped=True, archive_deleted=not archive.exists(),
        )  # fmt: skip

    def _inspect_archive(self, image: ImageSpec, archive: Path, deadline: float) -> str:
        """Prove the archive's manifest IS the release digest before anything starts,
        and learn its media type (an index needs `--all`)."""
        raw = self._run_checked(
            (
                str(self._config.skopeo_path),
                "--policy",
                str(self._config.skopeo_policy),
                "inspect",
                "--raw",
                f"oci-archive:{archive}",
            ),
            deadline,
        )
        if "sha256:" + sha256_hex(raw) != image.release_digest:
            raise Refusal("transfer_digest_mismatch")
        try:
            document = json.loads(raw)
            media_type = document.get("mediaType")
        except (ValueError, AttributeError):
            raise Refusal("transfer_digest_mismatch") from None
        if isinstance(media_type, str) and media_type:
            return media_type
        # An OCI index may omit `mediaType`; without `--all` its digest would be
        # lost on copy, so a document that lists `manifests` is treated as one.
        if isinstance(document.get("manifests"), list):
            return OCI_INDEX
        return "unknown"

    def _await_ready(self, handle: RegistryHandle, port: int, deadline: float) -> None:
        limit = min(READY_SECONDS, self._remaining(deadline))
        begun = self._monotonic()
        while self._monotonic() - begun < limit:
            if handle.poll() is not None:
                raise Refusal("transfer_command_failed")
            if self._probe(port):
                return
            self._sleep(0.2)
        raise Refusal("transfer_command_failed")

    def _run_checked(self, argv: tuple[str, ...], deadline: float) -> bytes:
        result = self._commands.run(argv, timeout=self._remaining(deadline))
        if result.exit_code != 0:
            raise Refusal("transfer_command_failed")
        return result.stdout

    def _verify(self, reference: str, deadline: float) -> tuple[str, str]:
        output = self._run_checked(
            (
                str(self._config.docker_path),
                "image",
                "inspect",
                "--format",
                "{{json .Id}} {{json .RepoDigests}}",
                reference,
            ),
            deadline,
        )
        try:
            raw_id, _, raw_digests = output.decode("utf-8").strip().partition(" ")
            image_id = json.loads(raw_id)
            repo_digests = json.loads(raw_digests)
        except (ValueError, UnicodeDecodeError):
            raise Refusal("transfer_digest_mismatch") from None
        if (
            not isinstance(image_id, str)
            or not _IMAGE_ID.fullmatch(image_id)
            or not isinstance(repo_digests, list)
            or reference not in repo_digests
        ):
            raise Refusal("transfer_digest_mismatch")
        return image_id, reference

    def _teardown(self, handle: RegistryHandle | None, port: int) -> bool:
        """Stop the registry and PROVE it: the process is gone and the port closed."""
        if handle is not None and handle.poll() is None:
            handle.terminate()
            try:
                handle.wait(10.0)
            except Refusal:
                handle.kill()
                try:
                    handle.wait(10.0)
                except Refusal:
                    return False
        return handle is None or (handle.poll() is not None and not self._probe(port))
