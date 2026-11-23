"""Read-only reconcile observation (DESIGN-rev3 s2.6.2).

When the host lease expired unreleased, the next acquire needs a reconcile
marker. The agent posts what is RUNNING, nothing else: it takes no lease, writes
no params, delivers no secret and runs no mutating command. Control compares the
observed release with the last committed or planned one and issues the marker
itself, or routes the target to restore-and-fix-forward.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .canon import canonical_json, digest
from .commands import Commands
from .config import AgentConfig
from .observation import build_observation
from .refusal import Refusal


@dataclass(frozen=True)
class ObservedContainer:
    name: str
    image_ref: str
    image_id: str
    repo_digests: tuple[str, ...]


def observe_containers(
    config: AgentConfig, commands: Commands
) -> tuple[ObservedContainer, ...]:
    """`docker inspect` on the configured containers, then `docker image inspect`
    for each image's repo digests. Names, image refs, IDs and digests only. Any
    command that cannot be read refuses (the observation is then `failed`)."""
    found: list[ObservedContainer] = []
    for name in config.reconcile_containers:
        result = commands.run(
            (
                str(config.docker_path),
                "inspect",
                "--format",
                "{{.Config.Image}} {{.Image}}",
                name,
            ),
            timeout=30.0,
        )
        if result.exit_code != 0:
            # Only PROVEN absence is an observation: `docker inspect` also fails when
            # the daemon is down, so ask the listing, and treat anything but a
            # successful, empty answer as "could not observe" (reported as failed).
            if not _absent(config, commands, name):
                raise Refusal("reconcile_unavailable")
            continue
        parts = result.stdout.decode("utf-8", errors="replace").split()
        if len(parts) != 2:
            raise Refusal("reconcile_unavailable")
        found.append(
            ObservedContainer(
                name, parts[0], parts[1], _repo_digests(config, commands, parts[1])
            )
        )
    return tuple(found)


def _absent(config: AgentConfig, commands: Commands, name: str) -> bool:
    listing = commands.run(
        (
            str(config.docker_path),
            "ps",
            "-a",
            "--filter",
            f"name=^/{name}$",
            "--format",
            "{{.ID}}",
        ),
        timeout=30.0,
    )
    return listing.exit_code == 0 and not listing.stdout.strip()


def _repo_digests(
    config: AgentConfig, commands: Commands, image_id: str
) -> tuple[str, ...]:
    result = commands.run(
        (
            str(config.docker_path),
            "image",
            "inspect",
            "--format",
            "{{json .RepoDigests}}",
            image_id,
        ),
        timeout=30.0,
    )
    try:
        listed = json.loads(result.stdout) if result.exit_code == 0 else None
    except ValueError:
        listed = None
    if not isinstance(listed, list) or not all(isinstance(d, str) for d in listed):
        raise Refusal("reconcile_unavailable")
    return tuple(sorted(listed))


def observed_digest(containers: tuple[ObservedContainer, ...]) -> str:
    """sha256 of the canonical observed-container set: the de-duplication key."""
    return digest(
        canonical_json([c.__dict__ for c in sorted(containers, key=lambda c: c.name)])
    )


def reconcile_observation(
    config: AgentConfig,
    commands: Commands,
    *,
    expired_dispatch_id: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[dict[str, object], str | None]:
    """The facts only, for the EXPIRED dispatch: `reconciled` means the observation
    is complete, `failed` that it could not be made. The agent never decides that
    the host is fine and never sends `not_needed`: Control's pure `reconcile_marker`
    decides. Returns `(observation, observed_digest)`; the digest is None on
    failure."""
    containers: tuple[ObservedContainer, ...] = ()
    outcome, reason, observed = "reconciled", None, None
    try:
        containers = observe_containers(config, commands)
        observed = observed_digest(containers)
    except Refusal as refusal:
        outcome, reason = "failed", refusal.code
    details: dict[str, object] = {
        "expired_dispatch_id": expired_dispatch_id,
        "configured": list(config.reconcile_containers),
        "containers": [
            {
                "name": c.name,
                "image_ref": c.image_ref,
                "image_id": c.image_id,
                "repo_digests": list(c.repo_digests),
            }
            for c in containers
        ],
    }
    return (
        build_observation(
            kind="reconcile",
            dispatch_id=expired_dispatch_id,
            target_ref=config.target_ref,
            outcome=outcome,
            reason=reason,
            version=config.version,
            details=details,
            now=clock(),
        ),
        observed,
    )
