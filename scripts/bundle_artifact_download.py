#!/usr/bin/env python3
"""Acquire one already-observed GitHub Actions artifact without publishing it."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

import bundle_github_observation
import github_actions_transport
from bundle_github_observation import TrustedProducerPolicy

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024


def _require_private_parent(destination: Path) -> None:
    """The action must provide an existing, private output directory.

    Mode and ownership are checked here; placement outside the candidate
    checkout and the absence of concurrently running untrusted same-UID code
    remain obligations of the future pinned action, not facts a path proves.
    """

    if not destination.is_absolute():
        raise ValueError("artifact destination must be absolute")
    try:
        parent = destination.parent.lstat()
    except FileNotFoundError as exc:
        raise ValueError("artifact parent must already exist") from exc
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise ValueError("artifact parent must be an owned private directory")


def acquire_observed_artifact(
    policy: TrustedProducerPolicy,
    run_id: int,
    artifact_id: int,
    token: str,
    destination: Path,
) -> tuple[dict[str, Any], Path]:
    """Observe, stream, verify, and atomically install one artifact archive."""

    observed = bundle_github_observation.observe(run_id, artifact_id, token, policy)
    size = observed.get("artifact_size_in_bytes")
    digest = observed.get("artifact_digest")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("observed artifact size is missing or invalid")
    if size > MAX_ARTIFACT_BYTES:
        raise ValueError("observed artifact exceeds size cap")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("observed artifact digest is missing or invalid")
    expected_digest = digest.removeprefix("sha256:")
    if len(expected_digest) != 64 or any(
        c not in "0123456789abcdef" for c in expected_digest
    ):
        raise ValueError("observed artifact digest is not sha256")
    _require_private_parent(destination)
    if destination.exists():
        raise FileExistsError(destination)
    url = github_actions_transport.url_for_path(
        f"/repos/{policy.repository}/actions/artifacts/{artifact_id}/zip"
    )
    request = urllib.request.Request(  # noqa: S310 -- URL is fixed HTTPS origin.
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        },
    )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=".artifact-", delete=False
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary.name, 0o600)
            with github_actions_transport.OPENER.open(request, timeout=60) as response:
                count = 0
                hasher = hashlib.sha256()
                while chunk := response.read(_CHUNK_SIZE):
                    count += len(chunk)
                    if count > size or count > MAX_ARTIFACT_BYTES:
                        raise ValueError("artifact exceeds size cap")
                    temporary.write(chunk)
                    hasher.update(chunk)
            if count != size:
                raise ValueError("downloaded artifact size differs from observation")
            if hasher.hexdigest() != expected_digest:
                raise ValueError("downloaded artifact digest differs from observation")
            temporary.flush()
            os.fsync(temporary.fileno())
            verified = os.fstat(temporary.fileno())
            source = os.stat(temporary_name, follow_symlinks=False)
            if not stat.S_ISREG(source.st_mode) or (source.st_dev, source.st_ino) != (
                verified.st_dev,
                verified.st_ino,
            ):
                raise ValueError("artifact tempfile no longer names verified bytes")
            os.link(temporary_name, destination, follow_symlinks=False)
            published = os.stat(destination, follow_symlinks=False)
            if not stat.S_ISREG(published.st_mode) or (
                published.st_dev,
                published.st_ino,
            ) != (verified.st_dev, verified.st_ino):
                raise ValueError(
                    "published artifact does not name verified bytes; "
                    "discard the private directory"
                )
            os.unlink(temporary_name)
            parent_fd = os.open(
                destination.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return observed, destination
