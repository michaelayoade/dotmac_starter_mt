"""Value-free candidate material probes; product code owns secret semantics."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

MATERIAL_PROBE_RESULT_SCHEMA: Final = "CandidateMaterialProbeResult.v1"
MATERIAL_PROBE_MAX_STDOUT_BYTES: Final = 16_384
MATERIAL_PROBE_MAX_CHECKS: Final = 64
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,95}$")


class MaterialProbePhase(str, Enum):
    PRE_MUTATION = "pre_mutation"
    POST_MIGRATION = "post_migration"


class MaterialProbeStatus(str, Enum):
    READY = "ready"
    REFUSED = "refused"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class MaterialProbeCheck:
    name: str
    status: MaterialProbeStatus

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError("invalid material probe check name")
        if not isinstance(self.status, MaterialProbeStatus):
            raise ValueError("invalid material probe check status")


@dataclass(frozen=True, slots=True)
class MaterialProbeResult:
    status: MaterialProbeStatus
    checks: tuple[MaterialProbeCheck, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.checks, tuple)
            or not 1 <= len(self.checks) <= MATERIAL_PROBE_MAX_CHECKS
        ):
            raise ValueError("invalid material probe check count")
        if any(not isinstance(check, MaterialProbeCheck) for check in self.checks):
            raise ValueError("invalid material probe checks")
        if len({check.name for check in self.checks}) != len(self.checks):
            raise ValueError("duplicate material probe check")
        statuses = {check.status for check in self.checks}
        expected = (
            MaterialProbeStatus.REFUSED
            if MaterialProbeStatus.REFUSED in statuses
            else MaterialProbeStatus.UNAVAILABLE
            if MaterialProbeStatus.UNAVAILABLE in statuses
            else MaterialProbeStatus.READY
        )
        if (
            not isinstance(self.status, MaterialProbeStatus)
            or self.status is not expected
        ):
            raise ValueError("inconsistent material probe result")


@dataclass(frozen=True, slots=True)
class MaterialProbeObservation:
    """Coordinates supplied by the provider, never accepted from child output."""

    image: str
    role: str
    phase: MaterialProbePhase
    result: MaterialProbeResult

    def __post_init__(self) -> None:
        if not isinstance(self.image, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._\-/:]*@sha256:[0-9a-f]{64}", self.image
        ):
            raise ValueError("invalid material probe image coordinate")
        if not isinstance(self.role, str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,62}", self.role
        ):
            raise ValueError("invalid material probe role coordinate")
        if not isinstance(self.phase, MaterialProbePhase) or not isinstance(
            self.result, MaterialProbeResult
        ):
            raise ValueError("invalid material probe observation")


def unavailable_material_probe() -> MaterialProbeResult:
    return MaterialProbeResult(
        MaterialProbeStatus.UNAVAILABLE,
        (MaterialProbeCheck("provider.transport", MaterialProbeStatus.UNAVAILABLE),),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate material probe field")
        result[key] = value
    return result


def parse_material_probe_result(wire: str | bytes) -> MaterialProbeResult:
    """Strict single bounded JSON document; no child diagnostics escape."""
    try:
        raw = wire.encode("utf-8") if isinstance(wire, str) else wire
        if len(raw) > MATERIAL_PROBE_MAX_STDOUT_BYTES:
            raise ValueError
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(document, dict) or set(document) != {
            "schema",
            "status",
            "checks",
        }:
            raise ValueError
        if document["schema"] != MATERIAL_PROBE_RESULT_SCHEMA:
            raise ValueError
        rows = document["checks"]
        if (
            not isinstance(rows, list)
            or not 1 <= len(rows) <= MATERIAL_PROBE_MAX_CHECKS
        ):
            raise ValueError
        checks = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"name", "status"}:
                raise ValueError
            checks.append(
                MaterialProbeCheck(row["name"], MaterialProbeStatus(row["status"]))
            )
        return MaterialProbeResult(
            MaterialProbeStatus(document["status"]), tuple(checks)
        )
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError("invalid material probe document") from None


__all__ = [
    "MATERIAL_PROBE_RESULT_SCHEMA",
    "MATERIAL_PROBE_MAX_STDOUT_BYTES",
    "MATERIAL_PROBE_MAX_CHECKS",
    "MaterialProbePhase",
    "MaterialProbeStatus",
    "MaterialProbeCheck",
    "MaterialProbeResult",
    "MaterialProbeObservation",
    "parse_material_probe_result",
    "unavailable_material_probe",
]
