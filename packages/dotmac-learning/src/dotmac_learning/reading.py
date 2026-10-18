"""Source-derived reading and annotations on the caller's active transaction.

Ported from Academy 0968881 reading_progress.py, bookmarks.py and learn.py.
Required persistence errors propagate. Chapter completion delegates the optional
ledger consequence to the existing event owner. Read-then-write toggles retain
source races; they do not promise linearizable/concurrent toggle semantics.
Entitlement, HTML heading extraction/rendering and escaping belong to adapters.
"""

from __future__ import annotations

import re
from datetime import tzinfo
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import events
from .curriculum_models import (
    Chapter,
    ChapterBookmark,
    ChapterNote,
    ChapterRead,
    SubtopicRead,
)
from .event_contracts import EventWrite
from .models import KIND_CHAPTER_COMPLETED, Course
from .reading_contracts import (
    AnnotatedChapter,
    AnnotationCommand,
    AnnotationGroup,
    AnnotationState,
    ChapterReadingCommand,
    ChapterReadWrite,
    ChapterSubtopicCommand,
    Heading,
    NoteCommand,
    NoteFact,
    ReadingChapterFact,
    ReadingCourseFact,
    SubtopicCompletion,
    SubtopicFact,
    SubtopicReadCommand,
)

# Reuse the first slice's caller-root contract. This is an internal module
# helper, not a Kernel private API or a new transaction authority.
from .results import _transaction

_GROUPED_INTRO_HEADINGS = {"why-this-matters", "catch-up-sidebar"}


def slugify_heading(value: str) -> str:
    """Exact source Unicode/punctuation/whitespace policy, including fallback."""
    return (
        re.sub(
            r"-+",
            "-",
            re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", (value or "").lower().strip())),
        )[:64]
        or "section"
    )


def chapter_subtopics(headings: tuple[Heading, ...]) -> tuple[SubtopicFact, ...]:
    """Assign source ordered slugs to adapter-extracted nonempty headings.

    Duplicate suffixes are appended after truncation, just as in learn.py;
    they can exceed 64 characters. mark_complete independently caps stored
    slugs, preserving the observed source edge case rather than changing it.
    """
    used: set[str] = set()
    topics: list[SubtopicFact] = []
    for heading in headings:
        title = " ".join(heading.title.split())
        if not title:
            continue
        base = slugify_heading(title)
        if base.casefold() in _GROUPED_INTRO_HEADINGS:
            continue
        slug, counter = base, 2
        while slug in used:
            slug = f"{base}-{counter}"
            counter += 1
        used.add(slug)
        topics.append(SubtopicFact(heading.level, title, slug))
    return tuple(topics)


def _chapter(db: Session, payload: ChapterReadingCommand) -> Chapter:
    row = db.scalar(
        select(Chapter).where(
            Chapter.tenant_id == payload.tenant_id,
            Chapter.course_id == payload.course_id,
            Chapter.id == payload.chapter_id,
        )
    )
    if row is None:
        raise ValueError("chapter not found for tenant and course")
    return row


def completed_slugs(
    db: Session, *, tenant_id: UUID, person_id: UUID, chapter_id: UUID
) -> set[str]:
    return set(
        db.scalars(
            select(SubtopicRead.subtopic_slug).where(
                SubtopicRead.tenant_id == tenant_id,
                SubtopicRead.person_id == person_id,
                SubtopicRead.chapter_id == chapter_id,
            )
        ).all()
    )


def completed_slugs_by_chapter(
    db: Session, *, tenant_id: UUID, person_id: UUID, chapter_ids: list[UUID]
) -> dict[UUID, set[str]]:
    if not chapter_ids:
        return {}
    out: dict[UUID, set[str]] = {}
    for chapter_id, slug in db.execute(
        select(SubtopicRead.chapter_id, SubtopicRead.subtopic_slug).where(
            SubtopicRead.tenant_id == tenant_id,
            SubtopicRead.person_id == person_id,
            SubtopicRead.chapter_id.in_(chapter_ids),
        )
    ).all():
        out.setdefault(chapter_id, set()).add(slug)
    return out


def completed_chapter_ids(
    db: Session, *, tenant_id: UUID, person_id: UUID, chapter_ids: list[UUID]
) -> set[UUID]:
    if not chapter_ids:
        return set()
    return set(
        db.scalars(
            select(ChapterRead.chapter_id).where(
                ChapterRead.tenant_id == tenant_id,
                ChapterRead.person_id == person_id,
                ChapterRead.chapter_id.in_(chapter_ids),
            )
        ).all()
    )


def mark_complete(db: Session, payload: SubtopicReadCommand) -> bool:
    """Source primitive: trim/cap/ignore blank; append with ON CONFLICT NOTHING.

    No invented reference/type authorization: chapter ID FK and plain learner
    UUID match storage. Use complete_subtopic after adapter access checks for
    the chapter/heading-validated workflow.
    """
    _transaction(db)
    slug = (payload.subtopic_slug or "").strip()[:64]
    if not slug:
        return False
    inserted = db.execute(
        insert(SubtopicRead)
        .values(
            tenant_id=payload.tenant_id,
            person_id=payload.person_id,
            chapter_id=payload.chapter_id,
            subtopic_slug=slug,
        )
        .on_conflict_do_nothing(constraint="uq_subtopic_reads_person_subtopic")
        .returning(SubtopicRead.id)
    ).first()
    db.flush()
    return inserted is not None


def complete_subtopic(
    db: Session, payload: ChapterSubtopicCommand
) -> SubtopicCompletion:
    """Validate exact submitted slug against stored-content adapter headings.

    The headings must come from the resolved chapter, not a browser payload;
    this function is domain validation, never proof of course access.
    """
    _transaction(db)
    _chapter(db, payload)
    known = {topic.slug for topic in chapter_subtopics(payload.headings)}
    if payload.subtopic_slug not in known:
        raise ValueError("subtopic not found in chapter headings")
    created = mark_complete(
        db,
        SubtopicReadCommand(
            tenant_id=payload.tenant_id,
            person_id=payload.person_id,
            chapter_id=payload.chapter_id,
            subtopic_slug=payload.subtopic_slug,
        ),
    )
    done = completed_slugs(
        db,
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        chapter_id=payload.chapter_id,
    )
    return SubtopicCompletion(created, frozenset(done), known.issubset(done))


def toggle_chapter_read(
    db: Session, payload: ChapterReadingCommand, *, local_day_zone: tzinfo | None = None
) -> ChapterReadWrite:
    _transaction(db)
    _chapter(db, payload)
    existing = db.scalars(
        select(ChapterRead).where(
            ChapterRead.tenant_id == payload.tenant_id,
            ChapterRead.person_id == payload.person_id,
            ChapterRead.chapter_id == payload.chapter_id,
        )
    ).first()
    if existing is not None:
        db.delete(existing)
        db.flush()
        return ChapterReadWrite(False)
    db.add(
        ChapterRead(
            tenant_id=payload.tenant_id,
            person_id=payload.person_id,
            chapter_id=payload.chapter_id,
        )
    )
    # Existing emit preflushes required pending work outside its optional
    # SAVEPOINT. This preserves the accepted first-slice failure boundary.
    emission = events.emit(
        db,
        EventWrite(
            tenant_id=payload.tenant_id,
            person_id=payload.person_id,
            kind=KIND_CHAPTER_COMPLETED,
            course_id=payload.course_id,
            subject_id=payload.chapter_id,
        ),
        local_day_zone=local_day_zone,
    )
    db.flush()
    return ChapterReadWrite(True, emission)


def _note_fact(row: ChapterNote) -> NoteFact:
    return NoteFact(
        **{name: getattr(row, name) for name in NoteFact.__dataclass_fields__}
    )


def get_state(
    db: Session, *, tenant_id: UUID, person_id: UUID, chapter_id: UUID
) -> AnnotationState:
    bookmark = db.scalars(
        select(ChapterBookmark).where(
            ChapterBookmark.tenant_id == tenant_id,
            ChapterBookmark.person_id == person_id,
            ChapterBookmark.chapter_id == chapter_id,
        )
    ).first()
    note = db.scalars(
        select(ChapterNote).where(
            ChapterNote.tenant_id == tenant_id,
            ChapterNote.person_id == person_id,
            ChapterNote.chapter_id == chapter_id,
        )
    ).first()
    return AnnotationState(
        bookmark is not None, _note_fact(note) if note is not None else None
    )


def toggle_bookmark(db: Session, payload: AnnotationCommand) -> bool:
    """Source lookup scope: tenant/person/chapter, course used only on insert."""
    _transaction(db)
    existing = db.scalars(
        select(ChapterBookmark).where(
            ChapterBookmark.tenant_id == payload.tenant_id,
            ChapterBookmark.person_id == payload.person_id,
            ChapterBookmark.chapter_id == payload.chapter_id,
        )
    ).first()
    if existing is not None:
        db.delete(existing)
        db.flush()
        return False
    db.add(ChapterBookmark(**payload.model_dump()))
    db.flush()
    return True


def save_note(db: Session, payload: NoteCommand) -> NoteFact | None:
    """Trim, update raw text, delete blank; escaping stays with the renderer."""
    _transaction(db)
    body = (payload.body or "").strip()
    note = db.scalars(
        select(ChapterNote).where(
            ChapterNote.tenant_id == payload.tenant_id,
            ChapterNote.person_id == payload.person_id,
            ChapterNote.chapter_id == payload.chapter_id,
        )
    ).first()
    if not body:
        if note is not None:
            db.delete(note)
            db.flush()
        return None
    if note is None:
        note = ChapterNote(**payload.model_dump(exclude={"body"}), body=body)
        db.add(note)
    else:
        note.body = body
    db.flush()
    return _note_fact(note)


def list_for_person(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    visible_course_ids: tuple[UUID, ...],
) -> tuple[AnnotationGroup, ...]:
    """Source grouping/order after caller's actual entitlement reader.

    An explicit empty visibility result returns empty. Supplied IDs do not
    confer authorization and the domain does not manufacture visibility.
    Chapter joins retain tenant equality despite the source SIMPLE FK.
    """
    if not visible_course_ids:
        return ()
    bookmarks = db.execute(
        select(ChapterBookmark, Chapter)
        .join(
            Chapter,
            (Chapter.id == ChapterBookmark.chapter_id)
            & (Chapter.tenant_id == ChapterBookmark.tenant_id),
        )
        .where(
            ChapterBookmark.tenant_id == tenant_id,
            ChapterBookmark.person_id == person_id,
            ChapterBookmark.course_id.in_(visible_course_ids),
        )
    ).all()
    notes = db.execute(
        select(ChapterNote, Chapter)
        .join(
            Chapter,
            (Chapter.id == ChapterNote.chapter_id)
            & (Chapter.tenant_id == ChapterNote.tenant_id),
        )
        .where(
            ChapterNote.tenant_id == tenant_id,
            ChapterNote.person_id == person_id,
            ChapterNote.course_id.in_(visible_course_ids),
        )
    ).all()
    by_course: dict[UUID, dict[int, AnnotatedChapter]] = {}
    for bm, chapter in bookmarks:
        entry = by_course.setdefault(bm.course_id, {})
        current = entry.get(chapter.number)
        if current is None:
            current = AnnotatedChapter(
                ReadingChapterFact(
                    chapter.id,
                    chapter.tenant_id,
                    chapter.course_id,
                    chapter.number,
                    chapter.title,
                )
            )
        entry[chapter.number] = AnnotatedChapter(current.chapter, True, current.note)
    for note, chapter in notes:
        entry = by_course.setdefault(note.course_id, {})
        current = entry.get(chapter.number)
        if current is None:
            current = AnnotatedChapter(
                ReadingChapterFact(
                    chapter.id,
                    chapter.tenant_id,
                    chapter.course_id,
                    chapter.number,
                    chapter.title,
                )
            )
        entry[chapter.number] = AnnotatedChapter(
            current.chapter, current.bookmarked, _note_fact(note)
        )
    if not by_course:
        return ()
    courses = db.scalars(
        select(Course).where(
            Course.tenant_id == tenant_id, Course.id.in_(by_course.keys())
        )
    ).all()
    return tuple(
        AnnotationGroup(
            ReadingCourseFact(course.id, course.tenant_id, course.slug, course.title),
            tuple(by_course[course.id][n] for n in sorted(by_course[course.id])),
        )
        for course in sorted(courses, key=lambda row: row.title)
    )


__all__ = [
    "slugify_heading",
    "chapter_subtopics",
    "completed_slugs",
    "completed_slugs_by_chapter",
    "completed_chapter_ids",
    "mark_complete",
    "complete_subtopic",
    "toggle_chapter_read",
    "get_state",
    "toggle_bookmark",
    "save_note",
    "list_for_person",
]
