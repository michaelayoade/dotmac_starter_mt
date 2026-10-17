"""Value-free startup observations, never authenticated enrollment authority.

Only the consuming Executor may publish a complete tuple. Custody must attest
historical immutable assignment at process start; reading current files cannot
supply that fact. Control performs its enrollment and postcommit joins later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol, runtime_checkable

from .authorization_v3 import ExecutionContextV3
from .errors import DeploymentError

__all__ = [
    "LiveInstanceRefusal",
    "StartupObservationContext",
    "CustodyMountReference",
    "StartupAssignmentRequest",
    "StartupAssignmentConfirmation",
    "StartupAssignmentCustody",
    "LiveInstanceMeasurement",
    "LiveInstanceObservationEffects",
]


class LiveInstanceRefusal(DeploymentError):
    """A bounded reason; never accepts raw provider or custody diagnostics."""

    def __init__(self, reason: str) -> None:
        if reason not in {
            "unsupported",
            "unavailable",
            "mismatch",
            "instance_changed",
            "custody_missing",
        }:
            reason = "unavailable"
        self.reason = reason
        super().__init__("startup observation refused: " + reason)


def _identity(value: str) -> None:
    if type(value) is not str or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}", value
    ):
        raise ValueError("invalid startup identity")


def require_aware(value: datetime) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("startup observation requires aware time")


@dataclass(frozen=True, slots=True)
class StartupObservationContext:
    """Read-only continuation data; its public construction grants no authority."""

    execution_context: ExecutionContextV3
    descriptor_digest: str
    execution_plan_digest: str
    control_consumption_ref: str
    started_at: datetime

    def __post_init__(self) -> None:
        if type(self.execution_context) is not ExecutionContextV3:
            raise ValueError("startup observation requires typed host context")
        for value in (self.descriptor_digest, self.execution_plan_digest):
            if type(value) is not str or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", value
            ):
                raise ValueError("invalid startup digest")
        if not self.control_consumption_ref.startswith("control-dispatch:"):
            raise ValueError("invalid startup consumption coordinate")
        require_aware(self.started_at)


@dataclass(frozen=True, slots=True)
class CustodyMountReference:
    mount_index: int
    source_material: str
    target_material: str
    literal_target: str

    def __post_init__(self) -> None:
        if type(self.mount_index) is not int or not 0 <= self.mount_index < 128:
            raise ValueError("invalid startup mount index")
        if type(self.source_material) is not str or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,127}", self.source_material
        ):
            raise ValueError("invalid startup source material")
        if (
            type(self.target_material) is not str
            or type(self.literal_target) is not str
            or bool(self.target_material) == bool(self.literal_target)
        ):
            raise ValueError("invalid startup target reference")
        if self.target_material and not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,127}", self.target_material
        ):
            raise ValueError("invalid startup target material")
        if self.literal_target and (
            not self.literal_target.startswith("/") or len(self.literal_target) > 4096
        ):
            raise ValueError("invalid startup literal target")


@dataclass(frozen=True, slots=True)
class StartupAssignmentRequest:
    role: str
    container_id: str
    started_at: str
    context: StartupObservationContext
    material_names: tuple[str, ...]
    custody_mounts: tuple[CustodyMountReference, ...]

    def __post_init__(self) -> None:
        _identity(self.role)
        if type(self.container_id) is not str or not re.fullmatch(
            r"[0-9a-f]{64}", self.container_id
        ):
            raise ValueError("invalid startup instance")
        _identity(self.started_at)
        if type(self.context) is not StartupObservationContext:
            raise ValueError("invalid startup context")
        _require_roster(self.material_names, self.custody_mounts)


def _require_roster(
    names: tuple[str, ...], mounts: tuple[CustodyMountReference, ...]
) -> None:
    if (
        type(names) is not tuple
        or len(names) > 128
        or any(
            type(name) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name)
            for name in names
        )
        or names != tuple(sorted(set(names)))
    ):
        raise ValueError("invalid startup material roster")
    if (
        type(mounts) is not tuple
        or len(mounts) > 128
        or any(type(row) is not CustodyMountReference for row in mounts)
    ):
        raise ValueError("invalid startup custody roster")
    indexes = tuple(row.mount_index for row in mounts)
    if indexes != tuple(sorted(set(indexes))):
        raise ValueError("invalid startup custody mount order")


@dataclass(frozen=True, slots=True)
class StartupAssignmentConfirmation:
    container_id: str
    started_at: str
    installation_ref: str
    config_revision_ref: str
    credential_ref: str
    credential_version: str
    material_names: tuple[str, ...]
    custody_mounts: tuple[CustodyMountReference, ...]
    assignment_ref: str
    confirmed_at: datetime
    assigned_at_process_start: bool
    immutable_since_start: bool

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.container_id):
            raise ValueError("invalid startup container identity")
        for value in (
            self.started_at,
            self.installation_ref,
            self.config_revision_ref,
            self.credential_ref,
            self.credential_version,
            self.assignment_ref,
        ):
            _identity(value)
        _require_roster(self.material_names, self.custody_mounts)
        require_aware(self.confirmed_at)
        if (
            self.assigned_at_process_start is not True
            or self.immutable_since_start is not True
        ):
            raise ValueError("custody must confirm immutable process-start assignment")


@runtime_checkable
class StartupAssignmentCustody(Protocol):
    def confirm_startup_assignment(
        self, request: StartupAssignmentRequest
    ) -> StartupAssignmentConfirmation:
        """Independently confirm historical assignment, never merely current files."""
        ...


@dataclass(frozen=True, slots=True)
class LiveInstanceMeasurement:
    role: str
    container_id: str
    process_started_at: str
    process_pid: int
    restart_count: int
    running: bool
    image_id: str
    approved_image_ref: str
    nonsecret_file_digests: tuple[tuple[int, str], ...]
    environment_digests: tuple[tuple[str, str], ...]
    observation_started_at: datetime
    observation_finished_at: datetime
    context: StartupObservationContext
    assignment: StartupAssignmentConfirmation
    # Nonsecret filesystem identity supports the final all-role recheck.
    nonsecret_file_identities: tuple[tuple[int, tuple[int, ...]], ...] = field(
        default=(), repr=False
    )

    def __post_init__(self) -> None:
        _identity(self.role)
        if type(self.container_id) is not str or not re.fullmatch(
            r"[0-9a-f]{64}", self.container_id
        ):
            raise ValueError("invalid startup container identity")
        if type(self.process_pid) is not int or self.process_pid < 1:
            raise ValueError("invalid startup process identity")
        if (
            type(self.restart_count) is not int
            or self.restart_count < 0
            or self.running is not True
        ):
            raise ValueError("invalid startup process state")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id):
            raise ValueError("invalid startup image identity")
        if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", self.approved_image_ref):
            raise ValueError("invalid startup image reference")
        if (
            type(self.context) is not StartupObservationContext
            or type(self.assignment) is not StartupAssignmentConfirmation
        ):
            raise ValueError("invalid startup measurement context")
        if (
            type(self.nonsecret_file_identities) is not tuple
            or len(self.nonsecret_file_identities) > 128
        ):
            raise ValueError("invalid startup file identity roster")
        for index, identity in self.nonsecret_file_identities:
            if (
                type(index) is not int
                or index < 0
                or type(identity) is not tuple
                or len(identity) != 6
                or any(type(part) is not int for part in identity)
            ):
                raise ValueError("invalid startup file identity")
        require_aware(self.observation_started_at)
        require_aware(self.observation_finished_at)
        if (
            not self.context.started_at
            <= self.observation_started_at
            <= self.assignment.confirmed_at
            <= self.observation_finished_at
        ):
            raise ValueError("incoherent startup observation times")
        if (
            self.assignment.container_id != self.container_id
            or self.assignment.started_at != self.process_started_at
        ):
            raise ValueError("custody startup instance differs")
        for rows in (self.nonsecret_file_digests, self.environment_digests):
            if (
                type(rows) is not tuple
                or len(rows) > 128
                or rows != tuple(sorted(set(rows)))
            ):
                raise ValueError("invalid startup digest roster")
            for _, digest in rows:
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    raise ValueError("invalid startup approved digest")


@runtime_checkable
class LiveInstanceObservationEffects(Protocol):
    def preflight_live_instance(self, *, custody: StartupAssignmentCustody) -> None: ...

    def recheck_live_instance(
        self, measurement: LiveInstanceMeasurement, *, custody: StartupAssignmentCustody
    ) -> LiveInstanceMeasurement:
        """Recheck the pinned full ID and files before publishing all roles."""
        ...

    def observe_live_instance(
        self,
        *,
        role: str,
        custody: StartupAssignmentCustody,
        context: StartupObservationContext,
    ) -> LiveInstanceMeasurement: ...


def _require_startup_recheck(
    original: LiveInstanceMeasurement, fresh: LiveInstanceMeasurement
) -> None:
    """Compare immutable measurements; freshness never substitutes a subject."""
    if (
        type(original) is not LiveInstanceMeasurement
        or type(fresh) is not LiveInstanceMeasurement
    ):
        raise LiveInstanceRefusal("mismatch")
    # Revalidate supplied DTOs, including nested timestamps and custody roster.
    # A frozen Python record is an integrity convention, not authentication.
    replace(fresh.context)
    replace(fresh.assignment)
    replace(fresh)
    if (
        fresh.observation_started_at < original.observation_finished_at
        or fresh.observation_finished_at < original.observation_finished_at
        or fresh.assignment.confirmed_at < original.assignment.confirmed_at
    ):
        raise LiveInstanceRefusal("instance_changed")
    unchanged = replace(
        fresh,
        observation_started_at=original.observation_started_at,
        observation_finished_at=original.observation_finished_at,
        assignment=replace(
            fresh.assignment, confirmed_at=original.assignment.confirmed_at
        ),
    )
    if unchanged != original:
        raise LiveInstanceRefusal("instance_changed")
