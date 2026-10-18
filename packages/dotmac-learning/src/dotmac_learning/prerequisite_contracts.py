"""Typed curriculum prerequisite intent and source reconciliation observations."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, field_validator

from .participation_contracts import ParticipationCommand


class PrerequisiteDeclaration(BaseModel):
    """An entry preserves raw slugs and normalizes source ``requires or []``."""

    model_config = ConfigDict(extra="forbid")
    course: str
    requires: tuple[str, ...] = ()

    @field_validator("requires", mode="before")
    @classmethod
    def normalize_requires(cls, value: object) -> object:
        return () if value is None else value


class ReconcilePrerequisitesCommand(ParticipationCommand):
    """Product supplies the tenant; discipline retains source ``doc.get``."""

    discipline: str | None
    declarations: tuple[PrerequisiteDeclaration, ...] = ()


class PrerequisiteReconciliationError(ValueError):
    """Invalid wanted graph; CLI adapters may translate to source SystemExit."""


@dataclass(frozen=True, slots=True)
class PrerequisiteReconciliationResult:
    """Staged counts only; no claim of flush, commit or durable application."""

    added: int
    removed: int
    declared: int
