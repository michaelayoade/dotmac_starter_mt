"""Reusable curriculum lifecycle from Academy 0968881.

Caller supplies an active Session/root transaction and normalized rendered
content, or a trusted product renderer. Commands mutate and flush only. Source
read-then-write concurrency and database uniqueness behavior are preserved; no
retry guarantee is added.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from typing import Any, cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, SessionTransaction

from .bank_validation import BankDoc, _competency_of
from .bank_validation import lint_bank as _lint_bank
from .curriculum_contracts import (
    AuthorChapterCommand,
    BankDocument,
    BankDocumentHeader,
    BankFact,
    BankQuestion,
    ChapterFact,
    ChapterSource,
    ChapterWrite,
    CourseCreate,
    ImportedChapter,
    ManualImport,
    ManualImportHeader,
    PublicCatalogItem,
)
from .curriculum_models import Chapter, Question, QuestionBank
from .models import Course
from .references import (
    CourseFact,
    CourseWrite,
    get_course,
)
from .references import _course_fact as _project_course
from .references import (
    create_course as create_reference_course,
)
from .results import _transaction, _unchanged


class CurriculumConflict(ValueError):
    pass


class CurriculumNotFound(ValueError):
    pass


def _fact(row: Chapter) -> ChapterFact:
    return ChapterFact(
        **{name: getattr(row, name) for name in ChapterFact.__dataclass_fields__}
    )


def _bank_fact(row: QuestionBank) -> BankFact:
    return BankFact(
        **{name: getattr(row, name) for name in BankFact.__dataclass_fields__}
    )


def _course(db: Session, tenant_id: UUID, course_id: UUID) -> Course:
    row = db.scalars(
        select(Course).where(Course.tenant_id == tenant_id, Course.id == course_id)
    ).first()
    if row is None:
        raise CurriculumNotFound("course not found for tenant")
    return row


def _course_fact(db: Session, row: Course) -> CourseFact:
    result = get_course(db, tenant_id=row.tenant_id, course_id=row.id)
    if result is None:
        raise AssertionError
    return result


def create_course(db: Session, command: CourseCreate) -> CourseFact:
    """Normalize slug and reject an observed duplicate; create an in-app draft."""
    from uuid import uuid4

    boundary = _transaction(db)
    slug = (command.slug or "").strip().lower()
    existing = db.scalars(
        select(Course).where(Course.tenant_id == command.tenant_id, Course.slug == slug)
    ).first()
    if existing is not None:
        raise CurriculumConflict(f"a course with slug {slug!r} already exists")
    result = create_reference_course(
        db,
        CourseWrite(
            id=uuid4(),
            tenant_id=command.tenant_id,
            slug=slug,
            title=command.title,
            discipline=command.discipline,
            source_ref="in-app",
            version=1,
            status="draft",
            listed=command.listed,
        ),
    )
    _unchanged(db, boundary)
    return result


def author_chapter(
    db: Session,
    command: AuthorChapterCommand,
    *,
    render_markdown: Callable[[str], str],
) -> ChapterFact:
    """Resolve the scoped course, then render once before looking up the chapter.

    The trusted product renderer owns HTML safety and must preserve the caller's
    transaction boundary. Renderer failures propagate before chapter work.
    """
    boundary = _transaction(db)
    course = _course(db, command.tenant_id, command.course_id)
    body_html = render_markdown(command.body_md)
    _unchanged(db, boundary)
    result = _write_chapter(
        db, course, ChapterWrite(**command.model_dump(), body_html=body_html)
    )
    _unchanged(db, boundary)
    return result


def upsert_chapter(db: Session, command: ChapterWrite) -> ChapterFact:
    """Every authoring save bumps version, including an identical save."""
    boundary = _transaction(db)
    course = _course(db, command.tenant_id, command.course_id)
    result = _write_chapter(db, course, command)
    _unchanged(db, boundary)
    return result


def _write_chapter(db: Session, course: Course, command: ChapterWrite) -> ChapterFact:
    chapter = db.scalars(
        select(Chapter).where(
            Chapter.tenant_id == command.tenant_id,
            Chapter.course_id == command.course_id,
            Chapter.number == command.number,
        )
    ).first()
    if chapter is None:
        chapter = Chapter(**command.model_dump(), order_index=command.number)
        db.add(chapter)
    else:
        for name in ("title", "part", "body_md", "body_html"):
            setattr(chapter, name, getattr(command, name))
    course.version += 1
    db.flush()
    result = _fact(chapter)
    return result


def delete_chapter(
    db: Session, *, tenant_id: UUID, course_id: UUID, chapter_id: UUID
) -> None:
    boundary = _transaction(db)
    course = _course(db, tenant_id, course_id)
    chapter = db.scalars(
        select(Chapter).where(
            Chapter.tenant_id == tenant_id,
            Chapter.course_id == course_id,
            Chapter.id == chapter_id,
        )
    ).first()
    if chapter is None:
        raise CurriculumNotFound("chapter not found for tenant")
    db.delete(chapter)
    course.version += 1
    db.flush()
    _unchanged(db, boundary)


def chapter_source_hash(source: ChapterSource) -> str:
    """Exact source hash: raw UTF-8 plus hex fingerprint of sorted figure IDs."""
    figures = hashlib.sha256()
    for figure in sorted(source.figures, key=lambda item: item.id):
        figures.update(figure.id.encode())
        figures.update(figure.content if figure.content is not None else b"<missing>")
    return hashlib.sha256(source.raw_utf8 + figures.hexdigest().encode()).hexdigest()


def import_manual(db: Session, command: ManualImport) -> CourseFact:
    """Upsert metadata and hash-changed chapters; absent chapters are retained.

    A new course with chapters starts at version 2, exactly as the source.
    Existing body_md/order_index survive an imported-content update.
    """
    boundary = _transaction(db)
    course = _write_manual(db, command, command.chapters, boundary)
    result = _course_fact(db, course)
    _unchanged(db, boundary)
    return result


def import_manual_chapters(
    db: Session, command: ManualImportHeader, *, chapters: Iterable[ImportedChapter]
) -> CourseFact:
    """Consume trusted product sources after staging course metadata.

    The iterable owns decoding/rendering and must preserve the caller transaction.
    Python source failures leave earlier work staged for the caller to resolve.
    Returned values are immutable snapshots; no final refresh query is added.
    """
    boundary = _transaction(db)
    course = _write_manual(db, command, chapters, boundary)
    result = _project_course(course)
    _unchanged(db, boundary)
    return result


def _write_manual(
    db: Session,
    command: ManualImportHeader,
    chapters: Iterable[ImportedChapter],
    boundary: tuple[SessionTransaction, SessionTransaction | None],
) -> Course:
    course = db.scalars(
        select(Course).where(
            Course.tenant_id == command.tenant_id, Course.slug == command.slug
        )
    ).first()
    if course is None:
        course = Course(
            tenant_id=command.tenant_id,
            slug=command.slug,
            title=command.title,
            discipline=command.discipline,
            source_ref=command.source_ref,
            version=1,
            status=command.new_course_status,
            listed=command.new_course_listed,
        )
        db.add(course)
        db.flush()
    changed = False
    for name in ("title", "discipline", "source_ref"):
        if getattr(course, name) != getattr(command, name):
            setattr(course, name, getattr(command, name))
            changed = True
    for doc in chapters:
        _unchanged(db, boundary)
        existing = db.scalars(
            select(Chapter).where(
                Chapter.tenant_id == command.tenant_id,
                Chapter.course_id == course.id,
                Chapter.number == doc.number,
            )
        ).first()
        if existing is None:
            db.add(
                Chapter(
                    tenant_id=command.tenant_id,
                    course_id=course.id,
                    **doc.model_dump(),
                    order_index=doc.number,
                )
            )
            changed = True
        elif existing.source_hash != doc.source_hash:
            for name in ("title", "part", "body_html", "source_hash"):
                setattr(existing, name, getattr(doc, name))
            changed = True
    _unchanged(db, boundary)
    if changed:
        course.version += 1
    db.flush()
    return course


def public_catalog(db: Session, *, tenant_id: UUID) -> tuple[PublicCatalogItem, ...]:
    """Anonymous catalog eligibility is published AND listed, ordered by title."""
    rows = db.execute(
        select(Course, func.count(Chapter.id))
        .outerjoin(
            Chapter,
            (Chapter.tenant_id == Course.tenant_id) & (Chapter.course_id == Course.id),
        )
        .where(
            Course.tenant_id == tenant_id,
            Course.status == "published",
            Course.listed.is_(True),
        )
        .group_by(Course.id)
        .order_by(Course.title)
    ).all()
    return tuple(
        PublicCatalogItem(
            course=CourseFact(
                **{
                    name: getattr(course, name)
                    for name in CourseFact.__dataclass_fields__
                }
            ),
            chapter_count=int(count),
        )
        for course, count in rows
    )


def lint_bank(doc: BankDoc | BankDocument) -> list[str]:
    """Delegate to the ported single pure rules owner; loading does not lint."""
    if isinstance(doc, BankDoc):
        return _lint_bank(doc)
    return _lint_bank(
        BankDoc(
            course=doc.course,
            chapter=doc.chapter,
            kind=doc.kind,
            version=doc.version,
            questions=[q.model_dump() for q in doc.questions],
            policy=dict(doc.policy),
        )
    )


def load_bank(
    db: Session,
    *,
    tenant_id: UUID,
    course_id: UUID,
    doc: BankDoc | BankDocument,
    missing_question_weight: int,
) -> BankFact:
    """Replace questions in source order, preserving raw decoder failure stages.

    BankDoc is deliberately unvalidated: defaults, category conversion and weight
    conversion happen per question, after bank creation or replacement deletion.
    """
    if type(missing_question_weight) is not int or missing_question_weight <= 0:
        raise ValueError("missing_question_weight must be a positive integer")
    boundary = _transaction(db)
    bank = db.scalars(
        select(QuestionBank).where(
            QuestionBank.tenant_id == tenant_id,
            QuestionBank.course_id == course_id,
            QuestionBank.chapter_number == doc.chapter,
            QuestionBank.kind == doc.kind,
        )
    ).first()
    if bank is None:
        bank = QuestionBank(
            tenant_id=tenant_id,
            course_id=course_id,
            chapter_number=doc.chapter,
            kind=doc.kind,
            version=doc.version,
        )
        db.add(bank)
        db.flush()
    else:
        db.execute(delete(Question).where(Question.bank_id == bank.id))
        bank.version = doc.version
    for question in doc.questions:
        values = (
            cast(dict[str, Any], question)
            if isinstance(doc, BankDoc)
            else cast(BankQuestion, question).model_dump()
        )
        db.add(
            Question(
                tenant_id=tenant_id,
                bank_id=bank.id,
                ext_id=values["id"],
                stem=values["stem"],
                type=values["type"],
                options=values.get("options", []),
                correct=values.get("correct", []),
                rubric_category=values["rubric_category"],
                category=_competency_of(values),
                explanation=values.get("explanation", ""),
                weight=int(values.get("weight", missing_question_weight)),
            )
        )
    db.flush()
    result = _bank_fact(bank)
    _unchanged(db, boundary)
    return result


def doc_from_db(
    db: Session, *, tenant_id: UUID, bank_id: UUID, course_slug: str
) -> BankDocument:
    bank = db.scalars(
        select(QuestionBank).where(
            QuestionBank.tenant_id == tenant_id, QuestionBank.id == bank_id
        )
    ).first()
    if bank is None:
        raise CurriculumNotFound("bank not found for tenant")
    rows = db.scalars(
        select(Question)
        .where(Question.tenant_id == tenant_id, Question.bank_id == bank.id)
        .order_by(Question.ext_id)
    ).all()
    return BankDocument(
        course=course_slug,
        chapter=bank.chapter_number,
        kind=bank.kind,
        version=bank.version,
        questions=tuple(
            BankQuestion(
                id=q.ext_id,
                stem=q.stem,
                type=q.type,
                options=cast(JsonValue, q.options),
                correct=cast(JsonValue, q.correct),
                rubric_category=q.rubric_category,
                category=q.category,
                explanation=q.explanation,
                weight=q.weight,
            )
            for q in rows
        ),
        policy={},
    )


def decoded_doc_from_db(
    db: Session, *, bank: BankDocumentHeader, course_slug: str
) -> BankDoc:
    """Rebuild a BankDoc from a bank already in the database.

    So the live estate can be checked with :func:`lint_bank` itself rather than
    a second implementation of the rules in SQL. Two implementations of the same
    decision drift the moment a threshold moves; this keeps one.
    """
    rows = db.scalars(
        select(Question)
        .where(Question.tenant_id == bank.tenant_id)
        .where(Question.bank_id == bank.id)
        .order_by(Question.ext_id)
    ).all()
    return BankDoc(
        course=course_slug,
        chapter=bank.chapter_number,
        kind=bank.kind,
        version=bank.version,
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
        # Policy lives on the Activity once loaded, not on the bank, so a
        # database-sourced doc carries none — and lint_bank skips an empty one.
        policy={},
    )
