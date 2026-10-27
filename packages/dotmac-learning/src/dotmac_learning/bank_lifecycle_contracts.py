"""Question-bank version lifecycle commands, facts and audit attribution.

Learning performs no role checks: every mutating command receives a trusted
:class:`BankAuditContext` built by the product composition, following the
attempt-grant pattern. Publish/repoint/export/retire authority (admin-only
at Academy composition) is enforced by the caller, never inferred here.
Audit details are content-free by construction: ids, hashes, counts and
violation counts only — never stems, options or correct values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .attempt_contracts import QuestionFact
from .curriculum_contracts import BankDocument

BankVersionState = Literal["draft", "published", "retired"]
BankVersionOrigin = Literal["ui", "yaml_import", "backfill"]

BANK_VERSION_STATES: Final[frozenset[str]] = frozenset(get_args(BankVersionState))
BANK_VERSION_ORIGINS: Final[frozenset[str]] = frozenset(get_args(BankVersionOrigin))


class BankLifecycleError(ValueError):
    """Base refusal for bank-version lifecycle commands."""


class BankNotFound(BankLifecycleError):
    pass


class BankVersionNotFound(BankLifecycleError):
    pass


class BankLifecycleConflict(BankLifecycleError):
    pass


class BankVersionImmutable(BankLifecycleConflict):
    """A mutating command targeted a non-draft version."""


class BankPublishRefused(BankLifecycleConflict):
    pass


class BankImportRefused(BankLifecycleConflict):
    pass


class LegacyBankWriterRetired(BankLifecycleError):
    """The destructive pre-versioning writers are fail-closed shims."""


BankActorType = Literal["system", "user", "api_key", "service"]
_BANK_ACTOR_TYPES: Final[frozenset[str]] = frozenset(get_args(BankActorType))


@dataclass(frozen=True, slots=True)
class BankAuditContext:
    """Trusted product attribution, never inferred from command fields."""

    tenant_id: UUID
    actor_type: BankActorType
    source_application: str
    actor_id: str | None = None
    actor_label: str | None = None
    actor_party_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.actor_type not in _BANK_ACTOR_TYPES:
            raise ValueError(
                "bank audit actor_type must be " + ", ".join(sorted(_BANK_ACTOR_TYPES))
            )


class BankCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CreateBankDraftCommand(BankCommand):
    tenant_id: UUID
    course_id: UUID
    chapter_number: int | None = None
    kind: str
    origin: BankVersionOrigin = "ui"
    # Copy source: explicit version, else the bank's current published
    # version; ``from_empty`` forces an empty draft.
    from_version_id: UUID | None = None
    from_empty: bool = Field(strict=True, default=False)


class DraftQuestionWrite(BankCommand):
    ext_id: str
    stem: str
    type: str
    rubric_category: str
    options: JsonValue = Field(default_factory=list)
    correct: JsonValue = Field(default_factory=list)
    category: str | None = None
    explanation: str = ""
    weight: int


class UpsertDraftQuestionCommand(BankCommand):
    tenant_id: UUID
    version_id: UUID
    question: DraftQuestionWrite


class DeleteDraftQuestionCommand(BankCommand):
    tenant_id: UUID
    version_id: UUID
    ext_id: str


class DiscardDraftCommand(BankCommand):
    tenant_id: UUID
    version_id: UUID


class PublishBankVersionCommand(BankCommand):
    tenant_id: UUID
    version_id: UUID
    # Optimistic guard: when supplied it must still be the bank's current
    # published version; the draft's recorded base is always checked.
    expected_current_version_id: UUID | None = None
    now: datetime | None = None


class RetireBankVersionCommand(BankCommand):
    tenant_id: UUID
    version_id: UUID
    reason: str
    now: datetime | None = None


class RepointActivityBankVersionCommand(BankCommand):
    tenant_id: UUID
    activity_id: UUID
    version_id: UUID


class ImportBankDocumentAsDraftCommand(BankCommand):
    tenant_id: UUID
    course_id: UUID
    doc: BankDocument
    missing_question_weight: int
    replace_imported_draft: bool = Field(strict=True, default=False)


@dataclass(frozen=True, slots=True)
class BankVersionFact:
    id: UUID
    tenant_id: UUID
    bank_id: UUID
    version_no: int | None
    state: str
    origin: str
    content_hash: str
    base_version_id: UUID | None
    draft_revision: int
    question_count: int
    lint_violation_count: int
    created_by_actor: UUID | None
    published_by: UUID | None
    published_at: datetime | None
    retired_by: UUID | None
    retired_at: datetime | None
    retire_reason: str | None
    superseded_by_version_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PublishResult:
    version: BankVersionFact
    published: bool
    # "published" or "noop_identical_to_current".
    outcome: str


@dataclass(frozen=True, slots=True)
class LintResult:
    version_id: UUID
    violations: tuple[str, ...]
    content_hash: str


@dataclass(frozen=True, slots=True)
class BankImportReport:
    # "created" | "replaced_imported_draft" | "noop_hash_equal".
    outcome: str
    bank_id: UUID
    version_id: UUID | None
    content_hash: str
    lint_violations: tuple[str, ...]
    # YAML policy block, returned verbatim and NEVER applied to any
    # Activity by import (pass-mark reset regression guard).
    policy_proposals: tuple[tuple[str, JsonValue], ...]


@dataclass(frozen=True, slots=True)
class SittingQuestion:
    """Learner-facing projection: no correct/explanation fields exist."""

    ext_id: str
    stem: str
    type: str
    options: tuple[JsonValue, ...]


@dataclass(frozen=True, slots=True)
class BankVersionQuestions:
    version: BankVersionFact
    questions: tuple[QuestionFact, ...]


__all__ = [
    "BANK_VERSION_ORIGINS",
    "BANK_VERSION_STATES",
    "BankActorType",
    "BankAuditContext",
    "BankCommand",
    "BankImportRefused",
    "BankImportReport",
    "BankLifecycleConflict",
    "BankLifecycleError",
    "BankNotFound",
    "BankPublishRefused",
    "BankVersionFact",
    "BankVersionImmutable",
    "BankVersionNotFound",
    "BankVersionOrigin",
    "BankVersionQuestions",
    "BankVersionState",
    "CreateBankDraftCommand",
    "DeleteDraftQuestionCommand",
    "DiscardDraftCommand",
    "DraftQuestionWrite",
    "ImportBankDocumentAsDraftCommand",
    "LegacyBankWriterRetired",
    "LintResult",
    "PublishBankVersionCommand",
    "PublishResult",
    "RepointActivityBankVersionCommand",
    "RetireBankVersionCommand",
    "SittingQuestion",
    "UpsertDraftQuestionCommand",
]
