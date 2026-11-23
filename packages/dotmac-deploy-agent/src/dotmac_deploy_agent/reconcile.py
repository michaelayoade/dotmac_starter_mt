"""Read-only reconcile observation (DESIGN-rev3 s2.6.2).

When the host lease expired unreleased, the next acquire needs a reconcile
marker. The agent posts what is RUNNING, nothing else: it takes no lease, writes
no params, delivers no secret and runs no mutating command. Control compares the
observed release with the last committed or planned one and issues the marker
itself, or routes the target to restore-and-fix-forward.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .commands import Commands
from .config import AgentConfig
from .observation import build_observation
from .refusal import Refusal


@dataclass(frozen=True)
class ObservedContainer:
    name: str
    image_ref: str
    image_id: str


def observe_containers(
    config: AgentConfig, commands: Commands
) -> tuple[ObservedContainer, ...]:
    """`docker inspect` on the configured containers. Names, image refs and IDs only."""
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
            continue  # a container that is not there is itself an observation
        parts = result.stdout.decode("utf-8", errors="replace").split()
        if len(parts) != 2:
            raise Refusal("reconcile_unavailable")
        found.append(ObservedContainer(name, parts[0], parts[1]))
    return tuple(found)


def _digest_of(reference: str) -> str | None:
    _, _, digest = reference.partition("@")
    return digest or None


def reconcile_outcome(
    containers: tuple[ObservedContainer, ...], expected_image: str | None
) -> str:
    """`not_needed`: nothing of the stack runs. `reconciled`: every running
    container carries the digest of the image this agent last applied. Anything else
    is `failed`: Control then routes to restore-and-fix-forward, never a marker."""
    if not containers:
        return "not_needed"
    expected = _digest_of(expected_image) if expected_image else None
    if expected is not None and all(
        _digest_of(c.image_ref) == expected for c in containers
    ):
        return "reconciled"
    return "failed"


def reconcile_observation(
    config: AgentConfig,
    commands: Commands,
    *,
    dispatch_id: str,
    standing: str,
    expected_image: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, object]:
    containers = observe_containers(config, commands)
    outcome = reconcile_outcome(containers, expected_image)
    return build_observation(
        kind="reconcile",
        dispatch_id=dispatch_id,
        target_ref=config.target_ref,
        outcome=outcome,
        reason=None,
        version=config.version,
        details={
            "standing": standing,
            "configured": list(config.reconcile_containers),
            "containers": [
                {"name": c.name, "image_ref": c.image_ref, "image_id": c.image_id}
                for c in containers
            ],
        },
        now=clock(),
    )
