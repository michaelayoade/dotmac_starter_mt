"""Source item analysis in the caller's transaction, without grading or writes.

Each person's first maximum-fraction row wins; pass, provenance and recency do
not break ties. SQL encounter order is intentionally unspecified. No activity
lookup, enrollment, availability or source filter is added to the source query.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .analytics_contracts import ItemAnalysis, ItemIdentifier
from .models import Score, Submission
from .progress_contracts import ScoreFact
from .reports import _fraction_winner


def _activity_scores_query(
    tenant_id: UUID, activity_id: UUID
) -> Select[tuple[UUID, Score]]:
    return (
        select(Submission.person_id, Score)
        .join(
            Score,
            (Score.submission_id == Submission.id)
            & (Score.tenant_id == Submission.tenant_id),
        )
        .where(Submission.tenant_id == tenant_id)
        .where(Submission.activity_id == activity_id)
    )


def _best_scores_for_activity(
    db: Session, *, tenant_id: UUID, activity_id: UUID
) -> list[Score]:
    rows = db.execute(_activity_scores_query(tenant_id, activity_id)).all()
    best: dict[UUID, Score] = {}
    for person_id, score in rows:
        cur = best.get(person_id)
        best[person_id] = _fraction_winner(cur, score)
    return list(best.values())


def _analysis_from(scores: Iterable[Score | ScoreFact]) -> tuple[ItemAnalysis, ...]:
    tally: dict[ItemIdentifier, list[int]] = {}
    order: list[ItemIdentifier] = []
    for score in scores:
        # A typing-only cast retains historical JSON behavior, including the
        # source's errors for non-item payloads; no validation/coercion occurs.
        for item in cast(list[dict[str, JsonValue]], score.per_item or []):
            qid_value = item.get("id")
            if qid_value is None:
                continue
            qid = cast(ItemIdentifier, qid_value)
            if qid not in tally:
                tally[qid] = [0, 0]
                order.append(qid)
            tally[qid][0] += 1
            if item.get("correct"):
                tally[qid][1] += 1
    return tuple(
        ItemAnalysis(
            qid,
            tally[qid][0],
            tally[qid][1],
            (tally[qid][1] / tally[qid][0]) if tally[qid][0] else 0.0,
        )
        for qid in order
    )


def item_analysis(
    db: Session, *, tenant_id: UUID, activity_id: UUID
) -> tuple[ItemAnalysis, ...]:
    return _analysis_from(
        _best_scores_for_activity(db, tenant_id=tenant_id, activity_id=activity_id)
    )
