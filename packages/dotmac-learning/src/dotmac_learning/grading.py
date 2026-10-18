from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from pydantic import JsonValue

from .assessment_contracts import GradingQuestion
from .result_contracts import GradedResult


def _grade_numeric(chosen: list[Any], correct: list[Any], opts: Any) -> bool:
    if not chosen:
        return False
    try:
        got = float(chosen[0])
    except (ValueError, TypeError):
        return False
    if isinstance(opts, dict) and "tolerance" in opts:  # gaps: tolerance mode
        try:
            tol = float(opts.get("tolerance", 0))
        except (ValueError, TypeError):
            tol = 0.0
        try:
            target = float(correct[0])
        except (ValueError, TypeError, IndexError):
            return False
        return abs(got - target) <= tol
    try:  # buildout: exact-or-range
        bounds = [float(x) for x in correct]
    except (ValueError, TypeError):
        return False
    if not bounds:
        return False
    if len(bounds) == 1:
        return got == bounds[0]
    lo, hi = min(bounds[0], bounds[1]), max(bounds[0], bounds[1])
    return lo <= got <= hi


def _norm_text(s: object) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _grade_short_text(chosen: list[Any], correct: list[Any], opts: Any) -> bool:
    if not chosen or not str(chosen[0]).strip():
        return False
    if isinstance(opts, dict) and opts.get("regex"):  # gaps: regex mode
        for pattern in correct:
            try:
                if re.fullmatch(str(pattern), str(chosen[0]).strip(), re.IGNORECASE):
                    return True
            except re.error:
                pass
        return False
    target = _norm_text(chosen[0])  # buildout: normalized
    return target in {_norm_text(e) for e in correct}


def _is_correct(
    qtype: str, chosen: list[Any], expected: list[Any], opts: Any = None
) -> bool:
    if qtype == "numeric":
        return _grade_numeric(chosen, expected, opts)
    if qtype == "short_text":
        return _grade_short_text(chosen, expected, opts)
    return set(chosen) == set(expected)


def grade_submission(
    answers: Mapping[str, JsonValue],
    questions: Sequence[GradingQuestion],
    pass_threshold: float,
    *,
    missing_question_weight: int,
) -> GradedResult:
    if type(missing_question_weight) is not int or missing_question_weight <= 0:
        raise ValueError("missing_question_weight must be a positive integer")
    # Source JSON can be malformed: retain its list/scalar/error semantics.
    # Any is confined to the original algorithm rather than the public contract.
    score = 0.0
    max_score = 0.0
    per_item: list[dict[str, JsonValue]] = []
    for q in cast(Any, questions):
        w = float(q.get("weight", missing_question_weight))
        max_score += w
        chosen = list(cast(Any, answers).get(q["ext_id"], []))
        raw_correct = q.get("correct", [])
        # Authors may store `correct` as a scalar (e.g. numeric 42) or a list;
        # normalize.
        expected = list(raw_correct) if isinstance(raw_correct, list) else [raw_correct]
        opts = q.get("options") or {}
        ok = _is_correct(q.get("type", "single"), chosen, expected, opts)
        if ok:
            score += w
        per_item.append(
            {
                "id": q["ext_id"],
                "correct": ok,
                "chosen": chosen,
                "expected": expected,
                "weight": q.get("weight", missing_question_weight),
            }
        )
    fraction = (score / max_score) if max_score else 0.0
    return GradedResult(
        score=score,
        max_score=max_score,
        fraction=fraction,
        passed=(max_score > 0 and fraction >= pass_threshold),
        per_item=per_item,
    )
