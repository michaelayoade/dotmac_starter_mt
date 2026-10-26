"""Question-bank system-of-record lifecycle (lr_0010).

One immutable-version layer over the existing QuestionBank/Question rows.
Drafts are copy-on-write and invisible to sittings; publishing freezes a
draft as the next Learning-allocated version number and moves only the
bank's current pointer — activities repoint explicitly, never implicitly.
YAML is a transport format: import lands as a draft and never touches
Activity policy. Every mutation writes a content-free Kernel audit event
in the caller transaction and refuses non-draft question rows.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from dotmac_kernel import write_audit_event
from pydantic import JsonValue
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import exam_engine
from .attempt_contracts import QuestionFact
from .bank_lifecycle_contracts import (
    BankAuditContext,
    BankImportRefused,
    BankImportReport,
    BankLifecycleConflict,
    BankNotFound,
    BankPublishRefused,
    BankVersionFact,
    BankVersionImmutable,
    BankVersionNotFound,
    BankVersionQuestions,
    CreateBankDraftCommand,
    DeleteDraftQuestionCommand,
    DiscardDraftCommand,
    ImportBankDocumentAsDraftCommand,
    LintResult,
    PublishBankVersionCommand,
    PublishResult,
    RepointActivityBankVersionCommand,
    RetireBankVersionCommand,
    SittingQuestion,
    UpsertDraftQuestionCommand,
)
from .bank_validation import BankDoc, _competency_of
from .bank_validation import lint_bank as _lint_bank_rules
from .curriculum_contracts import BankDocument, BankFact, BankQuestion
from .curriculum_models import Question, QuestionBank, QuestionBankVersion
from .models import Activity
from .results import _transaction, _unchanged


def canonical_content_hash(questions: list[dict[str, Any]]) -> str:
    """SHA-256 over questions ordered by ext_id, canonical JSON encoding.

    The field set is exactly (ext_id, stem, type, options, correct,
    rubric_category, category, explanation, weight). lr_0010's backfill
    duplicates this definition; hosted tests pin the two together.
    """
    ordered = sorted(questions, key=lambda q: str(q["ext_id"]))
    payload = [
        {
            "ext_id": q["ext_id"],
            "stem": q["stem"],
            "type": q["type"],
            "options": q.get("options", []),
            "correct": q.get("correct", []),
            "rubric_category": q["rubric_category"],
            "category": q.get("category"),
            "explanation": q.get("explanation", ""),
            "weight": q["weight"],
        }
        for q in ordered
    ]
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _question_dict(row: Question) -> dict[str, Any]:
    return {
        "ext_id": row.ext_id,
        "stem": row.stem,
        "type": row.type,
        "options": deepcopy(row.options),
        "correct": deepcopy(row.correct),
        "rubric_category": row.rubric_category,
        "category": row.category,
        "explanation": row.explanation,
        "weight": row.weight,
    }


def _version_fact(
    db: Session, row: QuestionBankVersion, *, question_count: int | None = None
) -> BankVersionFact:
    if question_count is None:
        question_count = int(
            db.scalar(
                select(func.count())
                .select_from(Question)
                .where(
                    Question.tenant_id == row.tenant_id,
                    Question.bank_version_id == row.id,
                )
            )
            or 0
        )
    violations = row.lint_violations if isinstance(row.lint_violations, list) else []
    return BankVersionFact(
        id=row.id,
        tenant_id=row.tenant_id,
        bank_id=row.bank_id,
        version_no=row.version_no,
        state=row.state,
        origin=row.origin,
        content_hash=row.content_hash,
        base_version_id=row.base_version_id,
        draft_revision=row.draft_revision,
        question_count=question_count,
        lint_violation_count=len(violations),
        created_by_actor=row.created_by_actor,
        published_by=row.published_by,
        published_at=row.published_at,
        retired_by=row.retired_by,
        retired_at=row.retired_at,
        retire_reason=row.retire_reason,
        superseded_by_version_id=row.superseded_by_version_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _question_fact(row: Question) -> QuestionFact:
    return QuestionFact(
        id=row.id,
        tenant_id=row.tenant_id,
        bank_id=row.bank_id,
        ext_id=row.ext_id,
        stem=row.stem,
        type=row.type,
        options=cast(JsonValue, deepcopy(row.options)),
        correct=cast(JsonValue, deepcopy(row.correct)),
        rubric_category=row.rubric_category,
        category=row.category,
        explanation=row.explanation,
        weight=row.weight,
        created_at=row.created_at,
        updated_at=row.updated_at,
        bank_version_id=row.bank_version_id,
    )


def _check_context(context: BankAuditContext, tenant_id: UUID) -> None:
    if context.tenant_id != tenant_id:
        raise ValueError("bank audit context scope does not match command")


def _audit(
    db: Session,
    context: BankAuditContext,
    *,
    action: str,
    entity_id: UUID,
    details: dict[str, Any],
) -> None:
    write_audit_event(
        db,
        tenant_id=context.tenant_id,
        actor_type=context.actor_type,
        actor_id=context.actor_id,
        actor_label=context.actor_label,
        source_application=context.source_application,
        actor_party_id=context.actor_party_id,
        action=action,
        entity_type="question_bank",
        entity_id=str(entity_id),
        details=details,
    )


def _bank(
    db: Session, tenant_id: UUID, bank_id: UUID, *, lock: bool = False
) -> QuestionBank:
    statement = select(QuestionBank).where(
        QuestionBank.tenant_id == tenant_id, QuestionBank.id == bank_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalars(statement).first()
    if row is None:
        raise BankNotFound("bank not found for tenant")
    return row


def _version(
    db: Session, tenant_id: UUID, version_id: UUID, *, lock: bool = False
) -> QuestionBankVersion:
    statement = select(QuestionBankVersion).where(
        QuestionBankVersion.tenant_id == tenant_id,
        QuestionBankVersion.id == version_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalars(statement).first()
    if row is None:
        raise BankVersionNotFound("bank version not found for tenant")
    return row


def _draft_of(
    db: Session, tenant_id: UUID, bank_id: UUID
) -> QuestionBankVersion | None:
    return db.scalars(
        select(QuestionBankVersion).where(
            QuestionBankVersion.tenant_id == tenant_id,
            QuestionBankVersion.bank_id == bank_id,
            QuestionBankVersion.state == "draft",
        )
    ).first()


def _require_draft(row: QuestionBankVersion) -> None:
    if row.state != "draft":
        raise BankVersionImmutable(
            f"bank version is {row.state}; only drafts may be edited"
        )


def _version_rows(db: Session, tenant_id: UUID, version_id: UUID) -> list[Question]:
    return list(
        db.scalars(
            select(Question)
            .where(
                Question.tenant_id == tenant_id,
                Question.bank_version_id == version_id,
            )
            .order_by(Question.ext_id)
        ).all()
    )


def _refresh_hash(db: Session, version: QuestionBankVersion) -> str:
    rows = _version_rows(db, version.tenant_id, version.id)
    version.content_hash = canonical_content_hash([_question_dict(r) for r in rows])
    return version.content_hash


def _doc_for_version(
    db: Session,
    bank: QuestionBank,
    version: QuestionBankVersion,
    *,
    course_slug: str,
) -> BankDoc:
    rows = _version_rows(db, bank.tenant_id, version.id)
    return BankDoc(
        course=course_slug,
        chapter=bank.chapter_number,
        kind=bank.kind,
        version=version.version_no or 0,
        questions=[
            {
                "id": q.ext_id,
                "stem": q.stem,
                "type": q.type,
                "options": q.options,
                "correct": q.correct,
                "rubric_category": q.rubric_category,
                "category": q.category,
                "explanation": q.explanation,
                "weight": q.weight,
            }
            for q in rows
        ],
        policy={},
    )


def _copy_questions(
    db: Session,
    *,
    tenant_id: UUID,
    bank_id: UUID,
    source_version_id: UUID,
    target_version_id: UUID,
) -> int:
    count = 0
    for row in _version_rows(db, tenant_id, source_version_id):
        db.add(
            Question(
                tenant_id=tenant_id,
                bank_id=bank_id,
                bank_version_id=target_version_id,
                ext_id=row.ext_id,
                stem=row.stem,
                type=row.type,
                options=deepcopy(row.options),
                correct=deepcopy(row.correct),
                rubric_category=row.rubric_category,
                category=row.category,
                explanation=row.explanation,
                weight=row.weight,
            )
        )
        count += 1
    return count


def create_bank_draft(
    db: Session,
    command: CreateBankDraftCommand,
    *,
    audit_context: BankAuditContext,
) -> BankVersionFact:
    """Create the bank's single mutable draft, copying a source version."""
    _check_context(audit_context, command.tenant_id)
    boundary = _transaction(db)
    bank = db.scalars(
        select(QuestionBank)
        .where(
            QuestionBank.tenant_id == command.tenant_id,
            QuestionBank.course_id == command.course_id,
            QuestionBank.chapter_number == command.chapter_number,
            QuestionBank.kind == command.kind,
        )
        .with_for_update()
    ).first()
    if bank is None:
        bank = QuestionBank(
            tenant_id=command.tenant_id,
            course_id=command.course_id,
            chapter_number=command.chapter_number,
            kind=command.kind,
            version=1,
        )
        db.add(bank)
        db.flush()
    existing = _draft_of(db, command.tenant_id, bank.id)
    if existing is not None:
        raise BankLifecycleConflict(
            "a draft already exists for this bank; publish or discard it first"
        )
    source_id: UUID | None = None
    if not command.from_empty:
        source_id = command.from_version_id or bank.current_version_id
    if source_id is not None:
        source = _version(db, command.tenant_id, source_id)
        if source.bank_id != bank.id:
            raise BankLifecycleConflict("copy source belongs to another bank")
        if source.state == "draft":
            raise BankLifecycleConflict("cannot copy from a draft version")
    version = QuestionBankVersion(
        tenant_id=command.tenant_id,
        bank_id=bank.id,
        version_no=None,
        state="draft",
        origin=command.origin,
        base_version_id=bank.current_version_id,
        created_by_actor=audit_context.actor_party_id,
    )
    db.add(version)
    db.flush()
    copied = 0
    if source_id is not None:
        copied = _copy_questions(
            db,
            tenant_id=command.tenant_id,
            bank_id=bank.id,
            source_version_id=source_id,
            target_version_id=version.id,
        )
        db.flush()
    _refresh_hash(db, version)
    db.flush()
    _audit(
        db,
        audit_context,
        action="question_bank.draft_created",
        entity_id=bank.id,
        details={
            "bank_id": str(bank.id),
            "version_id": str(version.id),
            "origin": version.origin,
            "content_hash": version.content_hash,
            "question_count": copied,
        },
    )
    result = _version_fact(db, version, question_count=copied)
    _unchanged(db, boundary)
    return result


def upsert_draft_question(
    db: Session,
    command: UpsertDraftQuestionCommand,
    *,
    audit_context: BankAuditContext,
) -> QuestionFact:
    """Insert or replace one question in a draft, keyed by ext_id."""
    _check_context(audit_context, command.tenant_id)
    boundary = _transaction(db)
    version = _version(db, command.tenant_id, command.version_id, lock=True)
    _require_draft(version)
    write = command.question
    row = db.scalars(
        select(Question).where(
            Question.tenant_id == command.tenant_id,
            Question.bank_version_id == version.id,
            Question.ext_id == write.ext_id,
        )
    ).first()
    values: dict[str, Any] = {
        "stem": write.stem,
        "type": write.type,
        "options": deepcopy(write.options),
        "correct": deepcopy(write.correct),
        "rubric_category": write.rubric_category,
        "category": write.category,
        "explanation": write.explanation,
        "weight": write.weight,
    }
    if row is None:
        row = Question(
            tenant_id=command.tenant_id,
            bank_id=version.bank_id,
            bank_version_id=version.id,
            ext_id=write.ext_id,
            **values,
        )
        db.add(row)
    else:
        if row.bank_id != version.bank_id:
            raise BankLifecycleConflict("question bank does not match its version")
        for name, value in values.items():
            setattr(row, name, value)
    version.draft_revision += 1
    db.flush()
    _refresh_hash(db, version)
    db.flush()
    _audit(
        db,
        audit_context,
        action="question_bank.question_upserted",
        entity_id=version.bank_id,
        details={
            "bank_id": str(version.bank_id),
            "version_id": str(version.id),
            "ext_id": write.ext_id,
            "content_hash": version.content_hash,
        },
    )
    result = _question_fact(row)
    _unchanged(db, boundary)
    return result


def delete_draft_question(
    db: Session,
    command: DeleteDraftQuestionCommand,
    *,
    audit_context: BankAuditContext,
) -> None:
    _check_context(audit_context, command.tenant_id)
    boundary = _transaction(db)
    version = _version(db, command.tenant_id, command.version_id, lock=True)
    _require_draft(version)
    row = db.scalars(
        select(Question).where(
            Question.tenant_id == command.tenant_id,
            Question.bank_version_id == version.id,
            Question.ext_id == command.ext_id,
        )
    ).first()
    if row is None:
        raise BankVersionNotFound("draft question not found")
    db.delete(row)
    version.draft_revision += 1
    db.flush()
    _refresh_hash(db, version)
    db.flush()
    _audit(
        db,
        audit_context,
        action="question_bank.question_deleted",
        entity_id=version.bank_id,
        details={
            "bank_id": str(version.bank_id),
            "version_id": str(version.id),
            "ext_id": command.ext_id,
            "content_hash": version.content_hash,
        },
    )
    _unchanged(db, boundary)


def discard_draft(
    db: Session,
    command: DiscardDraftCommand,
    *,
    audit_context: BankAuditContext,
) -> None:
    """Delete a draft and its questions; published history is untouched."""
    _check_context(audit_context, command.tenant_id)
    boundary = _transaction(db)
    version = _version(db, command.tenant_id, command.version_id, lock=True)
    _require_draft(version)
    bank_id, version_id = version.bank_id, version.id
    content_hash = version.content_hash
    count = int(
        db.scalar(
            select(func.count())
            .select_from(Question)
            .where(
                Question.tenant_id == command.tenant_id,
                Question.bank_version_id == version_id,
            )
        )
        or 0
    )
    _audit(
        db,
        audit_context,
        action="question_bank.draft_discarded",
        entity_id=bank_id,
        details={
            "bank_id": str(bank_id),
            "version_id": str(version_id),
            "content_hash": content_hash,
            "question_count": count,
        },
    )
    db.execute(
        delete(Question).where(
            Question.tenant_id == command.tenant_id,
            Question.bank_version_id == version_id,
        )
    )
    db.delete(version)
    db.flush()
    _unchanged(db, boundary)


def lint_bank_version(
    db: Session, *, tenant_id: UUID, version_id: UUID, course_slug: str = ""
) -> LintResult:
    """Recompute and store a draft's violations via the single rules owner.

    Draft-only: the stored lint state and content hash of a published or
    retired version are part of its immutable record and are never
    rewritten here.
    """
    boundary = _transaction(db)
    version = _version(db, tenant_id, version_id, lock=True)
    _require_draft(version)
    bank = _bank(db, tenant_id, version.bank_id)
    doc = _doc_for_version(db, bank, version, course_slug=course_slug)
    violations = _lint_bank_rules(doc)
    version.lint_violations = list(violations)
    _refresh_hash(db, version)
    db.flush()
    result = LintResult(
        version_id=version.id,
        violations=tuple(violations),
        content_hash=version.content_hash,
    )
    _unchanged(db, boundary)
    return result


def publish_bank_version(
    db: Session,
    command: PublishBankVersionCommand,
    *,
    audit_context: BankAuditContext,
    course_slug: str = "",
) -> PublishResult:
    """Freeze a lint-clean draft as the next version; never repoints activities.

    A refusal stages the refreshed draft lint state in the caller's
    transaction and then raises: it persists only if the caller catches
    the refusal and commits. The conventional rollback-on-refusal
    discards it.
    """
    _check_context(audit_context, command.tenant_id)
    boundary = _transaction(db)
    version = _version(db, command.tenant_id, command.version_id, lock=True)
    _require_draft(version)
    bank = _bank(db, command.tenant_id, version.bank_id, lock=True)
    if bank.current_version_id != version.base_version_id:
        raise BankPublishRefused(
            "the bank's current version changed since this draft was based "
            "on it; discard and re-draft from the new current version"
        )
    if (
        command.expected_current_version_id is not None
        and command.expected_current_version_id != bank.current_version_id
    ):
        raise BankPublishRefused(
            "expected current version does not match the bank's current version"
        )
    doc = _doc_for_version(db, bank, version, course_slug=course_slug)
    violations = _lint_bank_rules(doc)
    version.lint_violations = list(violations)
    _refresh_hash(db, version)
    db.flush()
    if violations:
        raise BankPublishRefused(
            f"bank draft has {len(violations)} lint violation(s) and cannot publish"
        )
    if bank.current_version_id is not None:
        current = _version(db, command.tenant_id, bank.current_version_id)
        if current.content_hash == version.content_hash:
            result = PublishResult(
                version=_version_fact(db, version),
                published=False,
                outcome="noop_identical_to_current",
            )
            _unchanged(db, boundary)
            return result
    max_no = db.scalar(
        select(func.coalesce(func.max(QuestionBankVersion.version_no), 0)).where(
            QuestionBankVersion.tenant_id == command.tenant_id,
            QuestionBankVersion.bank_id == bank.id,
        )
    )
    previous_current = bank.current_version_id
    version.version_no = int(max_no or 0) + 1
    version.state = "published"
    version.published_by = audit_context.actor_party_id
    version.published_at = command.now or datetime.now(UTC)
    if previous_current is not None:
        previous = _version(db, command.tenant_id, previous_current)
        previous.superseded_by_version_id = version.id
    bank.current_version_id = version.id
    bank.version = version.version_no
    db.flush()
    count = int(
        db.scalar(
            select(func.count())
            .select_from(Question)
            .where(
                Question.tenant_id == command.tenant_id,
                Question.bank_version_id == version.id,
            )
        )
        or 0
    )
    _audit(
        db,
        audit_context,
        action="question_bank.published",
        entity_id=bank.id,
        details={
            "bank_id": str(bank.id),
            "version_id": str(version.id),
            "version_no": version.version_no,
            "content_hash": version.content_hash,
            "question_count": count,
            "previous_current_version_id": (
                str(previous_current) if previous_current else None
            ),
            "new_current_version_id": str(version.id),
        },
    )
    result = PublishResult(
        version=_version_fact(db, version, question_count=count),
        published=True,
        outcome="published",
    )
    _unchanged(db, boundary)
    return result


def retire_bank_version(
    db: Session,
    command: RetireBankVersionCommand,
    *,
    audit_context: BankAuditContext,
) -> BankVersionFact:
    """Retire a published version: no new sittings, history stays readable.

    Retiring the current version clears the bank's current pointer but
    does not rewrite the legacy ``QuestionBank.version`` mirror, which
    keeps naming the last published version number.
    """
    _check_context(audit_context, command.tenant_id)
    if not (command.reason or "").strip():
        raise ValueError("retiring a bank version requires a reason")
    boundary = _transaction(db)
    version = _version(db, command.tenant_id, command.version_id, lock=True)
    if version.state != "published":
        raise BankLifecycleConflict("only a published version can be retired")
    bank = _bank(db, command.tenant_id, version.bank_id, lock=True)
    version.state = "retired"
    version.retired_by = audit_context.actor_party_id
    version.retired_at = command.now or datetime.now(UTC)
    version.retire_reason = command.reason.strip()
    if bank.current_version_id == version.id:
        bank.current_version_id = None
    db.flush()
    _audit(
        db,
        audit_context,
        action="question_bank.version_retired",
        entity_id=bank.id,
        details={
            "bank_id": str(bank.id),
            "version_id": str(version.id),
            "version_no": version.version_no,
            "content_hash": version.content_hash,
            "retire_reason": version.retire_reason,
        },
    )
    result = _version_fact(db, version)
    _unchanged(db, boundary)
    return result


def repoint_activity_bank_version(
    db: Session,
    command: RepointActivityBankVersionCommand,
    *,
    audit_context: BankAuditContext,
) -> BankVersionFact:
    """Explicitly point one activity at a published version of its bank."""
    _check_context(audit_context, command.tenant_id)
    boundary = _transaction(db)
    version = _version(db, command.tenant_id, command.version_id)
    if version.state != "published":
        raise BankLifecycleConflict(
            "an activity may only serve a published bank version"
        )
    activity = db.scalars(
        select(Activity)
        .where(
            Activity.tenant_id == command.tenant_id,
            Activity.id == command.activity_id,
        )
        .with_for_update()
    ).first()
    if activity is None:
        raise BankNotFound("activity not found for tenant")
    if activity.bank_id is not None and activity.bank_id != version.bank_id:
        raise BankLifecycleConflict(
            "version belongs to a different bank than the activity"
        )
    previous = activity.bank_version_id
    activity.bank_id = version.bank_id
    activity.bank_version_id = version.id
    db.flush()
    _audit(
        db,
        audit_context,
        action="question_bank.activity_repointed",
        entity_id=version.bank_id,
        details={
            "bank_id": str(version.bank_id),
            "version_id": str(version.id),
            "version_no": version.version_no,
            "activity_id": str(activity.id),
            "previous_version_id": str(previous) if previous else None,
            "new_version_id": str(version.id),
        },
    )
    result = _version_fact(db, version)
    _unchanged(db, boundary)
    return result


def bank_version_questions(
    db: Session, *, tenant_id: UUID, version_id: UUID
) -> BankVersionQuestions:
    """Answer-key-bearing read of one version; the caller gates disclosure."""
    version = _version(db, tenant_id, version_id)
    rows = _version_rows(db, tenant_id, version_id)
    return BankVersionQuestions(
        version=_version_fact(db, version, question_count=len(rows)),
        questions=tuple(_question_fact(r) for r in rows),
    )


def bank_version_history(
    db: Session, *, tenant_id: UUID, bank_id: UUID
) -> tuple[BankVersionFact, ...]:
    """Metadata, hashes and counts only — no question content."""
    _bank(db, tenant_id, bank_id)
    rows = db.scalars(
        select(QuestionBankVersion)
        .where(
            QuestionBankVersion.tenant_id == tenant_id,
            QuestionBankVersion.bank_id == bank_id,
        )
        .order_by(
            QuestionBankVersion.version_no.asc().nulls_last(),
            QuestionBankVersion.created_at,
        )
    ).all()
    return tuple(_version_fact(db, row) for row in rows)


@dataclass(frozen=True)
class _Sitter:
    id: UUID


@dataclass(frozen=True)
class _EngineQuestion:
    ext_id: str
    category: str | None
    options: JsonValue
    stem: str
    type: str


def sitting_questions(
    db: Session,
    *,
    tenant_id: UUID,
    version_id: UUID,
    sitter_id: UUID,
    only_ext_ids: list[str] | tuple[str, ...] | None = None,
) -> tuple[SittingQuestion, ...]:
    """Key-free sitting projection in stored order, options per sitter.

    Selection stays with the caller/exam engine; this read only strips
    answer keys (the dataclass has no correct/explanation fields) and
    applies the engine's deterministic per-sitter option order.
    """
    version = _version(db, tenant_id, version_id)
    if version.state != "published":
        raise BankLifecycleConflict(f"a {version.state} version cannot serve a sitting")
    rows = _version_rows(db, tenant_id, version_id)
    if only_ext_ids is not None:
        keep = list(only_ext_ids)
        by_ext = {r.ext_id: r for r in rows}
        if any(ext not in by_ext for ext in keep):
            raise BankVersionNotFound(
                "a requested question is not in this bank version"
            )
        rows = [by_ext[ext] for ext in keep]
    sitter = _Sitter(sitter_id)
    out: list[SittingQuestion] = []
    for row in rows:
        engine_q = _EngineQuestion(
            ext_id=row.ext_id,
            category=row.category,
            options=cast(JsonValue, deepcopy(row.options)),
            stem=row.stem,
            type=row.type,
        )
        ordered = exam_engine.present_options(sitter, engine_q)
        out.append(
            SittingQuestion(
                ext_id=row.ext_id,
                stem=row.stem,
                type=row.type,
                options=tuple(cast(JsonValue, item) for item in ordered),
            )
        )
    return tuple(out)


def published_bank_doc(
    db: Session, *, tenant_id: UUID, version_id: UUID, course_slug: str
) -> BankDocument:
    """Export data for one version in the transport document shape.

    Policy is empty: assessment policy lives on activities and is not
    part of a bank version. Encoding to YAML and any file handling are
    product responsibilities; the audit event is written by
    :func:`export_bank_version`.
    """
    version = _version(db, tenant_id, version_id)
    bank = _bank(db, tenant_id, version.bank_id)
    rows = _version_rows(db, tenant_id, version_id)
    return BankDocument(
        course=course_slug,
        chapter=bank.chapter_number,
        kind=bank.kind,
        version=version.version_no or 0,
        questions=tuple(
            BankQuestion(
                id=q.ext_id,
                stem=q.stem,
                type=q.type,
                options=cast(JsonValue, deepcopy(q.options)),
                correct=cast(JsonValue, deepcopy(q.correct)),
                rubric_category=q.rubric_category,
                category=q.category,
                explanation=q.explanation,
                weight=q.weight,
            )
            for q in rows
        ),
        policy={},
    )


def export_bank_version(
    db: Session,
    *,
    tenant_id: UUID,
    version_id: UUID,
    course_slug: str,
    audit_context: BankAuditContext,
) -> BankDocument:
    """Audited export read; full answer keys leave only through here."""
    _check_context(audit_context, tenant_id)
    boundary = _transaction(db)
    version = _version(db, tenant_id, version_id)
    doc = published_bank_doc(
        db, tenant_id=tenant_id, version_id=version_id, course_slug=course_slug
    )
    _audit(
        db,
        audit_context,
        action="question_bank.exported",
        entity_id=version.bank_id,
        details={
            "bank_id": str(version.bank_id),
            "version_id": str(version.id),
            "version_no": version.version_no,
            "content_hash": version.content_hash,
            "question_count": len(doc.questions),
        },
    )
    _unchanged(db, boundary)
    return doc


def _document_question_dicts(
    doc: BankDocument, missing_question_weight: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for question in doc.questions:
        values = question.model_dump()
        out.append(
            {
                "ext_id": values["id"],
                "stem": values["stem"],
                "type": values["type"],
                "options": values.get("options", []),
                "correct": values.get("correct", []),
                "rubric_category": values["rubric_category"],
                "category": _competency_of(values),
                "explanation": values.get("explanation", ""),
                "weight": int(values.get("weight", missing_question_weight)),
            }
        )
    return out


def import_bank_document_as_draft(
    db: Session,
    command: ImportBankDocumentAsDraftCommand,
    *,
    audit_context: BankAuditContext,
) -> BankImportReport:
    """Land a decoded document as a draft; never publish or touch activities.

    ``doc.version`` is ignored for versioning (Learning allocates numbers
    at publish). A lint-failing document still becomes a draft for repair
    but can never publish until clean. An existing UI-authored or
    UI-touched draft is never overwritten; an untouched yaml_import draft
    may be replaced only with the explicit replace flag.

    Stored draft lint covers questions only: the document policy is
    returned as unapplied proposals and never represented as draft
    question lint, so import and publish lint agree. A refusal stages
    its ``question_bank.import_refused`` audit in the caller's
    transaction and then raises: the audit persists only if the caller
    catches the refusal and commits. The conventional
    rollback-on-refusal discards it, so Academy composition must commit
    on refusal for import paths (an A1/A2 gate).
    """
    _check_context(audit_context, command.tenant_id)
    if (
        type(command.missing_question_weight) is not int
        or command.missing_question_weight <= 0
    ):
        raise ValueError("missing_question_weight must be a positive integer")
    boundary = _transaction(db)
    doc = command.doc
    dicts = _document_question_dicts(doc, command.missing_question_weight)
    content_hash = canonical_content_hash(dicts)
    proposals = tuple(sorted(doc.policy.items()))
    bank = db.scalars(
        select(QuestionBank)
        .where(
            QuestionBank.tenant_id == command.tenant_id,
            QuestionBank.course_id == command.course_id,
            QuestionBank.chapter_number == doc.chapter,
            QuestionBank.kind == doc.kind,
        )
        .with_for_update()
    ).first()
    if bank is None:
        bank = QuestionBank(
            tenant_id=command.tenant_id,
            course_id=command.course_id,
            chapter_number=doc.chapter,
            kind=doc.kind,
            version=1,
        )
        db.add(bank)
        db.flush()
    if bank.current_version_id is not None:
        current = _version(db, command.tenant_id, bank.current_version_id)
        if current.content_hash == content_hash:
            result = BankImportReport(
                outcome="noop_hash_equal",
                bank_id=bank.id,
                version_id=None,
                content_hash=content_hash,
                lint_violations=(),
                policy_proposals=proposals,
            )
            _unchanged(db, boundary)
            return result
    outcome = "created"
    draft = _draft_of(db, command.tenant_id, bank.id)
    if draft is not None:
        replaceable = draft.origin == "yaml_import" and draft.draft_revision == 0
        if not (replaceable and command.replace_imported_draft):
            _audit(
                db,
                audit_context,
                action="question_bank.import_refused",
                entity_id=bank.id,
                details={
                    "bank_id": str(bank.id),
                    "version_id": str(draft.id),
                    "origin": draft.origin,
                    "content_hash": content_hash,
                },
            )
            db.flush()
            raise BankImportRefused(
                "a draft already exists for this bank "
                f"(origin={draft.origin}); publish or discard it first"
            )
        db.execute(
            delete(Question).where(
                Question.tenant_id == command.tenant_id,
                Question.bank_version_id == draft.id,
            )
        )
        db.delete(draft)
        db.flush()
        outcome = "replaced_imported_draft"
    # Question-only lint: versions store no policy, so policy diagnostics
    # could never be repaired in the draft. The document policy travels
    # separately as unapplied proposals in the import report.
    rules_doc = BankDoc(
        course=doc.course,
        chapter=doc.chapter,
        kind=doc.kind,
        version=doc.version,
        questions=[q.model_dump() for q in doc.questions],
        policy={},
    )
    violations = _lint_bank_rules(rules_doc)
    version = QuestionBankVersion(
        tenant_id=command.tenant_id,
        bank_id=bank.id,
        version_no=None,
        state="draft",
        origin="yaml_import",
        content_hash=content_hash,
        lint_violations=list(violations),
        base_version_id=bank.current_version_id,
        created_by_actor=audit_context.actor_party_id,
    )
    db.add(version)
    db.flush()
    for values in dicts:
        db.add(
            Question(
                tenant_id=command.tenant_id,
                bank_id=bank.id,
                bank_version_id=version.id,
                **values,
            )
        )
    db.flush()
    _audit(
        db,
        audit_context,
        action="question_bank.imported",
        entity_id=bank.id,
        details={
            "bank_id": str(bank.id),
            "version_id": str(version.id),
            "origin": "yaml_import",
            "content_hash": content_hash,
            "question_count": len(dicts),
            "lint_violation_count": len(violations),
            "outcome": outcome,
        },
    )
    result = BankImportReport(
        outcome=outcome,
        bank_id=bank.id,
        version_id=version.id,
        content_hash=content_hash,
        lint_violations=tuple(violations),
        policy_proposals=proposals,
    )
    _unchanged(db, boundary)
    return result


def get_bank_fact(db: Session, *, tenant_id: UUID, bank_id: UUID) -> BankFact:
    bank = _bank(db, tenant_id, bank_id)
    return BankFact(
        id=bank.id,
        tenant_id=bank.tenant_id,
        course_id=bank.course_id,
        chapter_number=bank.chapter_number,
        kind=bank.kind,
        version=bank.version,
        current_version_id=bank.current_version_id,
    )


__all__ = [
    "bank_version_history",
    "bank_version_questions",
    "canonical_content_hash",
    "create_bank_draft",
    "delete_draft_question",
    "discard_draft",
    "export_bank_version",
    "get_bank_fact",
    "import_bank_document_as_draft",
    "lint_bank_version",
    "publish_bank_version",
    "published_bank_doc",
    "repoint_activity_bank_version",
    "retire_bank_version",
    "sitting_questions",
    "upsert_draft_question",
]
